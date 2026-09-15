# Signal / Plan Lifecycle Quality

[中文](signal-quality.md) · [English](signal-quality.en.md)

RogueTrader's daily output is a state update in a continuous strategy, not an independent fixed-horizon forecast. Signal quality is therefore evaluated over each parameterized plan's natural lifecycle rather than forcing every daily rating into 1/3/7/14/30-day samples.

## Evaluation protocol

- Signal definitions come from the research-qualified parameterized-order ledger. Triggers, fills, take-profits, and stop-losses come from the matching hourly multi-factor replay.
- A plan starts on its first executable 1H bar and ends when the next eligible plan takes effect, the plan expires, or the evidence window ends.
- A new plan updates strategy state. Later market movement is never attributed back to the superseded plan. A quarantined date creates no new plan, so the last eligible plan continues naturally.
- Each plan is compared with a same-state no-action counterfactual that freezes its exact incoming cash and BTC position: `lifecycle value add = actual plan return − no-action return`.
- An untriggered condition is recorded as dormant, expired, or superseded—not as a loss. Every order retains its terminal state and completion timestamp.

This evaluates the complete plan update, including conditions, sizing, and exit rules; it is not pure directional Forecast Alpha. Fees, slippage, and historical portfolio state remain in scope. Full 60-day Portfolio PnL remains the final system-level acceptance result.

```bash
ops/evaluate-signal-quality \
  --execution-result backtest/多因子版/<RUN_ID> \
  --ledger my_results/研究/参数化委托.csv \
  --window-start 2026-07-13T14:26:03+00:00 \
  --window-days 60
```

The bundle contains plan lifecycles, order lifecycles, machine-readable metrics, bilingual Markdown reports, and an HTML summary.
