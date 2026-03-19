#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
执行质量监控模块 - execution_quality_monitor.py

机构级订单执行质量追踪系统，覆盖：
- 订单成交率 / 拒绝率 / 部分成交率
- 实际滑点 vs 预期滑点对比
- 平均成交延迟（下单到成交的毫秒数）
- Maker/Taker 比例与手续费节省
- 按币种 / 按时段的执行质量分解
- Redis 持久化 + 内存 LRU 双层存储
"""

import time
import threading
import numpy as np
from collections import deque, defaultdict
from datetime import datetime, timedelta
from utils.logger_setup import logger

# ==========================================
# 常量定义
# ==========================================
MAX_METRICS_SIZE = 2000          # 内存中保留最近 2000 笔订单指标
REDIS_KEY = "wjbot:eqm:metrics"  # Redis List key
REDIS_MAX_SIZE = 5000            # Redis 中保留最近 5000 笔
STATS_CACHE_TTL = 30             # 统计缓存有效期（秒）


class ExecutionQualityMonitor:
    """
    机构级订单执行质量监控器

    设计原则：
    1. 零阻塞：record_* 方法全部 O(1)，不阻塞交易主线程
    2. 双层存储：内存 deque（快速查询）+ Redis List（跨重启持久化）
    3. 统计缓存：get_stats() 结果缓存 30 秒，避免高频调用时重复计算
    4. 线程安全：所有读写操作通过 RLock 保护
    """

    def __init__(self):
        self._metrics = deque(maxlen=MAX_METRICS_SIZE)
        self._lock = threading.RLock()
        self._stats_cache = None
        self._stats_cache_time = 0
        self._redis = None

        # 尝试加载 Redis
        try:
            from redis_manager import redis_db
            if redis_db.enabled:
                self._redis = redis_db
                self._load_from_redis()
                logger.info("✅ EQM: Redis 持久化已连接")
        except Exception as e:
            logger.warning(f"⚠️ EQM: Redis 不可用，仅使用内存存储: {e}")

        logger.info(f"✅ ExecutionQualityMonitor 已初始化 (内存={len(self._metrics)} 条)")

    # ------------------------------------------------------------------
    # 数据录入接口
    # ------------------------------------------------------------------

    def record_entry_order(self, symbol: str, side: str, quantity: float,
                           expected_price: float, expected_slippage: float,
                           actual_fill_price: float, order_id: str,
                           order_identity: str = "TAKER",
                           submit_ts: float = 0, fill_ts: float = 0,
                           status: str = "FILLED",
                           commission: float = 0,
                           batch_response: dict = None):
        """
        记录一笔开仓订单的执行质量数据

        Args:
            symbol:             交易对
            side:               BUY / SELL
            quantity:           成交数量
            expected_price:     下单时的参考价格（信号价格）
            expected_slippage:  预期滑点率（来自 check_orderbook_slippage 的 VWAP 估算）
            actual_fill_price:  实际成交均价（来自 API 返回的 avgPrice）
            order_id:           订单 ID
            order_identity:     MAKER / TAKER / SANDBOX
            submit_ts:          下单时间戳（time.time()）
            fill_ts:            成交确认时间戳（time.time()）
            status:             FILLED / REJECTED / PARTIAL / EXPIRED
            commission:         手续费
            batch_response:     原始批量下单响应（可选，用于提取更多细节）
        """
        now = time.time()
        submit_ts = submit_ts or now
        fill_ts = fill_ts or now

        # 计算实际滑点
        actual_slippage = 0.0
        if expected_price > 0 and actual_fill_price > 0:
            actual_slippage = abs(actual_fill_price - expected_price) / expected_price

        # 计算成交延迟
        fill_latency_ms = (fill_ts - submit_ts) * 1000 if fill_ts > submit_ts else 0

        metric = {
            'timestamp': datetime.now().isoformat(),
            'ts': now,
            'symbol': symbol,
            'side': side,
            'quantity': quantity,
            'expected_price': expected_price,
            'actual_fill_price': actual_fill_price,
            'expected_slippage': expected_slippage,
            'actual_slippage': actual_slippage,
            'slippage_diff': actual_slippage - expected_slippage,  # 正=比预期差
            'fill_latency_ms': fill_latency_ms,
            'order_id': str(order_id),
            'order_identity': order_identity,
            'status': status,
            'commission': commission,
            'order_type': 'ENTRY',
        }

        self._append_metric(metric)

    def record_exit_order(self, symbol: str, side: str, quantity: float,
                          exit_price: float, order_id: str,
                          status: str = "FILLED",
                          order_identity: str = "TAKER",
                          commission: float = 0,
                          exit_reason: str = "SIGNAL"):
        """
        记录一笔平仓订单的执行质量数据
        """
        now = time.time()
        metric = {
            'timestamp': datetime.now().isoformat(),
            'ts': now,
            'symbol': symbol,
            'side': side,
            'quantity': quantity,
            'expected_price': exit_price,
            'actual_fill_price': exit_price,
            'expected_slippage': 0,
            'actual_slippage': 0,
            'slippage_diff': 0,
            'fill_latency_ms': 0,
            'order_id': str(order_id),
            'order_identity': order_identity,
            'status': status,
            'commission': commission,
            'order_type': 'EXIT',
            'exit_reason': exit_reason,
        }
        self._append_metric(metric)

    def record_rejection(self, symbol: str, side: str, quantity: float,
                         reason: str, order_type: str = "ENTRY"):
        """
        记录一笔被拒绝的订单（滑点超限 / API 拒绝 / 风控拦截）
        """
        now = time.time()
        metric = {
            'timestamp': datetime.now().isoformat(),
            'ts': now,
            'symbol': symbol,
            'side': side,
            'quantity': quantity,
            'expected_price': 0,
            'actual_fill_price': 0,
            'expected_slippage': 0,
            'actual_slippage': 0,
            'slippage_diff': 0,
            'fill_latency_ms': 0,
            'order_id': '',
            'order_identity': 'REJECTED',
            'status': 'REJECTED',
            'commission': 0,
            'order_type': order_type,
            'reject_reason': reason,
        }
        self._append_metric(metric)

    # ------------------------------------------------------------------
    # 统计查询接口
    # ------------------------------------------------------------------

    def get_stats(self, lookback_hours: float = 24, symbol: str = None) -> dict:
        """
        获取执行质量统计（带缓存）

        Args:
            lookback_hours: 回溯时间窗口（小时），默认 24h
            symbol:         按币种过滤（None = 全部）

        Returns:
            dict: 完整的执行质量统计
        """
        cache_key = f"{lookback_hours}_{symbol}"
        now = time.time()

        # 缓存命中
        if (self._stats_cache is not None
                and self._stats_cache.get('_key') == cache_key
                and now - self._stats_cache_time < STATS_CACHE_TTL):
            return self._stats_cache

        # 重新计算
        stats = self._compute_stats(lookback_hours, symbol)
        stats['_key'] = cache_key
        self._stats_cache = stats
        self._stats_cache_time = now
        return stats

    def get_symbol_breakdown(self, lookback_hours: float = 24) -> dict:
        """
        按币种分解执行质量

        Returns:
            dict: {symbol: stats_dict}
        """
        with self._lock:
            cutoff = time.time() - lookback_hours * 3600
            symbols = set(m['symbol'] for m in self._metrics if m['ts'] >= cutoff)

        breakdown = {}
        for sym in symbols:
            breakdown[sym] = self._compute_stats(lookback_hours, sym)
        return breakdown

    def get_hourly_breakdown(self, lookback_hours: float = 24) -> list:
        """
        按小时分解执行质量（用于趋势分析）

        Returns:
            list: [{hour: str, fill_rate: float, avg_slippage: float, count: int}, ...]
        """
        with self._lock:
            cutoff = time.time() - lookback_hours * 3600
            filtered = [m for m in self._metrics if m['ts'] >= cutoff]

        hourly = defaultdict(list)
        for m in filtered:
            hour_key = m['timestamp'][:13]  # "2026-03-13T06"
            hourly[hour_key].append(m)

        result = []
        for hour, metrics in sorted(hourly.items()):
            total = len(metrics)
            filled = sum(1 for m in metrics if m['status'] == 'FILLED')
            slippages = [m['actual_slippage'] for m in metrics
                         if m['status'] == 'FILLED' and m['actual_slippage'] > 0]
            result.append({
                'hour': hour,
                'count': total,
                'fill_rate': filled / total if total > 0 else 0,
                'avg_slippage': float(np.mean(slippages)) if slippages else 0,
            })
        return result

    def format_report(self, lookback_hours: float = 24) -> str:
        """
        生成 Telegram HTML 格式的执行质量报告

        Returns:
            str: HTML 格式报告文本
        """
        s = self.get_stats(lookback_hours)
        if s['total_orders'] == 0:
            return "📊 <b>[执行质量报告]</b>\n\n暂无订单数据"

        # 评级
        grade = self._calculate_grade(s)

        report = (
            f"📊 <b>[执行质量报告 - {lookback_hours:.0f}h]</b>\n"
            f"评级: <b>{grade}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"<b>📋 订单统计</b>\n"
            f"├ 总订单数: <code>{s['total_orders']}</code>\n"
            f"├ 成交率: <code>{s['fill_rate']*100:.1f}%</code>\n"
            f"├ 拒绝率: <code>{s['reject_rate']*100:.1f}%</code>\n"
            f"├ 部分成交率: <code>{s['partial_rate']*100:.1f}%</code>\n"
            f"└ 过期率: <code>{s['expired_rate']*100:.1f}%</code>\n\n"
            f"<b>📉 滑点分析</b>\n"
            f"├ 平均实际滑点: <code>{s['avg_actual_slippage']*100:.3f}%</code>\n"
            f"├ 平均预期滑点: <code>{s['avg_expected_slippage']*100:.3f}%</code>\n"
            f"├ 滑点偏差(实际-预期): <code>{s['avg_slippage_diff']*100:+.3f}%</code>\n"
            f"├ 最大滑点: <code>{s['max_slippage']*100:.3f}%</code>\n"
            f"└ P95 滑点: <code>{s['p95_slippage']*100:.3f}%</code>\n\n"
            f"<b>⏱️ 成交延迟</b>\n"
            f"├ 平均延迟: <code>{s['avg_latency_ms']:.0f}ms</code>\n"
            f"├ P95 延迟: <code>{s['p95_latency_ms']:.0f}ms</code>\n"
            f"└ 最大延迟: <code>{s['max_latency_ms']:.0f}ms</code>\n\n"
            f"<b>💎 Maker/Taker 分析</b>\n"
            f"├ Maker 比例: <code>{s['maker_ratio']*100:.1f}%</code>\n"
            f"├ Taker 比例: <code>{s['taker_ratio']*100:.1f}%</code>\n"
            f"└ 手续费总计: <code>${s['total_commission']:.2f}</code>\n\n"
            f"<b>📈 开仓 vs 平仓</b>\n"
            f"├ 开仓订单: <code>{s['entry_count']}</code>\n"
            f"└ 平仓订单: <code>{s['exit_count']}</code>"
        )
        return report

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _append_metric(self, metric: dict):
        """线程安全地追加一条指标记录"""
        with self._lock:
            self._metrics.append(metric)
            # 清除统计缓存
            self._stats_cache = None

        # 异步写入 Redis（不阻塞主线程）
        if self._redis:
            try:
                self._redis.append_to_list(REDIS_KEY, metric, max_length=REDIS_MAX_SIZE)
            except Exception as e:
                logger.debug(f"EQM Redis 写入失败（不影响主流程）: {e}")

    def _load_from_redis(self):
        """启动时从 Redis 恢复历史数据"""
        if not self._redis:
            return
        try:
            data = self._redis.get_list(REDIS_KEY, start=0, end=MAX_METRICS_SIZE - 1)
            if data:
                with self._lock:
                    for item in data:
                        if isinstance(item, dict):
                            self._metrics.append(item)
                logger.info(f"✅ EQM: 从 Redis 恢复 {len(data)} 条历史指标")
        except Exception as e:
            logger.warning(f"⚠️ EQM: Redis 恢复失败: {e}")

    def _compute_stats(self, lookback_hours: float, symbol: str = None) -> dict:
        """核心统计计算"""
        with self._lock:
            cutoff = time.time() - lookback_hours * 3600
            filtered = [m for m in self._metrics if m['ts'] >= cutoff]
            if symbol:
                filtered = [m for m in filtered if m['symbol'] == symbol]

        total = len(filtered)
        if total == 0:
            return self._empty_stats()

        # 状态分布
        filled = [m for m in filtered if m['status'] == 'FILLED']
        rejected = [m for m in filtered if m['status'] == 'REJECTED']
        partial = [m for m in filtered if m['status'] == 'PARTIAL']
        expired = [m for m in filtered if m['status'] == 'EXPIRED']

        # 滑点统计（仅已成交订单）
        actual_slippages = [m['actual_slippage'] for m in filled if m.get('actual_slippage', 0) > 0]
        expected_slippages = [m['expected_slippage'] for m in filled if m.get('expected_slippage', 0) > 0]
        slippage_diffs = [m['slippage_diff'] for m in filled if m.get('slippage_diff') is not None]

        # 延迟统计
        latencies = [m['fill_latency_ms'] for m in filled if m.get('fill_latency_ms', 0) > 0]

        # Maker/Taker 统计
        makers = [m for m in filled if m.get('order_identity') == 'MAKER']
        takers = [m for m in filled if m.get('order_identity') == 'TAKER']

        # 开仓/平仓统计
        entries = [m for m in filtered if m.get('order_type') == 'ENTRY']
        exits = [m for m in filtered if m.get('order_type') == 'EXIT']

        # 手续费
        total_commission = sum(m.get('commission', 0) for m in filtered)

        filled_count = len(filled)

        return {
            'total_orders': total,
            'filled_count': filled_count,
            'rejected_count': len(rejected),
            'partial_count': len(partial),
            'expired_count': len(expired),
            'fill_rate': filled_count / total,
            'reject_rate': len(rejected) / total,
            'partial_rate': len(partial) / total,
            'expired_rate': len(expired) / total,
            # 滑点
            'avg_actual_slippage': float(np.mean(actual_slippages)) if actual_slippages else 0,
            'avg_expected_slippage': float(np.mean(expected_slippages)) if expected_slippages else 0,
            'avg_slippage_diff': float(np.mean(slippage_diffs)) if slippage_diffs else 0,
            'max_slippage': float(np.max(actual_slippages)) if actual_slippages else 0,
            'p95_slippage': float(np.percentile(actual_slippages, 95)) if len(actual_slippages) >= 5 else 0,
            'median_slippage': float(np.median(actual_slippages)) if actual_slippages else 0,
            # 延迟
            'avg_latency_ms': float(np.mean(latencies)) if latencies else 0,
            'p95_latency_ms': float(np.percentile(latencies, 95)) if len(latencies) >= 5 else 0,
            'max_latency_ms': float(np.max(latencies)) if latencies else 0,
            'median_latency_ms': float(np.median(latencies)) if latencies else 0,
            # Maker/Taker
            'maker_count': len(makers),
            'taker_count': len(takers),
            'maker_ratio': len(makers) / filled_count if filled_count > 0 else 0,
            'taker_ratio': len(takers) / filled_count if filled_count > 0 else 0,
            # 开仓/平仓
            'entry_count': len(entries),
            'exit_count': len(exits),
            # 手续费
            'total_commission': total_commission,
            # 元数据
            'lookback_hours': lookback_hours,
            'symbol_filter': symbol,
            'computed_at': datetime.now().isoformat(),
        }

    def _empty_stats(self) -> dict:
        """返回空统计结构"""
        return {
            'total_orders': 0, 'filled_count': 0, 'rejected_count': 0,
            'partial_count': 0, 'expired_count': 0,
            'fill_rate': 0, 'reject_rate': 0, 'partial_rate': 0, 'expired_rate': 0,
            'avg_actual_slippage': 0, 'avg_expected_slippage': 0, 'avg_slippage_diff': 0,
            'max_slippage': 0, 'p95_slippage': 0, 'median_slippage': 0,
            'avg_latency_ms': 0, 'p95_latency_ms': 0, 'max_latency_ms': 0, 'median_latency_ms': 0,
            'maker_count': 0, 'taker_count': 0, 'maker_ratio': 0, 'taker_ratio': 0,
            'entry_count': 0, 'exit_count': 0, 'total_commission': 0,
            'lookback_hours': 0, 'symbol_filter': None,
            'computed_at': datetime.now().isoformat(),
        }

    @staticmethod
    def _calculate_grade(stats: dict) -> str:
        """根据执行质量统计计算综合评级"""
        score = 0

        # 成交率 (40分)
        fr = stats.get('fill_rate', 0)
        if fr >= 0.99:
            score += 40
        elif fr >= 0.95:
            score += 35
        elif fr >= 0.90:
            score += 25
        else:
            score += max(0, int(fr * 40))

        # 平均滑点 (30分) — 越低越好
        avg_slip = stats.get('avg_actual_slippage', 0)
        if avg_slip <= 0.0005:
            score += 30
        elif avg_slip <= 0.001:
            score += 25
        elif avg_slip <= 0.002:
            score += 15
        else:
            score += 5

        # 成交延迟 (15分)
        avg_lat = stats.get('avg_latency_ms', 0)
        if avg_lat <= 100:
            score += 15
        elif avg_lat <= 300:
            score += 10
        elif avg_lat <= 500:
            score += 5

        # Maker 比例 (15分)
        mr = stats.get('maker_ratio', 0)
        if mr >= 0.5:
            score += 15
        elif mr >= 0.3:
            score += 10
        elif mr >= 0.1:
            score += 5

        if score >= 90:
            return "⭐ S (卓越)"
        elif score >= 80:
            return "🟢 A (优秀)"
        elif score >= 65:
            return "🟡 B (良好)"
        elif score >= 50:
            return "🟠 C (一般)"
        else:
            return "🔴 D (需改进)"


# ==========================================
# 全局单例
# ==========================================
_eqm_instance = None
_eqm_lock = threading.Lock()


def get_eqm() -> ExecutionQualityMonitor:
    """获取 ExecutionQualityMonitor 全局单例"""
    global _eqm_instance
    if _eqm_instance is None:
        with _eqm_lock:
            if _eqm_instance is None:
                _eqm_instance = ExecutionQualityMonitor()
    return _eqm_instance


print("✅ 执行质量监控模块已加载 (ExecutionQualityMonitor)")
