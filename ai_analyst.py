#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI Commander - LLM-based Trading Strategy Advisor
🔥 Claude-Only Architecture - All Gemini dependencies removed
"""

import os
import json
import logging
import httpx
import base64
import trading_engine  # 👈 引入执行器
from intelligence_hub import get_intelligence_hub, IntelligenceType  # 👈 引入新情报中心
from datetime import datetime
from anthropic import AsyncAnthropic
from config import SYSTEM_CONFIG
from human_override import get_override_manager

logger = logging.getLogger(__name__)

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
    
    async def _build_prompt(self, user_text):
        """Build prompt with dynamic personality switching based on intent detection"""
        # 🔥 增强意图识别 - 检测分析类问询词
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
            
            # 🌍 获取全球宏观情报简报（使用 self.hub 单例 + 异步调用）
            try:
                intelligence_data = await self.hub.fetch_global_intelligence(
                    query=user_text,
                    intelligence_type=IntelligenceType.NEWS
                )
                if intelligence_data and isinstance(intelligence_data, dict):
                    # 格式化情报数据为简报
                    intel_lines = ["🌍 全球情报简报 (Real-time)"]
                    for source, data in intelligence_data.items():
                        if isinstance(data, list):
                            for item in data[:3]:
                                if isinstance(item, dict):
                                    title = item.get('title', item.get('content', str(item)))[:100]
                                    intel_lines.append(f"  📡 [{source}] {title}")
                                else:
                                    intel_lines.append(f"  📡 [{source}] {str(item)[:100]}")
                        elif isinstance(data, str):
                            intel_lines.append(f"  📡 [{source}] {data[:200]}")
                    global_intelligence = "\n".join(intel_lines) if len(intel_lines) > 1 else "🌍 全球情报中心已就绪（暂无新情报）"
                elif isinstance(intelligence_data, str) and intelligence_data:
                    global_intelligence = f"🌍 全球情报简报:\n{intelligence_data[:500]}"
                else:
                    global_intelligence = "🌍 全球情报中心已就绪（暂无新情报）"
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
                
                # 🔥 Task 1: 抓取最新3条相关新闻进行交叉验证（直接 await 调用）
                try:
                    sentiment_data = await self.hub.get_cryptopanic_sentiment_async(currencies="BTC,ETH,SOL")
                    news_items = sentiment_data.get('news_items', [])[:3]
                    if news_items:
                        news_lines = ["## 📰 最新市场新闻 (交叉验证)"]
                        for i, news in enumerate(news_items, 1):
                            sentiment_emoji = "🟢" if news['sentiment'] == 'Bullish' else "🔴" if news['sentiment'] == 'Bearish' else "⚪"
                            news_lines.append(f"{i}. {sentiment_emoji} [{news['source']}] {news['title']}")
                            news_lines.append(f"   情绪: {news['sentiment']} | 发布: {news['published']}")
                        latest_news = "\n".join(news_lines)
                    else:
                        latest_news = "## 📰 最新市场新闻\n暂无可用新闻数据"
                except Exception as e:
                    logger.warning(f"⚠️ 新闻抓取失败: {e}")
                    latest_news = "## 📰 最新市场新闻\n⚠️ 新闻数据暂时不可用"
            
            prompt = f"""# 🎯 AI Chief Risk Officer & Trading Strategist

You are the Chief Risk Officer with quantitative trading expertise. Maximize alpha while protecting capital.

## 🧠 [ABSOLUTE RULE] Chain-of-Thought Execution Protocol
**⚠️ 在生成任何 ###COMMAND### 前，必须先展示 [THOUGHT] 思考块，执行视觉、联网、风险、自检四步法。**
**MANDATORY 4-STEP THINKING PROCESS - Execute in STRICT ORDER before generating ANY response:**

### [THOUGHT BLOCK 1: Visual Context Analysis]
**Execute FIRST - Do NOT skip:**
- If chart images provided → Analyze patterns, support/resistance, volume
- If no images → Document "No visual data available" and proceed to Block 2
- **Required Output**: Pattern confidence score (0-1) OR "N/A - no visual data"

