import os
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()

class I18NManager:
    """
    X-VOID OMEGA | Global Localization & Internationalization Manager
    Ensures 'Institutional-Grade' terminology across all system modules.
    """
    def __init__(self, language='EN'):
        self.language = language.upper()
        
        # Hardcore Institutional Dictionary
        self.translations = {
            'EN': {
                # --- System Status & Infrastructure ---
                'sys_startup': "🛡️ X-VOID OMEGA | INSTITUTIONAL TERMINAL INITIALIZED",
                'sys_status': "📡 SYSTEM STATUS: OPERATIONAL",
                'regime_detect': "🔍 MARKET REGIME: {regime}",
                'ledger_sync': "📝 LEDGER WAL SYNC: ATOMIC PARITY ACHIEVED",
                'persistence_ok': "💾 DATA PERSISTENCE: POWER-LOSS RESILIENT",
                'weight_monitor': "⚖️ API WEIGHT: {weight} / 1200",
                
                # --- Trading Execution (The War Room) ---
                'exec_commenced': "⚔️ EXECUTION COMMENCED | DEPLOYING CAPITAL",
                'order_chase': "🏃 ASYNC ORDER CHASER ACTIVE | RE-ANCHORING TO BBO",
                'position_opened': "📥 POSITION SECURED | {symbol} {side} @ {price}",
                'position_closed': "💰 POSITION LIQUIDATED | PNL: {pnl}%",
                'ghost_exec': "👻 GHOST EXECUTION: L2 LIQUIDITY AUDIT PASSED",
                'slippage_warn': "⚠️ SLIPPAGE ALERT: VWAP DEVIATION > 0.5%",

                # --- Risk Armor (Defense Systems) ---
                'risk_armor_active': "🛡️ RISK ARMOR: MULTI-LAYER DEFENSE ENGAGED",
                'tsl_trigger': "📉 V-THRESHOLDS: TRAILING STOP TRIGGERED",
                'max_dd_breaker': "🛑 CIRCUIT BREAKER: 48H HALT INITIATED | MAX DD REACHED",
                'delta_imbalance': "⚖️ DELTA IMBALANCE: MANDATORY HEDGE RECTIFICATION",
                'pos_isolation': "🚧 POSITION ISOLATION: MARGIN CONTAGION PREVENTED",
                'emergency_halt': "🚨 EMERGENCY HALT: SYSTEM-WIDE LIQUIDATION EXECUTED",

                # --- Neural Oversight (AI Sentinel Hub) ---
                'neural_audit': "🧠 NEURAL OVERSIGHT: ASYNCHRONOUS AUDIT IN PROGRESS",
                'gemini_vision': "👁️ GEMINI-V: MULTI-MODAL K-LINE RESONANCE CONFIRMED",
                'claude_logic': "📜 CLAUDE-L: SMC LOGIC CERTAINTY AUDIT PASSED",
                'deepseek_sent': "🌊 DEEPSEEK-S: WHALE FLOW & SENTIMENT ANALYSIS SYNCED",
                'ai_confidence': "🎯 AI CONFIDENCE SCORE: {score}%",

                # --- Signal Matrix (SMC/CVD) ---
                'sig_smc_long': "⚡ SIGNAL: SMC BULLISH ORDER BLOCK DETECTED",
                'sig_cvd_div': "📉 SIGNAL: CVD ABSORPTION DIVERGENCE IDENTIFIED",
                'sig_mtf_res': "🌊 SIGNAL: MULTI-TIME-FRAME RESONANCE DETECTED",
                'sig_invalid': "❌ SIGNAL REJECTED: NOISE THRESHOLD NOT MET",

                # --- Telegram Terminal Buttons ---
                'btn_metrics': "📊 LIVE METRICS",
                'btn_risk': "🛡️ RISK ARMOR",
                'btn_neural': "🧠 NEURAL OVERSIGHT",
                'btn_signals': "⚡ SIGNAL MATRIX",
                'btn_halt': "⛔ EMERGENCY HALT",
                'btn_config': "⚙️ TERMINAL CONFIG"
            },
            'CN': {
                'sys_startup': "🛡️ X-VOID OMEGA | 机构级终端已启动",
                'sys_status': "📡 系统状态：运行中",
                'regime_detect': "🔍 当前行情环境：{regime}",
                'ledger_sync': "📝 账本 WAL 同步：实现原子性对账",
                'btn_metrics': "📊 实盘状态",
                'btn_risk': "🛡️ 风险控制",
                'btn_neural': "🧠 AI 智库",
                'btn_halt': "⛔ 紧急停机",
            }
        }

    def get(self, key, **kwargs):
        """Retrieve the localized string with optional formatting."""
        text = self.translations.get(self.language, self.translations['EN']).get(key, key)
        return text.format(**kwargs) if kwargs else text

# Singleton instance based on .env configuration
GLOBAL_LANG = os.getenv("APP_LANGUAGE", "EN")
lang = I18NManager(GLOBAL_LANG)