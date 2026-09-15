# 参数化委托回测

[中文](execution-backtest.md) · [English](execution-backtest.en.md)

回测器默认消费经过研究资格门禁和确定性链路重接的 `my_results/研究/参数化委托.csv`，不重新调用 Agent，也不修改原始执行计划。它把参数化委托绑定到连续模拟组合，并使用已确认的 1H OHLCV 复原能够被行情验证的成交。

本页描述保留不变的 OHLCV 基线。需要复原 ETF、情绪、资金费率、链上与复合条件时，使用平行的[多因子条件回测](execution-backtest-multifactor.md)；两个版本分别输出，互不覆盖。

## 时间与数据

CSV v2 为每条委托保存 `order_generated_at`、`order_generated_at_source` 和 `order_valid_until`。时间只允许使用真实的 `最终决策.generated_at`；缺失、早于任务开始或历史修复明显跨时段且无匹配快照时安全失败，不再回填虚构的时间。

默认数据来自 `crypto_data_lake` 的 OKX BTC-USDT 现货 1H 已确认 K 线。未完结 K 线被过滤；重复时间、非法 OHLCV 或低于 99% 的连续覆盖会阻止回测。BTC-USD 决策映射到 BTC-USDT 现货，因此结果仍包含 USD/USDT 基差未建模的限制。

## 成交模型

- 初始组合默认是 10,000 USDT 等值 BTC、现金为零。
- 每份新计划生效时，`X_POSITION` 和 `X_CASH` 绑定到当时组合状态；同标的新计划替换旧计划，未成交 GTC 委托被撤销。
- 委托只能从生成时间后的下一根完整 K 线开始成交，避免使用信号形成前的当根 K 线高低价。
- 市价单在安全 K 线开盘成交；限价和止损单必须被后续 OHLC 区间实际触及，并计入手续费和滑点。
- 同一根 K 线无法确定止盈、止损或多笔委托的盘中先后时，使用保守顺序。
- `manual_confirmation` 场景不会仅凭价格数据推测成交，而是在委托状态表中保留为未评估。

## 运行与输出

```bash
ops/rebuild-research-ledger --ticker BTC-USD
ops/backtest-execution-ledger --ledger my_results/研究/参数化委托.csv
```

默认输出到 `my_results/回测结果/<时间>__BTC_USD/`：

```text
回测指标.json
权益曲线.csv
成交明细.csv
委托回测状态.csv
回测报告.md
回测报告.html
```

报告会同时给出策略与满仓持有基准、回撤、成本、成交数和 OHLCV 可评价覆盖率。部分覆盖的结果只能衡量行情可验证指令，不能被表述为完整 Agent 决策质量。
