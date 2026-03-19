#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram Bot 回调处理器
处理所有 InlineKeyboard 回调查询
"""

import html
from telebot import types
from logger_setup import logger
from config import (
    SYSTEM_CONFIG, SENTRY_CONFIG, SENTRY_INTERVAL_OPTIONS,
    STRATEGY_PRESETS, LAUNCH_MODE_MAP,
    save_data, save_sentry_watchlist, mark_custom_mode, apply_strategy_preset,
    state_lock
)
import config
from utils import (
    get_current_price,
    safe_send_message, safe_edit_message, safe_delete_message, safe_answer_callback,
    send_tg_msg, get_bot, normalize_weights
)
from human_override import get_override_manager
from redis_manager import redis_db
if not redis_db.enabled:
    logger.warning("⚠️ Bot 检测到 Redis 不可用，自动切入本地内存模式，响应速度已优化")

# 🔥 修复：将可能引发循环导入的模块改为延迟导入
# from trading_engine import sync_positions, emergency_close_all  # 已移至函数内部
# from monitors import push_sentry_price_report                   # 已移至函数内部


def trigger_sim_balance_input(chat_id):
    """
    💰 虚拟金补给 - 触发余额输入流程
    从 handle_text 路由调用，引导用户输入新的沙盒余额
    """
    bot = get_bot()
    if not bot:
        logger.error("❌ trigger_sim_balance_input: bot 实例不可用")
        return
    
    current_balance = SYSTEM_CONFIG.get("SIM_CURRENT_BALANCE", 0.0)
    msg = "💰 <b>虚拟金补给</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
    msg += f"<b>当前沙盒余额:</b> <code>${current_balance:.2f}</code>\n\n"
    msg += "✍️ <b>请输入新的余额数值:</b>\n"
    msg += "<i>或回复 <code>取消</code> 返回</i>"
    
    sent_msg = safe_send_message(chat_id, msg, parse_mode="HTML")
    if sent_msg:
        from bot_handlers import process_sim_balance_input
        bot.register_next_step_handler(sent_msg, process_sim_balance_input)


def show_ai_autonomy_confirm(chat_id):
    """
    🤖 激活满血 AI 指挥权 - 显示二次确认对话框
    从 handle_text 路由调用
    """
    from telebot import types as _types
    
    msg = "⚠️ <b>危险操作确认</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
    msg += "🤖 <b>即将激活 AI 满血指挥权</b>\n\n"
    msg += "✅ <b>AI 将获得以下权限:</b>\n"
    msg += "├ 自主开仓/平仓决策\n"
    msg += "├ 动态调整策略参数\n"
    msg += "├ 风险管理与止损控制\n"
    msg += "└ 市场异常应急响应\n\n"
    msg += "⚠️ <b>风险提示:</b>\n"
    msg += "• AI 将在您设定的风控边界内运作\n"
    msg += "• 重大决策仍会通知您确认\n"
    msg += "• 可随时撤销授权\n\n"
    msg += "❓ <b>确认激活 AI 满血接管模式？</b>"
    
    markup = _types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        _types.InlineKeyboardButton("✅ 确认激活", callback_data="confirm_ai_autonomy"),
        _types.InlineKeyboardButton("❌ 取消", callback_data="cancel_ai_autonomy")
    )
    
    safe_send_message(chat_id, msg, parse_mode="HTML", reply_markup=markup)
    logger.info(f"🤖 AI 满血接管模式确认对话框已显示 to {chat_id}")


def handle_callback(call, client):
    """处理所有回调查询"""
    bot = get_bot()
    from config import clear_user_session
    clear_user_session(call.message.chat.id)
    try:
        from bot_handlers import (
            handle_start_command, handle_dashboard, handle_positions,
            handle_vault_panel, handle_sentry_panel,
            show_strategy_center, show_settings_menu,
            show_indicators_settings, show_real_time_prices,
            enable_vault, disable_vault, show_vault_status,
            ask_withdraw_ratio, manual_vault_transfer,show_sim_ledger_center
        )
    except ImportError as e:
        logger.error(f"回调导入失败: {e}")
    # =========================================================



    # ====== 🔥 Step 1: 调试日志 - 确认回调已到达 ======
    logger.info(f"DEBUG: Callback Received - Data: {call.data}, ChatID: {call.message.chat.id}")
    # ===================================================

    # TODO: 需要创建 bot_handlers.py 文件或将这些函数移到其他模块
    # 延迟导入避免循环引用
    # from bot_handlers import (
    #     handle_start_command, handle_dashboard, handle_positions,
    #     handle_vault_panel, handle_sentry_panel,
    #     show_strategy_center,
    #     show_settings_menu, show_indicators_settings,
    #     show_real_time_prices,
    #     enable_vault, disable_vault, show_vault_status,
    #     manual_vault_transfer, ask_withdraw_ratio,
    #     show_sim_ledger_center
    # )
    from bot_handlers_additions import (
        show_risk_settings_menu, show_atr_settings_menu, show_mad_dog_settings_menu,
        show_sentry_interval_menu, show_asset_settings_menu, toggle_price_monitor,
        process_custom_input, process_asset_search, process_sentry_add_symbol
    )

    chat_id = call.message.chat.id
    message_id = call.message.message_id
    data = call.data

    # ====== 🚨 顶级云端安全防线：拦截越权点击 ======
    owner_chat_id = str(SYSTEM_CONFIG.get("TG_CHAT_ID", ""))
    if str(chat_id) != owner_chat_id:
        logger.warning(f"⛔ 触发越权拦截！陌生访客试图点击按钮: {chat_id} | 动作: {data}")
        safe_answer_callback(call.id, "⛔ 警告：您无权操作此量化引擎！", show_alert=True)
        return
    # =================================================

    try:
        # 主菜单导航
        if data == "back_to_main":
            # 🔥 V7.0: In-place edit 回到主控台，避免消息洪水
            from bot_handlers import create_main_menu
            msg_text, markup = create_main_menu()
            safe_edit_message(chat_id, message_id, msg_text, parse_mode="HTML", reply_markup=markup)
            safe_answer_callback(call.id)

        elif data == "show_dashboard":
            safe_delete_message(chat_id, message_id)
            handle_dashboard(call.message, client)

        elif data == "show_positions":
            safe_delete_message(chat_id, message_id)
            handle_positions(call.message, client)

        elif data == "show_positions_detail":
            safe_delete_message(chat_id, message_id)
            handle_positions(call.message, client)

        elif data == "refresh_dashboard":
            safe_delete_message(chat_id, message_id)
            handle_dashboard(call.message, client)

        elif data == "show_launch_wizard":
            # 🔥 V7.0: 启动向导入口
            safe_delete_message(chat_id, message_id)
            from bot_handlers import show_launch_wizard
            show_launch_wizard(chat_id, client)

        elif data == "show_strategy_center":
            safe_delete_message(chat_id, message_id)
            show_strategy_center(chat_id, client)

        elif data == "show_asset_center":
            # 🔥 V7.0: 资产管理入口
            safe_answer_callback(call.id)
            show_asset_settings_menu(chat_id, client, message_id=message_id)

        elif data == "show_vault":
            safe_delete_message(chat_id, message_id)
            handle_vault_panel(call.message, client)

        elif data == "show_sentry":
            safe_delete_message(chat_id, message_id)
            handle_sentry_panel(call.message, client)

        elif data == "show_settings":
            safe_delete_message(chat_id, message_id)
            show_settings_menu(chat_id, client=client)

        # 设置菜单
        elif data == "back_to_settings":
            show_settings_menu(chat_id, message_id, client=client)

        elif data == "settings_strategy_mode":
            show_strategy_center(chat_id, client)

        elif data == "settings_indicators":
            show_indicators_settings(chat_id, message_id)

        elif data.startswith("strategy_mode_"):
            mode_key = data.replace("strategy_mode_", "")
            if apply_strategy_preset(mode_key):
                save_data()  # 🔥 修复：策略切换后立即持久化
                preset = STRATEGY_PRESETS[mode_key]
                safe_answer_callback(call.id, f"✅ 已切换到{preset['name']}")
                send_tg_msg(
                    f"🎯 <b>策略模式已切换</b>\n\n"
                    f"<b>新模式:</b> {preset['emoji']} {preset['name']}\n"
                    f"<b>说明:</b> {preset['description']}\n\n"
                    f"<b>新参数:</b>\n"
                    f"• INTERVAL: <code>{SYSTEM_CONFIG['INTERVAL']}</code>\n"
                    f"• ADX_THR: <code>{SYSTEM_CONFIG['ADX_THR']}</code>\n"
                    f"• EMA_TREND: <code>{SYSTEM_CONFIG['EMA_TREND']}</code>\n"
                    f"• ATR_MULT: <code>{SYSTEM_CONFIG['ATR_MULT']}</code>\n\n"
                    f"✅ 策略参数已实时注入，下一个扫描周期将自动生效。"
                )
                show_strategy_center(chat_id, client)

        elif data == "toggle_engine":
            config.TRADING_ENGINE_ACTIVE = not config.TRADING_ENGINE_ACTIVE
            status = "启动" if config.TRADING_ENGINE_ACTIVE else "停止"
            safe_answer_callback(call.id, f"✅ 交易引擎已{status}")
            show_settings_menu(chat_id, message_id, client=client)

        elif data == "toggle_verification":
            config.VERIFICATION_MODE = not config.VERIFICATION_MODE
            mode = "验证模式" if config.VERIFICATION_MODE else "实盘模式"
            safe_answer_callback(call.id, f"✅ 已切换到{mode}")
            show_settings_menu(chat_id, message_id, client=client)

        elif data == "toggle_dry_run":
            with state_lock:
                SYSTEM_CONFIG["DRY_RUN"] = not SYSTEM_CONFIG.get("DRY_RUN", False)
                save_data()
                is_dry_run = SYSTEM_CONFIG.get("DRY_RUN", False)
            mode_text = "模拟开单 (DRY_RUN)" if is_dry_run else "实盘交易 (REAL_MODE)"
            icon = "🔍" if is_dry_run else "🚨"
            safe_answer_callback(call.id, f"✅ 已切换到{mode_text}")
            send_tg_msg(
                f"{icon} <b>运行环境已切换</b>\n\n"
                f"<b>当前模式:</b> {mode_text}\n"
                f"<b>状态说明:</b> {'所有交易将仅模拟执行，不会发送真实API请求' if is_dry_run else '交易将发送真实API请求到交易所'}\n\n"
                f"{'⚠️ <b>提示:</b> 模拟模式下，PnL计算和历史记录依然正常运行' if is_dry_run else '🚨 <b>警告:</b> 当前为实盘交易模式，请谨慎操作！'}"
            )
            show_settings_menu(chat_id, message_id, client=client)

        elif data == "toggle_auto_tune":
            with state_lock:
                current_state = SYSTEM_CONFIG.get("AUTO_TUNE_ENABLED", False)
                SYSTEM_CONFIG["AUTO_TUNE_ENABLED"] = not current_state
                save_data()
                new_state = SYSTEM_CONFIG["AUTO_TUNE_ENABLED"]
            
            state_text = "开启" if new_state else "关闭"
            state_icon = "🟢" if new_state else "🔴"
            
            safe_answer_callback(call.id, f"✅ AI自动调参已{state_text}")
            
            send_tg_msg(
                f"🤖 <b>AI自适应巡航调参引擎</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
                f"<b>状态:</b> {state_icon} {state_text}\n\n"
                f"{'✅ <b>引擎已启动</b>\n'
                 '├ 每15分钟评估市场状态\n'
                 '├ 自动微调策略参数\n'
                 '├ 冷却期: 2小时\n'
                 '└ 安全边界保护已激活\n\n'
                 '💡 AI将根据实时指标自动优化参数，所有调整将通过Telegram通知。' 
                 if new_state else 
                 '❌ <b>引擎已停止</b>\n\n'
                 '参数将保持手动设置，不再自动调整。'}"
            )
            
            show_settings_menu(chat_id, message_id, client=client)

        elif data == "toggle_ai_autonomy":
            with state_lock:
                # 🔒 物理安全锁：禁止在实盘模式下开启满血接管
                if not SYSTEM_CONFIG.get("DRY_RUN", False) and not SYSTEM_CONFIG.get("AI_FULL_AUTONOMY_MODE", False):
                    safe_answer_callback(call.id, "⛔ 拒绝访问：满血接管只能在模拟盘(DRY_RUN)下开启！", show_alert=True)
                    send_tg_msg(
                        "⛔ <b>物理安全锁触发</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
                        "<b>拒绝原因:</b> AI满血接管模式仅允许在模拟盘环境下激活\n\n"
                        "🛡️ <b>安全提示:</b>\n"
                        "• 请先切换到 DRY_RUN 模式\n"
                        "• 在模拟环境中充分测试后再考虑实盘\n"
                        "• 实盘模式下必须保持人工审批流程\n\n"
                        "💡 使用 /settings 切换到模拟盘模式"
                    )
                    return
                
                current_state = SYSTEM_CONFIG.get("AI_FULL_AUTONOMY_MODE", False)
                SYSTEM_CONFIG["AI_FULL_AUTONOMY_MODE"] = not current_state
                save_data()
                new_state = SYSTEM_CONFIG["AI_FULL_AUTONOMY_MODE"]
            
            if new_state:
                safe_answer_callback(call.id, "🔥 AI满血接管已激活！")
                send_tg_msg(
                    "🧠 <b>[AI 满血接管模式已激活]</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
                    "⚠️ <b>安全协议已解除，人类审批流已旁路。</b>\n"
                    "从现在起，AI 生成的所有调参指令将<b>瞬间强制生效</b>！\n\n"
                    "🔥 <b>核心变更:</b>\n"
                    "├ AI拥有绝对控制权\n"
                    "├ 所有参数修改即时生效\n"
                    "├ 无需人工确认\n"
                    "└ AI将主动调整策略\n\n"
                    "🤖 <i>\"Commander, I have taken full control. Commencing surgical execution.\"</i>"
                )
            else:
                safe_answer_callback(call.id, "🔒 AI满血接管已解除")
                send_tg_msg(
                    "🔒 <b>[AI 满血接管已解除]</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
                    "控制权已交还给人类统帅，审批流已恢复。\n\n"
                    "✅ <b>当前状态:</b>\n"
                    "├ AI恢复为建议模式\n"
                    "├ 所有参数修改需要人工确认\n"
                    "└ 使用 /confirm TOKEN 或 /reject TOKEN 审批\n\n"
                    "💡 系统已恢复正常安全模式"
                )
            
            show_settings_menu(chat_id, message_id, client=client)

        elif data == "sync_positions":
            safe_answer_callback(call.id, "🔄 正在同步仓位...")
            from trading_engine import sync_positions
            sync_positions(client, chat_id)

        elif data == "emergency_close":
            # 🔥 修复 #22: 添加二次确认，防止误操作
            safe_answer_callback(call.id)
            
            # 获取当前持仓数量
            from config import ACTIVE_POSITIONS, positions_lock
            with positions_lock:
                position_count = len(ACTIVE_POSITIONS)
            
            if position_count == 0:
                safe_answer_callback(call.id, "📭 当前无持仓，无需平仓", show_alert=True)
                return
            
            # 显示二次确认对话框
            markup = types.InlineKeyboardMarkup()
            markup.row(
                types.InlineKeyboardButton("✅ 确认全平", callback_data="emergency_close_confirm"),
                types.InlineKeyboardButton("❌ 取消", callback_data="emergency_close_cancel")
            )
            
            msg = "🚨 <b>一键全平确认</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
            msg += f"⚠️ <b>警告：此操作将平掉所有持仓！</b>\n\n"
            msg += f"<b>当前持仓数量:</b> <code>{position_count}</code> 个\n\n"
            msg += "<b>操作说明:</b>\n"
            msg += "• 将立即平掉所有活跃持仓\n"
            msg += "• 所有止损单将被取消\n"
            msg += "• 此操作不可撤销\n\n"
            msg += "⚠️ <b>请再次确认是否继续？</b>"
            
            safe_edit_message(chat_id, message_id, msg, parse_mode="HTML", reply_markup=markup)

        elif data == "emergency_close_confirm":
            # 用户确认后执行全平
            safe_answer_callback(call.id, "⚠️ 正在执行一键全平...")
            from trading_engine import emergency_close_all
            
            # 发送执行中的消息
            msg = "🔄 <b>正在执行一键全平...</b>\n\n"
            msg += "请稍候，系统正在平掉所有持仓..."
            safe_edit_message(chat_id, message_id, msg, parse_mode="HTML")
            
            # 执行全平
            emergency_close_all(client, chat_id)

        elif data == "emergency_close_cancel":
            # 用户取消操作
            safe_answer_callback(call.id, "❌ 已取消全平操作")
            
            msg = "❌ <b>操作已取消</b>\n\n"
            msg += "一键全平操作已取消，所有持仓保持不变。"
            
            safe_edit_message(chat_id, message_id, msg, parse_mode="HTML")
            
            # 3秒后返回设置菜单
            import time
            time.sleep(3)
            show_settings_menu(chat_id, message_id, client=client)

        # 保险库
        elif data == "vault_enable":
            safe_answer_callback(call.id)
            enable_vault(chat_id)

        elif data == "vault_disable":
            safe_answer_callback(call.id)
            disable_vault(chat_id)

        elif data == "vault_status":
            safe_answer_callback(call.id)
            show_vault_status(chat_id)

        # 🔥 V8.0 Vault Management GUI 回调
        elif data == "show_vault_mgmt":
            safe_answer_callback(call.id)
            from bot_handlers import show_vault_management
            show_vault_management(chat_id, message_id)

        elif data == "vault_emergency_reset":
            safe_answer_callback(call.id, "🚨 正在执行 Emergency Reset...")
            from bot_handlers import _vault_emergency_reset
            _vault_emergency_reset(chat_id, message_id)

        elif data == "vault_sync_hwm":
            safe_answer_callback(call.id, "🔄 正在同步 HWM...")
            from bot_handlers import _vault_sync_hwm
            _vault_sync_hwm(chat_id, message_id)

        elif data == "vault_custom_input":
            safe_answer_callback(call.id)
            from bot_handlers import _vault_custom_input_prompt
            _vault_custom_input_prompt(chat_id)

        # 哨所
        elif data == "back_to_sentry":
            safe_delete_message(chat_id, message_id)
            handle_sentry_panel(call.message, client)

        elif data == "sentry_toggle":
            SENTRY_CONFIG["ENABLED"] = not SENTRY_CONFIG["ENABLED"]
            status = "启用" if SENTRY_CONFIG["ENABLED"] else "停用"
            save_sentry_watchlist()
            safe_answer_callback(call.id, f"✅ 哨所已{status}")
            safe_delete_message(chat_id, message_id)
            handle_sentry_panel(call.message, client)

        elif data == "sentry_interval":
            show_sentry_interval_menu(chat_id, message_id)

        elif data.startswith("sentry_interval_"):
            interval_key = data.replace("sentry_interval_", "")
            if interval_key in SENTRY_INTERVAL_OPTIONS:
                SENTRY_CONFIG["INTERVAL_KEY"] = interval_key
                SENTRY_CONFIG["INTERVAL"] = SENTRY_INTERVAL_OPTIONS[interval_key]["seconds"]
                save_sentry_watchlist()
                safe_answer_callback(call.id, f"✅ 已切换到{SENTRY_INTERVAL_OPTIONS[interval_key]['name']}")
                show_sentry_interval_menu(chat_id, message_id)

        elif data == "sentry_add":
            safe_answer_callback(call.id, "请输入币种代码")
            sent_msg = safe_send_message(chat_id, "➕ <b>添加币种到哨所</b>\n\n请输入币种代码（例如: BTC, ETH, DOGE）\n或回复 <code>取消</code> 返回", parse_mode="HTML")
            if sent_msg and bot:
                bot.register_next_step_handler(sent_msg, process_sentry_add_symbol, client)

        elif data == "sentry_remove":
            safe_answer_callback(call.id, "请输入要移除的币种")
            sent_msg = safe_send_message(chat_id, "➖ <b>从哨所移除币种</b>\n\n请输入币种代码\n或回复 <code>取消</code> 返回", parse_mode="HTML")

        elif data == "sentry_push_now":
            safe_answer_callback(call.id, "📊 正在推送价格战报...")
            from monitors import push_sentry_price_report
            push_sentry_price_report(client, chat_id)

        elif data == "refresh_prices":
            safe_answer_callback(call.id)
            show_real_time_prices(chat_id, client, message_id)

        # 参数设置回调（使用 update_config_param 安全转换 + 反馈）
        elif data.startswith("set_"):
            parts = data.split("_")
            val_str = parts[-1]
            param = "_".join(parts[1:-1])
            try:
                from config import update_config_param
                success, msg_text = update_config_param(param, val_str)
                
                if success:
                    # 锁定参数，防止被自动覆盖
                    final_val = SYSTEM_CONFIG.get(param)
                    get_override_manager().lock_parameter(param, final_val, reason="Telegram 按钮修改")
                    
                    safe_answer_callback(call.id, f"✅ {msg_text}")
                else:
                    safe_answer_callback(call.id, f"❌ {msg_text}", show_alert=True)
                
                # 🔥 刷新对应的 Inline 菜单，让用户即时看到开关状态变化
                if param in ["ADX_THR", "LOW_VOL_MODE", "EMA_TREND", "INTERVAL"]:
                    show_indicators_settings(chat_id, message_id)
                elif param in ["LEVERAGE", "HEDGE_MODE", "RISK_RATIO", "BENCHMARK_CASH"]:
                    show_risk_settings_menu(chat_id, message_id)
                elif param in ["ATR_MULT", "ATR_PERIOD", "SL_BUFFER"]:
                    show_atr_settings_menu(chat_id, message_id)
                elif param in ["MAD_DOG_MODE", "MAD_DOG_BOOST", "MAD_DOG_TRIGGER"]:
                    show_mad_dog_settings_menu(chat_id, message_id)
            except Exception as e:
                safe_answer_callback(call.id, f"❌ 设置错误: {str(e)}")

        # 自定义输入回调
        elif data.startswith("input_"):
            param = data.replace("input_", "")
            safe_answer_callback(call.id)
            param_info = {
                # 指标参数
                "ADX_THR": {"name": "ADX阈值", "min": 0, "max": 50, "type": "int", "category": "indicator"},
                "EMA_TREND": {"name": "EMA趋势线", "min": 10, "max": 500, "type": "int", "category": "indicator"},
                "INTERVAL": {"name": "时间周期", "min": 0, "max": 0, "type": "str", "category": "indicator"},
                "MACD_FAST": {"name": "MACD快线", "min": 5, "max": 50, "type": "int", "category": "indicator"},
                "MACD_SLOW": {"name": "MACD慢线", "min": 10, "max": 100, "type": "int", "category": "indicator"},
                "MACD_SIGNAL": {"name": "MACD信号线", "min": 3, "max": 20, "type": "int", "category": "indicator"},
                "RSI_PERIOD": {"name": "RSI周期", "min": 5, "max": 50, "type": "int", "category": "indicator"},
                "RSI_OVERBOUGHT": {"name": "RSI超买线", "min": 60, "max": 90, "type": "int", "category": "indicator"},
                "RSI_OVERSOLD": {"name": "RSI超卖线", "min": 10, "max": 40, "type": "int", "category": "indicator"},
                
                # 风险管理参数
                "BENCHMARK_CASH": {"name": "基准本金", "min": 100, "max": 1000000, "type": "float", "category": "risk"},
                "RISK_RATIO": {"name": "风险系数", "min": 0.001, "max": 0.2, "type": "float", "category": "risk"},
                "LEVERAGE": {"name": "杠杆倍数", "min": 1, "max": 125, "type": "int", "category": "risk"},
                
                # ATR止损参数
                "ATR_MULT": {"name": "ATR倍数", "min": 0.5, "max": 10, "type": "float", "category": "atr"},
                "ATR_PERIOD": {"name": "ATR周期", "min": 5, "max": 50, "type": "int", "category": "atr"},
                "SL_BUFFER": {"name": "止损缓冲", "min": 1.0, "max": 2.0, "type": "float", "category": "atr"},
                
                # 疯狗模式参数
                "MAD_DOG_BOOST": {"name": "疯狗倍率", "min": 1.0, "max": 10.0, "type": "float", "category": "maddog"},
                "MAD_DOG_TRIGGER": {"name": "疯狗触发线", "min": 1.0, "max": 5.0, "type": "float", "category": "maddog"},
                
                # 保险库参数
                "VAULT_THR": {"name": "保险库阈值", "min": 0, "max": 100000, "type": "float", "category": "vault"},
                "WITHDRAW_RATIO": {"name": "提取比例", "min": 0.01, "max": 1.0, "type": "float", "category": "vault"},
            }
            if param in param_info:
                info = param_info[param]
                current_val = SYSTEM_CONFIG.get(param, "未设置")
                msg = f"⚙️ <b>修改 {info['name']}</b>\n\n"
                msg += f"<b>当前值:</b> <code>{current_val}</code>\n"
                if info['type'] != 'str':
                    msg += f"<b>允许范围:</b> {info['min']} - {info['max']}\n"
                msg += f"<b>数据类型:</b> {'整数' if info['type'] == 'int' else '小数' if info['type'] == 'float' else '文本'}\n\n"
                msg += "✍️ <b>请直接回复您要设置的数值:</b>\n<i>或回复 <code>取消</code> 返回菜单</i>"
                sent_msg = safe_send_message(chat_id, msg, parse_mode="HTML")
                if sent_msg and bot:
                    bot.register_next_step_handler(sent_msg, process_custom_input, param, info, message_id)

        # 资产管理回调
        elif data.startswith("asset_page_"):
            page = int(data.split("_")[2])
            safe_answer_callback(call.id)
            show_asset_settings_menu(chat_id, client, page=page, message_id=message_id)

        elif data == "asset_search_start":
            safe_answer_callback(call.id)
            sent_msg = safe_send_message(chat_id, "🔍 <b>请输入要搜索的币种代码 (例如 PEPE, DOGE):</b>\n回复 <code>取消</code> 退出", parse_mode="HTML")
            if sent_msg and bot:
                bot.register_next_step_handler(sent_msg, process_asset_search, client)

        elif data == "asset_balance_weights":
            num_symbols = len(SYSTEM_CONFIG["ASSET_WEIGHTS"])
            if num_symbols == 0:
                safe_answer_callback(call.id, "❌ 当前没有监控的币对", show_alert=True)
                return
            with state_lock:
                avg_weight = round(1.0 / num_symbols, 4)
                for sym in SYSTEM_CONFIG["ASSET_WEIGHTS"]:
                    SYSTEM_CONFIG["ASSET_WEIGHTS"][sym] = avg_weight
                current_total = sum(SYSTEM_CONFIG["ASSET_WEIGHTS"].values())
                if abs(current_total - 1.0) > 0.0001 and len(SYSTEM_CONFIG["ASSET_WEIGHTS"]) > 0:
                    last_sym = list(SYSTEM_CONFIG["ASSET_WEIGHTS"].keys())[-1]
                    SYSTEM_CONFIG["ASSET_WEIGHTS"][last_sym] = round(SYSTEM_CONFIG["ASSET_WEIGHTS"][last_sym] + (1.0 - current_total), 4)
                save_data()
            safe_answer_callback(call.id, "✅ 权重已平均分配！")
            show_asset_settings_menu(chat_id, client, message_id=message_id)

        elif data.startswith("asset_remove_"):
            parts = data.split("_")
            sym = parts[2]
            page = int(parts[3])
            with state_lock:
                if sym in SYSTEM_CONFIG["ASSET_WEIGHTS"]:
                    del SYSTEM_CONFIG["ASSET_WEIGHTS"][sym]
                    save_data()
            normalize_weights(client)
            safe_answer_callback(call.id, f"✅ 已移除 {sym}")
            show_asset_settings_menu(chat_id, client, page=page, message_id=message_id)

        elif data.startswith("asset_add_"):
            parts = data.split("_")
            sym = parts[2]
            page = int(parts[3])
            max_symbols = SYSTEM_CONFIG.get("MAX_ACTIVE_SYMBOLS", 5)
            if len(SYSTEM_CONFIG["ASSET_WEIGHTS"]) >= max_symbols:
                safe_answer_callback(call.id, f"❌ 已达到最大允许币对数量 ({max_symbols})", show_alert=True)
                return
            with state_lock:
                SYSTEM_CONFIG["ASSET_WEIGHTS"][sym] = 1.0 / (len(SYSTEM_CONFIG["ASSET_WEIGHTS"]) + 1)
                save_data()
            normalize_weights(client)
            safe_answer_callback(call.id, f"✅ 已添加 {sym}")
            show_asset_settings_menu(chat_id, client, page=page, message_id=message_id)

        # 保险库回调
        elif data == "vault_manual_transfer":
            safe_answer_callback(call.id)
            manual_vault_transfer(chat_id, client)
        
        elif data == "confirm_manual_transfer":
            # 🔥 修复：手动划转确认 - 确保实盘 API 写权限检查
            try:
                safe_answer_callback(call.id, "🔄 正在执行划转...")
            except Exception:
                pass
            
            try:
                # 检查是否有 client 连接（实盘模式必需）
                if client is None:
                    try:
                        safe_answer_callback(call.id, "❌ 无API连接，无法执行实盘划转", show_alert=True)
                    except Exception:
                        pass
                    send_tg_msg("❌ <b>划转失败</b>\n\n无API连接，请检查网络或API配置")
                    return
                
                # 检查运行模式
                running_mode = SYSTEM_CONFIG.get("RUNNING_MODE", "SANDBOX")
                if running_mode == "SANDBOX":
                    try:
                        safe_answer_callback(call.id, "⚠️ 沙盒模式下无法执行真实划转", show_alert=True)
                    except Exception:
                        pass
                    send_tg_msg("⚠️ <b>划转被拒绝</b>\n\n当前为沙盒模式，无法执行真实资金划转")
                    return
                
                # 调用 execute_vault_transfer 执行划转
                from utils import execute_vault_transfer
                result = execute_vault_transfer(client)
                
                if result['success']:
                    try:
                        safe_answer_callback(call.id, f"✅ 划转成功: ${result['amount']:.2f}")
                    except Exception:
                        pass
                    send_tg_msg(
                        f"✅ <b>保险库划转成功</b>\n\n"
                        f"<b>划转金额:</b> <code>${result['amount']:.2f}</code>\n"
                        f"<b>当前余额:</b> <code>${result.get('new_balance', 0):.2f}</code>\n\n"
                        f"💰 资金已安全转入保险库"
                    )
                else:
                    try:
                        safe_answer_callback(call.id, f"❌ {result['message']}", show_alert=True)
                    except Exception:
                        pass
                    send_tg_msg(f"❌ <b>划转失败</b>\n\n{result['message']}")
                
                # 刷新保险库面板
                safe_delete_message(chat_id, message_id)
                handle_vault_panel(call.message, client)
                
            except Exception as transfer_e:
                logger.error(f"❌ 手动划转执行失败: {transfer_e}", exc_info=True)
                try:
                    safe_answer_callback(call.id, f"❌ 划转异常: {str(transfer_e)[:50]}", show_alert=True)
                except Exception:
                    pass
                send_tg_msg(f"❌ <b>划转异常</b>\n\n{str(transfer_e)[:200]}")

        elif data == "vault_set_ratio":
            safe_answer_callback(call.id)
            ask_withdraw_ratio(chat_id)
        
        elif data == "vault_toggle_adapt":
            safe_answer_callback(call.id)
            from bot_handlers import toggle_vault_adapt
            toggle_vault_adapt(chat_id, call.message, client)
        
        elif data == "back_to_vault":
            # 🔥 修复：返回保险库按钮 - 优化重绘延迟
            safe_answer_callback(call.id)
            
            # 立即删除当前消息，避免重绘延迟
            safe_delete_message(chat_id, message_id)
            
            # 使用异步方式重新显示保险库面板
            import threading
            def async_show_vault():
                try:
                    import time
                    time.sleep(0.1)  # 短暂延迟确保删除完成
                    handle_vault_panel(call.message, client)
                except Exception as e:
                    logger.error(f"❌ 异步显示保险库失败: {e}")
            
            threading.Thread(target=async_show_vault, daemon=True).start()

        # 价格监控回调
        elif data == "toggle_price_monitor":
            toggle_price_monitor(chat_id, message_id, callback_id=call.id)

        # 设置菜单扩展回调
        elif data == "settings_risk":
            safe_answer_callback(call.id)
            show_risk_settings_menu(chat_id, message_id)

        elif data == "settings_atr":
            safe_answer_callback(call.id)
            show_atr_settings_menu(chat_id, message_id)

        elif data == "settings_maddog":
            safe_answer_callback(call.id)
            show_mad_dog_settings_menu(chat_id, message_id)

        elif data == "settings_price":
            toggle_price_monitor(chat_id, message_id, callback_id=call.id)

        elif data == "settings_assets":
            safe_answer_callback(call.id)
            show_asset_settings_menu(chat_id, client, message_id=message_id)

        # 🎛️ 核心引擎开关面板
        elif data == "menu_engine_switches":
            text = (
                "🎛️ **核心引擎控制台**\n\n"
                "💡 *提示*：\n"
                "- 关闭风控过滤，开启 `SML放大器`，即可 100% 对齐回测暴利模式。\n"
                "- 强烈建议在任何时候保留 `黑天鹅防御` 为 ✅ 状态！"
            )
            from bot_handlers import get_engine_switches_keyboard
            safe_edit_message(
                chat_id, message_id,
                text=text, 
                reply_markup=get_engine_switches_keyboard(), 
                parse_mode='Markdown'
            )
            safe_answer_callback(call.id)

        elif data.startswith("toggle_eng_"):
            # 提取配置键名
            config_key = data.replace("toggle_eng_", "")
            
            # 翻转布尔状态
            with state_lock:
                current_state = config.SYSTEM_CONFIG.get(config_key, False)
                config.SYSTEM_CONFIG[config_key] = not current_state
                save_data()
            
            # 刷新键盘显示
            from bot_handlers import get_engine_switches_keyboard
            safe_edit_message(
                chat_id, message_id,
                text=(
                    "🎛️ **核心引擎控制台**\n\n"
                    "💡 *提示*：\n"
                    "- 关闭风控过滤，开启 `SML放大器`，即可 100% 对齐回测暴利模式。\n"
                    "- 强烈建议在任何时候保留 `黑天鹅防御` 为 ✅ 状态！"
                ),
                reply_markup=get_engine_switches_keyboard(),
                parse_mode='Markdown'
            )
            safe_answer_callback(call.id)

        elif data == "ignore":
            safe_answer_callback(call.id)

        # 启动向导回调
        elif data.startswith("launch_start_"):
            mode_key = data.replace("launch_start_", "")
            if mode_key in LAUNCH_MODE_MAP:
                mode_info = LAUNCH_MODE_MAP[mode_key]
                
                # 🔥 使用 .get() 方法提供默认值，防止 KeyError
                emoji = mode_info.get('emoji', '🚀')
                name = mode_info.get('name', mode_key)
                description = mode_info.get('description', '无说明')
                verification = mode_info.get('verification', True)
                dry_run = mode_info.get('dry_run', True)
                
                # 显示确认对话框
                markup = types.InlineKeyboardMarkup()
                markup.row(
                    types.InlineKeyboardButton("✅ 确认启动", callback_data=f"launch_confirm_{mode_key}"),
                    types.InlineKeyboardButton("❌ 取消", callback_data="launch_cancel")
                )
                
                msg = f"🚀 <b>启动确认</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
                msg += f"<b>模式:</b> {emoji} {name}\n"
                msg += f"<b>说明:</b> {description}\n\n"
                
                if verification:
                    msg += "🔍 <b>验证模式:</b> 开启\n"
                    msg += "• 所有信号需要人工确认\n"
                    msg += "• 适合谨慎观察和学习\n\n"
                else:
                    msg += "⚡ <b>自动模式:</b> 开启\n"
                    msg += "• 信号将自动执行\n"
                    msg += "• 请确保策略参数已优化\n\n"
                
                if dry_run:
                    msg += "🔍 <b>模拟交易:</b> 开启\n"
                    msg += "• 不会发送真实API请求\n"
                    msg += "• 安全测试策略逻辑\n\n"
                else:
                    msg += "🚨 <b>实盘交易:</b> 开启\n"
                    msg += "• 将发送真实API请求\n"
                    msg += "• 请确保账户资金充足\n\n"
                
                msg += "⚠️ <b>请确认是否启动引擎？</b>"
                
                safe_edit_message(chat_id, message_id, msg, parse_mode="HTML", reply_markup=markup)
                safe_answer_callback(call.id)

        elif data.startswith("launch_confirm_"):
            mode_key = data.replace("launch_confirm_", "")
            if mode_key in LAUNCH_MODE_MAP:
                mode_info = LAUNCH_MODE_MAP[mode_key]
                
                # 🔥 使用 .get() 方法提供默认值，防止 KeyError
                mode_emoji = mode_info.get('emoji', '🚀')
                mode_name = mode_info.get('name', mode_key)
                mode_verification = mode_info.get('verification', True)
                # 🔒 严禁从 mode_info 读取 dry_run，强制环境锁
                
                # 🔥 强制环境锁：DRY_RUN 必须等于 (RUNNING_MODE == "SANDBOX")
                running_mode = SYSTEM_CONFIG.get("RUNNING_MODE", "SANDBOX")
                forced_dry_run = (running_mode == "SANDBOX")
                
                # 应用配置
                with state_lock:
                    config.VERIFICATION_MODE = mode_verification
                    
                    # 🔒 环境锁：DRY_RUN 完全由 RUNNING_MODE 决定，与 mode_info 无关
                    SYSTEM_CONFIG["DRY_RUN"] = forced_dry_run
                    logger.info(f"🔒 环境锁生效：RUNNING_MODE={running_mode} → DRY_RUN={forced_dry_run}")
                    
                    save_data()
                
                # 启动引擎
                config.TRADING_ENGINE_ACTIVE = True
                
                safe_answer_callback(call.id, "✅ 引擎已启动")
                
                # 🔥 二次确认提示：明确告知用户当前运行环境
                if running_mode == "SANDBOX":
                    env_status = "🟡 模拟环境"
                    env_warning = "⚠️ 所有交易将在沙盒中模拟执行，不会产生真实资金变动"
                else:
                    env_status = "🔴 真实实盘"
                    env_warning = "⚠️ 警告：系统将执行真实交易，请确保风控参数已优化"
                
                msg = f"✅ <b>引擎启动成功</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
                msg += f"<b>运行模式:</b> {mode_emoji} {mode_name}\n"
                msg += f"<b>运行环境:</b> {env_status}\n"
                msg += f"<b>验证模式:</b> {'🔍 开启' if mode_verification else '⚡关闭'}\n"
                msg += f"<b>交易模式:</b> {'🔍 模拟' if SYSTEM_CONFIG.get('DRY_RUN') else '🚨 实盘'}\n\n"
                msg += f"{env_warning}\n\n"
                msg += "💡 引擎正在运行，您可以通过主菜单监控状态。"
                
                send_tg_msg(msg)
                safe_delete_message(chat_id, message_id)
                handle_start_command(call.message)

        elif data.startswith("launch_switch_"):
            mode_key = data.replace("launch_switch_", "")
            if mode_key in LAUNCH_MODE_MAP:
                mode_info = LAUNCH_MODE_MAP[mode_key]
                
                # 🔥 使用 .get() 方法提供默认值，防止 KeyError
                sw_emoji = mode_info.get('emoji', '🚀')
                sw_name = mode_info.get('name', mode_key)
                sw_description = mode_info.get('description', '无说明')
                
                # 显示切换确认对话框
                markup = types.InlineKeyboardMarkup()
                markup.row(
                    types.InlineKeyboardButton("✅ 确认切换", callback_data=f"launch_switch_confirm_{mode_key}"),
                    types.InlineKeyboardButton("❌ 取消", callback_data="launch_cancel")
                )
                
                msg = f"🔄 <b>切换模式确认</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
                msg += f"<b>目标模式:</b> {sw_emoji} {sw_name}\n"
                msg += f"<b>说明:</b> {sw_description}\n\n"
                msg += "⚠️ <b>注意:</b>\n"
                msg += "• 切换模式不会影响现有持仓\n"
                msg += "• 新信号将按新模式执行\n"
                msg += "• 建议在无持仓时切换\n\n"
                msg += "是否确认切换？"
                
                safe_edit_message(chat_id, message_id, msg, parse_mode="HTML", reply_markup=markup)
                safe_answer_callback(call.id)

        elif data.startswith("launch_switch_confirm_"):
            mode_key = data.replace("launch_switch_confirm_", "")
            if mode_key in LAUNCH_MODE_MAP:
                mode_info = LAUNCH_MODE_MAP[mode_key]
                
                # 🔥 使用 .get() 方法提供默认值，防止 KeyError
                mode_emoji = mode_info.get('emoji', '🚀')
                mode_name = mode_info.get('name', mode_key)
                mode_verification = mode_info.get('verification', True)
                # 🔒 严禁从 mode_info 读取 dry_run，强制环境锁
                
                # 🔥 强制环境锁：DRY_RUN 必须等于 (RUNNING_MODE == "SANDBOX")
                running_mode = SYSTEM_CONFIG.get("RUNNING_MODE", "SANDBOX")
                forced_dry_run = (running_mode == "SANDBOX")
                
                # 应用新配置
                with state_lock:
                    config.VERIFICATION_MODE = mode_verification
                    
                    # 🔒 环境锁：DRY_RUN 完全由 RUNNING_MODE 决定，与 mode_info 无关
                    SYSTEM_CONFIG["DRY_RUN"] = forced_dry_run
                    logger.info(f"🔒 环境锁生效：RUNNING_MODE={running_mode} → DRY_RUN={forced_dry_run}")
                    
                    save_data()
                
                safe_answer_callback(call.id, "✅ 模式已切换")
                
                # 🔥 二次确认提示：明确告知用户当前运行环境
                if running_mode == "SANDBOX":
                    env_status = "🟡 模拟环境"
                else:
                    env_status = "🔴 真实实盘"
                
                msg = f"✅ <b>模式切换成功</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
                msg += f"<b>新模式:</b> {mode_emoji} {mode_name}\n"
                msg += f"<b>运行环境:</b> {env_status}\n"
                msg += f"<b>验证模式:</b> {'🔍 开启' if mode_verification else '⚡关闭'}\n"
                msg += f"<b>交易模式:</b> {'🔍 模拟' if SYSTEM_CONFIG.get('DRY_RUN') else '🚨 实盘'}\n\n"
                msg += "💡 新配置已生效，后续信号将按新模式执行。"
                
                send_tg_msg(msg)
                
                # 返回启动向导
                from bot_handlers import show_launch_wizard
                safe_delete_message(chat_id, message_id)
                show_launch_wizard(chat_id, client)

        elif data == "launch_stop":
            # 显示停止确认对话框
            markup = types.InlineKeyboardMarkup()
            markup.row(
                types.InlineKeyboardButton("✅ 确认停止", callback_data="launch_stop_confirm"),
                types.InlineKeyboardButton("❌ 取消", callback_data="launch_cancel")
            )
            
            msg = "⏹️ <b>停止引擎确认</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
            msg += "⚠️ <b>注意:</b>\n"
            msg += "• 停止引擎将不再监控新信号\n"
            msg += "• 现有持仓不会自动平仓\n"
            msg += "• 您可以随时重新启动\n\n"
            msg += "是否确认停止引擎？"
            
            safe_edit_message(chat_id, message_id, msg, parse_mode="HTML", reply_markup=markup)
            safe_answer_callback(call.id)

        elif data == "launch_stop_confirm":
            config.TRADING_ENGINE_ACTIVE = False
            safe_answer_callback(call.id, "✅ 引擎已停止")
            
            msg = "⏹️ <b>引擎已停止</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
            msg += "引擎已安全停止，不再监控新信号。\n\n"
            msg += "💡 现有持仓保持不变，您可以通过主菜单管理。"
            
            send_tg_msg(msg)
            safe_delete_message(chat_id, message_id)
            handle_start_command(call.message)

        elif data == "toggle_hedge_mode":
            # 对冲模式切换回调
            with state_lock:
                hedge_current = SYSTEM_CONFIG.get("HEDGE_MODE_ENABLED", False)
                SYSTEM_CONFIG["HEDGE_MODE_ENABLED"] = not hedge_current
                save_data()
            
            new_state = SYSTEM_CONFIG["HEDGE_MODE_ENABLED"]
            state_text = "对冲模式 (多空异步并存)" if new_state else "单向模式 (多空自动互斥)"
            state_icon = "✅" if new_state else "❌"
            
            safe_answer_callback(call.id, f"{state_icon} 已切换到{state_text}")
            
            send_tg_msg(
                f"🔀 <b>持仓模式已切换</b>\n\n"
                f"<b>当前模式:</b> {state_icon} {state_text}\n\n"
                f"{'⚠️ 对冲模式下，同一币种可同时持有多单和空单，互不干扰。' if new_state else '⚠️ 单向模式下，反向信号将自动平掉现有持仓。'}\n\n"
                f"💡 引擎启动时将自动同步币安账户的持仓模式。"
            )
            
            # 刷新启动向导
            from bot_handlers import show_launch_wizard
            safe_delete_message(chat_id, message_id)
            show_launch_wizard(chat_id, client)

        elif data == "launch_cancel":
            safe_answer_callback(call.id, "❌ 已取消")
            safe_delete_message(chat_id, message_id)
            from bot_handlers import show_launch_wizard
            show_launch_wizard(chat_id, client)

        # 模拟账本回调
        elif data == "sim_ledger_refresh":
            safe_answer_callback(call.id, "🔄 刷新中...")
            safe_delete_message(chat_id, message_id)
            show_sim_ledger_center(chat_id, client)

        elif data == "sim_ledger_download":
            safe_answer_callback(call.id)
            import os
            csv_file = SYSTEM_CONFIG.get("SIM_REPORT_FILE", "simulated_ledger.csv")
            if os.path.exists(csv_file):
                try:
                    with open(csv_file, 'rb') as f:
                        bot.send_document(chat_id, f, caption="📊 模拟账本报表")
                    safe_answer_callback(call.id, "✅ 报表已发送")
                except Exception as e:
                    logger.error(f"发送账本文件失败: {e}")
                    safe_answer_callback(call.id, f"❌ 发送失败: {str(e)}", show_alert=True)
            else:
                safe_answer_callback(call.id, "❌ 账本文件不存在", show_alert=True)

        elif data == "sim_ledger_reset":
            safe_answer_callback(call.id)
            markup = types.InlineKeyboardMarkup()
            markup.row(
                types.InlineKeyboardButton("✅ 确认重置", callback_data="sim_ledger_reset_confirm"),
                types.InlineKeyboardButton("❌ 取消", callback_data="sim_ledger_refresh")
            )
            msg = "⚠️ <b>确认重置沙盒余额？</b>\n\n"
            msg += "此操作将:\n"
            msg += "• 重置余额为初始本金\n"
            msg += "• 不会清空交易记录\n\n"
            msg += "请确认是否继续？"
            safe_edit_message(chat_id, message_id, msg, parse_mode="HTML", reply_markup=markup)

        elif data == "sim_ledger_reset_confirm":
            with state_lock:
                initial = SYSTEM_CONFIG.get("SIM_INITIAL_BALANCE", 10000.0)
                SYSTEM_CONFIG["SIM_CURRENT_BALANCE"] = initial
                save_data()
            safe_answer_callback(call.id, "✅ 余额已重置")
            send_tg_msg(f"💰 <b>沙盒余额已重置</b>\n\n当前余额: <code>${initial:.2f}</code>")
            safe_delete_message(chat_id, message_id)
            show_sim_ledger_center(chat_id, client)

        elif data == "sim_ledger_clear":
            safe_answer_callback(call.id)
            markup = types.InlineKeyboardMarkup()
            markup.row(
                types.InlineKeyboardButton("✅ 确认清空", callback_data="sim_ledger_clear_confirm"),
                types.InlineKeyboardButton("❌ 取消", callback_data="sim_ledger_refresh")
            )
            msg = "⚠️ <b>确认清空交易记录？</b>\n\n"
            msg += "此操作将:\n"
            msg += "• 删除所有历史交易记录\n"
            msg += "• 不会重置余额\n"
            msg += "• <b>此操作不可恢复！</b>\n\n"
            msg += "请确认是否继续？"
            safe_edit_message(chat_id, message_id, msg, parse_mode="HTML", reply_markup=markup)

        elif data == "sim_ledger_clear_confirm":
            import os
            csv_file = SYSTEM_CONFIG.get("SIM_REPORT_FILE", "simulated_ledger.csv")
            if os.path.exists(csv_file):
                try:
                    os.remove(csv_file)
                    safe_answer_callback(call.id, "✅ 记录已清空")
                    send_tg_msg("🗑️ <b>交易记录已清空</b>\n\n所有历史记录已删除")
                except Exception as e:
                    logger.error(f"清空账本失败: {e}")
                    safe_answer_callback(call.id, f"❌ 清空失败: {str(e)}", show_alert=True)
            else:
                safe_answer_callback(call.id, "✅ 无记录需要清空")
            safe_delete_message(chat_id, message_id)
            show_sim_ledger_center(chat_id, client)

        # 🔥 子仓位控制回调
        elif data.startswith("protect_"):
            # 格式: protect_{symbol}_{pos_type} 或 protect_{trade_id}
            parts = data.split("_", 2)
            if len(parts) >= 3:
                symbol = parts[1]
                pos_type = parts[2]
                trade_key = f"{symbol}_{pos_type}"
            else:
                trade_key = parts[1]
            
            # 调用保本止损功能
            from trading_engine import update_sl_to_breakeven
            result = update_sl_to_breakeven(trade_key)
            
            if result['success']:
                safe_answer_callback(call.id, f"✅ {result['message']}")
                send_tg_msg(
                    f"🛡️ <b>保本止损已设置</b>\n\n"
                    f"标识: {html.escape(trade_key)}\n"
                    f"新止损价: <code>${result['new_sl_price']:.4f}</code>\n"
                    f"状态: 该单已设置为保本止损"
                )
            else:
                safe_answer_callback(call.id, f"❌ {result['message']}", show_alert=True)
        
        elif data.startswith("close_sub_"):
            # 格式: close_sub_{symbol}_{pos_type} 或 close_sub_{trade_id}
            parts = data.split("_", 3)
            if len(parts) >= 4:
                symbol = parts[2]
                pos_type = parts[3]
                trade_key = f"{symbol}_{pos_type}"
            else:
                trade_key = "_".join(parts[2:])
            
            # 获取持仓信息并平仓
            from trading_engine import get_position_by_key, execute_trade
            
            position_info = get_position_by_key(trade_key)
            if not position_info:
                safe_answer_callback(call.id, "❌ 未找到该笔订单", show_alert=True)
                return
            
            symbol = position_info.get('real_symbol', trade_key.split('_')[0])
            current_price = get_current_price(client, symbol)
            
            if not current_price:
                safe_answer_callback(call.id, "❌ 无法获取当前价格", show_alert=True)
                return
            
            # 执行平仓
            signal_type = 'SELL' if position_info['type'] == 'LONG' else 'BUY'
            result = execute_trade(
                client, symbol, signal_type, current_price,
                {'quantity': position_info['qty']},
                position_action='EXIT_LONG' if position_info['type'] == 'LONG' else 'EXIT_SHORT'
            )
            
            if result['success']:
                safe_answer_callback(call.id, "✅ 该单已强平")
                send_tg_msg(
                    f"🔥 <b>子仓位已强平</b>\n\n"
                    f"币种: {html.escape(symbol)}\n"
                    f"标识: {html.escape(trade_key)}\n"
                    f"平仓价: <code>${current_price:.4f}</code>\n"
                    f"净利: <code>${result.get('pnl', 0):.2f}</code>"
                )
            else:
                safe_answer_callback(call.id, f"❌ 平仓失败: {result['message']}", show_alert=True)
        
        # 🔥 决策审计系统：/trace 命令回调
        elif data.startswith("trace_"):
            trade_id = data.replace("trace_", "")
            from trading_engine import get_audit_log
            
            audit_log = get_audit_log(trade_id)
            if not audit_log:
                safe_answer_callback(call.id, "❌ 未找到该笔订单的审计日志", show_alert=True)
                return
            
            msg = f"📋 <b>决策审计日志</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
            msg += f"<b>Trade ID:</b> <code>{html.escape(trade_id)}</code>\n"
            msg += f"<b>币种:</b> {html.escape(audit_log.get('symbol', '?'))}\n"
            msg += f"<b>方向:</b> {audit_log.get('direction', '?')}\n"
            msg += f"<b>开仓时间:</b> {audit_log.get('timestamp', '?')}\n\n"
            
            msg += "<b>技术指标快照:</b>\n"
            msg += f"├ MACD_hist: <code>{audit_log.get('MACD_hist', 0):.6f}</code>\n"
            msg += f"├ Relative_ATR: <code>{audit_log.get('Relative_ATR', 0):.2f}</code>\n"
            msg += f"├ RSI: <code>{audit_log.get('RSI', 0):.2f}</code>\n"
            msg += f"├ Squeeze_On: <code>{audit_log.get('Squeeze_On', False)}</code>\n"
            msg += f"├ ADX: <code>{audit_log.get('ADX', 0):.2f}</code>\n"
            msg += f"└ EMA_TREND: <code>{audit_log.get('EMA_TREND', 0):.2f}</code>\n\n"
            
            msg += "<b>决策信息:</b>\n"
            msg += f"├ 信号类型: {audit_log.get('signal_type', '?')}\n"
            msg += f"├ 信号强度: {audit_log.get('signal_strength', '?')}\n"
            msg += f"└ 决策理由: {audit_log.get('decision_reason', '?')}\n"
            
            safe_send_message(chat_id, msg, parse_mode="HTML")
            safe_answer_callback(call.id, "✅ 审计日志已发送")

        # 🤖 AI战略战报：应用推荐策略
        elif data.startswith("apply_strategy:"):
            mode_key = data.split(":")[1]
            if mode_key in STRATEGY_PRESETS:
                if apply_strategy_preset(mode_key):
                    save_data()  # 🔥 修复：AI建议应用后立即持久化
                    preset = STRATEGY_PRESETS[mode_key]
                    safe_answer_callback(call.id, f"✅ 已应用 {preset['name']}")
                    
                    send_tg_msg(
                        f"⚡ <b>AI建议已应用</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
                        f"<b>新策略:</b> {preset['emoji']} {preset['name']}\n"
                        f"<b>K线周期:</b> <code>{SYSTEM_CONFIG['INTERVAL']}</code>\n"
                        f"<b>ADX阈值:</b> <code>{SYSTEM_CONFIG['ADX_THR']}</code>\n"
                        f"<b>EMA趋势:</b> <code>{SYSTEM_CONFIG['EMA_TREND']}</code>\n"
                        f"<b>ATR倍数:</b> <code>{SYSTEM_CONFIG['ATR_MULT']}</code>\n"
                        f"<b>杠杆:</b> <code>{SYSTEM_CONFIG.get('LEVERAGE', 20)}x</code>\n\n"
                        f"✅ 策略参数已实时注入，下一个扫描周期将自动生效。"
                    )
                    
                    # 更新原消息，移除按钮
                    try:
                        safe_edit_message(
                            chat_id, message_id,
                            call.message.text + f"\n\n✅ <b>已应用: {preset['name']}</b>",
                            parse_mode="HTML"
                        )
                    except:
                        pass
                else:
                    safe_answer_callback(call.id, "❌ 策略应用失败", show_alert=True)
            else:
                safe_answer_callback(call.id, f"❌ 未知策略: {mode_key}", show_alert=True)

        elif data == "view_strategy_details":
            safe_answer_callback(call.id)
            msg = "📋 <b>策略模式详情</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
            for key, preset in STRATEGY_PRESETS.items():
                current = " 👈 当前" if key == SYSTEM_CONFIG.get("STRATEGY_MODE") else ""
                msg += f"{preset['emoji']} <b>{preset['name']}</b>{current}\n"
                msg += f"   {preset['description']}\n\n"
            safe_send_message(chat_id, msg, parse_mode="HTML")

        # 🔥 AI 满血接管模式确认/撤销回调（合并处理）
        elif data in ("confirm_ai_autonomy", "confirm_revoke_ai_autonomy"):
            # 🔒 统一处理激活与撤销：根据 callback_data 决定目标状态
            is_activating = (data == "confirm_ai_autonomy")
            
            with state_lock:
                SYSTEM_CONFIG["AI_FULL_AUTONOMY_MODE"] = is_activating
                save_data()
            
            if is_activating:
                safe_answer_callback(call.id, "🤖 AI 满血指挥权已激活")
                # 🧠 合并后的统一激活提示
                msg = "🧠 <b>幽灵模式已接管，AI 将自主执行战术</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
                msg += "✅ <b>已授权 AI 执行以下操作:</b>\n"
                msg += "├ 自主开仓/平仓决策\n"
                msg += "├ 动态调整策略参数\n"
                msg += "├ 风险管理与止损控制\n"
                msg += "└ 市场异常应急响应\n\n"
                msg += "⚠️ 可随时点击【🧠 幽灵接管中】撤销授权"
                logger.info(f"🤖 AI 满血接管模式已激活 by {chat_id}")
            else:
                safe_answer_callback(call.id, "🔒 AI 指挥权已撤销")
                msg = "🔒 <b>AI 指挥权已撤销</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
                msg += "✅ 已恢复人工控制模式\n"
                msg += "• AI 将继续提供分析建议\n"
                msg += "• 所有交易需您手动确认\n\n"
                msg += "💡 可随时点击【🤖 激活满血 AI 指挥权】重新授权"
                logger.info(f"🔒 AI 满血接管模式已撤销 by {chat_id}")
            
            safe_edit_message(chat_id, message_id, msg, parse_mode="HTML")
            
            # 3秒后返回交易菜单
            import time
            time.sleep(3)
            from bot_handlers import create_main_menu
            msg_text, markup = create_main_menu()
            safe_send_message(chat_id, msg_text, parse_mode="HTML", reply_markup=markup)

        elif data in ("cancel_ai_autonomy", "cancel_revoke_ai_autonomy"):
            # 🔒 统一处理取消操作
            safe_answer_callback(call.id, "❌ 已取消操作")
            
            current_state = SYSTEM_CONFIG.get("AI_FULL_AUTONOMY_MODE", False)
            state_desc = "AI 满血接管模式继续运行" if current_state else "AI 满血接管模式未激活"
            
            msg = f"❌ <b>操作已取消</b>\n\n{state_desc}，系统保持当前状态。"
            
            safe_edit_message(chat_id, message_id, msg, parse_mode="HTML")
            
            # 3秒后返回交易菜单
            import time
            time.sleep(3)
            from bot_handlers import create_main_menu
            msg_text, markup = create_main_menu()
            safe_send_message(chat_id, msg_text, parse_mode="HTML", reply_markup=markup)        
        # 🔥 V7.0: param_ 回调已由 bot_handlers.py 的泛型修改器优先拦截，此处不再处理

        else:
            safe_answer_callback(call.id, "⚠️ 未知操作")

    except Exception as e:
        logger.error(f"❌ 回调处理失败: {e}", exc_info=True)
        safe_answer_callback(call.id, f"❌ 操作失败: {str(e)}")
