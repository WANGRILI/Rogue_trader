# Multi-factor Conditional Backtest

[中文](execution-backtest-multifactor.md) · [English](execution-backtest-multifactor.en.md)

The multi-factor replay lives beside the original OHLCV backtest. It consumes the same `参数化委托.csv` while reconstructing ETF flow, sentiment, funding, network metrics, daily/weekly confirmation, volume, and macro-event conditions under explicit availability rules. Existing backtest bundles are never mutated or overwritten.

## Evidence model

- **Exact evidence** means both the historical field and explicit threshold can be reconstructed directly, such as a daily close, ETF-flow streak, funding rate, or MVRV.
- **Declared proxy** covers unavailable public history or subjective language, including NVT, CME basis, institutional volume quality, news shocks, and stabilization. Every fixed proxy is disclosed in the audit and is never presented as the original signal truth.
- A factor becomes visible only after its `available_at`. A confirmed condition can execute no earlier than the next complete 1H bar.
- The current factor bundle is a historical snapshot collected after the evaluation window, not a daily-sealed provider vintage. Availability gates reduce overt look-ahead but cannot rule out later source revisions.
- The run fails if any external condition lacks an audit record; nothing is silently skipped.

## Data sources

| Factor | Historical source | Availability convention |
|---|---|---|
| Fear & Greed | [Alternative.me](https://alternative.me/crypto/fear-and-greed-index/) | Provider timestamp |
| US spot BTC ETF daily flow | [Farside Investors](https://farside.co.uk/bitcoin-etf-flow-all-data/) | Trade date + 24 hours |
| BTC perpetual funding | Local lake sourced from [OKX Funding History](https://app.okx.com/docs-v5/en/#public-data-rest-api-get-funding-rate-history) | Settlement timestamp |
| MVRV, hash rate, market volume | [Coin Metrics Community API](https://docs.coinmetrics.io/api/v4/) | Metric date + 24 hours |
| Coinbase daily volume | [Coinbase Exchange Candles](https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-product-candles) | Candle start + 24 hours |
| FOMC / CPI | [Federal Reserve](https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm) / [BLS](https://www.bls.gov/schedule/news_release/cpi.htm) | Official release time |

## Run

```bash
ops/collect-backtest-factors
ops/backtest-execution-ledger-multifactor
```

The local data lake is auto-discovered; `ROGUETRADER_OHLCV_PATH` and `ROGUETRADER_FUNDING_PATH` can override it. Collected evidence is written under `my_results/回测数据/多因子条件版/`, with results under `my_results/回测结果/多因子条件版/`. Both use immutable timestamped bundles and remain Git-ignored.

Every result includes metrics, equity, fills, per-order status, condition audit, data quality, provenance, Markdown/HTML reports, and a validation notebook. Interpret performance together with exact/proxy coverage and the OHLCV baseline; a short proxy-assisted replay is not live-performance evidence.
