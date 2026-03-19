#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
监控系统模块 - monitors.py
负责止损监控、回撤监控、每日统计等安全防御系统
"""

import socket
import asyncio
import sys

# 🔥 解决 Windows 代理拦截 loopback 的物理补丁
if sys.platform == "win32":
    # 强制让 socketpair 使用不被代理拦截的协议
    if not hasattr(socket, 'socketpair'):
        def socketpair(family=socket.AF_INET, type=socket.SOCK_STREAM, proto=0):
            lsock = socket.socket(family, type, proto)
            lsock.bind(('127.0.0.1', 0))
            lsock.listen(1)
            csock = socket.socket(family, type, proto)
            csock.connect(lsock.getsockname())
            asock, _ = lsock.accept()
            lsock.close()
            return (asock, csock)
        socket.socketpair = socketpair
import time
import numpy as np
import pandas as pd
from logger_setup import logger
import config
from execution_algo import execute_liquidity_aware_close, execute_ioc_protected_close
from dlq_worker import add_to_dlq
# ==========================================
# 🔥 机构级风险精算引擎 (V7.2 Final)
# ==========================================

def calculate_risk_metrics_optimized(returns_seq, interval_mins=15):
    """
    针对中高频交易优化的风险指标精算
    
    Args:
        returns_seq: 收益率序列 [0.01, -0.02, ...]
        interval_mins: 你的交易周期（分钟），用于精确年化计算
    """
    if len(returns_seq) < 15:
        return None

    try:
        # 1. 物理清洗：剔除由于 API 错误或极端插针导致的非理性数据（>30% 波动）
        rets = np.array(returns_seq)
        rets = rets[abs(rets) < 0.3] 
        
        # 2. 精准年化因子计算
        # 公式：sqrt(一年总分钟数 / 交易周期分钟数)
        # 这确保了夏普比率在 15m 线和 4h 线之间具有可比性
        annual_factor = np.sqrt((365 * 24 * 60) / interval_mins)
        
        # 3. VaR & CVaR (历史模拟法：最直观反映极端亏损)
        var_95 = np.percentile(rets, 5)
        tail_losses = rets[rets <= var_95]
        cvar_95 = np.mean(tail_losses) if len(tail_losses) > 0 else var_95
        
        # 4. 索提诺比率 (Sortino)：剔除上行波动的干扰
        mu = np.mean(rets)
        downside_rets = rets[rets < 0]
        downside_std = np.std(downside_rets) if len(downside_rets) > 1 else 1e-6
        sortino = (mu / downside_std) * annual_factor
        
        # 5. 夏普比率 (Sharpe)：衡量综合风险回报
        sigma = np.std(rets)
        sharpe = (mu / sigma) * annual_factor if sigma > 0 else 0

        return {
            'VaR_95_%': round(var_95 * 100, 2),    # 单笔预期最惨亏损
            'CVaR_95_%': round(cvar_95 * 100, 2),  # 极端情况下的平均亏损
            'Sharpe': round(sharpe, 2),            # 综合评分（>2.0 优秀）
            'Sortino': round(sortino, 2),          # 下行风险评分（>2.5 极佳）
            'Sample_Count': len(rets)
        }
    except Exception as e:
        logger.error(f"❌ 风险指标精算异常: {e}")
        return None
from datetime import datetime

from config import (
    SYSTEM_CONFIG, ACTIVE_POSITIONS, positions_lock,
    save_data, TRADE_HISTORY, state_lock
)
import config  # 用于修改模块级变量
from utils import send_tg_msg, get_current_price, generate_trade_chart, MESSAGE_THREAD_POOL 
from trading_engine import emergency_close_all, get_indicator_cache, get_performance_stats
from execution_algo import execute_liquidity_aware_close

def get_current_regime():
    """
    获取当前市场状态（从 SYSTEM_CONFIG 共享总线读取）
    返回格式: {
        'regime': str,  # 市场状态名称
        'emoji': str,   # 状态表情
        'volatility': float  # 波动率水平
    }
    """
    with state_lock:
        raw = SYSTEM_CONFIG.get("MARKET_REGIME", "NORMAL")
    
    # 解析状态字符串（格式：状态名|emoji|波动率）
    if '|' in raw:
        parts = raw.split('|')
        return {
            'regime': parts[0],
            'emoji': parts[1] if len(parts) > 1 else '⚪',
            'volatility': float(parts[2]) if len(parts) > 2 else 0.0
        }
    else:
        return {
            'regime': raw,
            'emoji': '⚪',
            'volatility': 0.0
        }

# ==========================================
# SCALPER 模式动态止盈止损监控
# ==========================================

def monitor_scalper_positions(client):
    """SCALPER 模式专用：动态监控模拟持仓的止盈止损"""
    from config import STRATEGY_PRESETS
    
    print("⚡ SCALPER 动态止盈止损监控已启动")
    send_tg_msg("⚡ <b>SCALPER 动态止盈止损监控已激活</b>\n将实时监控模拟持仓并自动平仓")
    
    while True:
        if not config.BOT_ACTIVE or not client:
            time.sleep(5)
            continue
        
        # 仅在 SCALPER 模式下运行
        if SYSTEM_CONFIG.get("STRATEGY_MODE", "STANDARD") != "SCALPER":
            time.sleep(30)
            continue
        
        try:
            with positions_lock:
                positions_snapshot = dict(ACTIVE_POSITIONS.items())
            
            for key_sym, positions_data in positions_snapshot.items():
                # 🔥 支持列表形式的多笔订单
                if not isinstance(positions_data, list):
                    positions_data = [positions_data]  # 兼容旧格式
                
                # 遍历该方向下的所有子订单
                for position in positions_data:
                    # 仅处理模拟持仓
                    if not position.get('simulated', False):
                        continue
                    
                    symbol = position.get('real_symbol', key_sym.split('_')[0] if '_' in key_sym else key_sym)
                    entry_price = position['entry']
                    position_type = position['type']
                    trade_id = position.get('trade_id', 'UNKNOWN')
                    
                    # 获取当前价格
                    current_price = get_current_price(client, symbol)
                    if not current_price:
                        continue
                    
                    # 🔥 修复：直接从子订单字典读取 sl 和 tp 价格，不再使用硬编码百分比
                    sl_price = position.get('sl', 0)
                    tp_price = position.get('tp', 0)
                    
                    # 如果没有设置止损止盈价格，跳过该订单
                    if sl_price <= 0 and tp_price <= 0:
                        continue
                    
                    # 检查是否触发止损或止盈
                    if position_type == 'LONG':
                        # 多单：当前价格 <= 止损价 触发止损
                        if sl_price > 0 and current_price <= sl_price:
                            _close_scalper_position(client, key_sym, position, current_price, "STOP_LOSS")
                        # 多单：当前价格 >= 止盈价 触发止盈
                        elif tp_price > 0 and current_price >= tp_price:
                            _close_scalper_position(client, key_sym, position, current_price, "TAKE_PROFIT")
                    
                    else:  # SHORT
                        # 空单：当前价格 >= 止损价 触发止损
                        if sl_price > 0 and current_price >= sl_price:
                            _close_scalper_position(client, key_sym, position, current_price, "STOP_LOSS")
                        # 空单：当前价格 <= 止盈价 触发止盈
                        elif tp_price > 0 and current_price <= tp_price:
                            _close_scalper_position(client, key_sym, position, current_price, "TAKE_PROFIT")
        
        except Exception as e:
            print(f"⚠️ SCALPER 监控循环异常: {e}")
        
        time.sleep(5)  # SCALPER 模式需要更频繁的监控


def _record_return_to_history(net_pnl):
    """
    补全引用：将每笔交易的收益率喂给精算序列
    
    Args:
        net_pnl: 交易净盈亏
    """
    with state_lock:
        # 估算平仓前的权益，用于计算收益率
        prev_balance = SYSTEM_CONFIG.get("SIM_CURRENT_BALANCE", 0.0) - net_pnl
        if prev_balance > 0:
            return_rate = net_pnl / prev_balance
            config.ACCOUNT_RETURNS_HISTORY.append(return_rate)
            print(f"📊 收益率已记录: {return_rate*100:.2f}% (序列长度: {len(config.ACCOUNT_RETURNS_HISTORY)})")


def _close_scalper_position(client, key_sym, position, exit_price, reason):
    """
    关闭 SCALPER 模拟持仓并记录到账本
    ✅ 修复死锁风险：统一锁顺序 state_lock → positions_lock（嵌套）
    """
    from trading_engine import _log_sim_trade_to_csv
    
    try:
        symbol = position.get('real_symbol', key_sym.split('_')[0] if '_' in key_sym else key_sym)
        entry_price = position['entry']
        qty = position['qty']
        position_type = position['type']
        
        # 计算盈亏
        if position_type == 'LONG':
            gross_pnl = (exit_price - entry_price) * qty
        else:
            gross_pnl = (entry_price - exit_price) * qty
        
        # 扣除手续费
        commission = (entry_price + exit_price) * qty * SYSTEM_CONFIG["COMMISSION_RATE"]
        net_pnl = gross_pnl - commission
        
        # ✅ 统一锁顺序：先 state_lock，后 positions_lock（嵌套）
        with state_lock:
            # 更新模拟账本余额
            SYSTEM_CONFIG["SIM_CURRENT_BALANCE"] = SYSTEM_CONFIG.get("SIM_CURRENT_BALANCE", 0.0) + net_pnl
            
            # 🔥 记录收益率到历史序列（用于风险精算）
            _record_return_to_history(net_pnl)
            
            # 在 state_lock 内部获取 positions_lock
            with positions_lock:
                if key_sym in ACTIVE_POSITIONS:
                    if isinstance(ACTIVE_POSITIONS[key_sym], list):
                        ACTIVE_POSITIONS[key_sym] = [
                            p for p in ACTIVE_POSITIONS[key_sym] 
                            if p.get('trade_id') != position.get('trade_id')
                        ]
                        # 如果列表为空，删除整个key
                        if not ACTIVE_POSITIONS[key_sym]:
                            ACTIVE_POSITIONS.pop(key_sym, None)
                    else:
                        ACTIVE_POSITIONS.pop(key_sym, None)
            
            # 在锁内保存数据
            save_data()
        
        # 记录到 CSV
        _log_sim_trade_to_csv(
            symbol=symbol,
            direction=position_type,
            entry_price=entry_price,
            exit_price=exit_price,
            quantity=qty,
            net_pnl=net_pnl,
            current_balance=SYSTEM_CONFIG.get("SIM_CURRENT_BALANCE", 0.0)
        )
        
        # 🔥 异步生成交易图表
        MESSAGE_THREAD_POOL.submit(
            generate_trade_chart,
            symbol=symbol,
            direction=position_type,
            entry_price=entry_price,
            exit_price=exit_price,
            pnl=net_pnl,
            trade_id=position.get('trade_id', ''),
            timestamp=datetime.now()
        )
        
        # 发送通知
        reason_emoji = "🛑" if reason == "STOP_LOSS" else "🎯"
        reason_text = "止损触发" if reason == "STOP_LOSS" else "止盈触发"
        pnl_emoji = "🟢" if net_pnl > 0 else "🔴"
        
        send_tg_msg(
            f"{reason_emoji} <b>SCALPER {reason_text}</b>\n"
            f"币种: {symbol}\n"
            f"方向: {position_type}\n"
            f"入场价: ${entry_price:.4f}\n"
            f"出场价: ${exit_price:.4f}\n"
            f"毛利: ${gross_pnl:.2f}\n"
            f"手续费: ${commission:.2f}\n"
            f"净利: {pnl_emoji} ${net_pnl:.2f}\n"
            f"模拟余额: ${SYSTEM_CONFIG.get('SIM_CURRENT_BALANCE', 0.0):.2f}"
        )
        
        print(f"{reason_emoji} SCALPER {reason_text}: {symbol}, 净利: ${net_pnl:.2f}")
    
    except Exception as e:
        print(f"⚠️ 关闭 SCALPER 持仓失败: {e}")


# ==========================================
# 🔥 P1修复：跳空缺口防护
# ==========================================

def check_gap_risk(client, symbol, position):
    """
    检测跳空风险（P1修复）
    
    场景：周末重大事件导致周一开盘跳空-10%
    防护：检测到跳空>5%时立即市价平仓
    
    Args:
        client: Binance客户端
        symbol: 交易对
        position: 持仓信息
    
    Returns:
        (has_gap: bool, gap_pct: float, action_taken: str)
    """
    try:
        # 获取当前价格
        current_price = get_current_price(client, symbol)
        if not current_price:
            return False, 0.0, "PRICE_UNAVAILABLE"
        
        # 获取上一根K线收盘价作为参考
        from trading_engine import get_historical_klines
        df = get_historical_klines(client, symbol, "1m", limit=2)
        if df is None or len(df) < 2:
            return False, 0.0, "DATA_INSUFFICIENT"
        
        last_close = float(df.iloc[-2]['close'])
        
        # 计算跳空幅度
        gap_pct = abs(current_price - last_close) / last_close * 100
        
        # 跳空阈值：5%
        GAP_THRESHOLD = 5.0
        
        if gap_pct > GAP_THRESHOLD:
            logger.critical(f"🚨 [{symbol}] 检测到{gap_pct:.2f}%跳空！触发紧急平仓")
            
            # 立即市价平仓，不等待止损触发
            try:
                from trading_engine import execute_trade
                
                position_type = position.get('type', 'LONG')
                signal_type = 'SELL' if position_type == 'LONG' else 'BUY'
                
                result = execute_trade(
                    client=client,
                    symbol=symbol,
                    signal_type=signal_type,
                    price=current_price,
                    position_info={'quantity': position.get('qty', 0)},
                    position_action='EXIT_LONG' if position_type == 'LONG' else 'EXIT_SHORT'
                )
                
                if result['success']:
                    send_tg_msg(
                        f"🚨 <b>[跳空缺口紧急平仓]</b>\n\n"
                        f"币种: {symbol}\n"
                        f"跳空幅度: {gap_pct:.2f}%\n"
                        f"上一收盘: ${last_close:.4f}\n"
                        f"当前价格: ${current_price:.4f}\n"
                        f"平仓价格: ${current_price:.4f}\n\n"
                        f"✅ 已执行紧急平仓保护"
                    )
                    return True, gap_pct, "EMERGENCY_CLOSED"
                else:
                    return True, gap_pct, "CLOSE_FAILED"
                    
            except Exception as close_e:
                logger.error(f"❌ [{symbol}] 跳空紧急平仓失败: {close_e}")
                return True, gap_pct, "CLOSE_ERROR"
        
        return False, gap_pct, "NORMAL"
        
    except Exception as e:
        logger.error(f"⚠️ [{symbol}] 跳空检测异常: {e}")
        return False, 0.0, "CHECK_ERROR"


# ==========================================
# 🔥 P1修复：流动性检查
# ==========================================

def check_liquidity_before_close(client, symbol, quantity, side='SELL'):
    """
    平仓前检查流动性（P1修复）
    
    场景：极端行情下市场深度不足
    防护：如果订单量超过市场深度50%，启动分批平仓
    
    Args:
        client: Binance客户端
        symbol: 交易对
        quantity: 平仓数量
        side: 平仓方向 ('SELL' for LONG, 'BUY' for SHORT)
    
    Returns:
        (liquidity_ok: bool, depth_ratio: float, recommendation: str)
    """
    try:
        # 获取订单簿深度（20档）
        order_book = client.futures_order_book(symbol=symbol, limit=20)
        
        # 计算市场深度（前5档）
        if side == 'SELL':
            # 平多仓看买盘深度
            total_depth = sum([float(bid[1]) for bid in order_book['bids'][:5]])
        else:
            # 平空仓看卖盘深度
            total_depth = sum([float(ask[1]) for ask in order_book['asks'][:5]])
        
        # 计算订单量占市场深度的比例
        depth_ratio = quantity / total_depth if total_depth > 0 else float('inf')
        
        # 流动性阈值：50%
        LIQUIDITY_THRESHOLD = 0.5
        
        if depth_ratio > LIQUIDITY_THRESHOLD:
            logger.warning(
                f"⚠️ [{symbol}] 流动性不足！订单量={quantity:.4f}, "
                f"市场深度={total_depth:.4f}, 占比={depth_ratio*100:.1f}%"
            )
            
            send_tg_msg(
                f"⚠️ <b>[流动性预警]</b>\n\n"
                f"币种: {symbol}\n"
                f"平仓数量: {quantity:.4f}\n"
                f"市场深度: {total_depth:.4f}\n"
                f"占比: {depth_ratio*100:.1f}%\n\n"
                f"💡 建议: 分批平仓以减少滑点"
            )
            
            return False, depth_ratio, "SPLIT_RECOMMENDED"
        
        return True, depth_ratio, "LIQUIDITY_OK"
        
    except Exception as e:
        logger.error(f"⚠️ [{symbol}] 流动性检查异常: {e}")
        # 检查失败时保守返回流动性不足
        return False, 0.0, "CHECK_ERROR"


# ==========================================
# 🔥 P1修复：硬性亏损限制
# ==========================================

# 硬性亏损限制配置
MAX_SINGLE_POSITION_LOSS = 1000.0  # 单笔最大亏损 (USDT)
MAX_DAILY_LOSS = 5000.0  # 当日最大亏损 (USDT)

def enforce_hard_loss_limit(position, current_pnl):
    """
    强制执行亏损硬限制（P1修复）
    
    场景：防止单笔或当日亏损失控
    防护：超过硬限制时强制平仓并停止交易
    
    Args:
        position: 持仓信息
        current_pnl: 当前盈亏
    
    Returns:
        (limit_exceeded: bool, limit_type: str, action_required: str)
    """
    try:
        # 检查1：单笔亏损硬限制
        if current_pnl < 0 and abs(current_pnl) > MAX_SINGLE_POSITION_LOSS:
            logger.critical(
                f"🚨 单笔亏损超过硬限制！"
                f"当前亏损: ${abs(current_pnl):.2f} > 限制: ${MAX_SINGLE_POSITION_LOSS:.2f}"
            )
            
            send_tg_msg(
                f"🚨 <b>[单笔亏损硬限制触发]</b>\n\n"
                f"币种: {position.get('real_symbol', 'UNKNOWN')}\n"
                f"当前亏损: ${abs(current_pnl):.2f}\n"
                f"硬限制: ${MAX_SINGLE_POSITION_LOSS:.2f}\n\n"
                f"🛑 将强制平仓该持仓"
            )
            
            return True, "SINGLE_POSITION", "FORCE_CLOSE"
        
        # 检查2：当日亏损硬限制
        daily_loss = calculate_daily_loss()
        if daily_loss < 0 and abs(daily_loss) > MAX_DAILY_LOSS:
            logger.critical(
                f"🚨 当日亏损超过硬限制！"
                f"当日亏损: ${abs(daily_loss):.2f} > 限制: ${MAX_DAILY_LOSS:.2f}"
            )
            
            # 停止所有交易
            config.BOT_ACTIVE = False
            config.TRADING_ENGINE_ACTIVE = False
            
            send_tg_msg(
                f"🚨 <b>[当日亏损硬限制触发]</b>\n\n"
                f"当日亏损: ${abs(daily_loss):.2f}\n"
                f"硬限制: ${MAX_DAILY_LOSS:.2f}\n\n"
                f"🛑 交易引擎已自动停止\n"
                f"🛑 将触发一键全平保护剩余资金\n\n"
                f"⚠️ 请检查策略并在明日重新评估"
            )
            
            return True, "DAILY_LOSS", "STOP_TRADING"
        
        return False, "NONE", "NORMAL"
        
    except Exception as e:
        logger.error(f"⚠️ 硬性亏损限制检查异常: {e}")
        return False, "CHECK_ERROR", "NORMAL"


def calculate_daily_loss():
    """
    计算当日累计亏损
    
    Returns:
        float: 当日累计盈亏（负数表示亏损）
    """
    try:
        today = datetime.now().date()
        daily_pnl = 0.0
        
        with state_lock:
            for trade in TRADE_HISTORY:
                try:
                    trade_date_str = trade.get('timestamp', '')
                    if trade_date_str:
                        trade_date = datetime.fromisoformat(trade_date_str).date()
                        if trade_date == today:
                            daily_pnl += trade.get('pnl', 0)
                except Exception:
                    continue
        
        return daily_pnl
        
    except Exception as e:
        logger.error(f"⚠️ 计算当日亏损失败: {e}")
        return 0.0


# ==========================================
# 止损单监控（增强版：集成P1防护）
# ==========================================

def monitor_stop_loss_orders(client):
    """
    止损单状态监控线程（🔥 P1增强版）
    支持对冲模式 + 多重子仓位：独立监控每笔订单的止损单
    
    🔥 P1新增功能：
    1. 跳空缺口检测与紧急平仓
    2. 流动性预检查
    3. 硬性亏损限制执行
    """
    print("🛡️ 止损监控系统已启动（P1增强版：跳空防护+流动性检查+硬限制）")
    
    hedge_enabled = SYSTEM_CONFIG.get("HEDGE_MODE_ENABLED", False)
    mode_text = "对冲模式 (多空独立监控)" if hedge_enabled else "单向模式"
    send_tg_msg(f"🛡️ <b>止损监控系统已激活</b>\n模式: {mode_text}\n每60秒验证一次止损单状态")
    
    while True:
        if not config.BOT_ACTIVE or not client:
            time.sleep(30)
            continue
        
        try:
            with positions_lock:
                positions_snapshot = dict(ACTIVE_POSITIONS.items())
            
            for key_sym, positions_data in positions_snapshot.items():
                # 🔥 核心改造：支持列表形式的多笔订单
                if not isinstance(positions_data, list):
                    positions_data = [positions_data]  # 兼容旧格式
                
                for position in positions_data:
                    if position.get('simulated', False):
                        continue
                    
                    # 对冲模式下，key_sym 格式为 "BTCUSDT_LONG" 或 "BTCUSDT_SHORT"
                    # 单向模式下，key_sym 格式为 "BTCUSDT"
                    if '_LONG' in key_sym or '_SHORT' in key_sym:
                        real_symbol = key_sym.rsplit('_', 1)[0]  # 移除 _LONG/_SHORT 后缀
                        position_side = key_sym.rsplit('_', 1)[1]  # 提取 LONG/SHORT
                    else:
                        real_symbol = key_sym
                        position_side = 'BOTH'
                    
                    sl_order_id = position.get('sl_order_id')
                    trade_id = position.get('trade_id', 'UNKNOWN')
                    
                    # 🔥 P1检查1：跳空缺口防护
                    has_gap, gap_pct, gap_action = check_gap_risk(client, real_symbol, position)
                    if has_gap and gap_action == "EMERGENCY_CLOSED":
                        continue  # 已紧急平仓，跳过后续检查
                    
                    # 🔥 P1检查2：硬性亏损限制
                    current_price = get_current_price(client, real_symbol)
                    if current_price:
                        entry_price = position['entry']
                        qty = position['qty']
                        if position['type'] == 'LONG':
                            current_pnl = (current_price - entry_price) * qty
                        else:
                            current_pnl = (entry_price - current_price) * qty
                        
                        limit_exceeded, limit_type, action_required = enforce_hard_loss_limit(position, current_pnl)
                        if limit_exceeded and action_required == "FORCE_CLOSE":
                            # 强制平仓该持仓
                            try:
                                from trading_engine import execute_trade
                                signal_type = 'SELL' if position['type'] == 'LONG' else 'BUY'
                                execute_trade(
                                    client=client,
                                    symbol=real_symbol,
                                    signal_type=signal_type,
                                    price=current_price,
                                    position_info={'quantity': qty},
                                    position_action='EXIT_LONG' if position['type'] == 'LONG' else 'EXIT_SHORT'
                                )
                                continue
                            except Exception as e:
                                logger.error(f"❌ 硬限制强制平仓失败: {e}")
                        elif limit_exceeded and action_required == "STOP_TRADING":
                            # 当日亏损超限，已在函数内停止交易
                            break
                    
                    if not sl_order_id or sl_order_id == "UNKNOWN":
                        send_tg_msg(
                            f"🚨 <b>止损单缺失警报</b>\n"
                            f"币种: {real_symbol}\n"
                            f"方向: {position['type']}\n"
                            f"Trade ID: {trade_id}\n"
                            f"⚠️ 该持仓没有有效的止损单保护！\n"
                            f"建议立即手动设置止损或平仓。"
                        )
                        continue
                    
                    try:
                        order = client.futures_get_order(symbol=real_symbol, orderId=sl_order_id)
                        order_status = order['status']
                        
                        if order_status == 'FILLED':
                            fill_price = float(order.get('avgPrice', position['sl']))
                            entry_price = position['entry']
                            qty = position['qty']
                            
                            # 计算毛利
                            if position['type'] == 'LONG':
                                gross_pnl = (fill_price - entry_price) * qty
                            else:
                                gross_pnl = (entry_price - fill_price) * qty
                            
                            # 扣除双边手续费（开仓+平仓，单边万四）
                            commission = (entry_price + fill_price) * qty * SYSTEM_CONFIG["COMMISSION_RATE"]
                            net_pnl = gross_pnl - commission
                            
                            # ✅ 修复死锁：统一锁顺序 state_lock → positions_lock（嵌套）
                            trade_record = {
                                'symbol': real_symbol,
                                'type': position['type'],
                                'entry': entry_price,
                                'exit': fill_price,
                                'qty': qty,
                                'pnl': net_pnl,
                                'gross_pnl': gross_pnl,
                                'commission': commission,
                                'exit_reason': 'STOP_LOSS',
                                'trade_id': trade_id,
                                'timestamp': datetime.now().isoformat()
                            }
                            
                            # 先获取 state_lock，再嵌套获取 positions_lock
                            with state_lock:
                                # 记录到历史交易账本
                                TRADE_HISTORY.append(trade_record)
                                if len(TRADE_HISTORY) > 1000:
                                    TRADE_HISTORY[:] = TRADE_HISTORY[-1000:]
                                
                                # 🔥 记录收益率到历史序列（用于风险精算）
                                # 注意：止损单触发时，需要基于实盘账户计算收益率
                                if not SYSTEM_CONFIG.get("DRY_RUN", False):
                                    # 实盘模式：使用 _refresh_benchmark_after_close 后的余额
                                    # 这里先记录收益率，后续会刷新基准资金
                                    try:
                                        acc = client.futures_account()
                                        current_balance = float(acc['totalMarginBalance'])
                                        prev_balance = current_balance - net_pnl
                                        if prev_balance > 0:
                                            return_rate = net_pnl / prev_balance
                                            config.ACCOUNT_RETURNS_HISTORY.append(return_rate)
                                            print(f"📊 收益率已记录(实盘): {return_rate*100:.2f}% (序列长度: {len(config.ACCOUNT_RETURNS_HISTORY)})")
                                    except Exception as e:
                                        print(f"⚠️ 实盘收益率记录失败: {e}")
                                else:
                                    # 模拟模式：使用模拟账户余额
                                    _record_return_to_history(net_pnl)
                                
                                # 在 state_lock 内部嵌套获取 positions_lock
                                with positions_lock:
                                    if key_sym in ACTIVE_POSITIONS:
                                        if isinstance(ACTIVE_POSITIONS[key_sym], list):
                                            ACTIVE_POSITIONS[key_sym] = [
                                                p for p in ACTIVE_POSITIONS[key_sym] 
                                                if p.get('trade_id') != trade_id
                                            ]
                                            # 如果列表为空，删除整个key
                                            if not ACTIVE_POSITIONS[key_sym]:
                                                ACTIVE_POSITIONS.pop(key_sym, None)
                                        else:
                                            ACTIVE_POSITIONS.pop(key_sym, None)
                                
                                # 在锁内保存数据
                                save_data()
                            
                            # 🔥 修复财务幻觉：刷新基准资金，避免凯利公式配资错乱
                            from trading_engine import _refresh_benchmark_after_close
                            _refresh_benchmark_after_close(client)
                            
                            # 🔥 异步生成交易图表
                            MESSAGE_THREAD_POOL.submit(
                                generate_trade_chart,
                                symbol=real_symbol,
                                direction=position['type'],
                                entry_price=entry_price,
                                exit_price=fill_price,
                                pnl=net_pnl,
                                trade_id=trade_id,
                                timestamp=datetime.now()
                            )
                            
                            pnl_emoji = "🟢" if net_pnl > 0 else "🔴"
                            send_tg_msg(
                                f"🛑 <b>止损触发通知</b>\n"
                                f"币种: {real_symbol}\n"
                                f"方向: {position['type']}\n"
                                f"Trade ID: {trade_id}\n"
                                f"止损价: ${fill_price:.4f}\n"
                                f"毛利: ${gross_pnl:.2f}\n"
                                f"手续费: ${commission:.2f}\n"
                                f"净利: {pnl_emoji} ${net_pnl:.2f}\n"
                                f"订单ID: {sl_order_id}"
                            )
                            print(f"🛑 止损触发: {key_sym} [{trade_id}], 净利: ${net_pnl:.2f}")
                        
                        elif order_status in ['CANCELED', 'EXPIRED', 'REJECTED']:
                            send_tg_msg(
                                f"🚨 <b>止损单异常</b>: {real_symbol} [{trade_id}] "
                                f"状态={order_status}，立即自动补单..."
                            )
                            print(f"🚨 止损单异常: {key_sym} [{trade_id}], 状态: {order_status}，启动补单")
                            
                            # === 三层防护：自动补单机制 ===
                            try:
                                # 第一层：重新下止损单
                                new_stop_order = client.futures_create_order(
                                    symbol=real_symbol,
                                    side='SELL' if position['type'] == 'LONG' else 'BUY',
                                    type='STOP_MARKET',
                                    stopPrice=position['sl'],
                                    closePosition=True
                                )
                                
                                # 更新止损单ID
                                with positions_lock:
                                    if key_sym in ACTIVE_POSITIONS:
                                        if isinstance(ACTIVE_POSITIONS[key_sym], list):
                                            for p in ACTIVE_POSITIONS[key_sym]:
                                                if p.get('trade_id') == trade_id:
                                                    p['sl_order_id'] = new_stop_order['orderId']
                                                    break
                                        else:
                                            ACTIVE_POSITIONS[key_sym]['sl_order_id'] = new_stop_order['orderId']
                                save_data()
                                
                                print(f"✅ {real_symbol} [{trade_id}] 止损单补单成功，新ID: {new_stop_order['orderId']}")
                                send_tg_msg(
                                    f"✅ <b>止损单补单成功</b>\n"
                                    f"币种: {real_symbol}\n"
                                    f"Trade ID: {trade_id}\n"
                                    f"新订单ID: {new_stop_order['orderId']}"
                                )
                                
                            except Exception as reorder_err:
                                # 第二层：补单失败，紧急市价平仓
                                print(f"❌ {real_symbol} [{trade_id}] 补单失败: {reorder_err}，执行紧急平仓")
                                send_tg_msg(
                                    f"🚨🚨🚨 <b>止损补单失败，执行紧急平仓</b>\n"
                                    f"币种: {real_symbol}\n"
                                    f"Trade ID: {trade_id}\n"
                                    f"原因: {str(reorder_err)[:100]}"
                                )
                                
                                # ==================== 🔥 IOC 部分成交防护注入点 ====================
                                try:
                                    # 🚨 使用 IOC 保护性限价单进行紧急平仓
                                    close_side = 'SELL' if position['type'] == 'LONG' else 'BUY'
                                    ioc_result = execute_ioc_protected_close(
                                        client=client,
                                        symbol=real_symbol,
                                        position=position,
                                        total_qty=position['qty'],
                                        side=close_side
                                    )
                                    
                                    if not ioc_result.get('success', False):
                                        raise Exception(f"IOC 平仓失败: {ioc_result.get('error', 'UNKNOWN')}")
                                    
                                    # 🔥 提取真实成交数据
                                    filled_qty = ioc_result.get('filled_qty', 0.0)
                                    avg_price = ioc_result.get('avg_price', 0.0)
                                    planned_qty = ioc_result.get('planned_qty', position['qty'])
                                    is_partial = ioc_result.get('is_partial', False)
                                    
                                    # 🔥 计算 PnL（严格使用 filled_qty）
                                    entry_price = position['entry']
                                    if position['type'] == 'LONG':
                                        gross_pnl = (avg_price - entry_price) * filled_qty
                                    else:
                                        gross_pnl = (entry_price - avg_price) * filled_qty
                                    
                                    commission = (entry_price + avg_price) * filled_qty * SYSTEM_CONFIG["COMMISSION_RATE"]
                                    net_pnl = gross_pnl - commission
                                    
                                    # 🔥 残仓扣减逻辑
                                    remaining_qty = planned_qty - filled_qty
                                    
                                    with state_lock:
                                        with positions_lock:
                                            if remaining_qty <= 0:
                                                # 完全成交，正常抹除
                                                if key_sym in ACTIVE_POSITIONS:
                                                    if isinstance(ACTIVE_POSITIONS[key_sym], list):
                                                        ACTIVE_POSITIONS[key_sym] = [p for p in ACTIVE_POSITIONS[key_sym] if p.get('trade_id') != trade_id]
                                                        if not ACTIVE_POSITIONS[key_sym]:
                                                            ACTIVE_POSITIONS.pop(key_sym, None)
                                                    else:
                                                        ACTIVE_POSITIONS.pop(key_sym, None)
                                            else:
                                                # 部分成交，保留残仓并更新数量
                                                if key_sym in ACTIVE_POSITIONS:
                                                    if isinstance(ACTIVE_POSITIONS[key_sym], list):
                                                        for p in ACTIVE_POSITIONS[key_sym]:
                                                            if p.get('trade_id') == trade_id:
                                                                p['qty'] = remaining_qty
                                                                break
                                                    else:
                                                        ACTIVE_POSITIONS[key_sym]['qty'] = remaining_qty
                                                
                                                # 🔥 转入 DLQ 死信队列重试
                                                add_to_dlq(
                                                    symbol=real_symbol,
                                                    position_type=position['type'],
                                                    qty=remaining_qty,
                                                    entry_price=entry_price,
                                                    trade_id=trade_id,
                                                    error_reason=f"IOC部分成交残仓 (已成交{filled_qty}/{planned_qty})"
                                                )
                                                
                                                logger.warning(
                                                    f"⚠️ [{real_symbol}] IOC 部分成交，剩余残仓 {remaining_qty:.4f} 单位，"
                                                    f"已转入 DLQ 宽幅限价重试"
                                                )
                                                
                                                send_tg_msg(
                                                    f"⚠️ <b>[IOC 部分成交警报]</b>\n\n"
                                                    f"币种: {real_symbol}\n"
                                                    f"Trade ID: {trade_id}\n"
                                                    f"计划量: {planned_qty:.4f}\n"
                                                    f"成交量: {filled_qty:.4f}\n"
                                                    f"残仓量: {remaining_qty:.4f}\n"
                                                    f"成交价: ${avg_price:.4f}\n"
                                                    f"净利: ${net_pnl:.2f}\n\n"
                                                    f"🔄 残仓已转入 DLQ 死信队列，将使用宽幅限价单重试平仓"
                                                )
                                        save_data()
                                    
                                    print(f"✅ {real_symbol} [{trade_id}] IOC 紧急平仓完成 (成交 {filled_qty}/{planned_qty})")
                                # ========================================================
                                    
                                except Exception as emergency_err:
                                    # 第三层：连平仓都失败，最高级别告警
                                    print(f"🔥🔥🔥 {real_symbol} [{trade_id}] 紧急平仓也失败: {emergency_err}")
                                    send_tg_msg(
                                        f"🔥🔥🔥 <b>严重告警！无法平仓！</b>\n"
                                        f"币种: {real_symbol}\n"
                                        f"Trade ID: {trade_id}\n"
                                        f"⚠️ 请立即手动处理！"
                                    )
                    
                    except Exception as e:
                        error_msg = str(e)
                        if 'Order does not exist' in error_msg or '-2013' in error_msg:
                            send_tg_msg(
                                f"🚨 <b>止损单丢失警报</b>\n"
                                f"币种: {real_symbol}\n"
                                f"方向: {position['type']}\n"
                                f"Trade ID: {trade_id}\n"
                                f"⚠️ 止损单在交易所不存在！\n"
                                f"可能已被手动删除或系统错误。\n"
                                f"请立即检查持仓并重新设置止损。"
                            )
                        else:
                            print(f"⚠️ 验证止损单失败 {key_sym} [{trade_id}]: {error_msg[:100]}")
        
        except Exception as e:
            print(f"⚠️ 止损监控循环异常: {e}")
        
        time.sleep(60)

# ==========================================
# 回撤监控与熔断
# ==========================================

def monitor_account_drawdown(client):
    """
    最大回撤监控与紧急熔断线程
    ✅ V2.0 模式感知：REAL 和 SANDBOX 独立回撤监控
    ✅ V3.0: 统一使用 REAL_HIGH_WATER_MARK / SIM_HIGH_WATER_MARK（与 risk_manager.py 一致）
    """
    print("📉 回撤监控系统已启动（V3.0 统一 HIGH_WATER_MARK）")
    send_tg_msg("📉 <b>回撤监控系统已激活</b>\n将独立监控 REAL/SANDBOX 回撤并在必要时触发熔断")
    
    # 初始化高水位线（从持久化配置中恢复，使用统一的 HIGH_WATER_MARK 变量）
    with state_lock:
        # 初始化 REAL 高水位线
        if SYSTEM_CONFIG.get("REAL_HIGH_WATER_MARK", 0.0) <= 0:
            if client and not SYSTEM_CONFIG.get("DRY_RUN", False):
                try:
                    acc = client.futures_account()
                    SYSTEM_CONFIG["REAL_HIGH_WATER_MARK"] = float(acc['totalMarginBalance'])
                    print(f"📊 初始 REAL 高水位线: ${SYSTEM_CONFIG['REAL_HIGH_WATER_MARK']:.2f}")
                except:
                    SYSTEM_CONFIG["REAL_HIGH_WATER_MARK"] = SYSTEM_CONFIG.get("BENCHMARK_CASH", 1800.0)
            else:
                SYSTEM_CONFIG["REAL_HIGH_WATER_MARK"] = SYSTEM_CONFIG.get("BENCHMARK_CASH", 1800.0)
        else:
            print(f"📊 从配置恢复 REAL 高水位线: ${SYSTEM_CONFIG['REAL_HIGH_WATER_MARK']:.2f}")
        
        # 初始化 SIM 高水位线
        if SYSTEM_CONFIG.get("SIM_HIGH_WATER_MARK", 0.0) <= 0:
            SYSTEM_CONFIG["SIM_HIGH_WATER_MARK"] = SYSTEM_CONFIG.get("SIM_CURRENT_BALANCE", 10000.0)
            print(f"📊 初始 SIM 高水位线: ${SYSTEM_CONFIG['SIM_HIGH_WATER_MARK']:.2f}")
        else:
            print(f"📊 从配置恢复 SIM 高水位线: ${SYSTEM_CONFIG['SIM_HIGH_WATER_MARK']:.2f}")
        
        save_data()
    
    while True:
        if not client:
            time.sleep(300)
            continue
        
        try:
            # 🔥 判断当前运行模式
            running_mode = SYSTEM_CONFIG.get("RUNNING_MODE", "SANDBOX")
            is_real = (running_mode == "REAL")
            mode_label = "REAL" if is_real else "SANDBOX"
            peak_key = "REAL_HIGH_WATER_MARK" if is_real else "SIM_HIGH_WATER_MARK"
            
            # 🔥 获取当前权益（在锁外执行网络请求）
            if is_real:
                try:
                    acc = client.futures_account()
                    current_equity = float(acc['totalMarginBalance'])
                except Exception as e:
                    print(f"⚠️ 获取实盘权益失败: {e}")
                    time.sleep(300)
                    continue
            else:
                current_equity = SYSTEM_CONFIG.get("SIM_CURRENT_BALANCE", 0.0)
            
            # ✅ 在 state_lock 内部读取和更新峰值权益
            with state_lock:
                peak_equity = SYSTEM_CONFIG.get(peak_key, current_equity)
                
                # 更新峰值权益
                if current_equity > peak_equity:
                    SYSTEM_CONFIG[peak_key] = current_equity
                    save_data()
                    print(f"📈 [{mode_label}] 更新峰值权益: ${current_equity:.2f}")
                
                # 在锁内计算回撤
                if SYSTEM_CONFIG[peak_key] > 0:
                    drawdown = (SYSTEM_CONFIG[peak_key] - current_equity) / SYSTEM_CONFIG[peak_key]
                    drawdown_pct = drawdown * 100
                    peak_for_msg = SYSTEM_CONFIG[peak_key]
                else:
                    drawdown = 0
                    drawdown_pct = 0
                    peak_for_msg = 0
                
                # 兼容：同步更新旧字段（供仪表盘等模块读取）
                SYSTEM_CONFIG["PEAK_EQUITY"] = SYSTEM_CONFIG[peak_key]
            
            # 在锁外发送消息和执行熔断（避免长时间持锁）
            if drawdown > 0.15 and drawdown <= 0.20:
                send_tg_msg(
                    f"⚠️ <b>[{mode_label}] 回撤预警</b>\n"
                    f"当前回撤: {drawdown_pct:.2f}%\n"
                    f"峰值权益: ${peak_for_msg:.2f}\n"
                    f"当前权益: ${current_equity:.2f}\n"
                    f"回撤金额: ${peak_for_msg - current_equity:.2f}\n\n"
                    f"💡 建议检查策略表现，考虑降低仓位或暂停交易。"
                )
                print(f"⚠️ [{mode_label}] 回撤预警: {drawdown_pct:.2f}%")
            
            elif drawdown > 0.20 and drawdown <= 0.25:
                send_tg_msg(
                    f"🚨 <b>[{mode_label}] 回撤严重警告</b>\n"
                    f"当前回撤: {drawdown_pct:.2f}%\n"
                    f"峰值权益: ${peak_for_msg:.2f}\n"
                    f"当前权益: ${current_equity:.2f}\n"
                    f"回撤金额: ${peak_for_msg - current_equity:.2f}\n\n"
                    f"⚠️ 回撤已超过20%，强烈建议立即检查！"
                )
                print(f"🚨 [{mode_label}] 回撤严重: {drawdown_pct:.2f}%")
            
            elif drawdown > 0.25:
                print(f"🔴 [{mode_label}] 紧急熔断触发: 回撤 {drawdown_pct:.2f}%")
                
                # 🔥 模式感知熔断：只停止触发模式的交易
                with state_lock:
                    if is_real:
                        # REAL 模式熔断：停止实盘交易引擎
                        config.TRADING_ENGINE_ACTIVE = False
                    else:
                        # SANDBOX 模式熔断：停止模拟交易
                        config.BOT_ACTIVE = False
                
                # REAL 模式下执行一键全平保护资金
                if is_real:
                    emergency_close_all(client, chat_id=None)
                
                send_tg_msg(
                    f"🔴 <b>[{mode_label}] 紧急熔断触发</b>\n"
                    f"当前回撤: {drawdown_pct:.2f}%\n"
                    f"峰值权益: ${peak_for_msg:.2f}\n"
                    f"当前权益: ${current_equity:.2f}\n"
                    f"回撤金额: ${peak_for_msg - current_equity:.2f}\n\n"
                    f"🛑 [{mode_label}] 交易引擎已自动停止！\n"
                    f"{'🛑 已触发系统级强制一键全平，保护剩余本金！' if is_real else '🛑 模拟交易已暂停。'}\n"
                    f"⚠️ 请立即检查策略和持仓情况。\n"
                    f"💡 确认问题解决后，可通过菜单手动重启交易。"
                )
                
                time.sleep(600)
                continue
        
        except Exception as e:
            print(f"⚠️ 回撤监控异常: {e}")
        
        time.sleep(300)

# ==========================================
# 每日统计监控
# ==========================================

def monitor_daily_performance(client):
    """每日交易统计与风险监控（V5.0 含 Maker/Taker 手续费节省统计）"""
    print("📊 每日统计监控已启动（V5.0 含手续费分析）")
    
    daily_stats = {
        'date': datetime.now().date(),
        'start_equity': 0.0
    }
    
    # 获取初始权益（适配模拟模式）
    if SYSTEM_CONFIG.get("DRY_RUN", False):
        daily_stats['start_equity'] = SYSTEM_CONFIG.get("SIM_CURRENT_BALANCE", 0.0)
    elif client:
        try:
            acc = client.futures_account()
            daily_stats['start_equity'] = float(acc['totalMarginBalance'])
        except:
            daily_stats['start_equity'] = SYSTEM_CONFIG.get("BENCHMARK_CASH", 1800.0)
    else:
        daily_stats['start_equity'] = SYSTEM_CONFIG.get("BENCHMARK_CASH", 1800.0)
    
    while True:
        try:
            current_date = datetime.now().date()
            
            # 如果日期变更，发送昨日统计并重置
            if current_date != daily_stats['date']:
                yesterday = daily_stats['date']
                
                # 从 TRADE_HISTORY 中动态统计昨日数据（不依赖局部变量）
                yesterday_trades = []
                for trade in TRADE_HISTORY:
                    try:
                        trade_date_str = trade.get('timestamp', '')
                        if trade_date_str:
                            # 解析时间戳，提取日期
                            trade_date = datetime.fromisoformat(trade_date_str).date()
                            if trade_date == yesterday:
                                yesterday_trades.append(trade)
                    except Exception as parse_err:
                        # 时间戳解析失败，跳过该记录
                        continue
                
                # 只有当有交易记录时才发送统计
                if len(yesterday_trades) > 0:
                    trades_count = len(yesterday_trades)
                    wins_count = sum(1 for t in yesterday_trades if t.get('pnl', 0) > 0)
                    losses_count = sum(1 for t in yesterday_trades if t.get('pnl', 0) < 0)
                    total_pnl = sum(t.get('pnl', 0) for t in yesterday_trades)
                    
                    win_rate = (wins_count / trades_count) * 100 if trades_count > 0 else 0
                    
                    # 🔥 V5.0 新增：统计 Maker/Taker 手续费节省
                    maker_count = 0
                    taker_count = 0
                    total_fee_saved = 0.0
                    total_commission = sum(t.get('commission', 0) for t in yesterday_trades)
                    
                    maker_fee_rate = SYSTEM_CONFIG.get("MAKER_FEE_RATE", 0.0002)
                    taker_fee_rate = SYSTEM_CONFIG.get("TAKER_FEE_RATE", 0.0004)
                    fee_diff = taker_fee_rate - maker_fee_rate
                    
                    for trade in yesterday_trades:
                        order_identity = trade.get('order_identity', 'TAKER')
                        entry_price = trade.get('entry', 0)
                        qty = trade.get('qty', 0)
                        notional_value = entry_price * qty  # 名义价值
                        
                        if order_identity == 'MAKER':
                            maker_count += 1
                            # 计算节省的手续费（相比 Taker）
                            fee_saved = notional_value * fee_diff
                            total_fee_saved += fee_saved
                        else:
                            taker_count += 1
                    
                    maker_ratio = maker_count / trades_count * 100 if trades_count > 0 else 0
                    
                    # 获取当前权益
                    end_equity = daily_stats['start_equity']
                    if SYSTEM_CONFIG.get("DRY_RUN", False):
                        end_equity = SYSTEM_CONFIG.get("SIM_CURRENT_BALANCE", 0.0)
                    elif client:
                        try:
                            acc = client.futures_account()
                            end_equity = float(acc['totalMarginBalance'])
                        except:
                            pass
                    
                    daily_pnl = end_equity - daily_stats['start_equity']
                    daily_pnl_pct = (daily_pnl / daily_stats['start_equity']) * 100 if daily_stats['start_equity'] > 0 else 0
                    
                    pnl_emoji = "🟢" if daily_pnl > 0 else "🔴" if daily_pnl < 0 else "⚪"
                    
                    # 构建战报消息
                    msg = (
                        f"📊 <b>每日交易统计 V5.0</b>\n"
                        f"日期: {yesterday}\n\n"
                        f"<b>交易概况:</b>\n"
                        f"• 总交易次数: {trades_count}\n"
                        f"• 盈利次数: {wins_count}\n"
                        f"• 亏损次数: {losses_count}\n"
                        f"• 胜率: {win_rate:.1f}%\n"
                        f"• 交易盈亏: ${total_pnl:.2f}\n\n"
                        f"<b>盈亏情况:</b>\n"
                        f"• 起始权益: ${daily_stats['start_equity']:.2f}\n"
                        f"• 结束权益: ${end_equity:.2f}\n"
                        f"• 当日盈亏: {pnl_emoji} ${daily_pnl:.2f} ({daily_pnl_pct:+.2f}%)\n\n"
                        f"<b>💎 手续费优化统计:</b>\n"
                        f"• Maker成交: {maker_count} 笔 ({maker_ratio:.1f}%)\n"
                        f"• Taker成交: {taker_count} 笔 ({100-maker_ratio:.1f}%)\n"
                        f"• 💰 节省手续费: ${total_fee_saved:.2f}\n"
                        f"• 📊 总手续费: ${total_commission:.2f}\n"
                        f"• 📈 Maker费率: {maker_fee_rate*100:.02f}% | Taker费率: {taker_fee_rate*100:.02f}%"
                    )
                    
                    send_tg_msg(msg)
                    print(f"✅ 每日战报已生成（含手续费分析）: {yesterday}")
                    
                    # 🔥 V2.0 新增：机构级风险审计报告
                    try:
                        stats = get_performance_stats(lookback=100)
                        risk_msg = (
                            f"📊 <b>机构级风险审计</b>\n"
                            f"• Sharpe: <code>{stats['sharpe']}</code>\n"
                            f"• Sortino: <code>{stats['sortino']}</code>\n"
                            f"• VaR (95%): <code>{stats['var_95']}%</code>\n"
                            f"• CVaR (95%): <code>{stats['cvar_95']}%</code>\n"
                            f"• 样本数: <code>{stats['sample_count']}</code>"
                        )
                        send_tg_msg(risk_msg)
                        print(f"✅ 机构级风险审计已推送")
                    except Exception as risk_err:
                        print(f"⚠️ 机构级风险审计失败: {risk_err}")
                    
                    # 更新起始权益为今日开始
                    daily_stats['start_equity'] = end_equity
                else:
                    # 无交易记录，仅更新权益
                    if SYSTEM_CONFIG.get("DRY_RUN", False):
                        daily_stats['start_equity'] = SYSTEM_CONFIG.get("SIM_CURRENT_BALANCE", 0.0)
                    elif client:
                        try:
                            acc = client.futures_account()
                            daily_stats['start_equity'] = float(acc['totalMarginBalance'])
                        except:
                            pass
                
                # 重置日期为今天
                daily_stats['date'] = current_date
        
        except Exception as e:
            print(f"⚠️ 每日统计监控异常: {e}")
        
        time.sleep(3600)

# ==========================================
# 价格监控引擎
# ==========================================

def price_monitor_engine(client):
    """价格监控引擎"""
    from config import price_history
    
    send_tg_msg("📊 <b>价格监控系统已激活</b>\n监控开始运行...")
    
    while True:
        try:
            if SYSTEM_CONFIG.get("PRICE_MONITOR_ENABLED", True):
                symbols = list(SYSTEM_CONFIG["ASSET_WEIGHTS"].keys())
                for symbol in symbols:
                    price = get_current_price(client, symbol)
                    if price:
                        if symbol in price_history:
                            old_price = price_history[symbol]
                            change = (price - old_price) / old_price
                            if abs(change) >= SYSTEM_CONFIG.get("PRICE_ALERT_THRESHOLD", 0.03):
                                direction = "🟢 上涨" if change > 0 else "🔴 下跌"
                                send_tg_msg(
                                    f"🚨 <b>价格警报</b>\n"
                                    f"💎 {symbol} {direction}\n"
                                    f"💰 现价: ${price:.2f}\n"
                                    f"📈 变动: {change*100:.2f}%"
                                )
                                price_history[symbol] = price
                        else:
                            price_history[symbol] = price
        except Exception as e:
            print(f"⚠️ 价格监控异常: {e}")
        
        time.sleep(SYSTEM_CONFIG.get("PRICE_UPDATE_INTERVAL", 300))

# ==========================================
# 报价哨所引擎
# ==========================================

def price_sentry_engine(client):
    """15分钟报价哨所独立监控线程"""
    from config import SENTRY_CONFIG, sentry_price_cache
    
    print("🔭 15分钟报价哨所已启动")
    send_tg_msg("🔭 <b>15分钟报价哨所已激活</b>\n将每15分钟推送价格战报")
    
    while True:
        try:
            if SENTRY_CONFIG["ENABLED"] and len(SENTRY_CONFIG["WATCH_LIST"]) > 0:
                push_sentry_price_report(client)
        except Exception as e:
            print(f"⚠️ 哨所监控异常: {e}")
        
        time.sleep(SENTRY_CONFIG["INTERVAL"])


def push_sentry_price_report(client, chat_id=None):
    """推送哨所价格战报"""
    from config import SENTRY_CONFIG, sentry_price_cache
    from utils import get_bot
    import html
    
    if not SENTRY_CONFIG["WATCH_LIST"]:
        if chat_id:
            bot = get_bot()
            if bot:
                bot.send_message(chat_id, "📭 哨所监控列表为空，无法生成战报。", parse_mode="HTML")
        return
    
    msg = f"🔭 <b>15分钟报价哨所 - 战报</b>\n"
    msg += f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    
    for symbol in SENTRY_CONFIG["WATCH_LIST"]:
        try:
            price = get_current_price(client, symbol)
            if price:
                change_str = ""
                if symbol in sentry_price_cache:
                    old_price = sentry_price_cache[symbol]
                    change = (price - old_price) / old_price
                    change_pct = change * 100
                    if abs(change_pct) >= 0.01:
                        emoji = "🟢" if change > 0 else "🔴" if change < 0 else "⚪"
                        change_str = f" {emoji} {change_pct:+.2f}%"
                
                sentry_price_cache[symbol] = price
                msg += f"💎 <b>{html.escape(symbol)}</b>: ${price:.4f}{change_str}\n"
            else:
                msg += f"⚠️ {html.escape(symbol)}: 无法获取价格\n"
        except Exception as e:
            msg += f"❌ {html.escape(symbol)}: 错误 ({str(e)[:20]})\n"
    
    msg += f"\n📊 监控币种: {len(SENTRY_CONFIG['WATCH_LIST'])} 个"
    
    target_chat_id = chat_id if chat_id else SYSTEM_CONFIG.get("TG_CHAT_ID", "")
    if target_chat_id:
        try:
            bot = get_bot()
            if bot:
                # 删除上次的战报消息
                if SENTRY_CONFIG.get("LAST_REPORT_MSG_ID"):
                    try:
                        bot.delete_message(target_chat_id, SENTRY_CONFIG["LAST_REPORT_MSG_ID"])
                    except:
                        pass
                
                # 发送新战报
                sent_msg = bot.send_message(target_chat_id, msg, parse_mode="HTML")
                SENTRY_CONFIG["LAST_REPORT_MSG_ID"] = sent_msg.message_id
        except Exception as e:
            print(f"⚠️ 推送哨所战报失败: {e}")


# ==========================================
# AI 战略战报系统
# ==========================================


def _generate_enhanced_chart(client):
    """
    生成增强版4h级别K线图，带SMC标注（供给需求区、流动性池等）
    
    Returns:
        bytes: 图表的二进制数据
    """
    try:
        import io
        from trading_engine import get_historical_klines
        from utils import get_kline_buffer
        
        # 获取主要交易对的4h K线数据
        symbols = SYSTEM_CONFIG.get("MONITOR_SYMBOLS", ["BTCUSDT"])
        primary_symbol = symbols[0] if symbols else "BTCUSDT"
        
        # 获取最近100根4h K线
        df = get_historical_klines(client, primary_symbol, "4h", limit=100)
        if df is None or len(df) < 50:
            print(f"⚠️ 无法生成增强图表: {primary_symbol} 数据不足")
            return None
        
        # 使用现有的 get_kline_buffer 函数生成图表
        chart_bytes = get_kline_buffer(df, symbol=primary_symbol, num_candles=80)
        
        if chart_bytes:
            print(f"✅ 增强图表已生成: {primary_symbol}, 4h级别")
        
        return chart_bytes
        
    except Exception as e:
        print(f"⚠️ 增强图表生成失败: {e}")
        return None


def extract_json(text):
    """
    从AI响应文本中提取JSON对象
    
    Args:
        text: AI响应文本
    
    Returns:
        dict: 解析后的JSON对象，失败返回None
    """
    import re
    import json
    
    try:
        # 尝试提取 ```json 代码块
        json_match = re.search(r'```json\s*(\{.*?\})\s*```', text, re.DOTALL)
        if json_match:
            return json.loads(json_match.group(1))
        
        # 尝试提取花括号包裹的JSON
        brace_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text, re.DOTALL)
        if brace_match:
            return json.loads(brace_match.group(0))
        
        return None
    
    except Exception as e:
        print(f"⚠️ JSON提取失败: {e}")
        return None


def daily_ai_report_engine(client):
    """
    每日AI战略战报引擎（🔥 V2.0 增强版：视觉反馈 + 数据分析 + 参数调优闭环）
    每天凌晨00:05触发，生成战报并推送到TG
    
    核心功能：
    1. 数据分析：提取上一周期的胜率、回撤、参数盈亏比
    2. 视觉审计：生成4h级别带SMC标注的图表
    3. 闭环决策：将数据+图片喂给Claude，AI决定明天的参数
    4. 自动反馈：解析并执行调参JSON
    """
    from ai_analyst import get_commander
    
    print("🤖 AI战略战报引擎已启动（V2.0 增强版：视觉反馈 + 数据分析）")
    send_tg_msg("🤖 <b>AI战略战报引擎已激活 V2.0</b>\n将在每日00:05生成战报（含性能分析+K线图视觉审计）")
    
    last_report_date = None
    
    while True:
        # ✅ 修复死循环：将 sleep 移到 try 块内部，确保无论如何都会执行
        try:
            now = datetime.now()
            current_date = now.date()
            
            if now.hour == 0 and now.minute >= 5 and now.minute < 10:
                if last_report_date != current_date:
                    print(f"🌅 开始生成每日AI战报 V2.0: {current_date}")
                    
                    # 🔥 Step 1: 数据分析 - 提取上一周期的性能统计
                    performance = get_performance_stats(lookback=50)
                    
                    # 🔥 Step 2: 视觉审计 - 生成4h级别带SMC标注的图表
                    chart_bytes = _generate_enhanced_chart(client)
                    
                    # 🔥 Step 3: 构建增强版Prompt（数据+视觉上下文）
                    commander = get_commander()
                    
                    # 获取当前参数
                    current_params = {
                        'LEVERAGE': SYSTEM_CONFIG.get("LEVERAGE", 20.0),
                        'RISK_RATIO': SYSTEM_CONFIG.get("RISK_RATIO", 0.025),
                        'ATR_MULT': SYSTEM_CONFIG.get("ATR_MULT", 2.3),
                        'ADX_THR': SYSTEM_CONFIG.get("ADX_THR", 12),
                        'STRATEGY_MODE': SYSTEM_CONFIG.get("STRATEGY_MODE", "STANDARD")
                    }
                    
                    prompt = f"""# 🎯 每日AI战略战报 V2.0 - 数据驱动参数调优

