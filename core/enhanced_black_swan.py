#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
🔥 P2优化：连续黑天鹅检测增强模块
Enhanced Black Swan Detection with Consecutive Event Tracking
"""

import threading
from datetime import datetime, timedelta
from utils.logger_setup import logger

# Redis 持久化 key
_REDIS_BLACKSWAN_KEY = "wjbot:global:blackswan_state"

# 全局黑天鹅事件历史记录
BLACK_SWAN_HISTORY = []
BLACK_SWAN_LOCK = threading.Lock()

# 熔断状态
CIRCUIT_BREAKER_STATE = {
    'active': False,
    'resume_time': None,
    'trigger_count': 0
}


def check_black_swan_defense_enhanced(df, symbol):
    """
    🔥 增强版黑天鹅防御（含连续事件检测 + 分级熔断）
    
    核心改进：
    1. 记录黑天鹅事件历史（时间戳 + 类型）
    2. 检测1小时内连续3次触发 → 触发1小时熔断
    3. 检测24小时内累计5次触发 → 触发4小时熔断
    4. 自动恢复机制：熔断到期后自动解除
    
    Args:
        df: K线数据
        symbol: 交易对
    
    Returns:
        dict or None: {'signal': 'HOLD', 'reason': str} 或 None（放行）
    """
    if df is None or len(df) < 2:
        return None
    
    try:
        current_candle = df.iloc[-1]
        prev_candle = df.iloc[-2]
        current_time = datetime.now()
        
        # ==========================================
        # 检查1：熔断状态检查（优先级最高）
        # ==========================================
        with BLACK_SWAN_LOCK:
            if CIRCUIT_BREAKER_STATE['active']:
                if current_time < CIRCUIT_BREAKER_STATE['resume_time']:
                    remaining = (CIRCUIT_BREAKER_STATE['resume_time'] - current_time).total_seconds() / 60
                    reason = f"系统熔断中，剩余{remaining:.0f}分钟（连续黑天鹅触发{CIRCUIT_BREAKER_STATE['trigger_count']}次）"
                    logger.warning(f"🚨 [{symbol}] {reason}")
                    return {'signal': 'HOLD', 'reason': reason}
                else:
                    # 熔断到期，自动恢复
                    CIRCUIT_BREAKER_STATE['active'] = False
                    CIRCUIT_BREAKER_STATE['resume_time'] = None
                    CIRCUIT_BREAKER_STATE['_last_resume_time'] = current_time  # 🔥 记录恢复时间
                    logger.info(f"✅ 熔断结束，系统恢复交易")
                    _persist_circuit_breaker_to_redis()
                    _send_recovery_notification()
        
        # ==========================================
        # 检查2：跳空检测（>5%）
        # ==========================================
        prev_close = prev_candle['close']
        current_open = current_candle['open']
        
        if prev_close > 0:
            gap_ratio = abs(current_open - prev_close) / prev_close
            if gap_ratio > 0.05:
                _record_black_swan_event(symbol, 'GAP', gap_ratio, current_time)
                reason = f"检测到跳空异动 ({gap_ratio*100:.2f}%)"
                logger.warning(f"🚨 [{symbol}] 黑天鹅拦截: {reason}")
                
                # 检查是否触发连续熔断
                _check_and_trigger_circuit_breaker(symbol)
                
                return {'signal': 'HOLD', 'reason': f'黑天鹅防御: {reason}'}
        
        # ==========================================
        # 检查3：极端振幅（>10%）
        # ==========================================
        candle_range = current_candle['high'] - current_candle['low']
        current_close = current_candle['close']
        
        if current_close > 0:
            amplitude_ratio = candle_range / current_close
            if amplitude_ratio > 0.10:
                _record_black_swan_event(symbol, 'AMPLITUDE', amplitude_ratio, current_time)
                reason = f"检测到极端振幅 ({amplitude_ratio*100:.2f}%)"
                logger.warning(f"🚨 [{symbol}] 黑天鹅拦截: {reason}")
                
                # 检查是否触发连续熔断
                _check_and_trigger_circuit_breaker(symbol)
                
                return {'signal': 'HOLD', 'reason': f'黑天鹅防御: {reason}'}
        
        # ==========================================
        # 检查4：天量异动（>5倍均量）
        # ==========================================
        current_volume = current_candle['volume']
        avg_volume_20 = df['volume'].tail(20).mean()
        
        if avg_volume_20 > 0:
            volume_ratio = current_volume / avg_volume_20
            if volume_ratio > 5.0:
                _record_black_swan_event(symbol, 'VOLUME', volume_ratio, current_time)
                reason = f"检测到天量异动 ({volume_ratio:.2f}x均量)"
                logger.warning(f"🚨 [{symbol}] 黑天鹅拦截: {reason}")
                
                # 检查是否触发连续熔断
                _check_and_trigger_circuit_breaker(symbol)
                
                return {'signal': 'HOLD', 'reason': f'黑天鹅防御: {reason}'}
        
        # 所有检查通过
        return None
        
    except Exception as e:
        logger.error(f"⚠️ [{symbol}] 黑天鹅检测异常: {e}")
        # 异常时保守拦截
        return {'signal': 'HOLD', 'reason': f'黑天鹅检测异常: {str(e)[:50]}'}


def _persist_circuit_breaker_to_redis():
    """将熔断状态持久化到 Redis（静默降级）"""
    try:
        from redis_manager import redis_db
        if not redis_db.enabled:
            return
        
        last_resume = CIRCUIT_BREAKER_STATE.get('_last_resume_time')
        state_data = {
            'active': CIRCUIT_BREAKER_STATE['active'],
            'resume_time': CIRCUIT_BREAKER_STATE['resume_time'].isoformat() if CIRCUIT_BREAKER_STATE['resume_time'] else '',
            'trigger_count': CIRCUIT_BREAKER_STATE['trigger_count'],
            'last_resume_time': last_resume.isoformat() if last_resume else ''
        }
        redis_db.save_hash(_REDIS_BLACKSWAN_KEY, state_data)
        logger.debug(f"✅ 熔断状态已持久化到 Redis")
    except Exception as e:
        logger.warning(f"⚠️ Redis 持久化失败（降级到内存模式）: {e}")


def _restore_circuit_breaker_from_redis():
    """从 Redis 恢复熔断状态（静默降级）"""
    try:
        from redis_manager import redis_db
        if not redis_db.enabled:
            return
        
        state_data = redis_db.load_hash(_REDIS_BLACKSWAN_KEY)
        if not state_data:
            return
        
        active = state_data.get('active', False)
        if isinstance(active, str):
            active = active == 'True'
        resume_time_str = state_data.get('resume_time', '')
        trigger_count = int(state_data.get('trigger_count', 0))
        last_resume_str = state_data.get('last_resume_time', '')
        
        # 恢复 _last_resume_time（用于灾后重建乘数）
        if last_resume_str:
            try:
                CIRCUIT_BREAKER_STATE['_last_resume_time'] = datetime.fromisoformat(last_resume_str)
            except:
                pass
        
        if active and resume_time_str:
            resume_time = datetime.fromisoformat(resume_time_str)
            if datetime.now() < resume_time:
                CIRCUIT_BREAKER_STATE['active'] = True
                CIRCUIT_BREAKER_STATE['resume_time'] = resume_time
                CIRCUIT_BREAKER_STATE['trigger_count'] = trigger_count
                remaining = (resume_time - datetime.now()).total_seconds() / 60
                logger.warning(f"🔄 从 Redis 恢复熔断状态，剩余 {remaining:.0f} 分钟")
            else:
                # 熔断已过期，清除 Redis 中的过期状态
                logger.info(f"ℹ️ Redis 中的熔断已过期，清除状态")
                redis_db.save_hash(_REDIS_BLACKSWAN_KEY, {'active': False, 'resume_time': '', 'trigger_count': 0})
    except Exception as e:
        logger.warning(f"⚠️ Redis 恢复失败（降级到内存模式）: {e}")


def _record_black_swan_event(symbol, event_type, severity, timestamp):
    """
    记录黑天鹅事件到历史
    
    Args:
        symbol: 交易对
        event_type: 事件类型（GAP/AMPLITUDE/VOLUME）
        severity: 严重程度（比率值）
        timestamp: 时间戳
    """
    with BLACK_SWAN_LOCK:
        BLACK_SWAN_HISTORY.append({
            'symbol': symbol,
            'type': event_type,
            'severity': severity,
            'timestamp': timestamp
        })
        
        # 限制历史记录数量（保留最近100条）
        if len(BLACK_SWAN_HISTORY) > 100:
            BLACK_SWAN_HISTORY[:] = BLACK_SWAN_HISTORY[-100:]
        
        logger.info(f"📋 黑天鹅事件已记录: {symbol} {event_type} {severity:.2f}")


def _check_and_trigger_circuit_breaker(symbol):
    """
    检查是否触发连续黑天鹅熔断
    
    分级熔断逻辑：
    - Level 1: 1小时内3次触发 → 熔断1小时
    - Level 2: 24小时内5次触发 → 熔断4小时
    
    Args:
        symbol: 触发事件的交易对
    """
    current_time = datetime.now()
    
    with BLACK_SWAN_LOCK:
        # 统计1小时内的事件
        events_1h = [
            e for e in BLACK_SWAN_HISTORY
            if current_time - e['timestamp'] < timedelta(hours=1)
        ]
        
        # 统计24小时内的事件
        events_24h = [
            e for e in BLACK_SWAN_HISTORY
            if current_time - e['timestamp'] < timedelta(hours=24)
        ]
        
        # Level 2: 24小时内累计5次 → 熔断4小时
        if len(events_24h) >= 5:
            CIRCUIT_BREAKER_STATE['active'] = True
            CIRCUIT_BREAKER_STATE['resume_time'] = current_time + timedelta(hours=4)
            CIRCUIT_BREAKER_STATE['trigger_count'] = len(events_24h)
            
            logger.critical(f"🚨🚨🚨 Level 2 熔断触发！24小时内检测到{len(events_24h)}次黑天鹅事件，系统熔断4小时")
            _persist_circuit_breaker_to_redis()
            _send_circuit_breaker_alert(2, 4, len(events_24h), events_24h[-5:])
            return
        
        # Level 1: 1小时内3次 → 熔断1小时
        if len(events_1h) >= 3:
            CIRCUIT_BREAKER_STATE['active'] = True
            CIRCUIT_BREAKER_STATE['resume_time'] = current_time + timedelta(hours=1)
            CIRCUIT_BREAKER_STATE['trigger_count'] = len(events_1h)
            
            logger.critical(f"🚨 Level 1 熔断触发！1小时内检测到{len(events_1h)}次黑天鹅事件，系统熔断1小时")
            _persist_circuit_breaker_to_redis()
            _send_circuit_breaker_alert(1, 1, len(events_1h), events_1h[-3:])
            return


def _send_circuit_breaker_alert(level, duration_hours, event_count, recent_events):
    """
    发送熔断告警通知
    
    Args:
        level: 熔断级别（1或2）
        duration_hours: 熔断时长（小时）
        event_count: 触发事件数量
        recent_events: 最近的事件列表
    """
    try:
        from utils import send_tg_alert
        import html
        
        # 构建事件详情
        event_details = "\n".join([
            f"• {e['symbol']} {e['type']} {e['severity']:.2f} ({e['timestamp'].strftime('%H:%M:%S')})"
            for e in recent_events
        ])
        
        alert_msg = (
            f"🚨🚨🚨 <b>[连续黑天鹅熔断 Level {level}]</b>\n\n"
            f"触发条件: {'1小时内3次' if level == 1 else '24小时内5次'}\n"
            f"检测到事件: {event_count} 次\n"
            f"熔断时长: {duration_hours} 小时\n\n"
            f"<b>最近事件:</b>\n{html.escape(event_details)}\n\n"
            f"⚠️ 系统已暂停所有新开仓，现有持仓不受影响\n"
            f"🕐 预计恢复时间: {CIRCUIT_BREAKER_STATE['resume_time'].strftime('%Y-%m-%d %H:%M:%S')}"
        )
        
        send_tg_alert(alert_msg)
        
    except Exception as e:
        logger.error(f"⚠️ 发送熔断告警失败: {e}")


def _send_recovery_notification():
    """发送熔断恢复通知"""
    try:
        from utils import send_tg_msg
        
        msg = (
            f"✅ <b>[系统熔断已解除]</b>\n\n"
            f"熔断时长已到期，系统已恢复正常交易\n"
            f"恢复时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            f"🔄 黑天鹅防御系统继续监控中"
        )
        
        send_tg_msg(msg)
        
    except Exception as e:
        logger.error(f"⚠️ 发送恢复通知失败: {e}")


def resume_trading_manually():
    """手动恢复交易（管理员命令）"""
    with BLACK_SWAN_LOCK:
        if CIRCUIT_BREAKER_STATE['active']:
            CIRCUIT_BREAKER_STATE['active'] = False
            CIRCUIT_BREAKER_STATE['resume_time'] = None
            CIRCUIT_BREAKER_STATE['_last_resume_time'] = datetime.now()  # 🔥 记录恢复时间
            logger.info("✅ 管理员手动解除熔断")
            _persist_circuit_breaker_to_redis()
            _send_recovery_notification()
            return True
        else:
            logger.info("ℹ️ 系统未处于熔断状态")
            return False


def get_black_swan_status():
    """
    获取黑天鹅防御系统状态
    
    Returns:
        dict: 状态信息
    """
    with BLACK_SWAN_LOCK:
        current_time = datetime.now()
        
        # 统计最近事件
        events_1h = [
            e for e in BLACK_SWAN_HISTORY
            if current_time - e['timestamp'] < timedelta(hours=1)
        ]
        
        events_24h = [
            e for e in BLACK_SWAN_HISTORY
            if current_time - e['timestamp'] < timedelta(hours=24)
        ]
        
        return {
            'circuit_breaker_active': CIRCUIT_BREAKER_STATE['active'],
            'resume_time': CIRCUIT_BREAKER_STATE['resume_time'].isoformat() if CIRCUIT_BREAKER_STATE['resume_time'] else None,
            'trigger_count': CIRCUIT_BREAKER_STATE['trigger_count'],
            'events_1h': len(events_1h),
            'events_24h': len(events_24h),
            'total_events': len(BLACK_SWAN_HISTORY),
            'recent_events': [
                {
                    'symbol': e['symbol'],
                    'type': e['type'],
                    'severity': e['severity'],
                    'timestamp': e['timestamp'].isoformat()
                }
                for e in BLACK_SWAN_HISTORY[-10:]
            ]
        }


# =========================================
# 🔥 黑天鹅后重建乘数：熔断恢复后放大被高ATR压缩的仓位
# ==========================================

# 黑天鹅后重建期时长（秒）
POST_DISASTER_RECOVERY_WINDOW = 24 * 3600  # 24小时
# 黑天鹅后重建期仓位放大乘数
POST_DISASTER_RECOVERY_MULT = 1.5


def get_post_disaster_recovery_multiplier():
    """
    🔥 黑天鹅后重建乘数：捕获 V 型反转利润
    
    当黑天鹅熔断结束后，ATR 仍处于历史极值余震，导致 Risk/(ATR*Mult) 公式
    算出的仓位极小（"蚂蚁仓"）。本函数在熔断恢复后的 24 小时内返回放大乘数，
    允许在被高 ATR 压缩后的仓位基础上适度放大，捕获灾后最丰厚的 V 型反转。
    
    逻辑：
    1. 读取内存中的 CIRCUIT_BREAKER_STATE['resume_time']（熔断结束时间）
    2. 如果内存无记录，尝试从 Redis 恢复
    3. 如果当前时间距 resume_time < 24 小时 → 返回 1.5（放大 50%）
    4. 否则返回 1.0（正常仓位）
    
    Returns:
        float: 仓位放大乘数（1.0 或 1.5）
    """
    try:
        current_time = datetime.now()
        resume_time = None
        
        with BLACK_SWAN_LOCK:
            # 情况1：熔断仍在进行中 → 不放大（此时不应开仓）
            if CIRCUIT_BREAKER_STATE['active']:
                return 1.0
            
            # 情况2：熔断已结束，检查 resume_time
            # resume_time 在熔断解除时会被设为 None，但我们需要记录"最后一次恢复时间"
            # 使用新字段 _last_resume_time 来追踪
            resume_time = CIRCUIT_BREAKER_STATE.get('_last_resume_time')
        
        # 情况3：内存中无恢复时间记录，尝试从 Redis 读取
        if resume_time is None:
            try:
                from redis_manager import redis_db
                if redis_db.enabled:
                    state_data = redis_db.load_hash(_REDIS_BLACKSWAN_KEY)
                    if state_data:
                        last_resume_str = state_data.get('last_resume_time', '')
                        if last_resume_str:
                            resume_time = datetime.fromisoformat(last_resume_str)
            except Exception as e:
                logger.debug(f"灾后重建乘数: Redis 读取失败（降级）: {e}")
        
        # 情况4：从未发生过熔断
        if resume_time is None:
            return 1.0
        
        # 情况5：计算距离恢复时间的间隔
        elapsed = (current_time - resume_time).total_seconds()
        
        if 0 < elapsed < POST_DISASTER_RECOVERY_WINDOW:
            remaining_hours = (POST_DISASTER_RECOVERY_WINDOW - elapsed) / 3600
            logger.info(
                f"🛠️ 灾后重建乘数 {POST_DISASTER_RECOVERY_MULT}x 生效，"
                f"正在捕获 V 型反转（剩余 {remaining_hours:.1f} 小时）"
            )
            return POST_DISASTER_RECOVERY_MULT
        
        # 超过 24 小时，恢复正常
        return 1.0
    
    except Exception as e:
        logger.error(f"⚠️ 灾后重建乘数计算异常: {e}")
        return 1.0


# 模块加载时从 Redis 恢复熔断状态（确保重启后熔断不失效）
_restore_circuit_breaker_from_redis()

logger.info("✅ 增强版黑天鹅防御模块已加载（连续事件检测 + 分级熔断 + Redis 持久化）")
