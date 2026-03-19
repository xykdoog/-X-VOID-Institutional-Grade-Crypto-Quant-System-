#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# -*- coding: utf-8 -*-
"""
X-VOID Omega: Institutional-Grade Crypto Quant System
Copyright (C) 2026 xykdoog (nq12841155@gmail.com)

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""


"""
交易引擎模块 - trading_engine.py
负责交易信号生成、订单执行、仓位管理
"""

# Standard library imports
import argparse
import builtins
import csv
import functools
import json
import math
import multiprocessing as mp
import os
import sys
import threading
import time
from datetime import datetime

# Third-party imports
import numpy as np
import pandas as pd
import pandas_ta as ta
from binance.enums import *

# Local imports
from logger_setup import logger

import config
from config import (
    positions_lock,
    csv_lock, config_lock, state_lock, get_binance_interval, save_data
)

from utils import (
    get_current_price, round_to_tick_size, round_to_quantity_precision,
    send_tg_msg, send_tg_alert, execute_vault_transfer
)

from risk_manager import get_risk_manager

# 🔥 导入 SMC 开火信号模板
try:
    from smc_signal_template import build_smc_signal_from_trade_context
    SMC_TEMPLATE_ENABLED = True
except ImportError:
    SMC_TEMPLATE_ENABLED = False

# 🔥 导入灾后重建乘数（熔断恢复后放大被高ATR压缩的仓位）
try:
    from enhanced_black_swan import get_post_disaster_recovery_multiplier
    POST_DISASTER_RECOVERY_ENABLED = True
except ImportError:
    POST_DISASTER_RECOVERY_ENABLED = False

# 🔥 导入执行质量监控模块
try:
    from execution_quality_monitor import get_eqm
    EQM_ENABLED = True
except ImportError:
    EQM_ENABLED = False

# 🔥 导入自适应追单模块（IOC + Chasing）
try:
    from execution_algo import execute_ioc_then_chase_entry
    IOC_CHASE_ENABLED = True
    print("✅ 自适应追单模块(IOC+Chasing)已加载")
except ImportError as e:
    print(f"⚠️ 自适应追单模块未找到: {e}")
    IOC_CHASE_ENABLED = False

# 🔥 导入权重监控模块
try:
    from api_weight_monitor import get_weight_status
    API_WEIGHT_MONITOR_ENABLED = True
    print("✅ API权重监控模块已加载")
except ImportError as e:
    print(f"⚠️ API权重监控模块未找到: {e}")
    API_WEIGHT_MONITOR_ENABLED = False

# 🔥 导入仓位隔离模块
try:
    from position_isolation import (
        generate_bot_order_id,
        is_bot_order,
        validate_close_permission,
        sync_positions_with_isolation,
        emergency_close_all_bot_positions
    )
    POSITION_ISOLATION_ENABLED = True
    print("✅ 仓位隔离模块已加载")
except ImportError as e:
    print(f"⚠️ 仓位隔离模块未找到，使用传统模式: {e}")
    POSITION_ISOLATION_ENABLED = False

# ==========================================
# 🔥 全局常量（消除魔法数字）
# ==========================================

# --- API 权重防限流阈值 ---
API_WEIGHT_CIRCUIT_BREAKER_RATIO = 0.88   # >88% 紧急熔断，拦截请求
API_WEIGHT_THROTTLE_RATIO = 0.70          # >70% 强制减速，sleep 冷却
API_WEIGHT_THROTTLE_SLEEP_SECS = 5        # 减速冷却时长（秒）

# --- 盘口滑点阈值 ---
SLIPPAGE_HIGH_LIQUIDITY = 0.0010          # 主流币（BTC/ETH/SOL）最大滑点 0.10%
SLIPPAGE_LOW_LIQUIDITY = 0.0015           # 山寨币最大滑点 0.15%
HIGH_LIQUIDITY_SYMBOLS = ('BTCUSDT', 'ETHUSDT', 'SOLUSDT')

# --- 幽灵流动性探测器 (Spread Z-Score) ---
SPREAD_ZSCORE_THRESHOLD = 3.0             # Z-Score > 3.0 判定为流动性闪崩
SPREAD_HISTORY_WINDOW = 3600              # 历史价差窗口（秒）= 60 分钟

# --- 黑天鹅防御阈值 ---
BLACK_SWAN_GAP_THRESHOLD = 0.04           # 跳空异动阈值 4%
BLACK_SWAN_AMPLITUDE_THRESHOLD = 0.06     # 极端振幅阈值 6%
BLACK_SWAN_VOLUME_MULT_STANDARD = 4.0     # 天量异动倍数（标准模式）
BLACK_SWAN_VOLUME_MULT_SCALPER = 10.0     # 天量异动倍数（SCALPER 模式）

# --- OBI 冰山拦截 ---
OBI_ICEBERG_THRESHOLD = 0.45             # 盘口失衡拦截阈值

# --- 资金费率预警 ---
FUNDING_RATE_EXTREME_HIGH = 0.001         # 资金费率过高（散户疯狂做多）
FUNDING_RATE_EXTREME_LOW = -0.001         # 资金费率极负（散户疯狂做空）

# --- 兜底止损 ---
FALLBACK_SL_LONG_MULT = 0.98             # 做多兜底止损 = 价格 × 0.98（-2%）
FALLBACK_SL_SHORT_MULT = 1.02            # 做空兜底止损 = 价格 × 1.02（+2%）

# --- ADX 动态止损缩放 ---
ADX_STRONG_TREND_THRESHOLD = 30           # 强趋势 ADX 阈值
ADX_WEAK_TREND_THRESHOLD = 20             # 弱趋势 ADX 阈值
ADX_SCALAR_STRONG = 1.15                  # 强趋势止损放宽系数
ADX_SCALAR_WEAK = 0.75                    # 弱趋势止损收紧系数

# --- 凯利公式 ---
KELLY_BASELINE_WIN_RATE = 0.45            # 历史固定基准胜率
KELLY_BASELINE_PL_RATIO = 1.8            # 历史固定基准盈亏比
KELLY_SMOOTH_WEIGHT_CURRENT = 0.6         # 当前统计权重
KELLY_SMOOTH_WEIGHT_BASELINE = 0.4        # 基准权重
KELLY_MIN_FACTOR = 0.3                    # 凯利系数下限
KELLY_MAX_FACTOR = 1.2                    # 凯利系数上限
KELLY_MIN_SAMPLE_SIZE = 30                # 最小样本量

# --- 单笔风险硬上限 ---
MAX_SINGLE_RISK_RATIO = 0.02             # 单笔风险不超过 BENCHMARK_CASH 的 2%

# --- 自动保本巡逻 ---
BREAKEVEN_ATR_TRIGGER = 1.2              # ATR 保本触发倍数
BREAKEVEN_FIXED_PROFIT_PCT = 0.006       # 固定保本触发百分比 0.6%
BREAKEVEN_FIXED_THRESHOLD_PCT = 0.008    # 固定保本阈值百分比 0.8%
BREAKEVEN_LONG_OFFSET = 1.0005           # 做多保本微利偏移
BREAKEVEN_SHORT_OFFSET = 0.9995          # 做空保本微利偏移

# --- CONSERVATIVE 模式 ---
CONSERVATIVE_OVEREXTEND_MULT = 1.2       # 偏离 200 日均线 20% 判定为过度延伸

# --- 默认加密货币相关系数 ---
DEFAULT_CRYPTO_CORRELATION = 0.50

# ==========================================
# 🔥 UI 状态广播（Redis Pub/Sub）
# ==========================================

def notify_ui_update(event_type):
    """
    向 Redis 频道发布 UI 状态变更事件，供前端 Dashboard 实时刷新。
    
    Args:
        event_type: 事件类型字符串，如 "POSITION_CHANGE"
    """
    try:
        from redis_manager import redis_db
        if not redis_db.enabled:
            return
        redis_db.publish("wjbot:updates", json.dumps({"type": event_type, "ts": time.time()}))
    except Exception:
        # Redis 未开启或发布失败时静默忽略，绝不阻断交易流程
        pass


# 🔥 终极消音器：检测当前进程是否为回测进程
# 🔥 安全加固 v2: 使用环境变量检测，不再依赖脆弱的 sys.argv 匹配
IS_BACKTEST_PROCESS = os.environ.get('RUNNING_ENV') == 'BACKTEST'

def silent_print(*args, **kwargs):
    """引擎专用打印：实盘时大声汇报，回测时绝对闭嘴"""
    if not IS_BACKTEST_PROCESS:
        builtins.print(*args, **kwargs)

# 🔥 劫持当前模块所有的 print 函数
print = silent_print

# ==========================================
# 🔥 环境隔离装饰器（防止SANDBOX模式API泄漏）
# ==========================================

def _ensure_live_mode(func):
    """
    装饰器：确保只在LIVE模式下调用实盘API
    
    在所有币安API调用前强制检查环境模式：
    - SANDBOX模式：直接拦截并抛出异常
    - LIVE模式：正常执行
    
    Args:
        func: 被装饰的函数
    
    Returns:
        wrapper: 包装后的函数
    
    Raises:
        RuntimeError: 在SANDBOX模式下调用时抛出
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        if config.SYSTEM_CONFIG.get("RUNNING_MODE") == "SANDBOX":
            func_name = func.__name__
            raise RuntimeError(
                f"🚨 安全拦截: {func_name} 在SANDBOX模式下被禁止调用！"
            )
        return func(*args, **kwargs)
    return wrapper


# 🔥 包装所有币安API调用的安全函数
@_ensure_live_mode
def safe_futures_create_order(client, **kwargs):
    """安全的下单API（带环境检查 + 🔥 P0修复#2: API权重安全检查）"""
    ensure_api_safety("safe_futures_create_order")
    return client.futures_create_order(**kwargs)


@_ensure_live_mode
def safe_futures_cancel_order(client, **kwargs):
    """安全的撤单API（带环境检查 + 🔥 P0修复#2: API权重安全检查）"""
    ensure_api_safety("safe_futures_cancel_order")
    return client.futures_cancel_order(**kwargs)


@_ensure_live_mode
def safe_futures_change_leverage(client, **kwargs):
    """安全的杠杆调整API（带环境检查 + 🔥 P0修复#2: API权重安全检查）"""
    ensure_api_safety("safe_futures_change_leverage")
    return client.futures_change_leverage(**kwargs)


@_ensure_live_mode
def safe_futures_place_batch_orders(client, **kwargs):
    """安全的批量下单API（带环境检查 + 🔥 P0修复#2: API权重安全检查）"""
    ensure_api_safety("safe_futures_place_batch_orders")
    return client.futures_place_batch_orders(**kwargs)


# ==========================================
# 🔥 API 权重安全检查（防限流守卫）
# ==========================================

def ensure_api_safety(caller_name="unknown"):
    """
    API 请求前的权重安全检查 - 三级防护
    
    在发起任何 API 请求前调用此函数：
    - 权重 > 85%: 强制 sleep(2) + 打印警告（减速）
    - 权重 > 95%: 直接拦截请求，抛出异常（熔断）
    - 权重监控模块未加载时: 静默放行
    
    Args:
        caller_name: 调用方名称（用于日志追踪）
    
    Returns:
        bool: True=安全放行, False=不应该到达（>95%时直接抛异常）
    
    Raises:
        Exception: 权重 > 95% 时抛出异常，阻止 API 调用
    """
    if not API_WEIGHT_MONITOR_ENABLED:
        return True
    
    try:
        status = get_weight_status()
        usage_ratio = status['usage_ratio']
        usage_pct = status['usage_percent']
        
        # 🚨 Level 3: 紧急熔断（>88%）- 直接拦截
        if usage_ratio > 0.88:
            msg = f"🚨 [API权重熔断] {caller_name}: 权重 {usage_pct:.1f}% > 95%，拦截请求！等待权重回落..."
            print(msg)
            logger.critical(msg)
            raise Exception(f"API权重过高({usage_pct:.1f}%)，请求被拦截以防限流")
        
        # ⚠️ Level 2: 强制减速（>70%）- sleep + 警告
        if usage_ratio > 0.70:
            msg = f"⚠️ [API权重减速] {caller_name}: 权重 {usage_pct:.1f}% > 85%，强制冷却 5 秒..."
            print(msg)
            logger.warning(msg)
            time.sleep(5)
        
        return True
    
    except Exception as e:
        # 如果是我们自己抛出的熔断异常，继续向上传播
        if "API权重过高" in str(e):
            raise
        # 其他异常（如模块内部错误），静默放行不阻塞交易
        logger.warning(f"⚠️ API权重检查异常（静默放行）: {e}")
        return True


# ==========================================
# 🔥 AI 发单频率限流器（滑动窗口）
# ==========================================

class AIOrderRateLimiter:
    """
    滑动窗口限流器 — 防止 AI 幻觉死循环打满币安 API
    
    规则：单一交易对（Symbol）在 window_seconds 秒内，
    最多允许 max_requests 次来自 AI 自动触发的开仓请求（ENTRY）。
    
    线程安全：内部使用 threading.Lock 保护共享状态。
    """

    def __init__(self, max_requests=2, window_seconds=60):
        self._max_requests = max_requests
        self._window_seconds = window_seconds
        self._lock = threading.Lock()
        # {symbol: [timestamp1, timestamp2, ...]}
        self._requests: dict[str, list[float]] = {}

    def allow(self, symbol: str) -> bool:
        """
        判断本次请求是否放行。
        
        Returns:
            True  — 放行（同时记录本次时间戳）
            False — 拦截（超出频率）
        """
        now = time.time()
        cutoff = now - self._window_seconds

        with self._lock:
            timestamps = self._requests.setdefault(symbol, [])
            # 清除窗口外的过期记录
            timestamps[:] = [ts for ts in timestamps if ts > cutoff]

            if len(timestamps) >= self._max_requests:
                return False

            timestamps.append(now)
            return True

    def reset(self, symbol: str | None = None):
        """手动重置计数（调试 / 紧急恢复用）"""
        with self._lock:
            if symbol is None:
                self._requests.clear()
            else:
                self._requests.pop(symbol, None)


# 全局单例：60 秒内同一 Symbol 最多 2 次 AI 自动开仓
_ai_order_rate_limiter = AIOrderRateLimiter(max_requests=2, window_seconds=60)


def get_ai_order_rate_limiter() -> AIOrderRateLimiter:
    """获取全局 AI 发单限流器实例"""
    return _ai_order_rate_limiter


# ==========================================
# 引擎全局状态（连续亏损断路器）
# ==========================================
ENGINE_STATE = {
    'consecutive_losses': 0,
    'breaker_until': 0
}

# ==========================================
# 🔥 利滚利：平仓后刷新 BENCHMARK_CASH
# ==========================================
def _refresh_benchmark_after_close(client):
    """
    平仓后刷新 BENCHMARK_CASH，实现"利滚利"效果
    使凯利公式能基于最新资金量计算下一单仓位
    """
    from utils import _to_decimal
    
    if client is None:
        print(f"   ⚠️ 利滚利刷新失败: 无API连接")
        return
    
    try:
        acc_info = client.futures_account()
        total_margin_balance = float(acc_info.get('totalMarginBalance', 0))
        
        if total_margin_balance > 0:
            benchmark_value = float(_to_decimal(total_margin_balance).quantize(_to_decimal('0.01')))
            with state_lock:
                config.SYSTEM_CONFIG["BENCHMARK_CASH"] = benchmark_value
                save_data()
            print(f"   💰 利滚利：BENCHMARK_CASH 已更新为 ${benchmark_value:.2f}")
    except Exception as e:
        print(f"   ⚠️ 利滚利刷新失败: {e}")


# ==========================================
# 🔥 动态对账系统：BENCHMARK_CASH 初始化同步
# ==========================================
def sync_benchmark_with_api(client):
    """
    引擎启动时同步 BENCHMARK_CASH 到真实账户余额（动态对账模式）
    
    逻辑：
    1. 通过 client.futures_account() 获取 totalMarginBalance
    2. 使用 state_lock 将获取到的值写入 config.SYSTEM_CONFIG["BENCHMARK_CASH"] 和 PEAK_EQUITY
    3. 完成后调用 save_data() 持久化
    
    安全防御：
    - 如果 API 获取失败且没有本地缓存，必须抛出异常并阻止引擎启动
    - 严禁在金额为 0 的情况下运行
    
    Returns:
        (success: bool, message: str)
    """
    from utils import _to_decimal
    
    try:
        # 检查客户端连接
        if client is None:
            # 无API连接，检查本地缓存
            cached_benchmark = config.SYSTEM_CONFIG.get("BENCHMARK_CASH", 0.0)
            if cached_benchmark > 0:
                msg = f"⚠️ 无API连接，使用本地缓存: BENCHMARK_CASH=${cached_benchmark:.2f}"
                print(msg)
                send_tg_msg(f"⚠️ <b>{msg}</b>")
                return True, msg
            else:
                # 致命错误：无API且无缓存
                error_msg = "🚨 致命错误：无法连接交易所API且本地无有效缓存，拒绝启动引擎！"
                print(error_msg)
                send_tg_alert(
                    f"🔴 <b>[引擎启动失败]</b>\n\n"
                    f"{error_msg}\n\n"
                    f"⚠️ 请检查网络连接或API配置后重试。"
                )
                raise Exception(error_msg)
        
        # 从交易所获取真实余额
        try:
            acc_info = client.futures_account()
            total_margin_balance = float(acc_info.get('totalMarginBalance', 0))
            
            # 安全检查：余额不能为0
            if total_margin_balance <= 0:
                error_msg = f"🚨 致命错误：交易所返回余额为 ${total_margin_balance:.2f}，拒绝启动引擎！"
                print(error_msg)
                send_tg_alert(
                    f"🔴 <b>[引擎启动失败]</b>\n\n"
                    f"{error_msg}\n\n"
                    f"⚠️ 请检查账户余额或API权限。"
                )
                raise Exception(error_msg)
            
            # 使用 _to_decimal 确保精度符合 2 位小数要求
            benchmark_value = float(_to_decimal(total_margin_balance).quantize(_to_decimal('0.01')))
            
            with state_lock:
                config.SYSTEM_CONFIG["BENCHMARK_CASH"] = benchmark_value
                # 同步更新 PEAK_EQUITY（如果当前为0或小于基准）
                if config.SYSTEM_CONFIG.get("PEAK_EQUITY", 0) < benchmark_value:
                    config.SYSTEM_CONFIG["PEAK_EQUITY"] = benchmark_value
                save_data()
            
            msg = f"✅ 动态对账完成（实盘模式）: BENCHMARK_CASH=${benchmark_value:.2f}"
            print(msg)
            send_tg_msg(
                f"📊 <b>动态对账完成</b>\n\n"
                f"💰 当前账户余额: <code>${benchmark_value:.2f}</code>\n"
                f"📈 PEAK_EQUITY: <code>${config.SYSTEM_CONFIG['PEAK_EQUITY']:.2f}</code>\n\n"
                f"✅ 基准本金已同步到真实账户余额"
            )
            return True, msg
            
        except Exception as api_error:
            # API调用失败，检查本地缓存
            cached_benchmark = config.SYSTEM_CONFIG.get("BENCHMARK_CASH", 0.0)
            if cached_benchmark > 0:
                error_msg = f"⚠️ API调用失败: {str(api_error)[:100]}，使用本地缓存: ${cached_benchmark:.2f}"
                print(error_msg)
                send_tg_alert(
                    f"⚠️ <b>[动态对账警告]</b>\n\n"
                    f"API调用失败，已使用本地缓存\n"
                    f"缓存值: ${cached_benchmark:.2f}\n\n"
                    f"错误: {str(api_error)[:200]}"
                )
                return True, error_msg
            else:
                # 致命错误：API失败且无缓存
                error_msg = f"🚨 致命错误：API调用失败且本地无有效缓存，拒绝启动引擎！错误: {str(api_error)[:100]}"
                print(error_msg)
                send_tg_alert(
                    f"🔴 <b>[引擎启动失败]</b>\n\n"
                    f"{error_msg}\n\n"
                    f"⚠️ 请检查网络连接或API配置后重试。"
                )
                raise Exception(error_msg)
    
    except Exception as e:
        error_msg = f"🚨 动态对账异常: {str(e)[:100]}"
        print(error_msg)
        send_tg_alert(f"🔴 <b>[动态对账失败]</b>\n\n{error_msg}")
        raise


# ==========================================
# 币安持仓模式同步（对冲/单向）
# ==========================================
def sync_hedge_mode_to_binance(client):
    """
    引擎启动时同步币安账户的持仓模式（dualSidePosition）
    对冲模式 = dualSidePosition=true
    单向模式 = dualSidePosition=false

    🔥 环境隔离：SANDBOX 模式下跳过物理 API 调用

    Returns:
        (success: bool, message: str)
    """
    hedge_enabled = config.SYSTEM_CONFIG.get("HEDGE_MODE_ENABLED", False)
    dual_side = "true" if hedge_enabled else "false"
    mode_name = "对冲模式" if hedge_enabled else "单向模式"

    # 🔥 环境门控：SANDBOX 模式优雅绕过
    if config.SYSTEM_CONFIG.get("RUNNING_MODE") != "SANDBOX":
        if client is None:
            error_msg = "🚨 无API连接，无法同步持仓模式"
            print(error_msg)
            send_tg_alert(f"🔴 <b>[引擎启动失败]</b>\n\n{error_msg}")
            return False, error_msg

        try:
            # 🔍 预检查：先查询当前模式，避免不必要的切换
            account_info = client.futures_account()
            current_dual_side = account_info.get('dualSidePosition', False)
            
            # 如果已经是目标模式，直接返回成功
            if current_dual_side == hedge_enabled:
                msg = f"✅ 币安账户已处于{mode_name}，无需切换"
                print(msg)
                return True, msg
            
            # 需要切换模式
            client.futures_change_position_mode(dualSidePosition=dual_side)
            msg = f"✅ 币安账户持仓模式已同步为: {mode_name} (dualSidePosition={dual_side})"
            print(msg)
            send_tg_msg(f"🔧 <b>{msg}</b>")
            return True, msg
        except Exception as e:
            error_str = str(e)
            # APIError -4059: 当前模式已经是目标模式，无需切换（备用处理）
            if '-4059' in error_str or 'No need to change position side' in error_str:
                msg = f"✅ 币安账户已处于{mode_name}，无需切换"
                print(msg)
                return True, msg
            else:
                # 真正的错误（如有持仓导致无法切换）
                msg = f"🚨 切换持仓模式失败: {error_str[:150]}"
                print(msg)
                send_tg_alert(
                    f"🚨 <b>[紧急] 持仓模式同步失败</b>\n\n"
                    f"目标模式: {mode_name}\n"
                    f"错误: {error_str[:200]}\n\n"
                    f"⚠️ 可能原因: 当前有活跃持仓，无法切换模式。\n"
                    f"请先平掉所有持仓后再切换，或手动在币安APP中操作。\n\n"
                    f"🛑 <b>引擎启动已终止！</b>"
                )
                return False, msg
    else:
        # 🏖️ SANDBOX 模式：优雅绕过物理 API 调用
        msg = f"🏖️ [Sandbox Isolation] Bypassing physical Hedge Mode sync to avoid API conflict (目标模式: {mode_name})"
        print(msg)
        logger.info(msg)
        return True, msg


# ==========================================
# 🔥 幽灵流动性探测器：Spread 历史记录（全局字典 + 线程锁）
# ==========================================
_spread_history = {}        # {symbol: [(timestamp, spread_ratio), ...]}  最近60分钟
_spread_history_lock = threading.Lock()


def _record_and_check_spread_zscore(symbol, current_spread):
    """
    记录当前 Spread 并计算 Z-Score，判定是否为"幽灵流动性"。

    维护逻辑：
    1. 将 (timestamp, spread) 追加到 _spread_history[symbol]
    2. 清除超过 SPREAD_HISTORY_WINDOW（60分钟）的过期条目
    3. 计算 Z-Score = (当前价差 - 均值) / 标准差
    4. 返回 (z_score, is_ghost) — is_ghost=True 表示 Z-Score > 阈值

    Args:
        symbol: 交易对
        current_spread: 当前 Spread = (Ask1 - Bid1) / Mid_Price

    Returns:
        (z_score: float, is_ghost: bool)
    """
    now = time.time()
    cutoff = now - SPREAD_HISTORY_WINDOW

    with _spread_history_lock:
        # 初始化
        if symbol not in _spread_history:
            _spread_history[symbol] = []

        # 追加当前记录
        _spread_history[symbol].append((now, current_spread))

        # 清除过期条目
        _spread_history[symbol] = [
            (ts, sp) for ts, sp in _spread_history[symbol] if ts > cutoff
        ]

        spreads = [sp for _, sp in _spread_history[symbol]]

    # 样本不足时无法计算 Z-Score，放行
    if len(spreads) < 20:
        return 0.0, False

    mean_spread = np.mean(spreads)
    std_spread = np.std(spreads)

    if std_spread < 1e-12:
        # 标准差为零（所有价差完全相同），无异常
        return 0.0, False

    z_score = (current_spread - mean_spread) / std_spread
    is_ghost = z_score > SPREAD_ZSCORE_THRESHOLD

    return z_score, is_ghost


# ==========================================
# 盘口滑点预检
# ==========================================

def check_orderbook_slippage(client, symbol, side, quantity, max_slippage=0.0010):
    """
    🔥 Task 2: L2 订单簿深度审计 - VWAP 滑点计算
    
    核心逻辑：
    1. 获取 L2 订单簿深度（20档）
    2. 计算加权平均成交价 VWAP = Σ(price × qty) / Σ(qty)
    3. 计算滑点率 = |VWAP - 盘口价| / 盘口价
    4. 若滑点率 > max_slippage，拒绝开仓并发送 Telegram 预警
    
    🔥 Warrior-Scalper 双层滑点缓冲:
    - BTC/ETH/SOL (高流动性): 0.10% (10 bps) - 深度充足，紧控滑点
    - 其他山寨币 (低流动性): 0.15% (15 bps) - 给予额外缓冲，防止频繁拒单
    - 目标：确保执行速度的同时，拦截 0.3%+ 的滑点陷阱
    
    Args:
        client: 币安客户端
        symbol: 交易对
        side: 'BUY' 或 'SELL'
        quantity: 预计成交数量
        max_slippage: 最大容忍滑点率（默认 0.10%，可通过动态双层逻辑覆盖）
    
    Returns:
        (allowed: bool, reason: str, estimated_vwap: float)
    """
    try:
        from decimal import Decimal
        
        # 🔥 Warrior-Scalper 双层动态滑点缓冲
        # Tier 1: BTC/ETH/SOL 高流动性主流币 → 0.10% (10 bps)
        # Tier 2: 其他山寨币 → 0.15% (15 bps)
        HIGH_LIQUIDITY_SYMBOLS = ('BTCUSDT', 'ETHUSDT', 'SOLUSDT')
        if symbol in HIGH_LIQUIDITY_SYMBOLS:
            max_slippage = 0.0010  # 0.10% - 主流币深度充足，紧控滑点
        else:
            max_slippage = 0.0015  # 0.15% - 山寨币给予额外缓冲
        
        # 🔥 Fix #13: config_slippage 仅作为上限 cap，不覆盖双层动态逻辑
        # 旧逻辑：config 有值就直接覆盖 → SCALPER preset 的 0.0005 会把山寨币 0.15% 压成 0.05%
        # 新逻辑：config 值仅作为天花板，双层值超过 cap 时才截断
        config_slippage = config.SYSTEM_CONFIG.get("MAX_SLIPPAGE", None)
        if config_slippage is not None:
            max_slippage = min(max_slippage, config_slippage)
        
        # 🔥 Fix #14: L2 订单簿动态深度 — 根据仓位名义价值自适应档位
        # 小仓位（< $5000）: 20 档足够
        # 中仓位（$5000~$20000）: 50 档
        # 大仓位（> $20000）: 100 档
        try:
            # 用盘口估价快速计算名义价值（避免额外 API 调用）
            _est_price = float(client.futures_book_ticker(symbol=symbol).get('bidPrice', 0)) or 1.0
            _notional_value = quantity * _est_price
            if _notional_value > 20000:
                _ob_depth = 100
            elif _notional_value > 5000:
                _ob_depth = 50
            else:
                _ob_depth = 20
        except Exception:
            _ob_depth = 20  # 估价失败时保守使用 20 档
        
        # 获取 L2 订单簿（动态深度）
        orderbook = client.futures_order_book(symbol=symbol, limit=_ob_depth)
        
        if side == 'BUY':
            # 买入看卖盘 (asks)
            levels = orderbook['asks']
            best_price = float(levels[0][0])
        else:
            # 卖出看买盘 (bids)
            levels = orderbook['bids']
            best_price = float(levels[0][0])
        
        # ==========================================
        # 🔥 幽灵流动性探测器：Spread Z-Score 检查
        # 在 VWAP 深度审计之前，先检测当前 Spread 是否异常偏离历史均值
        # 如果 Z-Score > 3.0，判定为"幽灵流动性"（做市商撤单 / 流动性闪崩），拒绝开仓
        # ==========================================
        try:
            _bid1 = float(orderbook['bids'][0][0]) if orderbook['bids'] else 0
            _ask1 = float(orderbook['asks'][0][0]) if orderbook['asks'] else 0
            _mid_price = (_bid1 + _ask1) / 2.0 if (_bid1 > 0 and _ask1 > 0) else 0
            
            if _mid_price > 0:
                _current_spread = (_ask1 - _bid1) / _mid_price
                _z_score, _is_ghost = _record_and_check_spread_zscore(symbol, _current_spread)
                
                if _is_ghost:
                    reason = (
                        f"幽灵流动性拦截: Spread Z-Score={_z_score:.2f} > {SPREAD_ZSCORE_THRESHOLD} "
                        f"(Bid1={_bid1}, Ask1={_ask1}, Spread={_current_spread*100:.4f}%)"
                    )
                    print(f"   👻 [{symbol}] {reason}")
                    
                    from utils import send_tg_alert
                    import html
                    send_tg_alert(
                        f"👻 <b>[幽灵流动性拦截]</b>\n\n"
                        f"币种: {html.escape(symbol)}\n"
                        f"方向: {side}\n"
                        f"Bid1: {_bid1}\n"
                        f"Ask1: {_ask1}\n"
                        f"Spread: {_current_spread*100:.4f}%\n"
                        f"Z-Score: <b>{_z_score:.2f}</b> > {SPREAD_ZSCORE_THRESHOLD}\n\n"
                        f"⚠️ 做市商疑似撤单，流动性闪崩，拒绝开仓"
                    )
                    return False, reason, 0.0
                else:
                    if _z_score > 0:
                        print(f"   👁️ [{symbol}] Spread Z-Score={_z_score:.2f} (正常范围), Spread={_current_spread*100:.4f}%")
        except Exception as spread_e:
            # Spread 检查失败不阻断主流程，仅记录日志
            print(f"   ⚠️ [{symbol}] Spread Z-Score 检查异常（不阻断）: {spread_e}")
        
        # 🔥 L2 深度审计：使用 Decimal 精确累加，消除浮点误差
        d_remaining = Decimal(str(quantity))
        d_total_cost = Decimal('0')
        d_total_qty = Decimal('0')
        
        for price_str, qty_str in levels:
            d_price = Decimal(price_str)   # 直接从字符串构造，无精度损失
            d_qty = Decimal(qty_str)
            
            if d_remaining <= 0:
                break
            
            # 计算本档可成交数量
            d_filled = min(d_remaining, d_qty)
            d_total_cost += d_filled * d_price
            d_total_qty += d_filled
            d_remaining -= d_filled
        
        # 转回 float 用于后续日志和返回值
        remaining_qty = float(d_remaining)
        total_qty_filled = float(d_total_qty)
        
        # 检查1：盘口深度不足
        if remaining_qty > 0:
            reason = f"L2深度不足，缺口 {remaining_qty:.4f} (需求 {quantity:.4f})"
            print(f"   🚨 [{symbol}] {reason}")
            
            # 发送 Telegram 预警
            from utils import send_tg_alert
            import html
            send_tg_alert(
                f"🚨 <b>[L2滑点预警-深度不足]</b>\n\n"
                f"币种: {html.escape(symbol)}\n"
                f"方向: {side}\n"
                f"需求数量: {quantity:.4f}\n"
                f"可成交: {total_qty_filled:.4f}\n"
                f"缺口: {remaining_qty:.4f}\n\n"
                f"⚠️ 盘口深度不足，拒绝开仓"
            )
            return False, reason, 0.0
        
        # 🔥 计算 VWAP（加权平均成交价）- 使用 Decimal 精确除法后转 float
        d_best_price = Decimal(str(best_price))
        d_vwap = d_total_cost / d_total_qty if d_total_qty > 0 else d_best_price
        vwap = float(d_vwap)
        
        # 🔥 计算滑点率 = |VWAP - 盘口价| / 盘口价（Decimal 精确计算）
        d_slippage = abs(d_vwap - d_best_price) / d_best_price if d_best_price > 0 else Decimal('0')
        slippage_rate = float(d_slippage)
        
        # 检查2：滑点率超限
        if slippage_rate > max_slippage:
            reason = f"VWAP滑点 {slippage_rate*100:.3f}% > 阈值 {max_slippage*100:.2f}%"
            print(f"   🚨 [{symbol}] {reason}")
            print(f"      盘口价: {best_price:.4f}, VWAP: {vwap:.4f}")
            
            # 🔥 发送 Telegram 预警
            from utils import send_tg_alert
            import html
            send_tg_alert(
                f"🚨 <b>[L2滑点预警-超限]</b>\n\n"
                f"币种: {html.escape(symbol)}\n"
                f"方向: {side}\n"
                f"盘口价: {best_price:.4f}\n"
                f"VWAP: {vwap:.4f}\n"
                f"滑点率: <b>{slippage_rate*100:.3f}%</b>\n"
                f"阈值: {max_slippage*100:.2f}%\n\n"
                f"⚠️ 滑点超限，拒绝开仓"
            )
            return False, reason, vwap
        
        # 通过检查
        print(f"   ✅ [{symbol}] L2滑点检查通过: VWAP={vwap:.4f}, 滑点={slippage_rate*100:.3f}%")
        return True, "OK", vwap
        
    except Exception as e:
        error_msg = f"L2盘口检查异常: {str(e)[:50]}"
        print(f"   ⚠️ [{symbol}] {error_msg}")
        
        # 异常时保守拒绝
        from utils import send_tg_alert
        import html
        send_tg_alert(
            f"⚠️ <b>[L2滑点检查异常]</b>\n\n"
            f"币种: {html.escape(symbol)}\n"
            f"错误: {html.escape(str(e)[:100])}\n\n"
            f"🛡️ 保守拒绝开仓"
        )
        return False, error_msg, 0.0

# ==========================================
# K线数据获取
# ==========================================

def get_historical_klines(client, symbol, interval, limit=500):
    """获取历史K线数据（支持长周期抓取，使用 HistoricalKlinesType.FUTURES）
    
    核心逻辑：
    1. 使用 client.get_historical_klines 并指定 HistoricalKlinesType.FUTURES
    2. 根据传入的 limit 和 interval 自动计算 start_str (毫秒时间戳)
    3. 自动处理分页拼接，突破单次 1000 根的物理限制
    
    Args:
        client: Binance 客户端
        symbol: 交易对（如 'BTCUSDT'）
        interval: K线周期（如 '1m', '5m', '15m', '1h', '4h', '1d'）
        limit: 需要的K线根数（不再受1000硬性限制）
    
    Returns:
        pd.DataFrame: K线数据，失败返回 None
    """
    if client is None:
        print(f"❌ 无API连接，无法获取K线数据")
        return None
    
    # 映射 interval 到毫秒数以计算起始时间
    ms_map = {
        "1m": 60000, 
        "5m": 300000, 
        "15m": 900000, 
        "1h": 3600000, 
        "4h": 14400000, 
        "1d": 86400000
    }
    start_ts = int(time.time() * 1000) - (limit * ms_map.get(interval, 3600000))

    try:
        from binance.client import HistoricalKlinesType
        # 使用 Historical 接口自动处理分页拼接
        klines = client.get_historical_klines(
            symbol=symbol,
            interval=get_binance_interval(interval),
            start_str=start_ts,
            klines_type=HistoricalKlinesType.FUTURES
        )
        # 确保截取最后请求的 limit 数量
        if klines: 
            klines = klines[-limit:]
    except Exception as e:
        print(f"❌ 抓取长周期K线失败: {e}")
        return None
    
    # 保持原有的 DataFrame 构建逻辑
    df = pd.DataFrame(klines, columns=[
        'timestamp', 'open', 'high', 'low', 'close', 'volume',
        'close_time', 'quote_asset_volume', 'number_of_trades',
        'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
    ])
    numeric_cols = ['open', 'high', 'low', 'close', 'volume', 'taker_buy_base_asset_volume']
    df[numeric_cols] = df[numeric_cols].astype(float)
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    return df

# ==========================================
# 🔥 机构衍生品雷达：未平仓合约量 (OI) 与资金费率（含 TTL 缓存）
# ==========================================

# 🔥 Fix #13: TTL 缓存 - 衍生品数据和 RS 数据每 10 分钟刷新一次，避免多币种监控时 API 权重爆炸
_derivatives_cache = {}  # {symbol: {'data': dict, 'ts': float}}
_rs_cache = {}           # {symbol: {'value': float, 'ts': float}}
_DERIVATIVES_TTL = 600   # 10 分钟
_RS_TTL = 600            # 10 分钟

# 🔥 任务1: HTF EMA 实盘缓存（15分钟TTL）
# 实盘模式下，同 Symbol、同周期的高周期 EMA 在 15 分钟内仅允许请求一次 API，其余时间直接读缓存
_htf_ema_cache = {}      # {f"{symbol}_{interval}": {'ema': float, 'ts': float}}
_HTF_EMA_CACHE_LOCK = threading.Lock()
_HTF_EMA_TTL = 900       # 15 分钟


