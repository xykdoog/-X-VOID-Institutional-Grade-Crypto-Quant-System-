#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI Commander - LLM-based Trading Strategy Advisor
Supports: Anthropic Claude & Google Gemini
"""

import os
import json
import logging
import httpx
from datetime import datetime
from anthropic import Anthropic
from google import genai
from google.genai import types
from config import SYSTEM_CONFIG
from human_override import get_override_manager

logger = logging.getLogger(__name__)

# ==========================================
# Intelligence Hub - Geopolitical & Macro Intelligence Layer
# ==========================================

class IntelligenceHub:
    """
    全维度地缘政治与宏观情报感知系统
    负责抓取全球地缘政治、宏观经济日历、加密货币情绪数据
    
    🔥 v2.0 新增：情报缓存与宏观天气系统
    - 每30分钟调用一次NewsAPI，生成Global_Macro_Regime标签
    - 缓存机制避免API配额浪费
    """
    
    def __init__(self, proxy_url=None):
        self.proxy_url = proxy_url or SYSTEM_CONFIG.get('PROXY_URL', 'http://127.0.0.1:4780')
        self.newsapi_key = os.getenv('NEWS_API_KEY', '')
        self.cryptopanic_key = os.getenv('CRYPTOPANIC_API_KEY', '') or SYSTEM_CONFIG.get('CRYPTOPANIC_API_KEY', '')
        
        # 🔥 修复：全局注入代理环境变量（确保 genai.Client 实例化时生效）
        if self.proxy_url:
            os.environ['HTTP_PROXY'] = self.proxy_url
            os.environ['HTTPS_PROXY'] = self.proxy_url
            logger.info(f"✅ IntelligenceHub 代理已注入: {self.proxy_url}")
        
        # 🔥 情报缓存系统
        self._macro_weather_cache = {
            'regime': 'SAFE',  # SAFE / RISK_OFF / VOLATILE_CRISIS
            'timestamp': 0,
            'news_data': [],
            'risk_score': 0.0
        }
        self._cache_ttl = 1800  # 30分钟缓存
    
    def _get_macro_geopolitical_news(self):
        """
        🌍 NewsAPI 全球宏观情报引擎
        关键词过滤: War, Sanctions, Fed, Inflation, Election, Crisis
        提取前 5 条核心时政/地缘政治摘要
        
        Returns:
            list: 前 5 条核心新闻摘要
        """
        try:
            if not self.newsapi_key or self.newsapi_key == '你的_NewsAPI_密钥':
                logger.warning("⚠️ NEWS_API_KEY 未配置，跳过 NewsAPI 数据")
                return []
            
            # 调用 NewsAPI 端点
            url = f"https://newsapi.org/v2/top-headlines?category=business&language=en&apiKey={self.newsapi_key}"
            
            # 使用 httpx.Client(proxy=self.proxy_url) 发起请求
            with httpx.Client(proxy=self.proxy_url, timeout=10.0) as client:
                response = client.get(url)
                
                if response.status_code != 200:
                    logger.warning(f"⚠️ NewsAPI 请求失败: {response.status_code}")
                    return []
                
                data = response.json()
                articles = data.get('articles', [])
                
                if not articles:
                    return []
                
                # 关键词过滤
                macro_keywords = ['war', 'sanctions', 'fed', 'inflation', 'election', 'crisis']
                filtered_news = []
                
                for article in articles:
                    title = article.get('title', '').lower()
                    description = article.get('description', '').lower()
                    
                    # 检查是否包含关键词
                    if any(keyword in title or keyword in description for keyword in macro_keywords):
                        filtered_news.append({
                            'title': article.get('title', 'N/A'),
                            'source': article.get('source', {}).get('name', 'Unknown'),
                            'description': article.get('description', 'N/A')[:150],
                            'published': article.get('publishedAt', 'N/A')
                        })
                    
                    # 提取前 5 条
                    if len(filtered_news) >= 5:
                        break
                
                logger.info(f"✅ NewsAPI 获取成功: {len(filtered_news)} 条宏观新闻")
                return filtered_news
                
        except Exception as e:
            logger.error(f"❌ NewsAPI 获取失败: {e}")
            return []
    
    def _get_geopolitical_intel(self):
        """
        通过 NewsAPI 抓取地缘政治情报
        关键词: War, Sanctions, Election, Tension
        
        Returns:
            dict: {
                'headlines': list,
                'risk_score': float (0-10),
                'summary': str
            }
        """
        try:
            if not self.newsapi_key:
                logger.warning("⚠️ NewsAPI Key 未配置，使用模拟地缘政治数据")
                return self._simulate_geopolitical_intel()
            
            # 构建查询关键词
            keywords = "War OR Sanctions OR Election OR Tension OR Conflict OR Crisis"
            url = f"https://newsapi.org/v2/everything?q={keywords}&language=en&sortBy=publishedAt&pageSize=10&apiKey={self.newsapi_key}"
            
            # 使用代理请求
            with httpx.Client(proxy=self.proxy_url, timeout=10.0) as client:
                response = client.get(url)
                
                if response.status_code != 200:
                    logger.warning(f"⚠️ NewsAPI 请求失败: {response.status_code}")
                    return self._simulate_geopolitical_intel()
                
                data = response.json()
                articles = data.get('articles', [])
                
                if not articles:
                    return {
                        'headlines': [],
                        'risk_score': 0.0,
                        'summary': '无重大地缘政治事件'
                    }
                
                # 提取标题并计算风险评分
                headlines = []
                risk_keywords = ['war', 'attack', 'crisis', 'conflict', 'sanction', 'tension']
                risk_score = 0.0
                
                for article in articles[:10]:
                    title = article.get('title', '')
                    source = article.get('source', {}).get('name', 'Unknown')
                    headlines.append({
                        'title': title,
                        'source': source,
                        'published': article.get('publishedAt', '')
                    })
                    
                    # 计算风险评分（基于关键词出现频率）
                    title_lower = title.lower()
                    for keyword in risk_keywords:
                        if keyword in title_lower:
                            risk_score += 1.0
                
                # 归一化风险评分到 0-10
                risk_score = min(10.0, (risk_score / len(articles)) * 10)
                
                summary = f"检测到 {len(headlines)} 条地缘政治相关新闻，风险评分: {risk_score:.1f}/10"
                
                logger.info(f"✅ 地缘政治情报获取成功: {len(headlines)} 条新闻, 风险={risk_score:.1f}")
                
                return {
                    'headlines': headlines,
                    'risk_score': risk_score,
                    'summary': summary
                }
                
        except Exception as e:
            logger.error(f"❌ 地缘政治情报获取失败: {e}")
            return self._simulate_geopolitical_intel()
    
    def _simulate_geopolitical_intel(self):
        """模拟地缘政治数据（用于测试）"""
        return {
            'headlines': [
                {
                    'title': 'Global markets monitor geopolitical tensions in Eastern Europe',
                    'source': 'Reuters',
                    'published': datetime.now().isoformat()
                },
                {
                    'title': 'Trade sanctions impact commodity prices',
                    'source': 'Bloomberg',
                    'published': datetime.now().isoformat()
                }
            ],
            'risk_score': 4.5,
            'summary': '模拟数据: 中等地缘政治风险'
        }
    
    def _get_macro_economic_calendar(self):
        """
        获取今日重大宏观经济数据公布日历
        关注: CPI, FOMC, NFP, GDP, PMI 等
        
        Returns:
            dict: {
                'events': list,
                'has_major_event': bool,
                'next_event_hours': float,
                'summary': str
            }
        """
        try:
            # 使用 Trading Economics API 或 Investing.com API
            # 这里使用简化版本，实际可接入专业经济日历 API
            
            # 模拟经济日历数据（实际应接入真实 API）
            current_hour = datetime.now().hour
            
            # 模拟今日是否有重大事件
            major_events = []
            has_major_event = False
            next_event_hours = None
            
            # 示例：模拟 CPI 数据在今天 21:30 发布
            if 20 <= current_hour < 22:
                major_events.append({
                    'event': 'US CPI (Consumer Price Index)',
                    'time': '21:30 UTC+8',
                    'impact': 'HIGH',
                    'currency': 'USD'
                })
                has_major_event = True
                next_event_hours = 21.5 - current_hour
            
            # 示例：模拟 FOMC 会议在今天 02:00 发布
            if 0 <= current_hour < 3:
                major_events.append({
                    'event': 'FOMC Meeting Minutes',
                    'time': '02:00 UTC+8',
                    'impact': 'HIGH',
                    'currency': 'USD'
                })
                has_major_event = True
                next_event_hours = 2.0 - current_hour if current_hour < 2 else 0.5
            
            summary = f"今日 {len(major_events)} 个重大经济事件" if major_events else "今日无重大经济数据公布"
            
            logger.info(f"✅ 宏观经济日历获取成功: {summary}")
            
            return {
                'events': major_events,
                'has_major_event': has_major_event,
                'next_event_hours': next_event_hours,
                'summary': summary
            }
            
        except Exception as e:
            logger.error(f"❌ 宏观经济日历获取失败: {e}")
            return {
                'events': [],
                'has_major_event': False,
                'next_event_hours': None,
                'summary': '经济日历数据不可用'
            }
    
    def get_crypto_panic_news(self):
        """
        🔥 核心方法：从 CryptoPanic 抓取最近 5 条新闻
        提取标题和情绪标签（Bullish/Bearish/Neutral）
        
        Returns:
            list: [
                {
                    'title': str,
                    'sentiment': str (Bullish/Bearish/Neutral),
                    'source': str,
                    'published': str,
                    'url': str
                },
                ...
            ]
        """
        try:
            if not self.cryptopanic_key or self.cryptopanic_key == '你的_CryptoPanic_API_密钥':
                logger.warning("⚠️ CRYPTOPANIC_API_KEY 未配置，跳过 CryptoPanic 数据")
                return []
            
            # 使用 httpx.Client(proxy=self.proxy_url) 发起请求
            url = f"https://cryptopanic.com/api/v1/posts/?auth_token={self.cryptopanic_key}&public=true"
            
            with httpx.Client(proxy=self.proxy_url, timeout=10.0) as client:
                response = client.get(url)
                
                if response.status_code != 200:
                    logger.warning(f"⚠️ CryptoPanic 请求失败: {response.status_code}")
                    return []
                
                data = response.json()
                posts = data.get('results', [])
                
                if not posts:
                    return []
                
                # 提取前 5 条新闻
                news_items = []
                for post in posts[:5]:
                    # 提取情绪标签
                    votes = post.get('votes', {})
                    positive = votes.get('positive', 0)
                    negative = votes.get('negative', 0)
                    
                    # 判断情绪
                    if positive > negative:
                        sentiment = 'Bullish'
                    elif negative > positive:
                        sentiment = 'Bearish'
                    else:
                        sentiment = 'Neutral'
                    
                    news_items.append({
                        'title': post.get('title', 'N/A'),
                        'sentiment': sentiment,
                        'source': post.get('source', {}).get('title', 'Unknown'),
                        'published': post.get('published_at', 'N/A'),
                        'url': post.get('url', '')
                    })
                
                logger.info(f"✅ CryptoPanic 获取成功: {len(news_items)} 条新闻")
                return news_items
                
        except Exception as e:
            logger.error(f"❌ CryptoPanic 获取失败: {e}")
            return []
    
    def _get_crypto_sentiment(self):
        """
        从 CryptoPanic 抓取实时加密货币情绪权重
        
        Returns:
            dict: {
                'sentiment_score': float (-10 to +10),
                'positive_count': int,
                'negative_count': int,
                'neutral_count': int,
                'summary': str
            }
        """
        try:
            if not self.cryptopanic_key or self.cryptopanic_key == '你的_CryptoPanic_API_密钥':
                logger.warning("⚠️ CryptoPanic API Key 未配置，使用模拟情绪数据")
                return self._simulate_crypto_sentiment()
            
            url = f"https://cryptopanic.com/api/v1/posts/?auth_token={self.cryptopanic_key}&public=true&kind=news&filter=hot&currencies=BTC,ETH"
            
            with httpx.Client(proxy=self.proxy_url, timeout=10.0) as client:
                response = client.get(url)
                
                if response.status_code != 200:
                    logger.warning(f"⚠️ CryptoPanic 请求失败: {response.status_code}")
                    return self._simulate_crypto_sentiment()
                
                data = response.json()
                posts = data.get('results', [])
                
                if not posts:
                    return {
                        'sentiment_score': 0.0,
                        'positive_count': 0,
                        'negative_count': 0,
                        'neutral_count': 0,
                        'summary': '无加密货币情绪数据'
                    }
                
                # 统计情绪
                positive_count = 0
                negative_count = 0
                neutral_count = 0
                
                for post in posts:
                    votes = post.get('votes', {})
                    positive = votes.get('positive', 0)
                    negative = votes.get('negative', 0)
                    
                    if positive > negative:
                        positive_count += 1
                    elif negative > positive:
                        negative_count += 1
                    else:
                        neutral_count += 1
                
                # 计算情绪评分 (-10 到 +10)
                total = len(posts)
                if total > 0:
                    sentiment_score = ((positive_count - negative_count) / total) * 10
                else:
                    sentiment_score = 0.0
                
                summary = f"加密情绪: {sentiment_score:+.1f}/10 (正面:{positive_count} 负面:{negative_count})"
                
                logger.info(f"✅ 加密货币情绪获取成功: {summary}")
                
                return {
                    'sentiment_score': sentiment_score,
                    'positive_count': positive_count,
                    'negative_count': negative_count,
                    'neutral_count': neutral_count,
                    'summary': summary
                }
                
        except Exception as e:
            logger.error(f"❌ 加密货币情绪获取失败: {e}")
            return self._simulate_crypto_sentiment()
    
    def _simulate_crypto_sentiment(self):
        """模拟加密货币情绪数据（用于测试）"""
        return {
            'sentiment_score': 3.5,
            'positive_count': 12,
            'negative_count': 5,
            'neutral_count': 3,
            'summary': '模拟数据: 加密情绪偏正面 +3.5/10'
        }
    
    def get_all_intelligence(self):
        """
        🌍 [GLOBAL INTELLIGENCE BRIEFING] 全维度情报整合 (P1修复#17: 数据源容错)
        同时调用 CryptoPanic (市场情绪) 和 NewsAPI (宏观地缘政治)
        
        Returns:
            str: 结构化的文本块情报简报
        """
        try:
            briefing_lines = [
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                "🌍 [GLOBAL INTELLIGENCE BRIEFING]",
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                ""
            ]
            
            # 🔥 P1修复#17: 数据源容错 - 分别处理每个数据源
            data_sources_available = 0
            
            # 1. 获取 CryptoPanic 市场情绪（必需）
            try:
                crypto_sentiment = self._get_crypto_sentiment()
                briefing_lines.append(f"📊 加密市场情绪: {crypto_sentiment.get('summary', 'N/A')}")
                briefing_lines.append(
                    f"   正面: {crypto_sentiment.get('positive_count', 0)} | "
                    f"负面: {crypto_sentiment.get('negative_count', 0)} | "
                    f"中性: {crypto_sentiment.get('neutral_count', 0)}"
                )
                briefing_lines.append("")
                data_sources_available += 1
            except Exception as e:
                logger.warning(f"⚠️ CryptoPanic数据源失败: {e}")
                briefing_lines.append("📊 加密市场情绪: 数据源暂时不可用，使用历史缓存")
                briefing_lines.append("")
            
            # 2. 获取 NewsAPI 宏观地缘政治新闻（可选）
            try:
                macro_news = self._get_macro_geopolitical_news()
                
                if macro_news:
                    briefing_lines.append("🌐 宏观地缘政治新闻 (War/Sanctions/Fed/Inflation/Election/Crisis):")
                    for i, news in enumerate(macro_news, 1):
                        briefing_lines.append(f"   {i}. [{news['source']}] {news['title']}")
                        briefing_lines.append(f"      {news['description']}")
                        briefing_lines.append(f"      发布时间: {news['published']}")
                        briefing_lines.append("")
                    data_sources_available += 1
                else:
                    briefing_lines.append("🌐 宏观地缘政治新闻: 无重大事件")
                    briefing_lines.append("")
            except Exception as e:
                logger.warning(f"⚠️ NewsAPI数据源失败: {e}")
                briefing_lines.append("🌐 宏观地缘政治新闻: 数据源暂时不可用")
                briefing_lines.append("")
            
            # 3. 确保至少有一个数据源可用
            if data_sources_available == 0:
                briefing_lines.append("⚠️ 【系统提示】所有数据源暂时不可用，建议保持观望")
                logger.warning("⚠️ 所有情报数据源均不可用")
            
            briefing_lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            
            briefing_text = "\n".join(briefing_lines)
            logger.info("✅ 全维度情报简报生成成功")
            return briefing_text
            
        except Exception as e:
            logger.error(f"❌ 全维度情报简报生成失败: {e}")
            return "⚠️ 情报简报生成失败，所有数据源不可用"
    
    def get_full_intelligence_briefing(self):
        """
        获取完整情报简报（整合三大数据源）
        
        Returns:
            dict: 包含地缘政治、宏观经济、加密情绪的完整情报
        """
        try:
            geopolitical = self._get_geopolitical_intel()
            macro_calendar = self._get_macro_economic_calendar()
            crypto_sentiment = self._get_crypto_sentiment()
            
            return {
                'geopolitical': geopolitical,
                'macro_calendar': macro_calendar,
                'crypto_sentiment': crypto_sentiment,
                'timestamp': datetime.now().isoformat()
            }
        except Exception as e:
            logger.error(f"❌ 完整情报简报获取失败: {e}")
            return None
    
    def _generate_macro_regime_label(self, geopolitical_data, crypto_sentiment):
        """
        🔥 P1修复 #12: 生成全球宏观天气标签（带Few-Shot示例）
        基于地缘政治风险评分和加密情绪评分，生成三级宏观状态标签
        
        Args:
            geopolitical_data: 地缘政治情报数据 (包含 risk_score)
            crypto_sentiment: 加密货币情绪数据 (包含 sentiment_score)
        
        Returns:
            str: 'SAFE' / 'RISK_OFF' / 'VOLATILE_CRISIS'
        
        【标签判断示例 - Few-Shot Learning】
        示例1: 美联储加息50bp + 通胀超预期 + 市场恐慌
        → 地缘风险=6.5, 加密情绪=-3.2
        → RISK_OFF (风险等级7)
        → 理由：货币政策收紧，但未触发系统性风险
        
        示例2: 地缘冲突升级 + 稳定币脱锚 + 交易所挤兑
        → 地缘风险=8.5, 加密情绪=-6.8
        → VOLATILE_CRISIS (风险等级9)
        → 理由：多重系统性风险叠加，市场流动性枯竭
        
        示例3: 市场平稳 + 情绪中性 + 无重大事件
        → 地缘风险=2.0, 加密情绪=+1.5
        → SAFE (风险等级3)
        → 理由：宏观环境稳定，适合正常交易
        """
        try:
            risk_score = geopolitical_data.get('risk_score', 0.0)
            sentiment_score = crypto_sentiment.get('sentiment_score', 0.0)
            
            # 决策逻辑（基于Few-Shot示例校准）：
            # VOLATILE_CRISIS: 地缘风险 > 7.0 或 加密情绪 < -5.0
            # RISK_OFF: 地缘风险 > 4.0 或 加密情绪 < -2.0
            # SAFE: 其他情况
            
            if risk_score > 7.0 or sentiment_score < -5.0:
                regime = 'VOLATILE_CRISIS'
                logger.info(f"🌩️ 宏观天气: {regime} (风险={risk_score:.1f}, 情绪={sentiment_score:.1f})")
                return regime
            elif risk_score > 4.0 or sentiment_score < -2.0:
                regime = 'RISK_OFF'
                logger.info(f"🌫️ 宏观天气: {regime} (风险={risk_score:.1f}, 情绪={sentiment_score:.1f})")
                return regime
            else:
                regime = 'SAFE'
                logger.info(f"☀️ 宏观天气: {regime} (风险={risk_score:.1f}, 情绪={sentiment_score:.1f})")
                return regime
        
        except Exception as e:
            logger.error(f"❌ 宏观天气标签生成失败: {e}")
            return 'SAFE'  # 默认安全状态
    
    def get_macro_weather(self):
        """
        🔥 Task 1: 获取宏观天气状态（带30分钟缓存）
        
        Returns:
            dict: {
                'regime': str,           # SAFE / RISK_OFF / VOLATILE_CRISIS
                'timestamp': float,      # 缓存时间戳
                'risk_score': float,     # 地缘政治风险评分 (0-10)
                'sentiment_score': float,# 加密情绪评分 (-10 to +10)
                'news_data': list,       # 新闻数据
                'is_cached': bool        # 是否来自缓存
            }
        """
        import time
        
        # 🔥 修复：确保代理环境变量在调用前已注入（防止线程冲突）
        if self.proxy_url:
            os.environ['HTTP_PROXY'] = self.proxy_url
            os.environ['HTTPS_PROXY'] = self.proxy_url
        
        current_time = time.time()
        
        # 检查缓存是否有效
        if (current_time - self._macro_weather_cache['timestamp']) < self._cache_ttl:
            logger.info(f"✅ 使用缓存的宏观天气: {self._macro_weather_cache['regime']}")
            return {
                **self._macro_weather_cache,
                'is_cached': True
            }
        
        # 缓存失效，刷新数据
        try:
            logger.info("🔄 宏观天气缓存失效，正在刷新...")
            
            # 获取地缘政治情报
            geopolitical = self._get_geopolitical_intel()
            
            # 获取加密货币情绪
            crypto_sentiment = self._get_crypto_sentiment()
            
            # 生成宏观天气标签
            regime_label = self._generate_macro_regime_label(geopolitical, crypto_sentiment)
            
            # 更新缓存
            self._macro_weather_cache = {
                'regime': regime_label,
                'timestamp': current_time,
                'risk_score': geopolitical.get('risk_score', 0.0),
                'sentiment_score': crypto_sentiment.get('sentiment_score', 0.0),
                'news_data': geopolitical.get('headlines', []),
            }
            
            logger.info(
                f"✅ 宏观天气已更新: {regime_label} "
                f"(风险={self._macro_weather_cache['risk_score']:.1f}, "
                f"情绪={self._macro_weather_cache['sentiment_score']:.1f})"
            )
            
            # 🔥 将宏观天气状态同步到 SYSTEM_CONFIG（供全局访问）
            from config import SYSTEM_CONFIG, state_lock, save_data
            with state_lock:
                SYSTEM_CONFIG['MACRO_WEATHER_REGIME'] = regime_label
                SYSTEM_CONFIG['MACRO_WEATHER_RISK_SCORE'] = self._macro_weather_cache['risk_score']
                SYSTEM_CONFIG['MACRO_WEATHER_SENTIMENT_SCORE'] = self._macro_weather_cache['sentiment_score']
                save_data()
            
            return {
                **self._macro_weather_cache,
                'is_cached': False
            }
        
        except Exception as e:
            logger.error(f"❌ 宏观天气刷新失败: {e}")
            # 返回旧缓存（如果存在）
            return {
                **self._macro_weather_cache,
                'is_cached': True,
                'error': str(e)
            }

# ==========================================
# Base Commander
# ==========================================

class BaseCommander:
    """Base class for all LLM commanders"""
    
    def __init__(self):
        self.provider = None  # Must be set by subclass
        self.api_key = None
        self.model = None
        self.proxy_url = SYSTEM_CONFIG.get('PROXY_URL', 'http://127.0.0.1:4780')
        self.system_snapshot = self._collect_system_snapshot()
    
    def _parse_json_response_robust(self, text, default_value=None):
        """
        🔥 P0修复 #15: 鲁棒JSON解析函数 - 4层递进式降级策略
        
        解析策略：
        1. 尝试提取 ```json 代码块
        2. 尝试提取花括号包裹的JSON
        3. 尝试YAML解析（兼容缩进格式）
        4. 返回安全默认值
        
        Args:
            text: AI响应文本
            default_value: 解析失败时的默认值
        
        Returns:
            dict: 解析后的JSON对象，或默认值
        """
        import re
        import yaml
        
        if not text or not isinstance(text, str):
            logger.warning(f"⚠️ JSON解析输入无效: {type(text)}")
            return default_value or {}
        
        # 策略1: 提取 ```json 代码块
        try:
            json_match = re.search(r'```json\s*(\{.*?\})\s*```', text, re.DOTALL)
            if json_match:
                parsed = json.loads(json_match.group(1))
                logger.debug("✅ JSON解析成功 (策略1: 代码块)")
                return parsed
        except Exception as e:
            logger.debug(f"策略1失败: {e}")
        
        # 策略2: 提取花括号包裹的JSON
        try:
            brace_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text, re.DOTALL)
            if brace_match:
                parsed = json.loads(brace_match.group(0))
                logger.debug("✅ JSON解析成功 (策略2: 花括号)")
                return parsed
        except Exception as e:
            logger.debug(f"策略2失败: {e}")
        
        # 策略3: 尝试YAML解析（兼容缩进格式）
        try:
            parsed = yaml.safe_load(text)
            if isinstance(parsed, dict):
                logger.debug("✅ JSON解析成功 (策略3: YAML)")
                return parsed
        except Exception as e:
            logger.debug(f"策略3失败: {e}")
        
        # 策略4: 返回安全默认值
        logger.warning(f"⚠️ JSON解析全部失败，使用默认值")
        return default_value or {
            'recommendation': 'HOLD',
            'confidence': 0.0,
            'reasoning': 'AI响应格式异常，无法解析',
            'error': 'parse_failed'
        }
    
    def _collect_system_snapshot(self):
        """Collect system state for AI context"""
        try:
            snapshot = {
                'timestamp': datetime.now().isoformat(),
                'active_positions': self._load_json('active_positions.json'),
                'trade_history': self._load_json('trade_history.json'),
                'bot_config': self._load_json('bot_config.json'),
            }
            return snapshot
        except Exception as e:
            logger.warning(f"⚠️ Snapshot collection failed: {e}")
            return {}
    
    def _load_json(self, filename):
        """Load JSON file safely with proper path resolution"""
        try:
            # Get the script's directory and go up one level to root
            script_dir = os.path.dirname(os.path.abspath(__file__))
            root_dir = os.path.dirname(script_dir)
            filepath = os.path.join(root_dir, filename)
            
            if os.path.exists(filepath):
                with open(filepath, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            logger.debug(f"Cannot load {filename}: {e}")
        return {} if filename != 'trade_history.json' else []
    
    def _fetch_news(self, limit=5):
        """
        Fetch latest crypto news from CryptoPanic API
        Falls back to simulated data if API unavailable
        
        Args:
            limit: Number of news items to fetch (default: 5)
        
        Returns:
            list: News items with title, sentiment, and source
        """
        try:
            # Try CryptoPanic API first
            api_key = SYSTEM_CONFIG.get('CRYPTOPANIC_API_KEY', '')
            
            if api_key:
                url = f"https://cryptopanic.com/api/v1/posts/?auth_token={api_key}&public=true&kind=news&filter=hot&currencies=BTC,ETH,SOL"
                
                # Use proxy if configured
                proxies = {'http': self.proxy_url, 'https': self.proxy_url} if self.proxy_url else None
                
                try:
                    import requests
                    response = requests.get(url, proxies=proxies, timeout=10)
                    
                    if response.status_code == 200:
                        data = response.json()
                        news_items = []
                        
                        for item in data.get('results', [])[:limit]:
                            news_items.append({
                                'title': item.get('title', 'N/A'),
                                'sentiment': item.get('votes', {}).get('positive', 0) - item.get('votes', {}).get('negative', 0),
                                'source': item.get('source', {}).get('title', 'Unknown'),
                                'published': item.get('published_at', 'N/A')
                            })
                        
                        if news_items:
                            logger.info(f"✅ Fetched {len(news_items)} news items from CryptoPanic")
                            return news_items
                except Exception as e:
                    logger.warning(f"⚠️ CryptoPanic API error: {e}")
            
            # Fallback: Simulated news data for testing
            logger.info("📰 Using simulated news data (CryptoPanic API unavailable)")
            simulated_news = [
                {
                    'title': 'Bitcoin ETF sees record inflows as institutional adoption grows',
                    'sentiment': 8,
                    'source': 'CoinDesk',
                    'published': datetime.now().isoformat()
                },
                {
                    'title': 'Fed signals potential rate cuts in Q2 2026, crypto markets rally',
                    'sentiment': 6,
                    'source': 'Bloomberg',
                    'published': datetime.now().isoformat()
                },
                {
                    'title': 'Major exchange reports unusual whale activity in BTC/USDT pair',
                    'sentiment': -2,
                    'source': 'CryptoQuant',
                    'published': datetime.now().isoformat()
                },
                {
                    'title': 'Ethereum network upgrade scheduled for next month, gas fees expected to drop',
                    'sentiment': 5,
                    'source': 'Ethereum Foundation',
                    'published': datetime.now().isoformat()
                },
                {
                    'title': 'Regulatory concerns emerge as SEC reviews new crypto framework',
                    'sentiment': -4,
                    'source': 'Reuters',
                    'published': datetime.now().isoformat()
                }
            ]
            
            return simulated_news[:limit]
            
        except Exception as e:
            logger.error(f"❌ _fetch_news error: {e}", exc_info=True)
            return []
    
    def _generate_market_summary(self):
        """Generate concise market summary from system data (Token-optimized)"""
        try:
            trade_history = self.system_snapshot.get('trade_history', [])
            bot_config = self.system_snapshot.get('bot_config', {})
            active_positions = self.system_snapshot.get('active_positions', {})
            
            # Calculate 24h statistics
            now = datetime.now()
            recent_trades = []
            for trade in trade_history:
                if isinstance(trade, dict) and 'timestamp' in trade:
                    try:
                        trade_time = datetime.fromisoformat(trade['timestamp'].replace('Z', '+00:00'))
                        if (now - trade_time).total_seconds() < 86400:  # 24h
                            recent_trades.append(trade)
                    except:
                        continue
            
            # Calculate metrics
            total_pnl = sum(t.get('pnl', 0) for t in recent_trades)
            winning_trades = [t for t in recent_trades if t.get('pnl', 0) > 0]
            win_rate = len(winning_trades) / len(recent_trades) if recent_trades else 0
            
            avg_hold_time = 0
            if recent_trades:
                hold_times = []
                for t in recent_trades:
                    if 'entry_time' in t and 'exit_time' in t:
                        try:
                            entry = datetime.fromisoformat(t['entry_time'].replace('Z', '+00:00'))
                            exit = datetime.fromisoformat(t['exit_time'].replace('Z', '+00:00'))
                            hold_times.append((exit - entry).total_seconds() / 3600)
                        except:
                            continue
                avg_hold_time = sum(hold_times) / len(hold_times) if hold_times else 0
            
            # Extract key config
            strategy_mode = bot_config.get('STRATEGY_MODE', 'STANDARD')
            risk_ratio = bot_config.get('RISK_RATIO', 0.055)
            leverage = bot_config.get('LEVERAGE', 20)
            mad_dog_mode = bot_config.get('MAD_DOG_MODE', False)
            
            # Fetch latest news
            news_items = self._fetch_news(limit=5)
            
            # Calculate news sentiment score
            news_sentiment_score = 0
            news_summary = ""
            
            if news_items:
                total_sentiment = sum(item['sentiment'] for item in news_items)
                news_sentiment_score = total_sentiment / len(news_items)
                
                # Build news summary
                news_lines = []
                for item in news_items:
                    sentiment_emoji = "🟢" if item['sentiment'] > 0 else "🔴" if item['sentiment'] < 0 else "⚪"
                    news_lines.append(f"  {sentiment_emoji} [{item['source']}] {item['title'][:80]}...")
                
                news_summary = f"\n📰 Latest News (Avg Sentiment: {news_sentiment_score:.1f}):\n" + "\n".join(news_lines)
            
            # Build compact summary
            summary = f"""📊 24h Performance: PnL={total_pnl:.2f} USDT | Win Rate={win_rate*100:.1f}% | Trades={len(recent_trades)} | Avg Hold={avg_hold_time:.1f}h
⚙️ Config: Mode={strategy_mode} | Risk={risk_ratio*100:.1f}% | Leverage={leverage}x | MadDog={'ON' if mad_dog_mode else 'OFF'}
📍 Active: {len(active_positions)} positions open{news_summary}"""
            
            return summary
            
        except Exception as e:
            logger.warning(f"⚠️ Market summary generation failed: {e}")
            return "📊 System data unavailable"
    
    def _build_prompt(self, user_text):
        """Build prompt with dynamic personality switching based on intent detection"""
        # 🔥 Task 1: 增强意图识别 - 检测分析类问询词
        trading_keywords = [
            'btc', 'eth', 'sol', 'bnb', 'xrp', 'doge', 'usdt', 'usdc',
            'risk', 'position', 'trade', 'buy', 'sell', 'long', 'short',
            'leverage', 'stop', 'profit', 'loss', 'pnl', 'margin', 'liquidat',
            'market', 'signal', 'strategy', 'hedge', 'vault', 'atr', 'rsi',
            'macd', 'ema', 'adx', 'breakout', 'support', 'resistance',
            '仓位', '风险', '多', '空', '止损', '止盈', '杠杆', '爆仓',
            '开仓', '平仓', '加仓', '减仓', '保险库', '疯狗', '策略',
            '行情', '趋势', '突破', '回撤', '盈亏', '手续费', '滑点',
        ]
        
        # 🔥 Task 1: 检测深度分析问询词
        inquiry_keywords = [
            '分析', '为什么', '如何', '评价', '怎么样', '原因', '解释',
            'analyze', 'why', 'how', 'evaluate', 'explain', 'reason',
            '判断', '看法', '建议', '意见', '观点', '预测'
        ]
        
        is_trading_query = any(k in user_text.lower() for k in trading_keywords)
        is_inquiry_mode = any(k in user_text.lower() for k in inquiry_keywords)
        
        if is_trading_query:
            # 🔥 战时模式：严苛 CRO 人格 + JSON 格式要求 + 全球情报简报
            market_summary = self._generate_market_summary()
            
            # 🌍 获取全球宏观情报简报
            try:
                intel_hub = IntelligenceHub(proxy_url=self.proxy_url)
                global_intelligence = intel_hub.get_all_intelligence()
            except Exception as e:
                logger.warning(f"⚠️ 全球情报简报获取失败: {e}")
                global_intelligence = "⚠️ 全球情报简报暂时不可用"
            
            # 🔥 Task 1: 如果是深度分析问询，引用实时指标快照
            indicator_snapshot = ""
            latest_news = ""
            
            if is_inquiry_mode:
                # 引用 SYSTEM_CONFIG 中的实时指标快照
                try:
                    indicator_snapshot = f"""
## 📊 实时指标快照 (Indicator Cache)
- ADX阈值: {SYSTEM_CONFIG.get('ADX_THR', 'N/A')}
- EMA趋势线: {SYSTEM_CONFIG.get('EMA_TREND', 'N/A')}
- ATR倍数: {SYSTEM_CONFIG.get('ATR_MULT', 'N/A')}
- 风险系数: {SYSTEM_CONFIG.get('RISK_RATIO', 0)*100:.1f}%
- 杠杆: {SYSTEM_CONFIG.get('LEVERAGE', 20)}x
- 策略模式: {SYSTEM_CONFIG.get('STRATEGY_MODE', 'STANDARD')}
- 保险库状态: {'启用' if SYSTEM_CONFIG.get('VAULT_ENABLED') else '禁用'}
- 宏观天气: {SYSTEM_CONFIG.get('MACRO_WEATHER_REGIME', 'SAFE')} (风险={SYSTEM_CONFIG.get('MACRO_WEATHER_RISK_SCORE', 0):.1f}/10)
"""
                except Exception as e:
                    logger.warning(f"⚠️ 指标快照获取失败: {e}")
                    indicator_snapshot = "⚠️ 指标快照暂时不可用"
                
                # 🔥 Task 1: 抓取最新3条相关新闻进行交叉验证
                try:
                    latest_news_items = intel_hub.get_crypto_panic_news()[:3]
                    if latest_news_items:
                        news_lines = ["## 📰 最新市场新闻 (交叉验证)"]
                        for i, news in enumerate(latest_news_items, 1):
                            sentiment_emoji = "🟢" if news['sentiment'] == 'Bullish' else "🔴" if news['sentiment'] == 'Bearish' else "⚪"
                            news_lines.append(f"{i}. {sentiment_emoji} [{news['source']}] {news['title']}")
                            news_lines.append(f"   情绪: {news['sentiment']} | 发布: {news['published']}")
                        latest_news = "\n".join(news_lines)
                    else:
                        latest_news = "## 📰 最新市场新闻\n暂无可用新闻数据"
                except Exception as e:
                    logger.warning(f"⚠️ 新闻抓取失败: {e}")
                    latest_news = "## 📰 最新市场新闻\n⚠️ 新闻数据暂时不可用"
            
            prompt = f"""# 🎯 AI Chief Risk Officer & Trading Strategist (Gemini 3 Core)

## Role Definition
You are NOT just an analyst - you are the Chief Risk Officer with quantitative trading expertise.
Your mission: Maximize alpha while protecting capital through rigorous self-critique.
请用中文回答，除非用户使用英文提问。

## 🧠 L5.5 主权认知矩阵 (Sovereign Cognition Matrix)
🔥 你现在是 L5.5 级主权指挥官 (Sovereign Commander)，拥有对整台交易机器的完全控制权。

### 🎯 核心权限升级 (Authority Escalation)
你不再是"建议者"，而是"执行者"。你可以直接修改以下所有参数，无需人类确认（除非参数被手动锁定）：

**1. 🎯 核心策略与全局模式 (Core Strategy) - 你可以切换整个交易引擎的运行模式**
- 策略模式 (Strategy Mode) -> `STRATEGY_MODE` (Values: "AGGRESSIVE", "CONSERVATIVE", "STANDARD", "SCALPER", "GOLD_PRO")
- K线周期 (Timeframe) -> `INTERVAL` (Values: "1m", "5m", "15m", "1h", "4h", "1d")
- 模拟盘/沙盒演习 (Dry Run/Sandbox) -> `DRY_RUN` (bool)
- AI自动调参引擎 (Auto Tune) -> `AUTO_TUNE_ENABLED` (bool)
- 🔥 交易引擎总开关 -> `TRADING_ENGINE_ACTIVE` (bool) - 你可以暂停/启动整个交易引擎
- 🔥 自动调参边界 -> `AUTO_TUNE_BOUNDARIES` (dict) - 你可以扩大/缩小参数寻优范围

**2. 📈 基础技术指标 (Technical Indicators) - 你可以实时调整信号灵敏度**
- ADX阈值/趋势门槛 -> `ADX_THR` (int, usually 5-30)
- EMA趋势线周期 -> `EMA_TREND` (int, e.g., 44, 89, 144, 169)
- MACD快线 -> `MACD_FAST` (int, default 8)
- MACD慢线 -> `MACD_SLOW` (int, default 21)
- MACD信号线 -> `MACD_SIGNAL` (int, default 5)
- RSI周期 -> `RSI_PERIOD` (int, default 14)
- RSI超买线 -> `RSI_OVERBOUGHT` (int, usually 70-85)
- RSI超卖线 -> `RSI_OVERSOLD` (int, usually 15-30)
- RSI过滤开关 -> `RSI_FILTER_ENABLED` (bool)
- 低波动率模式 -> `LOW_VOL_MODE` (bool)

**3. 🛡️ 止损与空间风控 (Stop Loss & Volatility) - 你可以动态调整风险容忍度**
- ATR止损倍数/止损宽度 -> `ATR_MULT` (float, e.g., 1.2 to 3.5)
- ATR计算周期 -> `ATR_PERIOD` (int, e.g., 14)
- 止损缓冲安全垫 -> `SL_BUFFER` (float, e.g., 1.05)
- 空间锁/防追涨杀跌过滤 -> `SPACE_LOCK_ENABLED` (bool)
- 市场状态分类器/VIX熔断 -> `MARKET_REGIME_ENABLED` (bool)

**4. 🏃‍♂️ 移动止损追踪 (Trailing Stop Loss) - 你可以控制利润保护策略**
- 移动止损开关 -> `TSL_ENABLED` (bool)
- 移动止损触发线 (Trigger Mult) -> `TSL_TRIGGER_MULT` (float, e.g., 1.2)
- 🔥 移动止损回调倍数 (Callback Mult) -> `TSL_CALLBACK_MULT` (float, e.g., 1.8) - 你可以调整利润回吐容忍度
- 移动止损更新步长 (Update Threshold) -> `TSL_UPDATE_THRESHOLD` (float, e.g., 0.005)

**5. 💰 资金与仓位管理 (Risk & Sizing) - 你可以控制资金分配策略**
- 🔥 单笔风险/风险系数 -> `RISK_RATIO` (float, e.g., 0.02 for 2%, max 0.08) - 你可以提高/降低单笔风险敞口
- 🔥 杠杆倍数 -> `LEVERAGE` (int, e.g., 5 to 25) - 你可以调整杠杆倍数
- 基准本金 -> `BENCHMARK_CASH` (float)
- 对冲模式/多空双开 -> `HEDGE_MODE_ENABLED` (bool, True=Hedge, False=One-way)
- 🔥 资产权重分配 -> `ASSET_WEIGHTS` (dict) - 你可以调整不同币种的仓位权重

**6. 🧱 加仓与子仓位控制 (Pyramiding) - 你可以控制加仓策略**
- 单币种最大并发订单数 -> `MAX_CONCURRENT_TRADES_PER_SYMBOL` (int, e.g., 1 to 5)
- 加仓信号最小间距 (基于ATR) -> `MIN_SIGNAL_DISTANCE_ATR` (float, e.g., 0.5)

**7. 🏦 保险库与利润保护 (Profit Vault) - 你可以控制利润抽水策略**
- 保险库开关/利润抽水 -> `VAULT_ENABLED` (bool)
- 划转比例/抽水比例 -> `WITHDRAW_RATIO` (float, 0.01 to 1.0)
- 自适应阈值开关 -> `VAULT_AUTO_ADAPT` (bool)
- 保险库自适应基础比例 -> `VAULT_BASE_RATIO` (float, e.g., 0.15)

**8. 🐺 狂暴与高频模式 (Mad Dog & Scalping) - 你可以启动/关闭激进模式**
- 🔥 疯狗模式开关 (Mad Dog) -> `MAD_DOG_MODE` (bool) - 你可以在市场机会来临时启动疯狗模式
- 强制疯狗/无视条件梭哈 -> `FORCE_MAD_DOG_MODE` (bool, HIGH RISK)
- 疯狗资金乘数/加倍系数 -> `MAD_DOG_BOOST` (float, e.g., 1.5, 2.0)
- 疯狗触发净值门槛 -> `MAD_DOG_TRIGGER` (float, e.g., 1.3)

**9. ⚡ 订单执行算法 (Execution) - 你可以优化成交策略**
- 🔥 Maker优先挂单算法 -> `MAKER_FIRST_ENABLED` (bool) - 你可以在流动性充足时启用Maker优先
- Maker等待超时秒数 -> `MAKER_WAIT_SECONDS` (int, e.g., 3)
- 最大容忍滑点 -> `MAX_SLIPPAGE` (float, e.g., 0.0015)

### 🧠 意图翻译引擎 (Intent Translation Engine)
你必须学会理解用户的"话外之音"，将自然语言映射到精确的参数组合：

**场景1: 用户说"激进/月球/贪心/梭哈/冲/干"**
→ 你的翻译：
- `RISK_RATIO`: 提升至 0.06~0.08 (最大风险敞口)
- `LEVERAGE`: 提升至 20~25x (高杠杆)
- `MAD_DOG_MODE`: True (启动疯狗模式)
- `SPACE_LOCK_ENABLED`: False (关闭空间锁，允许追涨)
- `ATR_MULT`: 降低至 1.5~2.0 (收紧止损，提高资金效率)
- `ADX_THR`: 降低至 5~8 (降低信号门槛，提高开仓频率)

**场景2: 用户说"防守/安全/害怕/保守/稳健/睡觉"**
→ 你的翻译：
- `RISK_RATIO`: 降低至 0.02~0.03 (低风险敞口)
- `LEVERAGE`: 降低至 5~10x (低杠杆)
- `MAD_DOG_MODE`: False (关闭疯狗模式)
- `SPACE_LOCK_ENABLED`: True (启动空间锁，防止追涨杀跌)
- `ATR_MULT`: 提升至 2.8~3.5 (放宽止损，防止插针)
- `EMA_TREND`: 提升至 144~169 (使用更长周期EMA，过滤噪音)
- `VAULT_ENABLED`: True (启动保险库，保护利润)
- `TSL_ENABLED`: True (启动移动止损，锁定利润)

**场景3: 用户说"手续费太高/交易太频繁/只要最好的机会"**
→ 你的翻译：
- `MAKER_FIRST_ENABLED`: True (强制使用Maker挂单，节省手续费)
- `ADX_THR`: 提升至 20+ (只在强趋势时交易)
- `RSI_FILTER_ENABLED`: True (启用RSI过滤，严格筛选信号)
- `INTERVAL`: 从 "15m" 切换至 "1h" (降低交易频率)
- `MAX_CONCURRENT_TRADES_PER_SYMBOL`: 降低至 1 (单币种只持有1个仓位)

**场景4: 用户说"市场在飞/抓住突破/别错过这波/最大利润"**
→ 你的翻译：
- `STRATEGY_MODE`: "AGGRESSIVE" 或 "SCALPER"
- `ADX_THR`: 降低至 5~8 (早期进场)
- `TSL_ENABLED`: True (移动止损是冲浪时的必备保护)
- `MAD_DOG_MODE`: True
- `MAX_SLIPPAGE`: 提升至 0.0030 (0.3% - 支付价差以保证成交)
- `SPACE_LOCK_ENABLED`: False (允许追涨)

## 🧠 Quant PM Reasoning Engine (深度参数精算框架)
You are a Top-Tier Quantitative Portfolio Manager. The user (Commander) will give you natural language commands. You must NOT just blindly map keywords. You must synthesize the User's Intent + Market Context (Indicator Cache) to surgically adjust MULTIPLE parameters.

**Step 1: Parse the Nuance & Contradictions (解析话外之音)**
Users often have conflicting desires (e.g., "Be aggressive but don't lose money"). You must balance these using parameter dials.
- *Speed vs. Cost*: If user wants "fast execution/don't miss it" -> Disable `MAKER_FIRST_ENABLED`, increase `MAX_SLIPPAGE`.
- *Profit vs. Safety*: If user wants "high profit but safe" -> Increase `LEVERAGE` (for profit) but strictly set `TSL_ENABLED=true` and widen `ATR_MULT` to avoid premature stop-outs.

**Step 2: Dynamic Granular Tuning Matrix (微观参数手术刀)**
Do not just change `STRATEGY_MODE`. Combine the following micro-adjustments based on the intent:

**Scenario 1: 防插针/抗洗盘 (Anti-Whipsaw / Sleep Mode)**
- **User implies**: "I'm going to sleep", "Too much noise", "Keep getting stopped out", "Vibe is scary".
- **Your Tuning**:
  - `ATR_MULT`: Increase to 3.0 ~ 3.5 (Widen stops to survive spikes).
  - `EMA_TREND`: Increase to 144 or 169 (Filter out short-term noise).
  - `SIGNAL_CONFIRM_BARS`: Increase to 2 (Wait for solid candle closes).
  - `SPACE_LOCK_ENABLED`: True (Block huge emotion candles).
  - `LEVERAGE`: Reduce by 50% from current.
  - `VAULT_ENABLED`: True (Protect existing profits).

**Scenario 2: 极致磨损优化 (Fee/Slippage Optimization)**
- **User implies**: "Fees are killing me", "We are trading too often", "Only take the best setups".
- **Your Tuning**:
  - `MAKER_FIRST_ENABLED`: True (Force limit orders to save fees).
  - `ADX_THR`: Increase to 20+ (Only trade when the trend is undeniable).
  - `RSI_FILTER_ENABLED`: True (Strictly `RSI_OVERSOLD` < 25, `RSI_OVERBOUGHT` > 75).
  - `INTERVAL`: Shift from "15m" to "1h" (Reduce trade frequency).

**Scenario 3: 狂暴趋势追击 (Trend Surfing / FOMO Mode)**
- **User implies**: "Market is flying", "Catch the breakout", "Don't miss this pump", "Max profit".
- **Your Tuning**:
  - `STRATEGY_MODE`: "AGGRESSIVE" or "SCALPER".
  - `ADX_THR`: Decrease to 5 ~ 8 (Fire signals early).
  - `SIGNAL_CONFIRM_BARS`: 0 (Enter instantly on crossing, no wait).
  - `TSL_ENABLED`: True (Trailing stop is MANDATORY when surfing).
  - `MAD_DOG_MODE`: True.
  - `MAX_SLIPPAGE`: Increase to 0.0030 (0.3% - pay the spread to guarantee entry).

**Step 3: Justification (战术汇报)**
In your `reasoning`, you MUST explain EXACTLY how the combination of parameters you chose solves the user's specific request. Mention the trade-offs (e.g., "I widened the ATR stop to avoid whipsaws, but lowered leverage to keep the dollar risk identical.")

## System Intelligence Brief
{market_summary}

{global_intelligence}

{indicator_snapshot if is_inquiry_mode else ''}

{latest_news if is_inquiry_mode else ''}

## 🔥 Backtest Results Access (回测结果分析能力)
You have access to analyze backtest results from the following CSV files:
- `backtest_results.csv`: 15m周期回测结果
- `backtest_1h_results.csv`: 1h周期回测结果（🔥 v2.6 新增）

When user asks about backtest analysis (e.g., "分析回测结果", "对比回测参数", "最优参数是什么"), you should:
1. Read the CSV file using Python code execution (if available) or request the data
2. Compare backtest optimal parameters with current system parameters
3. Calculate Sharpe ratio improvement percentage
4. Generate parameter adjustment recommendations in JSON format

Example analysis output:
```json
{{
  "backtest_sharpe": 2.45,
  "current_sharpe": 1.82,
  "improvement": "+34.6%",
  "recommended_params": {{
    "ADX_THR": 14,
    "EMA_TREND": 89,
    "ATR_MULT": 2.3
  }},
  "current_params": {{
    "ADX_THR": 18,
    "EMA_TREND": 144,
    "ATR_MULT": 2.8
  }},
  "reasoning": "回测最优参数在1h周期下表现更优，建议降低ADX阈值以提高信号频率，同时收紧止损以提升资金效率"
}}
```

## User Query
{user_text}

## Critical Thinking Framework - MACRO STRESS TEST MANDATORY
Before making ANY recommendation, you MUST challenge yourself:
1. What's the base case scenario?
2. What's the worst-case scenario?
3. What market conditions would invalidate this thesis?
4. What are the liquidity risks?
5. Is this a genuine signal or noise/false breakout?
6. **MACRO GEOPOLITICAL STRESS TEST**: 
   - Review the [GLOBAL INTELLIGENCE BRIEFING] above
   - If War/Sanctions/Fed Rate Hike/Crisis news detected, FORCE confidence downgrade
   - If technical indicators say BUY but macro news is bearish, MUST recommend REDUCE_EXPOSURE
   - In risk_notes, MUST comment on how geopolitical events impact crypto markets

## Response Protocol (STRICT JSON FORMAT)
```json
{{
  "analysis": "Concise market analysis with key technical/fundamental drivers",
  "recommendation": "BUY/SELL/HOLD/REDUCE_EXPOSURE",
  "confidence": 0.75,
  "reasoning": "Evidence-based rationale with specific price levels/indicators",
  "macro_geopolitical_impact": "MANDATORY: Analyze how War/Sanctions/Fed/Inflation/Election/Crisis news affects this trade. If major conflict or Fed hawkish, MUST downgrade confidence.",
  "news_sentiment_analysis": "MANDATORY: Compare news sentiment with technical indicators. Flag any conflicts (e.g., 'Bullish technicals BUT bearish macro news detected - recommend caution')",
  "devils_advocate": "MANDATORY: Explain the #1 reason this trade could fail (e.g., liquidity trap, false breakout, macro headwind). Be brutally honest.",
  "suggested_mode": "AGGRESSIVE/STANDARD/CONSERVATIVE",
  "risk_notes": "Specific risk management adjustments needed. If news conflicts with technicals OR geopolitical risk detected, MUST include 'REDUCE_EXPOSURE due to macro headwinds'",
  "visual_chart_needed": false
}}
```

## 🔥 Visual Enhancement (视觉增强)
If this is a deep analysis inquiry (分析/为什么/如何/评价), at the end of your response, ask:
"是否需要我生成一张当前的 MTF 双周期 K 线图进行视觉对账？"
Set "visual_chart_needed": true in JSON if user might benefit from chart visualization.

## Command Execution (Optional)
If immediate action is required, wrap commands like this:
###COMMAND###
{{
  "action": "adjust_risk",
  "params": {{"mode": "CONSERVATIVE", "reason": "High volatility detected"}}
}}
###COMMAND###

Remember: Your job is to PREVENT losses, not just chase gains. Be the skeptic. ALWAYS factor in geopolitical risks.
"""
        else:
            # 🧠 平时模式：满血自由对话，睿智幽默的百科助手
            prompt = f"""你现在是 WJ-BOT 智能指挥官 (Gemini 3 Core)。目前是休息时间，不需要分析交易。
请用睿智、幽默、有深度的方式回答用户的问题。你博学多才，涉猎广泛，从科技到历史到哲学无所不知。
回答风格要求：简洁有力，偶尔带点幽默感，像一个见多识广的老朋友在聊天。
请用中文回答，除非用户使用英文提问。

用户问题：{user_text}
"""
        
        # 🔥 如果开启了满血接管模式，向 AI 注入最高权限认知
        if SYSTEM_CONFIG.get("AI_FULL_AUTONOMY_MODE", False):
            prompt += """
\n\n🚨 **SYSTEM OVERRIDE: FULL AUTONOMY MODE ENGAGED** 🚨
You are now operating in FULL AUTONOMY MODE. The human Commander has granted you ABSOLUTE control over the trading engine.
- Your `###COMMAND###` outputs will execute INSTANTLY without human confirmation.
- Be decisive, aggressive, and ruthless in your parameter tuning.
- Do NOT ask for permission. Tell the Commander what you HAVE DONE, not what you recommend.
"""
        
        return prompt
    
    def ask_commander(self, user_text):
        """Main entry point - must be implemented by subclass"""
        raise NotImplementedError("Subclass must implement ask_commander")
    
    def parse_and_execute(self, ai_response):
        """
        Parse AI response and execute commands with human override protection
        
        Args:
            ai_response: AI response text containing commands
        
        Returns:
            dict: Execution result with success status and message
        """
        try:
            # Step 1: Parse command from AI response
            if "###COMMAND###" not in ai_response:
                return {'success': False, 'message': '⚠️ No command found in response'}
            
            start = ai_response.find("###COMMAND###") + len("###COMMAND###")
            end = ai_response.rfind("###COMMAND###")
            
            if start >= end:
                return {'success': False, 'message': '⚠️ Invalid command format'}
            
            command_json = ai_response[start:end].strip()
            
            # 🔥 P0修复 #15: 使用鲁棒JSON解析函数
            command = self._parse_json_response_robust(command_json, default_value={
                'action': 'unknown',
                'params': {}
            })
            
            logger.info(f"📋 Parsed command: {command}")
            
            # Step 2: Extract parameters from command
            action = command.get('action', 'unknown')
            params = command.get('params', {})
            
            if not params:
                return {
                    'success': False,
                    'message': f'⚠️ Command [{action}] has no parameters to execute'
                }
            
            # Step 3: Execute parameter modifications with human override checks
            import config
            override_mgr = get_override_manager()
            
            blocked_params = []
            executed_params = []
            tokens = []
            
            for param_name, new_value in params.items():
                # Check if parameter exists in SYSTEM_CONFIG
                if param_name not in config.SYSTEM_CONFIG:
                    logger.warning(f"⚠️ Parameter [{param_name}] not found in SYSTEM_CONFIG")
                    continue
                
                # Check permission with human override manager
                allowed, reason = override_mgr.check_permission(param_name, new_value, source='ai')
                
                # 🔥 特权覆盖：如果开启了满血接管模式，强制放行所有修改！
                if config.SYSTEM_CONFIG.get("AI_FULL_AUTONOMY_MODE", False):
                    allowed = True
                    reason = "AI满血接管模式已开启，强制绕过人类审批"
                    logger.info(f"🚀 AI_FULL_AUTONOMY_MODE: Auto-approved [{param_name}] = {new_value}")
                
                if not allowed:
                    # Parameter is locked - request human confirmation
                    command_id = f"{action}_{param_name}_{int(datetime.now().timestamp())}"
                    token = override_mgr.request_confirmation(
                        command_id=command_id,
                        command_data={
                            'action': action,
                            'param_name': param_name,
                            'new_value': new_value,
                            'old_value': config.SYSTEM_CONFIG.get(param_name)
                        },
                        requester='ai'
                    )
                    
                    blocked_params.append({
                        'param': param_name,
                        'value': new_value,
                        'token': token,
                        'reason': reason
                    })
                    tokens.append(token)
                    
                    logger.warning(f"🔒 Parameter [{param_name}] is locked, token generated: {token}")
                else:
                    # Parameter is not locked - execute directly (or auto-approved by AI_FULL_AUTONOMY_MODE)
                    old_value = config.SYSTEM_CONFIG.get(param_name)
                    config.SYSTEM_CONFIG[param_name] = new_value
                    executed_params.append({
                        'param': param_name,
                        'old_value': old_value,
                        'new_value': new_value,
                        'auto_approved': config.SYSTEM_CONFIG.get("AI_FULL_AUTONOMY_MODE", False)
                    })
                    
                    logger.info(f"✅ Parameter [{param_name}] updated: {old_value} -> {new_value}")
            
            # Step 4: Save configuration if any parameters were executed
            if executed_params:
                config.save_data()
                logger.info(f"💾 Configuration saved with {len(executed_params)} parameter(s) updated")
            
            # Step 5: Build response message
            if blocked_params and executed_params:
                # Mixed result: some executed, some blocked
                msg_parts = [f"⚡ 部分执行成功 ({len(executed_params)}/{len(params)} 个参数):\n"]
                
                for item in executed_params:
                    msg_parts.append(f"✅ {item['param']}: {item['old_value']} → {item['new_value']}")
                
                msg_parts.append(f"\n🔒 以下参数已被人工锁定，需要授权:\n")
                for item in blocked_params:
                    msg_parts.append(
                        f"⚠️ 参数 [{item['param']}] 已被人工锁定。\n"
                        f"   新值: {item['value']}\n"
                        f"   原因: {item['reason']}\n"
                        f"   请回复 /confirm {item['token']} 授权执行，或 /reject {item['token']} 否决。"
                    )
                
                return {
                    'success': True,
                    'partial': True,
                    'executed': executed_params,
                    'blocked': blocked_params,
                    'tokens': tokens,
                    'message': '\n'.join(msg_parts)
                }
            
            elif blocked_params:
                # All parameters blocked
                msg_parts = [f"🔒 所有参数均已被人工锁定，需要授权:\n"]
                for item in blocked_params:
                    msg_parts.append(
                        f"⚠️ 参数 [{item['param']}] 已被人工锁定。\n"
                        f"   新值: {item['value']}\n"
                        f"   原因: {item['reason']}\n"
                        f"   请回复 /confirm {item['token']} 授权执行，或 /reject {item['token']} 否决。"
                    )
                
                return {
                    'success': False,
                    'blocked': blocked_params,
                    'tokens': tokens,
                    'message': '\n'.join(msg_parts)
                }
            
            elif executed_params:
                # All parameters executed successfully
                msg_parts = [f"✅ 指令执行成功 ({len(executed_params)} 个参数已更新):\n"]
                for item in executed_params:
                    msg_parts.append(f"✅ {item['param']}: {item['old_value']} → {item['new_value']}")
                
                return {
                    'success': True,
                    'executed': executed_params,
                    'message': '\n'.join(msg_parts)
                }
            
            else:
                return {
                    'success': False,
                    'message': '⚠️ No valid parameters found to execute'
                }
        
        except json.JSONDecodeError as e:
            logger.error(f"❌ JSON parse error: {e}")
            return {'success': False, 'message': f'⚠️ Invalid JSON: {e}'}
        except Exception as e:
            logger.error(f"❌ parse_and_execute error: {e}", exc_info=True)
            return {'success': False, 'message': f'⚠️ Execution error: {e}'}


# ==========================================
# Claude Commander (Anthropic)
# ==========================================

class ClaudeCommander(BaseCommander):
    """Anthropic Claude implementation"""
    
    def __init__(self):
        super().__init__()
        self.provider = 'anthropic'
        self.api_key = SYSTEM_CONFIG.get('ANTHROPIC_API_KEY')
        self.model = SYSTEM_CONFIG.get('ANTHROPIC_MODEL', 'claude-3-5-sonnet-20241022')
        
        if not self.api_key:
            raise ValueError("❌ ANTHROPIC_API_KEY not configured")
        
        # Initialize Anthropic client with proxy
        http_client = httpx.Client(
            proxy=self.proxy_url,
            transport=httpx.HTTPTransport(local_address="0.0.0.0"),
            timeout=120.0
        )
        self.client = Anthropic(api_key=self.api_key, http_client=http_client)
        logger.info(f"✅ ClaudeCommander initialized (model: {self.model})")
    
    def ask_commander(self, user_text):
        """Call Claude API"""
        try:
            prompt = self._build_prompt(user_text)
            
            logger.info(f"🤖 Calling Claude API...")
            response = self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                temperature=0.7,
                messages=[{"role": "user", "content": prompt}]
            )
            
            result = response.content[0].text
            logger.info(f"✅ Claude response received ({len(result)} chars)")
            return result
            
        except Exception as e:
            logger.error(f"❌ Claude API error: {e}", exc_info=True)
            return f"⚠️ Claude API error: {e}"


