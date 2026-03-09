#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
日志系统配置模块
提供统一的日志记录器（含回测静默模式 + 全局print劫持）
"""

import logging
import sys
import builtins
from datetime import datetime

# 🔥 全局回测模式标记
_BACKTEST_MODE = False
_DEBUG_MODE = False
_ORIGINAL_PRINT = builtins.print  # 保存原始print函数

def silence_print():
    """
    🔥 劫持全局 print 函数，回测期间屏蔽所有 stdout 输出
    通过 builtins.print 替换为空函数，彻底静默所有模块的 print() 调用
    """
    builtins.print = lambda *args, **kwargs: None

def restore_print():
    """
    🔥 恢复原始 print 函数
    """
    builtins.print = _ORIGINAL_PRINT

def set_backtest_mode(enabled=True, debug=False):
    """
    设置回测模式（静默日志 + 劫持全局 print）
    
    Args:
        enabled: 是否启用回测模式
        debug: 是否启用调试模式（显示详细日志）
    """
    global _BACKTEST_MODE, _DEBUG_MODE
    _BACKTEST_MODE = enabled
    _DEBUG_MODE = debug
    
    # 动态调整全局 logger 级别
    logger = logging.getLogger("WJ-BOT")
    if enabled and not debug:
        # 🔥 回测静默模式：劫持 print + 只显示 WARNING 及以上
        silence_print()
        for handler in logger.handlers:
            if isinstance(handler, logging.StreamHandler):
                handler.setLevel(logging.WARNING)
    elif enabled and debug:
        # 🔥 调试模式：恢复 print + 显示所有日志
        restore_print()
        for handler in logger.handlers:
            if isinstance(handler, logging.StreamHandler):
                handler.setLevel(logging.INFO)
    else:
        # 正常模式：恢复 print + 显示所有日志
        restore_print()
        for handler in logger.handlers:
            if isinstance(handler, logging.StreamHandler):
                handler.setLevel(logging.INFO)

def is_backtest_mode():
    """检查是否处于回测模式"""
    return _BACKTEST_MODE

def is_debug_mode():
    """检查是否处于调试模式"""
    return _DEBUG_MODE

def setup_logger(name="WJ-BOT", level=logging.INFO):
    """
    配置并返回日志记录器
    
    Args:
        name: 日志记录器名称
        level: 日志级别
    
    Returns:
        logger: 配置好的日志记录器
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)
    
    # 避免重复添加处理器
    if logger.handlers:
        return logger
    
    # 控制台处理器
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    
    # 日志格式
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    console_handler.setFormatter(formatter)
    
    logger.addHandler(console_handler)
    
    return logger

# 创建全局日志记录器
logger = setup_logger()
