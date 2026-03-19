#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram Bot 命令处理器
处理所有用户交互、命令、回调和界面显示
"""

import html
import time
import asyncio
import concurrent.futures
from datetime import datetime
from functools import wraps
from telebot import types
from telebot import apihelper
from config import (
    USE_PROXY_HARD_SWITCH, SYSTEM_CONFIG, ACTIVE_POSITIONS, SENTRY_CONFIG, SENTRY_INTERVAL_OPTIONS,
    STRATEGY_PRESETS, TRADE_HISTORY, LAUNCH_MODE_MAP, positions_lock,
    save_data, state_lock, save_sentry_watchlist,
    get_elastic_boundaries
)
from utils import (
    get_current_price, get_24h_change, get_all_valid_symbols, search_symbols_fuzzy,
    safe_send_message, safe_edit_message, safe_delete_message, safe_answer_callback,
    send_tg_msg, get_bot, create_progress_bar, normalize_weights,
    get_kline_chart_buffer
)
import config
from logger_setup import logger
_bot_ref = None

def register_bot_instance(bot_instance):
    global _bot_ref
    global bot # 声明全局 bot 变量供下方装饰器使用
    _bot_ref = bot_instance
    bot = bot_instance
from network_config import get_telebot_proxy
from human_override import get_override_manager

# ==========================================
# 🌍 TeleBot 全球网络物理硬开关 (从 config.py 读取)
# ==========================================
# 🇹🇭 出国直连模式：False | 🇨🇳 国内代理模式：True
if USE_PROXY_HARD_SWITCH:
    _PROXY_URL = "http://127.0.0.1:4780"
    apihelper.proxy = {'http': _PROXY_URL, 'https': _PROXY_URL}
    logger.info(f"✅ TeleBot 代理已启用: {_PROXY_URL}")
else:
    apihelper.proxy = None
    logger.info("ℹ️ TeleBot 直连模式（代理已禁用）")
# ==========================================

# ==========================================
# 鉴权装饰器
# ==========================================

def require_auth(func):
    """
    Telegram 命令鉴权装饰器
    自动验证用户身份，拦截未授权访问
    """
    @wraps(func)
    def wrapper(message, *args, **kwargs):
        chat_id = message.chat.id
        owner_chat_id = str(SYSTEM_CONFIG.get("TG_CHAT_ID", ""))
        
        # 🚨 顶级云端安全防线：拦截陌生人的文字指令
        if str(chat_id) != owner_chat_id:
            logger.warning(f"⛔ 触发越权拦截！陌生访客 [{chat_id}] 试图发送指令: {message.text}")
            safe_send_message(
                chat_id,
                "⛔ <b>访问被拒绝</b>\n\n您没有权限使用此机器人。",
                parse_mode="HTML"
            )
            return
        
        return func(message, *args, **kwargs)
    
    return wrapper

# ==========================================
# 菜单创建函数
# ==========================================

def create_main_menu():
    """
    🚀 无界指挥部 v7.0 - 纯 Inline 主控台
    
    返回: (msg_text, markup) 元组
    - msg_text: 主控台标题 + 状态信息 (HTML)
    - markup: InlineKeyboardMarkup
    
    按钮布局：
    - 📊 仪表盘 | ⚖️ 深度对账
    - 🚀 启动/切换 | 🎯 策略中心
    - ⚙️ 参数调优 | 🏦 资产管理
    - 🚨 紧急熔断
    """
    # 🔥 读取运行模式
    running_mode = SYSTEM_CONFIG.get("RUNNING_MODE", "SANDBOX")
    if running_mode == "SANDBOX":
        mode_line = "🟡 [模拟演习]"
    else:
        mode_line = "🔴 [实战模式]"
    
    # 🔥 引擎 & AI 状态指示
    engine_icon = "🟢 运行中" if config.TRADING_ENGINE_ACTIVE else "🔴 已停止"
    ai_autonomy = SYSTEM_CONFIG.get("AI_FULL_AUTONOMY_MODE", False)
    ai_icon = "🤖 自主激活" if ai_autonomy else "🔒 已锁定"
    
    msg_text = (
        f"🚀 <b>无界指挥部 v7.0</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{mode_line}\n"
        f"⚡ 引擎: {engine_icon} | 🧠 AI: {ai_icon}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👇 请选择操作："
    )
    
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("📊 仪表盘", callback_data="show_dashboard"),
        types.InlineKeyboardButton("⚖️ 深度对账", callback_data="show_positions")
    )
    markup.add(
        types.InlineKeyboardButton("🚀 启动/切换", callback_data="show_launch_wizard"),
        types.InlineKeyboardButton("🎯 策略中心", callback_data="show_strategy_center")
    )
    markup.add(
        types.InlineKeyboardButton("⚙️ 参数调优", callback_data="show_settings"),
        types.InlineKeyboardButton("🏦 资产管理", callback_data="show_asset_center")
    )
    markup.add(
        types.InlineKeyboardButton("🔐 Vault 管理", callback_data="show_vault_mgmt"),
        types.InlineKeyboardButton("🚨 紧急熔断", callback_data="emergency_close")
    )
    
    return msg_text, markup


# ==========================================
# 基础命令处理函数
# ==========================================

@require_auth
def handle_start_command(message):
    """处理 /start 命令 - V7.0 清除旧键盘 + Inline Console"""
    chat_id = message.chat.id
    
    # Step 1: 清除旧版 ReplyKeyboard（如果存在）
    safe_send_message(
        chat_id,
        "🔄 正在初始化 v7.0 控制台...",
        reply_markup=types.ReplyKeyboardRemove()
    )
    
    # Step 2: 发送新版 Inline 主控台
    msg_text, markup = create_main_menu()
    safe_send_message(chat_id, msg_text, parse_mode="HTML", reply_markup=markup)
    
    logger.info(f"✅ /start 命令执行完成 (v7.0 Inline Console) for {chat_id}")

@require_auth
def handle_add_command(message, client):
    """处理 /add 命令 - 添加币种"""
    chat_id = message.chat.id
    parts = message.text.split()
    
    if len(parts) != 3:
        safe_send_message(chat_id, "❌ 格式错误。请使用: <code>/add 币对 权重</code> (例如: /add SOLUSDT 0.2)", parse_mode="HTML")
        return
    
    symbol = parts[1].upper()
    if not symbol.endswith('USDT'):
        symbol += 'USDT'
    
    try:
        weight = float(parts[2])
        if weight <= 0:
            safe_send_message(chat_id, "❌ 权重必须大于0")
            return
    except ValueError:
        safe_send_message(chat_id, "❌ 权重格式错误，请输入有效的数字")
        return
    
    max_symbols = SYSTEM_CONFIG.get("MAX_ACTIVE_SYMBOLS", 5)
    if symbol not in SYSTEM_CONFIG["ASSET_WEIGHTS"] and len(SYSTEM_CONFIG["ASSET_WEIGHTS"]) >= max_symbols:
        safe_send_message(chat_id, f"❌ 已达到最大允许币对数量 ({max_symbols})")
        return
    
    valid_symbols = get_all_valid_symbols(client)
    if valid_symbols and symbol not in valid_symbols:
        safe_send_message(chat_id, f"❌ 币对 {html.escape(symbol)} 在币安合约中不存在")
        return
    
    SYSTEM_CONFIG["ASSET_WEIGHTS"][symbol] = weight
    if symbol not in SYSTEM_CONFIG.get("MONITOR_SYMBOLS", []):
        SYSTEM_CONFIG.setdefault("MONITOR_SYMBOLS", []).append(symbol)
    
    save_data()
    safe_send_message(chat_id, f"✅ 已成功设置 {html.escape(symbol)} 权重为 {weight}")
    _normalize_weights_with_msg(chat_id)

@require_auth
def handle_del_command(message, client):
    """处理 /del 命令 - 删除币种"""
    chat_id = message.chat.id
    parts = message.text.split()
    
    if len(parts) != 2:
        safe_send_message(chat_id, "❌ 格式错误。请使用: <code>/del 币对</code>", parse_mode="HTML")
        return
    
    symbol = parts[1].upper()
    if not symbol.endswith('USDT'):
        symbol += 'USDT'
    
    if symbol in SYSTEM_CONFIG["ASSET_WEIGHTS"]:
        del SYSTEM_CONFIG["ASSET_WEIGHTS"][symbol]
        if symbol in SYSTEM_CONFIG.get("MONITOR_SYMBOLS", []):
            SYSTEM_CONFIG["MONITOR_SYMBOLS"].remove(symbol)
        save_data()
        msg = f"✅ 已从监控列表中移除 {html.escape(symbol)}"
        if symbol in ACTIVE_POSITIONS:
            msg += f"\n\n⚠️ <b>警告:</b> {html.escape(symbol)} 当前仍有活跃持仓！"
        safe_send_message(chat_id, msg, parse_mode="HTML")
        _normalize_weights_with_msg(chat_id)
    else:
        safe_send_message(chat_id, f"❌ 未找到币对 {html.escape(symbol)}")

@require_auth
def handle_balance_command(message, client):
    """处理 /balance 命令 - 平衡权重"""
    chat_id = message.chat.id
    num_symbols = len(SYSTEM_CONFIG["ASSET_WEIGHTS"])
    
    if num_symbols == 0:
        safe_send_message(chat_id, "❌ 当前没有监控的币对")
        return
    
    avg_weight = round(1.0 / num_symbols, 4)
    for sym in SYSTEM_CONFIG["ASSET_WEIGHTS"]:
        SYSTEM_CONFIG["ASSET_WEIGHTS"][sym] = avg_weight
    
    current_total = sum(SYSTEM_CONFIG["ASSET_WEIGHTS"].values())
    if abs(current_total - 1.0) > 0.0001:
        last_sym = list(SYSTEM_CONFIG["ASSET_WEIGHTS"].keys())[-1]
        SYSTEM_CONFIG["ASSET_WEIGHTS"][last_sym] = round(
            SYSTEM_CONFIG["ASSET_WEIGHTS"][last_sym] + (1.0 - current_total), 4
        )
    save_data()
    
    msg = "⚖️ <b>资产权重已平均分配</b>\n\n<b>当前权重:</b>\n"
    for k, v in SYSTEM_CONFIG["ASSET_WEIGHTS"].items():
        msg += f"• {k}: {round(v*100, 2)}%\n"
    safe_send_message(chat_id, msg, parse_mode="HTML")

@require_auth
def handle_set_command(message, client):
    """处理 /set 命令 - 设置参数"""
    chat_id = message.chat.id
    parts = message.text.split()
    
    if len(parts) != 3:
        safe_send_message(chat_id, "❌ 格式错误。请使用: <code>/set 参数名 数值</code>", parse_mode="HTML")
        return
    
    param_name = parts[1].upper()
    param_value_str = parts[2]
    
    if param_name not in SYSTEM_CONFIG:
        safe_send_message(chat_id, f"❌ 未找到参数 <b>{param_name}</b>", parse_mode="HTML")
        return
    
    try:
        orig_value = SYSTEM_CONFIG[param_name]
        if isinstance(orig_value, int) and not isinstance(orig_value, bool):
            new_value = int(float(param_value_str))
        elif isinstance(orig_value, float):
            new_value = float(param_value_str)
        else:
            new_value = float(param_value_str)
        SYSTEM_CONFIG[param_name] = new_value
        save_data()
        
        get_override_manager().lock_parameter(param_name, new_value, reason="Telegram 命令修改")
        
        safe_send_message(chat_id, f"✅ <b>{param_name}</b> 已调整为 <b>{new_value}</b>", parse_mode="HTML")
    except ValueError:
        safe_send_message(chat_id, "❌ 数值格式错误", parse_mode="HTML")

@require_auth
def handle_close_command(message, client):
    """处理 /close 命令 - 平仓指定币种"""
    from trading_engine import execute_trade
    
    chat_id = message.chat.id
    parts = message.text.split()
    
    if len(parts) != 2:
        safe_send_message(chat_id, "❌ 格式错误。请使用: <code>/close 币对</code>", parse_mode="HTML")
        return
    
    symbol = parts[1].upper()
    if not symbol.endswith('USDT'):
        symbol += 'USDT'
    
    keys_to_close = []
    for k in list(ACTIVE_POSITIONS.keys()):
        if k == symbol or k.startswith(f"{symbol}_"):
            keys_to_close.append(k)
    
    if not keys_to_close:
        safe_send_message(chat_id, f"📭 未找到 {html.escape(symbol)} 的持仓", parse_mode="HTML")
        return
    
    success_count = 0
    for key_sym in keys_to_close:
        try:
            # 🔥 修复：支持列表形式的多笔订单
            positions_data = ACTIVE_POSITIONS[key_sym]
            if not isinstance(positions_data, list):
                positions_data = [positions_data]
            
            # 遍历该方向下的所有子订单
            for position in positions_data:
                real_symbol = position.get('real_symbol', symbol)
                current_price = get_current_price(client, real_symbol)
                if current_price:
                    result = execute_trade(
                        client, real_symbol, 'SELL' if position['type'] == 'LONG' else 'BUY',
                        current_price, {'quantity': position['qty']},
                        position_action='EXIT_LONG' if position['type'] == 'LONG' else 'EXIT_SHORT'
                    )
                    if result['success']:
                        success_count += 1
        except Exception as e:
            logger.error(f"❌ 平仓失败: {e}", exc_info=True)
    
    if success_count > 0:
        safe_send_message(chat_id, f"✅ 成功平掉 {html.escape(symbol)} 的 {success_count} 个持仓", parse_mode="HTML")

# ==========================================
# 仪表盘和持仓显示
# ==========================================

@require_auth
def handle_dashboard(message, client):
    """
    显示实时仪表盘（🔥 V6.0 重构：双显示模式 - 真实余额 + 沙盒余额）
    
    核心变更：
    - 🔥 V6.0: 双显示模式 - 同时显示真实余额和沙盒余额
    - 优先从 trading_engine 的指标缓存读取实时数据
    - 即使交易引擎暂停，仪表盘仍显示最新市场数据
    - 模拟/实盘环境视觉强制隔离
    """
    chat_id = message.chat.id
    
    try:
        # 🔥 获取真实账户余额：只有在 REAL 模式下才抓取 API 数据
        real_equity = 0.0
        real_available = 0.0
        real_unrealized_pnl = 0.0
        running_mode = SYSTEM_CONFIG.get("RUNNING_MODE", "SANDBOX")

        if running_mode == "REAL" and client and not config.VERIFICATION_MODE:
            try:
                acc = client.futures_account()
                real_equity = float(acc.get('totalMarginBalance', 0))
                real_available = float(acc.get('availableBalance', 0))
                real_unrealized_pnl = float(acc.get('totalUnrealizedProfit', 0))
            except:
                pass

        # 🏝️ 沙盒模式：真实余额锁定为 0，不暴露真实账户数据
        if real_equity == 0 and running_mode == "SANDBOX":
            real_equity = 0.0
            real_available = 0.0
        
        # 🔥 获取沙盒账户余额 - 数据完整性保护
        from trading_engine import get_sandbox_balance
        try:
            # 🔥 Task 3: 数据完整性保护 - 仅从 sandbox_ledger.json 读取
            ledger = get_sandbox_balance()
            sandbox_balance = float(ledger.get('balance', 10000.0))
            sandbox_initial = float(SYSTEM_CONFIG.get("SANDBOX_INITIAL_BALANCE", 10000.0))
            
            # 计算盈亏
            sandbox_pnl = sandbox_balance - sandbox_initial
            sandbox_pnl_pct = (sandbox_pnl / sandbox_initial * 100) if sandbox_initial > 0 else 0
        except (TypeError, ValueError) as e:
            # 🔥 Task 3: 数据损坏保护 - 回退到初始余额，绝不使用真实账户余额
            logger.error(f"❌ 沙盒账本读取失败: {e}")
            sandbox_initial = float(SYSTEM_CONFIG.get("SANDBOX_INITIAL_BALANCE", 10000.0))
            sandbox_balance = sandbox_initial  # 回退到初始余额
            sandbox_pnl = 0.0
            sandbox_pnl_pct = 0.0
            logger.warning(f"⚠️ 沙盒余额已回退到初始值: ${sandbox_initial:.2f}")
        
        # 计算真实账户盈亏
        benchmark = SYSTEM_CONFIG.get("BENCHMARK_CASH", 1800.0)
        real_total_pnl = real_equity - benchmark
        real_pnl_pct = (real_total_pnl / benchmark * 100) if benchmark > 0 else 0
        
        # 🔒 使用 state_lock 保护 PEAK_EQUITY 读取（防止竞态条件）
        with state_lock:
            peak = SYSTEM_CONFIG["PEAK_EQUITY"] if SYSTEM_CONFIG["PEAK_EQUITY"] > 0 else real_equity
        drawdown = ((peak - real_equity) / peak * 100) if peak > 0 and real_equity < peak else 0
        
        win_rate = 0
        if len(TRADE_HISTORY) > 0:
            wins = sum(1 for t in TRADE_HISTORY if t.get('pnl', 0) > 0)
            win_rate = (wins / len(TRADE_HISTORY)) * 100
        
        used_margin = real_equity - real_available
        margin_usage = (used_margin / real_equity * 100) if real_equity > 0 else 0
        
        current_mode = SYSTEM_CONFIG.get("STRATEGY_MODE", "STANDARD")
        mode_preset = STRATEGY_PRESETS.get(current_mode, STRATEGY_PRESETS["STANDARD"])
        
        # 🔥 V6.0: 环境视觉强制隔离 - 根据 RUNNING_MODE 显示不同的页眉
        running_mode = SYSTEM_CONFIG.get("RUNNING_MODE", "SANDBOX")
        if running_mode == "SANDBOX":
            env_header = "🟡 模拟演习"
            env_icon = "🟡"
        else:  # REAL
            env_header = "🔴 实盘战场"
            env_icon = "🔴"
        
        # 🔥 系统状态红绿灯
        engine_light = "🟢" if config.TRADING_ENGINE_ACTIVE else "🔴"
        dry_run_light = "🟡" if SYSTEM_CONFIG.get("DRY_RUN", False) else "🟢"
        status_line = f"{engine_light} 引擎 | {dry_run_light} {'模拟' if SYSTEM_CONFIG.get('DRY_RUN', False) else '实盘'}"
        
        msg = f"📊 <b>实时仪表盘</b> {env_icon} {env_header}\n━━━━━━━━━━━━━━━━━━━━\n\n"
        
        # 🔥 V6.0: 双显示模式 - 真实余额
        msg += "💰 <b>真实账户</b>\n"
        msg += f"├ 总权益: <code>${real_equity:.2f}</code>\n"
        msg += f"├ 可用余额: <code>${real_available:.2f}</code>\n"
        msg += f"├ 未实现盈亏: <code>${real_unrealized_pnl:+.2f}</code>\n"
        
        real_pnl_emoji = "🟢" if real_total_pnl > 0 else "🔴" if real_total_pnl < 0 else "⚪"
        real_pnl_bar = create_progress_bar(real_pnl_pct, 100, 10)
        msg += f"├ 总盈亏: {real_pnl_emoji} <code>${real_total_pnl:+.2f}</code> ({real_pnl_pct:+.2f}%)\n"
        msg += f"│ {real_pnl_bar}\n"
        
        dd_emoji = "🟢" if drawdown < 5 else "🟡" if drawdown < 15 else "🔴"
        msg += f"├ 最大回撤: {dd_emoji} <code>{drawdown:.2f}%</code>\n"
        msg += f"└ 📊 动态基准: <code>${SYSTEM_CONFIG.get('BENCHMARK_CASH', 0):.2f}</code>\n\n"
        
        # 🔥 V6.0: 双显示模式 - 沙盒余额
        sandbox_pnl_emoji = "🟢" if sandbox_pnl > 0 else "🔴" if sandbox_pnl < 0 else "⚪"
        sandbox_pnl_bar = create_progress_bar(sandbox_pnl_pct, 100, 10)
        msg += "🟡 <b>沙盒账户</b>\n"
        msg += f"├ 当前余额: <code>${sandbox_balance:.2f}</code>\n"
        msg += f"├ 初始本金: <code>${sandbox_initial:.2f}</code>\n"
        msg += f"├ 累计盈亏: {sandbox_pnl_emoji} <code>${sandbox_pnl:+.2f}</code> ({sandbox_pnl_pct:+.2f}%)\n"
        msg += f"│ {sandbox_pnl_bar}\n"
        
        wr_emoji = "🟢" if win_rate >= 60 else "🟡" if win_rate >= 50 else "🔴"
        msg += f"└ 历史胜率: {wr_emoji} <code>{win_rate:.1f}%</code> ({len(TRADE_HISTORY)}笔)\n\n"
        
        # 🔥 V6.0: SANDBOX 模式警告
        if running_mode == "SANDBOX":
            msg += "⚠️ <b>当前处于 SANDBOX 模式</b>\n"
            msg += "• 所有交易在沙盒环境中执行\n"
            msg += "• 不会影响真实账户余额\n"
            msg += "• 点击【🚀 启动/切换】可切换到实盘模式\n\n"
        else:
            msg += f"🌍 <b>运行环境:</b> {env_icon} {env_header}\n\n"
        
        # 🔒 线程安全：使用 positions_lock 保护 ACTIVE_POSITIONS 遍历
        with positions_lock:
            position_count = len(ACTIVE_POSITIONS)
            msg += "💼 <b>持仓状态</b>\n"
            msg += f"├ 活跃持仓: <code>{position_count}</code> 个\n"
            
            if position_count > 0:
                total_position_value = 0
                for key_sym, pos_data in ACTIVE_POSITIONS.items():
                    # 🔥 修复：支持列表形式的多笔订单
                    if isinstance(pos_data, list):
                        positions_list = pos_data
                    else:
                        positions_list = [pos_data]
                    
                    for pos in positions_list:
                        real_symbol = pos.get('real_symbol', key_sym.split('_')[0] if '_' in key_sym else key_sym)
                        current_price = get_current_price(client, real_symbol)
                        if current_price:
                            total_position_value += current_price * pos.get('qty', 0)
                
                capital_usage = (total_position_value / real_equity * 100) if real_equity > 0 else 0
                cu_emoji = "🟢" if capital_usage < 80 else "🟡" if capital_usage < 95 else "🔴"
                msg += f"├ 总仓位价值: <code>${total_position_value:.2f}</code>\n"
                msg += f"└ 资金利用率: {cu_emoji} <code>{capital_usage:.1f}%</code>\n\n"
            else:
                msg += f"└ 当前无持仓\n\n"
        
        msg += "🎯 <b>策略状态</b>\n"
        msg += f"├ 当前模式: {mode_preset['emoji']} <code>{mode_preset['name']}</code>\n"
        msg += f"├ 时间周期: <code>{SYSTEM_CONFIG['INTERVAL']}</code>\n"
        msg += f"├ ADX阈值: <code>{SYSTEM_CONFIG['ADX_THR']}</code>\n"
        msg += f"└ ATR倍数: <code>{SYSTEM_CONFIG['ATR_MULT']}</code>\n\n"
        
        engine_status = "🟢 运行中" if config.TRADING_ENGINE_ACTIVE else "🔴 已停止"
        mode_status = "🔍 验证模式" if config.VERIFICATION_MODE else "🔥 实盘模式"
        # 🔥 市场状态检测器（从 monitors 获取）
        regime_info = ""
        try:
            from monitors import get_current_regime
            regime_data = get_current_regime()
            if regime_data:
                regime_name = regime_data.get('regime', 'Unknown')
                volatility = regime_data.get('volatility', 0)
                regime_emoji = regime_data.get('emoji', '⚪')
                regime_info = f"\n📊 <b>市场状态</b>\n"
                regime_info += f"├ 当前Regime: {regime_emoji} <code>{regime_name}</code>\n"
                regime_info += f"└ 波动率水位: <code>{volatility:.2%}</code>\n\n"
        except Exception as e:
            logger.debug(f"获取市场状态失败（非致命）: {e}")
        
        msg += "⚙️ <b>引擎状态</b>\n"
        msg += f"├ 交易引擎: {engine_status}\n"
        msg += f"├ 运行模式: {mode_status}\n"
        msg += f"├ 保险库: {'🟢 启用' if SYSTEM_CONFIG['VAULT_ENABLED'] else '🔴 禁用'}\n"
        
        # 🔥 API 权重监控显示
        try:
            from api_weight_monitor import get_weight_status
            weight_status = get_weight_status()
            current_weight = weight_status['current_weight']
            limit = weight_status['limit']
            usage_pct = weight_status['usage_percent']
            
            # 权重状态指示器
            if usage_pct < 50:
                weight_emoji = "🟢"
            elif usage_pct < 80:
                weight_emoji = "🟡"
            else:
                weight_emoji = "🔴"
            
            msg += f"└ API权重: {weight_emoji} <code>{current_weight}/{limit}</code> ({usage_pct:.0f}%)\n\n"
        except Exception:
            msg += f"└ API权重: ⚪ <code>未监控</code>\n\n"
        
        # 插入市场状态信息
        if regime_info:
            msg += regime_info
        
        msg += "📈 <b>监控币种</b>\n"
        for i, (symbol, weight) in enumerate(SYSTEM_CONFIG["ASSET_WEIGHTS"].items(), 1):
            price = get_current_price(client, symbol)
            if price:
                msg += f"├ {symbol}: <code>${price:.2f}</code> ({weight*100:.0f}%)\n"
        
        msg += f"\n⏰ 更新时间: <i>{datetime.now().strftime('%H:%M:%S')}</i>"
        
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton("🔄 刷新", callback_data="refresh_dashboard"),
            types.InlineKeyboardButton("💼 持仓详情", callback_data="show_positions_detail")
        )
        markup.add(types.InlineKeyboardButton("🔙 返回主菜单", callback_data="back_to_main"))
        
        safe_send_message(chat_id, msg, parse_mode="HTML", reply_markup=markup)
        logger.info(f"📤 发送实时仪表盘给用户: {chat_id}")
        
    except Exception as e:
        safe_send_message(chat_id, f"❌ 获取仪表盘数据失败: {str(e)}", parse_mode="HTML")
        logger.error(f"❌ 发送仪表盘失败: {e}", exc_info=True)

@require_auth
def handle_positions(message, client):
    """查看持仓 - 增强版，优先从交易所实时拉取，支持手术刀级子仓位控制"""
    chat_id = message.chat.id
    msg = "📈 <b>当前持仓详情</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
    
    total_value = 0
    total_pnl = 0
    position_count = 0
    
    # 🔒 线程安全：使用 positions_lock 保护 ACTIVE_POSITIONS 读取
    exchange_positions = []
    if client and not SYSTEM_CONFIG.get("DRY_RUN", False) and not config.VERIFICATION_MODE:
        try:
            acc_info = client.futures_account()
            for pos in acc_info.get('positions', []):
                amt = float(pos.get('positionAmt', 0))
                if amt != 0:
                    exchange_positions.append({
                        'symbol': pos['symbol'],
                        'entry': float(pos.get('entryPrice', 0)),
                        'qty': abs(amt),
                        'type': 'LONG' if amt > 0 else 'SHORT',
                        'unrealizedProfit': float(pos.get('unrealizedProfit', 0)),
                        'leverage': int(pos.get('leverage', 20)),
                        'marginType': pos.get('marginType', 'cross'),
                        'initialMargin': float(pos.get('initialMargin', 0)),
                    })
        except Exception as e:
            logger.error(f"⚠️ 从交易所拉取持仓失败: {e}", exc_info=True)
    
    if exchange_positions:
        for pos in exchange_positions:
            symbol = pos['symbol']
            entry_price = pos['entry']
            qty = pos['qty']
            pos_type = pos['type']
            unrealized_pnl_val = pos['unrealizedProfit']
            leverage = pos['leverage']
            
            try:
                current_price = get_current_price(client, symbol)
            except:
                current_price = entry_price
            
            if current_price is None:
                current_price = entry_price
            
            position_value = current_price * qty
            total_value += position_value
            
            if entry_price > 0:
                if pos_type == 'LONG':
                    pnl_percent = (current_price - entry_price) / entry_price * 100
                else:
                    pnl_percent = (entry_price - current_price) / entry_price * 100
            else:
                pnl_percent = 0
            
            pnl = unrealized_pnl_val
            total_pnl += pnl
            position_count += 1
            
            key_sym = f"{symbol}_{pos_type}"
            local_pos_data = ACTIVE_POSITIONS.get(key_sym) or ACTIVE_POSITIONS.get(symbol) or {}
            # 🔥 修复：处理列表形式的持仓数据
            if isinstance(local_pos_data, list):
                local_pos = local_pos_data[0] if local_pos_data else {}
            else:
                local_pos = local_pos_data
            sl_price = local_pos.get('sl', 0)
            
            if leverage > 0:
                margin_rate = 1.0 / leverage
                if pos_type == 'LONG':
                    liquidation_price = entry_price * (1 - margin_rate + 0.006)
                else:
                    liquidation_price = entry_price * (1 + margin_rate - 0.006)
            else:
                liquidation_price = 0
            
            timestamp = local_pos.get('timestamp', None)
            time_str = ""
            if timestamp:
                if isinstance(timestamp, str):
                    try:
                        timestamp = datetime.fromisoformat(timestamp)
                    except:
                        timestamp = None
                if timestamp:
                    holding_time = datetime.now() - timestamp
                    hours = int(holding_time.total_seconds() / 3600)
                    minutes = int((holding_time.total_seconds() % 3600) / 60)
                    time_str = f"├ 持仓时间: <code>{hours}h {minutes}m</code>\n"
            
            plr_str = ""
            if sl_price > 0 and entry_price > 0:
                risk_amount = abs(entry_price - sl_price) * qty
                if risk_amount > 0:
                    profit_loss_ratio = pnl / risk_amount
                    plr_emoji = "🟢" if profit_loss_ratio > 1 else "🟡" if profit_loss_ratio > 0 else "🔴"
                    plr_str = f"├ 盈亏比: {plr_emoji} <code>{profit_loss_ratio:.2f}R</code>\n"
            
            pnl_emoji = "🟢" if pnl > 0 else "🔴" if pnl < 0 else "⚪"
            dir_emoji = "🚀 多单" if pos_type == 'LONG' else "🩸 空单"
            safe_symbol = html.escape(str(symbol))
            
            msg += f"💎 <b>{safe_symbol}</b> | {dir_emoji} | {leverage}x\n"
            msg += f"├ 数量: <code>{qty}</code>\n"
            msg += f"├ 买入价: <code>${entry_price:.4f}</code>\n"
            msg += f"├ 当前价: <code>${current_price:.4f}</code>\n"
            if sl_price > 0:
                msg += f"├ 止损价: <code>${sl_price:.4f}</code>\n"
                # 🔥 V5.1: 可视化止损距离
                if pos_type == 'LONG':
                    sl_distance_pct = ((current_price - sl_price) / entry_price * 100) if entry_price > 0 else 0
                else:
                    sl_distance_pct = ((sl_price - current_price) / entry_price * 100) if entry_price > 0 else 0
                
                if sl_distance_pct > 0:
                    sl_bar = create_progress_bar(sl_distance_pct, 10, 10)
                    msg += f"│ 止损缓冲: {sl_bar} <code>{sl_distance_pct:.1f}%</code>\n"
            if liquidation_price > 0:
                msg += f"├ 预估强平: <code>${liquidation_price:.4f}</code>\n"
            msg += f"├ 盈亏: {pnl_emoji} <code>${pnl:.2f}</code> ({pnl_percent:+.2f}%)\n"
            msg += plr_str
            msg += time_str
            msg += f"└ 仓位价值: <code>${position_value:.2f}</code>\n"
            msg += f"⚡ <code>/close {safe_symbol}</code>\n"
            
            # 🔥 手术刀级子仓位控制按钮（每笔订单独立操作）
            markup = types.InlineKeyboardMarkup(row_width=2)
            markup.add(
                types.InlineKeyboardButton("🛡️ 保本止损", callback_data=f"protect_{symbol}_{pos_type}"),
                types.InlineKeyboardButton("🔥 强平此单", callback_data=f"close_sub_{symbol}_{pos_type}")
            )
            safe_send_message(chat_id, msg, parse_mode="HTML", reply_markup=markup)
            msg = ""  # 重置消息，为下一个持仓准备
        
        total_pnl_emoji = "🟢" if total_pnl > 0 else "🔴" if total_pnl < 0 else "⚪"
        msg += "━━━━━━━━━━━━━━━━━━━━\n"
        msg += f"📊 <b>持仓汇总</b>\n"
        msg += f"├ 持仓数量: <code>{position_count}</code> 个\n"
        msg += f"├ 总仓位价值: <code>${total_value:.2f}</code>\n"
        msg += f"└ 总浮动盈亏: {total_pnl_emoji} <code>${total_pnl:.2f}</code>\n\n"
        msg += "💡 数据来源: 交易所实时数据\n"
        msg += "⚠️ 极端行情可点击【🛑 一键全平】"
    else:
        with positions_lock:
            positions_snapshot = dict(ACTIVE_POSITIONS.items())
            
            if not positions_snapshot:
                msg += "📭 当前没有活跃持仓\n\n"
                msg += "💡 <b>提示:</b>\n"
                msg += "• 如果您在交易所有持仓，请点击【⚖️ 同步真实仓位】\n"
                msg += "• 确保API密钥已正确配置且非验证模式"
            else:
                # 🔥 修复：支持列表形式的多笔订单
                for key_sym, positions_data in positions_snapshot.items():
                    # 确保是列表格式
                    if not isinstance(positions_data, list):
                        positions_data = [positions_data]
                    
                    # 遍历该方向下的所有子订单
                    for position in positions_data:
                        real_symbol = position.get('real_symbol', key_sym.split('_')[0] if '_' in key_sym else key_sym)
                        try:
                            current_price = get_current_price(client, real_symbol)
                        except:
                            current_price = None
                        
                        entry_price = position['entry']
                        qty = position['qty']
                        pos_type = position.get('type', 'LONG')
                        trade_id = position.get('trade_id', 'UNKNOWN')
                        sl_price = position.get('sl', 0)
                        
                        if current_price:
                            position_value = current_price * qty
                            total_value += position_value
                            
                            if pos_type == 'LONG':
                                pnl = (current_price - entry_price) * qty
                                pnl_percent = (current_price - entry_price) / entry_price * 100 if entry_price > 0 else 0
                            else:
                                pnl = (entry_price - current_price) * qty
                                pnl_percent = (entry_price - current_price) / entry_price * 100 if entry_price > 0 else 0
                            
                            total_pnl += pnl
                            position_count += 1
                            
                            pnl_emoji = "🟢" if pnl > 0 else "🔴" if pnl < 0 else "⚪"
                            dir_emoji = "🚀 多单" if pos_type == 'LONG' else "🩸 空单"
                            safe_symbol = html.escape(str(real_symbol))
                            
                            msg += f"💎 <b>{safe_symbol}</b> | {dir_emoji}\n"
                            msg += f"├ Trade ID: <code>{trade_id}</code>\n"
                            msg += f"├ 数量: <code>{qty}</code>\n"
                            msg += f"├ 买入价: <code>${entry_price:.4f}</code>\n"
                            msg += f"├ 当前价: <code>${current_price:.4f}</code>\n"
                            if sl_price > 0:
                                msg += f"├ 止损价: <code>${sl_price:.4f}</code>\n"
                            msg += f"├ 盈亏: {pnl_emoji} <code>${pnl:.2f}</code> ({pnl_percent:+.2f}%)\n"
                            msg += f"└ 仓位价值: <code>${position_value:.2f}</code>\n"
                            msg += f"⚡ <code>/close {safe_symbol}</code>\n"
                            
                            # 🔥 手术刀级子仓位控制按钮
                            markup = types.InlineKeyboardMarkup(row_width=2)
                            markup.add(
                                types.InlineKeyboardButton("🛡️ 保本止损", callback_data=f"protect_{trade_id}"),
                                types.InlineKeyboardButton("🔥 强平此单", callback_data=f"close_sub_{trade_id}")
                            )
                            safe_send_message(chat_id, msg, parse_mode="HTML", reply_markup=markup)
                            msg = ""  # 重置消息
                
                if position_count > 0:
                    total_pnl_emoji = "🟢" if total_pnl > 0 else "🔴" if total_pnl < 0 else "⚪"
                    msg += "━━━━━━━━━━━━━━━━━━━━\n"
                    msg += f"📊 持仓: {position_count}个 | 总值: ${total_value:.2f} | 盈亏: {total_pnl_emoji} ${total_pnl:.2f}\n\n"
                    msg += "💡 数据来源: 本地记录（建议点击【⚖️ 同步真实仓位】获取最新数据）"
    
    # 如果还有剩余消息（汇总信息），发送
    if msg.strip():
        pos_markup = types.InlineKeyboardMarkup(row_width=2)
        pos_markup.add(
            types.InlineKeyboardButton("🔄 刷新", callback_data="show_positions"),
            types.InlineKeyboardButton("🔙 返回主菜单", callback_data="back_to_main")
        )
        safe_send_message(chat_id, msg, parse_mode="HTML", reply_markup=pos_markup)
    logger.info(f"📤 发送持仓信息给用户: {chat_id}")

# ==========================================
# 策略、设置、保险库、哨所面板
# ==========================================

def show_strategy_center(chat_id, client):
    """显示策略中心"""
    from config import get_custom_mode_diff
    
    current_mode = SYSTEM_CONFIG.get("STRATEGY_MODE", "STANDARD")
    is_custom = SYSTEM_CONFIG.get("IS_CUSTOM_MODE", False)
    current_preset = STRATEGY_PRESETS.get(current_mode, STRATEGY_PRESETS["STANDARD"])
    
    msg = "🎯 <b>策略中心</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
    
    # 🔥 自定义模式指示器
    if is_custom:
        msg += f"<b>当前策略:</b> 🛠️ 自定义模式 (基于 {current_preset['name']})\n"
        msg += f"<b>说明:</b> 您已手动修改策略参数，系统已自动切换到自定义模式\n\n"
        
        # 显示参数差异
        diff = get_custom_mode_diff()
        if diff:
            msg += "⚠️ <b>与预设模式的差异:</b>\n"
            for item in diff:
                msg += f"├ {item['param']}: <code>{item['preset']}</code> → <code>{item['current']}</code>\n"
            msg += "\n"
    else:
        msg += f"<b>当前策略:</b> {current_preset['emoji']} {current_preset['name']}\n"
        msg += f"<b>说明:</b> {current_preset['description']}\n\n"
    msg += "📊 <b>当前参数</b>\n"
    msg += f"├ 时间周期: <code>{SYSTEM_CONFIG['INTERVAL']}</code>\n"
    msg += f"├ ADX阈值: <code>{SYSTEM_CONFIG['ADX_THR']}</code>\n"
    msg += f"├ EMA趋势线: <code>{SYSTEM_CONFIG['EMA_TREND']}</code>\n"
    msg += f"├ ATR倍数: <code>{SYSTEM_CONFIG['ATR_MULT']}</code>\n"
    msg += f"└ 风险系数: <code>{SYSTEM_CONFIG['RISK_RATIO']*100:.1f}%</code>\n\n"
    
    if not is_custom:
        msg += "🎯 <b>可选策略模式</b>\n"
        for key, preset in STRATEGY_PRESETS.items():
            status = "✅" if key == current_mode else "⚪"
            msg += f"{status} {preset['emoji']} <b>{preset['name']}</b>\n"
            msg += f"   {preset['description']}\n\n"
    else:
        msg += "💡 <b>提示:</b> 切换到预设模式将覆盖当前自定义参数\n\n"
    
    markup = types.InlineKeyboardMarkup(row_width=1)
    for key, preset in STRATEGY_PRESETS.items():
        if key != current_mode or is_custom:
            markup.add(types.InlineKeyboardButton(
                f"{preset['emoji']} 切换到{preset['name']}",
                callback_data=f"strategy_mode_{key}"
            ))
    markup.add(types.InlineKeyboardButton("⚙️ 高级设置", callback_data="settings_indicators"))
    markup.add(types.InlineKeyboardButton("🔙 返回", callback_data="back_to_main"))
    
    safe_send_message(chat_id, msg, parse_mode="HTML", reply_markup=markup)

@require_auth
def handle_vault_panel(message, client):
    """处理保险库面板 - 自适应动态阈值引擎 v2.0"""
    chat_id = message.chat.id
    vault_enabled = SYSTEM_CONFIG.get("VAULT_ENABLED", False)
    vault_balance = SYSTEM_CONFIG.get("VAULT_BALANCE", 0.0)
    vault_thr = SYSTEM_CONFIG.get("VAULT_THR", 250.0)
    withdraw_ratio = SYSTEM_CONFIG.get("WITHDRAW_RATIO", 0.5)
    auto_adapt = SYSTEM_CONFIG.get("VAULT_AUTO_ADAPT", True)
    
    if client and not config.VERIFICATION_MODE:
        try:
            acc = client.futures_account()
            balance = float(acc['totalMarginBalance'])
        except:
            balance = SYSTEM_CONFIG.get("BENCHMARK_CASH", 1800.0)
    else:
        balance = SYSTEM_CONFIG.get("BENCHMARK_CASH", 1800.0)
    
    benchmark = SYSTEM_CONFIG.get("BENCHMARK_CASH", 1800.0)
    current_profit = balance - benchmark
    
    msg = f"🏦 <b>保险库管理 v2.0</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
    msg += f"<b>状态:</b> {'✅ 已启用' if vault_enabled else '❌ 未启用'}\n"
    msg += f"<b>保险库余额:</b> ${vault_balance:.2f}\n"
    msg += f"<b>划转比例:</b> {withdraw_ratio*100:.0f}%\n\n"
    
    # ====== 🔥 自适应引擎状态显示 ======
    if auto_adapt:
        from utils import calculate_dynamic_vault_ratio
        try:
            adaptive_info = calculate_dynamic_vault_ratio()
            kelly_factor = adaptive_info['kelly_factor']
            base_ratio = adaptive_info['base_ratio']
            dynamic_ratio = adaptive_info['dynamic_ratio']
            regime = adaptive_info['regime']
            drawdown_pct = adaptive_info['drawdown_pct']
            
            # 状态指示器
            if regime == '顺风局扩张':
                regime_emoji = "🚀"
            elif regime == '逆风局收缩':
                regime_emoji = "🛡️"
            elif regime == '回撤防守':
                regime_emoji = "⚠️"
            else:
                regime_emoji = "⚪"
            
            msg += f"🤖 <b>自适应引擎:</b> ✅ 开启\n"
            msg += f"├ Kelly系数: <code>{kelly_factor:.2f}x</code>\n"
            msg += f"├ 基准比例: <code>{base_ratio*100:.1f}%</code>\n"
            msg += f"├ 动态生效: <code>{dynamic_ratio*100:.1f}%</code> {regime_emoji}\n"
            msg += f"├ 市场判断: {regime}\n"
            msg += f"└ 当前回撤: <code>{drawdown_pct:.1f}%</code>\n\n"
            
            # 计算动态触发阈值
            vault_thr_dynamic = benchmark * dynamic_ratio
            msg += f"<b>触发阈值:</b> ${vault_thr_dynamic:.2f} (动态)\n\n"
            
            msg += f"<b>当前账户:</b>\n"
            msg += f"• 总权益: ${balance:.2f}\n"
            msg += f"• 基准资金: ${benchmark:.2f}\n"
            msg += f"• 当前利润: ${current_profit:+.2f}\n\n"
            
            if current_profit >= vault_thr_dynamic:
                msg += f"💡 当前利润已达动态阈值，可以触发划转！\n"
                msg += f"📊 预计划转: ${current_profit * withdraw_ratio:.2f}"
            else:
                remaining = vault_thr_dynamic - current_profit
                msg += f"📊 距离触发还需: ${remaining:.2f}\n"
                msg += f"💡 AI正在根据Kelly系数自动调节阈值"
        except Exception as e:
            logger.error(f"获取自适应信息失败: {e}")
            msg += f"🤖 <b>自适应引擎:</b> ⚠️ 数据加载中...\n\n"
            msg += f"<b>触发阈值:</b> ${vault_thr:.2f} (固定)\n\n"
            msg += f"<b>当前账户:</b>\n"
            msg += f"• 总权益: ${balance:.2f}\n"
            msg += f"• 基准资金: ${benchmark:.2f}\n"
            msg += f"• 当前利润: ${current_profit:+.2f}\n\n"
            
            if current_profit >= vault_thr:
                msg += f"💡 当前利润已达阈值，可以触发划转！"
            else:
                remaining = vault_thr - current_profit
                msg += f"📊 距离触发还需: ${remaining:.2f}"
    else:
        # 固定阈值模式
        msg += f"🤖 <b>自适应引擎:</b> ❌ 关闭 (固定阈值模式)\n\n"
        msg += f"<b>触发阈值:</b> ${vault_thr:.2f} (固定)\n\n"
        msg += f"<b>当前账户:</b>\n"
        msg += f"• 总权益: ${balance:.2f}\n"
        msg += f"• 基准资金: ${benchmark:.2f}\n"
        msg += f"• 当前利润: ${current_profit:+.2f}\n\n"
        
        if current_profit >= vault_thr:
            msg += f"💡 当前利润已达阈值，可以触发划转！"
        else:
            remaining = vault_thr - current_profit
            msg += f"📊 距离触发还需: ${remaining:.2f}"
    
    # 🔥 Fix: 补充保险库交互按钮
    vault_markup = types.InlineKeyboardMarkup(row_width=2)
    vault_markup.add(
        types.InlineKeyboardButton(
            "❌ 停用保险库" if vault_enabled else "✅ 启用保险库",
            callback_data="vault_disable" if vault_enabled else "vault_enable"
        ),
        types.InlineKeyboardButton("⚙️ 设置划转比例", callback_data="vault_set_ratio")
    )
    vault_markup.add(
        types.InlineKeyboardButton("💰 手动划转", callback_data="vault_manual_transfer"),
        types.InlineKeyboardButton(
            "📌 固定阈值" if auto_adapt else "🤖 自适应",
            callback_data="vault_toggle_adapt"
        )
    )
    vault_markup.add(types.InlineKeyboardButton("🔙 返回主菜单", callback_data="back_to_main"))
    
    safe_send_message(chat_id, msg, parse_mode="HTML", reply_markup=vault_markup)

@require_auth
def handle_sentry_panel(message, client):
    """处理价格哨所面板"""
    chat_id = message.chat.id
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.row(
        types.InlineKeyboardButton("➕ 添加币种", callback_data="sentry_add"),
        types.InlineKeyboardButton("➖ 移除币种", callback_data="sentry_remove")
    )
    markup.row(
        types.InlineKeyboardButton("✅ 启用哨所" if not SENTRY_CONFIG["ENABLED"] else "❌ 停用哨所",
                                  callback_data="sentry_toggle")
    )
    markup.row(
        types.InlineKeyboardButton("⏱️ 设置间隔", callback_data="sentry_interval"),
        types.InlineKeyboardButton("📊 立即推送", callback_data="sentry_push_now")
    )
    markup.row(types.InlineKeyboardButton("🔙 返回", callback_data="back_to_main"))
    
    msg = f"🔭 <b>价格哨所</b>\n\n"
    msg += f"<b>状态:</b> {'✅ 运行中' if SENTRY_CONFIG['ENABLED'] else '❌ 已停止'}\n"
    msg += f"<b>推送间隔:</b> {SENTRY_CONFIG['INTERVAL']//60} 分钟\n"
    msg += f"<b>监控币种:</b> {len(SENTRY_CONFIG['WATCH_LIST'])} 个\n\n"
    
    if SENTRY_CONFIG["WATCH_LIST"]:
        msg += "<b>监控列表:</b>\n"
        for i, symbol in enumerate(SENTRY_CONFIG["WATCH_LIST"], 1):
            price = get_current_price(client, symbol)
            if price:
                msg += f"{i}. {html.escape(symbol)}: ${price:.4f}\n"
            else:
                msg += f"{i}. {html.escape(symbol)}: ⚠️ 无价格\n"
    else:
        msg += "📭 监控列表为空"
    
    safe_send_message(chat_id, msg, parse_mode="HTML", reply_markup=markup)

def show_indicators_settings(chat_id, message_id=None):
    """显示指标参数设置面板"""
    msg = "📊 <b>指标参数设置</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
    msg += f"<b>当前参数:</b>\n"
    msg += f"├ 时间周期: <code>{SYSTEM_CONFIG['INTERVAL']}</code>\n"
    msg += f"├ ADX阈值: <code>{SYSTEM_CONFIG['ADX_THR']}</code>\n"
    msg += f"├ EMA趋势线: <code>{SYSTEM_CONFIG['EMA_TREND']}</code>\n"
    msg += f"├ ATR倍数: <code>{SYSTEM_CONFIG['ATR_MULT']}</code>\n"
    msg += f"├ 低波模式: <code>{'开启' if SYSTEM_CONFIG.get('LOW_VOL_MODE') else '关闭'}</code>\n"
    msg += f"└ 风险系数: <code>{SYSTEM_CONFIG['RISK_RATIO']*100:.1f}%</code>\n\n"
    msg += "💡 点击下方按钮调整参数"
    
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.row(
        types.InlineKeyboardButton("⏱️ 时间周期", callback_data="param_INTERVAL"),
        types.InlineKeyboardButton("📈 ADX阈值", callback_data="param_ADX_THR")
    )
    markup.row(
        types.InlineKeyboardButton("📊 EMA趋势", callback_data="param_EMA_TREND"),
        types.InlineKeyboardButton("📏 ATR倍数", callback_data="param_ATR_MULT")
    )
    markup.row(
        types.InlineKeyboardButton("🌊 低波模式", callback_data="param_LOW_VOL_MODE"),
        types.InlineKeyboardButton("⚡ 风险系数", callback_data="param_RISK_RATIO")
    )
    markup.row(types.InlineKeyboardButton("🔙 返回", callback_data="back_to_settings"))
    
    if message_id:
        safe_edit_message(chat_id, message_id, msg, parse_mode="HTML", reply_markup=markup)
    else:
        safe_send_message(chat_id, msg, parse_mode="HTML", reply_markup=markup)

def show_settings_menu(chat_id, message_id=None, client=None):
    """显示设置菜单"""
    current_mode = SYSTEM_CONFIG.get("STRATEGY_MODE", "STANDARD")
    current_preset = STRATEGY_PRESETS.get(current_mode, STRATEGY_PRESETS["STANDARD"])
    engine_status = "🟢 运行中" if config.TRADING_ENGINE_ACTIVE else "🔴 已停止"
    mode_status = "🔍 验证模式" if config.VERIFICATION_MODE else "🔥 实盘模式"
    auto_tune_enabled = SYSTEM_CONFIG.get("AUTO_TUNE_ENABLED", False)
    autonomy_enabled = SYSTEM_CONFIG.get("AI_FULL_AUTONOMY_MODE", False)
    
    msg = "⚙️ <b>系统设置</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
    msg += f"🎯 策略模式: {current_preset['emoji']} {current_preset['name']}\n"
    msg += f"⚡ 交易引擎: {engine_status}\n"
    msg += f"🔧 运行模式: {mode_status}\n"
    msg += f"🧪 运行环境: {'🔍 模拟开单 (DRY_RUN)' if SYSTEM_CONFIG.get('DRY_RUN') else '🔥 实盘交易 (REAL_MODE)'}\n"
    msg += f"🏦 保险库: {'✅ 启用' if SYSTEM_CONFIG['VAULT_ENABLED'] else '❌ 禁用'}\n"
    msg += f"🤖 AI自动调参: {'🟢 开启' if auto_tune_enabled else '🔴 关闭'}\n"
    msg += f"🧠 AI满血接管: {'🔥 已激活' if autonomy_enabled else '🔒 锁定'}\n"
    msg += f" 杠杆: {SYSTEM_CONFIG.get('LEVERAGE', 20)}x\n"
    msg += f"💰 基准本金: ${SYSTEM_CONFIG.get('BENCHMARK_CASH', 1800):.2f}\n"
    msg += f"📈 风险系数: {SYSTEM_CONFIG.get('RISK_RATIO', 0)*100:.1f}%\n"
    msg += f"⏱️ 时间周期: {SYSTEM_CONFIG.get('INTERVAL', '15m')}\n"
    
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.row(
        types.InlineKeyboardButton("🎯 策略模式", callback_data="settings_strategy_mode"),
        types.InlineKeyboardButton("📊 指标参数", callback_data="settings_indicators")
    )
    markup.row(
        types.InlineKeyboardButton("⚡ 启动引擎" if not config.TRADING_ENGINE_ACTIVE else "⏹️ 停止引擎",
                                  callback_data="toggle_engine"),
        types.InlineKeyboardButton("🔍 验证模式" if not config.VERIFICATION_MODE else "🔥 实盘模式",
                                  callback_data="toggle_verification")
    )
    markup.row(
        types.InlineKeyboardButton("🔍 模拟开单" if not SYSTEM_CONFIG.get('DRY_RUN') else "🔥 实盘交易",
                                  callback_data="toggle_dry_run"),
        types.InlineKeyboardButton("⚖️ 同步仓位", callback_data="sync_positions")
    )
    markup.row(
        types.InlineKeyboardButton(
            f"🤖 AI自动调参: {'🟢 开启' if auto_tune_enabled else '🔴 关闭'}",
            callback_data="toggle_auto_tune"
        )
    )
    markup.row(
        types.InlineKeyboardButton(
            f"🧠 AI满血接管: {'🔥 已激活' if autonomy_enabled else '🔒 锁定'}",
            callback_data="toggle_ai_autonomy"
        )
    )
    markup.row(
        types.InlineKeyboardButton("🎛️ 核心引擎开关 (对齐回测)", callback_data="menu_engine_switches")
    )
    markup.row(
        types.InlineKeyboardButton("🛑 一键全平", callback_data="emergency_close")
    )
    markup.row(types.InlineKeyboardButton("🔙 返回主菜单", callback_data="back_to_main"))
    
    if message_id:
        safe_edit_message(chat_id, message_id, msg, parse_mode="HTML", reply_markup=markup)
    else:
        safe_send_message(chat_id, msg, parse_mode="HTML", reply_markup=markup)

def show_real_time_prices(chat_id, client, message_id=None):
    """显示实时价格"""
    msg = "💰 <b>实时价格监控</b>\n\n"
    symbols = list(SYSTEM_CONFIG["ASSET_WEIGHTS"].keys())
    if not symbols:
        msg += "📭 当前没有监控的币种\n"
    for symbol in symbols:
        price = get_current_price(client, symbol)
        if price is not None:
            try:
                change_24h = get_24h_change(client, symbol)
            except:
                change_24h = None
            safe_symbol = html.escape(str(symbol))
            msg += f"💎 <b>{safe_symbol}</b>\n"
            msg += f"💰 当前价格: <code>${price:.2f}</code>\n"
            if change_24h is not None:
                change_pct = change_24h * 100
                if change_24h > 0:
                    msg += f"📈 24h变化: <code>+{change_pct:.2f}%</code> 🟢\n"
                elif change_24h < 0:
                    msg += f"📉 24h变化: <code>{change_pct:.2f}%</code> 🔴\n"
                else:
                    msg += f"📊 24h变化: <code>{change_pct:.2f}%</code> ⚪\n"
            msg += "\n"
        else:
            msg += f"⚠️ {html.escape(str(symbol))}: 无法获取价格\n\n"
    msg += f"⏰ 更新时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(types.InlineKeyboardButton("🔄 刷新价格", callback_data="refresh_prices"))
    markup.add(types.InlineKeyboardButton("🔙 返回主菜单", callback_data="back_to_main"))
    if message_id:
        safe_edit_message(chat_id, message_id, msg, parse_mode="HTML", reply_markup=markup)
    else:
        safe_send_message(chat_id, msg, parse_mode="HTML", reply_markup=markup)

# ==========================================
# 启动向导
# ==========================================

def show_launch_wizard(chat_id, client):
    """显示启动向导 - 统一入口（含凯利公式绩效指标）"""
    owner_chat_id = str(SYSTEM_CONFIG.get("TG_CHAT_ID", ""))
    if str(chat_id) != owner_chat_id:
        return
    
    # 获取当前状态
    engine_running = config.TRADING_ENGINE_ACTIVE
    verification_mode = config.VERIFICATION_MODE
    dry_run = SYSTEM_CONFIG.get("DRY_RUN", False)
    current_strategy = SYSTEM_CONFIG.get("STRATEGY_MODE", "STANDARD")
    
    msg = "🚀 <b>启动向导</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
    
    if engine_running:
        msg += f"⚡ <b>引擎状态:</b> 🟢 运行中\n"
        msg += f"🎯 <b>当前策略:</b> {STRATEGY_PRESETS[current_strategy]['emoji']} {STRATEGY_PRESETS[current_strategy]['name']}\n"
        msg += f"🔧 <b>运行模式:</b> {'🔍 验证模式' if verification_mode else '🔥 实盘模式'}\n"
        msg += f"🧪 <b>开单模式:</b> {'🔍 模拟开单' if dry_run else '🔥 实盘交易'}\n\n"
        msg += "💡 引擎正在运行，您可以切换策略或停止引擎。"
    else:
        msg += f"⚡ <b>引擎状态:</b> 🔴 已停止\n"
        msg += f"🎯 <b>当前策略:</b> {STRATEGY_PRESETS[current_strategy]['emoji']} {STRATEGY_PRESETS[current_strategy]['name']}\n"
        msg += f"🔧 <b>运行模式:</b> {'🔍 验证模式' if verification_mode else '🔥 实盘模式'}\n"
        msg += f"🧪 <b>开单模式:</b> {'🔍 模拟开单' if dry_run else '🔥 实盘交易'}\n\n"
        msg += "💡 请选择策略模式启动引擎："
    
    # 对冲模式状态
    hedge_enabled = SYSTEM_CONFIG.get("HEDGE_MODE_ENABLED", False)
    hedge_label = "✅ 对冲模式 (多空异步并存)" if hedge_enabled else "❌ 单向模式 (多空自动互斥)"
    
    msg += f"\n🔀 <b>持仓模式:</b> {hedge_label}\n"
    
    # ====== 新增：凯利公式绩效指标显示 ======
    from trading_engine import get_performance_stats
    try:
        perf_stats = get_performance_stats(lookback=50)
        kelly_factor = perf_stats['kelly_factor']
        win_rate = perf_stats['win_rate']
        plr = perf_stats['profit_loss_ratio']
        sample_size = perf_stats['sample_size']
        
        # 凯利系数状态指示
        if kelly_factor >= 1.2:
            kelly_emoji = "🟢"
            kelly_status = "优秀"
        elif kelly_factor >= 1.0:
            kelly_emoji = "🟡"
            kelly_status = "良好"
        elif kelly_factor >= 0.8:
            kelly_emoji = "🟠"
            kelly_status = "一般"
        else:
            kelly_emoji = "🔴"
            kelly_status = "保守"
        
        msg += f"\n📊 <b>凯利配资引擎</b>\n"
        msg += f"├ 胜率(W): <code>{win_rate:.1%}</code>\n"
        msg += f"├ 盈亏比(R): <code>{plr:.2f}</code>\n"
        msg += f"├ Kelly系数: {kelly_emoji} <code>{kelly_factor:.2f}x</code> ({kelly_status})\n"
        msg += f"└ 样本数: <code>{sample_size}</code> 笔\n"
        
        if sample_size < 10:
            msg += f"\n⚠️ 样本数不足，当前使用保守配资策略\n"
    except Exception as e:
        msg += f"\n📊 <b>凯利配资引擎:</b> 初始化中...\n"
    
    markup = types.InlineKeyboardMarkup(row_width=1)
    
    # 对冲模式切换按钮
    markup.add(types.InlineKeyboardButton(
        hedge_label,
        callback_data="toggle_hedge_mode"
    ))
    
    if engine_running:
        # 引擎运行中 - 显示切换策略和停止按钮
        for key, preset in STRATEGY_PRESETS.items():
            if key != current_strategy:
                markup.add(types.InlineKeyboardButton(
                    f"{preset['emoji']} 切换到{preset['name']}",
                    callback_data=f"strategy_mode_{key}"
                ))
        markup.add(types.InlineKeyboardButton("⏹️ 停止引擎", callback_data="launch_stop"))
    else:
        # 引擎停止 - 显示所有策略模式
        for key, preset in STRATEGY_PRESETS.items():
            status = "✅" if key == current_strategy else "⚪"
            markup.add(types.InlineKeyboardButton(
                f"{status} {preset['emoji']} {preset['name']}",
                callback_data=f"launch_start_{key}"
            ))
    
    markup.add(types.InlineKeyboardButton("🔙 返回主菜单", callback_data="back_to_main"))
    
    safe_send_message(chat_id, msg, parse_mode="HTML", reply_markup=markup)

# ==========================================
# 模拟账本中心
# ==========================================

def show_sim_ledger_center(chat_id, client):
    """显示模拟账本中心面板"""
    import os
    import csv
    
    owner_chat_id = str(SYSTEM_CONFIG.get("TG_CHAT_ID", ""))
    if str(chat_id) != owner_chat_id:
        return
    
    from trading_engine import get_sandbox_balance
    ledger = get_sandbox_balance()
    sim_balance = float(ledger.get("balance", 10000.0))
    sim_initial = float(SYSTEM_CONFIG.get("SANDBOX_INITIAL_BALANCE", 10000.0))
    sim_pnl = sim_balance - sim_initial
    sim_pnl_pct = (sim_pnl / sim_initial * 100) if sim_initial > 0 else 0
    csv_file = SYSTEM_CONFIG.get("SIM_REPORT_FILE", "simulated_ledger.csv")
    
    # 从CSV读取最近的交易记录
    recent_trades = []
    total_trades = 0
    total_wins = 0
    total_losses = 0
    total_sim_pnl = 0.0
    
    if os.path.exists(csv_file):
        try:
            with open(csv_file, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                all_trades = list(reader)
                total_trades = len(all_trades)
                
                for t in all_trades:
                    try:
                        pnl_val = float(t.get('净盈亏', 0))
                        total_sim_pnl += pnl_val
                        if pnl_val > 0:
                            total_wins += 1
                        elif pnl_val < 0:
                            total_losses += 1
                    except:
                        pass
                
                # 取最近5条
                recent_trades = all_trades[-5:] if len(all_trades) >= 5 else all_trades
        except Exception as e:
            logger.error(f"⚠️ 读取模拟账本CSV失败: {e}")
    
    win_rate = (total_wins / total_trades * 100) if total_trades > 0 else 0
    pnl_emoji = "🟢" if sim_pnl > 0 else "🔴" if sim_pnl < 0 else "⚪"
    
    msg = "📒 <b>模拟账本中心</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
    msg += "💰 <b>沙盒账户概览</b>\n"
    msg += f"├ 初始本金: <code>${sim_initial:.2f}</code>\n"
    msg += f"├ 当前余额: <code>${sim_balance:.2f}</code>\n"
    msg += f"├ 累计盈亏: {pnl_emoji} <code>${sim_pnl:+.2f}</code> ({sim_pnl_pct:+.2f}%)\n"
    msg += f"├ 总交易次数: <code>{total_trades}</code>\n"
    msg += f"├ 盈利/亏损: <code>{total_wins}/{total_losses}</code>\n"
    
    wr_emoji = "🟢" if win_rate >= 60 else "🟡" if win_rate >= 50 else "🔴"
    msg += f"└ 胜率: {wr_emoji} <code>{win_rate:.1f}%</code>\n\n"
    
    # ====== 新增：凯利公式绩效指标 ======
    from trading_engine import get_performance_stats
    try:
        perf_stats = get_performance_stats(lookback=50)
        kelly_factor = perf_stats['kelly_factor']
        plr = perf_stats['profit_loss_ratio']
        
        kelly_emoji = "🟢" if kelly_factor >= 1.0 else "🟡" if kelly_factor >= 0.8 else "🔴"
        msg += "📊 <b>凯利配资引擎</b>\n"
        msg += f"├ 盈亏比(R): <code>{plr:.2f}</code>\n"
        msg += f"└ Kelly系数: {kelly_emoji} <code>{kelly_factor:.2f}x</code>\n\n"
    except:
        pass
    
    # 显示最近交易记录
    if recent_trades:
        msg += "📋 <b>最近交易记录</b>\n"
        for t in reversed(recent_trades):
            try:
                symbol = t.get('币种', '?')
                direction = t.get('方向', '?')
                net_pnl_str = t.get('净盈亏', '0')
                net_pnl_val = float(net_pnl_str)
                timestamp = t.get('时间戳', '?')
                t_emoji = "🟢" if net_pnl_val > 0 else "🔴"
                dir_emoji = "📈" if direction == 'LONG' else "📉"
                msg += f"├ {dir_emoji} {html.escape(symbol)} {t_emoji} ${net_pnl_val:+.2f} ({timestamp[-8:]})\n"
            except:
                pass
        msg += "\n"
    else:
        msg += "📭 暂无交易记录\n\n"
    
    msg += f"📁 账本文件: <code>{csv_file}</code>\n"
    msg += f"⏰ 更新时间: <i>{datetime.now().strftime('%H:%M:%S')}</i>"
    
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.row(
        types.InlineKeyboardButton("🔄 刷新", callback_data="sim_ledger_refresh"),
        types.InlineKeyboardButton("📊 下载报表", callback_data="sim_ledger_download")
    )
    markup.row(
        types.InlineKeyboardButton("🔁 重置余额", callback_data="sim_ledger_reset"),
        types.InlineKeyboardButton("🗑️ 清空记录", callback_data="sim_ledger_clear")
    )
    markup.row(types.InlineKeyboardButton("🔙 返回主菜单", callback_data="back_to_main"))
    
    safe_send_message(chat_id, msg, parse_mode="HTML", reply_markup=markup)

# ==========================================
# 工具函数
# ==========================================

def _format_analysis_report(analysis_data, clean_text):
    """
    🔥 Task 2: 格式化分析报告（四大模块结构）
    
    Args:
        analysis_data: 从 AI 响应中提取的 JSON 数据
        clean_text: 清理后的文本分析内容
    
    Returns:
        str: 格式化的 HTML 报告
    """
    try:
        # 获取宏观天气状态
        macro_regime = SYSTEM_CONFIG.get('MACRO_WEATHER_REGIME', 'SAFE')
        risk_score = SYSTEM_CONFIG.get('MACRO_WEATHER_RISK_SCORE', 0)
        sentiment_score = SYSTEM_CONFIG.get('MACRO_WEATHER_SENTIMENT_SCORE', 0)
        
        weather_emoji = {
            'SAFE': '☀️',
            'RISK_OFF': '🌫️',
            'VOLATILE_CRISIS': '⛈️'
        }.get(macro_regime, '☀️')
        
        # 构建报告头部
        report = "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        report += "📊 <b>深度分析报告</b>\n"
        report += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        
        # 【模块一：宏观天气】
        report += f"🌍 <b>【宏观天气】</b>\n"
        report += f"├ 全球状态: {weather_emoji} <code>{macro_regime}</code>\n"
        report += f"├ 地缘风险: <code>{risk_score:.1f}/10</code>\n"
        report += f"└ 市场情绪: <code>{sentiment_score:+.1f}/10</code>\n\n"
        
        # 【模块二：技术指标面】
        report += "📈 <b>【技术指标面】</b>\n"
        
        # 提取技术分析内容
        recommendation = analysis_data.get('recommendation', 'HOLD')
        confidence = analysis_data.get('confidence', 0.5)
        suggested_mode = analysis_data.get('suggested_mode', 'STANDARD')
        
        rec_emoji = {
            'BUY': '🟢',
            'SELL': '🔴',
            'HOLD': '🟡',
            'REDUCE_EXPOSURE': '🟠'
        }.get(recommendation, '⚪')
        
        conf_bar = create_progress_bar(confidence * 100, 100, 10)
        
        report += f"├ AI建议: {rec_emoji} <code>{recommendation}</code>\n"
        report += f"├ 置信度: {conf_bar} <code>{confidence:.0%}</code>\n"
        report += f"└ 推荐策略: <code>{suggested_mode}</code>\n\n"
        
        # 【模块三：风险预警】
        report += "⚠️ <b>【风险预警】</b>\n"
        
        devils_advocate = analysis_data.get('devils_advocate', '无特殊风险')
        risk_notes = analysis_data.get('risk_notes', '常规风险管理')
        
        # 新闻-技术背离检测
        news_technical_alignment = analysis_data.get('news_technical_alignment', 'ALIGNED')
        alignment_emoji = '✅' if news_technical_alignment == 'ALIGNED' else '⚠️'
        
        report += f"├ 新闻对齐: {alignment_emoji} <code>{news_technical_alignment}</code>\n"
        report += f"├ 反向论证: <i>{devils_advocate[:80]}...</i>\n"
        report += f"└ 风险提示: <i>{risk_notes[:80]}...</i>\n\n"
        
        # 【模块四：操盘建议】
        report += "💡 <b>【操盘建议】</b>\n"
        
        reasoning = analysis_data.get('reasoning', clean_text[:200])
        macro_impact = analysis_data.get('macro_geopolitical_impact', '无重大影响')
        
        report += f"├ 核心逻辑: <i>{reasoning[:100]}...</i>\n"
        report += f"└ 宏观影响: <i>{macro_impact[:100]}...</i>\n\n"
        
        # 视觉增强提示
        visual_needed = analysis_data.get('visual_chart_needed', False)
        if visual_needed:
            report += "🎨 <b>建议生成 MTF 双周期 K 线图进行视觉对账</b>\n\n"
        
        report += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        report += f"⏰ 生成时间: <i>{datetime.now().strftime('%H:%M:%S')}</i>\n"
        
        return report
        
    except Exception as e:
        logger.error(f"❌ 格式化分析报告失败: {e}", exc_info=True)
        # 降级：返回原始文本
        return clean_text

def _detect_coin_symbol(text):
    """
    从用户文本中检测币种符号
    Returns: symbol (e.g. 'BTCUSDT') or None
    """
    import re
    text_upper = text.upper()
    
    # 常见币种别名映射
    alias_map = {
        '大饼': 'BTCUSDT', '比特币': 'BTCUSDT', 'BTC': 'BTCUSDT',
        '二饼': 'ETHUSDT', '以太': 'ETHUSDT', '以太坊': 'ETHUSDT', 'ETH': 'ETHUSDT',
        'SOL': 'SOLUSDT', 'BNB': 'BNBUSDT', 'XRP': 'XRPUSDT',
        'DOGE': 'DOGEUSDT', '狗狗': 'DOGEUSDT',
    }
    
    for alias, symbol in alias_map.items():
        if alias in text_upper or alias in text:
            return symbol
    
    # 正则匹配 xxxUSDT 格式
    match = re.search(r'\b([A-Z]{2,10})USDT\b', text_upper)
    if match:
        return match.group(0)
    
    # 匹配纯大写币种名
    match = re.search(r'\b([A-Z]{2,6})\b', text_upper)
    if match:
        candidate = match.group(1) + 'USDT'
        # 检查是否在监控列表中
        if candidate in SYSTEM_CONFIG.get("ASSET_WEIGHTS", {}):
            return candidate
    
    return None


def _build_local_indicator_report(symbol, indicator_data):
    """
    🔥 增强版本地指标报告：解决价格 $0.00 的幽灵 Bug
    基于 get_indicator_cache 数据生成纯本地分析
    """
    leverage = SYSTEM_CONFIG.get("LEVERAGE", 20)
    risk_ratio = SYSTEM_CONFIG.get("RISK_RATIO", 0.025)
    
    report = f"📊 <b>本地指标分析报告</b> (AI 超时降级)\n"
    report += "━━━━━━━━━━━━━━━━━━━━\n\n"
    report += f"🎯 币种: <code>{html.escape(symbol)}</code>\n\n"
    
    if not indicator_data:
        report += "⚠️ 无可用指标数据\n"
        return report
    
    # 🔥 多重路径抓取价格 (兼容大小写键名 + 实时 API 兜底)
    close_price = (
        indicator_data.get('close') or
        indicator_data.get('Close') or
        indicator_data.get('CLOSE') or
        indicator_data.get('last_price') or
        indicator_data.get('price') or
        0.0
    )
    
    # 🔥 API 兜底：如果缓存中价格仍为 0，尝试实时获取
    if close_price == 0.0:
        try:
            api_price = get_current_price(None, symbol)
            if api_price and api_price > 0:
                close_price = api_price
                logger.info(f"🔥 [{symbol}] 价格从 API 兜底获取: ${close_price:.2f}")
        except Exception as e:
            logger.warning(f"⚠️ [{symbol}] API 兜底获取价格失败: {e}")
    
    # ⚡ 瞬时趋势判断
    adx = indicator_data.get('ADX', 0)
    rsi = indicator_data.get('RSI', 50)
    macd_hist = indicator_data.get('MACD_hist', 0)
    ema_trend = indicator_data.get('EMA_TREND', 0)
    atr = indicator_data.get('ATR', 0)
    
    if macd_hist > 0 and rsi > 50 and (close_price > ema_trend if ema_trend else True):
        trend = "Bullish 🟢"
    elif macd_hist < 0 and rsi < 50 and (close_price < ema_trend if ema_trend else True):
        trend = "Bearish 🔴"
    else:
        trend = "Neutral ⚪"
    
    # 🔥 价格状态图标：$0.00 时显示警告
    price_status = '⚠️' if close_price == 0 else '✅'
    
    report += f"⚡ <b>瞬时趋势:</b> {trend}\n"
    report += f"├ ADX: <code>{adx:.1f}</code> ({'强趋势' if adx > 25 else '弱趋势/震荡'})\n"
    report += f"├ RSI: <code>{rsi:.1f}</code>\n"
    report += f"├ MACD_hist: <code>{macd_hist:.6f}</code>\n"
    report += f"└ 价格: <b>${close_price:.2f}</b> {price_status}\n\n"
    
    # 🎯 战术区间
    if close_price > 0 and atr > 0:
        support = close_price - atr * 2.0
        resistance = close_price + atr * 2.0
        report += f"🎯 <b>战术区间 (ATR={atr:.2f}):</b>\n"
        report += f"├ 阻力位: <code>${resistance:.2f}</code>\n"
        report += f"└ 支撑位: <code>${support:.2f}</code>\n\n"
    
    # 🛡️ 风险提示（基于杠杆的爆仓预警）
    if close_price > 0 and leverage > 0:
        margin_rate = 1.0 / leverage
        liq_long = close_price * (1 - margin_rate + 0.006)
        liq_short = close_price * (1 + margin_rate - 0.006)
        liq_distance_pct = margin_rate * 100
        
        report += f"🛡️ <b>风险提示 ({leverage}x 杠杆):</b>\n"
        report += f"├ 多单强平价: <code>${liq_long:.2f}</code>\n"
        report += f"├ 空单强平价: <code>${liq_short:.2f}</code>\n"
        report += f"├ 爆仓距离: <code>{liq_distance_pct:.1f}%</code>\n"
        report += f"└ 风险系数: <code>{risk_ratio*100:.1f}%</code>\n\n"
    
    report += f"⏰ 生成时间: <i>{datetime.now().strftime('%H:%M:%S')}</i>\n"
    report += "💡 <i>此为本地指标降级报告，AI 分析超时</i>"
    
    return report


def _market_query_callback(chat_id, ai_reply, meta, bot_instance):
    """
    LLM Worker 回调：处理市场查询的 AI 回复，格式化后推送给用户。
    在 llm_worker 线程中执行，不阻塞主线程。
    """
    import re
    import io

    symbol = meta.get("symbol", "UNKNOWN")
    indicator_data = meta.get("indicator_data")
    chart_bytes = meta.get("chart_bytes")

    if not ai_reply:
        local_report = _build_local_indicator_report(symbol, indicator_data)
        safe_send_message(chat_id, local_report, parse_mode="HTML")
        return

    # 清理 AI 回复中的思考过程和 JSON
    clean_reply = re.sub(r'###COMMAND###.*?###COMMAND###', '', ai_reply, flags=re.DOTALL).strip()
    clean_reply = re.sub(r'```json.*?```', '', clean_reply, flags=re.DOTALL).strip()
    clean_reply = re.sub(r'\{[^{}]*\}', '', clean_reply, flags=re.DOTALL).strip()
    clean_reply = re.sub(r'\[R1推演过程\].*?\n\n', '', clean_reply, flags=re.DOTALL).strip()
    clean_reply = re.sub(r'\[THOUGHT.*?\].*?\n', '', clean_reply, flags=re.DOTALL).strip()

    # 构建最终消息
    final_msg = f"📊 <b>{html.escape(symbol)} 市场分析</b>\n"
    final_msg += "━━━━━━━━━━━━━━━━━━━━\n\n"
    final_msg += f"{clean_reply[:1500]}\n"
    final_msg += f"\n⏰ <i>{datetime.now().strftime('%H:%M:%S')}</i>"

    safe_send_message(chat_id, final_msg, parse_mode="HTML")

    # 如果有图表，也发送给用户
    if chart_bytes and bot_instance:
        try:
            bot_instance.send_photo(chat_id, io.BytesIO(chart_bytes), caption=f"📈 {symbol} 15m K线图")
        except Exception as e:
            logger.debug(f"K线图发送失败: {e}")

    logger.info(f"✅ 市场分析完成: {symbol} for {chat_id}")


def handle_market_query(chat_id, user_text, symbol, client):
    """
    🔥 V9.0 生产者模式：组装 prompt → 入队 → 立即返回
    彻底消除 ThreadPoolExecutor 嵌套 asyncio.run 的死锁风险
    """
    from llm_worker import llm_task_queue

    bot_instance = get_bot()
    if bot_instance:
        bot_instance.send_chat_action(chat_id, 'typing')

    # ====== Step 1: 数据注入 - 读取实时指标快照（纯同步，无阻塞） ======
    from trading_engine import get_indicator_cache, get_all_audit_logs, get_historical_klines, calculate_indicators

    indicator_data = get_indicator_cache(symbol)
    leverage = SYSTEM_CONFIG.get("LEVERAGE", 20)
    risk_ratio = SYSTEM_CONFIG.get("RISK_RATIO", 0.025)

    # 读取最近5条审计日志
    audit_summary = ""
    try:
        all_logs = get_all_audit_logs(limit=5)
        if all_logs:
            audit_lines = []
            for log in all_logs[:5]:
                if isinstance(log, dict):
                    ts = log.get('timestamp', '?')
                    sym = log.get('symbol', '?')
                    direction = log.get('direction', '?')
                    reason = log.get('decision_reason', '?')[:50]
                    audit_lines.append(f"  [{ts}] {sym} {direction} - {reason}")
            if audit_lines:
                audit_summary = "\n".join(audit_lines)
    except Exception as e:
        logger.debug(f"审计日志读取失败（非致命）: {e}")

    # 格式化指标快照
    indicator_text = "无可用数据"
    if indicator_data:
        ind_lines = []
        for k, v in indicator_data.items():
            if isinstance(v, float):
                ind_lines.append(f"  {k}: {v:.4f}")
            else:
                ind_lines.append(f"  {k}: {v}")
        indicator_text = "\n".join(ind_lines[:15])

    # ====== Step 2: 尝试生成 K 线图（纯同步） ======
    chart_bytes = None
    try:
        df_15m = get_historical_klines(client, symbol, '15m', limit=500)
        if df_15m is not None and len(df_15m) > 0:
            df_15m = calculate_indicators(df_15m)
            chart_bytes = get_kline_chart_buffer(df_15m, symbol=symbol, num_candles=80)
    except Exception as e:
        logger.debug(f"K线图生成失败（非致命）: {e}")

    # ====== Step 3: 组装 prompt ======
    structured_prompt = f"""# 🎯 市场快速分析请求

