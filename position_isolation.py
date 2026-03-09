#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
仓位隔离模块 - position_isolation.py
实现机器人仓位与手动仓位的隔离


核心功能：
1. 订单标签化：为所有机器人订单添加唯一标识符
2. 平仓权限校验：仅允许平掉机器人创建的订单
3. 对账逻辑隔离：同步时自动忽略手动订单
4. 紧急平仓白名单：支持特权指令强制平仓
"""

import time
import random
from datetime import datetime
from logger_setup import logger

# ==========================================
# 🔥 机器人订单标签生成器
# ==========================================
BOT_ORDER_PREFIX = "WJ_BOT"


def generate_bot_order_id():
    """
    生成机器人专属订单标签
    格式: WJ_BOT_[时间戳]_[随机数]
    
    Returns:
        str: 唯一订单标识符
    """
    timestamp = int(time.time() * 1000)
    random_suffix = random.randint(1000, 9999)
    return f"{BOT_ORDER_PREFIX}_{timestamp}_{random_suffix}"


def is_bot_order(client_order_id):
    """
    检查订单是否为机器人创建
    
    Args:
        client_order_id: 订单的 newClientOrderId
    
    Returns:
        bool: True=机器人订单，False=手动订单
    """
    if not client_order_id:
        return False
    return str(client_order_id).startswith(BOT_ORDER_PREFIX)


# ==========================================
# 🔥 平仓权限校验
# ==========================================
def validate_close_permission(position, symbol):
    """
    验证是否有权限平掉该持仓
    
    Args:
        position: 持仓信息字典
        symbol: 交易对符号
    
    Returns:
        tuple: (allowed: bool, reason: str)
    """
    try:
        client_order_id = position.get('client_order_id', '')
        trade_id = position.get('trade_id', 'UNKNOWN')
        
        if not is_bot_order(client_order_id):
            reason = f"订单 {trade_id} 不是机器人创建的（clientOrderId={client_order_id or 'NONE'}）"
            logger.warning(f"🚫 [{symbol}] 平仓权限拒绝: {reason}")
            return False, reason
        
        return True, "权限验证通过"
    
    except Exception as e:
        logger.error(f"❌ 平仓权限校验异常: {e}")
        return False, f"权限校验异常: {str(e)[:50]}"


# ==========================================
# 🔥 对账逻辑隔离
# ==========================================
def sync_positions_with_isolation(client, ACTIVE_POSITIONS, positions_lock, save_data_func):
    """
    同步币安真实仓位到本地（含对账隔离）
    
    核心逻辑：
    - 检测到非机器人订单时记录日志并忽略
    - 严禁将手动订单写入 ACTIVE_POSITIONS 或挂载止损单
    
    Args:
        client: 币安客户端
        ACTIVE_POSITIONS: 活跃持仓字典
        positions_lock: 线程锁
        save_data_func: 保存数据函数
    
    Returns:
        dict: 同步统计信息
    """
    from utils import send_tg_msg, send_tg_alert, round_to_tick_size
    import html
    
    if client is None:
        send_tg_msg("⚠️ 币安客户端未连接，无法同步真实仓位。")
        return {'success': False, 'message': '客户端未连接'}
    
    send_tg_msg("🔄 <b>正在与交易所服务器进行对账同步（含隔离检测）...</b>")
    
    try:
        acc_info = client.futures_account()
        real_positions = acc_info.get('positions', [])
        
        synced_count = 0
        cleared_count = 0
        manual_positions_ignored = 0
        new_active = {}
        
        for pos in real_positions:
            amt = float(pos['positionAmt'])
            sym = pos['symbol']
            
            if amt != 0:
                pos_type = 'LONG' if amt > 0 else 'SHORT'
                qty = abs(amt)
                entry_p = float(pos['entryPrice'])
                key_sym = f"{sym}_{pos_type}"
                
                # 🔥 对账隔离：检查该持仓是否为机器人创建
                try:
                    # 查询该持仓对应的开仓订单，检查 clientOrderId
                    trades = client.futures_account_trades(symbol=sym, limit=50)
                    is_bot_position = False
                    matched_client_order_id = None
                    
                    for trade in reversed(trades):  # 从最新交易开始查
                        trade_qty = abs(float(trade['qty']))
                        trade_side = trade['side']
                        
                        # 匹配持仓方向和数量
                        if ((pos_type == 'LONG' and trade_side == 'BUY') or 
                            (pos_type == 'SHORT' and trade_side == 'SELL')):
                            # 检查 clientOrderId 是否为机器人标签
                            client_order_id = trade.get('clientOrderId', '')
                            if is_bot_order(client_order_id):
                                is_bot_position = True
                                matched_client_order_id = client_order_id
                                break
                    
                    # 🔥 如果不是机器人持仓，记录日志并跳过
                    if not is_bot_position:
                        manual_positions_ignored += 1
                        logger.info(f"🚫 [{sym}] 检测到手动持仓，已自动忽略（不写入 ACTIVE_POSITIONS）")
                        logger.info(f"   方向: {pos_type}, 数量: {qty}, 开仓价: {entry_p}")
                        continue  # 跳过该持仓，不写入 ACTIVE_POSITIONS
                    
                    # 机器人持仓，继续同步
                    logger.info(f"✅ [{sym}] 确认为机器人持仓，clientOrderId={matched_client_order_id}")
                    
                    # 查询止损单
                    real_sl_order_id = ""
                    real_sl_price = entry_p * (0.98 if pos_type == 'LONG' else 1.02)
                    sl_found = False
                    
                    try:
                        open_orders = client.futures_get_all_open_orders(symbol=sym)
                        expected_sl_side = 'SELL' if pos_type == 'LONG' else 'BUY'
                        
                        for order in open_orders:
                            if (order.get('type') == 'STOP_MARKET' and 
                                order.get('side') == expected_sl_side):
                                # 检查止损单是否也是机器人创建的
                                sl_client_order_id = order.get('clientOrderId', '')
                                if is_bot_order(sl_client_order_id):
                                    real_sl_order_id = order['orderId']
                                    real_sl_price = float(order.get('stopPrice', real_sl_price))
                                    sl_found = True
                                    break
                    except Exception as sl_e:
                        logger.warning(f"⚠️ [{sym}] 查询止损单异常: {sl_e}")
                    
                    # 构建同步持仓记录
                    synced_pos = {
                        'entry': entry_p,
                        'sl': real_sl_price,
                        'qty': qty,
                        'type': pos_type,
                        'real_symbol': sym,
                        'timestamp': datetime.now(),
                        'trade_id': f"SYNC_{int(time.time())}",
                        'sl_order_id': real_sl_order_id if sl_found else "",
                        'simulated': False,
                        'sl_verified': sl_found,
                        'client_order_id': matched_client_order_id,  # 🔥 记录订单标签
                    }
                    
                    if key_sym not in new_active:
                        new_active[key_sym] = [synced_pos]
                    else:
                        new_active[key_sym].append(synced_pos)
                    
                    synced_count += 1
                    
                except Exception as check_e:
                    # 如果查询失败，保守处理：假设是手动持仓，跳过
                    logger.warning(f"⚠️ [{sym}] 无法验证持仓来源，保守跳过: {check_e}")
                    manual_positions_ignored += 1
                    continue
        
        # 清理本地死仓
        for old_sym in ACTIVE_POSITIONS.keys():
            if old_sym not in new_active:
                cleared_count += 1
        
        # 更新持仓
        with positions_lock:
            ACTIVE_POSITIONS.clear()
            ACTIVE_POSITIONS.update(new_active)
        
        save_data_func()
        
        # 发送同步报告
        msg = "⚖️ <b>持仓对账完成（含隔离检测）</b>\n\n"
        msg += f"✅ 同步到机器人持仓: {synced_count} 个\n"
        msg += f"🧹 清理本地死仓: {cleared_count} 个\n"
        if manual_positions_ignored > 0:
            msg += f"🚫 <b>手动仓位已隔离: {manual_positions_ignored} 个（未写入机器人管理）</b>\n"
        
        send_tg_msg(msg)
        
        return {
            'success': True,
            'synced_count': synced_count,
            'cleared_count': cleared_count,
            'manual_positions_ignored': manual_positions_ignored
        }
        
    except Exception as e:
        error_msg = f"同步异常: {str(e)[:100]}"
        logger.error(f"❌ {error_msg}")
        send_tg_msg(f"❌ {error_msg}")
        return {'success': False, 'message': error_msg}


# ==========================================
# 🔥 紧急平仓白名单
# ==========================================
def emergency_close_all_bot_positions(client, ACTIVE_POSITIONS, positions_lock, save_data_func, force_close_manual=False):
    """
    一键全平功能（支持白名单机制）
    
    Args:
        client: 币安客户端
        ACTIVE_POSITIONS: 活跃持仓字典
        positions_lock: 线程锁
        save_data_func: 保存数据函数
        force_close_manual: 是否强制平掉手动订单（默认 False，仅平机器人订单）
    
    Returns:
        dict: 平仓统计信息
    """
    from utils import send_tg_msg
    from binance.enums import SIDE_BUY, SIDE_SELL, FUTURE_ORDER_TYPE_MARKET
    
    with positions_lock:
        if not ACTIVE_POSITIONS:
            send_tg_msg("📭 本地记录当前没有活跃持仓可平。")
            return {'success': False, 'message': '无持仓'}
        
        symbols_to_close = list(ACTIVE_POSITIONS.keys())
    
    if force_close_manual:
        send_tg_msg("⏳ <b>正在执行一键全平指令（含手动订单）...</b>")
        logger.warning("🔥 强制平仓模式已激活，将平掉所有订单（含手动订单）")
    else:
        send_tg_msg("⏳ <b>正在执行一键全平指令（仅机器人订单）...</b>")
    
    closed_count = 0
    skipped_count = 0
    failed_syms = []
    
    for key_sym in symbols_to_close:
        try:
            positions_data = ACTIVE_POSITIONS[key_sym]
            if not isinstance(positions_data, list):
                positions_data = [positions_data]
            
            real_symbol = key_sym.split('_')[0] if '_' in key_sym else key_sym
            
            for position in positions_data:
                try:
                    # 🔥 紧急平仓白名单检查
                    client_order_id = position.get('client_order_id', '')
                    if not force_close_manual and not is_bot_order(client_order_id):
                        # 非强制模式下，跳过手动订单
                        skipped_count += 1
                        logger.info(f"🚫 跳过手动订单 {key_sym} [Trade_ID={position.get('trade_id')}], 标签: {client_order_id}")
                        continue
                    
                    if not position.get('simulated', False) and client:
                        # 取消止损单
                        if position.get('sl_order_id'):
                            try:
                                client.futures_cancel_order(
                                    symbol=real_symbol,
                                    orderId=position['sl_order_id']
                                )
                            except:
                                pass
                        
                        # 精准平仓
                        act_side = SIDE_SELL if position['type'] == 'LONG' else SIDE_BUY
                        client.futures_create_order(
                            symbol=real_symbol,
                            side=act_side,
                            type=FUTURE_ORDER_TYPE_MARKET,
                            quantity=position['qty'],
                            reduceOnly=True
                        )
                        logger.info(f"✅ 已平仓 {key_sym} 子订单 [Trade_ID={position.get('trade_id')}], 数量: {position['qty']}")
                    
                    closed_count += 1
                    
                except Exception as sub_e:
                    logger.error(f"❌ 平仓子订单失败 {key_sym} [Trade_ID={position.get('trade_id')}]: {sub_e}")
                    failed_syms.append(f"{key_sym}[{position.get('trade_id')}]")
            
            # 清空该方向的所有持仓
            with positions_lock:
                ACTIVE_POSITIONS.pop(key_sym, None)
            
        except Exception as e:
            failed_syms.append(key_sym)
            logger.error(f"❌ [一键全平] 处理 {key_sym} 失败: {e}")
    
    save_data_func()
    
    msg = "🛑 <b>一键全平报告</b>\n\n"
    msg += f"✅ 成功平仓子订单数: {closed_count}\n"
    if skipped_count > 0:
        msg += f"🚫 跳过手动订单: {skipped_count} 笔\n"
    if failed_syms:
        msg += f"❌ 平仓失败: {', '.join(failed_syms)}\n"
    
    send_tg_msg(msg)
    
    return {
        'success': True,
        'closed_count': closed_count,
        'skipped_count': skipped_count,
        'failed_count': len(failed_syms)
    }


logger.info("✅ 仓位隔离模块已加载")
