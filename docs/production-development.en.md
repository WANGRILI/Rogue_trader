# RogueTrader Production and Development Modes

[中文](production-development.md) · [English](production-development.en.md)

## Architecture Boundary

RogueTrader owns scheduling, analysis, result delivery, and version management. The project root is the stable control plane; development uses an isolated Git worktree, while production is sourced from immutable releases:

```text
Production control panel
  -> .runtime/production/current
  -> .runtime/releases/<version>  (fixed commit + isolated .venv)
  -> root my_results/              (production results and summary CSV)

Development control panel
  -> .runtime/development/worktree (develop branch + isolated .venv)
  -> worktree/my_results/          (development results and summary CSV)
```

`.runtime/`, secrets, generated data, and caches never enter Git. Every release retains its own source, dependency lock, and virtual environment, so later development cannot change an older version.

## Status and Operation

```bash
./ops/release_manager.py status
./ops/release_manager.py list
.runtime/bin/run-version --check
ops/production-control-panel-service status
```

The production control panel listens only on `127.0.0.1:8765`. Schedule state lives in `.runtime/production/control-panel/`; delivery state lives in `.runtime/production/publisher/`:

```bash
ops/production-control-panel-service start
ops/production-control-panel-service stop
```

Development runs use:

```bash
ops/run-development --ticker BTC-USD --date 2026-09-09
```

Development results and caches remain inside the development worktree.

To validate or execute an older version explicitly:

```bash
.runtime/bin/run-version --version v1.0.0 --check
.runtime/bin/run-version --version v1.0.0
```

The second command performs a real analysis and consumes external API quota.

## New Release

All new features begin on the `develop` branch in `.runtime/development/worktree`. After validation, create a production tag and immutable environment:

```bash
git tag -a production/<version> <candidate-commit> -m "RogueTrader production <version>"
./ops/release_manager.py install <version> production/<version>
./ops/release_manager.py verify <version> --import-check
.runtime/bin/run-version --version <version> --check
./ops/release_manager.py activate <version>
ops/production-control-panel-service start
```

`install` creates a worktree at a fixed commit, a dedicated virtual environment, and isolated data caches. `activate` verifies the version, atomically moves `current`, and preserves the old version in `previous`. Scheduler and publication state live outside release source directories and therefore survive upgrades.

## Rollback

Stop the production panel, switch to the previous release, and restart:

```bash
ops/production-control-panel-service stop
./ops/release_manager.py rollback
ops/production-control-panel-service start
.runtime/bin/run-version --check
```

Any installed version can also be activated directly:

```bash
./ops/release_manager.py activate v1.0.0
```

Rollback changes only the runtime pointer. It does not delete the newer release, historical results, or delivery state.

## Secrets and Data

- Root `.env` remains an untracked local baseline.
- Production reads `.runtime/production/.env`.
- Development reads `.runtime/development/.env`.
- `sync-production-env` and `sync-development-env` are explicit synchronization commands and create a backup before replacement.
- Production results and `my_results/汇总/每日决策.csv` live at the project root; every release owns a separate data cache.
- Feishu credentials are read from the active runtime's `.env`; the control panel exposes configuration status only.

## Compatibility Contract

- A version identifier can be installed once only. A fix requires a new version.
- The launcher rejects an installed release if tracked source files have changed.
- Every version retains its own source, `uv.lock`, and `.venv`.
- `my_scripts/roguetrader1.py` remains a stable compatibility entrypoint, not a daily feature surface.
- `my_scripts/daily_analysis.py` is the daily business entrypoint; the production panel invokes the selected release's `roguetrader0.py` per symbol.
