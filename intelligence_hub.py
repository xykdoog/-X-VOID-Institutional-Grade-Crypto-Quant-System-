#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Intelligence Hub - Macro Sentinel & Strategic Orchestrator (Phase 1)
监控全市场宏观数据，动态覆写 SYSTEM_CONFIG，记录审计日志

核心职责:
  1. 获取清算数据 / 多空比 / ATR波动率百分位
  2. 决策矩阵: BLACK_SWAN / CHOP_ZONE / SQUEEZE_EXHAUSTION
  3. 每5分钟运行 macro_commander_loop，审计每次宏观调控
"""

import time
import threading
import numpy as np
import httpx
from enum import Enum
from datetime import datetime
from binance.client import Client
from logger_setup import logger
import config
class IntelligenceType(Enum):
    BLACK_SWAN = "BLACK_SWAN"
    CHOP_ZONE = "CHOP_ZONE"
    SQUEEZE_EXHAUSTION = "SQUEEZE_EXHAUSTION"
    VOLATILE = "VOLATILE"
    OVERHEATED = "OVERHEATED"
    NORMAL = "NORMAL"
    NEWS = "NEWS"

# ==========================================
# 宏观数据获取器
# ==========================================

class MacroDataFetcher:
    """获取清算、多空比、ATR波动率等宏观指标"""

    def __init__(self, client: Client):
        self.client = client

    # ------------------------------------------
    # 1. 清算数据
    # ------------------------------------------
    def get_global_liquidations(self, symbol="BTCUSDT"):
        """
        获取近1h / 24h 清算总额（USD）
        如果 1h 清算 > $1M → VOLATILE
        Returns: dict {liq_1h, liq_24h}
        """
        try:
            liquidations = self.client.futures_liquidation_orders(
                symbol=symbol, limit=1000
            )
            now_ms = time.time() * 1000
            one_hour_ago = now_ms - 3_600_000
            twenty_four_hours_ago = now_ms - 86_400_000

            liq_1h = 0.0
            liq_24h = 0.0
            for liq in liquidations:
                notional = float(liq['origQty']) * float(liq['price'])
                ts = liq.get('time', 0)
                if ts > twenty_four_hours_ago:
                    liq_24h += notional
                if ts > one_hour_ago:
                    liq_1h += notional

            return {"liq_1h": liq_1h, "liq_24h": liq_24h}
        except Exception as e:
            logger.warning(f"⚠️ 获取清算数据失败: {e}")
            return {"liq_1h": 0.0, "liq_24h": 0.0}

    # ------------------------------------------
    # 2. 多空比
    # ------------------------------------------
    def get_binance_ls_ratio(self, symbol="BTCUSDT", period="5m"):
        """
        获取 Top Trader Long/Short Account Ratio
        如果 Ratio > 2.5 → OVERHEATED
        Returns: float
        """
        try:
            ratio_data = self.client.futures_top_longshort_account_ratio(
                symbol=symbol, period=period, limit=1
            )
            if ratio_data:
                return float(ratio_data[0]['longShortRatio'])
            return 1.0
        except Exception as e:
            logger.warning(f"⚠️ 获取多空比失败: {e}")
            return 1.0

    # ------------------------------------------
    # 3. VIX Regime (ATR 百分位)
    # ------------------------------------------
    def get_vix_regime(self, symbol="BTCUSDT", lookback_days=30):
        """
        计算当前 ATR 在过去 lookback_days 天中的百分位
        用 1h K线，每天24根，共 lookback_days*24 根
        Returns: dict {current_atr, percentile, regime}
        """
        try:
            limit = lookback_days * 24
            klines = self.client.futures_klines(
                symbol=symbol, interval="1h", limit=min(limit, 1500)
            )
            if not klines or len(klines) < 28:
                return {"current_atr": 0, "percentile": 50, "regime": "NORMAL"}

            highs = np.array([float(k[2]) for k in klines])
            lows = np.array([float(k[3]) for k in klines])
            closes = np.array([float(k[4]) for k in klines])

            # True Range 计算
            tr1 = highs[1:] - lows[1:]
            tr2 = np.abs(highs[1:] - closes[:-1])
            tr3 = np.abs(lows[1:] - closes[:-1])
            true_range = np.maximum(tr1, np.maximum(tr2, tr3))

            # 14-period ATR 滚动窗口
            atr_period = 14
            if len(true_range) < atr_period:
                return {"current_atr": 0, "percentile": 50, "regime": "NORMAL"}

            atr_series = []
            for i in range(atr_period - 1, len(true_range)):
                atr_val = np.mean(true_range[i - atr_period + 1: i + 1])
                atr_series.append(atr_val)

            atr_array = np.array(atr_series)
            current_atr = atr_array[-1]

            # 百分位排名
            percentile = float(np.sum(atr_array < current_atr) / len(atr_array) * 100)

            if percentile > 90:
                regime = "EXTREME"
            elif percentile > 70:
                regime = "HIGH"
            elif percentile < 30:
                regime = "LOW"
            else:
                regime = "NORMAL"

            return {
                "current_atr": round(float(current_atr), 2),
                "percentile": round(percentile, 1),
                "regime": regime
            }
        except Exception as e:
            logger.warning(f"⚠️ 获取VIX Regime失败: {e}")
            return {"current_atr": 0, "percentile": 50, "regime": "NORMAL"}

    # ------------------------------------------
    # 4. 辅助: ADX + RSI (用于 CHOP_ZONE / SQUEEZE 判断)
    # ------------------------------------------
    def get_adx_and_rsi(self, symbol="BTCUSDT"):
        """
        获取最新 ADX 和 RSI 值
        Returns: dict {adx, rsi}
        """
        try:
            klines = self.client.futures_klines(
                symbol=symbol, interval="15m", limit=100
            )
            if not klines or len(klines) < 30:
                return {"adx": 25, "rsi": 50}

            highs = np.array([float(k[2]) for k in klines])
            lows = np.array([float(k[3]) for k in klines])
            closes = np.array([float(k[4]) for k in klines])

            # --- ADX 计算 (14-period) ---
            adx_val = self._calculate_adx(highs, lows, closes, period=14)

            # --- RSI 计算 (14-period) ---
            rsi_val = self._calculate_rsi(closes, period=14)

            return {"adx": round(adx_val, 1), "rsi": round(rsi_val, 1)}
        except Exception as e:
            logger.warning(f"⚠️ 获取ADX/RSI失败: {e}")
            return {"adx": 25, "rsi": 50}

    @staticmethod
    def _calculate_adx(highs, lows, closes, period=14):
        """Wilder's ADX"""
        n = len(highs)
        if n < period + 1:
            return 25.0

        plus_dm = np.zeros(n)
        minus_dm = np.zeros(n)
        tr = np.zeros(n)

        for i in range(1, n):
            up_move = highs[i] - highs[i - 1]
            down_move = lows[i - 1] - lows[i]
            plus_dm[i] = up_move if (up_move > down_move and up_move > 0) else 0
            minus_dm[i] = down_move if (down_move > up_move and down_move > 0) else 0
            tr[i] = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1])
            )

        # Wilder smoothing
        atr = np.zeros(n)
        plus_di_arr = np.zeros(n)
        minus_di_arr = np.zeros(n)

        atr[period] = np.mean(tr[1:period + 1])
        plus_di_arr[period] = np.mean(plus_dm[1:period + 1])
        minus_di_arr[period] = np.mean(minus_dm[1:period + 1])

        for i in range(period + 1, n):
            atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
            plus_di_arr[i] = (plus_di_arr[i - 1] * (period - 1) + plus_dm[i]) / period
            minus_di_arr[i] = (minus_di_arr[i - 1] * (period - 1) + minus_dm[i]) / period

        # DX series
        dx_series = []
        for i in range(period, n):
            if atr[i] == 0:
                continue
            pdi = 100 * plus_di_arr[i] / atr[i]
            mdi = 100 * minus_di_arr[i] / atr[i]
            denom = pdi + mdi
            if denom == 0:
                continue
            dx_series.append(abs(pdi - mdi) / denom * 100)

        if len(dx_series) < period:
            return 25.0

        # ADX = smoothed DX
        adx = np.mean(dx_series[:period])
        for i in range(period, len(dx_series)):
            adx = (adx * (period - 1) + dx_series[i]) / period

        return float(adx)

    @staticmethod
    def _calculate_rsi(closes, period=14):
        """Standard RSI"""
        if len(closes) < period + 1:
            return 50.0
        deltas = np.diff(closes)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)

        avg_gain = np.mean(gains[:period])
        avg_loss = np.mean(losses[:period])

        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period

        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return float(100 - 100 / (1 + rs))


