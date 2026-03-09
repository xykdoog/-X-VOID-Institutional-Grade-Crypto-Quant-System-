#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
API 权重监控模块 - api_weight_monitor.py
实时监控币安 API 权重消耗，防止触发限流
"""

import time
import threading
from datetime import datetime
from logger_setup import logger
from utils import send_tg_alert

# 全局权重监控状态
API_WEIGHT_STATE = {
    'current_weight': 0,
    'max_weight': 1200,  # 币安默认每分钟权重限制
    'last_update': 0,
    'warning_sent': False,
    'critical_sent': False,
    'lock': threading.Lock()
}

# 权重阈值配置
WEIGHT_THRESHOLDS = {
    'WARNING': 0.80,   # 80% 发送警告
    'CRITICAL': 0.90,  # 90% 发送严重警告
    'EMERGENCY': 0.95  # 95% 紧急警告
}


def update_api_weight(response_headers):
    """
    从响应头中提取并更新 API 权重
    
    Args:
        response_headers: 币安 API 响应头字典
    
    Returns:
        int: 当前权重值
    """
    try:
        # 币安响应头中的权重字段
        weight_key = 'x-mbx-used-weight-1m'  # 1分钟权重
        
        if weight_key in response_headers:
            current_weight = int(response_headers[weight_key])
            
            with API_WEIGHT_STATE['lock']:
                API_WEIGHT_STATE['current_weight'] = current_weight
                API_WEIGHT_STATE['last_update'] = time.time()
            
            # 检查是否需要发送警报
            _check_weight_threshold(current_weight)
            
            return current_weight
        
        # 兼容旧版响应头字段
        old_weight_key = 'x-mbx-used-weight'
        if old_weight_key in response_headers:
            current_weight = int(response_headers[old_weight_key])
            
            with API_WEIGHT_STATE['lock']:
                API_WEIGHT_STATE['current_weight'] = current_weight
                API_WEIGHT_STATE['last_update'] = time.time()
            
            _check_weight_threshold(current_weight)
            
            return current_weight
        
        return 0
    
    except Exception as e:
        logger.error(f"❌ 解析 API 权重失败: {e}")
        return 0


def _check_weight_threshold(current_weight):
    """
    检查权重阈值并发送警报
    
    Args:
        current_weight: 当前权重值
    """
    max_weight = API_WEIGHT_STATE['max_weight']
    usage_ratio = current_weight / max_weight
    
    # 紧急警告（95%）
    if usage_ratio >= WEIGHT_THRESHOLDS['EMERGENCY']:
        if not API_WEIGHT_STATE.get('emergency_sent', False):
            send_tg_alert(
                f"🚨 <b>[紧急：API 权重即将耗尽]</b>\n\n"
                f"当前权重: {current_weight} / {max_weight}\n"
                f"使用率: {usage_ratio*100:.1f}%\n\n"
                f"⚠️ 系统即将触发限流！\n"
                f"建议立即暂停所有非必要操作！"
            )
            API_WEIGHT_STATE['emergency_sent'] = True
            logger.critical(f"🚨 API 权重紧急警告: {current_weight}/{max_weight} ({usage_ratio*100:.1f}%)")
    
    # 严重警告（90%）
    elif usage_ratio >= WEIGHT_THRESHOLDS['CRITICAL']:
        if not API_WEIGHT_STATE.get('critical_sent', False):
            send_tg_alert(
                f"🔴 <b>[严重：API 权重高负载]</b>\n\n"
                f"当前权重: {current_weight} / {max_weight}\n"
                f"使用率: {usage_ratio*100:.1f}%\n\n"
                f"⚠️ 权重消耗接近限制！\n"
                f"请注意控制请求频率。"
            )
            API_WEIGHT_STATE['critical_sent'] = True
            logger.warning(f"🔴 API 权重严重警告: {current_weight}/{max_weight} ({usage_ratio*100:.1f}%)")
    
    # 普通警告（80%）
    elif usage_ratio >= WEIGHT_THRESHOLDS['WARNING']:
        if not API_WEIGHT_STATE.get('warning_sent', False):
            send_tg_alert(
                f"⚠️ <b>[警告：API 权重消耗过高]</b>\n\n"
                f"当前权重: {current_weight} / {max_weight}\n"
                f"使用率: {usage_ratio*100:.1f}%\n\n"
                f"提示: 权重消耗已超过 80%，请注意监控。"
            )
            API_WEIGHT_STATE['warning_sent'] = True
            logger.warning(f"⚠️ API 权重警告: {current_weight}/{max_weight} ({usage_ratio*100:.1f}%)")
    
    # 如果权重降低，重置警报标志
    else:
        if usage_ratio < WEIGHT_THRESHOLDS['WARNING'] * 0.9:  # 降到 72% 以下才重置
            API_WEIGHT_STATE['warning_sent'] = False
            API_WEIGHT_STATE['critical_sent'] = False
            API_WEIGHT_STATE['emergency_sent'] = False


def get_weight_status():
    """
    获取当前权重状态
    
    Returns:
        dict: 权重状态信息
    """
    with API_WEIGHT_STATE['lock']:
        current_weight = API_WEIGHT_STATE['current_weight']
        max_weight = API_WEIGHT_STATE['max_weight']
        last_update = API_WEIGHT_STATE['last_update']
    
    usage_ratio = current_weight / max_weight if max_weight > 0 else 0
    
    # 确定状态等级
    if usage_ratio >= WEIGHT_THRESHOLDS['EMERGENCY']:
        status = '🚨 紧急'
        color = 'red'
    elif usage_ratio >= WEIGHT_THRESHOLDS['CRITICAL']:
        status = '🔴 严重'
        color = 'orange'
    elif usage_ratio >= WEIGHT_THRESHOLDS['WARNING']:
        status = '⚠️ 警告'
        color = 'yellow'
    else:
        status = '✅ 正常'
        color = 'green'
    
    # 计算距离上次更新的时间
    time_since_update = time.time() - last_update if last_update > 0 else 0
    
    return {
        'current_weight': current_weight,
        'max_weight': max_weight,
        'usage_ratio': usage_ratio,
        'usage_percent': usage_ratio * 100,
        'status': status,
        'color': color,
        'last_update': last_update,
        'time_since_update': time_since_update,
        'is_stale': time_since_update > 60  # 超过60秒未更新视为过期
    }


def format_weight_display():
    """
    格式化权重显示（用于仪表盘）
    
    Returns:
        str: 格式化的权重显示字符串
    """
    status = get_weight_status()
    
    # 构建进度条
    bar_length = 20
    filled_length = int(bar_length * status['usage_ratio'])
    bar = '█' * filled_length + '░' * (bar_length - filled_length)
    
    # 构建显示文本
    display = f"{status['status']} API权重: {status['current_weight']}/{status['max_weight']} ({status['usage_percent']:.1f}%)\n"
    display += f"[{bar}]"
    
    # 如果数据过期，添加提示
    if status['is_stale']:
        display += f"\n⚠️ 数据已过期 ({int(status['time_since_update'])}秒前)"
    
    return display


def reset_weight_alerts():
    """
    重置所有权重警报标志（用于测试或手动重置）
    """
    with API_WEIGHT_STATE['lock']:
        API_WEIGHT_STATE['warning_sent'] = False
        API_WEIGHT_STATE['critical_sent'] = False
        API_WEIGHT_STATE['emergency_sent'] = False
    
    logger.info("✅ API 权重警报标志已重置")


def set_max_weight(max_weight):
    """
    设置最大权重限制（用于不同的 API 密钥等级）
    
    Args:
        max_weight: 最大权重值
    """
    with API_WEIGHT_STATE['lock']:
        API_WEIGHT_STATE['max_weight'] = max_weight
    
    logger.info(f"✅ API 最大权重已设置为: {max_weight}")


# 权重监控装饰器（用于包装 API 调用）
def monitor_api_weight(func):
    """
    装饰器：自动监控 API 调用的权重
    
    使用方法:
        @monitor_api_weight
        def my_api_call():
            response = client.futures_account()
            return response
    """
    def wrapper(*args, **kwargs):
        try:
            result = func(*args, **kwargs)
            
            # 如果返回结果是字典且包含响应头信息
            if isinstance(result, dict) and '_response_headers' in result:
                update_api_weight(result['_response_headers'])
            
            return result
        
        except Exception as e:
            logger.error(f"❌ API 调用异常: {e}")
            raise
    
    return wrapper


logger.info("✅ API 权重监控模块已加载")
