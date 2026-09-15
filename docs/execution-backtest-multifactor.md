# 多因子条件回测

[中文](execution-backtest-multifactor.md) · [English](execution-backtest-multifactor.en.md)

多因子条件版与原 OHLCV 回测平行存在：它复用同一份 `参数化委托.csv`，并按显式可用时间规则复原 ETF 流量、市场情绪、资金费率、链上指标、日/周线确认、成交量和宏观事件条件。原回测结果不会被修改或覆盖。

## 证据模型

- **精确证据**：历史字段和明确阈值都可直接复原，例如日线收盘、ETF 连续流入、资金费率或 MVRV。
- **声明代理**：原始历史不可公开验证或语义主观，例如 NVT、CME 基差、机构成交质量、新闻冲击与“企稳”。代理规则固定写入审计，绝不伪装为原信号真值。
- 每个条件只在 `available_at` 之后可见；条件确认后，委托最早从下一根完整 1H K 线开始成交。
- 当前因子包是回测结束后采集的历史截面，不是每天封存的供应商 vintage；因此可用时间门禁能降低显性穿越，但不能排除来源事后修订。
- 只要一条外部条件没有留下审计记录，完整回测就会失败，不会静默跳过。

## 数据来源

| 因子 | 历史来源 | 可用时间口径 |
|---|---|---|
| 恐慌贪婪 | [Alternative.me](https://alternative.me/crypto/fear-and-greed-index/) | 来源时间戳 |
| 美国现货 BTC ETF 日流量 | [Farside Investors](https://farside.co.uk/bitcoin-etf-flow-all-data/) | 交易日后 24 小时 |
| BTC 永续资金费率 | 本地数据湖，源接口为 [OKX Funding History](https://app.okx.com/docs-v5/en/#public-data-rest-api-get-funding-rate-history) | 结算时间戳 |
| MVRV、算力、市场成交量 | [Coin Metrics Community API](https://docs.coinmetrics.io/api/v4/) | 指标日期后 24 小时 |
| Coinbase 日成交量 | [Coinbase Exchange Candles](https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-product-candles) | K 线开始后 24 小时 |
| FOMC / CPI | [Federal Reserve](https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm) / [BLS](https://www.bls.gov/schedule/news_release/cpi.htm) | 官方公布时间 |

## 运行

```bash
ops/collect-backtest-factors
ops/backtest-execution-ledger-multifactor
```

本地数据湖可自动发现，也可用 `ROGUETRADER_OHLCV_PATH` 和 `ROGUETRADER_FUNDING_PATH` 指定。采集数据写入 `my_results/回测数据/多因子条件版/`；结果写入 `my_results/回测结果/多因子条件版/`。两个目录均使用不可变时间戳子目录并被 Git 忽略。

每份结果包含指标、权益、成交、逐单状态、条件审计、数据质量、来源清单、Markdown/HTML 报告和可复核 Notebook。收益结论必须同时阅读精确/代理覆盖和原 OHLCV 基线，不能把短样本代理回测解释为完整实盘业绩。
