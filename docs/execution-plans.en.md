# Parameterized Execution Plans

[中文](execution-plans.md) · [English](execution-plans.en.md)

After the final investment decision, RogueTrader can make one additional quick-model call. A non-voting Execution Planner translates the natural-language decision into `执行计划.json` without changing the portfolio manager's rating.

## Initial Boundary

- Spot instruments (`spot`) and paper execution (`paper`) only.
- `auto_submit` is always `false`; no exchange-submission path exists.
- No leverage, margin, derivatives, or spot short selling.
- Price conditions can use only values explicitly present in the final decision.
- Planning failure cannot turn a completed research result into a failed run.

## Two-stage Output

```text
最终决策.json
      ↓ Execution Planner + strict protocol validation
执行计划.json (daily parameterized delivery; not executable)
      ↓ optional future binding by a simulator
执行实例.json (resolved quantities for paper use only)
```

`执行计划.json` can contain multiple scenarios and ordered instructions:

- Scenario: priority, exclusive group, and immediate, last-price, or confirmation trigger.
- Order: side, open/increase/reduce/exit intent, market/limit/stop type, and sequence.
- Size: current-position percentage, available-cash percentage, or fixed quantity.
- Risk: optional take-profit, stop-loss, maximum-position, and per-order cash limits.

## X Parameters: No Daily Input

The daily task does not request account data. Each instruction carries a readable `size_expression`:

- `0.30 × X_POSITION`: sell 30% of the position available at execution time.
- `0.10 × X_CASH`: use 10% of available cash at execution time.
- Fixed quantity is allowed only when the original decision specifies an absolute amount.

`X_POSITION` and `X_CASH` are valid plan parameters, not missing values. Once `执行计划.json` exists, the day's analysis is complete.

Only a future simulator or account adapter needs these three values to resolve formulas into quantities; they are not daily user inputs:

| Parameter | Meaning |
|---|---|
| `position_qty` | Current spot position quantity |
| `available_cash` | Available quote-currency cash |
| `last_price` | Latest price |

`open_buy_qty` and `open_sell_qty` are optional aggregate open-order quantities and default to zero. Numeric binding needs no complete account, order history, identity, or API key and cannot alter daily plan generation.

A daily close, indicator, or news event cannot be inferred from `last_price`. Such conditions remain `manual_confirmation`, and their orders remain `waiting_confirmation`, preserving the original decision instead of weakening it for automation.

Quantity precision and minimum notional are static paper-market rules, not real-time account state.

## Optional Numeric Binding

```bash
ops/bind-execution-plan <run-result-directory> \
  --position-qty 1 \
  --available-cash 10000 \
  --last-price 100
```

The command writes `执行实例.json` only and never connects to a broker or exchange. An instance can be:

- `ready`: a first order group is ready for simulation;
- `waiting`: no price condition is active;
- `no_action`: a selected scenario contains no order;
- `blocked`: cash, position, or risk limits prevent simulation;
- `expired`: the plan is beyond its validity window.

An order with `after_order_id` does not become ready with its predecessor. It remains `waiting_dependency` until a future simulator observes the preceding fill.

## Cross-day Strategy Continuity

A new plan reads the most recent completed plan for the same symbol and runtime lane and records:

- `previous_plan_id`: the previous plan's identity;
- `continuity_action`: `retain`, `amend`, or `replace`; the first plan uses `baseline`;
- `change_summary`: which instructions continue, are cancelled, or are replaced.

The planner never assumes that a previous order filled. It carries strategy and instruction relationships only, so no account state is required and development plans cannot connect to production plans.

Each new plan is the single authoritative snapshot for that symbol, not an incremental patch. A still-valid instruction must appear again; an omitted instruction is considered cancelled or replaced. Older files remain available for audit.

## Feishu Notification

When Feishu group delivery is enabled, a new result with a valid `执行计划.json` sends one merged card: parameterized execution instructions first, then the full decision summary.

Instructions are grouped and numbered by scenario, with state, meaning, trigger, and exact order. States such as “immediate intent,” “GTC limit orders available now,” “awaiting confirmation,” and “awaiting compound conditions” make clear that waiting instructions are not simultaneous open orders.

The local program validates and renders the JSON without an LLM call. The full card shares one `feishu` delivery state and retry. Failure cannot rerun paid analysis; older results without a plan use the original decision-only card.

## Enablement

“Parameterized execution plan” is an independent control-panel switch. Manual runs can enable it explicitly:

```bash
uv run --frozen python my_scripts/roguetrader0.py \
  --ticker BTC-USD \
  --date 2026-09-13 \
  --execution-plan \
  --no-debug
```

Enabling it adds one quick-model call. Disable it with `--no-execution-plan` or `ROGUETRADER_EXECUTION_PLAN_ENABLED=0`.
