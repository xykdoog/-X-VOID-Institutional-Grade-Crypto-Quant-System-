#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
🔥 P2优化：MTF趋势强度检测增强模块
Enhanced Multi-Timeframe Resonance with Trend Strength Analysis
"""

import numpy as np
from logger_setup import logger


def check_mtf_resonance_enhanced(client, symbol, df_low, signal_type, high_timeframe='4h'):
    """
    🔥 增强版MTF多周期共振检测（含趋势强度分析）
    
    核心改进：
    1. 计算高周期EMA斜率（最近5根K线）
    2. 判断趋势强度：斜率<1%视为横盘，拒绝开仓
    3. 多维度验证：价格位置 + EMA方向 + 趋势强度
    4. 动态阈值：根据波动率调整斜率阈值
    
    Args:
        client: Binance客户端
        symbol: 交易对
        df_low: 低周期K线数据（如15m）
        signal_type: 信号类型（'BUY' 或 'SELL'）
        high_timeframe: 高周期时间框架（默认4h）
    
    Returns:
        dict or None: {'signal': 'HOLD', 'reason': str} 或 None（放行）
    """
    if df_low is None or len(df_low) < 2:
        return None
    
    try:
        # ==========================================
        # Step 1: 获取高周期K线数据
        # ==========================================
        from trading_engine import get_historical_klines
        
        df_high = get_historical_klines(client, symbol, high_timeframe, limit=50)
        
        if df_high is None or len(df_high) < 10:
            logger.warning(f"⚠️ [{symbol}] 高周期数据不足，跳过MTF检测")
            return None
        
        # 计算高周期EMA（如果未计算）
        if 'EMA_Trend' not in df_high.columns:
            from config import SYSTEM_CONFIG, STRATEGY_PRESETS
            current_mode = SYSTEM_CONFIG.get("STRATEGY_MODE", "STANDARD")
            preset_config = STRATEGY_PRESETS.get(current_mode, {})
            ema_length = preset_config.get("MTF_TREND_EMA", SYSTEM_CONFIG.get("EMA_TREND", 89))
            import pandas_ta as ta
            ema_result = ta.ema(df_high['close'], length=ema_length)
            if ema_result is not None:
                df_high['EMA_Trend'] = ema_result
            else:
                logger.warning(f"⚠️ [{symbol}] 高周期EMA计算失败")
                return None
        
        # ==========================================
        # Step 2: 获取高周期最新数据
        # ==========================================
        high_close = df_high['close'].iloc[-1]
        high_ema = df_high['EMA_Trend'].iloc[-1]
        
        # ==========================================
        # Step 3: 计算EMA斜率（趋势强度）
        # ==========================================
        ema_slope, slope_pct = _calculate_ema_slope(df_high, lookback=5)
        
        # 计算波动率（用于动态阈值）
        volatility = _calculate_volatility(df_high, lookback=20)
        
        # 动态斜率阈值：波动率越大，阈值越高
        min_slope_threshold = max(0.01, volatility * 0.5)  # 最低1%，根据波动率调整
        
        logger.info(
            f"📊 [{symbol}] MTF分析 | "
            f"高周期: {high_timeframe} | "
            f"价格: {high_close:.2f} | "
            f"EMA: {high_ema:.2f} | "
            f"斜率: {slope_pct:.2f}% | "
            f"波动率: {volatility:.2f}% | "
            f"阈值: {min_slope_threshold:.2f}%"
        )
        
        # ==========================================
        # Step 4: 多单检测逻辑
        # ==========================================
        if signal_type == 'BUY':
            # 检查1：价格必须在EMA上方
            if high_close < high_ema:
                reason = f"MTF共振失败：高周期价格在EMA下方（{high_close:.2f} < {high_ema:.2f}）"
                logger.warning(f"🚫 [{symbol}] {reason}")
                return {'signal': 'HOLD', 'reason': reason}
            
            # 检查2：EMA必须向上（斜率>0）
            if ema_slope <= 0:
                reason = f"MTF共振失败：高周期EMA向下（斜率={slope_pct:.2f}%）"
                logger.warning(f"🚫 [{symbol}] {reason}")
                return {'signal': 'HOLD', 'reason': reason}
            
            # 检查3：趋势强度必须足够（斜率>阈值）
            if abs(slope_pct) < min_slope_threshold:
                reason = f"MTF共振失败：高周期横盘（斜率{slope_pct:.2f}% < 阈值{min_slope_threshold:.2f}%）"
                logger.warning(f"🚫 [{symbol}] {reason}")
                return {'signal': 'HOLD', 'reason': reason}
            
            # 所有检查通过
            logger.info(f"✅ [{symbol}] MTF共振通过：多头趋势强度{slope_pct:.2f}%")
            return None
        
        # ==========================================
        # Step 5: 空单检测逻辑
        # ==========================================
        elif signal_type == 'SELL':
            # 检查1：价格必须在EMA下方
            if high_close > high_ema:
                reason = f"MTF共振失败：高周期价格在EMA上方（{high_close:.2f} > {high_ema:.2f}）"
                logger.warning(f"🚫 [{symbol}] {reason}")
                return {'signal': 'HOLD', 'reason': reason}
            
            # 检查2：EMA必须向下（斜率<0）
            if ema_slope >= 0:
                reason = f"MTF共振失败：高周期EMA向上（斜率={slope_pct:.2f}%）"
                logger.warning(f"🚫 [{symbol}] {reason}")
                return {'signal': 'HOLD', 'reason': reason}
            
            # 检查3：趋势强度必须足够（斜率>阈值）
            if abs(slope_pct) < min_slope_threshold:
                reason = f"MTF共振失败：高周期横盘（斜率{abs(slope_pct):.2f}% < 阈值{min_slope_threshold:.2f}%）"
                logger.warning(f"🚫 [{symbol}] {reason}")
                return {'signal': 'HOLD', 'reason': reason}
            
            # 所有检查通过
            logger.info(f"✅ [{symbol}] MTF共振通过：空头趋势强度{abs(slope_pct):.2f}%")
            return None
        
        else:
            logger.warning(f"⚠️ [{symbol}] 未知信号类型: {signal_type}")
            return None
        
    except Exception as e:
        logger.error(f"⚠️ [{symbol}] MTF共振检测异常: {e}")
        # 异常时保守拦截
        return {'signal': 'HOLD', 'reason': f'MTF检测异常: {str(e)[:50]}'}


def _calculate_ema_slope(df, lookback=5):
    """
    计算EMA斜率（趋势强度指标）
    
    Args:
        df: K线数据（必须包含EMA_Trend列）
        lookback: 回溯周期（默认5根K线）
    
    Returns:
        tuple: (斜率绝对值, 斜率百分比)
    """
    if 'EMA_Trend' not in df.columns or len(df) < lookback + 1:
        return 0, 0
    
    try:
        # 获取最近N根K线的EMA值
        ema_current = df['EMA_Trend'].iloc[-1]
        ema_past = df['EMA_Trend'].iloc[-(lookback + 1)]
        
        # 计算斜率（百分比变化）
        if ema_past > 0:
            slope_pct = ((ema_current - ema_past) / ema_past) * 100
            slope_abs = ema_current - ema_past
            
            return slope_abs, slope_pct
        else:
            return 0, 0
        
    except Exception as e:
        logger.error(f"⚠️ EMA斜率计算异常: {e}")
        return 0, 0


def _calculate_volatility(df, lookback=20):
    """
    计算价格波动率（用于动态阈值）
    
    Args:
        df: K线数据
        lookback: 回溯周期（默认20根K线）
    
    Returns:
        float: 波动率（百分比）
    """
    if len(df) < lookback:
        return 0.02  # 默认2%
    
    try:
        # 计算收益率标准差
        returns = df['close'].tail(lookback).pct_change().dropna()
        
        if len(returns) > 0:
            volatility = returns.std() * 100  # 转换为百分比
            return volatility
        else:
            return 0.02
        
    except Exception as e:
        logger.error(f"⚠️ 波动率计算异常: {e}")
        return 0.02


def analyze_mtf_trend_strength(client, symbol, timeframes=['15m', '1h', '4h']):
    """
    多周期趋势强度分析（诊断工具）
    
    Args:
        client: Binance客户端
        symbol: 交易对
        timeframes: 时间框架列表
    
    Returns:
        dict: 各周期趋势强度分析结果
    """
    results = {}
    
    try:
        from trading_engine import get_historical_klines
        from config import SYSTEM_CONFIG, STRATEGY_PRESETS
        import pandas_ta as ta
        
        current_mode = SYSTEM_CONFIG.get("STRATEGY_MODE", "STANDARD")
        preset_config = STRATEGY_PRESETS.get(current_mode, {})
        ema_length = preset_config.get("MTF_TREND_EMA", SYSTEM_CONFIG.get("EMA_TREND", 89))
        
        for tf in timeframes:
            df = get_historical_klines(client, symbol, tf, limit=50)
            
            if df is None or len(df) < 10:
                results[tf] = {'error': '数据不足'}
                continue
            
            # 计算EMA
            if 'EMA_Trend' not in df.columns:
                ema_result = ta.ema(df['close'], length=ema_length)
                if ema_result is not None:
                    df['EMA_Trend'] = ema_result
                else:
                    results[tf] = {'error': 'EMA计算失败'}
                    continue
            
            # 获取数据
            close = df['close'].iloc[-1]
            ema = df['EMA_Trend'].iloc[-1]
            
            # 计算斜率和波动率
            slope_abs, slope_pct = _calculate_ema_slope(df, lookback=5)
            volatility = _calculate_volatility(df, lookback=20)
            
            # 判断趋势方向
            if close > ema and slope_pct > 0.01:
                trend = '上升趋势'
            elif close < ema and slope_pct < -0.01:
                trend = '下降趋势'
            else:
                trend = '横盘震荡'
            
            # 判断趋势强度
            if abs(slope_pct) > 0.05:
                strength = '强'
            elif abs(slope_pct) > 0.02:
                strength = '中'
            elif abs(slope_pct) > 0.01:
                strength = '弱'
            else:
                strength = '极弱/横盘'
            
            results[tf] = {
                'close': close,
                'ema': ema,
                'price_position': '上方' if close > ema else '下方',
                'slope_pct': slope_pct,
                'volatility': volatility,
                'trend': trend,
                'strength': strength
            }
        
        return results
        
    except Exception as e:
        logger.error(f"⚠️ [{symbol}] MTF趋势强度分析异常: {e}")
        return {'error': str(e)}


def get_mtf_recommendation(client, symbol):
    """
    获取MTF综合建议（辅助决策工具）
    
    Args:
        client: Binance客户端
        symbol: 交易对
    
    Returns:
        dict: 综合建议
    """
    try:
        analysis = analyze_mtf_trend_strength(client, symbol, timeframes=['15m', '1h', '4h'])
        
        if 'error' in analysis:
            return {'recommendation': 'HOLD', 'reason': '数据异常'}
        
        # 提取各周期趋势
        tf_15m = analysis.get('15m', {})
        tf_1h = analysis.get('1h', {})
        tf_4h = analysis.get('4h', {})
        
        # 多头共振检测
        bullish_count = sum([
            1 for tf in [tf_15m, tf_1h, tf_4h]
            if tf.get('trend') == '上升趋势'
        ])
        
        # 空头共振检测
        bearish_count = sum([
            1 for tf in [tf_15m, tf_1h, tf_4h]
            if tf.get('trend') == '下降趋势'
        ])
        
        # 综合判断
        if bullish_count >= 2 and tf_4h.get('strength') in ['强', '中']:
            recommendation = 'BUY'
            reason = f"多头共振（{bullish_count}/3周期），高周期趋势强度{tf_4h.get('strength')}"
        elif bearish_count >= 2 and tf_4h.get('strength') in ['强', '中']:
            recommendation = 'SELL'
            reason = f"空头共振（{bearish_count}/3周期），高周期趋势强度{tf_4h.get('strength')}"
        else:
            recommendation = 'HOLD'
            reason = f"周期不共振或趋势强度不足（多{bullish_count}/空{bearish_count}）"
        
        return {
            'recommendation': recommendation,
            'reason': reason,
            'analysis': analysis,
            'bullish_count': bullish_count,
            'bearish_count': bearish_count
        }
        
    except Exception as e:
        logger.error(f"⚠️ [{symbol}] MTF综合建议异常: {e}")
        return {'recommendation': 'HOLD', 'reason': f'异常: {str(e)[:50]}'}


logger.info("✅ 增强版MTF趋势强度检测模块已加载（EMA斜率 + 动态阈值）")