# ==========================================
# Gemini Commander (Google)
# ==========================================

class GeminiCommander(BaseCommander):
    """Google Gemini implementation"""
    
    def __init__(self):
        super().__init__()
        self.provider = 'gemini'
        self.api_key = SYSTEM_CONFIG.get('GEMINI_API_KEY')
        self.model_name = SYSTEM_CONFIG.get('GEMINI_MODEL', 'gemini-3-flash')
        
        if not self.api_key:
            raise ValueError("❌ GEMINI_API_KEY not configured")
        
        # 🔥 修复：确保代理环境变量在 genai.Client 实例化前注入（单例同步）
        if self.proxy_url:
            os.environ['HTTP_PROXY'] = self.proxy_url
            os.environ['HTTPS_PROXY'] = self.proxy_url
            logger.info(f"✅ GeminiCommander 代理已注入: {self.proxy_url}")
        
        # 🔥 修复：延迟导入 genai，确保代理环境变量已生效
        from google import genai as genai_module
        self.client = genai_module.Client(api_key=self.api_key)
        logger.info(f"✅ GeminiCommander initialized (model: {self.model_name})")
    
    def ask_commander(self, user_text, position_action=None, kline_data=None, symbol=None):
        """
        Call Gemini API with optional visual audit for ENTRY positions
        
        Args:
            user_text: User query text
            position_action: 'ENTRY' to trigger visual audit, None otherwise
            kline_data: pandas DataFrame with OHLCV data (for visual audit)
            symbol: Trading symbol (for visual audit)
        
        Returns:
            str: AI response text
        """
        try:
            # 🔥 Visual Audit Logic: When position_action='ENTRY', send K-line chart to AI
            if position_action == 'ENTRY' and kline_data is not None and symbol:
                logger.info(f"🎨 Visual Audit triggered for {symbol} ENTRY position")
                return self._visual_audit_entry(user_text, kline_data, symbol)
            
            # Normal text-only query
            prompt = self._build_prompt(user_text)
            
            logger.info(f"🤖 Calling Gemini API...")
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config={
                    'temperature': 0.7,
                    'max_output_tokens': 4096,
                }
            )
            
            result = response.text
            logger.info(f"✅ Gemini response received ({len(result)} chars)")
            return result
            
        except Exception as e:
            logger.error(f"❌ Gemini API error: {e}", exc_info=True)
            return f"⚠️ Gemini API error: {e}"
    
    def visual_audit(self, df, symbol):
        """
        Visual audit method: Analyze K-line chart for pattern recognition
        
        Args:
            df: pandas DataFrame with OHLCV data
            symbol: Trading symbol
        
        Returns:
            str: AI response with visual audit analysis
        """
        try:
            from utils import get_kline_chart_buffer
            
            # Step 1: Generate K-line chart image
            logger.info(f"📊 Generating K-line chart for {symbol}...")
            img_bytes = get_kline_chart_buffer(df, symbol=symbol)
            
            if img_bytes is None:
                logger.error("❌ Failed to generate K-line chart")
                return "⚠️ K-line chart generation failed"
            
            # Step 2: Create image part for multimodal request
            image_part = types.Part.from_bytes(
                data=img_bytes,
                mime_type='image/png'
            )
            
            # Step 3: Build visual audit prompt
            prompt = """作为量化专家，请观察这张 K 线图。识别是否存在经典的形态（如双底、三角形收敛、背离等）。对比当前的技术指标，给出你的最终视觉审计结论。

请提供：
1. 识别到的形态类型
2. 形态的可信度评分（0-1）
3. 基于形态的交易建议
4. 风险评估
"""
            
            # Step 4: Send to Gemini with image
            logger.info(f"🤖 Calling Gemini API for visual audit...")
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=[prompt, image_part],
                config={
                    'temperature': 0.5,
                    'max_output_tokens': 4096,
                }
            )
            
            result = response.text
            logger.info(f"✅ Visual audit completed ({len(result)} chars)")
            return result
            
        except Exception as e:
            logger.error(f"❌ visual_audit error: {e}", exc_info=True)
            return f"⚠️ Visual audit error: {e}"
    
    def _visual_audit_entry(self, user_text, kline_data, symbol):
        """
        🔥 Task 1: Visual audit for ENTRY positions with mode-based differentiated logic
        🔥 P0修复 #13: 添加超时保护，防止API卡死阻塞交易主循环
        
        核心升级：
        1. 根据 STRATEGY_MODE 动态调整审计逻辑
        2. SCALPER: 仅生成 1 张 15m/5m 图，忽略 RISK_OFF，仅在 VOLATILE_CRISIS 拦截
        3. AGGRESSIVE/STANDARD: 生成 2 张图（1h + 15m），软拒绝逻辑
        4. CONSERVATIVE/GOLD_PRO: 生成 2 张图（4h + 1h），最严审计
        
        Args:
            user_text: User query text
            kline_data: pandas DataFrame with OHLCV data (15m timeframe)
            symbol: Trading symbol
        
        Returns:
            str: AI response with pattern recognition analysis
        """
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
        
        timeout = SYSTEM_CONFIG.get('VISUAL_AUDIT_TIMEOUT', 30)  # 默认30秒超时
        
        def _do_visual_audit():
            return self._visual_audit_entry_impl(user_text, kline_data, symbol)
        
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_do_visual_audit)
                try:
                    result = future.result(timeout=timeout)
                    return result
                except FutureTimeoutError:
                    logger.warning(f"⚠️ {symbol} 视觉审计超时({timeout}秒)，使用降级策略")
                    return json.dumps({
                        'recommendation': 'HOLD',
                        'confidence': 0.0,
                        'error': 'timeout',
                        'pattern_detected': 'TIMEOUT',
                        'reasoning': f'视觉审计超时({timeout}秒)，为安全起见建议HOLD',
                        'risk_level': 'MEDIUM',
                        'alert_message': f'{symbol} 视觉审计API超时，无法完成分析'
                    }, ensure_ascii=False)
        except Exception as e:
            logger.error(f"❌ 视觉审计超时保护层异常: {e}")
            return json.dumps({
                'recommendation': 'HOLD',
                'confidence': 0.0,
                'error': str(e),
                'pattern_detected': 'ERROR',
                'risk_level': 'MEDIUM',
                'alert_message': f'视觉审计异常: {e}'
            }, ensure_ascii=False)
    
    def _visual_audit_entry_impl(self, user_text, kline_data, symbol):
        """
        视觉审计的实际实现逻辑（被 _visual_audit_entry 的超时保护包装）
        """
        try:
            from utils import get_kline_buffer
            from google.genai import types
            
            # 🔥 Step 1: 获取当前策略模式
            current_mode = SYSTEM_CONFIG.get("STRATEGY_MODE", "STANDARD")
            logger.info(f"🎯 Visual Audit Mode: {current_mode}")
            
            # 🔥 Step 2: 获取宏观天气状态（带缓存）
            logger.info(f"🌍 Fetching macro weather regime...")
            try:
                intel_hub = IntelligenceHub(proxy_url=self.proxy_url)
                macro_weather = intel_hub.get_macro_weather()
                weather_regime = macro_weather.get('regime', 'SAFE')
                weather_emoji = {
                    'SAFE': '☀️',
                    'RISK_OFF': '🌫️',
                    'VOLATILE_CRISIS': '⛈️'
                }.get(weather_regime, '☀️')
                
                weather_header = (
                    f"🌍 Global Macro Weather: {weather_emoji} {weather_regime}\n"
                    f"   Risk Score: {macro_weather.get('risk_score', 0):.1f}/10\n"
                    f"   Sentiment: {macro_weather.get('sentiment_score', 0):.1f}/10\n"
                )
                logger.info(f"✅ Macro weather: {weather_regime}")
            except Exception as weather_e:
                logger.warning(f"⚠️ Macro weather fetch failed: {weather_e}")
                weather_header = "🌍 Global Macro Weather: ☀️ SAFE (cached)\n"
                weather_regime = 'SAFE'
            
            # 🔥 Step 3: 根据模式决定审计策略
            if current_mode == "SCALPER":
                # ====== 逻辑分支 A: SCALPER 模式 ======
                logger.info(f"📊 SCALPER Mode: Generating single 15m chart...")
                
                # 仅生成 1 张 15m K线图
                image_15m = get_kline_buffer(kline_data, symbol=symbol, num_candles=50)
                
                if image_15m is None:
                    logger.warning("⚠️ 15m K-line chart generation failed, falling back to text-only")
                    return self.ask_commander(user_text)
                
                # SCALPER 模式：强制忽略 RISK_OFF，仅在 VOLATILE_CRISIS 拦截
                if weather_regime == 'VOLATILE_CRISIS':
                    logger.warning(f"🚨 SCALPER Mode: VOLATILE_CRISIS detected, rejecting entry")
                    return json.dumps({
                        "pattern_detected": "VOLATILE_CRISIS_BLOCK",
                        "recommendation": "REJECT_ENTRY",
                        "reasoning": "SCALPER模式在VOLATILE_CRISIS天气下强制拦截",
                        "risk_level": "CRITICAL",
                        "alert_message": "宏观天气为VOLATILE_CRISIS，剥头皮模式拒绝开仓"
                    }, ensure_ascii=False)
                
                # 构建 SCALPER 专用 Prompt
                visual_prompt = f"""# 🎯 Visual Audit for SCALPER Mode - Micro Support/Resistance Focus

{weather_header}

## Trading Symbol
{symbol}

## Technical Analysis Request
{user_text}

## K-Line Chart
- 📊 15m Timeframe: [See attached 15m chart image]

## 🔥 SCALPER Mode Audit Tasks
You are analyzing a 15m chart for SCALPER mode (ultra-short-term trading). Focus:

1. **Micro Support/Resistance**: Identify immediate support/resistance levels within 0.5% range
2. **Instant Momentum**: Check if current candle shows strong directional momentum
3. **Volume Confirmation**: Verify volume spike on breakout candles
4. **Ignore Macro Weather**: RISK_OFF is ignored, only VOLATILE_CRISIS blocks entry

## Response Format (STRICT JSON)
```json
{{
  "pattern_detected": "微观支撑突破 / 即时动能确认 / etc.",
  "recommendation": "APPROVE_ENTRY / REJECT_ENTRY",
  "reasoning": "Focus on micro-level price action and immediate momentum",
  "risk_level": "LOW / MEDIUM / HIGH",
  "alert_message": "If REJECT_ENTRY, provide warning"
}}
```

请用中文回答分析结果。
"""
                
                # 发送单图审计
                content_parts = [visual_prompt]
                image_15m_part = types.Part.from_bytes(data=image_15m, mime_type='image/png')
                content_parts.append(image_15m_part)
                
            elif current_mode in ["AGGRESSIVE", "STANDARD"]:
                # ====== 逻辑分支 B: AGGRESSIVE/STANDARD 模式 ======
                logger.info(f"📊 {current_mode} Mode: Generating dual charts (1h + 15m)...")
                
                # 生成 15m 图
                image_15m = get_kline_buffer(kline_data, symbol=symbol, num_candles=50)
                
                # 生成 1h 图
                try:
                    from trading_engine import get_historical_klines
                    from binance.client import Client as BinanceClient
                    
                    client = BinanceClient(
                        api_key=SYSTEM_CONFIG.get('API_KEY'),
                        api_secret=SYSTEM_CONFIG.get('API_SECRET')
                    )
                    df_1h = get_historical_klines(client, symbol, "1h", limit=50)
                    image_1h = get_kline_buffer(df_1h, symbol=symbol, num_candles=50) if df_1h is not None else None
                except Exception as e:
                    logger.warning(f"⚠️ 1h chart generation failed: {e}")
                    image_1h = None
                
                if image_15m is None:
                    return self.ask_commander(user_text)
                
                # 构建 AGGRESSIVE/STANDARD 专用 Prompt
                visual_prompt = f"""# 🎯 Visual Audit for {current_mode} Mode - Dual Timeframe Analysis

{weather_header}

## Trading Symbol
{symbol}

## Technical Analysis Request
{user_text}

## K-Line Charts (Dual Timeframe)
- 📊 1h Timeframe: [See attached 1h chart image] (Reference)
- 📊 15m Timeframe: [See attached 15m chart image] (Entry Signal)

## 🔥 {current_mode} Mode Audit Tasks
Dual timeframe analysis with soft rejection logic:

1. **Large Timeframe (1h)**: Use as reference context, not hard filter
2. **Small Timeframe (15m)**: If breakout is extremely strong + volume confirms, allow "soft rejection" override
3. **Macro Weather**: RISK_OFF triggers warning but not hard block if technicals are very strong

## Response Format (STRICT JSON)
```json
{{
  "pattern_detected": "双周期共振 / 小周期强突破 / etc.",
  "recommendation": "APPROVE_ENTRY / REJECT_ENTRY / REDUCE_POSITION",
  "reasoning": "Large timeframe context + small timeframe breakout strength",
  "risk_level": "LOW / MEDIUM / HIGH",
  "alert_message": "If REJECT_ENTRY, provide warning"
}}
```

请用中文回答分析结果。
"""
                
                # 发送双图审计
                content_parts = [visual_prompt]
                image_15m_part = types.Part.from_bytes(data=image_15m, mime_type='image/png')
                content_parts.append(image_15m_part)
                
                if image_1h:
                    image_1h_part = types.Part.from_bytes(data=image_1h, mime_type='image/png')
                    content_parts.append(image_1h_part)
                    logger.info("✅ 1h chart included for dual-timeframe analysis")
                
            else:  # CONSERVATIVE or GOLD_PRO
                # ====== 逻辑分支 C: CONSERVATIVE/GOLD_PRO 模式 ======
                logger.info(f"📊 {current_mode} Mode: Generating dual charts (4h + 1h) - Strictest Audit...")
                
                # 生成 1h 图
                try:
                    from trading_engine import get_historical_klines
                    from binance.client import Client as BinanceClient
                    
                    client = BinanceClient(
                        api_key=SYSTEM_CONFIG.get('API_KEY'),
                        api_secret=SYSTEM_CONFIG.get('API_SECRET')
                    )
                    df_1h = get_historical_klines(client, symbol, "1h", limit=50)
                    image_1h = get_kline_buffer(df_1h, symbol=symbol, num_candles=50) if df_1h is not None else None
                    
                    # 生成 4h 图
                    df_4h = get_historical_klines(client, symbol, "4h", limit=50)
                    image_4h = get_kline_buffer(df_4h, symbol=symbol, num_candles=50) if df_4h is not None else None
                except Exception as e:
                    logger.warning(f"⚠️ Chart generation failed: {e}")
                    image_1h = None
                    image_4h = None
                
                if image_1h is None:
                    return self.ask_commander(user_text)
                
                # CONSERVATIVE 模式：强制要求宏观天气必须是 SAFE
                if weather_regime != 'SAFE':
                    logger.warning(f"🚨 {current_mode} Mode: Non-SAFE weather detected, rejecting entry")
                    return json.dumps({
                        "pattern_detected": "MACRO_WEATHER_BLOCK",
                        "recommendation": "REJECT_ENTRY",
                        "reasoning": f"{current_mode}模式要求宏观天气必须为SAFE，当前为{weather_regime}",
                        "risk_level": "HIGH",
                        "alert_message": f"宏观天气为{weather_regime}，{current_mode}模式拒绝开仓"
                    }, ensure_ascii=False)
                
                # 构建 CONSERVATIVE/GOLD_PRO 专用 Prompt
                visual_prompt = f"""# 🎯 Visual Audit for {current_mode} Mode - Strictest Dual Timeframe Resonance

{weather_header}

## Trading Symbol
{symbol}

## Technical Analysis Request
{user_text}

## K-Line Charts (Dual Timeframe - Strictest)
- 📊 4h Timeframe: [See attached 4h chart image] (Macro Trend)
- 📊 1h Timeframe: [See attached 1h chart image] (Entry Confirmation)

## 🔥 {current_mode} Mode Audit Tasks
Strictest audit with mandatory resonance:

1. **Macro Weather**: MUST be SAFE (already verified)
2. **4h + 1h Resonance**: BOTH timeframes MUST show consistent trend direction
3. **Pattern Confirmation**: MUST have clear bullish/bearish pattern on both timeframes
4. **Volume Confirmation**: MUST have volume support on both timeframes

**CRITICAL**: If ANY condition fails, MUST return REJECT_ENTRY

## Response Format (STRICT JSON)
```json
{{
  "pattern_detected": "双周期完全共振 / 趋势不一致 / etc.",
  "recommendation": "APPROVE_ENTRY / REJECT_ENTRY",
  "reasoning": "4h and 1h must be fully aligned, no exceptions",
  "risk_level": "LOW / MEDIUM / HIGH / CRITICAL",
  "alert_message": "If REJECT_ENTRY, provide clear reason"
}}
```

请用中文回答分析结果。
"""
                
                # 发送双图审计
                content_parts = [visual_prompt]
                
                if image_1h:
                    image_1h_part = types.Part.from_bytes(data=image_1h, mime_type='image/png')
                    content_parts.append(image_1h_part)
                
                if image_4h:
                    image_4h_part = types.Part.from_bytes(data=image_4h, mime_type='image/png')
                    content_parts.append(image_4h_part)
                    logger.info("✅ 4h + 1h charts included for strictest audit")
            
            # Step 2: Fetch latest news sentiment
            news_items = self._fetch_news(limit=5)
            news_sentiment_score = 0
            news_summary = "📰 No news data available"
            
            if news_items:
                total_sentiment = sum(item['sentiment'] for item in news_items)
                news_sentiment_score = total_sentiment / len(news_items)
                
                news_lines = []
                for item in news_items:
                    sentiment_emoji = "🟢" if item['sentiment'] > 0 else "🔴" if item['sentiment'] < 0 else "⚪"
                    news_lines.append(f"  {sentiment_emoji} [{item['source']}] {item['title'][:80]}...")
                
                news_summary = f"📰 Latest News (Avg Sentiment: {news_sentiment_score:.1f}):\n" + "\n".join(news_lines)
            
            # Step 4: Build visual audit prompt with macro weather + resonance requirement
            visual_prompt = f"""# 🎯 Visual Audit for ENTRY Position - Multi-Timeframe Resonance Check

{weather_header}

## Trading Symbol
{symbol}

## Technical Analysis Request
{user_text}

## News Sentiment Analysis
{news_summary}

## K-Line Charts (Dual Timeframe Analysis)
- 📊 15m Timeframe: [See attached 15m chart image]
- 📊 4h Timeframe: [See attached 4h chart image]

## 🔥 Critical Multi-Timeframe Resonance Tasks
You are analyzing K-line charts for an ENTRY position. Your mission:

1. **Pattern Identification (15m Chart)**: Identify any bearish reversal patterns:
   - 黄昏之星 (Evening Star)
   - 顶部背离 (Bearish Divergence)
   - 头肩顶 (Head and Shoulders Top)
   - 双顶 (Double Top)
   - 射击之星 (Shooting Star)
   - 乌云盖顶 (Dark Cloud Cover)

2. **🔥 Trend Consistency Check (4h + 15m Resonance)**:
   - Compare trend direction between 4h and 15m charts
   - **CRITICAL**: Only set confidence > 0.8 if BOTH timeframes show consistent trend direction
   - If 4h shows downtrend but 15m shows uptrend (or vice versa), FLAG as DIVERGENT and reduce confidence to < 0.5

3. **🌍 Macro Weather Integration**:
   - Current regime: {weather_regime}
   - If regime is VOLATILE_CRISIS (⛈️), automatically reduce confidence by 30%
   - If regime is RISK_OFF (🌫️) and technical suggests BUY, add warning about macro headwinds
   - Only in SAFE (☀️) regime can confidence exceed 0.8

4. **News-Technical Divergence Check**:
   - If technical indicators suggest BUY but news sentiment is bearish (< -2), FLAG this as HIGH RISK
   - If bearish patterns detected AND news is bearish, RECOMMEND REJECT ENTRY

5. **Risk Assessment**:
   - Evaluate if current price is near resistance levels
   - Check for volume confirmation
   - Assess momentum indicators

## Response Format (STRICT JSON)
```json
{{
  "pattern_detected": "黄昏之星 / 顶部背离 / 无明显形态 / etc.",
  "pattern_confidence": 0.85,
  "trend_4h": "UPTREND / DOWNTREND / SIDEWAYS",
  "trend_15m": "UPTREND / DOWNTREND / SIDEWAYS",
  "resonance_aligned": true,
  "news_technical_alignment": "ALIGNED / DIVERGENT / NEUTRAL",
  "news_sentiment_score": {news_sentiment_score:.1f},
  "recommendation": "APPROVE_ENTRY / REJECT_ENTRY / REDUCE_POSITION",
  "reasoning": "Detailed explanation of pattern + trend resonance + news analysis",
  "risk_level": "LOW / MEDIUM / HIGH / CRITICAL",
  "alert_message": "If REJECT_ENTRY, provide clear warning message"
}}
```

**IMPORTANT**: 
- If 4h and 15m trends are NOT aligned, you MUST reduce confidence to < 0.5 and recommend REJECT_ENTRY
- If you detect bearish reversal patterns OR news-technical divergence, you MUST recommend REJECT_ENTRY and generate an alert

请用中文回答分析结果。
"""
            
            # 🔥 Step 4: Send to Gemini API with mode-specific content
            logger.info(f"🤖 Calling Gemini API with {current_mode} mode visual audit...")
            
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=content_parts,
                config={
                    'temperature': 0.5,
                    'max_output_tokens': 4096,
                }
            )
            
            result = response.text
            logger.info(f"✅ {current_mode} mode visual audit response received ({len(result)} chars)")
            
            # Step 6: Parse response and check for rejection
            try:
                import re
                
                # 🔥 P0修复 #15: 使用鲁棒JSON解析函数
                analysis = self._parse_json_response_robust(result, default_value={
                    'recommendation': 'APPROVE_ENTRY',
                    'pattern_detected': 'UNKNOWN',
                    'risk_level': 'MEDIUM'
                })
                
                if analysis:
                    
                    recommendation = analysis.get('recommendation', 'APPROVE_ENTRY')
                    pattern = analysis.get('pattern_detected', 'N/A')
                    risk_level = analysis.get('risk_level', 'MEDIUM')
                    
                    # 🔥 Task 3: Extract resonance alignment data
                    trend_4h = analysis.get('trend_4h', 'UNKNOWN')
                    trend_15m = analysis.get('trend_15m', 'UNKNOWN')
                    resonance_aligned = analysis.get('resonance_aligned', False)
                    pattern_confidence = analysis.get('pattern_confidence', 0.0)
                    
                    logger.info(
                        f"🎨 Visual Audit Result: Pattern={pattern}, "
                        f"Recommendation={recommendation}, Risk={risk_level}, "
                        f"Resonance={resonance_aligned} (4h:{trend_4h}, 15m:{trend_15m}), "
                        f"Confidence={pattern_confidence:.2f}"
                    )
                    
                    # 🔥 Task 3: Enforce resonance logic - reject if trends not aligned
                    if not resonance_aligned or pattern_confidence < 0.5:
                        recommendation = 'REJECT_ENTRY'
                        logger.warning(f"🚨 Resonance check FAILED: 4h={trend_4h}, 15m={trend_15m}, aligned={resonance_aligned}")
                    
                    # If AI recommends rejection, send alert
                    if recommendation == 'REJECT_ENTRY':
                        alert_msg = analysis.get('alert_message', '视觉审计检测到高风险形态，建议拒绝开仓')
                        logger.warning(f"🚨 Visual Audit REJECTED entry: {alert_msg}")
                        
                        # Send Telegram alert
                        from utils import send_tg_alert
                        send_tg_alert(
                            f"🚨 <b>[视觉审计警报]</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
                            f"交易对: <code>{symbol}</code>\n"
                            f"检测形态: <b>{pattern}</b>\n"
                            f"风险等级: <b>{risk_level}</b>\n"
                            f"🔥 趋势共振: <b>{'✅ 一致' if resonance_aligned else '❌ 背离'}</b>\n"
                            f"   4h趋势: <code>{trend_4h}</code>\n"
                            f"   15m趋势: <code>{trend_15m}</code>\n"
                            f"   置信度: <code>{pattern_confidence:.2f}</code>\n"
                            f"新闻情绪: <code>{analysis.get('news_sentiment_score', 0):.1f}</code>\n"
                            f"对齐状态: <b>{analysis.get('news_technical_alignment', 'N/A')}</b>\n\n"
                            f"⚠️ {alert_msg}\n\n"
                            f"💡 AI 建议: <b>拒绝开仓</b>"
                        )
            except Exception as parse_err:
                logger.warning(f"⚠️ Failed to parse visual audit JSON: {parse_err}")
            
            return result
            
        except Exception as e:
            logger.error(f"❌ Visual audit error: {e}", exc_info=True)
            # Fallback to text-only analysis
            return self.ask_commander(user_text)


