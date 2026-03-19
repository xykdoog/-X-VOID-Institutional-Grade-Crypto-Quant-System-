<p align="center">
  <img src="https://img.shields.io/badge/X--VOID-OMEGA_V9.0-00f2ff?style=for-the-badge&logo=target" alt="X-VOID OMEGA">
</p>

# 🚀 X-VOID Omega: Institutional-Grade Crypto Quant System

> [!IMPORTANT]
> **统帅声明**：这是一个基于 **NumPy 向量化引擎** 的工程级资产管理工具。

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