def get_derivatives_data(client, symbol):
    """
    🔥 机构衍生品雷达：获取全网未平仓合约量 (OI) 与资金费率
    🔥 Fix #13: 加入 TTL 缓存（10 分钟），多币种监控时大幅降低 API 权重消耗
    """
    if client is None:
        return {'oi': 0.0, 'funding_rate': 0.0}
    
    # 检查缓存
    now = time.time()
    cached = _derivatives_cache.get(symbol)
    if cached and (now - cached['ts']) < _DERIVATIVES_TTL:
        return cached['data']
    
    try:
        # 获取最新资金费率
        fr_data = client.futures_funding_rate(symbol=symbol, limit=1)
        funding_rate = float(fr_data[0]['fundingRate']) if fr_data else 0.0
        
        # 获取最新未平仓合约量 (Open Interest)
        oi_data = client.futures_open_interest(symbol=symbol)
        open_interest = float(oi_data['openInterest']) if oi_data else 0.0
        
        result = {
            'oi': open_interest,
            'funding_rate': funding_rate
        }
        # 写入缓存
        _derivatives_cache[symbol] = {'data': result, 'ts': now}
        return result
    except Exception as e:
        # 静默处理，防止阻断主流程；如果有旧缓存则返回旧值
        if cached:
            return cached['data']
        return {'oi': 0.0, 'funding_rate': 0.0}


# ==========================================
# 🔥 机构 RS 轮动矩阵：相对强弱指数（含 TTL 缓存）
# ==========================================

def get_relative_strength(client, symbol):
    """
    🔥 机构 RS 轮动矩阵：计算标的相对 BTC 的强弱指数
    🔥 Fix #13: 加入 TTL 缓存（10 分钟），避免每个扫描周期重复调用 futures_ticker
    """
    if client is None or symbol == 'BTCUSDT':
        return 0.0 # BTC 自身作为基准，RS差值为 0

    # 检查缓存
    now = time.time()
    cached = _rs_cache.get(symbol)
    if cached and (now - cached['ts']) < _RS_TTL:
        return cached['value']

    try:
        # 获取 BTC 24小时涨跌幅
        btc_ticker = client.futures_ticker(symbol='BTCUSDT')
        btc_change = float(btc_ticker['priceChangePercent'])
        
        # 获取当前币种 24小时涨跌幅
        sym_ticker = client.futures_ticker(symbol=symbol)
        sym_change = float(sym_ticker['priceChangePercent'])
        
        # RS 相对强弱值 = 当前币种涨跌幅 - BTC涨跌幅
        rs_value = sym_change - btc_change
        # 写入缓存
        _rs_cache[symbol] = {'value': rs_value, 'ts': now}
        return rs_value
    except Exception as e:
        if cached:
            return cached['value']
        return 0.0


# ==========================================
# 技术指标计算（含性能优化缓存）
# ==========================================

def _compute_ema(data, period):
    import pandas as pd
    return pd.Series(data).ewm(span=period, adjust=False).mean().values

def _compute_atr(high, low, close, period=14):
    import pandas as pd
    tr1 = high - low
    tr2 = np.abs(high - np.roll(close, 1))
    tr3 = np.abs(low - np.roll(close, 1))
    tr = np.maximum(tr1, np.maximum(tr2, tr3))
    tr[0] = tr1[0]
    return pd.Series(tr).ewm(span=period, adjust=False).mean().values

def _compute_adx(high, low, close, period=14, atr=None):
    import pandas as pd
    up_move = high - np.roll(high, 1)
    down_move = np.roll(low, 1) - low
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)
    
    if atr is None:
        atr = _compute_atr(high, low, close, period)
    plus_di = 100 * pd.Series(plus_dm).ewm(span=period, adjust=False).mean().values / (atr + 1e-10)
    minus_di = 100 * pd.Series(minus_dm).ewm(span=period, adjust=False).mean().values / (atr + 1e-10)
    
    dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)
    return pd.Series(dx).ewm(span=period, adjust=False).mean().values

def calculate_indicators(df, force_recalc=False, custom_config=None):
    """计算技术指标 (机构加强版 + 性能优化)
    
    Args:
        df: K线数据
        force_recalc: 强制重算长周期指标（K线更新时传True）
        custom_config: 可选的自定义配置字典，如果传入则优先使用它而非全局 config.SYSTEM_CONFIG
    """
    if df is None or len(df) < 100:
        return None
    
    try:
        # 🔥 配置隔离：优先使用传入的 custom_config
        cfg = custom_config if custom_config is not None else config.SYSTEM_CONFIG
        
        # 🔥 保留 taker_buy_base_asset_volume 用于 CVD 计算
        keep_cols = ['timestamp', 'open', 'high', 'low', 'close', 'volume']
        if 'taker_buy_base_asset_volume' in df.columns:
            keep_cols.append('taker_buy_base_asset_volume')
        df = df[keep_cols].copy()
        
        # 提取 NumPy 数组
        h = df['high'].values.astype(np.float64)
        l = df['low'].values.astype(np.float64)
        c = df['close'].values.astype(np.float64)
        
        # 1. 基础指标计算 (NumPy 重构版)
        df['ATR'] = _compute_atr(h, l, c, period=cfg["ATR_PERIOD"])
        df['EMA_TREND'] = _compute_ema(c, cfg["EMA_TREND"])
        df['ADX'] = _compute_adx(h, l, c, period=14, atr=df['ATR'].values)
        
        # MACD
        ema_fast = _compute_ema(c, cfg["MACD_FAST"])
        ema_slow = _compute_ema(c, cfg["MACD_SLOW"])
        macd_line = ema_fast - ema_slow
        macd_signal = _compute_ema(macd_line, cfg["MACD_SIGNAL"])
        df['MACD_line'] = macd_line
        df['MACD_signal'] = macd_signal
        df['MACD_hist'] = macd_line - macd_signal
        
        # 保留 ATR_SMA100 计算
        df['ATR_SMA100'] = pd.Series(df['ATR']).rolling(window=100).mean().values
        df['Relative_ATR'] = df['ATR'] / df['ATR_SMA100']
        
        # NumPy-based RSI calculation
        _rsi_period = cfg.get("RSI_PERIOD", 14)
        _delta = pd.Series(c).diff()
        _gain = _delta.clip(lower=0)
        _loss = (-_delta).clip(lower=0)
        _avg_gain = _gain.ewm(span=_rsi_period, adjust=False).mean()
        _avg_loss = _loss.ewm(span=_rsi_period, adjust=False).mean()
        _rs = _avg_gain / (_avg_loss + 1e-10)
        df['RSI'] = 100 - (100 / (1 + _rs))
        
        # 🔥 增强 5: Stochastic RSI（SCALPER P3 震荡乒乓精确入场）
        try:
            stoch_rsi = ta.stochrsi(df['close'], length=14, rsi_length=14, k=3, d=3)
            if stoch_rsi is not None and len(stoch_rsi.columns) >= 2:
                df['StochRSI_K'] = stoch_rsi.iloc[:, 0]
                df['StochRSI_D'] = stoch_rsi.iloc[:, 1]
        except Exception:
            df['StochRSI_K'] = float('nan')
            df['StochRSI_D'] = float('nan')
        
        # 2. 机构成本线 (VWAP) - 🔥 Rolling 72-period VWAP (aligned with backtest_worker.py)
        # 公式: VWAP = sum(close * volume, 72) / sum(volume, 72)
        # 抛弃 ta.vwap() 的 daily-anchor cumsum，统一为滚动窗口，消除回测/实盘撕裂
        try:
            _vwap_window = 72
            _typical_price = df['close']  # 与回测一致，使用 close 而非 (H+L+C)/3
            _rolling_vp = (_typical_price * df['volume']).rolling(_vwap_window, min_periods=1).sum()
            _rolling_v  = df['volume'].rolling(_vwap_window, min_periods=1).sum()
            df['VWAP'] = _rolling_vp / (_rolling_v + 1e-10)
        except Exception as vwap_e:
            df['VWAP'] = float('nan')
            print(f"⚠️ VWAP计算失败: {vwap_e}")
        
        # 3. 增强版 TTM Squeeze (蓄力过滤 + 动态通道)
        try:
            # 动态调整通道：趋势强则通道宽
            current_adx = df['ADX'].iloc[-1] if not pd.isna(df['ADX'].iloc[-1]) else 20
            dynamic_scalar = 2.0 if current_adx > 25 else 1.5
            
            bb = ta.bbands(df['close'], length=20, std=2.0)
            kc = ta.kc(df['high'], df['low'], df['close'], length=20, scalar=dynamic_scalar)
            
            if bb is not None and kc is not None:
                BBU = bb[[c for c in bb.columns if c.startswith('BBU')][0]]
                BBL = bb[[c for c in bb.columns if c.startswith('BBL')][0]]
                KCU = kc[[c for c in kc.columns if c.startswith('KCUe')][0]]
                KCL = kc[[c for c in kc.columns if c.startswith('KCLe')][0]]
                
                df['Squeeze_On'] = (BBU < KCU) & (BBL > KCL)
                
                # --- 新增：强制要求挤压蓄力超过 N 根 K 线（SCALPER 模式降低阈值）---
                # 🔥 Squeeze Duration for Scalpers: 3 bars minimum
                # 2 bars = 噪音太多（假突破频发），3 bars = 确保最小"蓄力盘旋"后再释放
                # 防止噪音假突破的同时保持对真实 squeeze 释放的响应速度
                mode_preset = cfg.get("STRATEGY_MODE", "STANDARD")
                squeeze_thr = 3 if mode_preset == "SCALPER" else 7
                
                df['Squeeze_Duration'] = (
                    df['Squeeze_On'].astype(int)
                    .groupby((df['Squeeze_On'] != df['Squeeze_On'].shift()).cumsum())
                    .cumsum()
                )
                df['Squeeze_Fired'] = (
                    (df['Squeeze_On'].shift(1) == True) &
                    (df['Squeeze_On'] == False) &
                    (df['Squeeze_Duration'].shift(1) >= squeeze_thr)
                )
            else:
                df['Squeeze_On'], df['Squeeze_Fired'] = False, False
        except:
            df['Squeeze_On'], df['Squeeze_Fired'] = False, False
        
        # 🔥 预热数据量检查：dropna 前预警
        if len(df) <= 200:
            logger.warning(f"预热数据可能不足，当前 K 线数量: {len(df)}")
        
        # 防御性清理：移除所有 NaN 行，防止新币种数据不足导致逻辑判断异常
        df = df.dropna()
        
        # 🔥 增加物理残留检查：防止断网导致的极短 K 线被全删
        if len(df) == 0:
            logger.error("⚠️ 指标计算后数据为空 (所有行均含NaN)，请检查预热 limit 是否充足。")
            return None
            
        return df
    except Exception as e:
        print(f"⚠️ 计算技术指标失败: {e}")
        return None

# ==========================================
# 🔥 Fix #12: 日线 EMA200 缓存（每天只拉一次）
# ==========================================
_daily_ema_cache = {}  # {symbol: {'ema200': float, 'date': str}}
_DAILY_EMA_CACHE_LOCK = threading.Lock()

# 🔥 增强 2：OI 变化率缓存
_oi_cache = {}  # {symbol: {'oi': float, 'timestamp': float}}
_OI_CACHE_LOCK = threading.Lock()

def _get_daily_ema200(client, symbol, custom_config=None, mtf_data=None):
    """
    获取日线 EMA200（带每日缓存）
    🔥 Fix #12: 每个交易日只拉取一次，避免重复 API 调用
    """
    is_backtest_mode = (custom_config is not None)
    today = datetime.now().strftime('%Y-%m-%d')
    
    # 检查缓存
    with _DAILY_EMA_CACHE_LOCK:
        cached = _daily_ema_cache.get(symbol)
        if cached and cached.get('date') == today:
            return cached['ema200']
    
    # 回测模式：从 mtf_data 获取
    if is_backtest_mode and mtf_data and '1d' in mtf_data:
        df_1d = mtf_data['1d']
        if df_1d is not None and len(df_1d) > 0:
            import pandas_ta as ta
            ema_1d = ta.ema(df_1d['close'], length=200)
            if ema_1d is not None and len(ema_1d) > 0:
                ema200 = float(ema_1d.iloc[-1])
                with _DAILY_EMA_CACHE_LOCK:
                    _daily_ema_cache[symbol] = {'ema200': ema200, 'date': today}
                return ema200
    
    # 实盘模式：从 API 获取
    elif not is_backtest_mode and client is not None:
        df_1d = get_historical_klines(client, symbol, "1d", limit=250)
        if df_1d is not None and len(df_1d) > 0:
            import pandas_ta as ta
            ema_1d = ta.ema(df_1d['close'], length=200)
            if ema_1d is not None and len(ema_1d) > 0:
                ema200 = float(ema_1d.iloc[-1])
                with _DAILY_EMA_CACHE_LOCK:
                    _daily_ema_cache[symbol] = {'ema200': ema200, 'date': today}
                return ema200
    
    return None


# ==========================================
# 交易信号生成
# ==========================================

