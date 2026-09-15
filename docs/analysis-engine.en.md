# Analysis Engine

[中文](analysis-engine.md) · [English](analysis-engine.en.md)

## Objective

RogueTrader organizes multi-source evidence, opposing research views, and different risk postures into a structured trading judgment. It produces research guidance and does not place live orders.

## Workflow

```text
Input: symbol + analysis date
        ↓
Analyst team: market, social, news, fundamentals, on-chain
        ↓
Bull researcher ↔ Bear researcher
        ↓
Research manager adjudication
        ↓
Trader constructs the strategy
        ↓
Aggressive, conservative, and neutral risk debate
        ↓
Portfolio manager issues the final decision
        ↓ optional
Non-voting execution planner creates a paper spot plan
```

The final action is limited to `BUY`, `OVERWEIGHT`, `HOLD`, `UNDERWEIGHT`, or `SELL`.

## Roles

| Role | Primary responsibility |
|---|---|
| Market analyst | Price, volume, trend, and technical indicators |
| Social analyst | Community discussion, sentiment, and attention shifts |
| News analyst | Market, macroeconomic, and company events |
| Fundamentals analyst | Financials, valuation, and operating performance |
| On-chain analyst | Supply, whales, DeFi, stablecoins, mining, and derivatives signals |
| Bull / bear researchers | Build and challenge the thesis from opposing positions |
| Research manager | Synthesize evidence and adjudicate the research debate |
| Trader | Convert research into direction, sizing, and conditional strategy |
| Three risk roles | Review the strategy from aggressive, conservative, and neutral perspectives |
| Portfolio manager | Produce the final decision and risk constraints |
| Execution planner | Translate the final decision into an X-parameter spot plan without voting, including continuity with the previous plan |

## On-chain Research

On-chain coverage includes:

- market capitalization, supply, FDV, ATH/ATL, and multi-period price changes;
- exchange-volume distribution and concentration proxies;
- chain or protocol TVL and stablecoin-supply changes;
- BTC hash rate, difficulty, activity, and miner revenue;
- Pi Cycle, NVT, funding rates, CME gaps, and Fear & Greed.

Free public data is often appropriate for current research, but it may not satisfy strict point-in-time historical backtesting requirements. The development runtime now classifies tools as date-bounded or snapshot-required. A current-day run records sanitized arguments, response hashes, and private snapshots under `数据血缘.json` / `数据快照/`. An analysis of an earlier date automatically enters strict mode and fails closed when a current-state API lacks a same-date, same-parameter snapshot.

These artifacts remain inside Git-ignored run directories and redact sensitive arguments. Their purpose is to establish what the model actually saw at decision time—not to inject today's observable data into an old report.

## Model Tiers

Roles are divided between fast and deep reasoning:

- analysts, researchers, the trader, and risk debaters normally use the quick model;
- research and portfolio managers normally use the deep model.

`configs/agents.yaml` can override the model, identity, focus, and communication style for each role. Unspecified values fall back to code defaults.

## Memory and Reflection

Research and management roles can retrieve similar historical cases through BM25 memory. Once realized performance is known, `reflect_and_remember()` can store a reflection for future cases.

Memory does not replace current data or eliminate model bias. Historical learning must be interpreted against data-time boundaries and real execution conditions.

## Structured Outputs

A completed run produces:

- `运行索引.json`: completion marker and file index;
- `最终决策.json`: symbol, date, action, and decision narrative;
- `执行计划.json`: optional multi-scenario, multi-order paper execution plan;
- `报告.md`: complete research report;
- `状态.json`: final graph state;
- `运行配置.json`: run configuration;
- `分段报告/`: stage-level reports.

The publisher consumes a run only after the index and decision pass consistency checks.
