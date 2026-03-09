#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram Bot 命令处理器
处理所有用户交互、命令、回调和界面显示
"""

import html
import time
from datetime import datetime
from functools import wraps
from telebot import types
from telebot import apihelper
from config import (
    SYSTEM_CONFIG, ACTIVE_POSITIONS, SENTRY_CONFIG, SENTRY_INTERVAL_OPTIONS,
    STRATEGY_PRESETS, TRADE_HISTORY, LAUNCH_MODE_MAP, positions_lock,
    save_data, state_lock, save_sentry_watchlist
)
import config
from utils import (
    get_current_price, get_24h_change, get_all_valid_symbols, search_symbols_fuzzy,
    safe_send_message, safe_edit_message, safe_delete_message, safe_answer_callback,
    send_tg_msg, get_bot, create_progress_bar, normalize_weights
)
from logger_setup import logger
from network_config import get_telebot_proxy
from human_override import get_override_manager

# ==========================================
# 🔥 Task 3: TeleBot 代理配置（严格指向 http://127.0.0.1:4780）
# ==========================================
# 🔥 强制使用硬编码代理地址，确保 Telegram 通信链路稳定
_PROXY_URL = "http://127.0.0.1:4780"
apihelper.proxy = {'http': _PROXY_URL, 'https': _PROXY_URL}
logger.info(f"✅ TeleBot 代理已强制配置: {_PROXY_URL}")

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
    """创建主菜单键盘"""
    markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    markup.add(
        types.KeyboardButton("📊 仪表盘"),
        types.KeyboardButton("💼 我的持仓"),
        types.KeyboardButton("▶️ 启动交易"),
        types.KeyboardButton("⏹️ 停止交易"),
        types.KeyboardButton("🎯 策略中心"),
        types.KeyboardButton("🏦 保险库"),
        types.KeyboardButton("🔭 价格哨所"),
        types.KeyboardButton("📈 行情分析"),
        types.KeyboardButton("📋 交易记录"),
        types.KeyboardButton("📒 模拟账本"),
        types.KeyboardButton("⚙️ 设置"),
    )
    return markup

def create_trading_menu():
    """创建交易控制菜单"""
    markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    markup.add(
        types.KeyboardButton("📈 查看持仓"),
        types.KeyboardButton("⚖️ 同步真实仓位"),
        types.KeyboardButton("🔍 验证模式"),
        types.KeyboardButton("🔥 实盘模式"),
        types.KeyboardButton("🛑 一键全平"),
        types.KeyboardButton("🔙 返回主菜单")
    )
    return markup

def create_vault_menu():
    """创建保险库管理菜单 - 自适应引擎 v2.0"""
    auto_adapt = SYSTEM_CONFIG.get("VAULT_AUTO_ADAPT", True)
    
    markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    markup.add(
        types.KeyboardButton("✅ 开启保险库"),
        types.KeyboardButton("❌ 关闭保险库"),
        types.KeyboardButton("📊 保险库状态"),
        types.KeyboardButton("⚙️ 设置划转比例"),
        types.KeyboardButton("💰 手动划转"),
        types.KeyboardButton("🤖 自适应阈值" if not auto_adapt else "📌 固定阈值"),
        types.KeyboardButton("🔙 返回主菜单")
    )
    return markup

# ==========================================
# 基础命令处理函数
# ==========================================

@require_auth
def handle_start_command(message):
    """处理 /start 和 /help 命令"""
    welcome_msg = (
        "🤖 <b>欢迎使用无界指挥部量化机器人</b>\n\n"
        "<b>主要功能：</b>\n"
        "• 📊 实时价格监控\n"
        "• 🚀 智能量化交易\n"
        "• 🏦 保险库利润保护\n"
        "• 📈 多维指标分析\n\n"
        "<b>使用方式：</b>\n"
        "1. 点击下方按钮选择功能\n"
        "2. 或使用命令如 /dashboard 查看仪表盘\n"
        "3. 所有操作都有中文提示\n\n"
        "👇 <b>请选择您要进行的操作：</b>"
    )
    markup = create_main_menu()
    safe_send_message(message.chat.id, welcome_msg, parse_mode="HTML", reply_markup=markup)

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
    显示实时仪表盘（🔥 V5.1 重构：使用后台常驻数据流）
    
    核心变更：
    - 优先从 trading_engine 的指标缓存读取实时数据
    - 即使交易引擎暂停，仪表盘仍显示最新市场数据
    """
    chat_id = message.chat.id
    
    try:
        equity = 0.0
        available = 0.0
        unrealized_pnl = 0.0
        
        if client and not config.VERIFICATION_MODE:
            try:
                acc = client.futures_account()
                equity = float(acc.get('totalMarginBalance', 0))
                available = float(acc.get('availableBalance', 0))
                unrealized_pnl = float(acc.get('totalUnrealizedProfit', 0))
            except:
                pass
        
        if equity == 0:
            equity = SYSTEM_CONFIG.get("BENCHMARK_CASH", 1800.0)
            available = equity
        
        benchmark = SYSTEM_CONFIG.get("BENCHMARK_CASH", 1800.0)
        total_pnl = equity - benchmark
        pnl_pct = (total_pnl / benchmark * 100) if benchmark > 0 else 0
        
        # 🔒 使用 state_lock 保护 PEAK_EQUITY 读取（防止竞态条件）
        with state_lock:
            peak = SYSTEM_CONFIG["PEAK_EQUITY"] if SYSTEM_CONFIG["PEAK_EQUITY"] > 0 else equity
        drawdown = ((peak - equity) / peak * 100) if peak > 0 and equity < peak else 0
        
        win_rate = 0
        if len(TRADE_HISTORY) > 0:
            wins = sum(1 for t in TRADE_HISTORY if t.get('pnl', 0) > 0)
            win_rate = (wins / len(TRADE_HISTORY)) * 100
        
        used_margin = equity - available
        margin_usage = (used_margin / equity * 100) if equity > 0 else 0
        
        current_mode = SYSTEM_CONFIG.get("STRATEGY_MODE", "STANDARD")
        mode_preset = STRATEGY_PRESETS.get(current_mode, STRATEGY_PRESETS["STANDARD"])
        
        # 🔥 系统状态红绿灯
        engine_light = "🟢" if config.TRADING_ENGINE_ACTIVE else "🔴"
        dry_run_light = "🟡" if SYSTEM_CONFIG.get("DRY_RUN", False) else "🟢"
        status_line = f"{engine_light} 引擎 | {dry_run_light} {'模拟' if SYSTEM_CONFIG.get('DRY_RUN', False) else '实盘'}"
        
        msg = f"📊 <b>实时仪表盘</b> {status_line}\n━━━━━━━━━━━━━━━━━━━━\n\n"
        msg += "💰 <b>账户概览</b>\n"
        msg += f"├ 总权益: <code>${equity:.2f}</code>\n"
        msg += f"├ 可用余额: <code>${available:.2f}</code>\n"
        msg += f"├ 未实现盈亏: <code>${unrealized_pnl:+.2f}</code>\n"
        
        pnl_emoji = "🟢" if total_pnl > 0 else "🔴" if total_pnl < 0 else "⚪"
        pnl_bar = create_progress_bar(pnl_pct, 100, 10)
        msg += f"├ 总盈亏: {pnl_emoji} <code>${total_pnl:+.2f}</code> ({pnl_pct:+.2f}%)\n"
        msg += f"│ {pnl_bar}\n"
        
        dd_emoji = "🟢" if drawdown < 5 else "🟡" if drawdown < 15 else "🔴"
        msg += f"├ 最大回撤: {dd_emoji} <code>{drawdown:.2f}%</code>\n"
        msg += f"├ 📊 动态基准: <code>${SYSTEM_CONFIG.get('BENCHMARK_CASH', 0):.2f}</code>\n"
        
        wr_emoji = "🟢" if win_rate >= 60 else "🟡" if win_rate >= 50 else "🔴"
        msg += f"└ 历史胜率: {wr_emoji} <code>{win_rate:.1f}%</code> ({len(TRADE_HISTORY)}笔)\n\n"
        
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
                
                capital_usage = (total_position_value / equity * 100) if equity > 0 else 0
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
        markup = create_trading_menu()
        safe_send_message(chat_id, msg, parse_mode="HTML", reply_markup=markup)
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
    
    markup = create_vault_menu()
    safe_send_message(chat_id, msg, parse_mode="HTML", reply_markup=markup)

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
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🔄 刷新价格", callback_data="refresh_prices"))
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
    """显示模拟账本中心面板（含凯利公式绩效指标）"""
    import os
    import csv
    
    owner_chat_id = str(SYSTEM_CONFIG.get("TG_CHAT_ID", ""))
    if str(chat_id) != owner_chat_id:
        return
    
    sim_balance = SYSTEM_CONFIG.get("SIM_CURRENT_BALANCE", 0.0)
    sim_initial = SYSTEM_CONFIG.get("SIM_INITIAL_BALANCE", 10000.0)
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
        
        if user_text == "📊 仪表盘":
            handle_dashboard(message, client)
        elif user_text == "💼 我的持仓":
            handle_positions(message, client)
        elif user_text == "▶️ 启动交易":
            show_launch_wizard(chat_id, client)
        elif user_text == "⏹️ 停止交易":
            config.TRADING_ENGINE_ACTIVE = False
            safe_send_message(chat_id, "⏹️ 交易引擎已停止", parse_mode="HTML")
        elif user_text == "🎯 策略中心":
            show_strategy_center(chat_id, client)
        elif user_text == "🏦 保险库":
            handle_vault_panel(message, client)
        elif user_text == "🤖 自适应阈值":
            toggle_vault_adapt(chat_id, message, client)
        elif user_text == "📌 固定阈值":
            toggle_vault_adapt(chat_id, message, client)
        elif user_text == "🔭 价格哨所":
            handle_sentry_panel(message, client)
        elif user_text == "📈 行情分析":
            show_real_time_prices(chat_id, client)
        elif user_text == "📋 交易记录":
            msg = "📋 <b>交易记录</b>\n\n"
            if len(TRADE_HISTORY) > 0:
                msg += f"总交易次数: {len(TRADE_HISTORY)}\n"
                wins = sum(1 for t in TRADE_HISTORY if t.get('pnl', 0) > 0)
                msg += f"盈利次数: {wins}\n"
                msg += f"胜率: {wins/len(TRADE_HISTORY)*100:.1f}%\n"
            else:
                msg += "暂无交易记录"
            safe_send_message(chat_id, msg, parse_mode="HTML")
        elif user_text == "📒 模拟账本":
            show_sim_ledger_center(chat_id, client)
        elif user_text == "⚙️ 设置":
            show_settings_menu(chat_id, client=client)
        else:
            # 🔥 Task 2: 自由对话路由 (AI 全面接管) - 增强分析报告格式化
            bot.send_chat_action(chat_id, 'typing')
            try:
                # 延迟初始化，避免循环导入
                from ai_analyst import AICommander
                current_commander = AICommander()
                
                ai_reply = current_commander.ask_commander(user_text)

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
                
                # 🔥 Task 2: 如果是分析模式且有 JSON 数据，格式化输出
                if analysis_data and not config.TRADING_ENGINE_ACTIVE:
                    formatted_report = _format_analysis_report(analysis_data, clean_text)
                    safe_send_message(chat_id, formatted_report, parse_mode="HTML")
                elif clean_text:
                    safe_send_message(chat_id, clean_text, parse_mode="HTML")
                    
            except Exception as e:
                logger.error(f"AI 响应失败: {e}", exc_info=True)
                # 容错降级 - 提供返回主菜单按钮
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("🔙 返回主菜单", callback_data="back_to_main"))
                safe_send_message(
                    chat_id, 
                    f"❌ 系统错误，AI 核心离线: {str(e)[:50]}",
                    reply_markup=markup
                )
    
    # 注册callback_query_handler
    from bot_callbacks import handle_callback
    @bot.callback_query_handler(func=lambda call: True)
    def callback_handler(call):
        handle_callback(call, client)
    
    logger.info("✅ 消息处理器注册完成")

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


