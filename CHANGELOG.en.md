# Changelog

[中文](CHANGELOG.md) · [English](CHANGELOG.en.md)

This document records the project's major production changes. Git history remains the source for implementation-level detail.

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
