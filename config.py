#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
配置管理模块 - config.py
负责所有系统配置的加载、保存和验证
"""

import sys
import builtins

# 🔥 终极消音器：检测当前进程是否为回测进程
IS_BACKTEST_PROCESS = any("backtest_worker.py" in arg for arg in sys.argv)

def silent_print(*args, **kwargs):
    """配置模块专用打印：实盘时大声汇报，回测时绝对闭嘴"""
    if not IS_BACKTEST_PROCESS:
        builtins.print(*args, **kwargs)

# 🔥 仅在检测到回测进程时劫持
if IS_BACKTEST_PROCESS:
    print = silent_print

import os
import json
import copy
import time
import threading
from datetime import datetime
from binance.enums import *
from dotenv import load_dotenv

# ==========================================
# 修复 Windows 控制台 UTF-8 编码问题
# ==========================================
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ('utf-8', 'utf8'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
if sys.stderr.encoding and sys.stderr.encoding.lower() not in ('utf-8', 'utf8'):
    try:
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

# 加载环境变量
load_dotenv()

# ==========================================
# 配置文件路径
# ==========================================
CONFIG_FILE = "bot_config.json"
POSITIONS_FILE = "active_positions.json"
TRADE_HISTORY_FILE = "trade_history.json"
SENTRY_FILE = "sentry_watchlist.json"
SESSION_FILE = "user_sessions.json"  # 🔥 用户会话状态持久化文件
DLQ_FILE = "dead_letter_queue.json"  # 🔥 死信队列持久化文件

# ==========================================
# 线程锁（并发安全）
# ==========================================
state_lock = threading.RLock()  # 🔥 全局状态锁（保护 SYSTEM_CONFIG、TRADE_HISTORY、ACTIVE_POSITIONS）
positions_lock = threading.RLock()
config_lock = threading.RLock()
engine_lock = threading.RLock()
cache_lock = threading.RLock()
circuit_breaker_lock = threading.RLock()
csv_lock = threading.RLock()  # CSV 写入锁（防止并发平仓时账本写入冲突）

# ==========================================
# 策略模式预设配置
# ==========================================
STRATEGY_PRESETS = {
    "CONSERVATIVE": {
        "name": "🟢 稳健模式 (Conservative)",
        "emoji": "🛡️",
        "INTERVAL": "1h",
        "HIGHER_INTERVAL": "4h",
        "ADX_THR": 22,
        "EMA_TREND": 169,
        "MTF_TREND_EMA": 169,             # 🔥 高周期趋势EMA（MTF对齐专用）
        "ATR_MULT": 3.0,
        "CHECK_INTERVAL": 3600,
        "USE_STRICT_LOGIC": True,
        "USE_HIGHER_TF_FILTER": True,      # 🔥 强制开启MTF过滤
        "VOL_LIMIT": 1.5,
        # 🔥 空间锁（Price Volatility Filter）
        "MAX_CANDLE_BODY_ATR": 1.2,        # 🔥 调至1.2，适度放宽以捕获有效突破
        "SIGNAL_CONFIRM_BARS": 2,          # 必须收盘确认2次
        # 🔥 RSI 动态过滤区间
        "RSI_RANGE": [35, 65],             # 极其严格的非超买超卖区
        # 🔥 差异化安全杠杆
        "LEVERAGE": 5.0,                   # 低杠杆抵御40%级别插针
        "description": "极高胜率，过滤95%噪音，每周2-4次信号，适合BTC/ETH核心资产 | 推荐杠杆: 5x"
    },
    "STANDARD": {
        "name": "🟡 标准模式 (Balanced)",
        "emoji": "⚖️",
        "INTERVAL": "15m",
        "HIGHER_INTERVAL": "1h",
        "ADX_THR": 10,                     # 🔥 降至10，提升信号灵敏度
        "EMA_TREND": 89,
        "ATR_MULT": 2.3,
        "CHECK_INTERVAL": 900,
        "USE_MACD_ACCEL": True,
        "USE_HIGHER_TF_FILTER": True,      # 🔥 开启MTF过滤
        "VOL_LIMIT": 2.0,
        # 🔥 空间锁（Price Volatility Filter）
        "MAX_CANDLE_BODY_ATR": 1.8,        # 🔥 设为1.8，平衡过滤与捕获
        "SIGNAL_CONFIRM_BARS": 1,          # 收盘即开
        # 🔥 RSI 动态过滤区间
        "RSI_RANGE": [30, 70],             # 标准超买超卖区
        # 🔥 差异化安全杠杆
        "LEVERAGE": 10.0,                  # 平衡收益与风险
        "description": "灵敏度与稳定性平衡，每天1-3次信号，适合主流币日常量化 | 推荐杠杆: 10x"
    },
    "AGGRESSIVE": {
        "name": "🔴 激进模式 (Aggressive)",
        "emoji": "🔥",
        "INTERVAL": "5m",
        "HIGHER_INTERVAL": "15m",
        "ADX_THR": 8,
        "EMA_TREND": 44,
        "ATR_MULT": 1.8,
        "CHECK_INTERVAL": 60,
        "USE_LATEST_CANDLE": True,
        "USE_HIGHER_TF_FILTER": True,
        "DYNAMIC_RSI": True,               # 🔥 动态RSI：ADX>30或疯狗时自动扩展RSI区间
        "VOL_LIMIT": 2.5,
        # 🔥 空间锁（Price Volatility Filter）
        "MAX_CANDLE_BODY_ATR": 2.2,        # 允许捕获爆发性突破（疯狗激活时自动*1.5=3.3）
        "SIGNAL_CONFIRM_BARS": 0,          # 即时触发
        # 🔥 RSI 动态过滤区间
        "RSI_RANGE": [25, 75],             # 宽松RSI空间（动态RSI开启时可扩展至[18,82]）
        # 🔥 差异化安全杠杆
        "LEVERAGE": 15.0,                  # 强化动能捕获
        "description": "高频收割，每分钟扫描，每天10-20次信号，需严格止损，适合低波动环境 | 推荐杠杆: 15x"
    },
    "SCALPER": {
        "name": "⚡ 狂战士模式 (Berserker Scalper)",
        "emoji": "⚡",
        "INTERVAL": "1m",
        "HIGHER_INTERVAL": "5m",
        "ADX_THR": 5,
        "EMA_TREND": 21,
        "ATR_MULT": 1.2,
        "CHECK_INTERVAL": 2,
        "USE_LATEST_CANDLE": True,
        "USE_HIGHER_TF_FILTER": False,
        "SIGNAL_CONFIRM_BARS": 0,
        "SQUEEZE_DURATION_THR": 2,
        "MAX_SLIPPAGE": 0.0005,
        "SCALPER_BREAKEVEN_ATR": 1.0,
        "SCALPER_PARTIAL_TP_ATR": 1.2,
        "SCALPER_PARTIAL_TP_RATIO": 0.5,
        "SCALPER_TIME_MELT_MINS": 15,
        "VOL_LIMIT": 3.5,
        # 🔥 空间锁（Price Volatility Filter）
        "MAX_CANDLE_BODY_ATR": 3.0,       # 最大化容忍高波动
        # 🔥 RSI 动态过滤区间
        "RSI_RANGE": [15, 85],             # 几乎不限制空间
        # 🔥 差异化安全杠杆
        "LEVERAGE": 20.0,                  # 高频小波段利润放大
        "description": "1分钟超高频剥头皮，即发即开，极致滑点拦截，15分钟时间熔断，适合高流动性品种 | 推荐杠杆: 20x"
    },
    "GOLD_PRO": {
        "name": "🏆 黄金专业模式 (Gold Pro)",
        "emoji": "🏆",
        "INTERVAL": "15m",
        "HIGHER_INTERVAL": "1h",
        "ADX_THR": 18,
        "EMA_TREND": 89,
        "ATR_MULT": 2.8,                   # 🔥 锁定2.8，黄金专属止损宽度
        "CHECK_INTERVAL": 900,
        "USE_STRICT_LOGIC": True,
        "USE_MACD_ACCEL": True,
        "USE_HIGHER_TF_FILTER": True,      # 🔥 严格执行1h周期趋势对齐
        "VOL_LIMIT": 1.8,
        # 🔥 空间锁（Price Volatility Filter）
        "MAX_CANDLE_BODY_ATR": 1.2,        # 🔥 黄金专属：严格波动控制，锁定1.2
        "SIGNAL_CONFIRM_BARS": 1,          # 收盘确认
        # 🔥 RSI 动态过滤区间
        "RSI_RANGE": [32, 68],             # 黄金专属：避开极端区域
        # 🔥 差异化安全杠杆
        "LEVERAGE": 8.0,                   # 黄金专属：保守杠杆（黄金波动大）
        "description": "黄金专业策略，15分钟级别，严格趋势过滤+1h MTF对齐，适合XAUUSDT等贵金属品种 | 推荐杠杆: 8x"
    }
}

# ==========================================
# 系统配置
# ==========================================
SYSTEM_CONFIG = {
    "API_KEY": os.getenv("BINANCE_API_KEY", ""),
    "API_SECRET": os.getenv("BINANCE_API_SECRET", ""),
    "TG_TOKEN": os.getenv("TG_TOKEN", ""),
    "TG_CHAT_ID": os.getenv("TG_CHAT_ID", ""),

    # LLM 配置
    "LLM_PROVIDER": os.getenv("LLM_PROVIDER", "gemini"),
    "GEMINI_API_KEY": os.getenv("GEMINI_API_KEY", ""),
    "ANTHROPIC_API_KEY": os.getenv("ANTHROPIC_API_KEY", ""),
    "ANTHROPIC_BASE_URL": os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com"),
    "LLM_MODEL_NAME": os.getenv("LLM_MODEL_NAME", "gemini-1.5-flash"),
    
    # 情报系统配置
    "CRYPTOPANIC_API_KEY": os.getenv("CRYPTOPANIC_API_KEY", ""),

    "SIGNALS_ENABLED": True,
    
    # 峰值权益追踪（持久化到配置文件，解决重启失效问题）
    "PEAK_EQUITY": 0.0,
    
    # 策略模式
    "STRATEGY_MODE": "STANDARD",
    "IS_CUSTOM_MODE": False,
    
    # 核心技术指标 (🔥 v1.0 性能诊断报告修复 - 标准MACD参数)
    "INTERVAL": "1h",
    "MACD_FAST": 12,               # 🔥 修复1: 从8改为12（标准参数，减少假信号）
    "MACD_SLOW": 26,               # 🔥 修复1: 从21改为26（标准参数，减少假信号）
    "MACD_SIGNAL": 9,              # 🔥 修复1: 从5改为9（标准参数，减少假信号）
    "EMA_TREND": 89,               # 🔥 修复2: 从144降低到89（提升反应速度）
    "ADX_THR": 25,                 # 🔥 修复3: 从20提升至25（强化趋势过滤）
    
    # 🔥 参谋部补强A: 阶梯式 ADX 门槛（Volume-Adaptive ADX Threshold）
    "ADX_THR_VOLUME_BURST": 20,    # 🔥 放量突破时的 ADX 门槛（早入场捕获动能）
    "ADX_THR_NORMAL": 28,          # 🔥 常规突破时的 ADX 门槛（严格过滤震荡噪音）
    "VOLUME_BURST_MULT": 1.5,      # 🔥 放量判定倍数（当前成交量 > 1.5x 20均量时视为放量）
    
    "HIGHER_INTERVAL": "4h",       # 🔥 强制开启4h趋势对齐
    "USE_HIGHER_TF_FILTER": True,  # 🔥 强制开启MTF过滤
    "MTF_TREND_EMA": 169,          # 🔥 高周期趋势EMA（MTF对齐专用）
    "RSI_PERIOD": 14,
    "RSI_OVERBOUGHT": 65,          # 🔥 ETH 1h 铁闸门
    "RSI_OVERSOLD": 25,
    "RSI_FILTER_ENABLED": True,
    "LOW_VOL_MODE": False,
    "HEDGE_MODE": False,
    "HEDGE_MODE_ENABLED": False,

    # 仓位与复利 (🔥 300天长周期生存补丁 - 激进与稳健的平衡点)
    "BENCHMARK_CASH": 1800.0,
    "RISK_RATIO": 0.025,           # 🔥 参谋部补强B: 降至 2.5%（点火期策略 - Sharpe>1.2前低风险保护本金）
    "LEVERAGE": 20.0,
    "MAD_DOG_MODE": True,
    "MAD_DOG_TRIGGER": 1.20,       # 🔥 盈利 20% 后再加速（温和触发）
    "MAD_DOG_BOOST": 1.5,          # 🔥 1.5倍配资（温和扩张）

    # 动态止损 (🔥 v3.0 三阶段动态止损 + v1.0诊断报告优化)
    "ATR_PERIOD": 14,
    "ATR_MULT": 2.0,               # 🔥 修复4: 从2.5降低到2.0（收紧止损，减少单笔亏损）
    "SL_BUFFER": 1.02,             # 🔥 修复4: 从1.05降低到1.02（减少滑点冗余）
    
    # 🔥 v3.0 三阶段动态止损参数 (优化后)
    "STAGE_A_PROFIT_MULT": 0.8,    # 阶段A触发：浮盈达到 0.8x ATR (更早保护)
    "STAGE_A_SL_MULT": 0.6,        # 阶段A止损：入场价 ± 0.6x ATR（风险减半）
    "STAGE_B_PROFIT_MULT": 2.2,    # 🔥 Patch v7.1: 上调至 2.2（宽追踪模式 - 保本门槛提升）
    "STAGE_B_SL_OFFSET": 0.002,    # 阶段B止损：入场价 × (1 ± 0.002)（智能保本）
    
    # 移动止损 (🔥 Stage 3: TSL 利润收割 - 参谋部补强C优化)
    "TSL_ENABLED": True,           # 🔥 开启移动止盈（进攻型必须开启）
    "TSL_TRIGGER_MULT": 3.0,       # 🔥 参谋部补强C: 上调至 3.0（利润达3.0x ATR时激活TSL）
    "TSL_CALLBACK_MULT": 2.0,      # 🔥 参谋部补强C: 放宽至 2.0（止损跟进在MaxPrice - 2.0x ATR）
    "TSL_UPDATE_THRESHOLD": 0.005,
    
    # 🔥 v3.0 量能过滤器
    "VOLUME_SURGE_THRESHOLD": 1.3, # 量能突破阈值：1.3x 平均成交量

    # 激进模式专属参数
    "AGG_HIST_EXTREME_MULT": 1.5,
    "AGG_REVERSAL_BARS": 2,
    "AGG_SL_ATR_MULT": 2.5,
    "AGG_PARTIAL_TP_ATR_MULT": 1.0,
    "AGG_PARTIAL_TP_RATIO": 0.5,
    "AGG_TSL_ATR_MULT": 1.5,
    "AGG_RSI_PERIOD": 14,
    "AGG_RSI_LONG_MAX": 40,
    "AGG_RSI_SHORT_MIN": 60,

    # 🔥 自适应动态保险库（v2.0 重构：删除旧 VAULT_THR，改用比例自适应）
    "VAULT_ENABLED": True,
    "VAULT_ASSET": "USDT",
    "VAULT_BALANCE": 0.0,  # 已成功划转到现货账户的累计利润总额
    "WITHDRAW_RATIO": 0.5,     # 触发后的划转比例
    
    # 🔥 自适应保险库（Auto-Adaptive Threshold）(🔥 激进模式：更早锁定利润)
    # 阈值 = BENCHMARK_CASH * 动态比例（由凯利系数和回撤率自动调节）
    "VAULT_AUTO_ADAPT": True,  # 开启自适应阈值（True=动态比例，False=固定 BASE_RATIO）
    "VAULT_BASE_RATIO": 0.15,  # 基准触发比例 15%
    "VAULT_MIN_RATIO": 0.03,   # 最低触发比例，防守底线 3% (🔥 降低抽水门槛)
    "VAULT_MAX_RATIO": 0.30,   # 最高触发比例，复利上限 30%
    
    # 🔥 空间锁（Space Lock）控制开关
    # True = 机构风控模式（启用波动率拦截）
    # False = 狂战士高频模式（关闭拦截，允许追涨杀跌）
    "SPACE_LOCK_ENABLED": True,
    "MAX_CANDLE_BODY_ATR": 1.5,  # 🔥 严格化：禁止在情绪化大阳/大阴线后追涨杀跌
    
    # 🔥 强制疯狗模式（Force Mad Dog Mode）
    # True = 无视盈利条件，强行激活双倍配资（危险！）
    "FORCE_MAD_DOG_MODE": False,

    # 资产分布
    "MAX_ACTIVE_SYMBOLS": 5,
    "ASSET_WEIGHTS": {
        "BTCUSDT": 0.5,
        "ETHUSDT": 0.5
    },
    
    # 多重子仓位管理（Multi-Trade Pyramiding）(🔥 300天长周期生存补丁：防止过度集中)
    "MAX_CONCURRENT_TRADES_PER_SYMBOL": 2,  # 🔥 锁定2笔（减少震荡市回撤堆叠）
    "MIN_SIGNAL_DISTANCE_ATR": 1.0,         # 🔥 提升至1.0（第一笔浮盈后才允许加仓）
    
    # 价格监控
    "PRICE_MONITOR_ENABLED": True,
    "PRICE_ALERT_THRESHOLD": 0.03,
    "PRICE_UPDATE_INTERVAL": 300,
    "MONITOR_SYMBOLS": ["BTCUSDT", "ETHUSDT"],
    
    # 手续费配置
    "COMMISSION_RATE": 0.0004,  # 币安期货单边手续费率（万四）
    
    # 🔥 V5.0 新增配置
    # WebSocket 实时流配置
    "WEBSOCKET_ENABLED": True,  # 是否启用 WebSocket 实时数据流
    "WEBSOCKET_SYMBOLS": [],  # WebSocket 监听的交易对列表（空列表=自动使用 ASSET_WEIGHTS）
    
    # Maker-first 执行算法配置
    "MAKER_FIRST_ENABLED": True,  # 是否启用 Maker-first 执行算法
    "MAKER_WAIT_SECONDS": 3,  # Post-Only 订单等待时间（秒）
    "MAKER_PRICE_DEVIATION_THR": 0.002,  # 价格偏离阈值（0.2%），超过则取消并转 Market Order
    "MAKER_FEE_RATE": 0.0002,  # Maker 手续费率（万二）
    "TAKER_FEE_RATE": 0.0004,  # Taker 手续费率（万四）
    
    # 市场状态分类器配置
    "MARKET_REGIME_ENABLED": True,  # 是否启用市场状态分类器
    "REGIME_CHECK_INTERVAL": 3600,  # 市场状态检测间隔（秒，默认1小时）
    "REGIME_ATR_LOOKBACK": 24,  # ATR 斜率计算回溯周期（小时）
    "REGIME_VOLATILITY_PERCENTILE": 90,  # 波动率熔断阈值（百分位数）
    "REGIME_VOLATILITY_LOOKBACK": 168,  # 波动率历史回溯周期（小时，7天）
    "REGIME_CIRCUIT_BREAKER_DURATION": 3600,  # 熔断持续时间（秒，1小时）
    
    # 🔥 Task 2 & 3: 代理与网络配置（从环境变量读取，端口已修正为 4780）
    "PROXY_ENABLED": os.getenv("PROXY_ENABLED", "false").lower() == "true",  # 是否启用代理
    "PROXY_HOST": os.getenv("PROXY_HOST", "127.0.0.1"),  # 代理主机地址
    "PROXY_PORT": int(os.getenv("PROXY_PORT", "4780")),  # 🔥 代理端口（已修正为 4780）
    "WS_RECONNECT_BASE_DELAY": 2,  # WebSocket 重连基础延迟（秒）
    "WS_RECONNECT_MAX_DELAY": 60,  # WebSocket 重连最大延迟（秒）
    "WS_PING_INTERVAL": 20,  # WebSocket 心跳间隔（秒）
    "WS_PING_TIMEOUT": 10,  # WebSocket 心跳超时（秒）
    "API_TIMEOUT": 20,  # API 请求超时（秒），与 NETWORK_CONFIG 保持一致
    
    # 🔥 AI 自动调参引擎物理开关（默认关闭，需手动开启）
    "AUTO_TUNE_ENABLED": False,  # AI 自动调参引擎总开关（1小时巡航）
    
    # 🔥 AI 满血接管测试模式（免审批直接执行）
    "AI_FULL_AUTONOMY_MODE": False,  # AI满血接管模式（免审批）
}

# ==========================================
# 🔥 AI 自适应巡航调参引擎 - 安全边界配置
# ==========================================
AUTO_TUNE_BOUNDARIES = {
    "ADX_THR": {"min": 5, "max": 25},
    "ATR_MULT": {"min": 1.2, "max": 3.5},
    "EMA_TREND": {"min": 20, "max": 200},
    "SIGNAL_CONFIRM_BARS": {"min": 0, "max": 3},
}

# 🔥 禁止 AI 调参的资金参数（硬编码保护）
AUTO_TUNE_FORBIDDEN_PARAMS = [
    "LEVERAGE",
    "RISK_RATIO",
    "BENCHMARK_CASH",
    "VAULT_THR",
    "WITHDRAW_RATIO",
    "MAD_DOG_BOOST",
    "MAD_DOG_TRIGGER",
]

# 🔥 自动调参冷却期（秒）- 防止频繁调参
AUTO_TUNE_COOLDOWN = 7200  # 2小时

# 🔥 最近一次自动调参时间戳
LAST_AUTO_TUNE_TIME = 0

# ==========================================
# 网络请求配置（🔥 Task 4: 放宽超时硬顶 + 增强重试间隔）
# ==========================================
NETWORK_CONFIG = {
    "API_TIMEOUT": 20,  # 🔥 从 10 秒提升至 20 秒，应对网络波动
    "MAX_RETRIES": 3,
    "RETRY_DELAY": 5,  # 🔥 从 2 秒提升至 5 秒，确保指数退避生效
    "CIRCUIT_BREAKER_THRESHOLD": 5,
    "CIRCUIT_BREAKER_TIMEOUT": 60,
}

# ==========================================
# 报价哨所配置
# ==========================================
SENTRY_INTERVAL_OPTIONS = {
    "5m": {"seconds": 300, "name": "5分钟"},
    "15m": {"seconds": 900, "name": "15分钟"},
    "30m": {"seconds": 1800, "name": "30分钟"},
    "1h": {"seconds": 3600, "name": "1小时"},
    "4h": {"seconds": 14400, "name": "4小时"},
    "1d": {"seconds": 86400, "name": "1天"}
}

SENTRY_CONFIG = {
    "ENABLED": True,
    "INTERVAL": 900,
    "INTERVAL_KEY": "15m",
    "WATCH_LIST": [],
    "LAST_REPORT_MSG_ID": None,
}

# ==========================================
# INTERVAL字符串到币安常量的映射
# ==========================================
INTERVAL_MAPPING = {
    "1m": KLINE_INTERVAL_1MINUTE,
    "3m": KLINE_INTERVAL_3MINUTE,
    "5m": KLINE_INTERVAL_5MINUTE,
    "15m": KLINE_INTERVAL_15MINUTE,
    "30m": KLINE_INTERVAL_30MINUTE,
    "1h": KLINE_INTERVAL_1HOUR,
    "2h": KLINE_INTERVAL_2HOUR,
    "4h": KLINE_INTERVAL_4HOUR,
    "6h": KLINE_INTERVAL_6HOUR,
    "8h": KLINE_INTERVAL_8HOUR,
    "12h": KLINE_INTERVAL_12HOUR,
    "1d": KLINE_INTERVAL_1DAY,
    "3d": KLINE_INTERVAL_3DAY,
    "1w": KLINE_INTERVAL_1WEEK,
    "1M": KLINE_INTERVAL_1MONTH,
}

# ==========================================
# 启动向导映射（模式 -> 策略预设键）
# ==========================================
LAUNCH_MODE_MAP = {
    "CONSERVATIVE": {"label": "🛡️ 稳健 (1d)", "interval": "1d", "preset_key": "CONSERVATIVE"},
    "STANDARD": {"label": "⚖️ 温和 (4h)", "interval": "4h", "preset_key": "STANDARD"},
    "AGGRESSIVE": {"label": "🔥 激进 (15m)", "interval": "15m", "preset_key": "AGGRESSIVE"},
    "SCALPER": {"label": "⚡ 剥头皮 (1m)", "interval": "1m", "preset_key": "SCALPER"},
    "GOLD_PRO": {"label": "🏆 黄金专业 (15m)", "interval": "15m", "preset_key": "GOLD_PRO"},
}

# ==========================================
# 全局状态变量
# ==========================================
BOT_ACTIVE = True
TRADING_ENGINE_ACTIVE = False
VERIFICATION_MODE = False  # 验证模式开关（False=实盘模式，True=验证模式）
ACTIVE_POSITIONS = {}
TRADE_HISTORY = []

# 启动向导临时状态（内存态，不持久化）
LAUNCH_WIZARD_STATE = {}

# ==========================================
# 🔥 死信队列（Dead Letter Queue）- 裸奔敞口防御
# ==========================================
# 用于存储所有未能成功挂上止损单且回滚失败的持仓
# 结构: [{'symbol': 'BTCUSDT', 'position_type': 'LONG', 'qty': 0.001, 
#         'entry_price': 50000, 'failed_at': timestamp, 'retry_count': 0, 
#         'trade_id': 'xxx', 'error_reason': 'xxx'}]
DEAD_LETTER_QUEUE = []
DLQ_LOCK = threading.RLock()  # 死信队列专用锁

# 死信队列配置
DLQ_CONFIG = {
    "MAX_RETRY_COUNT": 10,           # 单笔订单最大重试次数
    "INITIAL_BACKOFF": 30,           # 初始退避时间（秒）
    "MAX_BACKOFF": 1800,             # 最大退避时间（30分钟）
    "ALERT_INTERVAL": 300,           # 警报间隔（5分钟）
    "LAST_ALERT_TIME": 0,            # 上次警报时间戳
}

# ==========================================
# 🔥 用户会话状态持久化（防止交互断档）
# ==========================================
# 用于存储用户当前的输入上下文，防止 VPS 重启或轮询中断导致状态丢失
# 结构: {chat_id: {'expected_input_type': 'ASSET_SEARCH', 'timestamp': xxx, 'context': {}}}
USER_SESSION_STATE = {}
SESSION_LOCK = threading.RLock()  # 会话状态专用锁

# 缓存变量
symbol_precisions = {}
price_precisions = {}
tick_sizes = {}
quantity_step_sizes = {}  # 新增：存储 LOT_SIZE 的 stepSize
price_history = {}
last_price_update = {}
all_symbols_cache = []
symbols_cache_time = None
sentry_price_cache = {}
SYMBOLS_CACHE_DURATION = 3600

# 机构级熔断器状态机
circuit_breaker_state = {
    "failures": 0,
    "last_failure_time": None,
    "state": "CLOSED",  # CLOSED, OPEN, HALF_OPEN
    "half_open_testing": False  # 标记是否正在进行试探请求
}

# ==========================================
# 配置验证函数
# ==========================================
def get_binance_interval(interval_str):
    """将配置中的INTERVAL字符串转换为币安API常量"""
    if interval_str in INTERVAL_MAPPING:
        return INTERVAL_MAPPING[interval_str]
    else:
        print(f"⚠️ 无效的INTERVAL配置: {interval_str}，使用默认值 1h")
        return KLINE_INTERVAL_1HOUR

def validate_config():
    """
    验证系统配置的完整性和有效性
    修复审计报告1.2和1.3：配置验证（共12项验证）
    """
    errors = []
    warnings = []

    # 1. 验证INTERVAL配置（修复审计报告1.2）
    interval = SYSTEM_CONFIG.get("INTERVAL", "1h")
    if interval not in INTERVAL_MAPPING:
        errors.append(f"❌ INTERVAL配置无效: '{interval}'，必须是以下之一: {list(INTERVAL_MAPPING.keys())}")
    else:
        print(f"✅ INTERVAL配置有效: {interval} -> {INTERVAL_MAPPING[interval]}")

    # 2. 验证LEVERAGE范围（修复审计报告1.3）
    leverage = SYSTEM_CONFIG.get("LEVERAGE", 20)
    if not (1 <= leverage <= 125):
        errors.append(f"❌ LEVERAGE超出范围: {leverage}，必须在1-125之间")
    elif leverage > 75:
        warnings.append(f"⚠️ LEVERAGE设置较高: {leverage}x，请注意风险")

    # 3. 验证RISK_RATIO范围（修复审计报告1.3）
    risk_ratio = SYSTEM_CONFIG.get("RISK_RATIO", 0.055)
    if not (0.001 <= risk_ratio <= 0.5):
        errors.append(f"❌ RISK_RATIO超出合理范围: {risk_ratio}，建议在0.001-0.5之间")
    elif risk_ratio > 0.2:
        warnings.append(f"⚠️ RISK_RATIO设置较高: {risk_ratio*100}%，单笔风险较大")

    # 4. 验证ATR_MULT范围（修复审计报告1.3）
    atr_mult = SYSTEM_CONFIG.get("ATR_MULT", 2.3)
    if atr_mult <= 0:
        errors.append(f"❌ ATR_MULT必须大于0，当前值: {atr_mult}")
    elif atr_mult < 1.0:
        warnings.append(f"⚠️ ATR_MULT设置较小: {atr_mult}，止损可能过紧")

    # 5. 验证ATR_PERIOD范围
    atr_period = SYSTEM_CONFIG.get("ATR_PERIOD", 14)
    if not (5 <= atr_period <= 50):
        errors.append(f"❌ ATR_PERIOD超出合理范围: {atr_period}，建议在5-50之间")

    # 6. 验证BENCHMARK_CASH
    benchmark_cash = SYSTEM_CONFIG.get("BENCHMARK_CASH", 1800.0)
    if benchmark_cash <= 0:
        errors.append(f"❌ BENCHMARK_CASH必须大于0，当前值: {benchmark_cash}")

    # 7. 验证自适应保险库参数（v2.0 替代旧 VAULT_THR）
    vault_base = SYSTEM_CONFIG.get("VAULT_BASE_RATIO", 0.15)
    vault_min = SYSTEM_CONFIG.get("VAULT_MIN_RATIO", 0.05)
    vault_max = SYSTEM_CONFIG.get("VAULT_MAX_RATIO", 0.30)
    if not (0 < vault_min <= vault_base <= vault_max <= 1.0):
        errors.append(f"❌ 保险库比例链断裂: MIN({vault_min}) <= BASE({vault_base}) <= MAX({vault_max}) 必须成立，且均在 0~1 之间")
    else:
        print(f"✅ 自适应保险库比例链: MIN={vault_min:.0%} <= BASE={vault_base:.0%} <= MAX={vault_max:.0%}")
    
    # 7b. 验证 FORCE_MAD_DOG_MODE 安全警告
    if SYSTEM_CONFIG.get("FORCE_MAD_DOG_MODE", False):
        warnings.append(f"⚠️ 强制疯狗模式已开启！将无视盈利条件强行激活 {SYSTEM_CONFIG.get('MAD_DOG_BOOST', 2.0)}x 配资，风险极高！")
    
    # 7c. 验证 SPACE_LOCK_ENABLED 状态提示
    if not SYSTEM_CONFIG.get("SPACE_LOCK_ENABLED", True):
        warnings.append("⚠️ 空间锁已关闭（狂战士高频模式），波动率拦截功能禁用，追涨杀跌风险增加")

    # 8. 验证WITHDRAW_RATIO
    withdraw_ratio = SYSTEM_CONFIG.get("WITHDRAW_RATIO", 0.5)
    if not (0 < withdraw_ratio <= 1.0):
        errors.append(f"❌ WITHDRAW_RATIO必须在0-1之间，当前值: {withdraw_ratio}")

    # 9. 验证MAD_DOG_TRIGGER
    mad_dog_trigger = SYSTEM_CONFIG.get("MAD_DOG_TRIGGER", 1.3)
    if mad_dog_trigger < 1.0:
        errors.append(f"❌ MAD_DOG_TRIGGER必须>=1.0，当前值: {mad_dog_trigger}")

    # 10. 验证MAD_DOG_BOOST
    mad_dog_boost = SYSTEM_CONFIG.get("MAD_DOG_BOOST", 2.0)
    if mad_dog_boost < 1.0:
        errors.append(f"❌ MAD_DOG_BOOST必须>=1.0，当前值: {mad_dog_boost}")
    elif mad_dog_boost > 5.0:
        warnings.append(f"⚠️ MAD_DOG_BOOST设置过高: {mad_dog_boost}x，风险极大")

    # 11. 验证API密钥（修复审计报告1.4）
    api_key = SYSTEM_CONFIG.get("API_KEY", "")
    api_secret = SYSTEM_CONFIG.get("API_SECRET", "")
    if not api_key or not api_secret:
        errors.append("❌ API密钥未配置，系统无法启动")

    # 12. 验证TG配置
    tg_token = SYSTEM_CONFIG.get("TG_TOKEN", "")
    tg_chat_id = SYSTEM_CONFIG.get("TG_CHAT_ID", "")
    if not tg_token or not tg_chat_id:
        warnings.append("⚠️ Telegram配置不完整，消息推送功能将受限")

    # 输出验证结果
    if errors:
        print("\n" + "=" * 60)
        print("❌ 配置验证失败，发现以下错误:")
        for error in errors:
            print(f"   {error}")
        print("=" * 60 + "\n")
        return False, errors

    if warnings:
        print("\n" + "=" * 60)
        print("⚠️ 配置验证通过，但有以下警告:")
        for warning in warnings:
            print(f"   {warning}")
        print("=" * 60 + "\n")
    else:
        print("\n✅ 配置验证通过，所有参数均在合理范围内\n")

    return True, []

# ==========================================
# 策略参数键集合（用于自动检测自定义模式）
# 当用户修改这些参数时，自动切换到自定义模式
# ==========================================
STRATEGY_PARAM_KEYS = {
    "ADX_THR", "EMA_TREND", "ATR_MULT", "INTERVAL",
    "MACD_FAST", "MACD_SLOW", "MACD_SIGNAL",
    "RSI_PERIOD", "RSI_OVERBOUGHT", "RSI_OVERSOLD",
    "ATR_PERIOD", "SL_BUFFER",
}

def mark_custom_mode(param_name):
    """
    当用户手动修改策略相关参数时，自动切换到自定义模式。
    切断与预设模式的强绑定，防止用户混淆。
    
    Args:
        param_name: 被修改的参数名
    """
    if param_name in STRATEGY_PARAM_KEYS:
        with state_lock:
            if not SYSTEM_CONFIG.get("IS_CUSTOM_MODE", False):
                old_mode = SYSTEM_CONFIG.get("STRATEGY_MODE", "STANDARD")
                SYSTEM_CONFIG["IS_CUSTOM_MODE"] = True
                print(f"🛠️ 参数 {param_name} 被手动修改，已从 [{old_mode}] 切换到自定义模式")

def apply_strategy_preset(mode_key):
    """
    应用策略预设模式，覆盖所有相关参数。
    将 IS_CUSTOM_MODE 设为 False，恢复与预设的强绑定。
    
    Args:
        mode_key: 预设模式键名 (CONSERVATIVE/STANDARD/AGGRESSIVE/SCALPER)
    
    Returns:
        bool: 是否成功应用
    """
    if mode_key not in STRATEGY_PRESETS:
        return False
    
    preset = STRATEGY_PRESETS[mode_key]
    
    with state_lock:
        SYSTEM_CONFIG["STRATEGY_MODE"] = mode_key
        SYSTEM_CONFIG["IS_CUSTOM_MODE"] = False
        
        # 覆盖所有预设中定义的参数到 SYSTEM_CONFIG
        skip_keys = {"name", "emoji", "description"}
        for k, v in preset.items():
            if k not in skip_keys:
                SYSTEM_CONFIG[k] = v
        
        # 🔥 修复未来函数陷阱：CONSERVATIVE 和 STANDARD 强制使用已收盘K线
        if mode_key in ("CONSERVATIVE", "STANDARD"):
            SYSTEM_CONFIG["USE_LATEST_CANDLE"] = False
        
        # 🔥 差异化安全杠杆：记录杠杆自动同步
        leverage = preset.get("LEVERAGE", SYSTEM_CONFIG.get("LEVERAGE", 20.0))
        print(f"🎯 已应用策略预设: {preset['name']} (IS_CUSTOM_MODE=False)")
        print(f"⚡ 策略切换：杠杆已自动同步为 {leverage}x")
        
        save_data()
    
    return True

def get_custom_mode_diff():
    """
    获取当前自定义模式下与基准预设的参数差异。
    用于 UI 展示哪些参数被用户手动修改过。
    
    Returns:
        list[dict]: 差异列表 [{"param": "ADX_THR", "current": 15, "preset": 12}, ...]
    """
    base_mode = SYSTEM_CONFIG.get("STRATEGY_MODE", "STANDARD")
    preset = STRATEGY_PRESETS.get(base_mode, STRATEGY_PRESETS["STANDARD"])
    
    diffs = []
    skip_keys = {"name", "emoji", "description"}
    for k, v in preset.items():
        if k in skip_keys:
            continue
        current_val = SYSTEM_CONFIG.get(k)
        if current_val is not None and current_val != v:
            diffs.append({"param": k, "current": current_val, "preset": v})
    return diffs

# ==========================================
# 🔥 数据多级备份辅助函数
# ==========================================

def _parse_positions(positions):
    """解析持仓数据（从 JSON dict 还原到 ACTIVE_POSITIONS）"""
    global ACTIVE_POSITIONS
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
        
        ACTIVE_POSITIONS[sym] = pos_data


def _send_backup_recovery_alert(original_file, error_msg):
    """
    发送备份恢复通知到 Telegram（延迟导入避免循环依赖）
    
    Args:
        original_file: 原始损坏的文件名
        error_msg: 原始错误信息
    """
    try:
        # 延迟导入，避免与 utils.py 的循环依赖（config.py 在 utils.py 之前加载）
        def _deferred_alert():
            try:
                from utils import send_tg_alert
                send_tg_alert(
                    f"🚨 <b>[数据文件损坏 - 已自动恢复]</b>\n\n"
                    f"📄 损坏文件: <code>{original_file}</code>\n"
                    f"❌ 错误原因: <code>{error_msg[:200]}</code>\n"
                    f"✅ 已从 <code>{original_file}.bak</code> 恢复\n\n"
                    f"⚠️ 请检查数据完整性！如有异常请手动核查。"
                )
            except Exception:
                pass  # 启动阶段 utils 可能还未加载，静默忽略
        
        # 使用线程延迟执行，确保 utils 模块已经加载完毕
        import threading
        t = threading.Timer(5.0, _deferred_alert)
        t.daemon = True
        t.start()
    except Exception:
        pass


def _create_backup(filepath):
    """
    在覆盖前将现有文件存为 .bak 备份
    
    Args:
        filepath: 要备份的文件路径
    """
    if os.path.exists(filepath):
        backup_path = f"{filepath}.bak"
        try:
            import shutil
            shutil.copy2(filepath, backup_path)
        except Exception as e:
            print(f"⚠️ 创建备份失败 {filepath} -> {backup_path}: {e}")


# ==========================================
# 配置加载和保存
# ==========================================
def load_data():
    """加载持久化数据（🔥 增强：自动回退到 .bak 备份）"""
    global SYSTEM_CONFIG, ACTIVE_POSITIONS, TRADE_HISTORY, SENTRY_CONFIG
    
    # 加载配置（带备份回退）
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                saved_config = json.load(f)
                for k, v in saved_config.items():
                    if k not in ["API_KEY", "API_SECRET", "TG_TOKEN", "TG_CHAT_ID"]:
                        # 🔥 确保 PROXY_PORT 始终为整数类型（防止 requests 报错）
                        if k == "PROXY_PORT" and v is not None:
                            SYSTEM_CONFIG[k] = int(v)
                        else:
                            SYSTEM_CONFIG[k] = v
            print("✅ 成功加载持久化配置")
        except Exception as e:
            print(f"⚠️ 加载配置文件失败: {e}")
            # 🔥 尝试从备份文件恢复
            backup_file = f"{CONFIG_FILE}.bak"
            if os.path.exists(backup_file):
                try:
                    with open(backup_file, 'r', encoding='utf-8') as f:
                        saved_config = json.load(f)
                        for k, v in saved_config.items():
                            if k not in ["API_KEY", "API_SECRET", "TG_TOKEN", "TG_CHAT_ID"]:
                                SYSTEM_CONFIG[k] = v
                    print(f"✅ 已从备份文件恢复配置: {backup_file}")
                    _send_backup_recovery_alert(CONFIG_FILE, str(e))
                except Exception as backup_err:
                    print(f"❌ 备份文件也损坏: {backup_err}")
    
    # 加载持仓（🔥 防御性处理：确保列表结构 + 备份回退）
    positions_loaded = False
    if os.path.exists(POSITIONS_FILE):
        try:
            with open(POSITIONS_FILE, 'r', encoding='utf-8') as f:
                positions = json.load(f)
                _parse_positions(positions)
            positions_loaded = True
            total_trades = sum(len(v) if isinstance(v, list) else 1 for v in ACTIVE_POSITIONS.values())
            print(f"✅ 成功加载 {len(ACTIVE_POSITIONS)} 个币种方向，共 {total_trades} 笔子订单")
        except Exception as e:
            print(f"⚠️ 加载持仓文件失败: {e}")
            # 🔥 尝试从备份恢复
            backup_file = f"{POSITIONS_FILE}.bak"
            if os.path.exists(backup_file):
                try:
                    with open(backup_file, 'r', encoding='utf-8') as f:
                        positions = json.load(f)
                        _parse_positions(positions)
                    positions_loaded = True
                    print(f"✅ 已从备份文件恢复持仓: {backup_file}")
                    _send_backup_recovery_alert(POSITIONS_FILE, str(e))
                except Exception as backup_err:
                    print(f"❌ 持仓备份文件也损坏: {backup_err}")
    
    # 加载交易历史（带备份回退）
    if os.path.exists(TRADE_HISTORY_FILE):
        try:
            with open(TRADE_HISTORY_FILE, 'r', encoding='utf-8') as f:
                TRADE_HISTORY = json.load(f)
            print(f"✅ 成功加载 {len(TRADE_HISTORY)} 条交易历史记录")
        except Exception as e:
            print(f"⚠️ 加载交易历史失败: {e}")
            # 🔥 尝试从备份恢复
            backup_file = f"{TRADE_HISTORY_FILE}.bak"
            if os.path.exists(backup_file):
                try:
                    with open(backup_file, 'r', encoding='utf-8') as f:
                        TRADE_HISTORY = json.load(f)
                    print(f"✅ 已从备份文件恢复交易历史: {backup_file}")
                    _send_backup_recovery_alert(TRADE_HISTORY_FILE, str(e))
                except Exception as backup_err:
                    print(f"❌ 交易历史备份文件也损坏: {backup_err}")
    
    # 加载哨所配置
    if os.path.exists(SENTRY_FILE):
        try:
            with open(SENTRY_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                SENTRY_CONFIG["WATCH_LIST"] = data.get("WATCH_LIST", [])
                SENTRY_CONFIG["ENABLED"] = data.get("ENABLED", True)
            print(f"✅ 成功加载哨所监控列表: {len(SENTRY_CONFIG['WATCH_LIST'])} 个币种")
        except Exception as e:
            print(f"⚠️ 加载哨所监控列表失败: {e}")
    
    # 🔥 加载用户会话状态（防止交互断档）
    if os.path.exists(SESSION_FILE):
        try:
            with open(SESSION_FILE, 'r', encoding='utf-8') as f:
                sessions = json.load(f)
                with SESSION_LOCK:
                    # 清理过期会话（超过24小时）
                    current_time = time.time()
                    for chat_id, session_data in sessions.items():
                        timestamp = session_data.get('timestamp', 0)
                        if current_time - timestamp < 86400:  # 24小时内有效
                            USER_SESSION_STATE[int(chat_id)] = session_data
            print(f"✅ 成功加载 {len(USER_SESSION_STATE)} 个用户会话状态")
        except Exception as e:
            print(f"⚠️ 加载用户会话状态失败: {e}")
    
    # 🔥 加载死信队列（DLQ 持久化恢复）
    global DEAD_LETTER_QUEUE
    if os.path.exists(DLQ_FILE):
        try:
            with open(DLQ_FILE, 'r', encoding='utf-8') as f:
                DEAD_LETTER_QUEUE = json.load(f)
            print(f"✅ 成功加载死信队列: {len(DEAD_LETTER_QUEUE)} 条待处理记录")
        except Exception as e:
            print(f"⚠️ 加载死信队列失败: {e}")

def save_data():
    """
    原子性保存配置和持仓数据到JSON文件
    使用临时文件+原子替换，防止写入过程中崩溃导致数据损坏
    🔥 修复多线程竞态：使用 state_lock 保护全局状态，deepcopy 后释放锁再执行 I/O
    🔥 数据多级备份：覆盖前自动创建 .bak 备份，防止写入失败导致数据丢失
    """
    try:
        # 🔒 Step 1: 在锁内快速 deepcopy 所有需要持久化的数据
        with state_lock:
            config_snapshot = {k: v for k, v in SYSTEM_CONFIG.items() 
                             if k not in ["API_KEY", "API_SECRET", "TG_TOKEN", "TG_CHAT_ID"]}
            config_snapshot = copy.deepcopy(config_snapshot)
            
            positions_snapshot = copy.deepcopy(ACTIVE_POSITIONS)
            history_snapshot = copy.deepcopy(TRADE_HISTORY)
        
        # 🔓 Step 2: 释放锁后执行耗时的 I/O 操作（避免阻塞其他线程）
        
        # 🔥 Step 2.1: 在覆盖前创建 .bak 备份
        _create_backup(CONFIG_FILE)
        _create_backup(POSITIONS_FILE)
        _create_backup(TRADE_HISTORY_FILE)
        
        # 保存配置文件（原子写入）
        _atomic_save_json(CONFIG_FILE, config_snapshot)
        
        # 保存持仓文件（支持列表序列化 + 原子写入）
        positions_to_save = {}
        for sym, pos_data in positions_snapshot.items():
            if isinstance(pos_data, list):
                positions_list = []
                for pos in pos_data:
                    pos_copy = pos.copy()
                    if 'timestamp' in pos_copy and isinstance(pos_copy['timestamp'], datetime):
                        pos_copy['timestamp'] = pos_copy['timestamp'].isoformat()
                    positions_list.append(pos_copy)
                positions_to_save[sym] = positions_list
            else:
                pos_copy = pos_data.copy()
                if 'timestamp' in pos_copy and isinstance(pos_copy['timestamp'], datetime):
                    pos_copy['timestamp'] = pos_copy['timestamp'].isoformat()
                positions_to_save[sym] = pos_copy
        
        _atomic_save_json(POSITIONS_FILE, positions_to_save)
        
        # 保存交易历史文件（原子写入）
        _atomic_save_json(TRADE_HISTORY_FILE, history_snapshot)
        
        print("✅ 数据已安全保存（原子写入）")
        
    except Exception as e:
        print(f"❌ 保存数据失败: {e}")
        # 发送告警
        try:
            from utils import send_tg_msg
            send_tg_msg(f"⚠️ 数据保存失败: {e}")
        except:
            pass


def _atomic_save_json(filepath, data):
    """
    原子性保存JSON文件
    
    Args:
        filepath: 目标文件路径
        data: 要保存的数据（字典或列表）
    
    原理:
        1. 写入临时文件 filepath.tmp
        2. 使用 os.replace() 原子性替换原文件
        3. os.replace() 在所有平台都是原子操作（POSIX标准）
    """
    tmp_filepath = filepath + '.tmp'
    
    try:
        # 步骤1: 写入临时文件
        with open(tmp_filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False, default=str)
            f.flush()  # 强制刷新缓冲区到磁盘
            os.fsync(f.fileno())  # 确保数据真正写入磁盘（防止OS缓存）
        
        # 步骤2: 原子性替换原文件
        # os.replace() 是原子操作，即使在替换过程中崩溃，原文件也不会损坏
        os.replace(tmp_filepath, filepath)
        
    except Exception as e:
        # 清理临时文件
        if os.path.exists(tmp_filepath):
            try:
                os.remove(tmp_filepath)
            except:
                pass
        raise e

def load_sentry_watchlist():
    """加载哨所监控列表"""
    global SENTRY_CONFIG
    if os.path.exists(SENTRY_FILE):
        try:
            with open(SENTRY_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                SENTRY_CONFIG["WATCH_LIST"] = data.get("WATCH_LIST", [])
                SENTRY_CONFIG["ENABLED"] = data.get("ENABLED", True)
                interval_key = data.get("INTERVAL_KEY", "15m")
                SENTRY_CONFIG["INTERVAL_KEY"] = interval_key
                if interval_key in SENTRY_INTERVAL_OPTIONS:
                    SENTRY_CONFIG["INTERVAL"] = SENTRY_INTERVAL_OPTIONS[interval_key]["seconds"]
            print(f"✅ 成功加载哨所监控列表: {len(SENTRY_CONFIG['WATCH_LIST'])} 个币种")
        except Exception as e:
            print(f"⚠️ 加载哨所监控列表失败: {e}")

def save_sentry_watchlist():
    """保存哨所监控列表"""
    try:
        with open(SENTRY_FILE, 'w', encoding='utf-8') as f:
            json.dump({
                "WATCH_LIST": SENTRY_CONFIG["WATCH_LIST"],
                "ENABLED": SENTRY_CONFIG["ENABLED"],
                "INTERVAL_KEY": SENTRY_CONFIG.get("INTERVAL_KEY", "15m")
            }, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"⚠️ 保存哨所监控列表失败: {e}")


# ==========================================
# 🔥 用户会话状态管理函数
# ==========================================

def set_user_session(chat_id, expected_input_type, context=None):
    """
    设置用户会话状态（持久化到磁盘）
    
    Args:
        chat_id: Telegram 聊天 ID
        expected_input_type: 期望的输入类型 (如 'CUSTOM_INPUT', 'ASSET_SEARCH', 'SENTRY_ADD')
        context: 额外上下文信息 (如 {'param': 'ADX_THR', 'info': {...}})
    """
    with SESSION_LOCK:
        USER_SESSION_STATE[int(chat_id)] = {
            'expected_input_type': expected_input_type,
            'timestamp': time.time(),
            'context': context or {}
        }
    _save_session_state()


def get_user_session(chat_id):
    """
    获取用户会话状态
    
    Args:
        chat_id: Telegram 聊天 ID
    
    Returns:
        dict or None: 会话状态数据，如果不存在或已过期则返回 None
    """
    with SESSION_LOCK:
        session = USER_SESSION_STATE.get(int(chat_id))
        if session:
            # 检查是否过期（超过1小时视为过期）
            if time.time() - session.get('timestamp', 0) > 3600:
                USER_SESSION_STATE.pop(int(chat_id), None)
                return None
            return session
        return None


def clear_user_session(chat_id):
    """
    清除用户会话状态
    
    Args:
        chat_id: Telegram 聊天 ID
    """
    with SESSION_LOCK:
        USER_SESSION_STATE.pop(int(chat_id), None)
    _save_session_state()


def _save_session_state():
    """将用户会话状态持久化到磁盘"""
    try:
        with SESSION_LOCK:
            sessions_to_save = {str(k): v for k, v in USER_SESSION_STATE.items()}
        with open(SESSION_FILE, 'w', encoding='utf-8') as f:
            json.dump(sessions_to_save, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ 保存用户会话状态失败: {e}")


# ==========================================
# 🔥 死信队列持久化函数
# ==========================================

def save_dlq():
    """
    将死信队列持久化到磁盘（线程安全 + 原子性操作）
    用于保存所有未能成功挂上止损单且回滚失败的持仓记录
    """
    try:
        with DLQ_LOCK:
            dlq_snapshot = copy.deepcopy(DEAD_LETTER_QUEUE)
        
        with open(DLQ_FILE, 'w', encoding='utf-8') as f:
            json.dump(dlq_snapshot, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ 保存死信队列失败: {e}")


# 初始化时加载数据
load_data()

print("✅ 配置管理模块已加载")
