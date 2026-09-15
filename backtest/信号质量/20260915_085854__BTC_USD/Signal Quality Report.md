# Signal Quality · 60-Day Plan Lifecycle Replay

## Executive readout

This report evaluates RogueTrader as a stateful daily control loop. A decision is active only until the next eligible plan, its own expiry, or the end of the evidence window. Parameterized triggers, fills, take-profit, and stop-loss events come from the existing execution replay; no fixed forward horizon is imposed.

- Plans: **55**; plans with at least one fill: **27** (49.09%).
- Active-plan coverage: **99.79%** of 1H bars; **3** bars had no active plan after an expiry.
- Parameterized orders: **298**; filled: **35** (11.74%).
- Executed plans beating the same-state no-action counterfactual: **48.15%**.
- Mean / median lifecycle value add among executed plans: **-0.08% / -0.01%**.
- Positive / negative executed plans: **13 / 14**; average gain / loss: **0.30% / -0.44%**.
- Completed sell targets: **6 limit / 0 stop**; bracket exits: **0 take-profit / 0 stop-loss**.
- End-to-end portfolio replay remains **6.82%**, versus BTC **25.47%**, with max drawdown **-2.57%**.

| Plan date | Action | Lifecycle value add | Fills | Active time |
| --- | --- | ---: | ---: | ---: |
| 2026-07-31 | SELL | 1.03% | 1 | 48h |
| 2026-08-21 | BUY | 0.75% | 4 | 48h |
| 2026-07-20 | HOLD | 0.48% | 2 | 42h |
| 2026-07-13 | SELL | -3.27% | 1 | 25h |
| 2026-08-19 | OVERWEIGHT | -0.75% | 1 | 48h |
| 2026-08-28 | BUY | -0.57% | 1 | 24h |

## Interpretation boundary

Lifecycle value add compares the actual plan with freezing the exact incoming cash and BTC position over the same natural lifecycle. It is a local decision contribution—not a standalone trade return or pure Forecast Alpha. Untriggered conditions are preserved as dormant, expired, or superseded states and are never scored as losses. The result retains historical sizing, fees, slippage, and declared proxy conditions; Portfolio PnL remains the final system-level acceptance metric.
