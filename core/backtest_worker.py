# 🔥 安全加固：在任何 import 之前，强制注入回测环境标记
# 这必须是整个文件的第一段可执行代码，确保所有下游模块（config.py, trading_engine.py）
# 在被 import 时就能通过 os.environ 检测到回测环境，从而屏蔽真实 API Key
import os
os.environ['RUNNING_ENV'] = 'BACKTEST'

"""
X-VOID Omega v7.1 "The Fortress — Unleashed"
Senior Quantitative Architecture with:
- Cross-Margin Risk Logic (correct position sizing & liquidation)
- Vault & Compounding System (50% profit → 20% to vault)
- Dynamic Pyramiding (3 layers with break-even safety lock, relaxed offset)
- Signal Matrix (SML as Entry Booster, not hard gate)
- Anti-Suicide SL Logic (never moves SL backwards)
- Full Metric Reporting
"""
import sys
import time
import gc
import argparse
import traceback
from itertools import product

import numpy as np
import pandas as pd
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Dict, Tuple
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import warnings
warnings.filterwarnings('ignore')

# ============================================================
# INDICATOR SUITE
# ============================================================
def _compute_fvg(high, low, close):
    """Fair Value Gap detection"""
    fvg_bull = np.zeros(len(close))
    fvg_bear = np.zeros(len(close))
    for i in range(2, len(close)):
        if low[i] > high[i-2]:
            fvg_bull[i] = 1
        elif high[i] < low[i-2]:
            fvg_bear[i] = 1
    return fvg_bull, fvg_bear

# ============================================================
# ADAPTIVE LOOKBACK FOR LIQUIDITY SWEEPS
# ============================================================
LIQUIDITY_LOOKBACK_MAP = {
    '1m':  288,   # ~4.8 hours
    '5m':  144,   # ~12 hours
    '15m': 48,    # ~12 hours
    '1h':  24,    # ~24 hours
    '4h':  20,    # ~3.3 days
}
LIQUIDITY_LOOKBACK_DEFAULT = 48


def _get_adaptive_lookback(interval: str) -> int:
    """Return adaptive lookback bars based on kline interval."""
    return LIQUIDITY_LOOKBACK_MAP.get(interval, LIQUIDITY_LOOKBACK_DEFAULT)


def _compute_liquidity_sweeps(high, low, close, lookback=20):
    """Liquidity sweep detection (vectorized O(n) via rolling)"""
    n = len(close)
    # Rolling max/min over the *previous* `lookback` bars (exclude current bar)
    recent_high = pd.Series(high).rolling(lookback).max().shift(1).values
    recent_low = pd.Series(low).rolling(lookback).min().shift(1).values

    sweeps = np.zeros(n)
    valid = np.arange(n) >= lookback
    sweep_bear = valid & (high > recent_high) & (close < high * 0.998)
    sweep_bull = valid & (low < recent_low) & (close > low * 1.002)
    sweeps[sweep_bear] = -1
    sweeps[sweep_bull] = 1
    return sweeps

def _compute_cvd(close, volume):
    """Cumulative Volume Delta"""
    price_change = np.diff(close, prepend=close[0])
    direction = np.where(price_change > 0, 1, np.where(price_change < 0, -1, 0))
    delta = direction * volume
    cvd = np.cumsum(delta)
    return cvd

def _compute_ema(data, period):
    """Fast EMA calculation"""
    return pd.Series(data).ewm(span=period, adjust=False).mean().values

def _compute_atr(high, low, close, period=14):
    """ATR calculation"""
    tr1 = high - low
    tr2 = np.abs(high - np.roll(close, 1))
    tr3 = np.abs(low - np.roll(close, 1))
    tr = np.maximum(tr1, np.maximum(tr2, tr3))
    tr[0] = tr1[0]
    atr = pd.Series(tr).ewm(span=period, adjust=False).mean().values
    return atr

def _compute_adx(high, low, close, period=14, atr=None):
    """ADX calculation"""
    up_move = high - np.roll(high, 1)
    down_move = np.roll(low, 1) - low
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)
    
    if atr is None:
        atr = _compute_atr(high, low, close, period)
    plus_di = 100 * pd.Series(plus_dm).ewm(span=period, adjust=False).mean().values / atr
    minus_di = 100 * pd.Series(minus_dm).ewm(span=period, adjust=False).mean().values / atr
    
    dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)
    adx = pd.Series(dx).ewm(span=period, adjust=False).mean().values
    return adx