# ==========================================
# 决策矩阵 & 审计日志
# ==========================================

def _save_macro_audit(scenario, reason, overwrites, snapshot):
    """
    记录宏观调控审计日志到 Redis (通过 trading_engine.save_audit_log)
    """
    try:
        from trading_engine import save_audit_log
        audit_id = f"MACRO_{scenario}_{int(time.time())}"
        audit_data = {
            "type": "MACRO_SHIFT",
            "scenario": scenario,
            "reason": reason,
            "overwrites": overwrites,
            "snapshot": snapshot,
            "timestamp": datetime.now().isoformat(),
        }
        save_audit_log(audit_id, audit_data)
        logger.info(f"📋 宏观审计已记录: {audit_id} | {reason}")
    except Exception as e:
        logger.warning(f"⚠️ 宏观审计日志写入失败: {e}")


def _send_macro_alert(scenario, reason, overwrites):
    """通过 Telegram 推送宏观调控通知"""
    try:
        from utils import send_tg_msg
        emoji_map = {
            "BLACK_SWAN": "🦢",
            "CHOP_ZONE": "🌀",
            "SQUEEZE_EXHAUSTION": "💥",
            "VOLATILE": "⚡",
            "OVERHEATED": "🔥",
        }
        emoji = emoji_map.get(scenario, "📡")
        overwrite_lines = "\n".join([f"  • <code>{k}: {v}</code>" for k, v in overwrites.items()])
        msg = (
            f"{emoji} <b>[MacroCommander] {scenario}</b>\n\n"
            f"📝 原因: {reason}\n\n"
            f"🔧 配置覆写:\n{overwrite_lines}\n\n"
            f"⏰ {datetime.now().strftime('%H:%M:%S')}"
        )
        send_tg_msg(msg)
    except Exception as e:
        logger.warning(f"⚠️ 宏观通知推送失败: {e}")


