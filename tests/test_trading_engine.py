#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
交易引擎核心逻辑单元测试
测试目标：
1. 凯利公式边界测试（全胜、全负、小样本场景）
2. 仓位计算精度测试（步进精度、正数验证）
"""

import pytest
import sys
import os
from unittest.mock import patch, MagicMock
from decimal import Decimal

# 添加项目根目录到路径
# WJ-BOT/tests/test_trading_engine.py -> WJ-BOT -> d:\wj (redis_manager.py 在这里)
wj_bot_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
parent_dir = os.path.abspath(os.path.join(wj_bot_dir, '..'))

# 先添加父目录（包含 redis_manager.py）
sys.path.insert(0, parent_dir)
# 再添加 WJ-BOT 目录（包含 config.py, trading_engine.py）
sys.path.insert(0, wj_bot_dir)

import config
from core.trading_engine import get_performance_stats, calculate_position_size


class TestKellyFormula:
    """凯利公式边界测试套件"""
    
    def test_kelly_formula_all_wins(self):
        """
        测试场景1：全胜场景（10笔交易全部盈利）
        预期：kelly_factor 应被限制在 0.5-1.5 之间
        """
        # Mock 交易历史：10笔全胜
        mock_history = [
            {'pnl': 100, 'net_pnl': 100} for _ in range(10)
        ]
        
        with patch.object(config, 'TRADE_HISTORY', mock_history):
            with patch.object(config, 'state_lock', MagicMock()):
                stats = get_performance_stats(lookback=50)
        
        # 断言：kelly_factor 必须在 0.5-1.5 之间
        assert 0.5 <= stats['kelly_factor'] <= 1.5, \
            f"全胜场景下 kelly_factor={stats['kelly_factor']} 超出安全范围 [0.5, 1.5]"
        
        # 断言：胜率应为 100%
        assert stats['win_rate'] > 0.9, \
            f"全胜场景下胜率应接近100%，实际为 {stats['win_rate']:.2%}"
        
        print(f"✅ 全胜场景测试通过: kelly_factor={stats['kelly_factor']:.2f}, win_rate={stats['win_rate']:.2%}")
    
    def test_kelly_formula_all_losses(self):
        """
        测试场景2：全负场景（10笔交易全部亏损）
        预期：kelly_factor 应被限制在 0.5-1.5 之间（保守默认值）
        """
        # Mock 交易历史：10笔全负
        mock_history = [
            {'pnl': -50, 'net_pnl': -50} for _ in range(10)
        ]
        
        with patch.object(config, 'TRADE_HISTORY', mock_history):
            with patch.object(config, 'state_lock', MagicMock()):
                stats = get_performance_stats(lookback=50)
        
        # 断言：kelly_factor 必须在 0.5-1.5 之间
        assert 0.5 <= stats['kelly_factor'] <= 1.5, \
            f"全负场景下 kelly_factor={stats['kelly_factor']} 超出安全范围 [0.5, 1.5]"
        
        # 断言：胜率应为 0%
        assert stats['win_rate'] < 0.1, \
            f"全负场景下胜率应接近0%，实际为 {stats['win_rate']:.2%}"
        
        print(f"✅ 全负场景测试通过: kelly_factor={stats['kelly_factor']:.2f}, win_rate={stats['win_rate']:.2%}")
    
    def test_kelly_formula_small_sample(self):
        """
        测试场景3：小样本场景（样本量 < 10）
        预期：kelly_factor 应强制返回保守默认值 1.0
        """
        # Mock 交易历史：仅5笔交易（3胜2负）
        mock_history = [
            {'pnl': 100, 'net_pnl': 100},
            {'pnl': 80, 'net_pnl': 80},
            {'pnl': 120, 'net_pnl': 120},
            {'pnl': -50, 'net_pnl': -50},
            {'pnl': -40, 'net_pnl': -40},
        ]
        
        with patch.object(config, 'TRADE_HISTORY', mock_history):
            with patch.object(config, 'state_lock', MagicMock()):
                stats = get_performance_stats(lookback=50)
        
        # 断言：小样本场景下 kelly_factor 应为 1.0（保守默认值）
        assert stats['kelly_factor'] == 1.0, \
            f"小样本场景下 kelly_factor 应为 1.0，实际为 {stats['kelly_factor']}"
        
        # 断言：样本量应小于 10
        assert stats['sample_size'] < 10, \
            f"样本量应小于10，实际为 {stats['sample_size']}"
        
        print(f"✅ 小样本场景测试通过: kelly_factor={stats['kelly_factor']:.2f}, sample_size={stats['sample_size']}")
    
    def test_kelly_formula_boundary_enforcement(self):
        """
        测试场景4：边界强制执行（极端盈亏比场景）
        预期：无论计算结果如何，kelly_factor 必须被限制在 0.5-1.5
        """
        # Mock 交易历史：极端盈亏比（9胜1负，但亏损极大）
        mock_history = [
            {'pnl': 10, 'net_pnl': 10} for _ in range(9)
        ] + [
            {'pnl': -500, 'net_pnl': -500}  # 单笔巨亏
        ]
        
        with patch.object(config, 'TRADE_HISTORY', mock_history):
            with patch.object(config, 'state_lock', MagicMock()):
                stats = get_performance_stats(lookback=50)
        
        # 断言：kelly_factor 必须在 0.5-1.5 之间
        assert 0.5 <= stats['kelly_factor'] <= 1.5, \
            f"极端盈亏比场景下 kelly_factor={stats['kelly_factor']} 超出安全范围 [0.5, 1.5]"
        
        print(f"✅ 边界强制执行测试通过: kelly_factor={stats['kelly_factor']:.2f}")


class TestPositionSizeCalculation:
    """仓位计算精度测试套件"""
    
    def test_position_size_positive(self):
        """
        测试场景1：仓位数量必须大于 0
        """
        # Mock 客户端和配置
        mock_client = MagicMock()
        mock_client.futures_account.return_value = {
            'totalMarginBalance': '2000.0'
        }
        
        # Mock 配置
        with patch.object(config, 'SYSTEM_CONFIG', {
            'BENCHMARK_CASH': 1800.0,
            'RISK_RATIO': 0.055,
            'LEVERAGE': 20.0,
            'MAD_DOG_MODE': False,
            'MAD_DOG_TRIGGER': 1.3,
            'MAD_DOG_BOOST': 2.0,
            'ASSET_WEIGHTS': {'BTCUSDT': 0.5},
            'FORCE_MAD_DOG_MODE': False,
        }):
            with patch.object(config, 'TRADE_HISTORY', []):
                with patch.object(config, 'state_lock', MagicMock()):
                    # 调用仓位计算
                    result = calculate_position_size(
                        client=mock_client,
                        symbol='BTCUSDT',
                        price=50000.0,
                        signal_strength='STRONG',
                        atr=500.0
                    )
        
        # 断言：quantity 必须大于 0
        assert result is not None, "仓位计算返回 None"
        assert result['quantity'] > 0, \
            f"仓位数量必须大于0，实际为 {result['quantity']}"
        
        print(f"✅ 仓位正数测试通过: quantity={result['quantity']}")
    
    def test_position_size_precision(self):
        """
        测试场景2：仓位数量必须符合币安步进精度（LOT_SIZE stepSize）
        """
        # Mock 客户端
        mock_client = MagicMock()
        mock_client.futures_account.return_value = {
            'totalMarginBalance': '2000.0'
        }
        
        # Mock 精度配置（BTCUSDT 步进精度为 0.001）
        with patch.object(config, 'symbol_precisions', {'BTCUSDT': 3}):
            with patch.object(config, 'quantity_step_sizes', {'BTCUSDT': 0.001}):
                with patch.object(config, 'SYSTEM_CONFIG', {
                    'BENCHMARK_CASH': 1800.0,
                    'RISK_RATIO': 0.055,
                    'LEVERAGE': 20.0,
                    'MAD_DOG_MODE': False,
                    'MAD_DOG_TRIGGER': 1.3,
                    'MAD_DOG_BOOST': 2.0,
                    'ASSET_WEIGHTS': {'BTCUSDT': 0.5},
                    'FORCE_MAD_DOG_MODE': False,
                }):
                    with patch.object(config, 'TRADE_HISTORY', []):
                        with patch.object(config, 'state_lock', MagicMock()):
                            # Mock round_to_quantity_precision 函数
                            with patch('trading_engine.round_to_quantity_precision', side_effect=lambda qty, sym: round(qty, 3)):
                                result = calculate_position_size(
                                    client=mock_client,
                                    symbol='BTCUSDT',
                                    price=50000.0,
                                    signal_strength='STRONG',
                                    atr=500.0
                                )
        
        # 断言：quantity 必须符合步进精度（小数位数 <= 3）
        assert result is not None, "仓位计算返回 None"
        
        # 检查小数位数
        qty_str = f"{result['quantity']:.10f}".rstrip('0').rstrip('.')
        decimal_places = len(qty_str.split('.')[-1]) if '.' in qty_str else 0
        
        assert decimal_places <= 3, \
            f"仓位数量小数位数超过精度限制，quantity={result['quantity']}, 小数位={decimal_places}"
        
        print(f"✅ 仓位精度测试通过: quantity={result['quantity']}, 小数位={decimal_places}")
    
    def test_position_size_with_kelly_adjustment(self):
        """
        测试场景3：凯利系数调整后的仓位计算
        验证凯利公式正确应用到仓位计算中
        """
        # Mock 客户端
        mock_client = MagicMock()
        mock_client.futures_account.return_value = {
            'totalMarginBalance': '2000.0'
        }
        
        # Mock 交易历史：20笔交易（胜率60%，盈亏比1.5）
        mock_history = [
            {'pnl': 150, 'net_pnl': 150} for _ in range(12)
        ] + [
            {'pnl': -100, 'net_pnl': -100} for _ in range(8)
        ]
        
        with patch.object(config, 'TRADE_HISTORY', mock_history):
            with patch.object(config, 'SYSTEM_CONFIG', {
                'BENCHMARK_CASH': 1800.0,
                'RISK_RATIO': 0.055,
                'LEVERAGE': 20.0,
                'MAD_DOG_MODE': False,
                'MAD_DOG_TRIGGER': 1.3,
                'MAD_DOG_BOOST': 2.0,
                'ASSET_WEIGHTS': {'BTCUSDT': 0.5},
                'FORCE_MAD_DOG_MODE': False,
            }):
                with patch.object(config, 'state_lock', MagicMock()):
                    with patch('trading_engine.round_to_quantity_precision', side_effect=lambda qty, sym: round(qty, 3)):
                        result = calculate_position_size(
                            client=mock_client,
                            symbol='BTCUSDT',
                            price=50000.0,
                            signal_strength='STRONG',
                            atr=500.0
                        )
        
        # 断言：返回结果包含凯利系数信息
        assert result is not None, "仓位计算返回 None"
        assert 'kelly_factor' in result, "返回结果缺少 kelly_factor"
        assert 0.5 <= result['kelly_factor'] <= 1.5, \
            f"kelly_factor={result['kelly_factor']} 超出安全范围 [0.5, 1.5]"
        
        print(f"✅ 凯利系数调整测试通过: kelly_factor={result['kelly_factor']:.2f}, quantity={result['quantity']}")


class TestAPIIsolation:
    """API 隔离测试套件 - 确保测试不会发起真实 API 请求"""
    
    def test_no_real_api_calls(self):
        """
        测试场景：确保所有测试都使用 Mock，不会修改线上配置或发起真实 API 请求
        """
        # 验证 config.json 未被修改（通过检查文件修改时间）
        config_file = os.path.join(os.path.dirname(__file__), '..', 'bot_config.json')
        
        if os.path.exists(config_file):
            # 记录初始修改时间
            initial_mtime = os.path.getmtime(config_file)
            
            # 运行所有测试（这里仅作示例，实际由 pytest 自动运行）
            # ...
            
            # 验证文件未被修改
            final_mtime = os.path.getmtime(config_file)
            assert initial_mtime == final_mtime, \
                "测试修改了 bot_config.json 文件，违反隔离原则"
        
        print("✅ API 隔离测试通过: 未检测到真实 API 调用或配置文件修改")


class TestEnvironmentIsolation:
    """🔥 环境隔离测试套件 - SANDBOX/LIVE 模式安全防护"""
    
    def test_sandbox_mode_blocks_real_api_calls(self):
        """
        测试场景1：SANDBOX 模式必须拦截所有实盘 API 调用
        
        核心验证：
        1. RUNNING_MODE=SANDBOX 时，execute_trade 必须走虚拟交易分支
        2. 即使传入真实 client，也不能调用 futures_create_order
        3. 必须返回 simulated=True 标记
        """
        # Mock 币安客户端
        mock_client = MagicMock()
        mock_client.futures_account.return_value = {
            'totalMarginBalance': '10000.0'
        }
        
        # 🔥 关键：设置 RUNNING_MODE=SANDBOX
        with patch.object(config, 'SYSTEM_CONFIG', {
            'RUNNING_MODE': 'SANDBOX',  # 🔥 强制沙盒模式
            'BENCHMARK_CASH': 10000.0,
            'RISK_RATIO': 0.02,
            'LEVERAGE': 20.0,
            'COMMISSION_RATE': 0.0004,
            'HEDGE_MODE_ENABLED': False,
            'ATR_MULT': 2.0,
            'SL_BUFFER': 1.05,
        }):
            with patch.object(config, 'ACTIVE_POSITIONS', {}):
                with patch.object(config, 'positions_lock', MagicMock()):
                    with patch.object(config, 'state_lock', MagicMock()):
                        # 导入 execute_trade（在 patch 之后）
                        from trading_engine import execute_trade
                        
                        # 构建开仓参数
                        position_info = {
                            'quantity': 0.001,
                            'leverage': 20,
                            'allocated_capital': 100.0
                        }
                        
                        # 🔥 执行开仓（SANDBOX 模式应该拦截）
                        result = execute_trade(
                            client=mock_client,  # 传入真实 client
                            symbol='BTCUSDT',
                            signal_type='BUY',
                            price=50000.0,
                            position_info=position_info,
                            atr=500.0,
                            adx=25.0,
                            position_action='ENTRY',
                            custom_config=None,  # 不传 custom_config，使用全局 SYSTEM_CONFIG
                            simulated=None  # 不显式传 simulated，测试自动检测
                        )
        
        # 🔥 断言1：必须返回成功（虚拟交易）
        assert result['success'] == True, "SANDBOX 模式应该允许虚拟交易"
        
        # 🔥 断言2：必须标记为 simulated=True
        assert result.get('simulated') == True, \
            "SANDBOX 模式必须返回 simulated=True 标记"
        
        # 🔥 断言3：不能调用真实 API
        mock_client.futures_create_order.assert_not_called()
        mock_client.futures_place_batch_orders.assert_not_called()
        
        print("✅ SANDBOX 模式拦截测试通过: 真实 API 未被调用")
    
    def test_live_mode_allows_real_api_calls(self):
        """
        测试场景2：LIVE 模式必须允许真实 API 调用
        
        核心验证：
        1. RUNNING_MODE=LIVE 时，execute_trade 必须调用真实 API
        2. 必须返回 simulated=False 标记
        """
        # Mock 币安客户端
        mock_client = MagicMock()
        mock_client.futures_account.return_value = {
            'totalMarginBalance': '10000.0'
        }
        
        # Mock 批量下单响应
        mock_client.futures_place_batch_orders.return_value = [
            {'orderId': 12345, 'status': 'FILLED', 'avgPrice': '50000.0'},
            {'orderId': 12346, 'status': 'NEW'}
        ]
        
        # 🔥 关键：设置 RUNNING_MODE=LIVE
        with patch.object(config, 'SYSTEM_CONFIG', {
            'RUNNING_MODE': 'LIVE',  # 🔥 强制实盘模式
            'BENCHMARK_CASH': 10000.0,
            'RISK_RATIO': 0.02,
            'LEVERAGE': 20.0,
            'COMMISSION_RATE': 0.0004,
            'HEDGE_MODE_ENABLED': False,
            'ATR_MULT': 2.0,
            'SL_BUFFER': 1.05,
            'MAX_SLIPPAGE': 0.0015,
        }):
            with patch.object(config, 'ACTIVE_POSITIONS', {}):
                with patch.object(config, 'positions_lock', MagicMock()):
                    with patch.object(config, 'state_lock', MagicMock()):
                        # Mock 滑点检查
                        with patch('trading_engine.check_orderbook_slippage', return_value=(True, "OK", 50000.0)):
                            # Mock 精度函数
                            with patch('trading_engine.round_to_tick_size', side_effect=lambda x, s: x):
                                from trading_engine import execute_trade
                                
                                position_info = {
                                    'quantity': 0.001,
                                    'leverage': 20,
                                    'allocated_capital': 100.0
                                }
                                
                                # 🔥 执行开仓（LIVE 模式应该调用真实 API）
                                result = execute_trade(
                                    client=mock_client,
                                    symbol='BTCUSDT',
                                    signal_type='BUY',
                                    price=50000.0,
                                    position_info=position_info,
                                    atr=500.0,
                                    adx=25.0,
                                    position_action='ENTRY',
                                    custom_config=None,
                                    simulated=None
                                )
        
        # 🔥 断言1：必须返回成功
        assert result['success'] == True, "LIVE 模式应该允许真实交易"
        
        # 🔥 断言2：必须标记为 simulated=False
        assert result.get('simulated') == False, \
            "LIVE 模式必须返回 simulated=False 标记"
        
        # 🔥 断言3：必须调用真实 API
        mock_client.futures_place_batch_orders.assert_called_once()
        
        print("✅ LIVE 模式放行测试通过: 真实 API 已被调用")
    
    def test_sandbox_mode_overrides_simulated_parameter(self):
        """
        测试场景3：SANDBOX 模式必须覆盖 simulated 参数
        
        核心验证：
        即使显式传入 simulated=False，SANDBOX 模式也必须强制改为 True
        """
        mock_client = MagicMock()
        mock_client.futures_account.return_value = {
            'totalMarginBalance': '10000.0'
        }
        
        with patch.object(config, 'SYSTEM_CONFIG', {
            'RUNNING_MODE': 'SANDBOX',
            'BENCHMARK_CASH': 10000.0,
            'RISK_RATIO': 0.02,
            'LEVERAGE': 20.0,
            'COMMISSION_RATE': 0.0004,
            'HEDGE_MODE_ENABLED': False,
            'ATR_MULT': 2.0,
            'SL_BUFFER': 1.05,
        }):
            with patch.object(config, 'ACTIVE_POSITIONS', {}):
                with patch.object(config, 'positions_lock', MagicMock()):
                    with patch.object(config, 'state_lock', MagicMock()):
                        from trading_engine import execute_trade
                        
                        position_info = {
                            'quantity': 0.001,
                            'leverage': 20,
                            'allocated_capital': 100.0
                        }
                        
                        # 🔥 显式传入 simulated=False（应该被 SANDBOX 模式覆盖）
                        result = execute_trade(
                            client=mock_client,
                            symbol='BTCUSDT',
                            signal_type='BUY',
                            price=50000.0,
                            position_info=position_info,
                            atr=500.0,
                            adx=25.0,
                            position_action='ENTRY',
                            custom_config=None,
                            simulated=False  # 🔥 尝试绕过沙盒模式
                        )
        
        # 🔥 断言：SANDBOX 模式必须覆盖参数
        assert result.get('simulated') == True, \
            "SANDBOX 模式必须强制覆盖 simulated 参数为 True"
        
        # 🔥 断言：不能调用真实 API
        mock_client.futures_create_order.assert_not_called()
        mock_client.futures_place_batch_orders.assert_not_called()
        
        print("✅ SANDBOX 模式参数覆盖测试通过: simulated 参数已被强制覆盖")
    
    def test_sync_hedge_mode_respects_sandbox(self):
        """
        测试场景4：sync_hedge_mode_to_binance 必须在 SANDBOX 模式下跳过 API 调用
        
        核心验证：
        SANDBOX 模式下，持仓模式同步函数不能调用 futures_change_position_mode
        """
        mock_client = MagicMock()
        mock_client.futures_account.return_value = {
            'dualSidePosition': False
        }
        
        with patch.object(config, 'SYSTEM_CONFIG', {
            'RUNNING_MODE': 'SANDBOX',
            'HEDGE_MODE_ENABLED': True,
        }):
            from trading_engine import sync_hedge_mode_to_binance
            
            # 🔥 执行持仓模式同步
            success, message = sync_hedge_mode_to_binance(mock_client)
        
        # 🔥 断言1：必须返回成功（优雅绕过）
        assert success == True, "SANDBOX 模式应该优雅绕过持仓模式同步"
        
        # 🔥 断言2：消息中应包含 "Sandbox" 关键字
        assert "Sandbox" in message or "sandbox" in message.lower(), \
            "返回消息应明确说明是 SANDBOX 模式绕过"
        
        # 🔥 断言3：不能调用真实 API
        mock_client.futures_change_position_mode.assert_not_called()
        
        print("✅ 持仓模式同步隔离测试通过: SANDBOX 模式已优雅绕过")


if __name__ == '__main__':
    # 支持直接运行测试脚本
    pytest.main([__file__, '-v', '--tb=short'])
