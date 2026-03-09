#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Human Override Manager - 人工干预锁系统
确保 AI 指令受人类最终审批约束
"""

import json
import time
import threading
from datetime import datetime
from typing import Dict, Any, Tuple, Optional

# 线程锁
override_lock = threading.RLock()

# 人工锁定参数注册表
HUMAN_LOCKED_PARAMS = {}

# 待确认指令队列
PENDING_CONFIRMATIONS = {}


class HumanOverrideManager:
    """人工干预管理器"""
    
    def __init__(self):
        self.locked_params = HUMAN_LOCKED_PARAMS
        self.pending = PENDING_CONFIRMATIONS
        self._load_state()
    
    def _load_state(self):
        """加载持久化状态"""
        try:
            with open('human_override_state.json', 'r', encoding='utf-8') as f:
                data = json.load(f)
                self.locked_params.update(data.get('locked_params', {}))
        except:
            pass
    
    def _save_state(self):
        """保存持久化状态"""
        try:
            with open('human_override_state.json', 'w', encoding='utf-8') as f:
                json.dump({
                    'locked_params': self.locked_params,
                    'timestamp': datetime.now().isoformat()
                }, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"⚠️ 保存人工锁状态失败: {e}")
    
    def lock_parameter(self, param_name: str, current_value: Any, reason: str = "人工设定") -> bool:
        """
        锁定参数，禁止 AI 修改
        
        Args:
            param_name: 参数名
            current_value: 当前值
            reason: 锁定原因
        
        Returns:
            bool: 是否成功锁定
        """
        with override_lock:
            self.locked_params[param_name] = {
                'value': current_value,
                'locked_at': datetime.now().isoformat(),
                'reason': reason,
                'locked_by': 'human'
            }
            self._save_state()
            print(f"🔒 参数已锁定: {param_name} = {current_value} ({reason})")
            return True
    
    def unlock_parameter(self, param_name: str) -> bool:
        """
        解锁参数，允许 AI 修改
        
        Args:
            param_name: 参数名
        
        Returns:
            bool: 是否成功解锁
        """
        with override_lock:
            if param_name in self.locked_params:
                del self.locked_params[param_name]
                self._save_state()
                print(f"🔓 参数已解锁: {param_name}")
                return True
            return False
    
    def is_locked(self, param_name: str) -> bool:
        """检查参数是否被锁定"""
        return param_name in self.locked_params
    
    def check_permission(self, param_name: str, new_value: Any, source: str = 'ai') -> Tuple[bool, str]:
        """
        检查修改权限
        
        Args:
            param_name: 参数名
            new_value: 新值
            source: 来源 ('ai' 或 'human')
        
        Returns:
            (allowed: bool, reason: str)
        """
        with override_lock:
            # 人类操作始终允许
            if source == 'human':
                return True, "人类操作，无需审批"
            
            # AI 操作检查锁定状态
            if param_name in self.locked_params:
                lock_info = self.locked_params[param_name]
                return False, f"参数已被人工锁定 (原因: {lock_info['reason']})"
            
            return True, "允许修改"
    
    def request_confirmation(self, command_id: str, command_data: Dict[str, Any], 
                           requester: str = 'ai') -> str:
        """
        请求人工确认
        
        Args:
            command_id: 指令唯一ID
            command_data: 指令数据
            requester: 请求者
        
        Returns:
            str: 确认令牌
        """
        with override_lock:
            token = f"CONF_{int(time.time())}_{command_id}"
            self.pending[token] = {
                'command_id': command_id,
                'command_data': command_data,
                'requester': requester,
                'requested_at': datetime.now().isoformat(),
                'status': 'pending'
            }
            return token
    
    def confirm_command(self, token: str) -> Tuple[bool, Optional[Dict]]:
        """
        确认执行指令
        
        Args:
            token: 确认令牌
        
        Returns:
            (success: bool, command_data: dict)
        """
        with override_lock:
            if token not in self.pending:
                return False, None
            
            cmd = self.pending[token]
            cmd['status'] = 'confirmed'
            cmd['confirmed_at'] = datetime.now().isoformat()
            
            command_data = cmd['command_data']
            del self.pending[token]
            
            return True, command_data
    
    def reject_command(self, token: str) -> bool:
        """
        拒绝执行指令
        
        Args:
            token: 确认令牌
        
        Returns:
            bool: 是否成功拒绝
        """
        with override_lock:
            if token in self.pending:
                del self.pending[token]
                return True
            return False
    
    def get_pending_confirmations(self) -> Dict[str, Dict]:
        """获取所有待确认指令"""
        with override_lock:
            return dict(self.pending)
    
    def get_locked_params(self) -> Dict[str, Dict]:
        """获取所有锁定参数"""
        with override_lock:
            return dict(self.locked_params)


# 全局单例
_manager_instance = None

def get_override_manager() -> HumanOverrideManager:
    """获取人工干预管理器单例"""
    global _manager_instance
    if _manager_instance is None:
        _manager_instance = HumanOverrideManager()
    return _manager_instance


if __name__ == "__main__":
    # 测试代码
    mgr = get_override_manager()
    
    # 测试锁定参数
    mgr.lock_parameter("LEVERAGE", 20, "风控要求")
    
    # 测试权限检查
    allowed, reason = mgr.check_permission("LEVERAGE", 50, source='ai')
    print(f"AI修改杠杆: {allowed}, 原因: {reason}")
    
    allowed, reason = mgr.check_permission("LEVERAGE", 50, source='human')
    print(f"人类修改杠杆: {allowed}, 原因: {reason}")
    
    # 测试确认流程
    token = mgr.request_confirmation("CMD001", {"action": "adjust_leverage", "value": 50})
    print(f"确认令牌: {token}")
    
    success, data = mgr.confirm_command(token)
    print(f"确认结果: {success}, 数据: {data}")
