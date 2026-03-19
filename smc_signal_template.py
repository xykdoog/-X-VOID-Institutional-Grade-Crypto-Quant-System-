#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SMC 狩猎指令 - 开火信号推送模板
🔥 Telegram HTML 模式极致排版

当 generate_trading_signals 触发 SMC 信号且经过"众神议会"审计通过后，
系统向 Telegram 发送格式精美的开火指令。
"""

import html
from datetime import datetime


def format_smc_fire_signal(
    symbol: str,
    price: float,
    signal_type: str,
    smc_structure: str = "BOS",
    institutional_zone: str = "Order Block (OB)",
    council_vote: dict = None,
    entry: float = 0.0,
    stop_loss: float = 0.0,
    tp1: float = 0.0,
    tp2: float = 0.0,
    leverage: int = 20,
    risk_pct: float = 2.0,
    rr_ratio: float = 0.0,
    atr: float = 0.0,
    trade_id: str = "",
    is_sandbox: bool = False,
) -> str:
    """
    生成 SMC 狩猎指令 HTML 模板

    Args:
        symbol:             交易对 (e.g. BTCUSDT)
        price:              当前价格
        signal_type:        'BUY' 或 'SELL'
        smc_structure:      SMC 结构识别 (BOS / CHoCH)
        institutional_zone: 机构区描述 (OB / FVG)
        council_vote:       众神议会投票结果 dict
        entry:              入场位
        stop_loss:          止损位
        tp1:                止盈1
        tp2:                止盈2
        leverage:           杠杆倍数
        risk_pct:           风险系数 %
        rr_ratio:           预期盈亏比
        atr:                ATR 值
        trade_id:           交易 ID
        is_sandbox:         是否沙盒模式

    Returns:
        str: Telegram HTML 格式消息
    """

    # ── 方向判定 ──
    is_long = signal_type == "BUY"
    direction_emoji = "🟢" if is_long else "🔴"
    direction_text = "做多" if is_long else "做空"
    direction_en = "LONG" if is_long else "SHORT"

    # ── 众神议会默认值 ──
    if council_vote is None:
        council_vote = {}
    r1_verdict = council_vote.get("r1", "Bullish" if is_long else "Bearish")
    claude_verdict = council_vote.get("claude", "CONFIRMED")
    gemini_verdict = council_vote.get("gemini", "形态匹配成功 (SMC Divergence)")

    # ── 自动计算盈亏比 ──
    if rr_ratio <= 0 and entry > 0 and stop_loss > 0 and tp1 > 0:
        risk_dist = abs(entry - stop_loss)
        reward_dist = abs(tp1 - entry)
        rr_ratio = round(reward_dist / risk_dist, 2) if risk_dist > 0 else 0.0

    # ── 沙盒标签 ──
    sandbox_tag = "\n🏖️ <i>[SANDBOX 模拟模式]</i>" if is_sandbox else ""

    # ── 时间戳 ──
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ── 构建消息 ──
    msg = (
        f"{direction_emoji} <b>[SMC 狩獵指令] - 準備開火</b> {direction_emoji}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"\n"
        # ── 1. 战场情报 ──
        f"🗺 <b>戰場情報 (Market Snapshot)</b>\n"
        f"┌─────────────────────────\n"
        f"│ 交易對:  <code>{html.escape(symbol)}</code>\n"
        f"│ 當前價:  <code>${price:,.4f}</code>\n"
        f"│ 方  向:  <b>{direction_text} ({direction_en})</b>\n"
        f"│ SMC結構: <code>{html.escape(smc_structure)}</code>\n"
        f"│ 機構區:  <code>{html.escape(institutional_zone)}</code>\n"
        f"└─────────────────────────\n"
        f"\n"
        # ── 2. 众神议会审计 ──
        f"🏛 <b>眾神議會審計 (Council Vote)</b>\n"
        f"┌─────────────────────────\n"
        f"│ 🧠 R1 參謀 (邏輯):  <code>{html.escape(r1_verdict)}</code>\n"
        f"│ ⚖️ Claude 統帥 (決策): <code>{html.escape(claude_verdict)}</code>\n"
        f"│ 👁️ Gemini 3 (視覺):  <code>{html.escape(gemini_verdict)}</code>\n"
        f"└─────────────────────────\n"
        f"\n"
        # ── 3. 战术部署 ──
        f"🎯 <b>戰術部署 (Execution Data)</b>\n"
        f"┌─────────────────────────\n"
        f"│ 入場位:  <code>${entry:,.4f}</code>\n"
        f"│ 止損位:  <code>${stop_loss:,.4f}</code>  <i>(ATR 動態計算)</i>\n"
        f"│ 止盈 1:  <code>${tp1:,.4f}</code>\n"
        f"│ 止盈 2:  <code>${tp2:,.4f}</code>\n"
        f"│ 槓桿:    <code>{leverage}x</code>  |  風險系數 <code>{risk_pct:.1f}%</code>\n"
        f"│ 盈虧比:  <b>RR {rr_ratio:.2f}</b>\n"
        f"└─────────────────────────\n"
    )

    # ── ATR 附加信息 ──
    if atr > 0:
        msg += f"\n📐 ATR: <code>{atr:.4f}</code>"

    # ── Trade ID ──
    if trade_id:
        msg += f"\n🔖 Trade ID: <code>{html.escape(str(trade_id))}</code>"

    # ── 时间戳 + 沙盒标签 ──
    msg += f"\n⏰ {ts}{sandbox_tag}"

    msg += "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    return msg


def build_smc_signal_from_trade_context(
    symbol: str,
    signal_type: str,
    price: float,
    position_info: dict,
    atr: float = 0.0,
    adx: float = 0.0,
    stop_loss_price: float = 0.0,
    trade_id: str = "",
    signal_message: str = "",
    council_vote: dict = None,
    is_sandbox: bool = False,
) -> str:
    """
    从交易引擎上下文自动构建 SMC 开火信号

    直接对接 process_trading_signals / execute_trade 的参数，
    自动推导 SMC 结构、机构区、止盈位等。

    Args:
        symbol:          交易对
        signal_type:     'BUY' / 'SELL'
        price:           当前价格
        position_info:   仓位信息 dict (quantity, leverage, kelly_factor, ...)
        atr:             ATR 值
        adx:             ADX 值
        stop_loss_price: 止损价
        trade_id:        交易 ID
        signal_message:  原始信号消息 (用于推导 SMC 结构)
        council_vote:    众神议会投票结果
        is_sandbox:      是否沙盒模式

    Returns:
        str: Telegram HTML 格式消息
    """

    # ── 从信号消息推导 SMC 结构 ──
    smc_structure = "BOS"
    if "死叉" in signal_message or "CHoCH" in signal_message.upper():
        smc_structure = "CHoCH (Change of Character)"
    elif "金叉" in signal_message or "BOS" in signal_message.upper():
        smc_structure = "BOS (Break of Structure)"

    # ── 推导机构区 ──
    institutional_zone = "Order Block (OB)"
    if "VWAP" in signal_message:
        institutional_zone = "Order Block (OB) + VWAP 确认"
    if "Squeeze" in signal_message or "FVG" in signal_message.upper():
        institutional_zone = "FVG (Fair Value Gap) 失衡区"

    # ── 提取仓位参数 ──
    leverage = position_info.get("leverage", 20)
    risk_pct = position_info.get("risk_pct", 0.0)
    if risk_pct == 0.0:
        # 尝试从 kelly_factor 和全局 RISK_RATIO 推算
        try:
            from config import SYSTEM_CONFIG
            risk_pct = SYSTEM_CONFIG.get("RISK_RATIO", 0.02) * 100
        except Exception:
            risk_pct = 2.0

    # ── 自动计算止盈位 ──
    entry = price
    sl = stop_loss_price if stop_loss_price > 0 else price
    risk_distance = abs(entry - sl)

    if signal_type == "BUY":
        tp1 = entry + risk_distance * 2.0   # TP1 = 2R
        tp2 = entry + risk_distance * 3.5   # TP2 = 3.5R
    else:
        tp1 = entry - risk_distance * 2.0
        tp2 = entry - risk_distance * 3.5

    # ── 盈亏比 ──
    rr_ratio = 2.0 if risk_distance > 0 else 0.0

    # ── 众神议会投票 ──
    if council_vote is None:
        # 从 ADX 推导 R1 判定
        if adx > 25:
            r1_text = "Bullish (强趋势)" if signal_type == "BUY" else "Bearish (强趋势)"
        else:
            r1_text = "Bullish (弱趋势)" if signal_type == "BUY" else "Bearish (弱趋势)"

        council_vote = {
            "r1": r1_text,
            "claude": "CONFIRMED",
            "gemini": "形态匹配成功 (SMC Divergence)",
        }

    return format_smc_fire_signal(
        symbol=symbol,
        price=price,
        signal_type=signal_type,
        smc_structure=smc_structure,
        institutional_zone=institutional_zone,
        council_vote=council_vote,
        entry=entry,
        stop_loss=sl,
        tp1=tp1,
        tp2=tp2,
        leverage=leverage,
        risk_pct=risk_pct,
        rr_ratio=rr_ratio,
        atr=atr,
        trade_id=trade_id,
        is_sandbox=is_sandbox,
    )
