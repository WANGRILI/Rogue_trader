# Run-result Protocol

[中文](run-results.md) · [English](run-results.en.md)

RogueTrader uses a flat, parseable v2 directory name that separates the requested analysis date from the actual run time:

```text
20260913_115207__asof-20260913__prod__recovery-a02__BTC_USD
└─actual start       └─analysis date  └lane └trigger/attempt   └symbol
```

Runtime lanes are `prod`, `dev`, or `legacy`; triggers are `scheduled`, `recovery`, `manual`, or `unknown`. Attempts increment from `a01` within each analysis-date, symbol, and runtime-lane group.

`asof` is the date requested from the analysis engine. It does not claim that every external source is frozen at a precise point in time. Exact scheduled, start, and completion timestamps are stored in `运行清单.json`.

## Lifecycle Files

- The task atomically creates `运行清单.json` with status `running` at startup.
- Failure or interruption updates the manifest even if no analytical narrative exists yet, allowing the health monitor to detect it.
- `运行索引.json` is created only after all required outputs are complete and acts as the publisher's completion marker.
- `publication_event_id` is stored in the manifest and decoupled from the directory name, so migration cannot create a new publication event.
- When execution planning is enabled, the run may also produce `执行计划.json`; planning failure is recorded in `execution_plan_status` without invalidating completed research.

`执行计划.json` is optional and ignored by older runners and publishers. See [Parameterized Execution Plans](execution-plans.en.md).

Scheduled production and recovery runs may participate in official-result selection. Development, manual, and unclassified historical runs are candidates by default. Mutable states such as `official` live in metadata and publication state, never in the directory name.

## Historical Migration

Start with a read-only preview:

```bash
ops/migrate-run-layout --results-root /path/to/my_results
```

Apply only after production analysis and the control panel have stopped:

```bash
ops/migrate-run-layout --apply --results-root /path/to/my_results
```

Migration stores a private old-to-new mapping, backs up publication state, verifies the original files in every directory, and leaves CSV records unchanged. If validation fails before rollout, rerun the same command with `--rollback`.

Legacy scheduler logs at the root of `运行结果/` move unchanged to `my_results/历史日志/` and return to their original location on rollback.

v1 directories remain readable. Results produced by a rolled-back v1 release continue to work and can be normalized after returning to v2.