## 交易对: {symbol}

## 实时指标快照 (Indicator Cache)
{indicator_text}

## 系统参数
- 杠杆: {leverage}x
- 风险系数: {risk_ratio*100:.1f}%
- 策略模式: {SYSTEM_CONFIG.get('STRATEGY_MODE', 'STANDARD')}
- 时间周期: {SYSTEM_CONFIG.get('INTERVAL', '15m')}

## 最近5条审计日志
{audit_summary if audit_summary else '暂无审计记录'}

## 用户问题
{user_text}

## 输出格式要求（严格遵守，禁止输出思考过程）
请直接输出以下三个模块，不要输出任何自我修正或思考链：

⚡ 瞬时趋势: [Bullish/Bearish/Neutral] + 一句话理由
🎯 战术区间: 支撑位 $xxx / 阻力位 $xxx（基于ATR和关键结构）
🛡️ 风险提示: 基于{leverage}x杠杆的爆仓预警距离 + 仓位建议

用中文回答，简洁精准，总字数不超过200字。"""

    # ====== Step 4: 入队，立即返回 ======
    try:
        llm_task_queue.put_nowait({
            "type": "market_query",
            "chat_id": chat_id,
            "prompt": structured_prompt,
            "callback": _market_query_callback,
            "meta": {
                "symbol": symbol,
                "indicator_data": indicator_data,
                "chart_bytes": chart_bytes,
            }
        })
    except Exception as e:
        logger.error(f"❌ LLM 队列已满，降级为本地报告: {e}")
        local_report = _build_local_indicator_report(symbol, indicator_data)
        safe_send_message(chat_id, local_report, parse_mode="HTML")
        return

    # 立即回复用户，释放主线程
    safe_send_message(chat_id, "🧠 指挥官正在深度推演中，请稍候...", parse_mode="HTML")


def _normalize_weights_with_msg(chat_id):
    """归一化权重并发送消息"""
    normalize_weights(None)  # 使用 utils 中的函数
    msg = "⚖️ <b>权重已自动归一化</b>\n\n"
    for k, v in SYSTEM_CONFIG["ASSET_WEIGHTS"].items():
        msg += f"• {k}: {round(v*100, 2)}%\n"
    safe_send_message(chat_id, msg, parse_mode="HTML")

# ==========================================
# 命令注册函数
# ==========================================

def register_handlers(bot, client):
    """注册所有消息处理器"""
    
    @bot.message_handler(commands=['start', 'menu'])
    def cmd_start(message):
        handle_start_command(message)
    
    @bot.message_handler(commands=['dashboard', 'dash'])
    def cmd_dashboard(message):
        handle_dashboard(message, client)
    
    @bot.message_handler(commands=['positions', 'pos', 'p'])
    def cmd_positions(message):
        handle_positions(message, client)
    
    @bot.message_handler(commands=['balance', 'bal'])
    def cmd_balance(message):
        handle_balance_command(message, client)
    
    @bot.message_handler(commands=['add'])
    def cmd_add(message):
        handle_add_command(message, client)
    
    @bot.message_handler(commands=['del', 'remove'])
    def cmd_del(message):
        handle_del_command(message, client)
    
    @bot.message_handler(commands=['set'])
    def cmd_set(message):
        handle_set_command(message, client)
    
    @bot.message_handler(commands=['close'])
    def cmd_close(message):
        handle_close_command(message, client)
    
    @bot.message_handler(commands=['vault'])
    def cmd_vault(message):
        handle_vault_panel(message, client)
    
    @bot.message_handler(commands=['sentry'])
    def cmd_sentry(message):
        handle_sentry_panel(message, client)
    
    @bot.message_handler(commands=['settings', 'config'])
    def cmd_settings(message):
        show_settings_menu(message.chat.id, client=client)
    
    @bot.message_handler(commands=['strategy'])
    def cmd_strategy(message):
        show_strategy_center(message.chat.id, client)
    
    @bot.message_handler(commands=['prices', 'price'])
    def cmd_prices(message):
        show_real_time_prices(message.chat.id, client)
    
    @bot.message_handler(commands=['sync'])
    def cmd_sync(message):
        from trading_engine import sync_positions
        sync_positions(client, message.chat.id)
    
    @bot.message_handler(commands=['emergency', 'closeall'])
    def cmd_emergency(message):
        chat_id = message.chat.id
        markup = types.InlineKeyboardMarkup()
        markup.row(
            types.InlineKeyboardButton("✅ 确认全平", callback_data="emergency_close"),
            types.InlineKeyboardButton("❌ 取消", callback_data="back_to_main")
        )
        safe_send_message(chat_id,
            "⚠️ <b>危险操作确认</b>\n\n即将平掉所有持仓，请确认！",
            parse_mode="HTML", reply_markup=markup)
    
    @bot.message_handler(commands=['confirm'])
    def cmd_confirm(message):
        """处理 /confirm 命令 - 确认 AI 修改"""
        chat_id = message.chat.id
        parts = message.text.split()
        
        if len(parts) != 2:
            safe_send_message(
                chat_id,
                "❌ 格式错误。请使用: <code>/confirm TOKEN</code>",
                parse_mode="HTML"
            )
            return
        
        token = parts[1]
        
        try:
            success, command_data = get_override_manager().confirm_command(token)
            
            if not success or command_data is None:
                safe_send_message(
                    chat_id,
                    "❌ 无效的确认令牌或指令已过期",
                    parse_mode="HTML"
                )
                return
            
            # 将 AI 修改写入 SYSTEM_CONFIG
            with state_lock:
                for param_name, param_value in command_data.items():
                    if param_name in SYSTEM_CONFIG:
                        SYSTEM_CONFIG[param_name] = param_value
                        logger.info(f"✅ AI 修改已确认: {param_name} = {param_value}")
                
                save_data()
            
            msg = "✅ <b>授权成功，AI 修改已生效</b>\n\n"
            msg += "<b>已应用的修改:</b>\n"
            for param_name, param_value in command_data.items():
                msg += f"• {param_name}: <code>{param_value}</code>\n"
            
            safe_send_message(chat_id, msg, parse_mode="HTML")
            logger.info(f"✅ 用户确认 AI 修改: {command_data}")
            
        except Exception as e:
            logger.error(f"❌ 确认指令失败: {e}", exc_info=True)
            safe_send_message(
                chat_id,
                f"❌ 确认失败: {str(e)[:100]}",
                parse_mode="HTML"
            )
    
    @bot.message_handler(commands=['reject'])
    def cmd_reject(message):
        """处理 /reject 命令 - 拒绝 AI 修改"""
        chat_id = message.chat.id
        parts = message.text.split()
        
        if len(parts) != 2:
            safe_send_message(
                chat_id,
                "❌ 格式错误。请使用: <code>/reject TOKEN</code>",
                parse_mode="HTML"
            )
            return
        
        token = parts[1]
        
        try:
            success = get_override_manager().reject_command(token)
            
            if success:
                safe_send_message(
                    chat_id,
                    "❌ <b>已否决，保持人类原有设定</b>",
                    parse_mode="HTML"
                )
                logger.info(f"❌ 用户拒绝 AI 修改: {token}")
            else:
                safe_send_message(
                    chat_id,
                    "⚠️ 无效的令牌或指令已过期",
                    parse_mode="HTML"
                )
        except Exception as e:
            logger.error(f"❌ 拒绝指令失败: {e}", exc_info=True)
            safe_send_message(
                chat_id,
                f"❌ 拒绝失败: {str(e)[:100]}",
                parse_mode="HTML"
            )
    
    @bot.message_handler(commands=['add_sim', 'sub_sim'])
    def cmd_vault_redirect(message):
        """🔥 V8.0: /add_sim 和 /sub_sim 已迁移到 Vault Management GUI"""
        chat_id = message.chat.id
        safe_send_message(
            chat_id,
            "🔄 <b>命令已升级</b>\n\n"
            "💡 /add_sim 和 /sub_sim 已整合到 Vault Management GUI。\n"
            "正在为您打开 Vault 面板...",
            parse_mode="HTML"
        )
        show_vault_management(chat_id)
    
    @bot.message_handler(commands=['bill'])
    def cmd_bill(message):
        """处理 /bill 命令 - 查看沙盒账单历史"""
        chat_id = message.chat.id
        
        try:
            import json
            import os
            
            # 读取 sandbox_ledger.json
            ledger_file = "sandbox_ledger.json"
            
            if not os.path.exists(ledger_file):
                safe_send_message(
                    chat_id,
                    "📭 <b>暂无账单记录</b>\n\n沙盒账本文件不存在",
                    parse_mode="HTML"
                )
                return
            
            with open(ledger_file, 'r', encoding='utf-8') as f:
                ledger = json.load(f)
            
            current_balance = ledger.get('balance', 0.0)
            history = ledger.get('history', [])
            
            if not history:
                msg = "📭 <b>暂无账单记录</b>\n\n"
                msg += f"💰 当前余额: <code>${current_balance:.2f}</code>\n"
                safe_send_message(chat_id, msg, parse_mode="HTML")
                return
            
            # 构建账单消息（显示最近20条）
            msg = "📋 <b>沙盒账单历史</b>\n"
            msg += "━━━━━━━━━━━━━━━━━━━━\n\n"
            msg += f"💰 <b>当前余额:</b> <code>${current_balance:.2f}</code>\n"
            msg += f"📊 <b>历史记录:</b> {len(history)} 条\n\n"
            
            # 取最近20条记录
            recent_history = history[-20:] if len(history) > 20 else history
            
            msg += "<b>最近交易:</b>\n"
            for entry in reversed(recent_history):
                timestamp = entry.get('timestamp', '未知时间')
                amount = entry.get('amount', 0)
                reason = entry.get('reason', '未知原因')
                balance_after = entry.get('balance_after', 0)
                
                # 格式化时间（只显示日期和时间）
                try:
                    from datetime import datetime
                    dt = datetime.fromisoformat(timestamp)
                    time_str = dt.strftime('%m-%d %H:%M')
                except:
                    time_str = timestamp[-14:-7] if len(timestamp) > 14 else timestamp
                
                # 金额符号和emoji
                if amount > 0:
                    amount_emoji = "🟢"
                    amount_str = f"+${amount:.2f}"
                elif amount < 0:
                    amount_emoji = "🔴"
                    amount_str = f"-${abs(amount):.2f}"
                else:
                    amount_emoji = "⚪"
                    amount_str = f"${amount:.2f}"
                
                msg += f"{amount_emoji} <code>{time_str}</code> {amount_str}\n"
                msg += f"   {html.escape(reason[:30])}\n"
                msg += f"   余额: ${balance_after:.2f}\n\n"
            
            if len(history) > 20:
                msg += f"💡 仅显示最近20条，共{len(history)}条记录\n"
            
            msg += f"\n⏰ 查询时间: <i>{datetime.now().strftime('%H:%M:%S')}</i>"
            
            safe_send_message(chat_id, msg, parse_mode="HTML")
            logger.info(f"📋 /bill: 用户 {chat_id} 查看账单历史")
            
        except json.JSONDecodeError as e:
            logger.error(f"❌ JSON解析失败: {e}", exc_info=True)
            safe_send_message(
                chat_id,
                "❌ 账本文件格式错误，无法读取",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"❌ /bill 执行失败: {e}", exc_info=True)
            safe_send_message(
                chat_id,
                f"❌ 查询失败: {str(e)[:100]}",
                parse_mode="HTML"
            )
    
    @bot.message_handler(commands=['audit'])
    def cmd_audit(message):
        """
        处理 /audit 命令 - 强制执行完整技术和AI SMC分析
        
        功能：
        1. 获取当前1m/5m/15m K线数据
        2. 强制传递给AI分析师（绕过市场过滤器）
        3. 在Sandbox模式下输出###COMMAND###模拟交易
        """
        chat_id = message.chat.id
        parts = message.text.split()
        
        # 默认审计第一个监控币种，或用户指定币种
        if len(parts) >= 2:
            symbol = parts[1].upper()
            if not symbol.endswith('USDT'):
                symbol += 'USDT'
        else:
            # 使用第一个监控币种
            symbols = list(SYSTEM_CONFIG.get("ASSET_WEIGHTS", {}).keys())
            if not symbols:
                safe_send_message(
                    chat_id,
                    "❌ 没有监控币种。请先使用 /add 添加币种。",
                    parse_mode="HTML"
                )
                return
            symbol = symbols[0]
        
        safe_send_message(
            chat_id,
            f"🔍 <b>强制审计启动</b>\n\n"
            f"币种: {html.escape(symbol)}\n"
            f"模式: 完整技术+AI SMC分析\n"
            f"状态: 正在获取K线数据...",
            parse_mode="HTML"
        )
        
        try:
            from trading_engine import get_historical_klines, calculate_indicators, generate_trading_signals
            from ai_analyst import get_commander
            
            # Step 1: 获取多周期K线数据
            intervals = ['1m', '5m', '15m']
            kline_data = {}
            
            for interval in intervals:
                df = get_historical_klines(client, symbol, interval, limit=500)
                if df is not None and len(df) > 0:
                    df = calculate_indicators(df)
                    kline_data[interval] = df
            
            if not kline_data:
                safe_send_message(
                    chat_id,
                    f"❌ 无法获取 {html.escape(symbol)} 的K线数据",
                    parse_mode="HTML"
                )
                return
            
            # 使用15m作为主周期
            main_df = kline_data.get('15m')
            if main_df is None:
                safe_send_message(
                    chat_id,
                    "❌ 15m K线数据不可用",
                    parse_mode="HTML"
                )
                return
            
            # Step 2: 生成技术信号（强制模式，绕过过滤器）
            signals = generate_trading_signals(main_df, symbol, client=client)
            
            # 构建分析请求
            last_candle = main_df.iloc[-1]
            analysis_prompt = f"""# 🎯 强制审计请求 (/audit 命令)

