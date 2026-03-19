#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Redis 状态引擎 - redis_manager.py
彻底替代 JSON 磁盘 I/O，实现亚毫秒级状态同步
"""

import json
import redis
from datetime import datetime
from logger_setup import logger

class DateTimeEncoder(json.JSONEncoder):
    """处理 datetime 序列化"""
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)

class RedisManager:
    def __init__(self, host='127.0.0.1', port=6379, db=0, password=None):
        self.enabled = False
        self.r = None
        self.pool = None
        
        try:
            # 使用连接池提升高并发性能
            self.pool = redis.ConnectionPool(
                host=host, 
                port=port, 
                db=db, 
                password=password, 
                decode_responses=True, # 自动解码为字符串
                socket_connect_timeout=2,  # 2秒超时
                socket_timeout=2
            )
            self.r = redis.Redis(connection_pool=self.pool)
            self.r.ping() # 测试连通性
            self.enabled = True
            logger.info("✅ Redis 高速内存引擎连接成功")
        except Exception as e:
            logger.warning(f"⚠️ Redis 未运行，将使用降级模式（仅内存缓存）: {e}")
            self.enabled = False
            self.r = None
            self.pool = None

    def get_key_prefix(self, mode="SANDBOX"):
        """获取环境隔离的前缀"""
        return f"wjbot:{mode.lower()}"

    # ==========================================
    # Hash 操作：用于 SYSTEM_CONFIG, ACTIVE_POSITIONS, SESSIONS
    # ==========================================
    def save_hash(self, name, data_dict):
        """将 Python 字典存入 Redis Hash (O(N) 但在内存中极快)"""
        if not self.enabled or not data_dict:
            return
        try:
            # 将嵌套字典/列表序列化为 JSON 字符串
            mapping = {str(k): json.dumps(v, cls=DateTimeEncoder, ensure_ascii=False) for k, v in data_dict.items()}
            # 使用 pipeline 保证原子性
            pipe = self.r.pipeline()
            pipe.delete(name) # 清空旧数据
            pipe.hset(name, mapping=mapping)
            pipe.execute()
        except Exception as e:
            logger.debug(f"Redis save_hash 失败 (降级模式): {e}")

    def load_hash(self, name):
        """从 Redis Hash 恢复为 Python 字典"""
        if not self.enabled:
            return {}
        try:
            raw_data = self.r.hgetall(name)
            result = {}
            for k, v_str in raw_data.items():
                try:
                    result[k] = json.loads(v_str)
                except:
                    result[k] = v_str
            return result
        except Exception as e:
            logger.debug(f"Redis load_hash 失败 (降级模式): {e}")
            return {}

    # ==========================================
    # List 操作：用于 TRADE_HISTORY 和 DLQ (极致性能优化)
    # ==========================================
    def append_to_list(self, name, item, max_length=1000):
        """
        🔥 极速历史记录：O(1) 复杂度的右侧追加，并自动截断。
        彻底废弃了以往"读取 1000 条 -> Python 追加 -> 保存 1001 条"的灾难性 I/O。
        """
        if not self.enabled:
            return
        try:
            item_str = json.dumps(item, cls=DateTimeEncoder, ensure_ascii=False)
            pipe = self.r.pipeline()
            pipe.rpush(name, item_str)
            # 只保留最新的 max_length 条记录 (类似滑动窗口)
            pipe.ltrim(name, -max_length, -1)
            pipe.execute()
        except Exception as e:
            logger.debug(f"Redis append_to_list 失败 (降级模式): {e}")

    def load_list(self, name):
        """加载整个列表"""
        if not self.enabled:
            return []
        try:
            raw_list = self.r.lrange(name, 0, -1)
            return [json.loads(item) for item in raw_list]
        except Exception as e:
            logger.debug(f"Redis load_list 失败 (降级模式): {e}")
            return []

    def save_full_list(self, name, data_list, max_length=1000):
        """全量覆写 List (用于启动时或批量对账后)"""
        if not self.enabled:
            return
        try:
            pipe = self.r.pipeline()
            pipe.delete(name)
            if data_list:
                # 序列化
                str_list = [json.dumps(i, cls=DateTimeEncoder, ensure_ascii=False) for i in data_list[-max_length:]]
                pipe.rpush(name, *str_list)
            pipe.execute()
        except Exception as e:
            logger.debug(f"Redis save_full_list 失败 (降级模式): {e}")

    # ==========================================
    # 高层业务逻辑：数据加载和保存
    # ==========================================
    def load_all_data(self, SYSTEM_CONFIG, ACTIVE_POSITIONS, TRADE_HISTORY, 
                      USER_SESSION_STATE, DEAD_LETTER_QUEUE):
        """
        🔥 从 Redis 极速加载所有持久化数据
        """
        # 1. 加载主配置
        config_key = "wjbot:global:config"
        saved_config = self.load_hash(config_key)
        if saved_config:
            for k, v in saved_config.items():
                if k not in ["API_KEY", "API_SECRET", "TG_TOKEN", "TG_CHAT_ID", "USE_PROXY_HARD_SWITCH"]:
                    SYSTEM_CONFIG[k] = v
            logger.info("✅ 成功从 Redis 加载持久化配置")
        else:
            logger.warning("⚠️ Redis 中无配置数据，使用初始默认配置")

        running_mode = SYSTEM_CONFIG.get("RUNNING_MODE", "SANDBOX")
        prefix = self.get_key_prefix(running_mode)

        # 2. 加载持仓 (Hash)
        positions_key = f"{prefix}:positions"
        ACTIVE_POSITIONS.clear()
        positions_data = self.load_hash(positions_key)
        ACTIVE_POSITIONS.update(positions_data)
        # 恢复 datetime 对象
        self._parse_positions_datetime(ACTIVE_POSITIONS)
        logger.info(f"✅ 成功从 Redis 加载 {len(ACTIVE_POSITIONS)} 个持仓 [{running_mode}]")

        # 3. 加载交易历史 (List)
        history_key = f"{prefix}:history"
        TRADE_HISTORY.clear()
        TRADE_HISTORY.extend(self.load_list(history_key))
        logger.info(f"✅ 成功从 Redis 加载 {len(TRADE_HISTORY)} 条交易历史 [{running_mode}]")

        # 4. 加载死信队列 (List)
        DEAD_LETTER_QUEUE.clear()
        DEAD_LETTER_QUEUE.extend(self.load_list("wjbot:global:dlq"))
        
        # 5. 加载用户会话 (Hash)
        USER_SESSION_STATE.clear()
        sessions_data = self.load_hash("wjbot:global:sessions")
        USER_SESSION_STATE.update({int(k): v for k, v in sessions_data.items()})

    def save_all_data(self, SYSTEM_CONFIG, ACTIVE_POSITIONS, running_mode=None):
        """
        🔥 原子性保存配置和持仓到 Redis（亚毫秒级）
        注意：TRADE_HISTORY 使用 append_to_list 增量追加，不在此处理
        """
        try:
            # 1. 提取配置快照（排除敏感信息）
            config_snapshot = {k: v for k, v in SYSTEM_CONFIG.items() 
                             if k not in ["API_KEY", "API_SECRET", "TG_TOKEN", "TG_CHAT_ID"]}
            
            # 2. 确定运行模式
            if running_mode is None:
                running_mode = SYSTEM_CONFIG.get("RUNNING_MODE", "SANDBOX")
            prefix = self.get_key_prefix(running_mode)

            # 3. 写入 Redis
            self.save_hash("wjbot:global:config", config_snapshot)
            self.save_hash(f"{prefix}:positions", ACTIVE_POSITIONS)
            
            logger.debug(f"✅ 数据已同步至 Redis ({running_mode})")
        except Exception as e:
            logger.error(f"❌ 同步 Redis 失败: {e}")

    def save_dlq(self, DEAD_LETTER_QUEUE):
        """保存死信队列到 Redis"""
        self.save_full_list("wjbot:global:dlq", DEAD_LETTER_QUEUE)

    def save_session_state(self, USER_SESSION_STATE):
        """保存用户会话状态到 Redis"""
        sessions_to_save = {str(k): v for k, v in USER_SESSION_STATE.items()}
        self.save_hash("wjbot:global:sessions", sessions_to_save)

    def _parse_positions_datetime(self, positions):
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

# 全局单例
redis_db = RedisManager()
