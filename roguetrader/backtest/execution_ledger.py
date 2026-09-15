"""Backtest the order-level execution ledger against confirmed OHLCV bars."""

from __future__ import annotations

import argparse
from collections import Counter
import csv
from dataclasses import dataclass
from datetime import datetime
import html
import json
import math
import os
from pathlib import Path
from typing import Any

import pandas as pd

from roguetrader.publisher.execution_csv import EXECUTION_CSV_FIELDS


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LEDGER = PROJECT_ROOT / "my_results" / "研究" / "参数化委托.csv"


def discover_external_data_path(
    environment_name: str,
    sibling_project: str,
    relative_path: str,
) -> Path:
    """Resolve a local sibling data lake without embedding a user home path."""

    configured = os.getenv(environment_name, "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    for anchor in (PROJECT_ROOT, *PROJECT_ROOT.parents):
        candidate = anchor.parent / sibling_project / relative_path
        if candidate.exists():
            return candidate.resolve()
    return (PROJECT_ROOT.parent / sibling_project / relative_path).resolve()


DEFAULT_MARKET_DATA = discover_external_data_path(
    "ROGUETRADER_OHLCV_PATH",
    "crypto_data_lake",
    "data/curated/ohlcv/symbol=BTC-USDT/timeframe=1H/data.csv",
)
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "my_results" / "回测结果"
EPSILON = 1e-12


def portable_source_reference(path: str | Path) -> str:
    """Return a repository-safe source label without a machine-local prefix."""

    source = Path(path).expanduser().resolve()
    try:
        return str(source.relative_to(PROJECT_ROOT))
    except ValueError:
        pass
    for project_name in ("crypto_data_lake", "crypto_data"):
        if project_name in source.parts:
            index = source.parts.index(project_name)
            return str(Path(*source.parts[index:]))
    return source.name


class ExecutionBacktestError(ValueError):
    """Raised when a ledger backtest cannot be performed safely."""


def _number(value: str | None, *, optional: bool = False) -> float | None:
    text = str(value or "").strip()
    if not text and optional:
        return None
    try:
        result = float(text)
    except ValueError as exc:
        raise ExecutionBacktestError(f"无效数值：{text}") from exc
    if not math.isfinite(result):
        raise ExecutionBacktestError("回测输入包含非有限数值。")
    return result


def _integer(value: str | None) -> int:
    try:
        result = int(str(value or ""))
    except ValueError as exc:
        raise ExecutionBacktestError("回测输入包含无效整数。") from exc
    return result


def _timestamp(value: str) -> pd.Timestamp:
    parsed = pd.Timestamp(value)
    if parsed.tzinfo is None:
        raise ExecutionBacktestError("委托时间必须包含时区。")
    return parsed.tz_convert("UTC")


@dataclass(frozen=True)
class OrderSpec:
    event_id: str
    order_id: str
    sequence: int
    side: str
    intent: str
    order_type: str
    size_basis: str
    size_value: float
    limit_price: float | None
    stop_price: float | None
    take_profit: float | None
    stop_loss: float | None
    time_in_force: str
    after_order_id: str | None


@dataclass(frozen=True)
class ScenarioSpec:
    scenario_id: str
    priority: int
    exclusive_group: str | None
    trigger_type: str
    trigger_operator: str | None
    trigger_value: float | None
    condition: str
    orders: tuple[OrderSpec, ...]


@dataclass(frozen=True)
class PlanSpec:
    plan_id: str
    decision_event_id: str
    analysis_date: str
    ticker: str
    action: str
    generated_at: pd.Timestamp
    generated_at_source: str
    valid_until: pd.Timestamp
    max_position_pct: float
    max_order_cash_pct: float
    scenarios: tuple[ScenarioSpec, ...]


def load_execution_ledger(path: str | Path, ticker: str) -> tuple[PlanSpec, ...]:
    """Load the strict v2 order ledger without relying on physical row order."""

    ledger_path = Path(path).expanduser().resolve()
    if not ledger_path.is_file():
        raise ExecutionBacktestError(f"参数化委托 CSV 不存在：{ledger_path}")
    with ledger_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != EXECUTION_CSV_FIELDS:
            raise ExecutionBacktestError("参数化委托 CSV 不是当前回测协议。")
        rows = [dict(row) for row in reader if row.get("ticker") == ticker]
    if not rows:
        raise ExecutionBacktestError(f"参数化委托 CSV 中没有标的 {ticker}。")

    plan_rows: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        plan_rows.setdefault(row["plan_id"], []).append(row)
    plans: list[PlanSpec] = []
    for plan_id, grouped in plan_rows.items():
        first = grouped[0]
        stable_fields = (
            "decision_event_id",
            "analysis_date",
            "ticker",
            "action",
            "order_generated_at",
            "order_generated_at_source",
            "order_valid_until",
            "max_position_pct",
            "max_order_cash_pct",
        )
        if any(row[field] != first[field] for row in grouped for field in stable_fields):
            raise ExecutionBacktestError(f"计划 {plan_id} 的 CSV 元数据不一致。")
        scenario_rows: dict[str, list[dict[str, str]]] = {}
        for row in grouped:
            scenario_rows.setdefault(row["scenario_id"], []).append(row)
        scenarios: list[ScenarioSpec] = []
        for scenario_id, scenario_group in scenario_rows.items():
            base = scenario_group[0]
            scenario_fields = (
                "scenario_priority",
                "exclusive_group",
                "trigger_type",
                "trigger_operator",
                "trigger_value",
                "scenario_condition",
            )
            if any(
                row[field] != base[field]
                for row in scenario_group
                for field in scenario_fields
            ):
                raise ExecutionBacktestError(
                    f"计划 {plan_id} 的场景 {scenario_id} 元数据不一致。"
                )
            orders: list[OrderSpec] = []
            for row in scenario_group:
                if row["row_type"] == "no_order":
                    continue
                if row["row_type"] != "order":
                    raise ExecutionBacktestError("参数化委托 CSV 包含未知行类型。")
                orders.append(
                    OrderSpec(
                        event_id=row["order_event_id"],
                        order_id=row["order_id"],
                        sequence=_integer(row["order_sequence"]),
                        side=row["side"],
                        intent=row["intent"],
                        order_type=row["order_type"],
                        size_basis=row["size_basis"],
                        size_value=float(_number(row["size_value"])),
                        limit_price=_number(row["limit_price"], optional=True),
                        stop_price=_number(row["stop_price"], optional=True),
                        take_profit=_number(row["take_profit"], optional=True),
                        stop_loss=_number(row["stop_loss"], optional=True),
                        time_in_force=row["time_in_force"],
                        after_order_id=row["after_order_id"] or None,
                    )
                )
            trigger_value = _number(base["trigger_value"], optional=True)
            scenarios.append(
                ScenarioSpec(
                    scenario_id=scenario_id,
                    priority=_integer(base["scenario_priority"]),
                    exclusive_group=base["exclusive_group"] or None,
                    trigger_type=base["trigger_type"],
                    trigger_operator=base["trigger_operator"] or None,
                    trigger_value=trigger_value,
                    condition=base["scenario_condition"],
                    orders=tuple(sorted(orders, key=lambda order: order.sequence)),
                )
            )
        plans.append(
            PlanSpec(
                plan_id=plan_id,
                decision_event_id=first["decision_event_id"],
                analysis_date=first["analysis_date"],
                ticker=first["ticker"],
                action=first["action"],
                generated_at=_timestamp(first["order_generated_at"]),
                generated_at_source=first["order_generated_at_source"],
                valid_until=_timestamp(first["order_valid_until"]),
                max_position_pct=float(_number(first["max_position_pct"])),
                max_order_cash_pct=float(_number(first["max_order_cash_pct"])),
                scenarios=tuple(sorted(scenarios, key=lambda item: item.priority)),
            )
        )
    plans.sort(key=lambda plan: (plan.generated_at, plan.analysis_date, plan.plan_id))
    if len({plan.decision_event_id for plan in plans}) != len(plans):
        raise ExecutionBacktestError("同一决策事件对应了多个执行计划。")
    return tuple(plans)


def load_confirmed_ohlcv(path: str | Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Load confirmed OHLCV and enforce strict chronological data quality."""

    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise ExecutionBacktestError(f"行情文件不存在：{source}")
    raw = pd.read_csv(source)
    required = {"time", "open", "high", "low", "close", "volume"}
    missing = required - set(raw.columns)
    if missing:
        raise ExecutionBacktestError(f"行情缺少字段：{sorted(missing)}")
    unconfirmed = 0
    if "confirm" in raw.columns:
        confirmed = raw["confirm"].astype(str).str.strip().isin({"1", "1.0"})
        unconfirmed = int((~confirmed).sum())
        raw = raw[confirmed].copy()
    raw["time"] = pd.to_datetime(raw["time"], utc=True, errors="coerce")
    if raw["time"].isna().any():
        raise ExecutionBacktestError("行情包含无效时间戳。")
    duplicate_count = int(raw["time"].duplicated().sum())
    if duplicate_count:
        raise ExecutionBacktestError("行情包含重复时间戳。")
    for field in ("open", "high", "low", "close", "volume"):
        raw[field] = pd.to_numeric(raw[field], errors="coerce")
    frame = raw.set_index("time").sort_index()
    values = frame[["open", "high", "low", "close", "volume"]]
    if values.isna().any().any():
        raise ExecutionBacktestError("行情包含空值或非数值。")
    invalid = (
        (frame["high"] < frame[["open", "close", "low"]].max(axis=1))
        | (frame["low"] > frame[["open", "close", "high"]].min(axis=1))
        | (frame[["open", "high", "low", "close"]] <= 0).any(axis=1)
        | (frame["volume"] < 0)
    )
    if invalid.any():
        raise ExecutionBacktestError("行情包含无效 OHLCV 记录。")
    gaps = frame.index.to_series().diff().dropna().dt.total_seconds()
    missing_bars = int(sum(max(round(value / 3600) - 1, 0) for value in gaps if value > 5400))
    expected = len(frame) + missing_bars
    quality = {
        "source": portable_source_reference(source),
        "rows": int(len(frame)),
        "confirmed_rows": int(len(frame)),
        "unconfirmed_rows_filtered": unconfirmed,
        "duplicate_timestamps": duplicate_count,
        "missing_hourly_bars": missing_bars,
        "coverage_ratio": len(frame) / expected if expected else 0.0,
        "start": frame.index.min().isoformat(),
        "end": frame.index.max().isoformat(),
    }
    if quality["coverage_ratio"] < 0.99:
        raise ExecutionBacktestError("1H 行情连续覆盖率低于 99%。")
    return frame, quality


def select_evaluation_window(
    plans: tuple[PlanSpec, ...],
    market: pd.DataFrame,
    *,
    window_start: str | pd.Timestamp | None,
    window_days: int | None,
) -> tuple[tuple[PlanSpec, ...], pd.DataFrame, dict[str, Any]]:
    """Select one exact-duration window without changing default full-history runs."""

    if window_start is None and window_days is None:
        return plans, market, {}
    if window_start is None or window_days is None or window_days <= 0:
        raise ExecutionBacktestError("固定窗口必须同时提供起点和正整数天数。")
    start = pd.Timestamp(window_start)
    if start.tzinfo is None:
        raise ExecutionBacktestError("回测窗口起点必须包含时区。")
    start = start.tz_convert("UTC")
    end = start + pd.Timedelta(days=window_days)
    available_bars = market.index[market.index >= start]
    if available_bars.empty:
        raise ExecutionBacktestError("行情没有覆盖指定回测窗口。")
    bar_start = available_bars[0]
    bar_end = bar_start + pd.Timedelta(days=window_days)
    if market.index.max() + pd.Timedelta(hours=1) < bar_end:
        raise ExecutionBacktestError("行情尚未完整覆盖指定回测窗口。")
    selected_plans = tuple(
        plan for plan in plans if start <= plan.generated_at < end
    )
    selected_market = market.loc[
        (market.index >= bar_start) & (market.index < bar_end)
    ].copy()
    if not selected_plans:
        raise ExecutionBacktestError("指定窗口内没有执行计划。")
    if len(selected_market) != window_days * 24:
        raise ExecutionBacktestError(
            f"指定窗口应包含 {window_days * 24} 根 1H K 线，"
            f"实际为 {len(selected_market)} 根。"
        )
    return selected_plans, selected_market, {
        "requested_window_start": start.isoformat(),
        "requested_window_end_exclusive": end.isoformat(),
        "requested_window_days": window_days,
        "requested_window_hours": window_days * 24,
        "bar_window_start": bar_start.isoformat(),
        "bar_window_end_exclusive": bar_end.isoformat(),
        "window_alignment_policy": "decision_bounds_then_next_complete_hour_grid",
        "window_selection_policy": "explicit",
    }


def _triggered(scenario: ScenarioSpec, last_close: float | None) -> bool:
    if scenario.trigger_type == "immediate":
        return True
    if scenario.trigger_type == "manual_confirmation":
        return False
    if last_close is None or scenario.trigger_value is None:
        return False
    if scenario.trigger_operator == "gte":
        return last_close >= scenario.trigger_value
    if scenario.trigger_operator == "lte":
        return last_close <= scenario.trigger_value
    raise ExecutionBacktestError("价格触发器缺少受支持的比较符。")


def _raw_fill(order: OrderSpec, bar: pd.Series, slippage: float) -> float | None:
    opening = float(bar["open"])
    high = float(bar["high"])
    low = float(bar["low"])
    if order.order_type == "market":
        return opening * (1 + slippage if order.side == "buy" else 1 - slippage)
    if order.order_type == "limit":
        assert order.limit_price is not None
        if order.side == "buy" and low <= order.limit_price:
            return min(order.limit_price, opening * (1 + slippage))
        if order.side == "sell" and high >= order.limit_price:
            return max(order.limit_price, opening * (1 - slippage))
        return None
    if order.order_type == "stop_market":
        assert order.stop_price is not None
        if order.side == "buy" and high >= order.stop_price:
            raw = opening if opening >= order.stop_price else order.stop_price
            return raw * (1 + slippage)
        if order.side == "sell" and low <= order.stop_price:
            raw = opening if opening <= order.stop_price else order.stop_price
            return raw * (1 - slippage)
        return None
    raise ExecutionBacktestError(f"回测暂不支持委托类型：{order.order_type}")


def _drawdown(values: pd.Series) -> float:
    peak = values.cummax()
    return float((values / peak - 1).min())


def _svg_equity(curve: pd.DataFrame) -> str:
    sampled = curve.iloc[:: max(1, len(curve) // 700)].copy()
    if sampled.index[-1] != curve.index[-1]:
        sampled = pd.concat([sampled, curve.iloc[[-1]]])
    width, height, pad = 920, 300, 28
    low = float(sampled[["equity", "benchmark_equity"]].min().min())
    high = float(sampled[["equity", "benchmark_equity"]].max().max())
    span = high - low or 1.0

    def points(column: str) -> str:
        result = []
        for index, value in enumerate(sampled[column]):
            x = pad + index * (width - 2 * pad) / max(len(sampled) - 1, 1)
            y = height - pad - (float(value) - low) * (height - 2 * pad) / span
            result.append(f"{x:.1f},{y:.1f}")
        return " ".join(result)

    return (
        f'<svg viewBox="0 0 {width} {height}" role="img" '
        'aria-label="策略与持有基准权益曲线">'
        f'<polyline points="{points("benchmark_equity")}" '
        'fill="none" stroke="#7f8da6" stroke-width="2"/>'
        f'<polyline points="{points("equity")}" '
        'fill="none" stroke="#58d6b5" stroke-width="3"/>'
        f'<text x="{pad}" y="18" fill="#7f8da6">持有基准</text>'
        f'<text x="{pad + 90}" y="18" fill="#58d6b5">委托策略</text>'
        "</svg>"
    )


def run_backtest(
    plans: tuple[PlanSpec, ...],
    market: pd.DataFrame,
    *,
    initial_balance: float,
    fee_rate: float,
    slippage_rate: float,
    condition_resolver: Any | None = None,
) -> dict[str, Any]:
    if initial_balance <= 0:
        raise ExecutionBacktestError("初始资产必须大于零。")
    if not 0 <= fee_rate < 1 or not 0 <= slippage_rate < 1:
        raise ExecutionBacktestError("手续费和滑点必须位于 [0, 1)。")
    start_candidates = market.index[market.index >= plans[0].generated_at]
    if start_candidates.empty:
        raise ExecutionBacktestError("行情没有覆盖首份计划生成时间。")
    start = start_candidates[0]
    frame = market.loc[start:].copy()
    start_price = float(frame.iloc[0]["open"])
    cash = 0.0
    position = initial_balance / start_price
    initial_position = position
    benchmark_position = initial_position
    active_plan: PlanSpec | None = None
    plan_index = 0
    plan_position = position
    plan_cash = cash
    activated_scenarios: set[str] = set()
    selected_groups: set[str] = set()
    active_plan_fill_count = 0
    active_plan_take_profit_fill_count = 0
    open_orders: list[dict[str, Any]] = []
    brackets: list[dict[str, Any]] = []
    filled_ids: dict[str, int] = {}
    fills: list[dict[str, Any]] = []
    status: dict[str, dict[str, Any]] = {}
    total_orders = 0
    manual_orders = 0
    for plan in plans:
        for scenario in plan.scenarios:
            for order in scenario.orders:
                total_orders += 1
                initial_status = "not_activated"
                if scenario.trigger_type == "manual_confirmation":
                    initial_status = (
                        "not_evaluated_manual_confirmation"
                        if condition_resolver is None
                        else "condition_not_evaluated"
                    )
                    manual_orders += 1
                status[order.event_id] = {
                    "order_event_id": order.event_id,
                    "analysis_date": plan.analysis_date,
                    "ticker": plan.ticker,
                    "plan_id": plan.plan_id,
                    "scenario_id": scenario.scenario_id,
                    "order_id": order.order_id,
                    "side": order.side,
                    "order_type": order.order_type,
                    "status": initial_status,
                    "fill_time": "",
                    "fill_price": "",
                    "filled_quantity": "",
                }
    cancelled = 0
    rejected = 0
    turnover = 0.0
    fees = 0.0
    exposed_bars = 0
    equity_rows: list[dict[str, Any]] = []
    last_close: float | None = None

    def cancel_open(reason: str) -> None:
        nonlocal cancelled
        for state in open_orders:
            if status[state["order"].event_id]["status"] in {
                "open",
                "waiting_dependency",
            }:
                status[state["order"].event_id]["status"] = reason
                cancelled += 1
        open_orders.clear()
        brackets.clear()

    def close_inactive_conditions(plan: PlanSpec, reason: str) -> None:
        if condition_resolver is None:
            return
        for scenario in plan.scenarios:
            if scenario.trigger_type != "manual_confirmation":
                continue
            for order in scenario.orders:
                current = status[order.event_id]["status"]
                if current == "condition_not_evaluated":
                    status[order.event_id]["status"] = reason

    for bar_number, (timestamp, bar) in enumerate(frame.iterrows()):
        while plan_index < len(plans) and plans[plan_index].generated_at <= timestamp:
            if active_plan is not None:
                close_inactive_conditions(
                    active_plan, "condition_not_met_before_supersession"
                )
            cancel_open("cancelled_by_new_plan")
            active_plan = plans[plan_index]
            plan_index += 1
            plan_position = position
            plan_cash = cash
            active_plan_fill_count = 0
            active_plan_take_profit_fill_count = 0
            activated_scenarios.clear()
            selected_groups.clear()

        if active_plan is not None and timestamp >= active_plan.valid_until:
            close_inactive_conditions(active_plan, "condition_not_met_before_expiry")
            cancel_open("expired_unfilled")
            active_plan = None

        if active_plan is not None:
            for scenario in active_plan.scenarios:
                if scenario.scenario_id in activated_scenarios:
                    continue
                resolved_condition = False
                if (
                    scenario.trigger_type == "manual_confirmation"
                    and condition_resolver is not None
                ):
                    decision = condition_resolver.evaluate(
                        active_plan,
                        scenario,
                        timestamp,
                        {
                            "cash": cash,
                            "position": position,
                            "equity": cash + position * float(bar["close"]),
                            "plan_position": plan_position,
                            "plan_cash": plan_cash,
                            "active_plan_fill_count": active_plan_fill_count,
                            "active_plan_take_profit_fill_count": (
                                active_plan_take_profit_fill_count
                            ),
                        },
                    )
                    resolved_condition = decision.evaluated and decision.triggered
                elif _triggered(scenario, last_close):
                    resolved_condition = True
                if not resolved_condition:
                    continue
                if scenario.exclusive_group and scenario.exclusive_group in selected_groups:
                    continue
                if scenario.exclusive_group:
                    selected_groups.add(scenario.exclusive_group)
                activated_scenarios.add(scenario.scenario_id)
                for order in scenario.orders:
                    reference = order.limit_price or order.stop_price or float(bar["open"])
                    if order.size_basis == "current_position_pct":
                        quantity = plan_position * order.size_value
                    elif order.size_basis == "available_cash_pct":
                        quantity = plan_cash * order.size_value / reference
                    else:
                        quantity = order.size_value
                    state_name = "open" if not order.after_order_id else "waiting_dependency"
                    if quantity <= EPSILON:
                        state_name = "no_quantity"
                    status[order.event_id]["status"] = state_name
                    if state_name in {"open", "waiting_dependency"}:
                        open_orders.append(
                            {
                                "order": order,
                                "quantity": quantity,
                                "activated_bar": bar_number,
                                "fill_after_bar": (
                                    bar_number + 1
                                    if scenario.trigger_type == "manual_confirmation"
                                    and condition_resolver is not None
                                    else bar_number
                                ),
                                "scenario_id": scenario.scenario_id,
                            }
                        )

        for bracket in list(brackets):
            if bar_number <= bracket["start_bar"]:
                continue
            stop_hit = bracket["stop_loss"] is not None and float(bar["low"]) <= bracket["stop_loss"]
            take_hit = bracket["take_profit"] is not None and float(bar["high"]) >= bracket["take_profit"]
            if not stop_hit and not take_hit:
                continue
            raw = bracket["stop_loss"] if stop_hit else bracket["take_profit"]
            fill_price = float(raw) * (1 - slippage_rate if stop_hit else 1)
            quantity = min(float(bracket["quantity"]), position)
            if quantity <= EPSILON:
                brackets.remove(bracket)
                continue
            notional = quantity * fill_price
            fee = notional * fee_rate
            cash += notional - fee
            position -= quantity
            fees += fee
            turnover += notional
            fills.append(
                {
                    "time": timestamp.isoformat(),
                    "analysis_date": bracket["analysis_date"],
                    "plan_id": bracket["plan_id"],
                    "scenario_id": bracket["scenario_id"],
                    "order_id": bracket["order_id"],
                    "order_event_id": bracket["order_event_id"],
                    "fill_reason": "protective_stop_loss" if stop_hit else "protective_take_profit",
                    "side": "sell",
                    "quantity": quantity,
                    "fill_price": fill_price,
                    "notional": notional,
                    "fee": fee,
                    "cash_after": cash,
                    "position_after": position,
                }
            )
            active_plan_take_profit_fill_count += int(not stop_hit)
            brackets.remove(bracket)

        for state in list(open_orders):
            order: OrderSpec = state["order"]
            if bar_number < state["fill_after_bar"]:
                continue
            if order.after_order_id:
                parent_bar = filled_ids.get(order.after_order_id)
                if parent_bar is None or parent_bar >= bar_number:
                    continue
                status[order.event_id]["status"] = "open"
            fill_price = _raw_fill(order, bar, slippage_rate)
            if fill_price is None:
                continue
            requested = float(state["quantity"])
            if order.side == "sell":
                quantity = min(requested, position)
            else:
                max_cash_quantity = cash / (fill_price * (1 + fee_rate))
                order_cap = plan_cash * active_plan.max_order_cash_pct / fill_price
                equity_at_fill = cash + position * fill_price
                max_position_quantity = max(
                    0.0,
                    active_plan.max_position_pct * equity_at_fill / fill_price - position,
                )
                quantity = min(requested, max_cash_quantity, order_cap, max_position_quantity)
            if quantity <= EPSILON:
                status[order.event_id]["status"] = "rejected_by_portfolio_state"
                rejected += 1
                open_orders.remove(state)
                continue
            notional = quantity * fill_price
            fee = notional * fee_rate
            if order.side == "buy":
                cash -= notional + fee
                position += quantity
            else:
                cash += notional - fee
                position -= quantity
            cash = 0.0 if abs(cash) < EPSILON else cash
            position = 0.0 if abs(position) < EPSILON else position
            fees += fee
            turnover += notional
            active_plan_fill_count += 1
            filled_ids[order.order_id] = bar_number
            status_row = status[order.event_id]
            status_row.update(
                {
                    "status": "filled",
                    "fill_time": timestamp.isoformat(),
                    "fill_price": fill_price,
                    "filled_quantity": quantity,
                }
            )
            fills.append(
                {
                    "time": timestamp.isoformat(),
                    "analysis_date": active_plan.analysis_date,
                    "plan_id": active_plan.plan_id,
                    "scenario_id": state["scenario_id"],
                    "order_id": order.order_id,
                    "order_event_id": order.event_id,
                    "fill_reason": order.order_type,
                    "side": order.side,
                    "quantity": quantity,
                    "fill_price": fill_price,
                    "notional": notional,
                    "fee": fee,
                    "cash_after": cash,
                    "position_after": position,
                }
            )
            if order.side == "buy" and (
                order.take_profit is not None or order.stop_loss is not None
            ):
                brackets.append(
                    {
                        "quantity": quantity,
                        "take_profit": order.take_profit,
                        "stop_loss": order.stop_loss,
                        "start_bar": bar_number,
                        "analysis_date": active_plan.analysis_date,
                        "plan_id": active_plan.plan_id,
                        "scenario_id": fills[-1]["scenario_id"],
                        "order_id": order.order_id + "__protection",
                        "order_event_id": order.event_id + "__protection",
                    }
                )
            open_orders.remove(state)

        close = float(bar["close"])
        equity = cash + position * close
        benchmark = benchmark_position * close
        if position > EPSILON:
            exposed_bars += 1
        equity_rows.append(
            {
                "time": timestamp,
                "equity": equity,
                "benchmark_equity": benchmark,
                "cash": cash,
                "position": position,
                "close": close,
            }
        )
        last_close = close

    if active_plan is not None:
        close_inactive_conditions(active_plan, "condition_not_met_at_data_end")
    for state in open_orders:
        current = status[state["order"].event_id]["status"]
        if current in {"open", "waiting_dependency"}:
            status[state["order"].event_id]["status"] = "open_at_data_end"
    curve = pd.DataFrame(equity_rows).set_index("time")
    hourly_returns = curve["equity"].pct_change().dropna()
    volatility = float(hourly_returns.std(ddof=1) * math.sqrt(365 * 24)) if len(hourly_returns) > 1 else 0.0
    sharpe = (
        float(hourly_returns.mean() / hourly_returns.std(ddof=1) * math.sqrt(365 * 24))
        if len(hourly_returns) > 1 and hourly_returns.std(ddof=1) > 0
        else 0.0
    )
    final_equity = float(curve.iloc[-1]["equity"])
    benchmark_final = float(curve.iloc[-1]["benchmark_equity"])
    status_counts = pd.Series([item["status"] for item in status.values()]).value_counts().to_dict()
    condition_audit = (
        condition_resolver.audit_frame()
        if condition_resolver is not None
        else pd.DataFrame()
    )
    audit_grades = {
        (str(row.plan_id), str(row.scenario_id)): str(row.evidence_grade)
        for row in condition_audit.itertuples()
        if bool(row.evaluated)
    }
    exact_manual_orders = 0
    proxy_manual_orders = 0
    evaluated_manual_orders = 0
    for plan in plans:
        for scenario in plan.scenarios:
            if scenario.trigger_type != "manual_confirmation":
                continue
            grade = audit_grades.get((plan.plan_id, scenario.scenario_id))
            if grade is None:
                continue
            evaluated_manual_orders += len(scenario.orders)
            if grade == "exact":
                exact_manual_orders += len(scenario.orders)
            else:
                proxy_manual_orders += len(scenario.orders)
    metrics = {
        "initial_balance": initial_balance,
        "final_equity": final_equity,
        "total_return": final_equity / initial_balance - 1,
        "benchmark_final_equity": benchmark_final,
        "benchmark_return": benchmark_final / initial_balance - 1,
        "excess_return": (final_equity - benchmark_final) / initial_balance,
        "max_drawdown": _drawdown(curve["equity"]),
        "benchmark_max_drawdown": _drawdown(curve["benchmark_equity"]),
        "annualized_volatility": volatility,
        "sharpe_zero_rate": sharpe,
        "exposure_ratio": exposed_bars / len(curve),
        "turnover": turnover / initial_balance,
        "total_fees": fees,
        "fill_count": len(fills),
        "buy_fill_count": sum(fill["side"] == "buy" for fill in fills),
        "sell_fill_count": sum(fill["side"] == "sell" for fill in fills),
        "cancelled_order_count": cancelled,
        "rejected_order_count": rejected,
        "plan_count": len(plans),
        "total_order_count": total_orders,
        "ohlcv_evaluable_order_count": total_orders - manual_orders,
        "manual_condition_order_count": manual_orders,
        "ohlcv_evaluation_coverage": (total_orders - manual_orders) / total_orders,
        "condition_evaluable_order_count": (
            total_orders - manual_orders + evaluated_manual_orders
        ),
        "condition_evaluation_coverage": (
            (total_orders - manual_orders + evaluated_manual_orders) / total_orders
        ),
        "manual_exact_order_count": exact_manual_orders,
        "manual_proxy_order_count": proxy_manual_orders,
        "manual_unevaluated_order_count": manual_orders - evaluated_manual_orders,
        "triggered_manual_scenario_count": (
            int(condition_audit["triggered"].sum())
            if not condition_audit.empty
            else 0
        ),
        "triggered_exact_scenario_count": (
            int(
                (
                    condition_audit["triggered"]
                    & (condition_audit["evidence_grade"] == "exact")
                ).sum()
            )
            if not condition_audit.empty
            else 0
        ),
        "triggered_proxy_scenario_count": (
            int(
                (
                    condition_audit["triggered"]
                    & (condition_audit["evidence_grade"] == "proxy")
                ).sum()
            )
            if not condition_audit.empty
            else 0
        ),
        "status_counts": {str(key): int(value) for key, value in status_counts.items()},
        "order_time_source_counts": dict(
            Counter(plan.generated_at_source for plan in plans)
        ),
        "start": curve.index.min().isoformat(),
        "end": curve.index.max().isoformat(),
        "bars": len(curve),
        "fee_rate": fee_rate,
        "slippage_rate": slippage_rate,
        "initial_state_assumption": "100% BTC, 0% cash",
        "execution_model": "point_in_time_hourly_ohlcv_next_safe_bar",
        "condition_model": (
            "deterministic_exact_or_declared_proxy_next_bar"
            if condition_resolver is not None
            else "manual_conditions_not_evaluated"
        ),
    }
    return {
        "metrics": metrics,
        "equity": curve,
        "fills": pd.DataFrame(fills),
        "order_status": pd.DataFrame(status.values()),
        "condition_audit": condition_audit,
    }


def _report_markdown(metrics: dict[str, Any], quality: dict[str, Any]) -> str:
    pct = lambda value: f"{value * 100:.2f}%"
    outcome = "跑赢" if metrics["excess_return"] >= 0 else "落后于"
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
        f"{integrity.get('excluded_decisions', 0)} 份；被隔离日期不产生新计划，上一份合格 GTC 计划延续至下一份合格决策。\n"
        if integrity else ""
    )
    return f"""# 参数化委托回测：可验证指令的首轮证据

## 执行摘要

- 委托策略期末权益为 **{metrics['final_equity']:.2f} USDT**，区间收益率 **{pct(metrics['total_return'])}**；同期满仓持有基准收益率为 **{pct(metrics['benchmark_return'])}**，策略{outcome}基准 **{pct(abs(metrics['excess_return']))}**。
- 最大回撤为 **{pct(metrics['max_drawdown'])}**，持有基准最大回撤为 **{pct(metrics['benchmark_max_drawdown'])}**；共记录 **{metrics['fill_count']}** 次成交，累计费用 **{metrics['total_fees']:.2f} USDT**。
- 本次只评价能够由 1H OHLCV 确认的 **{metrics['ohlcv_evaluable_order_count']} / {metrics['total_order_count']}** 条委托，覆盖率 **{pct(metrics['ohlcv_evaluation_coverage'])}**。其余 {metrics['manual_condition_order_count']} 条依赖外部或人工条件，未被假设为成交。
- 委托时间全部锚定真实决策生成时间，共 {metrics['order_time_source_counts'].get('decision_generated_at', 0)} 份；历史补录不再允许伪造“任务开始后 20 分钟”的可交易时点。
{integrity_line}
{window_line}

## 回测口径

- 初始组合：10,000 USDT 等值 BTC、现金为零；参数 `X_POSITION` 与 `X_CASH` 在每份新计划生效时绑定到当时组合状态。
- 时间：委托在 CSV 的 `order_generated_at` 后首根完整 1H K 线开始可用；新计划替换同标的旧计划，未成交 GTC 委托随之撤销。
- 成交：市价单使用下一安全 K 线开盘并计入滑点；限价、止损单只在后续 K 线价格区间实际触及时成交。同一根 K 线无法判断路径时采用偏保守处理。
- 成本：手续费 {pct(metrics['fee_rate'])}，滑点 {pct(metrics['slippage_rate'])}；结果为研究回测，不代表实盘可成交价格。

## 数据质量与范围

- 数据：OKX BTC-USDT 现货 1H 已确认 K 线，区间 `{quality['start']}` 至 `{quality['end']}`。
- 已确认记录 {quality['confirmed_rows']} 条；过滤未完结 K 线 {quality['unconfirmed_rows_filtered']} 条；时间覆盖率 {pct(quality['coverage_ratio'])}。
- 实际回测区间 `{metrics['start']}` 至 `{metrics['end']}`，共 {metrics['bars']} 根 K 线。

## 解释边界

这不是对完整 Agent 决策质量的最终裁决。大量计划使用 ETF 流量、情绪指标、日线收盘或复合条件；仅有 OHLCV 时，这些条件无法按当时可得信息可靠复原。当前结果衡量的是“结构化委托中行情可验证部分”的执行表现，并为下一步接入更多历史上下文提供基线。
"""


def _html_summary(markdown_text: str) -> str:
    section = markdown_text.split("## 执行摘要", 1)[1].split("## 回测口径", 1)[0]
    bullets = [line[2:] for line in section.splitlines() if line.startswith("- ")]
    items = "".join(
        f"<li>{html.escape(item.replace('**', '').replace(chr(96), ''))}</li>"
        for item in bullets
    )
    return f"<ul>{items}</ul>"


def _report_html(markdown_text: str, metrics: dict[str, Any], curve: pd.DataFrame) -> str:
    pct = lambda value: f"{value * 100:.2f}%"
    summary = _html_summary(markdown_text)
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>RogueTrader 参数化委托回测</title><style>
:root{{--bg:#09111f;--panel:#111d31;--text:#edf4ff;--muted:#91a1bb;--accent:#58d6b5;--line:#253550}}
*{{box-sizing:border-box}}body{{margin:0;background:linear-gradient(145deg,#07101d,#0d1730);color:var(--text);font:15px/1.65 system-ui,sans-serif}}
main{{max-width:1040px;margin:auto;padding:48px 24px}}h1{{font-size:34px;margin:0 0 8px}}h2{{margin-top:34px}}.eyebrow{{color:var(--accent);letter-spacing:.12em;text-transform:uppercase}}
.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:26px 0}}.card{{background:rgba(17,29,49,.9);border:1px solid var(--line);border-radius:16px;padding:18px}}
.metric{{font-size:25px;font-weight:700}}.label,.muted{{color:var(--muted)}}.chart{{padding:18px;background:var(--panel);border:1px solid var(--line);border-radius:18px;overflow:hidden}}svg{{width:100%;height:auto}}
.summary{{background:rgba(88,214,181,.08);border-left:3px solid var(--accent);padding:18px 22px;border-radius:10px}}ul{{padding-left:20px}}code{{color:#b8e8dc}}@media(max-width:760px){{.grid{{grid-template-columns:repeat(2,1fr)}}h1{{font-size:28px}}}}
</style></head><body><main><div class="eyebrow">RogueTrader · Evidence Report</div><h1>参数化委托回测：可验证指令的首轮证据</h1>
<p class="muted">Point-in-time · Confirmed 1H OHLCV · Paper Simulation</p>
<div class="grid"><div class="card"><div class="label">策略收益</div><div class="metric">{pct(metrics['total_return'])}</div></div>
<div class="card"><div class="label">持有基准</div><div class="metric">{pct(metrics['benchmark_return'])}</div></div>
<div class="card"><div class="label">最大回撤</div><div class="metric">{pct(metrics['max_drawdown'])}</div></div>
<div class="card"><div class="label">OHLCV 覆盖</div><div class="metric">{pct(metrics['ohlcv_evaluation_coverage'])}</div></div></div>
<div class="chart">{_svg_equity(curve)}</div><h2>执行摘要</h2><div class="summary">{summary}</div>
<h2>关键事实</h2><ul><li>期末权益：{metrics['final_equity']:.2f} USDT；成交 {metrics['fill_count']} 次；费用 {metrics['total_fees']:.2f} USDT。</li>
<li>可由 OHLCV 评价 {metrics['ohlcv_evaluable_order_count']} / {metrics['total_order_count']} 条；人工或外部复合条件 {metrics['manual_condition_order_count']} 条保持未评估。</li>
<li>委托时间全部来自真实决策生成时间；历史修复不再回填虚构的可交易时点。</li>
<li>初始假设为 100% BTC、0% 现金；手续费 {pct(metrics['fee_rate'])}，滑点 {pct(metrics['slippage_rate'])}。</li></ul>
<h2>解释边界</h2><p>本报告只衡量结构化委托中能够由已确认 1H OHLCV 复原的部分，不把 ETF 流量、情绪、日线收盘或其他复合条件假设为已触发，因此不能替代对完整 Agent 决策质量的评估。</p>
</main></body></html>"""


def write_backtest_bundle(
    result: dict[str, Any],
    quality: dict[str, Any],
    output_root: str | Path,
    ticker: str,
) -> Path:
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    safe_ticker = ticker.replace("-", "_")
    target = Path(output_root).expanduser().resolve() / f"{stamp}__{safe_ticker}"
    target.mkdir(parents=True, exist_ok=False, mode=0o700)
    os.chmod(target, 0o700)
    metrics = dict(result["metrics"])
    metrics["data_quality"] = quality
    (target / "回测指标.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    result["equity"].to_csv(target / "权益曲线.csv", encoding="utf-8-sig")
    result["fills"].to_csv(target / "成交明细.csv", index=False, encoding="utf-8-sig")
    result["order_status"].to_csv(
        target / "委托回测状态.csv", index=False, encoding="utf-8-sig"
    )
    report = _report_markdown(metrics, quality)
    (target / "回测报告.md").write_text(report, encoding="utf-8")
    (target / "回测报告.html").write_text(
        _report_html(report, metrics, result["equity"]), encoding="utf-8"
    )
    for path in target.iterdir():
        if path.is_file():
            os.chmod(path, 0o600)
    return target


def run_execution_ledger_backtest(
    *,
    ledger_path: str | Path = DEFAULT_LEDGER,
    market_data_path: str | Path = DEFAULT_MARKET_DATA,
    output_root: str | Path = DEFAULT_OUTPUT_ROOT,
    ticker: str = "BTC-USD",
    initial_balance: float = 10_000.0,
    fee_rate: float = 0.001,
    slippage_rate: float = 0.0005,
    window_start: str | pd.Timestamp | None = None,
    window_days: int | None = None,
) -> tuple[Path, dict[str, Any]]:
    plans = load_execution_ledger(ledger_path, ticker)
    market, quality = load_confirmed_ohlcv(market_data_path)
    plans, market, window_metadata = select_evaluation_window(
        plans,
        market,
        window_start=window_start,
        window_days=window_days,
    )
    result = run_backtest(
        plans,
        market,
        initial_balance=initial_balance,
        fee_rate=fee_rate,
        slippage_rate=slippage_rate,
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
    target = write_backtest_bundle(result, quality, output_root, ticker)
    return target, result["metrics"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="使用参数化委托 CSV 和已确认 1H OHLCV 运行时点安全回测。"
    )
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--market-data", type=Path, default=DEFAULT_MARKET_DATA)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
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
        target, metrics = run_execution_ledger_backtest(
            ledger_path=args.ledger,
            market_data_path=args.market_data,
            output_root=args.output_root,
            ticker=args.ticker,
            initial_balance=args.initial_balance,
            fee_rate=args.fee_rate,
            slippage_rate=args.slippage_rate,
            window_start=args.window_start,
            window_days=args.window_days,
        )
    except ExecutionBacktestError as exc:
        raise SystemExit(f"execution ledger backtest error: {exc}") from None
    print(
        json.dumps(
            {
                "output": str(target),
                "plan_count": metrics["plan_count"],
                "order_count": metrics["total_order_count"],
                "evaluated_order_count": metrics["ohlcv_evaluable_order_count"],
                "fill_count": metrics["fill_count"],
                "total_return": metrics["total_return"],
                "benchmark_return": metrics["benchmark_return"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
