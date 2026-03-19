#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
死信队列工作线程 - dlq_worker.py
负责处理所有未能成功挂上止损单且回滚失败的持仓
采用指数退避策略持续重试，直到交易所确认成功
"""
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
死信队列（DLQ）工作模块
负责处理执行失败的订单、信号或通知的重试逻辑
"""

import time
import threading
from utils.logger_setup import logger
from config import SYSTEM_CONFIG

def dlq_sweeper(client=None):
    """
    🧹 死信清理巡检员
    定期扫描任务队列，处理那些‘卡死’或‘失败’的任务
    """
    logger.info("🧹 死信队列(DLQ)巡检线程已启动...")
    
    # 获取重试配置
    max_retries = SYSTEM_CONFIG.get("MAX_RETRIES", 3)
    
    while True:
        try:
            # 这是一个占位逻辑。在更复杂的版本中，我们会读取一个 dlq.json 文件
            # 或者从数据库中读取失败的任务记录。
            
            # 目前我们先让它处于‘待命’状态，确保主程序不再报错。
            # logger.debug("🔍 正在扫描死信箱...")
            
            # 如果您后续有具体的失败重试逻辑（比如重发失败的 TG 消息），可以写在这里。
            
            # 每 5 分钟巡检一次
            time.sleep(300) 
            
        except Exception as e:
            logger.error(f"❌ 死信巡检员遭遇异常: {e}")
            time.sleep(60)

def start_dlq_worker(client=None):
    """启动清理线程"""
    t = threading.Thread(target=dlq_sweeper, args=(client,), name="DLQSweeper", daemon=True)
    t.start()
    return t
import time
import math
from datetime import datetime
from binance.enums import SIDE_BUY, SIDE_SELL, FUTURE_ORDER_TYPE_LIMIT, TIME_IN_FORCE_GTC

from config import (
    DEAD_LETTER_QUEUE, DLQ_LOCK, DLQ_CONFIG, SYSTEM_CONFIG,
    state_lock, positions_lock
)
import config

from utils.utils import send_tg_alert, round_to_tick_size, round_to_quantity_precision
from utils.logger_setup import logger


def add_to_dlq(symbol, position_type, qty, entry_price, trade_id, error_reason):
    """
    将失败的持仓添加到死信队列
    
    Args:
        symbol: 交易对
        position_type: 'LONG' or 'SHORT'
        qty: 持仓数量
        entry_price: 开仓价格
        trade_id: 交易ID
        error_reason: 失败原因
    """
    with DLQ_LOCK:
        dlq_item = {
            'symbol': symbol,
            'position_type': position_type,
            'qty': qty,
            'entry_price': entry_price,
            'failed_at': time.time(),
            'retry_count': 0,
            'trade_id': trade_id,
            'error_reason': error_reason,
            'last_retry_at': 0
        }
        DEAD_LETTER_QUEUE.append(dlq_item)
        logger.warning(f"🚨 持仓已加入死信队列: {symbol} {position_type} qty={qty} trade_id={trade_id}")
        logger.warning(f"   失败原因: {error_reason}")


def calculate_backoff_time(retry_count):
    """
    计算指数退避时间
    
    Args:
        retry_count: 当前重试次数
    
    Returns:
        int: 退避时间（秒）
    """
    initial = DLQ_CONFIG["INITIAL_BACKOFF"]
    max_backoff = DLQ_CONFIG["MAX_BACKOFF"]
    
    # 指数退避: backoff = initial * (2 ^ retry_count)
    backoff = initial * (2 ** retry_count)
    
    # 限制最大退避时间
    return min(backoff, max_backoff)