## 📊 最近50笔交易性能分析
- 总交易次数: {performance['total_trades']}
- 胜率: {performance['win_rate']:.1f}%
- 总盈亏: ${performance['total_pnl']:.2f}
- 平均盈利: ${performance['avg_win']:.2f}
- 平均亏损: ${performance['avg_loss']:.2f}
- 盈亏比: {performance['profit_factor']:.2f}
- 最大回撤: {performance['max_drawdown']:.2f}%
- 夏普比率: {performance['sharpe_ratio']:.2f}

## ⚙️ 当前策略参数
- 策略模式: {current_params['STRATEGY_MODE']}
- 杠杆倍数: {current_params['LEVERAGE']}x
- 单笔风险: {current_params['RISK_RATIO']*100:.1f}%
- ATR止损倍数: {current_params['ATR_MULT']}
- ADX阈值: {current_params['ADX_THR']}

## 📈 4h级别K线图视觉审计
[图表已生成，请结合形态分析]

## 🎯 AI决策任务
1. 分析最近50笔交易的表现，识别参数优化空间
2. 结合4h K线图形态，评估明日市场环境
3. 如果需要调整参数以提升夏普比率或降低回撤，请给出具体建议

## 📋 可调整参数范围
- LEVERAGE: 5-50x（模拟模式可更高）
- RISK_RATIO: 0.01-0.08
- ATR_MULT: 1.5-3.5
- ADX_THR: 5-25
- STRATEGY_MODE: AGGRESSIVE/STANDARD/CONSERVATIVE/SCALPER