# ============================================================
# PRE-COMPUTE LAYER
# ============================================================
def precompute_indicators(df, ema_period, interval='1h'):
    """Build numpy arrays with all indicators (interval used for adaptive lookback)"""
    o = df['open'].values.astype(np.float64)
    h = df['high'].values.astype(np.float64)
    l = df['low'].values.astype(np.float64)
    c = df['close'].values.astype(np.float64)
    v = df['volume'].values.astype(np.float64)

    atr = _compute_atr(h, l, c, 14)
    ema = _compute_ema(c, ema_period)
    adx = _compute_adx(h, l, c, 14, atr=atr)

    # MACD (12, 26, 9)
    ema12 = _compute_ema(c, 12)
    ema26 = _compute_ema(c, 26)
    macd_line = ema12 - ema26
    macd_signal = _compute_ema(macd_line, 9)
    macd_hist = macd_line - macd_signal

    # VWAP — Rolling 72-period (aligned with trading_engine.py)
    _vwap_window = 72
    _rolling_vp = pd.Series(c * v).rolling(_vwap_window, min_periods=1).sum().values
    _rolling_v  = pd.Series(v).rolling(_vwap_window, min_periods=1).sum().values
    vwap = _rolling_vp / (_rolling_v + 1e-10)
    # Volume MA
    vol_ma_20 = pd.Series(v).rolling(20).mean().values

    # RSI (14)
    delta = np.diff(c, prepend=c[0])
    gain = np.where(delta > 0, delta, 0)
    loss_arr = np.where(delta < 0, -delta, 0)
    avg_gain = pd.Series(gain).ewm(span=14, adjust=False).mean().values
    avg_loss = pd.Series(loss_arr).ewm(span=14, adjust=False).mean().values
    rsi = 100 - (100 / (1 + avg_gain / (avg_loss + 1e-10)))

    # SML Suite
    fvg_bull, fvg_bear = _compute_fvg(h, l, c)
    adaptive_lookback = _get_adaptive_lookback(interval)
    liq_sweeps = _compute_liquidity_sweeps(h, l, c, adaptive_lookback)

    # CVD & OBI Proxy (Taker Buy Ratio)
    if 'taker_buy_base_asset_volume' in df.columns:
        taker_buy = df['taker_buy_base_asset_volume'].values.astype(np.float64)
        taker_sell = v - taker_buy
        volume_delta = taker_buy - taker_sell
        cvd = np.cumsum(volume_delta)
        taker_buy_ratio = taker_buy / (v + 1e-10)
    else:
        cvd = _compute_cvd(c, v)
        taker_buy_ratio = np.full(len(c), 0.5)

    cvd_ma_20 = pd.Series(cvd).rolling(20).mean().values

    # CVD Divergence (Price vs CVD)
    prev_c = np.roll(c, 1)
    prev_c[0] = c[0]
    prev_cvd = np.roll(cvd, 1)
    prev_cvd[0] = cvd[0]
    
    cvd_bearish_div = (c > prev_c) & (cvd < prev_cvd)  # Price up, CVD down (Distribution)
    cvd_bullish_div = (c < prev_c) & (cvd > prev_cvd)  # Price down, CVD up (Accumulation)

    return {
        'open': o, 'high': h, 'low': l, 'close': c, 'volume': v,
        'ATR': atr, 'EMA': ema, 'ADX': adx,
        'MACD_hist': macd_hist, 'VWAP': vwap,
        'vol_ma_20': vol_ma_20, 'RSI': rsi,
        'fvg_bull': fvg_bull, 'fvg_bear': fvg_bear,
        'liq_sweeps': liq_sweeps, 
        'CVD': cvd, 'CVD_MA_20': cvd_ma_20,
        'cvd_bearish_div': cvd_bearish_div,  
        'cvd_bullish_div': cvd_bullish_div,  
        'taker_buy_ratio': taker_buy_ratio   
    }


# ============================================================
# SIGNAL GENERATION WITH SML + CVD INTEGRATION
# ============================================================
def generate_signals(arrays, params):
    """Signal generation with CVD trend filter"""
    mode = params.get('STRATEGY_MODE', 'STANDARD')
    adx_thr = params.get('ADX_THR', 25)
    c = arrays['close']
    n = len(c)

    entry_long = np.zeros(n, dtype=bool)
    entry_short = np.zeros(n, dtype=bool)

    macd_h = np.nan_to_num(arrays['MACD_hist'])
    ema = np.nan_to_num(arrays['EMA'])
    adx = np.nan_to_num(arrays['ADX'])
    rsi = np.nan_to_num(arrays['RSI'])
    vwap = np.nan_to_num(arrays['VWAP'])
    vol = arrays['volume']
    vol_ma = np.nan_to_num(arrays['vol_ma_20'])
    cvd = arrays['CVD']
    cvd_ma = np.nan_to_num(arrays['CVD_MA_20'])
    fvg_bull = arrays['fvg_bull']
    fvg_bear = arrays['fvg_bear']
    liq = arrays['liq_sweeps']

    # Unpack new arrays
    cvd_bearish_div = arrays['cvd_bearish_div']
    cvd_bullish_div = arrays['cvd_bullish_div']
    taker_buy_ratio = arrays['taker_buy_ratio']

    # Space Lock (Anti-Fakeout)
    candle_body = np.abs(c - arrays['open'])
    max_body_atr = params.get('MAX_CANDLE_BODY_ATR', 1.8)
    space_lock_ok = candle_body <= (arrays['ATR'] * max_body_atr) if params.get('SPACE_LOCK_ENABLED', False) else np.ones(n, dtype=bool)

    # OBI Proxy Filter (Prevent buying into massive sell walls)
    obi_pass_long = taker_buy_ratio > 0.4 
    obi_pass_short = taker_buy_ratio < 0.6 

    # CVD trend filter
    cvd_bullish = cvd > cvd_ma
    cvd_bearish = cvd < cvd_ma

    # SML boost flags (used by simulate for +20% size boost)
    sml_long = (fvg_bull == 1) | (liq == 1)
    sml_short = (fvg_bear == 1) | (liq == -1)

    if mode == 'STANDARD':
        adx_ok = adx >= adx_thr
        entry_long = (c > ema) & (c > vwap) & (macd_h > 0) & adx_ok & cvd_bullish & space_lock_ok
        entry_short = (c < ema) & (c < vwap) & (macd_h < 0) & adx_ok & cvd_bearish & space_lock_ok

    elif mode == 'CONSERVATIVE':
        vol_burst = vol > (vol_ma * 1.8)
        entry_long = (rsi < 35) & (c < ema * 0.95) & vol_burst & cvd_bullish
        entry_short = (rsi > 75) & (c > ema * 1.05) & vol_burst & cvd_bearish

    elif mode == 'AGGRESSIVE':
        # --- DUAL-ENGINE STATE MACHINE ---
        vol_burst = vol > (vol_ma * 1.5)
        vol_burst_fvg = vol > vol_ma  # Require at least average volume for FVG validation
        
        # Engine A: Left-Side Assassin (Strict 8x Scope)
        # Must be extremely oversold/overbought (RSI 35/65) + CVD Divergence + (True Sweep OR High-Volume FVG)
        left_side_long = ((liq == 1) | ((fvg_bull == 1) & vol_burst_fvg)) & cvd_bullish_div & (rsi < 35) & obi_pass_long
        left_side_short = ((liq == -1) | ((fvg_bear == 1) & vol_burst_fvg)) & cvd_bearish_div & (rsi > 65) & obi_pass_short
        
        # Engine B: Right-Side Heavy Artillery (Trend Breakout + Momentum + Volume)
        right_side_long = (c > ema) & (macd_h > 0) & vol_burst & (adx >= adx_thr) & space_lock_ok & obi_pass_long
        right_side_short = (c < ema) & (macd_h < 0) & vol_burst & (adx >= adx_thr) & space_lock_ok & obi_pass_short
        
        entry_long = left_side_long | right_side_long
        entry_short = left_side_short | right_side_short

    else:  # SCALPER
        entry_long = ((liq == 1) | (fvg_bull == 1)) & cvd_bullish
        entry_short = ((liq == -1) | (fvg_bear == 1)) & cvd_bearish

    # Exit signals — Price vs. EMA cross (replaces noisy MACD Histogram)
    prev_c = np.roll(c, 1)
    prev_c[0] = c[0]
    # Exit only when price completely breaks the trend line (EMA)
    exit_long = (c < ema) & (prev_c >= ema)
    exit_short = (c > ema) & (prev_c <= ema)

    # Warmup
    entry_long[:150] = False
    entry_short[:150] = False

    return {
        'el': entry_long, 'es': entry_short,
        'xl': exit_long, 'xs': exit_short,
        'sml_long': sml_long, 'sml_short': sml_short,  # Entry Booster flags
    }


