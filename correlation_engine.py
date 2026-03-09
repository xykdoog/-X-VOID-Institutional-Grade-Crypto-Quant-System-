#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
资产相关性动态风控引擎 - correlation_engine.py
防止高度耦合风险，实现机构级风险分散
"""

import numpy as np
from datetime import datetime, timedelta
from logger_setup import logger
from config import SYSTEM_CONFIG, state_lock
from utils import retry_on_failure, send_tg_msg


def get_asset_correlation(client, sym1, sym2, lookback_days=3):
    """
    计算两个资产的皮尔逊相关系数
    
    Args:
        client: Binance客户端
        sym1: 第一个交易对
        sym2: 第二个交易对
        lookback_days: 回溯天数（默认3天）
    
    Returns:
        float: 相关系数 ρ ∈ [-1, 1]，失败返回 0
    """
    try:
        # 拉取过去N天的1h K线数据
        limit = lookback_days * 24
        
        @retry_on_failure(max_retries=2, retry_delay=1, operation_name=f"获取{sym1}相关性数据")
        def fetch_klines(symbol):
            klines = client.futures_klines(
                symbol=symbol,
                interval='1h',
                limit=limit
            )
            return klines
        
        klines1 = fetch_klines(sym1)
        klines2 = fetch_klines(sym2)
        
        if not klines1 or not klines2:
            logger.warning(f"⚠️ 无法获取K线数据: {sym1} 或 {sym2}")
            return 0.0
        
        # 确保数据长度一致
        min_len = min(len(klines1), len(klines2))
        if min_len < 24:  # 至少需要24小时数据
            logger.warning(f"⚠️ 数据不足: {sym1}/{sym2} 仅有 {min_len} 条")
            return 0.0
        
        # 提取收盘价并计算对数收益率
        closes1 = np.array([float(k[4]) for k in klines1[:min_len]])
        closes2 = np.array([float(k[4]) for k in klines2[:min_len]])
        
        # 计算对数收益率 log(P_t / P_{t-1})
        returns1 = np.diff(np.log(closes1))
        returns2 = np.diff(np.log(closes2))
        
        # 计算皮尔逊相关系数
        if len(returns1) < 2 or len(returns2) < 2:
            return 0.0
        
        correlation = np.corrcoef(returns1, returns2)[0, 1]
        
        # 处理NaN情况（如果收益率全为0）
        if np.isnan(correlation):
            correlation = 0.0
        
        logger.info(f"📊 相关性计算: {sym1} vs {sym2} = {correlation:.4f}")
        return float(correlation)
        
    except Exception as e:
        logger.error(f"❌ 计算相关性失败 {sym1}/{sym2}: {e}", exc_info=True)
        return 0.0


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
        correlation_threshold = SYSTEM_CONFIG.get("CORRELATION_THRESHOLD", 0.85)
        
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
            # 支持列表形式的多笔订单
            if isinstance(positions_data, list):
                for pos in positions_data:
                    real_symbol = pos.get('real_symbol', key_sym.split('_')[0] if '_' in key_sym else key_sym)
                    existing_symbols.add(real_symbol)
            else:
                real_symbol = positions_data.get('real_symbol', key_sym.split('_')[0] if '_' in key_sym else key_sym)
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
            
            # 发送告警
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
        # 异常情况下保守处理：允许开仓但记录日志
        return {
            'allowed': True,
            'max_correlation': 0.0,
            'correlated_symbol': None,
            'message': f'Check failed: {str(e)[:50]}'
        }


logger.info("✅ 资产相关性引擎已加载")
