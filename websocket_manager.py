#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WebSocket 实时流管理器 - websocket_manager.py
V5.0 核心重构：使用 websocket-client 实现币安 WebSocket 监听
支持实时 K 线推送、增量指标更新、信号即发即刻判定
"""

import json
import time
import threading
from datetime import datetime
from collections import deque
import pandas as pd
import websocket

from config import SYSTEM_CONFIG, get_binance_interval
import config
from utils import send_tg_msg

# ==========================================
# WebSocket 数据缓存
# ==========================================

# 全局 K 线缓存（线程安全）
kline_cache = {}
kline_cache_lock = threading.Lock()

# 全局订单簿缓存
orderbook_cache = {}
orderbook_cache_lock = threading.Lock()

# 全局 Ticker 缓存
ticker_cache = {}
ticker_cache_lock = threading.Lock()

# ==========================================
# WebSocket 连接管理
# ==========================================

def start_websocket_streams(client, symbols):
    """
    启动 WebSocket 实时数据流
    
    Args:
        client: 币安 REST API 客户端
        symbols: 监控币种列表
    """
    if not SYSTEM_CONFIG.get("WEBSOCKET_ENABLED", True):
        print("⚠️ WebSocket 已禁用，跳过启动")
        return
    
    print("🔌 正在启动 WebSocket 实时数据流...")
    send_tg_msg("🔌 <b>WebSocket 实时数据流启动中...</b>")
    
    # 初始化 K 线缓存
    for symbol in symbols:
        _initialize_kline_cache(client, symbol)
    
    # 构建 WebSocket 流 URL
    interval = SYSTEM_CONFIG.get("INTERVAL", "15m")
    binance_interval = get_binance_interval(interval)
    
    # 订阅多个流：kline + ticker + depth
    streams = []
    for symbol in symbols:
        symbol_lower = symbol.lower()
        streams.append(f"{symbol_lower}@kline_{binance_interval}")
        streams.append(f"{symbol_lower}@ticker")
        streams.append(f"{symbol_lower}@depth10@100ms")
    
    stream_url = f"wss://fstream.binance.com/stream?streams={'/'.join(streams)}"
    
    # 启动 WebSocket 连接线程
    ws_thread = threading.Thread(
        target=_websocket_worker,
        args=(stream_url, client),
        daemon=True
    )
    ws_thread.start()
    
    print(f"✅ WebSocket 已订阅 {len(symbols)} 个币种的实时数据流")
    send_tg_msg(f"✅ <b>WebSocket 实时数据流已激活</b>\n监控币种: {', '.join(symbols)}")


def _initialize_kline_cache(client, symbol):
    """初始化 K 线缓存（从 REST API 获取历史数据）"""
    try:
        from trading_engine import get_historical_klines
        
        interval = SYSTEM_CONFIG.get("INTERVAL", "15m")
        df = get_historical_klines(client, symbol, interval, limit=200)
        
        if df is not None and len(df) > 0:
            with kline_cache_lock:
                kline_cache[symbol] = df
            print(f"📊 {symbol} K线缓存已初始化 ({len(df)} 根)")
        else:
            print(f"⚠️ {symbol} K线缓存初始化失败")
            
    except Exception as e:
        print(f"❌ 初始化 {symbol} K线缓存异常: {e}")


def _websocket_worker(stream_url, client):
    """
    WebSocket 工作线程（🔥 Task 1: 强制代理穿透 + 心跳优化）
    
    核心变更：
    - 🔥 Task 1: 强制使用 wss://fstream.binance.com:443/ws（显式端口）
    - 🔥 Task 1: 注入本地代理 http_proxy_host=127.0.0.1, http_proxy_port=4780
    - 🔥 Task 1: 心跳优化 ping_interval=20, ping_timeout=10（应对 Windows 网络抖动）
    - 使用 BOT_ACTIVE 而非 TRADING_ENGINE_ACTIVE 控制生命周期
    - 指数退避重连策略（避免雪崩式重连）
    """
    base_delay = SYSTEM_CONFIG.get("WS_RECONNECT_BASE_DELAY", 2)
    max_delay = SYSTEM_CONFIG.get("WS_RECONNECT_MAX_DELAY", 60)
    retry_count = 0
    
    # 🔥 Task 1: 强制代理配置（端口 4780）
    proxy_host = SYSTEM_CONFIG.get("PROXY_HOST", "127.0.0.1")
    proxy_port = SYSTEM_CONFIG.get("PROXY_PORT", 4780)
    
    while config.BOT_ACTIVE:
        try:
            if retry_count > 0:
                # 🔥 Task 3: 修补断线期间的 K 线数据空白（断网失忆症修复）
                print("🔄 正在修补断线期间的 K 线数据空白...")
                with kline_cache_lock:
                    symbols_to_refresh = list(kline_cache.keys())
                
                for sym in symbols_to_refresh:
                    _initialize_kline_cache(client, sym)
                
                print(f"✅ 已修补 {len(symbols_to_refresh)} 个币种的 K 线数据")
                
                delay = min(base_delay * (2 ** (retry_count - 1)), max_delay)
                print(f"🔄 WebSocket 重连倒计时: {delay} 秒 (第 {retry_count} 次重试)")
                time.sleep(delay)
            
            # 🔥 Task 1: 强制使用 443 端口（显式指定）
            stable_url = stream_url.replace(":9443", ":443")
            if ":443" not in stable_url and ":9443" not in stable_url:
                stable_url = stable_url.replace("wss://fstream.binance.com", "wss://fstream.binance.com:443")
            
            print(f"🔗 正在连接 WebSocket (端口 443 + 代理 {proxy_host}:{proxy_port}): {stable_url[:80]}...")
            
            ws = websocket.WebSocketApp(
                stable_url,
                on_message=lambda ws, msg: _on_message(ws, msg, client),
                on_error=_on_error,
                on_close=_on_close,
                on_open=_on_open
            )
            
            # 🔥 Task 1: 启动 WebSocket（强制代理穿透 + 心跳优化）
            run_forever_kwargs = {
                'ping_interval': 20,  # 🔥 Task 1: 心跳间隔 20 秒
                'ping_timeout': 10,   # 🔥 Task 1: 心跳超时 10 秒
                'http_proxy_host': proxy_host,  # 🔥 Task 1: 强制代理主机
                'http_proxy_port': proxy_port   # 🔥 Task 1: 强制代理端口
            }
            
            print(f"🌐 WebSocket 使用代理: {proxy_host}:{proxy_port}")
            
            ws.run_forever(**run_forever_kwargs)
            
            # 如果正常退出（BOT_ACTIVE=False），跳出循环
            if not config.BOT_ACTIVE:
                print("✅ WebSocket 工作线程正常退出（BOT_ACTIVE=False）")
                break
            
            # 异常退出，增加重试计数
            retry_count += 1
            
        except KeyboardInterrupt:
            print("⚠️ WebSocket 工作线程收到中断信号，正在退出...")
            break
            
        except Exception as e:
            retry_count += 1
            error_msg = f"❌ WebSocket 连接异常 (第 {retry_count} 次重试): {e}"
            print(error_msg)
            
            # 🔥 Task 1: WinError 10060/10061 专项诊断
            error_str = str(e)
            if "10060" in error_str or "10061" in error_str or "WinError 10060" in error_str or "WinError 10061" in error_str:
                diagnostic_msg = (
                    f"🚨 <b>检测到 WinError 10060/10061 错误</b>\n\n"
                    f"⚠️ 这通常表示：\n"
                    f"1. 代理软件未开启（如 V2Ray/Clash）\n"
                    f"2. 代理端口 {proxy_port} 被占用或配置错误\n"
                    f"3. 防火墙阻止了连接\n\n"
                    f"💡 解决方案：\n"
                    f"- 检查代理软件是否运行在端口 {proxy_port}\n"
                    f"- 确认代理协议为 HTTP（非 SOCKS5）\n"
                    f"- 检查防火墙设置"
                )
                print(diagnostic_msg)
                send_tg_msg(diagnostic_msg)
            
            # 🔥 每 5 次失败发送一次 Telegram 告警（避免刷屏）
            if retry_count % 5 == 0:
                send_tg_msg(
                    f"🚨 <b>WebSocket 连接异常</b>\n"
                    f"已重试 {retry_count} 次\n"
                    f"错误: {str(e)[:100]}\n"
                    f"下次重连延迟: {min(base_delay * (2 ** retry_count), max_delay)} 秒"
                )
    
    print("🔌 WebSocket 工作线程已终止")


def _on_open(ws):
    """WebSocket 连接建立回调"""
    print("✅ WebSocket 连接已建立")


def _on_message(ws, message, client):
    """WebSocket 消息回调"""
    try:
        data = json.loads(message)
        
        # 检查消息格式
        if 'stream' not in data or 'data' not in data:
            return
        
        stream_name = data['stream']
        stream_data = data['data']
        
        # 处理 K 线数据
        if '@kline_' in stream_name:
            _handle_kline_update(stream_data, client)
        
        # 处理 Ticker 数据
        elif '@ticker' in stream_name:
            _handle_ticker_update(stream_data)
        
        # 处理订单簿数据
        elif '@depth' in stream_name:
            _handle_depth_update(stream_data)
    
    except Exception as e:
        print(f"⚠️ WebSocket 消息处理异常: {e}")


def _handle_kline_update(kline_data, client):
    """处理 K 线更新"""
    try:
        kline = kline_data['k']
        symbol = kline['s']
        is_closed = kline['x']  # K 线是否已关闭
        
        # 仅在 K 线关闭时更新缓存
        if not is_closed:
            return
        
        # 提取 K 线数据
        timestamp = pd.to_datetime(kline['t'], unit='ms')
        open_price = float(kline['o'])
        high_price = float(kline['h'])
        low_price = float(kline['l'])
        close_price = float(kline['c'])
        volume = float(kline['v'])
        
        # 更新 K 线缓存
        with kline_cache_lock:
            if symbol not in kline_cache:
                return
            
            df = kline_cache[symbol]
            
            # 追加新 K 线
            new_row = pd.DataFrame({
                'timestamp': [timestamp],
                'open': [open_price],
                'high': [high_price],
                'low': [low_price],
                'close': [close_price],
                'volume': [volume]
            })
            
            df = pd.concat([df, new_row], ignore_index=True)
            
            # 保持缓存大小（最近 200 根）
            if len(df) > 200:
                df = df.iloc[-200:].reset_index(drop=True)
            
            kline_cache[symbol] = df
        
        print(f"📈 {symbol} 新K线: {close_price:.4f}")
        
        # 触发信号检测（异步）
        threading.Thread(
            target=_trigger_signal_check,
            args=(symbol, client),
            daemon=True
        ).start()
    
    except Exception as e:
        print(f"⚠️ K线更新处理异常: {e}")


def _handle_ticker_update(ticker_data):
    """处理 Ticker 更新"""
    try:
        symbol = ticker_data['s']
        price = float(ticker_data['c'])
        
        with ticker_cache_lock:
            ticker_cache[symbol] = {
                'price': price,
                'timestamp': datetime.now()
            }
    
    except Exception as e:
        print(f"⚠️ Ticker更新处理异常: {e}")


def _handle_depth_update(depth_data):
    """处理订单簿更新"""
    try:
        symbol = depth_data['s']
        bids = depth_data['b']  # [[price, qty], ...]
        asks = depth_data['a']
        
        if not bids or not asks:
            return
        
        best_bid = float(bids[0][0])
        best_ask = float(asks[0][0])
        
        with orderbook_cache_lock:
            orderbook_cache[symbol] = {
                'bid': best_bid,
                'ask': best_ask,
                'spread': best_ask - best_bid,
                'timestamp': datetime.now()
            }
    
    except Exception as e:
        print(f"⚠️ 订单簿更新处理异常: {e}")


def _trigger_signal_check(symbol, client):
    """触发信号检测（由 K 线关闭事件触发）"""
    try:
        from trading_engine import calculate_indicators, generate_trading_signals
        
        # 获取 K 线缓存
        with kline_cache_lock:
            if symbol not in kline_cache:
                return
            df = kline_cache[symbol].copy()
        
        # 计算技术指标
        df_with_indicators = calculate_indicators(df, force_recalc=True)
        
        if df_with_indicators is None or len(df_with_indicators) == 0:
            return
        
        # 生成交易信号
        signals = generate_trading_signals(df_with_indicators, symbol, client)
        
        # 如果有信号，打印日志（实际交易由主循环处理）
        if signals and signals.get('signals'):
            print(f"🔔 {symbol} WebSocket 检测到信号: {len(signals['signals'])} 个")
    
    except Exception as e:
        print(f"⚠️ 信号检测异常 {symbol}: {e}")


def _on_error(ws, error):
    """WebSocket 错误回调"""
    print(f"❌ WebSocket 错误: {error}")


def _on_close(ws, close_status_code, close_msg):
    """WebSocket 关闭回调"""
    print(f"⚠️ WebSocket 连接已关闭 (状态码: {close_status_code})")


# ==========================================
# 数据访问接口
# ==========================================

def get_cached_kline(symbol):
    """获取缓存的 K 线数据"""
    with kline_cache_lock:
        return kline_cache.get(symbol)


def get_cached_orderbook(symbol):
    """获取缓存的订单簿数据"""
    with orderbook_cache_lock:
        return orderbook_cache.get(symbol)


def get_cached_ticker(symbol):
    """获取缓存的 Ticker 数据"""
    with ticker_cache_lock:
        return ticker_cache.get(symbol)


# ==========================================
# WebSocket 管理器工厂函数
# ==========================================

class WebSocketManager:
    """WebSocket 管理器类"""
    
    def __init__(self, client, signal_callback=None):
        self.client = client
        self.signal_callback = signal_callback
        self.ws_thread = None
        self.is_running = False
    
    def start(self):
        """启动 WebSocket 管理器"""
        if self.is_running:
            print("⚠️ WebSocket 管理器已在运行中")
            return
        
        self.is_running = True
        
        # 获取监控币种列表
        from config import SYSTEM_CONFIG
        symbols = list(SYSTEM_CONFIG.get("ASSET_WEIGHTS", {}).keys())
        
        if not symbols:
            print("⚠️ 未配置监控币种，跳过 WebSocket 启动")
            return
        
        # 启动 WebSocket 流
        start_websocket_streams(self.client, symbols)
        print("✅ WebSocket 管理器已启动")
    
    def stop(self):
        """停止 WebSocket 管理器"""
        self.is_running = False
        config.TRADING_ENGINE_ACTIVE = False
        print("✅ WebSocket 管理器已停止")


_websocket_manager_instance = None


def get_websocket_manager(client=None, signal_callback=None):
    """
    获取 WebSocket 管理器单例
    
    Args:
        client: 币安客户端实例（首次调用时必须提供）
        signal_callback: 信号回调函数（可选）
    
    Returns:
        WebSocketManager: WebSocket 管理器实例
    """
    global _websocket_manager_instance
    
    if _websocket_manager_instance is None:
        if client is None:
            raise ValueError("首次调用 get_websocket_manager 必须提供 client 参数")
        _websocket_manager_instance = WebSocketManager(client, signal_callback)
    
    return _websocket_manager_instance


print("✅ WebSocket 管理器模块已加载（V5.0 兼容版本）")
