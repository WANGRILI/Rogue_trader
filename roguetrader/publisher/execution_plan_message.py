"""Load and render a concise execution-plan notification without an LLM call."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from roguetrader.execution import PlanValidationError, validate_execution_plan
from roguetrader.output_paths import RUN_RESULTS_DIR
from roguetrader.publisher.models import (
    DecisionRecord,
    PreparedMessage,
    PublicationError,
    compact_text,
    now_iso,
)


MAX_PLAN_BYTES = 2 * 1024 * 1024
PLAN_MESSAGE_LIMIT = 5800


def _load_plan(path: Path) -> dict[str, Any]:
    if path.stat().st_size > MAX_PLAN_BYTES:
        raise PublicationError("执行计划文件过大。")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return validate_execution_plan(value)
    except OSError as exc:
        raise PublicationError("无法读取执行计划。") from exc
    except json.JSONDecodeError as exc:
        raise PublicationError("执行计划不是有效 JSON。") from exc
    except PlanValidationError as exc:
        raise PublicationError(f"执行计划校验失败：{exc}") from exc


def _run_root(results_root: str | Path) -> Path:
    root = Path(results_root).expanduser().resolve()
    return root if root.name == RUN_RESULTS_DIR else root / RUN_RESULTS_DIR


def load_execution_plan(
    results_root: str | Path, record: DecisionRecord
) -> dict[str, Any] | None:
    """Load a plan by run id, falling back to its stable decision event id."""

    run_root = _run_root(results_root)
    direct = (run_root / record.run_id).resolve()
    try:
        inside_root = direct.is_relative_to(run_root)
    except AttributeError:  # pragma: no cover - Python 3.8 compatibility
        inside_root = run_root == direct or run_root in direct.parents
    candidates: list[Path] = []
    if inside_root and direct.is_dir():
        candidates.append(direct / "执行计划.json")
    if not candidates:
        candidates.extend(sorted(run_root.glob("*/执行计划.json")))

    for path in candidates:
        if not path.is_file():
            continue
        plan = _load_plan(path)
        if plan["decision_event_id"] != record.event_id:
            if path.parent == direct:
                raise PublicationError("执行计划与发布事件不一致。")
            continue
        if (
            plan["ticker"] != record.ticker
            or plan["analysis_date"] != record.trade_date
            or plan["action"] != record.action
        ):
            raise PublicationError("执行计划与最终决策元数据不一致。")
        return plan
    return None


def _level(action: str) -> str:
    return {
        "BUY": "positive",
        "OVERWEIGHT": "positive",
        "SELL": "negative",
        "UNDERWEIGHT": "negative",
        "HOLD": "neutral",
        "NEUTRAL": "neutral",
    }.get(action, "info")


def _number(value: Any) -> str:
    numeric = float(value)
    if numeric.is_integer():
        return f"{int(numeric):,}"
    return f"{numeric:,.8f}".rstrip("0").rstrip(".")


def _percentage(value: Any) -> str:
    return f"{float(value) * 100:.4f}".rstrip("0").rstrip(".") + "%"


def _order_state(scenario: dict[str, Any]) -> str:
    orders = scenario["orders"]
    if not orders:
        return "无需执行"
    trigger = scenario["trigger"]
    if trigger["type"] == "immediate":
        if all(order["order_type"] == "market" for order in orders):
            return "立即执行意图"
        if all(
            order["order_type"] == "limit" and order["time_in_force"] == "GTC"
            for order in orders
        ):
            return "现在就可挂出的 GTC 限价单"
        return "现在可准备的委托"
    if trigger["type"] == "last_price":
        return "等待价格条件"
    condition = str(trigger.get("condition") or "")
    if re.search(r"\bAND\b|\bETF\b|volume-backed|同时|并且|且", condition, re.I):
        return "等待复合条件"
    return "等待条件确认"


def _scenario_meaning(scenario: dict[str, Any]) -> str:
    orders = scenario["orders"]
    if not orders:
        return "当前不产生委托，继续观察。"
    trigger = scenario["trigger"]
    sides = {order["side"] for order in orders}
    intents = {order["intent"] for order in orders}
    if trigger["type"] == "immediate":
        if sides == {"sell"} and all(order["order_type"] == "market" for order in orders):
            total = sum(float(order["size"]["value"]) for order in orders)
            return f"现在先减仓 {_percentage(total)}。"
        if sides == {"sell"} and all(order["order_type"] == "limit" for order in orders):
            prices = "、".join(
                _number(order["limit_price"]) for order in orders
            )
            return f"价格触及 {prices} 时分批减仓。"
        return "当前即可准备或执行这一组委托。"
    condition = str(trigger.get("condition") or "")
    lowered = condition.lower()
    if sides == {"sell"} and "exit" in intents:
        return "深度破位时退出剩余仓位。"
    if sides == {"sell"}:
        return "关键条件确认后进一步减仓。"
    if sides == {"buy"} and ("fear" in lowered or "恐慌" in condition):
        return "极度恐慌区域尝试小仓位逆势买入。"
    if sides == {"buy"} and ("close above" in lowered or "站上" in condition):
        return "趋势重新转强并获得确认后买回。"
    if sides == {"buy"}:
        return "条件成立后按计划买入。"
    return "条件成立后执行这一组委托。"


def _order_text(order: dict[str, Any]) -> str:
    side = "买入" if order["side"] == "buy" else "卖出"
    expression = order["size_expression"]
    order_type = order["order_type"]
    if order_type == "market":
        text = f"市价{side} `{expression}`"
    elif order_type == "limit":
        text = f"在 `{_number(order['limit_price'])}` 限价{side} `{expression}`"
    elif order_type == "stop_market":
        text = f"触及 `{_number(order['stop_price'])}` 后市价{side} `{expression}`"
    else:
        text = (
            f"触及 `{_number(order['stop_price'])}` 后，在 "
            f"`{_number(order['limit_price'])}` 限价{side} `{expression}`"
        )
    protections = []
    if order.get("take_profit") is not None:
        protections.append(f"止盈 `{_number(order['take_profit'])}`")
    if order.get("stop_loss") is not None:
        protections.append(f"止损 `{_number(order['stop_loss'])}`")
    if protections:
        text += "；" + "，".join(protections)
    return text


def _trigger_text(trigger: dict[str, Any]) -> str | None:
    if trigger["type"] == "immediate":
        return None
    if trigger["type"] == "manual_confirmation":
        return str(trigger["condition"])
    operator = "≥" if trigger["operator"] == "gte" else "≤"
    return f"最新价格 {operator} {_number(trigger['value'])}"


def render_execution_plan_message(
    plan: dict[str, Any], record: DecisionRecord
) -> PreparedMessage:
    lines = [f"**计划主旨**\n{plan['plan_summary']}", "", "**指令状态与含义**"]
    next_number = 1
    for scenario in plan["scenarios"]:
        count = len(scenario["orders"])
        if count == 0:
            number_label = "—"
        elif count == 1:
            number_label = str(next_number)
        else:
            number_label = f"{next_number}–{next_number + count - 1}"
        lines.extend(
            [
                "",
                f"**{number_label} · {_order_state(scenario)}**",
                f"含义：{_scenario_meaning(scenario)}",
            ]
        )
        condition = _trigger_text(scenario["trigger"])
        if condition:
            lines.append(f"条件：{condition}")
        for offset, order in enumerate(scenario["orders"]):
            prefix = (
                f"指令 {next_number + offset}："
                if count > 1
                else "指令："
            )
            lines.append(prefix + _order_text(order))
        next_number += count
    lines.extend(
        [
            "",
            "**关系说明**",
            "“立即执行/可挂出”表示当前可准备；“等待”表示条件成立后再处理，并非全部同时挂单。",
            "X_POSITION 与 X_CASH 在实际执行时取值，每日分析无需人工输入。",
        ]
    )
    continuity = {
        "baseline": "首份基准计划",
        "retain": "延续上一计划",
        "amend": "调整上一计划",
        "replace": "替换上一计划",
    }.get(plan.get("continuity_action"), "兼容历史计划")
    return PreparedMessage(
        event_id=record.event_id,
        title=f"RogueTrader 参数化执行指令 · {record.ticker}",
        level=_level(record.action),
        text=compact_text("\n".join(lines), PLAN_MESSAGE_LIMIT),
        fields=(
            ("分析日期", record.trade_date),
            ("最终评级", record.action),
            ("计划关系", continuity),
            ("每日人工输入", "0 项"),
            ("真实下单", "禁用"),
        ),
        run_id=record.run_id,
        created_at=now_iso(),
        section_title="执行指令",
        source_file="执行计划.json",
    )


def merge_execution_and_decision_message(
    execution: PreparedMessage, decision: PreparedMessage
) -> PreparedMessage:
    """Return one card payload with execution instructions before the decision."""

    fields: list[tuple[str, str]] = list(decision.fields)
    existing_labels = {label for label, _value in fields}
    for label, value in execution.fields:
        if label not in existing_labels:
            fields.append((label, value))
            existing_labels.add(label)
    return PreparedMessage(
        event_id=decision.event_id,
        title=f"RogueTrader 执行指令与决策 · {dict(decision.fields).get('标的', '')}",
        level=decision.level,
        text=execution.text,
        fields=tuple(fields),
        run_id=decision.run_id,
        created_at=now_iso(),
        section_title="执行指令",
        source_file="执行计划.json + 最终决策.json",
        sections=(
            ("执行指令", execution.text),
            ("决策摘要", decision.text),
        ),
    )
