# RogueTrader CSV-first Local Publisher

[中文](local-publisher.md) · [English](local-publisher.en.md)

The publisher is isolated from analysis. It consumes a completed structured decision, appends the decision ledger, rereads the exact row, and only then feeds the order ledger, local message package, Feishu group notification, and Feishu spreadsheet. Publication itself makes no LLM calls.

```text
completed run → CSV write + fsync → reread by event_id
                                      ├── parameterized order CSV
                                      ├── local message package
                                      ├── Feishu group message
                                      └── Feishu spreadsheet
```

## Outputs

The active runtime writes to `my_results/汇总/`:

```text
my_results/汇总/
├── 每日决策.csv
├── 参数化委托.csv
└── 消息/
    └── <event_id>.json
```

Delivery state lives in the active runtime's `publisher/publisher.sqlite3`; production uses `.runtime/production/publisher/publisher.sqlite3`. Results, CSV files, packages, state databases, and locks are Git-ignored.

Each completed analysis occupies one CSV row with these fields:

```text
event_id, run_id, trade_date, generated_at, ticker, action,
action_source, confidence, risk_level, time_horizon, entry_plan,
stop_loss, take_profit, key_reasons, invalidations,
decision_summary, source_schema_version, publication_schema_version
```

Strings use standard CSV escaping. Values beginning with `= + - @` are neutralized against spreadsheet-formula injection.

`参数化委托.csv` is a derived audit view of validated `执行计划.json` files. It flattens each scenario order into one row and retains an orderless scenario as `row_type=no_order`. v2 adds structured triggers, order availability time and provenance, backtest validity, and risk limits. Only the real final-decision timestamp is accepted; an anomalous historical repair without a matching snapshot is quarantined by the research gate instead of receiving a synthetic fallback time. Stable `order_event_id` values prevent duplicates, and a v1 ledger upgrades from its source plans.

## Integrity and Idempotency

- `运行清单.json` records the full lifecycle; only successful directories containing both `运行索引.json` and `最终决策.json` are publishable.
- `运行索引.json` is the final completion marker.
- Symbol, date, and action must agree between index and decision.
- `INCOMPLETE` results are never published.
- Development and manual runs are candidates by default. Only one official result is automatic per analysis date and symbol; candidate promotion requires an explicit `publish --promote`.
- `event_id` is stable over the run identity and decision content.
- CSV is the local source of truth. After write and `fsync`, the publisher rereads and validates the logical row by `event_id`; downstream channels no longer consume raw decision JSON directly.
- Existing `event_id` values prevent duplicate CSV rows.
- Local message packages use `event_id` as the filename and remain idempotent.
- A decision-ledger failure blocks every downstream target. Once it succeeds, the order ledger, local message, Feishu group, and Feishu Sheet retain independent delivery and retry state; failure never rolls back the source ledger or reruns paid analysis.
- When a valid `执行计划.json` exists, the Feishu group card merges instructions first and the decision second. Both sections share one delivery state and add no model call.
- Registered downstream retries can recover from the CSV even if the raw result later becomes unavailable.
- Automatic polling processes new events discovered from completed runs. Manually editing CSV rows does not trigger delivery; historical maintenance remains explicit.
- Decision summaries may contain line breaks. Consumers must use a real CSV parser and locate logical rows by `event_id`, never `tail -n 1`.

## Scripts

Run these commands from the isolated development worktree.

Preview without writing state:

```bash
ops/preview-result-message my_results/运行结果/<run-directory>
```

Append CSV and generate the local package from the persisted row:

```bash
ops/publish-result my_results/运行结果/<run-directory>
```

The script derives the correct `my_results` root from the input directory. An explicit production result writes to the production summary; a development result cannot leak into it.

`--message-only` remains as a compatibility name but still ensures CSV persistence first. There is no path that bypasses CSV.

Update CSV only:

```bash
ops/update-decision-csv my_results/运行结果/<run-directory>
```

Scan new results:

```bash
ops/publish-new-results
```

The first scan records existing runs as a baseline. Historical delivery requires explicit backfill:

```bash
ops/publish-new-results --backfill
```

Another result root can be supplied:

```bash
ops/publish-new-results --results-root /path/to/my_results
```