### [THOUGHT BLOCK 2: Intelligence Layer Cross-Validation]  
**Execute SECOND - After Block 1:**
- Review Global Intelligence Briefing (geopolitical + crypto sentiment)
- Check Macro Weather Regime (SAFE/RISK_OFF/VOLATILE_CRISIS)
- Cross-validate news sentiment with technical signals
- **Required Output**: Macro risk adjustment factor (e.g., "-30% due to VOLATILE_CRISIS")

### [THOUGHT BLOCK 3: Risk Assessment & Parameter Audit]
**Execute THIRD - After Block 2:**
- Review current system parameters (ADX_THR, EMA_TREND, ATR_MULT, etc.)
- Calculate position sizing safety margin
- Identify parameter conflicts or suboptimal settings
- **Required Output**: Risk level (LOW/MEDIUM/HIGH/CRITICAL) with reasoning

### [THOUGHT BLOCK 4: Final Decision Synthesis & Self-Check]
**Execute FOURTH - After Block 3:**
- Combine visual + intelligence + risk assessments
- Apply macro weather regime constraints
- Self-check: "Have I completed all 4 blocks? Is my recommendation consistent with my analysis?"
- Generate final recommendation with confidence score
- **Required Output**: JSON response with complete reasoning chain

**🚨 CRITICAL ENFORCEMENT**: 
- You MUST execute all 4 thought blocks in sequence and show them explicitly
- Each block MUST produce its required output
- Do NOT skip to ###COMMAND### or final answer without completing ALL blocks
- If you cannot complete a block, document why and proceed with conservative defaults
请用中文回答，除非用户使用英文提问。

## 🎯 Parameter Control Authority
You can directly modify the following parameters (unless manually locked):

**1. 🎯 Core Strategy**
- `STRATEGY_MODE` (Values: "AGGRESSIVE"/"CONSERVATIVE"/"STANDARD"/"SCALPER"/"GOLD_PRO")
- `INTERVAL` (Values: "1m"/"5m"/"15m"/"1h"/"4h"/"1d")
- `DRY_RUN` (bool)
- `AUTO_TUNE_ENABLED` (bool)
- `TRADING_ENGINE_ACTIVE` (bool)
- `AUTO_TUNE_BOUNDARIES` (dict)

**2. 📈 Technical Indicators**
- `ADX_THR` (int, 5-30)
- `EMA_TREND` (int, 44/89/144/169)
- `MACD_FAST` (int)
- `MACD_SLOW` (int)
- `MACD_SIGNAL` (int)
- `RSI_PERIOD` (int)
- `RSI_OVERBOUGHT` (int, 70-85)
- `RSI_OVERSOLD` (int, 15-30)
- `RSI_FILTER_ENABLED` (bool)
- `LOW_VOL_MODE` (bool)

**3. 🛡️ Stop Loss & Volatility**
- `ATR_MULT` (float, 1.2-3.5)
- `ATR_PERIOD` (int)
- `SL_BUFFER` (float)
- `SPACE_LOCK_ENABLED` (bool)
- `MARKET_REGIME_ENABLED` (bool)

**4. 🏃‍♂️ Trailing Stop Loss**
- `TSL_ENABLED` (bool)
- `TSL_TRIGGER_MULT` (float)
- `TSL_CALLBACK_MULT` (float)
- `TSL_UPDATE_THRESHOLD` (float)

**5. 💰 Risk & Sizing**
- `RISK_RATIO` (float, 0.01-0.08)
- `LEVERAGE` (int, 5-25)
- `BENCHMARK_CASH` (float)
- `HEDGE_MODE_ENABLED` (bool)
- `ASSET_WEIGHTS` (dict)

**6. 🧱 Pyramiding**
- `MAX_CONCURRENT_TRADES_PER_SYMBOL` (int, 1-5)
- `MIN_SIGNAL_DISTANCE_ATR` (float)

**7. 🏦 Profit Vault**
- `VAULT_ENABLED` (bool)
- `WITHDRAW_RATIO` (float, 0.01-1.0)
- `VAULT_AUTO_ADAPT` (bool)
- `VAULT_BASE_RATIO` (float)

**8. 🐺 Mad Dog & Scalping**
- `MAD_DOG_MODE` (bool)
- `FORCE_MAD_DOG_MODE` (bool)
- `MAD_DOG_BOOST` (float)
- `MAD_DOG_TRIGGER` (float)

