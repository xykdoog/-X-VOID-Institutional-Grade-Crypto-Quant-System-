#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WJ-BOT Web Dashboard - 完整移植 Telegram Bot 全部功能
对接 config.SYSTEM_CONFIG 共享内存，实现网页端实时控制
"""

import uvicorn
import time
import json
import os
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import config
from config import (
    SYSTEM_CONFIG, ACTIVE_POSITIONS, STRATEGY_PRESETS,
    SENTRY_CONFIG, SENTRY_INTERVAL_OPTIONS, TRADE_HISTORY,
    save_data, state_lock, positions_lock,
    apply_strategy_preset, save_sentry_watchlist
)
from logger_setup import logger

app = FastAPI(title="WJ-BOT Command Center")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ==========================================
# 数据模型
# ==========================================
class ParamUpdate(BaseModel):
    key: str
    value: str

class SymbolAction(BaseModel):
    symbol: str
    weight: float = 0.0


# ==========================================
# 核心状态 API (对接 index.html fetchState)
# ==========================================
@app.get("/api/state")
def get_state():
    """前端每秒心跳拉取全军状态 - 完整对接 SYSTEM_CONFIG"""
    try:
        from trading_engine import get_sandbox_balance
        ledger = get_sandbox_balance()
        sim_balance = float(ledger.get("balance", 10000.0))
    except Exception:
        sim_balance = float(SYSTEM_CONFIG.get("SIM_CURRENT_BALANCE", 10000.0))

    # 持仓快照 (带实时价格和PnL)
    pos_list = []
    # 极速快照释放锁
    with positions_lock:
        positions_snapshot = list(ACTIVE_POSITIONS.items())
    
    # 纯内存无 I/O 计算
    from trading_engine import get_indicator_cache
    for key_sym, pos_data in positions_snapshot:
        items = pos_data if isinstance(pos_data, list) else [pos_data]
        for pos in items:
            real_symbol = pos.get("real_symbol", key_sym.split("_")[0] if "_" in key_sym else key_sym)
            entry = pos.get("entry", 0)
            qty = pos.get("qty", 0)
            pos_type = pos.get("type", "LONG")
            
            # 安全读取价格：从缓存获取，避免 REST API 兜底
            cache_data = get_indicator_cache(real_symbol)
            if cache_data and 'price' in cache_data:
                current_price = cache_data['price']
            else:
                current_price = entry
            
            # 计算 PnL
            if pos_type == "LONG":
                pnl = (current_price - entry) * qty
            else:
                pnl = (entry - current_price) * qty
            
            pos_list.append({
                "symbol": key_sym,
                "real_symbol": real_symbol,
                "type": pos_type,
                "qty": qty,
                "entry": entry,
                "sl": pos.get("sl", 0),
                "trade_id": pos.get("trade_id", ""),
                "current_price": current_price,
                "pnl": pnl,
            })

    # 胜率计算
    win_rate = 0
    if len(TRADE_HISTORY) > 0:
        wins = sum(1 for t in TRADE_HISTORY if t.get('pnl', 0) > 0)
        win_rate = (wins / len(TRADE_HISTORY)) * 100

    return {
        # 顶部状态栏
        "running_mode": SYSTEM_CONFIG.get("RUNNING_MODE", "SANDBOX"),
        "engine_active": config.TRADING_ENGINE_ACTIVE,
        "ai_autonomy": SYSTEM_CONFIG.get("AI_FULL_AUTONOMY_MODE", False),
        # 资金池
        "sim_balance": sim_balance,
        "vault_balance": float(SYSTEM_CONFIG.get("VAULT_BALANCE", 0.0)),
        "risk_ratio": float(SYSTEM_CONFIG.get("RISK_RATIO", 0.025)),
        "leverage": int(SYSTEM_CONFIG.get("LEVERAGE", 20)),
        "benchmark_cash": float(SYSTEM_CONFIG.get("BENCHMARK_CASH", 1800.0)),
        # 策略
        "strategy_mode": SYSTEM_CONFIG.get("STRATEGY_MODE", "STANDARD"),
        "interval": SYSTEM_CONFIG.get("INTERVAL", "15m"),
        "adx_thr": SYSTEM_CONFIG.get("ADX_THR", 20),
        "ema_trend": SYSTEM_CONFIG.get("EMA_TREND", 200),
        "atr_mult": SYSTEM_CONFIG.get("ATR_MULT", 2.3),
        "atr_period": SYSTEM_CONFIG.get("ATR_PERIOD", 14),
        "sl_buffer": SYSTEM_CONFIG.get("SL_BUFFER", 1.02),
        # 引擎开关
        "black_swan": SYSTEM_CONFIG.get("BLACK_SWAN_DEFENSE", True),
        "kelly_formula": SYSTEM_CONFIG.get("USE_KELLY_FORMULA", False),
        "mad_dog": SYSTEM_CONFIG.get("MAD_DOG_MODE", False),
        "mad_dog_boost": SYSTEM_CONFIG.get("MAD_DOG_BOOST", 2.0),
        "mad_dog_trigger": SYSTEM_CONFIG.get("MAD_DOG_TRIGGER", 1.3),
        "volatility_scalar": SYSTEM_CONFIG.get("USE_VOLATILITY_SCALAR", False),
        "space_lock": SYSTEM_CONFIG.get("SPACE_LOCK_ENABLED", False),
        "obi_filter": SYSTEM_CONFIG.get("OBI_FILTER_ENABLED", False),
        "rs_filter": SYSTEM_CONFIG.get("RS_FILTER_ENABLED", False),
        "sml_booster": SYSTEM_CONFIG.get("SML_BOOSTER_ENABLED", False),
        # 运行环境
        "dry_run": SYSTEM_CONFIG.get("DRY_RUN", True),
        "vault_enabled": SYSTEM_CONFIG.get("VAULT_ENABLED", False),
        "auto_tune": SYSTEM_CONFIG.get("AUTO_TUNE_ENABLED", False),
        "hedge_mode": SYSTEM_CONFIG.get("HEDGE_MODE_ENABLED", False),
        "price_monitor": SYSTEM_CONFIG.get("PRICE_MONITOR_ENABLED", False),
        # 持仓
        "positions": pos_list,
        # 统计
        "win_rate": win_rate,
        "total_trades": len(TRADE_HISTORY),
        # 资产权重
        "asset_weights": dict(SYSTEM_CONFIG.get("ASSET_WEIGHTS", {})),
        # 哨所
        "sentry_enabled": SENTRY_CONFIG.get("ENABLED", False),
        "sentry_interval": SENTRY_CONFIG.get("INTERVAL", 900),
        "sentry_watchlist": SENTRY_CONFIG.get("WATCH_LIST", []),
        # Vault 管理
        "sim_initial": float(SYSTEM_CONFIG.get("SANDBOX_INITIAL_BALANCE", 10000.0)),
        "sim_hwm": float(SYSTEM_CONFIG.get("SIM_HIGH_WATER_MARK", 10000.0)),
    }


# ==========================================
# 参数注入 API
# ==========================================
@app.post("/api/update_param")
def update_param(data: ParamUpdate):
    """网页端参数注入：直接同步到主进程共享内存"""
    with config.state_lock:
        try:
            param_key = data.key
            new_val = data.value

            original_val = config.SYSTEM_CONFIG.get(param_key)
            if isinstance(original_val, bool):
                final_val = new_val.lower() in ['true', '1', 'yes']
            elif isinstance(original_val, int) and not isinstance(original_val, bool):
                final_val = int(float(new_val))
            elif isinstance(original_val, float):
                final_val = float(new_val)
            else:
                final_val = new_val

            config.SYSTEM_CONFIG[param_key] = final_val

            if param_key == "STRATEGY_MODE":
                apply_strategy_preset(final_val)

            config.save_data()

            # 安全防御：锁定参数，防止 AI 自动巡航覆盖人工设定
            try:
                from human_override import get_override_manager
                get_override_manager().lock_parameter(param_key, final_val, reason="Web UI 面板修改")
            except Exception as lock_err:
                logger.warning(f"⚠️ 人类锁同步失败: {lock_err}")

            logger.info(f"🌐 Web 参数注入: {param_key} = {final_val}")
            return {"status": "success", "msg": f"{param_key} 已更新为 {final_val}"}
        except Exception as e:
            return {"status": "error", "msg": str(e)}


# ==========================================
# 指令执行 API (对接 index.html cmd())
# ==========================================
@app.post("/api/command/{action}")
def execute_command(action: str):
    """接收前端按钮指令"""
    timestamp = time.strftime("%H:%M:%S")

    if action == "toggle_engine":
        config.TRADING_ENGINE_ACTIVE = not config.TRADING_ENGINE_ACTIVE
        status = "启动" if config.TRADING_ENGINE_ACTIVE else "停止"
        logger.info(f"🌐 Web 指令: 引擎{status}")
        return {"status": "success", "msg": f"引擎已{status}"}

    elif action == "toggle_ai":
        with state_lock:
            current = SYSTEM_CONFIG.get("AI_FULL_AUTONOMY_MODE", False)
            # 安全防御：实盘环境下禁止通过 Web 开启 AI 满血接管
            if not SYSTEM_CONFIG.get("DRY_RUN", False) and not current:
                return {"status": "error", "msg": "满血接管只能在模拟盘(DRY_RUN)下开启！"}
            SYSTEM_CONFIG["AI_FULL_AUTONOMY_MODE"] = not current
            save_data()
        status = "激活" if not current else "解除"
        logger.info(f"🌐 Web 指令: AI接管{status}")
        return {"status": "success", "msg": f"AI接管已{status}"}

    elif action == "emergency_close":
        try:
            from trading_engine import emergency_close_all
            emergency_close_all(None, None)
            logger.warning("🌐 Web 指令: 紧急全平已执行")
            return {"status": "success", "msg": "紧急全平已执行"}
        except Exception as e:
            return {"status": "error", "msg": str(e)}

    elif action == "toggle_dry_run":
        with state_lock:
            SYSTEM_CONFIG["DRY_RUN"] = not SYSTEM_CONFIG.get("DRY_RUN", True)
            save_data()
        return {"status": "success", "msg": f"DRY_RUN = {SYSTEM_CONFIG['DRY_RUN']}"}

    elif action == "toggle_auto_tune":
        with state_lock:
            SYSTEM_CONFIG["AUTO_TUNE_ENABLED"] = not SYSTEM_CONFIG.get("AUTO_TUNE_ENABLED", False)
            save_data()
        return {"status": "success", "msg": f"自动调参 = {SYSTEM_CONFIG['AUTO_TUNE_ENABLED']}"}

    elif action == "toggle_hedge":
        with state_lock:
            SYSTEM_CONFIG["HEDGE_MODE_ENABLED"] = not SYSTEM_CONFIG.get("HEDGE_MODE_ENABLED", False)
            save_data()
        return {"status": "success", "msg": f"对冲模式 = {SYSTEM_CONFIG['HEDGE_MODE_ENABLED']}"}

    elif action == "toggle_vault":
        with state_lock:
            SYSTEM_CONFIG["VAULT_ENABLED"] = not SYSTEM_CONFIG.get("VAULT_ENABLED", False)
            save_data()
        return {"status": "success", "msg": f"保险库 = {SYSTEM_CONFIG['VAULT_ENABLED']}"}

    elif action == "toggle_sentry":
        SENTRY_CONFIG["ENABLED"] = not SENTRY_CONFIG.get("ENABLED", False)
        save_sentry_watchlist()
        return {"status": "success", "msg": f"哨所 = {SENTRY_CONFIG['ENABLED']}"}

    elif action == "switch_sandbox":
        with state_lock:
            SYSTEM_CONFIG["RUNNING_MODE"] = "SANDBOX"
            SYSTEM_CONFIG["DRY_RUN"] = True
            save_data()
        return {"status": "success", "msg": "已切换到沙盒模式"}

    elif action == "switch_real":
        with state_lock:
            SYSTEM_CONFIG["RUNNING_MODE"] = "REAL"
            SYSTEM_CONFIG["DRY_RUN"] = False
            save_data()
        return {"status": "success", "msg": "已切换到实盘模式"}

    elif action == "sync_positions":
        try:
            from trading_engine import sync_positions
            sync_positions(None, None)
            return {"status": "success", "msg": "仓位同步完成"}
        except Exception as e:
            return {"status": "error", "msg": str(e)}

    elif action == "vault_emergency_reset":
        with state_lock:
            SYSTEM_CONFIG["SIM_CURRENT_BALANCE"] = 10000.0
            SYSTEM_CONFIG["SIM_INITIAL_BALANCE"] = 10000.0
            SYSTEM_CONFIG["SIM_HIGH_WATER_MARK"] = 10000.0
            save_data()
        # 安全防御：同步风控管理器高水位，防止假熔断
        try:
            from risk_manager import get_risk_manager
            rm = get_risk_manager(SYSTEM_CONFIG)
            rm.sim_high_water_mark = 10000.0
            rm._save_hwm()
        except Exception as hwm_err:
            logger.warning(f"⚠️ 风控 HWM 同步失败(reset): {hwm_err}")
        if not config.TRADING_ENGINE_ACTIVE:
            config.TRADING_ENGINE_ACTIVE = True
        return {"status": "success", "msg": "Vault 已重置为 $10,000"}

    elif action == "vault_sync_hwm":
        sim_bal = float(SYSTEM_CONFIG.get("SIM_CURRENT_BALANCE", 0.0))
        with state_lock:
            SYSTEM_CONFIG["SIM_HIGH_WATER_MARK"] = sim_bal
            save_data()
        # 安全防御：同步风控管理器高水位，防止假熔断
        try:
            from risk_manager import get_risk_manager
            rm = get_risk_manager(SYSTEM_CONFIG)
            rm.sim_high_water_mark = sim_bal
            rm._save_hwm()
        except Exception as hwm_err:
            logger.warning(f"⚠️ 风控 HWM 同步失败(sync): {hwm_err}")
        return {"status": "success", "msg": f"HWM 已同步为 ${sim_bal:.2f}"}

    return {"status": "error", "msg": f"未知指令: {action}"}


# ==========================================
# 资产管理 API
# ==========================================
@app.post("/api/asset/add")
def add_asset(data: SymbolAction):
    """添加监控币种"""
    symbol = data.symbol.upper()
    if not symbol.endswith("USDT"):
        symbol += "USDT"
    max_sym = SYSTEM_CONFIG.get("MAX_ACTIVE_SYMBOLS", 5)
    if len(SYSTEM_CONFIG["ASSET_WEIGHTS"]) >= max_sym:
        return {"status": "error", "msg": f"已达最大数量 {max_sym}"}
    weight = data.weight if data.weight > 0 else 1.0 / (len(SYSTEM_CONFIG["ASSET_WEIGHTS"]) + 1)
    with state_lock:
        SYSTEM_CONFIG["ASSET_WEIGHTS"][symbol] = weight
        save_data()
    return {"status": "success", "msg": f"已添加 {symbol}"}

@app.post("/api/asset/remove")
def remove_asset(data: SymbolAction):
    """移除监控币种"""
    symbol = data.symbol.upper()
    with state_lock:
        if symbol in SYSTEM_CONFIG["ASSET_WEIGHTS"]:
            del SYSTEM_CONFIG["ASSET_WEIGHTS"][symbol]
            save_data()
            return {"status": "success", "msg": f"已移除 {symbol}"}
    return {"status": "error", "msg": f"未找到 {symbol}"}

@app.post("/api/asset/balance")
def balance_weights():
    """平均分配权重"""
    num = len(SYSTEM_CONFIG["ASSET_WEIGHTS"])
    if num == 0:
        return {"status": "error", "msg": "无监控币种"}
    avg = round(1.0 / num, 4)
    with state_lock:
        for sym in SYSTEM_CONFIG["ASSET_WEIGHTS"]:
            SYSTEM_CONFIG["ASSET_WEIGHTS"][sym] = avg
        save_data()
    return {"status": "success", "msg": "权重已平均分配"}


# ==========================================
# 持仓操作 API
# ==========================================
@app.post("/api/position/{trade_key}/breakeven")
def position_breakeven(trade_key: str):
    """保本止损"""
    try:
        from trading_engine import update_sl_to_breakeven
        result = update_sl_to_breakeven(trade_key)
        return result
    except Exception as e:
        return {"success": False, "message": str(e)}

@app.post("/api/position/{trade_key}/close")
def position_close(trade_key: str):
    """强平指定持仓"""
    try:
        from trading_engine import get_position_by_key, execute_trade
        pos = get_position_by_key(trade_key)
        if not pos:
            return {"success": False, "message": "未找到持仓"}
        symbol = pos.get("real_symbol", trade_key.split("_")[0])
        signal = "SELL" if pos["type"] == "LONG" else "BUY"
        action = "EXIT_LONG" if pos["type"] == "LONG" else "EXIT_SHORT"
        result = execute_trade(None, symbol, signal, pos["entry"], {"quantity": pos["qty"]}, position_action=action)
        return result
    except Exception as e:
        return {"success": False, "message": str(e)}


# ==========================================
# 哨所管理 API
# ==========================================
@app.post("/api/sentry/add")
def sentry_add(data: SymbolAction):
    symbol = data.symbol.upper()
    if not symbol.endswith("USDT"):
        symbol += "USDT"
    if symbol in SENTRY_CONFIG["WATCH_LIST"]:
        return {"status": "error", "msg": f"{symbol} 已在哨所"}
    SENTRY_CONFIG["WATCH_LIST"].append(symbol)
    save_sentry_watchlist()
    return {"status": "success", "msg": f"已添加 {symbol}"}

@app.post("/api/sentry/remove")
def sentry_remove(data: SymbolAction):
    symbol = data.symbol.upper()
    if symbol in SENTRY_CONFIG["WATCH_LIST"]:
        SENTRY_CONFIG["WATCH_LIST"].remove(symbol)
        save_sentry_watchlist()
        return {"status": "success", "msg": f"已移除 {symbol}"}
    return {"status": "error", "msg": f"未找到 {symbol}"}


@app.post("/api/sentry/interval")
def update_sentry_interval(data: ParamUpdate):
    """修改哨所推送间隔"""
    try:
        minutes = int(data.value)
        SENTRY_CONFIG["INTERVAL"] = minutes * 60
        save_sentry_watchlist()
        return {"status": "success", "msg": f"频率已设为 {minutes}min"}
    except Exception:
        return {"status": "error", "msg": "格式错误"}


# ==========================================
# 账单历史 API
# ==========================================
@app.get("/api/bill")
def get_bill():
    """查看沙盒账单历史"""
    try:
        ledger_file = "sandbox_ledger.json"
        if not os.path.exists(ledger_file):
            return {"balance": 0, "history": []}
        with open(ledger_file, "r", encoding="utf-8") as f:
            ledger = json.load(f)
        return {
            "balance": ledger.get("balance", 0),
            "history": ledger.get("history", [])[-50:]
        }
    except Exception as e:
        return {"balance": 0, "history": [], "error": str(e)}


# ==========================================
# 前端页面
# ==========================================
@app.get("/")
def serve_dashboard():
    with open("index.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


if __name__ == "__main__":
    print("🚀 中枢控制台已启动！浏览器打开: http://127.0.0.1:8989")
    uvicorn.run(app, host="127.0.0.1", port=8989)
