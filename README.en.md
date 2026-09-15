# RogueTrader

<p align="center">
  <strong>A multi-agent investment committee for research, adversarial reasoning, risk adjudication, and production delivery</strong>
</p>

<p align="center">
  <a href="README.md">中文</a> ·
  <a href="README.en.md">English</a> ·
  <a href="backtest/README.md">Backtest results</a> ·
  <a href="ROADMAP.en.md">Roadmap</a> ·
  <a href="docs/control-panel.en.md">Control panel</a> ·
  <a href="docs/production-development.en.md">Operations</a>
</p>

RogueTrader does not ask one model to guess where a market is going. It organizes investment decisions as a team of specialized agents that advances through multi-source research, cross-examination, trade construction, and final risk review. A project-owned scheduler and CSV-first delivery pipeline make this virtual investment committee repeatable, auditable, and production-ready.

> This is a research system, not an automated execution engine or financial advice.

## Agent team architecture

The system orchestrates up to 14 specialized roles in a five-layer path from evidence gathering to executable expression:

```text
Intelligence
  Market · Social · News · Fundamentals · On-chain analysts
                         ↓ independent evidence
Deliberation
                 Bull researcher ↔ Bear researcher
                         ↓ Research manager
Strategy
              Trader: direction · sizing · conditions
                         ↓
Risk Committee
        Aggressive ↔ Neutral ↔ Conservative risk analysts
                         ↓ Portfolio manager
Execution
       Non-voting planner: parameterized scenario orders
```

This is more than a linear agent chain. Research must pass a bull/bear debate and managerial ruling; the resulting strategy then faces a three-way risk debate and portfolio review. Once the rating is final, an independent execution-planning agent translates it into auditable instructions without voting or changing the decision.

![RogueTrader control panel — sanitized production preview](docs/assets/control-panel-production.png)

_Sanitized example state; it contains no credentials, personal paths, or generated decision content._

## Core capabilities

- Configurable agent roster, per-role model tiers, and independent research/risk debate depth.
- Two adversarial decision loops, each resolved by a dedicated manager role.
- Optional multi-scenario, multi-order spot plans for paper simulation; live submission is always disabled.
- Loopback-only control panel for schedule, time, and symbol management.
- Sequential multi-symbol execution to avoid API and data-source contention.
- CSV-first delivery with an idempotent decision ledger and a separate order-level execution ledger for cross-date, cross-symbol review.
- Layered validation: Signal / Plan Lifecycle Quality evaluates local decision value over each parameterized plan's actual lifetime, while OHLCV and multi-factor replays accept final PnL.
- Point-in-time data governance with private tool snapshots, parameter hashes, and lineage; a historical rerun fails closed when its matching snapshot does not exist.
- A merged Feishu card with instructions first and the full decision second, plus an independent spreadsheet channel.
- Channel-only retries that never rerun paid analysis.
- Immutable tagged production releases with rollback support.
- Isolated development and production code, state, caches, results, and secrets.

## 60-day historical validation

Across 55 research-eligible plans, 298 parameterized orders, and 1,440 real hourly bars, RogueTrader completed the loop from agent decisions to continuous portfolio replay. One delayed repair without a contemporaneous snapshot was quarantined and treated as a day with no new trade. Results include modeled fees and slippage and represent a retrospective simulation, not live performance.

| | OHLCV baseline | Multi-factor replay | Fully invested BTC |
|---|---:|---:|---:|
| Return | **+8.03%** | **+6.82%** | +25.47% |
| Maximum drawdown | -2.24% | -2.57% | -6.45% |
| Fills | 22 | 35 | — |

The correction shows that one temporally contaminated decision—or one inverted condition interpretation—can materially change a portfolio conclusion. The lifecycle study no longer treats daily ratings as fixed-horizon forecasts: 48.15% of 27 executed plans beat a same-state no-action counterfactual; mean value add was -0.08% and median value add -0.01%. Inspect the [complete evidence archive](backtest/README.en.md), [Signal Quality method](docs/signal-quality.en.md), and [retrospective](docs/validation-evidence.en.md).

## Evolution

```text
Foundation → Operations → Decision Integrity → Execution Intelligence → Validation Loop → Portfolio Intelligence
   v1.0         v1.1             v1.2                  v1.3               v1.4               Horizon
```

The project is evolving from repeatable multi-agent research into measurable, feedback-driven investment intelligence. See the full [Evolution Roadmap](ROADMAP.en.md).

## Workflow

```text
Local control panel
  └── Asia/Shanghai daily scheduler
        └── sequential multi-symbol analysis
              └── completed run
                    ├── lifecycle manifest
                    ├── completion marker + structured decision
                    └── optional state-free execution plan
                              ↓
                    daily CSV (audit source of truth)
                              ├── point-in-time eligibility gate
                              │     ├── Signal / Plan Lifecycle Quality
                              │     │     └── activate → trigger/fill → exit/update/expiry
                              │     └── research order projection
                              │           └── Portfolio PnL → OHLCV / multi-factor
                              ├── local message package
                              ├── merged Feishu group card
                              └── Feishu spreadsheet row
```

