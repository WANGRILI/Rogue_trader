"""Parallel multi-factor replay for historical execution plans."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import html
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any

import pandas as pd

from roguetrader.backtest.condition_resolver import MultiFactorConditionResolver
from roguetrader.backtest.execution_ledger import (
    DEFAULT_LEDGER,
    DEFAULT_MARKET_DATA,
    DEFAULT_OUTPUT_ROOT,
    ExecutionBacktestError,
    _svg_equity,
    load_confirmed_ohlcv,
    load_execution_ledger,
    portable_source_reference,
    run_backtest,
    select_evaluation_window,
)
from roguetrader.backtest.factor_data import DEFAULT_FACTOR_ROOT, FactorDataError
from roguetrader.backtest.factor_store import build_feature_store


DEFAULT_MULTIFACTOR_OUTPUT_ROOT = DEFAULT_OUTPUT_ROOT / "多因子条件版"
SOURCE_DOCUMENTATION = {
    "fear_greed": "https://alternative.me/crypto/fear-and-greed-index/",
    "etf_flow": "https://farside.co.uk/bitcoin-etf-flow-all-data/",
    "funding_rate": "https://app.okx.com/docs-v5/en/#public-data-rest-api-get-funding-rate-history",
    "network_metrics": "https://docs.coinmetrics.io/api/v4/",
    "coinbase_candles": "https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-product-candles",
    "fomc_calendar": "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
    "cpi_calendar": "https://www.bls.gov/schedule/news_release/cpi.htm",
}


def _latest_directory(root: str | Path) -> Path:
    source = Path(root).expanduser().resolve()
    candidates = sorted(path for path in source.glob("*__BTC_USD") if path.is_dir())
    if not candidates:
        raise FactorDataError(f"没有可用的多因子数据包：{source}")
    return candidates[-1]


def _latest_baseline(
    root: str | Path,
    *,
    window_start: str | None = None,
    window_days: int | None = None,
) -> Path | None:
    source = Path(root).expanduser().resolve()
    candidates: list[Path] = []
    for metrics_path in source.rglob("回测指标.json"):
        try:
            metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if metrics.get("condition_model") != "manual_conditions_not_evaluated":
            continue
        if window_start is None:
            if metrics.get("requested_window_days") is not None:
                continue
        elif (
            metrics.get("requested_window_start") != window_start
            or metrics.get("requested_window_days") != window_days
        ):
            continue
        candidates.append(metrics_path.parent)
    candidates.sort(key=lambda path: path.stat().st_mtime)
    return candidates[-1] if candidates else None


def _read_baseline(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    return json.loads((path / "回测指标.json").read_text(encoding="utf-8"))


def _pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def _markdown_report(
    metrics: dict[str, Any],
    market_quality: dict[str, Any],
    factor_quality: dict[str, Any],
    baseline: dict[str, Any] | None,
) -> str:
    exact = metrics["manual_exact_order_count"]
    proxy = metrics["manual_proxy_order_count"]
    unevaluated = metrics["manual_unevaluated_order_count"]
    comparison = (
        f"基线 OHLCV 版收益率 **{_pct(baseline['total_return'])}**、最大回撤 "
        f"**{_pct(baseline['max_drawdown'])}**；多因子版相对基线收益差为 "
        f"**{_pct(metrics['total_return'] - baseline['total_return'])}**。"
        if baseline
        else "未找到可比较的 OHLCV 基线数据包。"
    )
    window_line = (
        f"- 展示窗口为严格 **{metrics['requested_window_days']}×24 小时**："
        f"`{metrics.get('bar_window_start', metrics['requested_window_start'])}` 至 "
        f"`{metrics.get('bar_window_end_exclusive', metrics['requested_window_end_exclusive'])}`"
        "（K 线开盘时间右侧开区间）。\n"
        if metrics.get("requested_window_days")
        else ""
    )
    integrity = metrics.get("research_integrity") or {}
    integrity_line = (
        f"- 研究资格门禁纳入 {integrity.get('eligible_decisions', 0)} 份决策、隔离 "
        f"{integrity.get('excluded_decisions', 0)} 份；隔离日期不产生新计划。\n"
        if integrity else ""
    )
    return f"""# 多因子条件回测：完整执行计划的历史重放

