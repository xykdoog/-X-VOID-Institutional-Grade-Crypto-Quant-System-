#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram Bot 扩展处理器 - 补全缺失的高级菜单逻辑
"""

import os
import sys
import html
from telebot import types
import config
engine_status = "🟢 正在战斗" if config.TRADING_ENGINE_ACTIVE else "🔴 休息中"
msg = f"🎮 <b>核心控制台</b>\n━━━━━━━━━━━━━━━━━━━━\n"
msg += f"🤖 <b>引擎状态:</b> {engine_status}\n"
# ... 后面保持原来的代码
# 强行将当前目录加入环境变量，解决“无法解析导入”的问题
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from config import (
    SYSTEM_CONFIG, 
    SENTRY_CONFIG, 
    SENTRY_INTERVAL_OPTIONS, 
    save_data, 
    save_sentry_watchlist, 
    state_lock, 
    mark_custom_mode,
    clear_user_session
)
from utils import (
    safe_send_message, 
    safe_edit_message, 
    search_symbols_fuzzy, 
    get_all_valid_symbols,
    get_bot, 
    normalize_weights
)
from human_override import get_override_manager

def process_sim_balance_input(message):
    """处理用户输入的虚拟金补给数值"""
    from logger_setup import logger
    chat_id = message.chat.id
    user_input = message.text.strip()
    
    clear_user_session(chat_id)
    if user_input.lower() in ['取消', 'cancel']:
        safe_send_message(chat_id, "❌ 已取消虚拟金补给", parse_mode="HTML")
        return
    
    try:
        new_balance = float(user_input)
        if new_balance < 0:
            safe_send_message(chat_id, "❌ 余额不能为负数！请输入有效的正数。", parse_mode="HTML")
            return
        
        old_balance = SYSTEM_CONFIG.get("SIM_CURRENT_BALANCE", 0.0)
        with state_lock:
            SYSTEM_CONFIG["SIM_CURRENT_BALANCE"] = new_balance
            save_data()
        
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
        safe_send_message(chat_id, "❌ 输入格式错误！请输入有效的数字。", parse_mode="HTML")

def show_risk_settings_menu(chat_id, message_id=None):
    msg = "⚖️ <b>风险管理设置</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
    msg += f"├ 基准本金: <code>${SYSTEM_CONFIG.get('BENCHMARK_CASH', 1800):.2f}</code>\n"
    msg += f"├ 风险系数: <code>{SYSTEM_CONFIG.get('RISK_RATIO', 0)*100:.1f}%</code>\n"
    msg += f"├ 杠杆倍数: <code>{SYSTEM_CONFIG.get('LEVERAGE', 20)}x</code>\n"
    
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.row(
        types.InlineKeyboardButton("💰 基准本金", callback_data="input_BENCHMARK_CASH"),
        types.InlineKeyboardButton("⚡ 风险系数", callback_data="input_RISK_RATIO")
    )
    markup.row(
        types.InlineKeyboardButton(" 杠杆倍数", callback_data="input_LEVERAGE"),
        types.InlineKeyboardButton("🔙 返回", callback_data="back_to_settings")
    )
    
    if message_id:
        safe_edit_message(chat_id, message_id, msg, parse_mode="HTML", reply_markup=markup)
    else:
        safe_send_message(chat_id, msg, parse_mode="HTML", reply_markup=markup)

def show_atr_settings_menu(chat_id, message_id=None):
    msg = "🛡️ <b>ATR 止损设置</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
    msg += f"├ ATR周期: <code>{SYSTEM_CONFIG.get('ATR_PERIOD', 14)}</code>\n"
    msg += f"├ ATR倍数: <code>{SYSTEM_CONFIG.get('ATR_MULT', 2.3)}</code>\n"
    msg += f"├ 止损缓冲: <code>{SYSTEM_CONFIG.get('SL_BUFFER', 1.02)}</code>\n"
    
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.row(
        types.InlineKeyboardButton("⏱️ ATR周期", callback_data="input_ATR_PERIOD"),
        types.InlineKeyboardButton("📏 ATR倍数", callback_data="input_ATR_MULT")
    )
    markup.row(
        types.InlineKeyboardButton("🛡️ 止损缓冲", callback_data="input_SL_BUFFER"),
        types.InlineKeyboardButton("🔙 返回", callback_data="back_to_settings")
    )
    if message_id:
        safe_edit_message(chat_id, message_id, msg, parse_mode="HTML", reply_markup=markup)
    else:
        safe_send_message(chat_id, msg, parse_mode="HTML", reply_markup=markup)

def show_mad_dog_settings_menu(chat_id, message_id=None):
    msg = "🐺 <b>疯狗模式设置</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
    msg += f"├ 状态: <code>{'开启' if SYSTEM_CONFIG.get('MAD_DOG_MODE', False) else '关闭'}</code>\n"
    msg += f"├ 触发线: <code>{SYSTEM_CONFIG.get('MAD_DOG_TRIGGER', 1.3)}</code>\n"
    msg += f"├ 倍率: <code>{SYSTEM_CONFIG.get('MAD_DOG_BOOST', 2.0)}</code>\n"
    
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.row(
        types.InlineKeyboardButton("🐺 状态切换", callback_data="set_MAD_DOG_MODE_1" if not SYSTEM_CONFIG.get("MAD_DOG_MODE") else "set_MAD_DOG_MODE_0"),
        types.InlineKeyboardButton("📈 触发线", callback_data="input_MAD_DOG_TRIGGER")
    )
    markup.row(
        types.InlineKeyboardButton("🚀 倍率", callback_data="input_MAD_DOG_BOOST"),
        types.InlineKeyboardButton("🔙 返回", callback_data="back_to_settings")
    )
    if message_id:
        safe_edit_message(chat_id, message_id, msg, parse_mode="HTML", reply_markup=markup)
    else:
        safe_send_message(chat_id, msg, parse_mode="HTML", reply_markup=markup)

def show_sentry_interval_menu(chat_id, message_id=None):
    msg = "⏱️ <b>选择哨所推送间隔</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
    current_key = SENTRY_CONFIG.get("INTERVAL_KEY", "15m")
    msg += f"当前间隔: <b>{SENTRY_INTERVAL_OPTIONS.get(current_key, {}).get('name', '未知')}</b>"
    
    markup = types.InlineKeyboardMarkup(row_width=3)
    buttons = []
    for key, info in SENTRY_INTERVAL_OPTIONS.items():
        prefix = "✅ " if key == current_key else ""
        buttons.append(types.InlineKeyboardButton(f"{prefix}{info['name']}", callback_data=f"sentry_interval_{key}"))
    markup.add(*buttons)
    markup.row(types.InlineKeyboardButton("🔙 返回", callback_data="back_to_sentry"))
    
    if message_id:
        safe_edit_message(chat_id, message_id, msg, parse_mode="HTML", reply_markup=markup)
    else:
        safe_send_message(chat_id, msg, parse_mode="HTML", reply_markup=markup)

def show_asset_settings_menu(chat_id, client, page=1, message_id=None):
    # 🔥 防御：client 未初始化时拒绝进入
    if client is None:
        safe_send_message(chat_id, "❌ <b>API 未连接</b>\n\n无法修改资产设置，请先确认 API 已正确配置并连接。", parse_mode="HTML")
        return

    items_per_page = 5
    symbols = list(SYSTEM_CONFIG["ASSET_WEIGHTS"].items())
    total_pages = max(1, (len(symbols) + items_per_page - 1) // items_per_page)
    page = max(1, min(page, total_pages))
    
    start_idx = (page - 1) * items_per_page
    end_idx = min(start_idx + items_per_page, len(symbols))
    page_symbols = symbols[start_idx:end_idx]
    
    msg = "💼 <b>资产监控与权重设置</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
    msg += f"<b>当前监控数量:</b> {len(symbols)}/{SYSTEM_CONFIG.get('MAX_ACTIVE_SYMBOLS', 5)}\n\n"
    
    markup = types.InlineKeyboardMarkup(row_width=2)
    for sym, weight in page_symbols:
        msg += f"💎 <b>{sym}</b>: {weight*100:.1f}%\n"
        markup.row(
            types.InlineKeyboardButton(f"❌ 移除 {sym}", callback_data=f"asset_remove_{sym}_{page}")
        )
    
    nav_buttons = []
    if page > 1:
        nav_buttons.append(types.InlineKeyboardButton("⬅️ 上一页", callback_data=f"asset_page_{page-1}"))
    if page < total_pages:
        nav_buttons.append(types.InlineKeyboardButton("下一页 ➡️", callback_data=f"asset_page_{page+1}"))
    if nav_buttons:
        markup.row(*nav_buttons)
        
    markup.row(
        types.InlineKeyboardButton("➕ 添加币种", callback_data="asset_search_start"),
        types.InlineKeyboardButton("⚖️ 平均分配权重", callback_data="asset_balance_weights")
    )
    markup.row(types.InlineKeyboardButton("🔙 返回", callback_data="back_to_settings"))
    
    if message_id:
        safe_edit_message(chat_id, message_id, msg, parse_mode="HTML", reply_markup=markup)
    else:
        safe_send_message(chat_id, msg, parse_mode="HTML", reply_markup=markup)

def toggle_price_monitor(chat_id, message_id=None, callback_id=None):
    """切换价格监控开关，支持 callback toast 反馈"""
    from utils import safe_answer_callback
    with state_lock:
        SYSTEM_CONFIG["PRICE_MONITOR_ENABLED"] = not SYSTEM_CONFIG.get("PRICE_MONITOR_ENABLED", False)
        save_data()
    status = "开启" if SYSTEM_CONFIG["PRICE_MONITOR_ENABLED"] else "关闭"
    icon = "🟢" if SYSTEM_CONFIG["PRICE_MONITOR_ENABLED"] else "🔴"
    
    # 🔥 顶部 toast 反馈
    if callback_id:
        safe_answer_callback(callback_id, f"✅ 价格监控已{status}")
    
    # 刷新设置菜单而非显示纯文本（让用户看到开关状态变化）
    try:
        from bot_handlers import show_settings_menu
        show_settings_menu(chat_id, message_id)
    except Exception:
        # fallback: 如果刷新菜单失败，至少显示文本
        msg = f"{icon} <b>价格监控已{status}</b>"
        if message_id:
            safe_edit_message(chat_id, message_id, msg, parse_mode="HTML")
        else:
            safe_send_message(chat_id, msg, parse_mode="HTML")

def process_custom_input(message, param, info, message_id=None):
    chat_id = message.chat.id
    user_input = message.text.strip()
    
    clear_user_session(chat_id)
    if user_input.lower() in ['取消', 'cancel']:
        safe_send_message(chat_id, "❌ 已取消修改")
        return
        
    try:
        if info['type'] == 'int':
            val = int(float(user_input))
        elif info['type'] == 'float':
            val = float(user_input)
        else:
            val = user_input
            
        if info['type'] != 'str':
            if val < info['min'] or val > info['max']:
                safe_send_message(chat_id, f"❌ 超出允许范围: {info['min']} - {info['max']}")
                return
                
        with state_lock:
            SYSTEM_CONFIG[param] = val
            mark_custom_mode(param)
            save_data()
            
        get_override_manager().lock_parameter(param, val, reason="Telegram 交互修改")
        safe_send_message(chat_id, f"✅ <b>{info['name']}</b> 已修改为: <code>{val}</code>", parse_mode="HTML")
        
        # 返回对应的菜单
        if info['category'] == 'indicator':
            from bot_handlers import show_indicators_settings
            show_indicators_settings(chat_id)
        elif info['category'] == 'risk':
            show_risk_settings_menu(chat_id)
        elif info['category'] == 'atr':
            show_atr_settings_menu(chat_id)
        elif info['category'] == 'maddog':
            show_mad_dog_settings_menu(chat_id)
            
    except ValueError:
        safe_send_message(chat_id, "❌ 输入格式错误，修改失败")

def process_asset_search(message, client):
    chat_id = message.chat.id
    keyword = message.text.strip().upper()
    
    clear_user_session(chat_id)
    
    # 🔥 防御：client 未初始化时拒绝操作
    if client is None:
        safe_send_message(chat_id, "❌ <b>API 未连接</b>\n\n无法搜索币种，请先确认 API 已正确配置并连接。", parse_mode="HTML")
        return
    if keyword.lower() in ['取消', 'cancel']:
        safe_send_message(chat_id, "❌ 已取消搜索")
        show_asset_settings_menu(chat_id, client)
        return
        
    matches = search_symbols_fuzzy(client, keyword)
    if not matches:
        safe_send_message(chat_id, f"❌ 未找到包含 {keyword} 的 USDT 合约")
        show_asset_settings_menu(chat_id, client)
        return
        
    markup = types.InlineKeyboardMarkup(row_width=3)
    buttons = []
    for sym in matches[:15]:
        if sym not in SYSTEM_CONFIG["ASSET_WEIGHTS"]:
            buttons.append(types.InlineKeyboardButton(sym, callback_data=f"asset_add_{sym}_1"))
            
    if not buttons:
        safe_send_message(chat_id, "💡 搜索到的币种已在监控列表中")
        show_asset_settings_menu(chat_id, client)
        return
        
    markup.add(*buttons)
    markup.row(types.InlineKeyboardButton("🔙 返回", callback_data="show_asset_center"))
    safe_send_message(chat_id, f"🔍 <b>找到以下币种，点击添加:</b>", parse_mode="HTML", reply_markup=markup)

def process_sentry_add_symbol(message, client):
    chat_id = message.chat.id
    symbol = message.text.strip().upper()
    
    clear_user_session(chat_id)
    
    # 🔥 防御：client 未初始化时拒绝操作
    if client is None:
        safe_send_message(chat_id, "❌ <b>API 未连接</b>\n\n无法验证币种，请先确认 API 已正确配置并连接。", parse_mode="HTML")
        return
    
    if symbol.lower() in ['取消', 'cancel']:
        safe_send_message(chat_id, "❌ 已取消添加")
        from bot_handlers import handle_sentry_panel
        handle_sentry_panel(message, client)
        return
        
    if not symbol.endswith('USDT'):
        symbol += 'USDT'
        
    all_symbols = get_all_valid_symbols(client)
    if all_symbols and symbol not in all_symbols:
        safe_send_message(chat_id, f"❌ 未找到合约 {symbol}")
        from bot_handlers import handle_sentry_panel
        handle_sentry_panel(message, client)
        return
        
    if symbol in SENTRY_CONFIG["WATCH_LIST"]:
        safe_send_message(chat_id, f"💡 {symbol} 已在哨所名单中")
        from bot_handlers import handle_sentry_panel
        handle_sentry_panel(message, client)
        return
        
    SENTRY_CONFIG["WATCH_LIST"].append(symbol)
    save_sentry_watchlist()
    safe_send_message(chat_id, f"✅ 已添加 {symbol} 到哨所")
    from bot_handlers import handle_sentry_panel
    handle_sentry_panel(message, client)