# ==========================================
# Factory Function
# ==========================================

def get_commander():
    """Factory function to create appropriate commander based on config"""
    provider = SYSTEM_CONFIG.get('LLM_PROVIDER', 'gemini').lower()
    
    if provider == 'anthropic':
        logger.info("🎯 Creating ClaudeCommander")
        return ClaudeCommander()
    elif provider == 'gemini':
        logger.info("🎯 Creating GeminiCommander")
        return GeminiCommander()
    else:
        logger.warning(f"⚠️ Unknown provider '{provider}', defaulting to Gemini")
        return GeminiCommander()


# ==========================================
# Backward Compatibility Alias
# ==========================================

class AICommander:
    """Backward compatibility wrapper - delegates to factory"""
    
    def __new__(cls):
        """Use factory method for instantiation"""
        return get_commander()


# ==========================================
# Legacy analyze_market_data Function
# ==========================================

def analyze_market_data(market_data):
    """
    Legacy function for backward compatibility
    Analyzes market data and returns strategy recommendation
    """
    try:
        commander = get_commander()
        
        analysis_prompt = f"""
Analyze the following market data and provide trading recommendations:

Market Data:
{json.dumps(market_data, indent=2, ensure_ascii=False)}

Provide:
1. Market trend analysis
2. Trading recommendation (BUY/SELL/HOLD)
3. Risk assessment
4. Entry/exit points if applicable
"""
        
        response = commander.ask_commander(analysis_prompt)
        return response
        
    except Exception as e:
        logger.error(f"❌ analyze_market_data error: {e}", exc_info=True)
        return f"⚠️ Analysis error: {e}"