## 交易对信息
- 币种: {symbol}
- 当前价格: ${last_candle['close']:.4f}
- 24h变化: {((last_candle['close'] - main_df.iloc[-24]['close']) / main_df.iloc[-24]['close'] * 100):.2f}%

## 技术指标快照
- MACD_hist: {last_candle.get('MACD_hist', 0):.4f}
- ADX: {last_candle.get('ADX', 0):.2f}
- RSI: {last_candle.get('RSI', 50):.2f}
- ATR: {last_candle.get('ATR', 0):.4f}
- Relative_ATR: {last_candle.get('Relative_ATR', 1.0):.2f}
- EMA_TREND: {last_candle.get('EMA_TREND', 0):.4f}
- Squeeze_On: {last_candle.get('Squeeze_On', False)}

## 审计要求
请执行完整的SMC（Smart Money Concepts）分析：
1. 识别当前市场结构（BOS/CHoCH）
2. 标记关键流动性区域
3. 评估订单块（Order Block）质量
4. 判断Fair Value Gap（FVG）
5. 给出明确的交易建议（BUY/SELL/HOLD）

⚠️ 这是强制审计，请忽略常规市场过滤器，提供完整分析。

## Sandbox模式指令
如果分析结果建议开仓，请在响应中包含 ###COMMAND### 块以触发模拟交易。
"""
            
            # Step 3: 调用AI分析师
            safe_send_message(
                chat_id,
                "🤖 <b>正在调用AI分析师...</b>\n\n"
                "⏳ 预计耗时: 30-60秒",
                parse_mode="HTML"
            )
            
            commander = get_commander()
            
            # 使用异步调用
            def run_audit_analysis():
                try:
                    import asyncio
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    try:
                        result = loop.run_until_complete(commander.ask_commander(analysis_prompt))
                        return result
                    finally:
                        loop.close()
                except Exception as e:
                    logger.error(f"❌ AI审计分析异常: {e}", exc_info=True)
                    return None
            
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(run_audit_analysis)
                try:
                    ai_response = future.result(timeout=180)
                except concurrent.futures.TimeoutError:
                    safe_send_message(
                        chat_id,
                        "⏱️ <b>AI分析超时</b>\n\n"
                        "分析时间超过3分钟，请稍后重试。",
                        parse_mode="HTML"
                    )
                    return
            
            if not ai_response:
                safe_send_message(
                    chat_id,
                    "❌ AI分析失败，请稍后重试。",
                    parse_mode="HTML"
                )
                return
            
            # Step 4: 处理AI响应
            if "###COMMAND###" in ai_response:
                # 解析并执行命令
                exec_result = commander.parse_and_execute(ai_response, client=client)
                
                # 发送执行结果
                safe_send_message(
                    chat_id,
                    f"✅ <b>审计完成 - 已执行交易指令</b>\n\n{exec_result['message']}",
                    parse_mode="HTML"
                )
            
            # 清理响应文本
            import re
            clean_response = re.sub(r'###COMMAND###.*?###COMMAND###', '', ai_response, flags=re.DOTALL).strip()
            clean_response = re.sub(r'```json.*?```', '', clean_response, flags=re.DOTALL).strip()
            clean_response = re.sub(r'\{[^{}]*\}', '', clean_response, flags=re.DOTALL).strip()
            
            # 发送分析报告
            if clean_response:
                report_msg = f"📊 <b>强制审计报告</b>\n"
                report_msg += f"━━━━━━━━━━━━━━━━━━━━\n\n"
                report_msg += f"🎯 币种: {html.escape(symbol)}\n"
                report_msg += f"⏰ 时间: {datetime.now().strftime('%H:%M:%S')}\n\n"
                report_msg += f"{clean_response[:3000]}"  # 限制长度
                
                safe_send_message(chat_id, report_msg, parse_mode="HTML")
            
            logger.info(f"✅ /audit 命令执行完成: {symbol} by {chat_id}")
            
        except Exception as e:
            logger.error(f"❌ /audit 命令执行失败: {e}", exc_info=True)
            safe_send_message(
                chat_id,
                f"❌ <b>审计执行失败</b>\n\n错误: {html.escape(str(e)[:200])}",
                parse_mode="HTML"
            )
    
    @bot.message_handler(commands=['help'])
    def cmd_help(message):
        chat_id = message.chat.id
        msg = "📖 <b>命令帮助</b>\n\n"
        msg += "<b>基础命令:</b>\n"
        msg += "/start - 显示主菜单\n"
        msg += "/dashboard - 实时仪表盘\n"
        msg += "/positions - 查看持仓\n"
        msg += "/balance - 账户余额\n"
        msg += "/prices - 实时价格\n\n"
        msg += "<b>交易命令:</b>\n"
        msg += "/add BTC 1000 - 添加监控币种\n"
        msg += "/del BTC - 移除监控币种\n"
        msg += "/close BTCUSDT - 平仓指定币种\n"
        msg += "/closeall - 一键全平\n"
        msg += "/sync - 同步真实仓位\n\n"
        msg += "<b>分析命令:</b>\n"
        msg += "/audit [币种] - 强制完整技术+AI分析\n\n"
        msg += "<b>沙盒管理:</b>\n"
        msg += "/add_sim / /sub_sim - 已整合到 🔐 Vault 管理 GUI\n"
        msg += "/bill - 查看账单历史\n\n"
        msg += "<b>设置命令:</b>\n"
        msg += "/set ADX_THR 30 - 修改参数\n"
        msg += "/strategy - 策略中心\n"
        msg += "/settings - 系统设置\n"
        msg += "/vault - 保险库管理\n"
        msg += "/sentry - 价格哨所\n\n"
        msg += "<b>审批命令:</b>\n"
        msg += "/confirm TOKEN - 确认 AI 修改\n"
        msg += "/reject TOKEN - 拒绝 AI 修改\n"
        safe_send_message(chat_id, msg, parse_mode="HTML")
    
    @bot.message_handler(content_types=['photo'])
    def handle_vision_request(message):
        """处理图片消息 - Claude 视觉审计"""
        chat_id = message.chat.id
        owner_chat_id = str(SYSTEM_CONFIG.get("TG_CHAT_ID", ""))
        
        # 鉴权检查
        if str(chat_id) != owner_chat_id:
            logger.warning(f"⛔ 触发越权拦截！陌生访客 [{chat_id}] 试图发送图片")
            safe_send_message(
                chat_id,
                "⛔ <b>访问被拒绝</b>\n\n您没有权限使用此机器人。",
                parse_mode="HTML"
            )
            return
        
        safe_send_message(chat_id, "🔍 <b>正在接入视觉神经审计Gemini 3 + Claude...</b>", parse_mode="HTML")
        
        try:
            # 下载图片
            file_info = bot.get_file(message.photo[-1].file_id)
            img_bytes = bot.download_file(file_info.file_path)
            
            # 驱动 Claude 视觉接口
            from ai_analyst import get_commander
            commander = get_commander()
            analysis = commander.analyze_chart_with_vision_bytes(
                img_bytes, 
                "统帅，请分析这张图。识别 SMC 结构，并根据你的猎人直觉给出调参建议。"
            )
            safe_send_message(chat_id, f"🧠 <b>视觉审计报告：</b>\n\n{analysis}", parse_mode="HTML")
            logger.info(f"✅ 视觉审计完成 for user {chat_id}")
        except Exception as e:
            logger.error(f"❌ 视觉系统故障: {e}", exc_info=True)
            safe_send_message(chat_id, f"❌ 视觉系统故障: {str(e)[:100]}", parse_mode="HTML")
    
    @bot.message_handler(func=lambda message: True)
    def handle_text(message):
        """处理普通文字消息（ReplyKeyboard按钮）"""
        # 鉴权检查
        chat_id = message.chat.id
        owner_chat_id = str(SYSTEM_CONFIG.get("TG_CHAT_ID", ""))
        
        if str(chat_id) != owner_chat_id:
            logger.warning(f"⛔ 触发越权拦截！陌生访客 [{chat_id}] 试图发送指令: {message.text}")
            safe_send_message(
                chat_id,
                "⛔ <b>访问被拒绝</b>\n\n您没有权限使用此机器人。",
                parse_mode="HTML"
            )
            return
        
        user_text = message.text
        
        # 🔥 Task 1: 子菜单深度路由 - 强制 Session 穿透
        # 任何以系统 Emoji 开头的按钮都必须强制清除 Session，防止卡在"等待输入"状态
        system_emoji_prefixes = [
            "📊", "💰", "🚀", "🤖", "📈", "🛑", "⚙️", "🔙", "▶️", "⏹️", 
            "🎯", "🏦", "🔭", "📋", "💼", "⚖️", "✅", "❌", "🧠", "🔍", 
            "🔥", "🔒", "🛡️", "📉", "💡", "🌊", "📏", "⏱️", "🔄", "🗑️"
        ]
        if any(user_text.startswith(emoji) for emoji in system_emoji_prefixes):
            config.clear_user_session(chat_id)
            logger.debug(f"🔓 强制清除用户 {chat_id} 的 Session 状态（系统按钮触发）")
        
        # ====== 主菜单按钮 (create_main_menu) - 新版6按钮菜单 ======
        # 🔥 处理带模式前缀的仪表盘按钮
        if user_text.endswith("📊 仪表盘"):  # 匹配 "🟡 [模拟] 📊 仪表盘" 或 "🔴 [实战] 📊 仪表盘"
            handle_dashboard(message, client)
        elif user_text == "⚖️ 深度对账":
            # 🔥 功能合并：先显示持仓，再自动触发同步
            handle_positions(message, client)
            try:
                from trading_engine import sync_positions
                sync_positions(client, chat_id)
            except Exception as e:
                logger.error(f"⚖️ 深度对账同步失败: {e}", exc_info=True)
        elif user_text == "🚀 启动/切换":
            show_launch_wizard(chat_id, client)
        elif user_text == "🤖 授权 AI 指挥":
            from bot_callbacks import show_ai_autonomy_confirm
            show_ai_autonomy_confirm(chat_id)
        elif user_text == "🧠 撤销 AI 接管":
            # 🔥 二次确认：显示撤销确认对话框
            msg = "⚠️ <b>撤销确认</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
            msg += "🔒 <b>即将撤销 AI 满血指挥权</b>\n\n"
            msg += "📊 <b>撤销后:</b>\n"
            msg += "• 系统将切换回手动决策模式\n"
            msg += "• AI 将继续提供分析建议\n"
            msg += "• 所有交易需您手动确认\n\n"
            msg += "❓ <b>确认撤销 AI 接管模式？</b>"
            
            markup = types.InlineKeyboardMarkup(row_width=2)
            markup.add(
                types.InlineKeyboardButton("✅ 确认撤销", callback_data="confirm_revoke_ai_autonomy"),
                types.InlineKeyboardButton("❌ 取消", callback_data="cancel_revoke_ai_autonomy")
            )
            
            safe_send_message(chat_id, msg, parse_mode="HTML", reply_markup=markup)
            logger.info(f"🔒 AI 满血接管模式撤销确认对话框已显示 to {chat_id}")
        elif user_text == "🏦 资产中心":
            # 显示资产管理面板
            from bot_handlers_additions import show_asset_settings_menu
            try:
                show_asset_settings_menu(chat_id, client)
            except Exception as e:
                logger.error(f"❌ 资产中心加载失败: {e}", exc_info=True)
                safe_send_message(chat_id, "⚠️ 资产中心暂时无法访问", parse_mode="HTML")
        elif user_text == "⚙️ 系统设置":
            show_settings_menu(chat_id, client=client)
        # ====== 旧版按钮兼容（逐步废弃） ======
        elif user_text == "💼 我的持仓":
            handle_positions(message, client)
        elif user_text == "▶️ 启动交易":
            show_launch_wizard(chat_id, client)
        elif user_text == "⏹️ 停止交易":
            # 停止交易引擎
            if config.TRADING_ENGINE_ACTIVE:
                config.TRADING_ENGINE_ACTIVE = False
                safe_send_message(chat_id, "⏹️ <b>交易引擎已停止</b>", parse_mode="HTML")
                logger.info(f"⏹️ 交易引擎已停止 by {chat_id}")
            else:
                safe_send_message(chat_id, "⚠️ 交易引擎未运行", parse_mode="HTML")
        elif user_text == "🎯 策略中心":
            show_strategy_center(chat_id, client)
        elif user_text == "🏦 保险库":
            handle_vault_panel(message, client)
        elif user_text == "🤖 自适应阈值" or user_text == "📌 固定阈值":
            toggle_vault_adapt(chat_id, message, client)
        elif user_text == "🔭 价格哨所":
            handle_sentry_panel(message, client)
        elif user_text == "📈 行情分析":
            show_real_time_prices(chat_id, client)
        elif user_text == "📋 交易记录":
            # 根据 RUNNING_MODE 显示不同的战报
            running_mode = SYSTEM_CONFIG.get("RUNNING_MODE", "SANDBOX")
            
            if running_mode == "SANDBOX":
                # 沙盒模式：显示沙盒演习战报
                msg = "📋 <b>沙盒演习战报</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
                msg += "🔍 <b>模式:</b> 沙盒演习\n\n"
            else:
                # 实盘模式：显示实盘战报
                msg = "📋 <b>实盘战报</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
                msg += "🔥 <b>模式:</b> 真实实盘\n\n"
            
            if len(TRADE_HISTORY) > 0:
                msg += f"📊 <b>交易统计</b>\n"
                msg += f"├ 总交易次数: <code>{len(TRADE_HISTORY)}</code>\n"
                wins = sum(1 for t in TRADE_HISTORY if t.get('pnl', 0) > 0)
                losses = sum(1 for t in TRADE_HISTORY if t.get('pnl', 0) < 0)
                win_rate = (wins / len(TRADE_HISTORY) * 100) if len(TRADE_HISTORY) > 0 else 0
                
                wr_emoji = "🟢" if win_rate >= 60 else "🟡" if win_rate >= 50 else "🔴"
                msg += f"├ 盈利/亏损: <code>{wins}/{losses}</code>\n"
                msg += f"└ 胜率: {wr_emoji} <code>{win_rate:.1f}%</code>\n\n"
                
                # 计算总盈亏
                total_pnl = sum(t.get('pnl', 0) for t in TRADE_HISTORY)
                pnl_emoji = "🟢" if total_pnl > 0 else "🔴" if total_pnl < 0 else "⚪"
                msg += f"💰 <b>累计盈亏:</b> {pnl_emoji} <code>${total_pnl:+.2f}</code>\n"
            else:
                msg += "📭 暂无交易记录\n"
            
            safe_send_message(chat_id, msg, parse_mode="HTML")
        elif user_text == "⚙️ 设置":
            show_settings_menu(chat_id, client=client)
        # ====== 交易控制菜单按钮 (create_trading_menu) ======
        elif user_text == "📈 查看持仓":
            handle_positions(message, client)
        elif user_text == "🚀 自动巡航开关":
            # 🔥 自动巡航开关（即 AI 自动调参）
            with state_lock:
                current_state = SYSTEM_CONFIG.get("AUTO_TUNE_ENABLED", False)
                SYSTEM_CONFIG["AUTO_TUNE_ENABLED"] = not current_state
                save_data()
                new_state = SYSTEM_CONFIG["AUTO_TUNE_ENABLED"]
            
            state_text = "开启" if new_state else "关闭"
            state_icon = "🟢" if new_state else "🔴"
            
            msg = f"� <b>自动巡航已{state_text}</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
            msg += f"<b>状态:</b> {state_icon} {state_text}\n\n"
            if new_state:
                msg += "✅ <b>引擎已启动</b>\n"
                msg += "├ 每15分钟评估市场状态\n"
                msg += "├ 自动微调策略参数\n"
                msg += "├ 冷却期: 2小时\n"
                msg += "└ 安全边界保护已激活\n\n"
                msg += "💡 AI将根据实时指标自动优化参数。"
            else:
                msg += "❌ <b>引擎已停止</b>\n\n"
                msg += "参数将保持手动设置，不再自动调整。"
            
            safe_send_message(chat_id, msg, parse_mode="HTML")
            logger.info(f"🚀 自动巡航已{state_text} by {chat_id}")
        elif user_text == "⚖️ 同步对账":
            from trading_engine import sync_positions
            sync_positions(client, chat_id)
        elif user_text == "切换至 🔥 真实实盘":
            # 🔥 Phase 4a: 环境锁 - DRY_RUN 严格绑定 RUNNING_MODE
            with state_lock:
                SYSTEM_CONFIG["RUNNING_MODE"] = "REAL"
                SYSTEM_CONFIG["DRY_RUN"] = False  # 🔒 REAL → DRY_RUN=False
                save_data()
            logger.info(f"🔒 环境锁生效：RUNNING_MODE=REAL → DRY_RUN=False")
            
            safe_send_message(chat_id,
                "🔥 <b>已切换到真实实盘模式</b>\n\n"
                "⚠️ <b>警告：系统将执行真实交易！</b>\n"
                f"• 杠杆倍数: {SYSTEM_CONFIG.get('LEVERAGE', 20)}x\n"
                f"• 当前策略: {STRATEGY_PRESETS.get(SYSTEM_CONFIG.get('STRATEGY_MODE', 'STANDARD'), {}).get('name', 'STANDARD')}\n\n"
                "💡 点击【切换至 🔍 模拟沙盒】可切换回安全模式。",
                parse_mode="HTML")
            logger.info(f"✅ 用户切换到真实实盘模式: {chat_id}")
        elif user_text == "切换至 🔍 模拟沙盒":
            # 🔥 Phase 4a: 环境锁 - DRY_RUN 严格绑定 RUNNING_MODE
            with state_lock:
                SYSTEM_CONFIG["RUNNING_MODE"] = "SANDBOX"
                SYSTEM_CONFIG["DRY_RUN"] = True  # 🔒 SANDBOX → DRY_RUN=True
                save_data()
            logger.info(f"🔒 环境锁生效：RUNNING_MODE=SANDBOX → DRY_RUN=True")
            
            safe_send_message(chat_id,
                "🔍 <b>已切换到模拟沙盒模式</b>\n\n"
                "• 所有交易将在沙盒环境中模拟执行\n"
                "• 适合策略测试和观察\n"
                "• 不会产生真实资金变动\n\n"
                "💡 点击【切换至 🔥 真实实盘】可切换到实盘。",
                parse_mode="HTML")
            logger.info(f"✅ 用户切换到模拟沙盒模式: {chat_id}")
        elif user_text == "🤖 激活满血 AI 指挥权":
            from bot_callbacks import show_ai_autonomy_confirm
            show_ai_autonomy_confirm(chat_id)
        elif user_text == "🧠 幽灵接管中 (点击撤销)":
            # 🔥 二次确认：显示撤销确认对话框
            msg = "⚠️ <b>撤销确认</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
            msg += "🔒 <b>即将撤销 AI 满血指挥权</b>\n\n"
            msg += "📊 <b>撤销后:</b>\n"
            msg += "• 系统将切换回手动决策模式\n"
            msg += "• AI 将继续提供分析建议\n"
            msg += "• 所有交易需您手动确认\n\n"
            msg += "❓ <b>确认撤销 AI 接管模式？</b>"
            
            markup = types.InlineKeyboardMarkup(row_width=2)
            markup.add(
                types.InlineKeyboardButton("✅ 确认撤销", callback_data="confirm_revoke_ai_autonomy"),
                types.InlineKeyboardButton("❌ 取消", callback_data="cancel_revoke_ai_autonomy")
            )
            
            safe_send_message(chat_id, msg, parse_mode="HTML", reply_markup=markup)
            logger.info(f"🔒 AI 满血接管模式撤销确认对话框已显示 to {chat_id}")
        elif user_text == "🛑 一键全平":
            cmd_emergency(message)
        elif user_text == "🔙 返回主菜单":
            handle_start_command(message)
        # ====== 保险库菜单按钮 (create_vault_menu) ======
        # ====== 保险库菜单按钮 (create_vault_menu) ======
        elif user_text == "✅ 开启保险库":
            config.clear_user_session(chat_id)  # 🔥 强制清除 Session
            enable_vault(chat_id)
        elif user_text == "❌ 关闭保险库":
            config.clear_user_session(chat_id)  # 🔥 强制清除 Session
            disable_vault(chat_id)
        elif user_text == "📊 保险库状态":
            config.clear_user_session(chat_id)  # 🔥 强制清除 Session
            show_vault_status(chat_id)
        elif user_text == "⚙️ 设置划转比例":
            config.clear_user_session(chat_id)  # 🔥 强制清除 Session
            ask_withdraw_ratio(chat_id)
        elif user_text == "💰 手动划转":
            config.clear_user_session(chat_id)  # 🔥 强制清除 Session
            manual_vault_transfer(chat_id, client)
        elif user_text == "💰 虚拟金补给":
            show_vault_management(chat_id)
        # 🔥 拦截所有 InlineKeyboard 回调文本，防止误触 AI 对话
        elif user_text.startswith(("🛡️ 保本止损", "🔥 强平此单", "🔄 刷新", "💼 持仓详情", 
                                   "📊 下载报表", "🔁 重置余额", "🗑️ 清空记录",
                                   "➕ 添加币种", "➖ 移除币种", "⏱️ 设置间隔", "📊 立即推送")):
            # 这些是 InlineKeyboard 按钮的文本，不应该被当作用户输入
            # 静默忽略，不发送给 AI
            logger.debug(f"🛡️ 拦截 InlineKeyboard 文本，防止误触 AI: {user_text}")
            return
        else:
            # 🔥 优化：检测币种查询，路由到 handle_market_query
            detected_symbol = _detect_coin_symbol(user_text)
            if detected_symbol:
                handle_market_query(chat_id, user_text, detected_symbol, client)
                return
            
            # 🔥 Task 2: 自由对话路由 (AI 全面接管) - 增强分析报告格式化
            bot = get_bot()
            if bot:
                bot.send_chat_action(chat_id, 'typing')
            
            # 🔥 修复方案：使用独立线程 + 超时保护 + 友好降级
            try:
                # 延迟初始化，避免循环导入
                from ai_analyst import AICommander
                current_commander = AICommander()
                
                # 🔥 方案1：使用 asyncio.run() 在独立线程中运行（避免事件循环冲突）                
                def run_ai_query():
                    """在独立线程中运行 AI 查询"""
                    try:
                        # 🔥 修复：使用 asyncio.run() 创建新的事件循环并等待协程完成
                        import asyncio
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        try:
                            result = loop.run_until_complete(current_commander.ask_commander(user_text))
                            return result
                        finally:
                            loop.close()
                    except Exception as e:
                        logger.error(f"❌ AI 查询内部异常: {e}", exc_info=True)
                        return None
                
                # 使用线程池执行，设置 180 秒超时（R1 + Claude 链式调用需要更长时间）
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(run_ai_query)
                    try:
                        ai_reply = future.result(timeout=180)
                    except concurrent.futures.TimeoutError:
                        # 超时友好提示
                        markup = types.InlineKeyboardMarkup()
                        markup.add(types.InlineKeyboardButton("🔙 返回主菜单", callback_data="back_to_main"))
                        safe_send_message(
                            chat_id,
                            "⏱️ <b>AI 分析超时</b>\n\n"
                            "当前市场数据量较大，AI 分析需要更多时间。\n\n"
                            "💡 <b>建议：</b>\n"
                            "• 简化您的问题\n"
                            "• 稍后重试\n"
                            "• 使用命令快捷操作（如 /dashboard）",
                            parse_mode="HTML",
                            reply_markup=markup
                        )
                        logger.warning(f"⏱️ AI 查询超时（180s）: {user_text[:50]}")
                        return

                if not ai_reply:
                    # 容错降级 - 提供返回主菜单按钮
                    markup = types.InlineKeyboardMarkup()
                    markup.add(types.InlineKeyboardButton("🔙 返回主菜单", callback_data="back_to_main"))
                    safe_send_message(
                        chat_id, 
                        "⚠️ AI 指挥官暂时无法响应，请稍后重试。",
                        reply_markup=markup
                    )
                    return

                # 解析指令与回执处理
                if "###COMMAND###" in ai_reply:
                    exec_result = current_commander.parse_and_execute(ai_reply)
                    safe_send_message(chat_id, exec_result['message'], parse_mode="HTML")

                # 🔥 Task 2: 格式化分析报告（非指挥模式）
                import re
                import json
                
                # 提取 JSON 数据（如果存在）
                json_match = re.search(r'```json\s*(\{.*?\})\s*```', ai_reply, re.DOTALL)
                analysis_data = None
                
                if json_match:
                    try:
                        analysis_data = json.loads(json_match.group(1))
                    except:
                        pass
                
                # 发送纯净文本给用户
                clean_text = re.sub(r'###COMMAND###.*', '', ai_reply, flags=re.DOTALL).strip()
                clean_text = re.sub(r'```json.*?```', '', clean_text, flags=re.DOTALL).strip()
                
                # 🔥 终极清理：移除所有花括号包裹的内容（确保只有人类语言）
                clean_text = re.sub(r'\{[^{}]*\}', '', clean_text, flags=re.DOTALL).strip()
                
                # 🔥 Task 2: 如果是分析模式且有 JSON 数据，格式化输出
                if analysis_data and not config.TRADING_ENGINE_ACTIVE:
                    formatted_report = _format_analysis_report(analysis_data, clean_text)
                    safe_send_message(chat_id, formatted_report, parse_mode="HTML")
                elif clean_text:
                    safe_send_message(chat_id, clean_text, parse_mode="HTML")
                    
            except concurrent.futures.TimeoutError:
                # 已在上面处理
                pass
            except Exception as e:
                logger.error(f"AI 响应失败: {e}", exc_info=True)
                # 容错降级 - 提供返回主菜单按钮
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("🔙 返回主菜单", callback_data="back_to_main"))
                
                # 根据错误类型提供不同的提示
                error_msg = str(e)
                if "timeout" in error_msg.lower():
                    msg = "⏱️ <b>AI 响应超时</b>\n\n请稍后重试或简化问题。"
                elif "connection" in error_msg.lower() or "network" in error_msg.lower():
                    msg = "🌐 <b>网络连接异常</b>\n\n请检查网络状态后重试。"
                else:
                    msg = f"❌ <b>系统错误</b>\n\n{error_msg[:100]}"
                
                safe_send_message(
                    chat_id, 
                    msg,
                    parse_mode="HTML",
                    reply_markup=markup
                )
    
    # 注册callback_query_handler
    from bot_callbacks import handle_callback
    # ==========================================
    # 🔥 V7.0 超级泛型参数修改器 (拦截所有 param_ 开头的按钮)
    # ==========================================
    @bot.callback_query_handler(func=lambda call: call.data.startswith('param_'))
    def handle_generic_param_click(call):
        import config
        from utils import safe_send_message, safe_answer_callback, get_bot
        
        chat_id = call.message.chat.id
        
        # 🔥 Phase 4b: 强制清除 Session，防止状态死锁
        config.clear_user_session(chat_id)
        
        # 1. 提取要修改的参数名 (例如从 'param_ADX_THR' 提取出 'ADX_THR')
        param_key = call.data.replace('param_', '')
        current_val = config.SYSTEM_CONFIG.get(param_key, '未知')
        
        # 2. 消除按钮上的转圈加载动画
        safe_answer_callback(call.id)
        
        # 3. 提示用户输入
        msg = f"⚙️ <b>修改参数: {param_key}</b>\n━━━━━━���━━━━━━━━━━━━━\n\n"
        msg += f"当前数值: <code>{current_val}</code>\n\n"
        msg += "✍️ <b>请直接回复新的数值：</b>\n"
        msg += "<i>(回复 'q' 或 '取消' 放弃修改)</i>"
        
        sent_msg = safe_send_message(chat_id, msg, parse_mode="HTML")
        
        # 4. 挂起等待，把用户的下一句话交给 process_generic_param_input 处理
        current_bot = get_bot()
        current_bot.register_next_step_handler(sent_msg, process_generic_param_input, param_key)

    def process_generic_param_input(message, param_key):
        """处理用户回复的具体数值，并同步到多进程共享字典"""
        import config
        from utils import safe_send_message
        from human_override import get_override_manager
        
        chat_id = message.chat.id
        user_input = message.text.strip()
        
        if user_input.lower() in ['取消', 'cancel', 'q']:
            config.clear_user_session(chat_id)
            safe_send_message(chat_id, "❌ 已取消参数修改。")
            return
            
        try:
            original_val = config.SYSTEM_CONFIG.get(param_key)
            if isinstance(original_val, int) and not isinstance(original_val, bool):
                new_val = int(float(user_input))
            elif isinstance(original_val, float):
                new_val = float(user_input)
            elif isinstance(original_val, str):
                new_val = str(user_input)
            else:
                new_val = float(user_input)
            
            config.SYSTEM_CONFIG[param_key] = new_val
            config.SYSTEM_CONFIG["IS_CUSTOM_MODE"] = True
            config.save_data()
            
            get_override_manager().lock_parameter(param_key, new_val, reason="Telegram 参数调优面板修改")
            config.clear_user_session(chat_id)
            
            markup = types.InlineKeyboardMarkup(row_width=2)
            markup.add(
                types.InlineKeyboardButton("🔙 返回参数调优", callback_data="show_settings"),
                types.InlineKeyboardButton("🏠 返回主控台", callback_data="back_to_main")
            )
            
            msg = f"✅ <b>{param_key}</b> 已修改为: <code>{new_val}</code>\n📌 已标记为自定义模式\n💡 <i>下一个 K 线周期将应用新参数</i>"
            safe_send_message(chat_id, msg, parse_mode="HTML", reply_markup=markup)
            logger.info(f"✅ 参数修改: {param_key} = {new_val} by {chat_id}")
            
        except ValueError:
            safe_send_message(chat_id, "❌ 格式错误！请输入有效的纯数字。", parse_mode="HTML")

    # ==========================================
    # 🚀 新增功能：杠杆、权重、火力控制
    # ==========================================
    @bot.callback_query_handler(func=lambda call: call.data.startswith("set_leverage_"))
    def handle_leverage_change(call):
        try:
            new_leverage = int(call.data.split("_")[-1])
            from config import update_config_param
            success, msg = update_config_param("LEVERAGE", new_leverage)
            if success:
                bot.answer_callback_query(call.id, f"✅ 杠杆已调整为 {new_leverage}x")
                bot.edit_message_text(
                    chat_id=call.message.chat.id, message_id=call.message.message_id,
                    text=f"⚙️ <b>系统参数已动态同步</b>\n\n🔹 当前杠杆: <code>{new_leverage}x</code>\n🔹 状态: 🟢 全进程已生效", parse_mode="HTML"
                )
            else:
                bot.answer_callback_query(call.id, f"❌ 调整失败: {msg}")
        except Exception as e:
            logger.error(f"❌ 杠杆调整回调异常: {e}")

    @bot.callback_query_handler(func=lambda call: call.data == "adjust_btc_weight")
    def ask_btc_weight(call):
        from config import set_user_session
        set_user_session(call.message.chat.id, "EXPECT_BTC_WEIGHT")
        bot.send_message(call.message.chat.id, "⚖️ 请输入新的 BTCUSDT 权重 (0.1 ~ 1.0):")

    @bot.message_handler(func=lambda msg: config.get_user_session(msg.chat.id) and config.get_user_session(msg.chat.id).get('expected_input_type') == "EXPECT_BTC_WEIGHT")
    def handle_weight_input(msg):
        from config import clear_user_session, save_data
        try:
            new_weight = float(msg.text)
            if not (0.1 <= new_weight <= 1.0): raise ValueError()
            with config.config_lock:
                current_weights = dict(config.SYSTEM_CONFIG["ASSET_WEIGHTS"])
                current_weights["BTCUSDT"] = new_weight
                config.SYSTEM_CONFIG["ASSET_WEIGHTS"] = current_weights
            save_data()
            bot.send_message(msg.chat.id, f"✅ BTCUSDT 权重已更新为: {new_weight}")
        except ValueError:
            bot.send_message(msg.chat.id, "❌ 请输入 0.1 到 1.0 之间的纯数字。")
        finally:
            clear_user_session(msg.chat.id)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("boost_fire_"))
    def handle_boost_fire(call):
        try:
            mult = float(call.data.split("_")[-1])
            from config import update_config_param
            update_config_param("MANUAL_BOOST_MULT", mult)
            update_config_param("FORCE_MAD_DOG_ACTIVE", True if mult > 1.0 else False)
            status_text = "🔥 疯狗模式已激活" if mult > 1.0 else "🟢 常规平稳模式"
            bot.answer_callback_query(call.id, f"✅ 火力已调整为 {mult}x")
            bot.edit_message_text(
                chat_id=call.message.chat.id, message_id=call.message.message_id,
                text=f"☢️ <b>火力控制中枢</b>\n\n当前乘数: <code>{mult}x</code>\n战斗状态: <b>{status_text}</b>", parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"❌ 火力调整异常: {e}")

    # ==========================================
    # 🔥 V7.0 回调路由总闸
    # ==========================================
    @bot.callback_query_handler(func=lambda call: True)
    def global_callback_router(call):
        from bot_callbacks import handle_callback
        handle_callback(call, client)

# <--- 注意：到这里 register_handlers 函数已经结束，所以下面的代码顶格写
logger.info("✅ 消息处理器与 V7.0 路由总闸注册完成")

# ==========================================
# 🔥 V8.0 Vault Management GUI (替代手动余额命令)
# ==========================================

def show_vault_management(chat_id, message_id=None):
    """🏦 Vault Management GUI"""
    # 🔥 核心修复：直接读取交易引擎的真实物理沙盒账本
    from trading_engine import get_sandbox_balance
    ledger = get_sandbox_balance()
    sim_balance = float(ledger.get("balance", 10000.0))
    
    sim_hwm = float(SYSTEM_CONFIG.get("SIM_HIGH_WATER_MARK", sim_balance))
    sim_initial = float(SYSTEM_CONFIG.get("SANDBOX_INITIAL_BALANCE", 10000.0))

    # 计算回撤百分比
    if sim_hwm > 0:
        drawdown_pct = ((sim_hwm - sim_balance) / sim_hwm) * 100
    else:
        drawdown_pct = 0.0
    drawdown_pct = max(drawdown_pct, 0.0)  # 不显示负回撤

    # 回撤状态指示
    if drawdown_pct < 3:
        dd_emoji = "🟢"
        dd_status = "安全"
    elif drawdown_pct < 7:
        dd_emoji = "🟡"
        dd_status = "注意"
    else:
        dd_emoji = "🔴"
        dd_status = "危险"

    # 引擎状态
    engine_status = "🟢 运行中" if config.TRADING_ENGINE_ACTIVE else "🔴 已停止"

    pnl = sim_balance - sim_initial
    pnl_emoji = "🟢" if pnl > 0 else "🔴" if pnl < 0 else "⚪"

    msg = "🏦 <b>Vault Management</b>\n"
    msg += "━━━━━━━━━━━━━━━━━━━━\n\n"
    msg += f"💰 <b>SIM 当前余额:</b> <code>${sim_balance:.2f}</code>\n"
    msg += f"📈 <b>SIM 高水位线:</b> <code>${sim_hwm:.2f}</code>\n"
    msg += f"📊 <b>SIM 初始本金:</b> <code>${sim_initial:.2f}</code>\n"
    msg += f"├ 累计盈亏: {pnl_emoji} <code>${pnl:+.2f}</code>\n"
    msg += f"└ 当前回撤: {dd_emoji} <code>{drawdown_pct:.2f}%</code> ({dd_status})\n\n"
    msg += f"⚡ 引擎状态: {engine_status}\n\n"
    msg += "👇 选择操作："

    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("🚨 Emergency Reset (重置为 $10,000)", callback_data="vault_emergency_reset"),
        types.InlineKeyboardButton("🔄 Sync HWM → 当前余额", callback_data="vault_sync_hwm"),
        types.InlineKeyboardButton("✏️ Custom Amount (自定义金额)", callback_data="vault_custom_input"),
    )
    markup.add(types.InlineKeyboardButton("🔙 返回主菜单", callback_data="back_to_main"))

    if message_id:
        safe_edit_message(chat_id, message_id, msg, parse_mode="HTML", reply_markup=markup)
    else:
        safe_send_message(chat_id, msg, parse_mode="HTML", reply_markup=markup)


def _vault_emergency_reset(chat_id, message_id=None):
    """
    🚨 Emergency Reset:
    - SIM_CURRENT_BALANCE = 10000
    - SIM_INITIAL_BALANCE = 10000
    - SIM_HIGH_WATER_MARK = 10000
    - 清除因回撤导致的引擎停止标志
    """
    with state_lock:
        SYSTEM_CONFIG["SIM_CURRENT_BALANCE"] = 10000.0
        SYSTEM_CONFIG["SIM_INITIAL_BALANCE"] = 10000.0
        SYSTEM_CONFIG["SIM_HIGH_WATER_MARK"] = 10000.0
        save_data()

    # 同步风控管理器的 sim_high_water_mark
    try:
        from risk_manager import get_risk_manager
        rm = get_risk_manager()
        rm.sim_high_water_mark = 10000.0
        rm._save_hwm()
    except Exception as e:
        logger.warning(f"⚠️ 同步风控 HWM 失败: {e}")

    # 清除因回撤导致的引擎停止 → 重新激活引擎
    if not config.TRADING_ENGINE_ACTIVE:
        config.TRADING_ENGINE_ACTIVE = True
        logger.info("🔄 Emergency Reset: 引擎已重新激活（清除回撤熔断）")

    logger.info(f"🚨 Vault Emergency Reset 执行完成 by {chat_id}")

    # 刷新面板
    show_vault_management(chat_id, message_id)


def _vault_sync_hwm(chat_id, message_id=None):
    """
    🔄 Sync HWM: 强制设置 SIM_HIGH_WATER_MARK = SIM_CURRENT_BALANCE
    立即消除回撤告警
    """
    sim_balance = float(SYSTEM_CONFIG.get("SIM_CURRENT_BALANCE", 0.0))

    with state_lock:
        SYSTEM_CONFIG["SIM_HIGH_WATER_MARK"] = sim_balance
        save_data()

    # 同步风控管理器
    try:
        from risk_manager import get_risk_manager
        rm = get_risk_manager()
        rm.sim_high_water_mark = sim_balance
        rm._save_hwm()
    except Exception as e:
        logger.warning(f"⚠️ 同步风控 HWM 失败: {e}")

    logger.info(f"🔄 Vault Sync HWM: SIM_HIGH_WATER_MARK → ${sim_balance:.2f} by {chat_id}")

    # 刷新面板
    show_vault_management(chat_id, message_id)


def _vault_custom_input_prompt(chat_id):
    """
    ✏️ Custom Amount: 提示用户输入自定义金额
    使用 register_next_step_handler 等待用户输入
    """
    bot = get_bot()
    if not bot:
        return

    current_balance = float(SYSTEM_CONFIG.get("SIM_CURRENT_BALANCE", 0.0))
    msg = "✏️ <b>自定义 Vault 金额</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
    msg += f"当前余额: <code>${current_balance:.2f}</code>\n\n"
    msg += "✍️ <b>请输入新的余额数值 (例如: 5000):</b>\n"
    msg += "<i>回复 <code>取消</code> 放弃操作</i>"

    sent_msg = safe_send_message(chat_id, msg, parse_mode="HTML")
    if sent_msg:
        bot.register_next_step_handler(sent_msg, _process_vault_custom_input)


def _process_vault_custom_input(message):
    """处理用户输入的自定义 Vault 金额"""
    chat_id = message.chat.id
    user_input = message.text.strip()

    if user_input.lower() in ['取消', 'cancel', 'q']:
        config.clear_user_session(chat_id)
        safe_send_message(chat_id, "❌ 已取消自定义金额设置。")
        return

    try:
        new_balance = float(user_input)
        if new_balance < 0:
            safe_send_message(chat_id, "❌ 金额不能为负数！", parse_mode="HTML")
            return

        old_balance = float(SYSTEM_CONFIG.get("SIM_CURRENT_BALANCE", 0.0))

        with state_lock:
            SYSTEM_CONFIG["SIM_CURRENT_BALANCE"] = new_balance
            # 如果新余额高于 HWM，同步更新 HWM
            current_hwm = float(SYSTEM_CONFIG.get("SIM_HIGH_WATER_MARK", 0.0))
            if new_balance > current_hwm:
                SYSTEM_CONFIG["SIM_HIGH_WATER_MARK"] = new_balance
            save_data()

        # 同步风控管理器
        try:
            from risk_manager import get_risk_manager
            rm = get_risk_manager()
            if new_balance > rm.sim_high_water_mark:
                rm.sim_high_water_mark = new_balance
                rm._save_hwm()
        except Exception as e:
            logger.warning(f"⚠️ 同步风控 HWM 失败: {e}")

        config.clear_user_session(chat_id)

        diff = new_balance - old_balance
        diff_emoji = "🟢" if diff > 0 else "🔴" if diff < 0 else "⚪"

        msg = "✅ <b>Vault 余额已更新</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
        msg += f"旧余额: <code>${old_balance:.2f}</code>\n"
        msg += f"新余额: <code>${new_balance:.2f}</code>\n"
        msg += f"变动: {diff_emoji} <code>${diff:+.2f}</code>"

        safe_send_message(chat_id, msg, parse_mode="HTML")
        logger.info(f"✏️ Vault Custom: ${old_balance:.2f} → ${new_balance:.2f} by {chat_id}")

        # 显示 Vault 面板
        show_vault_management(chat_id)

    except ValueError:
        safe_send_message(chat_id, "❌ 输入格式错误！请输入有效的数字。", parse_mode="HTML")


# ==========================================
# 保险库管理函数
# ==========================================

def enable_vault(chat_id):
    """启用保险库"""
    with state_lock:
        SYSTEM_CONFIG["VAULT_ENABLED"] = True
        save_data()
    
    msg = "✅ <b>保险库已启用</b>\n\n"
    msg += f"触发阈值: ${SYSTEM_CONFIG['VAULT_THR']:.2f}\n"
    msg += f"划转比例: {SYSTEM_CONFIG['WITHDRAW_RATIO']*100:.0f}%\n\n"
    msg += "💡 当合约账户净利润达到阈值时，系统将自动划转到现货账户。"
    
    safe_send_message(chat_id, msg, parse_mode="HTML")
    logger.info(f"保险库已启用 by {chat_id}")


def disable_vault(chat_id):
    """禁用保险库"""
    with state_lock:
        SYSTEM_CONFIG["VAULT_ENABLED"] = False
        save_data()
    
    msg = "❌ <b>保险库已禁用</b>\n\n"
    msg += "⚠️ 系统将不再自动划转利润到现货账户。"
    
    safe_send_message(chat_id, msg, parse_mode="HTML")
    logger.info(f"保险库已禁用 by {chat_id}")


def show_vault_status(chat_id):
    """显示保险库状态详情"""
    vault_enabled = SYSTEM_CONFIG.get("VAULT_ENABLED", False)
    vault_balance = SYSTEM_CONFIG.get("VAULT_BALANCE", 0.0)
    vault_thr = SYSTEM_CONFIG.get("VAULT_THR", 250.0)
    withdraw_ratio = SYSTEM_CONFIG.get("WITHDRAW_RATIO", 0.5)
    benchmark = SYSTEM_CONFIG.get("BENCHMARK_CASH", 1800.0)
    
    msg = "🏦 <b>保险库状态详情</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
    msg += f"<b>运行状态:</b> {'✅ 已启用' if vault_enabled else '❌ 已禁用'}\n"
    msg += f"<b>累计保险库余额:</b> <code>${vault_balance:.2f}</code>\n"
    msg += f"<b>触发阈值:</b> <code>${vault_thr:.2f}</code>\n"
    msg += f"<b>划转比例:</b> <code>{withdraw_ratio*100:.0f}%</code>\n"
    msg += f"<b>基准本金:</b> <code>${benchmark:.2f}</code>\n\n"
    
    msg += "📊 <b>工作原理:</b>\n"
    msg += "1. 系统监控合约账户净利润\n"
    msg += "2. 当净利润 ≥ 触发阈值时自动划转\n"
    msg += f"3. 划转金额 = 净利润 × {withdraw_ratio*100:.0f}%\n"
    msg += "4. 划转后基准本金自动上调\n\n"
    
    msg += "💡 <b>提示:</b> 保险库功能可保护已实现利润，降低回撤风险。"
    
    safe_send_message(chat_id, msg, parse_mode="HTML")


def ask_withdraw_ratio(chat_id):
    """引导用户输入新的提取比例"""
    bot = get_bot()
    if bot is None:
        return
    
    current_ratio = SYSTEM_CONFIG.get("WITHDRAW_RATIO", 0.5)
    
    msg = "⚙️ <b>设置保险库划转比例</b>\n\n"
    msg += f"<b>当前比例:</b> <code>{current_ratio*100:.0f}%</code>\n\n"
    msg += "<b>允许范围:</b> 1% - 100%\n"
    msg += "<b>建议值:</b> 50% (平衡保护与复利)\n\n"
    msg += "✍️ <b>请输入新的划转比例 (1-100):</b>\n"
    msg += "<i>或回复 <code>取消</code> 返回</i>"
    
    sent_msg = safe_send_message(chat_id, msg, parse_mode="HTML")
    if sent_msg:
        bot.register_next_step_handler(sent_msg, process_withdraw_ratio_input)


def process_withdraw_ratio_input(message):
    """处理用户输入的划转比例"""
    chat_id = message.chat.id
    user_input = message.text.strip()
    
    if user_input in ['取消', 'cancel', 'Cancel']:
        safe_send_message(chat_id, "❌ 已取消设置", parse_mode="HTML")
        return
    
    try:
        ratio_percent = float(user_input)
        
        if not (1 <= ratio_percent <= 100):
            safe_send_message(
                chat_id,
                "❌ 输入超出范围！请输入 1-100 之间的数值。",
                parse_mode="HTML"
            )
            return
        
        ratio_decimal = ratio_percent / 100.0
        
        with state_lock:
            SYSTEM_CONFIG["WITHDRAW_RATIO"] = ratio_decimal
            save_data()
        
        msg = f"✅ <b>划转比例已更新</b>\n\n"
        msg += f"<b>新比例:</b> <code>{ratio_percent:.0f}%</code>\n\n"
        msg += f"💡 下次触发时将按此比例划转利润。"
        
        safe_send_message(chat_id, msg, parse_mode="HTML")
        logger.info(f"保险库划转比例已更新为 {ratio_percent}% by {chat_id}")
        
    except ValueError:
        safe_send_message(
            chat_id,
            "❌ 输入格式错误！请输入有效的数字 (1-100)。",
            parse_mode="HTML"
        )


def process_sim_balance_input(message):
    """处理用户输入的虚拟金补给数值"""
    chat_id = message.chat.id
    user_input = message.text.strip()
    
    if user_input in ['取消', 'cancel', 'Cancel']:
        config.clear_user_session(chat_id)
        safe_send_message(chat_id, "❌ 已取消虚拟金补给", parse_mode="HTML")
        return
    
    try:
        new_balance = float(user_input)
        
        if new_balance < 0:
            safe_send_message(
                chat_id,
                "❌ 余额不能为负数！请输入有效的正数。",
                parse_mode="HTML"
            )
            return
        
        old_balance = SYSTEM_CONFIG.get("SIM_CURRENT_BALANCE", 0.0)
        
        with state_lock:
            SYSTEM_CONFIG["SIM_CURRENT_BALANCE"] = new_balance
            save_data()
        
        config.clear_user_session(chat_id)
        
        diff = new_balance - old_balance
        diff_emoji = "🟢" if diff > 0 else "🔴" if diff < 0 else "⚪"
        
        msg = "✅ <b>虚拟金补给成功</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
        msg += f"<b>旧余额:</b> <code>${old_balance:.2f}</code>\n"
        msg += f"<b>新余额:</b> <code>${new_balance:.2f}</code>\n"
        msg += f"<b>变动:</b> {diff_emoji} <code>${diff:+.2f}</code>\n\n"
        msg += "💡 沙盒余额已更新，可继续模拟交易。"
        
        safe_send_message(chat_id, msg, parse_mode="HTML")
        logger.info(f"💰 虚拟金补给: {old_balance} → {new_balance} by {chat_id}")
        
    except ValueError:
        safe_send_message(
            chat_id,
            "❌ 输入格式错误！请输入有效的数字。",
            parse_mode="HTML"
        )


def manual_vault_transfer(chat_id, client):
    """手动触发保险库划转"""
    try:
        if client and not config.VERIFICATION_MODE:
            acc = client.futures_account()
            balance = float(acc['totalMarginBalance'])
        else:
            balance = SYSTEM_CONFIG.get("BENCHMARK_CASH", 1800.0)
        
        benchmark = SYSTEM_CONFIG.get("BENCHMARK_CASH", 1800.0)
        current_profit = balance - benchmark
        withdraw_ratio = SYSTEM_CONFIG.get("WITHDRAW_RATIO", 0.5)
        
        if current_profit <= 0:
            safe_send_message(
                chat_id,
                "⚠️ <b>当前无可划转利润</b>\n\n请等待账户产生净利润后再试。",
                parse_mode="HTML"
            )
            return
        
        transfer_amount = current_profit * withdraw_ratio
        
        msg = f"💰 <b>手动划转确认</b>\n\n"
        msg += f"当前净利润: <code>${current_profit:.2f}</code>\n"
        msg += f"划转比例: <code>{withdraw_ratio*100:.0f}%</code>\n"
        msg += f"划转金额: <code>${transfer_amount:.2f}</code>\n\n"
        msg += "⚠️ 确认执行划转？"
        
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton("✅ 确认", callback_data="confirm_manual_transfer"),
            types.InlineKeyboardButton("❌ 取消", callback_data="back_to_vault")
        )
        
        safe_send_message(chat_id, msg, parse_mode="HTML", reply_markup=markup)
        
    except Exception as e:
        logger.error(f"手动划转执行失败: {e}", exc_info=True)
        safe_send_message(
            chat_id,
            f"❌ <b>划转执行失败</b>\n\n错误: {str(e)[:100]}",
            parse_mode="HTML"
        )


def toggle_vault_adapt(chat_id, message, client):
    """切换保险库自适应阈值模式"""
    from config import state_lock, save_data
    
    current_state = SYSTEM_CONFIG.get("VAULT_AUTO_ADAPT", True)
    new_state = not current_state
    
    with state_lock:
        SYSTEM_CONFIG["VAULT_AUTO_ADAPT"] = new_state
        save_data()
    
    mode_name = "🤖 自适应阈值模式" if new_state else "📌 固定阈值模式"
    mode_desc = (
        "系统将根据凯利系数和回撤率自动调节触发比例" if new_state 
        else "系统将使用固定的触发阈值 (VAULT_THR)"
    )
    
    msg = f"✅ <b>已切换到{mode_name}</b>\n\n"
    msg += f"📝 <b>说明:</b> {mode_desc}\n\n"
    
    if new_state:
        base_ratio = SYSTEM_CONFIG.get("VAULT_BASE_RATIO", 0.15)
        min_ratio = SYSTEM_CONFIG.get("VAULT_MIN_RATIO", 0.05)
        max_ratio = SYSTEM_CONFIG.get("VAULT_MAX_RATIO", 0.30)
        
        msg += f"⚙️ <b>自适应参数:</b>\n"
        msg += f"├ 基准比例: <code>{base_ratio*100:.1f}%</code>\n"
        msg += f"├ 最低比例: <code>{min_ratio*100:.1f}%</code>\n"
        msg += f"└ 最高比例: <code>{max_ratio*100:.1f}%</code>\n\n"
        msg += f"💡 AI将根据市场状态在 {min_ratio*100:.0f}%-{max_ratio*100:.0f}% 区间动态调节"
    else:
        vault_thr = SYSTEM_CONFIG.get("VAULT_THR", 250.0)
        msg += f"⚙️ <b>固定阈值:</b> <code>${vault_thr:.2f}</code>\n\n"
        msg += f"💡 当净利润达到 ${vault_thr:.2f} 时触发划转"
    
    safe_send_message(chat_id, msg, parse_mode="HTML")
    logger.info(f"保险库自适应模式已切换为: {new_state} by {chat_id}")
    
    # 刷新保险库面板
    handle_vault_panel(message, client)


# ==========================================
# 🔥 AI 自适应巡航调参 - 静默授权执行
# ==========================================

def execute_auto_tune(ai_json):
    """
    🔥 弹性边界引擎版 AI 自动调参（静默授权，无需 /confirm）
    
    双重校验流水线：
    1. 🛡️ 人工锁校验：is_locked → rejected_by_human（物理拦截，强制跳过）
    2. 🌊 弹性边界钳制：get_elastic_boundaries → clamp 到 [min, max]（不拒绝，只修正）
    3. 应用参数：state_lock 保护下写入 SYSTEM_CONFIG + save_data()
    4. 战报生成：分段展示 🔒拦截 / ⚠️修正 / ✅成功
    
    Args:
        ai_json: AI 返回的 JSON 数据，格式：
        {
            "need_tune": true,
            "tune_params": {"ADX_THR": 10, "ATR_MULT": 2.5, ...},
            "reasoning": "当前波动率上升，建议放宽ATR倍数..."
        }
    
    Returns:
        dict: {'success': bool, 'message': str, 'applied_params': dict}
    """
    try:
        tune_params = ai_json.get('tune_params', {})
        reasoning = ai_json.get('reasoning', '无说明')
        
        if not tune_params:
            return {'success': False, 'message': 'AI 未提供调参建议', 'applied_params': {}}
        
        # 🔥 Step 1: 获取弹性边界（根据当前 MARKET_REGIME + Overdrive 状态动态计算）
        from config import AUTO_TUNE_FORBIDDEN_PARAMS
        elastic_bounds = get_elastic_boundaries(SYSTEM_CONFIG)
        
        regime = str(SYSTEM_CONFIG.get("MARKET_REGIME", "NORMAL")).split('|')[0].strip()
        is_overdrive = SYSTEM_CONFIG.get("AI_FULL_AUTONOMY_MODE", False)
        
        # 🔥 Step 2: 获取人工锁管理器
        override_mgr = get_override_manager()
        
        # 校验结果容器
        rejected_by_human = []   # 🛡️ 人工锁拦截的参数
        corrected_params = {}    # 🌊 弹性边界钳制修正的参数
        approved_params = {}     # ✅ 最终通过校验、待写入的参数
        old_values = {}          # 变更前的旧值（用于战报）
        
        for param_name, param_value in tune_params.items():
            # ── 🛡️ 第一关：人工锁校验（物理拦截） ──
            if override_mgr.is_locked(param_name):
                rejected_by_human.append(param_name)
                continue  # 强制跳过，不进入后续流程
            
            # 禁止修改列表也视为拦截
            if param_name in AUTO_TUNE_FORBIDDEN_PARAMS:
                rejected_by_human.append(param_name)
                continue
            
            # 类型安全转换
            try:
                param_value = float(param_value)
            except (ValueError, TypeError):
                rejected_by_human.append(param_name)
                continue
            
            # ── 🌊 第二关：弹性边界钳制 (Clamp) ──
            if param_name in elastic_bounds:
                bounds = elastic_bounds[param_name]
                min_val = bounds["min"]
                max_val = bounds["max"]
                
                if param_value < min_val:
                    corrected_params[param_name] = {
                        'original': param_value, 'corrected': min_val,
                        'reason': f'低于 {regime} 下限 {min_val}'
                    }
                    param_value = min_val
                elif param_value > max_val:
                    corrected_params[param_name] = {
                        'original': param_value, 'corrected': max_val,
                        'reason': f'超过 {regime} 上限 {max_val}'
                    }
                    param_value = max_val
            
            # 校验通过，加入待写入队列
            if param_name in SYSTEM_CONFIG:
                old_values[param_name] = SYSTEM_CONFIG[param_name]
                approved_params[param_name] = param_value
        
        # 全部被拦截 → 无参数可调
        if not approved_params:
            info_parts = []
            if rejected_by_human:
                info_parts.append(f"🔒 人工锁拦截: {', '.join(rejected_by_human)}")
            error_msg = f"⚠️ <b>AI 调参全部被拦截</b>\n\n{' | '.join(info_parts)}\n\n<b>AI 原因:</b> {reasoning}"
            send_tg_msg(error_msg)
            logger.warning(f"⚠️ AI 调参全部被拦截: rejected_by_human={rejected_by_human}")
            return {'success': False, 'message': error_msg, 'applied_params': {}}
        
        # 🔥 Step 3: 应用参数（state_lock 保护写入）
        with state_lock:
            for param_name, param_value in approved_params.items():
                SYSTEM_CONFIG[param_name] = param_value
                logger.info(f"✅ AI 弹性调参: {param_name} = {param_value} (旧值: {old_values.get(param_name, '?')})")
            save_data()
        
        # 🔥 Step 4: 战报生成（三段式清晰展示）
        overdrive_tag = " 🔥Overdrive" if is_overdrive else ""
        msg = f"🤖 <b>AI 弹性边界调参战报</b>{overdrive_tag}\n━━━━━━━━━━━━━━━━━━━━\n"
        msg += f"📊 <b>Regime:</b> <code>{regime}</code>\n\n"
        
        # ── 🔒 人工锁拦截段 ──
        if rejected_by_human:
            msg += "🔒 <b>人工锁拦截（AI 不可覆盖）:</b>\n"
            for p in rejected_by_human:
                current_val = SYSTEM_CONFIG.get(p, '?')
                msg += f"├ {p}: <code>{current_val}</code> (锁定中)\n"
            msg += "\n"
        
        # ── ⚠️ 弹性边界钳制段 ──
        if corrected_params:
            msg += "⚠️ <b>弹性边界钳制（已自动修正）:</b>\n"
            for p, info in corrected_params.items():
                msg += f"├ {p}: <code>{info['original']}</code> → <code>{info['corrected']}</code> ({info['reason']})\n"
            msg += "\n"
        
        # ── ✅ 成功修改段 ──
        msg += "✅ <b>最终成功修改:</b>\n"
        for param_name, new_value in approved_params.items():
            old_value = old_values.get(param_name, '?')
            bounds_info = ""
            if param_name in elastic_bounds:
                b = elastic_bounds[param_name]
                bounds_info = f" [{b['min']}~{b['max']}]"
            msg += f"├ {param_name}: <code>{old_value}</code> → <code>{new_value}</code>{bounds_info}\n"
        
        msg += f"\n💡 <b>AI 分析:</b>\n{reasoning}\n\n"
        msg += f"⏰ 调参时间: <i>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</i>\n"
        msg += f"🛡️ 下次调参冷却: <i>2小时后</i>"
        
        send_tg_msg(msg)
        logger.info(f"✅ AI 弹性调参成功 [Regime={regime}]: applied={approved_params}, clamped={list(corrected_params.keys())}, rejected={rejected_by_human}")
        
        return {'success': True, 'message': msg, 'applied_params': approved_params}
    
    except Exception as e:
        error_msg = f"❌ AI 弹性调参执行失败: {str(e)[:100]}"
        logger.error(f"❌ execute_auto_tune 异常: {e}", exc_info=True)
        send_tg_msg(error_msg)
        return {'success': False, 'message': error_msg, 'applied_params': {}}


# ==========================================
# 🎛️ 核心引擎开关面板 (对齐回测)
# ==========================================

def get_engine_switches_keyboard():
    """动态生成引擎开关控制台键盘"""
    keyboard = []

    switches = {
        "BLACK_SWAN_DEFENSE": "🛡️ 黑天鹅防御熔断",
        "USE_KELLY_FORMULA": "⚖️ 凯利公式动态配资",
        "USE_VOLATILITY_SCALAR": "📉 ATR 波动率缩放",
        "SPACE_LOCK_ENABLED": "🔒 K线空间锁 (防假突破)",
        "OBI_FILTER_ENABLED": "🧊 L2盘口冰山拦截",
        "RS_FILTER_ENABLED": "🐉 大盘轮动强弱过滤",
        "SML_BOOSTER_ENABLED": "🚀 SML利润放大器 (+20%)"
    }

    for key, name in switches.items():
        default_val = True if key == "BLACK_SWAN_DEFENSE" else False
        current_state = config.SYSTEM_CONFIG.get(key, default_val)

        status_icon = "✅" if current_state else "❌"
        button_text = f"{status_icon} {name}"

        keyboard.append([types.InlineKeyboardButton(button_text, callback_data=f"toggle_eng_{key}")])

    keyboard.append([types.InlineKeyboardButton("🔙 返回参数设置", callback_data="menu_settings")])

    return types.InlineKeyboardMarkup(keyboard)


# ==========================================
# 导出所有处理函数
# ==========================================

__all__ = [
    'create_main_menu',
    'handle_start_command',
    'handle_add_command',
    'handle_del_command',
    'handle_balance_command',
    'handle_set_command',
    'handle_close_command',
    'handle_dashboard',
    'handle_positions',
    'handle_vault_panel',
    'handle_sentry_panel',
    'show_strategy_center',
    'show_settings_menu',
    'show_real_time_prices',
    'register_handlers',
    'enable_vault',
    'disable_vault',
    'show_vault_status',
    'ask_withdraw_ratio',
    'manual_vault_transfer',
    'execute_auto_tune',
    'show_vault_management',
    '_vault_emergency_reset',
    '_vault_sync_hwm',
    '_vault_custom_input_prompt',
    'get_engine_switches_keyboard',
]

print("✅ bot_handlers 模块已加载（V8.0 Vault Management GUI + Ghost Meltdown 防护）")
