# RogueTrader Control Panel

[中文](control-panel.md) · [English](control-panel.en.md)

The control panel is RogueTrader's local scheduling and result-delivery service. Development and production use separate code, configuration, state, and result directories.

## Capabilities and Behavior

- Master switch: enables or disables the project's daily schedule.
- Daily time: 24-hour time in `Asia/Shanghai`.
- Symbol management: add, remove, enable, or disable each symbol independently.
- Execution planning: independently enables one extra quick-model call for a paper spot plan.
- Runtime state: shows the next run, active symbol, and recent outcomes.
- Sequential execution: runs symbols one at a time to avoid API and data-source contention.
- No implicit catch-up: activation or restart schedules the next future time only.
- Result delivery: completes and rereads the summary CSV before local packages, Feishu group messages, or Feishu Sheets consume the record.
- Health audit: performs one initial check and three reviews, then sends an aggregated Feishu alert on final failure without rerunning paid analysis.

Adding or removing a symbol affects the next batch only. An active batch retains the symbol snapshot captured at startup.

## Production Mode

Manage the service from the stable project root:

```bash
ops/production-control-panel-service start
ops/production-control-panel-service status
ops/production-control-panel-service stop
```

Production always loads the immutable release selected by `.runtime/production/current` and writes results to the root `my_results/`. Scheduler state lives in `.runtime/production/control-panel/`; publication state lives in `.runtime/production/publisher/`. Both survive version upgrades.

Open <http://127.0.0.1:8765> after startup. The service must remain running for daily tasks to execute.

## Development Mode

Start the isolated development service from its worktree:

```bash
cd .runtime/development/worktree
ops/control-panel-service start
ops/control-panel-service status
ops/control-panel-service stop
```

Development state and results remain inside the development worktree. Production and development use the same loopback port by default, so only one panel can run at a time. Stop development before starting production during a release.

For foreground debugging:

```bash
ops/run-control-panel
```

## Data and Security Boundary

The control panel listens only on loopback and offers no public bind option. It serves fixed static resources and control APIs only; arbitrary file access is not exposed.

Runtime state includes:

- `config.json`: switches, time, and symbol list;
- `runs.json`: recent execution state;
- `logs/`: per-symbol process logs;
- `service.log` and `service.pid`: background service state;
- `daily-health.json`: latest daily audit and alert state;
- adjacent `publisher/publisher.sqlite3`: delivery idempotency and retry state.

The entire `.runtime/` tree is Git-ignored. `.env`, API credentials, analysis results, and logs never enter commits. The page does not read or display secrets or full report content.

Feishu group delivery and Feishu spreadsheet synchronization are independent of the analysis master switch. A channel cannot be enabled until its credentials pass a connection test. Delivery retries never rerun analysis.

Parameterized execution planning is also independent. It uses `X_POSITION` and `X_CASH`, carries strategy continuity across days, requires no daily account input, and cannot submit orders. See [Parameterized Execution Plans](execution-plans.en.md).

Feishu group delivery has one status. When a validated `执行计划.json` exists, one merged card is sent with execution instructions first and the full decision summary second. Historical runs without a plan retain the original decision-only card. A failure retries the merged card without rerunning analysis.

Each Feishu spreadsheet row is deduplicated by `event_id`. Sorting or filtering data rows does not affect append or deduplication, but headers, protocol columns, and `event_id` values must remain intact.

See [CSV-first Local Publisher](local-publisher.en.md) for the result and delivery protocol.

See [Daily Task Health Audit](daily-health-monitor.en.md) for checks and retry boundaries. With a 05:00 schedule, checks occur at 05:15, 05:30, 05:45, and 06:00; an alert is sent only if recovery has not occurred by 06:00.

## Executed Command

Each enabled symbol runs sequentially in the active environment:

```bash
.venv/bin/python my_scripts/roguetrader0.py \
  --ticker <SYMBOL> \
  --date <scheduled date in Asia/Shanghai> \
  --no-debug
```

This command uses real data sources and model APIs and writes to the active runtime's `my_results/`. Verify credentials, model access, and quota before enabling the master switch.
