#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
执行算法模块 - execution_algo.py
提供流动性感知的智能平仓算法（TWAP等）+ 自适应追单（Order Chasing）
"""

import time
import threading
from utils.logger_setup import logger
from utils.utils import send_tg_msg, send_tg_alert, get_current_price, round_to_quantity_precision, round_to_tick_size
from config import SYSTEM_CONFIG

# ==========================================
# 🔥 追单配置常量
# ==========================================
CHASE_ORDER_INTERVAL_SECS = 3      # 每次追单查询盘口间隔（秒）
CHASE_ORDER_MAX_ATTEMPTS = 5       # 最大追单次数
CHASE_ORDER_MAX_TIMEOUT_SECS = 20  # 最大追单超时时间（秒）


def execute_liquidity_aware_close(client, symbol, position):
    """
    流动性感知的智能平仓函数
    
    核心逻辑：
    1. 检查订单簿深度
    2. 如果流动性充足（订单量 < 市场深度50%），返回 False（使用市价单）
    3. 如果流动性不足，启动 TWAP 算法分批平仓，返回 True
    
    Args:
        client: Binance客户端
        symbol: 交易对
        position: 持仓信息字典
    
    Returns:
        bool: True=已启动TWAP算法, False=流动性充足可直接市价平仓
    """
    try:
        qty = position.get('qty', 0)
        position_type = position.get('type', 'LONG')
        side = 'SELL' if position_type == 'LONG' else 'BUY'
        
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
        depth_ratio = qty / total_depth if total_depth > 0 else float('inf')
        
        # 流动性阈值：50%
        LIQUIDITY_THRESHOLD = 0.5
        
        if depth_ratio > LIQUIDITY_THRESHOLD:
            # 流动性不足，启动 TWAP 算法
            logger.warning(
                f"⚠️ [{symbol}] 流动性不足！订单量={qty:.4f}, "
                f"市场深度={total_depth:.4f}, 占比={depth_ratio*100:.1f}%"
            )
            
            send_tg_msg(
                f"⚠️ <b>[流动性保护启动]</b>\n\n"
                f"币种: {symbol}\n"
                f"平仓数量: {qty:.4f}\n"
                f"市场深度: {total_depth:.4f}\n"
                f"占比: {depth_ratio*100:.1f}%\n\n"
                f"🔄 已启动 TWAP 算法分批平仓"
            )
            
            # 在后台线程启动 TWAP 算法
            twap_thread = threading.Thread(
                target=_execute_twap_close,
                args=(client, symbol, position, qty, side),
                daemon=True
            )
            twap_thread.start()
            
            return True  # 已启动 TWAP
        else:
            # 流动性充足，可以直接市价平仓
            logger.info(
                f"✅ [{symbol}] 流动性充足，订单量占比={depth_ratio*100:.1f}%，"
                f"可直接市价平仓"
            )
            return False  # 使用市价单
    
    except Exception as e:
        logger.error(f"❌ [{symbol}] 流动性检查失败: {e}")
        # 检查失败时保守返回 False，使用市价单快速平仓
        return False


def execute_ioc_protected_close(client, symbol, position, total_qty, side):
    """
    IOC 保护性平仓 - 用于紧急止损场景
    使用限价单 + IOC (Immediate Or Cancel) 替代盲目 TWAP sleep
    
    核心逻辑：
    1. 获取当前盘口价格（Bid/Ask）
    2. 平多单：限价 = Bid * 0.98（向下击穿2%）
    3. 平空单：限价 = Ask * 1.02（向上击穿2%）
    4. timeInForce='IOC' 确保立即成交或取消，滑点控制在2%以内
    """
    try:
        # 获取当前盘口价格（使用 orderbook ticker 获取实时 Bid/Ask）
        ticker = client.futures_orderbook_ticker(symbol=symbol)
        bid_price = float(ticker['bidPrice'])
        ask_price = float(ticker['askPrice'])
        mark_price = (bid_price + ask_price) / 2  # 使用中间价作为标记价格
        
        # 计算保护性限价（2%滑点保护）
        if side == 'SELL':
            limit_price = bid_price * 0.98  # 平多单：向下击穿2%
            entry_price = mark_price
        else:
            limit_price = ask_price * 1.02  # 平空单：向上击穿2%
            entry_price = mark_price
        
        # 价格精度处理
        limit_price = round_to_tick_size(limit_price, symbol)
        qty = round_to_quantity_precision(total_qty, symbol)
        
        logger.warning(
            f"🚨 [{symbol}] IOC 保护性平仓启动：{side} {qty} @ {limit_price} "
            f"(盘口={bid_price if side=='SELL' else ask_price}, 滑点保护=2%)"
        )
        
        # 构建 IOC 订单参数
        ioc_params = {
            'symbol': symbol,
            'side': side,
            'type': 'LIMIT',
            'price': limit_price,
            'quantity': qty,
            'timeInForce': 'IOC'
        }
        
        if SYSTEM_CONFIG.get("HEDGE_MODE_ENABLED", False):
            ioc_params['positionSide'] = position.get('type', 'LONG')
        else:
            ioc_params['positionSide'] = 'BOTH'
            ioc_params['reduceOnly'] = True
        
        # 执行 IOC 订单
        order = client.futures_create_order(**ioc_params)
        
        # 🔥 从币安 API 返回中提取真实成交数据
        filled_qty = float(order.get('executedQty', 0))
        avg_price = float(order.get('avgPrice', limit_price))
        order_status = order.get('status', 'UNKNOWN')
        
        # 计算实际滑点
        slippage_pct = abs(avg_price - entry_price) / entry_price * 100 if entry_price > 0 else 0
        
        # 判断是否部分成交
        is_partial = (filled_qty > 0 and filled_qty < float(qty))
        
        if is_partial:
            logger.warning(
                f"⚠️ [{symbol}] IOC 部分成交：成交 {filled_qty}/{qty}, "
                f"均价={avg_price}, 滑点={slippage_pct:.2f}%, 状态={order_status}"
            )
        else:
            logger.info(
                f"✅ [{symbol}] IOC 平仓完成：成交 {filled_qty}/{qty}, "
                f"均价={avg_price}, 滑点={slippage_pct:.2f}%"
            )
        
        send_tg_msg(
            f"🚨 <b>[IOC 保护性平仓{'部分成交' if is_partial else '完成'}]</b>\n\n"
            f"币种: {symbol}\n"
            f"方向: {side}\n"
            f"计划量: {qty:.4f}\n"
            f"成交量: {filled_qty:.4f}\n"
            f"成交价: {avg_price:.4f}\n"
            f"滑点: {slippage_pct:.2f}%\n"
            f"状态: {order_status}\n\n"
            f"{'⚠️ 存在未成交残仓，将转入 DLQ 重试' if is_partial else '✅ 滑点控制在 2% 以内'}"
        )
        
        # 🔥 返回结构化字典，包含真实成交数据供调用端精确核算
        return {
            'success': filled_qty > 0,
            'filled_qty': filled_qty,
            'avg_price': avg_price,
            'status': order_status,
            'planned_qty': float(qty),
            'is_partial': is_partial,
            'slippage_pct': slippage_pct
        }
        
    except Exception as e:
        logger.error(f"❌ [{symbol}] IOC 保护性平仓失败: {e}")
        send_tg_msg(f"❌ IOC 平仓失败 {symbol}: {e}")
        return {
            'success': False,
            'filled_qty': 0.0,
            'avg_price': 0.0,
            'status': 'ERROR',
            'planned_qty': float(total_qty),
            'is_partial': False,
            'slippage_pct': 0.0,
            'error': str(e)
        }


def _execute_twap_close(client, symbol, position, total_qty, side, is_emergency=False):
    """
    TWAP (Time-Weighted Average Price) 算法执行器
    将大单拆分为多个小单，在一定时间内均匀执行
    
    在紧急止损场景下，使用 IOC 保护性平仓替代盲目 sleep
    
    Args:
        client: Binance客户端
        symbol: 交易对
        position: 持仓信息
        total_qty: 总平仓数量
        side: 平仓方向 ('SELL' or 'BUY')
        is_emergency: 是否为紧急平仓场景
    """
    # 紧急场景：切换至 IOC 保护性平仓
    if is_emergency:
        logger.warning(f"⚠️ [{symbol}] 检测到紧急平仓场景，切换至 IOC 保护性平仓模式")
        return execute_ioc_protected_close(client, symbol, position, total_qty, side)
    try:
        # TWAP 参数配置
        NUM_SLICES = 5  # 拆分为5个子订单
        INTERVAL_SECONDS = 10  # 每个子订单间隔10秒
        
        # 🔥 P3修复: 分片数量必须符合 LOT_SIZE stepSize 规则
        slice_qty = round_to_quantity_precision(total_qty / NUM_SLICES, symbol)
        executed_qty = 0
        
        logger.info(
            f"🔄 [{symbol}] TWAP 算法启动：总量={total_qty:.4f}, "
            f"拆分={NUM_SLICES}笔, 每笔={slice_qty:.4f}"
        )
        
        for i in range(NUM_SLICES):
            try:
                # 最后一笔订单使用剩余数量（避免精度误差）
                if i == NUM_SLICES - 1:
                    current_qty = round_to_quantity_precision(total_qty - executed_qty, symbol)
                else:
                    current_qty = slice_qty
                
                # 🔥 P2修复: 动态构建 positionSide 参数（对冲模式支持）
                close_params = {
                    'symbol': symbol,
                    'side': side,
                    'type': 'MARKET',
                    'quantity': current_qty
                }
                if SYSTEM_CONFIG.get("HEDGE_MODE_ENABLED", False):
                    close_params['positionSide'] = position.get('type', 'LONG')
                else:
                    close_params['positionSide'] = 'BOTH'
                    close_params['reduceOnly'] = True
                
                order = client.futures_create_order(**close_params)
                
                executed_qty += current_qty
                
                logger.info(
                    f"✅ [{symbol}] TWAP 第{i+1}/{NUM_SLICES}笔已执行：{current_qty:.4f}, "
                    f"订单ID={order['orderId']}"
                )
                
                # 如果不是最后一笔，等待间隔时间
                if i < NUM_SLICES - 1:
                    time.sleep(INTERVAL_SECONDS)
            
            except Exception as slice_err:
                logger.error(f"❌ [{symbol}] TWAP 第{i+1}笔执行失败: {slice_err}")
                
                # 如果某笔失败，尝试用市价单平掉剩余仓位
                remaining_qty = round_to_quantity_precision(total_qty - executed_qty, symbol)
                if remaining_qty > 0:
                    try:
                        # 🔥 审计修复: 紧急平仓也需要 positionSide 参数
                        emergency_params = {'symbol': symbol, 'side': side, 'type': 'MARKET', 'quantity': remaining_qty}
                        if SYSTEM_CONFIG.get("HEDGE_MODE_ENABLED", False):
                            emergency_params['positionSide'] = position.get('type', 'LONG')
                        else:
                            emergency_params['positionSide'] = 'BOTH'
                            emergency_params['reduceOnly'] = True
                        emergency_order = client.futures_create_order(**emergency_params)
                        logger.warning(
                            f"⚠️ [{symbol}] TWAP 失败，已用市价单平掉剩余 {remaining_qty:.4f}"
                        )
                    except Exception as emergency_err:
                        logger.critical(
                            f"🔥 [{symbol}] TWAP 失败且紧急平仓也失败: {emergency_err}"
                        )
                        send_tg_msg(
                            f"🔥 <b>[严重告警]</b>\n\n"
                            f"币种: {symbol}\n"
                            f"TWAP 算法失败且紧急平仓也失败\n"
                            f"剩余数量: {remaining_qty:.4f}\n\n"
                            f"⚠️ 请立即手动处理！"
                        )
                break
        
        # TWAP 完成通知
        if executed_qty >= total_qty * 0.99:  # 允许0.01的精度误差
            send_tg_msg(
                f"✅ <b>[TWAP 算法完成]</b>\n\n"
                f"币种: {symbol}\n"
                f"总执行量: {executed_qty:.4f}\n"
                f"拆分笔数: {NUM_SLICES}\n\n"
                f"🎯 已成功保护盘口流动性"
            )
            logger.info(f"✅ [{symbol}] TWAP 算法完成，总执行量={executed_qty:.4f}")
    
    except Exception as e:
        logger.error(f"❌ [{symbol}] TWAP 算法异常: {e}")
        send_tg_msg(
            f"❌ <b>[TWAP 算法异常]</b>\n\n"
            f"币种: {symbol}\n"
            f"错误: {str(e)[:100]}\n\n"
            f"⚠️ 请检查持仓状态"
        )


# ==========================================
# 🔥 自适应追单 (Order Chasing) 逻辑
# ==========================================

async def chase_order(client, symbol, side, total_qty, position_side='BOTH'):
    """
    自适应追单逻辑 - 当订单未能全额成交时启动
    
    核心逻辑：
    1. 每隔 3 秒查询一次盘口（买一/卖一价）
    2. 如果盘口发生移动，撤销原单并以最新价重新挂出 POST_ONLY 订单
    3. 设定最大追单次数（5次）或最大超时时间（20秒）
    4. 超过限制仍未吃满，返回剩余数量转入 DLQ
    
    Args:
        client: Binance客户端
        symbol: 交易对
        side: 订单方向 ('BUY' or 'SELL')
        total_qty: 总目标数量
        position_side: 持仓方向（对冲模式）
    
    Returns:
        dict: {'filled_qty': float, 'remaining_qty': float, 'avg_price': float}
    """
    import asyncio
    
    filled_qty = 0.0
    total_filled_value = 0.0
    attempts = 0
    start_time = time.time()
    last_price = None
    active_order_id = None
    
    logger.info(f"🎯 [{symbol}] 追单启动：{side} {total_qty}, 最大{CHASE_ORDER_MAX_ATTEMPTS}次或{CHASE_ORDER_MAX_TIMEOUT_SECS}秒")
    
    try:
        while attempts < CHASE_ORDER_MAX_ATTEMPTS and (time.time() - start_time) < CHASE_ORDER_MAX_TIMEOUT_SECS:
            attempts += 1
            remaining_qty = round_to_quantity_precision(total_qty - filled_qty, symbol)
            
            if remaining_qty <= 0:
                break
            
            # 获取当前盘口价格
            ticker = client.futures_orderbook_ticker(symbol=symbol)
            current_price = float(ticker['bidPrice']) if side == 'SELL' else float(ticker['askPrice'])
            current_price = round_to_tick_size(current_price, symbol)
            
            # 如果盘口价格变化或首次下单，撤销旧单并重新挂单
            if last_price is None or abs(current_price - last_price) / last_price > 0.0001:
                # 撤销旧订单
                if active_order_id:
                    try:
                        client.futures_cancel_order(symbol=symbol, orderId=active_order_id)
                        logger.info(f"🔄 [{symbol}] 撤销旧单 {active_order_id}，盘口移动 {last_price} → {current_price}")
                    except Exception as cancel_err:
                        logger.warning(f"⚠️ [{symbol}] 撤单失败（可能已成交）: {cancel_err}")
                
                # 挂出新的 POST_ONLY 订单
                order_params = {
                    'symbol': symbol,
                    'side': side,
                    'type': 'LIMIT',
                    'price': current_price,
                    'quantity': remaining_qty,
                    'timeInForce': 'GTX',  # POST_ONLY
                    'positionSide': position_side
                }
                
                try:
                    order = client.futures_create_order(**order_params)
                    active_order_id = order['orderId']
                    last_price = current_price
                    logger.info(f"📌 [{symbol}] 追单第{attempts}次：{side} {remaining_qty} @ {current_price}, 订单ID={active_order_id}")
                except Exception as order_err:
                    logger.error(f"❌ [{symbol}] 追单下单失败: {order_err}")
                    break
            
            # 等待 3 秒后查询订单状态
            await asyncio.sleep(CHASE_ORDER_INTERVAL_SECS)
            
            # 查询订单状态
            if active_order_id:
                try:
                    order_status = client.futures_get_order(symbol=symbol, orderId=active_order_id)
                    exec_qty = float(order_status.get('executedQty', 0))
                    avg_price = float(order_status.get('avgPrice', 0))
                    
                    if exec_qty > 0:
                        filled_qty += exec_qty
                        total_filled_value += exec_qty * avg_price
                        logger.info(f"✅ [{symbol}] 追单部分成交：+{exec_qty}, 累计={filled_qty}/{total_qty}")
                        
                        if order_status['status'] in ['FILLED', 'CANCELED', 'EXPIRED']:
                            active_order_id = None
                except Exception as status_err:
                    logger.warning(f"⚠️ [{symbol}] 查询订单状态失败: {status_err}")
        
        # 追单结束，撤销剩余订单
        if active_order_id:
            try:
                client.futures_cancel_order(symbol=symbol, orderId=active_order_id)
            except:
                pass
        
        avg_price = total_filled_value / filled_qty if filled_qty > 0 else 0.0
        remaining_qty = round_to_quantity_precision(total_qty - filled_qty, symbol)
        
        logger.info(
            f"🏁 [{symbol}] 追单结束：成交={filled_qty}/{total_qty}, "
            f"均价={avg_price:.4f}, 剩余={remaining_qty}, 用时={time.time()-start_time:.1f}秒"
        )
        
        return {
            'filled_qty': filled_qty,
            'remaining_qty': remaining_qty,
            'avg_price': avg_price,
            'attempts': attempts
        }
    
    except Exception as e:
        logger.error(f"❌ [{symbol}] 追单异常: {e}")
        return {
            'filled_qty': filled_qty,
            'remaining_qty': round_to_quantity_precision(total_qty - filled_qty, symbol),
            'avg_price': total_filled_value / filled_qty if filled_qty > 0 else 0.0,
            'attempts': attempts,
            'error': str(e)
        }


def execute_ioc_then_chase_entry(client, symbol, side, total_qty, position_side='BOTH'):
    """
    首笔 IOC + 后续 Chasing 开仓策略
    
    核心逻辑：
    1. 首笔使用 IOC 订单快速吃掉部分流动性
    2. 如果未全额成交，启动 chase_order 追单逻辑
    3. 追单结束后，如果仍有剩余，返回剩余数量转入 DLQ
    
    Args:
        client: Binance客户端
        symbol: 交易对
        side: 订单方向 ('BUY' or 'SELL')
        total_qty: 总目标数量
        position_side: 持仓方向（对冲模式）
    
    Returns:
        dict: {'success': bool, 'filled_qty': float, 'remaining_qty': float, 'avg_price': float}
    """
    import asyncio
    
    try:
        # Phase 1: 首笔 IOC 订单
        ticker = client.futures_orderbook_ticker(symbol=symbol)
        ioc_price = float(ticker['askPrice']) if side == 'BUY' else float(ticker['bidPrice'])
        ioc_price = round_to_tick_size(ioc_price, symbol)
        qty = round_to_quantity_precision(total_qty, symbol)
        
        logger.info(f"🚀 [{symbol}] Phase 1: IOC 开仓 {side} {qty} @ {ioc_price}")
        
        ioc_params = {
            'symbol': symbol,
            'side': side,
            'type': 'LIMIT',
            'price': ioc_price,
            'quantity': qty,
            'timeInForce': 'IOC',
            'positionSide': position_side
        }
        
        order = client.futures_create_order(**ioc_params)
        filled_qty = float(order.get('executedQty', 0))
        avg_price = float(order.get('avgPrice', ioc_price))
        
        logger.info(f"✅ [{symbol}] IOC 成交：{filled_qty}/{qty}, 均价={avg_price:.4f}")
        
        # Phase 2: 如果未全额成交，启动追单
        remaining_qty = round_to_quantity_precision(total_qty - filled_qty, symbol)
        
        if remaining_qty > 0:
            logger.info(f"🎯 [{symbol}] Phase 2: 启动追单，剩余={remaining_qty}")
            
            # 使用 asyncio 运行异步追单
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            chase_result = loop.run_until_complete(
                chase_order(client, symbol, side, remaining_qty, position_side)
            )
            loop.close()
            
            # 合并结果
            total_filled = filled_qty + chase_result['filled_qty']
            total_value = filled_qty * avg_price + chase_result['filled_qty'] * chase_result['avg_price']
            final_avg_price = total_value / total_filled if total_filled > 0 else 0.0
            final_remaining = chase_result['remaining_qty']
            
            send_tg_msg(
                f"🎯 <b>[IOC+追单开仓完成]</b>\n\n"
                f"币种: {symbol}\n"
                f"方向: {side}\n"
                f"目标量: {total_qty:.4f}\n"
                f"IOC成交: {filled_qty:.4f}\n"
                f"追单成交: {chase_result['filled_qty']:.4f}\n"
                f"总成交: {total_filled:.4f}\n"
                f"均价: {final_avg_price:.4f}\n"
                f"剩余: {final_remaining:.4f}\n"
                f"追单次数: {chase_result['attempts']}\n\n"
                f"{'⚠️ 剩余部分将转入 DLQ' if final_remaining > 0 else '✅ 全额成交'}"
            )
            
            return {
                'success': total_filled > 0,
                'filled_qty': total_filled,
                'remaining_qty': final_remaining,
                'avg_price': final_avg_price,
                'chase_attempts': chase_result['attempts']
            }
        else:
            # 首笔 IOC 已全额成交
            send_tg_msg(
                f"✅ <b>[IOC 开仓完成]</b>\n\n"
                f"币种: {symbol}\n"
                f"方向: {side}\n"
                f"成交量: {filled_qty:.4f}\n"
                f"均价: {avg_price:.4f}\n\n"
                f"🎯 首笔 IOC 已全额成交"
            )
            
            return {
                'success': True,
                'filled_qty': filled_qty,
                'remaining_qty': 0.0,
                'avg_price': avg_price,
                'chase_attempts': 0
            }
    
    except Exception as e:
        logger.error(f"❌ [{symbol}] IOC+追单开仓失败: {e}")
        send_tg_alert(f"❌ IOC+追单开仓失败 {symbol}: {e}")
        return {
            'success': False,
            'filled_qty': 0.0,
            'remaining_qty': total_qty,
            'avg_price': 0.0,
            'error': str(e)
        }


print("✅ 执行算法模块已加载（含流动性感知 TWAP 算法 + IOC 保护性平仓 + 自适应追单）")
