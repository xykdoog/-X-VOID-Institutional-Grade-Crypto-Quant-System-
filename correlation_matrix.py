#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
相关性矩阵计算模块 - correlation_matrix.py
🔥 P0修复：投资组合相关性检测（基于价格相关性而非简单多空比例）

核心功能：
1. 计算持仓币种之间的价格相关系数（Pearson）
2. 检测过度集中风险（高相关性资产占比过高）
3. 动态调整仓位大小以控制组合风险
"""

import numpy as np
import pandas as pd
from datetime import datetime, timedelta
import threading
from logger_setup import logger

# 全局相关性矩阵缓存
_correlation_cache = {}
_correlation_cache_lock = threading.Lock()
_cache_ttl = 3600  # 缓存有效期1小时


def calculate_price_correlation(client, symbol1, symbol2, lookback_hours=168):
    """
    计算两个币种之间的价格相关系数（Pearson）
    
    Args:
        client: 币安客户端
        symbol1: 币种1（如 'BTCUSDT'）
        symbol2: 币种2（如 'ETHUSDT'）
        lookback_hours: 回溯时间（小时），默认168小时=7天
    
    Returns:
        float: 相关系数 [-1, 1]，失败返回 0
    """
    try:
        # 检查缓存
        cache_key = f"{symbol1}_{symbol2}_{lookback_hours}"
        with _correlation_cache_lock:
            if cache_key in _correlation_cache:
                cached_data = _correlation_cache[cache_key]
                if (datetime.now() - cached_data['timestamp']).seconds < _cache_ttl:
                    return cached_data['correlation']
        
        # 获取历史K线数据（1小时周期）
        from trading_engine import get_historical_klines
        
        limit = min(lookback_hours, 500)  # 币安API限制
        df1 = get_historical_klines(client, symbol1, "1h", limit=limit)
        df2 = get_historical_klines(client, symbol2, "1h", limit=limit)
        
        if df1 is None or df2 is None or len(df1) < 24 or len(df2) < 24:
            logger.warning(f"数据不足，无法计算 {symbol1} 和 {symbol2} 的相关性，安全返回1.0")
            return 1.0  # 🔥 安全修复：数据不足 = 未知 = 极度危险
        
        # 对齐时间戳（取交集）
        df1 = df1.set_index('timestamp')
        df2 = df2.set_index('timestamp')
        
        # 计算收益率序列
        returns1 = df1['close'].pct_change().dropna()
        returns2 = df2['close'].pct_change().dropna()
        
        # 对齐两个序列
        common_index = returns1.index.intersection(returns2.index)
        if len(common_index) < 24:
            logger.warning(f"对齐后数据不足，无法计算 {symbol1} 和 {symbol2} 的相关性，安全返回1.0")
            return 1.0  # 🔥 安全修复：数据不足 = 未知 = 极度危险
        
        returns1_aligned = returns1.loc[common_index]
        returns2_aligned = returns2.loc[common_index]
        
        # 计算Pearson相关系数
        correlation = returns1_aligned.corr(returns2_aligned)
        
        # 缓存结果
        with _correlation_cache_lock:
            _correlation_cache[cache_key] = {
                'correlation': float(correlation),
                'timestamp': datetime.now()
            }
        
        logger.info(f"📊 相关性计算: {symbol1} vs {symbol2} = {correlation:.3f}")
        return float(correlation)
        
    except Exception as e:
        logger.error(f"计算相关性失败 {symbol1} vs {symbol2}: {e}，安全返回1.0")
        return 1.0  # 🔥 安全修复：异常 = 未知 = 极度危险


def build_correlation_matrix(client, symbols):
    """
    构建投资组合相关性矩阵
    
    Args:
        client: 币安客户端
        symbols: 币种列表 ['BTCUSDT', 'ETHUSDT', ...]
    
    Returns:
        pd.DataFrame: 相关性矩阵
    """
    try:
        n = len(symbols)
        if n < 2:
            return pd.DataFrame()
        
        # 初始化矩阵
        corr_matrix = np.eye(n)  # 对角线为1
        
        # 计算上三角矩阵（对称矩阵只需计算一半）
        for i in range(n):
            for j in range(i+1, n):
                corr = calculate_price_correlation(client, symbols[i], symbols[j])
                corr_matrix[i, j] = corr
                corr_matrix[j, i] = corr  # 对称
        
        # 转换为DataFrame
        df_corr = pd.DataFrame(corr_matrix, index=symbols, columns=symbols)
        
        logger.info(f"✅ 相关性矩阵构建完成 ({n}x{n})")
        return df_corr
        
    except Exception as e:
        logger.error(f"构建相关性矩阵失败: {e}")
        return pd.DataFrame()


def check_portfolio_concentration_risk(client, active_positions, new_symbol, new_direction):
    """
    🔥 P0修复：投资组合集中度风险检测（基于价格相关性）
    
    核心逻辑：
    1. 提取当前持仓的所有币种
    2. 计算新币种与现有持仓的相关系数
    3. 检测高相关性资产占比是否超限
    
    Args:
        client: 币安客户端
        active_positions: 当前活跃持仓字典
        new_symbol: 新开仓币种
        new_direction: 新开仓方向 ('LONG' 或 'SHORT')
    
    Returns:
        (allowed: bool, reason: str, risk_scalar: float)
        - allowed: 是否允许开仓
        - reason: 拒绝原因或风险提示
        - risk_scalar: 风险缩放因子（1.0=正常，<1.0=降低仓位）
    """
    try:
        from config import SYSTEM_CONFIG
        
        # 提取当前持仓的币种列表
        existing_symbols = set()
        same_direction_symbols = []
        
        for key, positions in active_positions.items():
            positions_list = positions if isinstance(positions, list) else [positions]
            for pos in positions_list:
                symbol = pos.get('real_symbol', key.split('_')[0])
                existing_symbols.add(symbol)
                
                # 同向持仓
                if pos.get('type') == new_direction:
                    same_direction_symbols.append(symbol)
        
        if not existing_symbols:
            return True, "无现有持仓，放行", 1.0
        
        # 🔥 相关性阈值配置
        HIGH_CORR_THRESHOLD = SYSTEM_CONFIG.get("HIGH_CORRELATION_THRESHOLD", 0.7)
        MAX_HIGH_CORR_RATIO = SYSTEM_CONFIG.get("MAX_HIGH_CORR_RATIO", 0.6)
        
        # 计算新币种与现有持仓的相关系数
        high_corr_count = 0
        total_comparisons = 0
        max_correlation = 0.0
        high_corr_symbols = []
        
        for existing_symbol in same_direction_symbols:
            if existing_symbol == new_symbol:
                continue  # 跳过自己
            
            corr = calculate_price_correlation(client, new_symbol, existing_symbol)
            total_comparisons += 1
            
            if abs(corr) > HIGH_CORR_THRESHOLD:
                high_corr_count += 1
                high_corr_symbols.append(f"{existing_symbol}({corr:.2f})")
            
            max_correlation = max(max_correlation, abs(corr))
        
        if total_comparisons == 0:
            return True, "无同向持仓可比较，放行", 1.0
        
        # 计算高相关性资产占比
        high_corr_ratio = high_corr_count / total_comparisons
        
        logger.info(f"📊 相关性检测: {new_symbol} {new_direction}")
        logger.info(f"   高相关资产: {high_corr_count}/{total_comparisons} ({high_corr_ratio:.1%})")
        logger.info(f"   最大相关系数: {max_correlation:.3f}")
        
        # 🔥 风险判定逻辑
        if high_corr_ratio > MAX_HIGH_CORR_RATIO:
            # 高相关性资产占比超限
            reason = (
                f"高相关性资产占比 {high_corr_ratio:.1%} > {MAX_HIGH_CORR_RATIO:.0%}\n"
                f"高相关币种: {', '.join(high_corr_symbols[:3])}\n"
                f"拒绝开仓以防止过度集中风险"
            )
            logger.warning(f"🚫 {reason}")
            
            # 发送告警
            from utils import send_tg_alert
            import html
            send_tg_alert(
                f"🚫 <b>[相关性风控拦截]</b>\n\n"
                f"币种: {html.escape(new_symbol)}\n"
                f"方向: {new_direction}\n"
                f"高相关资产占比: {high_corr_ratio:.1%}\n"
                f"阈值: {MAX_HIGH_CORR_RATIO:.0%}\n"
                f"高相关币种: {html.escape(', '.join(high_corr_symbols[:3]))}\n\n"
                f"⚠️ 拒绝开仓以防止过度集中风险"
            )
            
            return False, reason, 0.0
        
        elif high_corr_ratio > MAX_HIGH_CORR_RATIO * 0.7:
            # 接近阈值，降低仓位
            risk_scalar = 0.5
            reason = (
                f"高相关性资产占比 {high_corr_ratio:.1%} 接近阈值\n"
                f"仓位已降低50%以控制风险"
            )
            logger.warning(f"⚠️ {reason}")
            
            from utils import send_tg_msg
            send_tg_msg(
                f"⚠️ <b>[相关性风险提示]</b>\n\n"
                f"币种: {new_symbol}\n"
                f"高相关资产占比: {high_corr_ratio:.1%}\n"
                f"仓位已自动降低50%"
            )
            
            return True, reason, risk_scalar
        
        else:
            # 相关性正常
            return True, f"相关性检查通过 (高相关占比={high_corr_ratio:.1%})", 1.0
        
    except Exception as e:
        logger.error(f"相关性风险检测失败: {e}")
        # 异常时保守拒绝
        return False, f"相关性检测异常: {str(e)[:50]}", 0.0


def get_portfolio_diversification_score(client, active_positions):
    """
    计算投资组合分散化得分（0-100分）
    
    得分越高表示分散化越好，相关性越低
    
    Args:
        client: 币安客户端
        active_positions: 当前活跃持仓字典
    
    Returns:
        float: 分散化得分 [0, 100]
    """
    try:
        # 提取所有持仓币种
        symbols = []
        for key, positions in active_positions.items():
            positions_list = positions if isinstance(positions, list) else [positions]
            for pos in positions_list:
                symbol = pos.get('real_symbol', key.split('_')[0])
                if symbol not in symbols:
                    symbols.append(symbol)
        
        if len(symbols) < 2:
            return 100.0  # 单一持仓视为完全分散
        
        # 构建相关性矩阵
        corr_matrix = build_correlation_matrix(client, symbols)
        if corr_matrix.empty:
            return 50.0  # 无法计算时返回中性分数
        
        # 计算平均相关系数（排除对角线）
        n = len(symbols)
        total_corr = 0.0
        count = 0
        
        for i in range(n):
            for j in range(i+1, n):
                total_corr += abs(corr_matrix.iloc[i, j])
                count += 1
        
        avg_corr = total_corr / count if count > 0 else 0.0
        
        # 转换为分散化得分（相关性越低，得分越高）
        # avg_corr=0 → score=100, avg_corr=1 → score=0
        score = (1 - avg_corr) * 100
        
        logger.info(f"📊 投资组合分散化得分: {score:.1f}/100 (平均相关性={avg_corr:.3f})")
        return score
        
    except Exception as e:
        logger.error(f"计算分散化得分失败: {e}")
        return 50.0


def clear_correlation_cache():
    """清空相关性缓存（用于手动刷新）"""
    with _correlation_cache_lock:
        _correlation_cache.clear()
    logger.info("✅ 相关性缓存已清空")


print("✅ 相关性矩阵模块已加载（P0修复：基于价格相关性的投资组合风险检测）")