def dlq_worker_loop(client):
    """
    死信队列后台清道夫线程
    
    职责:
    1. 不断轮询死信队列
    2. 对每个失败的持仓，采用指数退避策略重试市价平仓
    3. 如果平仓成功，从队列中移除
    4. 如果达到最大重试次数，发送最高级别警报
    5. 队列未清空期间，每5分钟发送一次警报
    
    Args:
        client: 币安客户端
    """
    logger.info("🔥 死信队列清道夫线程已启动")
    send_tg_alert(
        "🔥 <b>死信队列清道夫已激活</b>\n\n"
        "职责: 持续监控并处理所有回滚失败的持仓\n"
        "策略: 指数退避重试，直到交易所确认成功\n"
        "警报: 队列未清空期间每5分钟推送一次"
    )
    
    while True:
        try:
            # 如果引擎未激活，休眠后继续
            if not config.BOT_ACTIVE:
                time.sleep(30)
                continue
            
            # 获取队列快照（避免长时间持锁）
            with DLQ_LOCK:
                queue_snapshot = list(DEAD_LETTER_QUEUE)
            
            # 如果队列为空，休眠后继续
            if not queue_snapshot:
                time.sleep(10)
                continue
            
            # 🚨 队列未清空警报（每5分钟一次）
            current_time = time.time()
            if current_time - DLQ_CONFIG["LAST_ALERT_TIME"] >= DLQ_CONFIG["ALERT_INTERVAL"]:
                send_tg_alert(
                    f"🚨 <b>[高危：死信队列处理中]</b>\n\n"
                    f"队列长度: {len(queue_snapshot)} 笔\n"
                    f"状态: 清道夫正在持续重试\n\n"
                    f"⚠️ 这些持仓当前处于无止损保护状态！\n"
                    f"系统将持续尝试市价平仓，直到成功为止。"
                )
                DLQ_CONFIG["LAST_ALERT_TIME"] = current_time
            
            # 处理队列中的每个项目
            for item in queue_snapshot:
                try:
                    # 检查是否到达重试时间
                    backoff_time = calculate_backoff_time(item['retry_count'])
                    time_since_last_retry = current_time - item['last_retry_at']
                    
                    if time_since_last_retry < backoff_time:
                        # 还未到重试时间，跳过
                        continue
                    
                    # 检查是否达到最大重试次数
                    if item['retry_count'] >= DLQ_CONFIG["MAX_RETRY_COUNT"]:
                        logger.error(f"🔴 死信队列项目已达最大重试次数: {item['symbol']} {item['position_type']}")
                        send_tg_alert(
                            f"🔴 <b>[致命警告：死信队列重试失败]</b>\n\n"
                            f"币种: {item['symbol']}\n"
                            f"方向: {item['position_type']}\n"
                            f"数量: {item['qty']}\n"
                            f"Trade ID: {item['trade_id']}\n"
                            f"重试次数: {item['retry_count']}\n\n"
                            f"⚠️ 已达最大重试次数 ({DLQ_CONFIG['MAX_RETRY_COUNT']})！\n"
                            f"该持仓依然处于无止损裸奔状态！\n\n"
                            f"🚨 <b>请立即登录币安APP手动处理！</b>"
                        )
                        
                        # 从队列中移除（避免无限重试）
                        with DLQ_LOCK:
                            if item in DEAD_LETTER_QUEUE:
                                DEAD_LETTER_QUEUE.remove(item)
                        continue
                    
                    # 尝试市价平仓
                    logger.info(f"🔄 死信队列重试 #{item['retry_count']+1}: {item['symbol']} {item['position_type']}")
                    
                    success = _attempt_emergency_close(client, item)
                    
                    if success:
                        # 平仓成功，从队列中移除
                        with DLQ_LOCK:
                            if item in DEAD_LETTER_QUEUE:
                                DEAD_LETTER_QUEUE.remove(item)
                        
                        logger.info(f"✅ 死信队列项目已成功处理: {item['symbol']} {item['position_type']}")
                        send_tg_alert(
                            f"✅ <b>[死信队列：清理成功]</b>\n\n"
                            f"币种: {item['symbol']}\n"
                            f"方向: {item['position_type']}\n"
                            f"数量: {item['qty']}\n"
                            f"Trade ID: {item['trade_id']}\n"
                            f"重试次数: {item['retry_count']+1}\n\n"
                            f"🛡️ 风险敞口已彻底清除！"
                        )
                    else:
                        # 平仓失败，更新重试计数
                        with DLQ_LOCK:
                            if item in DEAD_LETTER_QUEUE:
                                idx = DEAD_LETTER_QUEUE.index(item)
                                DEAD_LETTER_QUEUE[idx]['retry_count'] += 1
                                DEAD_LETTER_QUEUE[idx]['last_retry_at'] = current_time
                        
                        next_backoff = calculate_backoff_time(item['retry_count'] + 1)
                        logger.warning(
                            f"⚠️ 死信队列重试失败: {item['symbol']} {item['position_type']}, "
                            f"下次重试: {next_backoff}秒后"
                        )
                
                except Exception as e:
                    logger.error(f"❌ 处理死信队列项目异常: {e}", exc_info=True)
                    continue
            
            # 短暂休眠后继续下一轮
            time.sleep(5)
        
        except Exception as e:
            logger.error(f"❌ 死信队列工作线程异常: {e}", exc_info=True)
            time.sleep(30)