The publisher consumes completed runs only. CSV, messaging, and spreadsheet delivery keep independent state, so a notification failure cannot affect the analysis task.

## Quick operations

```bash
ops/production-control-panel-service start
ops/production-control-panel-service status
ops/production-control-panel-service stop
```

Open <http://127.0.0.1:8765> after startup. The service listens on loopback only and must remain running for scheduled jobs to fire.

Manual analysis:

```bash
uv run --frozen python my_scripts/roguetrader0.py \
  --ticker BTC-USD \
  --date 2026-09-12 \
  --no-debug
```

Production version status:

```bash
./ops/release_manager.py status
./ops/release_manager.py list
.runtime/bin/run-version --check
```

## Result layout

```text
my_results/
├── 运行结果/20260913_115207__asof-20260913__prod__recovery-a02__BTC_USD/
│   ├── 运行清单.json
│   ├── 运行索引.json
│   ├── 最终决策.json
│   ├── 执行计划.json
│   ├── 执行实例.json (optional after manual binding)
│   ├── 报告.md
│   ├── 状态.json
│   └── 分段报告/
├── 汇总/
│   ├── 每日决策.csv
│   ├── 参数化委托.csv
│   └── 消息/
├── 回测数据/
│   └── 多因子条件版/<collection-time>__BTC_USD/
└── 回测结果/
    ├── <timestamp>__BTC_USD/ (OHLCV baseline)
    └── 多因子条件版/<timestamp>__BTC_USD/
        ├── 回测指标.json
        ├── 权益曲线.csv
        ├── 成交明细.csv
        ├── 委托回测状态.csv
        ├── 条件解析审计.csv
        ├── 数据质量.json
        ├── 数据来源.json
        ├── 回测报告.md
        ├── 回测报告.html
        ├── 验证方法.ipynb
        └── 结果清单.json
```

The directory name separates actual start time, requested analysis date, runtime lane, trigger, attempt, and symbol. A lifecycle manifest exists even for failed runs; only successful runs receive `运行索引.json` and become publishable.

Each symbol occupies one row in the decision ledger. Every plan is flattened into one row per scenario order in the execution ledger, with stable identities for idempotency. Official historical decisions can be planned in chronological order without rerunning the full agent team.

## Runtime isolation

| | Production | Development |
|---|---|---|
| Code | Immutable release selected by `.runtime/production/current` | `develop` worktree |
| Environment | Per-release `.venv` | Development `.venv` |
| Secrets | `.runtime/production/.env` | `.runtime/development/.env` |
| State | `.runtime/production/control-panel` | Worktree-local `.runtime/control-panel` |
| Results | Root `my_results/` | Worktree-local `my_results/` |

Secrets, generated results, CSV files, SQLite state, and logs are Git-ignored. The control panel never returns credentials or full generated reports.

## Development setup

Python 3.10+ and [uv](https://docs.astral.sh/uv/) are required:

```bash
uv sync --frozen
cp .env.example .env
./ops/release_manager.py bootstrap
./ops/release_manager.py setup-development
```

Keep real credentials in the local `.env` only.

## Documentation

- [Evolution roadmap](ROADMAP.en.md)
- [Control panel](docs/control-panel.en.md)
- [CSV-first local publisher](docs/local-publisher.en.md)
- [Run-result protocol](docs/run-results.en.md)
- [Production and development modes](docs/production-development.en.md)
- [Parameterized execution plans](docs/execution-plans.en.md)
- [Parameterized order backtest](docs/execution-backtest.en.md)
- [Multi-factor conditional backtest](docs/execution-backtest-multifactor.en.md)
- [Signal Quality](docs/signal-quality.en.md)
- [Complete 60-day backtest results](backtest/README.en.md)
- [60-day validation interpretation](docs/validation-evidence.en.md)
- [Analysis engine](docs/analysis-engine.en.md)
- [Models and data sources](docs/providers-and-data.en.md)
- [Development and manual runs](docs/development.en.md)
- [Daily health monitor](docs/daily-health-monitor.en.md)
- [Changelog](CHANGELOG.en.md)

## Verification

```bash
uv run --frozen python -m unittest discover -s tests -v
uv run --frozen python -m compileall -q cli roguetrader tests my_scripts main.py
```

## License and attribution

Apache License 2.0. See [LICENSE](LICENSE).

RogueTrader evolved from Tauric Research's [TradingAgents](https://github.com/TauricResearch/TradingAgents). See [NOTICE](NOTICE) for attribution and modification details.
