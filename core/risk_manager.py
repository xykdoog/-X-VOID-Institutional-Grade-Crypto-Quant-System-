#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全局投资组合风控模块 - risk_manager.py
负责：最大回撤熔断、并发头寸限制、同向敞口管控、资产相关性风控
"""

import json
import os
import time
import threading
from datetime import datetime

from utils.logger_setup import logger

# 🔥 V8.0 Safety Fix: 记录模块加载时间，用于启动期间防止 Ghost Meltdown
_MODULE_BOOT_TIME = time.monotonic()

# ==========================================
# 风控配置默认值（可由 SYSTEM_CONFIG 覆盖）
# ==========================================
DEFAULT_RISK_CONFIG = {
    "MAX_DRAWDOWN_PCT":     0.10,   # 全局最大回撤阈值 10%
    "MAX_TOTAL_POSITIONS":  3,      # 全局最大并发头寸数
    "MAX_SAME_DIRECTION":   2,      # 同向最大头寸数 (多/空各自最多 2 个)
    "NET_EXPOSURE_THRESHOLD": 0.5,  # 净敞口熔断阈值 50%
    "HWM_FILE":             os.path.join(os.path.dirname(__file__), "risk_hwm.json"),  # 历史最高净值持久化文件
}


class PortfolioRiskManager:
    """
    投资组合级全局风控门卫
    所有开仓动作在执行前必须通过此类的检查
    
    🔥 V8.0 解耦：Real 和 Sandbox 使用独立的高水位线和熔断逻辑
    """

    def __init__(self, system_config: dict):
        # 使用可重入锁 (RLock) 防止嵌套调用死锁
        self._lock = threading.RLock()
        # 🔥 保存 system_config 引用，用于运行时读取 RUNNING_MODE
        self._system_config = system_config
        self.max_drawdown_pct    = system_config.get("MAX_DRAWDOWN_PCT",    DEFAULT_RISK_CONFIG["MAX_DRAWDOWN_PCT"])
        self.max_total_positions = system_config.get("MAX_TOTAL_POSITIONS", DEFAULT_RISK_CONFIG["MAX_TOTAL_POSITIONS"])
        self.max_same_direction  = system_config.get("MAX_SAME_DIRECTION",  DEFAULT_RISK_CONFIG["MAX_SAME_DIRECTION"])
        self.net_exposure_threshold = system_config.get("NET_EXPOSURE_THRESHOLD", DEFAULT_RISK_CONFIG["NET_EXPOSURE_THRESHOLD"])
        self.hwm_file            = system_config.get("HWM_FILE",            DEFAULT_RISK_CONFIG["HWM_FILE"])

        # 🔥 V8.0: 分离 Real / Sandbox 高水位线（从 SYSTEM_CONFIG 恢复）
        self.real_high_water_mark = float(system_config.get("REAL_HIGH_WATER_MARK", 0.0))
        self.sim_high_water_mark  = float(system_config.get("SIM_HIGH_WATER_MARK", 0.0))

        # 兼容：从旧版 risk_hwm.json 迁移（仅首次）
        legacy_hwm = self._load_hwm()
        if legacy_hwm > 0 and self.real_high_water_mark <= 0:
            self.real_high_water_mark = legacy_hwm
            logger.info(f"📦 [风控] 从旧版 HWM 文件迁移 Real HWM: {legacy_hwm:.2f}")

        logger.info(
            f"🛡️ [风控] PortfolioRiskManager 已初始化 | 最大回撤:{self.max_drawdown_pct*100:.0f}% | "
            f"最大头寸:{self.max_total_positions} | 同向上限:{self.max_same_direction} | "
            f"净敞口阈值:{self.net_exposure_threshold*100:.0f}% | "
            f"Real HWM:{self.real_high_water_mark:.2f} | Sim HWM:{self.sim_high_water_mark:.2f}"
        )

    # ------------------------------------------------------------------
    # 内部：历史最高净值持久化
    # ------------------------------------------------------------------
    def _load_hwm(self) -> float:
        """从文件加载历史最高净值，文件不存在则返回 0"""
        try:
            if os.path.exists(self.hwm_file):
                with open(self.hwm_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return float(data.get("high_water_mark", 0.0))
        except Exception as e:
            logger.warning(f"⚠️ [风控] 加载历史最高净值失败: {e}，将从 0 开始", exc_info=True)
        return 0.0

    def _save_hwm(self):
        """
        🔥 V8.0: 持久化双轨高水位线到 SYSTEM_CONFIG（通过 save_data 落盘到 Redis）
        同时保留旧版 JSON 文件兼容
        """
        try:
            # 写入 SYSTEM_CONFIG（主路径：Redis 持久化）
            from config import save_data
            self._system_config["REAL_HIGH_WATER_MARK"] = self.real_high_water_mark
            self._system_config["SIM_HIGH_WATER_MARK"] = self.sim_high_water_mark
            save_data()
            
            # 兼容旧版 JSON 文件（写入当前模式的 HWM）
            running_mode = self._system_config.get("RUNNING_MODE", "SANDBOX")
            current_hwm = self.real_high_water_mark if running_mode == "REAL" else self.sim_high_water_mark
            data = {
                "high_water_mark": current_hwm,
                "real_high_water_mark": self.real_high_water_mark,
                "sim_high_water_mark": self.sim_high_water_mark,
                "updated_at": datetime.now().isoformat()
            }
            tmp_file = self.hwm_file + ".tmp"
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_file, self.hwm_file)
        except Exception as e:
            logger.error(f"⚠️ [风控] 持久化高水位线失败: {e}", exc_info=True)

    def _get_current_hwm(self) -> float:
        """根据当前 RUNNING_MODE 返回对应的高水位线"""
        running_mode = self._system_config.get("RUNNING_MODE", "SANDBOX")
        if running_mode == "REAL":
            return self.real_high_water_mark
        else:
            return self.sim_high_water_mark

    def _set_current_hwm(self, value: float):
        """根据当前 RUNNING_MODE 设置对应的高水位线"""
        running_mode = self._system_config.get("RUNNING_MODE", "SANDBOX")
        if running_mode == "REAL":
            self.real_high_water_mark = value
        else:
            self.sim_high_water_mark = value

    @property
    def high_water_mark(self) -> float:
        """兼容属性：返回当前模式的高水位线"""
        return self._get_current_hwm()

    @high_water_mark.setter
    def high_water_mark(self, value: float):
        """兼容属性：设置当前模式的高水位线"""
        self._set_current_hwm(value)

    def update_high_water_mark(self, current_equity: float):
        """
        🔥 V8.0: 用当前净值更新对应模式的高水位线（只涨不跌）
        """
        with self._lock:
            running_mode = self._system_config.get("RUNNING_MODE", "SANDBOX")
            current_hwm = self._get_current_hwm()
            
            if current_equity > current_hwm:
                old_hwm = current_hwm
                self._set_current_hwm(current_equity)
                self._save_hwm()
                if old_hwm > 0:
                    mode_label = "实盘" if running_mode == "REAL" else "沙盒"
                    logger.info(f"📈 [风控] [{mode_label}] 高水位线更新: {old_hwm:.2f} → {current_equity:.2f}")

    # ------------------------------------------------------------------
    # 核心检查 1：全局最大回撤熔断（🔥 V8.0 双轨解耦版）
    # ------------------------------------------------------------------
    def check_global_drawdown(self, current_equity: float) -> bool:
        """
        🔥 V8.0 解耦版：根据 RUNNING_MODE 独立计算回撤并独立熔断。
        
        - REAL 模式：比较 current_equity (真实账户余额) vs REAL_HIGH_WATER_MARK
        - SANDBOX 模式：比较 current_equity (SIM_CURRENT_BALANCE) vs SIM_HIGH_WATER_MARK
        
        熔断时只暂停对应模式的交易，不影响另一个模式。

        Args:
            current_equity: 当前账户总净值（USDT）
                           REAL 模式传入 futures_account totalMarginBalance
                           SANDBOX 模式传入 SIM_CURRENT_BALANCE

        Returns:
            True  → 净值正常，允许开仓
            False → 触发熔断，必须停止当前模式的新开仓
        """
        with self._lock:
            running_mode = self._system_config.get("RUNNING_MODE", "SANDBOX")
            mode_label = "实盘" if running_mode == "REAL" else "沙盒"
            hwm = self._get_current_hwm()

            # 🔥 V8.0 Safety Fix: 启动前5秒内，如果 SIM_CURRENT_BALANCE == 0，
            # 跳过回撤检查，防止数据尚未加载时触发 "Ghost Meltdown"
            if current_equity == 0 and (time.monotonic() - _MODULE_BOOT_TIME) < 5.0:
                logger.info(f"🛡️ [风控] [{mode_label}] 启动保护期：余额为0，跳过回撤检查（防止Ghost Meltdown）")
                return True

            # 首次运行高水位为 0，用当前净值初始化
            if hwm <= 0:
                self.update_high_water_mark(current_equity)
                logger.info(f"📊 [风控] [{mode_label}] 初始化高水位线: ${current_equity:.2f}")
                return True

            drawdown = (hwm - current_equity) / hwm

            if drawdown >= self.max_drawdown_pct:
                alert_message = (
                    f"🔴 <b>[风控熔断] {mode_label}最大回撤触发</b>\n"
                    f"模式: <b>{mode_label}</b>\n"
                    f"高水位: <b>${hwm:.2f}</b>\n"
                    f"当前净值: <b>${current_equity:.2f}</b>\n"
                    f"回撤幅度: <b>{drawdown*100:.2f}%</b>（阈值 {self.max_drawdown_pct*100:.0f}%）\n"
                    f"🛑 已暂停<b>{mode_label}</b>新开仓，另一模式不受影响。"
                )
                
                logger.warning(
                    f"🚨 [风控] [{mode_label}] 最大回撤熔断！\n"
                    f"   高水位:   ${hwm:.2f}\n"
                    f"   当前净值: ${current_equity:.2f}\n"
                    f"   回撤:     {drawdown*100:.2f}% ≥ 阈值 {self.max_drawdown_pct*100:.0f}%\n"
                    f"   → 仅暂停{mode_label}交易"
                )
                should_send_alert = True
            else:
                self.update_high_water_mark(current_equity)
                should_send_alert = False
                alert_message = None

        # 锁外执行网络 I/O
        if should_send_alert:
            try:
                from utils import send_tg_alert
                send_tg_alert(alert_message)
            except Exception as e:
                logger.error(f"⚠️ [风控] 发送熔断告警失败: {e}", exc_info=True)
            return False

        return True

    def initialize_hwm_from_balance(self, client):
        """
        🔥 V8.0: 系统启动时，如果 REAL_HIGH_WATER_MARK 为 0，
        从币安 API 获取真实余额初始化，而非使用硬编码的 10,000。
        SIM_HIGH_WATER_MARK 为 0 时，从 SIM_CURRENT_BALANCE 初始化。
        """
        with self._lock:
            # 初始化 Real HWM
            if self.real_high_water_mark <= 0:
                if client:
                    try:
                        acc = client.futures_account()
                        real_balance = float(acc.get('totalMarginBalance', 0))
                        if real_balance > 0:
                            self.real_high_water_mark = real_balance
                            logger.info(f"📊 [风控] Real HWM 从 API 初始化: ${real_balance:.2f}")
                        else:
                            # API 返回 0，使用 BENCHMARK_CASH 兜底
                            fallback = float(self._system_config.get("BENCHMARK_CASH", 0))
                            if fallback > 0:
                                self.real_high_water_mark = fallback
                                logger.info(f"📊 [风控] Real HWM 从 BENCHMARK_CASH 初始化: ${fallback:.2f}")
                    except Exception as e:
                        logger.warning(f"⚠️ [风控] 从 API 初始化 Real HWM 失败: {e}")
                        fallback = float(self._system_config.get("BENCHMARK_CASH", 0))
                        if fallback > 0:
                            self.real_high_water_mark = fallback

            # 初始化 Sim HWM
            if self.sim_high_water_mark <= 0:
                sim_balance = float(self._system_config.get("SIM_CURRENT_BALANCE", 0))
                if sim_balance > 0:
                    self.sim_high_water_mark = sim_balance
                else:
                    sim_initial = float(self._system_config.get("SANDBOX_INITIAL_BALANCE", 10000.0))
                    self.sim_high_water_mark = sim_initial
                logger.info(f"📊 [风控] Sim HWM 初始化: ${self.sim_high_water_mark:.2f}")

            # 持久化
            self._save_hwm()

    # ------------------------------------------------------------------
    # 核心检查 2：最大并发头寸 + 同向敞口限制
    # ------------------------------------------------------------------
    def can_open_new_position(self, active_positions: dict, direction: str,
                              symbol: str = None, client=None) -> tuple:
        """
        检查是否允许开新仓（机构级多子仓位列表适配 + 资产相关性风控）。

        Args:
            active_positions: 当前活跃持仓字典（来自 ACTIVE_POSITIONS）
                             结构: Dict[str, List[dict]] 例如 {"BTCUSDT_LONG": [{...}, {...}]}
            direction:        准备开仓方向，'LONG' 或 'SHORT'
            symbol:           准备开仓的交易对（用于相关性检查）
            client:           Binance客户端（用于相关性检查）

        Returns:
            (allowed: bool, reason: str)
            当 reason 以 "CORR_REDUCE:" 开头时，表示建议降低仓位50%而非完全拒绝
        """
        with self._lock:
            # 防御性拷贝，避免迭代时其他线程修改字典导致 RuntimeError
            positions_snapshot = active_positions.copy()
            
            # 🔥 机构级修复：统计总单量（兼容 list 和 dict）
            total = sum(len(v) if isinstance(v, list) else 1 for v in positions_snapshot.values())
            
            # 🔥 机构级修复：迭代列表内子订单的 type 字段统计多空方向
            long_count = 0
            short_count = 0
            
            for key, positions_list in positions_snapshot.items():
                # 强制列表化处理
                if not isinstance(positions_list, list):
                    positions_list = [positions_list]
                
                # 优先通过 Key 后缀判断（最可靠）
                if key.endswith("_LONG"):
                    long_count += len(positions_list)
                elif key.endswith("_SHORT"):
                    short_count += len(positions_list)
                else:
                    # 兜底：遍历子订单的 type 字段（彻底修复 AttributeError）
                    for pos in positions_list:
                        if isinstance(pos, dict):
                            pos_type = pos.get("type", "").upper()
                            if pos_type == "LONG":
                                long_count += 1
                            elif pos_type == "SHORT":
                                short_count += 1

            # ---- 检查 1：全局并发上限 ----
            if total >= self.max_total_positions:
                reason = (
                    f"全局并发头寸已达上限 [{total}/{self.max_total_positions}]，"
                    f"拒绝新开仓 ({direction})"
                )
                logger.info(f"🛡️ [风控] {reason}")
                return False, reason

            # ---- 检查 2：同向敞口上限 ----
            direction_upper = direction.upper()
            same_dir_count = long_count if direction_upper == "LONG" else short_count

            if same_dir_count >= self.max_same_direction:
                reason = (
                    f"同向 ({direction}) 头寸已达上限 [{same_dir_count}/{self.max_same_direction}]，"
                    f"拒绝叠加同向风险"
                )
                logger.info(f"🛡️ [风控] {reason}")
                return False, reason

            # ---- 检查 3：净敞口熔断 (Net_Delta Exposure Circuit Breaker) ----
            # 公式: Net_Delta = (多单名义价值 - 空单名义价值) / 账户总权益
            # 如果 Net_Delta > 阈值 且新信号做多，或 Net_Delta < -阈值 且新信号做空，拒绝
            try:
                long_notional = 0.0
                short_notional = 0.0

                for key, positions_list in positions_snapshot.items():
                    if not isinstance(positions_list, list):
                        positions_list = [positions_list]
                    for pos in positions_list:
                        if not isinstance(pos, dict):
                            continue
                        entry = float(pos.get("entry", 0))
                        qty = float(pos.get("qty", 0))
                        notional = entry * qty

                        # 判断方向：优先 key 后缀，兜底 type 字段
                        if key.endswith("_LONG"):
                            long_notional += notional
                        elif key.endswith("_SHORT"):
                            short_notional += notional
                        else:
                            pos_type = pos.get("type", "").upper()
                            if pos_type == "LONG":
                                long_notional += notional
                            elif pos_type == "SHORT":
                                short_notional += notional

                # 获取账户总权益（从 system_config 读取，无需 API 调用）
                running_mode = self._system_config.get("RUNNING_MODE", "SANDBOX")
                if running_mode == "REAL":
                    total_equity = float(self._system_config.get("BENCHMARK_CASH", 0))
                else:
                    total_equity = float(self._system_config.get("SIM_CURRENT_BALANCE", 0))
                    if total_equity <= 0:
                        total_equity = float(self._system_config.get("SANDBOX_INITIAL_BALANCE", 10000.0))

                if total_equity > 0:
                    net_delta = (long_notional - short_notional) / total_equity

                    if net_delta > self.net_exposure_threshold and direction_upper == "LONG":
                        reason = (
                            f"净敞口过大 (Net_Delta={net_delta:.2f})，"
                            f"超过阈值 {self.net_exposure_threshold}，拒绝反向敞口扩张 (做多)"
                        )
                        logger.info(f"🛡️ [风控拦截] 净敞口过大 ({net_delta:.2f})，拒绝反向敞口扩张")
                        return False, reason

                    if net_delta < -self.net_exposure_threshold and direction_upper == "SHORT":
                        reason = (
                            f"净敞口过大 (Net_Delta={net_delta:.2f})，"
                            f"超过阈值 -{self.net_exposure_threshold}，拒绝反向敞口扩张 (做空)"
                        )
                        logger.info(f"🛡️ [风控拦截] 净敞口过大 ({net_delta:.2f})，拒绝反向敞口扩张")
                        return False, reason

                    logger.debug(
                        f"📊 [风控] Net_Delta={net_delta:.2f} | "
                        f"多单名义=${long_notional:.2f} | 空单名义=${short_notional:.2f} | "
                        f"权益=${total_equity:.2f}"
                    )
            except Exception as e:
                logger.warning(f"⚠️ [风控] 净敞口计算异常（不阻塞开仓）: {e}")

        # ---- 检查 4：资产相关性风控（锁外执行，避免API调用阻塞） ----
        if symbol and client and total > 0:
            try:
                corr_result = self._check_correlation(client, symbol, positions_snapshot)
                if not corr_result['allowed']:
                    # 根据相关性强度决定是降仓还是拒绝
                    max_corr = abs(corr_result['max_correlation'])
                    corr_sym = corr_result['correlated_symbol']
                    
                    if max_corr >= 0.95:
                        # ρ >= 0.95：极高相关，直接拒绝开仓
                        reason = (
                            f"资产相关性极高 [{symbol} vs {corr_sym}, ρ={max_corr:.4f}]，"
                            f"直接拒绝开仓以防止风险耦合"
                        )
                        logger.warning(f"🚨 [风控] {reason}")
                        return False, reason
                    else:
                        # 0.85 <= ρ < 0.95：高相关，降低RISK_RATIO 50%
                        reason = (
                            f"CORR_REDUCE:资产相关性较高 [{symbol} vs {corr_sym}, ρ={max_corr:.4f}]，"
                            f"RISK_RATIO 将降低50%"
                        )
                        logger.warning(f"⚠️ [风控] {reason}")
                        return True, reason
            except Exception as e:
                logger.error(f"⚠️ [风控] 相关性检查异常（不阻塞开仓）: {e}")

        # ---- 通过 ----
        summary = (
            f"总持仓 {total}/{self.max_total_positions} | "
            f"多 {long_count} | 空 {short_count} | 准备开 {direction}"
        )
        logger.info(f"✅ [风控] 开仓申请通过 | {summary}")
        return True, "OK"

    # ------------------------------------------------------------------
    # 核心检查 3：资产相关性风控
    # ------------------------------------------------------------------
    def _check_correlation(self, client, new_symbol: str, positions_snapshot: dict) -> dict:
        """
        检查新开仓币种与现有持仓的相关性（内部方法）。
        
        Args:
            client: Binance客户端
            new_symbol: 准备开仓的交易对
            positions_snapshot: 当前持仓快照
        
        Returns:
            dict: {'allowed': bool, 'max_correlation': float, 'correlated_symbol': str, 'message': str}
        """
        from correlation_engine import check_portfolio_correlation
        return check_portfolio_correlation(client, new_symbol, positions_snapshot)

    # ------------------------------------------------------------------
    # 状态查询（日志/监控用）
    # ------------------------------------------------------------------
    def status_report(self, active_positions: dict, current_equity: float) -> str:
        """返回当前风控状态的可读摘要字符串（机构级多子仓位适配）"""
        # 防御性拷贝
        positions_snapshot = active_positions.copy()
        
        # 🔥 机构级修复：统计总单量（必须使用 sum(len(v) for v in values())）
        total = sum(len(v) for v in positions_snapshot.values() if isinstance(v, list))
        
        # 🔥 机构级修复：迭代子订单 type 字段统计多空单量
        long_count = 0
        short_count = 0
        for key, positions_list in positions_snapshot.items():
            if not isinstance(positions_list, list):
                positions_list = [positions_list]
            
            # 优先通过 Key 后缀判断
            if key.endswith("_LONG"):
                long_count += len(positions_list)
            elif key.endswith("_SHORT"):
                short_count += len(positions_list)
            else:
                # 兜底：遍历子订单的 type 字段
                for pos in positions_list:
                    if isinstance(pos, dict):
                        pos_type = pos.get("type", "").upper()
                        if pos_type == "LONG":
                            long_count += 1
                        elif pos_type == "SHORT":
                            short_count += 1
        
        drawdown = ((self.high_water_mark - current_equity) / self.high_water_mark * 100
                    if self.high_water_mark > 0 else 0.0)
        return (
            f"[风控状态] 净值=${current_equity:.2f} | HWM=${self.high_water_mark:.2f} | "
            f"回撤={drawdown:.2f}% | 持仓={total}(多{long_count}/空{short_count})"
        )


# ==========================================
# 模块级单例（在 trading_engine 中引用）
# ==========================================
_risk_manager_instance: PortfolioRiskManager = None


def get_risk_manager(system_config: dict = None) -> PortfolioRiskManager:
    """
    获取全局风控管理器单例。
    首次调用时传入 system_config 完成初始化；后续调用无需参数。
    """
    global _risk_manager_instance
    if _risk_manager_instance is None:
        if system_config is None:
            raise RuntimeError("首次调用 get_risk_manager() 必须提供 system_config 参数")
        _risk_manager_instance = PortfolioRiskManager(system_config)
    return _risk_manager_instance


logger.info("✅ 风控模块已加载 (risk_manager.py)")