## 执行摘要

- 多因子策略期末权益 **{metrics['final_equity']:.2f} USDT**，区间收益 **{_pct(metrics['total_return'])}**；同期满仓持有基准 **{_pct(metrics['benchmark_return'])}**。
- 最大回撤 **{_pct(metrics['max_drawdown'])}**，成交 **{metrics['fill_count']}** 次，费用 **{metrics['total_fees']:.2f} USDT**。
- 共处理 **{metrics['total_order_count']}** 条委托：OHLCV 原生可验证 {metrics['ohlcv_evaluable_order_count']} 条，外部条件精确证据 {exact} 条，声明代理 {proxy} 条，未评估 {unevaluated} 条；总条件覆盖率 **{_pct(metrics['condition_evaluation_coverage'])}**。
- 触发外部条件场景 **{metrics['triggered_manual_scenario_count']}** 个，其中精确证据 {metrics['triggered_exact_scenario_count']} 个、代理证据 {metrics['triggered_proxy_scenario_count']} 个。条件确认后统一从下一根 1H K 线开始执行，防止同根 K 线时间穿越。
- {comparison}
{integrity_line}
{window_line}

## 方法与口径

- 复用同一份参数化委托、初始 10,000 USDT 等值 BTC、手续费 {_pct(metrics['fee_rate'])}、滑点 {_pct(metrics['slippage_rate'])}；新计划仍会替换旧计划。
- 日线、周线、ETF、情绪与链上指标只在各自 `available_at` 之后进入特征表；ETF 日流量采用交易日后 24 小时的保守可用时间。
- 因子包是 `{factor_quality['manifest_collected_at']}` 采集的历史截面，并非逐日封存的供应商 vintage；可用时间门禁降低显性穿越，但不能排除来源事后修订。
- “精确证据”表示字段与阈值可直接历史复原；“声明代理”表示原条件缺少免费可验证历史（如 NVT、CME 基差或主观形态），使用固定替代规则。两类结果分开计数，代理结果不能等同原信号真值。
- 每个场景的规则、字段、最后观测与触发时间均写入 `条件解析审计.csv`，数据文件以 SHA-256 清单锁定。

## 数据质量

- 行情：{market_quality['confirmed_rows']} 根已确认 1H K 线，覆盖率 {_pct(market_quality['coverage_ratio'])}，区间 `{market_quality['start']}` 至 `{market_quality['end']}`。
- 特征：回测区间 {factor_quality['evaluation_rows']} 个逐时截面（含历史预热共 {factor_quality['hourly_rows']} 行），最低核心字段覆盖率 {_pct(factor_quality['minimum_required_coverage'])}；无重复时间戳。
- 因子数据包：`{factor_quality['bundle']}`，采集时间 `{factor_quality['manifest_collected_at']}`。

## 解释边界

