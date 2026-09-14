# Development and Manual Runs

[中文](development.md) · [English](development.en.md)

## Prerequisites

- Python 3.10+
- `uv`
- At least one supported LLM provider or local Ollama

## Initialize

```bash
uv sync --frozen
cp .env.example .env
```

Store real credentials in local `.env` only. The file is Git-ignored and must not be printed on the command line or copied into documentation.

Verify the baseline environment:

```bash
uv run --frozen roguetrader --help
uv run --frozen roguetrader analyze --help
uv run --frozen python -m unittest discover -s tests -v
```

## Isolated Development Runtime

The project root remains a stable control plane; feature development uses a separate worktree:

```bash
./ops/release_manager.py bootstrap
./ops/release_manager.py setup-development
ops/run-development --check
```

Development source lives in `.runtime/development/worktree`; its environment file is `.runtime/development/.env`.

## Development Control Panel

```bash
cd .runtime/development/worktree
ops/control-panel-service start
ops/control-panel-service status
ops/control-panel-service stop
```

Development and production listen on `127.0.0.1:8765` by default, so only one runs at a time.

## Full Manual Analysis

```bash
uv run --frozen python my_scripts/roguetrader0.py \
  --ticker ETH-USD \
  --date 2026-09-12 \
  --analysts market,onchain \
  --max-debate-rounds 1 \
  --execution-plan \
  --no-debug
```

Common options:

- `--ticker`: yfinance-compatible symbol;
- `--date`: `YYYY-MM-DD`;
- `--analysts`: comma-separated analyst list;
- `--output-language`: `Chinese` or `English`;
- `--max-debate-rounds`: bull/bear debate rounds;
- `--max-recur-limit`: LangGraph recursion ceiling;
- `--quick-model`, `--deep-model`: per-run model overrides;
- `--execution-plan` / `--no-execution-plan`: enable or disable the optional paper spot plan.

Do not patch scripts merely to preserve logs. Every standard run already includes `终端日志.log`.

Daily parameterized plans use `X_POSITION` and `X_CASH` and require no manual account input. A future simulator can optionally resolve quantities from three values:

```bash
ops/bind-execution-plan <run-result-directory> \
  --position-qty 1 --available-cash 10000 --last-price 100
```

This command writes paper-only `执行实例.json`. See [Parameterized Execution Plans](execution-plans.en.md).

## CLI

```bash
uv run --frozen roguetrader
```

The interactive CLI guides selection of symbol, date, language, analysts, research depth, provider, and models.

## Local Processed/Parquet Mode

Historical or offline checks should use normalized `processed/parquet` data truncated at the requested date:

```bash
uv run --frozen python my_scripts/roguetrader_local_data.py \
  --skip-roguetrader \
  --ticker BTC-USD \
  --date 2014-11-30 \
  --source manual_or_investing \
  --timeframe 1d \
  --days 30
```

`--skip-roguetrader` avoids LLM calls and is suitable for validating local data paths and output protocols.

## Tests

```bash
uv run --frozen python -m unittest discover -s tests -v
uv run --frozen python -m compileall -q cli roguetrader tests my_scripts main.py
node --check roguetrader/control_panel/static/app.js
git diff --check
```

Tests requiring real credentials or external charges must be run explicitly and are excluded from the default suite.

## Entrypoints

| Path | Purpose |
|---|---|
| `my_scripts/roguetrader0.py` | Parameterized manual analysis |
| `my_scripts/daily_analysis.py` | Daily analysis business entrypoint |
| `my_scripts/roguetrader1.py` | Stable compatibility entrypoint |
| `roguetrader/control_panel/` | Local control panel and scheduler |
| `roguetrader/publisher/` | CSV-first delivery and Feishu channels |
| `roguetrader/execution/` | Plan protocol, planning agent, and paper binder |
| `ops/release_manager.py` | Immutable installation, activation, and rollback |

See [Daily Task Health Audit](daily-health-monitor.en.md). Its default tests use a simulated clock and local state; they call neither models nor real Feishu endpoints.
