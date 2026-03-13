🌌 X-VOID: High-Frequency Meta-Strategy Orchestrator
X-VOID (WJ-BOT) 不仅仅是一个交易机器人。它是一套基于 多进程解耦架构 (Multi-Process Decoupling) 的工业级量化执行系统，集成了 CVD 订单流分析、L2 盘口审计、凯利公式动态配资 以及 三核 AI 战略调度。它为在高波动性加密货币市场中寻求“非对称交易优势”而生。

🏛️ 系统架构 (System Architecture)
X-VOID 采用了极致的生产力设计，彻底解决了 Python 在高频场景下的 GIL 性能瓶颈：

计算与执行分离 (Compute/IO Decoupling)：核心引擎通过 multiprocessing.Process 启动独立计算进程，实时处理复杂的 Pandas 指标运算，主进程仅负责轻量级 WebSocket 监听与原子化订单分发。

物理级环境隔离 (Physical Sandbox Isolation)：内置 @_ensure_live_mode 装饰器，在字节码层面物理拦截 SANDBOX 模式下的所有实盘 API 调用，确保资金绝对安全。

API 权重智能守卫 (Weight Sentry)：三级熔断机制（70% 预警减速、88% 强制断路、5秒物理冷却），确保在极端波动行情下不会因触发交易所限流而导致仓位失控。

🚀 核心技术栈 (Core Technologies)
1. 🩸 降维打击的量化因子 (Quant Factors)
CVD (Cumulative Volume Delta)：透视主动买卖盘的累积偏差，识别机构吸筹与派发的真实意图。

L2 OBI (Order Book Imbalance)：实时审计订单簿前 20 档深度，计算盘口失衡比率，精准识别机构冰山单。

Smart Money Lens：集成 POC (成交量控制点)、FVG (合理价值缺口) 与 Liquidity Sweep (流动性猎杀) 算法，捕捉庄家留下的物理足迹。

2. 🛡️ 严苛的机构级风控 (Risk Management)
2% 风险金律：系统强制锁定单笔交易最大本金损失上限为 2% (MAX_SINGLE_RISK)，任何策略无权逾越。

凯利公式动态配资 (Kelly Criterion)：基于最近 30 笔交易的盈亏比与胜率动态调整仓位，实现复利增长的最优路径。

三阶段动态止损 (Three-Stage TSL)：从“初始护盾”到“风险减半”，再到“智能保本”与“TSL 收割”，确保每一分利润都受到物理级保护。

3. ⚡ 原子化交易执行 (Execution Algo)
Atomic Transactions：利用 Binance 批量下单 API，将主单与止损单封装为原子包提交，解决“开仓易、止损难”的行业痛点。

VWAP 滑点审计：在入场前毫秒级模拟成交滑点，若 VWAP 偏离度超过万分之十 (10 bps)，系统将直接拒绝执行。

Ghost Maker (幽灵挂单)：SCALPER 模式专用 POST_ONLY 算法，强制 Maker 成交以获取手续费返还并降低冲击成本。

🧠 AI 统帅：众神议会 (Council of Gods)
X-VOID 的灵魂在于凌驾于底层引擎之上的 intelligence_hub.py：

三位一体决策内核：

DeepSeek-R1 (大脑)：负责极端行情下的逻辑链推演与多因子归因分析。

Claude opus4-6 (统帅)：执行最终的铁腕审计，纠正逻辑幻觉，直接下达调参指令。

Gemini 3 Pro (全知之眼)：通过视觉神经网络实时扫描 4h/1d K线形态，进行 SMC (聪明的钱) 结构对账。

宏观天气路由 (Regime Switching)：根据全网清算数据、大户多空比及波动率分位，自动调度“1m 狂战士”或“1d 核潜艇”进入战场。

🛠️ 快速启动 (Quick Start)
1. 克隆并安装
Bash
git clone https://github.com/your-username/X-VOID.git
cd X-VOID
pip install -r requirements.txt
2. 环境变量配置
创建 .env 文件并填入你的 API 凭证：

代码段
BINANCE_API_KEY=your_key
BINANCE_API_SECRET=your_secret
TG_TOKEN=your_telegram_bot_token
CLAUDE_API_KEY=your_anthropic_key
3. 点火运行
Bash
python main.py
系统将默认启动 SANDBOX (沙盒) 模式进行 0 风险模拟演习。

📊 战地复盘截图 (AI Visual Audit)
图：AI 统帅利用视觉官对 15m 级别的假突破进行实时拦截审计。

⚠️ 免责声明 (Disclaimer)
量化交易具有极高风险。本系统所涉及的任何参数（包括但不限于 2% 单笔风险上限、凯利公式配置等）仅为技术实现展示，不构成任何投资建议。
不完美，那不得亏死。 请在完全理解代码逻辑后方可切换至 REAL 模式。

📜 开源协议 (License)
本项目基于 MIT 协议开源。
