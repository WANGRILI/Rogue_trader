# RogueTrader · Evolution Roadmap

<p align="center">
  <strong>从多智能体投研工作流，走向可运行、可验证、可持续进化的决策智能系统</strong>
</p>

<p align="center">
  <a href="ROADMAP.md">中文</a> · <a href="ROADMAP.en.md">English</a>
</p>

## North Star

RogueTrader 的长期目标不是生成更多文本，而是建立一套具备独立研究、对抗论证、风险裁决、执行表达和结果反馈能力的投资决策基础设施。

```text
Research OS  →  Production Operations  →  Decision Integrity
             →  Execution Intelligence  →  Validation Loop
             →  Portfolio Intelligence
```

## Evolution

| 阶段 | 状态 | 核心跃迁 |
|---|---|---|
| **Foundation · v1.0** | Shipped | 从研究原型升级为生产/开发隔离、可回滚的版本化系统。 |
| **Operations · v1.1** | Shipped | 建立自动调度、CSV-first 发布、飞书双通道与运行告警。 |
| **Decision Integrity · v1.2** | Shipped | 统一运行身份、分析日期、重跑关系和正式结果边界。 |
| **Execution Intelligence · v1.3** | Current | 引入非投票执行规划 Agent，将最终判断转化为跨日承接的参数化多委托。 |
| **Validation Loop** | Next | 让计划进入模拟成交、持仓演进与收益归因，形成可衡量的决策反馈。 |
| **Portfolio Intelligence** | Horizon | 从单标的判断走向多资产配置、组合约束与自适应策略治理。 |

## Strategic Horizons

### Control Plane

把运行模式、Agent 席位、模型层级、辩论深度与输出协议拆分为可组合、可审计的独立控制面。

### Simulation Loop

连接参数化计划、模拟委托、成交状态与组合净值，让“建议是否有效”成为可以持续观察的问题。

### Decision Intelligence

建立跨日策略连续性、决策归因、Agent 质量评估和失败模式分析，使系统能够识别自己的优势与盲区。

### Portfolio System

在严格的人类授权与安全边界下，探索多标的资本分配、统一风险预算和外部执行适配层。

## Design Principles

- **Agentic by design**：角色分工、对抗论证和独立裁决优先于单次模型调用。
- **Evidence before action**：任何执行表达都必须可追溯到研究证据和最终决策。
- **Human accountable**：系统提供决策智能，人类始终保留资金与执行控制权。
- **Failure isolated**：分析、发布、通知与未来执行层彼此隔离，局部失败不放大。
- **Audit everything**：版本、输入边界、中间状态、结果和投递均可复核。

> Roadmap 表达产品方向，不构成时间或功能承诺；正式交付以 [CHANGELOG.md](CHANGELOG.md) 和生产标签为准。