def generate_trading_signals(df, symbol, client=None, custom_config=None, mtf_data=None):
    """生成交易信号 (含黑天鹅熔断 + 防骗线时间锁 + MTF多周期共振 + 动态RSI + 日线过滤)
    
    Args:
        df: K线数据
        symbol: 交易对
        client: Binance客户端
        custom_config: 可选的自定义配置字典，如果传入则优先使用它而非全局 config.SYSTEM_CONFIG
        mtf_data: 多周期数据字典（回测模式必传），格式: {'15m': DataFrame, '1h': DataFrame, '4h': DataFrame, '1d': DataFrame}
    """
    if df is None or len(df) < 2:
        return None
    
    # 🔥 v2.5: 支持自定义配置注入（回测隔离模式）
    cfg = custom_config if custom_config is not None else config.SYSTEM_CONFIG
    
    # 🔥 v2.8: 回测模式静默日志（检测是否传入 custom_config）
    is_backtest_mode = (custom_config is not None)
    
    # ==========================================
    # 🔥 日线级过滤 (1D Daily Filter) - Fix #12: 使用缓存
    # 🔥 Fix #8: 预初始化 daily_ema_200，防止 try 块异常后变量未定义
    # ==========================================
    daily_ema_200 = None
    try:
        daily_ema_200 = _get_daily_ema200(client, symbol, custom_config, mtf_data)
        
        if daily_ema_200 is not None:
            current_price = df.iloc[-1]['close']
            
            if not is_backtest_mode:
                print(f"   📊 [{symbol}] 日线过滤: 价格={current_price:.4f}, 1D_EMA200={daily_ema_200:.4f}")
    
    except Exception as daily_e:
        if not is_backtest_mode:
            print(f"   ⚠️ [{symbol}] 日线过滤计算失败: {daily_e}")
        daily_ema_200 = None
    
    try:
        signals = {
            'symbol': symbol,
            'timestamp': datetime.now(),
            'price': df.iloc[-1]['close'],
            'atr': df.iloc[-1].get('ATR', 0),
            'signals': []
        }
        
        if len(df) < 2:
            return None
        
        mode_preset = cfg.get("STRATEGY_MODE", "STANDARD")
        use_latest = cfg.get("USE_LATEST_CANDLE", False)
        
        # ====== SCALPER 模式特殊处理 ======
        is_scalper_mode = (mode_preset == "SCALPER")
        if is_scalper_mode:
            # 狂战士模式：强制使用最新K线，即发即开
            use_latest = True
        
        if use_latest:
            closed_candle = df.iloc[-1]
            prev_closed_candle = df.iloc[-2]
            prev2_closed_candle = df.iloc[-3]
        else:
            closed_candle = df.iloc[-2]
            prev_closed_candle = df.iloc[-3]
            prev2_closed_candle = df.iloc[-4]
        
        # ==========================================
        # 🛡️ 任务1：市场异常检测（防黑天鹅）- 一票否决权
        # ==========================================
        if cfg.get("BLACK_SWAN_DEFENSE", True):  # 👈 对接 TG 开关
            try:
                # 检测1：跳空检测（>5%）
                prev_close = prev_closed_candle['close']
                current_open = closed_candle['open']
                
                # 防除零处理
                if prev_close > 0:
                    gap_ratio = abs(current_open - prev_close) / prev_close
                    if gap_ratio > 0.04:
                        msg = f"🚨 [{symbol}] 触发黑天鹅拦截：检测到跳空异动 ({gap_ratio*100:.2f}%)"
                        if not is_backtest_mode:
                            print(msg)
                            logger.warning(msg)
                        return None
                
                # 检测2：极端振幅（>6%）
                candle_range = closed_candle['high'] - closed_candle['low']
                current_close = closed_candle['close']
                
                # 防除零处理
                if current_close > 0:
                    amplitude_ratio = candle_range / current_close
                    if amplitude_ratio > 0.06:
                        msg = f"🚨 [{symbol}] 触发黑天鹅拦截：检测到极端振幅 ({amplitude_ratio*100:.2f}%)"
                        if not is_backtest_mode:
                            print(msg)
                            logger.warning(msg)
                        return None
                
                # 检测3：天量异动（>5倍均量）
                current_volume = closed_candle['volume']
                avg_volume_20 = df['volume'].shift(1).tail(20).mean()  # 🔥 v7.0: 确保与历史均量对比
                
                # 防除零处理
                if avg_volume_20 > 0:
                    volume_ratio = current_volume / avg_volume_20
                    if volume_ratio > (10.0 if is_scalper_mode else 4.0):
                        msg = f"🚨 [{symbol}] 触发黑天鹅拦截：检测到天量异动 ({volume_ratio:.2f}x均量)"
                        if not is_backtest_mode:
                            print(msg)
                            logger.warning(msg)
                        return None
            
            except Exception as anomaly_e:
                print(f"⚠️ [{symbol}] 市场异常检测失败: {anomaly_e}")
                # 检测失败时保守拦截
                return None
        
        # ====== 防守补丁 A：黑天鹅波动率熔断（机构级全模式适配）======
        relative_atr = closed_candle.get('Relative_ATR', 1.0)
        if pd.isna(relative_atr):
            relative_atr = 1.0
        
        # 🔥 Fix #9: 从传入的 cfg 读取策略模式，而非全局 config.SYSTEM_CONFIG
        current_mode = cfg.get("STRATEGY_MODE", "STANDARD")
        preset_config = config.STRATEGY_PRESETS.get(current_mode, {})
        vol_limit = preset_config.get("VOL_LIMIT", cfg.get("VOL_LIMIT", 2.0))  # 默认 2.0
        
        if relative_atr > vol_limit:
            mode_name = preset_config.get("name", current_mode)
            print(f"   🚨 [{symbol}] 波动率异常，{mode_name} 已自动挂起!")
            print(f"      Relative_ATR={relative_atr:.2f} > VOL_LIMIT={vol_limit}")
            return None
        
        is_price_above_ema = False
        is_price_below_ema = False
        if 'EMA_TREND' in df.columns and not pd.isna(closed_candle['EMA_TREND']):
            is_price_above_ema = closed_candle['close'] > closed_candle['EMA_TREND']
            is_price_below_ema = closed_candle['close'] < closed_candle['EMA_TREND']
        
        adx_val = closed_candle.get('ADX', 0)
        is_low_vol = cfg.get("LOW_VOL_MODE", False)
        
        # 🔥 参谋部补强A: 阶梯式 ADX 门槛（Volume-Adaptive ADX Threshold）
        # 成交量爆发（Volume > 1.5x 均量）→ ADX 门槛降至 20（早入场捕获动能）
        # 常规突破（无放量确认）→ ADX 门槛升至 28（严格过滤震荡噪音）
        if is_low_vol:
            current_adx_thr = 10
        else:
            # 计算成交量爆发比率
            _vol_surge_ratio = 1.0
            if len(df) >= 21:
                _avg_vol_20 = df['volume'].iloc[-21:-1].mean()
                _current_vol = closed_candle.get('volume', 0)
                if _avg_vol_20 > 0:
                    _vol_surge_ratio = _current_vol / _avg_vol_20
            
            # 阶梯式门槛：放量时降低门槛，缩量时提高门槛
            ADX_THR_VOLUME_BURST = cfg.get("ADX_THR_VOLUME_BURST", 22)   # 放量门槛
            ADX_THR_NORMAL = cfg.get("ADX_THR_NORMAL", 25)               # 常规门槛
            VOLUME_BURST_MULT = cfg.get("VOLUME_BURST_MULT", 1.8)        # 放量判定倍数
            
            if _vol_surge_ratio > VOLUME_BURST_MULT:
                current_adx_thr = ADX_THR_VOLUME_BURST
                if not is_backtest_mode:
                    print(f"   🔥 [{symbol}] 阶梯式ADX: 放量{_vol_surge_ratio:.1f}x > {VOLUME_BURST_MULT}x → ADX门槛降至{ADX_THR_VOLUME_BURST}")
            else:
                current_adx_thr = ADX_THR_NORMAL
                if not is_backtest_mode:
                    print(f"   📊 [{symbol}] 阶梯式ADX: 常规量能{_vol_surge_ratio:.1f}x → ADX门槛升至{ADX_THR_NORMAL}")
        
        # === 🩸 死神之眼：CVD 订单流照妖镜（🔥 任务2: 10根K线波段背离升级）===
        cvd_bearish_divergence = False
        cvd_bullish_divergence = False
        try:
            # 🔥 v2.17 性能优化：STANDARD 回测模式跳过 CVD cumsum 重计算
            # cumsum() 在逐根回放时是 O(N²) 的算力黑洞
            _skip_cvd = (is_backtest_mode and mode_preset == 'STANDARD')
            _CVD_WINDOW = 10  # 🔥 任务2: 波段背离窗口（10根K线）
            if _skip_cvd:
                pass  # 跳过 CVD 计算，使用默认 False
            elif 'taker_buy_base_asset_volume' in df.columns and len(df) >= _CVD_WINDOW + 2:
                # 真实卖出量 = 总量 - 真实买入量
                df['taker_sell_vol'] = df['volume'] - df['taker_buy_base_asset_volume']
                # Delta = 主动买 - 主动卖
                df['volume_delta'] = df['taker_buy_base_asset_volume'] - df['taker_sell_vol']
                # CVD = Delta的累加
                df['CVD'] = df['volume_delta'].cumsum()
                
                # 🔥 任务2: 波段级背离判定（10根K线窗口）
                # 旧逻辑：仅比较最近2根K线（单根噪音太大）
                # 新逻辑：在10根K线窗口内，价格创新高/新低 vs CVD趋势方向
                _cvd_window_slice = df.iloc[-_CVD_WINDOW:]
                _price_window = _cvd_window_slice['close']
                _cvd_window = _cvd_window_slice['CVD']
                
                # 价格在窗口内的最高/最低位置
                _price_high_idx = _price_window.idxmax()
                _price_low_idx = _price_window.idxmin()
                _price_at_start = _price_window.iloc[0]
                _price_at_end = _price_window.iloc[-1]
                _cvd_at_start = _cvd_window.iloc[0]
                _cvd_at_end = _cvd_window.iloc[-1]
                
                # 顶背离（波段级）：窗口内价格创新高（最高点在后半段）且 CVD 整体下降
                _price_making_high = (_price_at_end >= _price_at_start * 1.001) and (_price_high_idx >= _price_window.index[-_CVD_WINDOW // 2])
                _cvd_declining = (_cvd_at_end < _cvd_at_start)
                cvd_bearish_divergence = _price_making_high and _cvd_declining
                
                # 底背离（波段级）：窗口内价格创新低（最低点在后半段）且 CVD 整体上升
                _price_making_low = (_price_at_end <= _price_at_start * 0.999) and (_price_low_idx >= _price_window.index[-_CVD_WINDOW // 2])
                _cvd_rising = (_cvd_at_end > _cvd_at_start)
                cvd_bullish_divergence = _price_making_low and _cvd_rising
                
                # 🔥 CVD 斜率加速度分析：用 polyfit 量化 CVD 线性趋势 + 加速度
                # 如果价格斜率 > 0 且 CVD 斜率 < -0.3 且 CVD 呈加速下滑，赋予顶背离更高置信度
                _cvd_slope_boost = 1.0  # 默认置信度权重乘数
                if len(_cvd_window) >= 5:
                    try:
                        _recent_cvd_5 = _cvd_window.iloc[-5:].values.astype(np.float64)
                        _recent_price_5 = _price_window.iloc[-5:].values.astype(np.float64)
                        _cvd_slope = np.polyfit(range(5), _recent_cvd_5, 1)[0]
                        _price_slope = np.polyfit(range(5), _recent_price_5, 1)[0]
                        
                        # 计算 CVD 加速度（二阶差分）：最近3个CVD差值的斜率
                        _cvd_diffs = np.diff(_recent_cvd_5)  # 4个一阶差分
                        _cvd_accel = np.polyfit(range(len(_cvd_diffs)), _cvd_diffs, 1)[0] if len(_cvd_diffs) >= 2 else 0.0
                        
                        # 顶背离加速度增强：价格上行 + CVD 下行斜率 < -0.3 + CVD 加速下滑（accel < 0）
                        if _price_slope > 0 and _cvd_slope < -0.3 and _cvd_accel < 0:
                            _cvd_slope_boost = 1.5  # 高置信度：权重 ×1.5
                            cvd_bearish_divergence = True  # 强制激活顶背离
                            if not is_backtest_mode:
                                print(f"   🔬 [{symbol}] CVD斜率加速度增强: price_slope={_price_slope:.2f}, cvd_slope={_cvd_slope:.2f}, cvd_accel={_cvd_accel:.2f} → 顶背离置信度×1.5")
                        
                        # 底背离加速度增强：价格下行 + CVD 上行斜率 > 0.3 + CVD 加速上升（accel > 0）
                        if _price_slope < 0 and _cvd_slope > 0.3 and _cvd_accel > 0:
                            _cvd_slope_boost = 1.5
                            cvd_bullish_divergence = True
                            if not is_backtest_mode:
                                print(f"   🔬 [{symbol}] CVD斜率加速度增强: price_slope={_price_slope:.2f}, cvd_slope={_cvd_slope:.2f}, cvd_accel={_cvd_accel:.2f} → 底背离置信度×1.5")
                    except Exception as _slope_e:
                        if not is_backtest_mode:
                            print(f"   ⚠️ [{symbol}] CVD斜率计算异常: {_slope_e}")
                
                if not is_backtest_mode:
                    if cvd_bearish_divergence:
                        print(f"   🩸 [{symbol}] CVD波段顶背离({_CVD_WINDOW}根): 价格↑{(_price_at_end/_price_at_start-1)*100:.2f}% 但CVD↓{(_cvd_at_end-_cvd_at_start):.0f} (主力暗中出货) [boost={_cvd_slope_boost:.1f}x]")
                    elif cvd_bullish_divergence:
                        print(f"   🩸 [{symbol}] CVD波段底背离({_CVD_WINDOW}根): 价格↓{(1-_price_at_end/_price_at_start)*100:.2f}% 但CVD↑{(_cvd_at_end-_cvd_at_start):.0f} (主力暗中吸筹) [boost={_cvd_slope_boost:.1f}x]")
        except Exception as cvd_e:
            if not is_backtest_mode:
                print(f"   ⚠️ [{symbol}] CVD计算异常: {cvd_e}")
        
     
        # === 👁️ 智能资金透镜 (Smart Money Lens) ===
        # 🔥 v2.17 性能优化：STANDARD 回测模式跳过 Smart Money Lens 重计算
        # Volume Profile groupby + FVG shift + rolling(20) 每根K线都重算是 CPU 杀手
        # STANDARD 模式的入场逻辑不依赖这些指标，跳过可节省 ~60% 计算时间
        _skip_sml = (is_backtest_mode and mode_preset == 'STANDARD')
        
        # 1. 简易成交量轮廓 (Volume Profile - POC)
        # 🔥 性能优化：弃用 groupby，改用 np.histogram 向量化计算
        if not _skip_sml and len(df) >= 100:
            recent_df = df.tail(100)
            _typical_prices = (recent_df['high'].values + recent_df['low'].values + recent_df['close'].values) / 3.0
            _volumes = recent_df['volume'].values
            # 动态 bins：价格区间跨度 / ATR，下限 10 上限 50
            _price_range = _typical_prices.max() - _typical_prices.min()
            _current_atr_val = df['ATR'].iloc[-1] if 'ATR' in df.columns and not pd.isna(df['ATR'].iloc[-1]) else 1.0
            _dynamic_bins = max(10, min(50, int(_price_range / (_current_atr_val + 1e-10))))
            counts, bin_edges = np.histogram(_typical_prices, bins=_dynamic_bins, weights=_volumes)
            if len(counts) > 0 and counts.max() > 0:
                poc_price = float(bin_edges[np.argmax(counts)])
            else:
                poc_price = df['close'].iloc[-1]
        else:
            poc_price = df['close'].iloc[-1]

        df['POC'] = poc_price

        # 2 & 3: FVG + Liquidity Sweep（回测 STANDARD 模式跳过）
        if not _skip_sml:
            # 2. 合理价值缺口 (FVG - Fair Value Gap)
            # 🔥 FVG 填补判定 (Mitigation Check)
            # 原始 FVG 检测：第 i 根 K 线形成看涨 FVG 当且仅当 high[i-2] < low[i] 且 close[i-1] > open[i-1]
            _raw_bullish_fvg = (df['high'].shift(2) < df['low']) & (df['close'].shift(1) > df['open'].shift(1))
            df['bearish_fvg_active'] = (df['low'].shift(2) > df['high']) & (df['close'].shift(1) < df['open'].shift(1))

            # 看涨 FVG 填补判定：如果后续任何一根 K 线的 low 曾低于该 FVG 缺口的上沿（high[i-2]），
            # 则该 FVG 已被填补（mitigated），bullish_fvg_active 永久设为 False
            _bullish_fvg_mitigated = pd.Series(False, index=df.index)
            _fvg_upper_edge = df['high'].shift(2)  # FVG 缺口上沿 = 形成 FVG 那根 K 线往前第 2 根的 high

            for i in range(2, len(df)):
                if _raw_bullish_fvg.iloc[i]:
                    upper_edge = _fvg_upper_edge.iloc[i]
                    # 检查从 i+1 到末尾的所有 K 线，是否有 low <= upper_edge
                    if i + 1 < len(df):
                        subsequent_lows = df['low'].iloc[i + 1:]
                        if (subsequent_lows <= upper_edge).any():
                            _bullish_fvg_mitigated.iloc[i] = True

            df['bullish_fvg_active'] = _raw_bullish_fvg & ~_bullish_fvg_mitigated

            # 3. 流动性猎杀 (Liquidity Sweep / Stop Hunt)
            df['rolling_high_20'] = df['high'].shift(1).rolling(20).max()
            df['rolling_low_20'] = df['low'].shift(1).rolling(20).min()
            df['liq_sweep_high'] = (df['high'] > df['rolling_high_20']) & (df['close'] < df['rolling_high_20']) & (df['close'] < df['open'])
            df['liq_sweep_low'] = (df['low'] < df['rolling_low_20']) & (df['close'] > df['rolling_low_20']) & (df['close'] > df['open'])

        if not is_backtest_mode:
            # 日志输出 Smart Money Lens 状态
            _sml_poc = poc_price
            _sml_bull_fvg = closed_candle.get('bullish_fvg_active', False) if 'bullish_fvg_active' in df.columns else False
            _sml_bear_fvg = closed_candle.get('bearish_fvg_active', False) if 'bearish_fvg_active' in df.columns else False
            _sml_sweep_hi = closed_candle.get('liq_sweep_high', False) if 'liq_sweep_high' in df.columns else False
            _sml_sweep_lo = closed_candle.get('liq_sweep_low', False) if 'liq_sweep_low' in df.columns else False
            print(f"   👁️ [{symbol}] Smart Money Lens: POC={_sml_poc:.2f}, BullFVG={_sml_bull_fvg}, BearFVG={_sml_bear_fvg}, SweepHi={_sml_sweep_hi}, SweepLo={_sml_sweep_lo}")

        # 🔥 暴露 CVD 背离状态到 signals 字典
        signals['cvd_bearish_div'] = cvd_bearish_divergence
        signals['cvd_bullish_div'] = cvd_bullish_divergence
        
        # 🔥 任务2.1：暴露 ADX 值到 signals 字典
        signals['adx'] = adx_val
        
        if 'MACD_hist' in df.columns:
            hist_cross_up = closed_candle['MACD_hist'] > 0 and prev_closed_candle['MACD_hist'] <= 0
            hist_cross_down = closed_candle['MACD_hist'] < 0 and prev_closed_candle['MACD_hist'] >= 0
            
            # 🔥 增强 3: MACD 柱状图动量加速度（二阶导数）
            # 过滤 MACD 虽在零轴上方但已开始衰减的"尾巴信号"
            macd_accel = closed_candle['MACD_hist'] - prev_closed_candle['MACD_hist']
            macd_prev_accel = prev_closed_candle['MACD_hist'] - prev2_closed_candle['MACD_hist']
            # 做多要求：加速度为正（动量在增强）；做空要求：加速度为负
            macd_accel_long = (macd_accel > 0)
            macd_accel_short = (macd_accel < 0)
            
            # 🔥 增强 4: 成交量加权 EMA 斜率 (VEMA Slope)
            # 放量时 EMA 斜率权重更高，缩量时斜率不可信
            vema_slope = 0.0
            if 'EMA_TREND' in df.columns and len(df) >= 5:
                _ema_changes = df['EMA_TREND'].diff().tail(5)
                _vol_tail = df['volume'].tail(5)
                _vol_sum = _vol_tail.sum()
                if _vol_sum > 0:
                    _vol_weights = _vol_tail / _vol_sum
                    vema_slope = float((_ema_changes * _vol_weights).sum())
            
            if hist_cross_down:
                signals['signals'].append({
                    'type': 'SELL', 'action': 'EXIT_LONG',
                    'indicator': 'MACD', 'strength': 'STRONG',
                    'message': "平多信号：MACD_Hist向下穿越0轴"
                })
            if hist_cross_up:
                signals['signals'].append({
                    'type': 'BUY', 'action': 'EXIT_SHORT',
                    'indicator': 'MACD', 'strength': 'STRONG',
                    'message': "平空信号：MACD_Hist向上穿越0轴"
                })
            
            # 🔥 v3.0 量能过滤器：成交量必须 > 20均量 × VOLUME_SURGE_THRESHOLD
            avg_vol_20 = df['volume'].tail(20).mean() if len(df) >= 20 else df['volume'].mean()
            volume_surge_thr = cfg.get('VOLUME_SURGE_THRESHOLD', 1.5)
            volume_burst = closed_candle['volume'] > avg_vol_20 * volume_surge_thr
            
            # 🔥 入场阈值弹性化：EMA_TREND 斜率向上且放量时，允许 ADX >= 15 即入场
            ema_slope_up = False
            if 'EMA_TREND' in df.columns and len(df) >= 3:
                ema_current = closed_candle.get('EMA_TREND', 0)
                ema_prev = df.iloc[-2].get('EMA_TREND', 0)
                ema_slope_up = ema_current > ema_prev and not pd.isna(ema_current) and not pd.isna(ema_prev)
            # 🔥 SCALPER 模式：降低 ADX 强制过滤权重，仅需方向确认不强制强度
            if is_scalper_mode:
                # 狂战士模式：ADX 仅作为方向参考，不作为硬性门槛
                # 只要 MACD 方向一致 + EMA 方向确认即可（ADX  0 即通过）
                long_momentum = (is_price_above_ema and adx_val > 0)
                short_momentum = (is_price_below_ema and adx_val > 0)
            else:
                # 🔥 弹性入场：如果 EMA 斜率向上且放量，允许 ADX >= 18 即入场
                if ema_slope_up and volume_burst and adx_val >= 18:
                    long_momentum = is_price_above_ema
                    short_momentum = is_price_below_ema
                    if not is_backtest_mode:
                        print(f"   🔥 [{symbol}] 弹性入场激活: EMA斜率向上+放量, ADX={adx_val:.1f} >= 15")
                else:
                    # 🔥 Patch v9.1: 解除逻辑死锁 - 条件互补 + MACD 强制确认
                    # 只要价格在EMA同侧且MACD在零轴同侧发散，就是有效动能；或者ADX证明有强趋势
                    long_momentum = (is_price_above_ema and closed_candle['MACD_hist'] > 0) or (adx_val >= current_adx_thr)
                    short_momentum = (is_price_below_ema and closed_candle['MACD_hist'] < 0) or (adx_val >= current_adx_thr)
            
            if is_low_vol and (closed_candle['MACD_hist'] > prev_closed_candle['MACD_hist']) and \
               (prev_closed_candle['MACD_hist'] > prev2_closed_candle['MACD_hist']):
                long_momentum = True
            
            if is_low_vol and (closed_candle['MACD_hist'] < prev_closed_candle['MACD_hist']) and \
               (prev_closed_candle['MACD_hist'] < prev2_closed_candle['MACD_hist']):
                short_momentum = True
            
            vwap_val = closed_candle.get('VWAP', float('nan'))
            has_vwap = not pd.isna(vwap_val)
            squeeze_fired = closed_candle.get('Squeeze_Fired', False)
            rsi_val = closed_candle.get('RSI', 50)
            has_rsi = not pd.isna(rsi_val)
            
            # 🔥 Patch v9.2 "狂暴引擎": 简化动量逻辑，移除复杂 RSI 评分
            # 只保留基础的超买超卖边界检查（防止极端追涨杀跌）
            rsi_oversold = 20   # 极度超卖边界
            rsi_overbought = 80  # 极度超买边界
            
            # 🔥 Patch v9.2: 移除动态 RSI 弹性区间，使用固定边界
            # 狂暴引擎：只要不是极端超买超卖（RSI 20-80），就放行
            
            # === 👁️ 神之眼：L2 盘口深度失衡 (Order Book Imbalance) ===
            obi_pass_long = True
            obi_pass_short = True
            obi_value = 0.0
            
            if is_scalper_mode and cfg.get("OBI_FILTER_ENABLED", False):  # 👈 对接 TG 开关
                try:
                    # 获取实时买卖
                    if client is not None and not is_backtest_mode:
                        depth = client.futures_order_book(symbol=symbol, limit=10)
                        
                        # 计算买盘和卖盘的总挂单量
                        bid_vol = sum([float(b[1]) for b in depth['bids']])
                        ask_vol = sum([float(a[1]) for a in depth['asks']])
                        
                        if (bid_vol + ask_vol) > 0:
                            # OBI 公式: (买单量 - 卖单量) / 总量. 范围 [-1, 1]
                            obi_value = (bid_vol - ask_vol) / (bid_vol + ask_vol)
                            
                        # 🔥 Warrior-Scalper OBI 冰山拦截阈值: 0.45 (黄金中位)
                        # 0.6 太严格（过滤掉80%的行情），0.3 太松（噪音太多）
                        # 0.45 精准捕捉机构级买卖压力，同时保留足够的交易机会
                        OBI_ICEBERG_THRESHOLD = 0.45
                        
                        # 冰山拦截逻辑：如果卖盘冰山压顶，绝对禁止做多！
                        if obi_value < -OBI_ICEBERG_THRESHOLD:
                            obi_pass_long = False
                            if not is_backtest_mode:
                                print(f"   🧊 [{symbol}] OBI冰山拦截做多: OBI={obi_value:.3f} < -{OBI_ICEBERG_THRESHOLD} (卖盘压顶)")
                            
                        # 冰山拦截逻辑：如果买盘铁板托底，绝对禁止做空！
                        if obi_value > OBI_ICEBERG_THRESHOLD:
                            obi_pass_short = False
                            if not is_backtest_mode:
                                print(f"   🧊 [{symbol}] OBI冰山拦截做空: OBI={obi_value:.3f} > {OBI_ICEBERG_THRESHOLD} (买盘托底)")
                        
                        if not is_backtest_mode and obi_pass_long and obi_pass_short:
                            print(f"   👁️ [{symbol}] OBI盘口均衡: OBI={obi_value:.3f}, 双向放行")
                        
                except Exception as e:
                    if not is_backtest_mode:
                        print(f"   ⚠️ [{symbol}] L2 Depth Fetch Error: {e}")
                    pass  # 容错：如果抓取失败，不阻断交易
            
            # === 🩸 衍生品雷达：OI 与资金费率 (Derivatives Context) ===
            derivatives_data = {'oi': 0.0, 'funding_rate': 0.0}
            if client is not None and not is_backtest_mode:
                derivatives_data = get_derivatives_data(client, symbol)
            
            funding_rate = derivatives_data['funding_rate']
            open_interest = derivatives_data['oi']
            
            # 将衍生品数据暴露给 signals 字典供下游或日志使用
            signals['funding_rate'] = funding_rate
            signals['open_interest'] = open_interest
            
            # 极值预警日志
            if not is_backtest_mode:
                if funding_rate > 0.001:  # 资金费率过高，散户疯狂做多
                    print(f"   🔥 [{symbol}] 衍生品预警: 资金费率极高 ({funding_rate*100:.4f}%), 谨防多头踩踏插针!")
                elif funding_rate < -0.001:  # 资金费率极低，散户疯狂做空
                    print(f"   🧊 [{symbol}] 衍生品预警: 资金费率极负 ({funding_rate*100:.4f}%), 谨防空头轧空爆拉!")
            
            # === 📈 跨币种轮动雷达：RS 相对强弱 (Relative Strength) ===
            rs_value = 0.0
            if client is not None and not is_backtest_mode and symbol != 'BTCUSDT':
                rs_value = get_relative_strength(client, symbol)
            
            # 暴露给下游信号字典，供 AI 统帅调度时做资金分配参考
            signals['rs_value'] = rs_value
            
            # RS 极值预警日志
            if not is_backtest_mode and symbol != 'BTCUSDT':
                if rs_value > 5.0:
                    print(f"   🐉 [{symbol}] RS轮动预警: 跑赢大盘 {rs_value:.2f}% (强势龙头, 优先做多)")
                elif rs_value < -5.0:
                    print(f"   🥀 [{symbol}] RS轮动预警: 跑输大盘 {rs_value:.2f}% (弱势标的, 优先做空)")
            
            # ==========================================
            # 👑 狂战士终极状态机 (SCALPER MASTER STATE)
            # ==========================================
            is_scalp_short = False
            is_scalp_long = False
            scalp_msg = ""
            is_maker_only = False  # 是否强制挂单
            
            # 🔥 预计算核心透镜数据（所有优先级共用）
            poc_val = closed_candle.get('POC', closed_candle['close'])
            is_fvg_bearish = bool(closed_candle.get('bearish_fvg_active', False))
            is_fvg_bullish = bool(closed_candle.get('bullish_fvg_active', False))
            is_sweep_high = bool(closed_candle.get('liq_sweep_high', False))
            is_sweep_low = bool(closed_candle.get('liq_sweep_low', False))
            current_atr = closed_candle.get('ATR', 0)
            
            # Wick 数据（SAR 用）
            body = abs(closed_candle['close'] - closed_candle['open'])
            upper_wick = closed_candle['high'] - max(closed_candle['open'], closed_candle['close'])
            lower_wick = min(closed_candle['open'], closed_candle['close']) - closed_candle['low']
            
            if is_scalper_mode and has_rsi:
                # 1. 环境感知 (Regime Detection)
                is_strong_trend = (adx_val >= 30)
                is_chop = (adx_val < 25)
                
                # 3. 优先级 1：核弹级订单流反转 (CVD 背离 / 扫损) -> 无视趋势，立刻市价抢单 (Maker=False)
                if cvd_bearish_divergence or is_sweep_high:
                    is_scalp_short = True
                    scalp_msg = "🩸 订单流刺杀: 庄家扫损/CVD背离，市价重锤开空！"
                    is_maker_only = False
                    if not is_backtest_mode:
                        print(f"   🩸 [{symbol}] P1-订单流刺杀空: CVD顶背离={cvd_bearish_divergence}, SweepHi={is_sweep_high}, RSI={rsi_val:.1f}")
                elif cvd_bullish_divergence or is_sweep_low:
                    is_scalp_long = True
                    scalp_msg = "🩸 订单流刺杀: 庄家扫损/CVD背离，市价重锤接多！"
                    is_maker_only = False
                    if not is_backtest_mode:
                        print(f"   🩸 [{symbol}] P1-订单流刺杀多: CVD底背离={cvd_bullish_divergence}, SweepLo={is_sweep_low}, RSI={rsi_val:.1f}")
                    
                # 4. 优先级 2：顺势冲浪 (Trend Surfing) -> 仅在强趋势中执行
                elif is_strong_trend:
                    if is_price_above_ema and rsi_val < 55 and closed_candle['MACD_hist'] > prev_closed_candle['MACD_hist']:
                        is_scalp_long = True
                        scalp_msg = "🌪️ 疯牛冲浪: 回踩结束，顺势接多！"
                        is_maker_only = True
                        if not is_backtest_mode:
                            print(f"   🌪️ [{symbol}] P2-顺势冲浪多: ADX={adx_val:.1f}>=30, 价格>EMA, RSI={rsi_val:.1f}<55, MACD_hist递增")
                    elif is_price_below_ema and rsi_val > 45 and closed_candle['MACD_hist'] < prev_closed_candle['MACD_hist']:
                        is_scalp_short = True
                        scalp_msg = "🌪️ 疯熊冲浪: 反弹结束，顺势做空！"
                        is_maker_only = True
                        if not is_backtest_mode:
                            print(f"   🌪️ [{symbol}] P2-顺势冲浪空: ADX={adx_val:.1f}>=30, 价格<EMA, RSI={rsi_val:.1f}>45, MACD_hist递减")
                    
                # 5. 优先级 3：震荡乒乓 (Ping-Pong) -> 仅在无趋势中执行
                # 🔥 RSI Recovery: Combat Zone - 28/72 机构甜蜜点
                # 25/75 对1m太慢（错过快速反转），30/70 太紧（噪音太多）
                # 28/72 是机构级快速剥头皮的最佳平衡点
                elif is_chop:
                    # 🔥 增强 5: StochRSI 精确入场（替代简单 RSI 28/72）
                    _srsi_k = closed_candle.get('StochRSI_K', float('nan'))
                    _srsi_d = closed_candle.get('StochRSI_D', float('nan'))
                    _prev_srsi_k = prev_closed_candle.get('StochRSI_K', float('nan'))
                    _prev_srsi_d = prev_closed_candle.get('StochRSI_D', float('nan'))
                    _has_stoch_rsi = not (pd.isna(_srsi_k) or pd.isna(_srsi_d) or pd.isna(_prev_srsi_k) or pd.isna(_prev_srsi_d))
                    
                    # 做空条件：StochRSI_K > 80 且 K 下穿 D（死叉）+ MACD_hist 衰减
                    if _has_stoch_rsi:
                        _srsi_death_cross = (_srsi_k < _srsi_d) and (_prev_srsi_k >= _prev_srsi_d)
                        _srsi_golden_cross = (_srsi_k > _srsi_d) and (_prev_srsi_k <= _prev_srsi_d)
                    else:
                        _srsi_death_cross = False
                        _srsi_golden_cross = False
                    
                    _short_trigger = (_has_stoch_rsi and _srsi_k > 80 and _srsi_death_cross) or (not _has_stoch_rsi and rsi_val > 72)
                    _long_trigger = (_has_stoch_rsi and _srsi_k < 20 and _srsi_golden_cross) or (not _has_stoch_rsi and rsi_val < 28)
                    
                    if _short_trigger and closed_candle['MACD_hist'] < prev_closed_candle['MACD_hist']:
                        is_scalp_short = True
                        scalp_msg = "🚀 震荡乒乓: 箱体顶部，开空！"
                        is_maker_only = True
                        if not is_backtest_mode:
                            _srsi_info = f"StochRSI_K={_srsi_k:.1f}" if _has_stoch_rsi else f"RSI={rsi_val:.1f}"
                            print(f"   🏓 [{symbol}] P3-乒乓顶部空: ADX={adx_val:.1f}<25, {_srsi_info}, MACD_hist衰减")
                    elif _long_trigger and closed_candle['MACD_hist'] > prev_closed_candle['MACD_hist']:
                        is_scalp_long = True
                        scalp_msg = "🚀 震荡乒乓: 箱体底部，开多！"
                        is_maker_only = True
                        if not is_backtest_mode:
                            _srsi_info = f"StochRSI_K={_srsi_k:.1f}" if _has_stoch_rsi else f"RSI={rsi_val:.1f}"
                            print(f"   🏓 [{symbol}] P3-乒乓底部多: ADX={adx_val:.1f}<25, {_srsi_info}, MACD_hist回升")
                    
                # 6. 🛡️ 互斥锁与 OBI 冰山过滤
                if is_scalp_short and is_scalp_long:
                    is_scalp_short = False
                    is_scalp_long = False
                    print(f"⚠️ [{symbol}] 互斥锁: 多空冲突，强制阻断！")
                    
                if is_scalp_short and obi_value > 0.4:
                    is_scalp_short = False
                    if not is_backtest_mode:
                        print(f"⚠️ [{symbol}] OBI拦截: 底部买盘冰山托底(OBI={obi_value:.3f}>0.4)，禁止做空！")
                    
                if is_scalp_long and obi_value < -0.4:
                    is_scalp_long = False
                    if not is_backtest_mode:
                        print(f"⚠️ [{symbol}] OBI拦截: 顶部卖盘冰山压顶(OBI={obi_value:.3f}<-0.4)，禁止做多！")
            
            # --- 狂战士状态机信号输出 ---
            # 🔥 兼容下游变量（保持 is_extreme_reversal_* 接口不变）
            is_extreme_reversal_short = is_scalp_short
            is_extreme_reversal_long = is_scalp_long
            is_ping_pong_active = False  # 已内化到状态机，不再单独暴露
            is_trend_surfing = False     # 已内化到状态机，不再单独暴露

            if is_scalp_short:
                signals['signals'].append({
                    'type': 'SELL', 'action': 'EXIT_LONG', 'indicator': 'SMART_SCALPER', 'strength': 'STRONG',
                    'message': "⚔️ 极速反手：多单全部落袋！"
                })
                signals['signals'].append({
                    'type': 'SELL', 'action': 'ENTRY', 'indicator': 'SMART_SCALPER', 'strength': 'STRONG',
                    'message': scalp_msg,
                    'is_maker_only': is_maker_only
                })
                if not is_backtest_mode:
                    print(f"   👑 [{symbol}] 状态机输出: SHORT | Maker={is_maker_only} | {scalp_msg}")
                
            if is_scalp_long:
                signals['signals'].append({
                    'type': 'BUY', 'action': 'EXIT_SHORT', 'indicator': 'SMART_SCALPER', 'strength': 'STRONG',
                    'message': "⚔️ 极速反手：空单全部落袋！"
                })
                signals['signals'].append({
                    'type': 'BUY', 'action': 'ENTRY', 'indicator': 'SMART_SCALPER', 'strength': 'STRONG',
                    'message': scalp_msg,
                    'is_maker_only': is_maker_only
                })
                if not is_backtest_mode:
                    print(f"   👑 [{symbol}] 状态机输出: LONG | Maker={is_maker_only} | {scalp_msg}")
            
            # ====== 🔥 防守补丁 C：空间锁增强版（Price Volatility + Volume Filter）======
            # 计算当前信号 K 线的实体长度（绝对值）
            candle_body = abs(closed_candle['close'] - closed_candle['open'])
            current_atr = closed_candle.get('ATR', 0)
            
            # 从策略预设中读取 MAX_CANDLE_BODY_ATR（空间锁阈值）
            # 🔥 v3.3: 临时强制改为 5.0，防止大实体K线被空间锁拦截
            # 🔥 解除空间锁参数硬编码：优先读取传入的动态参数
            max_candle_body_atr = cfg.get("MAX_CANDLE_BODY_ATR", preset_config.get("MAX_CANDLE_BODY_ATR", 2.0))
            
            # 🔥 动态空间锁矩阵：波动率越大，允许的入场实体空间越宽
            rel_atr = closed_candle.get('Relative_ATR', 1.0)
            max_candle_body_atr = max_candle_body_atr * (1 + rel_atr / 4)
            
            # 🔥 空间锁动态扩容：疯狗模式激活时自动上浮50%
            _is_mad_dog = config.SYSTEM_CONFIG.get("FORCE_MAD_DOG_MODE", False) or \
                          (config.SYSTEM_CONFIG.get("MAD_DOG_MODE", False) and config.SYSTEM_CONFIG.get("MAD_DOG_TRIGGER", 1.3) > 0)
            if _is_mad_dog:
                max_candle_body_atr = max_candle_body_atr * 1.5
                if not is_backtest_mode:
                    print(f"   🔥 [{symbol}] 空间锁动态扩容: {preset_config.get('MAX_CANDLE_BODY_ATR', 2.5):.2f} → {max_candle_body_atr:.2f}")
            
            # 🔥 v3.0 空间锁增强：成交量过滤（Volume Ratio验证）
            space_lock_enabled = config.SYSTEM_CONFIG.get("SPACE_LOCK_ENABLED", True)
            space_lock_triggered = False
            volume_breakout = False
            
            if current_atr > 0 and candle_body > (current_atr * max_candle_body_atr):
                if space_lock_enabled:
                    # 计算成交量比率：当前成交量 / 过去20根K线平均成交量
                    volume_ratio = 1.0
                    if len(df) >= 21:
                        # 获取过去20根K线的平均成交量（不包括当前K线）
                        past_20_volumes = df.iloc[-21:-1]['volume']
                        avg_volume_20 = past_20_volumes.mean()
                        current_volume = closed_candle.get('volume', 0)
                        
                        if avg_volume_20 > 0:
                            volume_ratio = current_volume / avg_volume_20
                        
                        if not is_backtest_mode:
                            print(f"   📊 [{symbol}] 成交量分析: 当前={current_volume:.2f}, 20均={avg_volume_20:.2f}, 比率={volume_ratio:.2f}x")
                    
                    # 判定逻辑：
                    # 1. 实体超限 + 成交量比率 > 2.0 → 有效突破，放行信号
                    # 2. 实体超限 + 缩量（比率 <= 2.0）→ 维持拦截
                    volume_breakout_threshold = config.SYSTEM_CONFIG.get("VOLUME_BREAKOUT_RATIO", 2.0)
                    
                    if volume_ratio > volume_breakout_threshold:
                        # 有效突破：放量突破，放行信号
                        volume_breakout = True
                        space_lock_triggered = False
                        space_lock_ratio = candle_body / current_atr
                        print(f"   ✅ [{symbol}] 空间锁豁免：实体超限但放量突破！")
                        print(f"      实体/ATR={space_lock_ratio:.2f}, 成交量比率={volume_ratio:.2f}x > {volume_breakout_threshold}x")
                        print(f"      判定为有效突破，放行信号")
                    else:
                        # 缩量拉升/砸盘：维持拦截（SCALPER模式豁免）
                        if not is_scalper_mode:
                            space_lock_triggered = True
                        space_lock_ratio = candle_body / current_atr
                        if not is_backtest_mode:
                            print(f"   🔒 [{symbol}] 空间锁触发！K线实体={candle_body:.4f} > ATR({current_atr:.4f}) * {max_candle_body_atr} = {current_atr * max_candle_body_atr:.4f}")
                            print(f"      实体/ATR比率={space_lock_ratio:.2f}, 成交量比率={volume_ratio:.2f}x <= {volume_breakout_threshold}x")
                            print(f"      判定为缩量情绪化拉升/砸盘，强制拦截信号")
                else:
                    # 空间锁已关闭（狂战士高频模式），仅记录日志不拦截
                    space_lock_ratio = candle_body / current_atr
                    print(f"   ⚡ [{symbol}] 空间锁已关闭（狂战士模式），放行高波动信号 | 实体/ATR={space_lock_ratio:.2f}")
            
            # ====== 防守补丁 B：防骗线时间锁 ======
            # MACD 穿越后要求连续 N 根 K 线保持同侧，防止假突破立即回撤
            # SCALPER 模式：即发即开，无需确认
            if is_scalper_mode:
                confirm_bars = config.STRATEGY_PRESETS["SCALPER"].get("SIGNAL_CONFIRM_BARS", 0)
            else:
                confirm_bars = preset_config.get("SIGNAL_CONFIRM_BARS", config.SYSTEM_CONFIG.get("SIGNAL_CONFIRM_BARS", 2))
            
            # ====== 🔥 Fix #4: 防骗线时间锁 — confirm_bars 实际过滤 ======
            # MACD 穿越后要求连续 N 根 K 线保持同侧，防止假突破立即回撤
            # confirm_bars_long_ok / confirm_bars_short_ok 供下游入场信号使用
            confirm_bars_long_ok = True
            confirm_bars_short_ok = True
            if confirm_bars > 0 and 'MACD_hist' in df.columns and len(df) >= confirm_bars + 1:
                # 做多确认：最近 confirm_bars 根 K 线的 MACD_hist 必须全部 > 0
                _recent_hist = df['MACD_hist'].iloc[-(confirm_bars + 1):-1]  # 不含当前K线
                confirm_bars_long_ok = bool((_recent_hist > 0).all())
                # 做空确认：最近 confirm_bars 根 K 线的 MACD_hist 必须全部 < 0
                confirm_bars_short_ok = bool((_recent_hist < 0).all())
                if not is_backtest_mode:
                    if not confirm_bars_long_ok and not confirm_bars_short_ok:
                        print(f"   🔒 [{symbol}] 防骗线时间锁: MACD_hist 未连续 {confirm_bars} 根保持同侧，多空均被拦截")
                    elif not confirm_bars_long_ok:
                        print(f"   🔒 [{symbol}] 防骗线时间锁: MACD_hist 未连续 {confirm_bars} 根 > 0，做多被拦截")
                    elif not confirm_bars_short_ok:
                        print(f"   🔒 [{symbol}] 防骗线时间锁: MACD_hist 未连续 {confirm_bars} 根 < 0，做空被拦截")
            
            # ====== 🔥 MTF多周期共振对齐检查 ======
            mtf_aligned = True
            mtf_reason = ""
            higher_tf_ema = None
            
            if preset_config.get("USE_HIGHER_TF_FILTER", False) and not is_scalper_mode:
                # 🔥 v2.8: 回测模式传入 custom_config 和 mtf_data
                higher_tf_ema = _fetch_higher_tf_ema(client, symbol, custom_config=custom_config, mtf_data=mtf_data)
                if higher_tf_ema is not None:
                    # 🔥 回测模式静默日志
                    if custom_config is None:
                        print(f"   📊 [{symbol}] 高周期EMA: {higher_tf_ema:.4f}")
                    else:
                        logger.debug(f"   📊 [BACKTEST] [{symbol}] 高周期EMA: {higher_tf_ema:.4f}")
            
            # ==========================================
            # 🔥 激进双擎状态机 (AGGRESSIVE DUAL-DRIVE: Left & Right)
            # ==========================================
            is_aggressive_mode = (mode_preset == "AGGRESSIVE")
            is_agg_short = False
            is_agg_long = False
            agg_msg = ""
            
            if is_aggressive_mode and not is_scalper_mode:
                # 预获取 MTF 状态
                mtf_aligned_long = False
                mtf_aligned_short = False
                if higher_tf_ema is not None:
                    mtf_aligned_long, _ = is_mtf_aligned(closed_candle['close'], higher_tf_ema, 'BUY')
                    mtf_aligned_short, _ = is_mtf_aligned(closed_candle['close'], higher_tf_ema, 'SELL')
                elif is_backtest_mode:
                    mtf_aligned_long, mtf_aligned_short = True, True
                    
                vwap_bullish = closed_candle['close'] > vwap_val if has_vwap else True
                vwap_bearish = closed_candle['close'] < vwap_val if has_vwap else True

                # ==========================================
                # 引擎 A: 左侧刺客 (Smart Money Reversal) - 高频抓假突破
                # ==========================================
                # 多头抄底：向下扫损 或 踩入看涨FVG + CVD底背离支撑
                if (is_sweep_low or is_fvg_bullish) and cvd_bullish_divergence:
                    # 左侧交易不需要看 MACD 方向，只要不极度超买，且 OBI 不压顶
                    if rsi_val < 50 and obi_pass_long:
                        is_agg_long = True
                        agg_msg = f"💉 左侧抄底(多): 庄家向下扫损/FVG + CVD底背离确认! 极限反转"
                
                # 空头摸顶：向上扫损 或 刺入看跌FVG + CVD顶背离压制
                if (is_sweep_high or is_fvg_bearish) and cvd_bearish_divergence:
                    if rsi_val > 50 and obi_pass_short:
                        is_agg_short = True
                        agg_msg = f"💉 左侧摸顶(空): 庄家向上扫损/FVG + CVD顶背离确认! 极限反转"

                # ==========================================
                # 引擎 B: 右侧重炮 (Volatility Breakout) - 低频抓大趋势
                # ==========================================
                is_volatility_breakout = closed_candle.get('Squeeze_Fired', False) or (adx_val > 35)
                
                # 如果左侧没触发，检查右侧真突破
                if not is_agg_long and is_volatility_breakout and volume_burst and mtf_aligned_long and vwap_bullish:
                    if (closed_candle['MACD_hist'] > 0 and closed_candle['MACD_hist'] > prev_closed_candle['MACD_hist']) or hist_cross_up:
                        if rsi_val < 75 and not space_lock_triggered and obi_pass_long:
                            is_agg_long = True
                            agg_msg = f"🚀 右侧爆破(多): Squeeze释放+量能爆发! ADX={adx_val:.1f}, 顺势跟进"
                            
                if not is_agg_short and is_volatility_breakout and volume_burst and mtf_aligned_short and vwap_bearish:
                    if (closed_candle['MACD_hist'] < 0 and closed_candle['MACD_hist'] < prev_closed_candle['MACD_hist']) or hist_cross_down:
                        if rsi_val > 25 and not space_lock_triggered and obi_pass_short:
                            is_agg_short = True
                            agg_msg = f"☄️ 右侧爆破(空): Squeeze释放+量能爆发! ADX={adx_val:.1f}, 顺势跟进"

                # 互斥与输出拦截
                if is_agg_long and is_agg_short:
                    is_agg_long, is_agg_short = False, False
                    print(f"⚠️ [{symbol}] 激进双擎互斥锁: 左右侧信号冲突，强制阻断！")

                if is_agg_long:
                    signals['signals'].append({'type': 'BUY', 'action': 'ENTRY', 'indicator': 'AGGRESSIVE_MASTER', 'strength': 'STRONG', 'message': agg_msg, 'is_maker_only': False})
                    if not is_backtest_mode: print(f"   🔥 [{symbol}] 激进双擎输出: LONG | {agg_msg}")
                    
                if is_agg_short:
                    signals['signals'].append({'type': 'SELL', 'action': 'ENTRY', 'indicator': 'AGGRESSIVE_MASTER', 'strength': 'STRONG', 'message': agg_msg, 'is_maker_only': False})
                    if not is_backtest_mode: print(f"   🔥 [{symbol}] 激进双擎输出: SHORT | {agg_msg}")
            
            # ==========================================
            # 🚜 1h 趋势装甲车 (STANDARD / RS Momentum Rider)
            # ==========================================
            is_standard_mode = (mode_preset == "STANDARD")
            
            if is_standard_mode and not is_scalper_mode and not is_aggressive_mode:
                # 🔥 修复 1：动态读取 ADX 参数，而不是写死 25
                adx_thr_dynamic = cfg.get("ADX_THR", 25)
                is_trend_established = (adx_val >= adx_thr_dynamic)
                vwap_bullish = closed_candle['close'] > vwap_val if has_vwap else True
                vwap_bearish = closed_candle['close'] < vwap_val if has_vwap else True
                
                # 🔥 修复 2：未开启 RS_FILTER 时无条件通过，回测模式也豁免
                is_rs_bullish = (rs_value > 2.0) or not cfg.get("RS_FILTER_ENABLED", False)
                is_rs_bearish = (rs_value < -2.0) or not cfg.get("RS_FILTER_ENABLED", False)
                
                # 做多：多头排列 + 绝对强势龙一 (回测豁免) + 资金费率未过热
                if is_trend_established and is_price_above_ema and vwap_bullish:
                    if is_rs_bullish and funding_rate < 0.001 and obi_pass_long:
                        if not space_lock_triggered and confirm_bars_long_ok:
                            signals['signals'].append({
                                'type': 'BUY', 'action': 'ENTRY', 'indicator': 'STANDARD_MASTER', 'strength': 'STRONG',
                                'message': f"🚀 趋势装甲(多): 强势确立(ADX={adx_val:.1f}) + 龙一标的(RS={rs_value:.1f}%)"
                            })
                            if not is_backtest_mode: print(f"   🚜 [{symbol}] 趋势装甲输出: LONG | RS={rs_value:.1f}%")
                            
                # 做空：空头排列 + 绝对弱势垃圾 (回测豁免) + 资金费率未极负
                if is_trend_established and is_price_below_ema and vwap_bearish:
                    if is_rs_bearish and funding_rate > -0.001 and obi_pass_short:
                        if not space_lock_triggered and confirm_bars_short_ok:
                            signals['signals'].append({
                                'type': 'SELL', 'action': 'ENTRY', 'indicator': 'STANDARD_MASTER', 'strength': 'STRONG',
                                'message': f"☄️ 趋势装甲(空): 跌势确立(ADX={adx_val:.1f}) + 弱势标的(RS={rs_value:.1f}%)"
                            })
                            if not is_backtest_mode: print(f"   🚜 [{symbol}] 趋势装甲输出: SHORT | RS={rs_value:.1f}%")

            # ==========================================
            # ⚓ 1d  (CONSERVATIVE / Wyckoff Accumulation)
            # ==========================================
            is_conservative_mode = (mode_preset == "CONSERVATIVE")
            
            if is_conservative_mode:
                # 核心逻辑：专抓日线级别的 恐慌底 (Spring) 和 狂热顶 (Climax)
                
                # 抓大底 (Spring)：价格低于 200日均线，且 RSI极度超卖，且 CVD 出现底背离（主力暗中吃货）
                is_deep_value = daily_ema_200 is not None and closed_candle['close'] < daily_ema_200
                if is_deep_value and rsi_val < 35 and cvd_bullish_divergence:
                    # 如果叠加了放量，说明恐慌盘被全部接走
                    if volume_burst and obi_pass_long:
                        signals['signals'].append({
                            'type': 'BUY', 'action': 'ENTRY', 'indicator': 'CONSERVATIVE_MASTER', 'strength': 'STRONG',
                            'message': f"🐳 威科夫探底(多): 极度超卖(RSI={rsi_val:.1f}) + CVD底背离 + 天量爆量!"
                        })
                        if not is_backtest_mode: print(f"   ⚓ [{symbol}] 核潜艇输出: LONG (巨鲸吸筹底)")
                
                # 抓大顶 (Climax)：价格远高于 200日均线，RSI极度超买，且 CVD 出现顶背离（主力暗中派发）
                is_over_extended = daily_ema_200 is not None and closed_candle['close'] > (daily_ema_200 * 1.2) # 偏离200均线20%
                if is_over_extended and rsi_val > 75 and cvd_bearish_divergence:
                    if volume_burst and obi_pass_short:
                        signals['signals'].append({
                            'type': 'SELL', 'action': 'ENTRY', 'indicator': 'CONSERVATIVE_MASTER', 'strength': 'STRONG',
                            'message': f"🌋 威科夫派发(空): 极度超买(RSI={rsi_val:.1f}) + 偏离均线 + CVD顶背离!"
                        })
                        if not is_backtest_mode: print(f"   ⚓ [{symbol}] 核潜艇输出: SHORT (巨鲸派发顶)")

        # 🎛️ 暴露 SML 状态到 signals 字典，供下游 SML_BOOSTER 使用
        signals['sml_boost_long'] = is_fvg_bullish or is_sweep_low
        signals['sml_boost_short'] = is_fvg_bearish or is_sweep_high
        
        return signals if signals['signals'] else None
    except Exception as e:
        print(f"⚠️ 生成交易信号失败: {e}")
        return None

# ==========================================
# 绩效统计（凯利公式基础数据）
# ==========================================

def get_performance_stats(lookback=50, strategy_mode=None):
    """
    从交易历史中提取绩效统计数据（含小样本防护 + 凯利公式平滑处理 + 策略模式隔离）
    
    🔥 v3.0 新增：加权平滑防止参数坍塌
    - 将本次统计结果与历史固定基准值按 0.7:0.3 比例加权融合
    - 防止单笔极端回撤导致凯利系数归零
    
    🔥 v4.0 新增：策略模式隔离
    - 支持按 strategy_mode 过滤交易记录
    - 防止剥头皮高胜率低盈亏比数据污染趋势策略的凯利系数
    
    Args:
        lookback: 回溯交易笔数（默认 50 笔）
        strategy_mode: 策略模式过滤（可选，如 'STANDARD', 'SCALPER' 等）
    
    Returns:
        dict: {
            'win_rate': 胜率 (W),
            'profit_loss_ratio': 盈亏比 (R),
            'kelly_factor': 半凯利系数,
            'sample_size': 样本数量,
            'smoothed': 是否应用了平滑处理,
            'strategy_mode': 过滤的策略模式
        }
    """
    
    # 🔥 历史固定基准值（用于平滑融合）
    BASELINE_WIN_RATE = 0.45  
    BASELINE_PROFIT_LOSS_RATIO = 1.8  
    SMOOTH_WEIGHT_CURRENT = 0.6  
    SMOOTH_WEIGHT_BASELINE = 0.4  
    
    try:
        # 🔒 线程锁保护：读取 config.TRADE_HISTORY（防止并发平仓时数据不一致）
        with state_lock:
            # 获取最近 N 笔已平仓交易
            if not config.TRADE_HISTORY or len(config.TRADE_HISTORY) == 0:
                # 无历史数据，返回保守默认值
                return {
                    'win_rate': BASELINE_WIN_RATE,
                    'profit_loss_ratio': BASELINE_PROFIT_LOSS_RATIO,
                    'kelly_factor': 1.0,  # 使用原始 RISK_RATIO
                    'sample_size': 0,
                    'smoothed': False,
                    'strategy_mode': strategy_mode
                }
            
            # 🔥 策略模式过滤：只统计指定策略的交易
            if strategy_mode:
                filtered_trades = [
                    t for t in config.TRADE_HISTORY 
                    if t.get('strategy_mode') == strategy_mode
                ]
                if not filtered_trades:
                    print(f"   ⚠️ 策略模式 {strategy_mode} 无历史交易，使用保守默认值")
                    return {
                        'win_rate': BASELINE_WIN_RATE,
                        'profit_loss_ratio': BASELINE_PROFIT_LOSS_RATIO,
                        'kelly_factor': 0.1,  # 小样本强制保守
                        'sample_size': 0,
                        'smoothed': False,
                        'strategy_mode': strategy_mode
                    }
                recent_trades = filtered_trades[-lookback:] if len(filtered_trades) >= lookback else filtered_trades
            else:
                recent_trades = config.TRADE_HISTORY[-lookback:] if len(config.TRADE_HISTORY) >= lookback else config.TRADE_HISTORY
        
        wins = []
        losses = []
        
        for trade in recent_trades:
            pnl = trade.get('pnl', 0) or trade.get('net_pnl', 0)
            if pnl > 0:
                wins.append(pnl)
            elif pnl < 0:
                losses.append(abs(pnl))
        
        total_trades = len(wins) + len(losses)
        
        if total_trades == 0:
            return {
                'win_rate': BASELINE_WIN_RATE,
                'profit_loss_ratio': BASELINE_PROFIT_LOSS_RATIO,
                'kelly_factor': 1.0,
                'sample_size': 0,
                'smoothed': False,
                'strategy_mode': strategy_mode
            }
        
        # 🔥 小样本防护：样本量 < 10 时强制返回保守 Kelly=0.1
        # 防止初期 1-2 笔偶然连胜/连败导致盈亏比 R 计算扭曲，引发仓位管理失控
        MIN_SAMPLE_SIZE = 10
        if total_trades < MIN_SAMPLE_SIZE:
            print(f"   ⚠️ 策略 {strategy_mode or 'ALL'} 样本量不足 ({total_trades} < {MIN_SAMPLE_SIZE})，Kelly 强制回退 0.1")
            return {
                'win_rate': len(wins) / total_trades if total_trades > 0 else BASELINE_WIN_RATE,
                'profit_loss_ratio': BASELINE_PROFIT_LOSS_RATIO,  # 保守默认值
                'kelly_factor': 0.1,  # 🔥 小样本强制保守，防止满仓豪赌
                'sample_size': total_trades,
                'smoothed': False,
                'strategy_mode': strategy_mode
            }
        
        # 计算原始胜率 (W)
        win_rate_raw = len(wins) / total_trades if total_trades > 0 else BASELINE_WIN_RATE
        
        # 计算原始盈亏比 (R) = 平均盈利 / 平均亏损
        avg_win = sum(wins) / len(wins) if wins else 0
        avg_loss = sum(losses) / len(losses) if losses else 1
        profit_loss_ratio_raw = avg_win / avg_loss if avg_loss > 0 else BASELINE_PROFIT_LOSS_RATIO
        
        # 🔥 v3.0 加权平滑处理：防止参数坍塌
        # 将本次统计结果与历史固定基准值按 0.7:0.3 比例加权融合
        win_rate_smoothed = (
            SMOOTH_WEIGHT_CURRENT * win_rate_raw + 
            SMOOTH_WEIGHT_BASELINE * BASELINE_WIN_RATE
        )
        
        profit_loss_ratio_smoothed = (
            SMOOTH_WEIGHT_CURRENT * profit_loss_ratio_raw + 
            SMOOTH_WEIGHT_BASELINE * BASELINE_PROFIT_LOSS_RATIO
        )
        
        # 使用平滑后的参数计算凯利系数
        win_rate = win_rate_smoothed
        profit_loss_ratio = profit_loss_ratio_smoothed
        
        # 半凯利公式: f* = 0.5 * (W * R - (1 - W)) / R
        # 防御性处理：确保分母不为 0
        if profit_loss_ratio > 0:
            kelly_raw = 0.5 * (win_rate * profit_loss_ratio - (1 - win_rate)) / profit_loss_ratio
        else:
            kelly_raw = 0.5
        
        # 强制限制在 0.3到 1.2 倍之间（数学稳健性）
        kelly_raw = max(0.3, min(1.2, kelly_raw))
        
        # 🔥 新增：渐进式平滑处理（防配资突变）
        if total_trades < 10:
            kelly_factor = 0.5  # 极度保守
        elif total_trades < 30:
            # 线性插值平滑过渡
            progress = (total_trades - 10) / 20.0
            kelly_factor = 0.5 + progress * (kelly_raw - 0.5)
        else:
            kelly_factor = kelly_raw
        
        # 🔥 日志输出：显示平滑前后对比
        mode_label = f"[{strategy_mode}]" if strategy_mode else "[ALL]"
        print(f"   📊 凯利公式平滑处理 {mode_label}:")
        print(f"      原始: W={win_rate_raw:.2%}, R={profit_loss_ratio_raw:.2f}")
        print(f"      平滑: W={win_rate:.2%}, R={profit_loss_ratio:.2f} (权重 {SMOOTH_WEIGHT_CURRENT:.0%}:{SMOOTH_WEIGHT_BASELINE:.0%})")
        print(f"      凯利系数: {kelly_factor:.2f}, 样本数: {total_trades}")
        
        return {
            'win_rate': win_rate,
            'profit_loss_ratio': profit_loss_ratio,
            'kelly_factor': kelly_factor,
            'sample_size': total_trades,
            'smoothed': True,
            'win_rate_raw': win_rate_raw,  # 保留原始值用于调试
            'profit_loss_ratio_raw': profit_loss_ratio_raw,
            'strategy_mode': strategy_mode
        }
        
    except Exception as e:
        print(f"⚠️ 计算绩效统计失败: {e}")
        return {
            'win_rate': BASELINE_WIN_RATE,
            'profit_loss_ratio': BASELINE_PROFIT_LOSS_RATIO,
            'kelly_factor': 1.0,
            'sample_size': 0,
            'smoothed': False,
            'strategy_mode': strategy_mode
        }


# ==========================================
# 仓位计算（凯利公式动态配资）
# ==========================================

def calculate_position_size(client, symbol, price, signal_strength, atr=0, sml_boost=False):
    """
    计算仓位大小（标准头寸风险公式 + 凯利公式动态配资）

    核心公式（消除双重惩罚）：
        risk_amount = BENCHMARK_CASH * RISK_RATIO
        stop_loss_distance = ATR * ATR_MULT
        position_qty = risk_amount / stop_loss_distance

    ATR 已经通过止损距离自然控制了仓位大小（ATR↑ → 止损宽 → 仓位小），
    不再额外乘以 vol_scalar，避免高波动资产被"双重惩罚"。

    安全护栏：
    1. 仓位名义价值不得超过 BENCHMARK_CASH * MAX_SINGLE_RISK_RATIO * LEVERAGE
    2. 仓位数量必须符合交易所 LOT_SIZE stepSize 精度
    """
    try:
        # ==========================================
        # 1. 获取账户净值
        # ==========================================
        if client:
            try:
                acc = client.futures_account()
                total_equity = float(acc['totalMarginBalance'])
            except:
                total_equity = config.SYSTEM_CONFIG["BENCHMARK_CASH"]
        else:
            total_equity = config.SYSTEM_CONFIG["BENCHMARK_CASH"]

        benchmark = config.SYSTEM_CONFIG["BENCHMARK_CASH"]
        leverage = config.SYSTEM_CONFIG["LEVERAGE"]

        # ==========================================
        # 2. 计算基础风险金额 risk_amount
        # ==========================================
        risk_amount = benchmark * config.SYSTEM_CONFIG["RISK_RATIO"]

        # ==========================================
        # 3. 疯狗模式 (MAD_DOG)
        # ==========================================
        current_profit = total_equity - benchmark
        force_mad_dog = config.SYSTEM_CONFIG.get("FORCE_MAD_DOG_MODE", False)
        is_mad_dog_active = False

        if force_mad_dog:
            is_mad_dog_active = True
            print(f"🔥 强制疯狗模式已激活！(FORCE_MAD_DOG_MODE=True)")
        elif config.SYSTEM_CONFIG["MAD_DOG_MODE"] and current_profit > 0:
            profit_ratio = total_equity / benchmark
            if profit_ratio >= config.SYSTEM_CONFIG["MAD_DOG_TRIGGER"]:
                is_mad_dog_active = True

        if is_mad_dog_active:
            risk_amount = risk_amount * config.SYSTEM_CONFIG["MAD_DOG_BOOST"]
            print(f"🔥 疯狗模式激活！应用 {config.SYSTEM_CONFIG['MAD_DOG_BOOST']}x 资金乘数")

        # ==========================================
        # 4. 暴力火力介入 (MANUAL BOOST)
        # ==========================================
        manual_boost = config.SYSTEM_CONFIG.get("MANUAL_BOOST_MULT", 1.0)
        is_force_boost = config.SYSTEM_CONFIG.get("FORCE_MAD_DOG_ACTIVE", False)

        if is_force_boost and manual_boost > 1.0:
            risk_amount = risk_amount * manual_boost
            print(f"🚀 [暴力介入] 手动火力已激活！风险金额放大 {manual_boost}x")

        # ==========================================
        # 5. 凯利公式动态配资
        # ==========================================
        if config.SYSTEM_CONFIG.get("USE_KELLY_FORMULA", False):
            _current_strategy_mode = config.SYSTEM_CONFIG.get("STRATEGY_MODE", "STANDARD")
            perf_stats = get_performance_stats(lookback=50, strategy_mode=_current_strategy_mode)
            kelly_factor = perf_stats['kelly_factor']
            sample_size = perf_stats['sample_size']

            MIN_SAMPLE_SIZE = 10
            if sample_size < MIN_SAMPLE_SIZE:
                kelly_factor = 0.1
                print(f"   ⚠️ 策略 {_current_strategy_mode} 样本量不足 ({sample_size} < {MIN_SAMPLE_SIZE})，Kelly 强制回退 0.1")
        else:
            kelly_factor = 1.0
            perf_stats = {'win_rate': 0.5, 'profit_loss_ratio': 1.5}
            sample_size = 0
            print(f"   ⚙️ [回测对齐] 凯利公式已关闭，使用固定风险比率。")

        risk_amount = risk_amount * kelly_factor

        # ==========================================
        # 6. 资产权重分配
        # ==========================================
        weight = config.SYSTEM_CONFIG.get("ASSET_WEIGHTS", {}).get(symbol, 0.3)
        risk_amount = risk_amount * weight

        # ==========================================
        # 6.5 🔥 波动率缩放 (Volatility Scalar)
        #   当市场波动大于基准时自动减仓，波动小于基准时允许加仓
        #   vol_scalar = ATR_BASELINE / current_ATR
        # ==========================================
        if config.SYSTEM_CONFIG.get("USE_VOLATILITY_SCALAR", False) and atr > 0:
            atr_baseline = config.SYSTEM_CONFIG.get("ATR_BASELINE", 30.0)
            vol_scalar = atr_baseline / atr
            risk_amount = risk_amount * vol_scalar
            print(f"   📊 波动率缩放激活: vol_scalar={vol_scalar:.2f} (ATR_BASELINE={atr_baseline}, ATR={atr:.4f})")

        # ==========================================
        # 7. 🔥 核心公式：标准头寸风险定仓（消除双重惩罚）
        #    position_qty = risk_amount / stop_loss_distance
        #    stop_loss_distance = ATR * ATR_MULT
        # ==========================================
        # 解析 ATR_MULT（与 execute_trade 保持一致的优先级逻辑）
        _current_mode = config.SYSTEM_CONFIG.get("STRATEGY_MODE", "STANDARD")
        _preset = config.STRATEGY_PRESETS.get(_current_mode, {})
        _preset_atr_mult = _preset.get("ATR_MULT", config.SYSTEM_CONFIG.get("ATR_MULT", 2.0))
        _cfg_atr_mult = config.SYSTEM_CONFIG.get("ATR_MULT", 2.0)
        if _cfg_atr_mult == 2.0 and _preset_atr_mult != 2.0:
            atr_mult = _preset_atr_mult
        else:
            atr_mult = _cfg_atr_mult
        if config.SYSTEM_CONFIG.get("LOW_VOL_MODE", False):
            atr_mult = 1.5

        if atr > 0:
            stop_loss_distance = atr * atr_mult
            quantity = risk_amount / stop_loss_distance
            print(f"   📐 标准风险定仓: risk=${risk_amount:.2f} / (ATR={atr:.4f} × ATR_MULT={atr_mult}) = qty={quantity:.4f}")
        else:
            # ATR 不可用时回退到杠杆公式（兜底）
            position_value = risk_amount * leverage
            quantity = position_value / price
            print(f"   ⚠️ ATR不可用，回退杠杆公式: risk=${risk_amount:.2f} × lev={leverage} / price={price:.4f} = qty={quantity:.4f}")

        # 反算仓位名义价值和保证金
        position_value = quantity * price
        allocated_capital = position_value / leverage

        # ==========================================
        # 8. 单笔风险硬上限（MAX_SINGLE_RISK_RATIO * BENCHMARK * LEVERAGE）
        # ==========================================
        max_position_value = benchmark * MAX_SINGLE_RISK_RATIO * leverage
        effective_max = max_position_value * (manual_boost if is_force_boost else 1.0)

        if position_value > effective_max:
            print(f"   🛡️ 单笔风险超限！原仓位=${position_value:.2f} > 动态上限=${effective_max:.2f}")
            position_value = effective_max
            quantity = position_value / price
            allocated_capital = position_value / leverage

        # ==========================================
        # 9. SML 利润放大器（风控上限检查之后应用）
        # ==========================================
        sml_boost_mult = config.SYSTEM_CONFIG.get("SML_BOOST_MULT", 1.20)
        if config.SYSTEM_CONFIG.get("SML_BOOSTER_ENABLED", True) and sml_boost:
            quantity *= sml_boost_mult
            position_value = quantity * price
            allocated_capital = position_value / leverage
            print(f"   🚀 [SML Booster] 聪明钱共振，下单数量强制放大 {sml_boost_mult:.0%}！")

            # SML boost 后二次风控 cap
            if position_value > effective_max:
                print(f"   🛡️ SML boost 后仓位超限！${position_value:.2f} > 动态上限=${effective_max:.2f}，重新截断")
                position_value = effective_max
                quantity = position_value / price
                allocated_capital = position_value / leverage

        # 记录凯利系数用于日志
        print(f"   📊 凯利配资: W={perf_stats['win_rate']:.2%}, R={perf_stats['profit_loss_ratio']:.2f}, "
              f"Kelly={kelly_factor:.2f} (样本={sample_size})")

        # ==========================================
        # 9.5 🔥 灾后重建乘数：熔断恢复后放大被高ATR压缩的仓位
        # ==========================================
        if POST_DISASTER_RECOVERY_ENABLED:
            recovery_mult = get_post_disaster_recovery_multiplier()
            if recovery_mult > 1.0:
                quantity *= recovery_mult
                position_value = quantity * price
                allocated_capital = position_value / leverage
                print(f"   🛠️ 灾后重建乘数 {recovery_mult}x 已应用，仓位放大捕获V型反转")

                # 安全底线：灾后放大后仍不得超过 effective_max
                if position_value > effective_max:
                    print(f"   🛡️ 灾后重建仓位超限！${position_value:.2f} > 动态上限=${effective_max:.2f}，截断")
                    position_value = effective_max
                    quantity = position_value / price
                    allocated_capital = position_value / leverage

        # ==========================================
        # 10. 精度处理：遵守交易所 LOT_SIZE stepSize
        # ==========================================
        from config import symbol_precisions
        precision = symbol_precisions.get(symbol, 3)

        if quantity < (10 ** -precision):
            quantity = (10 ** -precision)

        quantity = round_to_quantity_precision(quantity, symbol)

        return {
            'quantity': quantity,
            'position_value': round(position_value, 2),
            'leverage': leverage,
            'allocated_capital': round(allocated_capital, 2),
            'is_mad_dog': is_mad_dog_active,
            'kelly_factor': kelly_factor,
            'win_rate': perf_stats['win_rate'],
            'profit_loss_ratio': perf_stats['profit_loss_ratio'],
            'sample_size': sample_size,
            'atr_mult_used': atr_mult if atr > 0 else 0,
        }
    except Exception as e:
        print(f"⚠️ 计算仓位大小失败: {e}")
        return None

# ==========================================
# 订单执行逻辑（从 v1.0.py 提取）
# ==========================================

class OrderTransaction:
    """
    订单事务管理器 - 批量下单原子化版本
    使用 Binance futures_place_batch_orders API 实现真正的原子性：
    主订单（MARKET/LIMIT）+ 止损单（STOP_MARKET）在同一个批量请求中提交
    """
    # 🔥 Fix #14: 类级别锁和注册表 + 上限保护 + 定时清理
    _rollback_lock = threading.Lock()
    _rollback_registry = {}  # {order_id: timestamp} - 改为 dict 记录时间戳，便于过期清理
    _ROLLBACK_REGISTRY_MAX = 200  # 注册表上限，防止无界增长
    _ROLLBACK_ENTRY_TTL = 3600    # 条目存活时间（秒），超过 1 小时自动清理
    
    def __init__(self, client, symbol, position_type):
        self.client = client
        self.symbol = symbol
        self.position_type = position_type
        self.main_order_id = None
        self.stop_loss_order_id = None
        self.committed = False
        self.rollback_attempted = False
        self.batch_response = None  # 🔥 存储批量下单响应
        
    def submit_batch_orders(self, main_order_params, stop_loss_params):
        """
        🔥 批量下单原子化提交（主订单 + 止损单）
        
        Args:
            main_order_params: 主订单参数字典
            stop_loss_params: 止损单参数字典
        
        Returns:
            tuple: (main_order, sl_order) 或抛出异常
        """
        try:
            # 构建批量订单列表
            batch_orders = [main_order_params, stop_loss_params]
            
            print(f"🔥 [批量下单] 开始原子化提交: {self.symbol}")
            print(f"   主订单: {main_order_params.get('type')} {main_order_params.get('side')} {main_order_params.get('quantity')}")
            print(f"   止损单: STOP_MARKET stopPrice={stop_loss_params.get('stopPrice')}")
            
            # 🔥 调用 Binance 批量下单 API
            # 注意：批量下单返回的是列表，按提交顺序对应
            response = self.client.futures_place_batch_orders(batchOrders=batch_orders)
            
            self.batch_response = response
            
            # 解析响应（按顺序：[0]=主订单, [1]=止损单）
            if not response or len(response) < 2:
                raise Exception(f"批量下单响应异常: 期望2个订单，实际收到{len(response) if response else 0}个")
            
            main_order = response[0]
            sl_order = response[1]
            
            # 检查主订单状态
            if 'code' in main_order:
                # 主订单失败
                error_msg = main_order.get('msg', '未知错误')
                raise Exception(f"主订单提交失败: {error_msg}")
            
            # 检查止损单状态
            if 'code' in sl_order:
                # 止损单失败 - 这是致命错误，需要立即回滚主订单
                error_msg = sl_order.get('msg', '未知错误')
                self.main_order_id = main_order.get('orderId')
                raise Exception(f"止损单提交失败: {error_msg}，主订单已成交需回滚")
            
            # 两个订单都成功
            self.main_order_id = main_order.get('orderId')
            self.stop_loss_order_id = sl_order.get('orderId')
            
            print(f"✅ [批量下单] 原子化提交成功")
            print(f"   主订单ID: {self.main_order_id}")
            print(f"   止损单ID: {self.stop_loss_order_id}")
            
            return main_order, sl_order
            
        except Exception as e:
            error_msg = str(e)
            print(f"❌ [批量下单] 原子化提交失败: {error_msg}")
            
            # 🔥 批量下单失败时的精准异常处理
            # 场景1: 整个批量请求被拒绝（网络/权限问题）- 无需回滚
            # 场景2: 主订单成功但止损单失败 - 需要立即回滚主订单
            if self.main_order_id:
                print(f"⚠️ [批量下单] 检测到主订单已成交，触发紧急回滚...")
                self.rollback()
            
            raise Exception(f"批量下单原子化失败: {error_msg}")
    
    def commit(self):
        """提交事务（批量下单模式下此方法仅做验证）"""
        if self.main_order_id and self.stop_loss_order_id:
            self.committed = True
            print(f"✅ 订单事务已确认 (主单: {self.main_order_id}, 止损: {self.stop_loss_order_id})")
            return True
        else:
            print(f"❌ 订单事务不完整，无法确认")
            if self.main_order_id:
                # 如果主订单存在但止损单缺失，触发回滚
                print(f"⚠️ 检测到主订单存在但止损单缺失，触发回滚...")
                self.rollback()
            return False
    
    def rollback(self):
        """
        🔥 批量下单模式回滚 - 精准处理部分成交 + 死信队列集成 + 幂等性保护
        
        批量下单失败场景分析：
        1. 整个批量请求被拒绝 -> 无订单成交，无需回滚
        2. 主订单成功但止损单失败 -> 需要立即回滚主订单（本方法处理此场景）
        
        🔒 线程安全保证：
        - 使用类级别锁防止并发回滚
        - 使用注册表防止重复回滚
        - 幂等性参数确保反向清算不重复执行
        """
        # 🔥 线程安全检查：使用类级别锁包裹整个回滚逻辑
        with self._rollback_lock:
            # 幂等性检查1：检查是否已在注册表中
            if self.main_order_id in self._rollback_registry:
                print(f"⚠️ [批量下单回滚] 订单 {self.main_order_id} 已在回滚注册表中，跳过重复操作")
                return
            
            # 幂等性检查2：检查实例级别标记
            if self.rollback_attempted:
                print(f"⚠️ [批量下单回滚] 实例已尝试过回滚，跳过重复操作")
                return
            
            # 🔥 Fix #14: 定期清理过期条目（TTL 淘汰）
            now = time.time()
            expired_keys = [k for k, ts in self._rollback_registry.items() if now - ts > self._ROLLBACK_ENTRY_TTL]
            for k in expired_keys:
                del self._rollback_registry[k]
            if expired_keys:
                print(f"🗑️ [回滚注册表] 清理 {len(expired_keys)} 条过期条目 (TTL={self._ROLLBACK_ENTRY_TTL}s)")
            
            # 🔥 Fix #14: 上限保护 - 如果注册表已满，强制淘汰最早的条目
            if len(self._rollback_registry) >= self._ROLLBACK_REGISTRY_MAX:
                oldest_key = min(self._rollback_registry, key=self._rollback_registry.get)
                del self._rollback_registry[oldest_key]
                print(f"🗑️ [回滚注册表] 已满({self._ROLLBACK_REGISTRY_MAX})，淘汰最早条目: {oldest_key}")
            
            # 标记回滚开始：同时更新注册表和实例标记
            if self.main_order_id:
                self._rollback_registry[self.main_order_id] = time.time()
            self.rollback_attempted = True
            
            print(f"🔒 [批量下单回滚] 已获取回滚锁，订单 {self.main_order_id} 已加入注册表")
        
        rollback_success = True
        filled_qty = 0
        
        # ==========================================
        # 🔥 批量下单回滚：主订单极速清算
        # ==========================================
        if self.main_order_id:
            try:
                print(f"🔥 [批量下单回滚] 开始处理主订单: {self.main_order_id}")
                
                # 步骤1: 尝试撤单（阻断继续成交）
                try:
                    self.client.futures_cancel_order(
                        symbol=self.symbol,
                        orderId=self.main_order_id
                    )
                    print(f"   ✅ 主订单撤单指令已发送")
                except Exception as cancel_e:
                    # 订单可能已完全成交，继续查询状态
                    print(f"   ⚠️ 撤单失败（可能已成交）: {str(cancel_e)[:100]}")
                
                # 步骤2: 查询订单最终状态
                order_status = self.client.futures_get_order(
                    symbol=self.symbol,
                    orderId=self.main_order_id
                )
                
                filled_qty = float(order_status.get('executedQty', 0))
                order_status_str = order_status.get('status', 'UNKNOWN')
                
                print(f"   📊 订单状态: {order_status_str}, 成交数量: {filled_qty}")
                
                # 步骤3: 如果有成交，立即市价反向平仓
                if filled_qty > 0:
                    print(f"   ⚠️ 检测到成交数量 [{filled_qty}]，执行紧急反向平仓...")
                    
                    original_side = order_status['side']
                    reverse_side = 'SELL' if original_side == 'BUY' else 'BUY'
                    entry_price = float(order_status.get('avgPrice', 0))
                    
                    try:
                        # 构建反向平仓参数
                        rollback_params = {
                            'symbol': self.symbol,
                            'side': reverse_side,
                            'type': 'MARKET',
                            'quantity': filled_qty
                        }
                        
                        if config.SYSTEM_CONFIG.get("HEDGE_MODE_ENABLED", False):
                            rollback_params['positionSide'] = self.position_type
                            print(f"   🔀 对冲模式回滚: positionSide={self.position_type}")
                        else:
                            rollback_params['positionSide'] = 'BOTH'
                            rollback_params['reduceOnly'] = True
                        
                        # 🔥 幂等性参数：使用唯一的 newClientOrderId 防止重复提交
                        rollback_client_order_id = f"ROLL_{self.main_order_id}_{int(time.time())}"
                        rollback_params['newClientOrderId'] = rollback_client_order_id
                        
                        # 执行反向平仓
                        close_order = self.client.futures_create_order(**rollback_params)
                        print(f"   ✅ 反向平仓成功，订单ID: {close_order.get('orderId')}, ClientOrderId: {rollback_client_order_id}")
                        
                        # 发送告警通知
                        from utils import send_tg_alert
                        import html
                        send_tg_alert(
                            f"🚨 <b>[批量下单回滚成功]</b>\n\n"
                            f"币种: {html.escape(self.symbol)}\n"
                            f"原因: 批量下单中止损单失败\n"
                            f"状态: {order_status_str}\n"
                            f"成交数量: {filled_qty}\n"
                            f"处置: 已市价反向平仓\n\n"
                            f"✅ 风险敞口已清除"
                        )
                        
                        # 🔥 修复内存泄漏: 成功反向平仓后，从注册表中移除
                        try:
                            with self._rollback_lock:
                                self._rollback_registry.pop(self.main_order_id, None)
                        except Exception:
                            pass
                        
                    except Exception as close_e:
                        print(f"   ❌ 反向平仓失败: {close_e}")
                        rollback_success = False
                        
                        # 🔥 回滚失败：从注册表移除，允许 DLQ 重试
                        try:
                            with self._rollback_lock:
                                self._rollback_registry.pop(self.main_order_id, None)
                            print(f"   🔓 [批量下单回滚] 回滚失败，已从注册表移除 {self.main_order_id}，允许 DLQ 重试")
                        except Exception as reg_e:
                            print(f"   ⚠️ 从注册表移除失败: {reg_e}")
                        
                        # 🔥 加入死信队列
                        from dlq_worker import add_to_dlq
                        add_to_dlq(
                            symbol=self.symbol,
                            position_type=self.position_type,
                            qty=filled_qty,
                            entry_price=entry_price,
                            trade_id=str(self.main_order_id),
                            error_reason=f"批量下单回滚失败: {str(close_e)[:100]}"
                        )
                        from config import save_dlq
                        save_dlq() 

                        from utils import send_tg_alert
                        import html
                        send_tg_alert(
                            f"🔴 <b>[致命警告：批量下单回滚失败]</b>\n\n"
                            f"币种: {html.escape(self.symbol)}\n"
                            f"方向: {self.position_type}\n"
                            f"成交数量: {filled_qty}\n"
                            f"主订单ID: {self.main_order_id}\n\n"
                            f"⚠️ 该持仓处于无止损裸奔状态！\n"
                            f"🔥 已加入死信队列，清道夫将持续重试平仓\n\n"
                            f"错误: {html.escape(str(close_e)[:100])}"
                        )
                else:
                    print(f"   ✅ 订单未成交或已完全撤销，无需平仓")
                    
            except Exception as e:
                print(f"   ❌ 回滚过程异常: {e}")
                rollback_success = False
                
                # 🔥 异常处理：从注册表移除，允许后续重试
                try:
                    if self.main_order_id:
                        with self._rollback_lock:
                            self._rollback_registry.pop(self.main_order_id, None)
                        print(f"   🔓 [批量下单回滚] 异常处理，已从注册表移除 {self.main_order_id}")
                except Exception as reg_e:
                    print(f"   ⚠️ 异常处理中从注册表移除失败: {reg_e}")
        
        # ==========================================
        # 止损单清理（批量下单模式下通常不存在）
        # ==========================================
        if self.stop_loss_order_id:
            try:
                self.client.futures_cancel_order(
                    symbol=self.symbol,
                    orderId=self.stop_loss_order_id
                )
                print(f"   ✅ 止损单已撤销: {self.stop_loss_order_id}")
            except Exception as e:
                # 批量下单失败时止损单通常不会成功创建
                pass
        
        if not rollback_success:
            print(f"🚨 [批量下单回滚] 回滚未完全成功，请立即手动检查！")
            from utils import send_tg_alert
            send_tg_alert(
                f"🔴 <b>[紧急警告]</b>\n\n"
                f"{self.symbol} 批量下单回滚失败\n"
                f"可能存在无止损敞口\n"
                f"请立即登录币安APP手动检查！"
            )
        
        return rollback_success


def execute_trade(client, symbol, signal_type, price, position_info, atr=0, adx=0, position_action='ENTRY', custom_config=None, simulated=None):
    """执行交易（V5.0 Maker优先算法 + 事务支持 + ADX动态止损 + 🔥 环境感知）"""
    from binance.enums import SIDE_BUY, SIDE_SELL, FUTURE_ORDER_TYPE_MARKET, FUTURE_ORDER_TYPE_LIMIT
    from utils import send_tg_alert, round_to_tick_size
    import html
    
    try:
        # 🔥 配置隔离：优先使用传入的 custom_config，否则回退到全局 config.SYSTEM_CONFIG
        cfg = custom_config if custom_config is not None else config.SYSTEM_CONFIG
        
        # 🔥 环境感知逻辑：SANDBOX 模式强制 simulated=True
        running_mode = cfg.get("RUNNING_MODE", "SANDBOX")
        if running_mode == "SANDBOX":
            simulated = True
            print(f"   🏖️ [环境感知] SANDBOX 模式检测到，强制 simulated=True")
        # 🔥 Fix #5: 优先从当前策略预设读取 ATR_MULT，解决 SYSTEM_CONFIG 默认值 2.0 覆盖预设的冲突
        _current_mode = cfg.get("STRATEGY_MODE", "STANDARD")
        _preset = config.STRATEGY_PRESETS.get(_current_mode, {})
        _preset_atr_mult = _preset.get("ATR_MULT", cfg.get("ATR_MULT", 2.0))
        # 如果用户手动在 cfg 中覆盖了 ATR_MULT 且与预设不同，以用户手动值为准
        _cfg_atr_mult = cfg.get("ATR_MULT", 2.0)
        # 判断逻辑：如果 cfg 值等于硬编码默认值 2.0 且预设值不同，说明是默认值覆盖了预设，应使用预设值
        if _cfg_atr_mult == 2.0 and _preset_atr_mult != 2.0:
            current_atr_mult = _preset_atr_mult
        else:
            current_atr_mult = _cfg_atr_mult
        if cfg.get("LOW_VOL_MODE", False):
            current_atr_mult = 1.5
        
        # 🔥 Fix #6: ADX 动态止损缩放因子 — 线性插值替代粗糙两档阶梯
        # 旧逻辑：adx>30 → 1.15, adx<20 → 0.75, 中间不调整
        # 新逻辑：20~30 区间线性插值，让止损距离随趋势强度平滑变化
        adx_scalar = 1.0
        if adx > 30:
            adx_scalar = 1.15  # 强趋势：放宽止损 1.15x
        elif adx < 20:
            adx_scalar = 0.75  # 弱趋势：收紧止损 0.75x
        else:
            # 线性插值：adx=20 → 0.75, adx=30 → 1.15
            adx_scalar = 0.75 + (adx - 20) * (1.15 - 0.75) / (30 - 20)
        
        # ==========================================
        # 🔥 SL/TP 幻觉防御：数学硬边界判定
        # ==========================================
        # 止损价必须满足方向性约束 + 距离合理性约束，否则回退到 ATR 默认值
        SL_MAX_DISTANCE_PCT = cfg.get("SL_MAX_DISTANCE_PCT", 0.10)   # 止损距离不超过入场价的 10%
        SL_MIN_DISTANCE_PCT = cfg.get("SL_MIN_DISTANCE_PCT", 0.001)  # 止损距离不低于入场价的 0.1%

        def _validate_sl_price(sl_price, entry_price, direction, atr_val, label=""):
            """
            止损价数学硬边界校验 — 拦截 AI 幻觉 / 计算溢出 / 符号反转

            规则：
            1. 方向性：LONG → sl < entry；SHORT → sl > entry
            2. 最大距离：|sl - entry| / entry <= SL_MAX_DISTANCE_PCT
            3. 最小距离：|sl - entry| / entry >= SL_MIN_DISTANCE_PCT
            4. 正数检查：sl > 0

            违规时自动回退到 ATR 默认止损（或 2% 兜底）。

            Returns:
                float: 校验通过的止损价（可能是原值或回退值）
            """
            if entry_price <= 0:
                return sl_price  # 无法校验，原样返回

            # --- 规则 0: 正数检查 ---
            if sl_price <= 0:
                print(f"   🚨 [{label}] SL幻觉拦截: sl_price={sl_price} <= 0，回退到默认止损")
                return _build_fallback_sl(entry_price, direction, atr_val)

            distance = abs(sl_price - entry_price)
            distance_pct = distance / entry_price

            # --- 规则 1: 方向性校验 ---
            if direction == 'BUY' and sl_price >= entry_price:
                print(f"   🚨 [{label}] SL幻觉拦截: 做多止损 {sl_price:.4f} >= 入场价 {entry_price:.4f}，方向反转！")
                return _build_fallback_sl(entry_price, direction, atr_val)
            if direction == 'SELL' and sl_price <= entry_price:
                print(f"   🚨 [{label}] SL幻觉拦截: 做空止损 {sl_price:.4f} <= 入场价 {entry_price:.4f}，方向反转！")
                return _build_fallback_sl(entry_price, direction, atr_val)

            # --- 规则 2: 最大距离校验（防止离谱止损） ---
            if distance_pct > SL_MAX_DISTANCE_PCT:
                print(f"   🚨 [{label}] SL幻觉拦截: 止损距离 {distance_pct:.2%} > 上限 {SL_MAX_DISTANCE_PCT:.2%}，回退到默认止损")
                return _build_fallback_sl(entry_price, direction, atr_val)

            # --- 规则 3: 最小距离校验（防止零距离止损） ---
            if distance_pct < SL_MIN_DISTANCE_PCT:
                print(f"   🚨 [{label}] SL幻觉拦截: 止损距离 {distance_pct:.4%} < 下限 {SL_MIN_DISTANCE_PCT:.4%}，回退到默认止损")
                return _build_fallback_sl(entry_price, direction, atr_val)

            # 全部通过
            return sl_price

        def _build_fallback_sl(entry_price, direction, atr_val):
            """构建回退止损价：优先用 ATR，否则用固定 2%"""
            if atr_val > 0:
                fb_distance = atr_val * current_atr_mult * adx_scalar
                if direction == 'BUY':
                    fb_sl = entry_price - fb_distance
                else:
                    fb_sl = entry_price + fb_distance
                print(f"   🔄 回退止损: ATR={atr_val:.4f} × {current_atr_mult} × {adx_scalar:.3f} = {fb_distance:.4f}")
            else:
                if direction == 'BUY':
                    fb_sl = entry_price * FALLBACK_SL_LONG_MULT
                else:
                    fb_sl = entry_price * FALLBACK_SL_SHORT_MULT
                print(f"   🔄 回退止损: 固定 2% 兜底")
            return round_to_tick_size(fb_sl, symbol)

        # 🔥 Fix #7 (初始预计算): ATR 不可用时的智能兜底止损辅助函数
        def _calc_fallback_sl(base_price, direction, cli, sym, cfg_ref):
            """ATR 不可用时，用最近 20 根 K 线振幅中位数 × 1.5 作为止损距离"""
            try:
                _fb_df = get_historical_klines(cli, sym, cfg_ref.get("INTERVAL", "15m"), limit=30) if cli else None
                if _fb_df is not None and len(_fb_df) >= 20:
                    _recent_ranges = (_fb_df['high'] - _fb_df['low']).tail(20)
                    _median_range = _recent_ranges.median()
                    _fb_distance = _median_range * 1.5
                    if direction == 'BUY':
                        sl = base_price - _fb_distance
                    else:
                        sl = base_price + _fb_distance
                    print(f"   📐 智能兜底止损(预计算): 振幅中位数={_median_range:.4f} × 1.5 = {_fb_distance:.4f}")
                    return sl
            except Exception as _fb_e:
                print(f"   ⚠️ 智能兜底止损预计算失败: {_fb_e}")
            # 数据不足或异常时回退到固定 2%
            if direction == 'BUY':
                return base_price * 0.98
            else:
                return base_price * 1.02
        
        # 计算止损价
        if position_action == 'ENTRY':
            if signal_type == 'BUY':
                if atr > 0:
                    stop_loss_distance = atr * current_atr_mult * adx_scalar * cfg.get("SL_BUFFER", 1.02)
                    stop_loss_price = price - stop_loss_distance
                    
                    # 🔥 日志输出：显示动态止损调整
                    if adx_scalar != 1.0:
                        print(f"📉 动态止损介入：当前 ADX={adx:.1f}, 止损{'放宽' if adx_scalar > 1 else '收紧'} {adx_scalar:.3f}x")
                else:
                    # 🔥 Fix #7: 智能兜底止损（替代固定 2%）
                    stop_loss_price = _calc_fallback_sl(price, 'BUY', client, symbol, cfg)
                stop_loss_price = round_to_tick_size(stop_loss_price, symbol)
            elif signal_type == 'SELL':
                if atr > 0:
                    stop_loss_distance = atr * current_atr_mult * adx_scalar * cfg.get("SL_BUFFER", 1.02)
                    stop_loss_price = price + stop_loss_distance
                    
                    # 🔥 日志输出：显示动态止损调整
                    if adx_scalar != 1.0:
                        print(f"📉 动态止损介入：当前 ADX={adx:.1f}, 止损{'放宽' if adx_scalar > 1 else '收紧'} {adx_scalar:.3f}x")
                else:
                    # 🔥 Fix #7: 智能兜底止损（替代固定 2%）
                    stop_loss_price = _calc_fallback_sl(price, 'SELL', client, symbol, cfg)
                stop_loss_price = round_to_tick_size(stop_loss_price, symbol)

        # 🔥 SL 幻觉防御 Phase 0: 初始预计算止损校验（SANDBOX + LIVE 共用）
        if position_action == 'ENTRY':
            stop_loss_price = _validate_sl_price(
                stop_loss_price, price, signal_type, atr, label=f"{symbol}-Phase0"
            )
            stop_loss_price = round_to_tick_size(stop_loss_price, symbol)

        # ==========================================
        # 🛡️ 双轨路由拦截网：SANDBOX 模式虚拟交易
        # ==========================================
        is_sandbox = (simulated == True)
        
        if is_sandbox:
            print(f"   🏖️ [SANDBOX 模式] 拦截实盘调用，执行虚拟交易...")
            
            # 虚拟开仓 (ENTRY)
            if position_action == 'ENTRY':
                pos_type = 'LONG' if signal_type == 'BUY' else 'SHORT'
                key_sym = f"{symbol}_{pos_type}"
                
                # 🔥 沙盒账本：检查余额并扣除保证金
                position_value = position_info['quantity'] * price
                margin_required = position_value / position_info['leverage']
                
                # 扣除保证金
                deduct_result = update_sandbox_balance(-margin_required, f"开仓 {symbol} {pos_type} 保证金")
                if not deduct_result['success']:
                    print(f"   ❌ 沙盒余额不足: {deduct_result['message']}")
                    return {
                        'success': False,
                        'message': f"SANDBOX 余额不足: {deduct_result['message']}"
                    }
                
                # --- 1. 生成虚拟 trade_id ---
                virtual_trade_id = f"SIM_{int(time.time() * 1000)}"
                
                # --- 2. 🔥 [修复核心] 生成符合隔离规则的刺青标签 ---
                from position_isolation import generate_bot_order_id
                # 只有打上这个标签，平仓哨兵才会放行
                sim_client_id = generate_bot_order_id() if POSITION_ISOLATION_ENABLED else f"WJ_BOT_SIM_{virtual_trade_id}"
                
                # --- 3. 构建虚拟订单记录 (补齐标签) ---
                virtual_position = {
                    'entry': price,
                    'sl': stop_loss_price,
                    'qty': position_info['quantity'],
                    'type': pos_type,
                    'real_symbol': symbol,
                    'timestamp': datetime.now(),
                    'trade_id': virtual_trade_id,
                    'client_order_id': sim_client_id, # 👈 🔥 关键：必须加上这一行
                    'sl_order_id': f"SL_{virtual_trade_id}",
                    'simulated': True,  
                    'transaction_committed': True,
                    'order_identity': 'SANDBOX',
                    'fill_price': price,
                    'atr': atr
                }
                
                # 🔒 线程锁保护：虚拟开仓写入 config.ACTIVE_POSITIONS
                with positions_lock:
                    if key_sym not in config.ACTIVE_POSITIONS:
                        config.ACTIVE_POSITIONS[key_sym] = []
                    elif not isinstance(config.ACTIVE_POSITIONS[key_sym], list):
                        config.ACTIVE_POSITIONS[key_sym] = [config.ACTIVE_POSITIONS[key_sym]]
                    
                    config.ACTIVE_POSITIONS[key_sym].append(virtual_position)
                
                save_data()
                
                print(f"   🏖️ [沙盒演习] 虚拟开仓成功: {symbol} {signal_type}")
                print(f"      Trade_ID: {virtual_trade_id}")
                print(f"      开仓价: {price}, 止损价: {stop_loss_price}")
                print(f"      数量: {position_info['quantity']}, 杠杆: {position_info['leverage']}x")
                
                # 🔥 获取当前沙盒余额
                ledger = get_sandbox_balance()
                current_balance = ledger['balance']
                
                send_tg_alert(
                    f"🏖️ <b>[SANDBOX-虚拟开仓]</b>\n"
                    f"币种: {html.escape(symbol)}\n"
                    f"动作: 开{'多' if pos_type=='LONG' else '空'} ({signal_type})\n"
                    f"开仓价: {price}\n"
                    f"止损位: {stop_loss_price}\n"
                    f"保证金: ${margin_required:.2f}\n"
                    f"虚拟订单ID: {virtual_trade_id}\n"
                    f"沙盒余额: ${current_balance:.2f}\n"
                    f"⚠️ SANDBOX 模式：未调用实盘API"
                )
                
                return {
                    'success': True,
                    'trade_id': virtual_trade_id,
                    'sl_order_id': f"SL_{virtual_trade_id}",
                    'simulated': True,  # 🔥 双轨标记：SANDBOX 模式强制 True
                    'order_identity': 'SANDBOX',
                    'fill_price': price,
                    'message': f"SANDBOX 虚拟开仓成功，止损价: ${stop_loss_price}"
                }
            
            # 虚拟平仓 (EXIT)
            elif position_action.startswith('EXIT'):
                pos_type = 'LONG' if position_action == 'EXIT_LONG' else 'SHORT'
                key_sym = f"{symbol}_{pos_type}"
                
                if key_sym not in config.ACTIVE_POSITIONS and symbol in config.ACTIVE_POSITIONS:
                    key_sym = symbol
                
                if key_sym in config.ACTIVE_POSITIONS:
                    # 🔒 线程锁保护：虚拟平仓修改/删除 config.ACTIVE_POSITIONS
                    with positions_lock:
                        positions_list = config.ACTIVE_POSITIONS[key_sym] if isinstance(config.ACTIVE_POSITIONS[key_sym], list) else [config.ACTIVE_POSITIONS[key_sym]]
                        
                        if not positions_list:
                            return {'success': False, 'message': f"SANDBOX 模式：没有{symbol}的虚拟持仓可平仓"}
                        
                        position = positions_list.pop(0)
                    
                    # 计算虚拟盈亏（含手续费）
                    entry_price = position['entry']
                    exit_price = price
                    qty = position['qty']
                    
                    # 手续费：双边万四
                    commission = (entry_price + exit_price) * qty * cfg["COMMISSION_RATE"]
                    
                    if position['type'] == 'LONG':
                        gross_pnl = (exit_price - entry_price) * qty
                    else:
                        gross_pnl = (entry_price - exit_price) * qty
                    
                    net_pnl = gross_pnl - commission
                    
                    # 🔥 沙盒账本：归还保证金并结算盈亏
                    position_value = qty * entry_price
                    margin_used = position_value / cfg.get("LEVERAGE", 20)
                    
                    # 归还保证金
                    update_sandbox_balance(margin_used, f"平仓 {symbol} {position['type']} 归还保证金")
                    
                    # 结算盈亏
                    update_sandbox_balance(net_pnl, f"平仓 {symbol} {position['type']} 盈亏结算")
                    
                    # 获取最新余额
                    ledger = get_sandbox_balance()
                    current_balance = ledger['balance']
                    
                    # 🔥 写入沙盒历史文件
                    _log_sim_trade_to_csv(symbol, position['type'], entry_price, exit_price, qty, net_pnl, current_balance)
                    
                    # 🔒 线程锁保护：更新持仓列表
                    with positions_lock:
                        if not positions_list:
                            config.ACTIVE_POSITIONS.pop(key_sym)
                        else:
                            config.ACTIVE_POSITIONS[key_sym] = positions_list
                    
                    # 记录到交易历史
                    trade_record = {
                        'symbol': symbol,
                        'type': position['type'],
                        'entry': entry_price,
                        'exit': exit_price,
                        'qty': qty,
                        'pnl': net_pnl,
                        'gross_pnl': gross_pnl,
                        'commission': commission,
                        'exit_reason': 'SANDBOX_SIGNAL_EXIT',
                        'trade_id': position['trade_id'],
                        'timestamp': datetime.now().isoformat(),
                        'simulated': True,
                        'strategy_mode': cfg.get("STRATEGY_MODE", "STANDARD")
                    }
                    with state_lock:
                        config.TRADE_HISTORY.append(trade_record)
                        if len(config.TRADE_HISTORY) > 1000:
                            config.TRADE_HISTORY[:] = config.TRADE_HISTORY[-1000:]
                    
                    # 🔥 O(1) 极速追加到 Redis，无需全量覆写
                    try:
                        from redis_manager import redis_db
                        running_mode = config.SYSTEM_CONFIG.get("RUNNING_MODE", "SANDBOX")
                        prefix = redis_db.get_key_prefix(running_mode)
                        redis_db.append_to_list(f"{prefix}:history", trade_record, max_length=1000)
                    except Exception as redis_e:
                        logger.warning(f"⚠️ Redis追加失败（不影响本地存储）: {redis_e}")
                    
                    save_data()
                    
                    print(f"   🏖️ [SANDBOX] 虚拟平仓成功: {symbol} 平{'多' if position['type']=='LONG' else '空'}")
                    print(f"      平仓价: {price}")
                    print(f"      毛利: ${gross_pnl:.2f}, 手续费: ${commission:.2f}, 净利: ${net_pnl:.2f}")
                    print(f"      SANDBOX 余额: ${current_balance:.2f}")
                    
                    send_tg_alert(
                        f"🏖️ <b>[SANDBOX-虚拟平仓]</b>\n"
                        f"币种: {html.escape(symbol)}\n"
                        f"方向: 平{'多' if position['type']=='LONG' else '空'}\n"
                        f"平仓价: {price}\n"
                        f"毛利: ${gross_pnl:.2f}\n"
                        f"手续费: ${commission:.2f}\n"
                        f"净利: ${net_pnl:.2f}\n"
                        f"虚拟订单ID: {position['trade_id']}\n"
                        f"沙盒余额: ${current_balance:.2f}\n"
                        f"剩余子仓: {len(positions_list)} 笔\n"
                        f"⚠️ SANDBOX 模式：未调用实盘API"
                    )
                    
                    return {
                        'success': True,
                        'trade_id': position['trade_id'],
                        'pnl': net_pnl,
                        'gross_pnl': gross_pnl,
                        'commission': commission,
                        'simulated': True,  # 🔥 双轨标记：SANDBOX 模式强制 True
                        'message': f"SANDBOX 虚拟平仓成功，净利: ${net_pnl:.2f}"
                    }
                else:
                    return {'success': False, 'message': f"SANDBOX 模式：没有{symbol}的虚拟持仓可平仓"}

        # 实盘操作
        if client is None:
            return {'success': False, 'message': "币安客户端未连接"}
        
        # 开仓逻辑
        if position_action == 'ENTRY':
            pos_type = 'LONG' if signal_type == 'BUY' else 'SHORT'
            key_sym = f"{symbol}_{pos_type}"
            
            # 🔥 AI 发单频率限流：60 秒内同一 Symbol 最多 2 次自动开仓
            if not _ai_order_rate_limiter.allow(symbol):
                msg = f"🚦 [{symbol}] AI 发单限流拦截：60s 内开仓请求过于频繁，已丢弃"
                print(msg)
                logger.warning(msg)
                send_tg_alert(f"🚦 <b>[AI限流拦截]</b> {html.escape(symbol)}\n60秒内开仓请求超限，已丢弃本次信号")
                return {'success': False, 'message': msg}
            
            transaction = OrderTransaction(client, symbol, pos_type)
            
            try:
                # 🔥 环境门控：SANDBOX 模式跳过杠杆设置
                if config.SYSTEM_CONFIG.get("RUNNING_MODE") != "SANDBOX":
                    client.futures_change_leverage(
                        symbol=symbol, 
                        leverage=int(position_info['leverage'])
                    )
                else:
                    print(f"   🏖️ [Sandbox Isolation] Bypassing leverage change API call")
                
                # ====== 盘口滑点预检 ======
                act_side = SIDE_BUY if signal_type == 'BUY' else SIDE_SELL
                max_slip = config.SYSTEM_CONFIG.get("MAX_SLIPPAGE", 0.0015)
                slip_ok, slip_reason, est_price = check_orderbook_slippage(
                    client, symbol, signal_type, position_info['quantity'], max_slippage=max_slip
                )
                if not slip_ok:
                    msg = f"⚠️ [{symbol}] 滑点预检拒绝开仓: {slip_reason}"
                    print(msg)
                    send_tg_alert(f"⚠️ <b>[滑点预检]</b> {html.escape(symbol)}\n{html.escape(slip_reason)}")
                    # 🔥 EQM: 记录滑点拒绝
                    if EQM_ENABLED:
                        try:
                            get_eqm().record_rejection(symbol, signal_type, position_info['quantity'], slip_reason)
                        except Exception:
                            pass
                    return {'success': False, 'message': f"滑点过大，放弃开仓: {slip_reason}"}
                print(f"   ✅ 滑点预检通过 | 预计均价: {est_price:.4f}")
                
                # ====== 动态构建 positionSide 参数（对冲模式支持）======
                hedge_enabled = config.SYSTEM_CONFIG.get("HEDGE_MODE_ENABLED", False)
                order_params = {}
                
                if hedge_enabled:
                    # 对冲模式：强制指定 positionSide
                    order_params['positionSide'] = 'LONG' if signal_type == 'BUY' else 'SHORT'
                    print(f"   🔀 对冲模式：positionSide={order_params['positionSide']}")
                else:
                    # 单向模式：使用 BOTH 或不传参数
                    order_params['positionSide'] = 'BOTH'
                    print(f"   🔀 单向模式：positionSide=BOTH")
                
                # ====== 🔥 两阶段硬止损：先市价成交，再用真实均价挂 STOP_MARKET ======
                order_identity = "TAKER"  # 默认为TAKER
                actual_fill_price = price
                
                # 🔥 生成机器人订单标签
                bot_order_id = generate_bot_order_id() if POSITION_ISOLATION_ENABLED else None
                bot_sl_order_id = generate_bot_order_id() if POSITION_ISOLATION_ENABLED else None
                
                if bot_order_id:
                    logger.info(f"🏷️ 生成机器人订单标签: {bot_order_id}")
                
                # 🔥 幽灵刺客：读取上游传来的强制 Maker 指令
                is_scalp_order = (cfg.get("STRATEGY_MODE", "STANDARD") == "SCALPER")
                force_maker = position_info.get('is_maker_only', True)
                
                if is_scalp_order and force_maker:
                    try:
                        # 获取买一/卖一价作为挂单价
                        ticker = client.futures_book_ticker(symbol=symbol)
                        if signal_type == 'BUY':
                            limit_price = float(ticker['bidPrice'])  # 挂在买一价，排队等砸
                        else:
                            limit_price = float(ticker['askPrice'])  # 挂在卖一价，排队等吃
                        
                        limit_price = round_to_tick_size(limit_price, symbol)
                        
                        # 构建主订单参数（POST_ONLY LIMIT订单）
                        main_order_params = {
                            'symbol': symbol,
                            'side': act_side,
                            'type': 'LIMIT',
                            'timeInForce': 'GTX',  # Binance Post-Only 标志
                            'price': limit_price,
                            'quantity': position_info['quantity'],
                        }
                        order_identity = "MAKER"
                        print(f"   👻 幽灵刺客: POST_ONLY LIMIT @ {limit_price} (GTX Maker-Only)")
                    except Exception as gtx_e:
                        # 获取盘口失败时回退到 MARKET
                        print(f"   ⚠️ 幽灵刺客盘口获取失败，回退MARKET: {gtx_e}")
                        main_order_params = {
                            'symbol': symbol,
                            'side': act_side,
                            'type': FUTURE_ORDER_TYPE_MARKET,
                            'quantity': position_info['quantity'],
                        }
                        order_identity = "TAKER"
                else:
                    # 非 Scalper 模式：使用 MARKET 订单
                    main_order_params = {
                        'symbol': symbol,
                        'side': act_side,
                        'type': FUTURE_ORDER_TYPE_MARKET,
                        'quantity': position_info['quantity'],
                    }
                
                if bot_order_id:
                    main_order_params['newClientOrderId'] = bot_order_id
                main_order_params.update(order_params)  # 添加 positionSide
                
                # ==========================================
                # 🔥 Phase 1: 提交主订单（市价/限价）
                # 🔥 Fix #12: GTX Maker-Only 重试机制
                #   GTX 订单可能因盘口价移动而被交易所拒绝（-5022 Post-Only rejected）
                #   重试策略：刷新盘口价后重挂，最多 2 次，全部失败后 fallback 到 MARKET
                # ==========================================
                GTX_MAX_RETRIES = 2
                _gtx_succeeded = False
                
                if order_identity == "MAKER" and main_order_params.get('timeInForce') == 'GTX':
                    for _gtx_attempt in range(1, GTX_MAX_RETRIES + 1):
                        try:
                            print(f"🔥 [Phase 1] GTX Maker-Only 尝试 #{_gtx_attempt}: {main_order_params.get('side')} @ {main_order_params.get('price')}")
                            main_order = safe_futures_create_order(client, **main_order_params)
                            _gtx_succeeded = True
                            print(f"   ✅ GTX 挂单成功 (尝试 #{_gtx_attempt})")
                            break
                        except Exception as gtx_retry_e:
                            error_str = str(gtx_retry_e)
                            # -5022: Post-Only order would immediately match (盘口已移动)
                            # -4131: 类似的 GTX 拒绝错误
                            is_gtx_rejection = any(code in error_str for code in ['-5022', '-4131', 'Post Only', 'GTX'])
                            
                            if is_gtx_rejection and _gtx_attempt < GTX_MAX_RETRIES:
                                print(f"   ⚠️ GTX 被拒 (尝试 #{_gtx_attempt}): {error_str[:80]}")
                                print(f"   🔄 刷新盘口价后重试...")
                                time.sleep(0.15)  # 短暂等待盘口刷新
                                
                                # 刷新盘口价
                                try:
                                    _fresh_ticker = client.futures_book_ticker(symbol=symbol)
                                    if signal_type == 'BUY':
                                        _fresh_price = round_to_tick_size(float(_fresh_ticker['bidPrice']), symbol)
                                    else:
                                        _fresh_price = round_to_tick_size(float(_fresh_ticker['askPrice']), symbol)
                                    main_order_params['price'] = _fresh_price
                                    print(f"   📊 盘口价已刷新: {_fresh_price}")
                                except Exception:
                                    pass  # 刷新失败也继续重试，用旧价格
                            else:
                                # 非 GTX 拒绝错误 或 最后一次重试也失败
                                print(f"   ❌ GTX 最终失败 (尝试 #{_gtx_attempt}): {error_str[:80]}")
                                break
                    
                    # 🔥 GTX 全部失败：fallback 到 IOC+Chasing 或 MARKET
                    if not _gtx_succeeded:
                        # 🔥 优先使用 IOC+Chasing（显著降低高波动币种拒单率）
                        _use_ioc_chase = IOC_CHASE_ENABLED and cfg.get("IOC_CHASE_ENABLED", True)
                        if _use_ioc_chase:
                            print(f"   🔄 GTX {GTX_MAX_RETRIES} 次重试均失败，启动 IOC+Chasing 追单模式")
                            _position_side = order_params.get('positionSide', 'BOTH')
                            _chase_result = execute_ioc_then_chase_entry(
                                client, symbol, act_side, position_info['quantity'], _position_side
                            )
                            if _chase_result['success'] and _chase_result['filled_qty'] > 0:
                                # 构造兼容的 main_order 字典
                                main_order = {
                                    'orderId': f"IOC_CHASE_{int(time.time()*1000)}",
                                    'avgPrice': str(_chase_result['avg_price']),
                                    'executedQty': str(_chase_result['filled_qty']),
                                }
                                order_identity = "IOC_CHASE"
                                # 更新实际成交数量（可能部分成交）
                                position_info['quantity'] = round_to_quantity_precision(_chase_result['filled_qty'], symbol)
                                print(f"   ✅ IOC+Chasing 成交: {_chase_result['filled_qty']}, 均价={_chase_result['avg_price']:.4f}")
                                # 🔥 剩余部分转入 DLQ
                                if _chase_result['remaining_qty'] > 0:
                                    from dlq_worker import add_to_dlq
                                    add_to_dlq(
                                        symbol=symbol, position_type=pos_type,
                                        qty=_chase_result['remaining_qty'],
                                        entry_price=_chase_result['avg_price'],
                                        trade_id=str(main_order['orderId']),
                                        error_reason=f"IOC+Chase 未全额成交，剩余 {_chase_result['remaining_qty']}"
                                    )
                                    print(f"   ⚠️ 剩余 {_chase_result['remaining_qty']} 已转入 DLQ")
                            else:
                                # IOC+Chase 完全失败，最终 fallback 到 MARKET
                                print(f"   ❌ IOC+Chasing 失败，最终 fallback 到 MARKET")
                                main_order_params = {
                                    'symbol': symbol,
                                    'side': act_side,
                                    'type': FUTURE_ORDER_TYPE_MARKET,
                                    'quantity': position_info['quantity'],
                                }
                                if bot_order_id:
                                    main_order_params['newClientOrderId'] = bot_order_id
                                main_order_params.update(order_params)
                                order_identity = "TAKER"
                                main_order = safe_futures_create_order(client, **main_order_params)
                                print(f"   ✅ MARKET fallback 成交")
                        else:
                            # IOC+Chase 未启用，直接 MARKET fallback
                            print(f"   🔄 GTX {GTX_MAX_RETRIES} 次重试均失败，fallback 到 MARKET 吃单")
                            main_order_params = {
                                'symbol': symbol,
                                'side': act_side,
                                'type': FUTURE_ORDER_TYPE_MARKET,
                                'quantity': position_info['quantity'],
                            }
                            if bot_order_id:
                                main_order_params['newClientOrderId'] = bot_order_id
                            main_order_params.update(order_params)
                            order_identity = "TAKER"
                            main_order = safe_futures_create_order(client, **main_order_params)
                            print(f"   ✅ MARKET fallback 成交")
                else:
                    # 非 GTX 订单（MARKET）：优先尝试 IOC+Chasing（高波动币种降低拒单率）
                    _use_ioc_chase_market = IOC_CHASE_ENABLED and cfg.get("IOC_CHASE_ENABLED", True)
                    if _use_ioc_chase_market:
                        print(f"🔥 [Phase 1] IOC+Chasing 开仓: {act_side} {position_info['quantity']}")
                        _position_side = order_params.get('positionSide', 'BOTH')
                        _chase_result = execute_ioc_then_chase_entry(
                            client, symbol, act_side, position_info['quantity'], _position_side
                        )
                        if _chase_result['success'] and _chase_result['filled_qty'] > 0:
                            main_order = {
                                'orderId': f"IOC_CHASE_{int(time.time()*1000)}",
                                'avgPrice': str(_chase_result['avg_price']),
                                'executedQty': str(_chase_result['filled_qty']),
                            }
                            order_identity = "IOC_CHASE"
                            position_info['quantity'] = round_to_quantity_precision(_chase_result['filled_qty'], symbol)
                            print(f"   ✅ IOC+Chasing 成交: {_chase_result['filled_qty']}, 均价={_chase_result['avg_price']:.4f}")
                            if _chase_result['remaining_qty'] > 0:
                                from dlq_worker import add_to_dlq
                                add_to_dlq(
                                    symbol=symbol, position_type=pos_type,
                                    qty=_chase_result['remaining_qty'],
                                    entry_price=_chase_result['avg_price'],
                                    trade_id=str(main_order['orderId']),
                                    error_reason=f"IOC+Chase 未全额成交，剩余 {_chase_result['remaining_qty']}"
                                )
                                print(f"   ⚠️ 剩余 {_chase_result['remaining_qty']} 已转入 DLQ")
                        else:
                            # IOC+Chase 失败，fallback 到 MARKET
                            print(f"   ⚠️ IOC+Chasing 失败，fallback 到 MARKET")
                            print(f"🔥 [Phase 1] 提交主订单: {main_order_params.get('type')} {main_order_params.get('side')} {main_order_params.get('quantity')}")
                            main_order = safe_futures_create_order(client, **main_order_params)
                    else:
                        # IOC+Chase 未启用，直接 MARKET
                        print(f"🔥 [Phase 1] 提交主订单: {main_order_params.get('type')} {main_order_params.get('side')} {main_order_params.get('quantity')}")
                        main_order = safe_futures_create_order(client, **main_order_params)
                transaction.main_order_id = main_order.get('orderId')
                
                # 获取真实平均成交价 (Average Fill Price)
                actual_fill_price = float(main_order.get('avgPrice', 0))
                if actual_fill_price <= 0:
                    # avgPrice 可能在异步成交时为 0，查询订单获取最终成交价
                    try:
                        time.sleep(0.3)  # 等待交易所结算
                        order_detail = client.futures_get_order(symbol=symbol, orderId=transaction.main_order_id)
                        actual_fill_price = float(order_detail.get('avgPrice', 0))
                    except Exception:
                        pass
                    if actual_fill_price <= 0:
                        actual_fill_price = price  # 最终兜底：使用信号价格
                        print(f"   ⚠️ 无法获取真实成交价，使用信号价格兜底: {price}")
                
                print(f"   ✅ 主订单成交: orderId={transaction.main_order_id}, 真实均价={actual_fill_price}")
                
                # ==========================================
                # 🔥 Phase 2: 基于真实成交价重新计算止损价，挂 STOP_MARKET 硬止损
                # ==========================================
                # 止损价计算：做多 sl = fill_price - (atr * ATR_MULT)，做空反之
                if atr > 0:
                    if signal_type == 'BUY':
                        stop_loss_price = actual_fill_price - (atr * current_atr_mult * adx_scalar * cfg.get("SL_BUFFER", 1.02))
                    else:
                        stop_loss_price = actual_fill_price + (atr * current_atr_mult * adx_scalar * cfg.get("SL_BUFFER", 1.02))
                else:
                    # 🔥 Fix #7: ATR 不可用时的智能兜底止损（基于最近 N 根 K 线振幅中位数）
                    # 旧逻辑：固定 2% 止损对高波动山寨币（如 SOL）过于粗暴
                    # 新逻辑：计算最近 20 根 K 线的振幅中位数作为动态止损距离
                    try:
                        # 🔥 execute_trade 没有 df 参数，需要临时拉取 K 线数据
                        _fallback_df = get_historical_klines(client, symbol, cfg.get("INTERVAL", "15m"), limit=30) if client else None
                        if _fallback_df is not None and len(_fallback_df) >= 20:
                            # 计算最近 20 根 K 线的振幅（high - low）
                            recent_ranges = (_fallback_df['high'] - _fallback_df['low']).tail(20)
                            median_range = recent_ranges.median()
                            
                            # 使用振幅中位数的 1.5 倍作为止损距离（保守缓冲）
                            fallback_sl_distance = median_range * 1.5
                            
                            if signal_type == 'BUY':
                                stop_loss_price = actual_fill_price - fallback_sl_distance
                            else:
                                stop_loss_price = actual_fill_price + fallback_sl_distance
                            
                            print(f"   📐 智能兜底止损: 使用振幅中位数 {median_range:.4f} × 1.5 = {fallback_sl_distance:.4f}")
                        else:
                            # 数据不足时回退到固定 2%
                            if signal_type == 'BUY':
                                stop_loss_price = actual_fill_price * 0.98
                            else:
                                stop_loss_price = actual_fill_price * 1.02
                            print(f"   ⚠️ K线数据不足，使用固定 2% 兜底止损")
                    except Exception as fallback_e:
                        # 异常时使用固定 2% 保底
                        if signal_type == 'BUY':
                            stop_loss_price = actual_fill_price * 0.98
                        else:
                            stop_loss_price = actual_fill_price * 1.02
                        print(f"   ⚠️ 智能兜底止损计算失败: {fallback_e}，使用固定 2%")
                
                # 🔥 Tick Size 精度格式化（防止交易所 API 报错）
                stop_loss_price = round_to_tick_size(stop_loss_price, symbol)
                
                # 🔥 SL 幻觉防御 Phase 2: 基于真实成交价重算止损校验（LIVE 专用）
                stop_loss_price = _validate_sl_price(
                    stop_loss_price, actual_fill_price, signal_type, atr, label=f"{symbol}-Phase2"
                )
                stop_loss_price = round_to_tick_size(stop_loss_price, symbol)
                
                print(f"   📐 基于真实均价重算止损: fill={actual_fill_price}, ATR={atr:.4f}, ATR_MULT={current_atr_mult}, SL={stop_loss_price}")
                
                # 构建 STOP_MARKET 止损单参数
                sl_side = SIDE_SELL if signal_type == 'BUY' else SIDE_BUY
                stop_loss_params = {
                    'symbol': symbol,
                    'side': sl_side,
                    'type': 'STOP_MARKET',
                    'quantity': position_info['quantity'],
                    'stopPrice': stop_loss_price,
                }
                
                # 🔥 核心：reduceOnly 参数处理（对冲模式 vs 单向模式）
                if config.SYSTEM_CONFIG.get("HEDGE_MODE_ENABLED", False):
                    # 对冲模式：使用 positionSide 隐式 reduceOnly
                    stop_loss_params['positionSide'] = 'LONG' if signal_type == 'BUY' else 'SHORT'
                else:
                    # 单向模式：必须显式传 reduceOnly=True
                    stop_loss_params['positionSide'] = 'BOTH'
                    stop_loss_params['reduceOnly'] = True
                
                if bot_sl_order_id:
                    stop_loss_params['newClientOrderId'] = bot_sl_order_id
                
                # 提交 STOP_MARKET 止损单
                try:
                    sl_order = safe_futures_create_order(client, **stop_loss_params)
                    transaction.stop_loss_order_id = sl_order.get('orderId')
                    print(f"   ✅ STOP_MARKET 硬止损已挂出: orderId={transaction.stop_loss_order_id}, stopPrice={stop_loss_price}")
                except Exception as sl_e:
                    # 🔥 止损单挂单失败：这是致命错误，必须立即回滚主订单
                    error_msg = str(sl_e)
                    print(f"   ❌ STOP_MARKET 止损单提交失败: {error_msg}")
                    logger.critical(f"🚨 止损单提交失败，触发紧急回滚: {error_msg}")
                    
                    send_tg_alert(
                        f"🔴 <b>[致命：止损单挂单失败]</b>\n\n"
                        f"币种: {html.escape(symbol)}\n"
                        f"主订单已成交: {transaction.main_order_id}\n"
                        f"止损价: {stop_loss_price}\n"
                        f"错误: {html.escape(error_msg[:150])}\n\n"
                        f"⚠️ 正在紧急回滚主订单..."
                    )
                    
                    # 触发回滚：市价反向平仓
                    transaction.rollback()
                    raise Exception(f"止损单提交失败，已回滚主订单: {error_msg[:100]}")
                
                # 提交事务
                if transaction.commit():
                    # 🔒 线程锁保护：实盘开仓写入 config.ACTIVE_POSITIONS
                    with positions_lock:
                        # 多重子仓位：实盘也使用列表存储
                        if key_sym not in config.ACTIVE_POSITIONS:
                            config.ACTIVE_POSITIONS[key_sym] = []
                        elif not isinstance(config.ACTIVE_POSITIONS[key_sym], list):
                            config.ACTIVE_POSITIONS[key_sym] = [config.ACTIVE_POSITIONS[key_sym]]
                        
                        config.ACTIVE_POSITIONS[key_sym].append({
                            'entry': actual_fill_price,
                            'sl': stop_loss_price,
                            'qty': position_info['quantity'],
                            'type': pos_type,
                            'real_symbol': symbol,
                            'timestamp': datetime.now(),
                            'trade_id': main_order['orderId'],
                            'sl_order_id': sl_order['orderId'],
                            'simulated': False,
                            'transaction_committed': True,
                            'order_identity': order_identity,  # 🔥 记录Maker/Taker身份
                            'fill_price': actual_fill_price,
                            'client_order_id': bot_order_id if bot_order_id else '',  # 🔥 记录机器人订单标签
                            'atr': atr  # 🔥 弹性收割 v2: 存储开仓时的ATR，用于自适应保本计算
                        })
                    save_data()
                    
                    # 🔥 UI 状态广播：开仓成功
                    notify_ui_update("POSITION_CHANGE")
                    
                    # 🔥 EQM: 记录开仓订单执行质量
                    if EQM_ENABLED:
                        try:
                            _eqm_submit_ts = time.time()
                            get_eqm().record_entry_order(
                                symbol=symbol, side=signal_type,
                                quantity=position_info['quantity'],
                                expected_price=price,
                                expected_slippage=0,
                                actual_fill_price=actual_fill_price,
                                order_id=main_order['orderId'],
                                order_identity=order_identity,
                                submit_ts=_eqm_submit_ts,
                                fill_ts=time.time(),
                            )
                        except Exception as eqm_e:
                            logger.debug(f"EQM record_entry_order 失败: {eqm_e}")
                    
                    identity_emoji = "💎" if order_identity == "MAKER" else "⚡"
                    send_tg_alert(
                        f"✅ <b>[实盘开仓确认]</b>\n"
                        f"币种: {html.escape(symbol)}\n"
                        f"动作: 开{'多' if pos_type=='LONG' else '空'} ({signal_type})\n"
                        f"开仓价: {actual_fill_price}\n"
                        f"止损位: {stop_loss_price}\n"
                        f"主订单ID: {main_order['orderId']}\n"
                        f"止损单ID: {sl_order['orderId']}\n"
                        f"执行方式: {identity_emoji} {order_identity}"
                    )
                    
                    return {
                        'success': True,
                        'trade_id': main_order['orderId'],
                        'sl_order_id': sl_order['orderId'],
                        'simulated': False,
                        'order_identity': order_identity,
                        'fill_price': actual_fill_price,
                        'message': f"开仓交易执行成功({order_identity})，止损价: ${stop_loss_price}"
                    }
                else:
                    return {'success': False, 'message': "订单事务提交失败，已回滚"}
                    
            except Exception as e:
                error_msg = str(e)
                print(f"❌ 订单提交异常: {error_msg}")
                return {
                    'success': False,
                    'message': f"订单提交失败并已回滚: {error_msg[:100]}",
                    'rollback_attempted': transaction.rollback_attempted
                }
        
        # 平仓逻辑
        elif position_action.startswith('EXIT'):
            pos_type = 'LONG' if position_action == 'EXIT_LONG' else 'SHORT'
            key_sym = f"{symbol}_{pos_type}"
            
            if key_sym not in config.ACTIVE_POSITIONS and symbol in config.ACTIVE_POSITIONS:
                key_sym = symbol
                
            if key_sym in config.ACTIVE_POSITIONS:
                # 🔒 线程锁保护：实盘平仓修改/删除 config.ACTIVE_POSITIONS
                with positions_lock:
                    # 多重子仓位：从列表中取出最早的订单（FIFO）
                    positions_list = config.ACTIVE_POSITIONS[key_sym] if isinstance(config.ACTIVE_POSITIONS[key_sym], list) else [config.ACTIVE_POSITIONS[key_sym]]
                    
                    if not positions_list:
                        return {'success': False, 'message': f"没有{symbol}的持仓可平仓"}
                    
                    position = positions_list.pop(0)
                    real_symbol = position.get('real_symbol', symbol)
                
                # 取消止损单
                try:
                    if position.get('sl_order_id'):
                        client.futures_cancel_order(symbol=real_symbol, orderId=position['sl_order_id'])
                except:
                    pass
                
                act_side = SIDE_SELL if position['type'] == 'LONG' else SIDE_BUY
                
                # ====== 动态构建 positionSide 参数（对冲模式平仓支持）======
                hedge_enabled = config.SYSTEM_CONFIG.get("HEDGE_MODE_ENABLED", False)
                close_params = {}
                
                if hedge_enabled:
                    # 对冲模式：平仓时必须指定 positionSide，不能传 reduceOnly
                    close_params['positionSide'] = position['type']  # 'LONG' or 'SHORT'
                    print(f"   🔀 对冲模式平仓：positionSide={close_params['positionSide']}")
                else:
                    # 单向模式：使用 BOTH + reduceOnly
                    close_params['positionSide'] = 'BOTH'
                    close_params['reduceOnly'] = True
                    print(f"   🔀 单向模式平仓：positionSide=BOTH, reduceOnly=True")
                
                # 平仓 (🔥 Fix #3: 使用 safe_futures_create_order 确保环境拦截)
                order = safe_futures_create_order(
                    client,
                    symbol=real_symbol,
                    side=act_side,
                    type=FUTURE_ORDER_TYPE_MARKET,
                    quantity=position['qty'],
                    **close_params
                )
                
                # 计算盈亏（含手续费）
                entry_price = position['entry']
                exit_price = float(order.get('avgPrice', price))  # 🔥 P0修复#1: 使用真实成交价
                qty = position['qty']
                
                # 手续费：双边万四（开仓+平仓共两次）
                commission = (entry_price + exit_price) * qty * config.SYSTEM_CONFIG["COMMISSION_RATE"]
                
                if position['type'] == 'LONG':
                    gross_pnl = (exit_price - entry_price) * qty
                else:
                    gross_pnl = (entry_price - exit_price) * qty
                
                net_pnl = gross_pnl - commission
                
                # ====== 连续亏损统计（断路器触发检测）======
                global ENGINE_STATE
                if net_pnl < 0:
                    ENGINE_STATE['consecutive_losses'] += 1
                    max_consec = config.SYSTEM_CONFIG.get("MAX_CONSECUTIVE_LOSSES", 3)
                    if ENGINE_STATE['consecutive_losses'] >= max_consec:
                        # 触发断路器：禁止开仓 N 分钟
                        breaker_mins = config.SYSTEM_CONFIG.get("BREAKER_COOLDOWN_MINS", 30)
                        ENGINE_STATE['breaker_until'] = time.time() + (breaker_mins * 60)
                        send_tg_alert(
                            f"🚨 <b>[连续亏损断路器触发]</b>\n"
                            f"连续亏损: {ENGINE_STATE['consecutive_losses']} 笔\n"
                            f"冷却时间: {breaker_mins} 分钟\n"
                            f"期间将拒绝所有新开仓！"
                        )
                        print(f"🚨 连续亏损断路器触发！冷却 {breaker_mins} 分钟")
                else:
                    # 盈利则重置计数器
                    ENGINE_STATE['consecutive_losses'] = 0
                
                # 🔒 线程锁保护：更新持仓列表
                with positions_lock:
                    # 更新持仓列表：如果列表为空则删除key，否则保留剩余订单
                    if not positions_list:
                        config.ACTIVE_POSITIONS.pop(key_sym)
                    else:
                        config.ACTIVE_POSITIONS[key_sym] = positions_list
                
                # 🔥 补齐交易历史记录（实盘平仓）
                trade_record = {
                    'symbol': symbol,
                    'type': position['type'],
                    'entry': entry_price,
                    'exit': exit_price,
                    'qty': qty,
                    'pnl': net_pnl,
                    'gross_pnl': gross_pnl,
                    'commission': commission,
                    'exit_reason': 'SIGNAL_EXIT',
                    'trade_id': order['orderId'],
                    'timestamp': datetime.now().isoformat(),
                    'strategy_mode': cfg.get("STRATEGY_MODE", "STANDARD")
                }
                with state_lock:
                    config.TRADE_HISTORY.append(trade_record)
                    if len(config.TRADE_HISTORY) > 1000:
                        config.TRADE_HISTORY[:] = config.TRADE_HISTORY[-1000:]
                
                # 🔥 O(1) 极速追加到 Redis，无需全量覆写
                try:
                    from redis_manager import redis_db
                    running_mode = config.SYSTEM_CONFIG.get("RUNNING_MODE", "SANDBOX")
                    prefix = redis_db.get_key_prefix(running_mode)
                    redis_db.append_to_list(f"{prefix}:history", trade_record, max_length=1000)
                except Exception as redis_e:
                    logger.warning(f"⚠️ Redis追加失败（不影响本地存储）: {redis_e}")
                
                # 🔥 利滚利：平仓后刷新 BENCHMARK_CASH，使凯利公式基于最新资金量计算
                _refresh_benchmark_after_close(client)
                
                # 🔥 EQM: 记录平仓订单执行质量
                if EQM_ENABLED:
                    try:
                        get_eqm().record_exit_order(
                            symbol=symbol, side=act_side,
                            quantity=qty,
                            expected_price=price,
                            actual_fill_price=float(order.get('avgPrice', price)),
                            order_id=order['orderId'],
                            pnl=net_pnl,
                        )
                    except Exception as eqm_e:
                        logger.debug(f"EQM record_exit_order 失败: {eqm_e}")
                
                save_data()
                
                # ====== 保险库自动触发：每次盈利平仓后检查是否达到抽水阈值 ======
                if net_pnl > 0:
                    try:
                        logger.info(f"💰 盈利平仓完成，触发保险库检查...")
                        vault_result = execute_vault_transfer(client)
                        if vault_result['success']:
                            logger.info(f"✅ 保险库自动划转成功: ${vault_result['amount']:.2f}")
                        else:
                            logger.debug(f"ℹ️ 保险库检查: {vault_result['message']}")
                    except Exception as vault_e:
                        logger.error(f"⚠️ 保险库自动检查异常: {vault_e}")
                
                send_tg_alert(
                    f"🛡️ <b>[实盘平仓确认]</b>\n"
                    f"币种: {html.escape(symbol)}\n"
                    f"方向: 平{'多' if position['type']=='LONG' else '空'}\n"
                    f"平仓价: {price}\n"
                    f"毛利: ${gross_pnl:.2f}\n"
                    f"手续费: ${commission:.2f}\n"
                    f"净利: ${net_pnl:.2f}\n"
                    f"交易ID: {order['orderId']}\n"
                    f"剩余子仓: {len(positions_list)} 笔\n"
                    f"连亏计数: {ENGINE_STATE['consecutive_losses']}"
                )
                
                return {
                    'success': True,
                    'trade_id': order['orderId'],
                    'pnl': net_pnl,
                    'gross_pnl': gross_pnl,
                    'commission': commission,
                    'simulated': False,
                    'message': f"平仓交易执行成功，净利: ${net_pnl:.2f}"
                }
            else:
                return {'success': False, 'message': f"没有{symbol}的持仓可平仓"}

    except Exception as e:
        error_msg = str(e)
        print(f"❌ 执行交易失败: {error_msg}")
        from utils import send_tg_alert
        import html
        send_tg_alert(f"❌ <b>[交易执行异常]</b>\n币种: {html.escape(symbol)}\n错误: {html.escape(error_msg[:100])}")
        return {'success': False, 'message': f"交易执行失败: {error_msg[:100]}"}


def deep_reconcile(client):
    """
    深度对账 - 这是权重消耗大户
    
    核心功能：
    1. 🔥 注入检查：如果权重已满 90%，强制进入冷却期
    2. 执行完整的账户对账逻辑（余额、持仓、止损单）
    3. 自动修复不一致状态
    
    权重消耗预估：
    - futures_account(): 5 权重
    - futures_get_open_orders(): 1 权重/币种
    - 总计约 10-20 权重（取决于监控币种数量）
    
    Args:
        client: Binance 客户端
    
    Returns:
        dict: {
            'success': bool,
            'message': str,
            'synced_positions': int,
            'fixed_stop_loss': int,
            'weight_status': dict
        }
    """
    from utils import send_tg_msg, send_tg_alert
    
    if client is None:
        return {
            'success': False,
            'message': '币安客户端未连接',
            'synced_positions': 0,
            'fixed_stop_loss': 0,
            'weight_status': {}
        }
    
    try:
        # 🔥 注入检查：如果权重已满 90%，强制进入冷却期
        if API_WEIGHT_MONITOR_ENABLED:
            status = get_weight_status()
            if status['usage_ratio'] > 0.9:
                cooldown_seconds = 10
                logger.warning(f"⚠️ 权重过高 ({status['usage_percent']:.1f}%)，延迟对账 {cooldown_seconds} 秒以防限流")
                send_tg_msg(
                    f"⚠️ <b>深度对账延迟</b>\n\n"
                    f"API权重: {status['current_weight']}/{status['max_weight']} ({status['usage_percent']:.1f}%)\n"
                    f"冷却时间: {cooldown_seconds}秒\n\n"
                    f"🛡️ 防限流保护已激活"
                )
                time.sleep(cooldown_seconds)
        
        # 开始对账
        send_tg_msg("🔄 <b>开始深度对账...</b>")
        
        # ====== 步骤1：账户余额对账 ======
        try:
            acc_info = client.futures_account()
            total_margin_balance = float(acc_info.get('totalMarginBalance', 0))
            
            with state_lock:
                old_benchmark = config.SYSTEM_CONFIG.get("BENCHMARK_CASH", 0)
                if abs(total_margin_balance - old_benchmark) > 0.01:
                    config.SYSTEM_CONFIG["BENCHMARK_CASH"] = total_margin_balance
                    save_data()
                    logger.info(f"💰 基准本金已更新: ${old_benchmark:.2f} → ${total_margin_balance:.2f}")
        except Exception as balance_e:
            logger.error(f"⚠️ 余额对账失败: {balance_e}")
        
        # ====== 步骤2：持仓对账（调用现有的 sync_positions 逻辑）======
        real_positions = acc_info.get('positions', [])
        synced_count = 0
        fixed_sl_count = 0
        new_active = {}
        
        for pos in real_positions:
            amt = float(pos['positionAmt'])
            sym = pos['symbol']
            
            if amt != 0:
                pos_type = 'LONG' if amt > 0 else 'SHORT'
                qty = abs(amt)
                entry_p = float(pos['entryPrice'])
                key_sym = f"{sym}_{pos_type}"
                
                # 🔥 外部手动单隔离检查（复用 sync_positions 的逻辑）
                is_bot_position = True
                if POSITION_ISOLATION_ENABLED:
                    try:
                        open_orders = client.futures_get_open_orders(symbol=sym)
                        for order in open_orders:
                            if order.get('type') not in ['STOP_MARKET', 'TAKE_PROFIT_MARKET']:
                                client_order_id = order.get('clientOrderId', '')
                                if not is_bot_order(client_order_id):
                                    is_bot_position = False
                                    logger.warning(f"🚫 [{sym}] 检测到外部手动单，已隔离")
                                    break
                    except Exception as e:
                        logger.warning(f"⚠️ [{sym}] 订单查询异常: {e}")
                        is_bot_position = False
                
                if not is_bot_position:
                    continue
                
                # 止损单对账
                old_pos_data = config.ACTIVE_POSITIONS.get(key_sym) or {}
                sl_est = entry_p * (0.98 if pos_type == 'LONG' else 1.02)
                
                real_sl_order_id = ""
                real_sl_price = sl_est
                sl_found = False
                
                try:
                    open_orders = client.futures_get_open_orders(symbol=sym)
                    expected_sl_side = 'SELL' if pos_type == 'LONG' else 'BUY'
                    
                    for order in open_orders:
                        if (order.get('type') == 'STOP_MARKET' and 
                            order.get('side') == expected_sl_side):
                            real_sl_order_id = order['orderId']
                            real_sl_price = float(order.get('stopPrice', sl_est))
                            sl_found = True
                            break
                    
                    # 自动补挂止损单
                    if not sl_found:
                        from utils import round_to_tick_size
                        auto_sl_price = entry_p * (0.98 if pos_type == 'LONG' else 1.02)
                        auto_sl_price = round_to_tick_size(auto_sl_price, sym)
                        
                        sl_side = 'SELL' if pos_type == 'LONG' else 'BUY'
                        sl_order_params = {
                            'symbol': sym,
                            'side': sl_side,
                            'type': 'STOP_MARKET',
                            'quantity': qty,
                            'stopPrice': auto_sl_price,
                        }
                        
                        if config.SYSTEM_CONFIG.get("HEDGE_MODE_ENABLED", False):
                            sl_order_params['positionSide'] = pos_type
                        else:
                            sl_order_params['positionSide'] = 'BOTH'
                            sl_order_params['reduceOnly'] = True
                        
                        try:
                            sl_order = client.futures_create_order(**sl_order_params)
                            real_sl_order_id = sl_order['orderId']
                            real_sl_price = auto_sl_price
                            sl_found = True
                            fixed_sl_count += 1
                            logger.info(f"✅ [{sym}] 止损单自动补挂成功")
                        except Exception as create_e:
                            logger.error(f"❌ [{sym}] 自动补挂止损单失败: {create_e}")
                
                except Exception as e:
                    logger.warning(f"⚠️ [{sym}] 查询止损挂单异常: {e}")
                
                # 构建同步后的持仓数据
                synced_pos = {
                    'entry': entry_p,
                    'sl': real_sl_price,
                    'qty': qty,
                    'type': pos_type,
                    'real_symbol': sym,
                    'timestamp': datetime.now(),
                    'trade_id': f"SYNC_{int(time.time())}",
                    'sl_order_id': real_sl_order_id if sl_found else "",
                    'simulated': False,
                    'sl_verified': sl_found
                }
                
                if key_sym not in new_active:
                    new_active[key_sym] = [synced_pos]
                else:
                    new_active[key_sym].append(synced_pos)
                synced_count += 1
        
        # 更新持仓数据
        with positions_lock:
            config.ACTIVE_POSITIONS.clear()
            config.ACTIVE_POSITIONS.update(new_active)
        save_data()
        
        # 获取最终权重状态
        weight_status = {}
        if API_WEIGHT_MONITOR_ENABLED:
            weight_status = get_weight_status()
        
        # 发送对账报告
        msg = "⚖️ <b>深度对账完成</b>\n\n"
        msg += f"✅ 同步持仓: {synced_count} 个\n"
        msg += f"🔧 修复止损单: {fixed_sl_count} 个\n"
        if API_WEIGHT_MONITOR_ENABLED:
            msg += f"\n📊 API权重: {weight_status['current_weight']}/{weight_status['max_weight']} ({weight_status['usage_percent']:.1f}%)"
        
        send_tg_msg(msg)
        
        return {
            'success': True,
            'message': '深度对账完成',
            'synced_positions': synced_count,
            'fixed_stop_loss': fixed_sl_count,
            'weight_status': weight_status
        }
        
    except Exception as e:
        error_msg = f"深度对账异常: {str(e)[:100]}"
        logger.error(f"❌ {error_msg}", exc_info=True)
        send_tg_alert(f"❌ <b>[深度对账失败]</b>\n\n{error_msg}")
        
        return {
            'success': False,
            'message': error_msg,
            'synced_positions': 0,
            'fixed_stop_loss': 0,
            'weight_status': {}
        }


def sync_positions(client, chat_id):
    """同步币安真实仓位到本地（含止损单真实对账 + 自动补挂 + 🔥 外部手动单隔离）
    
    ⚠️ 重要：此函数在任何模式下都会穿透查询币安真实持仓
    不受 RUNNING_MODE 限制（SANDBOX/LIVE 均可调用）
    
    🔥 v3.0 新增：外部手动单隔离（安全加固）
    - 在遍历交易所返回的 real_positions 时，必须查询该持仓的原始 clientOrderId
    - 如果 clientOrderId 不是以 WJ_BOT 开头，严禁将其存入 config.ACTIVE_POSITIONS
    - 必须将其视为"外部手动单"并直接跳过，同时发送告警
    
    🔥 v2.0 新增：自动补挂止损单
    - 如果发现交易所存在持仓但不存在对应的 STOP_MARKET 挂单
    - 系统将立即自动补挂止损单，而不是仅仅发警告
    """
    from utils import send_tg_msg, send_tg_alert, round_to_tick_size
    import html
    
    if client is None:
        send_tg_msg("⚠️ 币安客户端未连接，无法同步真实仓位。")
        return
    
    # 移除所有模式限制，确保任何时候都能查询真实持仓
    send_tg_msg("🔄 <b>正在与交易所服务器进行对账同步...</b>")
    
    try:
        acc_info = client.futures_account()
        real_positions = acc_info.get('positions', [])
        
        synced_count = 0
        cleared_count = 0
        sl_matched_count = 0
        sl_missing_count = 0
        sl_auto_created_count = 0
        external_manual_orders_count = 0  # 🔥 外部手动单计数
        new_active = {}
        
        for pos in real_positions:
            amt = float(pos['positionAmt'])
            sym = pos['symbol']
            
            if amt != 0:
                pos_type = 'LONG' if amt > 0 else 'SHORT'
                qty = abs(amt)
                entry_p = float(pos['entryPrice'])
                key_sym = f"{sym}_{pos_type}"
                
                # ====== 🔥 v3.0 新增：外部手动单隔离检查 ======
                # 步骤1：查询该持仓对应的原始订单，获取 clientOrderId
                is_bot_position = False
                try:
                    print(f"   🔍 [{sym}] 正在验证持仓来源...")
                    
                    # 🔥 使用仓位隔离模块的 is_bot_order 函数
                    if POSITION_ISOLATION_ENABLED:
                        # 调用 futures_get_open_orders 获取所有挂单
                        open_orders = client.futures_get_open_orders(symbol=sym)
                        
                        # 查找该持仓方向对应的主订单（非止损单）
                        for order in open_orders:
                            if order.get('type') not in ['STOP_MARKET', 'TAKE_PROFIT_MARKET']:
                                order_side = order.get('side')
                                client_order_id = order.get('clientOrderId', '')
                                
                                # 检查订单方向是否与持仓方向匹配
                                if (pos_type == 'LONG' and order_side == 'BUY') or \
                                   (pos_type == 'SHORT' and order_side == 'SELL'):
                                    
                                    # 🔥 关键检查：使用 is_bot_order 验证
                                    if is_bot_order(client_order_id):
                                        is_bot_position = True
                                        print(f"   ✅ [{sym}] 检测到机器人订单: {client_order_id}")
                                        break
                                    else:
                                        # 外部手动单：不是机器人订单
                                        external_manual_orders_count += 1
                                        print(f"   🚫 [{sym}] 检测到外部手动单: {client_order_id}")
                                        
                                        # 发送告警
                                        send_tg_alert(
                                            f"🚫 <b>[外部手动单检测]</b>\n\n"
                                            f"币种: {html.escape(sym)}\n"
                                            f"方向: {'多头' if pos_type == 'LONG' else '空头'}\n"
                                            f"持仓量: {qty}\n"
                                            f"开仓价: {entry_p}\n"
                                            f"订单ID: {html.escape(client_order_id)}\n\n"
                                            f"⚠️ 该持仓不是机器人创建的，已被隔离\n"
                                            f"系统将跳过此持仓的管理"
                                        )
                                        break
                        
                        # 🔥 隔离逻辑：如果不是机器人订单，直接跳过此持仓
                        if not is_bot_position:
                            print(f"   🔒 [{sym}] 外部手动单已隔离，跳过此持仓的同步")
                            continue
                    else:
                        # 仓位隔离模块未启用，默认允许所有持仓
                        is_bot_position = True
                        print(f"   ⚠️ [{sym}] 仓位隔离模块未启用，跳过验证")
                
                except Exception as order_query_e:
                    print(f"   ⚠️ [{sym}] 查询订单信息异常: {order_query_e}")
                    # 查询异常时保守处理：跳过此持仓
                    send_tg_alert(
                        f"⚠️ <b>[订单查询异常]</b>\n\n"
                        f"币种: {html.escape(sym)}\n"
                        f"错误: {html.escape(str(order_query_e)[:100])}\n\n"
                        f"⚠️ 无法验证该持仓的订单来源，已跳过同步"
                    )
                    continue
                # ====== 原有逻辑：止损单对账 ======
                old_pos_data = config.ACTIVE_POSITIONS.get(key_sym) or config.ACTIVE_POSITIONS.get(sym) or {}
                
                # 🔥 防子单坍塌：如果本地子单数量 > 1 且总量与交易所一致，保留本地子单列表
                local_sub_orders = []
                if isinstance(old_pos_data, list) and len(old_pos_data) > 1:
                    local_sub_orders = old_pos_data
                elif isinstance(old_pos_data, list) and len(old_pos_data) == 1:
                    local_sub_orders = old_pos_data
                elif isinstance(old_pos_data, dict) and old_pos_data:
                    local_sub_orders = [old_pos_data]
                
                # 计算本地子单总数量
                local_total_qty = sum(sub.get('qty', 0) for sub in local_sub_orders)
                
                # 判断本地子单总量是否与交易所一致（允许万分之一精度误差）
                qty_tolerance = qty * 0.0001  # 万分之一
                local_matches_exchange = (
                    len(local_sub_orders) > 1 and
                    abs(local_total_qty - qty) <= max(qty_tolerance, 1e-8)
                )
                
                # 提取旧的止损估计值（兼容 dict 和 list）
                if isinstance(old_pos_data, dict) and old_pos_data:
                    sl_est = old_pos_data.get('sl', entry_p * (0.98 if pos_type == 'LONG' else 1.02))
                elif isinstance(old_pos_data, list) and old_pos_data:
                    sl_est = old_pos_data[0].get('sl', entry_p * (0.98 if pos_type == 'LONG' else 1.02))
                else:
                    sl_est = entry_p * (0.98 if pos_type == 'LONG' else 1.02)
                
                # ====== 止损单真实对账：从交易所查询实际挂单 ======
                real_sl_order_id = ""
                real_sl_price = sl_est
                sl_found = False
                
                try:
                    open_orders = client.futures_get_open_orders(symbol=sym)
                    # 止损单方向：多仓止损挂 SELL，空仓止损挂 BUY
                    expected_sl_side = 'SELL' if pos_type == 'LONG' else 'BUY'
                    
                    for order in open_orders:
                        if (order.get('type') == 'STOP_MARKET' and 
                            order.get('side') == expected_sl_side):
                            real_sl_order_id = order['orderId']
                            real_sl_price = float(order.get('stopPrice', sl_est))
                            sl_found = True
                            sl_matched_count += 1
                            print(f"   ✅ [{sym}] 找到止损单: orderId={real_sl_order_id}, stopPrice={real_sl_price}")
                            break
                    
                    # 🔥 v2.0 新增：自动补挂止损单
                    if not sl_found:
                        sl_missing_count += 1
                        print(f"   🔴 [{sym}] 未找到止损单！正在自动补挂...")
                        
                        # 计算止损价（使用保守的 2% 止损）
                        if pos_type == 'LONG':
                            auto_sl_price = entry_p * 0.98
                        else:
                            auto_sl_price = entry_p * 1.02
                        
                        auto_sl_price = round_to_tick_size(auto_sl_price, sym)
                        
                        # 构建止损单参数
                        sl_side = 'SELL' if pos_type == 'LONG' else 'BUY'
                        sl_order_params = {
                            'symbol': sym,
                            'side': sl_side,
                            'type': 'STOP_MARKET',
                            'quantity': qty,
                            'stopPrice': auto_sl_price,
                        }
                        
                        # 对冲模式需要指定 positionSide
                        if config.SYSTEM_CONFIG.get("HEDGE_MODE_ENABLED", False):
                            sl_order_params['positionSide'] = pos_type
                        else:
                            sl_order_params['positionSide'] = 'BOTH'
                            sl_order_params['reduceOnly'] = True
                        
                        try:
                            # 提交止损单
                            sl_order = client.futures_create_order(**sl_order_params)
                            real_sl_order_id = sl_order['orderId']
                            real_sl_price = auto_sl_price
                            sl_found = True
                            sl_auto_created_count += 1
                            
                            print(f"   ✅ [{sym}] 止损单自动补挂成功: orderId={real_sl_order_id}, stopPrice={auto_sl_price}")
                            send_tg_alert(
                                f"✅ <b>[止损单自动补挂成功]</b>\n\n"
                                f"币种: {html.escape(sym)}\n"
                                f"方向: {'多头' if pos_type == 'LONG' else '空头'}\n"
                                f"持仓量: {qty}\n"
                                f"开仓价: {entry_p}\n"
                                f"止损价: {auto_sl_price}\n"
                                f"止损单ID: {real_sl_order_id}\n\n"
                                f"🛡️ 系统已自动为该持仓补挂止损保护！"
                            )
                        except Exception as create_e:
                            print(f"   ❌ [{sym}] 自动补挂止损单失败: {create_e}")
                            send_tg_alert(
                                f"🔴 <b>[高危报警：止损单缺失且补挂失败]</b>\n\n"
                                f"币种: {html.escape(sym)}\n"
                                f"方向: {'多头' if pos_type == 'LONG' else '空头'}\n"
                                f"持仓量: {qty}\n"
                                f"开仓价: {entry_p}\n\n"
                                f"⚠️ 交易所未检测到对应的 STOP_MARKET 止损单！\n"
                                f"系统尝试自动补挂但失败: {html.escape(str(create_e)[:100])}\n\n"
                                f"🚨 该持仓当前处于<b>无止损裸奔</b>状态，请立即手动处理！"
                            )
                
                except Exception as e:
                    print(f"   ⚠️ [{sym}] 查询止损挂单异常: {e}")
                    send_tg_alert(
                        f"⚠️ <b>[止损对账异常]</b>\n"
                        f"币种: {html.escape(sym)}\n"
                        f"错误: {html.escape(str(e)[:100])}\n"
                        f"请手动检查该持仓的止损单状态！"
                    )
                
                # 🔥 防子单坍塌：本地多笔子单总量与交易所一致时，保留本地子单列表
                if local_matches_exchange:
                    print(f"   🔒 [{sym}] 本地 {len(local_sub_orders)} 笔子单总量={local_total_qty} ≈ 交易所={qty}，保留子单列表")
                    new_active[key_sym] = local_sub_orders
                else:
                    # 本地无子单或数量不匹配，用交易所数据生成单笔合并记录
                    old_pos = old_pos_data if isinstance(old_pos_data, dict) else (old_pos_data[0] if isinstance(old_pos_data, list) and old_pos_data else {})
                    synced_pos = {
                        'entry': entry_p,
                        'sl': real_sl_price,
                        'qty': qty,
                        'type': pos_type,
                        'real_symbol': sym,
                        'timestamp': old_pos.get('timestamp', datetime.now()) if isinstance(old_pos, dict) else datetime.now(),
                        'trade_id': old_pos.get('trade_id', f"SYNC_{int(time.time())}") if isinstance(old_pos, dict) else f"SYNC_{int(time.time())}",
                        'sl_order_id': real_sl_order_id if sl_found else (old_pos.get('sl_order_id', "") if isinstance(old_pos, dict) else ""),
                        'simulated': False,
                        'sl_verified': sl_found
                    }
                    
                    if key_sym not in new_active:
                        new_active[key_sym] = [synced_pos]
                    else:
                        new_active[key_sym].append(synced_pos)
                synced_count += 1
        
        for old_sym in config.ACTIVE_POSITIONS.keys():
            if old_sym not in new_active:
                old_val = config.ACTIVE_POSITIONS[old_sym]
                # 兼容 list 和 dict 两种存储格式
                if isinstance(old_val, list):
                    is_simulated = all(p.get('simulated', False) for p in old_val) if old_val else False
                elif isinstance(old_val, dict):
                    is_simulated = old_val.get('simulated', False)
                else:
                    is_simulated = False
                if not is_simulated:
                    cleared_count += 1
        
        # 🔒 线程锁保护：sync_positions 更新 config.ACTIVE_POSITIONS
        with positions_lock:
            config.ACTIVE_POSITIONS.clear()
            config.ACTIVE_POSITIONS.update(new_active)
        save_data()
        
        msg = "⚖️ <b>持仓对账完成</b>\n\n"
        msg += f"✅ 同步到真实持仓: {synced_count} 个\n"
        msg += f"🧹 清理本地死仓: {cleared_count} 个\n"
        msg += f"🛡️ 止损单已核实: {sl_matched_count} 个\n"
        if sl_auto_created_count > 0:
            msg += f"🔧 <b>止损单自动补挂: {sl_auto_created_count} 个</b>\n"
        if sl_missing_count > sl_auto_created_count:
            msg += f"🔴 <b>止损单缺失且补挂失败: {sl_missing_count - sl_auto_created_count} 个（请立即处理！）</b>\n"
        if external_manual_orders_count > 0:
            msg += f"🚫 <b>外部手动单已隔离: {external_manual_orders_count} 个</b>\n"
        
        send_tg_msg(msg)
        
    except Exception as e:
        send_tg_msg(f"❌ 同步异常: {str(e)[:100]}")


def emergency_close_all(client, chat_id):
    """一键全平功能（支持多重子仓位列表）"""
    from utils import send_tg_msg
    from binance.enums import SIDE_BUY, SIDE_SELL, FUTURE_ORDER_TYPE_MARKET
    
    with positions_lock:
        if not config.ACTIVE_POSITIONS:
            send_tg_msg("📭 本地记录当前没有活跃持仓可平。")
            return
        
        symbols_to_close = list(config.ACTIVE_POSITIONS.keys())
    
    send_tg_msg("⏳ <b>正在执行一键全平指令...</b>")
    closed_count = 0
    failed_syms = []
    
    for key_sym in symbols_to_close:
        try:
            # 🔥 支持列表形式的多笔订单
            positions_data = config.ACTIVE_POSITIONS[key_sym]
            if not isinstance(positions_data, list):
                positions_data = [positions_data]  # 兼容旧格式
            
            real_symbol = key_sym.split('_')[0] if '_' in key_sym else key_sym
            
            # 遍历该方向下的所有子订单
            for position in positions_data:
                try:
                    if not position.get('simulated', False) and client:
                        # 取消该订单的止损单
                        if position.get('sl_order_id'):
                            try:
                                client.futures_cancel_order(
                                    symbol=real_symbol, 
                                    orderId=position['sl_order_id']
                                )
                            except:
                                pass
                        
                        # 精准平仓：只平掉该笔订单的数量
                        act_side = SIDE_SELL if position['type'] == 'LONG' else SIDE_BUY
                        
                        # 动态构建平仓参数（对冲模式兼容）
                        close_params = {}
                        if config.SYSTEM_CONFIG.get("HEDGE_MODE_ENABLED", False):
                            close_params['positionSide'] = position['type']
                        else:
                            close_params['positionSide'] = 'BOTH'
                            close_params['reduceOnly'] = True
                        
                        client.futures_create_order(
                            symbol=real_symbol,
                            side=act_side,
                            type=FUTURE_ORDER_TYPE_MARKET,
                            quantity=position['qty'],
                            **close_params
                        )
                        print(f"   ✅ 已平仓 {key_sym} 子订单 [Trade_ID={position.get('trade_id')}], 数量: {position['qty']}")
                    
                    closed_count += 1
                    
                except Exception as sub_e:
                    print(f"   ❌ 平仓子订单失败 {key_sym} [Trade_ID={position.get('trade_id')}]: {sub_e}")
                    failed_syms.append(f"{key_sym}[{position.get('trade_id')}]")
            
            # 清空该方向的所有持仓
            with positions_lock:
                config.ACTIVE_POSITIONS.pop(key_sym, None)
            
        except Exception as e:
            failed_syms.append(key_sym)
            print(f"❌ [一键全平] 处理 {key_sym} 失败: {e}")
    
    save_data()
    
    msg = "🛑 <b>一键全平报告</b>\n\n"
    msg += f"✅ 成功平仓子订单数: {closed_count}\n"
    if failed_syms:
        msg += f"❌ 平仓失败: {', '.join(failed_syms)}\n"
    
    send_tg_msg(msg)


# ==========================================
# 🔥 MTF 多周期共振：高周期 EMA 趋势计算
# ==========================================

def _fetch_higher_tf_ema(client, symbol, custom_config=None, mtf_data=None):
    """
    获取高周期K线并计算 EMA_TREND，用于 MTF 趋势对齐校验

    🔥 v2.8: 回测模式全本地化 - 禁止API调用，强制使用 mtf_data

    Args:
        client: Binance客户端（回测模式下应为 None）
        symbol: 交易对
        custom_config: 自定义配置（回测模式传入）
        mtf_data: 多周期数据字典（回测模式必传）

    Returns:
        float or None: 高周期 EMA 值，失败返回 None
    """
    try:
        # 🔥 使用 custom_config 或全局 config.SYSTEM_CONFIG
        cfg = custom_config if custom_config is not None else config.SYSTEM_CONFIG
        
        preset_config = config.STRATEGY_PRESETS.get(cfg.get("STRATEGY_MODE", "STANDARD"), {})
        higher_interval = preset_config.get("HIGHER_INTERVAL", cfg.get("HIGHER_INTERVAL", "1h"))
        mtf_ema_length = preset_config.get("MTF_TREND_EMA", cfg.get("EMA_TREND", 89))
        
        # 🔥 回测模式检测：如果传入了 custom_config，强制使用本地数据
        is_backtest_mode = (custom_config is not None)
        
        if is_backtest_mode:
            # 回测模式：禁止API调用，必须使用 mtf_data
            if mtf_data is None:
                logger.debug(f"   ⚠️ [BACKTEST] {symbol} MTF数据未传入，跳过高周期过滤")
                return None
            
            # 从 mtf_data 中提取高周期数据
            if higher_interval not in mtf_data:
                logger.debug(f"   ⚠️ [BACKTEST] {symbol} 缺少 {higher_interval} 数据")
                return None
            
            df_htf = mtf_data[higher_interval]
            if df_htf is None or len(df_htf) < mtf_ema_length:
                logger.debug(f"   ⚠️ [BACKTEST] {symbol} {higher_interval} 数据不足")
                return None
            
            # 计算 EMA（使用已计算的指标或重新计算）
            if 'EMA_TREND' in df_htf.columns:
                # 如果已经计算过，直接使用
                last_ema = df_htf['EMA_TREND'].iloc[-1]
            else:
                # 重新计算
                import pandas_ta as ta
                ema_htf = ta.ema(df_htf['close'], length=mtf_ema_length)
                if ema_htf is None or len(ema_htf) == 0:
                    return None
                last_ema = ema_htf.iloc[-1]
            
            if pd.isna(last_ema):
                return None
            
            return float(last_ema)
        
        # 🔥 实盘模式：仅在有 client 时才调用 API
        if client is None:
            return None

        # 🔥 任务1: HTF EMA 实盘缓存（15分钟TTL）
        # 同 Symbol、同周期的高周期 EMA 在 15 分钟内仅允许请求一次 API，其余时间直接读缓存
        cache_key = f"{symbol}_{higher_interval}"
        now = time.time()
        with _HTF_EMA_CACHE_LOCK:
            cached = _htf_ema_cache.get(cache_key)
            if cached and (now - cached['ts']) < _HTF_EMA_TTL:
                print(f"   📦 [{symbol}] HTF EMA 命中缓存: {cached['ema']:.4f} (剩余TTL={int(_HTF_EMA_TTL - (now - cached['ts']))}s)")
                return cached['ema']

        # 缓存未命中，调用 API 获取高周期K线
        df_htf = get_historical_klines(client, symbol, higher_interval, limit=500)
        if df_htf is None or len(df_htf) < mtf_ema_length:
            return None

        import pandas_ta as ta
        ema_htf = ta.ema(df_htf['close'], length=mtf_ema_length)
        if ema_htf is None or len(ema_htf) == 0:
            return None

        last_ema = ema_htf.iloc[-1]
        if pd.isna(last_ema):
            return None

        ema_value = float(last_ema)

        # 写入缓存
        with _HTF_EMA_CACHE_LOCK:
            _htf_ema_cache[cache_key] = {'ema': ema_value, 'ts': now}
        print(f"   🔄 [{symbol}] HTF EMA 缓存已刷新: {ema_value:.4f} (TTL={_HTF_EMA_TTL}s)")

        return ema_value
    except Exception as e:
        if custom_config is not None:
            logger.debug(f"   ⚠️ [BACKTEST] {symbol} 获取高周期EMA失败: {e}")
        else:
            print(f"   ⚠️ [{symbol}] 获取高周期EMA失败: {e}")
        return None



def is_mtf_aligned(current_price, higher_tf_ema, signal_type):
    """
    MTF 多周期共振对齐校验
    
    Args:
        current_price: 当前价格
        higher_tf_ema: 高周期 EMA_TREND 值
        signal_type: 'BUY' (做多) 或 'SELL' (做空)
    
    Returns:
        (aligned: bool, reason: str)
    """
    if higher_tf_ema is None:
        return True, "MTF数据不可用，跳过对齐检查"
    
    if signal_type == 'BUY':
        aligned = current_price > higher_tf_ema
        reason = f"MTF做多对齐: 价格{current_price:.4f} {'>' if aligned else '<='} HTF_EMA{higher_tf_ema:.4f}"
    else:
        aligned = current_price < higher_tf_ema
        reason = f"MTF做空对齐: 价格{current_price:.4f} {'<' if aligned else '>='} HTF_EMA{higher_tf_ema:.4f}"
    
    return aligned, reason


# ==========================================
# 交易引擎主循环
# ==========================================

def trading_engine_loop(client):
    """
    交易引擎主循环（🔥 V5.1 重构：数据流与交易流解耦）
    
    核心变更：
    - 数据获取 + 指标计算 + 信号判定：始终运行（24/7 常驻）
    - 开平仓执行：仅在 TRADING_ENGINE_ACTIVE=True 时执行
    - 即使交易暂停，仪表盘/AI分析仍可读取最新真实数据
    """
    print("🚀 交易引擎线程已启动（V5.1 数据流常驻模式）")
    
    # 初始化风控管理器（首次调用传入配置）
    try:
        risk_mgr = get_risk_manager(config.SYSTEM_CONFIG)
    except Exception as e:
        print(f"⚠️ 风控管理器初始化失败: {e}，将跳过风控检查")
        risk_mgr = None
    
    # 标记：是否已完成本次启动的持仓模式同步
    _hedge_mode_synced = False
    
    # 🔥 Task 1: Heartbeat 心跳计数器
    _scan_cycle_count = 0
    _last_heartbeat_time = time.time()
    _heartbeat_interval = config.SYSTEM_CONFIG.get("HEARTBEAT_INTERVAL_MINUTES", 15) * 60  # 默认15分钟
    _last_trade_time = time.time()  # 记录最后一次交易时间
    
    while True:
        try:
            # 🔥 Task 1: 心跳检测 - 每15分钟或X个扫描周期后发送心跳
            current_time = time.time()
            time_since_last_heartbeat = current_time - _last_heartbeat_time
            time_since_last_trade = current_time - _last_trade_time
            
            # 如果超过心跳间隔且没有交易触发，发送心跳消息
            if time_since_last_heartbeat >= _heartbeat_interval and time_since_last_trade >= _heartbeat_interval:
                try:
                    symbols_count = len(config.SYSTEM_CONFIG.get("MONITOR_SYMBOLS", []))
                    
                    # 计算平均波动率（从指标缓存获取）
                    avg_volatility = 0.0
                    volatility_count = 0
                    for sym in config.SYSTEM_CONFIG.get("MONITOR_SYMBOLS", []):
                        indicator_data = get_indicator_cache(sym)
                        if indicator_data and 'Relative_ATR' in indicator_data:
                            avg_volatility += indicator_data['Relative_ATR']
                            volatility_count += 1
                    
                    if volatility_count > 0:
                        avg_volatility = (avg_volatility / volatility_count) * 100
                    
                    heartbeat_msg = (
                        f"💓 <b>系统心跳: 正常运行中</b>\n\n"
                        f"📊 当前状态: 正在监控 {symbols_count} 个币种\n"
                        f"🔍 最近一次扫描结果: 行情波动率 {avg_volatility:.2f}%，未达 SMC 触发阈值。\n"
                        f"⏰ 距上次交易: {int(time_since_last_trade/60)} 分钟\n"
                        f"🔄 扫描周期: {_scan_cycle_count} 次"
                    )
                    
                    send_tg_msg(heartbeat_msg)
                    print(f"💓 心跳已发送: 监控{symbols_count}个币种, 波动率{avg_volatility:.2f}%")
                    
                    _last_heartbeat_time = current_time
                    
                except Exception as hb_e:
                    print(f"⚠️ 心跳发送失败: {hb_e}")
            
            # 增加扫描周期计数
            _scan_cycle_count += 1
            
                        # ====== 🔥 数据流常驻：无论交易引擎是否激活，始终获取数据并计算指标 ======
                        
                        # 遍历所有监控币种（数据获取 + 指标计算 + 信号判定）
            for symbol in config.SYSTEM_CONFIG.get("MONITOR_SYMBOLS", []):
                try:
                    # 获取K线数据（始终执行）
                    df = get_historical_klines(
                        client, symbol, 
                        config.SYSTEM_CONFIG["INTERVAL"], 
                        limit=800
                    )
                    
                    if df is None or len(df) < 50:
                        continue
                    
                    # 计算技术指标（始终执行，保持指标实时更新）
                    use_latest = config.SYSTEM_CONFIG.get("USE_LATEST_CANDLE", False)
                    df = calculate_indicators(df, force_recalc=not use_latest)                    
                    if df is None or len(df) < 5:
                        continue
                    
                    # 🔥 将最新指标数据写入全局缓存
                    _update_indicator_cache(symbol, df)
                    
                    # 生成交易信号
                    signals = generate_trading_signals(df, symbol, client)
                    
                    # 健壮性检查
                    if signals is None or not isinstance(signals, dict) or not signals.get('signals'):
                        continue
                    
                    # 进入信号处理流程
                    print(f"\n📊 {symbol} 检测到信号:")
                    for sig in signals['signals']:
                        print(f"   {sig['message']}")
                        
                        # ====== 🔥 交易执行拦截点：仅在引擎激活时执行开平仓 ======
                        # 🔥 V7.1: 使用 config.TRADING_ENGINE_ACTIVE 动态读取，避免快照失效
                        if not config.TRADING_ENGINE_ACTIVE:
                           print(f"   ⏸️ 检测到 {len(signals['signals'])} 个信号，但引擎未激活，已跳过执行。")
                           continue # 直接跳到下一个币种
                        
                        # ====== 引擎点火：首次激活时同步币安持仓模式 ======
                        if not _hedge_mode_synced:
                            print("🔀 引擎点火：同步币安持仓模式...")
                            sync_ok, sync_msg = sync_hedge_mode_to_binance(client)
                            if not sync_ok:
                                print(f"🛑 持仓模式同步失败，引擎启动终止: {sync_msg}")
                                config.TRADING_ENGINE_ACTIVE = False
                                continue
                            _hedge_mode_synced = True
                            print(f"✅ 持仓模式同步完成: {sync_msg}")
                        
                        # ====== 全局风控检查：最大回撤熔断 ======
                        if risk_mgr is not None:
                            try:
                                # 🔥 V8.0: 根据 RUNNING_MODE 获取对应模式的净值
                                running_mode = config.SYSTEM_CONFIG.get("RUNNING_MODE", "SANDBOX")
                                if running_mode == "REAL":
                                    if client:
                                        acc = client.futures_account()
                                        current_equity = float(acc['totalMarginBalance'])
                                    else:
                                        current_equity = config.SYSTEM_CONFIG.get("BENCHMARK_CASH", 0)
                                else:
                                    # SANDBOX: 强制读取沙盒独立账本，彻底隔离实盘余额！
                                    from trading_engine import get_sandbox_balance
                                    ledger_data = get_sandbox_balance()
                                    current_equity = ledger_data.get('balance', 10000.0)
                                    
                                    # 顺手同步给内存，防止面板显示为 0
                                    with state_lock:
                                        config.SYSTEM_CONFIG["SIM_CURRENT_BALANCE"] = current_equity

                                if not risk_mgr.check_global_drawdown(current_equity):
                                    print(f"🚨 [风控] [{running_mode}] 最大回撤熔断已触发，暂停{running_mode}新开仓！")
                                    with positions_lock:
                                        status = risk_mgr.status_report(config.ACTIVE_POSITIONS, current_equity)
                                    print(f"   {status}")
                                    continue
                                
                            except Exception as risk_e:
                                print(f"⚠️ 风控检查异常: {risk_e}")
                        
                        # ====== 🔥 核心修复：将信号发送给执行器 ======
                        try:
                            process_trading_signals(client, signals, df=df, custom_config=config.SYSTEM_CONFIG)
                        except Exception as process_e:
                            print(f"⚠️ 信号处理执行异常: {process_e}")
                            
                    # 🔥 Task 2: Near-Miss Report - AI 否决后发送摘要
                    # 检查是否有技术过滤通过但 AI 否决的情况
                    has_technical_signal = any(
                        sig.get('action') == 'ENTRY' 
                        for sig in signals['signals']
                    )
                    
                    if has_technical_signal:
                        # 技术过滤通过，准备处理信号
                        # 在 process_trading_signals 中会调用 AI Commander
                        # 如果 AI 否决，会在那里发送 Near-Miss Report
                        pass
                    
                except Exception as e:
                    print(f"⚠️ 处理 {symbol} 时出错: {e}")
                    continue
            
            # ====== 🔥 新增：自动保本巡逻器 (Fix #5: 锁外网络调用，防死锁) ======
            try:
                # 🔥 Fix #5: Phase 1 — 在锁内只做纯内存快照，不调用任何网络 API
                _breakeven_candidates = []
                with positions_lock:
                    for key_sym, pos_list in list(config.ACTIVE_POSITIONS.items()):
                        if not isinstance(pos_list, list): pos_list = [pos_list]
                        for pos in pos_list:
                            if pos.get('sl') == pos.get('entry'):
                                continue
                            _breakeven_candidates.append({
                                'key_sym': key_sym,
                                'real_symbol': pos.get('real_symbol', key_sym.split('_')[0]),
                                'entry': pos['entry'],
                                'atr': pos.get('atr', 0),
                                'type': pos.get('type', 'LONG'),
                                'trade_id': str(pos.get('trade_id', '')),
                            })
                
                # 🔥 Fix #5: Phase 2 — 锁外执行网络请求 + update_sl_to_breakeven
                for _cand in _breakeven_candidates:
                    try:
                        current_price = get_current_price(client, _cand['real_symbol'])
                        if not current_price:
                            continue
                        
                        entry = _cand['entry']
                        pos_atr = _cand['atr']
                        
                        if _cand['type'] == 'LONG':
                            float_profit = current_price - entry
                        else:
                            float_profit = entry - current_price
                        
                        if pos_atr > 0:
                            breakeven_threshold = 1.2 * pos_atr
                            should_breakeven = float_profit >= breakeven_threshold
                        else:
                            profit_pct = float_profit / entry if entry > 0 else 0
                            breakeven_threshold = entry * 0.008
                            should_breakeven = profit_pct >= 0.006
                        
                        if should_breakeven:
                            breakeven_price = entry * 1.0005 if _cand['type'] == 'LONG' else entry * 0.9995
                            res = update_sl_to_breakeven(_cand['trade_id'], client=client, custom_breakeven_price=breakeven_price)
                            if res['success']:
                                threshold_info = f"ATR阈值={breakeven_threshold:.4f}" if pos_atr > 0 else "固定0.8%"
                                print(f"🛡️ 订单 {_cand['trade_id']} 浮盈={float_profit:.4f} >= {threshold_info}，已触发ATR自适应保本 → {breakeven_price:.4f}")
                    except Exception as _cand_e:
                        print(f"⚠️ 保本巡逻单笔异常 [{_cand.get('real_symbol')}]: {_cand_e}")
            except Exception as be_e:
                print(f"⚠️ 自动保本巡逻器异常: {be_e}")
            
            # 🔥 引擎停止后重置持仓模式同步标记
            # 🔥 V7.1: 使用 config 模块引用动态读取，确保跨进程/线程同步
            if not config.TRADING_ENGINE_ACTIVE:
                _hedge_mode_synced = False
            
            # 休眠间隔
            sleep_time = config.SYSTEM_CONFIG.get("ENGINE_SLEEP", 60)
            time.sleep(sleep_time)
            
        except Exception as e:
            print(f"❌ 交易引擎异常: {e}")
            time.sleep(60)


# ==========================================
# 🔥 指标数据全局缓存（供仪表盘/AI分析读取）
# ==========================================
import threading
from collections import OrderedDict

# 🔥 使用 OrderedDict 实现 LRU 缓存
_indicator_cache = OrderedDict()
_indicator_cache_lock = threading.Lock()
MAX_CACHE_SIZE = 100  # 最大缓存币种数量


def _update_indicator_cache(symbol, df):
    """
    更新指标缓存（由交易引擎主循环调用）
    
    🔥 内存安全：使用 LRU 淘汰策略，防止无界缓存导致 OOM
    - 缓存上限：100 个币种
    - 淘汰策略：FIFO（先进先出）
    """
    try:
        if df is None or len(df) == 0:
            return
        last = df.iloc[-1]
        with _indicator_cache_lock:
            # 🔥 修复 LRU 逻辑：使用 move_to_end 而非 delete+reinsert
            if symbol in _indicator_cache:
                _indicator_cache.move_to_end(symbol)
            
            _indicator_cache[symbol] = {
                'price': float(last.get('close', 0)),
                'ATR': float(last.get('ATR', 0)),
                'Relative_ATR': float(last.get('Relative_ATR', 0)),
                'ADX': float(last.get('ADX', 0)),
                'RSI': float(last.get('RSI', 50)),
                'MACD_hist': float(last.get('MACD_hist', 0)),
                'MACD_line': float(last.get('MACD_line', 0)),
                'MACD_signal': float(last.get('MACD_signal', 0)),
                'EMA_TREND': float(last.get('EMA_TREND', 0)),
                'VWAP': float(last.get('VWAP', 0)),
                'Squeeze_On': bool(last.get('Squeeze_On', False)),
                'volume': float(last.get('volume', 0)),
                'timestamp': datetime.now().isoformat(),
            }
            
            # 🔥 LRU 淘汰：移除最早未被访问的条目
            if len(_indicator_cache) > MAX_CACHE_SIZE:
                oldest_symbol, _ = _indicator_cache.popitem(last=False)
                print(f"🗑️ [LRU淘汰] 缓存已满，移除最久未访问币种: {oldest_symbol} (当前缓存: {len(_indicator_cache)}/{MAX_CACHE_SIZE})")
    except Exception as e:
        print(f"⚠️ 更新指标缓存失败 {symbol}: {e}")


def get_indicator_cache(symbol=None):
    """
    获取指标缓存数据（供仪表盘/AI分析/外部模块调用）
    
    Args:
        symbol: 指定币种，None 返回全部
    
    Returns:
        dict: 指标数据
    """
    with _indicator_cache_lock:
        if symbol:
            return _indicator_cache.get(symbol, {}).copy()
        return {k: v.copy() for k, v in _indicator_cache.items()}


def process_trading_signals(client, signals, df=None, custom_config=None):
    """处理交易信号（含黑匣子审计链路 + TSL动态追踪止盈）"""
    # 🔥 配置隔离：优先使用传入的 custom_config
    cfg = custom_config if custom_config is not None else config.SYSTEM_CONFIG
    
    symbol = signals['symbol']
    price = signals['price']
    atr = signals['atr']
    # 🔥 任务2.2：提取 ADX 值并传递给 execute_trade
    adx = signals.get('adx', 0)
    
    # ==========================================
    # 🔥 v3.0 三阶段动态止损巡逻器 (Three-Stage Dynamic SL)
    # Stage 1: 初始护盾 (ATR_MULT * ATR)
    # Stage 2A: 风险减半 (浮盈 >= 1.0*ATR → SL移至 Entry ± 0.5*ATR)
    # Stage 2B: 智能保本 (浮盈 >= 1.8*ATR → SL移至 Entry * 1.001/0.999)
    # Stage 3: TSL收割 (浮盈 >= 2.5*ATR → 追踪止损模式)
    # ==========================================
    try:
        # 从配置读取三阶段参数
        stage_a_profit = cfg.get('STAGE_A_PROFIT_MULT', 0.8)
        stage_a_sl = cfg.get('STAGE_A_SL_MULT', 0.3)
        stage_b_profit = cfg.get('STAGE_B_PROFIT_MULT', 1.5)
        stage_b_offset = cfg.get('STAGE_B_SL_OFFSET', 0.0005)
        tsl_trigger = cfg.get('TSL_TRIGGER_MULT', 2.0)
        tsl_callback = cfg.get('TSL_CALLBACK_MULT', 1.8)
        
        # 🔥 狂战士模式：闪电保本与追踪
        if cfg.get("STRATEGY_MODE", "STANDARD") == "SCALPER":
            stage_a_profit = 0.4  # 0.5 ATR 极速降险
            stage_b_profit = 0.6  # 0.8 ATR 极速保本
            tsl_trigger = 1.0     # 1.2 ATR 极速开启追踪止盈
            tsl_callback = 0.5    # 追踪回撤容忍度更小
        
        # 🔥 激进爆发模式：海龟宽幅追踪 (容忍震荡，吃尽单边)
        elif cfg.get("STRATEGY_MODE", "STANDARD") == "AGGRESSIVE":
            stage_a_profit = 0.8  # 1.0 ATR 减险
            stage_b_profit = 1.8  # 2.0 ATR 才保本 (给予充足的震荡洗盘空间)
            tsl_trigger = 2.5     # 3.0 ATR 开启终极追踪 (必须抓到大波段)
            tsl_callback = 1.2    # 1.5 ATR 宽幅回调容忍 (防扎针洗盘)
        
        with positions_lock:
            for key_sym, pos_list in list(config.ACTIVE_POSITIONS.items()):
                if not isinstance(pos_list, list): 
                    pos_list = [pos_list]
                
                for pos in pos_list:
                    # 只处理当前币种的持仓
                    if pos.get('real_symbol', key_sym.split('_')[0]) != symbol:
                        continue
                    
                    entry = pos.get('entry', 0)
                    current_sl = pos.get('sl', 0)
                    pos_atr = pos.get('atr', atr)
                    pos_type = pos.get('type', 'LONG')
                    current_stage = pos.get('sl_stage', 1)
                    
                    if pos_atr <= 0 or entry <= 0:
                        continue
                    
                    # 计算浮盈
                    if pos_type == 'LONG':
                        float_profit = price - entry
                    else:
                        float_profit = entry - price
                    
                    new_sl = current_sl
                    new_stage = current_stage
                    stage_changed = False
                    
                    # ====== Stage 3: 夺命多档 TSL 收割模式 ======
                    if float_profit >= tsl_trigger * pos_atr:
                        new_stage = 3
                        
                        # 狂战士专属：多档追踪 (利润越高，勒得越紧)
                        is_scalp = (cfg.get("STRATEGY_MODE", "STANDARD") == "SCALPER")
                        if is_scalp and float_profit >= 3.0 * pos_atr:
                            dynamic_callback = 0.2 * pos_atr  # 极值爆拉：贴脸防守
                        elif is_scalp and float_profit >= 2.0 * pos_atr:
                            dynamic_callback = 0.5 * pos_atr  # 利润翻倍：收紧防线
                        else:
                            dynamic_callback = tsl_callback * pos_atr # 默认追踪
                        
                        # 🔥 修复：引入当前闭合K线的 high/low，捕获插针极值
                        # 旧逻辑：highest_price = price 仅采样闭合价，忽略K线运行中的插针
                        # 新逻辑：用 df.iloc[-1]['high'] / df.iloc[-1]['low'] 覆盖真实极值
                        _candle_high = df.iloc[-1]['high'] if (df is not None and len(df) > 0) else price
                        _candle_low = df.iloc[-1]['low'] if (df is not None and len(df) > 0) else price
                        
                        if pos_type == 'LONG':
                            highest_price = max(pos.get('highest_price', entry), _candle_high)
                            pos['highest_price'] = highest_price
                            tsl_sl = highest_price - dynamic_callback
                            if tsl_sl > current_sl:
                                new_sl = tsl_sl
                        else:  # SHORT
                            lowest_price = min(pos.get('lowest_price', entry), _candle_low)
                            pos['lowest_price'] = lowest_price
                            tsl_sl = lowest_price + dynamic_callback
                            if tsl_sl < current_sl:
                                new_sl = tsl_sl
                    
                    # ====== Stage 2B: 智能保本 (浮盈 >= stage_b_profit * ATR) ======
                    elif float_profit >= stage_b_profit * pos_atr:
                        new_stage = max(current_stage, 2)
                        if pos_type == 'LONG':
                            breakeven_sl = entry * (1 + stage_b_offset)
                            if breakeven_sl > current_sl:
                                new_sl = breakeven_sl
                        else:
                            breakeven_sl = entry * (1 - stage_b_offset)
                            if breakeven_sl < current_sl:
                                new_sl = breakeven_sl
                    
                    # ====== Stage 2A: 风险减半 (浮盈 >= stage_a_profit * ATR) ======
                    elif float_profit >= stage_a_profit * pos_atr:
                        new_stage = max(current_stage, 2)
                        if pos_type == 'LONG':
                            half_risk_sl = entry - (stage_a_sl * pos_atr)
                            if half_risk_sl > current_sl:
                                new_sl = half_risk_sl
                        else:
                            half_risk_sl = entry + (stage_a_sl * pos_atr)
                            if half_risk_sl < current_sl:
                                new_sl = half_risk_sl
                    
                    # ====== 应用止损更新 ======
                    if new_stage != current_stage:
                        pos['sl_stage'] = new_stage
                        stage_changed = True
                    
                    sl_changed = (new_sl != current_sl)
                    if sl_changed:
                        pos['sl'] = new_sl
                        if new_stage == 3:
                            pos['tsl_active'] = True
                        
                        # 更新交易所止损单（实盘模式）
                        if not pos.get('simulated', False) and pos.get('sl_order_id'):
                            try:
                                real_symbol = pos.get('real_symbol', symbol)
                                try:
                                    client.futures_cancel_order(symbol=real_symbol, orderId=pos['sl_order_id'])
                                except:
                                    pass
                                
                                sl_side = 'SELL' if pos_type == 'LONG' else 'BUY'
                                sl_order_params = {
                                    'symbol': real_symbol,
                                    'side': sl_side,
                                    'type': 'STOP_MARKET',
                                    'quantity': pos['qty'],
                                    'stopPrice': round_to_tick_size(new_sl, real_symbol)
                                }
                                if config.SYSTEM_CONFIG.get("HEDGE_MODE_ENABLED", False):
                                    sl_order_params['positionSide'] = pos_type
                                else:
                                    sl_order_params['positionSide'] = 'BOTH'
                                    sl_order_params['reduceOnly'] = True
                                
                                new_sl_order = client.futures_create_order(**sl_order_params)
                                pos['sl_order_id'] = new_sl_order['orderId']
                                
                                stage_names = {1: 'Stage1:初始护盾', 2: 'Stage2:防洗盘', 3: 'Stage3:TSL收割'}
                                print(f"🎯 [{symbol}] {stage_names.get(new_stage, 'Unknown')}: 止损 {current_sl:.4f} → {new_sl:.4f} (浮盈={float_profit:.4f}, ATR={pos_atr:.4f})")
                            except Exception as sl_e:
                                print(f"⚠️ 三阶段SL更新止损单失败: {sl_e}")
                    
                    if sl_changed or stage_changed:
                        save_data()
    
    except Exception as tsl_e:
        print(f"⚠️ 三阶段动态止损巡逻器异常: {tsl_e}")
    
    for sig in signals['signals']:
        signal_type = sig['type']  # BUY or SELL
        action = sig['action']  # ENTRY, EXIT_LONG, EXIT_SHORT
        
        try:
            # 平仓信号
            if action.startswith('EXIT'):
                pos_type = 'LONG' if action == 'EXIT_LONG' else 'SHORT'
                key_sym = f"{symbol}_{pos_type}"
                
                if key_sym in config.ACTIVE_POSITIONS or symbol in config.ACTIVE_POSITIONS:
                    # 🔥 平仓权限校验（仓位隔离）
                    if POSITION_ISOLATION_ENABLED:
                        positions_list = config.ACTIVE_POSITIONS.get(key_sym, [])
                        if isinstance(positions_list, list) and positions_list:
                            position_to_close = positions_list[0]
                        elif isinstance(positions_list, dict):
                            position_to_close = positions_list
                        else:
                            position_to_close = None
                        
                        if position_to_close:
                            allowed, reason = validate_close_permission(position_to_close, symbol)
                            if not allowed:
                                logger.warning(f"🚫 [{symbol}] 平仓被拒绝: {reason}")
                                send_tg_alert(
                                    f"🚫 <b>[平仓权限拒绝]</b>\n"
                                    f"币种: {symbol}\n"
                                    f"原因: {reason}\n\n"
                                    f"⚠️ 该持仓不是机器人创建的，拒绝平仓"
                                )
                                continue
                    
                    result = execute_trade(
                        client, symbol, signal_type, price,
                        {'quantity': 0},  # 会从持仓中获取
                        atr=atr,
                        adx=adx,
                        position_action=action,
                        custom_config=cfg
                    )
                    
                    if result['success']:
                        print(f"✅ {symbol} 平仓成功")
                        send_tg_msg(
                            f"🛡️ <b>平仓执行</b>\n"
                            f"币种: {symbol}\n"
                            f"信号: {sig['message']}\n"
                            f"价格: ${price:.4f}"
                        )
            
            # 开仓信号
            elif action == 'ENTRY':
                # ==========================================
                # 🔥 宏观哨兵：拦截 IntelligenceHub 的战略指令
                # ==========================================
                if cfg.get("_TREND_ENTRY_BLOCKED", False):
                    block_reason = cfg.get("_BLOCK_REASON", "战略性拦截")
                    print(f"   🛑 [AI 统帅拦截] 当前处于宏观高风险区，禁止入场！原因: {block_reason}")
                    continue  # 跳过本条入场信号，但不影响后续的平仓或止损逻辑
                # ==========================================

                pos_type = 'LONG' if signal_type == 'BUY' else 'SHORT'
                key_sym = f"{symbol}_{pos_type}"

                # 🔥 Task 3: 投资组合相关性检查（Fix #11: 传入 client 以启用 BTC 4h 弱势检测）
                correlation_allowed, correlation_reason = check_portfolio_correlation(pos_type, client=client)
                if not correlation_allowed:
                    print(f"   🚫 [{symbol}] 投资组合相关性拦截: {correlation_reason}")
                    send_tg_alert(
                        f"🚫 <b>[投资组合相关性拦截]</b>\n"
                        f"币种: {symbol}\n"
                        f"方向: {pos_type}\n"
                        f"原因: {correlation_reason}\n\n"
                        f"⚠️ 防止过度集中风险"
                    )
                    continue
                
                # ====== 多重子仓位检查（Pyramiding）======
                is_scalp = (cfg.get("STRATEGY_MODE", "STANDARD") == "SCALPER")
                max_concurrent = 5 if is_scalp else cfg.get("MAX_CONCURRENT_TRADES_PER_SYMBOL", 3)
                min_distance_atr = 0.5 if is_scalp else cfg.get("MIN_SIGNAL_DISTANCE_ATR", 0.5)
                
                existing_trades = []
                if key_sym in config.ACTIVE_POSITIONS:
                    val = config.ACTIVE_POSITIONS[key_sym]
                    existing_trades = val if isinstance(val, list) else [val]

                # 🛡️ 防锁仓机制 (Anti-Hedge Lock) - 🔥 修复竞态条件
                with positions_lock:
                    opposing_type = 'SHORT' if pos_type == 'LONG' else 'LONG'
                    opposing_key = f"{symbol}_{opposing_type}"
                    opposing_positions = config.ACTIVE_POSITIONS.get(opposing_key, [])
                    if not isinstance(opposing_positions, list):
                        opposing_positions = [opposing_positions]
                    has_opposing_position = len(opposing_positions) > 0 and any(p.get('type') == opposing_type for p in opposing_positions)
                
                if has_opposing_position:
                    print(f"🚫 [{symbol}] 防锁仓拦截: 当前已持有反向仓位，禁止多空双开！")
                    continue  # 直接跳过开仓

                if len(existing_trades) >= max_concurrent:
                    print(f"⚠️ {symbol} {pos_type} 已达最大并发 {max_concurrent} 笔，跳过")
                    continue
                
                if existing_trades and atr > 0:
                    last_entry_price = existing_trades[-1].get('entry', 0)
                    price_distance = abs(price - last_entry_price)
                    min_distance = atr * min_distance_atr
                    
                    pyramid_exempt = False
                    last_pos = existing_trades[-1]
                    float_profit = (price - last_entry_price) if last_pos.get('type') == 'LONG' else (last_entry_price - price)
                    pos_atr = last_pos.get('atr', atr)
                    
                    # 狂战士浮盈加仓法：只要上一单浮盈超过 0.8 ATR，且在强趋势中 (ADX>=30)，立刻加仓！
                    # 动态读取回测验证过的加仓阈值 (默认 1.4 ATR)
                    pyramid_threshold = 1.0 if is_scalp else cfg.get("PYRAMID_ATR_TRIGGER", 2.0)
                    if pos_atr > 0 and float_profit >= pyramid_threshold * pos_atr:
                        # 检查当前 ADX（强趋势判定）
                        current_adx = df['ADX'].iloc[-1] if (df is not None and 'ADX' in df.columns) else 30
                        
                        if current_adx >= 30 or not is_scalp:
                            pyramid_exempt = True
                            print(f"🔥 [{symbol}] 顺势浮盈加仓激活: 浮盈={float_profit:.4f} >= {pyramid_threshold}*ATR, ADX={current_adx:.1f}，追加第 {len(existing_trades)+1} 仓！")
                        else:
                            print(f"🚫 [{symbol}] 震荡拦截: 浮盈虽达标，但 ADX={current_adx:.1f} < 30，拒绝在震荡市加仓")
                    
                    if not pyramid_exempt and price_distance < min_distance:
                        print(f"⚠️ {symbol} 入场价距离不足: {price_distance:.4f} < {min_distance:.4f} (ATR*{min_distance_atr}), 跳过")
                        continue
                
                # ====== 连续亏损断路器检查 ======
                global ENGINE_STATE
                if ENGINE_STATE['breaker_until'] > time.time():
                    remaining_mins = (ENGINE_STATE['breaker_until'] - time.time()) / 60
                    msg = f"连续亏损断路器冷却中，剩余 {remaining_mins:.1f} 分钟"
                    print(f"   🚨 [{symbol}] {msg}")
                    send_tg_msg(f"🚨 <b>[断路器拦截]</b> {symbol}\n{msg}")
                    continue
                
                # ====== 投资组合风控门卫：并发头寸 + 同向敞口检查 ======
                try:
                    risk_mgr = get_risk_manager()  # 获取已初始化的单例
                    with positions_lock:
                        pos_snapshot = dict(config.ACTIVE_POSITIONS)
                    allowed, reason = risk_mgr.can_open_new_position(pos_snapshot, pos_type)
                    if not allowed:
                        print(f"   🛡️ [{symbol}] 风控拦截开仓: {reason}")
                        send_tg_msg(
                            f"🛡️ <b>[风控拦截]</b> {symbol} 开{pos_type}信号被阻止\n"
                            f"原因: {reason}"
                        )
                        continue
                except Exception as risk_e:
                    print(f"⚠️ 风控检查异常，保守跳过: {risk_e}")
                    continue
                
                # 判断是否触发 SML 放大
                sml_boost_active = False
                if pos_type == 'LONG' and signals.get('sml_boost_long', False):
                    sml_boost_active = True
                elif pos_type == 'SHORT' and signals.get('sml_boost_short', False):
                    sml_boost_active = True

                # 计算仓位（传入 sml_boost 标志）
                position_info = calculate_position_size(
                    client, symbol, price, sig['strength'], atr=atr, sml_boost=sml_boost_active
                )
                
                if position_info:
                    # 🔥 把信号里的订单类型指令塞进 position_info 传给执行层
                    position_info['is_maker_only'] = sig.get('is_maker_only', True)
                    
                    result = execute_trade(
                        client, symbol, signal_type, price,
                        position_info,
                        atr=atr,
                        adx=adx,
                        position_action='ENTRY',
                        custom_config=cfg
                    )  
                        
                    
                    if result['success']:
                        # 🔥 黑匣子审计链路：开仓成功后立即生成快照并绑定 trade_id 存盘
                        try:
                            audit_snapshot = create_audit_snapshot(
                                df, symbol, signal_type,
                                sig.get('strength', 'STRONG'),
                                sig.get('message', '')
                            )
                            if audit_snapshot:
                                audit_snapshot['position_info'] = {
                                    'quantity': position_info['quantity'],
                                    'leverage': position_info['leverage'],
                                    'kelly_factor': position_info.get('kelly_factor', 1.0),
                                    'allocated_capital': position_info.get('allocated_capital', 0),
                                }
                                audit_snapshot['entry_price'] = price
                                audit_snapshot['atr'] = atr
                                save_audit_log(str(result['trade_id']), audit_snapshot)
                                print(f"📋 黑匣子审计已绑定: Trade_ID={result['trade_id']}")
                        except Exception as audit_e:
                            print(f"⚠️ 审计快照生成失败（不影响交易）: {audit_e}")
                        
                        print(f"✅ {symbol} 开仓成功")
                        
                        # 🔥 SMC 狩猎指令：使用精美模板推送开火信号
                        if SMC_TEMPLATE_ENABLED:
                            try:
                                _is_sandbox = result.get('simulated', False)
                                _stop_loss = 0.0
                                # 从持仓中提取止损价
                                _pos_type = 'LONG' if signal_type == 'BUY' else 'SHORT'
                                _key = f"{symbol}_{_pos_type}"
                                if _key in config.ACTIVE_POSITIONS:
                                    _pos_list = config.ACTIVE_POSITIONS[_key]
                                    if isinstance(_pos_list, list) and _pos_list:
                                        _stop_loss = _pos_list[-1].get('sl', 0)
                                    elif isinstance(_pos_list, dict):
                                        _stop_loss = _pos_list.get('sl', 0)
                                
                                smc_msg = build_smc_signal_from_trade_context(
                                    symbol=symbol,
                                    signal_type=signal_type,
                                    price=price,
                                    position_info=position_info,
                                    atr=atr,
                                    adx=adx,
                                    stop_loss_price=_stop_loss,
                                    trade_id=str(result.get('trade_id', '')),
                                    signal_message=sig.get('message', ''),
                                    is_sandbox=_is_sandbox,
                                )
                                send_tg_msg(smc_msg)
                            except Exception as smc_e:
                                print(f"⚠️ SMC模板生成失败，回退到简版: {smc_e}")
                                send_tg_msg(
                                    f"🚀 <b>开仓执行</b>\n"
                                    f"币种: {symbol}\n"
                                    f"方向: {'做多' if signal_type == 'BUY' else '做空'}\n"
                                    f"价格: ${price:.4f}\n"
                                    f"数量: {position_info['quantity']}\n"
                                    f"杠杆: {position_info['leverage']}x"
                                )
                        else:
                            send_tg_msg(
                                f"🚀 <b>开仓执行</b>\n"
                                f"币种: {symbol}\n"
                                f"方向: {'做多' if signal_type == 'BUY' else '做空'}\n"
                                f"信号: {sig['message']}\n"
                                f"价格: ${price:.4f}\n"
                                f"数量: {position_info['quantity']}\n"
                                f"杠杆: {position_info['leverage']}x"
                            )
        
        except Exception as e:
            print(f"❌ 处理信号失败: {e}")
            continue


# 为了兼容性，添加 generate_signals 别名
def generate_signals(df, symbol, client=None):
    """生成交易信号（兼容性别名）"""
    return generate_trading_signals(df, symbol, client=client)


# ==========================================
# 🔥 手术刀级子仓位精准控制
# ==========================================

def update_sl_to_breakeven(trade_key, client=None, custom_breakeven_price=None):
    """
    将指定订单的止损价更新为保本价（开仓价或自定义价格）
    
    🔥 弹性收割 v2: 支持自定义保本价（如 EntryPrice * 1.001 微利保本）
    
    Args:
        trade_key: 订单标识，格式为 "{symbol}_{pos_type}" 或 "trade_id"
        client: Binance客户端（可选）
        custom_breakeven_price: 自定义保本价（可选，默认使用开仓价）
    
    Returns:
        dict: {'success': bool, 'message': str, 'new_sl_price': float}
    """
    try:
        with positions_lock:
            # 尝试从 config.ACTIVE_POSITIONS 中查找订单
            position_info = None
            key_sym = None
            
            # 情况1：trade_key 是 "{symbol}_{pos_type}" 格式
            if trade_key in config.ACTIVE_POSITIONS:
                key_sym = trade_key
                positions_list = config.ACTIVE_POSITIONS[key_sym]
                if isinstance(positions_list, list) and positions_list:
                    position_info = positions_list[0]  # 取第一笔
                elif isinstance(positions_list, dict):
                    position_info = positions_list
            
            # 情况2：trade_key 是 trade_id，需要遍历查找
            if not position_info:
                for k, v in config.ACTIVE_POSITIONS.items():
                    positions_list = v if isinstance(v, list) else [v]
                    for pos in positions_list:
                        if str(pos.get('trade_id', '')) == trade_key:
                            position_info = pos
                            key_sym = k
                            break
                    if position_info:
                        break
            
            if not position_info:
                return {'success': False, 'message': '未找到该笔订单', 'new_sl_price': 0}
            
            # 🔥 弹性收割 v2: 使用自定义保本价或默认开仓价
            entry_price = position_info.get('entry', 0)
            if entry_price <= 0:
                return {'success': False, 'message': '无效的开仓价', 'new_sl_price': 0}
            
            breakeven_price = custom_breakeven_price if custom_breakeven_price is not None else entry_price
            
            # 更新止损价为保本价
            old_sl = position_info.get('sl', 0)
            position_info['sl'] = breakeven_price
            
            # 如果是实盘且有止损单ID，需要更新交易所的止损单
            if not position_info.get('simulated', False) and position_info.get('sl_order_id'):
                try:
                    if client is not None:
                        # 取消旧止损单
                        real_symbol = position_info.get('real_symbol', trade_key.split('_')[0])
                        try:
                            client.futures_cancel_order(
                                symbol=real_symbol,
                                orderId=position_info['sl_order_id']
                            )
                        except:
                            pass
                        
                        # 创建新止损单
                        pos_type = position_info.get('type', 'LONG')
                        sl_side = 'SELL' if pos_type == 'LONG' else 'BUY'
                        
                        sl_order_params = {
                            'symbol': real_symbol,
                            'side': sl_side,
                            'type': 'STOP_MARKET',
                            'quantity': position_info['qty'],
                            'stopPrice': round_to_tick_size(breakeven_price, real_symbol)
                        }
                        
                        # 对冲模式需要指定 positionSide
                        if config.SYSTEM_CONFIG.get("HEDGE_MODE_ENABLED", False):
                            sl_order_params['positionSide'] = pos_type
                        else:
                            sl_order_params['positionSide'] = 'BOTH'
                            sl_order_params['reduceOnly'] = True

                        new_sl_order = client.futures_create_order(**sl_order_params)
                        position_info['sl_order_id'] = new_sl_order['orderId']
                        
                        print(f"✅ 实盘止损单已更新: {real_symbol}, 新止损价={entry_price}")
                except Exception as e:
                    print(f"⚠️ 更新实盘止损单失败: {e}")
            
            save_data()
            
            return {
                'success': True,
                'message': f'止损价已更新为保本价 ${entry_price:.4f}',
                'new_sl_price': entry_price,
                'old_sl_price': old_sl
            }
    
    except Exception as e:
        print(f"❌ 更新保本止损失败: {e}")
        return {'success': False, 'message': f'操作失败: {str(e)[:50]}', 'new_sl_price': 0}


def get_position_by_key(trade_key):
    """
    根据 trade_key 获取持仓信息
    
    Args:
        trade_key: 订单标识，格式为 "{symbol}_{pos_type}" 或 "trade_id"
    
    Returns:
        dict: 持仓信息，如果未找到返回 None
    """
    try:
        with positions_lock:
            # 情况1：trade_key 是 "{symbol}_{pos_type}" 格式
            if trade_key in config.ACTIVE_POSITIONS:
                positions_list = config.ACTIVE_POSITIONS[trade_key]
                if isinstance(positions_list, list) and positions_list:
                    return positions_list[0]  # 返回第一笔
                elif isinstance(positions_list, dict):
                    return positions_list
            
            # 情况2：trade_key 是 trade_id，需要遍历查找
            for k, v in config.ACTIVE_POSITIONS.items():
                positions_list = v if isinstance(v, list) else [v]
                for pos in positions_list:
                    if str(pos.get('trade_id', '')) == trade_key:
                        return pos
            
            return None
    
    except Exception as e:
        print(f"❌ 获取持仓信息失败: {e}")
        return None


# ==========================================
# 🔥 SandboxLedger 管理系统
# ==========================================

SANDBOX_LEDGER_FILE = "sandbox_ledger.json"
_sandbox_ledger_lock = threading.Lock()  # 🔥 审计修复: 线程锁保护沙盒账本读写

# 🔥 审计修复: Redis key 常量
_SANDBOX_LEDGER_REDIS_KEY = "wjbot:sandbox:ledger"

# ==========================================
# 👈 漏了这里：第一处！定义全局内存缓存
_sandbox_ledger_cache = None 
# ==========================================

# 🔥 审计修复 6.4: 批量备份队列（防止频繁文件 I/O）
_sandbox_backup_queue = []
_sandbox_backup_lock = threading.Lock()
_sandbox_backup_dirty = False  # 脏标记：是否有未落盘的更新
_last_backup_time = 0  # 上次备份时间戳
BACKUP_INTERVAL_SECONDS = 60  # 批量备份间隔（60秒）


def get_sandbox_balance():
    """获取沙盒余额"""
    # ==========================================
    # 👈 漏了这里：第二处！声明并使用内存缓存
    global _sandbox_ledger_cache
    from redis_manager import redis_db
    
    # 优先级0: 内存中如果有，直接返回内存里的最新值！（阻断健忘症）
    if _sandbox_ledger_cache is not None:
        return _sandbox_ledger_cache
    # ==========================================
    
    # 🔥 优先级1: 从 Redis 读取（亚毫秒级）
    if redis_db.enabled:
        try:
            data = redis_db.load_hash(_SANDBOX_LEDGER_REDIS_KEY)
            if data and 'balance' in data:
                if 'history' not in data:
                    data['history'] = []
                data['balance'] = float(data['balance'])
                _sandbox_ledger_cache = data  # 👈 漏了这里：第三处！读出来后存入内存
                return data
        except Exception as e:
            logger.debug(f"Redis 读取沙盒账本失败，降级到文件: {e}")
    
    # 🔥 优先级2: 从 JSON 文件读取（降级）
    import json, os
    ledger_path = 'sandbox_ledger.json'
    
    if not os.path.exists(ledger_path):
        initial = config.SYSTEM_CONFIG.get("SANDBOX_INITIAL_BALANCE", 10000.0)
        initial_ledger = {"balance": float(initial), "history": []}
        with open(ledger_path, 'w', encoding='utf-8') as f:
            json.dump(initial_ledger, f, ensure_ascii=False, indent=4)
        # 同步写入 Redis
        if redis_db.enabled:
            try:
                redis_db.save_hash(_SANDBOX_LEDGER_REDIS_KEY, initial_ledger)
            except Exception:
                pass
        _sandbox_ledger_cache = initial_ledger # 👈 漏了这里：第四处！
        return initial_ledger
        
    try:
        with open(ledger_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            if 'balance' not in data:
                data['balance'] = 10000.0
            if 'history' not in data:
                data['history'] = []
            # 回填 Redis 缓存
            if redis_db.enabled:
                try:
                    redis_db.save_hash(_SANDBOX_LEDGER_REDIS_KEY, data)
                except Exception:
                    pass
            _sandbox_ledger_cache = data # 👈 漏了这里：第五处！
            return data
    except Exception as e:
        logger.error(f"❌ 读取沙盒账本失败: {e}")
        return {"balance": 10000.0, "history": []}


def update_sandbox_balance(amount, reason):
    """增减沙盒余额并记录日志"""
    from redis_manager import redis_db
    from datetime import datetime
    
    # ==========================================
    # 👈 漏了这里：第六处！把 _sandbox_ledger_cache 加进 global 声明里
    global _sandbox_backup_dirty, _last_backup_time, _sandbox_ledger_cache
    # ==========================================
    
    with _sandbox_ledger_lock:
        try:
            # 🔥 Step 1: 从 Redis 或文件加载当前账本
            ledger = get_sandbox_balance()

            # 🔥 Step 2: 类型安全提取
            old_balance = float(ledger.get('balance', 10000.0))
            new_balance = old_balance + amount
            
            # 🔥 Step 3: 余额检查（扣款时）
            if amount < 0 and new_balance < 0:
                return {
                    'success': False,
                    'new_balance': old_balance,
                    'message': f'余额不足: 当前${old_balance:.2f}, 需要${abs(amount):.2f}'
                }
            
            # 🔥 Step 4: 更新字典
            ledger['balance'] = float(new_balance)
            if 'history' not in ledger:
                ledger['history'] = []
            
            history_entry = {
                'timestamp': datetime.now().isoformat(),
                'amount': float(amount),
                'reason': reason,
                'balance_after': float(new_balance)
            }
            ledger['history'].append(history_entry)
            
            if len(ledger['history']) > 500:
                ledger['history'] = ledger['history'][-500:]
            
            # ==========================================
            # 👈 漏了这里：第七处（最致命的一处）！修改完把最新账本强行塞回内存
            _sandbox_ledger_cache = ledger
            # ==========================================
            
            # 🔥 彻底消除裂脑：同步到全局配置，让面板和风控能看到！
            config.SYSTEM_CONFIG["SIM_CURRENT_BALANCE"] = float(new_balance)
            
            # 🔥 Step 5: 写入 Redis（主存储，亚毫秒级）
            if redis_db.enabled:
                try:
                    redis_db.save_hash(_SANDBOX_LEDGER_REDIS_KEY, ledger)
                except Exception as redis_e:
                    logger.warning(f"⚠️ Redis 写入沙盒账本失败: {redis_e}")
            
            # 🔥 Step 6: 标记脏数据（需要备份）
            _sandbox_backup_dirty = True
            
            # 🔥 WAL 机制：强制同步落盘，确保断电级安全
            with open(SANDBOX_LEDGER_FILE, 'w', encoding='utf-8') as f:
                json.dump(ledger, f, ensure_ascii=False, indent=4)
                f.flush()
                os.fsync(f.fileno())
            
            logger.info(f"💰 沙盒账本更新: {reason} | 变动: {amount:+.2f} | 余额: ${new_balance:.2f}")
            
            return {
                'success': True,
                'new_balance': float(new_balance),
                'message': f'余额已更新: ${new_balance:.2f}'
            }
            
        except Exception as e:
            logger.error(f"❌ 更新沙盒账本失败: {e}")
            return {'success': False, 'new_balance': 0.0, 'message': str(e)}


def _sandbox_backup_worker():
    """
    🔥 审计修复 6.4: 沙盒账本批量备份工作线程
    
    核心逻辑：
    - 每60秒检查一次脏标记
    - 如果有未落盘的更新，执行批量备份到JSON文件
    - 防止频繁文件IO导致性能下降
    """
    global _sandbox_backup_dirty, _last_backup_time
    
    logger.info("🔄 沙盒账本批量备份线程已启动")
    
    while True:
        try:
            time.sleep(BACKUP_INTERVAL_SECONDS)
            
            # 检查是否有未落盘的更新
            if not _sandbox_backup_dirty:
                continue
            
            # 执行批量备份
            with _sandbox_ledger_lock:
                try:
                    ledger = get_sandbox_balance()
                    
                    import json
                    with open(SANDBOX_LEDGER_FILE, 'w', encoding='utf-8') as f:
                        json.dump(ledger, f, ensure_ascii=False, indent=4)
                    
                    _sandbox_backup_dirty = False
                    _last_backup_time = time.time()
                    
                    logger.debug(f"✅ 沙盒账本批量备份完成: 余额=${ledger.get('balance', 0):.2f}")
                    
                except Exception as backup_e:
                    logger.warning(f"⚠️ 沙盒账本批量备份失败: {backup_e}")
        
        except Exception as e:
            logger.error(f"❌ 沙盒账本备份线程异常: {e}")
            time.sleep(60)


# 🔥 启动沙盒账本批量备份线程（回测子进程跳过，避免 20 个进程同时启动后台线程）
if not IS_BACKTEST_PROCESS:
    _sandbox_backup_thread = threading.Thread(target=_sandbox_backup_worker, daemon=True, name="SandboxBackupWorker")
    _sandbox_backup_thread.start()


# ==========================================
# 🔥 决策审计系统
# ==========================================

# 🔥 审计修复 6.3: Redis List 审计日志系统
# 全局审计日志存储（内存缓存 + Redis持久化）
AUDIT_LOGS = {}
AUDIT_LOG_FILE = "trade_audit_logs.json"  # 保留作为降级备份
_AUDIT_LOG_REDIS_KEY = "wjbot:audit:logs"  # Redis List key

def save_audit_log(trade_id, audit_data):
    """
    保存交易决策审计日志 (v6.3 Redis List追加模式)
    
    🔥 审计修复 6.3: 从全量读写迁移到 Redis List 追加模式
    - 写入路径: Redis List (O(1)追加) + 内存缓存
    - 降级路径: JSON文件异步备份
    
    Args:
        trade_id: 交易ID
        audit_data: 审计数据字典，包含技术指标快照和决策信息
    """
    try:
        from redis_manager import redis_db
        
        # 添加时间戳和trade_id
        audit_data['timestamp'] = datetime.now().isoformat()
        audit_data['trade_id'] = str(trade_id)
        
        # 🔥 Step 1: 存储到内存缓存（快速查询）
        AUDIT_LOGS[trade_id] = audit_data
        
        # 🔥 Step 2: 追加到 Redis List（O(1)操作，无需全量读写）
        if redis_db.enabled:
            try:
                # 使用 LPUSH 追加到列表头部，LTRIM 保持最近1000条
                redis_db.append_to_list(_AUDIT_LOG_REDIS_KEY, audit_data, max_length=1000)
                logger.debug(f"📋 审计日志已追加到Redis: Trade_ID={trade_id}")
            except Exception as redis_e:
                logger.warning(f"⚠️ Redis追加审计日志失败: {redis_e}")
        
        # 🔥 Step 3: 异步备份到JSON文件（降级保护，不阻塞主流程）
        def _async_json_backup():
            try:
                import json
                # 只在Redis不可用时才执行文件备份
                if not redis_db.enabled:
                    if os.path.exists(AUDIT_LOG_FILE):
                        with open(AUDIT_LOG_FILE, 'r', encoding='utf-8') as f:
                            all_logs = json.load(f)
                    else:
                        all_logs = {}
                    
                    all_logs[trade_id] = audit_data
                    
                    if len(all_logs) > 1000:
                        sorted_logs = sorted(all_logs.items(), key=lambda x: x[1].get('timestamp', ''), reverse=True)
                        all_logs = dict(sorted_logs[:1000])
                    
                    with open(AUDIT_LOG_FILE, 'w', encoding='utf-8') as f:
                        json.dump(all_logs, f, ensure_ascii=False, indent=2)
            except Exception as file_e:
                logger.debug(f"审计日志文件备份失败（不影响主存储）: {file_e}")
        
        # 启动异步备份线程
        backup_thread = threading.Thread(target=_async_json_backup, daemon=True)
        backup_thread.start()
        
        print(f"📋 审计日志已保存: Trade_ID={trade_id}")
    
    except Exception as e:
        print(f"❌ 保存审计日志失败: {e}")


def get_audit_log(trade_id):
    """
    获取交易决策审计日志 (v6.3 Redis优先查询)
    
    🔥 审计修复 6.3: 优先从Redis List查询，降级到文件
    查询路径: 内存缓存 → Redis List → JSON文件
    
    Args:
        trade_id: 交易ID
    
    Returns:
        dict: 审计日志数据，如果未找到返回 None
    """
    try:
        from redis_manager import redis_db
        import json
        
        # 🔥 优先级1: 从内存缓存查找（最快）
        if trade_id in AUDIT_LOGS:
            return AUDIT_LOGS[trade_id]
        
        # 🔥 优先级2: 从 Redis List 查找
        if redis_db.enabled:
            try:
                # 获取整个列表（已限制1000条，性能可控）
                logs_list = redis_db.get_list(_AUDIT_LOG_REDIS_KEY, start=0, end=-1)
                if logs_list:
                    # 遍历查找匹配的trade_id
                    for log_entry in logs_list:
                        if isinstance(log_entry, dict) and log_entry.get('trade_id') == str(trade_id):
                            # 回填内存缓存
                            AUDIT_LOGS[trade_id] = log_entry
                            return log_entry
            except Exception as redis_e:
                logger.debug(f"Redis查询审计日志失败，降级到文件: {redis_e}")
        
        # 🔥 优先级3: 从JSON文件加载（降级）
        if os.path.exists(AUDIT_LOG_FILE):
            with open(AUDIT_LOG_FILE, 'r', encoding='utf-8') as f:
                all_logs = json.load(f)
                log_data = all_logs.get(trade_id)
                if log_data:
                    # 回填内存缓存
                    AUDIT_LOGS[trade_id] = log_data
                    return log_data
        
        return None
    
    except Exception as e:
        print(f"❌ 获取审计日志失败: {e}")
        return None


def get_all_audit_logs(limit=100):
    """
    获取所有审计日志（分页查询）
    
    🔥 审计修复 6.3: 从Redis List批量查询
    
    Args:
        limit: 返回数量限制（默认100条）
    
    Returns:
        list: 审计日志列表（按时间倒序）
    """
    try:
        from redis_manager import redis_db
        import json
        
        # 🔥 优先从Redis查询
        if redis_db.enabled:
            try:
                logs_list = redis_db.get_list(_AUDIT_LOG_REDIS_KEY, start=0, end=limit-1)
                if logs_list:
                    return logs_list
            except Exception as redis_e:
                logger.debug(f"Redis批量查询失败，降级到文件: {redis_e}")
        
        # 降级到文件
        if os.path.exists(AUDIT_LOG_FILE):
            with open(AUDIT_LOG_FILE, 'r', encoding='utf-8') as f:
                all_logs = json.load(f)
                # 转换为列表并按时间排序
                logs_list = sorted(
                    all_logs.values(),
                    key=lambda x: x.get('timestamp', ''),
                    reverse=True
                )
                return logs_list[:limit]
        
        return []
    
    except Exception as e:
        logger.error(f"❌ 批量获取审计日志失败: {e}")
        return []


def create_audit_snapshot(df, symbol, signal_type, signal_strength, decision_reason):
    """
    创建技术指标快照用于审计
    
    Args:
        df: K线数据（已计算指标）
        symbol: 币种
        signal_type: 信号类型（BUY/SELL）
        signal_strength: 信号强度
        decision_reason: 决策理由
    
    Returns:
        dict: 审计快照数据
    """
    try:
        if df is None or len(df) == 0:
            return {}
        
        last_candle = df.iloc[-1]
        
        audit_data = {
            'symbol': symbol,
            'signal_type': signal_type,
            'signal_strength': signal_strength,
            'decision_reason': decision_reason,
            'direction': 'LONG' if signal_type == 'BUY' else 'SHORT',
            
            # 技术指标快照
            'MACD_hist': float(last_candle.get('MACD_hist', 0)),
            'MACD_line': float(last_candle.get('MACD_line', 0)),
            'MACD_signal': float(last_candle.get('MACD_signal', 0)),
            'Relative_ATR': float(last_candle.get('Relative_ATR', 0)),
            'ATR': float(last_candle.get('ATR', 0)),
            'RSI': float(last_candle.get('RSI', 50)),
            'ADX': float(last_candle.get('ADX', 0)),
            'EMA_TREND': float(last_candle.get('EMA_TREND', 0)),
            'Squeeze_On': bool(last_candle.get('Squeeze_On', False)),
            'Squeeze_Fired': bool(last_candle.get('Squeeze_Fired', False)),
            'VWAP': float(last_candle.get('VWAP', 0)),
            
            # 价格信息
            'close_price': float(last_candle.get('close', 0)),
            'open_price': float(last_candle.get('open', 0)),
            'high_price': float(last_candle.get('high', 0)),
            'low_price': float(last_candle.get('low', 0)),
            'volume': float(last_candle.get('volume', 0)),
            
            # 策略配置快照
            'strategy_mode': config.SYSTEM_CONFIG.get("STRATEGY_MODE", "STANDARD"),
            'interval': config.SYSTEM_CONFIG.get("INTERVAL", "15m"),
            'leverage': config.SYSTEM_CONFIG.get("LEVERAGE", 20),
            'risk_ratio': config.SYSTEM_CONFIG.get("RISK_RATIO", 0.02),
        }
        
        return audit_data
    
    except Exception as e:
        print(f"❌ 创建审计快照失败: {e}")
        return {}


# ==========================================
# 🔥 SCALPER 模式模拟交易账本记录
# ==========================================

def _log_sim_trade_to_csv(symbol, direction, entry_price, exit_price, quantity, net_pnl, current_balance):
    """
    记录模拟交易到 CSV 账本（SCALPER 模式专用）
    
    Args:
        symbol: 交易对符号
        direction: 持仓方向 ('LONG' 或 'SHORT')
        entry_price: 开仓价格
        exit_price: 平仓价格
        quantity: 交易数量
        net_pnl: 净盈亏（已扣除手续费）
        current_balance: 当前模拟账户余额
    """
    try:
        # 获取 CSV 文件路径
        csv_file = config.SYSTEM_CONFIG.get("SIM_REPORT_FILE", "simulated_ledger.csv")
        
        # 使用线程锁保护文件写入
        with csv_lock:
            # 检查文件是否存在
            file_exists = os.path.exists(csv_file)
            
            # 以追加模式打开文件
            with open(csv_file, 'a', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                
                # 如果文件不存在，先写入表头
                if not file_exists:
                    writer.writerow([
                        'Timestamp',
                        'Symbol',
                        'Direction',
                        'Entry_Price',
                        'Exit_Price',
                        'Quantity',
                        'Net_PnL',
                        'Current_Balance'
                    ])
                
                # 写入交易数据
                writer.writerow([
                    datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    symbol,
                    direction,
                    f"{entry_price:.4f}",
                    f"{exit_price:.4f}",
                    f"{quantity:.4f}",
                    f"{net_pnl:.2f}",
                    f"{current_balance:.2f}"
                ])
        
        print(f"   📝 模拟交易已记录到 CSV: {symbol} {direction}, 净利: ${net_pnl:.2f}")
    
    except Exception as e:
        print(f"⚠️ 记录模拟交易到 CSV 失败: {e}")


# ==========================================
# 🔥 相关性矩阵与风险敞口计算
# ==========================================

# 🔥 修复#9: 动态相关性矩阵（自动适配 MONITOR_SYMBOLS，新增币种无需手动维护）
# 已知币对的精确相关系数（手动校准值优先级最高）
_KNOWN_CORRELATIONS = {
    ('BTCUSDT', 'ETHUSDT'): 0.85,
    ('BTCUSDT', 'BNBUSDT'): 0.75,
    ('BTCUSDT', 'SOLUSDT'): 0.70,
    ('BTCUSDT', 'ADAUSDT'): 0.65,
    ('ETHUSDT', 'BNBUSDT'): 0.70,
    ('ETHUSDT', 'SOLUSDT'): 0.65,
    ('ETHUSDT', 'ADAUSDT'): 0.60,
    ('BNBUSDT', 'SOLUSDT'): 0.60,
    ('BNBUSDT', 'ADAUSDT'): 0.55,
    ('SOLUSDT', 'ADAUSDT'): 0.50,
}

# 未定义币对的默认相关系数（使用模块级常量 DEFAULT_CRYPTO_CORRELATION）


def _get_correlation(symbol_a, symbol_b):
    """获取两个币种的相关系数，优先使用已知值，否则返回默认值"""
    if symbol_a == symbol_b:
        return 1.0
    # 查找正向和反向键
    corr = _KNOWN_CORRELATIONS.get((symbol_a, symbol_b))
    if corr is not None:
        return corr
    corr = _KNOWN_CORRELATIONS.get((symbol_b, symbol_a))
    if corr is not None:
        return corr
    return DEFAULT_CRYPTO_CORRELATION


def get_correlation_matrix():
    """
    动态生成相关性矩阵，自动包含 MONITOR_SYMBOLS 中的所有币种
    新增币种无需手动维护矩阵，自动使用默认相关系数 0.50
    """
    symbols = config.SYSTEM_CONFIG.get("MONITOR_SYMBOLS", [])
    matrix = {}
    for sym_a in symbols:
        matrix[sym_a] = {}
        for sym_b in symbols:
            if sym_a != sym_b:
                matrix[sym_a][sym_b] = _get_correlation(sym_a, sym_b)
    return matrix


def calculate_correlated_exposure(active_positions, new_symbol):
    """
    🔥 P1缺陷修复：计算相关性调整后的风险敞口
    
    核心逻辑：
    1. 遍历所有现有持仓
    2. 根据相关性矩阵计算等效风险敞口
    3. 累加得到总相关性敞口
    
    Args:
        active_positions: 当前活跃持仓字典
        new_symbol: 新开仓的交易对
    
    Returns:
        float: 相关性调整后的总风险敞口（以USDT计价）
    """
    try:
        total_exposure = 0.0
        
        for key, positions in active_positions.items():
            # 提取币种符号（去除 _LONG/_SHORT 后缀）
            symbol = key.split('_')[0] if '_' in key else key
            
            # 跳过自身
            if symbol == new_symbol:
                continue
            
            # 🔥 修复#9: 使用动态相关性查询，自动适配新增币种
            correlation = _get_correlation(new_symbol, symbol)
            
            # 计算持仓名义价值
            positions_list = positions if isinstance(positions, list) else [positions]
            for pos in positions_list:
                entry_price = pos.get('entry', 0)
                qty = pos.get('qty', 0)
                notional_value = entry_price * qty
                
                # 计算等效风险敞口
                equivalent_exposure = notional_value * correlation
                total_exposure += equivalent_exposure
                
                print(f"   📊 [{symbol}] 名义价值=${notional_value:.2f}, 相关系数={correlation:.2f}, 等效敞口=${equivalent_exposure:.2f}")
        
        print(f"   🔥 [{new_symbol}] 总相关性敞口: ${total_exposure:.2f}")
        return total_exposure
        
    except Exception as e:
        print(f"❌ 计算相关性敞口失败: {e}")
        return 0.0


def adjust_position_for_correlation(base_position_size, correlated_exposure, max_total_exposure, symbol):
    """
    🔥 P1缺陷修复：根据相关性调整仓位大小
    
    核心逻辑：
    1. 计算剩余可用敞口容量
    2. 如果相关性敞口已满，拒绝开仓
    3. 如果有剩余容量，调整仓位至不超过容量上限
    
    Args:
        base_position_size: 基础仓位大小（数量）
        correlated_exposure: 当前相关性敞口
        max_total_exposure: 最大总敞口限制
        symbol: 交易对符号
    
    Returns:
        float: 调整后的仓位大小
    """
    try:
        # 计算剩余容量
        remaining_capacity = max_total_exposure - correlated_exposure
        
        # 检查1：相关性敞口已满
        if remaining_capacity <= 0:
            logger.warning(f"⚠️ [{symbol}] 相关性风险敞口已满，拒绝开仓")
            send_tg_alert(
                f"🚨 <b>[相关性风险敞口已满]</b>\n\n"
                f"币种: {symbol}\n"
                f"当前相关性敞口: ${correlated_exposure:.2f}\n"
                f"最大敞口限制: ${max_total_exposure:.2f}\n\n"
                f"⚠️ 拒绝开仓以防止过度集中风险"
            )
            return 0
        
        # 获取当前价格计算名义价值
        current_price = get_current_price(None, symbol)
        if current_price is None or current_price <= 0:
            print(f"   ⚠️ [{symbol}] 无法获取当前价格，使用基础仓位")
            return base_position_size
        
        base_notional = base_position_size * current_price
        
        # 检查2：基础仓位是否超过剩余容量
        if base_notional > remaining_capacity:
            # 调整仓位至剩余容量
            adjusted_size = remaining_capacity / current_price
            
            # 应用精度处理
            adjusted_size = round_to_quantity_precision(adjusted_size, symbol)
            
            reduction_pct = (1 - adjusted_size / base_position_size) * 100
            
            logger.info(f"📊 [{symbol}] 相关性调整: {base_position_size:.4f} → {adjusted_size:.4f} (降低 {reduction_pct:.1f}%)")
            
            send_tg_msg(
                f"📊 <b>[相关性仓位调整]</b>\n"
                f"币种: {symbol}\n"
                f"原始仓位: {base_position_size:.4f}\n"
                f"调整后: {adjusted_size:.4f}\n"
                f"降低: {reduction_pct:.1f}%\n"
                f"原因: 相关性敞口接近上限\n"
                f"剩余容量: ${remaining_capacity:.2f}"
            )
            
            return adjusted_size
        else:
            # 基础仓位在容量范围内，无需调整
            print(f"   ✅ [{symbol}] 相关性检查通过，无需调整仓位")
            return base_position_size
        
    except Exception as e:
        print(f"❌ 调整相关性仓位失败: {e}")
        return base_position_size


# ==========================================
# 🔥 Task 3: 投资组合相关性控制
# ==========================================

def check_portfolio_correlation(new_position_type, client=None):
    """
    🔥 Task 3: 投资组合相关性断路器（含策略模式差异化阈值 + 4h视觉弱势检测）
    
    核心逻辑：
    1. 计算当前持仓的方向分布（LONG vs SHORT）
    2. 🔥 根据 STRATEGY_MODE 动态调整同向持仓阈值：
       - SCALPER: 90% (剥头皮持仓时间极短，允许高集中度)
       - AGGRESSIVE/STANDARD: 70% (默认阈值)
       - CONSERVATIVE/GOLD_PRO: 50% (最严格风控)
    3. 🔥 4h视觉弱势检测：如果同向持仓超限且 4h K线显示BTC弱势，强制 RISK_RATIO *= 0.5
    
    Args:
        new_position_type: 新开仓方向 ('LONG' 或 'SHORT')
    
    Returns:
        (allowed: bool, reason: str)
    """
    try:
        with positions_lock:
            if not config.ACTIVE_POSITIONS:
                return True, "无现有持仓，放行"
            
            # 统计当前持仓方向分布
            long_count = 0
            short_count = 0
            
            for key, positions in config.ACTIVE_POSITIONS.items():
                positions_list = positions if isinstance(positions, list) else [positions]
                for pos in positions_list:
                    if pos.get('type') == 'LONG':
                        long_count += 1
                    elif pos.get('type') == 'SHORT':
                        short_count += 1
            
            total_positions = long_count + short_count
            if total_positions == 0:
                return True, "无有效持仓，放行"
            
            # 🔥 Task 3: 根据策略模式动态调整同向持仓阈值
            current_mode = config.SYSTEM_CONFIG.get("STRATEGY_MODE", "STANDARD")
            
            # 模式阈值映射
            mode_thresholds = {
                "SCALPER": 0.90,        # 剥头皮：90% 同向持仓阈值
                "AGGRESSIVE": 0.70,     # 激进：70% 同向持仓阈值
                "STANDARD": 0.70,       # 标准：70% 同向持仓阈值
                "CONSERVATIVE": 0.50,   # 保守：50% 同向持仓阈值
                "GOLD_PRO": 0.50        # 黄金专业：50% 同向持仓阈值
            }
            
            same_direction_threshold = mode_thresholds.get(current_mode, 0.70)
            
            # 计算同向持仓比例
            if new_position_type == 'LONG':
                same_direction_count = long_count
            else:
                same_direction_count = short_count
            
            same_direction_ratio = same_direction_count / total_positions if total_positions > 0 else 0
            
            print(f"   📊 投资组合相关性检查: 模式={current_mode}, 同向持仓={same_direction_count}/{total_positions} ({same_direction_ratio:.1%}), 阈值={same_direction_threshold:.0%}")
            
            # 🔥 如果同向持仓比例超过阈值，触发断路器检查
            if same_direction_ratio > same_direction_threshold:
                print(f"   🚨 同向持仓比例 {same_direction_ratio:.1%} > 阈值 {same_direction_threshold:.0%}，触发相关性断路器检查...")
                
                # 🔥 Fix #10: 4h视觉弱势检测：复用调用方传入的 client，避免永远跳过
                btc_4h_weakness = check_btc_4h_visual_weakness(client=client)
                
                if btc_4h_weakness:
                    # 🔥 修复#8: 记录原始RISK_RATIO，用于后续恢复
                    if '_ORIGINAL_RISK_RATIO' not in config.SYSTEM_CONFIG:
                        config.SYSTEM_CONFIG['_ORIGINAL_RISK_RATIO'] = config.SYSTEM_CONFIG.get("RISK_RATIO", 0.02)
                    
                    original_risk_ratio = config.SYSTEM_CONFIG['_ORIGINAL_RISK_RATIO']
                    reduced_risk_ratio = original_risk_ratio * 0.5
                    
                    with state_lock:
                        config.SYSTEM_CONFIG["RISK_RATIO"] = reduced_risk_ratio
                        config.SYSTEM_CONFIG["_RISK_RATIO_REDUCED"] = True
                        config.SYSTEM_CONFIG["_RISK_RATIO_REDUCE_TIME"] = time.time()
                        save_data()
                    
                    print(f"   📉 相关性断路器触发！RISK_RATIO 已强制降低: {original_risk_ratio:.2%} → {reduced_risk_ratio:.2%}")
                    send_tg_alert(
                        f"🚨 <b>[相关性断路器触发]</b>\n\n"
                        f"策略模式: {current_mode}\n"
                        f"同向持仓比例: {same_direction_ratio:.1%} > {same_direction_threshold:.0%}\n"
                        f"4h视觉检测: BTC显示弱势\n"
                        f"风险比率已强制降低50%\n"
                        f"{original_risk_ratio:.2%} → {reduced_risk_ratio:.2%}\n\n"
                        f"🛡️ 防御性仓位管理已激活"
                    )
                    
                    return True, f"相关性断路器触发，风险比率已降低50% (同向持仓={same_direction_ratio:.1%})"
                else:
                    print(f"   ✅ 4h视觉检测：BTC未显示弱势，放行")
            
            # 🔥 修复#8: 恢复机制 - 如果同向持仓比例降低且距离上次降低超过1小时，恢复RISK_RATIO
            if config.SYSTEM_CONFIG.get("_RISK_RATIO_REDUCED", False):
                reduce_time = config.SYSTEM_CONFIG.get("_RISK_RATIO_REDUCE_TIME", 0)
                time_since_reduce = time.time() - reduce_time
                
                # 恢复条件：同向持仓比例 < 阈值-10% 且距离上次降低超过1小时
                if same_direction_ratio < (same_direction_threshold - 0.1) and time_since_reduce > 3600:
                    original_risk_ratio = config.SYSTEM_CONFIG.get('_ORIGINAL_RISK_RATIO', 0.02)
                    with state_lock:
                        config.SYSTEM_CONFIG["RISK_RATIO"] = original_risk_ratio
                        config.SYSTEM_CONFIG["_RISK_RATIO_REDUCED"] = False
                        save_data()
                    print(f"   ✅ RISK_RATIO已恢复: {config.SYSTEM_CONFIG['RISK_RATIO']:.2%} (同向持仓降至 {same_direction_ratio:.1%})")
            
            return True, f"相关性检查通过 (同向持仓={same_direction_ratio:.1%}, 阈值={same_direction_threshold:.0%})"
    
    except Exception as e:
        print(f"❌ 投资组合相关性检查失败: {e}")
        # 检查失败时保守拒绝
        return False, f"相关性检查异常: {str(e)[:50]}"


def check_btc_4h_visual_weakness(client=None):
    """
    🔥 Task 3: 4h视觉弱势检测
    
    检查BTC 4h K线是否显示弱势（用于相关性断路器）
    
    🔥 修复#7: 复用主循环传入的 client，避免每次新建 BinanceClient 浪费 TCP 握手和 API 权重
    
    判定逻辑：
    1. 获取BTC 4h K线最近10根
    2. 检查是否出现连续下跌或顶部反转形态
    3. 检查价格是否跌破 MA25
    
    Args:
        client: 复用的 Binance 客户端（由调用方传入）
    
    Returns:
        bool: True=检测到弱势，False=未检测到弱势
    """
    try:
        # 🔥 v2.17 修复：回测模式下跳过 API 调用，避免子进程卡死
        if IS_BACKTEST_PROCESS:
            return False
        
        # 🔥 修复#7: 如果没有传入 client，尝试从指标缓存判断（零API调用）
        if client is None:
            print(f"   ⚠️ BTC 4h弱势检测: 无可用client，跳过")
            return False
        
        df_4h = get_historical_klines(client, 'BTCUSDT', "4h", limit=50)
        if df_4h is None or len(df_4h) < 25:
            print(f"   ⚠️ BTC 4h K线数据不足，跳过弱势检测")
            return False
        
        # 计算 MA25
        import pandas_ta as ta
        ma25 = ta.sma(df_4h['close'], length=25)
        if ma25 is None or len(ma25) == 0:
            return False
        
        df_4h['MA25'] = ma25
        
        # 获取最近10根K线
        recent_candles = df_4h.tail(10)
        last_candle = recent_candles.iloc[-1]
        
        # 检查1：价格是否跌破 MA25
        price_below_ma25 = last_candle['close'] < last_candle['MA25']
        
        # 检查2：最近3根K线是否连续下跌
        last_3_candles = recent_candles.tail(3)
        consecutive_down = all(
            last_3_candles.iloc[i]['close'] < last_3_candles.iloc[i-1]['close']
            for i in range(1, len(last_3_candles))
        )
        
        # 检查3：最近一根K线是否为大阴线（实体 > ATR * 1.5）
        if 'ATR' in df_4h.columns:
            atr = last_candle.get('ATR', 0)
            candle_body = abs(last_candle['close'] - last_candle['open'])
            is_big_bearish = (last_candle['close'] < last_candle['open']) and (candle_body > atr * 1.5)
        else:
            is_big_bearish = False
        
        # 综合判定：任意两个条件满足即判定为弱势
        weakness_signals = [price_below_ma25, consecutive_down, is_big_bearish]
        weakness_count = sum(weakness_signals)
        
        is_weak = weakness_count >= 2
        
        if is_weak:
            print(f"   🔴 BTC 4h弱势检测: 价格破MA25={price_below_ma25}, 连续下跌={consecutive_down}, 大阴线={is_big_bearish}")
        else:
            print(f"   ✅ BTC 4h强势: 弱势信号数={weakness_count}/3")
        
        return is_weak
        
    except Exception as e:
        print(f"   ⚠️ BTC 4h弱势检测异常: {e}")
        return False  # 异常时保守返回False


def calculate_btc_recent_pnl(lookback_trades=5):
    """
    计算BTC相关交易的近期PnL（用于弱势检测）
    
    Args:
        lookback_trades: 回溯交易笔数
    
    Returns:
        float: 近期PnL总和
    """
    try:
        
        with state_lock:
            if not config.TRADE_HISTORY:
                return 0.0
            
            # 筛选BTC相关交易
            btc_trades = [
                t for t in config.TRADE_HISTORY 
                if 'BTC' in t.get('symbol', '').upper()
            ]
            
            if not btc_trades:
                return 0.0
            
            # 取最近N笔交易
            recent_btc_trades = btc_trades[-lookback_trades:]
            
            # 计算总PnL
            total_pnl = sum(t.get('pnl', 0) for t in recent_btc_trades)
            
            return total_pnl
    
    except Exception as e:
        print(f"⚠️ 计算BTC近期PnL失败: {e}")
        return 0.0


# ==========================================
# 🔥 Task 4: AI交易日志生成
# ==========================================

def generate_ai_journal_entry(trade_record, trade_id):
    """
    🔥 Task 4: 生成AI交易日志（使用 Claude Vision 分析 K 线图复盘）
    
    核心逻辑：
    1. 从审计日志中提取开仓时的技术形态和宏观背景
    2. 获取 mplfinance 生成的 K 线图 Byte 流
    3. 使用 ClaudeCommander.analyze_chart_with_vision_bytes() 进行视觉复盘
    4. 生成包含 K 线实体分析、假突破识别、AI 自主形态评分的复盘内容
    
    Args:
        trade_record: 交易记录字典
        trade_id: 交易ID（用于查询审计日志）
    
    Returns:
        str: AI 视觉复盘日志（包含形态分析和评分）
    """
    try:
        # 提取基础信息
        symbol = trade_record.get('symbol', 'UNKNOWN')
        direction = trade_record.get('type', 'UNKNOWN')
        pnl = trade_record.get('pnl', 0)
        entry_price = trade_record.get('entry', 0)
        exit_price = trade_record.get('exit', 0)
        
        # 从审计日志获取开仓时的技术形态
        audit_log = get_audit_log(str(trade_id)) if trade_id else None
        
        # 🔥 调用 ClaudeCommander 进行视觉复盘
        try:
            from ai_analyst import ClaudeCommander
            from utils import get_kline_chart_buffer
            from trading_engine import get_historical_klines
            from binance.client import Client as BinanceClient
            
            commander = ClaudeCommander()
            
            # 🔥 Step 1: 获取 K 线数据并生成图表
            print(f"   📊 正在生成 {symbol} K 线图用于复盘...")
            
            # 获取最近 50 根 K 线数据
            client = BinanceClient(
                api_key=config.SYSTEM_CONFIG.get('API_KEY'),
                api_secret=config.SYSTEM_CONFIG.get('API_SECRET')
            )
            df = get_historical_klines(client, symbol, "15m", limit=50)
            
            if df is None or len(df) < 10:
                raise Exception("K 线数据不足")
            
            # 生成 K 线图 Byte 流
            image_bytes = get_kline_chart_buffer(df, symbol=symbol, num_candles=50)
            
            if image_bytes is None:
                raise Exception("K 线图生成失败")
            
            # 🔥 Step 2: 构建视觉复盘 Prompt
            journal_prompt = f"""# 🎯 交易复盘 - K 线形态视觉分析

## 交易基本信息
- 交易对: {symbol}
- 方向: {direction}
- 盈亏: ${pnl:.2f}
- 开仓价: {entry_price}
- 平仓价: {exit_price}
- 技术形态: {audit_log.get('decision_reason', '未知') if audit_log else '未知'}
- 宏观背景: {config.SYSTEM_CONFIG.get('MACRO_WEATHER_REGIME', 'SAFE')}

## 复盘任务
请观察这张 K 线图，进行深度复盘分析：

1. **K 线实体分析**:
   - 识别开仓前后的 K 线实体大小和颜色
   - 判断是否存在大实体蜡烛（可能是追涨杀跌信号）
   - 分析实体与影线的比例

2. **假突破识别**:
   - 检查是否存在假突破形态（突破后立即回撤）
   - 识别诱多/诱空陷阱
   - 分析突破时的成交量确认

3. **AI 自主形态评分**:
   - 对该交易的入场时机进行评分（0-10分）
   - 评估形态的可靠性和成功概率
   - 给出改进建议

## 输出格式
请用简洁的中文回答（200字符以内），包含：
- 形态类型（如：假突破/真突破/震荡）
- 评分（0-10分）
- 核心问题或亮点
- 一句话改进建议

示例：
"假突破陷阱，评分3/10。开仓时K线实体过大（追涨），缺乏成交量确认。建议：等待回调至支撑位再入场。"
"""
            
            # 🔥 Step 3: 使用 Claude Vision 分析 K 线图
            print(f"   🤖 正在调用 Claude Vision 进行复盘分析...")
            response = commander.analyze_chart_with_vision_bytes(image_bytes, journal_prompt)
            
            # 提取日志（去除多余格式）
            journal = response.strip().replace('\n', ' ')
            
            # 限制长度（200字符）
            if len(journal) > 200:
                journal = journal[:197] + "..."
            
            print(f"   📝 AI 视觉复盘已生成: {journal}")
            return journal
            
        except Exception as ai_e:
            print(f"   ⚠️ AI 视觉复盘失败，使用简化模板: {ai_e}")
            
            # 回退到简化模板
            if pnl > 0:
                journal = f"✅{symbol}{direction}+${pnl:.0f}"
            else:
                journal = f"❌{symbol}{direction}-${abs(pnl):.0f}"
            
            # 添加技术形态信息（如果有）
            if audit_log:
                reason = audit_log.get('decision_reason', '')[:30]
                journal += f",{reason}"
            
            # 限制长度
            if len(journal) > 100:
                journal = journal[:97] + "..."
            
            return journal
    
    except Exception as e:
        print(f"❌ 生成AI日志失败: {e}")
        return f"日志生成失败: {str(e)[:30]}"
        
# ==========================================
# ☠️ 爆仓监听器蓝图 (WebSocket Liquidation Sniping)
# ==========================================

async def liquidation_sniper():
    """
    ☠️ 爆仓监听器蓝图 (需在主线程或后台异步运行)
    
    核心逻辑：
    1. 连接 Binance WebSocket: wss://fstream.binance.com/ws/btcusdt@forceOrder
    2. 监听巨额爆仓单 (e.g., > 250,000 USDT)
    3. 瞬间计算偏离度，在现价下方 2% (多头爆仓) 或上方 2% (空头爆仓)
    4. 极速挂出 Post-Only Limit 单，做 V 反剥头皮
    
    WebSocket 消息格式示例：
    {
        "e": "forceOrder",
        "E": 1568014460893,
        "o": {
            "s": "BTCUSDT",
            "S": "SELL",  # 爆仓方向：SELL=多头爆仓，BUY=空头爆仓
            "o": "LIMIT",
            "f": "IOC",
            "q": "0.014",
            "p": "9910",  # 爆仓价格
            "ap": "9910",
            "X": "FILLED",
            "l": "0.014",
            "z": "0.014",
            "T": 1568014460893
        }
    }
    
    实现步骤：
    1. 安装依赖: pip install websockets
    2. 在 main.py 中启动异步任务: asyncio.create_task(liquidation_sniper())
    3. 配置爆仓阈值: config.SYSTEM_CONFIG["LIQUIDATION_THRESHOLD"] = 250000
    4. 配置反弹偏离度: config.SYSTEM_CONFIG["LIQUIDATION_BOUNCE_PCT"] = 0.02
    
    ⚠️ 注意事项：
    - 需要极低延迟网络（建议使用 AWS Tokyo 服务器）
    - 需要配合 Post-Only 订单避免吃单手续费
    - 需要严格止损（爆仓后可能继续下跌）
    """
    import asyncio
    import websockets
    import json
    
    # WebSocket 端点（示例：BTC 爆仓流）
    ws_url = "wss://fstream.binance.com/ws/btcusdt@forceOrder"
    
    # 🔥 修复#11: 用 while True 循环替代递归重连，防止栈溢出
    while True:
        try:
            async with websockets.connect(ws_url) as websocket:
                print("☠️ 爆仓监听器已启动，等待巨额爆仓单...")
                
                while True:
                    try:
                        message = await websocket.recv()
                        data = json.loads(message)
                        
                        # 解析爆仓单
                        if data.get('e') == 'forceOrder':
                            order = data.get('o', {})
                            symbol = order.get('s', '')
                            side = order.get('S', '')  # SELL=多头爆仓，BUY=空头爆仓
                            price = float(order.get('p', 0))
                            quantity = float(order.get('q', 0))
                            
                            # 计算爆仓单名义价值
                            notional_value = price * quantity
                            
                            # 阈值过滤：只处理巨额爆仓单
                            threshold = config.SYSTEM_CONFIG.get("LIQUIDATION_THRESHOLD", 100000)
                            if notional_value < threshold:
                                continue
                            
                            print(f"☠️ 检测到巨额爆仓单: {symbol} {side} ${notional_value:.0f}")
                            
                            # 🔥 V 反剥头皮逻辑
                            bounce_pct = config.SYSTEM_CONFIG.get("LIQUIDATION_BOUNCE_PCT", 0.02)
                            
                            if side == 'SELL':
                                snipe_price = price * (1 - bounce_pct)
                                snipe_side = 'BUY'
                                print(f"   🎯 V反抄底: 在 ${snipe_price:.2f} 挂买单")
                            else:
                                snipe_price = price * (1 + bounce_pct)
                                snipe_side = 'SELL'
                                print(f"   🎯 V反摸顶: 在 ${snipe_price:.2f} 挂卖单")
                            
                            # TODO: 调用 execute_trade() 执行 Post-Only Limit 单
                            
                    except Exception as msg_e:
                        print(f"⚠️ 爆仓消息解析失败: {msg_e}")
                        continue
                        
        except Exception as e:
            print(f"❌ 爆仓监听器异常: {e}，5秒后重连...")
            await asyncio.sleep(5)
            # 循环回到 while True 顶部自动重连，不再递归调用


print("✅ 交易引擎模块已加载（含主循环和订单执行逻辑 + 子仓位控制 + 决策审计 + 投资组合相关性控制 + AI交易日志 + 爆仓监听器蓝图）")
