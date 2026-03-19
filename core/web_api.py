import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import threading
import os

# 导入你系统的真实核心组件
import config
from config import SYSTEM_CONFIG, ACTIVE_POSITIONS, state_lock, positions_lock

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.get("/api/state")
def get_system_state():
    """全息扫描：获取机甲当前所有真实状态"""
    with state_lock:
        state = {
            "engine_active": config.TRADING_ENGINE_ACTIVE,
            "running_mode": SYSTEM_CONFIG.get("RUNNING_MODE", "SANDBOX"),
            "dry_run": SYSTEM_CONFIG.get("DRY_RUN", True),
            "ai_autonomy": SYSTEM_CONFIG.get("AI_FULL_AUTONOMY_MODE", False),
            "strategy_mode": SYSTEM_CONFIG.get("STRATEGY_MODE", "STANDARD"),
            
            # 资金池状态
            "sim_balance": SYSTEM_CONFIG.get("SIM_CURRENT_BALANCE", 0.0),
            "vault_balance": SYSTEM_CONFIG.get("VAULT_BALANCE", 0.0),
            
            # 核心参数
            "leverage": SYSTEM_CONFIG.get("LEVERAGE", 20),
            "risk_ratio": SYSTEM_CONFIG.get("RISK_RATIO", 0.03),
            "adx_thr": SYSTEM_CONFIG.get("ADX_THR", 25),
            
            # 引擎开关
            "black_swan": SYSTEM_CONFIG.get("BLACK_SWAN_DEFENSE", True),
            "kelly_formula": SYSTEM_CONFIG.get("USE_KELLY_FORMULA", False),
            "mad_dog": SYSTEM_CONFIG.get("MAD_DOG_MODE", False),
        }
        
    # 获取真实持仓
    with positions_lock:
        pos_list = []
        for key, pos_data in ACTIVE_POSITIONS.items():
            if isinstance(pos_data, list):
                for p in pos_data: pos_list.append(p)
            else:
                pos_list.append(pos_data)
        state["positions"] = pos_list
        
    return state

@app.post("/api/command/{action}")
def execute_command(action: str):
    """接收网页端的控制指令，直接修改核心内存"""
    if action == "toggle_engine":
        config.TRADING_ENGINE_ACTIVE = not config.TRADING_ENGINE_ACTIVE
    
    elif action == "toggle_ai":
        with state_lock:
            SYSTEM_CONFIG["AI_FULL_AUTONOMY_MODE"] = not SYSTEM_CONFIG.get("AI_FULL_AUTONOMY_MODE", False)
            config.save_data()
            
    elif action == "emergency_close":
        from trading_engine import emergency_close_all
        # 注意：这里需要传入你的 client，可以先做标记或通过全局变量获取
        emergency_close_all(None, SYSTEM_CONFIG.get("TG_CHAT_ID"))
        
    return {"status": "success", "action": action}

@app.get("/")
def serve_dashboard():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(base_dir, "index.html"), "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

def start_web_server():
    """在独立线程中启动 API 服务，不阻塞主程序"""
    print("🚀 Web 监控中枢启动中: http://127.0.0.1:8989")
    uvicorn.run(app, host="127.0.0.1", port=8989, log_level="warning")

def init_web_dashboard():
    """被主程序调用的启动函数"""
    t = threading.Thread(target=start_web_server, daemon=True)
    t.start()