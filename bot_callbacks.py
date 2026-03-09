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
    STRATEGY_PRESETS, TRADE_HISTORY, LAUNCH_MODE_MAP,
    save_data, save_sentry_watchlist, mark_custom_mode, apply_strategy_preset,
    state_lock
)
import config
from utils import (
    get_current_price, get_all_valid_symbols,
    safe_send_message, safe_edit_message, safe_delete_message, safe_answer_callback,
    send_tg_msg, get_bot, normalize_weights
)
from human_override import get_override_manager

# 🔥 修复：将可能引发循环导入的模块改为延迟导入
# from trading_engine import sync_positions, emergency_close_all  # 已移至函数内部
# from monitors import push_sentry_price_report                   # 已移至函数内部


def handle_callback(call, client):
    """处理所有回调查询"""
    bot = get_bot()

    # ====== 🔥 Step 1: 立即应答回调，防止按钮转圈卡死 ======
    try:
        if bot:
            bot.answer_callback_query(call.id)
    except Exception as e:
        logger.warning(f"⚠️ answer_callback_query 失败 (非致命): {e}")
    # =========================================================

    # ====== 🔥 Step 2: 调试日志 - 确认回调已到达 ======
    logger.info(f"DEBUG: Callback Received - Data: {call.data}, ChatID: {call.message.chat.id}")
    # ===================================================

    # 延迟导入避免循环引用
    from bot_handlers import (
        handle_start_command, handle_dashboard, handle_positions,
        handle_vault_panel, handle_sentry_panel,
        show_strategy_center,
        show_settings_menu, show_indicators_settings,
        show_risk_settings_menu, show_atr_settings_menu, show_mad_dog_settings_menu,
        show_real_time_prices, show_sentry_interval_menu,
        show_asset_settings_menu, toggle_price_monitor,
        enable_vault, disable_vault, show_vault_status,
        manual_vault_transfer, ask_withdraw_ratio,
        process_custom_input, process_asset_search, process_sentry_add_symbol,
        create_vault_menu, show_sim_ledger_center
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
            safe_delete_message(chat_id, message_id)
            handle_start_command(call.message)

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

        elif data == "show_strategy_center":
            safe_delete_message(chat_id, message_id)
            show_strategy_center(chat_id, client)

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
            enable_vault(chat_id)

        elif data == "vault_disable":
            disable_vault(chat_id)

        elif data == "vault_status":
            show_vault_status(chat_id)

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
            show_real_time_prices(chat_id, client, message_id)

        # 参数设置回调
        elif data.startswith("set_"):
            parts = data.split("_")
            val_str = parts[-1]
            param = "_".join(parts[1:-1])
            try:
                if val_str in ['0', '1']:
                    val = True if val_str == '1' else False
                elif '.' in val_str:
                    val = float(val_str)
                else:
                    val = int(val_str)
                with state_lock:
                    SYSTEM_CONFIG[param] = val
                    # 🔥 自动触发自定义模式
                    mark_custom_mode(param)
                    save_data()
                
                #  锁定参数，防止被自动覆盖
                get_override_manager().lock_parameter(param, val, reason="Telegram 按钮修改")
                
                safe_answer_callback(call.id, f"✅ 已修改 {param} = {val}")
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

        elif data == "vault_set_ratio":
            safe_answer_callback(call.id)
            ask_withdraw_ratio(chat_id)

        # 价格监控回调
        elif data == "toggle_price_monitor":
            safe_answer_callback(call.id)
            toggle_price_monitor(chat_id, message_id)

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
            safe_answer_callback(call.id)
            toggle_price_monitor(chat_id, message_id)

        elif data == "settings_assets":
            safe_answer_callback(call.id)
            show_asset_settings_menu(chat_id, client, message_id=message_id)

        elif data == "ignore":
            safe_answer_callback(call.id)

        # 启动向导回调
        elif data.startswith("launch_start_"):
            mode_key = data.replace("launch_start_", "")
            if mode_key in LAUNCH_MODE_MAP:
                mode_info = LAUNCH_MODE_MAP[mode_key]
                
                # 显示确认对话框
                markup = types.InlineKeyboardMarkup()
                markup.row(
                    types.InlineKeyboardButton("✅ 确认启动", callback_data=f"launch_confirm_{mode_key}"),
                    types.InlineKeyboardButton("❌ 取消", callback_data="launch_cancel")
                )
                
                msg = f"🚀 <b>启动确认</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
                msg += f"<b>模式:</b> {mode_info['emoji']} {mode_info['name']}\n"
                msg += f"<b>说明:</b> {mode_info['description']}\n\n"
                
                if mode_info['verification']:
                    msg += "🔍 <b>验证模式:</b> 开启\n"
                    msg += "• 所有信号需要人工确认\n"
                    msg += "• 适合谨慎观察和学习\n\n"
                else:
                    msg += "⚡ <b>自动模式:</b> 开启\n"
                    msg += "• 信号将自动执行\n"
                    msg += "• 请确保策略参数已优化\n\n"
                
                if mode_info['dry_run']:
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
                
                # 应用配置
                with state_lock:
                    config.VERIFICATION_MODE = mode_info['verification']
                    SYSTEM_CONFIG["DRY_RUN"] = mode_info['dry_run']
                    save_data()
                
                # 启动引擎
                config.TRADING_ENGINE_ACTIVE = True
                
                safe_answer_callback(call.id, "✅ 引擎已启动")
                
                msg = f"✅ <b>引擎启动成功</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
                msg += f"<b>运行模式:</b> {mode_info['emoji']} {mode_info['name']}\n"
                msg += f"<b>验证模式:</b> {'🔍 开启' if mode_info['verification'] else '⚡关闭'}\n"
                msg += f"<b>交易模式:</b> {'🔍 模拟' if mode_info['dry_run'] else '🚨 实盘'}\n\n"
                msg += "💡 引擎正在运行，您可以通过主菜单监控状态。"
                
                send_tg_msg(msg)
                safe_delete_message(chat_id, message_id)
                handle_start_command(call.message)

        elif data.startswith("launch_switch_"):
            mode_key = data.replace("launch_switch_", "")
            if mode_key in LAUNCH_MODE_MAP:
                mode_info = LAUNCH_MODE_MAP[mode_key]
                
                # 显示切换确认对话框
                markup = types.InlineKeyboardMarkup()
                markup.row(
                    types.InlineKeyboardButton("✅ 确认切换", callback_data=f"launch_switch_confirm_{mode_key}"),
                    types.InlineKeyboardButton("❌ 取消", callback_data="launch_cancel")
                )
                
                msg = f"🔄 <b>切换模式确认</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
                msg += f"<b>目标模式:</b> {mode_info['emoji']} {mode_info['name']}\n"
                msg += f"<b>说明:</b> {mode_info['description']}\n\n"
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
                
                # 应用新配置
                with state_lock:
                    config.VERIFICATION_MODE = mode_info['verification']
                    SYSTEM_CONFIG["DRY_RUN"] = mode_info['dry_run']
                    save_data()
                
                safe_answer_callback(call.id, "✅ 模式已切换")
                
                msg = f"✅ <b>模式切换成功</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
                msg += f"<b>新模式:</b> {mode_info['emoji']} {mode_info['name']}\n"
                msg += f"<b>验证模式:</b> {'🔍 开启' if mode_info['verification'] else '⚡关闭'}\n"
                msg += f"<b>交易模式:</b> {'🔍 模拟' if mode_info['dry_run'] else '🚨 实盘'}\n\n"
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
            from bot_handlers import show_launch_wizard
            safe_answer_callback(call.id, "❌ 已取消")
            safe_delete_message(chat_id, message_id)
            show_launch_wizard(chat_id, client)

        # 模拟账本回调
        elif data == "sim_ledger_refresh":
            safe_answer_callback(call.id, "🔄 刷新中...")
            safe_delete_message(chat_id, message_id)
            show_sim_ledger_center(chat_id, client)

        elif data == "sim_ledger_download":
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

        else:
            safe_answer_callback(call.id, "⚠️ 未知操作")

    except Exception as e:
        logger.error(f"❌ 回调处理失败: {e}", exc_info=True)
        safe_answer_callback(call.id, f"❌ 操作失败: {str(e)}")
