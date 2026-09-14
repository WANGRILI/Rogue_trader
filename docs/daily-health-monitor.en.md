# Daily Task Health Audit

[中文](daily-health-monitor.md) · [English](daily-health-monitor.en.md)

The health audit independently verifies the daily analysis and delivery pipeline. It does not modify the analysis flow or automatically rerun paid analysis.

## Check Schedule

Checks are relative to the daily time configured in the control panel:

| Check | Offset | For a 05:00 schedule |
|---|---|---|
| Initial check | +15 minutes | 05:15 |
| First review | +30 minutes | 05:30 |
| Second review | +45 minutes | 05:45 |
| Final review and decision | +60 minutes | 06:00 |

If the full pipeline has succeeded at any check, the audit ends without an alert. If an issue remains at the final review, the system sends one aggregated failure card through the enabled and tested Feishu group bot.

To avoid false alerts during deployment or development, a service first started after the final deadline marks the day as a cold-start skip when no scheduled run, active task, or result directory exists. Existing failures or incomplete results are still reported.

Audit state is stored in the active runtime's `control-panel/daily-health.json`, preventing duplicate alerts after a restart.

## Coverage

For every enabled symbol, the audit checks:

1. whether `runs.json` contains the day's scheduled task and whether the process exited successfully;
2. whether the run directory matches `<start>__asof-<analysis-date>__<lane>__<trigger>-a<attempt>__<symbol>`;
3. whether `运行索引.json` and `最终决策.json` agree;
4. whether all reports, state, configuration, terminal log, and stage reports declared by the index exist;
5. local CSV and local-message delivery;
6. Feishu group delivery when enabled;
7. Feishu spreadsheet synchronization when enabled;
8. the publisher polling thread.

Failures across multiple symbols are aggregated into one alert rather than flooding the group.

## Failure Classes

| Class | Meaning |
|---|---|
| `task_not_started` | No task or result exists at check time |
| `analysis_running` / `analysis_timeout` | Analysis is still running or remains unfinished at the deadline |
| `analysis_failed` | The process exited with a non-zero code |
| `result_missing` / `result_incomplete` / `result_invalid` | The run directory, completion marker, or stage outputs are incomplete |
| `publisher_unavailable` | The publisher thread is not running |
| `csv_*` / `local_message_*` | The local publication boundary is pending or failed |
| `feishu_*` | Feishu group configuration or delivery failure |
| `feishu_sheet_*` | Feishu spreadsheet configuration or synchronization failure |

The control panel exposes only safe classifications and exit codes. It never returns decision content, credentials, Webhooks, App Secrets, or absolute local paths.

## Retry Boundary

- The three reviews reread current state and wake existing delivery work only.
- Feishu messaging and spreadsheet delivery retain their own idempotency keys and backoff policies.
- Analysis failure, missing outputs, or timeouts never start a new model run automatically.
- Alerts use the existing Feishu group bot. If that channel itself is unavailable, the failure remains recorded locally and visible in the control panel, but cannot be delivered through the failed channel.

This boundary avoids both premature alerts and unattended duplicate model costs.
