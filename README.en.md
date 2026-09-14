# RogueTrader

<p align="center">
  <strong>A multi-agent investment committee for research, adversarial reasoning, risk adjudication, and production delivery</strong>
</p>

<p align="center">
  <a href="README.md">中文</a> ·
  <a href="README.en.md">English</a> ·
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
- CSV-first delivery with idempotent `event_id` records.
- A merged Feishu card with instructions first and the full decision second, plus an independent spreadsheet channel.
- Channel-only retries that never rerun paid analysis.
- Immutable tagged production releases with rollback support.
- Isolated development and production code, state, caches, results, and secrets.

## Evolution

```text
Foundation → Operations → Decision Integrity → Execution Intelligence → Validation Loop → Portfolio Intelligence
   v1.0         v1.1             v1.2                  v1.3               Next               Horizon
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
                    daily CSV (source of truth)
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
└── 汇总/
    ├── 每日决策.csv
    └── 消息/
```

The directory name separates actual start time, requested analysis date, runtime lane, trigger, attempt, and symbol. A lifecycle manifest exists even for failed runs; only successful runs receive `运行索引.json` and become publishable.

Each symbol occupies one CSV row. Records are deduplicated by `event_id`; downstream channels consume the exact row reread from disk.

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
