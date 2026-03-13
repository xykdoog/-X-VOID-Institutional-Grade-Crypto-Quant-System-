# 🌌 X-VOID (WJ-BOT): The High-Frequency Sovereign Quant Protocol

[![SMC v4.5](https://img.shields.io/badge/SMC_Protocol-v4.5_Council-red.svg?style=for-the-badge)](#)
[![HFT Engine](https://img.shields.io/badge/Execution-Zero--Latency_Decoupled-green.svg?style=for-the-badge)](#)
[![Red Team Certified](https://img.shields.io/badge/Security-Red_Team_Audited-blueviolet.svg?style=for-the-badge)](#)
[![Kelly Optimized](https://img.shields.io/badge/Positioning-Dynamic_Kelly_Criterion-yellow.svg?style=for-the-badge)](#)

> **"If you are not the predator, you are the liquidity."**
> 
> **X-VOID** 是一套凌驾于传统脚本之上的 **工业级元策略指挥部**。它不是在“跑策略”，而是在“狩猎流动性”。通过多进程异步解耦、微秒级盘口审计与三位一体 AI 战略中枢，X-VOID 彻底终结了散户量化的“裸奔”时代。

---

## 🏛️ 宏观架构：工业级全链路闭环 (Fortress Architecture)

X-VOID 的设计哲学是 **物理隔离、零和博弈、绝对防御**。

### 1. ⚡ 异步多进程计算核心 (Distributed Execution)
传统机器人死于 Python 的 GIL 锁，X-VOID 诞生于对并发的极致压榨：
* **计算解耦**：通过 `multiprocessing.Process` 物理隔离信号计算与订单执行。当指标进程在实时解析 800 根 K 线的 Pandas 矩阵时，执行引擎在以微秒级速度刷新 WebSocket 深度流。
* **Ghost-to-Metal 路由**：内置物理级 `SANDBOX` 隔离装饰器，在字节码层面拦截 API 溢出，确保模拟环境的绝对纯度。

### 2. 🛡️ 战地风控防线 (Institutional Risk Perimeter)
* **2% 铁律 (Non-Negotiable SL)**：系统底层逻辑硬锁死。单笔风险溢出 2% 资本总额时，风控官拥有最高优先级的“一票平仓权”。
* **三级 API 熔断系统**：基于滑动窗口的权重监控（70% 减速、88% 物理断路），在市场插针导致的 API 洪水中提供物理级保护。
* **原子化事务回滚 (Rollback Protocol)**：主单与止损单封装为单次 API 原子包。若止损挂单失败，系统立即执行“战地回滚”，拒绝任何形式的无保护裸奔。

---

## 🚀 降维打击：全因子狩猎系统 (Factor Supremacy)

### 🩸 订单流与微观结构 (Order Flow Autopsy)
* **CVD (Cumulative Volume Delta) 底层透视**：通过解析主动买卖盘的累积背离，在价格启动前预判机构的吸筹与派发陷阱。
* **L2 OBI (Order Book Imbalance) 盘口审计**：实时审计买卖盘前 20 档深度。当买单强度超过阈值时，自动识别“机构冰山支撑”，开启 Ghost Maker 挂单模式。
* **SMC Smart Money Lens**：
    * **POC (Point of Control)**：识别 100 周期内成交量最密集的“地心价格”。
    * **FVG (Fair Value Gap)**：捕捉机构暴力拉升留下的失衡区作为磁铁回补点。
    * **Liquidity Sweep**：利用 20 周期高低点扫损算法，专吃散户止损盘。

### 📈 动态进化因子 (Dynamic Kelly Scaling)
* **凯利公式自适应 (Kelly Criterion)**：拒绝固定仓位。系统基于最近 30 笔实盘表现的动态胜率 $W$ 与盈亏比 $R$，利用 $f^* = \frac{WR - (1-W)}{R}$ 实时优化资本配比。
* **ATR 波动率缩放**：仓位与市场噪声成反比，确保账户净值曲线（Equity Curve）实现极低波动的复利增长。

---

## 🧠 AI Sovereign: 众神议会决策中枢 (Council of Gods)

**X-VOID 不再依赖死板的 if-else，它拥有自己的“元认知”。**

### 三位一体三核联动架构
1.  **DeepSeek-R1 (逻辑参谋长)**：负责 15m/1h 周期的逻辑链深度推演。
2.  **Claude 3.5 Sonnet (最高统帅)**：执行“铁腕审计”，纠正 R1 的决策偏差，下达最终指令。
3.  **Gemini 3 Pro (全知视觉官)**：通过视觉神经网络，实时扫描 K 线形态，识别算法无法描述的巨鲸足迹。

### 宏观天气路由 (Regime Switching)
AI 统帅根据 **全网多空比**、**百万级清算流** 及 **VIX 波动率分位** 实时调度模式：
* **⚡ SCALPER (狂战士)**：低波动下开启 OBI 盘口冰山对冲。
* **🔥 AGGRESSIVE (爆破手)**：Squeeze 点火时双擎驱动，抓取日内主升浪。
* **🛡️ CONSERVATIVE (核潜艇)**：VIX 爆表时进入极度防御，执行 1d 威科夫探底。

---

## 📊 X-VOID 与普通机器人的残酷对比

| 特性 | 普通交易机器人 (Generic Bots) | X-VOID Sovereign Protocol |
| :--- | :--- | :--- |
| **执行架构** | 单线程同步阻塞 | **异步多进程解耦计算与执行** |
| **风控逻辑** | 简单的百分比止损 | **2% 风险硬锁 + 三阶段动态 TSL** |
| **数据源** | 仅 K 线 OHLCV 数据 | **CVD 订单流 + L2 盘口审计 + 清算流** |
| **下单策略** | 粗暴市价成交 | **VWAP 滑点审计 + Ghost Maker** |
| **决策智能** | 固定技术指标 | **三核 AI 逻辑对账 + 视觉形态审计** |
| **资金管理** | 固定本金 | **基于 30 笔样本的动态凯利配资** |

---

## 🛠️ 点火序列 (Ignition Sequence)

1.  **环境封印**：建议部署在具备低延迟链路的 AWS Tokyo/Singapore 节点。
2.  **密钥注入**：在 `.env` 中填入 API 凭证。
3.  **沙盒演习**：系统默认以 `SANDBOX` 模式点火。

```bash
# 安装工业级依赖
pip install -r requirements.txt

# 开启收割之旅
python main.py