# ==========================================
# IntelligenceHub - 战略编排核心
# ==========================================

class IntelligenceHub:
    """
    智能中枢 - 宏观调控引擎 (Phase 1: Macro Sentinel)

    每5分钟扫描:
      - 清算数据 → VOLATILE 判定
      - 多空比 → OVERHEATED 判定
      - ATR百分位 → VIX Regime
      - ADX + RSI → CHOP_ZONE / SQUEEZE_EXHAUSTION

    决策矩阵:
      - BLACK_SWAN: VIX > 90% → CONSERVATIVE + RISK_RATIO=0.005
      - CHOP_ZONE: ADX < 20 + Low Vol → SCALPER + MAKER_FIRST
      - SQUEEZE_EXHAUSTION: High Liq + RSI Divergence → 拦截趋势跟踪入场
    """

    def __init__(self, client: Client, proxy_url: str = None):
        self.client = client
        self.fetcher = MacroDataFetcher(client)
        self.proxy_url = proxy_url
        self.running = False
        self._thread = None

        # 状态追踪 (避免重复触发)
        self._last_scenario = "NORMAL"
        self._last_shift_time = 0
        self._shift_cooldown = 300  # 5分钟冷却，防止抖动

        # SQUEEZE_EXHAUSTION 拦截标志
        self.trend_entry_blocked = False
        self._block_reason = ""

    # ------------------------------------------
    # 全量宏观扫描
    # ------------------------------------------
    def run_macro_scan(self):
        """
        执行一次完整宏观扫描，返回分析结果字典
        """
        liq_data = self.fetcher.get_global_liquidations()
        ls_ratio = self.fetcher.get_binance_ls_ratio()
        vix_data = self.fetcher.get_vix_regime()
        adx_rsi = self.fetcher.get_adx_and_rsi()

        # 基础判定
        macro_regime = "NORMAL"
        sentiment = "NEUTRAL"

        if liq_data["liq_1h"] > 1_000_000:
            macro_regime = "VOLATILE"
        if ls_ratio > 2.5:
            sentiment = "OVERHEATED"

        return {
            "liq_1h": liq_data["liq_1h"],
            "liq_24h": liq_data["liq_24h"],
            "ls_ratio": ls_ratio,
            "vix_percentile": vix_data["percentile"],
            "vix_regime": vix_data["regime"],
            "current_atr": vix_data["current_atr"],
            "adx": adx_rsi["adx"],
            "rsi": adx_rsi["rsi"],
            "macro_regime": macro_regime,
            "sentiment": sentiment,
        }

    # ------------------------------------------
    # 决策矩阵
    # ------------------------------------------
    def apply_decision_matrix(self, scan):
        scenario = "NORMAL"
        reason = ""
        overwrites = {} 

        vix_pct = scan["vix_percentile"]
        adx = scan["adx"]
        rsi = scan["rsi"]
        liq_1h = scan["liq_1h"]
        vix_regime = scan["vix_regime"]
        ls_ratio = scan["ls_ratio"]

        # ========== 1. BLACK_SWAN (最高优先级) ==========
        if vix_pct > 90:
            scenario = "BLACK_SWAN"
            reason = f"VIX百分位 {vix_pct:.1f}% > 90% (极端波动)"
            overwrites = {
                "STRATEGY_MODE": "CONSERVATIVE",
                "RISK_RATIO": 0.005,
            }

        # ========== 2. SQUEEZE_EXHAUSTION ==========
        elif liq_1h > 1_000_000 and self._detect_rsi_divergence(rsi):
            scenario = "SQUEEZE_EXHAUSTION"
            reason = f"高清算 ${liq_1h:,.0f} + RSI背离 → 拦截趋势跟踪"
            self.trend_entry_blocked = True
            self._block_reason = reason
            # 明确写入拦截标志
            overwrites = {
                "_TREND_ENTRY_BLOCKED": True,
                "_BLOCK_REASON": reason
            }

        # ========== 3. CHOP_ZONE ==========
        elif adx < 20 and vix_regime in ("LOW", "NORMAL"):
            scenario = "CHOP_ZONE"
            reason = f"ADX={adx:.1f} < 20 + 低波动 ({vix_regime}) → 切换剥头皮"
            overwrites = {
                "STRATEGY_MODE": "SCALPER",
                "MAKER_FIRST_ENABLED": True,
            }

        # ========== 4. 基础规则 (非极端场景) ==========
        else:
            # 清除拦截标志
            if self.trend_entry_blocked:
                self.trend_entry_blocked = False
                self._block_reason = ""
                logger.info("✅ SQUEEZE_EXHAUSTION 拦截已解除")

            # VOLATILE 基础处理
            if scan["macro_regime"] == "VOLATILE":
                scenario = "VOLATILE"
                reason = f"1h清算 ${liq_1h:,.0f} > $1M"
                overwrites = {"MACRO_REGIME": "VOLATILE"}

            # OVERHEATED 基础处理
            if scan["sentiment"] == "OVERHEATED":
                scenario = "OVERHEATED" if scenario == "NORMAL" else scenario
                reason += f" | 多空比 {ls_ratio:.2f} > 2.5 (极度拥挤多头)"
                current_risk = config.SYSTEM_CONFIG.get("RISK_RATIO", 0.025)
                overwrites["RISK_RATIO"] = round(current_risk * 0.5, 4)

        # ========== 执行覆写 ==========
        if scenario != "NORMAL" and overwrites:
            self._execute_overwrites(scenario, reason, overwrites, scan)

        return scenario, reason

    def _execute_overwrites(self, scenario, reason, overwrites, scan):
        """执行配置覆写 + 审计 + 通知"""
        now = time.time()

        # 冷却检查: 同一场景5分钟内不重复触发
        if scenario == self._last_scenario and (now - self._last_shift_time) < self._shift_cooldown:
            return

        with config.config_lock:
            original_snapshot = {
                "STRATEGY_MODE": config.SYSTEM_CONFIG.get("STRATEGY_MODE"),
                "RISK_RATIO": config.SYSTEM_CONFIG.get("RISK_RATIO"),
                "MAKER_FIRST_ENABLED": config.SYSTEM_CONFIG.get("MAKER_FIRST_ENABLED"),
            }

            for key, value in overwrites.items():
                if key.startswith("_") and not key.startswith("_TREND_"):
                    continue  # 内部标志，不写入 SYSTEM_CONFIG
                config.SYSTEM_CONFIG[key] = value

            # 如果切换了策略模式，应用预设
            new_mode = overwrites.get("STRATEGY_MODE")
            if new_mode and new_mode in config.STRATEGY_PRESETS:
                config.apply_strategy_preset(new_mode)
                # 覆写后再次设置 RISK_RATIO (预设可能覆盖)
                if "RISK_RATIO" in overwrites:
                    config.SYSTEM_CONFIG["RISK_RATIO"] = overwrites["RISK_RATIO"]

            config.save_data()

        self._last_scenario = scenario
        self._last_shift_time = now

        logger.info(f"🧠 [MacroCommander] {scenario}: {reason}")

        # 审计日志
        _save_macro_audit(scenario, reason, overwrites, {
            "before": original_snapshot,
            "scan": scan,
        })

        # Telegram 通知
        _send_macro_alert(scenario, reason, overwrites)

    @staticmethod
    def _detect_rsi_divergence(rsi):
        """
        简化版 RSI 背离检测:
        RSI 处于超买/超卖极端区域时视为潜在背离信号
        (完整版需要价格高低点对比，此处用阈值近似)
        """
        return rsi > 75 or rsi < 25

    # ------------------------------------------
    # 状态查询 (供 trading_engine 调用)
    # ------------------------------------------
    def is_trend_entry_blocked(self):
        """检查趋势跟踪入场是否被 SQUEEZE_EXHAUSTION 拦截"""
        return self.trend_entry_blocked

    def get_block_reason(self):
        return self._block_reason

    def get_status(self):
        """返回当前 Hub 状态摘要"""
        return {
            "last_scenario": self._last_scenario,
            "trend_blocked": self.trend_entry_blocked,
            "block_reason": self._block_reason,
            "last_shift_time": datetime.fromtimestamp(self._last_shift_time).strftime('%H:%M:%S')
            if self._last_shift_time > 0 else "N/A",
        }

    # ------------------------------------------
    # 后台循环
    # ------------------------------------------
    def macro_commander_loop_inner(self):
        """内部循环: 每5分钟执行一次宏观扫描 + 决策"""
        logger.info("🧠 MacroCommander 战略编排器已启动 (5分钟周期)")

        while self.running:
            try:
                scan = self.run_macro_scan()

                logger.info(
                    f"📡 [{datetime.now().strftime('%H:%M:%S')}] 宏观扫描 | "
                    f"清算1h: ${scan['liq_1h']:,.0f} | "
                    f"多空比: {scan['ls_ratio']:.2f} | "
                    f"VIX: {scan['vix_percentile']:.0f}% ({scan['vix_regime']}) | "
                    f"ADX: {scan['adx']:.1f} | RSI: {scan['rsi']:.1f}"
                )

                scenario, reason = self.apply_decision_matrix(scan)

                if scenario != "NORMAL":
                    logger.info(f"🎯 决策: {scenario} → {reason}")

            except Exception as e:
                logger.error(f"❌ MacroCommander 扫描异常: {e}")

            # 每5分钟
            for _ in range(300):
                if not self.running:
                    break
                time.sleep(1)

    def start(self):
        """启动后台线程"""
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(
            target=self.macro_commander_loop_inner,
            name="MacroCommander",
            daemon=True,
        )
        self._thread.start()

    def stop(self):
        """停止后台线程"""
        self.running = False
        if self._thread:
            self._thread.join(timeout=10)

    # ------------------------------------------
    # 异步情报获取 (供 ai_analyst.py 调用)
    # ------------------------------------------
    async def fetch_global_intelligence(self, query=None, intelligence_type=IntelligenceType.NEWS):
        """
        异步获取全球宏观情报简报，供大模型生成分析上下文。

        优先级:
          1. CryptoPanic API (加密市场新闻)
          2. NewsAPI (宏观政经新闻)
          3. 模拟测试数据兜底

        Args:
            query: 可选的搜索关键词
            intelligence_type: 情报类型 (IntelligenceType.NEWS)

        Returns:
            dict: 格式化的情报数据，绝不抛出异常
        """
        fallback = {
            "cryptopanic": [
                {"title": "Bitcoin ETF inflows continue to grow", "sentiment": "Bullish", "source": "CoinDesk"},
                {"title": "Fed maintains current rate policy", "sentiment": "Neutral", "source": "Bloomberg"},
                {"title": "Ethereum upgrade progress on track", "sentiment": "Bullish", "source": "CryptoSlate"},
            ],
            "source": "simulated",
            "timestamp": datetime.now().isoformat(),
        }

        cryptopanic_key = config.SYSTEM_CONFIG.get("CRYPTOPANIC_API_KEY", "")
        newsapi_key = config.SYSTEM_CONFIG.get("NEWS_API_KEY", "")
        result = {}
        proxy = self.proxy_url if getattr(self, 'proxy_url', None) else None

        # --- 1. CryptoPanic ---
        if cryptopanic_key:
            try:
                url = (
                    f"https://cryptopanic.com/api/v1/posts/"
                    f"?auth_token={cryptopanic_key}&public=true&kind=news"
                    f"&filter=hot&currencies=BTC,ETH,SOL"
                )
                async with httpx.AsyncClient(proxy=proxy, timeout=15) as client:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        data = resp.json()
                        items = []
                        for item in data.get("results", [])[:5]:
                            votes = item.get("votes", {})
                            pos = votes.get("positive", 0)
                            neg = votes.get("negative", 0)
                            sentiment = "Bullish" if pos > neg else "Bearish" if neg > pos else "Neutral"
                            items.append({
                                "title": item.get("title", "N/A"),
                                "sentiment": sentiment,
                                "source": item.get("source", {}).get("title", "Unknown"),
                                "published": item.get("published_at", ""),
                            })
                        if items:
                            result["cryptopanic"] = items
                            logger.info(f"✅ fetch_global_intelligence: CryptoPanic 获取 {len(items)} 条")
            except Exception as e:
                logger.warning(f"⚠️ fetch_global_intelligence CryptoPanic 失败: {e}")

        # --- 2. NewsAPI ---
        if newsapi_key:
            try:
                q = query if query else "crypto OR bitcoin OR ethereum OR federal reserve"
                url = (
                    f"https://newsapi.org/v2/everything"
                    f"?q={q}&sortBy=publishedAt&pageSize=5&apiKey={newsapi_key}"
                )
                async with httpx.AsyncClient(proxy=proxy, timeout=15) as client:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        data = resp.json()
                        items = []
                        for article in data.get("articles", [])[:5]:
                            items.append({
                                "title": article.get("title", "N/A"),
                                "content": (article.get("description") or "")[:200],
                                "source": article.get("source", {}).get("name", "Unknown"),
                                "published": article.get("publishedAt", ""),
                            })
                        if items:
                            result["newsapi"] = items
                            logger.info(f"✅ fetch_global_intelligence: NewsAPI 获取 {len(items)} 条")
            except Exception as e:
                logger.warning(f"⚠️ fetch_global_intelligence NewsAPI 失败: {e}")

        # --- 3. 兜底 ---
        if not result:
            logger.info("📰 fetch_global_intelligence: 使用模拟数据兜底")
            return fallback

        result["source"] = "live"
        result["timestamp"] = datetime.now().isoformat()
        return result

    async def get_cryptopanic_sentiment_async(self, currencies="BTC,ETH,SOL"):
        """
        异步获取 CryptoPanic 情绪数据，供大模型交叉验证新闻。

        Args:
            currencies: 逗号分隔的币种列表

        Returns:
            dict: {sentiment_score, news_items, ...}，绝不抛出异常
        """
        fallback = {
            "sentiment_score": 0,
            "sentiment_label": "Neutral",
            "news_items": [
                {"title": "Market consolidating near key levels", "sentiment": "Neutral",
                 "source": "Simulated", "published": datetime.now().isoformat()},
            ],
            "source": "simulated",
            "timestamp": datetime.now().isoformat(),
        }

        cryptopanic_key = config.SYSTEM_CONFIG.get("CRYPTOPANIC_API_KEY", "")
        if not cryptopanic_key:
            logger.info("📰 get_cryptopanic_sentiment_async: 无 API Key，返回模拟数据")
            return fallback

        try:
            url = (
                f"https://cryptopanic.com/api/v1/posts/"
                f"?auth_token={cryptopanic_key}&public=true&kind=news"
                f"&filter=hot&currencies={currencies}"
            )
            proxy = self.proxy_url if getattr(self, 'proxy_url', None) else None

            async with httpx.AsyncClient(proxy=proxy, timeout=15) as client:
                resp = await client.get(url)
                if resp.status_code != 200:
                    logger.warning(f"⚠️ CryptoPanic API 返回 {resp.status_code}")
                    return fallback

                data = resp.json()
                news_items = []
                total_score = 0

                for item in data.get("results", [])[:10]:
                    votes = item.get("votes", {})
                    pos = votes.get("positive", 0)
                    neg = votes.get("negative", 0)
                    score = pos - neg
                    total_score += score

                    sentiment = "Bullish" if score > 0 else "Bearish" if score < 0 else "Neutral"
                    news_items.append({
                        "title": item.get("title", "N/A"),
                        "sentiment": sentiment,
                        "source": item.get("source", {}).get("title", "Unknown"),
                        "published": item.get("published_at", ""),
                        "score": score,
                    })

                # 综合情绪判定
                if total_score > 3:
                    label = "Bullish"
                elif total_score < -3:
                    label = "Bearish"
                else:
                    label = "Neutral"

                logger.info(f"✅ CryptoPanic 情绪: {label} (score={total_score}, items={len(news_items)})")
                return {
                    "sentiment_score": total_score,
                    "sentiment_label": label,
                    "news_items": news_items,
                    "source": "cryptopanic",
                    "timestamp": datetime.now().isoformat(),
                }

        except httpx.TimeoutException:
            logger.warning("⚠️ get_cryptopanic_sentiment_async: 请求超时")
            return fallback
        except Exception as e:
            logger.warning(f"⚠️ get_cryptopanic_sentiment_async 异常: {e}")
            return fallback

    async def close(self):
        """优雅关闭 (兼容 main.py graceful_shutdown 的 asyncio.run(hub.close()) 调用)"""
        self.stop()
        logger.info("✅ IntelligenceHub 已关闭")


