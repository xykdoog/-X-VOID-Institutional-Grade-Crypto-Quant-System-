# 🌌 X-VOID: The High-Frequency Sovereign Quant Protocol

[![SMC v4.5](https://img.shields.io/badge/SMC_Protocol-v4.5_Council-red.svg?style=for-the-badge)](#)
[![HFT Engine](https://img.shields.io/badge/Execution-Zero--Latency_Decoupled-green.svg?style=for-the-badge)](#)
[![Red Team Certified](https://img.shields.io/badge/Security-Red_Team_Audited-blueviolet.svg?style=for-the-badge)](#)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-blue.svg?style=for-the-badge)](#)

> **"If you are not the predator, you are the liquidity."**
> 
> **X-VOID** 是一套凌驾于传统脚本之上的 **工业级元策略指挥部**。它不是在“跑策略”，而是在“狩猎流动性”。通过多进程异步解耦、微秒级盘口审计与三位一体 AI 战略中枢，X-VOID 彻底终结了散户量化的“裸奔”时代。

---

## 🏛️ 核心架构：工业级全链路闭环 (The Fortress)

X-VOID 的设计哲学是 **物理隔离、零和博弈、绝对防御**。

### 1. ⚡ 异步多进程计算核心 (Computation Decoupling)
传统机器人死于 Python 的 GIL 锁，X-VOID 诞生于对并发的极致压榨：
* **解耦执行**：通过 `multiprocessing.Process` 物理隔离信号计算与订单执行。当独立进程在秒级解析 Pandas 矩阵时，主进程在以微秒级速度分发原子化订单。
* **物理隔离锁**：内置 `@_ensure_live_mode` 装饰器，在字节码层面物理拦截 `SANDBOX` 环境下的实盘 API 溢出。

### 2. 🛡️ 战地风控防线 (Institutional Risk Perimeter)
* **2% 铁律 (Non-Negotiable SL)**：系统底层风控硬锁死。任何单笔交易风险若溢出本金 2%（MAX_SINGLE_RISK），风控官拥有最高优先级的“一票平仓权”。
* **原子化事务 (Rollback Protocol)**：主单与止损单封装为单次批量提交。若挂单不完整，系统立即执行“战地平仓回滚”并记录死信队列（DLQ），拒绝任何形式的无保护头寸裸奔。

---

## 🚀 降维打击：全因子狩猎系统 (Factor Supremacy)

### 🩸 订单流透视 (Order Flow Autopsy)
* **CVD (Cumulative Volume Delta)**：透视主动买卖盘累积偏差，在价格启动前预判机构吸筹/派发陷阱。
* **L2 OBI (Order Book Imbalance)**：毫秒级审计盘口前 20 档深度。当买盘强度 > 卖盘 2.6 倍时，自动识别“机构冰山支撑”。
* **Smart Money Lens**：集成 POC 成交量控制点、FVG 价值缺口与 Liquidity Sweep (扫损识别) 算法。

### 📈 动态进化因子 (Dynamic Positioning)
* **动态凯利公式**：拒绝固定仓位。基于最近 30 笔实盘表现的动态胜率 $W$ 与盈亏比 $R$，自动计算最优下注比例。
* **ATR 波动率缩放**：仓位与市场噪声成反比，确保账户净值曲线（Equity Curve）实现极低波动的复利增长。

---

## 🧠 AI Sovereign: 众神议会 (Council of Gods)

**X-VOID 不再依赖死板的 if-else，它拥有自己的“元认知”。**

### 三位一体三核联动架构
1. **DeepSeek-R1 (逻辑参谋)**：负责针对 15m/1h 周期的逻辑链深度推演。
2. **Claude 3.5 Sonnet (最高统帅)**：执行“铁腕审计”，纠正逻辑幻觉，直接下达调参 `###COMMAND###`。
3. **Gemini 3 Pro (全知视觉官)**：通过视觉神经网络，实时扫描 4h/1d K 线形态，进行 SMC 结构对账。

---

## 🛠️ 点火序列 (Ignition Sequence)

1. **配置注入**：在 `.env` 中填入你的作战权限。
2. **动态对账**：启动后，系统自动同步 `BENCHMARK_CASH` 至交易所真实余额。
3. **沙盒演习**：系统默认以 `SANDBOX` 模式点火。

```bash
# 安装工业级依赖
pip install -r requirements.txt


⚠️ 免责声明 (Disclaimer)
量化交易具有极高风险。本系统所涉及的任何参数（包括但不限于 2% 风险上限、凯利公式配置）仅为技术展示，不构成投资建议。
 请在完全理解代码逻辑后方可切换至 REAL 模式。

# 开启收割之旅
python main.py
