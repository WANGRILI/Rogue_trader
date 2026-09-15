# 60-Day Validation Archive

[中文](README.md) · [English](README.en.md)

Decision eligibility begins at `2026-07-13T14:26:03Z`; the market evidence is aligned to a complete hourly grid from `2026-07-13T15:00:00Z` to `2026-09-11T15:00:00Z`, exactly 1,440 confirmed 1H bars. Results are retrospective simulations with modeled fees and slippage, not live performance.

| Layer | Purpose | Headline |
|---|---|---|
| [Signal / Plan Lifecycle Quality](<信号质量/20260915_085854__BTC_USD/Signal Quality Report.md>) | Compare each natural plan lifetime with local inaction | 27 executed plans; 48.15% value-add hit rate, -0.08% mean |
| [OHLCV baseline](基础版/20260915_015514__BTC_USD/回测报告.md) | Execute only market-verifiable conditions | +8.03%, -2.24% max drawdown, 22 fills |
| [Multi-factor replay](多因子版/20260915_015525__BTC_USD/回测报告.md) | Replay compound conditions with exact evidence and declared proxies | +6.82%, -2.57% max drawdown, 35 fills |
| Fully invested BTC | Same-window benchmark | +25.47%, -6.45% max drawdown |

One August 20 delayed repair lacked a contemporaneous data snapshot and was quarantined as a day with no new trade. Its private source artifacts remain available for audit. This public archive contains de-identified reports, metrics, plan/order lifecycles, fills, and equity data—never secrets, runtime logs, full decision text, or machine-local paths.

See the [Signal Quality method](../docs/signal-quality.en.md) and [60-day retrospective](../docs/validation-evidence.en.md).
