#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
无界指挥部 - 主程序入口
工业化重构版本 v2.0 - 纯指挥官模式
职责：系统初始化、后台线程管理、轮询控制
"""

import os
import subprocess
import sys
import time
import signal
import threading
from datetime import datetime
from binance.client import Client
from telebot import TeleBot

# 可选依赖：进程管理（用于检测残留进程）
try:
    import psutil  # type: ignore
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

# 导入日志系统
from logger_setup import logger

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
from websocket_manager import get_websocket_manager

# 导入命令处理器（仅注册函数）
from bot_handlers import register_handlers

# ==========================================
# 全局变量
# ==========================================
client = None
bot = None


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
    初始化币安客户端
    
    Returns:
        Client or None: 币安客户端实例
    """
    api_key = SYSTEM_CONFIG.get("API_KEY", "")
    api_secret = SYSTEM_CONFIG.get("API_SECRET", "")
    
    if api_key and api_secret and not config.VERIFICATION_MODE:
        try:
            logger.info("🔗 正在连接币安API...")
            client = Client(api_key, api_secret)
            client.futures_account()
            logger.info("✅ 币安API连接成功")
            return client
        except Exception as e:
            logger.warning(f"⚠️ 币安API连接失败: {e}")
            logger.warning("⚠️ 将以模拟模式运行")
            return None
    else:
        logger.warning("⚠️ API密钥未配置或处于验证模式，将以模拟模式运行")
        return None