## 📤 响应格式（如需调参）
```json
{{
  "tune_params": {{
    "LEVERAGE": 25.0,
    "RISK_RATIO": 0.03,
    "ATR_MULT": 2.5
  }},
  "reasoning": "根据数据分析和图表形态，建议调整参数以优化风险收益比"
}}
```

请用中文生成战报，并根据数据和图表给出明确的参数调整建议。
"""
                    
                    # 🔥 Step 4: 闭环决策 - 通过 LLM 队列异步执行（V9.0 生产者模式）
                    from llm_worker import llm_task_queue

                    def _daily_report_callback(chat_id, ai_reply, meta, bot_instance):
                        """每日战报 LLM 回调：解析调参 JSON + 推送战报"""
                        try:
                            if not ai_reply:
                                send_tg_msg("⚠️ 每日AI战报生成失败：AI 未返回有效内容")
                                return

                            # 清理命令标记
                            clean_reply = ai_reply
                            if "###COMMAND###" in clean_reply:
                                start = clean_reply.find("###COMMAND###")
                                end = clean_reply.rfind("###COMMAND###")
                                if start < end:
                                    clean_reply = clean_reply[:start] + clean_reply[end + len("###COMMAND###"):]

                            # 自动反馈 - 解析并执行调参JSON
                            ai_json = extract_json(clean_reply)
                            if ai_json and 'tune_params' in ai_json:
                                tune_params_data = ai_json.get('tune_params', {})
                                reasoning = ai_json.get('reasoning', '无说明')

                                if tune_params_data:
                                    from config import update_dynamic_params
                                    updated_keys = update_dynamic_params(tune_params_data)

                                    if updated_keys:
                                        param_summary = "\n".join([f"• {key}: {tune_params_data[key]}" for key in updated_keys])
                                        send_tg_msg(
                                            f"🤖 <b>AI参数调优已执行</b>\n\n"
                                            f"📊 调整原因:\n{reasoning}\n\n"
                                            f"✅ 已更新参数:\n{param_summary}"
                                        )
                                        print(f"✅ AI参数调优成功: {len(updated_keys)}个参数已更新")
                                    else:
                                        print("⚠️ AI建议的参数未通过安全检查")

                            # 推送战报
                            send_tg_msg(f"🤖 <b>每日AI战报 V2.0</b>\n\n{clean_reply[:1000]}...")
                            print(f"✅ AI战报已推送")
                        except Exception as cb_err:
                            print(f"⚠️ 每日战报回调异常: {cb_err}")
                            send_tg_msg(f"⚠️ 每日AI战报后处理异常: {str(cb_err)[:100]}")

                    target_chat_id = SYSTEM_CONFIG.get("TG_CHAT_ID", "")
                    try:
                        llm_task_queue.put_nowait({
                            "type": "ai_war_report",
                            "chat_id": target_chat_id,
                            "prompt": prompt,
                            "callback": _daily_report_callback,
                            "meta": {"chart_bytes": chart_bytes},
                        })
                        print(f"✅ 每日AI战报任务已入队")
                    except Exception as q_err:
                        print(f"⚠️ 每日AI战报入队失败: {q_err}")
                    
                    last_report_date = current_date
        
        except KeyboardInterrupt:
            # 捕获键盘中断，优雅退出
            print("🛑 AI战报引擎收到停止信号，正在退出...")
            break
        except Exception as e:
            print(f"⚠️ AI战报引擎异常: {e}")
            import traceback
            traceback.print_exc()
        finally:
            # ✅ 使用 finally 确保无论如何都会执行 sleep，防止 CPU 空转
            time.sleep(300)




# ==========================================
# 🔥 V5.0 市场状态分类器（Market Regime Detection）
# ==========================================

def market_regime_detector(client):
    """
    市场状态分类器：每小时分析市场波动率，自动触发熔断
    
    核心逻辑：
    1. 计算小时级 ATR 斜率（波动率趋势）
    2. 计算 ADX 强度（趋势强度）
    3. 模拟 VIX：90分位数波动率阈值触发 DRY_RUN=True
    4. 自动发送 TG 警报并暂停交易
    """
    print("🌡️ 市场状态分类器已启动")
    send_tg_msg("🌡️ <b>市场状态分类器已激活</b>\n将每小时分析市场波动率并自动熔断")
    
    # 波动率历史缓存（用于计算90分位数）
    volatility_history = []
    MAX_HISTORY_SIZE = 168  # 保留7天数据（24小时*7天）
    
    while True:
        try:
            if not config.BOT_ACTIVE or not client:
                time.sleep(300)
                continue
            
            # 获取监控币种列表
            symbols = SYSTEM_CONFIG.get("MONITOR_SYMBOLS", [])
            if not symbols:
                time.sleep(3600)
                continue
            
            # 遍历所有币种进行市场状态分析
            for symbol in symbols:
                try:
                    # 获取小时级K线数据（需要足够数据计算ATR斜率）
                    from trading_engine import get_historical_klines, calculate_indicators
                    
                    df_1h = get_historical_klines(client, symbol, "1h", limit=500)
                    if df_1h is None or len(df_1h) < 100:
                        continue
                    
                    # 计算技术指标（包含ATR和ADX）
                    df_1h = calculate_indicators(df_1h, force_recalc=True)
                    if df_1h is None or len(df_1h) < 50:
                        continue
                    
                    # 提取最近的指标值
                    last_candle = df_1h.iloc[-1]
                    prev_candle = df_1h.iloc[-2]
                    
                    current_atr = last_candle.get('ATR', 0)
                    prev_atr = prev_candle.get('ATR', 0)
                    current_adx = last_candle.get('ADX', 0)
                    relative_atr = last_candle.get('Relative_ATR', 1.0)
                    
                    # 计算 ATR 斜率（波动率趋势）
                    atr_slope = 0
                    if prev_atr > 0:
                        atr_slope = (current_atr - prev_atr) / prev_atr
                    
                    # 记录当前波动率到历史缓存
                    volatility_history.append(relative_atr)
                    if len(volatility_history) > MAX_HISTORY_SIZE:
                        volatility_history.pop(0)
                    
                    # 计算90分位数阈值（模拟VIX）
                    if len(volatility_history) >= 24:  # 至少需要24小时数据
                        import numpy as np
                        vix_threshold = np.percentile(volatility_history, 90)
                    else:
                        vix_threshold = 2.0  # 默认阈值
                    
                    # 市场状态分类
                    regime = "NORMAL"
                    regime_emoji = "🟢"
                    
                    if relative_atr > vix_threshold:
                        regime = "HIGH_VOLATILITY"
                        regime_emoji = "🔴"
                    elif current_adx > 40:
                        regime = "STRONG_TREND"
                        regime_emoji = "🟡"
                    elif current_adx < 20 and relative_atr < 1.0:
                        regime = "LOW_VOLATILITY"
                        regime_emoji = "🔵"
                    
                    # 🔥 VIX 熔断触发：波动率超过90分位数
                    if relative_atr > vix_threshold:
                        print(f"🚨 [{symbol}] VIX熔断触发！Relative_ATR={relative_atr:.2f} > 90分位数={vix_threshold:.2f}")
                        
                        # 自动切换到模拟模式
                        if not SYSTEM_CONFIG.get("DRY_RUN", False):
                            with state_lock:
                                SYSTEM_CONFIG["DRY_RUN"] = True
                                save_data()
                            
                            send_tg_msg(
                                f"🚨 <b>[VIX熔断触发]</b>\n\n"
                                f"币种: {symbol}\n"
                                f"市场状态: {regime_emoji} {regime}\n"
                                f"当前波动率: {relative_atr:.2f}\n"
                                f"VIX阈值(90%): {vix_threshold:.2f}\n"
                                f"ATR斜率: {atr_slope*100:+.2f}%\n"
                                f"ADX强度: {current_adx:.1f}\n\n"
                                f"⚠️ 系统已自动切换到<b>模拟模式</b>！\n"
                                f"🛡️ 所有新开仓将使用模拟账户，保护真实资金。\n\n"
                                f"💡 待市场波动率恢复正常后，可手动切换回实盘模式。"
                            )
                        else:
                            # 已经是模拟模式，仅发送警报
                            send_tg_msg(
                                f"⚠️ <b>[高波动率警报]</b>\n\n"
                                f"币种: {symbol}\n"
                                f"市场状态: {regime_emoji} {regime}\n"
                                f"当前波动率: {relative_atr:.2f}\n"
                                f"VIX阈值(90%): {vix_threshold:.2f}\n"
                                f"ATR斜率: {atr_slope*100:+.2f}%\n"
                                f"ADX强度: {current_adx:.1f}\n\n"
                                f"ℹ️ 系统当前已处于模拟模式。"
                            )
                    else:
                        # 正常状态下的市场状态日志（仅打印，不发送TG）
                        print(f"   🌡️ [{symbol}] 市场状态: {regime_emoji} {regime} | "
                              f"Relative_ATR={relative_atr:.2f} | ADX={current_adx:.1f} | "
                              f"ATR斜率={atr_slope*100:+.2f}% | VIX阈值={vix_threshold:.2f}")
                    
                    # 🔥 更新市场状态到共享字典总线（多进程安全）
                    with state_lock:
                        SYSTEM_CONFIG["MARKET_REGIME"] = f"{regime}|{regime_emoji}|{relative_atr:.2f}"
                        save_data()
                
                except Exception as e:
                    print(f"⚠️ [{symbol}] 市场状态分析异常: {e}")
                    continue
        
        except Exception as e:
            print(f"⚠️ 市场状态分类器异常: {e}")
        
        # 每小时检查一次
        time.sleep(3600)


# ==========================================
# 🔥 AI 自适应巡航调参引擎
# ==========================================

def ai_auto_tuner_loop(client):
    """
    AI 自适应巡航调参引擎 - 后台驻留线程
    每15分钟评估市场状态，在安全边界内自动微调参数
    
    核心逻辑：
    1. 抓取实时指标和波动率
    2. 获取当前策略参数快照
    3. 发送给 ClaudeCommander 进行评估
    4. 如果参数在安全边界内，静默授权修改
    5. 冷却期保护：2小时内只允许调参一次
    """
    print("🤖 AI 自适应巡航调参引擎已启动")
    send_tg_msg("🤖 <b>AI 自适应巡航调参引擎已激活</b>\n将每15分钟评估市场状态并自动微调参数")
    
    while True:
        if not config.BOT_ACTIVE or not client:
            time.sleep(300)
            continue
        
        try:
            # 🔥 Step 1: 检查冷却期（2小时内只允许调参一次）
            current_time = time.time()
            last_tune_time = config.LAST_AUTO_TUNE_TIME
            cooldown = config.AUTO_TUNE_COOLDOWN
            
            if current_time - last_tune_time < cooldown:
                remaining = cooldown - (current_time - last_tune_time)
                print(f"⏳ AI 调参冷却中，剩余 {remaining/60:.0f} 分钟")
                time.sleep(900)  # 15分钟后再检查
                continue
            
            # 🔥 Step 2: 抓取实时指标缓存
            from trading_engine import get_indicator_cache
            
            indicator_cache = get_indicator_cache()
            if not indicator_cache:
                print("⚠️ 指标缓存为空，跳过本次调参")
                time.sleep(900)
                continue
            
            # 🔥 Step 3: 获取当前策略参数快照
            current_mode = SYSTEM_CONFIG.get("STRATEGY_MODE", "STANDARD")
            current_params = {
                "ADX_THR": SYSTEM_CONFIG.get("ADX_THR", 12),
                "ATR_MULT": SYSTEM_CONFIG.get("ATR_MULT", 2.3),
                "RSI_OVERSOLD": SYSTEM_CONFIG.get("RSI_OVERSOLD", 25),
                "RSI_OVERBOUGHT": SYSTEM_CONFIG.get("RSI_OVERBOUGHT", 75),
                "STRATEGY_MODE": current_mode,
            }
            
            # 🔥 Step 4: 构建 AI 评估 Prompt
            prompt = f"""# 🎯 AI 自适应巡航调参评估

