# 60-Day Historical Validation: Profitable, but Not Yet Alpha

[中文](validation-evidence.md) · [English](validation-evidence.en.md)

RogueTrader now goes beyond generating research reports. Frozen decisions first pass a point-in-time eligibility gate, then enter plan-lifecycle diagnostics and continuous-portfolio simulation using the same parameterized orders and hourly execution evidence.

## Results at a glance

Decision eligibility begins at 22:26 on July 13, 2026 (`Asia/Shanghai`). Market evidence is aligned to the complete hourly grid from 23:00 on July 13 to 23:00 on September 11: exactly 60×24 hours and 1,440 1H bars.

| | OHLCV baseline | Multi-factor replay | Fully invested BTC |
|---|---:|---:|---:|
| Period return | **+8.03%** | **+6.82%** | +25.47% |
| Maximum drawdown | -2.24% | -2.57% | -6.45% |
| Fills | 22 | 35 | 0 |
| Simulated costs | 39.95 USDT | 53.34 USDT | — |

The window contains 55 research-eligible plans and 298 parameterized orders. The baseline evaluates 56 orders that OHLCV can confirm directly. The multi-factor version evaluates another 242 compound-condition orders: 105 have directly reconstructable threshold evidence, 137 use explicitly documented historical proxies, and none remain unevaluated. Long-horizon technical indicators use full-history warm-up; compound or qualitative price language is no longer presented as exact evidence.

One August 20 decision was actually repaired on August 24 and incorporated later market material. It is now quarantined from research: August 20 introduces no new plan, and the eligible August 19 GTC plan remains in force until the August 21 decision. The original report remains available for audit but no longer enters evaluation.

## What the evidence says

**Data eligibility changes the investment conclusion.** Removing one temporally contaminated decision corrected the OHLCV baseline from +4.60% to +8.03%. This is not strategy optimization; it is an input-governance correction, demonstrating that “what was actually knowable then” must be verified before interpreting returns.

**Risk remained materially lower, at the cost of missing the trend.** Maximum drawdown was roughly one-third of fully invested BTC, but the baseline and multi-factor versions still trailed by about 17.44 and 18.66 percentage points. The agent committee remained distinctly defensive in this sample.

**More conditions did not automatically create more alpha.** The multi-factor replay added 13 fills, raised turnover from 3.99× to 5.33× initial capital, added 13.40 USDT in costs, and finished 1.22 percentage points behind the baseline.

**Plan lifecycles show a sub-50% hit rate and weak expectancy.** The 55 plans form non-overlapping natural lifetimes; 27 produced at least one fill. Against a local counterfactual that freezes each plan's incoming cash and BTC position, 13 plans added value and 14 detracted, a 48.15% hit rate. Mean value add was -0.08% and median value add -0.01%; average positive contribution was +0.30%, versus -0.44% for negative contributions. This measure includes sizing, costs, and execution and is not presented as pure directional Forecast Alpha.

## Backtest boundaries

- The portfolio starts with 10,000 USDT of BTC and zero cash. This is a retrospective simulation, not live-account performance.
- Fees are 0.10% and slippage is 0.05%. A confirmed condition can execute no earlier than the next complete 1H bar.
- ETF flow, sentiment, funding, and network metrics enter only after their declared historical availability time. This reduces overt look-ahead, but post-window historical snapshots cannot rule out later provider revisions.
- NVT, CME/institutional volume quality, and subjective patterns lack equivalent free historical truth, so declared proxies remain separately audited.
- A historical repair without a same-date, same-parameter snapshot now fails closed; order availability is never fabricated as “run start plus N minutes.”
- Signal / Plan Lifecycle Quality diagnoses each strategy update against local inaction. Portfolio PnL accepts the end-to-end system. Both must be read together.

See [Signal Quality](signal-quality.en.md), the [baseline method](execution-backtest.en.md), and the [multi-factor method](execution-backtest-multifactor.en.md). Raw decisions, order history, and machine-local research data remain private and are not committed to the public repository.