def initialize_telegram_bot():
    """
    初始化Telegram Bot（🔥 定向代理注入）
    
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
        bot = TeleBot(tg_token, parse_mode="HTML")
        
        # 🔥 Task 1: 定向代理注入（仅 Telegram 走代理）
        from telebot import apihelper
        proxy_enabled = SYSTEM_CONFIG.get("PROXY_ENABLED", False)
        if proxy_enabled:
            proxy_host = SYSTEM_CONFIG.get("PROXY_HOST", "127.0.0.1")
            proxy_port = SYSTEM_CONFIG.get("PROXY_PORT", 4780)
            proxy_url = f"http://{proxy_host}:{proxy_port}"
            apihelper.proxy = {'https': proxy_url}
            logger.info(f"✅ Telegram 代理已配置: {proxy_url}")
        else:
            logger.info("ℹ️ Telegram 代理未启用，使用直连")
        
        set_bot_instance(bot)
        logger.info("✅ Telegram Bot初始化成功")
        return bot
    except Exception as e:
        logger.error(f"❌ Telegram Bot初始化失败: {e}")
        sys.exit(1)


def check_all_connectivity():
    """
    🔥 Task 3: 网络三向自检 + 4780 端口可用性探测
    分别测试币安 API（直连）、Telegram API（代理）、LLM API（代理）
    
    Returns:
        bool: 所有连接是否正常
    """
    logger.info("🔍 开始网络三向自检...")
    all_ok = True
    
    # 🔥 Task 3: 0. 检查 4780 端口可用性（代理软件监听检测）
    proxy_enabled = SYSTEM_CONFIG.get("PROXY_ENABLED", False)
    proxy_host = SYSTEM_CONFIG.get("PROXY_HOST", "127.0.0.1")
    proxy_port = SYSTEM_CONFIG.get("PROXY_PORT", 4780)
    
    if proxy_enabled:
        try:
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            result = sock.connect_ex((proxy_host, proxy_port))
            sock.close()
            
            if result == 0:
                logger.info(f"✅ 代理端口 {proxy_host}:{proxy_port} 可用")
            else:
                logger.error(f"❌ 代理端口 {proxy_host}:{proxy_port} 不可用")
                logger.error(f"💡 请检查代理软件是否监听 {proxy_port} 端口")
                all_ok = False
        except Exception as e:
            logger.error(f"❌ 代理端口检测失败: {e}")
            logger.error(f"💡 请检查代理软件是否监听 {proxy_port} 端口")
            all_ok = False
    
    # 1. 测试币安 API（直连）
    try:
        import requests
        response = requests.get("https://fapi.binance.com/fapi/v1/ping", timeout=5)
        if response.status_code == 200:
            logger.info("✅ 币安 API 连接正常（直连）")
        else:
            logger.warning(f"⚠️ 币安 API 响应异常: {response.status_code}")
            all_ok = False
    except Exception as e:
        logger.error(f"❌ 币安 API 连接失败: {e}")
        all_ok = False
    
    # 2. 测试 Telegram API（通过代理）
    try:
        if proxy_enabled:
            proxy_url = f"http://{proxy_host}:{proxy_port}"
            proxies = {'https': proxy_url}
            response = requests.get("https://api.telegram.org", proxies=proxies, timeout=5)
            logger.info(f"✅ Telegram API 连接正常（代理 {proxy_url}）")
        else:
            response = requests.get("https://api.telegram.org", timeout=5)
            logger.info("✅ Telegram API 连接正常（直连）")
    except Exception as e:
        logger.warning(f"⚠️ Telegram API 连接测试失败: {e}")
        logger.warning("💡 如果代理未开启，Telegram 消息可能无法发送")
        # Telegram 失败不阻止启动
    
    # 3. 测试 LLM API（通过代理）
    try:
        llm_provider = SYSTEM_CONFIG.get("LLM_PROVIDER", "openai")
        if llm_provider == "openai":
            test_url = "https://api.openai.com"
        elif llm_provider == "anthropic":
            test_url = "https://api.anthropic.com"
        else:
            test_url = None
        
        if test_url:
            if proxy_enabled:
                proxy_url = f"http://{proxy_host}:{proxy_port}"
                proxies = {'https': proxy_url}
                response = requests.get(test_url, proxies=proxies, timeout=5)
                logger.info(f"✅ LLM API 连接正常（代理 {proxy_url}）")
            else:
                response = requests.get(test_url, timeout=5)
                logger.info("✅ LLM API 连接正常（直连）")
    except Exception as e:
        logger.warning(f"⚠️ LLM API 连接测试失败: {e}")
        logger.warning("💡 AI 分析功能可能受影响")
        # LLM 失败不阻止启动
    
    if all_ok:
        logger.info("✅ 网络三向自检全部通过")
    else:
        logger.warning("⚠️ 部分网络连接异常，请检查配置")
    
    return all_ok


def initialize_resources(client):
    """
    初始化系统资源
    
    Args:
        client: 币安客户端实例
    """
    logger.info("📦 正在初始化系统资源...")
    
    # 加载哨所监控列表
    load_sentry_watchlist()
    
    # 提前获取交易对和精度信息
    get_all_valid_symbols(client)
    
    # 归一化权重
    normalize_weights(client)
    
    logger.info("✅ 系统资源初始化完成")


def start_background_services(client):
    """
    启动后台服务线程（V5.0 含 WebSocket + 市场状态分类器）
    使用daemon=True模式，确保主程序退出时子线程自动关闭
    
    🔥 重构核心：数据流与交易流解耦
    - WebSocket/数据监控：始终运行，不受 TRADING_ENGINE_ACTIVE 限制
    - 交易执行：仅在 TRADING_ENGINE_ACTIVE=True 时执行开平仓
    
    Args:
        client: 币安客户端实例
    
    Returns:
        list: 所有后台线程的列表
    """
    logger.info("🚀 启动后台监控线程（V5.0 - 数据流常驻模式）...")
    
    threads = []
    
    # 🔥 V5.0 新增：WebSocket 实时流管理器（常驻运行，不受交易开关限制）
    # 需要先定义回调函数，用于接收 WebSocket 触发的信号
    def websocket_signal_callback(symbol, df, signals):
        """
        WebSocket 信号回调：当 WebSocket 接收到新 K 线并生成信号时触发
        
        Args:
            symbol: 交易对
            df: 包含指标的 K 线数据
            signals: 交易信号字典
        """
        try:
            from trading_engine import process_trading_signals
            logger.info(f"🔔 WebSocket 触发信号: {symbol}")
            
            # 调用交易引擎处理信号（内部会检查 TRADING_ENGINE_ACTIVE）
            process_trading_signals(client, symbol, df, signals)
        except Exception as e:
            logger.error(f"⚠️ WebSocket 信号处理异常 {symbol}: {e}")
    
    # 初始化并启动 WebSocket 管理器（常驻运行）
    if client and SYSTEM_CONFIG.get("WEBSOCKET_ENABLED", True):
        try:
            ws_manager = get_websocket_manager(client, websocket_signal_callback)
            
            # 🔥 重构：WebSocket 线程常驻运行，不依赖 TRADING_ENGINE_ACTIVE
            def start_websocket():
                try:
                    ws_manager.start()
                    # 保持线程运行（使用 BOT_ACTIVE 而非 TRADING_ENGINE_ACTIVE）
                    while config.BOT_ACTIVE:
                        time.sleep(1)
                except Exception as e:
                    logger.error(f"❌ WebSocket 线程异常: {e}")
            
            t_websocket = threading.Thread(
                target=start_websocket,
                name="WebSocketManager",
                daemon=True
            )
            threads.append(t_websocket)
            logger.info("✅ WebSocket 管理器已加入启动队列（常驻模式）")
        except Exception as e:
            logger.warning(f"⚠️ WebSocket 管理器初始化失败: {e}")
    else:
        logger.info("ℹ️ WebSocket 功能未启用或客户端未连接")
    
    # 🔥 V5.0 新增：市场状态分类器（每小时分析波动率，自动熔断）
    t_regime = threading.Thread(
        target=market_regime_detector,
        args=(client,),
        name="MarketRegimeDetector",
        daemon=True
    )
    threads.append(t_regime)
    logger.info("✅ 市场状态分类器已加入启动队列")
    
    # 1. 交易引擎主循环
    t_engine = threading.Thread(
        target=trading_engine_loop,
        args=(client,),
        name="TradingEngine",
        daemon=True
    )
    threads.append(t_engine)
    
    # 2. 止损巡逻
    t_sl = threading.Thread(
        target=monitor_stop_loss_orders,
        args=(client,),
        name="StopLossMonitor",
        daemon=True
    )
    threads.append(t_sl)
    
    # 3. 账户回撤监控
    t_drawdown = threading.Thread(
        target=monitor_account_drawdown,
        args=(client,),
        name="DrawdownMonitor",
        daemon=True
    )
    threads.append(t_drawdown)
    
    # 4. 报价哨所
    t_sentry = threading.Thread(
        target=price_sentry_engine,
        args=(client,),
        name="PriceSentry",
        daemon=True
    )
    threads.append(t_sentry)
    
    # 5. 每日统计
    t_daily = threading.Thread(
        target=monitor_daily_performance,
        args=(client,),
        name="DailyPerformance",
        daemon=True
    )
    threads.append(t_daily)
    
    # 6. SCALPER 模式动态止盈止损监控（始终启动，内部自行判断是否激活）
    t_scalper = threading.Thread(
        target=monitor_scalper_positions,
        args=(client,),
        name="ScalperMonitor",
        daemon=True
    )
    threads.append(t_scalper)
    
    # 7. AI战略战报引擎（每日00:05触发）
    t_ai_report = threading.Thread(
        target=daily_ai_report_engine,
        args=(client,),
        name="AIReportEngine",
        daemon=True
    )
    threads.append(t_ai_report)
    
    # 8. 🔥 AI自动调参引擎（15分钟巡航，2小时冷却）
    try:
        from monitors import ai_auto_tuner_loop
        t_auto_tune = threading.Thread(
            target=ai_auto_tuner_loop,
            args=(client,),
            name="AIAutoTuner",
            daemon=True
        )
        threads.append(t_auto_tune)
        logger.info("✅ AI自动调参引擎已加入启动队列")
    except ImportError as e:
        logger.warning(f"⚠️ AI自动调参模块未找到: {e}")
    
    # 9. 🔥 死信队列清道夫（指数退避重试残留头寸）
    try:
        from dlq_worker import dlq_sweeper
        t_dlq = threading.Thread(
            target=dlq_sweeper,
            args=(client,),
            name="DLQSweeper",
            daemon=True
        )
        threads.append(t_dlq)
        logger.info("✅ 死信队列清道夫已加入启动队列")
    except ImportError as e:
        logger.warning(f"⚠️ 死信队列模块未找到: {e}")
    
    # 启动所有线程
    for t in threads:
        t.start()
        logger.info(f"✅ 后台线程已启动: {t.name}")
    
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
    优雅退出：停止引擎、保存数据、关闭消息池、停止 WebSocket
    """
    # 停止交易引擎
    config.TRADING_ENGINE_ACTIVE = False
    logger.info("⏹️ 交易引擎已停止")
    
    # 🔥 V5.0 停止 WebSocket 管理器
    try:
        from websocket_manager import get_websocket_manager
        ws_manager = get_websocket_manager()
        if ws_manager:
            ws_manager.stop()
            logger.info("✅ WebSocket 管理器已停止")
    except Exception as e:
        logger.warning(f"⚠️ 停止 WebSocket 管理器失败: {e}")
    
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
    
    # 发送关闭通知@
    chat_id = SYSTEM_CONFIG.get("TG_CHAT_ID", "")
    if chat_id and bot:
        try:
            bot.send_message(chat_id, "⚠️ 系统已优雅关闭", parse_mode="HTML")
        except:
            pass
    
    logger.info("✅ 系统已关闭")


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
    print("🚀 无界指挥部量化交易系统 - 工业化版本 v2.0")
    print("=" * 60 + "\n")
    
    # 🔥 Task 3: 移除全局代理环境变量（改为模块级精准控制）
    # 原因：全局环境变量会影响币安 API，导致连接失败
    # 新方案：TeleBot 和 AI Analyst 在各自模块内配置代理
    logger.info("ℹ️ 网络配置：币安 API 强制直连，TeleBot/AI 使用模块级代理")
    
    # 🔥 Task 3: 僵尸进程自动清理（防止 409 Conflict）
    logger.info("🔍 正在检查并清理残留进程...")
    current_pid = os.getpid()
    
    try:
        # 使用 taskkill 命令清理所有 python.exe 进程（排除当前 PID）
        # 注意：这会终止所有 Python 进程，请确保没有其他重要的 Python 程序在运行
        cmd = f'taskkill /F /FI "IMAGENAME eq python.exe" /FI "PID ne {current_pid}"'
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
        
        if result.returncode == 0:
            logger.info("✅ 残留进程清理完成")
            if "SUCCESS" in result.stdout:
                logger.info(f"   清理详情: {result.stdout.strip()}")
        elif "not found" in result.stdout.lower() or "no tasks" in result.stdout.lower():
            logger.info("✅ 未检测到残留进程")
        else:
            logger.warning(f"⚠️ 进程清理命令执行异常: {result.stdout.strip()}")
    except subprocess.TimeoutExpired:
        logger.warning("⚠️ 进程清理命令超时")
    except Exception as e:
        logger.warning(f"⚠️ 进程清理异常: {e}")
        logger.warning("💡 如遇到 409 Conflict 错误，请手动在任务管理器中结束其他 Python 进程")
    
    # 1. 环境自检
    if not validate_environment():
        sys.exit(1)
    
    # 2. 初始化币安客户端
    client = initialize_binance_client()
    
    # 3. 初始化Telegram Bot
    bot = initialize_telegram_bot()
    
    # 🔥 Task 1: 网络三向自检（在初始化完成后立即执行）
    logger.info("🔍 执行网络连通性检查...")
    check_all_connectivity()
    
    # 4. 实盘模式警报检查
    check_live_mode_warning()
    
    # 5. 初始化系统资源
    initialize_resources(client)
    
    # 6. 注册消息处理器（仅注册，不包含具体逻辑）
    logger.info("📝 正在注册消息处理器...")
    register_handlers(bot, client)
    logger.info("✅ 消息处理器注册完成")
    
    # 7. 🔥 动态对账：同步 BENCHMARK_CASH 到真实账户余额（必须在后台服务启动前执行）
    logger.info("📊 正在执行动态对账（BENCHMARK_CASH 同步）...")
    try:
        sync_ok, sync_msg = sync_benchmark_with_api(client)
        if sync_ok:
            logger.info(f"✅ 动态对账完成: {sync_msg}")
        else:
            logger.error(f"❌ 动态对账失败: {sync_msg}")
            sys.exit(1)
    except Exception as e:
        logger.error(f"🚨 动态对账异常，引擎启动终止: {e}")
        sys.exit(1)
    
    # 8. 启动后台服务线程
    threads = start_background_services(client)
    
    # 8. 注册 SIGTERM 信号处理（VPS 停机/服务重启时优雅退出）
    def _sigterm_handler(signum, frame):
        logger.warning(f"⚠️ 收到 SIGTERM 信号 (signum={signum})，正在优雅关闭系统...")
        graceful_shutdown()
        sys.exit(0)
    
    signal.signal(signal.SIGTERM, _sigterm_handler)
    # Windows 也支持 SIGINT（Ctrl+C），统一绑定
    signal.signal(signal.SIGINT, _sigterm_handler)
    logger.info("✅ SIGTERM/SIGINT 信号处理器已注册（支持 VPS 优雅停机）")
    
    # 9. 发送启动通知
    send_startup_notification()
    
    # 10. 运行轮询循环（带自动重连）
    run_polling_loop()


if __name__ == "__main__":
    main()