本报告比 OHLCV 基线更接近完整 Agent 团队的历史执行，但仍不是无偏的实盘业绩证明。尤其 NVT、CME/机构成交质量、新闻冲击和主观“企稳”条件只能使用明确代理；短样本、单一 BTC 标的和执行假设也会显著影响结论。应同时阅读精确证据结果、代理敏感性与原始完整报告，而不是只看单一收益率。
"""


def _html_report(
    report: str,
    metrics: dict[str, Any],
    curve: pd.DataFrame,
    baseline: dict[str, Any] | None,
) -> str:
    comparison = (
        f"{_pct(metrics['total_return'] - baseline['total_return'])}"
        if baseline
        else "N/A"
    )
    summary_lines = [
        line[2:].replace("**", "").replace(chr(96), "")
        for line in report.split("## 方法与口径", 1)[0].splitlines()
        if line.startswith("- ")
    ]
    summary = "".join(f"<li>{html.escape(line)}</li>" for line in summary_lines)
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>RogueTrader 多因子条件回测</title><style>
:root{{--bg:#07101c;--panel:#101c30;--text:#eef5ff;--muted:#91a3bd;--mint:#5ce0b7;--gold:#e7c875;--line:#263854}}
*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 80% 0,#15294a 0,#07101c 45%);color:var(--text);font:15px/1.65 system-ui,sans-serif}}
main{{max-width:1080px;margin:auto;padding:52px 24px}}.eyebrow{{color:var(--mint);letter-spacing:.14em;text-transform:uppercase}}h1{{font-size:36px;line-height:1.2;margin:8px 0}}.muted,.label{{color:var(--muted)}}
.grid{{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin:28px 0}}.card,.chart,.summary{{background:rgba(16,28,48,.92);border:1px solid var(--line);border-radius:16px;padding:18px}}.metric{{font-size:24px;font-weight:750}}.accent{{color:var(--mint)}}.gold{{color:var(--gold)}}
.chart{{padding:18px;overflow:hidden}}svg{{width:100%;height:auto}}.summary{{margin-top:20px;border-left:3px solid var(--mint)}}ul{{margin:0;padding-left:21px}}li+li{{margin-top:8px}}
.note{{margin-top:28px;color:var(--muted);border-top:1px solid var(--line);padding-top:20px}}@media(max-width:840px){{.grid{{grid-template-columns:repeat(2,1fr)}}h1{{font-size:29px}}}}
</style></head><body><main><div class="eyebrow">RogueTrader · Multi-factor Evidence</div><h1>完整执行计划的历史重放</h1><p class="muted">Availability-lagged factors · Exact / Proxy evidence separated · Immutable bundle</p>
<div class="grid"><div class="card"><div class="label">策略收益</div><div class="metric accent">{_pct(metrics['total_return'])}</div></div><div class="card"><div class="label">持有基准</div><div class="metric">{_pct(metrics['benchmark_return'])}</div></div><div class="card"><div class="label">最大回撤</div><div class="metric">{_pct(metrics['max_drawdown'])}</div></div><div class="card"><div class="label">较 OHLCV 版</div><div class="metric gold">{comparison}</div></div><div class="card"><div class="label">条件覆盖</div><div class="metric">{_pct(metrics['condition_evaluation_coverage'])}</div></div></div>
<div class="chart">{_svg_equity(curve)}</div><div class="summary"><ul>{summary}</ul></div>
<p class="note">精确证据与声明代理已在审计表中分列。本报告衡量固定规则下的历史重放，不把代理条件包装成原始信号真值，也不代表实盘收益承诺。</p></main></body></html>"""