# ============================================================
# SIMULATION ENGINE: The Fortress Core
# Cross-Margin Liquidation + Vault + Dynamic Pyramiding
# ============================================================
def simulate(arrays, signals, params):
    """
    Event-driven simulator with:
    - Cross-margin: Position_Size = Active_Capital * RISK_RATIO * LEVERAGE
    - Liquidation: liq_threshold = 1.0 / (RISK_RATIO * LEVERAGE)
    - Vault: 50% balance increase → 20% profit to vault
    - Dynamic Pyramiding: up to 3 layers with BE safety lock
    """
    close = arrays['close']
    open_ = arrays['open']
    vault_trig_ratio = params.get('VAULT_TRIGGER_RATIO', 1.5) # 默认1.5倍触发
    vault_lock_ratio = params.get('VAULT_LOCK_RATIO', 0.2)   # 默认锁20%利润
    high = arrays['high']
    low = arrays['low']
    atr = arrays['ATR']
    cvd = arrays['CVD']
    cvd_ma = np.nan_to_num(arrays['CVD_MA_20'])
    n = len(close)

    risk_r = params.get('RISK_RATIO', 0.15)
    lev = params.get('LEVERAGE', 30.0)
    fee = 0.0012

    atr_mult = params.get('ATR_MULT', 2.3)

    # Three-stage dynamic SL
    s_a_profit = params.get('STAGE_A_PROFIT_MULT', 1.0)
    s_b_profit = params.get('STAGE_B_PROFIT_MULT', 1.8)
    tsl_trigger = params.get('TSL_TRIGGER_MULT', 2.0)
    tsl_callback = params.get('TSL_CALLBACK_MULT', 1.3)

    # Pyramiding config
    max_layers = 5

    pyramid_atr_trigger = params.get('PYRAMID_ATR_TRIGGER', 2.0)
    be_safety_offset = params.get('be_safety_offset', 1.2)

