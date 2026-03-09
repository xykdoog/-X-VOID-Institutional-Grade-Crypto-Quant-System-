#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
交易引擎模块 - trading_engine.py
负责交易信号生成、订单执行、仓位管理
"""

import time
import math
import threading
import pandas as pd
import pandas_ta as ta
from datetime import datetime
from binance.enums import *
from logger_setup import logger

import csv
import os

import config
from config import (
    SYSTEM_CONFIG, ACTIVE_POSITIONS, STRATEGY_PRESETS, positions_lock,
    csv_lock, config_lock, state_lock, get_binance_interval, save_data
)

from utils import (
    get_current_price, round_to_tick_size, round_to_quantity_precision,
    send_tg_msg, send_tg_alert, execute_vault_transfer
)

from risk_manager import get_risk_manager

# 🔥 导入仓位隔离模块
try:
    from position_isolation import (
        generate_bot_order_id,
        is_bot_order,
        validate_close_permission,
        sync_positions_with_isolation,
        emergency_close_all_bot_positions
    )
    POSITION_ISOLATION_ENABLED = True
    print("✅ 仓位隔离模块已加载")
except ImportError as e:
    print(f"⚠️ 仓位隔离模块未找到，使用传统模式: {e}")
    POSITION_ISOLATION_ENABLED = False

import sys
import builtins

# 🔥 终极消音器：检测当前进程是否为回测进程
IS_BACKTEST_PROCESS = any("backtest_worker.py" in arg for arg in sys.argv)

def silent_print(*args, **kwargs):
    """引擎专用打印：实盘时大声汇报，回测时绝对闭嘴"""
    if not IS_BACKTEST_PROCESS:
        builtins.print(*args, **kwargs)

# 🔥 劫持当前模块所有的 print 函数
print = silent_print

# ==========================================
# 引擎全局状态（连续亏损断路器）
# ==========================================
ENGINE_STATE = {
    'consecutive_losses': 0,
    'breaker_until': 0
}

# ==========================================
# 🔥 利滚利：平仓后刷新 BENCHMARK_CASH
# ==========================================
def _refresh_benchmark_after_close(client):
    """
    平仓后刷新 BENCHMARK_CASH，实现"利滚利"效果
    使凯利公式能基于最新资金量计算下一单仓位
    """
    from config import SYSTEM_CONFIG, state_lock, save_data
    from utils import _to_decimal
    
    if client is None:
        print(f"   ⚠️ 利滚利刷新失败: 无API连接")
        return
    
    try:
        acc_info = client.futures_account()
        total_margin_balance = float(acc_info.get('totalMarginBalance', 0))
        
        if total_margin_balance > 0:
            benchmark_value = float(_to_decimal(total_margin_balance).quantize(_to_decimal('0.01')))
            with state_lock:
                SYSTEM_CONFIG["BENCHMARK_CASH"] = benchmark_value
                save_data()
            print(f"   💰 利滚利：BENCHMARK_CASH 已更新为 ${benchmark_value:.2f}")
    except Exception as e:
        print(f"   ⚠️ 利滚利刷新失败: {e}")


# ==========================================
# 🔥 动态对账系统：BENCHMARK_CASH 初始化同步
# ==========================================
def sync_benchmark_with_api(client):
    """
    引擎启动时同步 BENCHMARK_CASH 到真实账户余额（动态对账模式）
    
    逻辑：
    1. 通过 client.futures_account() 获取 totalMarginBalance
    2. 使用 state_lock 将获取到的值写入 SYSTEM_CONFIG["BENCHMARK_CASH"] 和 PEAK_EQUITY
    3. 完成后调用 save_data() 持久化
    
    安全防御：
    - 如果 API 获取失败且没有本地缓存，必须抛出异常并阻止引擎启动
    - 严禁在金额为 0 的情况下运行
    
    Returns:
        (success: bool, message: str)
    """
    from config import SYSTEM_CONFIG, state_lock, save_data
    from utils import _to_decimal
    
    try:
        # 检查客户端连接
        if client is None:
            # 无API连接，检查本地缓存
            cached_benchmark = SYSTEM_CONFIG.get("BENCHMARK_CASH", 0.0)
            if cached_benchmark > 0:
                msg = f"⚠️ 无API连接，使用本地缓存: BENCHMARK_CASH=${cached_benchmark:.2f}"
                print(msg)
                send_tg_msg(f"⚠️ <b>{msg}</b>")
                return True, msg
            else:
                # 致命错误：无API且无缓存
                error_msg = "🚨 致命错误：无法连接交易所API且本地无有效缓存，拒绝启动引擎！"
                print(error_msg)
                send_tg_alert(
                    f"🔴 <b>[引擎启动失败]</b>\n\n"
                    f"{error_msg}\n\n"
                    f"⚠️ 请检查网络连接或API配置后重试。"
                )
                raise Exception(error_msg)
        
        # 从交易所获取真实余额
        try:
            acc_info = client.futures_account()
            total_margin_balance = float(acc_info.get('totalMarginBalance', 0))
            
            # 安全检查：余额不能为0
            if total_margin_balance <= 0:
                error_msg = f"🚨 致命错误：交易所返回余额为 ${total_margin_balance:.2f}，拒绝启动引擎！"
                print(error_msg)
                send_tg_alert(
                    f"🔴 <b>[引擎启动失败]</b>\n\n"
                    f"{error_msg}\n\n"
                    f"⚠️ 请检查账户余额或API权限。"
                )
                raise Exception(error_msg)
            
            # 使用 _to_decimal 确保精度符合 2 位小数要求
            benchmark_value = float(_to_decimal(total_margin_balance).quantize(_to_decimal('0.01')))
            
            with state_lock:
                SYSTEM_CONFIG["BENCHMARK_CASH"] = benchmark_value
                # 同步更新 PEAK_EQUITY（如果当前为0或小于基准）
                if SYSTEM_CONFIG.get("PEAK_EQUITY", 0) < benchmark_value:
                    SYSTEM_CONFIG["PEAK_EQUITY"] = benchmark_value
                save_data()
            
            msg = f"✅ 动态对账完成（实盘模式）: BENCHMARK_CASH=${benchmark_value:.2f}"
            print(msg)
            send_tg_msg(
                f"📊 <b>动态对账完成</b>\n\n"
                f"💰 当前账户余额: <code>${benchmark_value:.2f}</code>\n"
                f"📈 PEAK_EQUITY: <code>${SYSTEM_CONFIG['PEAK_EQUITY']:.2f}</code>\n\n"
                f"✅ 基准本金已同步到真实账户余额"
            )
            return True, msg
            
        except Exception as api_error:
            # API调用失败，检查本地缓存
            cached_benchmark = SYSTEM_CONFIG.get("BENCHMARK_CASH", 0.0)
            if cached_benchmark > 0:
                error_msg = f"⚠️ API调用失败: {str(api_error)[:100]}，使用本地缓存: ${cached_benchmark:.2f}"
                print(error_msg)
                send_tg_alert(
                    f"⚠️ <b>[动态对账警告]</b>\n\n"
                    f"API调用失败，已使用本地缓存\n"
                    f"缓存值: ${cached_benchmark:.2f}\n\n"
                    f"错误: {str(api_error)[:200]}"
                )
                return True, error_msg
            else:
                # 致命错误：API失败且无缓存
                error_msg = f"🚨 致命错误：API调用失败且本地无有效缓存，拒绝启动引擎！错误: {str(api_error)[:100]}"
                print(error_msg)
                send_tg_alert(
                    f"🔴 <b>[引擎启动失败]</b>\n\n"
                    f"{error_msg}\n\n"
                    f"⚠️ 请检查网络连接或API配置后重试。"
                )
                raise Exception(error_msg)
    
    except Exception as e:
        error_msg = f"🚨 动态对账异常: {str(e)[:100]}"
        print(error_msg)
        send_tg_alert(f"🔴 <b>[动态对账失败]</b>\n\n{error_msg}")
        raise


# ==========================================
# 币安持仓模式同步（对冲/单向）
# ==========================================
def sync_hedge_mode_to_binance(client):
    """
    引擎启动时同步币安账户的持仓模式（dualSidePosition）
    对冲模式 = dualSidePosition=true
    单向模式 = dualSidePosition=false
    
    Returns:
        (success: bool, message: str)
    """
    hedge_enabled = SYSTEM_CONFIG.get("HEDGE_MODE_ENABLED", False)
    dual_side = "true" if hedge_enabled else "false"
    mode_name = "对冲模式" if hedge_enabled else "单向模式"
    
    if client is None:
        error_msg = "🚨 无API连接，无法同步持仓模式"
        print(error_msg)
        send_tg_alert(f"🔴 <b>[引擎启动失败]</b>\n\n{error_msg}")
        return False, error_msg
    
    try:
        client.futures_change_position_mode(dualSidePosition=dual_side)
        msg = f"✅ 币安账户持仓模式已同步为: {mode_name} (dualSidePosition={dual_side})"
        print(msg)
        send_tg_msg(f"🔀 <b>{msg}</b>")
        return True, msg
    except Exception as e:
        error_str = str(e)
        # APIError -4059: 当前模式已经是目标模式，无需切换
        if '-4059' in error_str or 'No need to change position side' in error_str:
            msg = f"✅ 币安账户已处于{mode_name}，无需切换"
            print(msg)
            return True, msg
        else:
            # 真正的错误（如有持仓导致无法切换）
            msg = f"🚨 切换持仓模式失败: {error_str[:150]}"
            print(msg)
            send_tg_alert(
                f"🚨 <b>[紧急] 持仓模式同步失败</b>\n\n"
                f"目标模式: {mode_name}\n"
                f"错误: {error_str[:200]}\n\n"
                f"⚠️ 可能原因: 当前有活跃持仓，无法切换模式。\n"
                f"请先平掉所有持仓后再切换，或手动在币安APP中操作。\n\n"
                f"🛑 <b>引擎启动已终止！</b>"
            )
            return False, msg


# ==========================================
# 盘口滑点预检
# ==========================================

def check_orderbook_slippage(client, symbol, side, quantity, max_slippage=0.0015):
    """
    🔥 Task 2: L2 订单簿深度审计 - VWAP 滑点计算
    
    核心逻辑：
    1. 获取 L2 订单簿深度（20档）
    2. 计算加权平均成交价 VWAP = Σ(price × qty) / Σ(qty)
    3. 计算滑点率 = |VWAP - 盘口价| / 盘口价
    4. 若滑点率 > max_slippage，拒绝开仓并发送 Telegram 预警
    
    Args:
        client: 币安客户端
        symbol: 交易对
        side: 'BUY' 或 'SELL'
        quantity: 预计成交数量
        max_slippage: 最大容忍滑点率（默认 0.15%，可通过 config.MAX_SLIPPAGE 配置）
    
    Returns:
        (allowed: bool, reason: str, estimated_vwap: float)
    """
    try:
        # 🔥 从 SYSTEM_CONFIG 读取动态滑点阈值
        max_slippage = SYSTEM_CONFIG.get("MAX_SLIPPAGE", max_slippage)
        
        # 获取 L2 订单簿（20档深度）
        orderbook = client.futures_order_book(symbol=symbol, limit=20)
        
        if side == 'BUY':
            # 买入看卖盘 (asks)
            levels = orderbook['asks']
            best_price = float(levels[0][0])
        else:
            # 卖出看买盘 (bids)
            levels = orderbook['bids']
            best_price = float(levels[0][0])
        
        # 🔥 L2 深度审计：累加深度计算加权平均成交价（VWAP）
        remaining_qty = quantity
        total_cost = 0.0
        total_qty_filled = 0.0
        
        for price_str, qty_str in levels:
            level_price = float(price_str)
            level_qty = float(qty_str)
            
            if remaining_qty <= 0:
                break
            
            # 计算本档可成交数量
            filled_qty = min(remaining_qty, level_qty)
            total_cost += filled_qty * level_price
            total_qty_filled += filled_qty
            remaining_qty -= filled_qty
        
        # 检查1：盘口深度不足
        if remaining_qty > 0:
            reason = f"L2深度不足，缺口 {remaining_qty:.4f} (需求 {quantity:.4f})"
            print(f"   🚨 [{symbol}] {reason}")
            
            # 发送 Telegram 预警
            from utils import send_tg_alert
            import html
            send_tg_alert(
                f"🚨 <b>[L2滑点预警-深度不足]</b>\n\n"
                f"币种: {html.escape(symbol)}\n"
                f"方向: {side}\n"
                f"需求数量: {quantity:.4f}\n"
                f"可成交: {total_qty_filled:.4f}\n"
                f"缺口: {remaining_qty:.4f}\n\n"
                f"⚠️ 盘口深度不足，拒绝开仓"
            )
            return False, reason, 0.0
        
        # 🔥 计算 VWAP（加权平均成交价）
        vwap = total_cost / total_qty_filled if total_qty_filled > 0 else best_price
        
        # 🔥 计算滑点率 = |VWAP - 盘口价| / 盘口价
        slippage_rate = abs(vwap - best_price) / best_price if best_price > 0 else 0.0
        
        # 检查2：滑点率超限
        if slippage_rate > max_slippage:
            reason = f"VWAP滑点 {slippage_rate*100:.3f}% > 阈值 {max_slippage*100:.2f}%"
            print(f"   🚨 [{symbol}] {reason}")
            print(f"      盘口价: {best_price:.4f}, VWAP: {vwap:.4f}")
            
            # 🔥 发送 Telegram 预警
            from utils import send_tg_alert
            import html
            send_tg_alert(
                f"🚨 <b>[L2滑点预警-超限]</b>\n\n"
                f"币种: {html.escape(symbol)}\n"
                f"方向: {side}\n"
                f"盘口价: {best_price:.4f}\n"
                f"VWAP: {vwap:.4f}\n"
                f"滑点率: <b>{slippage_rate*100:.3f}%</b>\n"
                f"阈值: {max_slippage*100:.2f}%\n\n"
                f"⚠️ 滑点超限，拒绝开仓"
            )
            return False, reason, vwap
        
        # 通过检查
        print(f"   ✅ [{symbol}] L2滑点检查通过: VWAP={vwap:.4f}, 滑点={slippage_rate*100:.3f}%")
        return True, "OK", vwap
        
    except Exception as e:
        error_msg = f"L2盘口检查异常: {str(e)[:50]}"
        print(f"   ⚠️ [{symbol}] {error_msg}")
        
        # 异常时保守拒绝
        from utils import send_tg_alert
        import html
        send_tg_alert(
            f"⚠️ <b>[L2滑点检查异常]</b>\n\n"
            f"币种: {html.escape(symbol)}\n"
            f"错误: {html.escape(str(e)[:100])}\n\n"
            f"🛡️ 保守拒绝开仓"
        )
        return False, error_msg, 0.0

# ==========================================
# K线数据获取
# ==========================================

def get_historical_klines(client, symbol, interval, limit=200):
    """获取历史K线数据（支持长周期抓取，使用 HistoricalKlinesType.FUTURES）
    
    核心逻辑：
    1. 使用 client.get_historical_klines 并指定 HistoricalKlinesType.FUTURES
    2. 根据传入的 limit 和 interval 自动计算 start_str (毫秒时间戳)
    3. 自动处理分页拼接，突破单次 1000 根的物理限制
    
    Args:
        client: Binance 客户端
        symbol: 交易对（如 'BTCUSDT'）
        interval: K线周期（如 '1m', '5m', '15m', '1h', '4h', '1d'）
        limit: 需要的K线根数（不再受1000硬性限制）
    
    Returns:
        pd.DataFrame: K线数据，失败返回 None
    """
    if client is None:
        print(f"❌ 无API连接，无法获取K线数据")
        return None
    
    # 映射 interval 到毫秒数以计算起始时间
    ms_map = {
        "1m": 60000, 
        "5m": 300000, 
        "15m": 900000, 
        "1h": 3600000, 
        "4h": 14400000, 
        "1d": 86400000
    }
    start_ts = int(time.time() * 1000) - (limit * ms_map.get(interval, 3600000))

    try:
        from binance.client import HistoricalKlinesType
        # 使用 Historical 接口自动处理分页拼接
        klines = client.get_historical_klines(
            symbol=symbol,
            interval=get_binance_interval(interval),
            start_str=start_ts,
            klines_type=HistoricalKlinesType.FUTURES
        )
        # 确保截取最后请求的 limit 数量
        if klines: 
            klines = klines[-limit:]
    except Exception as e:
        print(f"❌ 抓取长周期K线失败: {e}")
        return None
    
    # 保持原有的 DataFrame 构建逻辑
    df = pd.DataFrame(klines, columns=[
        'timestamp', 'open', 'high', 'low', 'close', 'volume',
        'close_time', 'quote_asset_volume', 'number_of_trades',
        'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
    ])
    numeric_cols = ['open', 'high', 'low', 'close', 'volume']
    df[numeric_cols] = df[numeric_cols].astype(float)
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    return df

# ==========================================
# 技术指标计算（含性能优化缓存）
# ==========================================

def calculate_indicators(df, force_recalc=False, custom_config=None):
    """计算技术指标 (机构加强版 + 性能优化)
    
    Args:
        df: K线数据
        force_recalc: 强制重算长周期指标（K线更新时传True）
        custom_config: 可选的自定义配置字典，如果传入则优先使用它而非全局 SYSTEM_CONFIG
    """
    if df is None or len(df) < 100:
        return None
    
    try:
        # 🔥 配置隔离：优先使用传入的 custom_config
        cfg = custom_config if custom_config is not None else SYSTEM_CONFIG
        
        df = df[['timestamp', 'open', 'high', 'low', 'close', 'volume']].copy()
        
        # 1. 基础指标计算
        macd = ta.macd(df['close'], fast=cfg["MACD_FAST"], slow=cfg["MACD_SLOW"], signal=cfg["MACD_SIGNAL"])
        adx = ta.adx(df['high'], df['low'], df['close'], length=14)
        ema_trend = ta.ema(df['close'], length=cfg["EMA_TREND"])
        atr = ta.atr(df['high'], df['low'], df['close'], length=cfg["ATR_PERIOD"])
        
        if macd is not None:
            df['MACD_hist'] = macd.iloc[:, 1]
            df['MACD_line'] = macd.iloc[:, 0]
            df['MACD_signal'] = macd.iloc[:, 2]
        if adx is not None:
            df['ADX'] = adx.iloc[:, 0]
        if ema_trend is not None:
            df['EMA_TREND'] = ema_trend
        if atr is not None:
            df['ATR'] = atr
            df['ATR_SMA100'] = ta.sma(df['ATR'], length=100)
            df['Relative_ATR'] = df['ATR'] / df['ATR_SMA100']
        
        df['RSI'] = ta.rsi(df['close'], length=cfg.get("RSI_PERIOD", 14))
        
        # 2. 机构成本线 (VWAP) - 🔥 修复：确保 DatetimeIndex 有序
        try:
            # 设置 timestamp 为索引并排序，确保 VWAP 计算正确
            df_indexed = df.set_index('timestamp').sort_index()
            vwap = ta.vwap(df_indexed['high'], df_indexed['low'], df_indexed['close'], df_indexed['volume'])
            if vwap is not None:
                df['VWAP'] = vwap.values  # 使用 .values 避免索引对齐问题
        except Exception as vwap_e:
            df['VWAP'] = float('nan')
            print(f"⚠️ VWAP计算失败: {vwap_e}")
        
        # 3. 增强版 TTM Squeeze (蓄力过滤 + 动态通道)
        try:
            # 动态调整通道：趋势强则通道宽
            current_adx = df['ADX'].iloc[-1] if not pd.isna(df['ADX'].iloc[-1]) else 20
            dynamic_scalar = 2.0 if current_adx > 25 else 1.5
            
            bb = ta.bbands(df['close'], length=20, std=2.0)
            kc = ta.kc(df['high'], df['low'], df['close'], length=20, scalar=dynamic_scalar)
            
            if bb is not None and kc is not None:
                BBU = bb[[c for c in bb.columns if c.startswith('BBU')][0]]
                BBL = bb[[c for c in bb.columns if c.startswith('BBL')][0]]
                KCU = kc[[c for c in kc.columns if c.startswith('KCUe')][0]]
                KCL = kc[[c for c in kc.columns if c.startswith('KCLe')][0]]
                
                df['Squeeze_On'] = (BBU < KCU) & (BBL > KCL)
                
                # --- 新增：强制要求挤压蓄力超过 N 根 K 线（SCALPER 模式降低阈值）---
                mode_preset = cfg.get("STRATEGY_MODE", "STANDARD")
                squeeze_thr = 2 if mode_preset == "SCALPER" else 5
                
                df['Squeeze_Duration'] = (
                    df['Squeeze_On'].astype(int)
                    .groupby((df['Squeeze_On'] != df['Squeeze_On'].shift()).cumsum())
                    .cumsum()
                )
                df['Squeeze_Fired'] = (
                    (df['Squeeze_On'].shift(1) == True) &
                    (df['Squeeze_On'] == False) &
                    (df['Squeeze_Duration'].shift(1) >= squeeze_thr)
                )
            else:
                df['Squeeze_On'], df['Squeeze_Fired'] = False, False
        except:
            df['Squeeze_On'], df['Squeeze_Fired'] = False, False
        
        # 防御性清理：移除所有 NaN 行，防止新币种数据不足导致逻辑判断异常
        df = df.dropna()
        
        return df
    except Exception as e:
        print(f"⚠️ 计算技术指标失败: {e}")
        return None

# ==========================================
# 交易信号生成
# ==========================================

def generate_trading_signals(df, symbol, client=None, custom_config=None, mtf_data=None):
    """生成交易信号 (含黑天鹅熔断 + 防骗线时间锁 + MTF多周期共振 + 动态RSI + 日线过滤)
    
    Args:
        df: K线数据
        symbol: 交易对
        client: Binance客户端
        custom_config: 可选的自定义配置字典，如果传入则优先使用它而非全局 SYSTEM_CONFIG
        mtf_data: 多周期数据字典（回测模式必传），格式: {'15m': DataFrame, '1h': DataFrame, '4h': DataFrame, '1d': DataFrame}
    """
    if df is None or len(df) < 2:
        return None
    
    # 🔥 v2.5: 支持自定义配置注入（回测隔离模式）
    cfg = custom_config if custom_config is not None else SYSTEM_CONFIG
    
    # 🔥 v2.8: 回测模式静默日志（检测是否传入 custom_config）
    is_backtest_mode = (custom_config is not None)
    
    # ==========================================
    # 🔥 日线级过滤 (1D Daily Filter)
    # ==========================================
    try:
        daily_ema_200 = None
        
        # 回测模式：从 mtf_data 获取日线数据
        if is_backtest_mode and mtf_data and '1d' in mtf_data:
            df_1d = mtf_data['1d']
            if df_1d is not None and len(df_1d) > 0:
                import pandas_ta as ta
                ema_1d = ta.ema(df_1d['close'], length=200)
                if ema_1d is not None and len(ema_1d) > 0:
                    daily_ema_200 = float(ema_1d.iloc[-1])
        
        # 实盘模式：从API获取日线数据
        elif not is_backtest_mode and client is not None:
            df_1d = get_historical_klines(client, symbol, "1d", limit=250)
            if df_1d is not None and len(df_1d) > 0:
                import pandas_ta as ta
                ema_1d = ta.ema(df_1d['close'], length=200)
                if ema_1d is not None and len(ema_1d) > 0:
                    daily_ema_200 = float(ema_1d.iloc[-1])
        
        if daily_ema_200 is not None:
            current_price = df.iloc[-1]['close']
            
            if not is_backtest_mode:
                print(f"   📊 [{symbol}] 日线过滤: 价格={current_price:.4f}, 1D_EMA200={daily_ema_200:.4f}")
    
    except Exception as daily_e:
        if not is_backtest_mode:
            print(f"   ⚠️ [{symbol}] 日线过滤计算失败: {daily_e}")
        daily_ema_200 = None
    
    try:
        signals = {
            'symbol': symbol,
            'timestamp': datetime.now(),
            'price': df.iloc[-1]['close'],
            'atr': df.iloc[-1].get('ATR', 0),
            'signals': []
        }
        
        if len(df) < 4:
            return None
        
        mode_preset = cfg.get("STRATEGY_MODE", "STANDARD")
        use_latest = cfg.get("USE_LATEST_CANDLE", False)
        
        # ====== SCALPER 模式特殊处理 ======
        is_scalper_mode = (mode_preset == "SCALPER")
        if is_scalper_mode:
            # 狂战士模式：强制使用最新K线，即发即开
            use_latest = True
        
        if use_latest:
            closed_candle = df.iloc[-1]
            prev_closed_candle = df.iloc[-2]
            prev2_closed_candle = df.iloc[-3]
        else:
            closed_candle = df.iloc[-2]
            prev_closed_candle = df.iloc[-3]
            prev2_closed_candle = df.iloc[-4]
        
        # ==========================================
        # 🛡️ 任务1：市场异常检测（防黑天鹅）- 一票否决权
        # ==========================================
        try:
            # 检测1：跳空检测（>5%）
            prev_close = prev_closed_candle['close']
            current_open = closed_candle['open']
            
            # 防除零处理
            if prev_close > 0:
                gap_ratio = abs(current_open - prev_close) / prev_close
                if gap_ratio > 0.05:
                    msg = f"🚨 [{symbol}] 触发黑天鹅拦截：检测到跳空异动 ({gap_ratio*100:.2f}%)"
                    if not is_backtest_mode:
                        print(msg)
                        logger.warning(msg)
                    return None
            
            # 检测2：极端振幅（>10%）
            candle_range = closed_candle['high'] - closed_candle['low']
            current_close = closed_candle['close']
            
            # 防除零处理
            if current_close > 0:
                amplitude_ratio = candle_range / current_close
                if amplitude_ratio > 0.10:
                    msg = f"🚨 [{symbol}] 触发黑天鹅拦截：检测到极端振幅 ({amplitude_ratio*100:.2f}%)"
                    if not is_backtest_mode:
                        print(msg)
                        logger.warning(msg)
                    return None
            
            # 检测3：天量异动（>5倍均量）
            current_volume = closed_candle['volume']
            avg_volume_20 = df['volume'].shift(1).tail(20).mean()  # 🔥 v7.0: 确保与历史均量对比
            
            # 防除零处理
            if avg_volume_20 > 0:
                volume_ratio = current_volume / avg_volume_20
                if volume_ratio > 5.0:
                    msg = f"🚨 [{symbol}] 触发黑天鹅拦截：检测到天量异动 ({volume_ratio:.2f}x均量)"
                    if not is_backtest_mode:
                        print(msg)
                        logger.warning(msg)
                    return None
        
        except Exception as anomaly_e:
            print(f"⚠️ [{symbol}] 市场异常检测失败: {anomaly_e}")
            # 检测失败时保守拦截
            return None
        
        # ====== 防守补丁 A：黑天鹅波动率熔断（机构级全模式适配）======
        relative_atr = closed_candle.get('Relative_ATR', 1.0)
        if pd.isna(relative_atr):
            relative_atr = 1.0
        
        # 🔥 从当前策略预设中读取 VOL_LIMIT（全模式适配）
        current_mode = SYSTEM_CONFIG.get("STRATEGY_MODE", "STANDARD")
        preset_config = STRATEGY_PRESETS.get(current_mode, {})
        vol_limit = preset_config.get("VOL_LIMIT", 2.0)  # 默认 2.0
        
        if relative_atr > vol_limit:
            mode_name = preset_config.get("name", current_mode)
            print(f"   🚨 [{symbol}] 波动率异常，{mode_name} 已自动挂起!")
            print(f"      Relative_ATR={relative_atr:.2f} > VOL_LIMIT={vol_limit}")
            return None
        
        is_price_above_ema = False
        is_price_below_ema = False
        if 'EMA_TREND' in df.columns and not pd.isna(closed_candle['EMA_TREND']):
            is_price_above_ema = closed_candle['close'] > closed_candle['EMA_TREND']
            is_price_below_ema = closed_candle['close'] < closed_candle['EMA_TREND']
        
        adx_val = closed_candle.get('ADX', 0)
        is_low_vol = cfg.get("LOW_VOL_MODE", False)
        
        # 🔥 参谋部补强A: 阶梯式 ADX 门槛（Volume-Adaptive ADX Threshold）
        # 成交量爆发（Volume > 1.5x 均量）→ ADX 门槛降至 20（早入场捕获动能）
        # 常规突破（无放量确认）→ ADX 门槛升至 28（严格过滤震荡噪音）
        if is_low_vol:
            current_adx_thr = 10
        else:
            # 计算成交量爆发比率
            _vol_surge_ratio = 1.0
            if len(df) >= 21:
                _avg_vol_20 = df['volume'].iloc[-21:-1].mean()
                _current_vol = closed_candle.get('volume', 0)
                if _avg_vol_20 > 0:
                    _vol_surge_ratio = _current_vol / _avg_vol_20
            
            # 阶梯式门槛：放量时降低门槛，缩量时提高门槛
            ADX_THR_VOLUME_BURST = cfg.get("ADX_THR_VOLUME_BURST", 20)   # 放量门槛
            ADX_THR_NORMAL = cfg.get("ADX_THR_NORMAL", 28)               # 常规门槛
            VOLUME_BURST_MULT = cfg.get("VOLUME_BURST_MULT", 1.5)        # 放量判定倍数
            
            if _vol_surge_ratio > VOLUME_BURST_MULT:
                current_adx_thr = ADX_THR_VOLUME_BURST
                if not is_backtest_mode:
                    print(f"   🔥 [{symbol}] 阶梯式ADX: 放量{_vol_surge_ratio:.1f}x > {VOLUME_BURST_MULT}x → ADX门槛降至{ADX_THR_VOLUME_BURST}")
            else:
                current_adx_thr = ADX_THR_NORMAL
                if not is_backtest_mode:
                    print(f"   📊 [{symbol}] 阶梯式ADX: 常规量能{_vol_surge_ratio:.1f}x → ADX门槛升至{ADX_THR_NORMAL}")
        
        # 🔥 任务2.1：暴露 ADX 值到 signals 字典
        signals['adx'] = adx_val
        
        if 'MACD_hist' in df.columns:
            hist_cross_up = closed_candle['MACD_hist'] > 0 and prev_closed_candle['MACD_hist'] <= 0
            hist_cross_down = closed_candle['MACD_hist'] < 0 and prev_closed_candle['MACD_hist'] >= 0
            
            if hist_cross_down:
                signals['signals'].append({
                    'type': 'SELL', 'action': 'EXIT_LONG',
                    'indicator': 'MACD', 'strength': 'STRONG',
                    'message': "平多信号：MACD_Hist向下穿越0轴"
                })
            if hist_cross_up:
                signals['signals'].append({
                    'type': 'BUY', 'action': 'EXIT_SHORT',
                    'indicator': 'MACD', 'strength': 'STRONG',
                    'message': "平空信号：MACD_Hist向上穿越0轴"
                })
            
            # 🔥 v3.0 量能过滤器：成交量必须 > 20均量 × VOLUME_SURGE_THRESHOLD
            avg_vol_20 = df['volume'].tail(20).mean() if len(df) >= 20 else df['volume'].mean()
            volume_surge_thr = cfg.get('VOLUME_SURGE_THRESHOLD', 1.3)
            volume_burst = closed_candle['volume'] > avg_vol_20 * volume_surge_thr
            
            # 🔥 入场阈值弹性化：EMA_TREND 斜率向上且放量时，允许 ADX >= 15 即入场
            ema_slope_up = False
            if 'EMA_TREND' in df.columns and len(df) >= 3:
                ema_current = closed_candle.get('EMA_TREND', 0)
                ema_prev = df.iloc[-2].get('EMA_TREND', 0)
                ema_slope_up = ema_current > ema_prev and not pd.isna(ema_current) and not pd.isna(ema_prev)
            
            # 🔥 SCALPER 模式：降低 ADX 强制过滤权重，仅需方向确认不强制强度
            if is_scalper_mode:
                # 狂战士模式：ADX 仅作为方向参考，不作为硬性门槛
                # 只要 MACD 方向一致 + EMA 方向确认即可（ADX > 0 即通过）
                long_momentum = (is_price_above_ema and adx_val > 0)
                short_momentum = (is_price_below_ema and adx_val > 0)
            else:
                # 🔥 弹性入场：如果 EMA 斜率向上且放量，允许 ADX >= 15 即入场
                if ema_slope_up and volume_burst and adx_val >= 15:
                    long_momentum = is_price_above_ema
                    short_momentum = is_price_below_ema
                    if not is_backtest_mode:
                        print(f"   🔥 [{symbol}] 弹性入场激活: EMA斜率向上+放量, ADX={adx_val:.1f} >= 15")
                else:
                    # 🔥 Patch v9.1: 解除逻辑死锁 - 条件互补 + MACD 强制确认
                    # 只要价格在EMA同侧且MACD在零轴同侧发散，就是有效动能；或者ADX证明有强趋势
                    long_momentum = (is_price_above_ema and closed_candle['MACD_hist'] > 0) or (adx_val >= current_adx_thr)
                    short_momentum = (is_price_below_ema and closed_candle['MACD_hist'] < 0) or (adx_val >= current_adx_thr)
            
            if is_low_vol and (closed_candle['MACD_hist'] > prev_closed_candle['MACD_hist']) and \
               (prev_closed_candle['MACD_hist'] > prev2_closed_candle['MACD_hist']):
                long_momentum = True
            
            if is_low_vol and (closed_candle['MACD_hist'] < prev_closed_candle['MACD_hist']) and \
               (prev_closed_candle['MACD_hist'] < prev2_closed_candle['MACD_hist']):
                short_momentum = True
            
            vwap_val = closed_candle.get('VWAP', float('nan'))
            has_vwap = not pd.isna(vwap_val)
            squeeze_fired = closed_candle.get('Squeeze_Fired', False)
            rsi_val = closed_candle.get('RSI', 50)
            has_rsi = not pd.isna(rsi_val)
            
            # 🔥 Patch v9.2 "狂暴引擎": 简化动量逻辑，移除复杂 RSI 评分
            # 只保留基础的超买超卖边界检查（防止极端追涨杀跌）
            rsi_oversold = 20   # 极度超卖边界
            rsi_overbought = 80  # 极度超买边界
            
            # 🔥 Patch v9.2: 移除动态 RSI 弹性区间，使用固定边界
            # 狂暴引擎：只要不是极端超买超卖（RSI 20-80），就放行
            
            # ====== 🔥 防守补丁 C：空间锁增强版（Price Volatility + Volume Filter）======
            # 计算当前信号 K 线的实体长度（绝对值）
            candle_body = abs(closed_candle['close'] - closed_candle['open'])
            current_atr = closed_candle.get('ATR', 0)
            
            # 从策略预设中读取 MAX_CANDLE_BODY_ATR（空间锁阈值）
            # 🔥 v3.3: 临时强制改为 5.0，防止大实体K线被空间锁拦截
            # 🔥 解除空间锁参数硬编码：优先读取传入的动态参数
            max_candle_body_atr = cfg.get("MAX_CANDLE_BODY_ATR", preset_config.get("MAX_CANDLE_BODY_ATR", 2.0))
            
            # 🔥 动态空间锁矩阵：波动率越大，允许的入场实体空间越宽
            rel_atr = closed_candle.get('Relative_ATR', 1.0)
            max_candle_body_atr = max_candle_body_atr * (1 + rel_atr / 4)
            
            # 🔥 空间锁动态扩容：疯狗模式激活时自动上浮50%
            _is_mad_dog = SYSTEM_CONFIG.get("FORCE_MAD_DOG_MODE", False) or \
                          (SYSTEM_CONFIG.get("MAD_DOG_MODE", False) and SYSTEM_CONFIG.get("MAD_DOG_TRIGGER", 1.3) > 0)
            if _is_mad_dog:
                max_candle_body_atr = max_candle_body_atr * 1.5
                if not is_backtest_mode:
                    print(f"   🔥 [{symbol}] 空间锁动态扩容: {preset_config.get('MAX_CANDLE_BODY_ATR', 2.0):.2f} → {max_candle_body_atr:.2f}")
            
            # 🔥 v3.0 空间锁增强：成交量过滤（Volume Ratio验证）
            space_lock_enabled = SYSTEM_CONFIG.get("SPACE_LOCK_ENABLED", True)
            space_lock_triggered = False
            volume_breakout = False
            
            if current_atr > 0 and candle_body > (current_atr * max_candle_body_atr):
                if space_lock_enabled:
                    # 计算成交量比率：当前成交量 / 过去20根K线平均成交量
                    volume_ratio = 1.0
                    if len(df) >= 21:
                        # 获取过去20根K线的平均成交量（不包括当前K线）
                        past_20_volumes = df.iloc[-21:-1]['volume']
                        avg_volume_20 = past_20_volumes.mean()
                        current_volume = closed_candle.get('volume', 0)
                        
                        if avg_volume_20 > 0:
                            volume_ratio = current_volume / avg_volume_20
                        
                        if not is_backtest_mode:
                            print(f"   📊 [{symbol}] 成交量分析: 当前={current_volume:.2f}, 20均={avg_volume_20:.2f}, 比率={volume_ratio:.2f}x")
                    
                    # 判定逻辑：
                    # 1. 实体超限 + 成交量比率 > 2.0 → 有效突破，放行信号
                    # 2. 实体超限 + 缩量（比率 <= 2.0）→ 维持拦截
                    volume_breakout_threshold = SYSTEM_CONFIG.get("VOLUME_BREAKOUT_RATIO", 2.0)
                    
                    if volume_ratio > volume_breakout_threshold:
                        # 有效突破：放量突破，放行信号
                        volume_breakout = True
                        space_lock_triggered = False
                        space_lock_ratio = candle_body / current_atr
                        print(f"   ✅ [{symbol}] 空间锁豁免：实体超限但放量突破！")
                        print(f"      实体/ATR={space_lock_ratio:.2f}, 成交量比率={volume_ratio:.2f}x > {volume_breakout_threshold}x")
                        print(f"      判定为有效突破，放行信号")
                    else:
                        # 缩量拉升/砸盘：维持拦截
                        space_lock_triggered = True
                        space_lock_ratio = candle_body / current_atr
                        if not is_backtest_mode:
                            print(f"   🔒 [{symbol}] 空间锁触发！K线实体={candle_body:.4f} > ATR({current_atr:.4f}) * {max_candle_body_atr} = {current_atr * max_candle_body_atr:.4f}")
                            print(f"      实体/ATR比率={space_lock_ratio:.2f}, 成交量比率={volume_ratio:.2f}x <= {volume_breakout_threshold}x")
                            print(f"      判定为缩量情绪化拉升/砸盘，强制拦截信号")
                else:
                    # 空间锁已关闭（狂战士高频模式），仅记录日志不拦截
                    space_lock_ratio = candle_body / current_atr
                    print(f"   ⚡ [{symbol}] 空间锁已关闭（狂战士模式），放行高波动信号 | 实体/ATR={space_lock_ratio:.2f}")
            
            # ====== 防守补丁 B：防骗线时间锁 ======
            # MACD 穿越后要求连续 N 根 K 线保持同侧，防止假突破立即回撤
            # SCALPER 模式：即发即开，无需确认
            if is_scalper_mode:
                confirm_bars = STRATEGY_PRESETS["SCALPER"].get("SIGNAL_CONFIRM_BARS", 0)
            else:
                confirm_bars = preset_config.get("SIGNAL_CONFIRM_BARS", SYSTEM_CONFIG.get("SIGNAL_CONFIRM_BARS", 2))
            
            # ====== 🔥 MTF多周期共振对齐检查 ======
            mtf_aligned = True
            mtf_reason = ""
            higher_tf_ema = None
            
            if preset_config.get("USE_HIGHER_TF_FILTER", False) and not is_scalper_mode:
                # 🔥 v2.8: 回测模式传入 custom_config 和 mtf_data
                higher_tf_ema = _fetch_higher_tf_ema(client, symbol, custom_config=custom_config, mtf_data=mtf_data)
                if higher_tf_ema is not None:
                    # 🔥 回测模式静默日志
                    if custom_config is None:
                        print(f"   📊 [{symbol}] 高周期EMA: {higher_tf_ema:.4f}")
                    else:
                        logger.debug(f"   📊 [BACKTEST] [{symbol}] 高周期EMA: {higher_tf_ema:.4f}")
            
            if hist_cross_up and long_momentum:
                # 🔥 日线级过滤：拦截逆势做多（物理阻断）
                if daily_ema_200 is not None and closed_candle['close'] < daily_ema_200:
                    if not is_backtest_mode:
                        print(f"   🚫 [{symbol}] 做多信号被日线过滤拦截: 价格{closed_candle['close']:.4f} < 1D_EMA200{daily_ema_200:.4f}")
                    # 物理阻断：跳过后续逻辑
                elif higher_tf_ema is not None and not is_scalper_mode:
                    # 🔥 MTF对齐检查（SCALPER模式豁免）
                    mtf_aligned, mtf_reason = is_mtf_aligned(closed_candle['close'], higher_tf_ema, 'BUY')
                    if not mtf_aligned:
                        if not is_backtest_mode:
                            print(f"   🚫 [{symbol}] 做多信号被MTF拦截: {mtf_reason}")
                    
                    # 🔥 空间锁优先拦截：如果触发则跳过后续检查
                    if not mtf_aligned:
                        pass  # MTF拦截，跳过
                    elif space_lock_triggered:
                        if not is_backtest_mode:
                            print(f"   🔒 [{symbol}] 做多信号被空间锁拦截 (K线实体过大，疑似追涨)")
                    else:
                        # 时间锁：检查穿越后是否有足够的确认 K 线
                        time_lock_pass = True
                        if confirm_bars > 1 and use_latest:
                            for i in range(1, min(confirm_bars, len(df))):
                                check_candle = df.iloc[-(i)]
                                if check_candle.get('MACD_hist', 0) <= 0:
                                    time_lock_pass = False
                                    break
                        
                        vwap_pass = (closed_candle['close'] > vwap_val) if has_vwap else True
                        # 🔥 v4.0 优化：解开Squeeze枷锁 - 从"抓爆发"转为"抓趋势"
                        # 原逻辑：squeeze_pass = squeeze_fired
                        # 新逻辑：只要当前不在横盘蓄力（Squeeze_On=False），就允许入场捕捉趋势
                        # 🔥 关闭 15m 回测的 Squeeze 限制：回测模式下强行放行，看原生信号爆发力
                        squeeze_pass = True if is_backtest_mode else (not closed_candle.get('Squeeze_On', False))
                        
                        # 🔥 Patch v9.2: 简化 RSI 检查，只拦截极端值
                        rsi_pass = (rsi_oversold < rsi_val < rsi_overbought) if has_rsi else True
                        
                        # 🔥 SCALPER 探针模式：Squeeze 未释放但 MACD 方向一致时，降级为 PROBE 轻仓入场
                        is_probe_entry = False
                        if is_scalper_mode and not squeeze_pass:
                            # 狂战士探针：MACD_hist 连续 2 根同向递增即视为动能确认
                            macd_momentum_up = (
                                closed_candle['MACD_hist'] > 0 and
                                closed_candle['MACD_hist'] > prev_closed_candle['MACD_hist']
                            )
                            if macd_momentum_up and rsi_pass and vwap_pass and time_lock_pass:
                                is_probe_entry = True
                                squeeze_pass = True  # 探针模式下豁免 Squeeze 门槛
                        
                        filter_status = []
                        if has_vwap:
                            filter_status.append(f"VWAP: {'✅' if vwap_pass else '❌'}")
                        filter_status.append(f"Squeeze: {'✅' if squeeze_pass else '❌'}{'(PROBE)' if is_probe_entry else ''}")
                        if has_rsi:
                            filter_status.append(f"RSI({rsi_val:.1f} in [{rsi_oversold},{rsi_overbought}]): {'✅' if rsi_pass else '❌'}")
                        filter_status.append(f"TimeLock: {'✅' if time_lock_pass else '❌'}")
                        filter_status.append(f"SpaceLock: {'✅' if not space_lock_triggered else '❌'}")
                        filter_status.append(f"VolBrk({relative_atr:.1f}): ✅")
                        if higher_tf_ema is not None and not is_scalper_mode:
                            filter_status.append(f"MTF: {'✅' if mtf_aligned else '❌'}")
                        
                        # 🔥 Patch v9.1: MTF 对齐弱化为非硬性一票否决
                        if vwap_pass and squeeze_pass and rsi_pass and time_lock_pass:
                            entry_strength = 'PROBE' if is_probe_entry else ('STRONG' if mtf_aligned else 'MEDIUM')
                            probe_tag = "🔍探针轻仓" if is_probe_entry else ""
                            mtf_warning = "" if mtf_aligned else "⚠️MTF未对齐"
                            signals['signals'].append({
                                'type': 'BUY', 'action': 'ENTRY',
                                'indicator': 'MACD+EMA/ADX+VWAP+Squeeze+RSI+SpaceLock+Defense', 'strength': entry_strength,
                                'message': f"做多信号{probe_tag}{mtf_warning}：MACD金叉+动能确认+机构成本过滤+{'探针豁免' if is_probe_entry else '挤压释放'}+RSI空间+空间锁+防守通过 [{' '.join(filter_status)}]"
                            })
                        else:
                            if not is_backtest_mode:
                                print(f"   ⚠️ 做多信号被过滤: {' '.join(filter_status)}")
            
            if hist_cross_down and short_momentum:
                # 🔥 日线级过滤：拦截逆势做空
                if daily_ema_200 is not None and closed_candle['close'] > daily_ema_200:
                    if not is_backtest_mode:
                        print(f"   🚫 [{symbol}] 做空信号被日线过滤拦截: 价格{closed_candle['close']:.4f} > 1D_EMA200{daily_ema_200:.4f}")
                else:
                    # 🔥 MTF对齐检查（SCALPER模式豁免）
                    if higher_tf_ema is not None and not is_scalper_mode:
                        mtf_aligned, mtf_reason = is_mtf_aligned(closed_candle['close'], higher_tf_ema, 'SELL')
                        # 🔥 v4.1 终极解封：MTF 多周期共振豁免
                        # 回测 15m 时，如果 MTF 找不到 1h 数据会直接拦截。我们强行给回测模式放行
                        if is_backtest_mode:
                            mtf_aligned = True
                        if not mtf_aligned:
                            if not is_backtest_mode:
                                print(f"   🚫 [{symbol}] 做空信号被MTF拦截: {mtf_reason}")
                    
                    # 🔥 空间锁优先拦截：如果触发则跳过后续检查
                    if not mtf_aligned:
                        pass  # MTF拦截，跳过
                    elif space_lock_triggered:
                        if not is_backtest_mode:
                            print(f"   🔒 [{symbol}] 做空信号被空间锁拦截 (K线实体过大，疑似杀跌)")
                    else:
                        # 时间锁：检查穿越后是否有足够的确认 K 线
                        time_lock_pass = True
                        if confirm_bars > 1 and use_latest:
                            for i in range(1, min(confirm_bars, len(df))):
                                check_candle = df.iloc[-(i)]
                                if check_candle.get('MACD_hist', 0) >= 0:
                                    time_lock_pass = False
                                    break
                        
                        vwap_pass = (closed_candle['close'] < vwap_val) if has_vwap else True
                        # 🔥 优化1：放宽Squeeze限制 - 仅需当前不在挤压中（Squeeze_On=False）
                        # 🔥 关闭 15m 回测的 Squeeze 限制：回测模式下强行放行，看原生信号爆发力
                        squeeze_pass = True if is_backtest_mode else (closed_candle.get('Squeeze_On', False) == False)
                        
                        # 🔥 Patch v9.2: 简化 RSI 检查（SHORT侧）
                        rsi_pass = (rsi_oversold < rsi_val < rsi_overbought) if has_rsi else True
                        
                        # 🔥 SCALPER 探针模式：Squeeze 未释放但 MACD 方向一致时，降级为 PROBE 轻仓入场
                        is_probe_entry = False
                        if is_scalper_mode and not squeeze_pass:
                            # 狂战士探针：MACD_hist 连续 2 根同向递减即视为动能确认
                            macd_momentum_down = (
                                closed_candle['MACD_hist'] < 0 and
                                closed_candle['MACD_hist'] < prev_closed_candle['MACD_hist']
                            )
                            if macd_momentum_down and rsi_pass and vwap_pass and time_lock_pass:
                                is_probe_entry = True
                                squeeze_pass = True  # 探针模式下豁免 Squeeze 门槛
                        
                        filter_status = []
                        if has_vwap:
                            filter_status.append(f"VWAP: {'✅' if vwap_pass else '❌'}")
                        filter_status.append(f"Squeeze: {'✅' if squeeze_pass else '❌'}{'(PROBE)' if is_probe_entry else ''}")
                        if has_rsi:
                            filter_status.append(f"RSI({rsi_val:.1f} in [{rsi_oversold},{rsi_overbought}]): {'✅' if rsi_pass else '❌'}")
                        filter_status.append(f"TimeLock: {'✅' if time_lock_pass else '❌'}")
                        filter_status.append(f"SpaceLock: {'✅' if not space_lock_triggered else '❌'}")
                        filter_status.append(f"VolBrk({relative_atr:.1f}): ✅")
                        if higher_tf_ema is not None and not is_scalper_mode:
                            filter_status.append(f"MTF: {'✅' if mtf_aligned else '❌'}")
                        
                        if vwap_pass and squeeze_pass and rsi_pass and time_lock_pass and mtf_aligned:
                            entry_strength = 'PROBE' if is_probe_entry else 'STRONG'
                            probe_tag = "🔍探针轻仓" if is_probe_entry else ""
                            signals['signals'].append({
                                'type': 'SELL', 'action': 'ENTRY',
                                'indicator': 'MACD+EMA/ADX+VWAP+Squeeze+RSI+SpaceLock+Defense', 'strength': entry_strength,
                                'message': f"做空信号{probe_tag}：MACD死叉+动能确认+机构成本过滤+{'探针豁免' if is_probe_entry else '挤压释放'}+RSI空间+空间锁+防守通过 [{' '.join(filter_status)}]"
                            })
                        else:
                            if not is_backtest_mode:
                                print(f"   ⚠️ 做空信号被过滤: {' '.join(filter_status)}")
        
        return signals if signals['signals'] else None
    except Exception as e:
        print(f"⚠️ 生成交易信号失败: {e}")
        return None

# ==========================================
# 绩效统计（凯利公式基础数据）
# ==========================================

def get_performance_stats(lookback=50):
    """
    从交易历史中提取绩效统计数据（含小样本防护 + 凯利公式平滑处理）
    
    🔥 v3.0 新增：加权平滑防止参数坍塌
    - 将本次统计结果与历史固定基准值按 0.7:0.3 比例加权融合
    - 防止单笔极端回撤导致凯利系数归零
    
    Args:
        lookback: 回溯交易笔数（默认 50 笔）
    
    Returns:
        dict: {
            'win_rate': 胜率 (W),
            'profit_loss_ratio': 盈亏比 (R),
            'kelly_factor': 半凯利系数,
            'sample_size': 样本数量,
            'smoothed': 是否应用了平滑处理
        }
    """
    from config import TRADE_HISTORY, state_lock
    
    # 🔥 历史固定基准值（用于平滑融合）
    BASELINE_WIN_RATE = 0.50  # 基准胜率 50%
    BASELINE_PROFIT_LOSS_RATIO = 1.5  # 基准盈亏比 1.5
    SMOOTH_WEIGHT_CURRENT = 0.7  # 当前统计权重 70%
    SMOOTH_WEIGHT_BASELINE = 0.3  # 基准值权重 30%
    
    try:
        # 🔒 线程锁保护：读取 TRADE_HISTORY（防止并发平仓时数据不一致）
        with state_lock:
            # 获取最近 N 笔已平仓交易
            if not TRADE_HISTORY or len(TRADE_HISTORY) == 0:
                # 无历史数据，返回保守默认值
                return {
                    'win_rate': BASELINE_WIN_RATE,
                    'profit_loss_ratio': BASELINE_PROFIT_LOSS_RATIO,
                    'kelly_factor': 1.0,  # 使用原始 RISK_RATIO
                    'sample_size': 0,
                    'smoothed': False
                }
            
            # 提取最近 lookback 笔交易
            recent_trades = TRADE_HISTORY[-lookback:] if len(TRADE_HISTORY) >= lookback else TRADE_HISTORY
        
        wins = []
        losses = []
        
        for trade in recent_trades:
            pnl = trade.get('pnl', 0) or trade.get('net_pnl', 0)
            if pnl > 0:
                wins.append(pnl)
            elif pnl < 0:
                losses.append(abs(pnl))
        
        total_trades = len(wins) + len(losses)
        
        if total_trades == 0:
            return {
                'win_rate': BASELINE_WIN_RATE,
                'profit_loss_ratio': BASELINE_PROFIT_LOSS_RATIO,
                'kelly_factor': 1.0,
                'sample_size': 0,
                'smoothed': False
            }
        
        # 🔥 小样本防护：样本量 < 10 时强制返回保守默认值
        # 防止初期 1-2 笔偶然连胜/连败导致盈亏比 R 计算扭曲，引发仓位管理失控
        MIN_SAMPLE_SIZE = 10
        if total_trades < MIN_SAMPLE_SIZE:
            print(f"   ⚠️ 凯利公式样本量不足 ({total_trades} < {MIN_SAMPLE_SIZE})，使用保守默认值 Kelly=1.0")
            return {
                'win_rate': len(wins) / total_trades if total_trades > 0 else BASELINE_WIN_RATE,
                'profit_loss_ratio': BASELINE_PROFIT_LOSS_RATIO,  # 保守默认值
                'kelly_factor': 1.0,  # 小样本期间不调整仓位
                'sample_size': total_trades,
                'smoothed': False
            }
        
        # 计算原始胜率 (W)
        win_rate_raw = len(wins) / total_trades if total_trades > 0 else BASELINE_WIN_RATE
        
        # 计算原始盈亏比 (R) = 平均盈利 / 平均亏损
        avg_win = sum(wins) / len(wins) if wins else 0
        avg_loss = sum(losses) / len(losses) if losses else 1
        profit_loss_ratio_raw = avg_win / avg_loss if avg_loss > 0 else BASELINE_PROFIT_LOSS_RATIO
        
        # 🔥 v3.0 加权平滑处理：防止参数坍塌
        # 将本次统计结果与历史固定基准值按 0.7:0.3 比例加权融合
        win_rate_smoothed = (
            SMOOTH_WEIGHT_CURRENT * win_rate_raw + 
            SMOOTH_WEIGHT_BASELINE * BASELINE_WIN_RATE
        )
        
        profit_loss_ratio_smoothed = (
            SMOOTH_WEIGHT_CURRENT * profit_loss_ratio_raw + 
            SMOOTH_WEIGHT_BASELINE * BASELINE_PROFIT_LOSS_RATIO
        )
        
        # 使用平滑后的参数计算凯利系数
        win_rate = win_rate_smoothed
        profit_loss_ratio = profit_loss_ratio_smoothed
        
        # 半凯利公式: f* = 0.5 * (W * R - (1 - W)) / R
        # 防御性处理：确保分母不为 0
        if profit_loss_ratio > 0:
            kelly_raw = 0.5 * (win_rate * profit_loss_ratio - (1 - win_rate)) / profit_loss_ratio
        else:
            kelly_raw = 0.5
        
        # 强制限制在 0.5 到 1.5 倍之间（数学稳健性）
        kelly_raw = max(0.5, min(1.5, kelly_raw))
        
        # 🔥 新增：渐进式平滑处理（防配资突变）
        if total_trades < 10:
            kelly_factor = 0.5  # 极度保守
        elif total_trades < 30:
            # 线性插值平滑过渡
            progress = (total_trades - 10) / 20.0
            kelly_factor = 0.5 + progress * (kelly_raw - 0.5)
        else:
            kelly_factor = kelly_raw
        
        # 🔥 日志输出：显示平滑前后对比
        print(f"   📊 凯利公式平滑处理:")
        print(f"      原始: W={win_rate_raw:.2%}, R={profit_loss_ratio_raw:.2f}")
        print(f"      平滑: W={win_rate:.2%}, R={profit_loss_ratio:.2f} (权重 {SMOOTH_WEIGHT_CURRENT:.0%}:{SMOOTH_WEIGHT_BASELINE:.0%})")
        print(f"      凯利系数: {kelly_factor:.2f}")
        
        return {
            'win_rate': win_rate,
            'profit_loss_ratio': profit_loss_ratio,
            'kelly_factor': kelly_factor,
            'sample_size': total_trades,
            'smoothed': True,
            'win_rate_raw': win_rate_raw,  # 保留原始值用于调试
            'profit_loss_ratio_raw': profit_loss_ratio_raw
        }
        
    except Exception as e:
        print(f"⚠️ 计算绩效统计失败: {e}")
        return {
            'win_rate': BASELINE_WIN_RATE,
            'profit_loss_ratio': BASELINE_PROFIT_LOSS_RATIO,
            'kelly_factor': 1.0,
            'sample_size': 0,
            'smoothed': False
        }


# ==========================================
# 仓位计算（凯利公式动态配资）
# ==========================================

def calculate_position_size(client, symbol, price, signal_strength, atr=0):
    """
    计算仓位大小（含凯利公式动态调整 + 波动率缩放）
    
    核心逻辑：
    1. 半凯利公式：f* = 0.5 × (W×R - (1-W)) / R
    2. 安全检查：样本量 < 20 时使用保守默认值
    3. 单笔风险上限：不超过 BENCHMARK_CASH 的 5%
    4. 波动率缩放：仓位 ∝ 1/ATR（高波动降仓位）
    """
    try:
        # 获取当前账户净值
        if client:
            try:
                acc = client.futures_account()
                total_equity = float(acc['totalMarginBalance'])
            except:
                total_equity = SYSTEM_CONFIG["BENCHMARK_CASH"]
        else:
            total_equity = SYSTEM_CONFIG["BENCHMARK_CASH"]
        
        current_profit = total_equity - SYSTEM_CONFIG["BENCHMARK_CASH"]
        base_risk_capital = SYSTEM_CONFIG["BENCHMARK_CASH"] * SYSTEM_CONFIG["RISK_RATIO"]
        
        # 🔥 疯狗模式检查
        force_mad_dog = SYSTEM_CONFIG.get("FORCE_MAD_DOG_MODE", False)
        is_mad_dog_active = False
        
        if force_mad_dog:
            is_mad_dog_active = True
            print(f"🔥 强制疯狗模式已激活！(FORCE_MAD_DOG_MODE=True)")
        elif SYSTEM_CONFIG["MAD_DOG_MODE"] and current_profit > 0:
            profit_ratio = total_equity / SYSTEM_CONFIG["BENCHMARK_CASH"]
            if profit_ratio >= SYSTEM_CONFIG["MAD_DOG_TRIGGER"]:
                is_mad_dog_active = True
        
        if is_mad_dog_active:
            allocated_capital = base_risk_capital * SYSTEM_CONFIG["MAD_DOG_BOOST"]
            print(f"🔥 疯狗模式激活！应用 {SYSTEM_CONFIG['MAD_DOG_BOOST']}x 资金乘数")
        else:
            allocated_capital = base_risk_capital
        
        # ==========================================
        # 🔥 半凯利公式动态配资（含安全检查）
        # ==========================================
        perf_stats = get_performance_stats(lookback=50)
        kelly_factor = perf_stats['kelly_factor']
        sample_size = perf_stats['sample_size']
        
        # 安全检查1：样本量不足 20 笔时，强制使用保守系数 1.0
        MIN_SAMPLE_SIZE = 20
        if sample_size < MIN_SAMPLE_SIZE:
            kelly_factor = 1.0
            print(f"   ⚠️ 样本量不足 ({sample_size} < {MIN_SAMPLE_SIZE})，凯利系数强制为 1.0")
        
        # 应用凯利系数调整资金分配
        allocated_capital = allocated_capital * kelly_factor
        
        # 资产权重分配
        # 优先读取自定义配置（解决回测资金被锁死的问题）
        cfg = SYSTEM_CONFIG
        if 'custom_config' in globals() and globals()['custom_config'] is not None:
            cfg = globals()['custom_config']

        # 强制读取，如果没有则默认 1.0 (全仓测试)，而不是 0.1
        weight = cfg.get("ASSET_WEIGHTS", {}).get(symbol, 1.0)
        allocated_capital = allocated_capital * weight
        
        # ==========================================
        # 🔥 波动率缩放：仓位 ∝ 1/ATR
        # ==========================================
        if atr > 0:
            # 获取 ATR 基准值（可配置，默认使用当前 ATR）
            atr_baseline = SYSTEM_CONFIG.get("ATR_BASELINE", atr)
            
            # 波动率缩放因子：ATR 越大，仓位越小
            volatility_scalar = min(atr_baseline / atr, 2.0)  # 上限 2x，防止极端缩放
            allocated_capital = allocated_capital * volatility_scalar
            
            print(f"   📉 波动率缩放: ATR={atr:.4f}, 基准={atr_baseline:.4f}, 缩放={volatility_scalar:.2f}x")
        
        # 计算仓位价值
        leverage = SYSTEM_CONFIG["LEVERAGE"]
        position_value = allocated_capital * leverage
        quantity = position_value / price
        
        # ==========================================
        # 🔥 安全检查2：单笔风险上限（5% BENCHMARK_CASH）
        # ==========================================
        MAX_SINGLE_RISK = 0.05  # 5%
        max_position_value = SYSTEM_CONFIG["BENCHMARK_CASH"] * MAX_SINGLE_RISK * leverage
        
        if position_value > max_position_value:
            print(f"   🛡️ 单笔风险超限！原仓位=${position_value:.2f} > 上限=${max_position_value:.2f}")
            position_value = max_position_value
            quantity = position_value / price
            allocated_capital = position_value / leverage
        
        # 记录凯利系数用于日志
        print(f"   📊 凯利配资: W={perf_stats['win_rate']:.2%}, R={perf_stats['profit_loss_ratio']:.2f}, "
              f"Kelly={kelly_factor:.2f} (样本={sample_size})")
        
        # 精度处理
        from config import symbol_precisions
        precision = symbol_precisions.get(symbol, 3)
        
        if quantity < (10 ** -precision):
            quantity = (10 ** -precision)
        
        # 强制应用精度步进，确保符合 LOT_SIZE 的 stepSize 规则
        quantity = round_to_quantity_precision(quantity, symbol)
        
        return {
            'quantity': quantity,
            'position_value': round(position_value, 2),
            'leverage': leverage,
            'allocated_capital': round(allocated_capital, 2),
            'is_mad_dog': is_mad_dog_active,
            'kelly_factor': kelly_factor,
            'win_rate': perf_stats['win_rate'],
            'profit_loss_ratio': perf_stats['profit_loss_ratio'],
            'sample_size': sample_size,
            'volatility_scalar': volatility_scalar if atr > 0 else 1.0
        }
    except Exception as e:
        print(f"⚠️ 计算仓位大小失败: {e}")
        return None

# ==========================================
# 订单执行逻辑（从 v1.0.py 提取）
# ==========================================

class OrderTransaction:
    """
    订单事务管理器 - 批量下单原子化版本
    使用 Binance futures_place_batch_orders API 实现真正的原子性：
    主订单（MARKET/LIMIT）+ 止损单（STOP_MARKET）在同一个批量请求中提交
    """
    # 🔥 类级别锁和注册表：防止并发回滚竞态条件
    _rollback_lock = threading.Lock()
    _rollback_registry = set()
    
    def __init__(self, client, symbol, position_type):
        self.client = client
        self.symbol = symbol
        self.position_type = position_type
        self.main_order_id = None
        self.stop_loss_order_id = None
        self.committed = False
        self.rollback_attempted = False
        self.batch_response = None  # 🔥 存储批量下单响应
        
    def submit_batch_orders(self, main_order_params, stop_loss_params):
        """
        🔥 批量下单原子化提交（主订单 + 止损单）
        
        Args:
            main_order_params: 主订单参数字典
            stop_loss_params: 止损单参数字典
        
        Returns:
            tuple: (main_order, sl_order) 或抛出异常
        """
        try:
            # 构建批量订单列表
            batch_orders = [main_order_params, stop_loss_params]
            
            print(f"🔥 [批量下单] 开始原子化提交: {self.symbol}")
            print(f"   主订单: {main_order_params.get('type')} {main_order_params.get('side')} {main_order_params.get('quantity')}")
            print(f"   止损单: STOP_MARKET stopPrice={stop_loss_params.get('stopPrice')}")
            
            # 🔥 调用 Binance 批量下单 API
            # 注意：批量下单返回的是列表，按提交顺序对应
            response = self.client.futures_place_batch_orders(batchOrders=batch_orders)
            
            self.batch_response = response
            
            # 解析响应（按顺序：[0]=主订单, [1]=止损单）
            if not response or len(response) < 2:
                raise Exception(f"批量下单响应异常: 期望2个订单，实际收到{len(response) if response else 0}个")
            
            main_order = response[0]
            sl_order = response[1]
            
            # 检查主订单状态
            if 'code' in main_order:
                # 主订单失败
                error_msg = main_order.get('msg', '未知错误')
                raise Exception(f"主订单提交失败: {error_msg}")
            
            # 检查止损单状态
            if 'code' in sl_order:
                # 止损单失败 - 这是致命错误，需要立即回滚主订单
                error_msg = sl_order.get('msg', '未知错误')
                self.main_order_id = main_order.get('orderId')
                raise Exception(f"止损单提交失败: {error_msg}，主订单已成交需回滚")
            
            # 两个订单都成功
            self.main_order_id = main_order.get('orderId')
            self.stop_loss_order_id = sl_order.get('orderId')
            
            print(f"✅ [批量下单] 原子化提交成功")
            print(f"   主订单ID: {self.main_order_id}")
            print(f"   止损单ID: {self.stop_loss_order_id}")
            
            return main_order, sl_order
            
        except Exception as e:
            error_msg = str(e)
            print(f"❌ [批量下单] 原子化提交失败: {error_msg}")
            
            # 🔥 批量下单失败时的精准异常处理
            # 场景1: 整个批量请求被拒绝（网络/权限问题）- 无需回滚
            # 场景2: 主订单成功但止损单失败 - 需要立即回滚主订单
            if self.main_order_id:
                print(f"⚠️ [批量下单] 检测到主订单已成交，触发紧急回滚...")
                self.rollback()
            
            raise Exception(f"批量下单原子化失败: {error_msg}")
    
    def commit(self):
        """提交事务（批量下单模式下此方法仅做验证）"""
        if self.main_order_id and self.stop_loss_order_id:
            self.committed = True
            print(f"✅ 订单事务已确认 (主单: {self.main_order_id}, 止损: {self.stop_loss_order_id})")
            return True
        else:
            print(f"❌ 订单事务不完整，无法确认")
            if self.main_order_id:
                # 如果主订单存在但止损单缺失，触发回滚
                print(f"⚠️ 检测到主订单存在但止损单缺失，触发回滚...")
                self.rollback()
            return False
    
    def rollback(self):
        """
        🔥 批量下单模式回滚 - 精准处理部分成交 + 死信队列集成 + 幂等性保护
        
        批量下单失败场景分析：
        1. 整个批量请求被拒绝 -> 无订单成交，无需回滚
        2. 主订单成功但止损单失败 -> 需要立即回滚主订单（本方法处理此场景）
        
        🔒 线程安全保证：
        - 使用类级别锁防止并发回滚
        - 使用注册表防止重复回滚
        - 幂等性参数确保反向清算不重复执行
        """
        # 🔥 线程安全检查：使用类级别锁包裹整个回滚逻辑
        with self._rollback_lock:
            # 幂等性检查1：检查是否已在注册表中
            if self.main_order_id in self._rollback_registry:
                print(f"⚠️ [批量下单回滚] 订单 {self.main_order_id} 已在回滚注册表中，跳过重复操作")
                return
            
            # 幂等性检查2：检查实例级别标记
            if self.rollback_attempted:
                print(f"⚠️ [批量下单回滚] 实例已尝试过回滚，跳过重复操作")
                return
            
            # 标记回滚开始：同时更新注册表和实例标记
            if self.main_order_id:
                self._rollback_registry.add(self.main_order_id)
            self.rollback_attempted = True
            
            print(f"🔒 [批量下单回滚] 已获取回滚锁，订单 {self.main_order_id} 已加入注册表")
        
        rollback_success = True
        filled_qty = 0
        
        # ==========================================
        # 🔥 批量下单回滚：主订单极速清算
        # ==========================================
        if self.main_order_id:
            try:
                print(f"🔥 [批量下单回滚] 开始处理主订单: {self.main_order_id}")
                
                # 步骤1: 尝试撤单（阻断继续成交）
                try:
                    self.client.futures_cancel_order(
                        symbol=self.symbol,
                        orderId=self.main_order_id
                    )
                    print(f"   ✅ 主订单撤单指令已发送")
                except Exception as cancel_e:
                    # 订单可能已完全成交，继续查询状态
                    print(f"   ⚠️ 撤单失败（可能已成交）: {str(cancel_e)[:100]}")
                
                # 步骤2: 查询订单最终状态
                order_status = self.client.futures_get_order(
                    symbol=self.symbol,
                    orderId=self.main_order_id
                )
                
                filled_qty = float(order_status.get('executedQty', 0))
                order_status_str = order_status.get('status', 'UNKNOWN')
                
                print(f"   📊 订单状态: {order_status_str}, 成交数量: {filled_qty}")
                
                # 步骤3: 如果有成交，立即市价反向平仓
                if filled_qty > 0:
                    print(f"   ⚠️ 检测到成交数量 [{filled_qty}]，执行紧急反向平仓...")
                    
                    original_side = order_status['side']
                    reverse_side = 'SELL' if original_side == 'BUY' else 'BUY'
                    entry_price = float(order_status.get('avgPrice', 0))
                    
                    try:
                        # 构建反向平仓参数
                        rollback_params = {
                            'symbol': self.symbol,
                            'side': reverse_side,
                            'type': 'MARKET',
                            'quantity': filled_qty
                        }
                        
                        if SYSTEM_CONFIG.get("HEDGE_MODE_ENABLED", False):
                            rollback_params['positionSide'] = self.position_type
                            print(f"   🔀 对冲模式回滚: positionSide={self.position_type}")
                        else:
                            rollback_params['positionSide'] = 'BOTH'
                            rollback_params['reduceOnly'] = True
                        
                        # 🔥 幂等性参数：使用唯一的 newClientOrderId 防止重复提交
                        rollback_client_order_id = f"ROLL_{self.main_order_id}_{int(time.time())}"
                        rollback_params['newClientOrderId'] = rollback_client_order_id
                        
                        # 执行反向平仓
                        close_order = self.client.futures_create_order(**rollback_params)
                        print(f"   ✅ 反向平仓成功，订单ID: {close_order.get('orderId')}, ClientOrderId: {rollback_client_order_id}")
                        
                        # 发送告警通知
                        from utils import send_tg_alert
                        import html
                        send_tg_alert(
                            f"🚨 <b>[批量下单回滚成功]</b>\n\n"
                            f"币种: {html.escape(self.symbol)}\n"
                            f"原因: 批量下单中止损单失败\n"
                            f"状态: {order_status_str}\n"
                            f"成交数量: {filled_qty}\n"
                            f"处置: 已市价反向平仓\n\n"
                            f"✅ 风险敞口已清除"
                        )
                        
                    except Exception as close_e:
                        print(f"   ❌ 反向平仓失败: {close_e}")
                        rollback_success = False
                        
                        # 🔥 回滚失败：从注册表移除，允许 DLQ 重试
                        try:
                            with self._rollback_lock:
                                self._rollback_registry.discard(self.main_order_id)
                            print(f"   🔓 [批量下单回滚] 回滚失败，已从注册表移除 {self.main_order_id}，允许 DLQ 重试")
                        except Exception as reg_e:
                            print(f"   ⚠️ 从注册表移除失败: {reg_e}")
                        
                        # 🔥 加入死信队列
                        from dlq_worker import add_to_dlq
                        add_to_dlq(
                            symbol=self.symbol,
                            position_type=self.position_type,
                            qty=filled_qty,
                            entry_price=entry_price,
                            trade_id=str(self.main_order_id),
                            error_reason=f"批量下单回滚失败: {str(close_e)[:100]}"
                        )
                        
                        from utils import send_tg_alert
                        import html
                        send_tg_alert(
                            f"🔴 <b>[致命警告：批量下单回滚失败]</b>\n\n"
                            f"币种: {html.escape(self.symbol)}\n"
                            f"方向: {self.position_type}\n"
                            f"成交数量: {filled_qty}\n"
                            f"主订单ID: {self.main_order_id}\n\n"
                            f"⚠️ 该持仓处于无止损裸奔状态！\n"
                            f"🔥 已加入死信队列，清道夫将持续重试平仓\n\n"
                            f"错误: {html.escape(str(close_e)[:100])}"
                        )
                else:
                    print(f"   ✅ 订单未成交或已完全撤销，无需平仓")
                    
            except Exception as e:
                print(f"   ❌ 回滚过程异常: {e}")
                rollback_success = False
                
                # 🔥 异常处理：从注册表移除，允许后续重试
                try:
                    if self.main_order_id:
                        with self._rollback_lock:
                            self._rollback_registry.discard(self.main_order_id)
                        print(f"   🔓 [批量下单回滚] 异常处理，已从注册表移除 {self.main_order_id}")
                except Exception as reg_e:
                    print(f"   ⚠️ 异常处理中从注册表移除失败: {reg_e}")
        
        # ==========================================
        # 止损单清理（批量下单模式下通常不存在）
        # ==========================================
        if self.stop_loss_order_id:
            try:
                self.client.futures_cancel_order(
                    symbol=self.symbol,
                    orderId=self.stop_loss_order_id
                )
                print(f"   ✅ 止损单已撤销: {self.stop_loss_order_id}")
            except Exception as e:
                # 批量下单失败时止损单通常不会成功创建
                pass
        
        if not rollback_success:
            print(f"🚨 [批量下单回滚] 回滚未完全成功，请立即手动检查！")
            from utils import send_tg_alert
            send_tg_alert(
                f"🔴 <b>[紧急警告]</b>\n\n"
                f"{self.symbol} 批量下单回滚失败\n"
                f"可能存在无止损敞口\n"
                f"请立即登录币安APP手动检查！"
            )
        
        return rollback_success


def execute_trade(client, symbol, signal_type, price, position_info, atr=0, adx=0, position_action='ENTRY', custom_config=None):
    """执行交易（V5.0 Maker优先算法 + 事务支持 + ADX动态止损）"""
    from binance.enums import SIDE_BUY, SIDE_SELL, FUTURE_ORDER_TYPE_MARKET, FUTURE_ORDER_TYPE_LIMIT
    from utils import send_tg_alert, round_to_tick_size
    import html
    
    try:
        # 🔥 配置隔离：优先使用传入的 custom_config，否则回退到全局 SYSTEM_CONFIG
        cfg = custom_config if custom_config is not None else SYSTEM_CONFIG
        current_atr_mult = 1.8 if cfg.get("LOW_VOL_MODE", False) else cfg.get("ATR_MULT", 2.3)
        
        # 🔥 任务2.3：ADX 动态止损缩放因子
        adx_scalar = 1.0
        if adx > 30:
            adx_scalar = 1.25  # 强趋势：放宽止损 1.25x
        elif adx < 20:
            adx_scalar = 0.8   # 弱趋势：收紧止损 0.8x
        
        # 计算止损价
        if position_action == 'ENTRY':
            if signal_type == 'BUY':
                if atr > 0:
                    stop_loss_distance = atr * current_atr_mult * adx_scalar * cfg.get("SL_BUFFER", 1.05)
                    stop_loss_price = price - stop_loss_distance
                    
                    # 🔥 日志输出：显示动态止损调整
                    if adx_scalar != 1.0:
                        print(f"📉 动态止损介入：当前 ADX={adx:.1f}, 止损{'放宽' if adx_scalar > 1 else '收紧'} {adx_scalar}x")
                else:
                    stop_loss_price = price * 0.98
                stop_loss_price = round_to_tick_size(stop_loss_price, symbol)
            elif signal_type == 'SELL':
                if atr > 0:
                    stop_loss_distance = atr * current_atr_mult * adx_scalar * cfg.get("SL_BUFFER", 1.05)
                    stop_loss_price = price + stop_loss_distance
                    
                    # 🔥 日志输出：显示动态止损调整
                    if adx_scalar != 1.0:
                        print(f"📉 动态止损介入：当前 ADX={adx:.1f}, 止损{'放宽' if adx_scalar > 1 else '收紧'} {adx_scalar}x")
                else:
                    stop_loss_price = price * 1.02
                stop_loss_price = round_to_tick_size(stop_loss_price, symbol)

        # ==========================================
        # 🛡️ 沙盒拦截网：DRY RUN 模式虚拟交易
        # ==========================================
        is_dry_run = SYSTEM_CONFIG.get("DRY_RUN", False)
        
        if is_dry_run:
            print(f"   🏖️ [沙盒演习模式] 拦截实盘调用，执行虚拟交易...")
            
            # 虚拟开仓 (ENTRY)
            if position_action == 'ENTRY':
                pos_type = 'LONG' if signal_type == 'BUY' else 'SHORT'
                key_sym = f"{symbol}_{pos_type}"
                
                # 生成虚拟 trade_id
                virtual_trade_id = f"SIM_{int(time.time() * 1000)}"
                
                # 构建虚拟订单记录
                virtual_position = {
                    'entry': price,
                    'sl': stop_loss_price,
                    'qty': position_info['quantity'],
                    'type': pos_type,
                    'real_symbol': symbol,
                    'timestamp': datetime.now(),
                    'trade_id': virtual_trade_id,
                    'sl_order_id': f"SL_{virtual_trade_id}",
                    'simulated': True,
                    'transaction_committed': True,
                    'order_identity': 'SANDBOX',
                    'fill_price': price,
                    'atr': atr  # 🔥 弹性收割 v2: 存储开仓时的ATR，用于自适应保本计算
                }
                
                # 🔒 线程锁保护：虚拟开仓写入 ACTIVE_POSITIONS
                with positions_lock:
                    if key_sym not in ACTIVE_POSITIONS:
                        ACTIVE_POSITIONS[key_sym] = []
                    elif not isinstance(ACTIVE_POSITIONS[key_sym], list):
                        ACTIVE_POSITIONS[key_sym] = [ACTIVE_POSITIONS[key_sym]]
                    
                    ACTIVE_POSITIONS[key_sym].append(virtual_position)
                
                save_data()
                
                print(f"   🏖️ [沙盒演习] 虚拟开仓成功: {symbol} {signal_type}")
                print(f"      Trade_ID: {virtual_trade_id}")
                print(f"      开仓价: {price}, 止损价: {stop_loss_price}")
                print(f"      数量: {position_info['quantity']}, 杠杆: {position_info['leverage']}x")
                
                send_tg_alert(
                    f"🏖️ <b>[沙盒演习-虚拟开仓]</b>\n"
                    f"币种: {html.escape(symbol)}\n"
                    f"动作: 开{'多' if pos_type=='LONG' else '空'} ({signal_type})\n"
                    f"开仓价: {price}\n"
                    f"止损位: {stop_loss_price}\n"
                    f"虚拟订单ID: {virtual_trade_id}\n"
                    f"⚠️ 沙盒模式：未调用实盘API"
                )
                
                return {
                    'success': True,
                    'trade_id': virtual_trade_id,
                    'sl_order_id': f"SL_{virtual_trade_id}",
                    'simulated': True,
                    'order_identity': 'SANDBOX',
                    'fill_price': price,
                    'message': f"沙盒虚拟开仓成功，止损价: ${stop_loss_price}"
                }
            
            # 虚拟平仓 (EXIT)
            elif position_action.startswith('EXIT'):
                pos_type = 'LONG' if position_action == 'EXIT_LONG' else 'SHORT'
                key_sym = f"{symbol}_{pos_type}"
                
                if key_sym not in ACTIVE_POSITIONS and symbol in ACTIVE_POSITIONS:
                    key_sym = symbol
                
                if key_sym in ACTIVE_POSITIONS:
                    # 🔒 线程锁保护：虚拟平仓修改/删除 ACTIVE_POSITIONS
                    with positions_lock:
                        positions_list = ACTIVE_POSITIONS[key_sym] if isinstance(ACTIVE_POSITIONS[key_sym], list) else [ACTIVE_POSITIONS[key_sym]]
                        
                        if not positions_list:
                            return {'success': False, 'message': f"沙盒模式：没有{symbol}的虚拟持仓可平仓"}
                        
                        position = positions_list.pop(0)
                    
                    # 计算虚拟盈亏（含手续费）
                    entry_price = position['entry']
                    exit_price = price
                    qty = position['qty']
                    
                    # 手续费：双边万四
                    commission = (entry_price + exit_price) * qty * SYSTEM_CONFIG["COMMISSION_RATE"]
                    
                    if position['type'] == 'LONG':
                        gross_pnl = (exit_price - entry_price) * qty
                    else:
                        gross_pnl = (entry_price - exit_price) * qty
                    
                    net_pnl = gross_pnl - commission
                    
                    # 更新沙盒模拟余额
                    with state_lock:
                        if "SIM_CURRENT_BALANCE" not in SYSTEM_CONFIG:
                            SYSTEM_CONFIG["SIM_CURRENT_BALANCE"] = SYSTEM_CONFIG.get("BENCHMARK_CASH", 10000.0)
                        
                        SYSTEM_CONFIG["SIM_CURRENT_BALANCE"] += net_pnl
                        current_balance = SYSTEM_CONFIG["SIM_CURRENT_BALANCE"]
                    
                    # 🔒 线程锁保护：更新持仓列表
                    with positions_lock:
                        if not positions_list:
                            ACTIVE_POSITIONS.pop(key_sym)
                        else:
                            ACTIVE_POSITIONS[key_sym] = positions_list
                    
                    # 记录到交易历史
                    from config import TRADE_HISTORY
                    trade_record = {
                        'symbol': symbol,
                        'type': position['type'],
                        'entry': entry_price,
                        'exit': exit_price,
                        'qty': qty,
                        'pnl': net_pnl,
                        'gross_pnl': gross_pnl,
                        'commission': commission,
                        'exit_reason': 'SANDBOX_SIGNAL_EXIT',
                        'trade_id': position['trade_id'],
                        'timestamp': datetime.now().isoformat(),
                        'simulated': True
                    }
                    with state_lock:
                        TRADE_HISTORY.append(trade_record)
                        if len(TRADE_HISTORY) > 1000:
                            TRADE_HISTORY[:] = TRADE_HISTORY[-1000:]
                    
                    save_data()
                    
                    print(f"   🏖️ [沙盒演习] 虚拟平仓成功: {symbol} 平{'多' if position['type']=='LONG' else '空'}")
                    print(f"      平仓价: {price}")
                    print(f"      毛利: ${gross_pnl:.2f}, 手续费: ${commission:.2f}, 净利: ${net_pnl:.2f}")
                    print(f"      沙盒余额: ${SYSTEM_CONFIG['SIM_CURRENT_BALANCE']:.2f}")
                    
                    send_tg_alert(
                        f"🏖️ <b>[沙盒演习-虚拟平仓]</b>\n"
                        f"币种: {html.escape(symbol)}\n"
                        f"方向: 平{'多' if position['type']=='LONG' else '空'}\n"
                        f"平仓价: {price}\n"
                        f"毛利: ${gross_pnl:.2f}\n"
                        f"手续费: ${commission:.2f}\n"
                        f"净利: ${net_pnl:.2f}\n"
                        f"虚拟订单ID: {position['trade_id']}\n"
                        f"沙盒余额: ${SYSTEM_CONFIG['SIM_CURRENT_BALANCE']:.2f}\n"
                        f"剩余子仓: {len(positions_list)} 笔\n"
                        f"⚠️ 沙盒模式：未调用实盘API"
                    )
                    
                    return {
                        'success': True,
                        'trade_id': position['trade_id'],
                        'pnl': net_pnl,
                        'gross_pnl': gross_pnl,
                        'commission': commission,
                        'simulated': True,
                        'message': f"沙盒虚拟平仓成功，净利: ${net_pnl:.2f}"
                    }
                else:
                    return {'success': False, 'message': f"沙盒模式：没有{symbol}的虚拟持仓可平仓"}

        # 实盘操作
        if client is None:
            return {'success': False, 'message': "币安客户端未连接"}
        
        # 开仓逻辑
        if position_action == 'ENTRY':
            pos_type = 'LONG' if signal_type == 'BUY' else 'SHORT'
            key_sym = f"{symbol}_{pos_type}"
            
            transaction = OrderTransaction(client, symbol, pos_type)
            
            try:
                # 设置杠杆
                client.futures_change_leverage(
                    symbol=symbol, 
                    leverage=int(position_info['leverage'])
                )
                
                # ====== 盘口滑点预检 ======
                act_side = SIDE_BUY if signal_type == 'BUY' else SIDE_SELL
                max_slip = SYSTEM_CONFIG.get("MAX_SLIPPAGE", 0.0015)
                slip_ok, slip_reason, est_price = check_orderbook_slippage(
                    client, symbol, signal_type, position_info['quantity'], max_slippage=max_slip
                )
                if not slip_ok:
                    msg = f"⚠️ [{symbol}] 滑点预检拒绝开仓: {slip_reason}"
                    print(msg)
                    send_tg_alert(f"⚠️ <b>[滑点预检]</b> {html.escape(symbol)}\n{html.escape(slip_reason)}")
                    return {'success': False, 'message': f"滑点过大，放弃开仓: {slip_reason}"}
                print(f"   ✅ 滑点预检通过 | 预计均价: {est_price:.4f}")
                
                # ====== 动态构建 positionSide 参数（对冲模式支持）======
                hedge_enabled = SYSTEM_CONFIG.get("HEDGE_MODE_ENABLED", False)
                order_params = {}
                
                if hedge_enabled:
                    # 对冲模式：强制指定 positionSide
                    order_params['positionSide'] = 'LONG' if signal_type == 'BUY' else 'SHORT'
                    print(f"   🔀 对冲模式：positionSide={order_params['positionSide']}")
                else:
                    # 单向模式：使用 BOTH 或不传参数
                    order_params['positionSide'] = 'BOTH'
                    print(f"   🔀 单向模式：positionSide=BOTH")
                
                # ====== 🔥 批量下单原子化：构建主订单和止损单参数 ======
                order_identity = "TAKER"  # 批量下单默认为TAKER
                actual_fill_price = price
                
                # 🔥 生成机器人订单标签
                bot_order_id = generate_bot_order_id() if POSITION_ISOLATION_ENABLED else None
                bot_sl_order_id = generate_bot_order_id() if POSITION_ISOLATION_ENABLED else None
                
                if bot_order_id:
                    logger.info(f"🏷️ 生成机器人订单标签: {bot_order_id}")
                
                # 构建主订单参数（MARKET订单）
                main_order_params = {
                    'symbol': symbol,
                    'side': act_side,
                    'type': FUTURE_ORDER_TYPE_MARKET,
                    'quantity': position_info['quantity'],
                }
                if bot_order_id:
                    main_order_params['newClientOrderId'] = bot_order_id
                main_order_params.update(order_params)  # 添加 positionSide
                
                # 构建止损单参数（STOP_MARKET订单）
                sl_side = SIDE_SELL if signal_type == 'BUY' else SIDE_BUY
                stop_loss_params = {
                    'symbol': symbol,
                    'side': sl_side,
                    'type': 'STOP_MARKET',
                    'quantity': position_info['quantity'],
                    'stopPrice': stop_loss_price,
                }
                if bot_sl_order_id:
                    stop_loss_params['newClientOrderId'] = bot_sl_order_id
                stop_loss_params.update(order_params)  # 添加 positionSide
                
                # 🔥 调用批量下单原子化提交
                main_order, sl_order = transaction.submit_batch_orders(
                    main_order_params, 
                    stop_loss_params
                )
                
                # 获取实际成交价
                actual_fill_price = float(main_order.get('avgPrice', price))
                
                # 提交事务
                if transaction.commit():
                    # 🔒 线程锁保护：实盘开仓写入 ACTIVE_POSITIONS
                    with positions_lock:
                        # 多重子仓位：实盘也使用列表存储
                        if key_sym not in ACTIVE_POSITIONS:
                            ACTIVE_POSITIONS[key_sym] = []
                        elif not isinstance(ACTIVE_POSITIONS[key_sym], list):
                            ACTIVE_POSITIONS[key_sym] = [ACTIVE_POSITIONS[key_sym]]
                        
                        ACTIVE_POSITIONS[key_sym].append({
                            'entry': actual_fill_price,
                            'sl': stop_loss_price,
                            'qty': position_info['quantity'],
                            'type': pos_type,
                            'real_symbol': symbol,
                            'timestamp': datetime.now(),
                            'trade_id': main_order['orderId'],
                            'sl_order_id': sl_order['orderId'],
                            'simulated': False,
                            'transaction_committed': True,
                            'order_identity': order_identity,  # 🔥 记录Maker/Taker身份
                            'fill_price': actual_fill_price,
                            'client_order_id': bot_order_id if bot_order_id else '',  # 🔥 记录机器人订单标签
                            'atr': atr  # 🔥 弹性收割 v2: 存储开仓时的ATR，用于自适应保本计算
                        })
                    save_data()
                    
                    identity_emoji = "💎" if order_identity == "MAKER" else "⚡"
                    send_tg_alert(
                        f"✅ <b>[实盘开仓确认]</b>\n"
                        f"币种: {html.escape(symbol)}\n"
                        f"动作: 开{'多' if pos_type=='LONG' else '空'} ({signal_type})\n"
                        f"开仓价: {actual_fill_price}\n"
                        f"止损位: {stop_loss_price}\n"
                        f"主订单ID: {main_order['orderId']}\n"
                        f"止损单ID: {sl_order['orderId']}\n"
                        f"执行方式: {identity_emoji} {order_identity}"
                    )
                    
                    return {
                        'success': True,
                        'trade_id': main_order['orderId'],
                        'sl_order_id': sl_order['orderId'],
                        'simulated': False,
                        'order_identity': order_identity,
                        'fill_price': actual_fill_price,
                        'message': f"开仓交易执行成功({order_identity})，止损价: ${stop_loss_price}"
                    }
                else:
                    return {'success': False, 'message': "订单事务提交失败，已回滚"}
                    
            except Exception as e:
                error_msg = str(e)
                print(f"❌ 订单提交异常: {error_msg}")
                return {
                    'success': False,
                    'message': f"订单提交失败并已回滚: {error_msg[:100]}",
                    'rollback_attempted': transaction.rollback_attempted
                }
        
        # 平仓逻辑
        elif position_action.startswith('EXIT'):
            pos_type = 'LONG' if position_action == 'EXIT_LONG' else 'SHORT'
            key_sym = f"{symbol}_{pos_type}"
            
            if key_sym not in ACTIVE_POSITIONS and symbol in ACTIVE_POSITIONS:
                key_sym = symbol
                
            if key_sym in ACTIVE_POSITIONS:
                # 🔒 线程锁保护：实盘平仓修改/删除 ACTIVE_POSITIONS
                with positions_lock:
                    # 多重子仓位：从列表中取出最早的订单（FIFO）
                    positions_list = ACTIVE_POSITIONS[key_sym] if isinstance(ACTIVE_POSITIONS[key_sym], list) else [ACTIVE_POSITIONS[key_sym]]
                    
                    if not positions_list:
                        return {'success': False, 'message': f"没有{symbol}的持仓可平仓"}
                    
                    position = positions_list.pop(0)
                    real_symbol = position.get('real_symbol', symbol)
                
                # 取消止损单
                try:
                    if position.get('sl_order_id'):
                        client.futures_cancel_order(symbol=real_symbol, orderId=position['sl_order_id'])
                except:
                    pass
                
                act_side = SIDE_SELL if position['type'] == 'LONG' else SIDE_BUY
                
                # ====== 动态构建 positionSide 参数（对冲模式平仓支持）======
                hedge_enabled = SYSTEM_CONFIG.get("HEDGE_MODE_ENABLED", False)
                close_params = {}
                
                if hedge_enabled:
                    # 对冲模式：平仓时必须指定 positionSide，不能传 reduceOnly
                    close_params['positionSide'] = position['type']  # 'LONG' or 'SHORT'
                    print(f"   🔀 对冲模式平仓：positionSide={close_params['positionSide']}")
                else:
                    # 单向模式：使用 BOTH + reduceOnly
                    close_params['positionSide'] = 'BOTH'
                    close_params['reduceOnly'] = True
                    print(f"   🔀 单向模式平仓：positionSide=BOTH, reduceOnly=True")
                
                # 平仓
                order = client.futures_create_order(
                    symbol=real_symbol,
                    side=act_side,
                    type=FUTURE_ORDER_TYPE_MARKET,
                    quantity=position['qty'],
                    **close_params
                )
                
                # 计算盈亏（含手续费）
                entry_price = position['entry']
                exit_price = price
                qty = position['qty']
                
                # 手续费：双边万四（开仓+平仓共两次）
                commission = (entry_price + exit_price) * qty * SYSTEM_CONFIG["COMMISSION_RATE"]
                
                if position['type'] == 'LONG':
                    gross_pnl = (exit_price - entry_price) * qty
                else:
                    gross_pnl = (entry_price - exit_price) * qty
                
                net_pnl = gross_pnl - commission
                
                # ====== 连续亏损统计（断路器触发检测）======
                global ENGINE_STATE
                if net_pnl < 0:
                    ENGINE_STATE['consecutive_losses'] += 1
                    max_consec = SYSTEM_CONFIG.get("MAX_CONSECUTIVE_LOSSES", 3)
                    if ENGINE_STATE['consecutive_losses'] >= max_consec:
                        # 触发断路器：禁止开仓 N 分钟
                        breaker_mins = SYSTEM_CONFIG.get("BREAKER_COOLDOWN_MINS", 30)
                        ENGINE_STATE['breaker_until'] = time.time() + (breaker_mins * 60)
                        send_tg_alert(
                            f"🚨 <b>[连续亏损断路器触发]</b>\n"
                            f"连续亏损: {ENGINE_STATE['consecutive_losses']} 笔\n"
                            f"冷却时间: {breaker_mins} 分钟\n"
                            f"期间将拒绝所有新开仓！"
                        )
                        print(f"🚨 连续亏损断路器触发！冷却 {breaker_mins} 分钟")
                else:
                    # 盈利则重置计数器
                    ENGINE_STATE['consecutive_losses'] = 0
                
                # 🔒 线程锁保护：更新持仓列表
                with positions_lock:
                    # 更新持仓列表：如果列表为空则删除key，否则保留剩余订单
                    if not positions_list:
                        ACTIVE_POSITIONS.pop(key_sym)
                    else:
                        ACTIVE_POSITIONS[key_sym] = positions_list
                
                # 🔥 补齐交易历史记录（实盘平仓）
                from config import TRADE_HISTORY
                trade_record = {
                    'symbol': symbol,
                    'type': position['type'],
                    'entry': entry_price,
                    'exit': exit_price,
                    'qty': qty,
                    'pnl': net_pnl,
                    'gross_pnl': gross_pnl,
                    'commission': commission,
                    'exit_reason': 'SIGNAL_EXIT',
                    'trade_id': order['orderId'],
                    'timestamp': datetime.now().isoformat()
                }
                with state_lock:
                    TRADE_HISTORY.append(trade_record)
                    if len(TRADE_HISTORY) > 1000:
                        TRADE_HISTORY[:] = TRADE_HISTORY[-1000:]
                
                # 🔥 利滚利：平仓后刷新 BENCHMARK_CASH，使凯利公式基于最新资金量计算
                _refresh_benchmark_after_close(client)
                
                save_data()
                
                # ====== 保险库自动触发：每次盈利平仓后检查是否达到抽水阈值 ======
                if net_pnl > 0:
                    try:
                        logger.info(f"💰 盈利平仓完成，触发保险库检查...")
                        vault_result = execute_vault_transfer(client)
                        if vault_result['success']:
                            logger.info(f"✅ 保险库自动划转成功: ${vault_result['amount']:.2f}")
                        else:
                            logger.debug(f"ℹ️ 保险库检查: {vault_result['message']}")
                    except Exception as vault_e:
                        logger.error(f"⚠️ 保险库自动检查异常: {vault_e}")
                
                send_tg_alert(
                    f"🛡️ <b>[实盘平仓确认]</b>\n"
                    f"币种: {html.escape(symbol)}\n"
                    f"方向: 平{'多' if position['type']=='LONG' else '空'}\n"
                    f"平仓价: {price}\n"
                    f"毛利: ${gross_pnl:.2f}\n"
                    f"手续费: ${commission:.2f}\n"
                    f"净利: ${net_pnl:.2f}\n"
                    f"交易ID: {order['orderId']}\n"
                    f"剩余子仓: {len(positions_list)} 笔\n"
                    f"连亏计数: {ENGINE_STATE['consecutive_losses']}"
                )
                
                return {
                    'success': True,
                    'trade_id': order['orderId'],
                    'pnl': net_pnl,
                    'gross_pnl': gross_pnl,
                    'commission': commission,
                    'simulated': False,
                    'message': f"平仓交易执行成功，净利: ${net_pnl:.2f}"
                }
            else:
                return {'success': False, 'message': f"没有{symbol}的持仓可平仓"}

    except Exception as e:
        error_msg = str(e)
        print(f"❌ 执行交易失败: {error_msg}")
        from utils import send_tg_alert
        import html
        send_tg_alert(f"❌ <b>[交易执行异常]</b>\n币种: {html.escape(symbol)}\n错误: {html.escape(error_msg[:100])}")
        return {'success': False, 'message': f"交易执行失败: {error_msg[:100]}"}


def sync_positions(client, chat_id):
    """同步币安真实仓位到本地（含止损单真实对账 + 自动补挂 + 🔥 外部手动单隔离）
    
    ⚠️ 重要：此函数在任何模式下都会穿透查询币安真实持仓
    不受 DRY_RUN 限制
    
    🔥 v3.0 新增：外部手动单隔离（安全加固）
    - 在遍历交易所返回的 real_positions 时，必须查询该持仓的原始 clientOrderId
    - 如果 clientOrderId 不是以 WJ_BOT 开头，严禁将其存入 ACTIVE_POSITIONS
    - 必须将其视为"外部手动单"并直接跳过，同时发送告警
    
    🔥 v2.0 新增：自动补挂止损单
    - 如果发现交易所存在持仓但不存在对应的 STOP_MARKET 挂单
    - 系统将立即自动补挂止损单，而不是仅仅发警告
    """
    from utils import send_tg_msg, send_tg_alert, round_to_tick_size
    import html
    
    if client is None:
        send_tg_msg("⚠️ 币安客户端未连接，无法同步真实仓位。")
        return
    
    # 移除所有模式限制，确保任何时候都能查询真实持仓
    send_tg_msg("🔄 <b>正在与交易所服务器进行对账同步...</b>")
    
    try:
        acc_info = client.futures_account()
        real_positions = acc_info.get('positions', [])
        
        synced_count = 0
        cleared_count = 0
        sl_matched_count = 0
        sl_missing_count = 0
        sl_auto_created_count = 0
        external_manual_orders_count = 0  # 🔥 外部手动单计数
        new_active = {}
        
        for pos in real_positions:
            amt = float(pos['positionAmt'])
            sym = pos['symbol']
            
            if amt != 0:
                pos_type = 'LONG' if amt > 0 else 'SHORT'
                qty = abs(amt)
                entry_p = float(pos['entryPrice'])
                key_sym = f"{sym}_{pos_type}"
                
                # ====== 🔥 v3.0 新增：外部手动单隔离检查 ======
                # 步骤1：查询该持仓对应的原始订单，获取 clientOrderId
                is_bot_position = False
                try:
                    print(f"   🔍 [{sym}] 正在验证持仓来源...")
                    
                    # 🔥 使用仓位隔离模块的 is_bot_order 函数
                    if POSITION_ISOLATION_ENABLED:
                        # 调用 futures_get_all_open_orders 获取所有挂单
                        open_orders = client.futures_get_all_open_orders(symbol=sym)
                        
                        # 查找该持仓方向对应的主订单（非止损单）
                        for order in open_orders:
                            if order.get('type') not in ['STOP_MARKET', 'TAKE_PROFIT_MARKET']:
                                order_side = order.get('side')
                                client_order_id = order.get('clientOrderId', '')
                                
                                # 检查订单方向是否与持仓方向匹配
                                if (pos_type == 'LONG' and order_side == 'BUY') or \
                                   (pos_type == 'SHORT' and order_side == 'SELL'):
                                    
                                    # 🔥 关键检查：使用 is_bot_order 验证
                                    if is_bot_order(client_order_id):
                                        is_bot_position = True
                                        print(f"   ✅ [{sym}] 检测到机器人订单: {client_order_id}")
                                        break
                                    else:
                                        # 外部手动单：不是机器人订单
                                        external_manual_orders_count += 1
                                        print(f"   🚫 [{sym}] 检测到外部手动单: {client_order_id}")
                                        
                                        # 发送告警
                                        send_tg_alert(
                                            f"🚫 <b>[外部手动单检测]</b>\n\n"
                                            f"币种: {html.escape(sym)}\n"
                                            f"方向: {'多头' if pos_type == 'LONG' else '空头'}\n"
                                            f"持仓量: {qty}\n"
                                            f"开仓价: {entry_p}\n"
                                            f"订单ID: {html.escape(client_order_id)}\n\n"
                                            f"⚠️ 该持仓不是机器人创建的，已被隔离\n"
                                            f"系统将跳过此持仓的管理"
                                        )
                                        break
                        
                        # 🔥 隔离逻辑：如果不是机器人订单，直接跳过此持仓
                        if not is_bot_position:
                            print(f"   🔒 [{sym}] 外部手动单已隔离，跳过此持仓的同步")
                            continue
                    else:
                        # 仓位隔离模块未启用，默认允许所有持仓
                        is_bot_position = True
                        print(f"   ⚠️ [{sym}] 仓位隔离模块未启用，跳过验证")
                
                except Exception as order_query_e:
                    print(f"   ⚠️ [{sym}] 查询订单信息异常: {order_query_e}")
                    # 查询异常时保守处理：跳过此持仓
                    send_tg_alert(
                        f"⚠️ <b>[订单查询异常]</b>\n\n"
                        f"币种: {html.escape(sym)}\n"
                        f"错误: {html.escape(str(order_query_e)[:100])}\n\n"
                        f"⚠️ 无法验证该持仓的订单来源，已跳过同步"
                    )
                    continue
                # ====== 原有逻辑：止损单对账 ======
                old_pos_data = ACTIVE_POSITIONS.get(key_sym) or ACTIVE_POSITIONS.get(sym) or {}
                
                # 🔥 防子单坍塌：如果本地子单数量 > 1 且总量与交易所一致，保留本地子单列表
                local_sub_orders = []
                if isinstance(old_pos_data, list) and len(old_pos_data) > 1:
                    local_sub_orders = old_pos_data
                elif isinstance(old_pos_data, list) and len(old_pos_data) == 1:
                    local_sub_orders = old_pos_data
                elif isinstance(old_pos_data, dict) and old_pos_data:
                    local_sub_orders = [old_pos_data]
                
                # 计算本地子单总数量
                local_total_qty = sum(sub.get('qty', 0) for sub in local_sub_orders)
                
                # 判断本地子单总量是否与交易所一致（允许万分之一精度误差）
                qty_tolerance = qty * 0.0001  # 万分之一
                local_matches_exchange = (
                    len(local_sub_orders) > 1 and
                    abs(local_total_qty - qty) <= max(qty_tolerance, 1e-8)
                )
                
                # 提取旧的止损估计值（兼容 dict 和 list）
                if isinstance(old_pos_data, dict) and old_pos_data:
                    sl_est = old_pos_data.get('sl', entry_p * (0.98 if pos_type == 'LONG' else 1.02))
                elif isinstance(old_pos_data, list) and old_pos_data:
                    sl_est = old_pos_data[0].get('sl', entry_p * (0.98 if pos_type == 'LONG' else 1.02))
                else:
                    sl_est = entry_p * (0.98 if pos_type == 'LONG' else 1.02)
                
                # ====== 止损单真实对账：从交易所查询实际挂单 ======
                real_sl_order_id = ""
                real_sl_price = sl_est
                sl_found = False
                
                try:
                    open_orders = client.futures_get_all_open_orders(symbol=sym)
                    # 止损单方向：多仓止损挂 SELL，空仓止损挂 BUY
                    expected_sl_side = 'SELL' if pos_type == 'LONG' else 'BUY'
                    
                    for order in open_orders:
                        if (order.get('type') == 'STOP_MARKET' and 
                            order.get('side') == expected_sl_side):
                            real_sl_order_id = order['orderId']
                            real_sl_price = float(order.get('stopPrice', sl_est))
                            sl_found = True
                            sl_matched_count += 1
                            print(f"   ✅ [{sym}] 找到止损单: orderId={real_sl_order_id}, stopPrice={real_sl_price}")
                            break
                    
                    # 🔥 v2.0 新增：自动补挂止损单
                    if not sl_found:
                        sl_missing_count += 1
                        print(f"   🔴 [{sym}] 未找到止损单！正在自动补挂...")
                        
                        # 计算止损价（使用保守的 2% 止损）
                        if pos_type == 'LONG':
                            auto_sl_price = entry_p * 0.98
                        else:
                            auto_sl_price = entry_p * 1.02
                        
                        auto_sl_price = round_to_tick_size(auto_sl_price, sym)
                        
                        # 构建止损单参数
                        sl_side = 'SELL' if pos_type == 'LONG' else 'BUY'
                        sl_order_params = {
                            'symbol': sym,
                            'side': sl_side,
                            'type': 'STOP_MARKET',
                            'quantity': qty,
                            'stopPrice': auto_sl_price,
                        }
                        
                        # 对冲模式需要指定 positionSide
                        if SYSTEM_CONFIG.get("HEDGE_MODE_ENABLED", False):
                            sl_order_params['positionSide'] = pos_type
                        else:
                            sl_order_params['positionSide'] = 'BOTH'
                            sl_order_params['reduceOnly'] = True
                        
                        try:
                            # 提交止损单
                            sl_order = client.futures_create_order(**sl_order_params)
                            real_sl_order_id = sl_order['orderId']
                            real_sl_price = auto_sl_price
                            sl_found = True
                            sl_auto_created_count += 1
                            
                            print(f"   ✅ [{sym}] 止损单自动补挂成功: orderId={real_sl_order_id}, stopPrice={auto_sl_price}")
                            send_tg_alert(
                                f"✅ <b>[止损单自动补挂成功]</b>\n\n"
                                f"币种: {html.escape(sym)}\n"
                                f"方向: {'多头' if pos_type == 'LONG' else '空头'}\n"
                                f"持仓量: {qty}\n"
                                f"开仓价: {entry_p}\n"
                                f"止损价: {auto_sl_price}\n"
                                f"止损单ID: {real_sl_order_id}\n\n"
                                f"🛡️ 系统已自动为该持仓补挂止损保护！"
                            )
                        except Exception as create_e:
                            print(f"   ❌ [{sym}] 自动补挂止损单失败: {create_e}")
                            send_tg_alert(
                                f"🔴 <b>[高危报警：止损单缺失且补挂失败]</b>\n\n"
                                f"币种: {html.escape(sym)}\n"
                                f"方向: {'多头' if pos_type == 'LONG' else '空头'}\n"
                                f"持仓量: {qty}\n"
                                f"开仓价: {entry_p}\n\n"
                                f"⚠️ 交易所未检测到对应的 STOP_MARKET 止损单！\n"
                                f"系统尝试自动补挂但失败: {html.escape(str(create_e)[:100])}\n\n"
                                f"🚨 该持仓当前处于<b>无止损裸奔</b>状态，请立即手动处理！"
                            )
                
                except Exception as e:
                    print(f"   ⚠️ [{sym}] 查询止损挂单异常: {e}")
                    send_tg_alert(
                        f"⚠️ <b>[止损对账异常]</b>\n"
                        f"币种: {html.escape(sym)}\n"
                        f"错误: {html.escape(str(e)[:100])}\n"
                        f"请手动检查该持仓的止损单状态！"
                    )
                
                # 🔥 防子单坍塌：本地多笔子单总量与交易所一致时，保留本地子单列表
                if local_matches_exchange:
                    print(f"   🔒 [{sym}] 本地 {len(local_sub_orders)} 笔子单总量={local_total_qty} ≈ 交易所={qty}，保留子单列表")
                    new_active[key_sym] = local_sub_orders
                else:
                    # 本地无子单或数量不匹配，用交易所数据生成单笔合并记录
                    old_pos = old_pos_data if isinstance(old_pos_data, dict) else (old_pos_data[0] if isinstance(old_pos_data, list) and old_pos_data else {})
                    synced_pos = {
                        'entry': entry_p,
                        'sl': real_sl_price,
                        'qty': qty,
                        'type': pos_type,
                        'real_symbol': sym,
                        'timestamp': old_pos.get('timestamp', datetime.now()) if isinstance(old_pos, dict) else datetime.now(),
                        'trade_id': old_pos.get('trade_id', f"SYNC_{int(time.time())}") if isinstance(old_pos, dict) else f"SYNC_{int(time.time())}",
                        'sl_order_id': real_sl_order_id if sl_found else (old_pos.get('sl_order_id', "") if isinstance(old_pos, dict) else ""),
                        'simulated': False,
                        'sl_verified': sl_found
                    }
                    
                    if key_sym not in new_active:
                        new_active[key_sym] = [synced_pos]
                    else:
                        new_active[key_sym].append(synced_pos)
                synced_count += 1
        
        for old_sym in ACTIVE_POSITIONS.keys():
            if old_sym not in new_active and not ACTIVE_POSITIONS[old_sym].get('simulated', False):
                cleared_count += 1
        
        # 🔒 线程锁保护：sync_positions 更新 ACTIVE_POSITIONS
        with positions_lock:
            ACTIVE_POSITIONS.clear()
            ACTIVE_POSITIONS.update(new_active)
        save_data()
        
        msg = "⚖️ <b>持仓对账完成</b>\n\n"
        msg += f"✅ 同步到真实持仓: {synced_count} 个\n"
        msg += f"🧹 清理本地死仓: {cleared_count} 个\n"
        msg += f"🛡️ 止损单已核实: {sl_matched_count} 个\n"
        if sl_auto_created_count > 0:
            msg += f"🔧 <b>止损单自动补挂: {sl_auto_created_count} 个</b>\n"
        if sl_missing_count > sl_auto_created_count:
            msg += f"🔴 <b>止损单缺失且补挂失败: {sl_missing_count - sl_auto_created_count} 个（请立即处理！）</b>\n"
        if external_manual_orders_count > 0:
            msg += f"🚫 <b>外部手动单已隔离: {external_manual_orders_count} 个</b>\n"
        
        send_tg_msg(msg)
        
    except Exception as e:
        send_tg_msg(f"❌ 同步异常: {str(e)[:100]}")


def emergency_close_all(client, chat_id):
    """一键全平功能（支持多重子仓位列表）"""
    from utils import send_tg_msg
    from binance.enums import SIDE_BUY, SIDE_SELL, FUTURE_ORDER_TYPE_MARKET
    
    with positions_lock:
        if not ACTIVE_POSITIONS:
            send_tg_msg("📭 本地记录当前没有活跃持仓可平。")
            return
        
        symbols_to_close = list(ACTIVE_POSITIONS.keys())
    
    send_tg_msg("⏳ <b>正在执行一键全平指令...</b>")
    closed_count = 0
    failed_syms = []
    
    for key_sym in symbols_to_close:
        try:
            # 🔥 支持列表形式的多笔订单
            positions_data = ACTIVE_POSITIONS[key_sym]
            if not isinstance(positions_data, list):
                positions_data = [positions_data]  # 兼容旧格式
            
            real_symbol = key_sym.split('_')[0] if '_' in key_sym else key_sym
            
            # 遍历该方向下的所有子订单
            for position in positions_data:
                try:
                    if not position.get('simulated', False) and client:
                        # 取消该订单的止损单
                        if position.get('sl_order_id'):
                            try:
                                client.futures_cancel_order(
                                    symbol=real_symbol, 
                                    orderId=position['sl_order_id']
                                )
                            except:
                                pass
                        
                        # 精准平仓：只平掉该笔订单的数量
                        act_side = SIDE_SELL if position['type'] == 'LONG' else SIDE_BUY
                        
                        # 动态构建平仓参数（对冲模式兼容）
                        close_params = {}
                        if SYSTEM_CONFIG.get("HEDGE_MODE_ENABLED", False):
                            close_params['positionSide'] = position['type']
                        else:
                            close_params['positionSide'] = 'BOTH'
                            close_params['reduceOnly'] = True
                        
                        client.futures_create_order(
                            symbol=real_symbol,
                            side=act_side,
                            type=FUTURE_ORDER_TYPE_MARKET,
                            quantity=position['qty'],
                            **close_params
                        )
                        print(f"   ✅ 已平仓 {key_sym} 子订单 [Trade_ID={position.get('trade_id')}], 数量: {position['qty']}")
                    
                    closed_count += 1
                    
                except Exception as sub_e:
                    print(f"   ❌ 平仓子订单失败 {key_sym} [Trade_ID={position.get('trade_id')}]: {sub_e}")
                    failed_syms.append(f"{key_sym}[{position.get('trade_id')}]")
            
            # 清空该方向的所有持仓
            with positions_lock:
                ACTIVE_POSITIONS.pop(key_sym, None)
            
        except Exception as e:
            failed_syms.append(key_sym)
            print(f"❌ [一键全平] 处理 {key_sym} 失败: {e}")
    
    save_data()
    
    msg = "🛑 <b>一键全平报告</b>\n\n"
    msg += f"✅ 成功平仓子订单数: {closed_count}\n"
    if failed_syms:
        msg += f"❌ 平仓失败: {', '.join(failed_syms)}\n"
    
    send_tg_msg(msg)


# ==========================================
# 🔥 MTF 多周期共振：高周期 EMA 趋势计算
# ==========================================

def _fetch_higher_tf_ema(client, symbol, custom_config=None, mtf_data=None):
    """
    获取高周期K线并计算 EMA_TREND，用于 MTF 趋势对齐校验

    🔥 v2.8: 回测模式全本地化 - 禁止API调用，强制使用 mtf_data

    Args:
        client: Binance客户端（回测模式下应为 None）
        symbol: 交易对
        custom_config: 自定义配置（回测模式传入）
        mtf_data: 多周期数据字典（回测模式必传）

    Returns:
        float or None: 高周期 EMA 值，失败返回 None
    """
    try:
        # 🔥 使用 custom_config 或全局 SYSTEM_CONFIG
        cfg = custom_config if custom_config is not None else SYSTEM_CONFIG
        
        preset_config = STRATEGY_PRESETS.get(cfg.get("STRATEGY_MODE", "STANDARD"), {})
        higher_interval = preset_config.get("HIGHER_INTERVAL", cfg.get("HIGHER_INTERVAL", "1h"))
        mtf_ema_length = preset_config.get("MTF_TREND_EMA", cfg.get("EMA_TREND", 89))
        
        # 🔥 回测模式检测：如果传入了 custom_config，强制使用本地数据
        is_backtest_mode = (custom_config is not None)
        
        if is_backtest_mode:
            # 回测模式：禁止API调用，必须使用 mtf_data
            if mtf_data is None:
                logger.debug(f"   ⚠️ [BACKTEST] {symbol} MTF数据未传入，跳过高周期过滤")
                return None
            
            # 从 mtf_data 中提取高周期数据
            if higher_interval not in mtf_data:
                logger.debug(f"   ⚠️ [BACKTEST] {symbol} 缺少 {higher_interval} 数据")
                return None
            
            df_htf = mtf_data[higher_interval]
            if df_htf is None or len(df_htf) < mtf_ema_length:
                logger.debug(f"   ⚠️ [BACKTEST] {symbol} {higher_interval} 数据不足")
                return None
            
            # 计算 EMA（使用已计算的指标或重新计算）
            if 'EMA_TREND' in df_htf.columns:
                # 如果已经计算过，直接使用
                last_ema = df_htf['EMA_TREND'].iloc[-1]
            else:
                # 重新计算
                import pandas_ta as ta
                ema_htf = ta.ema(df_htf['close'], length=mtf_ema_length)
                if ema_htf is None or len(ema_htf) == 0:
                    return None
                last_ema = ema_htf.iloc[-1]
            
            if pd.isna(last_ema):
                return None
            
            return float(last_ema)
        
        # 🔥 实盘模式：仅在有 client 时才调用 API
        if client is None:
            return None

        # 获取高周期K线（需要足够数据计算EMA）
        df_htf = get_historical_klines(client, symbol, higher_interval, limit=200)
        if df_htf is None or len(df_htf) < mtf_ema_length:
            return None

        import pandas_ta as ta
        ema_htf = ta.ema(df_htf['close'], length=mtf_ema_length)
        if ema_htf is None or len(ema_htf) == 0:
            return None

        last_ema = ema_htf.iloc[-1]
        if pd.isna(last_ema):
            return None

        return float(last_ema)
    except Exception as e:
        if custom_config is not None:
            logger.debug(f"   ⚠️ [BACKTEST] {symbol} 获取高周期EMA失败: {e}")
        else:
            print(f"   ⚠️ [{symbol}] 获取高周期EMA失败: {e}")
        return None



def is_mtf_aligned(current_price, higher_tf_ema, signal_type):
    """
    MTF 多周期共振对齐校验
    
    Args:
        current_price: 当前价格
        higher_tf_ema: 高周期 EMA_TREND 值
        signal_type: 'BUY' (做多) 或 'SELL' (做空)
    
    Returns:
        (aligned: bool, reason: str)
    """
    if higher_tf_ema is None:
        return True, "MTF数据不可用，跳过对齐检查"
    
    if signal_type == 'BUY':
        aligned = current_price > higher_tf_ema
        reason = f"MTF做多对齐: 价格{current_price:.4f} {'>' if aligned else '<='} HTF_EMA{higher_tf_ema:.4f}"
    else:
        aligned = current_price < higher_tf_ema
        reason = f"MTF做空对齐: 价格{current_price:.4f} {'<' if aligned else '>='} HTF_EMA{higher_tf_ema:.4f}"
    
    return aligned, reason


# ==========================================
# 交易引擎主循环
# ==========================================

def trading_engine_loop(client):
    """
    交易引擎主循环（🔥 V5.1 重构：数据流与交易流解耦）
    
    核心变更：
    - 数据获取 + 指标计算 + 信号判定：始终运行（24/7 常驻）
    - 开平仓执行：仅在 TRADING_ENGINE_ACTIVE=True 时执行
    - 即使交易暂停，仪表盘/AI分析仍可读取最新真实数据
    """
    print("🚀 交易引擎线程已启动（V5.1 数据流常驻模式）")
    
    # 初始化风控管理器（首次调用传入配置）
    try:
        risk_mgr = get_risk_manager(SYSTEM_CONFIG)
    except Exception as e:
        print(f"⚠️ 风控管理器初始化失败: {e}，将跳过风控检查")
        risk_mgr = None
    
    # 标记：是否已完成本次启动的持仓模式同步
    _hedge_mode_synced = False
    
    while True:
        try:
                        # ====== 🔥 数据流常驻：无论交易引擎是否激活，始终获取数据并计算指标 ======
                        
                        # 遍历所有监控币种（数据获取 + 指标计算 + 信号判定）
            for symbol in SYSTEM_CONFIG.get("MONITOR_SYMBOLS", []):
                try:
                    # 获取K线数据（始终执行）
                    df = get_historical_klines(
                        client, symbol, 
                        SYSTEM_CONFIG["INTERVAL"], 
                        limit=200
                    )
                    
                    if df is None or len(df) < 50:
                        continue
                    
                    # 计算技术指标（始终执行，保持指标实时更新）
                    use_latest = SYSTEM_CONFIG.get("USE_LATEST_CANDLE", False)
                    df = calculate_indicators(df, force_recalc=not use_latest)
                    if df is None:
                        continue
                    
                    # 🔥 将最新指标数据写入全局缓存，供仪表盘/AI分析读取
                    _update_indicator_cache(symbol, df)
                    
                    # 生成交易信号（始终执行，保持信号判定实时运行）
                    signals = generate_trading_signals(df, symbol, client=client)
                    
                    if signals and signals['signals']:
                        print(f"\n📊 {symbol} 检测到信号:")
                        for sig in signals['signals']:
                            print(f"   {sig['message']}")
                        
                        # ====== 🔥 交易执行拦截点：仅在引擎激活时执行开平仓 ======
                        from config import TRADING_ENGINE_ACTIVE
                        if not TRADING_ENGINE_ACTIVE:
                            for sig in signals['signals']:
                                print(f"   ⏸️ 信号触发，但交易引擎未激活，跳过执行: {sig['message']}")
                            continue
                        
                        # ====== 引擎点火：首次激活时同步币安持仓模式 ======
                        if not _hedge_mode_synced:
                            print("🔀 引擎点火：同步币安持仓模式...")
                            sync_ok, sync_msg = sync_hedge_mode_to_binance(client)
                            if not sync_ok:
                                print(f"🛑 持仓模式同步失败，引擎启动终止: {sync_msg}")
                                from config import TRADING_ENGINE_ACTIVE
                                TRADING_ENGINE_ACTIVE = False
                                continue
                            _hedge_mode_synced = True
                            print(f"✅ 持仓模式同步完成: {sync_msg}")
                        
                        # ====== 全局风控检查：最大回撤熔断 ======
                        if risk_mgr is not None:
                            try:
                                if client:
                                    acc = client.futures_account()
                                    current_equity = float(acc['totalMarginBalance'])
                                else:
                                    current_equity = SYSTEM_CONFIG["BENCHMARK_CASH"]
                                
                                if not risk_mgr.check_global_drawdown(current_equity):
                                    print("🚨 [风控] 全局最大回撤熔断已触发，暂停所有新开仓！")
                                    with positions_lock:
                                        status = risk_mgr.status_report(ACTIVE_POSITIONS, current_equity)
                                    print(f"   {status}")
                                    continue
                                
                            except Exception as risk_e:
                                print(f"⚠️ 风控检查异常: {risk_e}")
                        
                        # 处理信号（内部会再次进行头寸风控检查）
                        process_trading_signals(client, signals)
                    
                except Exception as e:
                    print(f"⚠️ 处理 {symbol} 时出错: {e}")
                    continue
            
            # ====== 🔥 新增：自动保本巡逻器 ======
            try:
                with positions_lock:
                    for key_sym, pos_list in list(ACTIVE_POSITIONS.items()):
                        if not isinstance(pos_list, list): pos_list = [pos_list]
                        for pos in pos_list:
                            # 如果已经保本，跳过
                            if pos.get('sl') == pos.get('entry'):
                                continue
                            
                            # 🔥 弹性收割 v2: 自适应保本逻辑（ATR-Based Breakeven）
                            # 从固定 0.8% 改为 1.5 * ATR，给高波动行情留出"回撤不扫损"的空间
                            current_price = get_current_price(client, pos.get('real_symbol', key_sym.split('_')[0]))
                            if current_price:
                                entry = pos['entry']
                                pos_atr = pos.get('atr', 0)
                                
                                if pos['type'] == 'LONG':
                                    float_profit = current_price - entry
                                else:
                                    float_profit = entry - current_price
                                
                                # 动态保本阈值：1.5 * ATR（如果 ATR 不可用，回退到固定 0.8%）
                                if pos_atr > 0:
                                    breakeven_threshold = 1.5 * pos_atr
                                    should_breakeven = float_profit >= breakeven_threshold
                                else:
                                    profit_pct = float_profit / entry if entry > 0 else 0
                                    breakeven_threshold = entry * 0.008
                                    should_breakeven = profit_pct >= 0.008
                                
                                if should_breakeven:
                                    trade_id = str(pos.get('trade_id', ''))
                                    # 🔥 保本价设为 EntryPrice * 1.001（微利保本，覆盖手续费）
                                    breakeven_price = entry * 1.001 if pos['type'] == 'LONG' else entry * 0.999
                                    res = update_sl_to_breakeven(trade_id, client=client, custom_breakeven_price=breakeven_price)
                                    if res['success']:
                                        threshold_info = f"ATR阈值={breakeven_threshold:.4f}" if pos_atr > 0 else "固定0.8%"
                                        print(f"🛡️ 订单 {trade_id} 浮盈={float_profit:.4f} >= {threshold_info}，已触发ATR自适应保本 → {breakeven_price:.4f}")
            except Exception as be_e:
                print(f"⚠️ 自动保本巡逻器异常: {be_e}")
            
            # 🔥 引擎停止后重置持仓模式同步标记
            from config import TRADING_ENGINE_ACTIVE
            if not TRADING_ENGINE_ACTIVE:
                _hedge_mode_synced = False
            
            # 休眠间隔
            sleep_time = SYSTEM_CONFIG.get("ENGINE_SLEEP", 60)
            time.sleep(sleep_time)
            
        except Exception as e:
            print(f"❌ 交易引擎异常: {e}")
            time.sleep(60)


# ==========================================
# 🔥 指标数据全局缓存（供仪表盘/AI分析读取）
# ==========================================
import threading

_indicator_cache = {}
_indicator_cache_lock = threading.Lock()


def _update_indicator_cache(symbol, df):
    """更新指标缓存（由交易引擎主循环调用）"""
    try:
        if df is None or len(df) == 0:
            return
        last = df.iloc[-1]
        with _indicator_cache_lock:
            _indicator_cache[symbol] = {
                'price': float(last.get('close', 0)),
                'ATR': float(last.get('ATR', 0)),
                'Relative_ATR': float(last.get('Relative_ATR', 0)),
                'ADX': float(last.get('ADX', 0)),
                'RSI': float(last.get('RSI', 50)),
                'MACD_hist': float(last.get('MACD_hist', 0)),
                'MACD_line': float(last.get('MACD_line', 0)),
                'MACD_signal': float(last.get('MACD_signal', 0)),
                'EMA_TREND': float(last.get('EMA_TREND', 0)),
                'VWAP': float(last.get('VWAP', 0)),
                'Squeeze_On': bool(last.get('Squeeze_On', False)),
                'volume': float(last.get('volume', 0)),
                'timestamp': datetime.now().isoformat(),
            }
    except Exception as e:
        print(f"⚠️ 更新指标缓存失败 {symbol}: {e}")


def get_indicator_cache(symbol=None):
    """
    获取指标缓存数据（供仪表盘/AI分析/外部模块调用）
    
    Args:
        symbol: 指定币种，None 返回全部
    
    Returns:
        dict: 指标数据
    """
    with _indicator_cache_lock:
        if symbol:
            return _indicator_cache.get(symbol, {}).copy()
        return {k: v.copy() for k, v in _indicator_cache.items()}


def process_trading_signals(client, signals, df=None, custom_config=None):
    """处理交易信号（含黑匣子审计链路 + TSL动态追踪止盈）"""
    # 🔥 配置隔离：优先使用传入的 custom_config
    cfg = custom_config if custom_config is not None else SYSTEM_CONFIG
    
    symbol = signals['symbol']
    price = signals['price']
    atr = signals['atr']
    # 🔥 任务2.2：提取 ADX 值并传递给 execute_trade
    adx = signals.get('adx', 0)
    
    # ==========================================
    # 🔥 v3.0 三阶段动态止损巡逻器 (Three-Stage Dynamic SL)
    # Stage 1: 初始护盾 (ATR_MULT * ATR)
    # Stage 2A: 风险减半 (浮盈 >= 1.0*ATR → SL移至 Entry ± 0.5*ATR)
    # Stage 2B: 智能保本 (浮盈 >= 1.8*ATR → SL移至 Entry * 1.001/0.999)
    # Stage 3: TSL收割 (浮盈 >= 2.5*ATR → 追踪止损模式)
    # ==========================================
    try:
        # 从配置读取三阶段参数
        stage_a_profit = cfg.get('STAGE_A_PROFIT_MULT', 1.0)
        stage_a_sl = cfg.get('STAGE_A_SL_MULT', 0.5)
        stage_b_profit = cfg.get('STAGE_B_PROFIT_MULT', 1.8)
        stage_b_offset = cfg.get('STAGE_B_SL_OFFSET', 0.001)
        tsl_trigger = cfg.get('TSL_TRIGGER_MULT', 2.5)
        tsl_callback = cfg.get('TSL_CALLBACK_MULT', 2.5)
        
        with positions_lock:
            for key_sym, pos_list in list(ACTIVE_POSITIONS.items()):
                if not isinstance(pos_list, list): 
                    pos_list = [pos_list]
                
                for pos in pos_list:
                    # 只处理当前币种的持仓
                    if pos.get('real_symbol', key_sym.split('_')[0]) != symbol:
                        continue
                    
                    entry = pos.get('entry', 0)
                    current_sl = pos.get('sl', 0)
                    pos_atr = pos.get('atr', atr)
                    pos_type = pos.get('type', 'LONG')
                    current_stage = pos.get('sl_stage', 1)
                    
                    if pos_atr <= 0 or entry <= 0:
                        continue
                    
                    # 计算浮盈
                    if pos_type == 'LONG':
                        float_profit = price - entry
                    else:
                        float_profit = entry - price
                    
                    new_sl = current_sl
                    new_stage = current_stage
                    stage_changed = False
                    
                    # ====== Stage 3: TSL 收割模式 (浮盈 >= tsl_trigger * ATR) ======
                    if float_profit >= tsl_trigger * pos_atr:
                        new_stage = 3
                        if pos_type == 'LONG':
                            highest_price = pos.get('highest_price', entry)
                            if price > highest_price:
                                pos['highest_price'] = price
                                highest_price = price
                            tsl_sl = highest_price - (tsl_callback * pos_atr)
                            if tsl_sl > current_sl:
                                new_sl = tsl_sl
                        else:  # SHORT
                            lowest_price = pos.get('lowest_price', entry)
                            if price < lowest_price:
                                pos['lowest_price'] = price
                                lowest_price = price
                            tsl_sl = lowest_price + (tsl_callback * pos_atr)
                            if tsl_sl < current_sl:
                                new_sl = tsl_sl
                    
                    # ====== Stage 2B: 智能保本 (浮盈 >= stage_b_profit * ATR) ======
                    elif float_profit >= stage_b_profit * pos_atr:
                        new_stage = max(current_stage, 2)
                        if pos_type == 'LONG':
                            breakeven_sl = entry * (1 + stage_b_offset)
                            if breakeven_sl > current_sl:
                                new_sl = breakeven_sl
                        else:
                            breakeven_sl = entry * (1 - stage_b_offset)
                            if breakeven_sl < current_sl:
                                new_sl = breakeven_sl
                    
                    # ====== Stage 2A: 风险减半 (浮盈 >= stage_a_profit * ATR) ======
                    elif float_profit >= stage_a_profit * pos_atr:
                        new_stage = max(current_stage, 2)
                        if pos_type == 'LONG':
                            half_risk_sl = entry - (stage_a_sl * pos_atr)
                            if half_risk_sl > current_sl:
                                new_sl = half_risk_sl
                        else:
                            half_risk_sl = entry + (stage_a_sl * pos_atr)
                            if half_risk_sl < current_sl:
                                new_sl = half_risk_sl
                    
                    # ====== 应用止损更新 ======
                    if new_stage != current_stage:
                        pos['sl_stage'] = new_stage
                        stage_changed = True
                    
                    sl_changed = (new_sl != current_sl)
                    if sl_changed:
                        pos['sl'] = new_sl
                        if new_stage == 3:
                            pos['tsl_active'] = True
                        
                        # 更新交易所止损单（实盘模式）
                        if not pos.get('simulated', False) and pos.get('sl_order_id'):
                            try:
                                real_symbol = pos.get('real_symbol', symbol)
                                try:
                                    client.futures_cancel_order(symbol=real_symbol, orderId=pos['sl_order_id'])
                                except:
                                    pass
                                
                                sl_side = 'SELL' if pos_type == 'LONG' else 'BUY'
                                sl_order_params = {
                                    'symbol': real_symbol,
                                    'side': sl_side,
                                    'type': 'STOP_MARKET',
                                    'quantity': pos['qty'],
                                    'stopPrice': round_to_tick_size(new_sl, real_symbol)
                                }
                                if SYSTEM_CONFIG.get("HEDGE_MODE_ENABLED", False):
                                    sl_order_params['positionSide'] = pos_type
                                else:
                                    sl_order_params['positionSide'] = 'BOTH'
                                    sl_order_params['reduceOnly'] = True
                                
                                new_sl_order = client.futures_create_order(**sl_order_params)
                                pos['sl_order_id'] = new_sl_order['orderId']
                                
                                stage_names = {1: 'Stage1:初始护盾', 2: 'Stage2:防洗盘', 3: 'Stage3:TSL收割'}
                                print(f"🎯 [{symbol}] {stage_names.get(new_stage, 'Unknown')}: 止损 {current_sl:.4f} → {new_sl:.4f} (浮盈={float_profit:.4f}, ATR={pos_atr:.4f})")
                            except Exception as sl_e:
                                print(f"⚠️ 三阶段SL更新止损单失败: {sl_e}")
                    
                    if sl_changed or stage_changed:
                        save_data()
    
    except Exception as tsl_e:
        print(f"⚠️ 三阶段动态止损巡逻器异常: {tsl_e}")
    
    for sig in signals['signals']:
        signal_type = sig['type']  # BUY or SELL
        action = sig['action']  # ENTRY, EXIT_LONG, EXIT_SHORT
        
        try:
            # 平仓信号
            if action.startswith('EXIT'):
                pos_type = 'LONG' if action == 'EXIT_LONG' else 'SHORT'
                key_sym = f"{symbol}_{pos_type}"
                
                if key_sym in ACTIVE_POSITIONS or symbol in ACTIVE_POSITIONS:
                    # 🔥 平仓权限校验（仓位隔离）
                    if POSITION_ISOLATION_ENABLED:
                        positions_list = ACTIVE_POSITIONS.get(key_sym, [])
                        if isinstance(positions_list, list) and positions_list:
                            position_to_close = positions_list[0]
                        elif isinstance(positions_list, dict):
                            position_to_close = positions_list
                        else:
                            position_to_close = None
                        
                        if position_to_close:
                            allowed, reason = validate_close_permission(position_to_close, symbol)
                            if not allowed:
                                logger.warning(f"🚫 [{symbol}] 平仓被拒绝: {reason}")
                                send_tg_alert(
                                    f"🚫 <b>[平仓权限拒绝]</b>\n"
                                    f"币种: {symbol}\n"
                                    f"原因: {reason}\n\n"
                                    f"⚠️ 该持仓不是机器人创建的，拒绝平仓"
                                )
                                continue
                    
                        result = execute_trade(
                        client, symbol, signal_type, price,
                        {'quantity': 0},  # 会从持仓中获取
                        atr=atr,
                        adx=adx,
                        position_action=action,
                        custom_config=cfg
                    )
                    
                    if result['success']:
                        print(f"✅ {symbol} 平仓成功")
                        send_tg_msg(
                            f"🛡️ <b>平仓执行</b>\n"
                            f"币种: {symbol}\n"
                            f"信号: {sig['message']}\n"
                            f"价格: ${price:.4f}"
                        )
            
            # 开仓信号
            elif action == 'ENTRY':
                pos_type = 'LONG' if signal_type == 'BUY' else 'SHORT'
                key_sym = f"{symbol}_{pos_type}"
                
                # 🔥 Task 3: 投资组合相关性检查
                correlation_allowed, correlation_reason = check_portfolio_correlation(pos_type)
                if not correlation_allowed:
                    print(f"   🚫 [{symbol}] 投资组合相关性拦截: {correlation_reason}")
                    send_tg_alert(
                        f"🚫 <b>[投资组合相关性拦截]</b>\n"
                        f"币种: {symbol}\n"
                        f"方向: {pos_type}\n"
                        f"原因: {correlation_reason}\n\n"
                        f"⚠️ 防止过度集中风险"
                    )
                    continue
                
                # ====== 多重子仓位检查（Pyramiding）======
                max_concurrent = SYSTEM_CONFIG.get("MAX_CONCURRENT_TRADES_PER_SYMBOL", 3)
                min_distance_atr = SYSTEM_CONFIG.get("MIN_SIGNAL_DISTANCE_ATR", 0.5)
                
                existing_trades = []
                if key_sym in ACTIVE_POSITIONS:
                    val = ACTIVE_POSITIONS[key_sym]
                    existing_trades = val if isinstance(val, list) else [val]
                
                # 检查1：是否达到最大并发数
                if len(existing_trades) >= max_concurrent:
                    print(f"⚠️ {symbol} {pos_type} 已达最大并发 {max_concurrent} 笔，跳过")
                    continue
                
                # 检查2：与最后一笔订单的入场价距离是否足够（防密集重复开仓）
                # 🔥 弹性收割 v3: 盈利加仓豁免 - 当现有仓位已浮盈 >= 1*ATR 时，允许顺势加仓（跳过距离检查）
                if existing_trades and atr > 0:
                    last_entry_price = existing_trades[-1].get('entry', 0)
                    price_distance = abs(price - last_entry_price)
                    min_distance = atr * min_distance_atr
                    
                    # 计算现有仓位浮盈
                    pyramid_exempt = False
                    if len(existing_trades) < max_concurrent:
                        last_pos = existing_trades[-1]
                        if last_pos.get('type') == 'LONG':
                            float_profit = price - last_entry_price
                        else:
                            float_profit = last_entry_price - price
                        
                        pos_atr = last_pos.get('atr', atr)
                        if pos_atr > 0 and float_profit >= 1.0 * pos_atr:
                            pyramid_exempt = True
                            print(f"🔥 {symbol} 盈利加仓豁免: 浮盈={float_profit:.4f} >= 1*ATR={pos_atr:.4f}，允许顺势加仓")
                    
                    if not pyramid_exempt and price_distance < min_distance:
                        print(f"⚠️ {symbol} 入场价距离不足: {price_distance:.4f} < {min_distance:.4f} (ATR*{min_distance_atr}), 跳过")
                        continue
                
                # ====== 连续亏损断路器检查 ======
                global ENGINE_STATE
                if ENGINE_STATE['breaker_until'] > time.time():
                    remaining_mins = (ENGINE_STATE['breaker_until'] - time.time()) / 60
                    msg = f"连续亏损断路器冷却中，剩余 {remaining_mins:.1f} 分钟"
                    print(f"   🚨 [{symbol}] {msg}")
                    send_tg_msg(f"🚨 <b>[断路器拦截]</b> {symbol}\n{msg}")
                    continue
                
                # ====== 投资组合风控门卫：并发头寸 + 同向敞口检查 ======
                try:
                    risk_mgr = get_risk_manager()  # 获取已初始化的单例
                    with positions_lock:
                        pos_snapshot = dict(ACTIVE_POSITIONS)
                    allowed, reason = risk_mgr.can_open_new_position(pos_snapshot, pos_type)
                    if not allowed:
                        print(f"   🛡️ [{symbol}] 风控拦截开仓: {reason}")
                        send_tg_msg(
                            f"🛡️ <b>[风控拦截]</b> {symbol} 开{pos_type}信号被阻止\n"
                            f"原因: {reason}"
                        )
                        continue
                except Exception as risk_e:
                    print(f"⚠️ 风控检查异常，保守跳过: {risk_e}")
                    continue
                
                # 计算仓位（传入 ATR 用于波动率缩放）
                position_info = calculate_position_size(
                    client, symbol, price, sig['strength'], atr=atr
                )
                
                if position_info:
                    result = execute_trade(
                        client, symbol, signal_type, price,
                        position_info,
                        atr=atr,
                        adx=adx,
                        position_action='ENTRY',
                        custom_config=cfg
                    )  
                        
                    
                    if result['success']:
                        # 🔥 黑匣子审计链路：开仓成功后立即生成快照并绑定 trade_id 存盘
                        try:
                            audit_snapshot = create_audit_snapshot(
                                df, symbol, signal_type,
                                sig.get('strength', 'STRONG'),
                                sig.get('message', '')
                            )
                            if audit_snapshot:
                                audit_snapshot['position_info'] = {
                                    'quantity': position_info['quantity'],
                                    'leverage': position_info['leverage'],
                                    'kelly_factor': position_info.get('kelly_factor', 1.0),
                                    'allocated_capital': position_info.get('allocated_capital', 0),
                                }
                                audit_snapshot['entry_price'] = price
                                audit_snapshot['atr'] = atr
                                save_audit_log(str(result['trade_id']), audit_snapshot)
                                print(f"📋 黑匣子审计已绑定: Trade_ID={result['trade_id']}")
                        except Exception as audit_e:
                            print(f"⚠️ 审计快照生成失败（不影响交易）: {audit_e}")
                        
                        print(f"✅ {symbol} 开仓成功")
                        send_tg_msg(
                            f"🚀 <b>开仓执行</b>\n"
                            f"币种: {symbol}\n"
                            f"方向: {'做多' if signal_type == 'BUY' else '做空'}\n"
                            f"信号: {sig['message']}\n"
                            f"价格: ${price:.4f}\n"
                            f"数量: {position_info['quantity']}\n"
                            f"杠杆: {position_info['leverage']}x"
                        )
        
        except Exception as e:
            print(f"❌ 处理信号失败: {e}")
            continue


# 为了兼容性，添加 generate_signals 别名
def generate_signals(df, symbol, client=None):
    """生成交易信号（兼容性别名）"""
    return generate_trading_signals(df, symbol, client=client)


# ==========================================
# 🔥 手术刀级子仓位精准控制
# ==========================================

def update_sl_to_breakeven(trade_key, client=None, custom_breakeven_price=None):
    """
    将指定订单的止损价更新为保本价（开仓价或自定义价格）
    
    🔥 弹性收割 v2: 支持自定义保本价（如 EntryPrice * 1.001 微利保本）
    
    Args:
        trade_key: 订单标识，格式为 "{symbol}_{pos_type}" 或 "trade_id"
        client: Binance客户端（可选）
        custom_breakeven_price: 自定义保本价（可选，默认使用开仓价）
    
    Returns:
        dict: {'success': bool, 'message': str, 'new_sl_price': float}
    """
    try:
        with positions_lock:
            # 尝试从 ACTIVE_POSITIONS 中查找订单
            position_info = None
            key_sym = None
            
            # 情况1：trade_key 是 "{symbol}_{pos_type}" 格式
            if trade_key in ACTIVE_POSITIONS:
                key_sym = trade_key
                positions_list = ACTIVE_POSITIONS[key_sym]
                if isinstance(positions_list, list) and positions_list:
                    position_info = positions_list[0]  # 取第一笔
                elif isinstance(positions_list, dict):
                    position_info = positions_list
            
            # 情况2：trade_key 是 trade_id，需要遍历查找
            if not position_info:
                for k, v in ACTIVE_POSITIONS.items():
                    positions_list = v if isinstance(v, list) else [v]
                    for pos in positions_list:
                        if str(pos.get('trade_id', '')) == trade_key:
                            position_info = pos
                            key_sym = k
                            break
                    if position_info:
                        break
            
            if not position_info:
                return {'success': False, 'message': '未找到该笔订单', 'new_sl_price': 0}
            
            # 🔥 弹性收割 v2: 使用自定义保本价或默认开仓价
            entry_price = position_info.get('entry', 0)
            if entry_price <= 0:
                return {'success': False, 'message': '无效的开仓价', 'new_sl_price': 0}
            
            breakeven_price = custom_breakeven_price if custom_breakeven_price is not None else entry_price
            
            # 更新止损价为保本价
            old_sl = position_info.get('sl', 0)
            position_info['sl'] = breakeven_price
            
            # 如果是实盘且有止损单ID，需要更新交易所的止损单
            if not position_info.get('simulated', False) and position_info.get('sl_order_id'):
                try:
                    if client is not None:
                        # 取消旧止损单
                        real_symbol = position_info.get('real_symbol', trade_key.split('_')[0])
                        try:
                            client.futures_cancel_order(
                                symbol=real_symbol,
                                orderId=position_info['sl_order_id']
                            )
                        except:
                            pass
                        
                        # 创建新止损单
                        pos_type = position_info.get('type', 'LONG')
                        sl_side = 'SELL' if pos_type == 'LONG' else 'BUY'
                        
                        sl_order_params = {
                            'symbol': real_symbol,
                            'side': sl_side,
                            'type': 'STOP_MARKET',
                            'quantity': position_info['qty'],
                            'stopPrice': round_to_tick_size(breakeven_price, real_symbol)
                        }
                        
                        # 对冲模式需要指定 positionSide
                        if SYSTEM_CONFIG.get("HEDGE_MODE_ENABLED", False):
                            sl_order_params['positionSide'] = pos_type
                        else:
                            sl_order_params['positionSide'] = 'BOTH'
                            sl_order_params['reduceOnly'] = True

                        new_sl_order = client.futures_create_order(**sl_order_params)
                        position_info['sl_order_id'] = new_sl_order['orderId']
                        
                        print(f"✅ 实盘止损单已更新: {real_symbol}, 新止损价={entry_price}")
                except Exception as e:
                    print(f"⚠️ 更新实盘止损单失败: {e}")
            
            save_data()
            
            return {
                'success': True,
                'message': f'止损价已更新为保本价 ${entry_price:.4f}',
                'new_sl_price': entry_price,
                'old_sl_price': old_sl
            }
    
    except Exception as e:
        print(f"❌ 更新保本止损失败: {e}")
        return {'success': False, 'message': f'操作失败: {str(e)[:50]}', 'new_sl_price': 0}


def get_position_by_key(trade_key):
    """
    根据 trade_key 获取持仓信息
    
    Args:
        trade_key: 订单标识，格式为 "{symbol}_{pos_type}" 或 "trade_id"
    
    Returns:
        dict: 持仓信息，如果未找到返回 None
    """
    try:
        with positions_lock:
            # 情况1：trade_key 是 "{symbol}_{pos_type}" 格式
            if trade_key in ACTIVE_POSITIONS:
                positions_list = ACTIVE_POSITIONS[trade_key]
                if isinstance(positions_list, list) and positions_list:
                    return positions_list[0]  # 返回第一笔
                elif isinstance(positions_list, dict):
                    return positions_list
            
            # 情况2：trade_key 是 trade_id，需要遍历查找
            for k, v in ACTIVE_POSITIONS.items():
                positions_list = v if isinstance(v, list) else [v]
                for pos in positions_list:
                    if str(pos.get('trade_id', '')) == trade_key:
                        return pos
            
            return None
    
    except Exception as e:
        print(f"❌ 获取持仓信息失败: {e}")
        return None


# ==========================================
# 🔥 决策审计系统
# ==========================================

# 全局审计日志存储（内存 + 持久化）
AUDIT_LOGS = {}
AUDIT_LOG_FILE = "trade_audit_logs.json"

def save_audit_log(trade_id, audit_data):
    """
    保存交易决策审计日志
    
    Args:
        trade_id: 交易ID
        audit_data: 审计数据字典，包含技术指标快照和决策信息
    """
    try:
        import json
        
        # 添加时间戳
        audit_data['timestamp'] = datetime.now().isoformat()
        
        # 存储到内存
        AUDIT_LOGS[trade_id] = audit_data
        
        # 持久化到文件（追加模式）
        try:
            # 读取现有日志
            if os.path.exists(AUDIT_LOG_FILE):
                with open(AUDIT_LOG_FILE, 'r', encoding='utf-8') as f:
                    all_logs = json.load(f)
            else:
                all_logs = {}
            
            # 更新日志
            all_logs[trade_id] = audit_data
            
            # 限制日志数量（保留最近1000条）
            if len(all_logs) > 1000:
                sorted_logs = sorted(all_logs.items(), key=lambda x: x[1].get('timestamp', ''), reverse=True)
                all_logs = dict(sorted_logs[:1000])
            
            # 写回文件
            with open(AUDIT_LOG_FILE, 'w', encoding='utf-8') as f:
                json.dump(all_logs, f, ensure_ascii=False, indent=2)
            
            print(f"📋 审计日志已保存: Trade_ID={trade_id}")
        
        except Exception as e:
            print(f"⚠️ 持久化审计日志失败: {e}")
    
    except Exception as e:
        print(f"❌ 保存审计日志失败: {e}")


def get_audit_log(trade_id):
    """
    获取交易决策审计日志
    
    Args:
        trade_id: 交易ID
    
    Returns:
        dict: 审计日志数据，如果未找到返回 None
    """
    try:
        import json
        
        # 先从内存查找
        if trade_id in AUDIT_LOGS:
            return AUDIT_LOGS[trade_id]
        
        # 从文件加载
        if os.path.exists(AUDIT_LOG_FILE):
            with open(AUDIT_LOG_FILE, 'r', encoding='utf-8') as f:
                all_logs = json.load(f)
                return all_logs.get(trade_id)
        
        return None
    
    except Exception as e:
        print(f"❌ 获取审计日志失败: {e}")
        return None


def create_audit_snapshot(df, symbol, signal_type, signal_strength, decision_reason):
    """
    创建技术指标快照用于审计
    
    Args:
        df: K线数据（已计算指标）
        symbol: 币种
        signal_type: 信号类型（BUY/SELL）
        signal_strength: 信号强度
        decision_reason: 决策理由
    
    Returns:
        dict: 审计快照数据
    """
    try:
        if df is None or len(df) == 0:
            return {}
        
        last_candle = df.iloc[-1]
        
        audit_data = {
            'symbol': symbol,
            'signal_type': signal_type,
            'signal_strength': signal_strength,
            'decision_reason': decision_reason,
            'direction': 'LONG' if signal_type == 'BUY' else 'SHORT',
            
            # 技术指标快照
            'MACD_hist': float(last_candle.get('MACD_hist', 0)),
            'MACD_line': float(last_candle.get('MACD_line', 0)),
            'MACD_signal': float(last_candle.get('MACD_signal', 0)),
            'Relative_ATR': float(last_candle.get('Relative_ATR', 0)),
            'ATR': float(last_candle.get('ATR', 0)),
            'RSI': float(last_candle.get('RSI', 50)),
            'ADX': float(last_candle.get('ADX', 0)),
            'EMA_TREND': float(last_candle.get('EMA_TREND', 0)),
            'Squeeze_On': bool(last_candle.get('Squeeze_On', False)),
            'Squeeze_Fired': bool(last_candle.get('Squeeze_Fired', False)),
            'VWAP': float(last_candle.get('VWAP', 0)),
            
            # 价格信息
            'close_price': float(last_candle.get('close', 0)),
            'open_price': float(last_candle.get('open', 0)),
            'high_price': float(last_candle.get('high', 0)),
            'low_price': float(last_candle.get('low', 0)),
            'volume': float(last_candle.get('volume', 0)),
            
            # 策略配置快照
            'strategy_mode': SYSTEM_CONFIG.get("STRATEGY_MODE", "STANDARD"),
            'interval': SYSTEM_CONFIG.get("INTERVAL", "15m"),
            'leverage': SYSTEM_CONFIG.get("LEVERAGE", 20),
            'risk_ratio': SYSTEM_CONFIG.get("RISK_RATIO", 0.02),
        }
        
        return audit_data
    
    except Exception as e:
        print(f"❌ 创建审计快照失败: {e}")
        return {}


# ==========================================
# 🔥 SCALPER 模式模拟交易账本记录
# ==========================================

def _log_sim_trade_to_csv(symbol, direction, entry_price, exit_price, quantity, net_pnl, current_balance):
    """
    记录模拟交易到 CSV 账本（SCALPER 模式专用）
    
    Args:
        symbol: 交易对符号
        direction: 持仓方向 ('LONG' 或 'SHORT')
        entry_price: 开仓价格
        exit_price: 平仓价格
        quantity: 交易数量
        net_pnl: 净盈亏（已扣除手续费）
        current_balance: 当前模拟账户余额
    """
    try:
        # 获取 CSV 文件路径
        csv_file = SYSTEM_CONFIG.get("SIM_REPORT_FILE", "simulated_ledger.csv")
        
        # 使用线程锁保护文件写入
        with csv_lock:
            # 检查文件是否存在
            file_exists = os.path.exists(csv_file)
            
            # 以追加模式打开文件
            with open(csv_file, 'a', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                
                # 如果文件不存在，先写入表头
                if not file_exists:
                    writer.writerow([
                        'Timestamp',
                        'Symbol',
                        'Direction',
                        'Entry_Price',
                        'Exit_Price',
                        'Quantity',
                        'Net_PnL',
                        'Current_Balance'
                    ])
                
                # 写入交易数据
                writer.writerow([
                    datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    symbol,
                    direction,
                    f"{entry_price:.4f}",
                    f"{exit_price:.4f}",
                    f"{quantity:.4f}",
                    f"{net_pnl:.2f}",
                    f"{current_balance:.2f}"
                ])
        
        print(f"   📝 模拟交易已记录到 CSV: {symbol} {direction}, 净利: ${net_pnl:.2f}")
    
    except Exception as e:
        print(f"⚠️ 记录模拟交易到 CSV 失败: {e}")


# ==========================================
# 🔥 Task 3: 投资组合相关性控制
# ==========================================

def check_portfolio_correlation(new_position_type):
    """
    🔥 Task 3: 投资组合相关性断路器（含策略模式差异化阈值 + 4h视觉弱势检测）
    
    核心逻辑：
    1. 计算当前持仓的方向分布（LONG vs SHORT）
    2. 🔥 根据 STRATEGY_MODE 动态调整同向持仓阈值：
       - SCALPER: 90% (剥头皮持仓时间极短，允许高集中度)
       - AGGRESSIVE/STANDARD: 70% (默认阈值)
       - CONSERVATIVE/GOLD_PRO: 50% (最严格风控)
    3. 🔥 4h视觉弱势检测：如果同向持仓超限且 4h K线显示BTC弱势，强制 RISK_RATIO *= 0.5
    
    Args:
        new_position_type: 新开仓方向 ('LONG' 或 'SHORT')
    
    Returns:
        (allowed: bool, reason: str)
    """
    try:
        with positions_lock:
            if not ACTIVE_POSITIONS:
                return True, "无现有持仓，放行"
            
            # 统计当前持仓方向分布
            long_count = 0
            short_count = 0
            
            for key, positions in ACTIVE_POSITIONS.items():
                positions_list = positions if isinstance(positions, list) else [positions]
                for pos in positions_list:
                    if pos.get('type') == 'LONG':
                        long_count += 1
                    elif pos.get('type') == 'SHORT':
                        short_count += 1
            
            total_positions = long_count + short_count
            if total_positions == 0:
                return True, "无有效持仓，放行"
            
            # 🔥 Task 3: 根据策略模式动态调整同向持仓阈值
            current_mode = SYSTEM_CONFIG.get("STRATEGY_MODE", "STANDARD")
            
            # 模式阈值映射
            mode_thresholds = {
                "SCALPER": 0.90,        # 剥头皮：90% 同向持仓阈值
                "AGGRESSIVE": 0.70,     # 激进：70% 同向持仓阈值
                "STANDARD": 0.70,       # 标准：70% 同向持仓阈值
                "CONSERVATIVE": 0.50,   # 保守：50% 同向持仓阈值
                "GOLD_PRO": 0.50        # 黄金专业：50% 同向持仓阈值
            }
            
            same_direction_threshold = mode_thresholds.get(current_mode, 0.70)
            
            # 计算同向持仓比例
            if new_position_type == 'LONG':
                same_direction_count = long_count
            else:
                same_direction_count = short_count
            
            same_direction_ratio = same_direction_count / total_positions if total_positions > 0 else 0
            
            print(f"   📊 投资组合相关性检查: 模式={current_mode}, 同向持仓={same_direction_count}/{total_positions} ({same_direction_ratio:.1%}), 阈值={same_direction_threshold:.0%}")
            
            # 🔥 如果同向持仓比例超过阈值，触发断路器检查
            if same_direction_ratio > same_direction_threshold:
                print(f"   🚨 同向持仓比例 {same_direction_ratio:.1%} > 阈值 {same_direction_threshold:.0%}，触发相关性断路器检查...")
                
                # 🔥 4h视觉弱势检测：检查BTC 4h K线是否显示弱势
                btc_4h_weakness = check_btc_4h_visual_weakness()
                
                if btc_4h_weakness:
                    # 强制降低风险比率
                    original_risk_ratio = SYSTEM_CONFIG.get("RISK_RATIO", 0.02)
                    reduced_risk_ratio = original_risk_ratio * 0.5
                    
                    with state_lock:
                        SYSTEM_CONFIG["RISK_RATIO"] = reduced_risk_ratio
                        save_data()
                    
                    print(f"   📉 相关性断路器触发！RISK_RATIO 已强制降低: {original_risk_ratio:.2%} → {reduced_risk_ratio:.2%}")
                    send_tg_alert(
                        f"🚨 <b>[相关性断路器触发]</b>\n\n"
                        f"策略模式: {current_mode}\n"
                        f"同向持仓比例: {same_direction_ratio:.1%} > {same_direction_threshold:.0%}\n"
                        f"4h视觉检测: BTC显示弱势\n"
                        f"风险比率已强制降低50%\n"
                        f"{original_risk_ratio:.2%} → {reduced_risk_ratio:.2%}\n\n"
                        f"🛡️ 防御性仓位管理已激活"
                    )
                    
                    return True, f"相关性断路器触发，风险比率已降低50% (同向持仓={same_direction_ratio:.1%})"
                else:
                    print(f"   ✅ 4h视觉检测：BTC未显示弱势，放行")
            
            return True, f"相关性检查通过 (同向持仓={same_direction_ratio:.1%}, 阈值={same_direction_threshold:.0%})"
    
    except Exception as e:
        print(f"❌ 投资组合相关性检查失败: {e}")
        # 检查失败时保守拒绝
        return False, f"相关性检查异常: {str(e)[:50]}"


def check_btc_4h_visual_weakness():
    """
    🔥 Task 3: 4h视觉弱势检测
    
    检查BTC 4h K线是否显示弱势（用于相关性断路器）
    
    判定逻辑：
    1. 获取BTC 4h K线最近10根
    2. 检查是否出现连续下跌或顶部反转形态
    3. 检查价格是否跌破 MA25
    
    Returns:
        bool: True=检测到弱势，False=未检测到弱势
    """
    try:
        from binance.client import Client as BinanceClient
        
        # 获取BTC 4h K线数据
        client = BinanceClient(
            api_key=SYSTEM_CONFIG.get('API_KEY'),
            api_secret=SYSTEM_CONFIG.get('API_SECRET')
        )
        
        df_4h = get_historical_klines(client, 'BTCUSDT', "4h", limit=50)
        if df_4h is None or len(df_4h) < 25:
            print(f"   ⚠️ BTC 4h K线数据不足，跳过弱势检测")
            return False
        
        # 计算 MA25
        import pandas_ta as ta
        ma25 = ta.sma(df_4h['close'], length=25)
        if ma25 is None or len(ma25) == 0:
            return False
        
        df_4h['MA25'] = ma25
        
        # 获取最近10根K线
        recent_candles = df_4h.tail(10)
        last_candle = recent_candles.iloc[-1]
        
        # 检查1：价格是否跌破 MA25
        price_below_ma25 = last_candle['close'] < last_candle['MA25']
        
        # 检查2：最近3根K线是否连续下跌
        last_3_candles = recent_candles.tail(3)
        consecutive_down = all(
            last_3_candles.iloc[i]['close'] < last_3_candles.iloc[i-1]['close']
            for i in range(1, len(last_3_candles))
        )
        
        # 检查3：最近一根K线是否为大阴线（实体 > ATR * 1.5）
        if 'ATR' in df_4h.columns:
            atr = last_candle.get('ATR', 0)
            candle_body = abs(last_candle['close'] - last_candle['open'])
            is_big_bearish = (last_candle['close'] < last_candle['open']) and (candle_body > atr * 1.5)
        else:
            is_big_bearish = False
        
        # 综合判定：任意两个条件满足即判定为弱势
        weakness_signals = [price_below_ma25, consecutive_down, is_big_bearish]
        weakness_count = sum(weakness_signals)
        
        is_weak = weakness_count >= 2
        
        if is_weak:
            print(f"   🔴 BTC 4h弱势检测: 价格破MA25={price_below_ma25}, 连续下跌={consecutive_down}, 大阴线={is_big_bearish}")
        else:
            print(f"   ✅ BTC 4h强势: 弱势信号数={weakness_count}/3")
        
        return is_weak
        
    except Exception as e:
        print(f"   ⚠️ BTC 4h弱势检测异常: {e}")
        return False  # 异常时保守返回False


def calculate_btc_recent_pnl(lookback_trades=5):
    """
    计算BTC相关交易的近期PnL（用于弱势检测）
    
    Args:
        lookback_trades: 回溯交易笔数
    
    Returns:
        float: 近期PnL总和
    """
    try:
        from config import TRADE_HISTORY
        
        with state_lock:
            if not TRADE_HISTORY:
                return 0.0
            
            # 筛选BTC相关交易
            btc_trades = [
                t for t in TRADE_HISTORY 
                if 'BTC' in t.get('symbol', '').upper()
            ]
            
            if not btc_trades:
                return 0.0
            
            # 取最近N笔交易
            recent_btc_trades = btc_trades[-lookback_trades:]
            
            # 计算总PnL
            total_pnl = sum(t.get('pnl', 0) for t in recent_btc_trades)
            
            return total_pnl
    
    except Exception as e:
        print(f"⚠️ 计算BTC近期PnL失败: {e}")
        return 0.0


# ==========================================
# 🔥 Task 4: AI交易日志生成
# ==========================================

def generate_ai_journal_entry(trade_record, trade_id):
    """
    🔥 Task 4: 生成AI交易日志（50字符以内的复盘总结）
    
    核心逻辑：
    1. 从审计日志中提取开仓时的技术形态和宏观背景
    2. 结合平仓结果（盈亏）生成简洁的复盘总结
    3. 🔥 调用 GeminiCommander 生成智能日志（限制50字符）
    
    Args:
        trade_record: 交易记录字典
        trade_id: 交易ID（用于查询审计日志）
    
    Returns:
        str: AI日志条目（50字符以内）
    """
    try:
        # 提取基础信息
        symbol = trade_record.get('symbol', 'UNKNOWN')
        direction = trade_record.get('type', 'UNKNOWN')
        pnl = trade_record.get('pnl', 0)
        entry_price = trade_record.get('entry', 0)
        exit_price = trade_record.get('exit', 0)
        
        # 从审计日志获取开仓时的技术形态
        audit_log = get_audit_log(str(trade_id)) if trade_id else None
        
        # 🔥 调用 GeminiCommander 生成智能日志
        try:
            from ai_analyst import GeminiCommander
            
            commander = GeminiCommander()
            
            # 构建日志生成 Prompt
            journal_prompt = f"""请为以下交易生成一条50字符以内的复盘日志（中文）：

