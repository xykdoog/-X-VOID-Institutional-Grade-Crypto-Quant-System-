#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI Emergency Control - P2修复
- 增强#1: 模型降级机制
- 增强#3: 全局紧急停止
"""

import json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# ==========================================
# 增强#3: 全局紧急停止
# ==========================================

EMERGENCY_STOP = {
    'enabled': False,
    'reason': '',
    'triggered_at': None,
    'triggered_by': ''
}


def trigger_emergency_stop(reason, triggered_by='system'):
    """触发全局紧急停止"""
    EMERGENCY_STOP['enabled'] = True
    EMERGENCY_STOP['reason'] = reason
    EMERGENCY_STOP['triggered_at'] = datetime.now()
    EMERGENCY_STOP['triggered_by'] = triggered_by
    
    logger.critical(f"🚨 紧急停止触发: {reason}")
    
    return {
        'success': True,
        'message': f"紧急停止已触发: {reason}",
        'triggered_at': EMERGENCY_STOP['triggered_at'],
        'triggered_by': triggered_by
    }


def check_emergency_stop():
    """检查紧急停止状态"""
    if EMERGENCY_STOP['enabled']:
        return True, EMERGENCY_STOP['reason']
    return False, None


def release_emergency_stop(operator):
    """解除紧急停止（需人工确认）"""
    if not EMERGENCY_STOP['enabled']:
        return {'success': False, 'message': '系统未处于紧急停止状态'}
    
    EMERGENCY_STOP['enabled'] = False
    logger.info(f"✅ 紧急停止已解除，操作者: {operator}")
    
    return {
        'success': True,
        'message': f"紧急停止已解除，操作者: {operator}",
        'released_at': datetime.now()
    }


def get_emergency_status():
    """获取紧急停止状态"""
    return EMERGENCY_STOP.copy()


# ==========================================
# 增强#1: 模型降级机制
# ==========================================

class AIModelFallback:
    """AI模型降级管理器"""
    
    def __init__(self, claude_commander=None, gemini_commander=None):
        self.claude = claude_commander
        self.gemini = gemini_commander
        self.fallback_count = 0
        self.last_fallback_time = None
    
    def ask_with_fallback(self, prompt, primary='claude', fallback='gemini'):
        """
        带降级的AI调用
        
        Args:
            prompt: 提示词
            primary: 主模型 ('claude' or 'gemini')
            fallback: 降级模型 ('claude' or 'gemini')
        
        Returns:
            dict: {
                'response': str,
                'model_used': str,
                'fallback_triggered': bool
            }
        """
        # 检查紧急停止
        is_stopped, reason = check_emergency_stop()
        if is_stopped:
            logger.warning(f"⚠️ 系统处于紧急停止状态: {reason}")
            return self._get_emergency_response()
        
        # 尝试主模型
        try:
            if primary == 'claude' and self.claude:
                response = self.claude.ask_commander(prompt)
                logger.info(f"✅ Claude调用成功")
                return {
                    'response': response,
                    'model_used': 'claude',
                    'fallback_triggered': False
                }
            elif primary == 'gemini' and self.gemini:
                response = self.gemini.ask_commander(prompt)
                logger.info(f"✅ Gemini调用成功")
                return {
                    'response': response,
                    'model_used': 'gemini',
                    'fallback_triggered': False
                }
            else:
                raise Exception(f"主模型{primary}不可用")
        
        except Exception as e:
            logger.warning(f"⚠️ {primary}调用失败: {e}，降级到{fallback}")
            self.fallback_count += 1
            self.last_fallback_time = datetime.now()
            
            # 尝试降级模型
            try:
                if fallback == 'claude' and self.claude:
                    response = self.claude.ask_commander(prompt)
                    logger.info(f"✅ 降级到Claude成功")
                    return {
                        'response': response,
                        'model_used': 'claude',
                        'fallback_triggered': True
                    }
                elif fallback == 'gemini' and self.gemini:
                    response = self.gemini.ask_commander(prompt)
                    logger.info(f"✅ 降级到Gemini成功")
                    return {
                        'response': response,
                        'model_used': 'gemini',
                        'fallback_triggered': True
                    }
                else:
                    raise Exception(f"降级模型{fallback}不可用")
            
            except Exception as e2:
                logger.error(f"❌ {fallback}也失败: {e2}，返回降级响应")
                return self._get_fallback_response()
    
    def _get_fallback_response(self):
        """获取降级响应（所有AI模型都失败时）"""
        return {
            'response': json.dumps({
                'recommendation': 'HOLD',
                'confidence': 0.0,
                'error': 'all_models_failed',
                'message': '所有AI模型调用失败，建议保持观望'
            }),
            'model_used': 'fallback',
            'fallback_triggered': True
        }
    
    def _get_emergency_response(self):
        """获取紧急停止响应"""
        return {
            'response': json.dumps({
                'recommendation': 'EMERGENCY_STOP',
                'confidence': 0.0,
                'error': 'emergency_stop',
                'message': '系统处于紧急停止状态'
            }),
            'model_used': 'emergency',
            'fallback_triggered': False
        }
    
    def get_fallback_stats(self):
        """获取降级统计"""
        return {
            'fallback_count': self.fallback_count,
            'last_fallback_time': self.last_fallback_time
        }


# ==========================================
# 模块测试
# ==========================================

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    
    print("=" * 60)
    print("🧪 AI Emergency Control Test")
    print("=" * 60)
    
    # 测试1: 紧急停止机制
    print("\n[测试1] 紧急停止机制")
    result = trigger_emergency_stop("检测到异常交易行为", "admin")
    print(f"触发结果: {result}")
    
    is_stopped, reason = check_emergency_stop()
    print(f"停止状态: {is_stopped}, 原因: {reason}")
    
    status = get_emergency_status()
    print(f"完整状态: {status}")
    
    release_result = release_emergency_stop("admin")
    print(f"解除结果: {release_result}")
    
    # 测试2: 模型降级机制
    print("\n[测试2] 模型降级机制")
    
    class MockCommander:
        def __init__(self, name, should_fail=False):
            self.name = name
            self.should_fail = should_fail
        
        def ask_commander(self, prompt):
            if self.should_fail:
                raise Exception(f"{self.name} API调用失败")
            return f"{self.name}的响应: 市场分析完成"
    
    # 场景1: 主模型成功
    claude = MockCommander("Claude", should_fail=False)
    gemini = MockCommander("Gemini", should_fail=False)
    fallback_mgr = AIModelFallback(claude, gemini)
    
    result = fallback_mgr.ask_with_fallback("分析市场", primary='claude')
    print(f"场景1 - 主模型成功: {result}")
    
    # 场景2: 主模型失败，降级成功
    claude_fail = MockCommander("Claude", should_fail=True)
    fallback_mgr2 = AIModelFallback(claude_fail, gemini)
    
    result = fallback_mgr2.ask_with_fallback("分析市场", primary='claude', fallback='gemini')
    print(f"场景2 - 降级成功: {result}")
    
    # 场景3: 所有模型失败
    gemini_fail = MockCommander("Gemini", should_fail=True)
    fallback_mgr3 = AIModelFallback(claude_fail, gemini_fail)
    
    result = fallback_mgr3.ask_with_fallback("分析市场", primary='claude', fallback='gemini')
    print(f"场景3 - 所有失败: {result}")
    
    print("\n✅ 所有测试完成!")