def _validation_notebook() -> dict[str, Any]:
    code = """from pathlib import Path
import json
import pandas as pd

ROOT = Path.cwd()
metrics = json.loads((ROOT / '回测指标.json').read_text(encoding='utf-8'))
audit = pd.read_csv(ROOT / '条件解析审计.csv')
status = pd.read_csv(ROOT / '委托回测状态.csv')
fills = pd.read_csv(ROOT / '成交明细.csv')

assert len(status) == metrics['total_order_count']
assert status['order_event_id'].is_unique
assert len(fills) == metrics['fill_count']
assert audit[['plan_id', 'scenario_id']].duplicated().sum() == 0
print({
    'orders': len(status),
    'fills': len(fills),
    'condition_coverage': metrics['condition_evaluation_coverage'],
    'exact': metrics['manual_exact_order_count'],
    'proxy': metrics['manual_proxy_order_count'],
})
"""
    return {
        "cells": [
            {
                "cell_type": "markdown",
                "metadata": {},
                "source": ["# RogueTrader 多因子回测验证\n", "加载同目录产物并复核行数、唯一键与覆盖率。"],
            },
            {
                "cell_type": "code",
                "execution_count": None,
                "metadata": {},
                "outputs": [],
                "source": code.splitlines(keepends=True),
            },
        ],
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python", "version": "3"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_multifactor_bundle(
    result: dict[str, Any],
    market_quality: dict[str, Any],
    factor_quality: dict[str, Any],
    factor_manifest: dict[str, Any],
    output_root: str | Path,
    ticker: str,
    baseline_path: Path | None,
) -> Path:
    output = Path(output_root).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    target = output / f"{stamp}__{ticker.replace('-', '_')}"
    temporary = Path(tempfile.mkdtemp(prefix=".writing-", dir=output))
    os.chmod(temporary, 0o700)
    try:
        baseline = _read_baseline(baseline_path)
        factor_manifest = json.loads(json.dumps(factor_manifest))
        for source in factor_manifest.get("sources", []):
            if source.get("path"):
                source["path"] = portable_source_reference(source["path"])
        metrics = dict(result["metrics"])
        factor_quality = dict(factor_quality)
        factor_quality["bundle"] = (
            "private_results/回测数据/多因子条件版/"
            f"{Path(str(factor_quality['bundle'])).name}"
        )
        metrics["market_data_quality"] = market_quality
        metrics["factor_data_quality"] = factor_quality
        metrics["baseline_result"] = (
            "private_results/回测结果/60日窗口/基础版/"
            f"{baseline_path.name}"
            if baseline_path
            else None
        )
        metrics["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        (temporary / "回测指标.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
        result["equity"].to_csv(temporary / "权益曲线.csv", encoding="utf-8-sig")
        result["fills"].to_csv(temporary / "成交明细.csv", index=False, encoding="utf-8-sig")
        result["order_status"].to_csv(temporary / "委托回测状态.csv", index=False, encoding="utf-8-sig")
        result["condition_audit"].to_csv(temporary / "条件解析审计.csv", index=False, encoding="utf-8-sig")
        (temporary / "数据质量.json").write_text(json.dumps({"market": market_quality, "factors": factor_quality}, ensure_ascii=False, indent=2), encoding="utf-8")
        (temporary / "数据来源.json").write_text(json.dumps({"factor_manifest": factor_manifest, "documentation": SOURCE_DOCUMENTATION}, ensure_ascii=False, indent=2), encoding="utf-8")
        report = _markdown_report(metrics, market_quality, factor_quality, baseline)
        (temporary / "回测报告.md").write_text(report, encoding="utf-8")
        (temporary / "回测报告.html").write_text(_html_report(report, metrics, result["equity"], baseline), encoding="utf-8")
        (temporary / "验证方法.ipynb").write_text(json.dumps(_validation_notebook(), ensure_ascii=False, indent=2), encoding="utf-8")
        for path in temporary.iterdir():
            if path.is_file():
                os.chmod(path, 0o600)
        manifest_files = {
            path.name: {"sha256": _sha256(path), "bytes": path.stat().st_size}
            for path in temporary.iterdir()
            if path.is_file()
        }
        (temporary / "结果清单.json").write_text(json.dumps({"schema_version": "1.0", "files": manifest_files}, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(temporary / "结果清单.json", 0o600)
        os.replace(temporary, target)
        os.chmod(target, 0o700)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return target


def run_multifactor_backtest(
    *,
    factor_bundle: str | Path | None = None,
    ledger_path: str | Path = DEFAULT_LEDGER,
    market_data_path: str | Path = DEFAULT_MARKET_DATA,
    output_root: str | Path = DEFAULT_MULTIFACTOR_OUTPUT_ROOT,
    baseline_result: str | Path | None = None,
    ticker: str = "BTC-USD",
    initial_balance: float = 10_000.0,
    fee_rate: float = 0.001,
    slippage_rate: float = 0.0005,
    window_start: str | pd.Timestamp | None = None,
    window_days: int | None = None,
) -> tuple[Path, dict[str, Any]]:
    factor_path = Path(factor_bundle).resolve() if factor_bundle else _latest_directory(DEFAULT_FACTOR_ROOT)
    plans = load_execution_ledger(ledger_path, ticker)
    market_history, market_quality = load_confirmed_ohlcv(market_data_path)
    plans, market, window_metadata = select_evaluation_window(
        plans,
        market_history,
        window_start=window_start,
        window_days=window_days,
    )
    baseline_path = (
        Path(baseline_result).resolve()
        if baseline_result
        else _latest_baseline(
            DEFAULT_OUTPUT_ROOT,
            window_start=window_metadata.get("requested_window_start"),
            window_days=window_metadata.get("requested_window_days"),
        )
    )
    evaluation_start = (
        pd.Timestamp(window_metadata["requested_window_start"])
        if window_metadata
        else None
    )
    evaluation_end = (
        pd.Timestamp(window_metadata["requested_window_end_exclusive"])
        if window_metadata
        else None
    )
    # Keep complete confirmed history for rolling daily/weekly indicators.
    # Building from only the selected slice makes SMA200 silently unavailable.
    features, factor_quality = build_feature_store(
        market_history,
        factor_path,
        evaluation_start=evaluation_start,
        evaluation_end=evaluation_end,
    )
    factor_manifest = json.loads((factor_path / "数据清单.json").read_text(encoding="utf-8"))
    resolver = MultiFactorConditionResolver(features)
    result = run_backtest(
        plans,
        market,
        initial_balance=initial_balance,
        fee_rate=fee_rate,
        slippage_rate=slippage_rate,
        condition_resolver=resolver,
    )
    result["metrics"].update(window_metadata)
    projection_path = Path(ledger_path).expanduser().resolve().parent / "研究投影.json"
    if projection_path.is_file():
        projection = json.loads(projection_path.read_text(encoding="utf-8"))
        excluded_events = [
            {
                "event_id": item.get("event_id"),
                "analysis_date": item.get("analysis_date"),
                "reason_code": item.get("reason_code"),
            }
            for item in projection.get("plan_chain", [])
            if item.get("status") == "excluded"
            and (
                not window_start
                or str(pd.Timestamp(window_start).date())
                <= str(item.get("analysis_date"))
                <= str((pd.Timestamp(window_start) + pd.Timedelta(days=int(window_days or 0))).date())
            )
        ]
        result["metrics"]["research_integrity"] = {
            "policy": projection.get("policy"),
            "eligible_decisions": len(plans),
            "excluded_decisions": len(excluded_events),
            "excluded_events": excluded_events,
        }
    expected_conditions = {
        (plan.plan_id, scenario.scenario_id)
        for plan in plans
        for scenario in plan.scenarios
        if scenario.trigger_type == "manual_confirmation"
    }
    audited_conditions = {
        (str(row.plan_id), str(row.scenario_id))
        for row in result["condition_audit"].itertuples()
    }
    missing_conditions = expected_conditions - audited_conditions
    if missing_conditions or result["metrics"]["manual_unevaluated_order_count"]:
        raise ExecutionBacktestError(
            "多因子回测没有覆盖全部外部条件："
            f"缺少 {len(missing_conditions)} 个场景、"
            f"{result['metrics']['manual_unevaluated_order_count']} 条委托。"
        )
    target = write_multifactor_bundle(result, market_quality, factor_quality, factor_manifest, output_root, ticker, baseline_path)
    return target, result["metrics"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="运行 ETF、情绪、链上与复合条件的平行历史回测。")
    parser.add_argument("--factor-bundle", type=Path)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--market-data", type=Path, default=DEFAULT_MARKET_DATA)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_MULTIFACTOR_OUTPUT_ROOT)
    parser.add_argument("--baseline-result", type=Path)
    parser.add_argument("--ticker", default="BTC-USD")
    parser.add_argument("--initial-balance", type=float, default=10_000.0)
    parser.add_argument("--fee-rate", type=float, default=0.001)
    parser.add_argument("--slippage-rate", type=float, default=0.0005)
    parser.add_argument("--window-start", help="包含时区的固定窗口起点。")
    parser.add_argument("--window-days", type=int, help="固定窗口的自然日数。")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        target, metrics = run_multifactor_backtest(
            factor_bundle=args.factor_bundle,
            ledger_path=args.ledger,
            market_data_path=args.market_data,
            output_root=args.output_root,
            baseline_result=args.baseline_result,
            ticker=args.ticker,
            initial_balance=args.initial_balance,
            fee_rate=args.fee_rate,
            slippage_rate=args.slippage_rate,
            window_start=args.window_start,
            window_days=args.window_days,
        )
    except (ExecutionBacktestError, FactorDataError) as exc:
        raise SystemExit(f"multi-factor backtest error: {exc}") from None
    print(json.dumps({"output": str(target), "plans": metrics["plan_count"], "orders": metrics["total_order_count"], "fills": metrics["fill_count"], "return": metrics["total_return"], "condition_coverage": metrics["condition_evaluation_coverage"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
