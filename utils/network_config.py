#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全局网络配置模块 - network_config.py
统一管理所有网络连接的代理配置，严格遵循 USE_PROXY_HARD_SWITCH
"""

import os
from config import USE_PROXY_HARD_SWITCH

# ==========================================
# 🌍 代理配置（严格遵循硬开关，单一真相源）
# ==========================================
PROXY_ENABLED = USE_PROXY_HARD_SWITCH
PROXY_HOST = "127.0.0.1"
PROXY_PORT = 4780

# 构建代理 URL
PROXY_URL = f"http://{PROXY_HOST}:{PROXY_PORT}" if PROXY_ENABLED else None

print(f"🌐 网络配置加载完成: PROXY_ENABLED={PROXY_ENABLED}, PROXY_URL={PROXY_URL}")

# ==========================================
# TeleBot 代理配置
# ==========================================
def get_telebot_proxy():
    """
    获取 TeleBot 的代理配置
    
    Returns:
        dict or None: apihelper.proxy 参数
    """
    if not PROXY_ENABLED:
        return None
    
    return {
        'http': PROXY_URL,
        'https': PROXY_URL
    }


# ==========================================
# Requests 代理配置
# ==========================================
def get_requests_proxies():
    """
    获取 requests 库的代理配置
    
    Returns:
        dict or None: requests.post(proxies=...) 参数
    """
    if not PROXY_ENABLED:
        return None
    
    return {
        'http': PROXY_URL,
        'https': PROXY_URL
    }


# ==========================================
# Binance API 代理配置
# ==========================================
def get_binance_proxies():
    """
    获取 Binance API 的代理配置
    
    注意：Binance API 应该直连，不使用代理
    
    Returns:
        dict: 空字典（强制直连）
    """
    # 🔥 Binance API 强制直连，不使用代理
    return {}


# ==========================================
# WebSocket 代理配置
# ==========================================
def get_websocket_proxy():
    """
    获取 WebSocket 的代理配置
    
    Returns:
        tuple or None: (proxy_host, proxy_port) 或 None
    """
    if not PROXY_ENABLED:
        return None
    
    return (PROXY_HOST, PROXY_PORT)


# ==========================================
# ==========================================
# Legacy function - no longer used (Gemini removed)
# ==========================================
def get_gemini_proxies():
    """
    DEPRECATED: Gemini has been removed from the system
    
    注意：google-generativeai 库不直接支持代理参数
    需要通过 requests 的 Session 或环境变量设置
    
    Returns:
        dict or None: requests 格式的代理配置
    """
    if not PROXY_ENABLED:
        return None
    
    return {
        'http': PROXY_URL,
        'https': PROXY_URL
    }


print("✅ 全局网络配置模块已加载")
