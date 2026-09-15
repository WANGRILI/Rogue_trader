# Parameterized Order Backtest

[中文](execution-backtest.md) · [English](execution-backtest.en.md)

The backtester defaults to the research-qualified and deterministically re-linked `my_results/研究/参数化委托.csv`. It neither calls an agent again nor mutates source execution plans. Parameterized orders bind to one continuous paper portfolio and confirmed 1H OHLCV reconstructs only fills that market data can verify.

This page documents the preserved OHLCV baseline. Use the parallel [multi-factor conditional backtest](execution-backtest-multifactor.en.md) to reconstruct ETF, sentiment, funding, network, and compound conditions. The two versions write separate immutable outputs.

## Time and Data

CSV v2 stores `order_generated_at`, `order_generated_at_source`, and `order_valid_until` for every row. Only the real `最终决策.generated_at` is accepted. A missing, pre-run, or materially delayed repair timestamp without a matching snapshot fails closed; no synthetic timestamp is backfilled.

The default source is confirmed OKX BTC-USDT spot 1H OHLCV curated by `crypto_data_lake`. Unfinished bars are filtered; duplicate timestamps, invalid OHLCV, or less than 99% continuous coverage fail the run. BTC-USD decisions map to BTC-USDT spot, so USD/USDT basis remains an unmodeled limitation.

## Execution Model

- The default initial portfolio is 10,000 USDT of BTC and zero cash.
- At each new plan, `X_POSITION` and `X_CASH` bind to the current portfolio. A newer plan replaces its predecessor and cancels unfilled GTC orders.
- An order can execute only from the first complete bar after its availability time, preventing pre-signal highs and lows from leaking into fills.
- Market orders fill at the safe bar open. Limit and stop orders require a subsequent OHLC touch and include fees and slippage.
- When one bar cannot reveal the intrabar order of stops, targets, or multiple instructions, the simulator uses a conservative ordering.
- `manual_confirmation` scenarios remain unevaluated rather than being inferred from price alone.

## Run and Outputs

```bash
ops/rebuild-research-ledger --ticker BTC-USD
ops/backtest-execution-ledger --ledger my_results/研究/参数化委托.csv
```

The default bundle is written under `my_results/回测结果/<timestamp>__BTC_USD/`:

```text
回测指标.json
权益曲线.csv
成交明细.csv
委托回测状态.csv
回测报告.md
回测报告.html
```

The report compares the order strategy with fully invested buy-and-hold and includes drawdown, costs, fills, and OHLCV evaluation coverage. A partial-coverage result measures only market-verifiable instructions; it is not evidence about the complete agent decision process.
