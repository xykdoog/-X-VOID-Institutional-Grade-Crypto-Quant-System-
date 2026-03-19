#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Redis-based 配置加载和保存函数
用于替换 config.py 中的 JSON 文件操作
"""

from redis_manager import redis_db
from datetime import datetime
import threading
import time

# 从 config.py 导入必要的全局变量和锁
# 注意：这些将在实际集成时直接在 config.py 中使用

def load_data_redis(SYSTEM_CONFIG, ACTIVE_POSITIONS, TRADE_HISTORY, SENTRY_CONFIG, 
                    USER_SESSION_STATE, DEAD_LETTER_QUEUE, state_lock, SESSION_LOCK, DLQ_LOCK):
    """
    🔥 从 Redis 极速加载持久化数据
    """
    # 1. 加载主配置
    config_key = "wjbot:global:config"
    saved_config = redis_db.load_hash(config_key)
    if saved_config:
        for k, v in saved_config.items():
            if k not in ["API_KEY", "API_SECRET", "TG_TOKEN", "TG_CHAT_ID", "USE_PROXY_HARD_SWITCH"]:
                SYSTEM_CONFIG[k] = v
        print(f"✅ 成功从 Redis 加载持久化配置")
    else:
        print("⚠️ Redis 中无配置数据，使用初始默认配置")

    running_mode = SYSTEM_CONFIG.get("RUNNING_MODE", "SANDBOX")
    prefix = redis_db.get_key_prefix(running_mode)

    # 2. 加载持仓 (Hash)
    positions_key = f"{prefix}:positions"
    ACTIVE_POSITIONS.clear()
    positions_data = redis_db.load_hash(positions_key)
    ACTIVE_POSITIONS.update(positions_data)
    # 恢复 datetime 对象
    _parse_positions_redis(ACTIVE_POSITIONS)
    print(f"✅ 成功从 Redis 加载 {len(ACTIVE_POSITIONS)} 个持仓 [{running_mode}]")

    # 3. 加载交易历史 (List)
    history_key = f"{prefix}:history"
    TRADE_HISTORY.clear()
    TRADE_HISTORY.extend(redis_db.load_list(history_key))
    print(f"✅ 成功从 Redis 加载 {len(TRADE_HISTORY)} 条交易历史 [{running_mode}]")

    # 4. 加载死信队列 (List)
    DEAD_LETTER_QUEUE.clear()
    DEAD_LETTER_QUEUE.extend(redis_db.load_list("wjbot:global:dlq"))
    
    # 5. 加载用户会话 (Hash)
    USER_SESSION_STATE.clear()
    sessions_data = redis_db.load_hash("wjbot:global:sessions")
    USER_SESSION_STATE.update({int(k): v for k, v in sessions_data.items()})


def save_data_redis(SYSTEM_CONFIG, ACTIVE_POSITIONS, TRADE_HISTORY, state_lock):
    """
    🔥 原子性保存数据到 Redis（亚毫秒级）
    由于写入极快，直接在 state_lock 内完成，彻底消灭 IO 阻塞
    """
    try:
        with state_lock:
            # 1. 提取快照
            config_snapshot = {k: v for k, v in SYSTEM_CONFIG.items() 
                             if k not in ["API_KEY", "API_SECRET", "TG_TOKEN", "TG_CHAT_ID"]}
            positions_snapshot = ACTIVE_POSITIONS.copy()
            running_mode = SYSTEM_CONFIG.get("RUNNING_MODE", "SANDBOX")
            prefix = redis_db.get_key_prefix(running_mode)

            # 2. 写入 Redis
            # 配置写入
            redis_db.save_hash("wjbot:global:config", config_snapshot)
            
            # 持仓写入（datetime 的序列化已在 redis_db 内部处理）
            redis_db.save_hash(f"{prefix}:positions", positions_snapshot)
            
            # ⚠️ 注意：TRADE_HISTORY 不再在这里全量保存！
            # 交易历史的追加将在平仓逻辑中直接调用 redis_db.append_to_list 完成
            
        # print(f"✅ 数据已同步至 Redis ({running_mode})") # 实盘太频繁可注释掉
    except Exception as e:
        print(f"❌ 同步 Redis 失败: {e}")


def save_dlq_redis(DEAD_LETTER_QUEUE, DLQ_LOCK):
    """将死信队列同步到 Redis"""
    with DLQ_LOCK:
        redis_db.save_full_list("wjbot:global:dlq", DEAD_LETTER_QUEUE)


def _save_session_state_redis(USER_SESSION_STATE, SESSION_LOCK):
    """将用户会话状态同步到 Redis"""
    with SESSION_LOCK:
        sessions_to_save = {str(k): v for k, v in USER_SESSION_STATE.items()}
        redis_db.save_hash("wjbot:global:sessions", sessions_to_save)


def _parse_positions_redis(positions):
    """解析持仓数据（从 Redis Hash 还原 datetime 对象）"""
    for sym, pos_data in positions.items():
        # 🔥 强制列表化：如果读取到的不是列表，转换为列表
        if not isinstance(pos_data, list):
            pos_data = [pos_data]
        
        # 处理每个子订单的时间戳
        for pos in pos_data:
            if 'timestamp' in pos and isinstance(pos['timestamp'], str):
                try:
                    pos['timestamp'] = datetime.fromisoformat(pos['timestamp'])
                except:
                    pos['timestamp'] = datetime.now()
        
        positions[sym] = pos_data
