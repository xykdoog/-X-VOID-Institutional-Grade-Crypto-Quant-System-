#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
资产相关性动态风控引擎 - correlation_engine.py
🔥 V2.0 安全重构：动态标的池 + Beta兜底 + 未知即危险

核心安全原则：
- 废除硬编码 _TRACKED_SYMBOLS，动态从 ASSET_WEIGHTS + ACTIVE_POSITIONS 提取
- 数据获取失败时，通过 BTCUSDT Beta 推断相关性
- 连 Beta 都算不出来时，返回 1.0（完全正相关 = 最高惩罚）
- 金融风控铁律："未知" == "极度危险"，绝不用 0.5 蒙混过关
"""

import time
import threading
import numpy as np
from datetime import datetime
from utils.logger_setup import logger
import config
from utils.utils import retry_on_failure, send_tg_msg

# ==========================================
# 全局相关性内存缓存
# ==========================================
_CORRELATION_CACHE = {}
_CACHE_LOCK = threading.Lock()

# BTC Beta 缓存（各币种与 BTCUSDT 的相关性）
_BTC_BETA_CACHE = {}
_BTC_BETA_LOCK = threading.Lock()

# 安全常量
_BENCHMARK_SYMBOL = "BTCUSDT"
_HIGH_BETA_THRESHOLD = 0.7       # Beta > 0.7 视为与大盘高度正相关
_SAFE_FALLBACK_CORRELATION = 1.0  # 未知 = 极度危险 = 完全正相关
_MIN_DATA_POINTS = 24             # 最少需要 24 根 K 线才能计算相关性


def _get_dynamic_symbol_pool():
    """
    🔥 动态标的池：实时从 ASSET_WEIGHTS + ACTIVE_POSITIONS 提取所有需要监控的币种
    废除硬编码 _TRACKED_SYMBOLS，确保任何新币种都能被覆盖
    """
    symbols = set()

    # 1. 从配置的资产权重中提取
    asset_weights = config.SYSTEM_CONFIG.get("ASSET_WEIGHTS", {})
    symbols.update(asset_weights.keys())

    # 2. 从当前活跃持仓中提取
    try:
        with config.positions_lock:
            for key_sym, positions_data in config.ACTIVE_POSITIONS.items():
                if isinstance(positions_data, list):
                    for pos in positions_data:
                        real_symbol = pos.get('real_symbol',
                                              key_sym.split('_')[0] if '_' in key_sym else key_sym)
                        symbols.add(real_symbol)
                elif isinstance(positions_data, dict):
                    real_symbol = positions_data.get('real_symbol',
                                                     key_sym.split('_')[0] if '_' in key_sym else key_sym)
                    symbols.add(real_symbol)
    except Exception as e:
        logger.warning(f"⚠️ 从 ACTIVE_POSITIONS 提取标的失败: {e}")

    # 3. 确保基准币种 BTCUSDT 始终在池中（用于 Beta 计算）
    symbols.add(_BENCHMARK_SYMBOL)

    return list(symbols)


def _fetch_klines_safe(client, symbol, limit):
    """
    安全获取 K 线数据，带重试。失败返回 None（而非空列表）。
    """
    try:
        @retry_on_failure(max_retries=2, retry_delay=1, operation_name=f"获取{symbol}K线")
        def _fetch(**kwargs):
            return client.futures_klines(symbol=symbol, interval='1h', limit=limit)
        result = _fetch()
        return result if result else None
    except Exception as e:
        logger.debug(f"获取K线失败 {symbol}: {e}")
        return None


def _extract_log_returns(klines, min_len=None):
    """
    从 K 线数据提取对数收益率序列。
    返回 numpy array 或 None（数据不足时）。
    """
    if not klines:
        return None
    if min_len and len(klines) < min_len:
        return None

    closes = np.array([float(k[4]) for k in klines])
    if len(closes) < 2:
        return None

    # 过滤零值和负值（防止 log 爆炸）
    if np.any(closes <= 0):
        return None

    returns = np.diff(np.log(closes))
    return returns if len(returns) >= 2 else None


def _pearson_correlation(returns1, returns2):
    """
    计算皮尔逊相关系数。返回 float 或 None（计算失败时）。
    """
    if returns1 is None or returns2 is None:
        return None
    min_len = min(len(returns1), len(returns2))
    if min_len < 2:
        return None

    r1 = returns1[:min_len]
    r2 = returns2[:min_len]

    corr = np.corrcoef(r1, r2)[0, 1]
    if np.isnan(corr) or np.isinf(corr):
        return None
    return float(corr)


def _calculate_direct_correlation(client, sym1, sym2, lookback_days=3):
    """
    直接计算两个资产的皮尔逊相关系数。
    返回 float（成功）或 None（失败，需要走 fallback）。
    注意：返回 None 而非 0.0，让调用方明确知道"计算失败"。
    """
    try:
        limit = lookback_days * 24

        klines1 = _fetch_klines_safe(client, sym1, limit)
        klines2 = _fetch_klines_safe(client, sym2, limit)

        if klines1 is None or klines2 is None:
            return None

        min_len = min(len(klines1), len(klines2))
        if min_len < _MIN_DATA_POINTS:
            return None

        returns1 = _extract_log_returns(klines1[:min_len])
        returns2 = _extract_log_returns(klines2[:min_len])

        return _pearson_correlation(returns1, returns2)

    except Exception as e:
        logger.debug(f"直接计算相关性失败 {sym1}/{sym2}: {e}")
        return None


def _calculate_btc_beta(client, symbol, lookback_days=3):
    """
    计算单个币种与 BTCUSDT 的 Beta（相关性）。
    返回 float（成功）或 None（失败）。
    """
    if symbol == _BENCHMARK_SYMBOL:
        return 1.0  # BTC 与自身完全正相关

    # 先查 Beta 缓存
    with _BTC_BETA_LOCK:
        cached = _BTC_BETA_CACHE.get(symbol)
        if cached is not None:
            return cached

    # 实时计算
    beta = _calculate_direct_correlation(client, symbol, _BENCHMARK_SYMBOL, lookback_days)

    # 写入缓存（即使是 None 也不缓存，下次重试）
    if beta is not None:
        with _BTC_BETA_LOCK:
            _BTC_BETA_CACHE[symbol] = beta

    return beta


def _infer_correlation_via_beta(client, sym1, sym2, lookback_days=3):
    """
    🔥 动态 Beta 兜底：通过各自与 BTCUSDT 的相关性推断两币种间的相关性。

    逻辑：
    - 如果 sym1 和 sym2 与 BTC 都是高度正相关（> 0.7），推断它们之间极高相关 → 返回 1.0
    - 如果一个高度正相关、一个不是，返回两者 Beta 的乘积（保守估计）
    - 如果连 Beta 都算不出来 → 返回 None（交给绝对安全底线）
    """
    beta1 = _calculate_btc_beta(client, sym1, lookback_days)
    beta2 = _calculate_btc_beta(client, sym2, lookback_days)

    if beta1 is None or beta2 is None:
        # 连大盘相关性都无法计算 → 返回 None，触发绝对安全底线
        logger.warning(
            f"⚠️ Beta兜底失败: {sym1}(beta={beta1}) / {sym2}(beta={beta2})，"
            f"无法推断相关性，将触发安全底线"
        )
        return None

    # 两者都与大盘高度正相关 → 推断它们之间极高相关
    if beta1 > _HIGH_BETA_THRESHOLD and beta2 > _HIGH_BETA_THRESHOLD:
        logger.warning(
            f"🚨 Beta推断: {sym1}(β={beta1:.3f}) 和 {sym2}(β={beta2:.3f}) "
            f"均与BTC高度正相关 → 推断相关性=1.0（最高惩罚）"
        )
        return 1.0

    # 保守估计：返回两者 Beta 的乘积（数学上的上界近似）
    inferred = beta1 * beta2
    logger.info(
        f"📊 Beta推断: {sym1}(β={beta1:.3f}) × {sym2}(β={beta2:.3f}) "
        f"→ 推断相关性={inferred:.3f}"
    )
    return inferred


def _get_correlation_with_fallback(client, sym1, sym2, lookback_days=3):
    """
    🔥 三级回退相关性计算（核心安全链路）：

    Level 1: 直接计算两币种的皮尔逊相关系数
    Level 2: 通过 BTCUSDT Beta 推断相关性
    Level 3: 绝对安全底线 → 返回 1.0（未知即危险）
    """
    # Level 1: 直接计算
    direct_corr = _calculate_direct_correlation(client, sym1, sym2, lookback_days)
    if direct_corr is not None:
        return direct_corr

    logger.info(f"📊 直接计算失败 {sym1}/{sym2}，启动 Beta 兜底...")

    # Level 2: Beta 推断
    beta_corr = _infer_correlation_via_beta(client, sym1, sym2, lookback_days)
    if beta_corr is not None:
        return beta_corr

    # Level 3: 绝对安全底线 — "未知"即"极度危险"
    logger.error(
        f"🚨 安全底线触发: {sym1}/{sym2} 所有相关性计算均失败，"
        f"返回 {_SAFE_FALLBACK_CORRELATION}（完全正相关 = 最高惩罚）"
    )
    return _SAFE_FALLBACK_CORRELATION


def correlation_updater_loop(client):
    """
    🔥 后台静默更新线程：每 4 小时重新计算一次全市场相关性矩阵
    动态标的池：从 ASSET_WEIGHTS + ACTIVE_POSITIONS 实时提取
    """
    logger.info("🔄 相关性动态矩阵更新线程已启动 (每4小时更新)")
    while True:
        try:
            if client is None:
                time.sleep(60)
                continue

            # 🔥 动态提取标的池（废除硬编码 _TRACKED_SYMBOLS）
            symbols = _get_dynamic_symbol_pool()
            n = len(symbols)
            new_cache = {}
            new_beta_cache = {}

            # 先计算所有币种与 BTC 的 Beta（供后续兜底使用）
            for sym in symbols:
                if sym == _BENCHMARK_SYMBOL:
                    new_beta_cache[sym] = 1.0
                    continue
                beta = _calculate_direct_correlation(client, sym, _BENCHMARK_SYMBOL)
                if beta is not None:
                    new_beta_cache[sym] = beta

            # 更新 Beta 缓存
            with _BTC_BETA_LOCK:
                _BTC_BETA_CACHE.clear()
                _BTC_BETA_CACHE.update(new_beta_cache)

            # 两两计算相关性（使用三级回退）
            for i in range(n):
                for j in range(i + 1, n):
                    sym1, sym2 = symbols[i], symbols[j]
                    corr = _get_correlation_with_fallback(client, sym1, sym2)
                    key = tuple(sorted([sym1, sym2]))
                    new_cache[key] = corr

            # 批量更新缓存
            with _CACHE_LOCK:
                _CORRELATION_CACHE.clear()
                _CORRELATION_CACHE.update(new_cache)

            logger.info(
                f"✅ 动态相关性矩阵已更新: {len(new_cache)} 对资产, "
                f"{len(new_beta_cache)} 个Beta值, 标的池={symbols}"
            )

        except Exception as e:
            logger.error(f"❌ 相关性矩阵更新异常: {e}")

        # 休息 4 小时 (14400秒) 后再次计算
        time.sleep(14400)


def get_asset_correlation(client, sym1, sym2, lookback_days=3):
    """
    🔥 O(1) 极速读取 + 安全兜底

    优先从缓存读取；缓存未命中时：
    1. 尝试 Beta 推断（不阻塞，因为 Beta 缓存已由后台线程预热）
    2. 所有方法失败 → 返回 1.0（绝对安全底线）

    绝不返回 0.5！在风控中，"未知"即"极度危险"。
    """
    if sym1 == sym2:
        return 1.0

    key = tuple(sorted([sym1, sym2]))

    # 优先从缓存读取
    with _CACHE_LOCK:
        cached = _CORRELATION_CACHE.get(key)
        if cached is not None:
            return cached

    # 缓存未命中 → 尝试用已有的 Beta 缓存快速推断（不发网络请求）
    with _BTC_BETA_LOCK:
        beta1 = _BTC_BETA_CACHE.get(sym1)
        beta2 = _BTC_BETA_CACHE.get(sym2)

    if beta1 is not None and beta2 is not None:
        # 两者都与 BTC 高度正相关 → 推断极高相关
        if beta1 > _HIGH_BETA_THRESHOLD and beta2 > _HIGH_BETA_THRESHOLD:
            inferred = 1.0
        else:
            inferred = beta1 * beta2

        logger.info(
            f"📊 缓存未命中 {sym1}/{sym2}，Beta快速推断: "
            f"β1={beta1:.3f}, β2={beta2:.3f} → ρ={inferred:.3f}"
        )
        return inferred

    # 所有方法失败 → 绝对安全底线
    logger.warning(
        f"🚨 {sym1}/{sym2} 缓存未命中且无Beta数据，"
        f"返回安全底线 {_SAFE_FALLBACK_CORRELATION}"
    )
    return _SAFE_FALLBACK_CORRELATION


def check_portfolio_correlation(client, new_symbol, existing_positions):
    """
    检查新开仓是否与现有持仓高度相关

    Args:
        client: Binance客户端
        new_symbol: 准备开仓的交易对
        existing_positions: 当前活跃持仓字典

    Returns:
        dict: {
            'allowed': bool,
            'max_correlation': float,
            'correlated_symbol': str,
            'message': str
        }
    """
    try:
        # 相关性阈值（可配置）
        correlation_threshold = config.SYSTEM_CONFIG.get("CORRELATION_THRESHOLD", 0.85)

        # 如果没有现有持仓，直接通过
        if not existing_positions:
            return {
                'allowed': True,
                'max_correlation': 0.0,
                'correlated_symbol': None,
                'message': 'OK'
            }

        # 提取所有现有持仓的交易对
        existing_symbols = set()
        for key_sym, positions_data in existing_positions.items():
            if isinstance(positions_data, list):
                for pos in positions_data:
                    real_symbol = pos.get('real_symbol',
                                          key_sym.split('_')[0] if '_' in key_sym else key_sym)
                    existing_symbols.add(real_symbol)
            else:
                real_symbol = positions_data.get('real_symbol',
                                                  key_sym.split('_')[0] if '_' in key_sym else key_sym)
                existing_symbols.add(real_symbol)

        # 如果新交易对已在持仓中，跳过检查（允许加仓）
        if new_symbol in existing_symbols:
            return {
                'allowed': True,
                'max_correlation': 0.0,
                'correlated_symbol': None,
                'message': 'Same symbol, skip correlation check'
            }

        # 计算与所有现有持仓的相关性
        max_correlation = 0.0
        correlated_symbol = None

        for existing_symbol in existing_symbols:
            if existing_symbol == new_symbol:
                continue

            correlation = get_asset_correlation(client, new_symbol, existing_symbol)

            if abs(correlation) > abs(max_correlation):
                max_correlation = correlation
                correlated_symbol = existing_symbol

        # 判断是否超过阈值
        if abs(max_correlation) >= correlation_threshold:
            message = (
                f"高度相关风险: {new_symbol} 与 {correlated_symbol} "
                f"相关系数 ρ={max_correlation:.4f} (阈值 {correlation_threshold})"
            )
            logger.warning(f"🚨 {message}")

            send_tg_msg(
                f"🚨 <b>相关性风控拦截</b>\n\n"
                f"<b>准备开仓:</b> {new_symbol}\n"
                f"<b>高度相关持仓:</b> {correlated_symbol}\n"
                f"<b>相关系数:</b> <code>ρ = {max_correlation:.4f}</code>\n"
                f"<b>阈值:</b> <code>{correlation_threshold}</code>\n\n"
                f"⚠️ 为防止高度耦合风险，系统已拒绝开仓！"
            )

            return {
                'allowed': False,
                'max_correlation': max_correlation,
                'correlated_symbol': correlated_symbol,
                'message': message
            }

        # 通过检查
        return {
            'allowed': True,
            'max_correlation': max_correlation,
            'correlated_symbol': correlated_symbol,
            'message': 'OK'
        }

    except Exception as e:
        logger.error(f"❌ 相关性检查异常: {e}", exc_info=True)
        # 🔥 安全重构：异常时保守拒绝开仓（fail-closed），绝不放行
        logger.warning(f"🚨 相关性检查异常，安全拒绝开仓: {new_symbol}")
        return {
            'allowed': False,
            'max_correlation': 1.0,
            'correlated_symbol': None,
            'message': f'相关性检查异常(安全拒绝): {str(e)[:80]}'
        }


logger.info("✅ 资产相关性引擎已加载 (V2.0 安全重构: 动态标的池 + Beta兜底 + 未知即危险)")