# ==========================================
# 全局实例管理
# ==========================================

_hub_instance = None
_hub_lock = threading.Lock()


def get_intelligence_hub(client: Client = None, proxy_url: str = None):
    """
    获取全局 IntelligenceHub 单例

    - 带 client 参数: 初始化并返回实例
    - 带 proxy_url 参数 (无 client): 创建轻量级实例供 AI 模块调用
    - 不带参数: 返回已有实例 (供 graceful_shutdown 调用)
    """
    global _hub_instance
    with _hub_lock:
        if _hub_instance is None:
            if client is not None:
                _hub_instance = IntelligenceHub(client, proxy_url=proxy_url)
            elif proxy_url is not None:
                # 轻量级模式: 无 Binance client，仅用于异步情报获取
                _hub_instance = IntelligenceHub.__new__(IntelligenceHub)
                _hub_instance.client = None
                _hub_instance.fetcher = None
                _hub_instance.proxy_url = proxy_url
                _hub_instance.running = False
                _hub_instance._thread = None
                _hub_instance._last_scenario = "NORMAL"
                _hub_instance._last_shift_time = 0
                _hub_instance._shift_cooldown = 300
                _hub_instance.trend_entry_blocked = False
                _hub_instance._block_reason = ""
        elif proxy_url and not getattr(_hub_instance, 'proxy_url', None):
            _hub_instance.proxy_url = proxy_url
        return _hub_instance


# ==========================================
# 顶层入口函数 (供 main.py 线程启动)
# ==========================================

def macro_commander_loop(client: Client):
    """
    main.py 中以线程方式调用的入口:
        from intelligence_hub import macro_commander_loop
        t = threading.Thread(target=macro_commander_loop, args=(client_inst,))

    内部创建/获取 IntelligenceHub 单例并运行循环
    """
    hub = get_intelligence_hub(client)
    hub.running = True
    hub.macro_commander_loop_inner()
