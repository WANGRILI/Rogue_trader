# RogueTrader

<p align="center">
  <strong>Multi-agent market research, scheduled analysis, and structured result delivery</strong>
</p>

<p align="center">
  <a href="README.md">中文</a> ·
  <a href="README.en.md">English</a> ·
  <a href="docs/control-panel.md">Control panel</a> ·
  <a href="docs/production-development.md">Operations</a>
</p>

RogueTrader combines a multi-agent investment-research pipeline with a project-owned production scheduler. It analyzes enabled symbols serially, produces structured decisions, persists each result to a local CSV, and then delivers the same record to Feishu channels.

> This is a research system, not an automated execution engine or financial advice.

![RogueTrader control panel — sanitized production preview](docs/assets/control-panel-production.png)

_Sanitized example state; it contains no credentials, personal paths, or generated decision content._

## Core capabilities

- Multi-agent market, social, news, fundamentals, and on-chain research.
- Loopback-only control panel for schedule, time, and symbol management.
- Sequential multi-symbol execution to avoid API and data-source contention.
- CSV-first delivery with idempotent `event_id` records.
- Independent Feishu group notification and spreadsheet channels.
- Channel-only retries that never rerun paid analysis.
- Immutable tagged production releases with rollback support.
- Isolated development and production code, state, caches, results, and secrets.

## Workflow

```text
Local control panel
  └── Asia/Shanghai daily scheduler
        └── sequential multi-symbol analysis
              └── completion marker + structured decision
                    └── daily CSV (source of truth)
                          ├── local message package
                          ├── Feishu group notification
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
├── 运行结果/<timestamp>_<symbol>/
│   ├── 运行索引.json
│   ├── 最终决策.json
│   ├── 报告.md
│   ├── 状态.json
│   └── 分段报告/
└── 汇总/
    ├── 每日决策.csv
    └── 消息/
```

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

## Analysis engine

```text
Analysts: market · social · news · fundamentals · on-chain
        ↓
Bull ↔ Bear researchers → Research manager
        ↓
Trader
        ↓
Aggressive ↔ Conservative ↔ Neutral risk → Portfolio manager
        ↓
BUY / OVERWEIGHT / HOLD / UNDERWEIGHT / SELL
```

The LangGraph-based engine supports DeepSeek, OpenAI, Anthropic, Google, xAI, OpenRouter, and local Ollama models.

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

- [Control panel](docs/control-panel.md)
- [CSV-first local publisher](docs/local-publisher.md)
- [Production and development modes](docs/production-development.md)
- [Analysis engine](docs/analysis-engine.md)
- [Models and data sources](docs/providers-and-data.md)
- [Development and manual runs](docs/development.md)
- [Changelog](CHANGELOG.md)

## Verification

```bash
uv run --frozen python -m unittest discover -s tests -v
uv run --frozen python -m compileall -q cli roguetrader tests my_scripts main.py
```

## License and attribution

Apache License 2.0. See [LICENSE](LICENSE).

RogueTrader evolved from Tauric Research's [TradingAgents](https://github.com/TauricResearch/TradingAgents). See [NOTICE](NOTICE) for attribution and modification details.
