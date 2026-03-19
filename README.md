<p align="center">
  <img src="image_0.png" alt="X-VOID OMEGA: Institutional-Grade Crypto Quant System" width="700">
</p>

<h1 align="center">🚀 X-VOID Omega: Institutional-Grade Crypto Quant System</h1>

<p align="center">
  <img src="https://img.shields.io/badge/Version-9.0_%22The_Fortress%22-0a0a0a.svg?style=for-the-badge&logo=git&logoColor=cyan" alt="Version 9.0">
  <img src="https://img.shields.io/badge/Python-3.10%2B-fedc0a.svg?style=for-the-badge&logo=python&logoColor=0a0a0a" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/License-MIT-008000.svg?style=for-the-badge&logo=license&logoColor=white" alt="MIT License">
  <img src="https://img.shields.io/badge/Status-Production__Ready-00ff00.svg?style=for-the-badge&logo=serverfault&logoColor=0a0a0a" alt="Status">
</p>

<p align="center">
  <strong>X-VOID Omega</strong> 是一套专为中高频波动率捕获而设计的分布式量化交易系统。系统集成了 **SMC (聪明钱概念)**、**CVD 订单流审计**以及 **LLM 异步推理引擎**，实现了从宏观情绪感知到微观亚毫秒执行的完整闭环。
</p>

---
# 🚀 X-VOID Omega: Institutional-Grade Crypto Quant System

> [!IMPORTANT]
> **声明**：这不是一个简单的炒币脚本。** 这是一个基于 **NumPy 向量化引擎** 的工程级资产管理工具，旨在提供极端的稳定性、对冲功能和 AI 驱动的控制。

## 🏗️ 核心架构图 (Core Architecture)

```mermaid
graph TD
    %% 样式定义
    classDef hardware fill:#1a1a1a,stroke:#00f2ff,stroke-width:2px,color:#fff;
    classDef logic fill:#0d0d0d,stroke:#ff00ea,stroke-width:2px,color:#fff;
    classDef ai fill:#000,stroke:#fedc0a,stroke-width:2px,color:#fff;

    %% 节点定义
    WS["Binance Websocket Stream"]:::hardware
    Vector["Vectorized Tensor Engine"]:::logic
    SMC["SMC and CVD Matrix"]:::logic
    AI_Intel{"AI Sentinel Hub"}:::ai
    Risk["Risk Armor Logic"]:::logic
    Algo["Async Execution Algo"]:::hardware
    Redis[("Redis Data Bus")]:::hardware
    Dashboard["FastAPI Dashboard"]:::logic

    %% 逻辑流向
    WS ==> Vector
    Vector --> SMC
    WS -.-> AI_Intel
    SMC --> Risk
    AI_Intel -.-> Risk
    Risk ==> Algo
    Algo --> Redis
    Redis --- Dashboard
    Redis -.-> WS

## 🏗️ 核心架构图 (Core Architecture)

```mermaid
graph TD
    A[数据源] --> B[引擎]
    B --> C[执行]
12,架构隐患,网络 IO,异步 IO 混合阻塞风险在同步框架中混用 ThreadPoolExecutor 和协程。,行情暴走、多币种同时报警时，大语言模型的长轮询会导致事件循环锁死或僵死。,将所有 LLM 网络请求放入完全独立的进程或纯异步队列中处理。
🌟 核心技术矩阵 (Core Technical Matrix)
1. 🩸 极致的算法同源 (Zero-Drift Engine Parity)
“回测即实盘，所见即所得。” 系统消除了量化交易中最致命的“环境不一致性”。

纯 NumPy 向量化流控：实盘引擎摒弃了传统的循环逐行处理逻辑，将实时 Websocket 数据直接映射进与回测引擎完全一致的 NumPy C-扩展张量中。这意味着每一根 K 线的指标计算在数学层面上与历史回测实现 Bit-for-Bit (逐位对齐)，彻底解决信号漂移难题。

1:1 物理级高保真模拟：

全仓保证金模拟 (Cross-Margin Simulation)：回测逻辑内置了动态预估强平价计算，完美复刻币安合约的阶梯保证金制度。

滑点审计与微观流动性：基于 L2 订单簿深度的 VWAP 滑点模拟，能够真实反馈在大额订单下的成交磨损。

时钟一致性熔断：回测系统同步了 48 小时的“黑天鹅冷却时间锁”，确保在回放历史极端行情时，策略的停火逻辑得到真实演练。

2. 🛡️ 机构级风控护甲 (The Fortress: Quantitative Risk Control)
“在波动中生存，在确定性中增压。” 系统内置了多层级、多维度的风险阻断算法。

三阶段动态止损 (TSL - Tri-Stage Guardian)：

Stage 1 (初始护盾)：基于 ATR 的自适应物理止损，过滤市场震荡噪音。

Stage 2 (动态保本)：当浮盈触及波动率阈值，止损线自动上移至 Entry ± 微利偏移，确保单笔交易不再由盈转亏。

Stage 3 (利润收割)：锁定高位插针极值点（Highest/Lowest Price），以移动追踪模式最大程度捕获单边趋势的尾部利润。

净敞口 Delta 管理 (Net Delta Circuit Breaker)：系统超越了单一品种的风险考量。通过 Net_Delta 算法，实时统计账户的总名义价值暴露。当全账户做多/做空比例失衡（例如超过 0.7 净敞口）时，系统将强行拦截同向新订单，强迫资产进入对冲态势。

黑天鹅主动扫描仪：实时审计价格跳空 (Gap)、分钟级极端振幅、以及相对于 20 日均量的天量异动。一旦触发高维波动率预警，系统将进入“堡垒模式”，自动挂起所有策略循环 48 小时。

3. 🧠 AI 智能哨兵 (The Neural Overseer: LLM Intelligence)
“赋予冰冷代码以‘盘感’。” 利用大模型的逻辑推理能力作为决策的最后一道终审。

异步决策链路 (Agentic Multi-LLM Inference)：

集成 Claude-3.5-Sonnet 负责长文本逻辑链推理，对宏观政经数据进行深度“天气预报”。

集成 DeepSeek-R1 执行极速的市场情绪审计，过滤由于链上虚假消息引发的短线信号。

零延迟架构：LLM 推理运行在独立的异步工作线程中，绝不阻塞核心交易循环的心跳。

弹性边界自适应 (Market Regime Adaptation)：

AI 会自动识别当前市场处于“单边、震荡、或极端恐慌”哪种状态 (Regime)。

根据识别结果，AI 会动态收缩或扩张策略边界：在高波动环境下自动撑大 ATR 止损倍数，在低迷期自动收紧 ADX 入场门槛，实现真正的“全天候自愈”。


