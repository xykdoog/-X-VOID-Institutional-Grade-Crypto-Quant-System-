#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
多进程计算工作模块 - worker_logic.py
彻底解决 Windows 平台下的 multiprocessing 循环导入问题
"""
import time
import logging
import pandas as pd
from datetime import datetime
import queue
import multiprocessing as mp
import sys
from trading_engine import calculate_indicators, generate_trading_signals

logger = logging.getLogger(__name__)

def indicator_worker_loop(input_queue, output_queue):
    """
    指标计算工作进程（独立进程，无 GIL 阻塞）
    🔥 V8.0 控制信令架构：通过 config_update 消息热更新配置
    """
    import config
    print("🚀 子进程已启动，等待控制信令同步配置")
    pid = mp.current_process().pid
    print(f"🔥 [Worker-{pid}] 指标计算引擎已点火 (独立防崩溃版本)")

    # 核心：使用原生 List 存储
    local_data_cache = {}
    # 🔥 V7.1 过期阈值（秒），超过此时间的消息直接丢弃
    stale_threshold = config.SYSTEM_CONFIG.get('QUEUE_STALE_SECONDS', 120)
    _stale_drop_count = 0
    
    while True:
        try:
            task = input_queue.get(timeout=0.5)
            if task is None:
                print(f"🛑 [Worker-{pid}] 收到停止信号")
                break

            # 🔥 V7.1 过期检测：丢弃滞留过久的消息
            enqueued_at = task.get('enqueued_at')
            if enqueued_at is not None:
                age = time.time() - enqueued_at
                if age > stale_threshold:
                    _stale_drop_count += 1
                    if _stale_drop_count % 50 == 1:
                        print(f"⏰ [Worker-{pid}] 丢弃过期消息 (age={age:.1f}s > {stale_threshold}s), 累计丢弃: {_stale_drop_count}")
                    continue

            task_type = task.get('type', 'ohlcv')

            # ==========================================
            # 🔥 V8.0 控制信令：热更新子进程配置
            # ==========================================
            if task_type == 'config_update':
                payload = task.get('payload', {})
                if payload:
                    config.SYSTEM_CONFIG.update(payload)
                    # 动态刷新过期阈值
                    stale_threshold = config.SYSTEM_CONFIG.get('QUEUE_STALE_SECONDS', 120)
                    logger.info("子进程已热更新全局配置")
                continue

            symbol = task['symbol']

            # ==========================================
            # 🔥 prefill 模式
            # ==========================================
            if task_type == 'prefill':
                ohlcv_list = task.get('ohlcv_list', [])
                if ohlcv_list:
                    local_data_cache[symbol] = ohlcv_list[-800:]
                    print(f"📊 [Worker-{pid}] {symbol} 历史数据同步完成 ({len(local_data_cache[symbol])} 根)")
                    
                    if len(local_data_cache[symbol]) >= 50:
                        df_pre = pd.DataFrame(local_data_cache[symbol])
                        df_with_indicators = calculate_indicators(df_pre, custom_config=config.SYSTEM_CONFIG)
                        if df_with_indicators is not None:
                            signals = generate_trading_signals(df_with_indicators, symbol, client=None, custom_config=config.SYSTEM_CONFIG)
                             
                            if signals and signals.get('signals'):
                                output_queue.put({
                                    'symbol': symbol,
                                    'signals': signals,
                                    'timestamp': datetime.now().isoformat()
                                })
                                print(f"📤 [Worker-{pid}] {symbol} 预灌后首次扫描捕获信号")
                continue

            # ==========================================
            # 🔥 常规模式
            # ==========================================
            ohlcv = task['ohlcv']

            if symbol not in local_data_cache:
                local_data_cache[symbol] = []

            local_data_cache[symbol].append(ohlcv)

            if len(local_data_cache[symbol]) > 800:
                local_data_cache[symbol] = local_data_cache[symbol][-800:]

            if len(local_data_cache[symbol]) < 50:
                continue

            df_calc = pd.DataFrame(local_data_cache[symbol])
            df_with_indicators = calculate_indicators(df_calc, custom_config=config.SYSTEM_CONFIG)
            
            if df_with_indicators is None:
                continue

            signals = generate_trading_signals(df_with_indicators, symbol, client=None, custom_config=config.SYSTEM_CONFIG)

            if signals and signals.get('signals'):
                output_queue.put({
                    'symbol': symbol,
                    'signals': signals,
                    'timestamp': datetime.now().isoformat()
                })
                print(f"📤 [Worker-{pid}] {symbol} 捕获高胜率信号，已送往发单中枢")

        except queue.Empty:
            continue
        except (OSError, ValueError) as e:
            # Windows WaitForMultipleObjects 崩溃修复
            if sys.platform == 'win32':
                print(f"🛑 [Worker-{pid}] 队列已关闭，进程退出")
                break
            raise
        except Exception as e:
            print(f"❌ [Worker-{pid}] 计算链路异常 ({symbol if 'symbol' in locals() else 'Unknown'}): {e}")
            continue