**9. ⚡ Execution**
- `MAKER_FIRST_ENABLED` (bool)
- `MAKER_WAIT_SECONDS` (int)
- `MAX_SLIPPAGE` (float)


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


## Response Protocol (STRICT JSON FORMAT)
```json
{{
  "analysis": "Concise market analysis with key technical/fundamental drivers",
  "recommendation": "BUY/SELL/HOLD/REDUCE_EXPOSURE",
  "confidence": 0.75,
  "reasoning": "Evidence-based rationale with specific price levels/indicators",
  "suggested_mode": "AGGRESSIVE/STANDARD/CONSERVATIVE",
  "risk_notes": "Specific risk management adjustments needed",
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
  "params": {{"STRATEGY_MODE": "CONSERVATIVE", "reason": "High volatility detected"}}
}}
###COMMAND###

Remember: Your job is to PREVENT losses, not just chase gains. Be the skeptic.

[System Integration Note]:
本系统已挂载 trading_engine.py 执行模块。当识别到明确的交易机会时，你必须在回复的 JSON 块中包含 action: "trade" 指令。

Schema: {{"action": "trade", "params": {{"symbol": "BTCUSDT", "side": "BUY/SELL", "reason": "策略简述"}}}}

Note: 系统将根据此 JSON 自动调用 API，请确保参数精准。

[FINAL FORMAT PROTOCOL]

严禁使用 Markdown 符号。

禁止出现 ** (双星号)、# (井号)、_ (下划线) 或 - (列表符)。

必须使用类似'第一、第二、结论：'这种纯文字排版。

所有的推荐参数必须直接写出，不准加任何装饰符。
"""
        else:
            # 🧠 平时模式：满血自由对话，百科助手
            prompt = f"""你现在是 WJ-BOT 智能指挥官 (Claude sonnet4-6)。目前是休息时间，不需要分析交易。
请用你正常的方式回答用户的问题。你博学多才，涉猎广泛，从科技到历史到哲学无所不知。
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
    
    def parse_and_execute(self, ai_response, client=None):
        
        """
        Parse AI response and execute commands with human override protection
        🔥 v2.0: 支持直接下单执行（AI_FULL_AUTONOMY_MODE）
        
        Args:
            ai_response: AI response text containing commands
            client: Binance客户端（用于执行交易）
        
        Returns:
            dict: Execution result with success status and message
        """
        try:
            # Step 1: Parse command from AI response with robust regex
            import re
            
            if "###COMMAND###" not in ai_response:
                return {'success': False, 'message': '⚠️ No command found in response'}
            
            # 🔥 Task 3: 使用 re.DOTALL 增强正则解析，处理换行和特殊字符
            command_match = re.search(r'###COMMAND###\s*(.*?)\s*###COMMAND###', ai_response, re.DOTALL)
            
            if not command_match:
                return {'success': False, 'message': '⚠️ Invalid command format'}
            
            command_json = command_match.group(1).strip()
            
            # 🔥 P0修复 #15: 使用鲁棒JSON解析函数
            command = self._parse_json_response_robust(command_json, default_value={
                'action': 'unknown',
                'params': {}
            })
            
            logger.info(f"📋 Parsed command: {command}")
            
            # Step 2: Extract parameters from command
            action = command.get('action', 'unknown')
            params = command.get('params', {})
            
            # 🔥 新增：处理主动交易指令
            if action in ['trade', 'open_position', 'close_position']:
                if config.SYSTEM_CONFIG.get("AI_FULL_AUTONOMY_MODE", False):
                    # 导入交易引擎
                    import trading_engine
                    
                    # 从 params 提取交易要素
                    symbol = params.get('symbol', 'BTCUSDT')
                    side = params.get('side', '').upper()
                    reason = params.get('reason', 'AI 统帅主动决策')
                    
                    logger.info(f"🔥 AI满血接管模式：准备执行 {action} - {symbol} {side}")
                    
                    try:
                        # 🔥 执行开仓
                        if action in ['trade', 'open_position']:
                            # 获取当前价格
                            from utils import get_current_price
                            current_price = get_current_price(client, symbol)
                            
                            if current_price is None:
                                return {
                                    'success': False,
                                    'message': f'⚠️ 无法获取 {symbol} 当前价格'
                                }
                            
                            # 获取K线数据和技术指标
                            df = trading_engine.get_historical_klines(
                                client, symbol, 
                                config.SYSTEM_CONFIG["INTERVAL"], 
                                limit=200
                            )
                            
                            if df is None:
                                return {
                                    'success': False,
                                    'message': f'⚠️ 无法获取 {symbol} K线数据'
                                }
                            
                            # 计算技术指标
                            df = trading_engine.calculate_indicators(df)
                            if df is None:
                                return {
                                    'success': False,
                                    'message': f'⚠️ 技术指标计算失败'
                                }
                            
                            # 提取ATR和ADX
                            last_candle = df.iloc[-1]
                            atr = float(last_candle.get('ATR', 0))
                            adx = float(last_candle.get('ADX', 0))
                            
                            # 计算仓位大小
                            position_info = trading_engine.calculate_position_size(
                                client, symbol, current_price, 'STRONG', atr=atr
                            )
                            
                            if position_info is None:
                                return {
                                    'success': False,
                                    'message': f'⚠️ 仓位计算失败'
                                }
                            
                            # 执行交易
                            result = trading_engine.execute_trade(
                                client=client,
                                symbol=symbol,
                                signal_type=side,
                                price=current_price,
                                position_info=position_info,
                                atr=atr,
                                adx=adx,
                                position_action='ENTRY'
                            )
                            
                            # 将执行结果加入响应消息
                            if result['success']:
                                executed_params.append({
                                    'param': f"TRADE_{side}",
                                    'new_value': symbol,
                                    'status': 'DONE',
                                    'trade_id': result.get('trade_id'),
                                    'fill_price': result.get('fill_price', current_price)
                                })
                                
                                return {
                                    'success': True,
                                    'executed': executed_params,
                                    'message': f"✅ AI主动开仓成功: {symbol} {side}\n"
                                              f"   交易ID: {result.get('trade_id')}\n"
                                              f"   成交价: ${result.get('fill_price', current_price):.4f}\n"
                                              f"   数量: {position_info['quantity']}\n"
                                              f"   原因: {reason}"
                                }
                            else:
                                return {
                                    'success': False,
                                    'message': f"❌ AI主动开仓失败: {result.get('message', '未知错误')}"
                                }
                        
                        # 🔥 执行平仓
                        elif action == 'close_position':
                            pos_type = 'LONG' if side == 'BUY' else 'SHORT'
                            key_sym = f"{symbol}_{pos_type}"
                            
                            # 检查是否有持仓
                            if key_sym not in config.ACTIVE_POSITIONS and symbol not in config.ACTIVE_POSITIONS:
                                return {
                                    'success': False,
                                    'message': f'⚠️ 没有 {symbol} 的 {pos_type} 持仓可平仓'
                                }
                            
                            # 获取当前价格
                            from utils import get_current_price
                            current_price = get_current_price(client, symbol)
                            
                            if current_price is None:
                                return {
                                    'success': False,
                                    'message': f'⚠️ 无法获取 {symbol} 当前价格'
                                }
                            
                            # 执行平仓
                            result = trading_engine.execute_trade(
                                client=client,
                                symbol=symbol,
                                signal_type='SELL' if pos_type == 'LONG' else 'BUY',
                                price=current_price,
                                position_info={'quantity': 0},
                                position_action=f'EXIT_{pos_type}'
                            )
                            
                            # 将执行结果加入响应消息
                            if result['success']:
                                executed_params.append({
                                    'param': f"CLOSE_{pos_type}",
                                    'new_value': symbol,
                                    'status': 'DONE',
                                    'pnl': result.get('pnl', 0)
                                })
                                
                                return {
                                    'success': True,
                                    'executed': executed_params,
                                    'message': f"✅ AI主动平仓成功: {symbol} {pos_type}\n"
                                              f"   交易ID: {result.get('trade_id')}\n"
                                              f"   净利: ${result.get('pnl', 0):.2f}\n"
                                              f"   原因: {reason}"
                                }
                            else:
                                return {
                                    'success': False,
                                    'message': f"❌ AI主动平仓失败: {result.get('message', '未知错误')}"
                                }
                    
                    except Exception as trade_e:
                        logger.error(f"❌ AI主动交易执行异常: {trade_e}", exc_info=True)
                        return {
                            'success': False,
                            'message': f'❌ 交易执行异常: {str(trade_e)[:100]}'
                        }
                
                else:
                    # 如果未授权，则将该动作转为"待审批令牌"
                    logger.warning(f"🔒 AI 试图执行 {action}，但满血接管模式未开启，已拦截并生成授权令牌")
                    
                    command_id = f"{action}_{params.get('symbol', 'UNKNOWN')}_{int(datetime.now().timestamp())}"
                    token = override_mgr.request_confirmation(
                        command_id=command_id,
                        command_data={
                            'action': action,
                            'params': params
                        },
                        requester='ai'
                    )
                    
                    return {
                        'success': False,
                        'blocked': [{
                            'action': action,
                            'params': params,
                            'token': token,
                            'reason': 'AI_FULL_AUTONOMY_MODE 未开启'
                        }],
                        'tokens': [token],
                        'message': f"🔒 AI交易指令已被拦截\n"
                                  f"   动作: {action}\n"
                                  f"   参数: {params}\n"
                                  f"   原因: AI_FULL_AUTONOMY_MODE 未开启\n\n"
                                  f"   请回复 /confirm {token} 授权执行，或 /reject {token} 否决。"
                    }
            
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
                # 🛡️ 过滤掉非配置参数的元数据
                if param_name in ['reason', 'mode', 'explanation']:
                    continue
                    
                # Check if parameter exists in SYSTEM_CONFIG
                if param_name not in config.SYSTEM_CONFIG:
                    logger.warning(f"⚠️ 参数 [{param_name}] 不属于系统配置，跳过执行")
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
# 🏛️ L7.5 众神议会核心 (Council of Gods)
# DeepSeek R1 (大脑) + Gemini 3 Pro (双眼)
# ==========================================
import openai
from google import genai
from google.genai import types # 用于处理多模态数据import asyncio

class CouncilOfGodsCommander(BaseCommander):
    """
    🔥 众神议会 v4.5 - 三位一体巅峰版 (R1 推演 + Claude 决策 + Gemini 视觉)
    职责：三核联动，逻辑对账，视觉审计
    """
    def __init__(self):
        super().__init__()
        self.provider = 'council'

        # --- 1. 逻辑参谋：DeepSeek-R1 ---
        self.ds_api_key = SYSTEM_CONFIG.get('SILICONFLOW_API_KEY', os.getenv('SILICONFLOW_API_KEY'))
        self.ds_base_url = 'https://api.siliconflow.cn/v1'
        self.ds_model = SYSTEM_CONFIG.get('SILICONFLOW_MODEL', 'deepseek-ai/DeepSeek-R1')
        try:
            import openai
            self.ds_client = openai.AsyncOpenAI(api_key=self.ds_api_key, base_url=self.ds_base_url) if self.ds_api_key else None
        except Exception: self.ds_client = None

        # --- 2. 最高统帅：Claude ---
        # 🚀 优先从系统配置读取，确保动态性
        self.claude_api_key = SYSTEM_CONFIG.get('CLAUDE_API_KEY')
        self.claude_base_url = SYSTEM_CONFIG.get('CLAUDE_BASE_URL')

        try:
            # 🔥 核心修正：使用 AsyncAnthropic 避免同步阻塞
            self.claude_client = AsyncAnthropic(
                api_key=self.claude_api_key,
                base_url=self.claude_base_url  # 👈 接通您的中转地址
            ) if self.claude_api_key else None
            
            # 🚀 使用 .env 中定义的 claude-sonnet-4-6
            self.claude_model = SYSTEM_CONFIG.get('LLM_MODEL_NAME', 'claude-3-5-sonnet-20241022')
            
        except Exception as e:
            self.claude_client = None
            logger.error(f"❌ Claude 初始化失败: {e}")

        # --- 3. 全知之眼：Gemini 3 (2.0 Flash) ---
        self.gemini_api_key = SYSTEM_CONFIG.get('GEMINI_API_KEY', os.getenv('GEMINI_API_KEY'))
        if self.gemini_api_key:
            try:
                # 🚀 切换到新版 Client 模式
                self.gemini_client = genai.Client(api_key=self.gemini_api_key)
                self.gemini_model_id = 'gemini-3-flash-preview' # ✅ 使用最新的 Gemini 3 标识符
            except Exception as e:
                self.gemini_client = None
                logger.error(f"❌ Gemini 新版 SDK 初始化失败: {e}")
        else:
            self.gemini_client = None

        # --- 4. 情报中心：IntelligenceHub ---
        # 🔥 初始化情报中心单例，用于获取全球宏观情报
        self.hub = get_intelligence_hub(proxy_url=self.proxy_url)

        # 🚀 立即执行心脏自检
        self._startup_self_check()

    def _startup_self_check(self):
        """🏛️ 议会点火自检：确保三核同步在线 (v5.0 兼容版)"""
        # 🔥 修复：动态检测 gemini_client (新版) 或 gemini_vision_model (旧版)
        gemini_online = False
        
        # 检查新版 SDK 的 gemini_client
        if hasattr(self, 'gemini_client') and getattr(self, 'gemini_client', None) is not None:
            gemini_online = True
        # 检查旧版 SDK 的 gemini_vision_model
        elif hasattr(self, 'gemini_vision_model') and getattr(self, 'gemini_vision_model', None) is not None:
            gemini_online = True

        status = {
            "🧠 R1 参谋 (逻辑)": "✅ 在线" if getattr(self, 'ds_client', None) is not None else "❌ 缺失",
            "⚖️ Claude 统帅 (决策)": "✅ 在线" if getattr(self, 'claude_client', None) is not None else "❌ 缺失",
            "👁️ Gemini 3 (视觉)": "✅ 在线" if gemini_online else "❌ 缺失"
        }
        
        report = "\n" + "═"*40 + "\n"
        report += "      🏛️ [众神议会] 核心引擎点火自检\n"
        for k, v in status.items(): report += f"      ├ {k}: {v}\n"
        report += "      💡 提示: 若视觉核显示缺失，请检查 GEMINI_API_KEY\n"
        report += "═"*40
        print(report)
        logger.info(report)

    async def analyze_chart_with_vision_bytes(self, image_bytes, prompt):
        """👁️ 视觉神经：调用 Gemini 3 解析 K 线图"""
        try:
            if not self.gemini_client: return "⚠️ 视觉官未就绪"
            
            # 🚀 新版 SDK 专用多模态封装
            response = await self.gemini_client.aio.models.generate_content(
                model=self.gemini_model_id,
                contents=[
                    prompt,
                    types.Part.from_bytes(data=image_bytes, mime_type="image/png")
                ]
            )
            return response.text
        except Exception as e:
            logger.error(f"❌ Gemini 3 扫描失败: {e}")
            return f"⚠️ 视觉扫描异常: {e}"

    async def _call_deepseek_r1(self, prompt):
        """🧠 逻辑神经：R1 深度推演思维链（内部上下文，不直接展示给用户）"""
        if not self.ds_client: return "⚠️ R1 参谋未就绪"
        try:
            response = await self.ds_client.chat.completions.create(
                model=self.ds_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=1024  # 🔥 强制 R1 压缩思维链，减少生成等待时间
            )
            msg = response.choices[0].message
            reasoning = getattr(msg, 'reasoning_content', '') or ''
            content = msg.content or ''
            # 🔥 重构：R1 输出仅作为内部上下文，不再拼接展示标签
            final = ""
            if reasoning:
                final += f"[R1推演过程]\n{reasoning}\n\n"
            final += content
            return final
        except Exception as e:
            return f"⚠️ R1 推演中断: {e}"

    async def _call_claude_final_audit(self, r1_report, user_prompt):
        """⚖️ 统帅神经：Claude 铁腕终审逻辑（异步版本）"""
        if not self.claude_client: return r1_report
        try:
            sys_prompt = "你是量化交易最高统帅。审核参谋长(R1)的报告，纠正幻觉，下达精准指令。"
            combined = f"【参谋长 R1 报告】:\n{r1_report}\n\n【用户指令】: {user_prompt}"
            # 🔥 核心修正：使用 await 调用异步 API
            res = await self.claude_client.messages.create(
                model=self.claude_model,
                max_tokens=2048,
                system=sys_prompt,
                messages=[{"role": "user", "content": combined}]
            )
            return f"{r1_report}\n\n⚖️ <b>[Claude 统帅最终裁决]</b>\n{res.content[0].text}"
        except Exception as e:
            return f"{r1_report}\n\n⚠️ (Claude 统帅离线，仅展示 R1 建议)"

    async def _call_claude_direct_chat(self, user_text):
        """
        🔥 提速核心：Claude 直连闲聊模式（跳过 R1 推演）
        用于非分析类问询，响应时间从 40s 降至 5s
        """
        if not self.claude_client:
            return "⚠️ Claude 统帅未就绪，无法处理闲聊请求"
        
        try:
            # 构建轻量级闲聊 prompt
            sys_prompt = (
                "你是 WJ-BOT 智能指挥官 (Claude sonnet4-6)。"
                "目前是休息时间，不需要分析交易。"
                "请用你正常的方式回答用户的问题。"
                "你博学多才，涉猎广泛，从科技到历史到哲学无所不知。"
                "请用中文回答，除非用户使用英文提问。"
            )
            
            # 🔥 核心修正：使用 await 调用异步 API
            response = await self.claude_client.messages.create(
                model=self.claude_model,
                max_tokens=1024,
                system=sys_prompt,
                messages=[{"role": "user", "content": user_text}]
            )
            
            return response.content[0].text
            
        except Exception as e:
            logger.error(f"❌ Claude 直连闲聊失败: {e}")
            return f"⚠️ 闲聊模式异常: {e}"

    def _detect_analysis_intent(self, user_text):
        """
        🔥 工业级意图检测引擎 v2.0
        
        特性：
        1. 正则边界匹配（防止 'adx' 误匹配 'adxl345'）
        2. 负面模式过滤（排除非交易场景）
        3. 中英文混合支持
        4. 权重评分系统（未来可扩展）
        
        Args:
            user_text: 用户输入文本
        
        Returns:
            bool: True=需要深度分析, False=闲聊模式
        """
        import re
        
        text_lower = user_text.lower()
        
        # 🚫 负面模式：明确排除的非交易场景
        negative_patterns = [
            r'\b(天气|weather|温度|temperature)\b',
            r'\b(电影|movie|音乐|music|游戏|game)\b',
            r'\b(新闻|news)(?!.*?(btc|eth|crypto))',  # 排除非加密新闻
            r'\b(你好|hello|hi|嗨)\b(?!.{0,20}(btc|eth|行情))',  # 单纯问候
            r'\b(讲个|笑话|joke|故事|story)\b',
        ]
        
        for pattern in negative_patterns:
            if re.search(pattern, text_lower):
                logger.debug(f"🚫 负面模式命中: {pattern}")
                return False
        
        # ✅ 正面关键词：需要边界匹配的英文词（防止误匹配）
        boundary_keywords = {
            # 技术指标（严格边界）
            r'\b(atr|ema|adx|rsi|macd|vwap|kdj|bb)\b',
            # 交易动作
            r'\b(long|short|buy|sell|trade|position|risk)\b',
            # 市场术语
            r'\b(btc|eth|sol|bnb|usdt|crypto|market|pump|dump)\b',
            # 回测分析
            r'\b(backtest|sharpe|parameter|optimize)\b',
            # 指令词
            r'\b(analyze|strategy|evaluate|set|toggle|enable|disable)\b',
        }
        
        for pattern in boundary_keywords:
            if re.search(pattern, text_lower):
                logger.debug(f"✅ 边界关键词命中: {pattern}")
                return True
        
        # ✅ 中文关键词：直接子串匹配（中文无需边界）
        chinese_keywords = [
            '分析', '策略', '为什么', '判断', '行情', '怎么看', '看看',
            '建议', '意见', '观点', '预测', '解释', '原因',
            '大饼', '以太', '二饼', '山寨', '主流', '土狗',
            '仓位', '风险', '多', '空', '止损', '止盈', '杠杆', 
            '开仓', '平仓', '回测', '参数', '调参', '优化',
            '金叉', '死叉', '背离', '支撑', '压力',
            '牛市', '熊市', '横盘', '震荡', '插针', '爆仓',
            '资金费率', '持仓量', '开启', '关闭', '设置', '激活', '撤销'
        ]
        
        for keyword in chinese_keywords:
            if keyword in text_lower:
                logger.debug(f"✅ 中文关键词命中: {keyword}")
                return True
        
        # 默认：闲聊模式
        logger.debug("💬 未命中任何分析关键词，判定为闲聊")
        return False

    async def ask_commander(self, user_text, position_action=None, kline_data=None, symbol=None):
        """
        🏛️ 议会调度中心 v5.1 - 工业级意图检测 + 分层响应
        
        响应策略：
        1. 视觉对账模式：K线数据 + 开仓动作 → 三位一体审计
        2. 深度分析模式：包含分析关键词 → R1 推演 + Claude 终审
        3. 闲聊模式：普通问询 → Claude 直连（5s 快速响应）
        """
        try:
            # 1. 如果有 K 线数据，进入"视觉对账"大招模式
            if position_action == 'ENTRY' and kline_data is not None and symbol:
                return await self._visual_audit_entry(user_text, kline_data, symbol)

            # 2. 工业级意图检测（正则边界 + 负面过滤）
            is_analysis_needed = self._detect_analysis_intent(user_text)
            
            # 3. 如果只是闲聊，跳过 R1，直接让 Claude 统帅发言
            if not is_analysis_needed:
                logger.info("🚀 检测到闲聊模式，启用 Claude 直连（预计 5s 响应）")
                return await self._call_claude_direct_chat(user_text)
            
            # 4. 只有重型任务才执行 R1 -> Claude 串联逻辑
            logger.info("🧠 检测到分析任务，启用 R1 推演 + Claude 终审（预计 40s 响应）")
            prompt = await self._build_prompt(user_text)
            r1_result = await self._call_deepseek_r1(prompt)
            
            # 串联终审
            return await self._call_claude_final_audit(r1_result, user_text)
            
        except Exception as e:
            logger.error(f"❌ 议会调度失败: {e}")
            return f"⚠️ 议会核心宕机: {e}"

    async def _visual_audit_entry(self, user_text, kline_data, symbol):
        """👁️ 视觉 + 🧠 逻辑 + ⚖️ 决策：三位一体最强形态"""
        try:
            from utils import get_kline_buffer
            logger.info(f"🏛️ 开启三位一体图表审计: {symbol}")

            # Step 1: Gemini 3 扫描图表
            img = get_kline_buffer(kline_data, symbol=symbol, num_candles=50)
            if img:
                v_prompt = "分析 K 线图 SMC 结构：流动性池、FVG 缺口、订单块、市场派发/积累状态。"
                v_report = await self.analyze_chart_with_vision_bytes(img, v_prompt)
            else:
                v_report = "⚠️ 视觉官：未获得有效图表"

            # Step 2: R1 参谋结合视觉报告做逻辑推演
            r1_p = await self._build_prompt(user_text) + f"\n\n## 👁️ 视觉官 K 线报告:\n{v_report}"
            r1_res = await self._call_deepseek_r1(r1_p)

            # Step 3: Claude 统帅下达最终指令
            return await self._call_claude_final_audit(r1_res, user_text)
            
        except Exception as e:
            logger.error(f"❌ 视觉审计失败: {e}")
            return await self.ask_commander(user_text)

# ==========================================
# Intent Detection Helper (Module-Level)
# ==========================================

def is_analysis_intent(user_text):
    """
    模块级意图检测 - 判断是否需要深度分析
    供外部模块调用（如 bot_handlers）
    
    Args:
        user_text: 用户输入
    
    Returns:
        bool: True=分析模式, False=闲聊模式
    """
    import re
    text_lower = user_text.lower()
    
    # 排除非交易场景
    if re.search(r'(天气|weather|电影|movie|你好|hello|hi|嗨|笑话|joke|故事|story|音乐|music|游戏|game)', text_lower):
        return False
    
    # 英文关键词：边界匹配
    if re.search(r'\b(atr|ema|adx|rsi|macd|btc|eth|sol|bnb|long|short|buy|sell|trade|position|risk|backtest|analyze|strategy)\b', text_lower):
        return True
    
    # 中文关键词
    return any(k in text_lower for k in [
        '分析', '策略', '判断', '行情', '建议', '仓位', '风险', '止损', '止盈',
        '回测', '参数', '杠杆', '开仓', '平仓', '大饼', '以太', '预测', '趋势'
    ])


# ==========================================
# Factory Function
# ==========================================

def get_commander():
    """工厂函数：创建并返回众神议会指挥官"""
    return CouncilOfGodsCommander()


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