def _attempt_emergency_close(client, dlq_item):
    """
    尝试紧急宽幅限价平仓（Fallback from MARKET → wide LIMIT）

    为什么不用市价单？
      币安在极端行情下会触发"市价单保护限制"（Market Order Price Protection），
      偏离标记价格过大的市价单会被直接拒单。DLQ 如果一直重试市价单，
      会被无限拒绝，导致账户裸奔直至爆仓。

    策略：
      - 先获取当前标记价格（markPrice）
      - 平多（SELL）：price = markPrice * 0.97（低于市价 3%，确保能成交）
      - 平空（BUY） ：price = markPrice * 1.03（高于市价 3%，确保能成交）
      - 使用 LIMIT + GTC，强制排入撮合引擎，绕过市价单保护机制

    Args:
        client: 币安客户端
        dlq_item: 死信队列项目

    Returns:
        bool: 是否成功
    """
    if client is None:
        logger.warning("客户端未连接，无法执行紧急平仓")
        return False

    try:
        symbol = dlq_item['symbol']
        position_type = dlq_item['position_type']
        qty = dlq_item['qty']

        # 确定平仓方向
        close_side = SIDE_SELL if position_type == 'LONG' else SIDE_BUY

        # ── Step 1: 获取当前标记价格 ──
        mark_data = client.futures_mark_price(symbol=symbol)
        mark_price = float(mark_data['markPrice'])
        logger.info(f"   📊 {symbol} 当前标记价格: {mark_price}")

        # ── Step 2: 计算宽幅限价 ──
        # 平多(SELL): 挂低 3%，确保在极端下跌中也能被撮合
        # 平空(BUY):  挂高 3%，确保在极端上涨中也能被撮合
        if close_side == SIDE_SELL:
            raw_price = mark_price * 0.97
        else:
            raw_price = mark_price * 1.03

        # 按交易所 tickSize 精度截断
        limit_price = round_to_tick_size(raw_price, symbol)
        logger.info(f"   💰 宽幅限价: {limit_price} (方向={close_side}, 偏移=3%)")

        # ── Step 3: 构建限价单参数 ──
        hedge_enabled = SYSTEM_CONFIG.get("HEDGE_MODE_ENABLED", False)
        order_params = {
            'symbol': symbol,
            'side': close_side,
            'type': FUTURE_ORDER_TYPE_LIMIT,
            'quantity': qty,
            'price': limit_price,
            'timeInForce': TIME_IN_FORCE_GTC,
        }

        if hedge_enabled:
            order_params['positionSide'] = position_type  # 'LONG' or 'SHORT'
            logger.info(f"   🔀 对冲模式紧急平仓：positionSide={position_type}")
        else:
            order_params['positionSide'] = 'BOTH'
            order_params['reduceOnly'] = True
            logger.info(f"   🔀 单向模式紧急平仓：positionSide=BOTH, reduceOnly=True")

        # ── Step 4: 提交限价单 ──
        order = client.futures_create_order(**order_params)
        order_id = order['orderId']
        logger.info(f"✅ 紧急限价平仓订单已提交: orderId={order_id}, price={limit_price}")

        # ── Step 5: 等待并验证订单状态 ──
        time.sleep(2)
        order_status = client.futures_get_order(symbol=symbol, orderId=order_id)

        if order_status['status'] in ['FILLED', 'PARTIALLY_FILLED']:
            filled_qty = float(order_status['executedQty'])
            logger.info(f"✅ 紧急平仓成功: 已成交 {filled_qty} / {qty}")

            if filled_qty < qty:
                remaining_qty = qty - filled_qty
                logger.warning(f"⚠️ 部分成交，剩余数量 {remaining_qty} 将继续重试")
                with DLQ_LOCK:
                    if dlq_item in DEAD_LETTER_QUEUE:
                        idx = DEAD_LETTER_QUEUE.index(dlq_item)
                        DEAD_LETTER_QUEUE[idx]['qty'] = remaining_qty
                return False  # 部分成交视为未完成

            return True
        elif order_status['status'] == 'NEW':
            # 限价单已挂出但尚未成交，取消后下轮重试（避免挂单堆积）
            logger.warning(f"⏳ 限价单尚未成交 (status=NEW)，取消后等待下轮重试")
            try:
                client.futures_cancel_order(symbol=symbol, orderId=order_id)
                logger.info(f"   🗑️ 已取消未成交限价单: orderId={order_id}")
            except Exception as cancel_err:
                logger.warning(f"   ⚠️ 取消限价单失败（可能已成交）: {cancel_err}")
            return False
        else:
            logger.warning(f"⚠️ 紧急平仓订单状态异常: {order_status['status']}")
            return False

    except Exception as e:
        error_msg = str(e)
        logger.error(f"❌ 紧急平仓失败: {error_msg[:200]}", exc_info=True)
        return False


logger.info("✅ 死信队列工作模块已加载")
