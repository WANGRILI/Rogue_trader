"""Evaluate parameterized decisions over their actual plan lifecycles."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any

import pandas as pd

from roguetrader.backtest.execution_ledger import (
    DEFAULT_LEDGER,
    DEFAULT_MARKET_DATA,
    ExecutionBacktestError,
    OrderSpec,
    PlanSpec,
    load_confirmed_ohlcv,
    load_execution_ledger,
    select_evaluation_window,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "my_results" / "回测结果" / "信号质量"
EPSILON = 1e-12
PROTECTIVE_REASONS = {"protective_stop_loss", "protective_take_profit"}


class SignalQualityError(ValueError):
    """Raised when lifecycle evidence is incomplete or inconsistent."""


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.is_file():
        raise SignalQualityError(f"执行回放缺少文件：{path.name}")
    try:
        return pd.read_csv(path)
    except (OSError, pd.errors.ParserError) as exc:
        raise SignalQualityError(f"无法读取 {path.name}：{exc}") from exc


def load_execution_evidence(path: str | Path) -> dict[str, Any]:
    """Load one execution replay bundle as the authoritative lifecycle evidence."""

    root = Path(path).expanduser().resolve()
    metrics_path = root / "回测指标.json"
    if not metrics_path.is_file():
        raise SignalQualityError("执行回放目录缺少回测指标.json。")
    try:
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SignalQualityError(f"无法读取回测指标.json：{exc}") from exc
    statuses = _read_csv(root / "委托回测状态.csv")
    fills = _read_csv(root / "成交明细.csv")
    curve = _read_csv(root / "权益曲线.csv")
    audit_path = root / "条件解析审计.csv"
    audit = _read_csv(audit_path) if audit_path.is_file() else pd.DataFrame()

    required = (
        ("委托回测状态.csv", statuses, {"order_event_id", "plan_id", "scenario_id", "order_id", "status"}),
        ("成交明细.csv", fills, {"time", "plan_id", "scenario_id", "order_id", "order_event_id", "fill_reason", "side", "fill_price"}),
        ("权益曲线.csv", curve, {"time", "equity", "cash", "position", "close"}),
    )
    for label, frame, fields in required:
        missing = fields - set(frame.columns)
        if missing:
            raise SignalQualityError(f"{label} 缺少字段：{sorted(missing)}")
    if statuses["order_event_id"].duplicated().any():
        raise SignalQualityError("委托回测状态存在重复 order_event_id。")
    if len(statuses) != int(metrics.get("total_order_count", -1)):
        raise SignalQualityError("委托状态行数与回测指标不一致。")
    if len(fills) != int(metrics.get("fill_count", -1)):
        raise SignalQualityError("成交行数与回测指标不一致。")

    curve["time"] = pd.to_datetime(curve["time"], utc=True, errors="coerce")
    if curve["time"].isna().any() or curve["time"].duplicated().any():
        raise SignalQualityError("权益曲线包含无效或重复时间戳。")
    curve = curve.set_index("time").sort_index()
    for field in ("equity", "cash", "position", "close"):
        curve[field] = pd.to_numeric(curve[field], errors="coerce")
    if curve[["equity", "cash", "position", "close"]].isna().any().any():
        raise SignalQualityError("权益曲线包含空值或非数值。")
    fills["time"] = pd.to_datetime(fills["time"], utc=True, errors="coerce")
    fills["fill_price"] = pd.to_numeric(fills["fill_price"], errors="coerce")
    if fills["time"].isna().any() or fills["fill_price"].isna().any():
        raise SignalQualityError("成交明细包含无效时间或成交价。")
    return {
        "metrics": metrics,
        "statuses": statuses.fillna(""),
        "fills": fills.fillna(""),
        "curve": curve,
        "condition_audit": audit.fillna(""),
    }


def _order_lookup(plans: tuple[PlanSpec, ...]) -> dict[str, tuple[PlanSpec, Any, OrderSpec]]:
    result: dict[str, tuple[PlanSpec, Any, OrderSpec]] = {}
    for plan in plans:
        for scenario in plan.scenarios:
            for order in scenario.orders:
                if order.event_id in result:
                    raise SignalQualityError("参数化委托包含重复 order_event_id。")
                result[order.event_id] = (plan, scenario, order)
    return result


def _first_bar(index: pd.DatetimeIndex, timestamp: pd.Timestamp) -> pd.Timestamp | None:
    available = index[index >= timestamp]
    return available[0] if len(available) else None


def _plan_boundaries(
    plans: tuple[PlanSpec, ...], curve: pd.DataFrame
) -> dict[str, dict[str, Any]]:
    """Build non-overlapping windows using the execution engine's transition order."""

    result: dict[str, dict[str, Any]] = {}
    for number, plan in enumerate(plans):
        start = _first_bar(curve.index, plan.generated_at)
        if start is None:
            continue
        next_start = (
            _first_bar(curve.index, plans[number + 1].generated_at)
            if number + 1 < len(plans)
            else None
        )
        expiry = _first_bar(curve.index, plan.valid_until)
        transition_candidates = [item for item in (next_start, expiry) if item is not None]
        transition = min(transition_candidates) if transition_candidates else None
        active = curve.index[curve.index >= start]
        if transition is not None:
            active = active[active < transition]
        if len(active) == 0:
            continue
        if next_start is not None and transition == next_start:
            reason = "superseded_by_next_plan"
        elif expiry is not None and transition == expiry:
            reason = "expired"
        else:
            reason = "window_end"
        result[plan.plan_id] = {
            "start": start,
            "end": active[-1],
            "end_reason": reason,
        }
    return result


