#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
监控系统模块 - monitors.py
负责止损监控、回撤监控、每日统计等安全防御系统
"""

import time
from datetime import datetime

from config import (
    SYSTEM_CONFIG, ACTIVE_POSITIONS, positions_lock,
    save_data, TRADE_HISTORY, state_lock
)
import config  # 用于修改模块级变量
from utils import send_tg_msg, get_current_price, generate_trade_chart, MESSAGE_THREAD_POOL
from trading_engine import emergency_close_all, get_indicator_cache

# ==========================================
# 全局变量：当前市场状态
# ==========================================
_CURRENT_MARKET_REGIME = "NORMAL"

def get_current_regime():
    """
    获取当前市场状态
    返回格式: {
        'regime': str,  # 市场状态名称
        'emoji': str,   # 状态表情
        'volatility': float  # 波动率水平
    }
    """
    global _CURRENT_MARKET_REGIME
    
    # 解析状态字符串（格式：状态名|emoji|波动率）
    if '|' in _CURRENT_MARKET_REGIME:
        parts = _CURRENT_MARKET_REGIME.split('|')
        return {
            'regime': parts[0],
            'emoji': parts[1] if len(parts) > 1 else '⚪',
            'volatility': float(parts[2]) if len(parts) > 2 else 0.0
        }
    else:
        # 兼容旧格式（仅状态名）
        return {
            'regime': _CURRENT_MARKET_REGIME,
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
            SYSTEM_CONFIG["SIM_CURRENT_BALANCE"] += net_pnl
            
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
            current_balance=SYSTEM_CONFIG["SIM_CURRENT_BALANCE"]
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
            f"模拟余额: ${SYSTEM_CONFIG['SIM_CURRENT_BALANCE']:.2f}"
        )
        
        print(f"{reason_emoji} SCALPER {reason_text}: {symbol}, 净利: ${net_pnl:.2f}")
    
    except Exception as e:
        print(f"⚠️ 关闭 SCALPER 持仓失败: {e}")


# ==========================================
# 止损单监控
# ==========================================

def monitor_stop_loss_orders(client):
    """
    止损单状态监控线程
    支持对冲模式 + 多重子仓位：独立监控每笔订单的止损单
    """
    print("🛡️ 止损监控系统已启动")
    
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
                            
                            # 从列表中移除这笔订单
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
                            
                            # 🔒 线程锁保护：记录到历史交易账本（存储净利润）
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
                            with state_lock:
                                TRADE_HISTORY.append(trade_record)
                                if len(TRADE_HISTORY) > 1000:
                                    TRADE_HISTORY[:] = TRADE_HISTORY[-1000:]
                            
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
                                
                                try:
                                    emergency_order = client.futures_create_order(
                                        symbol=real_symbol,
                                        side='SELL' if position['type'] == 'LONG' else 'BUY',
                                        type='MARKET',
                                        quantity=position['qty']
                                    )
                                    
                                    # 清除持仓记录
                                    with positions_lock:
                                        if key_sym in ACTIVE_POSITIONS:
                                            if isinstance(ACTIVE_POSITIONS[key_sym], list):
                                                ACTIVE_POSITIONS[key_sym] = [
                                                    p for p in ACTIVE_POSITIONS[key_sym]
                                                    if p.get('trade_id') != trade_id
                                                ]
                                                if not ACTIVE_POSITIONS[key_sym]:
                                                    ACTIVE_POSITIONS.pop(key_sym, None)
                                            else:
                                                ACTIVE_POSITIONS.pop(key_sym, None)
                                    save_data()
                                    
                                    print(f"✅ {real_symbol} [{trade_id}] 紧急平仓成功")
                                    send_tg_msg(
                                        f"✅ <b>紧急平仓成功</b>\n"
                                        f"币种: {real_symbol}\n"
                                        f"Trade ID: {trade_id}\n"
                                        f"订单ID: {emergency_order['orderId']}"
                                    )
                                    
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
    ✅ 修复死锁风险：使用 state_lock 保护 PEAK_EQUITY 的读取和更新
    """
    print("📉 回撤监控系统已启动")
    send_tg_msg("📉 <b>回撤监控系统已激活</b>\n将监控账户最大回撤并在必要时触发熔断")
    
    # 初始化峰值权益（从持久化配置中恢复）
    with state_lock:
        if SYSTEM_CONFIG.get("PEAK_EQUITY", 0.0) > 0:
            print(f"📊 从配置恢复峰值权益: ${SYSTEM_CONFIG['PEAK_EQUITY']:.2f}")
        else:
            if client and not SYSTEM_CONFIG["DRY_RUN"]:
                try:
                    acc = client.futures_account()
                    SYSTEM_CONFIG["PEAK_EQUITY"] = float(acc['totalMarginBalance'])
                    save_data()
                    print(f"📊 初始峰值权益: ${SYSTEM_CONFIG['PEAK_EQUITY']:.2f}")
                except:
                    SYSTEM_CONFIG["PEAK_EQUITY"] = SYSTEM_CONFIG.get("BENCHMARK_CASH", 1800.0)
            else:
                SYSTEM_CONFIG["PEAK_EQUITY"] = SYSTEM_CONFIG.get("BENCHMARK_CASH", 1800.0)
    
    while True:
        if not client:
            time.sleep(300)
            continue
        
        try:
            # 🔥 获取当前权益（在锁外执行网络请求）
            if SYSTEM_CONFIG["DRY_RUN"]:
                current_equity = SYSTEM_CONFIG["SIM_CURRENT_BALANCE"]
            else:
                acc = client.futures_account()
                current_equity = float(acc['totalMarginBalance'])
            
            # ✅ 在 state_lock 内部读取和更新 PEAK_EQUITY
            with state_lock:
                peak_equity = SYSTEM_CONFIG.get("PEAK_EQUITY", current_equity)
                
                # 更新峰值权益
                if current_equity > peak_equity:
                    SYSTEM_CONFIG["PEAK_EQUITY"] = current_equity
                    save_data()
                    print(f"📈 更新峰值权益: ${current_equity:.2f}")
                
                # 在锁内计算回撤（确保 PEAK_EQUITY 不会被其他线程修改）
                if SYSTEM_CONFIG["PEAK_EQUITY"] > 0:
                    drawdown = (SYSTEM_CONFIG["PEAK_EQUITY"] - current_equity) / SYSTEM_CONFIG["PEAK_EQUITY"]
                    drawdown_pct = drawdown * 100
                    peak_for_msg = SYSTEM_CONFIG["PEAK_EQUITY"]
                else:
                    drawdown = 0
                    drawdown_pct = 0
                    peak_for_msg = 0
            
            # 在锁外发送消息和执行熔断（避免长时间持锁）
            if drawdown > 0.15 and drawdown <= 0.20:
                send_tg_msg(
                    f"⚠️ <b>回撤预警</b>\n"
                    f"当前回撤: {drawdown_pct:.2f}%\n"
                    f"峰值权益: ${peak_for_msg:.2f}\n"
                    f"当前权益: ${current_equity:.2f}\n"
                    f"回撤金额: ${peak_for_msg - current_equity:.2f}\n\n"
                    f"💡 建议检查策略表现，考虑降低仓位或暂停交易。"
                )
                print(f"⚠️ 回撤预警: {drawdown_pct:.2f}%")
            
            elif drawdown > 0.20 and drawdown <= 0.25:
                send_tg_msg(
                    f"🚨 <b>回撤严重警告</b>\n"
                    f"当前回撤: {drawdown_pct:.2f}%\n"
                    f"峰值权益: ${peak_for_msg:.2f}\n"
                    f"当前权益: ${current_equity:.2f}\n"
                    f"回撤金额: ${peak_for_msg - current_equity:.2f}\n\n"
                    f"⚠️ 回撤已超过20%，强烈建议立即检查！"
                )
                print(f"🚨 回撤严重: {drawdown_pct:.2f}%")
            
            elif drawdown > 0.25:
                print(f"🔴 紧急熔断触发: 回撤 {drawdown_pct:.2f}%")
                
                # 设置熔断标志（在锁内）
                with state_lock:
                    config.TRADING_ENGINE_ACTIVE = False
                
                # 在锁外执行一键全平（避免长时间持锁）
                emergency_close_all(client, chat_id=None)
                
                send_tg_msg(
                    f"🔴 <b>紧急熔断触发</b>\n"
                    f"当前回撤: {drawdown_pct:.2f}%\n"
                    f"峰值权益: ${peak_for_msg:.2f}\n"
                    f"当前权益: ${current_equity:.2f}\n"
                    f"回撤金额: ${peak_for_msg - current_equity:.2f}\n\n"
                    f"🛑 交易引擎已自动停止！\n"
                    f"🛑 已触发系统级强制一键全平，保护剩余本金！\n"
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
    if SYSTEM_CONFIG["DRY_RUN"]:
        daily_stats['start_equity'] = SYSTEM_CONFIG["SIM_CURRENT_BALANCE"]
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
                    if SYSTEM_CONFIG["DRY_RUN"]:
                        end_equity = SYSTEM_CONFIG["SIM_CURRENT_BALANCE"]
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
                    
                    # 更新起始权益为今日开始
                    daily_stats['start_equity'] = end_equity
                else:
                    # 无交易记录，仅更新权益
                    if SYSTEM_CONFIG["DRY_RUN"]:
                        daily_stats['start_equity'] = SYSTEM_CONFIG["SIM_CURRENT_BALANCE"]
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

def daily_ai_report_engine(client):
    """
    每日AI战略战报引擎
    每天凌晨00:05触发，生成战报并推送到TG
    """
    from ai_analyst import get_commander
    
    print("🤖 AI战略战报引擎已启动")
    send_tg_msg("🤖 <b>AI战略战报引擎已激活</b>\n将在每日00:05生成战报")
    
    last_report_date = None
    
    while True:
        try:
            now = datetime.now()
            current_date = now.date()
            
            if now.hour == 0 and now.minute >= 5 and now.minute < 10:
                if last_report_date != current_date:
                    print(f"🌅 开始生成每日AI战报: {current_date}")
                    
                    commander = get_commander()
                    prompt = "请生成今日交易战报，分析系统的盈亏状态，并指出潜在风险。"
                    
                    report_text = commander.ask_commander(prompt)
                    
                    if "###COMMAND###" in report_text:
                        start = report_text.find("###COMMAND###")
                        end = report_text.rfind("###COMMAND###")
                        if start < end:
                            report_text = report_text[:start] + report_text[end + len("###COMMAND###"):]
                    
                    send_tg_msg(f"🤖 <b>每日AI战报</b>\n\n{report_text}")
                    print(f"✅ AI战报已推送")
                    
                    last_report_date = current_date
        
        except Exception as e:
            print(f"⚠️ AI战报引擎异常: {e}")
        
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
                    
                    df_1h = get_historical_klines(client, symbol, "1h", limit=200)
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
                    
                    # 🔥 更新全局市场状态变量
                    global _CURRENT_MARKET_REGIME
                    _CURRENT_MARKET_REGIME = f"{regime}|{regime_emoji}|{relative_atr:.2f}"
                
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
    3. 发送给 GeminiCommander 进行评估
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
            
            # 🔥 Step 5: 调用 GeminiCommander
            from ai_analyst import get_commander
            
            commander = get_commander()
            ai_response = commander.ask_commander(prompt)
            
            if not ai_response:
                print("⚠️ AI 响应为空，跳过本次调参")
                time.sleep(900)
                continue
            
            # 🔥 Step 6: 解析 AI 响应
            import json
            import re
            
            json_match = re.search(r'```json\s*(\{.*?\})\s*```', ai_response, re.DOTALL)
            if not json_match:
                print("⚠️ AI 响应中未找到 JSON，跳过本次调参")
                time.sleep(900)
                continue
            
            ai_json = json.loads(json_match.group(1))
            need_tune = ai_json.get('need_tune', False)
            
            if not need_tune:
                reasoning = ai_json.get('reasoning', '无需调整')
                print(f"✅ AI 评估：{reasoning}")
                time.sleep(900)
                continue
            
            # 🔥 Step 7: 执行自动调参（调用 bot_handlers 中的函数）
            from bot_handlers import execute_auto_tune
            
            result = execute_auto_tune(ai_json)
            
            if result['success']:
                # 更新冷却时间戳
                config.LAST_AUTO_TUNE_TIME = current_time
                print(f"✅ AI 自动调参成功：{result['message']}")
            else:
                print(f"⚠️ AI 自动调参失败：{result['message']}")
        
        except Exception as e:
            print(f"⚠️ AI 自适应巡航调参异常: {e}")
        
        # 每15分钟执行一次
        time.sleep(900)


print("✅ 监控系统模块已加载（含V5.0市场状态分类器 + AI自适应巡航调参引擎）")
