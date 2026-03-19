#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
无界指挥部 - 主程序入口
工业化重构版本 v7.0 - 纯指挥官模式（多进程解耦）
职责：系统初始化、后台线程管理、轮询控制
"""
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

# 🔥 Windows 物理套接字劫持补丁（必须在所有 import 之前）
import asyncio
import socket
import sys
import nest_asyncio

# 🔥 针对 Windows，设置 WindowsSelectorEventLoopPolicy
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    # 🔥 物理重写 socket.socketpair：解决代理软件（TUN模式）拦截 loopback 通信
    # 通过手动建立本地 TCP 服务器和客户端连接来代替系统原有的信号管道
    # 这是为了彻底解决 Unexpected peer connection 报错
    def _socketpair_fix(family=socket.AF_INET, type=socket.SOCK_STREAM, proto=0):
        """
        Windows socketpair 修复：手动创建本地 TCP 连接对
        避免代理软件拦截导致的 Unexpected peer connection 错误
        """
        # 创建临时服务器监听随机端口
        temp_srv = socket.socket(family, type, proto)
        temp_srv.bind(('127.0.0.1', 0))
        temp_srv.listen(1)
        
        # 获取服务器地址
        addr = temp_srv.getsockname()
        
        # 创建客户端连接
        client = socket.socket(family, type, proto)
        client.setblocking(False)
        try:
            client.connect(addr)
        except BlockingIOError:
            pass
        
        # 接受连接
        server, _ = temp_srv.accept()
        client.setblocking(True)
        temp_srv.close()
        
        return server, client
    
    # 🔥 注入修复：替换系统 socketpair
    socket.socketpair = _socketpair_fix

# 🔥 激活嵌套补丁：允许在已有事件循环中运行 asyncio.run()
nest_asyncio.apply()
import os

# ==========================================
# 🛑 物理阉割全局“幽灵代理” 
# 强制屏蔽 Windows 系统级别的代理环境变量，防止 requests 库被误导
# ==========================================
for key in ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy', 'ALL_PROXY', 'all_proxy']:
    if key in os.environ:
        del os.environ[key]

import multiprocessing as mp
import time
import signal
import threading
import subprocess
from datetime import datetime
from binance.client import Client
import telebot
from web_api import init_web_dashboard
from telebot import TeleBot
import config
from utils import send_tg_msg
import config

def push_start_menu_on_launch():
    """
    🚀 系统启动时主动推送主菜单，无需用户手动输入 /start
    """
    startup_msg = (
        "🤖 <b>WJ-BOT (X-VOID) 系统已上线！</b>\n\n"
        "📊 <b>当前模式</b>: <code>{mode}</code>\n"
        "💰 <b>沙盒余额</b>: <code>${balance:.2f}</code>\n"
        "🛡️ <b>风控状态</b>: 🟢 运行中\n\n"
        "请点击下方按钮或直接操作菜单："
    ).format(
        mode=config.SYSTEM_CONFIG.get("STRATEGY_MODE", "STANDARD"),
        balance=config.SYSTEM_CONFIG.get("SIM_CURRENT_BALANCE", 0)
    )

from config import USE_PROXY_HARD_SWITCH, SYSTEM_CONFIG

# ==========================================
# 🌍 Phase 1: 全球代理硬开关执行层（单一真相源）
# ==========================================
# 🔥 环境变量物理清除：使用 os.environ.pop() 彻底移除代理配置
USE_PROXY = USE_PROXY_HARD_SWITCH 

if not USE_PROXY:
    # 🔥 Phase 1: 强制清除所有代理环境变量（防止 urllib3/requests 自动抓取）
    # 清除小写变量（Linux/macOS 标准）
    os.environ.pop('http_proxy', None)
    os.environ.pop('https_proxy', None)
    os.environ.pop('all_proxy', None)
    # 清除大写变量（Windows 标准）
    os.environ.pop('HTTP_PROXY', None)
    os.environ.pop('HTTPS_PROXY', None)
    os.environ.pop('ALL_PROXY', None)
    proxies = None
    print("Phase 1: 直连模式 - 代理环境变量已物理清除")
else:
    PROXY_URL = "http://127.0.0.1:4780"
    os.environ['http_proxy'] = PROXY_URL
    os.environ['https_proxy'] = PROXY_URL
    proxies = {'http': PROXY_URL, 'https': PROXY_URL}
    print("🇨🇳 Phase 1: 国内代理模式 - 已挂载 4780 代理")
# ==========================================

# 可选依赖：进程管理（用于检测残留进程）
try:
    import psutil  # type: ignore
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

# 导入日志系统
from logger_setup import logger

# 🔥 日志记录代理状态
if not USE_PROXY:
    logger.info("直连：物理开关已生效，代理环境变量已排空")
else:
    logger.info("🌐 国内模式：已挂载 4780 代理")

# 导入配置和工具
from config import (
    SYSTEM_CONFIG, validate_config, save_data, 
    load_sentry_watchlist
)
import config

# 导入工具函数
from utils import (
    set_bot_instance, get_all_valid_symbols, 
    normalize_weights, shutdown_message_pool
)

# 导入交易引擎
from trading_engine import trading_engine_loop, sync_benchmark_with_api

# 导入监控系统
from monitors import (
    monitor_stop_loss_orders, monitor_account_drawdown,
    monitor_daily_performance, price_sentry_engine, monitor_scalper_positions,
    daily_ai_report_engine, market_regime_detector
)

# 🔥 V5.0 导入 WebSocket 管理器
from websocket_manager import get_websocket_manager, set_input_queue

# 导入命令处理器（仅注册函数）
from bot_handlers import register_handlers

# ==========================================
# 全局变量
# ==========================================
client = None
bot = None
# 🔥 V7.0 多进程全局变量
_input_queue = None
_output_queue = None
_worker_process = None


def validate_environment():
    """
    环境自检与配置验证
    
    Returns:
        bool: 验证是否通过
    """
    logger.info("📋 正在验证系统配置...")
    
    is_valid, errors = validate_config()
    if not is_valid:
        logger.error("❌ 配置验证失败，请修正以下错误后重试:")
        for error in errors:
            logger.error(f"   {error}")
        return False
    
    logger.info("✅ 配置验证通过")
    return True


def check_live_mode_warning():
    """
    实盘模式安全警报
    如果处于实盘模式（DRY_RUN=False 且 VERIFICATION_MODE=False），
    发出醒目的红色警报
    """
    if not SYSTEM_CONFIG.get("DRY_RUN", False) and not config.VERIFICATION_MODE:
        warning_msg = "🔥 警报：当前正处于【实盘模式】！系统将消耗真实资金，请确保网络和风控配置正确！"
        print("\n" + "=" * 60)
        print(warning_msg)
        print("=" * 60 + "\n")
        logger.warning(warning_msg)
        
        # 通过Telegram发送实盘警报
        chat_id = SYSTEM_CONFIG.get("TG_CHAT_ID", "")
        if chat_id and bot:
            try:
                alert_msg = "🔥🔥🔥 <b>【实盘模式警报】</b> 🔥🔥🔥\n\n"
                alert_msg += "⚠️ 系统正在以<b>实盘模式</b>启动！\n"
                alert_msg += "💰 将消耗<b>真实资金</b>进行交易\n"
                alert_msg += "🌐 请确保网络稳定\n"
                alert_msg += "🛡️ 请确认风控配置正确\n\n"
                alert_msg += f"📊 基准本金: ${SYSTEM_CONFIG['BENCHMARK_CASH']:.2f}\n"
                alert_msg += f"⚡ 杠杆倍数: {SYSTEM_CONFIG.get('LEVERAGE', 20)}x\n"
                alert_msg += f"📈 风险系数: {SYSTEM_CONFIG.get('RISK_RATIO', 0)*100:.1f}%"
                bot.send_message(chat_id, alert_msg, parse_mode="HTML")
            except Exception as e:
                logger.warning(f"⚠️ 发送实盘警报失败: {e}")


def initialize_binance_client():
    """
    让 API 客户端始终保持连接，并修复代理引起的 NoneType 报错
    """
    api_key = SYSTEM_CONFIG.get("API_KEY", "")
    api_secret = SYSTEM_CONFIG.get("API_SECRET", "")
    
    # 调试日志
    logger.info(f"🔍 [配置自检] API_KEY 长度: {len(api_key)}")
    
    try:
        logger.info("🔗 正在尝试连接币安API...")
        
        # 1. 动态构造请求参数，绝不传递 {'proxies': None}
        req_params = {'timeout': 15}
        if USE_PROXY_HARD_SWITCH:
            req_params['proxies'] = {
                'http': 'http://127.0.0.1:4780', 
                'https': 'http://127.0.0.1:4780'
            }
        
        # 2. 创建客户端实例
        client_inst = Client(
            api_key=api_key if api_key else None, 
            api_secret=api_secret if api_secret else None, 
            requests_params=req_params  # 👈 使用动态构造的参数
        )
        
        # 3. 尝试握手，失败也返回实例
        try:
            client_inst.ping()
            logger.info("✅ 币安API连接成功")
        except Exception as ping_e:
            logger.warning(f"⚠️ API握手失败 (但不影响沙盒启动): {ping_e}")
            
        return client_inst
        
    except Exception as e:
        # 强制转换报错信息，防止看到空的提示
        logger.error(f"❌ 客户端初始化崩溃: {repr(e)}")
        # 终极保命：返回一个空壳实例，确保 main() 函数不崩溃
        return Client(api_key="none", api_secret="none")


def initialize_telegram_bot():
    """
    初始化Telegram Bot（🔥 根据硬开关决定是否使用代理）
    
    Returns:
        TeleBot: Telegram Bot实例
    
    Raises:
        SystemExit: 如果初始化失败
    """
    tg_token = SYSTEM_CONFIG.get("TG_TOKEN", "")
    if not tg_token:
        logger.error("❌ TG_TOKEN未配置")
        sys.exit(1)
    
    try:
        logger.info("🤖 正在初始化Telegram Bot...")
        bot_inst = TeleBot(tg_token, parse_mode="HTML")
        
        # 🔥 根据硬开关决定是否配置代理
        from telebot import apihelper
        if USE_PROXY_HARD_SWITCH:
            proxy_url = "http://127.0.0.1:4780"
            apihelper.proxy = {'http': proxy_url, 'https': proxy_url}
            logger.info(f"✅ Telegram 代理已配置: {proxy_url}")
        else:
            apihelper.proxy = None
            logger.info("ℹ️ Telegram 使用直连模式")
        
        set_bot_instance(bot_inst)
        logger.info("✅ Telegram Bot初始化成功")
        return bot_inst
    except Exception as e:
        logger.error(f"❌ Telegram Bot初始化失败: {e}")
        sys.exit(1)


def check_all_connectivity():
    """
    🔥 网络连通性检查：探测 NewsAPI、CryptoPanic、Exa、Tavily 四个接口
    根据 USE_PROXY 开关决定是否使用代理
    检查完成后，将结果汇总为 HTML 格式并通过 Telegram 发送
    
    Returns:
        bool: 所有连接是否正常
    """
    import requests
    from utils import send_tg_msg
    
    logger.info("🔍 开始 API 连通性检查...")
    
    # 🔥 配置代理（严格遵循硬开关）
    proxies_cfg = {
        "http": "http://127.0.0.1:4780",
        "https": "http://127.0.0.1:4780"
    } if USE_PROXY_HARD_SWITCH else {} 
    # 获取 API Keys
    newsapi_key = SYSTEM_CONFIG.get("NEWS_API_KEY", "")
    cryptopanic_key = SYSTEM_CONFIG.get("CRYPTOPANIC_API_KEY", "")
    exa_key = SYSTEM_CONFIG.get("EXA_API_KEY", "")
    tavily_key = SYSTEM_CONFIG.get("TAVILY_API_KEY", "")
    
    # 存储检测结果
    results = {}
    
    # 1. 检测 NewsAPI
    logger.info("检测 NewsAPI...")
    try:
        if newsapi_key:
            url = f"https://newsapi.org/v2/top-headlines?country=us&apiKey={newsapi_key}&pageSize=1"
            response = requests.get(url, proxies=proxies_cfg, timeout=10)
            results["NewsAPI"] = "✅" if response.status_code == 200 else "❌"
        else:
            results["NewsAPI"] = "❌"
    except Exception as e:
        results["NewsAPI"] = "❌"
        logger.error(f"❌ NewsAPI 连接失败: {e}")
    
    # 2. 检测 CryptoPanic
    logger.info("检测 CryptoPanic...")
    try:
        if cryptopanic_key:
            url = f"https://cryptopanic.com/api/v1/posts/?auth_token={cryptopanic_key}&public=true"
            response = requests.get(url, proxies=proxies_cfg, timeout=10)
            results["CryptoPanic"] = "✅" if response.status_code == 200 else "❌"
        else:
            results["CryptoPanic"] = "❌"
    except Exception as e:
        results["CryptoPanic"] = "❌"
        logger.error(f"❌ CryptoPanic 连接失败: {e}")
    
    # 3. 检测 Exa (Neural Search)
    logger.info("检测 Exa...")
    try:
        if exa_key:
            url = "https://api.exa.ai/search"
            headers = {"Content-Type": "application/json", "x-api-key": exa_key}
            payload = {"query": "test", "numResults": 1}
            response = requests.post(url, json=payload, headers=headers, proxies=proxies_cfg, timeout=10)
            results["Exa"] = "✅" if response.status_code in [200, 201] else "❌"
        else:
            results["Exa"] = "❌"
    except Exception as e:
        results["Exa"] = "❌"
        logger.error(f"❌ Exa 连接失败: {e}")
    
    # 4. 检测 Tavily
    logger.info("检测 Tavily...")
    try:
        if tavily_key:
            url = "https://api.tavily.com/search"
            headers = {"Content-Type": "application/json"}
            payload = {"api_key": tavily_key, "query": "test", "max_results": 1}
            response = requests.post(url, json=payload, headers=headers, proxies=proxies_cfg, timeout=10)
            results["Tavily"] = "✅" if response.status_code in [200, 201] else "❌"
        else:
            results["Tavily"] = "❌"
    except Exception as e:
        results["Tavily"] = "❌"
        logger.error(f"❌ Tavily 连接失败: {e}")
    
    # 生成 HTML 汇总报告
    html_report = "🔍 <b>API 连通性检查报告</b>\n"
    html_report += "━━━━━━━━━━━━━━━━━━━━\n\n"
    html_report += f"📰 NewsAPI: {results.get('NewsAPI', '❌')}\n"
    html_report += f"📊 CryptoPanic: {results.get('CryptoPanic', '❌')}\n"
    html_report += f"🧠 Exa: {results.get('Exa', '❌')}\n"
    html_report += f"🔷 Tavily: {results.get('Tavily', '❌')}\n\n"
    
    total = len(results)
    passed = sum(1 for v in results.values() if v == "✅")
    html_report += f"✅ 通过: {passed}/{total}\n"
    if USE_PROXY_HARD_SWITCH:
        html_report += f"🌐 代理: http://127.0.0.1:4780"
    
    try:
        send_tg_msg(html_report)
    except Exception as e:
        logger.warning(f"⚠️ 发送报告失败: {e}")
    
    return passed == total


def initialize_resources(client_inst):
    """
    初始化系统资源
    
    Args:
        client_inst: 币安客户端实例
    """
    logger.info("📦 正在初始化系统资源...")
    
    # 加载哨所监控列表
    load_sentry_watchlist()
    
    # 提前获取交易对和精度信息
    get_all_valid_symbols(client_inst)
    
    # 归一化权重
    normalize_weights(client_inst)
    
    logger.info("✅ 系统资源初始化完成")


def _prefill_indicator_worker(client_inst, symbols, input_queue):
    """
    🔥 预灌历史 K 线到 input_queue
    加入防卡死金钟罩：单个币种失败绝不影响全局启动
    """
    if client_inst is None:
        logger.warning("⚠️ 无API连接，跳过历史K线预灌")
        return
    
    from trading_engine import get_historical_klines
    interval = SYSTEM_CONFIG.get("INTERVAL", "15m")
    
    for symbol in symbols:
        try:
            df = get_historical_klines(client_inst, symbol, interval, limit=800)
            if df is not None and len(df) > 0:
                ohlcv_list = []
                for _, row in df.iterrows():
                    ohlcv_list.append({
                        'timestamp': row['timestamp'],
                        'open': float(row['open']),
                        'high': float(row['high']),
                        'low': float(row['low']),
                        'close': float(row['close']),
                        'volume': float(row['volume'])
                    })
                
                try:
                    # 🔥 缩短超时时间到 3 秒，防止队列满导致主线程卡死
                    input_queue.put({
                        'type': 'prefill',
                        'symbol': symbol,
                        'ohlcv_list': ohlcv_list
                    }, block=True, timeout=3)
                    logger.info(f"📊 {symbol} 历史K线预灌完成 ({len(ohlcv_list)} 根)")
                except Exception as q_e:
                    logger.warning(f"⚠️ {symbol} 预灌队列阻塞或超时: {q_e}")
            else:
                logger.warning(f"⚠️ {symbol} 历史K线获取为空，跳过预灌")
        except Exception as e:
            # 异常隔离：单个币种报错，直接 continue 处理下一个
            logger.warning(f"⚠️ {symbol} 预灌期间发生异常: {e}")
            continue


def start_background_services(client_inst):
    """
    启动后台服务线程（V8.0 控制信令架构）
    使用daemon=True模式，确保主程序退出时子线程自动关闭
    
    🔥 重构核心：数据流与计算流解耦
    - 废除 Manager.dict，改用 _input_queue 控制信令同步配置
    - 引入 mp.Process 独立运行 Pandas 指标计算
    """
    logger.info("🚀 启动后台监控线程（V7.0 - 多进程架构）...")
    threads = []
    
    # ==========================================
    # 🔥 V7.0 多进程架构核心：信号计算与网络 IO 解耦
    # ==========================================
    global _input_queue, _output_queue, _worker_process
    _input_queue = mp.Queue(maxsize=5000)
    _output_queue = mp.Queue(maxsize=5000)
    
    from websocket_manager import set_input_queue
    from worker_logic import indicator_worker_loop
    
    # 1. 注入 input_queue 到 WebSocket 模块，使其能推送 OHLCV
    set_input_queue(_input_queue)
    
    # 🔥 V8.0 注入 _input_queue 到 config 模块，使配置变更能广播到子进程
    from config import set_input_queue_ref
    set_input_queue_ref(_input_queue)
    
    # 2. 创建并启动独立计算进程（彻底打破 GIL 限制）
    # 🔥 V8.0 不再传入 shared_config，子进程通过 _input_queue 接收 config_update 信令
    _worker_process = mp.Process(
        target=indicator_worker_loop,
        args=(_input_queue, _output_queue),
        daemon=True
    )
    _worker_process.start()
    logger.info(f"✅ 独立指标计算进程已点火 (PID: {_worker_process.pid})")
    
    # 3. 启动轻量级极速下单消费线程（在主进程运行，负责极速发单）
    def _signal_consumer_loop():
        from trading_engine import process_trading_signals
        import inspect
        sig = inspect.signature(process_trading_signals)
        logger.info("🎯 极速下单消费线程已启动")
        
        # 🔥 P2修复：指数退避 + 连续失败熔断
        consecutive_errors = 0
        MAX_CONSECUTIVE_ERRORS = 10
        
        while config.BOT_ACTIVE:
            try:
                # 从队列获取信号数据
                signal_data = _output_queue.get(timeout=0.5)
                process_trading_signals(client_inst, signal_data['signals'])
                
                # 成功处理，重置错误计数
                consecutive_errors = 0
            except mp.queues.Empty:
                continue
            except Exception as e:
                consecutive_errors += 1
                logger.error(f"❌ 信号执行消费异常 ({consecutive_errors}/{MAX_CONSECUTIVE_ERRORS}): {e}")
                
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    logger.critical(f"🚨 消费线程连续失败 {MAX_CONSECUTIVE_ERRORS} 次，触发熔断！")
                    try:
                        from utils import send_tg_msg
                        send_tg_msg(f"🚨 <b>信号消费线程熔断</b>\n连续失败: {consecutive_errors}次\n最后错误: {e}")
                    except Exception:
                        pass
                    break
                
                # 🔥 指数退避：2^n 秒，上限60秒
                backoff = min(2 ** consecutive_errors, 60)
                logger.warning(f"⏳ 指数退避 {backoff}s 后重试...")
                time.sleep(backoff)
                
    t_consumer = threading.Thread(target=_signal_consumer_loop, name="SignalConsumer", daemon=True)
    t_consumer.start()
    threads.append(t_consumer)

    # ==========================================
    # 📡 数据层：启动 WebSocket 实时流 (常驻运行)
    # 🔥 修复：WebSocket 行情流是只读的，SANDBOX 模式也必须连接
    #    否则 indicator_worker_loop 拿不到 K 线，队列空转超时
    # ==========================================
    if client_inst and SYSTEM_CONFIG.get("WEBSOCKET_ENABLED", True):
        try:
            from websocket_manager import start_websocket_streams
            monitor_symbols = list(SYSTEM_CONFIG.get("ASSET_WEIGHTS", {}).keys())
            if not monitor_symbols:
                monitor_symbols = SYSTEM_CONFIG.get("MONITOR_SYMBOLS", ["BTCUSDT", "ETHUSDT"])
            
            # 🔥 预灌历史 K 线到 input_queue，解决 indicator_worker_loop 冷启动无数据问题
            _prefill_indicator_worker(client_inst, monitor_symbols, _input_queue)
            
            # 🔥 使用 start_websocket_streams() 而非已废弃的 get_websocket_manager()
            start_websocket_streams(client_inst, monitor_symbols)
            logger.info(f"✅ WebSocket 实时行情流已启动（监控 {len(monitor_symbols)} 个币种，SANDBOX/LIVE 均接入行情）")
        except Exception as e:
            logger.warning(f"⚠️ WebSocket 初始化失败: {e}")
    
    # ==========================================
    # 🛡️ 巡逻层：启动所有安全风控与辅助线程 (1:1 还原原有功能)
    # ==========================================
    
    # 1. 市场状态分类器（波动率监控）
    t_regime = threading.Thread(target=market_regime_detector, args=(client_inst,), name="MarketRegime", daemon=True)
    
    # 2. 交易引擎主循环（负责 24/7 数据轮询兜底）
    t_engine = threading.Thread(target=trading_engine_loop, args=(client_inst,), name="TradingEngine", daemon=True)
    
    # 3. 止损巡逻（实时对账止损单）
    t_sl = threading.Thread(target=monitor_stop_loss_orders, args=(client_inst,), name="StopLoss", daemon=True)
    
    # 4. 账户回撤监控（熔断保护）
    t_drawdown = threading.Thread(target=monitor_account_drawdown, args=(client_inst,), name="Drawdown", daemon=True)
    
    # 5. 价格哨所（Telegram 价格推送）
    t_sentry = threading.Thread(target=price_sentry_engine, args=(client_inst,), name="PriceSentry", daemon=True)
    
    # 6. 每日统计（PnL 汇报）
    t_daily = threading.Thread(target=monitor_daily_performance, args=(client_inst,), name="DailyPerf", daemon=True)
    
    # 7. SCALPER 监控（剥头皮模式专用）
    t_scalper = threading.Thread(target=monitor_scalper_positions, args=(client_inst,), name="ScalperMon", daemon=True)
    
    # 8. AI战略战报引擎（凌晨定时触发）
    t_ai_report = threading.Thread(target=daily_ai_report_engine, args=(client_inst,), name="AIReport", daemon=True)
    
    # 批量点火巡逻线程
    patrol_threads = [t_regime, t_engine, t_sl, t_drawdown, t_sentry, t_daily, t_scalper, t_ai_report]
    for t in patrol_threads:
        t.start()
        threads.append(t)
        logger.info(f"✅ 安全巡逻线程已启动: {t.name}")
        
    # 9. AI 自动调参引擎 (可选模块加载)
    try:
        from monitors import ai_auto_tuner_loop
        t_auto_tune = threading.Thread(target=ai_auto_tuner_loop, args=(client_inst,), name="AIAutoTuner", daemon=True)
        t_auto_tune.start()
        threads.append(t_auto_tune)
        logger.info("✅ AI 自动调参引擎已启动")
    except (ImportError, AttributeError):
        pass

    # 10. 死信队列(DLQ)清道夫 (核心灾备)
    try:
        from dlq_worker import dlq_worker_loop
        t_dlq = threading.Thread(target=dlq_worker_loop, args=(client_inst,), name="DLQSweeper", daemon=True)
        t_dlq.start()
        threads.append(t_dlq)
        logger.info("✅ 死信队列清道夫已启动 (真实引擎)")
    except (ImportError, AttributeError):
        pass
    
    # 11. 相关性矩阵后台计算 (每4小时更新)
    try:
        from correlation_engine import correlation_updater_loop
        t_corr = threading.Thread(target=correlation_updater_loop, args=(client_inst,), name="CorrUpdater", daemon=True)
        t_corr.start()
        threads.append(t_corr)
        logger.info("✅ 动态相关性矩阵计算线程已启动")
    except (ImportError, AttributeError):
        pass

    # 12. 🌐 MacroCommander 战略编排器 (每5分钟扫描宏观气象)
    try:
        from intelligence_hub import macro_commander_loop
        t_macro = threading.Thread(target=macro_commander_loop, args=(client_inst,), name="MacroCommander", daemon=True)
        t_macro.start()
        threads.append(t_macro)
        logger.info("✅ MacroCommander 战略编排器已启动 (5分钟周期)")
    except (ImportError, AttributeError) as e:
        logger.warning(f"⚠️ MacroCommander 启动失败: {e}")

    return threads


def send_startup_notification():
    """
    发送系统启动通知到Telegram
    """
    chat_id = SYSTEM_CONFIG.get("TG_CHAT_ID", "")
    if not chat_id:
        return
    
    try:
        startup_msg = "🚀 <b>无界指挥部已上线</b>\n\n"
        startup_msg += f"📊 运行模式: {'🔍 验证模式' if config.VERIFICATION_MODE else ('💰 实盘模式' if not SYSTEM_CONFIG.get('DRY_RUN', False) else '🧪 模拟模式')}\n"
        startup_msg += f"🎯 策略: {SYSTEM_CONFIG.get('STRATEGY_MODE', 'STANDARD')}\n"
        startup_msg += f"📈 监控币种: {len(SYSTEM_CONFIG['ASSET_WEIGHTS'])} 个\n"
        startup_msg += f"⏱️ K线周期: {SYSTEM_CONFIG['INTERVAL']}\n"
        startup_msg += f"💰 基准本金: ${SYSTEM_CONFIG['BENCHMARK_CASH']:.2f}\n"
        startup_msg += f"⏰ 启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        startup_msg += "发送 /start 显示主菜单"
        bot.send_message(chat_id, startup_msg, parse_mode="HTML")
        logger.info("✅ 启动通知已发送")
    except Exception as e:
        logger.warning(f"⚠️ 发送启动通知失败: {e}")


def run_polling_loop():
    """
    运行Bot轮询循环
    带自动重连和异常处理，确保网络波动时能自动恢复
    """
    logger.info("=" * 60)
    logger.info("✅ 系统启动完成，开始监听消息...")
    logger.info("=" * 60)
    
    while True:
        try:
            logger.info("🤖 Bot开始轮询...")
            bot.infinity_polling(
                timeout=30,
                long_polling_timeout=30,
                allowed_updates=["message", "callback_query"]
            )
        except KeyboardInterrupt:
            logger.warning("\n\n⚠️ 收到退出信号，正在优雅关闭系统...")
            graceful_shutdown()
            break
        except Exception as e:
            logger.error(f"❌ Bot轮询异常: {e}")
            logger.info("⏳ 5秒后重新连接...")
            time.sleep(5)


def graceful_shutdown():
    """
    优雅退出：停止引擎、保存数据、关闭消息池、停止 WebSocket、停止多进程计算器、关闭 httpx 客户端
    """
    config.TRADING_ENGINE_ACTIVE = False
    config.BOT_ACTIVE = False
    logger.info("⏹️ 交易引擎已停止")
    
    # 🔥 V7.0 发送毒药包并等待计算进程安全退出
    global _input_queue, _worker_process
    if _input_queue is not None:
        try:
            _input_queue.put(None)
        except: pass
    if _worker_process is not None:
        _worker_process.join(timeout=3)
        logger.info("✅ 多进程指标计算器已安全退出")
    
    # 停止 WebSocket 管理器
    try:
        from websocket_manager import get_websocket_manager
        ws_manager = get_websocket_manager()
        if ws_manager:
            ws_manager.stop()
            logger.info("✅ WebSocket 管理器已停止")
    except Exception as e:
        logger.warning(f"⚠️ 停止 WebSocket 管理器失败: {e}")
    
    # 关闭 IntelligenceHub 的 httpx.AsyncClient
    try:
        from intelligence_hub import get_intelligence_hub
        hub = get_intelligence_hub()
        if hub:
            asyncio.run(hub.close())
            logger.info("✅ IntelligenceHub 连接已关闭")
    except Exception as e:
        logger.warning(f"⚠️ 关闭 IntelligenceHub 连接失败: {e}")
    
    # 关闭消息池
    try:
        shutdown_message_pool()
        logger.info("✅ 消息池已关闭")
    except Exception as e:
        logger.warning(f"⚠️ 关闭消息池失败: {e}")
    
    # 保存数据
    try:
        save_data()
        logger.info("✅ 数据已保存")
    except Exception as e:
        logger.warning(f"⚠️ 保存数据失败: {e}")
    
    # 发送关闭通知
    chat_id = SYSTEM_CONFIG.get("TG_CHAT_ID", "")
    if chat_id and bot:
        try:
            bot.send_message(chat_id, "⚠️ 系统已优雅关闭", parse_mode="HTML")
        except:
            pass
    
    logger.info("✅ 系统已彻底关闭")


def main():
    """
    主函数 - 系统指挥官
    职责：
    1. 环境检查与初始化
    2. 后台线程启动
    3. 轮询控制与异常恢复
    4. 优雅退出
    """
    global client, bot
    
    print("\n" + "=" * 60)
    print("🚀 无界指挥部量化交易系统 - 工业化版本 v7.0 (异步计算解耦)")
    print("=" * 60 + "\n")
    
    logger.info("ℹ️ 网络配置：币安 API 强制直连，TeleBot/AI 使用模块级代理")

    # 1. 环境自检
    if not validate_environment():
        sys.exit(1)
        
    # 🔥 [修复] 系统初始化第一步：从 Redis/JSON 恢复所有持仓和配置
    from config import load_data
    load_data()
    logger.info("💾 历史持仓与沙盒账本数据恢复完成")
    current_pid = os.getpid()

    try:
        cmd = f'taskkill /F /FI "IMAGENAME eq python.exe" /FI "PID ne {current_pid}"'
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            logger.info("✅ 残留进程清理完成")
    except Exception as e:
        logger.warning(f"⚠️ 进程清理异常: {e}")

    # 🔥 V8.0 弃用 Manager.dict，改用控制信令同步配置
    # 主进程和子进程各自维护普通 dict，通过 _input_queue 推送 config_update 信令
    logger.info("✅ 配置同步模式: 控制信令 (Control Message via _input_queue)")
    
    # 2. 初始化币安客户端
    client = initialize_binance_client()
    # 检查是否是空壳 Client
    if not client.API_KEY or client.API_KEY == "none":
        logger.warning("⚠️ API 连接未建立或 Key 缺失，系统将运行在受限/模拟模式")

    # 3. 初始化Telegram Bot
    bot = initialize_telegram_bot()
    
    # 4. 网络三向自检（一字不漏）
    logger.info("🔍 执行网络连通性检查...")
    check_all_connectivity()
    
    # 5. 实盘模式警报检查
    check_live_mode_warning()

    # 6. 初始化系统资源
    initialize_resources(client)
    
    # 7. 注册消息处理器
    logger.info("📝 正在注册消息处理器...")
    register_handlers(bot, client)
    logger.info("✅ 消息处理器注册完成")
    
    # 8. 动态对账：同步 BENCHMARK_CASH 到真实账户余额
    logger.info("📊 正在执行动态对账（BENCHMARK_CASH 同步）...")
    if config.SYSTEM_CONFIG.get("RUNNING_MODE") == "SANDBOX":
        logger.info("🏖️ 沙盒模式检测：跳过实盘对账，保护模拟本金不被归零！")
    else:
        try:
            if client:
                sync_ok, sync_msg = sync_benchmark_with_api(client)
                if sync_ok:
                    logger.info(f"✅ 动态对账完成: {sync_msg}")
                else:
                    logger.error(f"❌ 动态对账失败: {sync_msg}")
                    sys.exit(1)
        except Exception as e:
            logger.error(f"🚨 动态对账异常，引擎启动终止: {e}")
            sys.exit(1)
    
    # 9. 🔥 V8.0: 初始化风控高水位线（从真实余额，非硬编码 10,000）
    logger.info("🛡️ 正在初始化风控高水位线...")
    try:
        from risk_manager import get_risk_manager
        risk_mgr = get_risk_manager(config.SYSTEM_CONFIG)
        risk_mgr.initialize_hwm_from_balance(client)
        logger.info(f"✅ 风控高水位线初始化完成 | Real HWM: ${risk_mgr.real_high_water_mark:.2f} | Sim HWM: ${risk_mgr.sim_high_water_mark:.2f}")
    except Exception as e:
        logger.warning(f"⚠️ 风控高水位线初始化失败: {e}")
    
    # 10. 启动 LLM Worker 守护线程（V9.0 生产者-消费者架构）
    from llm_worker import start_llm_worker
    start_llm_worker(bot)
    logger.info("✅ LLM Worker 守护线程已启动（统一异步 LLM 调用）")
    
    # 11. 启动多进程后台服务线程（V8.0 控制信令架构）
    threads = start_background_services(client)
    
    # 10. 注册 SIGTERM 信号处理
    def _sigterm_handler(signum, frame):
        logger.warning(f"⚠️ 收到 SIGTERM 信号 (signum={signum})，正在优雅关闭系统...")
        graceful_shutdown()
        sys.exit(0)
    
    signal.signal(signal.SIGTERM, _sigterm_handler)
    signal.signal(signal.SIGINT, _sigterm_handler)
    logger.info("✅ SIGTERM/SIGINT 信号处理器已注册（支持 VPS 优雅停机）")
    
    # 11. 发送启动通知
    send_startup_notification()
    
    try:
        init_web_dashboard()
        # 既然你前面没看到那行字，我们在这里加个强力打印
        print("🚀 [SYSTEM] 网页监控中枢已在后台线程点火：http://127.0.0.1:8989")
    except Exception as e:
        logger.error(f"❌ 网页监控中枢启动失败: {e}")
    
    # 12. 运行轮询循环 (这个函数是阻塞的，一旦运行，后面的代码都不会执行)
    run_polling_loop()


if __name__ == "__main__":
    # 🔥 Windows 平台多进程必须包裹在 __main__ 下并调用 freeze_support
    mp.freeze_support() 
    main()