# 🛑 软熔断与黑天鹅配置 (新增)
    bars_per_48h = 48  # 假设 1h 级别，48 根 K 线就是 48 小时
    pause_until_idx = 0
    consecutive_losses = 0
    max_consec_losses = params.get('MAX_CONSEC_LOSSES', 4) # 默认连亏5单熔断
    black_swan_dd_limit = params.get('BLACK_SWAN_DD', 0.12) # 默认 10% 回撤熔断

    # Cross-margin liquidation threshold
    liq_threshold = 1.0 / (risk_r * lev)

    INITIAL_BALANCE = 1000.0
    account_balance = INITIAL_BALANCE
    vault_balance = 0.0
    active_capital = INITIAL_BALANCE

    vault_peak = INITIAL_BALANCE  
    peak = INITIAL_BALANCE
    max_dd = 0.0
    positions = []
    history = []
    
    _eq_max = 1 + (n - 150)
    equity_curve = np.empty(_eq_max, dtype=np.float64)
    equity_curve[0] = INITIAL_BALANCE
    _eq_idx = 1

    # 🛑 软熔断与黑天鹅配置
    bars_per_48h = 48  
    pause_until_idx = 0
    consecutive_losses = 0
    max_consec_losses = params.get('MAX_CONSEC_LOSSES', 5) 
    black_swan_dd_limit = params.get('BLACK_SWAN_DD', 0.10) 

    def _update_vault():
        nonlocal account_balance, vault_balance, vault_peak
        trig_ratio = params.get('VAULT_TRIGGER_RATIO', 1.5)
        lock_ratio = params.get('VAULT_LOCK_RATIO', 0.2)
        if account_balance >= vault_peak * trig_ratio:
            total_profit = account_balance - INITIAL_BALANCE
            if total_profit > 0:
                siphon = total_profit * lock_ratio
                vault_balance += siphon
                account_balance -= siphon
                vault_peak = account_balance

    def _recalc_active_capital():
        nonlocal active_capital
        active_capital = max(0.0, account_balance)

    def _get_combined_position():
        if not positions: return None
        total_sz = sum(p['size'] for p in positions)
        if total_sz == 0: return None
        w_entry = sum(p['entry'] * p['size'] for p in positions) / total_sz
        return {'type': positions[0]['type'], 'avg_entry': w_entry, 'total_size': total_sz}

    def _move_all_sl_to_be(c_atr):
        combo = _get_combined_position()
        if combo is None:
            return
        be = combo['avg_entry']
        for p in positions:
            if p['type'] == 'LONG':
                new_sl = be + be_safety_offset * c_atr
                p['sl'] = max(p['sl'], new_sl)
            else:
                new_sl = be - be_safety_offset * c_atr
                p['sl'] = min(p['sl'], new_sl)

    for i in range(150, n - 1):
        # --- 统计总身家 ---
        current_total_net_worth = account_balance + vault_balance
        equity_curve[_eq_idx] = current_total_net_worth
        _eq_idx += 1

        if peak > 0:
            current_dd = (peak - current_total_net_worth) / peak
            max_dd = max(max_dd, current_dd)
            
            # 🛑 黑天鹅熔断：账户总回撤达到 10%，强制拔电源 48 小时
            if current_dd >= black_swan_dd_limit and i >= pause_until_idx:
                pause_until_idx = i + bars_per_48h
                peak = current_total_net_worth  # ⚠️ 必须重置水位线，防连环触发
        
        peak = max(peak, current_total_net_worth)

        exec_p = open_[i + 1]
        b_hi = high[i + 1]
        b_lo = low[i + 1]
        c_atr = atr[i]
        current_cvd = cvd[i]
        current_cvd_ma = cvd_ma[i]

        # --- Cross-Margin Liquidation Check (account-wide) ---

        # --- Cross-Margin Liquidation Check (account-wide) ---
        if positions:
            combo = _get_combined_position()
            if combo is not None:
                if combo['type'] == 'LONG':
                    adverse_move = (combo['avg_entry'] - b_lo) / combo['avg_entry']
                else:
                    adverse_move = (b_hi - combo['avg_entry']) / combo['avg_entry']

                if adverse_move >= liq_threshold:
                    # 清算：账户余额归零，模拟终止
                    liq_loss = -account_balance
                    account_balance = 0.0
                    active_capital = 0.0
                    positions.clear()
                    history.append({'r': -1.0, 'pnl': liq_loss})
                    return (-999.0, account_balance, account_balance - INITIAL_BALANCE,
                            1.0, len(history), 0.0, 0.0, vault_balance, equity_curve[:_eq_idx])

        # --- Dynamic SL & Exit Check ---
        just_closed_in_this_candle = False
        for p in positions[:]:
            p_type = p['type']
            p_ent = p['entry']
            p_sl = p['sl']
            p_atr = p['atr']
            is_closed = False
            close_price = 0.0

            if p_type == 'LONG':
                float_prof = exec_p - p_ent
                if float_prof >= tsl_trigger * p_atr:
                    p['max_p'] = max(p['max_p'], exec_p)
                    new_sl = p['max_p'] - tsl_callback * p_atr
                    p_sl = max(p_sl, new_sl)  # Anti-suicide: never move SL down
                elif float_prof >= s_b_profit * p_atr:
                    new_sl = p_ent * 1.001
                    p_sl = max(p_sl, new_sl)  # Anti-suicide
                elif float_prof >= s_a_profit * p_atr:
                    new_sl = p_ent - 0.5 * p_atr
                    p_sl = max(p_sl, new_sl)  # Anti-suicide
                p['sl'] = p_sl

                if b_lo <= p_sl:
                    is_closed, close_price = True, min(exec_p, p_sl)
                elif signals['xl'][i]:
                    is_closed, close_price = True, exec_p
            else:  # SHORT
                float_prof = p_ent - exec_p
                if float_prof >= tsl_trigger * p_atr:
                    p['min_p'] = min(p['min_p'], exec_p)
                    new_sl = p['min_p'] + tsl_callback * p_atr
                    p_sl = min(p_sl, new_sl)  # Anti-suicide: never move SL up (worse for short)
                elif float_prof >= s_b_profit * p_atr:
                    new_sl = p_ent * 0.999
                    p_sl = min(p_sl, new_sl)  # Anti-suicide
                elif float_prof >= s_a_profit * p_atr:
                    new_sl = p_ent + 0.5 * p_atr
                    p_sl = min(p_sl, new_sl)  # Anti-suicide
                p['sl'] = p_sl

                if b_hi >= p_sl:
                    is_closed, close_price = True, max(exec_p, p_sl)
                elif signals['xs'][i]:
                    is_closed, close_price = True, exec_p

            if is_closed:
                if p_type == 'LONG':
                    r = (close_price - p_ent) / p_ent
                else:
                    r = (p_ent - close_price) / p_ent
                net_r = r - fee
                pnl = p['size'] * net_r
                account_balance += pnl
                history.append({'r': net_r, 'pnl': pnl})
                positions.remove(p)
                just_closed_in_this_candle = True
                
                # --- 🛑 连亏与单笔黑天鹅熔断检测 ---
                if pnl < 0:
                    consecutive_losses += 1
                    if abs(pnl) >= account_balance * 0.10: 
                        pause_until_idx = max(pause_until_idx, i + bars_per_48h)
                else:
                    consecutive_losses = 0
                
                if consecutive_losses >= max_consec_losses:
                    pause_until_idx = max(pause_until_idx, i + bars_per_48h)
                    consecutive_losses = 0

        # After closing, recalc and check vault
        _recalc_active_capital()
        _update_vault()
        _recalc_active_capital()

        # --- Dynamic Pyramiding Check ---
        if positions and len(positions) < max_layers and active_capital > 0:
            combo = _get_combined_position()
            if combo is not None:
                p_type = combo['type']
                if p_type == 'LONG':
                    profit_in_price = exec_p - combo['avg_entry']
                    cvd_ok = current_cvd > current_cvd_ma
                else:
                    profit_in_price = combo['avg_entry'] - exec_p
                    cvd_ok = current_cvd < current_cvd_ma

                if profit_in_price > pyramid_atr_trigger * c_atr and cvd_ok:
                    decay_factor = 0.5 ** len(positions)
                    sl_p = (exec_p - c_atr * atr_mult) if p_type == 'LONG' else (exec_p + c_atr * atr_mult)
                    layer_size = active_capital * risk_r * lev * decay_factor
                    new_layer = {
                        'type': p_type, 'entry': exec_p, 'size': layer_size,
                        'sl': sl_p, 'atr': c_atr, 'max_p': exec_p, 'min_p': exec_p,
                        'layer': len(positions) + 1
                    }
                    positions.append(new_layer)
                    # Safety Lock: move ALL SLs to combined BE + 0.2*ATR
                    _move_all_sl_to_be(c_atr)

        # --- New Entry Check ---
        # 🛑 增加熔断时间锁：i >= pause_until_idx
        if active_capital > 0 and len(positions) == 0 and not just_closed_in_this_candle and i >= pause_until_idx:
            do_l = signals['el'][i]
            do_s = signals['es'][i]
            if (do_l or do_s) and not (do_l and do_s):
                p_t = 'LONG' if do_l else 'SHORT'
                sl_p = (exec_p - c_atr * atr_mult) if p_t == 'LONG' else (exec_p + c_atr * atr_mult)
                
                # 1. 基础配置：凯利公式与波动率缩放
                kelly_factor = 1.0
                if params.get('USE_KELLY_FORMULA', False) and len(history) >= 100:
                    recent = history[-100:]
                    wins = [h['pnl'] for h in recent if h['pnl'] > 0]
                    losses = [abs(h['pnl']) for h in recent if h['pnl'] < 0]
                    w_rate = len(wins) / len(recent) if recent else 0.5
                    # b = avg_win / avg_loss (odds ratio)
                    pl_ratio = (sum(wins)/len(wins)) / (sum(losses)/len(losses)) if (wins and losses) else 1.0
                    if pl_ratio > 0:
                        # Standard Kelly: f* = (w * b - (1 - w)) / b
                        # Half-Kelly (0.5x) used intentionally to reduce variance
                        k_full = (w_rate * pl_ratio - (1 - w_rate)) / pl_ratio
                        k_raw = 0.5 * k_full
                        kelly_factor = max(0.05, min(1.2, k_raw))

                # 🔥 标准头寸风险定仓（与 trading_engine.calculate_position_size 对齐）
                # 公式: risk_amount / stop_loss_distance * exec_price = notional
                if c_atr > 0:
                    _risk_amount = active_capital * risk_r * kelly_factor

                    # 🔥 波动率缩放 (Volatility Scalar) — 与 trading_engine 对齐
                    if params.get('USE_VOLATILITY_SCALAR', False) and c_atr > 0:
                        _atr_baseline = params.get('ATR_BASELINE', 30.0)
                        _vol_scalar = _atr_baseline / c_atr
                        _risk_amount = _risk_amount * _vol_scalar

                    _stop_loss_dist = c_atr * atr_mult
                    _qty = _risk_amount / _stop_loss_dist
                    pos_size = _qty * exec_p
                else:
                    # ATR 不可用时回退到杠杆公式（兜底）
                    pos_size = active_capital * risk_r * lev * kelly_factor

                # SML Booster (+20%)
                _sml_mult = params.get('SML_BOOST_MULT', 1.20)
                if p_t == 'LONG' and signals['sml_long'][i]:
                    pos_size *= _sml_mult
                elif p_t == 'SHORT' and signals['sml_short'][i]:
                    pos_size *= _sml_mult

                positions.append({
                    'type': p_t, 'entry': exec_p, 'size': pos_size,
                    'sl': sl_p, 'atr': c_atr, 'max_p': exec_p, 'min_p': exec_p,
                    'layer': 1
                })

    # --- Settlement: close remaining positions at last close ---
    last_close = close[-1]
    for p in positions[:]:
        if p['type'] == 'LONG':
            r = (last_close - p['entry']) / p['entry']
        else:
            r = (p['entry'] - last_close) / p['entry']
        net_r = r - fee
        pnl = p['size'] * net_r
        account_balance += pnl
        history.append({'r': net_r, 'pnl': pnl})
    positions.clear()
    _recalc_active_capital()
    _update_vault()

    # --- Metrics ---
    if not history:
        return (-999.0, INITIAL_BALANCE, 0.0, 0.0, 0, 0.0, 0.0, 0.0)

    total_balance = account_balance + vault_balance
    rets = np.array([h['r'] for h in history])
    pnls = np.array([h['pnl'] for h in history])
    wins = pnls[pnls > 0]
    losses = pnls[pnls < 0]

    win_rate = len(wins) / len(rets) if len(rets) > 0 else 0.0
    plr = (np.mean(wins) / abs(np.mean(losses))) if len(losses) > 0 and len(wins) > 0 else 0.0
    net_profit = total_balance - INITIAL_BALANCE

    # Trim pre-allocated equity curve to actual length
    equity_curve = equity_curve[:_eq_idx]

    # Equity-curve Sharpe (1h data = 8760 periods/year)
    eq_returns = np.diff(equity_curve) / (equity_curve[:-1] + 1e-10)
    if len(eq_returns) > 0 and np.std(eq_returns) > 0:
        sharpe = float(np.mean(eq_returns) / np.std(eq_returns) * np.sqrt(8760))
    else:
        sharpe = 0.0

    return (sharpe, total_balance, net_profit, max_dd, len(rets), win_rate, plr, vault_balance, equity_curve)


