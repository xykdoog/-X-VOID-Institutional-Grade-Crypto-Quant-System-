#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WebSocket 实时流管理器 - websocket_manager.py
🔥 V7.0 多进程架构重构：K线关闭事件 → 推送 OHLCV 到 input_queue
"""

import json
import time
import threading
from datetime import datetime
import pandas as pd
import websocket

from config import SYSTEM_CONFIG, get_binance_interval, USE_PROXY_HARD_SWITCH
import config
from utils.utils import send_tg_msg

# 🔥 V7.0 新增：全局 input_queue 引用（由 main.py 注入）
_global_input_queue = None
_global_input_queue_lock = threading.Lock()

# 全局 K 线缓存（线程安全）
kline_cache = {}
kline_cache_lock = threading.Lock()

# ... [保留其他缓存：orderbook_cache, ticker_cache] ...


def set_input_queue(input_queue):
    """
    注入 input_queue（由 main.py 调用）
    
    Args:
        input_queue: multiprocessing.Queue 实例
    """
    global _global_input_queue
    with _global_input_queue_lock:
        _global_input_queue = input_queue
    print("✅ WebSocket 已绑定 input_queue")


def get_websocket_manager(client=None, callback=None):
    """返回 WebSocket 管理器实例（兼容性函数）"""
    return None


def start_websocket_streams(client, symbols):
    """启动 WebSocket 实时数据流"""
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
    
    streams = []
    for symbol in symbols:
        symbol_lower = symbol.lower()
        streams.append(f"{symbol_lower}@kline_{binance_interval}")
    
    stream_url = f"wss://fstream.binance.com/stream?streams={'/'.join(streams)}"
    
    # 启动 WebSocket 连接线程
    ws_thread = threading.Thread(
        target=_websocket_worker,
        args=(stream_url, client),
        daemon=True
    )
    ws_thread.start()
    
    print(f"✅ WebSocket 已订阅 {len(symbols)} 个币种的实时数据流")


def _initialize_kline_cache(client, symbol):
    """初始化 K 线缓存（从 REST API 获取历史数据）"""
    try:
        from trading_engine import get_historical_klines
        
        interval = SYSTEM_CONFIG.get("INTERVAL", "15m")
        df = get_historical_klines(client, symbol, interval, limit=500)
        
        if df is not None and len(df) > 0:
            with kline_cache_lock:
                kline_cache[symbol] = df
            print(f"📊 {symbol} K线缓存已初始化 ({len(df)} 根)")
    except Exception as e:
        print(f"❌ 初始化 {symbol} K线缓存异常: {e}")


def _websocket_worker(stream_url, client):
    """WebSocket 工作线程"""
    base_delay = SYSTEM_CONFIG.get("WS_RECONNECT_BASE_DELAY", 2)
    max_delay = SYSTEM_CONFIG.get("WS_RECONNECT_MAX_DELAY", 60)
    retry_count = 0
    
    proxy_host = "127.0.0.1"
    proxy_port = 4780
    
    while config.BOT_ACTIVE:
        try:
            if retry_count > 0:
                delay = min(base_delay * (2 ** (retry_count - 1)), max_delay)
                print(f"🔄 WebSocket 重连倒计时: {delay} 秒")
                time.sleep(delay)
            
            stable_url = stream_url.replace(":9443", ":443")
            if ":443" not in stable_url:
                stable_url = stable_url.replace("wss://fstream.binance.com", "wss://fstream.binance.com:443")
            
            print(f"🔗 正在连接 WebSocket: {stable_url[:80]}...")
            
            ws = websocket.WebSocketApp(
                stable_url,
                on_message=lambda ws, msg: _on_message(ws, msg, client),
                on_error=_on_error,
                on_close=_on_close,
                on_open=_on_open
            )
            
            if USE_PROXY_HARD_SWITCH:
                ws.run_forever(
                    http_proxy_host="127.0.0.1",
                    http_proxy_port=4780,
                    proxy_type="http",
                    ping_interval=20,
                    ping_timeout=10
                )
            else:
                ws.run_forever(ping_interval=20, ping_timeout=10)
            
            if not config.BOT_ACTIVE:
                break
            
            retry_count += 1
            
        except KeyboardInterrupt:
            break
        except Exception as e:
            retry_count += 1
            print(f"❌ WebSocket 连接异常: {e}")
    
    print("🔌 WebSocket 工作线程已终止")


def _on_open(ws):
    """WebSocket 连接建立回调"""
    print("✅ WebSocket 连接已建立")


def _on_message(ws, message, client):
    """
    🔥 V7.0 重构：WebSocket 消息回调
    K线关闭事件 → 提取 OHLCV dict → 推送到 input_queue
    """
    try:
        data = json.loads(message)
        
        if 'stream' not in data or 'data' not in data:
            return
        
        stream_name = data['stream']
        stream_data = data['data']
        
        # 处理 K 线数据
        if '@kline_' in stream_name:
            _handle_kline_update(stream_data, client)
    
    except Exception as e:
        print(f"⚠️ WebSocket 消息处理异常: {e}")


def _handle_kline_update(kline_data, client):
    """
    🔥 V7.0 重构：处理 K 线更新
    旧逻辑：K线关闭 → calculate_indicators() → GIL 阻塞
    新逻辑：K线关闭 → 提取 OHLCV dict → push 到 input_queue → 零阻塞
    """
    try:
        kline = kline_data['k']
        symbol = kline['s']
        is_closed = kline['x']
        
        if not is_closed:
            return
        
        # 提取 OHLCV 数据
        ohlcv = {
            'timestamp': pd.to_datetime(kline['t'], unit='ms'),
            'open': float(kline['o']),
            'high': float(kline['h']),
            'low': float(kline['l']),
            'close': float(kline['c']),
            'volume': float(kline['v'])
        }
        
        # 更新本地缓存（用于仪表盘显示）
        with kline_cache_lock:
            if symbol in kline_cache:
                df = kline_cache[symbol]
                new_row = pd.DataFrame([ohlcv])
                df = pd.concat([df, new_row], ignore_index=True)
                if len(df) > 200:
                    df = df.iloc[-200:].reset_index(drop=True)
                kline_cache[symbol] = df
        
        print(f"📈 {symbol} 新K线: {ohlcv['close']:.4f}")
        
        # 🔥 V7.1 Drop-Oldest 策略：推送到 input_queue（零阻塞，防溢出）
        global _global_input_queue
        if _global_input_queue is not None:
            msg = {
                'symbol': symbol,
                'ohlcv': ohlcv,
                'enqueued_at': time.time()  # 用于消费端过期检测
            }
            try:
                _global_input_queue.put_nowait(msg)
                print(f"📤 {symbol} OHLCV 已推送到计算进程队列")
            except Exception:
                # 队列已满 → 丢弃最旧的一条，腾出空间
                try:
                    dropped = _global_input_queue.get_nowait()
                    print(f"⚠️ 队列已满，丢弃最旧消息: {dropped.get('symbol','?')}")
                except Exception:
                    pass  # 竞态：其他消费者刚好取走了，忽略
                try:
                    _global_input_queue.put_nowait(msg)
                    print(f"📤 {symbol} OHLCV 已推送到计算进程队列 (drop-oldest)")
                except Exception as retry_e:
                    print(f"⚠️ 推送队列二次失败: {retry_e}")
    
    except Exception as e:
        print(f"⚠️ K线更新处理异常: {e}")


def _on_error(ws, error):
    """WebSocket 错误回调"""
    print(f"❌ WebSocket 错误: {error}")


def _on_close(ws, close_status_code, close_msg):
    """WebSocket 关闭回调"""
    print(f"⚠️ WebSocket 连接已关闭 (状态码: {close_status_code})")


# ... [保留其他函数：get_cached_kline, WebSocketManager 类等] ...


print("✅ WebSocket 管理器模块已加载（V7.0 多进程架构）")