## 当前市场状态
{indicator_cache}

## 当前策略参数
- 策略模式: {current_mode}
- ADX阈值: {current_params['ADX_THR']}
- ATR倍数: {current_params['ATR_MULT']}
- RSI超卖: {current_params['RSI_OVERSOLD']}
- RSI超买: {current_params['RSI_OVERBOUGHT']}

## 安全边界（禁止超出此范围）
- ADX_THR: 5 ~ 25
- ATR_MULT: 1.2 ~ 3.5
- RSI_OVERSOLD: 15 ~ 35
- RSI_OVERBOUGHT: 65 ~ 85

## 任务
评估当前波动率与趋势强度。如果当前参数导致：
1. 错失大级别趋势突破（ADX过高，信号过于保守）
2. 波动率过高容易被打损（ATR倍数过小，止损过紧）
3. RSI区间设置不合理（超买超卖区过窄或过宽）

请在安全边界内提出参数微调建议。

## 响应格式（STRICT JSON）
```json
{{
  "need_tune": true,
  "tune_params": {{
    "ADX_THR": 10,
    "ATR_MULT": 2.5
  }},
  "reasoning": "当前波动率上升，建议放宽ATR倍数至2.5以避免频繁止损；ADX降至10提升信号灵敏度"
}}
```

如果不需要调参，返回：
```json
{{
  "need_tune": false,
  "reasoning": "当前参数适配市场状态，无需调整"
}}
```