# ============================================================
# WORKER PROCESS GLOBALS & INITIALIZER
# ============================================================
_DATA_POOL = None
_SYMBOL = None
_INTERVAL = None


def _init_worker(data_pool, symbol, interval):
    global _DATA_POOL, _SYMBOL, _INTERVAL
    _DATA_POOL = data_pool
    _SYMBOL = symbol
    _INTERVAL = interval


def _backtest_worker(params):
    """Single parameter combination worker"""
    global _DATA_POOL
    try:
        ema_key = params.get('EMA_TREND', 89)
        arrays = _DATA_POOL[ema_key]
        sigs = generate_signals(arrays, params)
        result = simulate(arrays, sigs, params)
        sharpe, balance, net_profit, max_dd, trades, win_rate, plr, vault, eq_curve = result
        return (params, sharpe, balance, net_profit, max_dd, trades, win_rate, plr, vault, eq_curve)
    except Exception as e:
        print(f"[worker error] {params}: {e}")
        traceback.print_exc()
        return (params, -999.0, 0.0, 0.0, 0.0, 0, 0.0, 0.0, 0.0)


# ============================================================
# BACKTEST WORKER CLASS
# ============================================================
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import importlib
    config_module = importlib.import_module('WJ-BOT.config')
except ImportError:
    try:
        config_module = importlib.import_module('config')
    except ImportError:
        config_module = None

