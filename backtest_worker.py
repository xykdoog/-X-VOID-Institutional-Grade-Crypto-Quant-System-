#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
回测引擎 v2.12 - backtest_worker.py
"手术级性能重构" (Surgical Performance Overhaul v2.12)

核心特性：
1. 物理级配置隔离：使用 custom_config 副本，不污染全局 SYSTEM_CONFIG
2. 真实成交仿真：信号在第 i 根 K 线收盘产生，使用第 i+1 根 K 线 open 价成交
3. 未来函数消除：严格切片，只看到索引 i 之前的数据
4. 🔥 v2.9：按需动态抓取 - 仅拉取主周期 + config 中 HIGHER_INTERVAL 指定的高周期
5. 🔥 修复：返回一致性 - 所有失败情况返回完整元组 (-999.0, 0.0, 0.0, 0.0, 0.0, 0)
6. 🔥 v2.7：动态指标重算 - 每次回测根据参数组合的 EMA_TREND 重新计算指标
7. 🔥 v2.7：消除重复拷贝 - 逐根回放使用视图切片替代 .copy()，性能提升 ~50%
8. 🔥 v2.8：自适应 EMA 映射 - 根据 --period 动态调整 EMA_TREND 搜索范围
9. 🔥 v2.8：MTF 时间同步切片 - 辅助周期按主周期时间戳截断，彻底消除未来函数
10. 🔥 v2.9：物理屏蔽 - 严禁拉取主周期之外的非必要低周期数据，节省 API 调用
11. 🔥 v2.10：并行计算矩阵 - ProcessPoolExecutor 多进程并行执行参数组合
12. 🔥 v2.11：二级缓存 - 预计算所有 EMA_TREND 指标，从 405 次降至 5 次计算
13. 🔥 v2.12：Numpy 向量化 - 将 DataFrame 列转为 numpy 数组，消除对象搜索开销
14. 🔥 v2.12：共享内存并行 - 使用全局变量避免进程间重复序列化 DataFrame
15. 🔥 v2.12：物理剔除冗余数据 - 严格仅拉取主周期和必要的高周期数据
"""

import sys
import os
import argparse
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
import csv
from concurrent.futures import ProcessPoolExecutor, as_completed
import multiprocessing

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from binance.client import Client as BinanceClient

# 动态导入以避免模块名中的连字符问题
import importlib
config_module = importlib.import_module('WJ-BOT.config')
trading_engine_module = importlib.import_module('WJ-BOT.trading_engine')

SYSTEM_CONFIG = config_module.SYSTEM_CONFIG
get_historical_klines = trading_engine_module.get_historical_klines
calculate_indicators = trading_engine_module.calculate_indicators
generate_trading_signals = trading_engine_module.generate_trading_signals

# ═══════════════════════════════════════════════════════════════
# 🔥 v2.12 共享内存：全局只读数据（子进程通过 fork/initializer 继承）
# ═══════════════════════════════════════════════════════════════
_SHARED_INDICATOR_CACHE = {}   # {ema_value: {interval: numpy_arrays_dict}}
_SHARED_SYMBOL = ""
_SHARED_MAIN_INTERVAL = ""


def _init_worker(indicator_cache, symbol, main_interval):
    """🔥 v2.12 进程池初始化器：将只读数据注入子进程全局变量"""
    global _SHARED_INDICATOR_CACHE, _SHARED_SYMBOL, _SHARED_MAIN_INTERVAL
    _SHARED_INDICATOR_CACHE = indicator_cache
    _SHARED_SYMBOL = symbol
    _SHARED_MAIN_INTERVAL = main_interval


def _vectorized_backtest_worker(params):
    """
    🔥 v2.12 向量化回测工作函数：
    - 从全局共享内存读取数据（零拷贝）
    - 使用 numpy 数组索引替代 DataFrame.iloc 切片
    - 仅传递轻量 params dict，不传递 DataFrame
    
    Args:
        params: 参数字典（轻量，仅几十字节）
    
    Returns:
        Tuple: (params, sharpe, final_balance, max_dd, win_rate, pl_ratio, trades)
    """
    global _SHARED_INDICATOR_CACHE, _SHARED_SYMBOL, _SHARED_MAIN_INTERVAL
    symbol = _SHARED_SYMBOL
    main_interval = _SHARED_MAIN_INTERVAL
    
    try:
        ema_trend = params.get('EMA_TREND')
        if ema_trend not in _SHARED_INDICATOR_CACHE:
            return (params, -999.0, 0.0, 0.0, 0.0, 0.0, 0)
        
        cached_data = _SHARED_INDICATOR_CACHE[ema_trend]
        if main_interval not in cached_data:
            return (params, -999.0, 0.0, 0.0, 0.0, 0.0, 0)
        
        # 🔥 v2.12 核心：直接使用 numpy 数组，零 DataFrame 开销
        arrays = cached_data[main_interval]
        n = len(arrays['close'])
        
        # 构建配置沙盒
        backtest_config = SYSTEM_CONFIG.copy()
        backtest_config["ASSET_WEIGHTS"] = {symbol: 1.0}
        for key, value in params.items():
            backtest_config[key] = value
        
        # 🔥 v2.12：从 numpy 数组重建轻量 DataFrame（仅一次，用于 generate_trading_signals）
        df_main = pd.DataFrame(arrays)
        
        # 构建辅助周期数据
        local_mtf_data = {main_interval: df_main}
        for interval, arr_data in cached_data.items():
            if interval != main_interval:
                local_mtf_data[interval] = pd.DataFrame(arr_data)
        
        # 🔥 v2.12 向量化：预提取 numpy 数组用于止损检测（避免 iloc 开销）
        np_open = arrays['open'] if isinstance(arrays['open'], np.ndarray) else np.array(arrays['open'])
        np_high = arrays['high'] if isinstance(arrays['high'], np.ndarray) else np.array(arrays['high'])
        np_low = arrays['low'] if isinstance(arrays['low'], np.ndarray) else np.array(arrays['low'])
        np_close = arrays['close'] if isinstance(arrays['close'], np.ndarray) else np.array(arrays['close'])
        np_atr = arrays.get('ATR')
        if np_atr is not None and not isinstance(np_atr, np.ndarray):
            np_atr = np.array(np_atr)
        np_ts = arrays.get('timestamp')
        if np_ts is not None and not isinstance(np_ts, np.ndarray):
            np_ts = np.array(np_ts)
        
        # 初始化回测状态
        initial_balance = 10000.0
        current_balance = initial_balance
        peak_balance = initial_balance
        max_drawdown = 0.0
        positions = []
        trade_history = []
        
        FEE_RATE = 0.0004
        SLIPPAGE = 0.0006 if main_interval == '1h' else 0.0005
        
        # 逐根 K 线回放（使用 numpy 索引替代 iloc）
        for i in range(100, n - 1):
            # 🔥 v2.12：使用视图切片传递给信号函数
            current_df = df_main.iloc[:i+1]
            current_ts = np_ts[i] if np_ts is not None else None
            
            # MTF 时间同步
            synced_mtf_data = {}
            for interval, mtf_df in local_mtf_data.items():
                if interval == main_interval:
                    synced_mtf_data[interval] = current_df
                elif current_ts is not None:
                    synced_mtf_data[interval] = mtf_df[mtf_df['timestamp'] <= current_ts]
                else:
                    synced_mtf_data[interval] = mtf_df
            
            signals = generate_trading_signals(
                current_df, symbol, client=None,
                custom_config=backtest_config, mtf_data=synced_mtf_data
            )
            
            if signals is None or not signals.get('signals'):
                continue
            
            # 🔥 v2.12：numpy 直接索引，O(1) 访问
            execution_price = np_open[i+1]
            
            for sig in signals['signals']:
                signal_type = sig['type']
                action = sig['action']
                
                if action.startswith('EXIT'):
                    pos_type = 'LONG' if action == 'EXIT_LONG' else 'SHORT'
                    for pos in positions[:]:
                        if pos['type'] == pos_type:
                            if pos_type == 'LONG':
                                pnl_ratio = (execution_price - pos['entry']) / pos['entry']
                            else:
                                pnl_ratio = (pos['entry'] - execution_price) / pos['entry']
                            pnl_ratio -= (FEE_RATE + SLIPPAGE)
                            pnl = pos['size'] * pnl_ratio
                            current_balance += pnl
                            trade_history.append({
                                'type': pos_type, 'entry': pos['entry'],
                                'exit': execution_price, 'pnl': pnl, 'pnl_ratio': pnl_ratio
                            })
                            positions.remove(pos)
                            break
                
                elif action == 'ENTRY':
                    pos_type = 'LONG' if signal_type == 'BUY' else 'SHORT'
                    max_concurrent = backtest_config.get("MAX_CONCURRENT_TRADES_PER_SYMBOL", 5)
                    if len([p for p in positions if p['type'] == pos_type]) >= max_concurrent:
                        continue
                    
                    risk_ratio = backtest_config.get('RISK_RATIO', 0.05)
                    leverage = backtest_config.get('LEVERAGE', 20.0)
                    position_size = (current_balance * risk_ratio) * leverage
                    
                    # 🔥 v2.12：numpy 直接索引获取 ATR
                    current_atr = float(np_atr[i]) if np_atr is not None and i < len(np_atr) else 0.0
                    atr_mult = backtest_config.get('ATR_MULT', 2.3)
                    
                    if pos_type == 'LONG':
                        sl_price = execution_price - (current_atr * atr_mult)
                    else:
                        sl_price = execution_price + (current_atr * atr_mult)
                    
                    positions.append({
                        'type': pos_type, 'entry': execution_price,
                        'size': position_size, 'sl': sl_price,
                        'atr': current_atr,  # 🔥 存储开仓时的ATR（与实盘一致）
                        'sl_stage': 1  # 🔥 v7.0: 初始止损阶段
                    })
            
            # 🔥 v2.12：numpy 直接索引进行止损检测
            next_low = np_low[i+1]
            next_high = np_high[i+1]
            
            # 🔥 v7.0 三阶段动态止损仿真（状态不可逆 + TSL 持续追踪）
            stage_a_profit = backtest_config.get('STAGE_A_PROFIT_MULT', 1.0)
            stage_a_sl = backtest_config.get('STAGE_A_SL_MULT', 0.5)
            stage_b_profit = backtest_config.get('STAGE_B_PROFIT_MULT', 1.8)
            stage_b_offset = backtest_config.get('STAGE_B_SL_OFFSET', 0.001)
            tsl_trigger = backtest_config.get('TSL_TRIGGER_MULT', 2.5)
            tsl_callback = backtest_config.get('TSL_CALLBACK_MULT', 2.5)
            
            for pos in positions:
                pos_atr = pos.get('atr', 0)
                if pos_atr <= 0:
                    continue
                entry = pos['entry']
                current_sl = pos['sl']
                current_stage = pos.get('sl_stage', 1)  # 🔥 v7.0: 获取当前阶段
                
                if pos['type'] == 'LONG':
                    float_profit = next_high - entry
                else:
                    float_profit = entry - next_low
                
                new_sl = current_sl
                new_stage = current_stage
                
                # 🔥 v7.0: 按优先级检测，状态不可逆转（3 -> 2B -> 2A）
                # Stage 3: TSL 收割（最高优先级）
                if current_stage >= 3 or float_profit >= tsl_trigger * pos_atr:
                    if pos['type'] == 'LONG':
                        # 🔥 v7.0: 每根 K 线都更新 max_seen_price
                        pos['max_seen_price'] = max(pos.get('max_seen_price', entry), next_high)
                        tsl_sl = pos['max_seen_price'] - (tsl_callback * pos_atr)
                        if tsl_sl > current_sl:
                            new_sl = tsl_sl
                            new_stage = 3
                    else:
                        pos['min_seen_price'] = min(pos.get('min_seen_price', entry), next_low)
                        tsl_sl = pos['min_seen_price'] + (tsl_callback * pos_atr)
                        if tsl_sl < current_sl:
                            new_sl = tsl_sl
                            new_stage = 3
                # Stage 2B: 智能保本（次优先级，仅当未进入 Stage 3）
                elif current_stage >= 2 or (current_stage < 3 and float_profit >= stage_b_profit * pos_atr):
                    if pos['type'] == 'LONG':
                        be_sl = entry * (1 + stage_b_offset)
                        if be_sl > current_sl:
                            new_sl = be_sl
                            new_stage = max(2, current_stage)
                    else:
                        be_sl = entry * (1 - stage_b_offset)
                        if be_sl < current_sl:
                            new_sl = be_sl
                            new_stage = max(2, current_stage)
                # Stage 2A: 风险减半（最低优先级，仅当未进入 Stage 2B/3）
                elif current_stage < 2 and float_profit >= stage_a_profit * pos_atr:
                    if pos['type'] == 'LONG':
                        half_sl = entry - (stage_a_sl * pos_atr)
                        if half_sl > current_sl:
                            new_sl = half_sl
                            new_stage = max(1, current_stage)
                    else:
                        half_sl = entry + (stage_a_sl * pos_atr)
                        if half_sl < current_sl:
                            new_sl = half_sl
                            new_stage = max(1, current_stage)
                
                if new_sl != current_sl:
                    pos['sl'] = new_sl
                    pos['sl_stage'] = new_stage  # 🔥 v7.0: 更新阶段状态
            
            for pos in positions[:]:
                sl = pos.get('sl')
                if sl is None:
                    continue
                hit_sl = False
                if pos['type'] == 'LONG' and next_low <= sl:
                    hit_sl = True
                    exit_price = sl
                elif pos['type'] == 'SHORT' and next_high >= sl:
                    hit_sl = True
                    exit_price = sl
                if hit_sl:
                    if pos['type'] == 'LONG':
                        pnl_ratio = (exit_price - pos['entry']) / pos['entry']
                    else:
                        pnl_ratio = (pos['entry'] - exit_price) / pos['entry']
                    pnl_ratio -= (FEE_RATE + SLIPPAGE)
                    pnl = pos['size'] * pnl_ratio
                    current_balance += pnl
                    trade_history.append({
                        'type': pos['type'], 'entry': pos['entry'],
                        'exit': exit_price, 'pnl': pnl, 'pnl_ratio': pnl_ratio,
                        'exit_reason': 'HARD_SL'
                    })
                    positions.remove(pos)
            
            if current_balance > peak_balance:
                peak_balance = current_balance
            drawdown = (peak_balance - current_balance) / peak_balance
            if drawdown > max_drawdown:
                max_drawdown = drawdown
        
        # 强制平仓
        final_price = np_close[-1]
        for pos in positions:
            if pos['type'] == 'LONG':
                pnl_ratio = (final_price - pos['entry']) / pos['entry']
            else:
                pnl_ratio = (pos['entry'] - final_price) / pos['entry']
            pnl_ratio -= (FEE_RATE + SLIPPAGE)
            pnl = pos['size'] * pnl_ratio
            current_balance += pnl
            trade_history.append({
                'type': pos['type'], 'entry': pos['entry'],
                'exit': float(final_price), 'pnl': pnl, 'pnl_ratio': pnl_ratio
            })
        
        if len(trade_history) == 0:
            return (params, -999.0, 0.0, 0.0, 0.0, 0.0, 0)
        
        returns = np.array([t['pnl_ratio'] for t in trade_history])
        sharpe_ratio = 0.0
        if len(returns) >= 2:
            mean_r = np.mean(returns)
            std_r = np.std(returns)
            sharpe_ratio = (mean_r / std_r * np.sqrt(252)) if std_r > 0 else 0.0
        
        pnls = np.array([t['pnl'] for t in trade_history])
        wins = pnls[pnls > 0]
        losses_arr = pnls[pnls < 0]
        win_rate = len(wins) / len(trade_history)
        avg_win = np.mean(wins) if len(wins) > 0 else 0.0
        avg_loss = abs(np.mean(losses_arr)) if len(losses_arr) > 0 else 1.0
        profit_loss_ratio = avg_win / avg_loss if avg_loss > 0 else 0.0
        
        return (params, float(sharpe_ratio), float(current_balance), float(max_drawdown),
                float(win_rate), float(profit_loss_ratio), len(trade_history))
    
    except Exception as e:
        return (params, -999.0, 0.0, 0.0, 0.0, 0.0, 0)


# 🔥 v2.11 二级缓存：静态工作函数（用于进程池 - 兼容保留）
def _parallel_backtest_worker(args_tuple):
    """
    🔥 v2.11 二级缓存优化：直接使用预计算的指标数据
    
    Args:
        args_tuple: (params, symbol, main_interval, indicator_cache_dict)
    
    Returns:
        Tuple: (params, sharpe, final_balance, max_dd, win_rate, pl_ratio, trades)
    """
    params, symbol, main_interval, indicator_cache_dict = args_tuple
    
    try:
        # 🔥 v2.11 关键优化：从缓存中获取已计算好的指标数据
        # indicator_cache_dict 结构: {ema_value: {interval: df_dict}}
        ema_trend = params.get('EMA_TREND')
        if ema_trend not in indicator_cache_dict:
            return (params, -999.0, 0.0, 0.0, 0.0, 0.0, 0)
        
        # 重建 mtf_data（从序列化的字典恢复 DataFrame）
        cached_data = indicator_cache_dict[ema_trend]
        local_mtf_data = {interval: pd.DataFrame(data) for interval, data in cached_data.items()}
        
        # 获取主周期数据
        if main_interval not in local_mtf_data:
            return (params, -999.0, 0.0, 0.0, 0.0, 0.0, 0)
        
        df_main = local_mtf_data[main_interval]
        
        # 配置沙盒（仅用于非指标参数）
        backtest_config = SYSTEM_CONFIG.copy()
        backtest_config["ASSET_WEIGHTS"] = {symbol: 1.0}
        
        # 注入寻优参数
        for key, value in params.items():
            backtest_config[key] = value
        
        # 初始化回测状态
        initial_balance = 10000.0
        current_balance = initial_balance
        peak_balance = initial_balance
        max_drawdown = 0.0
        positions = []
        trade_history = []
        
        FEE_RATE = 0.0004
        SLIPPAGE = 0.0006 if main_interval == '1h' else 0.0005
        
        # 逐根 K 线回放
        for i in range(100, len(df_main) - 1):
            current_df = df_main.iloc[:i+1]
            current_ts = df_main.iloc[i]['timestamp']
            
            synced_mtf_data = {}
            for interval, mtf_df in local_mtf_data.items():
                if interval == main_interval:
                    synced_mtf_data[interval] = current_df
                else:
                    synced_mtf_data[interval] = mtf_df[mtf_df['timestamp'] <= current_ts]
            
            signals = generate_trading_signals(
                current_df,
                symbol,
                client=None,
                custom_config=backtest_config,
                mtf_data=synced_mtf_data
            )
            
            if signals is None or not signals.get('signals'):
                continue
            
            execution_price = df_main.iloc[i+1]['open']
            
            for sig in signals['signals']:
                signal_type = sig['type']
                action = sig['action']
                
                if action.startswith('EXIT'):
                    pos_type = 'LONG' if action == 'EXIT_LONG' else 'SHORT'
                    for pos in positions[:]:
                        if pos['type'] == pos_type:
                            if pos_type == 'LONG':
                                pnl_ratio = (execution_price - pos['entry']) / pos['entry']
                            else:
                                pnl_ratio = (pos['entry'] - execution_price) / pos['entry']
                            pnl_ratio -= (FEE_RATE + SLIPPAGE)
                            pnl = pos['size'] * pnl_ratio
                            current_balance += pnl
                            trade_history.append({
                                'type': pos_type,
                                'entry': pos['entry'],
                                'exit': execution_price,
                                'pnl': pnl,
                                'pnl_ratio': pnl_ratio
                            })
                            positions.remove(pos)
                            break
                
                elif action == 'ENTRY':
                    pos_type = 'LONG' if signal_type == 'BUY' else 'SHORT'
                    max_concurrent = backtest_config.get("MAX_CONCURRENT_TRADES_PER_SYMBOL", 5)
                    existing_trades = [p for p in positions if p['type'] == pos_type]
                    if len(existing_trades) >= max_concurrent:
                        continue
                    
                    risk_ratio = backtest_config.get('RISK_RATIO', 0.05)
                    leverage = backtest_config.get('LEVERAGE', 20.0)
                    position_size = (current_balance * risk_ratio) * leverage
                    
                    current_atr = current_df.iloc[-1].get('ATR', 0)
                    atr_mult = backtest_config.get('ATR_MULT', 2.3)
                    
                    if pos_type == 'LONG':
                        sl_price = execution_price - (current_atr * atr_mult)
                    else:
                        sl_price = execution_price + (current_atr * atr_mult)
                    
                    positions.append({
                        'type': pos_type,
                        'entry': execution_price,
                        'size': position_size,
                        'sl': sl_price,
                        'atr': current_atr,  # 🔥 存储开仓时的ATR（与实盘一致）
                        'sl_stage': 1,  # 🔥 v7.0: 初始止损阶段
                        'timestamp': current_df.iloc[-1]['timestamp']
                    })
            
            # 硬止损检测
            next_bar = df_main.iloc[i+1]
            
            # 🔥 v7.0 三阶段动态止损仿真（状态不可逆 + TSL 持续追踪）
            stage_a_profit = backtest_config.get('STAGE_A_PROFIT_MULT', 1.0)
            stage_a_sl = backtest_config.get('STAGE_A_SL_MULT', 0.5)
            stage_b_profit = backtest_config.get('STAGE_B_PROFIT_MULT', 1.8)
            stage_b_offset = backtest_config.get('STAGE_B_SL_OFFSET', 0.001)
            tsl_trigger = backtest_config.get('TSL_TRIGGER_MULT', 2.5)
            tsl_callback = backtest_config.get('TSL_CALLBACK_MULT', 2.5)
            
            for pos in positions:
                pos_atr = pos.get('atr', 0)
                if pos_atr <= 0:
                    continue
                entry = pos['entry']
                current_sl = pos['sl']
                current_stage = pos.get('sl_stage', 1)  # 🔥 v7.0: 获取当前阶段
                
                if pos['type'] == 'LONG':
                    float_profit = next_bar['high'] - entry
                else:
                    float_profit = entry - next_bar['low']
                
                new_sl = current_sl
                new_stage = current_stage
                
                # 🔥 v7.0: 按优先级检测，状态不可逆转（3 -> 2B -> 2A）
                # Stage 3: TSL 收割（最高优先级）
                if current_stage >= 3 or float_profit >= tsl_trigger * pos_atr:
                    if pos['type'] == 'LONG':
                        # 🔥 v7.0: 每根 K 线都更新 max_seen_price
                        pos['max_seen_price'] = max(pos.get('max_seen_price', entry), next_bar['high'])
                        tsl_sl = pos['max_seen_price'] - (tsl_callback * pos_atr)
                        if tsl_sl > current_sl:
                            new_sl = tsl_sl
                            new_stage = 3
                    else:
                        pos['min_seen_price'] = min(pos.get('min_seen_price', entry), next_bar['low'])
                        tsl_sl = pos['min_seen_price'] + (tsl_callback * pos_atr)
                        if tsl_sl < current_sl:
                            new_sl = tsl_sl
                            new_stage = 3
                # Stage 2B: 智能保本（次优先级，仅当未进入 Stage 3）
                elif current_stage >= 2 or (current_stage < 3 and float_profit >= stage_b_profit * pos_atr):
                    if pos['type'] == 'LONG':
                        be_sl = entry * (1 + stage_b_offset)
                        if be_sl > current_sl:
                            new_sl = be_sl
                            new_stage = max(2, current_stage)
                    else:
                        be_sl = entry * (1 - stage_b_offset)
                        if be_sl < current_sl:
                            new_sl = be_sl
                            new_stage = max(2, current_stage)
                # Stage 2A: 风险减半（最低优先级，仅当未进入 Stage 2B/3）
                elif current_stage < 2 and float_profit >= stage_a_profit * pos_atr:
                    if pos['type'] == 'LONG':
                        half_sl = entry - (stage_a_sl * pos_atr)
                        if half_sl > current_sl:
                            new_sl = half_sl
                            new_stage = max(1, current_stage)
                    else:
                        half_sl = entry + (stage_a_sl * pos_atr)
                        if half_sl < current_sl:
                            new_sl = half_sl
                            new_stage = max(1, current_stage)
                
                if new_sl != current_sl:
                    pos['sl'] = new_sl
                    pos['sl_stage'] = new_stage  # 🔥 v7.0: 更新阶段状态
            
            for pos in positions[:]:
                sl = pos.get('sl')
                if sl is None:
                    continue
                
                hit_sl = False
                if pos['type'] == 'LONG' and next_bar['low'] <= sl:
                    hit_sl = True
                    exit_price = sl
                elif pos['type'] == 'SHORT' and next_bar['high'] >= sl:
                    hit_sl = True
                    exit_price = sl
                
                if hit_sl:
                    if pos['type'] == 'LONG':
                        pnl_ratio = (exit_price - pos['entry']) / pos['entry']
                    else:
                        pnl_ratio = (pos['entry'] - exit_price) / pos['entry']
                    pnl_ratio -= (FEE_RATE + SLIPPAGE)
                    pnl = pos['size'] * pnl_ratio
                    current_balance += pnl
                    trade_history.append({
                        'type': pos['type'],
                        'entry': pos['entry'],
                        'exit': exit_price,
                        'pnl': pnl,
                        'pnl_ratio': pnl_ratio,
                        'exit_reason': 'HARD_SL'
                    })
                    positions.remove(pos)
            
            # 更新最大回撤
            if current_balance > peak_balance:
                peak_balance = current_balance
            drawdown = (peak_balance - current_balance) / peak_balance
            if drawdown > max_drawdown:
                max_drawdown = drawdown
        
        # 强制平仓
        final_price = df_main.iloc[-1]['close']
        for pos in positions:
            if pos['type'] == 'LONG':
                pnl_ratio = (final_price - pos['entry']) / pos['entry']
            else:
                pnl_ratio = (pos['entry'] - final_price) / pos['entry']
            pnl_ratio -= (FEE_RATE + SLIPPAGE)
            pnl = pos['size'] * pnl_ratio
            current_balance += pnl
            trade_history.append({
                'type': pos['type'],
                'entry': pos['entry'],
                'exit': final_price,
                'pnl': pnl,
                'pnl_ratio': pnl_ratio
            })
        
        # 计算绩效指标
        if len(trade_history) == 0:
            return (params, -999.0, 0.0, 0.0, 0.0, 0.0, 0)
        
        returns = [t['pnl_ratio'] for t in trade_history]
        if len(returns) < 2:
            sharpe_ratio = 0.0
        else:
            mean_return = np.mean(returns)
            std_return = np.std(returns)
            sharpe_ratio = (mean_return / std_return * np.sqrt(252)) if std_return > 0 else 0.0
        
        wins = [t for t in trade_history if t['pnl'] > 0]
        losses = [t for t in trade_history if t['pnl'] < 0]
        win_rate = len(wins) / len(trade_history) if trade_history else 0.0
        avg_win = np.mean([t['pnl'] for t in wins]) if wins else 0.0
        avg_loss = abs(np.mean([t['pnl'] for t in losses])) if losses else 1.0
        profit_loss_ratio = avg_win / avg_loss if avg_loss > 0 else 0.0
        final_balance = current_balance
        total_trades = len(trade_history)
        
        return (params, sharpe_ratio, final_balance, max_drawdown, win_rate, profit_loss_ratio, total_trades)
        
    except Exception as e:
        return (params, -999.0, 0.0, 0.0, 0.0, 0.0, 0)


class BacktestWorker:
    """回测工作器 v2.6 - 满血隔离模式"""
    
    def __init__(self, client: BinanceClient, symbol: str = "BTCUSDT"):
        self.client = client
        self.symbol = symbol
        self.mtf_data = {}  # 多周期数据缓存
        
    def _fetch_multi_timeframe_data(self, lookback_days: int = 30, main_interval: str = '1h') -> bool:
        """
        🔥 v2.9 按需动态抓取：仅拉取主周期 + config 中 HIGHER_INTERVAL 指定的高周期
        物理屏蔽：严禁拉取主周期之外的非必要低周期数据
        
        Args:
            lookback_days: 回溯天数
            main_interval: 主周期（'15m'、'1h' 或 '4h'）
        
        Returns:
            bool: 是否成功
        """
        try:
            print(f"\n📊 [v2.9 按需抓取] 正在拉取原始数据...")
            
            # 🔥 v2.9 核心逻辑：动态构建需要拉取的周期列表
            intervals_to_fetch = [main_interval]  # 始终拉取主周期
            
            # 🔥 致命修复：无论高周期是否开启，只要开启日线过滤，就必须拉取 1d
            use_daily_filter = SYSTEM_CONFIG.get('USE_DAILY_FILTER', True)
            if use_daily_filter and '1d' not in intervals_to_fetch:
                intervals_to_fetch.append('1d')
            
            # 检查是否启用高周期过滤
            use_higher_tf = SYSTEM_CONFIG.get('USE_HIGHER_TF_FILTER', False)
            if use_higher_tf:
                higher_interval = SYSTEM_CONFIG.get('HIGHER_INTERVAL', '4h')
                # 物理屏蔽：只有当高周期不等于主周期时才添加
                if higher_interval != main_interval and higher_interval not in intervals_to_fetch:
                    intervals_to_fetch.append(higher_interval)
                    print(f"   🔍 检测到 USE_HIGHER_TF_FILTER=True，将额外拉取 {higher_interval} 数据")
            
            # K 线数量映射表
            interval_limits = {
                '15m': lookback_days * 96,
                '1h': lookback_days * 24,
                '4h': lookback_days * 6,
                '1d': lookback_days * 1    # 🔥 致命修复：必须加入1d，否则请求量将放大24倍！
            }
            
            interval_names = {
                '15m': '15分钟',
                '1h': '1小时',
                '4h': '4小时'
            }
            
            print(f"   📋 拉取计划: {intervals_to_fetch}")
            
            # 🔥 v2.9：仅拉取计划中的周期
            for interval in intervals_to_fetch:
                limit = interval_limits.get(interval, lookback_days * 24)
                name = interval_names.get(interval, interval)
                
                print(f"   拉取 {name} 数据...")
                df = get_historical_klines(
                    self.client,
                    self.symbol,
                    interval,
                    limit=limit
                )
                
                if df is None or len(df) < 100:
                    print(f"   ❌ {name} 数据不足")
                    return False
                
                # 保存原始数据（不计算指标）
                self.mtf_data[interval] = df
                print(f"   ✅ {name}: {len(df)} 根K线（原始数据）")
            
            print(f"✅ 按需数据拉取完成（共 {len(intervals_to_fetch)} 个周期）")
            return True
            
        except Exception as e:
            print(f"❌ 数据拉取失败: {e}")
            return False
    
    def run_single_backtest(
        self,
        params: Dict,
        main_interval: str = '1h',
        lookback_days: int = 30,
        indicator_cache: Optional[Dict] = None
    ) -> Tuple[float, float, float, float, float, int]:
        """
        🔥 v2.11 二级缓存优化：支持使用预计算的指标缓存
        
        运行单次回测（配置隔离模式）
        
        Args:
            params: 参数字典（如 {'ADX_THR': 14, 'EMA_TREND': 89}）
            main_interval: 主周期
            lookback_days: 回溯天数
            indicator_cache: 可选的指标缓存 {ema_value: {interval: df_with_indicators}}
        
        Returns:
            Tuple: (sharpe_ratio, final_balance, max_drawdown, win_rate, profit_loss_ratio, total_trades)
                  失败时返回 (-999.0, 0.0, 0.0, 0.0, 0.0, 0)
        """
        try:
            # 🔥 配置沙盒：创建副本，不污染全局配置
            backtest_config = SYSTEM_CONFIG.copy()
            
            # 🔥 解除资金封印：强制全仓配置（让回测参数真正发挥作用）
            backtest_config["ASSET_WEIGHTS"] = {self.symbol: 1.0}
            
            # 注入寻优参数
            for key, value in params.items():
                backtest_config[key] = value
            
            # 获取主周期数据
            if main_interval not in self.mtf_data:
                print(f"   ❌ 缺少 {main_interval} 数据")
                return (-999.0, 0.0, 0.0, 0.0, 0.0, 0)
            
            # 🔥 v2.11 二级缓存：优先使用预计算的指标数据
            if indicator_cache is not None:
                ema_trend = params.get('EMA_TREND')
                if ema_trend in indicator_cache:
                    # 直接使用缓存的指标数据，跳过重复计算
                    local_mtf_data = indicator_cache[ema_trend]
                    df_main = local_mtf_data.get(main_interval)
                    if df_main is None:
                        print(f"   ❌ 缓存中缺少 {main_interval} 数据")
                        return (-999.0, 0.0, 0.0, 0.0, 0.0, 0)
                else:
                    print(f"   ⚠️ 缓存中未找到 EMA_TREND={ema_trend}，回退到实时计算")
                    indicator_cache = None  # 回退到实时计算模式
            
            # 🔥 回退模式：实时计算指标（兼容性保留）
            if indicator_cache is None:
                # 根据网格参数动态计算当前组合的指标
                df_main = calculate_indicators(
                    self.mtf_data[main_interval].copy(),
                    force_recalc=True,
                    custom_config=backtest_config
                )
                if df_main is None:
                    print(f"   ❌ 主周期指标计算失败 (params={params})")
                    return (-999.0, 0.0, 0.0, 0.0, 0.0, 0)
                
                # 仅对 self.mtf_data 中实际存在的辅助周期重算指标
                local_mtf_data = {}
                for interval, raw_df in self.mtf_data.items():
                    if interval == main_interval:
                        local_mtf_data[interval] = df_main  # 已计算过
                    else:
                        # 仅对实际拉取的辅助周期计算指标
                        df_aux = calculate_indicators(
                            raw_df.copy(),
                            force_recalc=True,
                            custom_config=backtest_config
                        )
                        local_mtf_data[interval] = df_aux if df_aux is not None else raw_df
            
            # 初始化回测状态
            initial_balance = 10000.0
            current_balance = initial_balance
            peak_balance = initial_balance
            max_drawdown = 0.0
            
            positions = []  # 当前持仓
            trade_history = []  # 交易历史
            
            # 成本参数
            FEE_RATE = 0.0004  # 0.04% 手续费
            SLIPPAGE = 0.0006 if main_interval == '1h' else 0.0005
            
            # 🔥 未来函数消除：逐根 K 线回放
            for i in range(100, len(df_main) - 1):  # 留出指标计算空间 + 预留下一根 K 线
                # 🔥 v2.7 性能优化：使用视图切片替代 .copy()
                # generate_trading_signals 只读取 df.iloc[-1/-2/-3/-4] 等，不修改数据
                # 视图切片避免每根 K 线都进行 O(i) 的内存分配和拷贝
                current_df = df_main.iloc[:i+1]
                
                # 🔥 v2.8 关键修复：获取当前主周期时间戳，同步切片所有辅助周期
                # 确保信号函数只能看到"过去"的数据，彻底消除未来函数
                current_ts = df_main.iloc[i]['timestamp']
                synced_mtf_data = {}
                for interval, mtf_df in local_mtf_data.items():
                    if interval == main_interval:
                        synced_mtf_data[interval] = current_df  # 主周期已切片
                    else:
                        synced_mtf_data[interval] = mtf_df[mtf_df['timestamp'] <= current_ts]
                
                # 生成交易信号（使用配置沙盒 + 时间同步的 MTF 数据）
                signals = generate_trading_signals(
                    current_df,
                    self.symbol,
                    client=None,  # 回测模式不需要 API
                    custom_config=backtest_config,
                    mtf_data=synced_mtf_data  # 🔥 v2.8：时间同步切片，消除未来函数
                )
                
                if signals is None or not signals.get('signals'):
                    continue
                
                # 信号在第 i 根 K 线收盘产生
                signal_price = current_df.iloc[-1]['close']
                
                # 🔥 真实成交仿真：使用第 i+1 根 K 线的 open 价格
                execution_price = df_main.iloc[i+1]['open']
                
                # 处理信号
                for sig in signals['signals']:
                    signal_type = sig['type']  # BUY or SELL
                    action = sig['action']  # ENTRY, EXIT_LONG, EXIT_SHORT
                    
                    # 平仓逻辑
                    if action.startswith('EXIT'):
                        pos_type = 'LONG' if action == 'EXIT_LONG' else 'SHORT'
                        
                        # 查找匹配的持仓
                        for pos in positions[:]:
                            if pos['type'] == pos_type:
                                # 计算盈亏
                                if pos_type == 'LONG':
                                    pnl_ratio = (execution_price - pos['entry']) / pos['entry']
                                else:
                                    pnl_ratio = (pos['entry'] - execution_price) / pos['entry']
                                
                                # 扣除成本
                                pnl_ratio -= (FEE_RATE + SLIPPAGE)
                                
                                # 更新余额
                                pnl = pos['size'] * pnl_ratio
                                current_balance += pnl
                                
                                # 记录交易
                                trade_history.append({
                                    'type': pos_type,
                                    'entry': pos['entry'],
                                    'exit': execution_price,
                                    'pnl': pnl,
                                    'pnl_ratio': pnl_ratio
                                })
                                
                                # 移除持仓
                                positions.remove(pos)
                                break
                    
                    # 开仓逻辑
                    elif action == 'ENTRY':
                        pos_type = 'LONG' if signal_type == 'BUY' else 'SHORT'
                        
                        # 🔥 v2.8 Pyramiding：允许根据配置进行多笔加仓
                        max_concurrent = backtest_config.get("MAX_CONCURRENT_TRADES_PER_SYMBOL", 5)
                        existing_trades = [p for p in positions if p['type'] == pos_type]
                        if len(existing_trades) >= max_concurrent:
                            continue
                        
                        # 🔥 修复：计算带有杠杆的真实头寸价值 (真实购买力 = 动用保证金 * 杠杆)
                        risk_ratio = backtest_config.get('RISK_RATIO', 0.05)
                        leverage = backtest_config.get('LEVERAGE', 20.0)
                        position_size = (current_balance * risk_ratio) * leverage
                        
                        # 获取当前 ATR 计算止损
                        current_atr = current_df.iloc[-1].get('ATR', 0)
                        atr_mult = backtest_config.get('ATR_MULT', 2.3)
                        
                        if pos_type == 'LONG':
                            sl_price = execution_price - (current_atr * atr_mult)
                        else:
                            sl_price = execution_price + (current_atr * atr_mult)
                        
                        # 开仓并记录 sl
                        positions.append({
                            'type': pos_type,
                            'entry': execution_price,
                            'size': position_size,
                            'sl': sl_price,  # 🔥 新增止损价记录
                            'atr': current_atr,  # 🔥 存储开仓时的ATR（与实盘一致）
                            'sl_stage': 1,  # 🔥 v7.0: 初始止损阶段
                            'timestamp': current_df.iloc[-1]['timestamp']
                        })
                
                # 🔥 逐棒硬止损检测：用下一根 K 线的 high/low 判断是否触及止损
                next_bar = df_main.iloc[i+1]
                
                # 🔥 v3.0 三阶段动态止损仿真（与实盘 process_trading_signals 一致）
                stage_a_profit = backtest_config.get('STAGE_A_PROFIT_MULT', 1.0)
                stage_a_sl = backtest_config.get('STAGE_A_SL_MULT', 0.5)
                stage_b_profit = backtest_config.get('STAGE_B_PROFIT_MULT', 1.8)
                stage_b_offset = backtest_config.get('STAGE_B_SL_OFFSET', 0.001)
                tsl_trigger = backtest_config.get('TSL_TRIGGER_MULT', 2.5)
                tsl_callback = backtest_config.get('TSL_CALLBACK_MULT', 2.5)
                
                for pos in positions:
                    pos_atr = pos.get('atr', 0)
                    if pos_atr <= 0:
                        continue
                    entry = pos['entry']
                    current_sl = pos['sl']
                    
                    if pos['type'] == 'LONG':
                        float_profit = next_bar['high'] - entry
                    else:
                        float_profit = entry - next_bar['low']
                    
                    new_sl = current_sl
                    
                    # Stage 3: TSL 收割
                    if float_profit >= tsl_trigger * pos_atr:
                        if pos['type'] == 'LONG':
                            pos['max_seen_price'] = max(pos.get('max_seen_price', entry), next_bar['high'])
                            tsl_sl = pos['max_seen_price'] - (tsl_callback * pos_atr)
                            if tsl_sl > current_sl:
                                new_sl = tsl_sl
                        else:
                            pos['min_seen_price'] = min(pos.get('min_seen_price', entry), next_bar['low'])
                            tsl_sl = pos['min_seen_price'] + (tsl_callback * pos_atr)
                            if tsl_sl < current_sl:
                                new_sl = tsl_sl
                    # Stage 2B: 智能保本
                    elif float_profit >= stage_b_profit * pos_atr:
                        if pos['type'] == 'LONG':
                            be_sl = entry * (1 + stage_b_offset)
                            if be_sl > current_sl:
                                new_sl = be_sl
                        else:
                            be_sl = entry * (1 - stage_b_offset)
                            if be_sl < current_sl:
                                new_sl = be_sl
                    # Stage 2A: 风险减半
                    elif float_profit >= stage_a_profit * pos_atr:
                        if pos['type'] == 'LONG':
                            half_sl = entry - (stage_a_sl * pos_atr)
                            if half_sl > current_sl:
                                new_sl = half_sl
                        else:
                            half_sl = entry + (stage_a_sl * pos_atr)
                            if half_sl < current_sl:
                                new_sl = half_sl
                    
                    if new_sl != current_sl:
                        pos['sl'] = new_sl
                
                for pos in positions[:]:
                    sl = pos.get('sl')
                    if sl is None:
                        continue
                    
                    hit_sl = False
                    if pos['type'] == 'LONG' and next_bar['low'] <= sl:
                        hit_sl = True
                        exit_price = sl  # 以止损价成交
                    elif pos['type'] == 'SHORT' and next_bar['high'] >= sl:
                        hit_sl = True
                        exit_price = sl  # 以止损价成交
                    
                    if hit_sl:
                        if pos['type'] == 'LONG':
                            pnl_ratio = (exit_price - pos['entry']) / pos['entry']
                        else:
                            pnl_ratio = (pos['entry'] - exit_price) / pos['entry']
                        
                        pnl_ratio -= (FEE_RATE + SLIPPAGE)
                        pnl = pos['size'] * pnl_ratio
                        current_balance += pnl
                        
                        trade_history.append({
                            'type': pos['type'],
                            'entry': pos['entry'],
                            'exit': exit_price,
                            'pnl': pnl,
                            'pnl_ratio': pnl_ratio,
                            'exit_reason': 'HARD_SL'  # 标记止损出场
                        })
                        
                        positions.remove(pos)
                
                # 更新最大回撤
                if current_balance > peak_balance:
                    peak_balance = current_balance
                
                drawdown = (peak_balance - current_balance) / peak_balance
                if drawdown > max_drawdown:
                    max_drawdown = drawdown
            
            # 强制平掉所有剩余持仓
            final_price = df_main.iloc[-1]['close']
            for pos in positions:
                if pos['type'] == 'LONG':
                    pnl_ratio = (final_price - pos['entry']) / pos['entry']
                else:
                    pnl_ratio = (pos['entry'] - final_price) / pos['entry']
                
                pnl_ratio -= (FEE_RATE + SLIPPAGE)
                pnl = pos['size'] * pnl_ratio
                current_balance += pnl
                
                trade_history.append({
                    'type': pos['type'],
                    'entry': pos['entry'],
                    'exit': final_price,
                    'pnl': pnl,
                    'pnl_ratio': pnl_ratio
                })
            
            # 计算绩效指标
            if len(trade_history) == 0:
                print(f"   ⚠️ 无交易记录")
                # 🔥 修复 L515：返回完整元组
                return (-999.0, 0.0, 0.0, 0.0, 0.0, 0)
            
            # 计算夏普比率
            returns = [t['pnl_ratio'] for t in trade_history]
            if len(returns) < 2:
                sharpe_ratio = 0.0
            else:
                mean_return = np.mean(returns)
                std_return = np.std(returns)
                sharpe_ratio = (mean_return / std_return * np.sqrt(252)) if std_return > 0 else 0.0
            
            # 🔥 补全 CSV 导出列：计算 Win_Rate 和 Profit_Loss_Ratio
            wins = [t for t in trade_history if t['pnl'] > 0]
            losses = [t for t in trade_history if t['pnl'] < 0]
            
            win_rate = len(wins) / len(trade_history) if trade_history else 0.0
            
            avg_win = np.mean([t['pnl'] for t in wins]) if wins else 0.0
            avg_loss = abs(np.mean([t['pnl'] for t in losses])) if losses else 1.0
            profit_loss_ratio = avg_win / avg_loss if avg_loss > 0 else 0.0
            
            final_balance = current_balance
            total_trades = len(trade_history)
            
            return (sharpe_ratio, final_balance, max_drawdown, win_rate, profit_loss_ratio, total_trades)
            
        except Exception as e:
            print(f"   ❌ 回测异常: {e}")
            # 🔥 修复：异常时也返回完整元组
            return (-999.0, 0.0, 0.0, 0.0, 0.0, 0)
    
    def run_grid_search(
        self,
        param_grid: Dict[str, List],
        main_interval: str = '1h',
        lookback_days: int = 30,
        output_file: str = 'backtest_results_full.csv',
        use_parallel: bool = True,
        max_workers: Optional[int] = None
    ) -> Dict:
        """
        🔥 v2.15 并行网格搜索最优参数 + 永存与追加逻辑
        
        核心特性：
        1. 唯一性命名：results_SYMBOL_PERIOD_YYYYMMDD_HHMM.csv
        2. 综合报告追加：强制追加到 backtest_comprehensive_report.csv
        3. 智能表头控制：文件存在且大小>0时禁止写入表头
        
        Args:
            param_grid: 参数网格，如 {'ADX_THR': [10, 14, 18], 'EMA_TREND': [55, 89, 144]}
            main_interval: 主周期
            lookback_days: 回溯天数
            output_file: 输出文件名（已废弃，自动生成唯一时间戳文件名）
            use_parallel: 是否使用并行计算（默认 True）
            max_workers: 最大工作进程数（默认为 CPU 核心数）
        
        Returns:
            Dict: 最优参数和结果
        """
        try:
            # 🔥 v2.15 唯一性命名：results_SYMBOL_PERIOD_YYYYMMDD_HHMM.csv
            timestamp_str = datetime.now().strftime('%Y%m%d_%H%M')
            unique_filename = f"results_{self.symbol}_{main_interval}_{timestamp_str}.csv"
            master_log_file = "backtest_comprehensive_report.csv"
            
            print(f"\n📝 本次回测文件: {unique_filename}")
            print(f"📚 综合报告: {master_log_file}")
            
            # 拉取数据
            if not self._fetch_multi_timeframe_data(lookback_days, main_interval):
                return {'success': False, 'message': '数据拉取失败'}
            
            # 生成参数组合
            from itertools import product
            param_names = list(param_grid.keys())
            param_values = list(param_grid.values())
            param_combinations = list(product(*param_values))
            
            total_combinations = len(param_combinations)
            
            # 确定工作进程数
            if max_workers is None:
                max_workers = max(1, multiprocessing.cpu_count() - 1)
            
            print(f"\n🔍 开始网格搜索，共 {total_combinations} 种参数组合")
            if use_parallel:
                print(f"⚡ 并行模式：使用 {max_workers} 个工作进程")
            else:
                print(f"🐌 串行模式：单进程执行")
            print("=" * 60)
            
            # 🔥 v2.11 二级缓存：预计算所有 EMA_TREND 值的指标
            print(f"\n🚀 [v2.11 二级缓存] 开始预计算指标...")
            ema_values = param_grid.get('EMA_TREND', [])
            indicator_cache = {}  # {ema_value: {interval: df_with_indicators}}
            
            for ema_val in ema_values:
                print(f"   计算 EMA_TREND={ema_val} 的指标...")
                
                # 创建临时配置
                temp_config = SYSTEM_CONFIG.copy()
                temp_config['EMA_TREND'] = ema_val
                temp_config["ASSET_WEIGHTS"] = {self.symbol: 1.0}
                
                # 为所有周期计算指标
                ema_cache = {}
                for interval, raw_df in self.mtf_data.items():
                    df_with_indicators = calculate_indicators(
                        raw_df.copy(),
                        force_recalc=True,
                        custom_config=temp_config
                    )
                    if df_with_indicators is not None:
                        ema_cache[interval] = df_with_indicators
                    else:
                        ema_cache[interval] = raw_df
                
                indicator_cache[ema_val] = ema_cache
            
            print(f"✅ 指标预计算完成！共缓存 {len(ema_values)} 组 EMA_TREND 配置")
            print(f"💡 性能提升：从 {total_combinations} 次指标计算降至 {len(ema_values)} 次")
            print("=" * 60)
            
            best_sharpe = -999.0
            best_params = {}
            all_results = []
            
            if use_parallel and total_combinations > 1:
                # 🔥 v2.12 共享内存并行：使用 initializer 注入只读数据
                # 将 DataFrame 转为 dict（numpy 友好格式），仅在 initializer 中传递一次
                print(f"\n⚡ [v2.12 共享内存] 准备进程池数据...")
                shared_cache = {}
                for ema_val, mtf_data in indicator_cache.items():
                    shared_cache[ema_val] = {
                        interval: {col: df[col].values for col in df.columns}
                        for interval, df in mtf_data.items()
                    }
                
                # 🔥 v2.12：仅传递轻量 params，数据通过 initializer 共享
                param_list = [dict(zip(param_names, combo)) for combo in param_combinations]
                
                print(f"   📦 共享缓存大小: {len(shared_cache)} 组 EMA_TREND")
                print(f"   🚀 每个任务仅传递 ~{sys.getsizeof(param_list[0])} 字节参数")
                
                completed = 0
                with ProcessPoolExecutor(
                    max_workers=max_workers,
                    initializer=_init_worker,
                    initargs=(shared_cache, self.symbol, main_interval)
                ) as executor:
                    future_to_params = {
                        executor.submit(_vectorized_backtest_worker, p): p
                        for p in param_list
                    }
                    
                    for future in as_completed(future_to_params):
                        params = future_to_params[future]
                        try:
                            result_tuple = future.result()
                            params_result, sharpe, final_balance, max_dd, win_rate, pl_ratio, trades = result_tuple
                            
                            result = {
                                'Timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                                'Symbol': self.symbol,
                                'Period': main_interval,
                                'Days': lookback_days,
                                'Sharpe_Ratio': sharpe,
                                'Final_Balance': final_balance,
                                'Max_Drawdown': max_dd,
                                'Win_Rate': win_rate,
                                'Profit_Loss_Ratio': pl_ratio,
                                'Total_Trades': trades,
                                **params_result
                            }
                            all_results.append(result)
                            
                            if sharpe > best_sharpe:
                                best_sharpe = sharpe
                                best_params = params_result.copy()
                                print(f"🎯 发现更优参数 | Sharpe={sharpe:.2f} | {params_result}")
                            
                            completed += 1
                            if completed % 10 == 0 or completed == total_combinations:
                                print(f"   进度: {completed}/{total_combinations} ({completed/total_combinations*100:.1f}%)")
                        
                        except Exception as e:
                            print(f"   ⚠️ 任务执行失败: {params} - {e}")
                            completed += 1
            
            else:
                # 🔥 v2.11 串行执行模式（使用二级缓存）
                for idx, combo in enumerate(param_combinations, 1):
                    params = dict(zip(param_names, combo))
                    
                    # 运行回测（使用缓存）
                    sharpe, final_balance, max_dd, win_rate, pl_ratio, trades = self.run_single_backtest(
                        params, main_interval, lookback_days,
                        indicator_cache=indicator_cache  # 🔥 v2.11：传入缓存
                    )
                    
                    # 记录结果
                    result = {
                        'Timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                        'Symbol': self.symbol,
                        'Period': main_interval,
                        'Days': lookback_days,
                        'Sharpe_Ratio': sharpe,
                        'Final_Balance': final_balance,
                        'Max_Drawdown': max_dd,
                        'Win_Rate': win_rate,
                        'Profit_Loss_Ratio': pl_ratio,
                        'Total_Trades': trades,
                        **params
                    }
                    all_results.append(result)
                    
                    # 更新最优结果
                    if sharpe > best_sharpe:
                        best_sharpe = sharpe
                        best_params = params.copy()
                        print(f"🎯 发现更优参数 ({idx}/{total_combinations}) | Sharpe={sharpe:.2f} | {params}")
                    
                    # 进度显示
                    if idx % 10 == 0:
                        print(f"   进度: {idx}/{total_combinations} ({idx/total_combinations*100:.1f}%)")
            
            # 🔥 v2.15 双重保存机制：唯一文件 + 强制追加综合报告
            if all_results:
                # 1️⃣ 保存到唯一时间戳文件（本次回测独立记录）
                with open(unique_filename, 'w', newline='', encoding='utf-8') as f:
                    writer = csv.DictWriter(f, fieldnames=all_results[0].keys())
                    writer.writeheader()
                    writer.writerows(all_results)
                print(f"\n💾 本次回测已保存: {unique_filename}")
                
                # 2️⃣ 强制追加到综合报告（智能表头控制）
                # 逻辑：文件存在且大小>0时，禁止写入表头
                master_exists = os.path.exists(master_log_file)
                master_has_content = master_exists and os.path.getsize(master_log_file) > 0
                
                with open(master_log_file, 'a', newline='', encoding='utf-8') as f:
                    writer = csv.DictWriter(f, fieldnames=all_results[0].keys())
                    # 🔥 核心逻辑：仅在文件不存在或为空时写入表头
                    if not master_has_content:
                        writer.writeheader()
                        print(f"📋 综合报告首次创建，已写入表头")
                    writer.writerows(all_results)
                print(f"📚 结果已追加到综合报告: {master_log_file} (共 {len(all_results)} 条记录)")
            
            print("\n" + "=" * 60)
            print("✅ 回测完成！")
            print("=" * 60)
            print(f"📈 最优夏普比率: {best_sharpe:.4f}")
            print(f"🎯 最优参数:")
            for key, value in best_params.items():
                print(f"   • {key}: {value}")
            
            return {
                'success': True,
                'best_sharpe': best_sharpe,
                'best_params': best_params,
                'all_results': all_results,
                'output_file': unique_filename,  # 🔥 返回唯一文件名
                'master_log': master_log_file
            }
            
        except Exception as e:
            print(f"❌ 网格搜索失败: {e}")
            return {'success': False, 'message': str(e)}


def main():
    """
    主入口 - 编队集群回测 v2.15
    
    🔥 v2.15 新特性：
    1. 唯一性命名：每次回测生成 results_SYMBOL_PERIOD_YYYYMMDD_HHMM.csv
    2. 综合报告追加：强制追加到 backtest_comprehensive_report.csv（智能表头控制）
    3. 多币种循环隔离：每个币种物理清空 shared_cache，严禁数据污染
    4. 多币种识别：--symbol 支持逗号分隔（如 BTCUSDT,ETHUSDT,SOLUSDT）
    5. 自动加载：未指定币种时自动从 SENTRY_CONFIG["WATCH_LIST"] 加载
    """
    parser = argparse.ArgumentParser(description='WJ-BOT 回测引擎 v2.15 - 永存与追加逻辑 + 多币种循环隔离')
    parser.add_argument('--period', type=str, default='1h', choices=['15m', '1h', '4h'],
                       help='回测周期 (默认: 1h)')
    parser.add_argument('--days', type=int, default=30,
                       help='回测天数 (默认: 30)')
    parser.add_argument('--symbol', type=str, default='',
                       help='指定回测的交易对，支持逗号分隔多币种 (如: BTCUSDT,ETHUSDT,SOLUSDT)，留空则自动加载 WATCH_LIST')
    parser.add_argument('--vault', type=str, default='auto', choices=['on', 'off', 'auto'],
                       help='金库开关 (on=强制开启, off=强制关闭, auto=跟随config配置, 默认: auto)')
    
    args = parser.parse_args()
    
    # 🔥 解析金库指令
    if args.vault == 'on':
        vault_status = True
    elif args.vault == 'off':
        vault_status = False
    else:
        vault_status = SYSTEM_CONFIG.get('VAULT_ENABLED', True)
    print(f"🏦 金库状态: {'✅ 启用' if vault_status else '❌ 禁用'} (--vault={args.vault})")
    
    # ═══════════════════════════════════════════════════════════════
    # 🔥 Step 1: 多币种识别逻辑
    # ═══════════════════════════════════════════════════════════════
    symbols_list = []
    
    if args.symbol:
        # 命令行指定：支持逗号分隔
        symbols_list = [s.strip().upper() for s in args.symbol.split(',') if s.strip()]
        print(f"📋 命令行指定币种: {symbols_list}")
    else:
        # 自动加载 WATCH_LIST
        try:
            SENTRY_CONFIG = config_module.SENTRY_CONFIG
            watch_list = SENTRY_CONFIG.get("WATCH_LIST", [])
            if not watch_list:
                print("❌ 监控列表为空，请先配置 WATCH_LIST 或使用 --symbol 参数指定币种")
                sys.exit(1)
            symbols_list = watch_list
            print(f"📋 自动加载 WATCH_LIST: {symbols_list}")
        except Exception as e:
            print(f"❌ 无法加载监控列表: {e}")
            print("💡 请使用 --symbol 参数手动指定币种")
            sys.exit(1)
    
    if not symbols_list:
        print("❌ 未指定任何币种，退出")
        sys.exit(1)
    
    # ═══════════════════════════════════════════════════════════════
    # 🔥 Step 2: 初始化币安客户端
    # ═══════════════════════════════════════════════════════════════
    use_higher_tf = SYSTEM_CONFIG.get('USE_HIGHER_TF_FILTER', False)
    higher_interval = SYSTEM_CONFIG.get('HIGHER_INTERVAL', '4h')
    
    print("\n🚀 WJ-BOT 回测引擎 v2.15 - 永存与追加逻辑")
    print("=" * 60)
    print(f"📅 主周期: {args.period}")
    print(f"📆 回测天数: {args.days} 天")
    print(f"📊 回测币种数量: {len(symbols_list)}")
    print(f"🎯 币种列表: {', '.join(symbols_list)}")
    
    if use_higher_tf and higher_interval != args.period:
        print(f"🔍 高周期过滤: 启用 (将额外拉取 {higher_interval} 数据)")
    else:
        print(f"🔍 高周期过滤: 禁用 (仅拉取主周期数据)")
    
    print("=" * 60)
    
    try:
        req_params = {}
        if SYSTEM_CONFIG.get("PROXY_ENABLED"):
            proxy_url = f"http://{SYSTEM_CONFIG.get('PROXY_HOST', '127.0.0.1')}:{SYSTEM_CONFIG.get('PROXY_PORT', '4780')}"
            req_params['proxies'] = {'http': proxy_url, 'https': proxy_url}
            print(f"🌐 已挂载系统代理: {proxy_url}")
            
        client = BinanceClient(
            api_key=SYSTEM_CONFIG.get('API_KEY'),
            api_secret=SYSTEM_CONFIG.get('API_SECRET'),
            requests_params=req_params
        )
        print("✅ 币安客户端初始化成功\n")
    except Exception as e:
        print(f"❌ 币安客户端初始化失败: {e}")
        return
    
    # ═══════════════════════════════════════════════════════════════
    # 🔥 Step 3: 参数网格配置 (v2.8 冠军回测优化)
    # ═══════════════════════════════════════════════════════════════
    param_grid = {
        'EMA_TREND': [89, 144],         # 均线周期：快慢组合
        'ATR_MULT': [2.5, 3.0],         # 初始防线：适度拉宽防插针
        'ADX_THR': [18, 20],            # 动能门槛
        'RISK_RATIO': [0.03],           # 风险比率：单笔 3%
        'MIN_SIGNAL_DISTANCE_ATR': [1.5],
        'USE_HIGHER_TF_FILTER': [False], # 彻底关闭 4小时 MTF 压制！
        
        # 💥 必须硬编码的"长周期生存止损矩阵" 💥
        'STAGE_A_PROFIT_MULT': [1.2],    # 浮盈 1.2 倍才减风险 (防过早割肉)
        'STAGE_A_SL_MULT': [0.8],        # 止损退守在开仓价后 0.8 倍 ATR 处
        'STAGE_B_PROFIT_MULT': [1.8],    # 浮盈 1.8 倍再保本
        'TSL_TRIGGER_MULT': [2.0],       # 降维：赚到 2 倍 ATR 立刻启动移动止盈！
        'TSL_CALLBACK_MULT': [1.5],      # 允许 1.5 倍 ATR 的回撤容忍度
        
        'VAULT_ENABLED': [vault_status]  # 接收命令行金库指令
    }

    
    total_combinations = len(param_grid['EMA_TREND']) * len(param_grid['ATR_MULT']) * len(param_grid['ADX_THR'])
    print(f"📐 参数网格配置:")
    print(f"   • EMA_TREND: {param_grid['EMA_TREND']}")
    print(f"   • ATR_MULT: {param_grid['ATR_MULT']}")
    print(f"   • ADX_THR: {param_grid['ADX_THR']}")
    print(f"   • 参数组合总数: {total_combinations}")
    print(f"   • 总回测任务数: {len(symbols_list)} 币种 × {total_combinations} 组合 = {len(symbols_list) * total_combinations}\n")
    
    # ═══════════════════════════════════════════════════════════════
    # 🔥 Step 4: 序列化演习 - 逐币种回测（v2.15 物理隔离 + 强制追加）
    # ═══════════════════════════════════════════════════════════════
    master_log_file = 'backtest_comprehensive_report.csv'
    individual_files = []  # 记录每个币种生成的独立文件
    
    print(f"\n📚 综合报告文件: {master_log_file}")
    print(f"💡 每个币种将生成独立的时间戳文件，并强制追加到综合报告")
    
    for idx, symbol in enumerate(symbols_list, 1):
        print("\n" + "=" * 60)
        print(f"🎯 [{idx}/{len(symbols_list)}] 开始回测币种: {symbol}")
        print("=" * 60)
        
        # 🔥 v2.15 核心物理隔离：每个币种重新实例化 BacktestWorker
        worker = BacktestWorker(client, symbol)
        print(f"✅ 已为 {symbol} 创建独立回测工作器")
        
        # 🔥 v2.15 共享内存物理清空：严禁数据污染
        global _SHARED_INDICATOR_CACHE, _SHARED_SYMBOL, _SHARED_MAIN_INTERVAL
        _SHARED_INDICATOR_CACHE = {}
        _SHARED_SYMBOL = ""
        _SHARED_MAIN_INTERVAL = ""
        print(f"🧹 已物理清空 shared_cache（防止 {symbol} 数据污染）")
        
        # 运行网格搜索（v2.15 自动生成唯一文件名 + 强制追加综合报告）
        result = worker.run_grid_search(
            param_grid,
            main_interval=args.period,
            lookback_days=args.days,
            output_file='placeholder'  # 🔥 v2.15：此参数已废弃，函数内部自动生成唯一文件名
        )
        
        if result['success']:
            print(f"\n✅ {symbol} 回测完成")
            print(f"   • 独立文件: {result.get('output_file', 'N/A')}")
            print(f"   • 最优夏普比率: {result['best_sharpe']:.4f}")
            print(f"   • 最优参数: {result['best_params']}")
            individual_files.append(result.get('output_file', 'N/A'))
        else:
            print(f"\n❌ {symbol} 回测失败: {result.get('message', '未知错误')}")
        
        # 释放内存
        del worker
        print(f"🗑️ 已释放 {symbol} 的回测工作器内存")
    
    # ═══════════════════════════════════════════════════════════════
    # 🔥 Step 5: 汇总报告（v2.15 永存与追加版）
    # ═══════════════════════════════════════════════════════════════
    print("\n" + "=" * 60)
    print("🎉 编队集群回测完成！")
    print("=" * 60)
    print(f"📊 回测币种数: {len(symbols_list)}")
    print(f"📚 综合报告 (永久追加): {master_log_file}")
    print(f"\n📁 各币种独立文件 (唯一时间戳):")
    for i, file in enumerate(individual_files, 1):
        print(f"   {i}. {file}")
    print(f"\n💡 下一步:")
    print(f"   1. 查看综合报告进行跨币种横向对比: {master_log_file}")
    print(f"   2. 查看各币种独立文件了解详细参数组合")
    print(f"   3. 使用 Excel/Python 筛选出各币种的最优参数组合")
    print(f"   4. 通过 /evolution 命令应用最优参数到实盘")
    print("=" * 60 + "\n")


if __name__ == '__main__':
    main()