## Feishu Group Notification

Add a custom bot to the target Feishu group and enable signature verification. Store its Webhook and signing secret only in the isolated runtime's `.env`:

```dotenv
ROGUETRADER_FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/...
ROGUETRADER_FEISHU_SIGNING_SECRET=...
```

Never place real values in `.env.example`, command arguments, chat, or Git. The control panel exposes only configured/unconfigured state.

Send a test card containing no analysis result:

```bash
ops/test-feishu-notification
```

After a successful test, enable automatic delivery in the control panel. First enablement establishes a baseline and does not resend history. To send one completed result explicitly:

```bash
ops/send-feishu-result my_results/运行结果/<run-directory>
```

The card is based on the persisted CSV record, preserves Markdown and line breaks, and allows up to 6,000 characters for the decision summary. Failure does not affect CSV. Retries wait 1 minute, 5 minutes, 15 minutes, 1 hour, 3 hours, and 6 hours, then expire 24 hours after the first failure.

Normal delivery is idempotent. Explicitly retry an already successful result with:

```bash
ops/send-feishu-result my_results/运行结果/<run-directory> --force
```

## Automatic Operation

The control-panel service starts an independent publisher thread that checks the active `my_results/运行结果/` every 15 seconds. It persists new completed results to CSV before downstream delivery. It does not watch or upload manually edited CSV rows, and neither analysis nor publication failures rerun the other subsystem.

The panel shows local publisher state and, for both Feishu channels, whether they are configured and enabled, the last test and delivery, and pending retries. It never returns Webhooks, signing secrets, App Secrets, spreadsheet locators, or message bodies.

## Feishu Spreadsheet Synchronization

The integration uses a standard Feishu spreadsheet rather than uploading and replacing a CSV file. Every new logical CSV record appends one row, so multiple symbols on the same date naturally remain filterable and exportable. Local `每日决策.csv` remains the audit copy.

The leading columns expose `trade_date`, `ticker`, `action`, confidence, risk, and execution plan; `run_id`, `event_id`, and protocol versions follow. Deduplication combines local SQLite state with the remote `event_id` column, preventing duplicate rows even after local-state loss.

Use a dedicated Feishu application and configure only the local `.env`:

```dotenv
ROGUETRADER_FEISHU_APP_ID=cli_...
ROGUETRADER_FEISHU_APP_SECRET=...
ROGUETRADER_FEISHU_SPREADSHEET_TOKEN=...
ROGUETRADER_FEISHU_SHEET_ID=...
```

A spreadsheet URL normally resembles `https://.../sheets/<spreadsheet_token>?sheet=<sheet_id>`. The application needs spreadsheet read/write scope and document access. After permission changes, publish or enable the required application version, complete administrator approval, and add the application as an editable collaborator on the target spreadsheet.

Test access and initialize headers in an empty sheet without adding test data:

```bash
ops/test-feishu-sheet
```

After success, enable Feishu Sheet synchronization in the panel. First enablement creates a baseline. Synchronize one historical result explicitly with:

```bash
ops/sync-feishu-sheet-result my_results/运行结果/<run-directory>
```

Normal synchronization is idempotent. `--force` resets local state only; the remote `event_id` still prevents duplicate rows:

```bash
ops/sync-feishu-sheet-result my_results/运行结果/<run-directory> --force
```

Spreadsheet and group channels retry independently, and no failure reruns analysis. The sheet uses the same 1-minute, 5-minute, 15-minute, 1-hour, 3-hour, and 6-hour backoff with a 24-hour expiry.

### Safe Manual Spreadsheet Operations

- Sorting, filtering, freezing, and styling below the first row are safe. Deduplication matches `event_id`, not a row number or current order.
- Newly appended records may initially appear at the bottom; reapplying sorting is safe and does not indicate failure.
- Do not include the header in the data sort or rename, remove, or reorder protocol columns.
- Do not modify `event_id` cells.

Official API references: [tenant_access_token](https://open.feishu.cn/document/server-docs/authentication-management/access-token/tenant_access_token_internal) and [append data](https://open.feishu.cn/document/server-docs/docs/sheets-v3/data-operation/append-data).