try:
    from binance.client import Client as BinanceClient
except ImportError:
    BinanceClient = None


class BacktestWorker:
    def __init__(self, client, symbol: str = 'ETHUSDT'):
        self.client = client
        self.symbol = symbol
        self.df_raw = None

    def _fetch_data(self, lookback_days: int, interval: str) -> bool:
        """Fetch OHLCV data from Binance or local cache"""
        cache_dir = os.path.join(os.path.dirname(__file__), 'data_cache')
        os.makedirs(cache_dir, exist_ok=True)

        cache_files = [f for f in os.listdir(cache_dir)
                       if f.startswith(f"{self.symbol}_{interval}")]
        if cache_files:
            latest = os.path.join(cache_dir, sorted(cache_files, reverse=True)[0])
            df = pd.read_csv(latest)
            if 'timestamp' in df.columns:
                df['timestamp'] = pd.to_datetime(df['timestamp'])
            rows_needed = lookback_days * (24 if 'h' in interval else 1)
            # 修复：根据分钟/小时自动计算需要的行数
            if 'm' in interval:
                mins = int(interval.replace('m', ''))
                rows_needed = lookback_days * 24 * 60 // mins
            elif 'h' in interval:
                hours = int(interval.replace('h', ''))
                rows_needed = lookback_days * 24 // hours
            if len(df) >= rows_needed:
                self.df_raw = df.tail(rows_needed).reset_index(drop=True)
                print(f"  [cache] Loaded {len(self.df_raw)} bars from {latest}")
                return True

        print(f"  [api] Fetching {self.symbol} {interval} ({lookback_days}d)...")
        try:
            from trading_engine import get_historical_klines
            df = get_historical_klines(self.client, self.symbol, interval,
                                       limit=lookback_days * 24)
            if df is not None and len(df) > 0:
                cache_path = os.path.join(
                    cache_dir,
                    f"{self.symbol}_{interval}_{lookback_days}d.csv")
                df.to_csv(cache_path, index=False)
                self.df_raw = df
                print(f"  [api] Got {len(df)} bars, cached to {cache_path}")
                return True
        except Exception as e:
            print(f"  [error] Data fetch failed: {e}")
        return False

    def run_mega_grid(self, param_grid: Dict, interval: str, days: int, max_workers: int = 4):
        """超算级网格搜索：支持 20万+ 组合，内存隔离模式"""
        if not self._fetch_data(days, interval):
            return

        # 1. 预计算所有 EMA 矩阵
        ema_values = param_grid.get('EMA_TREND', [91])
        adaptive_lb = _get_adaptive_lookback(interval)
        print(f"  [SML] Adaptive liquidity lookback: {adaptive_lb} bars (interval={interval})")
        data_pool = {ev: precompute_indicators(self.df_raw, ev, interval) for ev in ema_values}

        # 2. 准备生成器，防止内存爆炸
        keys = list(param_grid.keys())
        # 使用 itertools.product 生成器，不占用实际内存
        combos = (dict(zip(keys, v)) for v in product(*param_grid.values()))

        # 3. 统计总数 (为了显示进度条)
        import math
        total_tasks = math.prod(len(v) for v in param_grid.values())
        print(f"🚀 [MEGA MODE] 启动！总任务数: {total_tasks} | 核心数: {max_workers}")

        results = []
        t0 = time.time()

        # 4. 使用多进程，设置较小的 chunksize 提升吞吐量
        with ProcessPoolExecutor(
            max_workers=max_workers,
            initializer=_init_worker,
            initargs=(data_pool, self.symbol, interval)
        ) as executor:
            # 💡 核心优化：不再保存 eq_curve，只存关键指标
            # 💡 增加 tqdm 进度条
            from tqdm import tqdm
            for res in tqdm(executor.map(_backtest_worker, combos, chunksize=100), total=total_tasks):
                if res and res[1] > -990:  # 过滤清算结果
                    p, sharpe, balance, net_profit, max_dd, trades, wr, plr, vault, _ = res
                    results.append({
                        'Sharpe': round(sharpe, 4),
                        'NetProfit': round(net_profit, 2),
                        'MaxDD_Pct': round(max_dd * 100, 2),
                        'Trades_Count': trades,
                        **p
                    })
                    # 💡 定期清理内存
                    if len(results) % 5000 == 0:
                        gc.collect()

        elapsed = time.time() - t0
        print(f"\n\n[done] {len(results)} valid results in {elapsed:.1f}s")

        # 5. 后处理逻辑 (Top 50 重新绘图)
        if results:
            df_out = pd.DataFrame(results)
            df_out = df_out.sort_values('Sharpe', ascending=False)
            report_path = os.path.join(os.path.dirname(__file__), 'omega_report.csv')
            df_out.to_csv(report_path, index=False)
            print(f"[saved] {report_path}")

            print("\n=== TOP 10 RESULTS (MEGA MODE) ===")
            display_cols = ['Sharpe', 'NetProfit', 'MaxDD_Pct', 'Trades_Count']
            print(df_out[display_cols].head(10).to_string(index=False))

            # Top 1 重新跑一次完整回测以获取 equity curve 用于绘图
            best_params = df_out.iloc[0].to_dict()
            # 从 best_params 中提取原始参数（去掉指标列）
            metric_cols = {'Sharpe', 'NetProfit', 'MaxDD_Pct', 'Trades_Count'}
            raw_params = {k: v for k, v in best_params.items() if k not in metric_cols}
            ema_key = raw_params.get('EMA_TREND', 91)
            arrays = data_pool[ema_key]
            sigs = generate_signals(arrays, raw_params)
            result = simulate(arrays, sigs, raw_params)
            self.best_eq_curve = result[-1]  # equity_curve

            # 构造完整 best_row 用于绘图
            sharpe, balance, net_profit, max_dd, trades_count, wr, plr, vault = result[:8]
            best_row = {
                'Sharpe': sharpe,
                'Final_Balance': balance,
                'NetProfit': net_profit,
                'MaxDD_Pct': max_dd * 100,
                'Trades_Count': trades_count,
                'WinRate_Pct': wr * 100,
                'PLRatio': plr,
                'Final_Vault_Amount': vault,
                **raw_params
            }
            self._plot_best_equity(pd.Series(best_row), self.best_eq_curve)
        else:
            print("[warn] No valid results generated")

        del data_pool
        gc.collect()

    def _plot_best_equity(self, best_row, equity):
        """Plot the actual equity curve from simulate (no duplicate sim needed)"""
        try:
            import matplotlib.dates as mdates

            INITIAL = 1000.0
            n_bars = len(self.df_raw)

            # Align dates with equity curve length
            # equity_curve has 1 initial point + 1 per loop iteration (bars 150..n-2)
            # So the loop-generated portion has len(equity) - 1 points starting from bar 151
            loop_len = len(equity) - 1  # exclude the initial balance point
            dates = pd.to_datetime(
                self.df_raw['timestamp'].iloc[n_bars - loop_len : n_bars]
            ).values

            # Use the loop portion of equity (skip initial balance point) to match dates
            eq_plot = equity[1:1 + len(dates)]

            fig, ax = plt.subplots(figsize=(15, 7))

            ax.plot(dates, eq_plot, linewidth=1.2, color='#00d4aa')
            ax.axhline(y=INITIAL, color='gray', linestyle='--', alpha=0.5)

            ax.fill_between(dates, eq_plot, INITIAL,
                            where=[e >= INITIAL for e in eq_plot],
                            alpha=0.15, color='green')
            ax.fill_between(dates, eq_plot, INITIAL,
                            where=[e < INITIAL for e in eq_plot],
                            alpha=0.15, color='red')

            ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
            plt.xticks(rotation=45)

            ax.set_title(f'X-VOID Omega "The Fortress" | {self.symbol} | '
                         f'Sharpe = {best_row["Sharpe"]:.3f}',
                         fontsize=14, fontweight='bold')
            ax.set_xlabel('Date (YYYY-MM)', fontsize=12)
            ax.set_ylabel('Equity ($)', fontsize=12)

            stats_text = (
                f"► METRICS DASHBOARD\n"
                f"------------------------\n"
                f"Initial Balance: ${INITIAL:,.0f}\n"
                f"Final Balance: ${best_row['Final_Balance']:,.0f}\n"
                f"Net Profit: ${best_row['NetProfit']:,.0f}\n"
                f"Max Drawdown: {best_row['MaxDD_Pct']:.2f}%\n"
                f"Win Rate: {best_row['WinRate_Pct']:.2f}%\n"
                f"Total Trades: {int(best_row['Trades_Count'])}\n"
                f"Vault Locked: ${best_row['Final_Vault_Amount']:,.0f}\n"
                f"Risk Ratio: {best_row['RISK_RATIO']}\n"
            )
            props = dict(boxstyle='round,pad=0.5', facecolor='#1e1e1e',
                         alpha=0.85, edgecolor='#00d4aa')
            ax.text(0.02, 0.95, stats_text, transform=ax.transAxes, fontsize=10,
                    verticalalignment='top', color='white', bbox=props,
                    family='monospace')

            ax.grid(True, alpha=0.3)
            fig.tight_layout()

            plot_path = os.path.join(os.path.dirname(__file__), 'omega_equity.png')
            fig.savefig(plot_path, dpi=200)
            plt.close(fig)
            print(f"[plot] Saved equity curve to {plot_path}")

        except Exception as e:
            print(f"[plot error] {e}")
            import traceback
            traceback.print_exc()