交易对: {symbol}
方向: {direction}
盈亏: ${pnl:.2f}
开仓价: {entry_price}
平仓价: {exit_price}

技术形态: {audit_log.get('decision_reason', '未知') if audit_log else '未知'}
宏观背景: {SYSTEM_CONFIG.get('MACRO_WEATHER_REGIME', 'SAFE')}

要求：
1. 限制在50字符以内
2. 包含关键信息：币种、方向、盈亏、原因
3. 简洁有力，适合快速回顾

示例格式：
- 盈利："✅BTC多单+$120,MACD金叉+趋势共振"
- 亏损："❌ETH空单-$50,假突破,需改进过滤"
"""
            
            response = commander.ask_commander(journal_prompt)
            
            # 提取日志（去除多余格式）
            journal = response.strip().replace('\n', ' ')
            
            # 确保不超过50字符
            if len(journal) > 50:
                journal = journal[:47] + "..."
            
            print(f"   📝 AI日志已生成: {journal}")
            return journal
            
        except Exception as ai_e:
            print(f"   ⚠️ AI日志生成失败，使用模板: {ai_e}")
            
            # 回退到模板生成
            if pnl > 0:
                journal = f"✅{symbol}{direction}+${pnl:.0f}"
            else:
                journal = f"❌{symbol}{direction}-${abs(pnl):.0f}"
            
            # 确保不超过50字符
            if len(journal) > 50:
                journal = journal[:47] + "..."
            
            return journal
    
    except Exception as e:
        print(f"❌ 生成AI日志失败: {e}")
        return f"日志生成失败"


print("✅ 交易引擎模块已加载（含主循环和订单执行逻辑 + 子仓位控制 + 决策审计 + 投资组合相关性控制 + AI交易日志）")
