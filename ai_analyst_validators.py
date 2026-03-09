#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI Analyst Validators - P1修复 #14, #16
参数变化幅度限制 & AI幻觉验证
"""

import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# ==========================================
# P1修复 #14: 参数变化幅度限制
# ==========================================

def validate_tune_params(old_params, new_params, max_change_ratio=0.3):
    """
    验证并限制参数变化幅度（P1修复#14）
    
    Args:
        old_params: 旧参数字典
        new_params: 新参数字典
        max_change_ratio: 单次最大变化比例（默认30%）
    
    Returns:
        dict: 验证后的参数字典
    """
    validated_params = {}
    warnings = []
    
    for key in new_params:
        old_val = old_params.get(key, 0)
        new_val = new_params[key]
        
        # 跳过布尔值和字符串类型
        if isinstance(new_val, (bool, str)):
            validated_params[key] = new_val
            continue
        
        # 数值类型参数检查
        if old_val > 0 and isinstance(old_val, (int, float)) and isinstance(new_val, (int, float)):
            change_ratio = abs(new_val - old_val) / old_val
            
            if change_ratio > max_change_ratio:
                # 限制变化幅度
                if new_val > old_val:
                    validated_val = old_val * (1 + max_change_ratio)
                else:
                    validated_val = old_val * (1 - max_change_ratio)
                
                # 保持原始类型
                if isinstance(old_val, int):
                    validated_val = int(validated_val)
                
                validated_params[key] = validated_val
                
                warning_msg = (
                    f"⚠️ {key}变化过大({change_ratio:.1%})，"
                    f"从{old_val}→{new_val}限制为{validated_val} "
                    f"(最大变化{max_change_ratio:.0%})"
                )
                logger.warning(warning_msg)
                warnings.append(warning_msg)
            else:
                validated_params[key] = new_val
        else:
            # 新参数或零值参数，直接使用新值
            validated_params[key] = new_val
    
    if warnings:
        logger.info(f"✅ 参数验证完成，{len(warnings)}个参数被限制变化幅度")
    
    return validated_params, warnings


# ==========================================
# P1修复 #16: AI幻觉验证
# ==========================================

def validate_ai_response(response, symbol, current_price):
    """
    验证AI响应的合理性（P1修复#16）
    
    Args:
        response: AI响应字典
        symbol: 交易对符号
        current_price: 当前价格
    
    Returns:
        dict: 验证后的响应字典
    """
    validated = response.copy()
    warnings = []
    
    # 1. 检查支撑位是否合理（±50%范围内）
    if 'support_level' in validated:
        support = validated['support_level']
        if isinstance(support, (int, float)):
            if support < current_price * 0.5 or support > current_price * 1.5:
                warning = f"⚠️ AI返回的支撑位{support}不合理，当前价{current_price}"
                logger.warning(warning)
                warnings.append(warning)
                validated['support_level'] = current_price * 0.95  # 使用保守估计
                validated['support_level_adjusted'] = True
    
    # 2. 检查阻力位是否合理（±50%范围内）
    if 'resistance_level' in validated:
        resistance = validated['resistance_level']
        if isinstance(resistance, (int, float)):
            if resistance < current_price * 0.5 or resistance > current_price * 1.5:
                warning = f"⚠️ AI返回的阻力位{resistance}不合理，当前价{current_price}"
                logger.warning(warning)
                warnings.append(warning)
                validated['resistance_level'] = current_price * 1.05
                validated['resistance_level_adjusted'] = True
    
    # 3. 检查置信度范围（0-1）
    if 'confidence' in validated:
        conf = validated['confidence']
        if isinstance(conf, (int, float)):
            if not (0 <= conf <= 1):
                warning = f"⚠️ 置信度{conf}超出范围[0,1]，重置为0.5"
                logger.warning(warning)
                warnings.append(warning)
                validated['confidence'] = 0.5
                validated['confidence_adjusted'] = True
    
    # 4. 检查风险等级范围（1-10）
    if 'risk_level' in validated:
        risk = validated['risk_level']
        if isinstance(risk, (int, float)):
            if not (1 <= risk <= 10):
                warning = f"⚠️ 风险等级{risk}超出范围[1,10]，重置为5"
                logger.warning(warning)
                warnings.append(warning)
                validated['risk_level'] = 5
                validated['risk_level_adjusted'] = True
    
    # 5. 检查止损价格合理性
    if 'stop_loss' in validated:
        stop_loss = validated['stop_loss']
        if isinstance(stop_loss, (int, float)):
            # 止损不应超过当前价格的±20%
            if stop_loss < current_price * 0.8 or stop_loss > current_price * 1.2:
                warning = f"⚠️ 止损价{stop_loss}偏离当前价{current_price}过大"
                logger.warning(warning)
                warnings.append(warning)
                validated['stop_loss'] = current_price * 0.95  # 默认5%止损
                validated['stop_loss_adjusted'] = True
    
    # 6. 检查目标价格合理性
    if 'target_price' in validated:
        target = validated['target_price']
        if isinstance(target, (int, float)):
            # 目标价不应超过当前价格的±100%
            if target < current_price * 0.5 or target > current_price * 2.0:
                warning = f"⚠️ 目标价{target}偏离当前价{current_price}过大"
                logger.warning(warning)
                warnings.append(warning)
                validated['target_price'] = current_price * 1.1  # 默认10%目标
                validated['target_price_adjusted'] = True
    
    # 7. 添加验证元数据
    if warnings:
        validated['validation_warnings'] = warnings
        validated['validation_timestamp'] = datetime.now().isoformat()
        logger.info(f"✅ AI响应验证完成，{len(warnings)}个字段被调整")
    
    return validated


# ==========================================
# P1修复 #18: 加权情绪分析
# ==========================================

def calculate_weighted_sentiment(news_list):
    """
    计算加权情绪分数（P1修复#18）
    
    Args:
        news_list: 新闻列表，每条新闻包含：
            - published_at: 发布时间（datetime对象）
            - votes: 投票数（int）
            - sentiment: 情绪标签（'positive'/'neutral'/'negative'）
    
    Returns:
        dict: {
            'score': float,        # 归一化情绪分数 [-1, 1]
            'label': str,          # 'BULLISH'/'NEUTRAL'/'BEARISH'
            'confidence': float    # 置信度 [0, 1]
        }
    """
    import math
    
    if not news_list:
        return {
            'score': 0.0,
            'label': 'NEUTRAL',
            'confidence': 0.0
        }
    
    total_weight = 0
    weighted_score = 0
    
    for news in news_list:
        # 时效性权重（越新权重越高，24小时半衰期）
        try:
            if isinstance(news.get('published_at'), str):
                from dateutil import parser
                published_at = parser.parse(news['published_at'])
            else:
                published_at = news.get('published_at', datetime.now())
            
            age_hours = (datetime.now() - published_at).total_seconds() / 3600
            time_weight = math.exp(-age_hours / 24)
        except Exception as e:
            logger.warning(f"⚠️ 时间解析失败: {e}")
            time_weight = 0.5  # 默认权重
        
        # 影响力权重（基于投票数）
        votes = news.get('votes', 1)
        if isinstance(votes, dict):
            votes = votes.get('positive', 0) + votes.get('negative', 0)
        influence_weight = 1 + math.log10(max(votes, 1))
        
        # 综合权重
        weight = time_weight * influence_weight
        
        # 情绪分数（positive=1, neutral=0, negative=-1）
        sentiment_map = {'positive': 1, 'neutral': 0, 'negative': -1}
        sentiment_score = sentiment_map.get(news.get('sentiment', 'neutral').lower(), 0)
        
        weighted_score += sentiment_score * weight
        total_weight += weight
    
    # 归一化到[-1, 1]
    final_sentiment = weighted_score / total_weight if total_weight > 0 else 0
    
    # 分类标签
    if final_sentiment > 0.3:
        label = 'BULLISH'
    elif final_sentiment < -0.3:
        label = 'BEARISH'
    else:
        label = 'NEUTRAL'
    
    # 置信度（权重越高置信度越高）
    confidence = min(total_weight / 10, 1.0)
    
    logger.info(
        f"✅ 加权情绪分析: {label} "
        f"(分数={final_sentiment:.2f}, 置信度={confidence:.2f})"
    )
    
    return {
        'score': final_sentiment,
        'label': label,
        'confidence': confidence
    }


# ==========================================
# 模块测试
# ==========================================

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    
    print("=" * 60)
    print("🧪 AI Analyst Validators Test")
    print("=" * 60)
    
    # 测试1: 参数变化幅度限制
    print("\n[测试1] 参数变化幅度限制")
    old_params = {
        'ADX_THR': 12,
        'ATR_MULT': 2.0,
        'LEVERAGE': 20,
        'RISK_RATIO': 0.05
    }
    new_params = {
        'ADX_THR': 28,  # 变化133%，超过30%
        'ATR_MULT': 2.5,  # 变化25%，未超过
        'LEVERAGE': 15,  # 变化-25%，未超过
        'RISK_RATIO': 0.08  # 变化60%，超过30%
    }
    
    validated, warnings = validate_tune_params(old_params, new_params)
    print(f"原始参数: {new_params}")
    print(f"验证后参数: {validated}")
    print(f"警告数量: {len(warnings)}")
    
    # 测试2: AI幻觉验证
    print("\n[测试2] AI幻觉验证")
    ai_response = {
        'support_level': 999999,  # 不合理
        'resistance_level': 50000,
        'confidence': 1.5,  # 超出范围
        'risk_level': 15,  # 超出范围
        'stop_loss': 45000
    }
    current_price = 50000
    
    validated_response = validate_ai_response(ai_response, 'BTCUSDT', current_price)
    print(f"原始响应: {ai_response}")
    print(f"验证后响应: {validated_response}")
    
    # 测试3: 加权情绪分析
    print("\n[测试3] 加权情绪分析")
    news_list = [
        {
            'published_at': datetime.now(),
            'votes': 100,
            'sentiment': 'positive'
        },
        {
            'published_at': datetime.now(),
            'votes': 50,
            'sentiment': 'negative'
        },
        {
            'published_at': datetime.now(),
            'votes': 20,
            'sentiment': 'neutral'
        }
    ]
    
    sentiment_result = calculate_weighted_sentiment(news_list)
    print(f"情绪分析结果: {sentiment_result}")
    
    print("\n✅ 所有测试完成!")