请用中文回答。
"""
            
            # 🔥 Step 5: 通过 LLM 队列异步执行（V9.0 生产者模式）
            from llm_worker import llm_task_queue

            def _auto_tune_callback(chat_id, ai_response, meta, bot_instance):
                """自动调参 LLM 回调：解析 JSON 并执行调参"""
                import json
                import re
                try:
                    if not ai_response:
                        print("⚠️ AI 响应为空，跳过本次调参")
                        return

                    json_match = re.search(r'```json\s*(\{.*?\})\s*```', ai_response, re.DOTALL)
                    if not json_match:
                        print("⚠️ AI 响应中未找到 JSON，跳过本次调参")
                        return

                    ai_json = json.loads(json_match.group(1))
                    need_tune = ai_json.get('need_tune', False)

                    if not need_tune:
                        reasoning = ai_json.get('reasoning', '无需调整')
                        print(f"✅ AI 评估：{reasoning}")
                        return

                    from bot_handlers import execute_auto_tune
                    result = execute_auto_tune(ai_json)

                    if result['success']:
                        config.LAST_AUTO_TUNE_TIME = time.time()
                        print(f"✅ AI 自动调参成功：{result['message']}")
                    else:
                        print(f"⚠️ AI 自动调参失败：{result['message']}")
                except Exception as cb_err:
                    print(f"⚠️ 自动调参回调异常: {cb_err}")

            target_chat_id = SYSTEM_CONFIG.get("TG_CHAT_ID", "")
            try:
                llm_task_queue.put_nowait({
                    "type": "auto_tune",
                    "chat_id": target_chat_id,
                    "prompt": prompt,
                    "callback": _auto_tune_callback,
                    "meta": {},
                })
                print("✅ AI 自动调参任务已入队")
            except Exception as q_err:
                print(f"⚠️ AI 自动调参入队失败（队列满）: {q_err}")
        
        except Exception as e:
            print(f"⚠️ AI 自适应巡航调参异常: {e}")
        
        # 每15分钟执行一次
        time.sleep(900)


print("✅ 监控系统模块已加载（含V5.0市场状态分类器 + AI自适应巡航调参引擎）")