def _evidence_grades(plans: tuple[PlanSpec, ...], audit: pd.DataFrame) -> dict[tuple[str, str], str]:
    grades: dict[tuple[str, str], str] = {}
    if not audit.empty and {"plan_id", "scenario_id", "evidence_grade"} <= set(audit.columns):
        for row in audit.itertuples(index=False):
            grades[(str(row.plan_id), str(row.scenario_id))] = str(row.evidence_grade)
    for plan in plans:
        for scenario in plan.scenarios:
            if scenario.trigger_type != "manual_confirmation":
                grades[(plan.plan_id, scenario.scenario_id)] = "exact_ohlcv"
    return grades


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _mean(values: pd.Series) -> float | None:
    numeric = pd.to_numeric(values, errors="coerce").dropna()
    return float(numeric.mean()) if not numeric.empty else None


def _median(values: pd.Series) -> float | None:
    numeric = pd.to_numeric(values, errors="coerce").dropna()
    return float(numeric.median()) if not numeric.empty else None


def evaluate_lifecycles(
    plans: tuple[PlanSpec, ...],
    market: pd.DataFrame,
    evidence: dict[str, Any],
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Evaluate daily plans from activation through their natural termination."""

    if not plans:
        raise SignalQualityError("窗口内没有可评价执行计划。")
    curve = evidence["curve"]
    if not curve.index.equals(market.index):
        raise SignalQualityError("权益曲线与选定 1H 行情窗口的时间轴不一致。")
    if not pd.Series(curve["close"].to_numpy()).equals(
        pd.Series(market["close"].astype(float).to_numpy())
    ):
        raise SignalQualityError("权益曲线与选定行情收盘价不一致。")

    lookup = _order_lookup(plans)
    statuses: pd.DataFrame = evidence["statuses"]
    status_ids = set(statuses["order_event_id"].astype(str))
    if status_ids != set(lookup):
        raise SignalQualityError(
            f"委托状态与窗口计划不一致：缺少 {len(set(lookup) - status_ids)}，"
            f"多出 {len(status_ids - set(lookup))}。"
        )
    fills: pd.DataFrame = evidence["fills"].copy()
    fills["time"] = pd.to_datetime(fills["time"], utc=True, errors="coerce")
    fills["fill_price"] = pd.to_numeric(fills["fill_price"], errors="coerce")
    if fills["time"].isna().any() or fills["fill_price"].isna().any():
        raise SignalQualityError("成交证据包含无效时间或价格。")
    ordinary_fills = fills[~fills["fill_reason"].isin(PROTECTIVE_REASONS)]
    if ordinary_fills["order_event_id"].duplicated().any():
        raise SignalQualityError("同一参数化委托出现多次普通成交。")
    if not set(ordinary_fills["order_event_id"].astype(str)) <= set(lookup):
        raise SignalQualityError("成交明细包含窗口计划之外的委托。")

    boundaries = _plan_boundaries(plans, curve)
    if len(boundaries) != len(plans):
        raise SignalQualityError("部分计划在执行时间轴中没有有效生命周期。")
    grades = _evidence_grades(plans, evidence["condition_audit"])
    fill_by_id = {
        str(row.order_event_id): row for row in ordinary_fills.itertuples(index=False)
    }
    protection_by_parent: dict[str, Any] = {}
    for row in fills[fills["fill_reason"].isin(PROTECTIVE_REASONS)].itertuples(index=False):
        parent = str(row.order_event_id).removesuffix("__protection")
        previous = protection_by_parent.get(parent)
        if previous is None or row.time < previous.time:
            protection_by_parent[parent] = row

    order_rows: list[dict[str, Any]] = []
    for state in statuses.itertuples(index=False):
        event_id = str(state.order_event_id)
        plan, scenario, order = lookup[event_id]
        boundary = boundaries[plan.plan_id]
        fill = fill_by_id.get(event_id)
        protection = protection_by_parent.get(event_id)
        filled = fill is not None
        if (str(state.status) == "filled") != filled:
            raise SignalQualityError(f"委托 {event_id} 的状态与成交明细不一致。")
        if protection is not None:
            completed_at = protection.time.isoformat()
            terminal_event = str(protection.fill_reason)
        elif fill is not None:
            completed_at = fill.time.isoformat()
            terminal_event = "order_filled"
        else:
            completed_at = boundary["end"].isoformat()
            terminal_event = str(state.status)
        order_rows.append(
            {
                "order_event_id": event_id,
                "analysis_date": plan.analysis_date,
                "plan_id": plan.plan_id,
                "action": plan.action,
                "scenario_id": scenario.scenario_id,
                "scenario_priority": scenario.priority,
                "trigger_type": scenario.trigger_type,
                "evidence_grade": grades.get((plan.plan_id, scenario.scenario_id), "unavailable"),
                "order_id": order.order_id,
                "side": order.side,
                "intent": order.intent,
                "order_type": order.order_type,
                "size_basis": order.size_basis,
                "size_value": order.size_value,
                "take_profit": order.take_profit,
                "stop_loss": order.stop_loss,
                "plan_activated_at": boundary["start"].isoformat(),
                "plan_completed_at": boundary["end"].isoformat(),
                "order_status": str(state.status),
                "filled": filled,
                "fill_time": fill.time.isoformat() if fill is not None else None,
                "fill_price": float(fill.fill_price) if fill is not None else None,
                "terminal_event": terminal_event,
                "completed_at": completed_at,
            }
        )
    order_frame = pd.DataFrame(order_rows).sort_values(
        ["plan_activated_at", "scenario_priority", "order_id"]
    ).reset_index(drop=True)

    plan_rows: list[dict[str, Any]] = []
    initial_balance = float(evidence["metrics"]["initial_balance"])
    for plan in plans:
        boundary = boundaries[plan.plan_id]
        start, end = boundary["start"], boundary["end"]
        start_number = curve.index.get_loc(start)
        if start_number == 0:
            start_cash = 0.0
            start_position = initial_balance / float(market.loc[start, "open"])
        else:
            incoming = curve.iloc[start_number - 1]
            start_cash = float(incoming["cash"])
            start_position = float(incoming["position"])
        start_price = float(market.loc[start, "open"])
        start_equity = start_cash + start_position * start_price
        active_curve = curve.loc[(curve.index >= start) & (curve.index <= end)]
        counterfactual = start_cash + start_position * active_curve["close"]
        actual_path = active_curve["equity"] / start_equity - 1
        no_action_path = counterfactual / start_equity - 1
        value_add_path = actual_path - no_action_path
        scoped_orders = order_frame[order_frame["plan_id"] == plan.plan_id]
        scoped_fills = fills[fills["plan_id"] == plan.plan_id]
        filled_count = int(scoped_orders["filled"].sum())
        value_added = float(value_add_path.iloc[-1])
        plan_rows.append(
            {
                "analysis_date": plan.analysis_date,
                "plan_id": plan.plan_id,
                "decision_event_id": plan.decision_event_id,
                "action": plan.action,
                "activated_at": start.isoformat(),
                "completed_at": end.isoformat(),
                "lifecycle_hours": int(len(active_curve)),
                "lifecycle_end_reason": boundary["end_reason"],
                "incoming_cash": start_cash,
                "incoming_position": start_position,
                "incoming_equity": start_equity,
                "incoming_btc_exposure": start_position * start_price / start_equity if start_equity else 0.0,
                "configured_order_count": int(len(scoped_orders)),
                "filled_order_count": filled_count,
                "plan_executed": filled_count > 0,
                "take_profit_exit_count": int((scoped_fills["fill_reason"] == "protective_take_profit").sum()),
                "stop_loss_exit_count": int((scoped_fills["fill_reason"] == "protective_stop_loss").sum()),
                "actual_return": float(actual_path.iloc[-1]),
                "no_action_return": float(no_action_path.iloc[-1]),
                "lifecycle_value_added_return": value_added,
                "value_add_mfe": float(value_add_path.max()),
                "value_add_mae": float(value_add_path.min()),
                "beat_no_action": value_added > EPSILON if filled_count else None,
            }
        )
    plan_frame = pd.DataFrame(plan_rows).sort_values("activated_at").reset_index(drop=True)
    executed = plan_frame[plan_frame["plan_executed"]]
    positive = executed[executed["lifecycle_value_added_return"] > EPSILON]
    negative = executed[executed["lifecycle_value_added_return"] < -EPSILON]
    active_lifecycle_bars = int(plan_frame["lifecycle_hours"].sum())
    target_limit_fills = int(
        (
            order_frame["filled"]
            & (order_frame["side"] == "sell")
            & (order_frame["order_type"] == "limit")
        ).sum()
    )
    stop_order_fills = int(
        (
            order_frame["filled"]
            & (order_frame["side"] == "sell")
            & order_frame["order_type"].isin({"stop_market", "stop_limit"})
        ).sum()
    )
    episode_fields = [
        "analysis_date", "action", "filled_order_count", "lifecycle_hours",
        "lifecycle_value_added_return",
    ]
    strongest = executed.nlargest(3, "lifecycle_value_added_return")[episode_fields]
    weakest = executed.nsmallest(3, "lifecycle_value_added_return")[episode_fields]
    metrics = {
        "schema_version": "2.0",
        "evaluation_status": "retrospective_lifecycle_replay",
        "primary_layer": "plan_lifecycle_quality",
        "primary_unit": "non_overlapping_active_plan_lifecycle",
        "methodology": "event_driven_parameterized_plan_with_local_no_action_counterfactual",
        "window_start": evidence["metrics"].get("requested_window_start", curve.index.min().isoformat()),
        "window_end_exclusive": evidence["metrics"].get("requested_window_end_exclusive", (curve.index.max() + pd.Timedelta(hours=1)).isoformat()),
        "bar_window_start": evidence["metrics"].get("bar_window_start", curve.index.min().isoformat()),
        "bar_window_end_exclusive": evidence["metrics"].get(
            "bar_window_end_exclusive",
            (curve.index.max() + pd.Timedelta(hours=1)).isoformat(),
        ),
        "first_observed_bar": curve.index.min().isoformat(),
        "last_observed_bar": curve.index.max().isoformat(),
        "bars": int(len(curve)),
        "active_lifecycle_bars": active_lifecycle_bars,
        "inactive_strategy_bars": int(len(curve)) - active_lifecycle_bars,
        "lifecycle_coverage": _rate(active_lifecycle_bars, int(len(curve))),
        "plan_count": int(len(plan_frame)),
        "executed_plan_count": int(len(executed)),
        "inactive_plan_count": int((~plan_frame["plan_executed"]).sum()),
        "plan_execution_rate": _rate(int(len(executed)), int(len(plan_frame))),
        "order_count": int(len(order_frame)),
        "filled_order_count": int(order_frame["filled"].sum()),
        "order_fill_rate": _rate(int(order_frame["filled"].sum()), int(len(order_frame))),
        "take_profit_exit_count": int(plan_frame["take_profit_exit_count"].sum()),
        "stop_loss_exit_count": int(plan_frame["stop_loss_exit_count"].sum()),
        "sell_limit_target_fill_count": target_limit_fills,
        "sell_stop_order_fill_count": stop_order_fills,
        "executed_plan_hit_rate_vs_no_action": float((executed["lifecycle_value_added_return"] > EPSILON).mean()) if not executed.empty else None,
        "executed_plan_mean_value_added_return": _mean(executed["lifecycle_value_added_return"]),
        "executed_plan_median_value_added_return": _median(executed["lifecycle_value_added_return"]),
        "positive_executed_plan_count": int(len(positive)),
        "negative_executed_plan_count": int(len(negative)),
        "mean_positive_value_added_return": _mean(positive["lifecycle_value_added_return"]),
        "mean_negative_value_added_return": _mean(negative["lifecycle_value_added_return"]),
        "value_add_payoff_ratio": (
            float(positive["lifecycle_value_added_return"].mean() / abs(negative["lifecycle_value_added_return"].mean()))
            if not positive.empty and not negative.empty else None
        ),
        "strongest_plan_updates": strongest.to_dict(orient="records"),
        "weakest_plan_updates": weakest.to_dict(orient="records"),
        "executed_plan_mean_value_add_mfe": _mean(executed["value_add_mfe"]),
        "executed_plan_mean_value_add_mae": _mean(executed["value_add_mae"]),
        "all_plan_mean_value_added_return": _mean(plan_frame["lifecycle_value_added_return"]),
        "order_status_counts": dict(sorted(Counter(order_frame["order_status"].astype(str)).items())),
        "lifecycle_end_reason_counts": dict(sorted(Counter(plan_frame["lifecycle_end_reason"].astype(str)).items())),
        "execution_result": {
            "total_return": evidence["metrics"].get("total_return"),
            "max_drawdown": evidence["metrics"].get("max_drawdown"),
            "benchmark_return": evidence["metrics"].get("benchmark_return"),
            "condition_evaluation_coverage": evidence["metrics"].get("condition_evaluation_coverage"),
            "research_integrity": evidence["metrics"].get("research_integrity"),
        },
        "data_quality": {
            "plan_order_key_match": True,
            "status_rows_match_metrics": True,
            "fill_rows_match_metrics": True,
            "curve_market_timeline_match": True,
            "non_overlapping_plan_lifecycles": True,
        },
        "guardrails": [
            "不再把每日配置评级扩展成固定远期独立样本。",
            "计划价值增量以同一入场现金和持仓原地不动作为局部反事实。",
            "未触发条件不是亏损，只保留其生命周期状态。",
            "价值增量包含仓位、成交、手续费和滑点影响，不等同纯方向 Forecast Alpha。",
            "声明代理仍不等同原始条件真值，必须与精确证据分开解读。",
        ],
    }
    return plan_frame, order_frame, metrics


def _pct(value: Any) -> str:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return "—"
    return f"{float(value) * 100:.2f}%"


def _report(metrics: dict[str, Any], *, english: bool = False) -> str:
    result = metrics["execution_result"]
    strongest = metrics["strongest_plan_updates"]
    weakest = metrics["weakest_plan_updates"]
    episode_rows = "\n".join(
        f"| {item['analysis_date']} | {item['action']} | {_pct(item['lifecycle_value_added_return'])} | "
        f"{item['filled_order_count']} | {item['lifecycle_hours']}h |"
        for item in (*strongest, *weakest)
    )
    if english:
        return f"""# Signal Quality · 60-Day Plan Lifecycle Replay

## Executive readout

This report evaluates RogueTrader as a stateful daily control loop. A decision is active only until the next eligible plan, its own expiry, or the end of the evidence window. Parameterized triggers, fills, take-profit, and stop-loss events come from the existing execution replay; no fixed forward horizon is imposed.

- Plans: **{metrics['plan_count']}**; plans with at least one fill: **{metrics['executed_plan_count']}** ({_pct(metrics['plan_execution_rate'])}).
- Active-plan coverage: **{_pct(metrics['lifecycle_coverage'])}** of 1H bars; **{metrics['inactive_strategy_bars']}** bars had no active plan after an expiry.
- Parameterized orders: **{metrics['order_count']}**; filled: **{metrics['filled_order_count']}** ({_pct(metrics['order_fill_rate'])}).
- Executed plans beating the same-state no-action counterfactual: **{_pct(metrics['executed_plan_hit_rate_vs_no_action'])}**.
- Mean / median lifecycle value add among executed plans: **{_pct(metrics['executed_plan_mean_value_added_return'])} / {_pct(metrics['executed_plan_median_value_added_return'])}**.
- Positive / negative executed plans: **{metrics['positive_executed_plan_count']} / {metrics['negative_executed_plan_count']}**; average gain / loss: **{_pct(metrics['mean_positive_value_added_return'])} / {_pct(metrics['mean_negative_value_added_return'])}**.
- Completed sell targets: **{metrics['sell_limit_target_fill_count']} limit / {metrics['sell_stop_order_fill_count']} stop**; bracket exits: **{metrics['take_profit_exit_count']} take-profit / {metrics['stop_loss_exit_count']} stop-loss**.
- End-to-end portfolio replay remains **{_pct(result['total_return'])}**, versus BTC **{_pct(result['benchmark_return'])}**, with max drawdown **{_pct(result['max_drawdown'])}**.

| Plan date | Action | Lifecycle value add | Fills | Active time |
| --- | --- | ---: | ---: | ---: |
{episode_rows}

## Interpretation boundary

Lifecycle value add compares the actual plan with freezing the exact incoming cash and BTC position over the same natural lifecycle. It is a local decision contribution—not a standalone trade return or pure Forecast Alpha. Untriggered conditions are preserved as dormant, expired, or superseded states and are never scored as losses. The result retains historical sizing, fees, slippage, and declared proxy conditions; Portfolio PnL remains the final system-level acceptance metric.
"""
    return f"""# Signal Quality · 60 日计划生命周期回放

## 核心结果

本报告把 RogueTrader 视为每天更新的有状态控制循环。一份决策只在下一份合格计划到来、计划自身到期或证据窗口结束前有效；参数化条件、成交、止盈和止损直接使用既有执行回放，不再人为设置固定远期周期。

- 计划 **{metrics['plan_count']}** 份；至少发生一次成交的计划 **{metrics['executed_plan_count']}** 份，执行率 **{_pct(metrics['plan_execution_rate'])}**。
- 有效计划覆盖 **{_pct(metrics['lifecycle_coverage'])}** 的 1H K 线；一份计划到期后共有 **{metrics['inactive_strategy_bars']}** 根 K 线处于无有效计划状态。
- 参数化委托 **{metrics['order_count']}** 条；成交 **{metrics['filled_order_count']}** 条，成交率 **{_pct(metrics['order_fill_rate'])}**。
- 已执行计划相对“保持入场时现金与 BTC 持仓不动”的局部反事实，胜率 **{_pct(metrics['executed_plan_hit_rate_vs_no_action'])}**。
- 已执行计划平均 / 中位生命周期价值增量：**{_pct(metrics['executed_plan_mean_value_added_return'])} / {_pct(metrics['executed_plan_median_value_added_return'])}**。
- 正 / 负价值增量计划 **{metrics['positive_executed_plan_count']} / {metrics['negative_executed_plan_count']}** 份；平均正贡献 **{_pct(metrics['mean_positive_value_added_return'])}**，平均负贡献 **{_pct(metrics['mean_negative_value_added_return'])}**。
- 已成交的卖出目标：限价 **{metrics['sell_limit_target_fill_count']}** 次、止损单 **{metrics['sell_stop_order_fill_count']}** 次；买入委托附带的保护性退出实际触发止盈 **{metrics['take_profit_exit_count']}** 次、止损 **{metrics['stop_loss_exit_count']}** 次。
- 完整组合回放仍为 **{_pct(result['total_return'])}**，同期 BTC **{_pct(result['benchmark_return'])}**，最大回撤 **{_pct(result['max_drawdown'])}**。

| 计划日期 | 评级 | 生命周期价值增量 | 成交 | 有效时间 |
| --- | --- | ---: | ---: | ---: |
{episode_rows}

## 正确解读

“生命周期价值增量”比较的是：在完全相同的期初现金和 BTC 持仓下，执行当日计划，相比原地不动，到该计划自然结束时增加或减少了多少价值。它是局部决策贡献，不是把每一天伪装成独立交易，也不是纯方向 Forecast Alpha。没有触发的条件只记录为等待、到期或被新计划替代，不计为亏损。

## 解释边界

本结果保留历史组合状态，因此包含仓位比例、手续费、滑点和声明代理条件的影响。`Signal / Plan Lifecycle Quality` 用于诊断每次计划更新，最终 Portfolio PnL 仍用于验收完整系统。
"""


def _html_report(metrics: dict[str, Any]) -> str:
    result = metrics["execution_result"]
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>RogueTrader Signal Quality</title><style>
:root{{--bg:#07101c;--panel:#101c30;--text:#eef5ff;--muted:#91a3bd;--mint:#5ce0b7;--line:#263854}}*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 80% 0,#15294a 0,#07101c 45%);color:var(--text);font:15px/1.7 system-ui,sans-serif}}main{{max-width:1050px;margin:auto;padding:52px 24px}}.eyebrow{{color:var(--mint);letter-spacing:.14em}}h1{{font-size:38px;margin:8px 0}}.muted{{color:var(--muted)}}.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:28px 0}}.card,.panel{{background:rgba(16,28,48,.94);border:1px solid var(--line);border-radius:18px;padding:21px}}.metric{{font-size:27px;font-weight:760;color:var(--mint)}}.panel{{margin-top:18px}}@media(max-width:760px){{.grid{{grid-template-columns:1fr 1fr}}h1{{font-size:30px}}}}</style></head><body><main>
<div class="eyebrow">ROGUETRADER · STATEFUL SIGNAL INTELLIGENCE</div><h1>60 日计划生命周期回放</h1><p class="muted">Parameterized plans · Natural lifecycle · Local no-action counterfactual</p>
<div class="grid"><div class="card"><div class="muted">计划 / 已执行</div><div class="metric">{metrics['plan_count']} / {metrics['executed_plan_count']}</div></div><div class="card"><div class="muted">价值增量胜率</div><div class="metric">{_pct(metrics['executed_plan_hit_rate_vs_no_action'])}</div></div><div class="card"><div class="muted">平均价值增量</div><div class="metric">{_pct(metrics['executed_plan_mean_value_added_return'])}</div></div><div class="card"><div class="muted">完整组合收益</div><div class="metric">{_pct(result['total_return'])}</div></div></div>
<div class="panel"><strong>评价单位</strong><p>每份每日计划从激活开始，只计算到下一份合格计划、计划到期或窗口结束。条件委托、成交与保护性退出来自既有逐小时执行回放。</p></div><div class="panel"><strong>反事实</strong><p>以计划激活时完全相同的现金和 BTC 持仓为起点，对比“执行该计划”和“原地不动”。未触发条件不计为亏损。</p></div><div class="panel"><strong>边界</strong><p>价值增量包含仓位、手续费、滑点及代理条件影响，不是纯方向预测 Alpha；完整 Portfolio PnL 仍是最终验收指标。</p></div></main></body></html>"""


def write_bundle(
    plans: pd.DataFrame,
    orders: pd.DataFrame,
    metrics: dict[str, Any],
    output_root: str | Path,
    ticker: str,
) -> Path:
    root = Path(output_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = root / f"{datetime.now().astimezone():%Y%m%d_%H%M%S}__{ticker.replace('-', '_')}"
    temporary = Path(tempfile.mkdtemp(prefix=".writing-", dir=root))
    os.chmod(temporary, 0o700)
    try:
        plans.to_csv(temporary / "逐计划生命周期.csv", index=False, encoding="utf-8-sig")
        orders.to_csv(temporary / "逐委托生命周期.csv", index=False, encoding="utf-8-sig")
        (temporary / "信号质量指标.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        (temporary / "信号质量报告.md").write_text(_report(metrics), encoding="utf-8")
        (temporary / "Signal Quality Report.md").write_text(_report(metrics, english=True), encoding="utf-8")
        (temporary / "信号质量报告.html").write_text(_html_report(metrics), encoding="utf-8")
        for path in temporary.iterdir():
            os.chmod(path, 0o600)
        os.replace(temporary, target)
        os.chmod(target, 0o700)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return target


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="按参数化委托的自然生命周期评价每日计划。")
    parser.add_argument("--execution-result", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--market-data", type=Path, default=DEFAULT_MARKET_DATA)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--ticker", default="BTC-USD")
    parser.add_argument("--window-start")
    parser.add_argument("--window-days", type=int)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        evidence = load_execution_evidence(args.execution_result)
        replay_metrics = evidence["metrics"]
        window_start = args.window_start or replay_metrics.get("requested_window_start")
        window_days = args.window_days or replay_metrics.get("requested_window_days")
        if window_start is None or window_days is None:
            raise SignalQualityError("执行回放没有固定窗口元数据，请显式提供窗口。")
        plans = load_execution_ledger(args.ledger, args.ticker)
        market, market_quality = load_confirmed_ohlcv(args.market_data)
        plans, market, window_metadata = select_evaluation_window(
            plans, market, window_start=window_start, window_days=int(window_days)
        )
        if (
            replay_metrics.get("requested_window_start") != window_metadata["requested_window_start"]
            or int(replay_metrics.get("requested_window_days", -1)) != int(window_metadata["requested_window_days"])
        ):
            raise SignalQualityError("执行回放与请求的研究窗口不一致。")
        plan_frame, order_frame, quality = evaluate_lifecycles(plans, market, evidence)
        quality["market_data_quality"] = {key: value for key, value in market_quality.items() if key != "source"}
        quality["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        target = write_bundle(plan_frame, order_frame, quality, args.output_root, args.ticker)
    except (SignalQualityError, ExecutionBacktestError, ValueError, OSError) as exc:
        raise SystemExit(f"signal quality error: {exc}") from None
    print(json.dumps({"output": str(target), "plans": quality["plan_count"], "executed_plans": quality["executed_plan_count"], "orders": quality["order_count"], "fills": quality["filled_order_count"], "hit_rate_vs_no_action": quality["executed_plan_hit_rate_vs_no_action"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
