#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
🔥 V7.0 WebSocket 队列集成代码片段
将这些代码添加到 websocket_manager.py
"""

import threading
import pandas as pd

# ==========================================
# 第一步：在文件顶部添加全局变量
# ==========================================

# 🔥 V7.0 新增：全局 input_queue 引用（由 main.py 注入）
_global_input_queue = None
_global_input_queue_lock = threading.Lock()


# ==========================================
# 第二步：添加队列注入函数
# ==========================================

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


# ==========================================
# 第三步：修改 _handle_kline_update 函数
# 找到现有的 _handle_kline_update 函数，替换为以下版本
# ==========================================

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
        # 注意：保留原有的缓存更新逻辑
        from websocket_manager import kline_cache, kline_cache_lock
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
        import time as _time
        global _global_input_queue
        if _global_input_queue is not None:
            msg = {
                'symbol': symbol,
                'ohlcv': ohlcv,
                'enqueued_at': _time.time()
            }
            try:
                _global_input_queue.put_nowait(msg)
                print(f"📤 {symbol} OHLCV 已推送到计算进程队列")
            except Exception:
                try:
                    dropped = _global_input_queue.get_nowait()
                    print(f"⚠️ 队列已满，丢弃最旧消息: {dropped.get('symbol','?')}")
                except Exception:
                    pass
                try:
                    _global_input_queue.put_nowait(msg)
                    print(f"📤 {symbol} OHLCV 已推送到计算进程队列 (drop-oldest)")
                except Exception as retry_e:
                    print(f"⚠️ 推送队列二次失败: {retry_e}")
        else:
            print(f"⚠️ input_queue 未初始化，跳过信号处理")
    
    except Exception as e:
        print(f"⚠️ K线更新处理异常: {e}")


# ==========================================
# 集成说明
# ==========================================

"""
集成步骤：

1. 在 websocket_manager.py 顶部添加：
   - _global_input_queue = None
   - _global_input_queue_lock = threading.Lock()

2. 在 websocket_manager.py 中添加 set_input_queue() 函数

3. 找到现有的 _handle_kline_update() 函数，替换为上面的新版本
   关键修改：
   - 移除 threading.Thread(target=_trigger_signal_check, ...).start()
   - 添加 _global_input_queue.put(...) 推送逻辑

4. 确保保留所有其他现有功能（orderbook, ticker 等）
"""