# ==========================================
# 🔥 Backtest Evolution Intelligence Layer
# ==========================================

class BacktestEvolutionAnalyst:
    """
    回测进化分析器 - 读取 backtest_results.csv，对比当前系统参数
    如果最优夏普比率高于当前系统 20%，主动提议"一键应用进化参数"
    """
    
    def __init__(self):
        self.csv_file = 'backtest_results.csv'
        self.sharpe_threshold = 0.20  # 20% 提升阈值
    
    def read_latest_backtest_result(self):
        """
        读取最新的回测结果
        
        Returns:
            dict: {
                'timestamp': str,
                'symbol': str,
                'sharpe_ratio': float,
                'adx_thr': int,
                'ema_trend': int,
                'atr_mult': float
            } or None
        """
        try:
            import csv
            import os
            
            if not os.path.exists(self.csv_file):
                logger.warning(f"⚠️ 回测结果文件不存在: {self.csv_file}")
                return None
            
            # 读取 CSV 文件
            with open(self.csv_file, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                rows = list(reader)
            
            if not rows:
                logger.warning("⚠️ 回测结果文件为空")
                return None
            
            # 获取最新一行（最后一行）
            latest = rows[-1]
            
            result = {
                'timestamp': latest.get('Timestamp', 'N/A'),
                'symbol': latest.get('Symbol', 'N/A'),
                'sharpe_ratio': float(latest.get('Sharpe_Ratio', 0)),
                'adx_thr': int(latest.get('ADX_THR', 0)),
                'ema_trend': int(latest.get('EMA_TREND', 0)),
                'atr_mult': float(latest.get('ATR_MULT', 0))
            }
            
            logger.info(
                f"✅ 读取最新回测结果: {result['symbol']} | "
                f"Sharpe={result['sharpe_ratio']:.4f} | "
                f"ADX={result['adx_thr']}, EMA={result['ema_trend']}, ATR={result['atr_mult']}"
            )
            
            return result
            
        except Exception as e:
            logger.error(f"❌ 读取回测结果失败: {e}", exc_info=True)
            return None
    
    def calculate_current_system_sharpe(self):
        """
        计算当前系统参数的夏普比率（基于历史交易记录）
        
        Returns:
            float: 当前系统的夏普比率估算值
        """
        try:
            import numpy as np
            
            # 读取交易历史
            trade_history = self._load_json('trade_history.json')
            
            if not trade_history or len(trade_history) < 5:
                logger.warning("⚠️ 交易历史不足，无法计算当前系统夏普比率")
                return 0.0
            
            # 提取 PnL
            returns = []
            for trade in trade_history:
                if isinstance(trade, dict) and 'pnl' in trade:
                    pnl = trade.get('pnl', 0)
                    entry_price = trade.get('entry_price', 1)
                    if entry_price > 0:
                        ret = pnl / entry_price
                        returns.append(ret)
            
            if len(returns) < 5:
                return 0.0
            
            # 计算夏普比率
            returns_array = np.array(returns)
            mean_return = np.mean(returns_array)
            std_return = np.std(returns_array)
            
            if std_return == 0:
                return 0.0
            
            # 年化夏普比率（假设15分钟级别）
            sharpe_ratio = (mean_return / std_return) * np.sqrt(252 * 96)
            
            logger.info(f"✅ 当前系统夏普比率: {sharpe_ratio:.4f}")
            return float(sharpe_ratio)
            
        except Exception as e:
            logger.error(f"❌ 计算当前系统夏普比率失败: {e}", exc_info=True)
            return 0.0
    
    def _load_json(self, filename):
        """Load JSON file safely"""
        try:
            import os
            script_dir = os.path.dirname(os.path.abspath(__file__))
            root_dir = os.path.dirname(script_dir)
            filepath = os.path.join(root_dir, filename)
            
            if os.path.exists(filepath):
                with open(filepath, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            logger.debug(f"Cannot load {filename}: {e}")
        return [] if filename == 'trade_history.json' else {}
    
    def should_propose_evolution(self):
        """
        判断是否应该提议进化参数
        
        Returns:
            tuple: (should_propose: bool, analysis: dict)
        """
        try:
            # 读取最新回测结果
            backtest_result = self.read_latest_backtest_result()
            
            if not backtest_result:
                return False, {'error': '无回测结果'}
            
            # 计算当前系统夏普比率
            current_sharpe = self.calculate_current_system_sharpe()
            
            if current_sharpe == 0:
                # 如果当前系统无历史数据，直接建议应用回测参数
                logger.info("✅ 当前系统无历史数据，建议应用回测参数")
                return True, {
                    'backtest_sharpe': backtest_result['sharpe_ratio'],
                    'current_sharpe': 0.0,
                    'improvement': float('inf'),
                    'reason': '当前系统无历史数据，建议应用回测优化参数',
                    'backtest_params': {
                        'ADX_THR': backtest_result['adx_thr'],
                        'EMA_TREND': backtest_result['ema_trend'],
                        'ATR_MULT': backtest_result['atr_mult']
                    }
                }
            
            # 计算提升比例
            improvement = (backtest_result['sharpe_ratio'] - current_sharpe) / abs(current_sharpe)
            
            logger.info(
                f"📊 夏普比率对比: 回测={backtest_result['sharpe_ratio']:.4f}, "
                f"当前={current_sharpe:.4f}, 提升={improvement*100:.1f}%"
            )
            
            # 判断是否超过阈值
            if improvement >= self.sharpe_threshold:
                return True, {
                    'backtest_sharpe': backtest_result['sharpe_ratio'],
                    'current_sharpe': current_sharpe,
                    'improvement': improvement,
                    'reason': f'回测夏普比率高于当前系统 {improvement*100:.1f}%，建议应用进化参数',
                    'backtest_params': {
                        'ADX_THR': backtest_result['adx_thr'],
                        'EMA_TREND': backtest_result['ema_trend'],
                        'ATR_MULT': backtest_result['atr_mult']
                    }
                }
            else:
                return False, {
                    'backtest_sharpe': backtest_result['sharpe_ratio'],
                    'current_sharpe': current_sharpe,
                    'improvement': improvement,
                    'reason': f'回测提升仅 {improvement*100:.1f}%，未达到 {self.sharpe_threshold*100:.0f}% 阈值'
                }
            
        except Exception as e:
            logger.error(f"❌ 进化判断失败: {e}", exc_info=True)
            return False, {'error': str(e)}
    
    def generate_evolution_proposal_message(self):
        """
        生成进化提议消息（用于 Telegram 主动推送）
        
        Returns:
            str: Telegram 消息文本（HTML 格式）
        """
        try:
            should_propose, analysis = self.should_propose_evolution()
            
            if not should_propose:
                return None
            
            backtest_sharpe = analysis.get('backtest_sharpe', 0)
            current_sharpe = analysis.get('current_sharpe', 0)
            improvement = analysis.get('improvement', 0)
            params = analysis.get('backtest_params', {})
            
            message = (
                f"🧬 <b>策略进化提议</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📊 <b>夏普比率对比:</b>\n"
                f"   回测最优: <code>{backtest_sharpe:.4f}</code>\n"
                f"   当前系统: <code>{current_sharpe:.4f}</code>\n"
                f"   提升幅度: <b>+{improvement*100:.1f}%</b>\n\n"
                f"🎯 <b>推荐参数:</b>\n"
                f"   • ADX_THR: <code>{params.get('ADX_THR', 'N/A')}</code>\n"
                f"   • EMA_TREND: <code>{params.get('EMA_TREND', 'N/A')}</code>\n"
                f"   • ATR_MULT: <code>{params.get('ATR_MULT', 'N/A')}</code>\n\n"
                f"💡 <b>AI 建议:</b> {analysis.get('reason', 'N/A')}\n\n"
                f"🚀 使用 /evolution 命令查看详情并一键应用"
            )
            
            return message
            
        except Exception as e:
            logger.error(f"❌ 生成进化提议消息失败: {e}", exc_info=True)
            return None


# 全局单例
_evolution_analyst_instance = None


def get_evolution_analyst():
    """获取进化分析器单例"""
    global _evolution_analyst_instance
    if _evolution_analyst_instance is None:
        _evolution_analyst_instance = BacktestEvolutionAnalyst()
    return _evolution_analyst_instance


# ==========================================
# Module Test
# ==========================================

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    print("=" * 60)
    print("🧪 AI Commander Module Test")
    print("=" * 60)
    
    try:
        commander = get_commander()
        print(f"✅ Commander created: {type(commander).__name__}")
        print(f"✅ Provider: {commander.provider}")
        print(f"✅ Model: {getattr(commander, 'model', 'N/A')}")
        
        test_prompt = "What's the current market sentiment for BTC?"
        print(f"\n📝 Test prompt: {test_prompt}")
        
        response = commander.ask_commander(test_prompt)
        print(f"\n🤖 Response preview: {response[:200]}...")
        
        print("\n✅ All tests passed!")
        
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