def manual_vault_transfer(chat_id, client):
    """手动触发一次保险库划转检查与执行"""
    from utils import execute_vault_transfer
    
    vault_enabled = SYSTEM_CONFIG.get("VAULT_ENABLED", False)
    
    if not vault_enabled:
        safe_send_message(
            chat_id,
            "⚠️ <b>保险库未启用</b>\n\n请先启用保险库功能。",
            parse_mode="HTML"
        )
        return
    
    safe_send_message(
        chat_id,
        "🔄 <b>正在执行手动划转检查...</b>",
        parse_mode="HTML"
    )
    
    try:
        result = execute_vault_transfer(client)
        
        if result['success']:
            msg = "✅ <b>手动划转执行成功</b>\n\n"
            msg += f"划转金额: <code>${result['amount']:.2f}</code>\n"
            msg += f"累计保险库余额: <code>${result['vault_balance']:.2f}</code>\n"
            msg += f"新基准本金: <code>${result['new_benchmark']:.2f}</code>"
        else:
            msg = f"ℹ️ <b>划转未执行</b>\n\n{result['message']}"
        
        safe_send_message(chat_id, msg, parse_mode="HTML")
        
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
    执行 AI 自动调参（静默授权，无需 /confirm）
    
    核心逻辑：
    1. 验证参数是否在安全边界内
    2. 检查是否触碰禁止修改的参数
    3. 直接应用修改并发送高优先级通知
    
    Args:
        ai_json: AI 返回的 JSON 数据，格式：
        {
            "need_tune": true,
            "tune_params": {
                "ADX_THR": 10,
                "ATR_MULT": 2.5
            },
            "reasoning": "当前波动率上升，建议放宽ATR倍数..."
        }
    
    Returns:
        dict: {
            'success': bool,
            'message': str,
            'applied_params': dict
        }
    """
    try:
        tune_params = ai_json.get('tune_params', {})
        reasoning = ai_json.get('reasoning', '无说明')
        
        if not tune_params:
            return {
                'success': False,
                'message': 'AI 未提供调参建议',
                'applied_params': {}
            }
        
        # 🔥 Step 1: 验证参数是否在安全边界内
        from config import AUTO_TUNE_BOUNDARIES, AUTO_TUNE_FORBIDDEN_PARAMS
        
        rejected_params = []
        out_of_bounds = []
        corrected_params = {}
        
        for param_name, param_value in tune_params.items():
            # 检查是否为禁止修改的参数
            if param_name in AUTO_TUNE_FORBIDDEN_PARAMS:
                rejected_params.append(param_name)
                continue
            
            # 检查是否在安全边界内，如果越界则强制修正
            if param_name in AUTO_TUNE_BOUNDARIES:
                min_val, max_val = AUTO_TUNE_BOUNDARIES[param_name]
                if param_value < min_val:
                    corrected_params[param_name] = {
                        'original': param_value,
                        'corrected': min_val,
                        'reason': f'低于最小值 {min_val}'
                    }
                    tune_params[param_name] = min_val
                elif param_value > max_val:
                    corrected_params[param_name] = {
                        'original': param_value,
                        'corrected': max_val,
                        'reason': f'超过最大值 {max_val}'
                    }
                    tune_params[param_name] = max_val
        
        # 如果有禁止修改的参数，拒绝整个调参请求
        if rejected_params:
            error_msg = "⚠️ <b>AI 调参被拒绝（触碰禁止参数）</b>\n\n"
            error_msg += "<b>禁止修改的参数:</b>\n"
            for p in rejected_params:
                error_msg += f"• {p}\n"
            error_msg += f"\n<b>AI 原因:</b> {reasoning}"
            
            send_tg_msg(error_msg)
            logger.warning(f"⚠️ AI 调参被拒绝（禁止参数）: {rejected_params}")
            
            return {
                'success': False,
                'message': error_msg,
                'applied_params': {}
            }
        
        # 🔥 Step 2: 应用参数修改（静默授权，越界参数已自动修正）
        applied_params = {}
        old_values = {}
        
        with state_lock:
            for param_name, param_value in tune_params.items():
                if param_name not in rejected_params and param_name in SYSTEM_CONFIG:
                    old_values[param_name] = SYSTEM_CONFIG[param_name]
                    SYSTEM_CONFIG[param_name] = param_value
                    applied_params[param_name] = param_value
                    logger.info(f"✅ AI 自动调参: {param_name} = {param_value} (旧值: {old_values[param_name]})")
            
            save_data()
        
        # 🔥 Step 3: 发送高优先级 Telegram 通知
        msg = "🤖 <b>AI 自适应巡航微调已生效</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
        msg += "🧪 <b>状态:</b> 模拟盘测试中 (Dry Run)\n\n"
        msg += "📊 <b>参数变更:</b>\n"
        
        for param_name, new_value in applied_params.items():
            old_value = old_values.get(param_name, '未知')
            msg += f"├ {param_name}: <code>{old_value}</code> → <code>{new_value}</code>\n"
            
            # 如果该参数被修正，显示修正信息
            if param_name in corrected_params:
                correction = corrected_params[param_name]
                msg += f"│ ⚠️ 已修正: {correction['original']} → {correction['corrected']} ({correction['reason']})\n"
        
        msg += f"\n💡 <b>AI 分析:</b>\n{reasoning}\n\n"
        
        # 显示市场状态快照（从指标缓存获取）
        try:
            from trading_engine import get_indicator_cache
            indicator_cache = get_indicator_cache()
            if indicator_cache and isinstance(indicator_cache, dict):
                msg += "📈 <b>市场状态快照:</b>\n"
                for key, value in list(indicator_cache.items())[:5]:
                    msg += f"├ {key}: <code>{value}</code>\n"
                msg += "\n"
        except Exception as e:
            logger.debug(f"获取指标缓存失败（非致命）: {e}")
        
        msg += f"⏰ 调参时间: <i>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</i>\n"
        msg += f"🛡️ 下次调参冷却: <i>2小时后</i>"
        
        send_tg_msg(msg)
        logger.info(f"✅ AI 自动调参成功: {applied_params}")
        
        return {
            'success': True,
            'message': msg,
            'applied_params': applied_params
        }
    
    except Exception as e:
        error_msg = f"❌ AI 自动调参执行失败: {str(e)[:100]}"
        logger.error(f"❌ execute_auto_tune 异常: {e}", exc_info=True)
        send_tg_msg(error_msg)
        
        return {
            'success': False,
            'message': error_msg,
            'applied_params': {}
        }


# ==========================================
# 导出所有处理函数
# ==========================================

__all__ = [
    'create_main_menu',
    'create_trading_menu',
    'create_vault_menu',
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
]
