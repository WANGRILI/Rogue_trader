# Changelog

[中文](CHANGELOG.md) · [English](CHANGELOG.en.md)

This document records the project's major production changes. Git history remains the source for implementation-level detail.

## Unreleased

## v1.5.0 — 2026-09-15

- Added point-in-time data governance: current-day tool calls create private snapshots and lineage; historical dates fail closed without a matching snapshot.
- Removed the “run start plus 20 minutes” order-time fallback and routed delayed repairs through the research eligibility gate.
- Added a research-only order projection that preserves source artifacts, treats quarantined dates as no new trade, and deterministically reconnects plan lineage.
- Corrected Signal Quality's unit of analysis: parameterized plan lifecycles and order terminal states replace fixed-horizon rating samples, with a same-state no-action counterfactual.
- Rebuilt the strict 60-day evidence: +8.03% OHLCV baseline and +6.82% multi-factor replay; replaced the public archive and removed machine-local absolute paths.

## v1.4.3 — 2026-09-15

- Made LLM request timeout and retry settings explicit and configurable in production to reduce full-run failures from transient upstream read timeouts.
- Fixed the version launcher so explicit analysis arguments use the CLI entrypoint and preserve recovery identity, original schedule time, and parent-run linkage.

## v1.4.1 — 2026-09-15

- Simplified the 60-day validation boundaries so the public narrative retains only its retrospective-simulation status.

## v1.4.0 — 2026-09-15

- Added a historical execution-plan backfiller that selects official decisions, preserves per-symbol chronology, and requires an explicit model-call cap.
- Date-filtered retries now seed the immediate predecessor and reject missing or discontinuous lineage.
- Added `参数化委托.csv`, an order-level plan ledger with scenario lineage, no-order markers, stable idempotency keys, and automatic daily appends.
- Upgraded the order ledger to v2 with availability provenance, structured triggers, backtest validity, and risk limits; repair-displaced history falls back to run start plus 20 minutes.
- Added a point-in-time order backtest over confirmed OKX 1H OHLCV with metrics, equity, fills, per-order status, and Markdown/HTML reports.
- Preserved the OHLCV baseline and added a parallel multi-factor replay over ETF, sentiment, funding, network, volume, and macro history, with point-in-time availability and exact/proxy evidence audits for every external condition.
- Added a strict 60×24-hour dual-track validation case with real market history, modeled costs, a fully invested benchmark, and an explicit strategy retrospective while keeping raw decisions and research data private.
- Extended daily health audits to cover order-ledger delivery; historical backfill sends no Feishu message and never reruns full research.

## v1.3.1 — 2026-09-14

- Added a bilingual Evolution Roadmap that presents major releases, current capabilities, and long-term direction as product stages.
- Completed English counterparts for all nine public topic guides and unified language navigation, terminology, and cross-document links.
- Removed outdated environment wording from the health-audit and execution-planning documentation.

## v1.3.0 — 2026-09-14

- Added a non-voting execution-planning agent that converts the final decision into a parameterized, multi-scenario, multi-order spot plan and reconciles it with the previous plan for the same symbol.
- Merged valid execution instructions and the full decision summary into one Feishu card, with instructions first and a single idempotent delivery state.
- Added strict protocol validation and an optional paper-binding tool that requires only current position, available cash, and last price.
- Restricted execution plans to `spot + paper`; live submission remains disabled, and a planning failure cannot invalidate completed research.

## v1.2.0

- Added v2 run identities that separate the analysis date, actual start time, runtime lane, trigger, attempt, and symbol.
- Added lifecycle manifests at task start and reserved the run index as the final completion marker.
- Decoupled publication identity from directory names and limited automatic publication to one official result per analysis date and symbol.
- Added dry-run, apply, and rollback support for historical directory migration without rewriting CSV records or resending Feishu messages.

## v1.1.4

- Reworked the Chinese and English README files around product capabilities, a sanitized control-panel preview, and concise operations.
- Added daily health audits with an initial check and three reviews across analysis, output, CSV, and Feishu failures.
- Limited health recovery to existing delivery retries; paid analysis is never rerun automatically.

## v1.1.2

- Added a sanitized control-panel state image to the README.
- Replaced development-specific wording in the control-panel header with a runtime-neutral identity.

## v1.1.1

- Added path-level baselines for legacy results to eliminate false historical polling alerts.
- Preserved normal reporting for newly generated invalid results.

## v1.1.0

- Added the local control panel and project-owned daily scheduler.
- Added multi-symbol management and `Asia/Shanghai` schedule configuration.
- Added the CSV-first publication protocol and SQLite idempotency state.
- Added Feishu group delivery, full decision summaries, and Feishu spreadsheet synchronization.
- Added isolated development and production runtimes, immutable installations, and rollback.

## v1.0.0

- Established the stable compatibility entrypoint, baseline result structure, and first immutable production release.