# ============================================================
# MAIN ENTRY POINT
# ============================================================
def main():
    parser = argparse.ArgumentParser(description='X-VOID Omega v7.0 "The Fortress" Backtest')
    parser.add_argument('--symbol', type=str, default='ETHUSDT')
    parser.add_argument('--days', type=int, default=500)
    parser.add_argument('--period', type=str, default='1h')
    parser.add_argument('--workers', type=int, default=4)
    args = parser.parse_args()

    # 🎯 i7-13700 本地精英级网格 (约 5,184 组)
    param_grid = {
        'STRATEGY_MODE': ['AGGRESSIVE'],           # 🔒 锁定模式
        'EMA_TREND': [44,40],                      # 🔒 锁定圣杯均线
        
        # --- 进场与趋势过滤 (步进优化) ---
        'ADX_THR': [37,40],                 # 3: 拒绝 22 杂讯，聚焦强趋势
        'MIN_SIGNAL_DISTANCE_ATR': [1.9],   # 2: 信号间距
        'MAX_CANDLE_BODY_ATR': [1.6],  # 3: 过滤大阴大阳线

        # --- 止损与止盈收割 (核心维度) ---
        'ATR_MULT': [5.5,4.0],                         # 1: 止损宽度
        'TSL_TRIGGER_MULT': [1.6],# 4: 触发追踪止盈的门槛
        'TSL_CALLBACK_MULT': [1.6],    # 3: 追踪止盈的回吐容忍度
        'be_safety_offset': [0.4],     # 3: 移动保本位的安全垫

        # --- 动态止损阶段优化 ---
        'STAGE_A_PROFIT_MULT': [1.1],            # 1: 第一阶段保护触发
        'STAGE_B_PROFIT_MULT': [2.25],            # 1: 第二阶段保护触发
        
        # --- 加仓与金库系统 ---
        'PYRAMID_ATR_TRIGGER': [1.6],            # 1: 浮盈加仓间距
        'VAULT_TRIGGER_RATIO': [1.0],            # 1: 抽水触发比例
        'VAULT_LOCK_RATIO': [0.3],               # 1: 利润锁死比例
        
        # --- 风控与环境适应 ---
        'RISK_RATIO': [0.06],                    # 1: 单笔风险 4%，拒绝爆仓风险
        'LEVERAGE': [30.0],                      # 🔒 固定杠杆
        'USE_VOLATILITY_SCALAR': [False],        # 1: 波动率缩放开关
        'ATR_BASELINE': [25.0, 35.0],            # 2: 波动率参照基准
        'USE_KELLY_FORMULA': [True],            # 🔒 固定凯利公式
        'SPACE_LOCK_ENABLED': [False],           # 1: 空间锁开关
        
        # --- 熔断保护逻辑 ---
        'MAX_CONSEC_LOSSES': [4],             # 2: 连亏限制
        'BLACK_SWAN_DD': [0.25],            # 2: 账户回撤熔断阈值
    }
    # 🧮 组合总数: 3 * 2 * 3 * 1 * 4 * 3 * 3 * 1 * 1 * 1 * 1 * 1 * 1 * 1 * 2 * 1 * 1 * 2 * 2 = 5,184 组

    eff_lev = param_grid['RISK_RATIO'][0] * param_grid['LEVERAGE'][0]
    liq_thr = 1.0 / eff_lev

    print("=" * 64)
    print('  X-VOID Omega v7.1 "The Fortress — Unleashed"')
    print("=" * 64)
    print(f"  Symbol: {args.symbol} | Period: {args.period} | Days: {args.days}")
    print(f"  Effective Leverage: {eff_lev}x")
    print(f"  Liq Threshold: {liq_thr:.2%} adverse move")
    print(f"  Vault System: 50% growth → 20% profit locked")
    print(f"  Pyramiding: Up to 3 layers | BE+0.8*ATR safety lock (relaxed)")
    print(f"  Signal Matrix: SML as Entry Booster (+20% size), not hard gate")
    print(f"  Anti-Suicide SL: Never moves SL backwards")
    print(f"  Workers: {args.workers}")
    print("=" * 64)

    api_key = os.getenv('BINANCE_API_KEY', '')
    api_secret = os.getenv('BINANCE_API_SECRET', '')
    if config_module:
        try:
            cfg = config_module.SYSTEM_CONFIG
            api_key = cfg.get('API_KEY', api_key)
            api_secret = cfg.get('API_SECRET', api_secret)
        except Exception:
            pass

    if BinanceClient:
        client = BinanceClient(api_key=api_key, api_secret=api_secret)
    else:
        client = None

    worker = BacktestWorker(client, args.symbol)
    worker.run_mega_grid(param_grid, args.period, args.days,
                         max_workers=args.workers)


if __name__ == '__main__':
    main()
