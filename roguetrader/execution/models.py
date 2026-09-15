"""Strict models for parameterized spot execution plans and paper binding."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_DOWN
import hashlib
import json
import math
import re
from typing import Any


EXECUTION_PLAN_SCHEMA_VERSION = "1.0"
EXECUTION_INSTANCE_SCHEMA_VERSION = "1.0"
MAX_SCENARIOS = 8
MAX_ORDERS_PER_SCENARIO = 6
IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
HEX_ID_RE = re.compile(r"^[0-9a-f]{64}$")
ACTIONS = {"BUY", "OVERWEIGHT", "HOLD", "UNDERWEIGHT", "SELL"}
SIDES = {"buy", "sell"}
INTENTS = {"open", "increase", "reduce", "exit"}
ORDER_TYPES = {"market", "limit", "stop_market", "stop_limit"}
SIZE_BASES = {"current_position_pct", "available_cash_pct", "fixed_quantity"}
TIME_IN_FORCE = {"GTC", "IOC", "FOK", "DAY"}
TRIGGER_OPERATORS = {"gte", "lte"}
CONTINUITY_ACTIONS = {"baseline", "retain", "amend", "replace"}
REQUIRED_RUNTIME_INPUTS = ("position_qty", "available_cash", "last_price")
OPTIONAL_RUNTIME_INPUTS = ("open_buy_qty", "open_sell_qty")
SYMBOLIC_PARAMETERS = {
    "X_POSITION": "执行时的当前现货持仓数量",
    "X_CASH": "执行时的可用计价货币资金",
}


class PlanValidationError(ValueError):
    """Raised when an execution plan or runtime binding is unsafe."""


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PlanValidationError(f"{label}必须是对象。")
    return value


def _exact_fields(
    value: dict[str, Any],
    *,
    label: str,
    required: set[str],
    optional: set[str] | None = None,
) -> None:
    optional = optional or set()
    missing = required - set(value)
    unknown = set(value) - required - optional
    if missing:
        raise PlanValidationError(f"{label}缺少字段：{', '.join(sorted(missing))}。")
    if unknown:
        raise PlanValidationError(f"{label}包含未知字段：{', '.join(sorted(unknown))}。")


def _text(value: Any, label: str, *, limit: int = 3000) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PlanValidationError(f"{label}必须是非空字符串。")
    normalized = value.strip()
    if len(normalized) > limit:
        raise PlanValidationError(f"{label}过长。")
    return normalized


def _identifier(value: Any, label: str) -> str:
    normalized = _text(value, label, limit=48).lower()
    if not IDENTIFIER_RE.fullmatch(normalized):
        raise PlanValidationError(f"{label}必须使用小写字母、数字和下划线。")
    return normalized


def _number(
    value: Any,
    label: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PlanValidationError(f"{label}必须是数字。")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise PlanValidationError(f"{label}必须是有限数字。")
    if minimum is not None and normalized < minimum:
        raise PlanValidationError(f"{label}不能小于 {minimum}。")
    if maximum is not None and normalized > maximum:
        raise PlanValidationError(f"{label}不能大于 {maximum}。")
    return normalized


def _optional_price(value: Any, label: str) -> float | None:
    if value is None:
        return None
    return _number(value, label, minimum=0.000000000001)


def _compact_number(value: float) -> str:
    return format(value, ".12g")


def _size_expression(basis: str, value: float) -> str:
    coefficient = _compact_number(value)
    if basis == "current_position_pct":
        return f"{coefficient} × X_POSITION"
    if basis == "available_cash_pct":
        return f"{coefficient} × X_CASH"
    return coefficient


def _enum(value: Any, label: str, allowed: set[str], *, upper: bool = False) -> str:
    normalized = _text(value, label, limit=32)
    normalized = normalized.upper() if upper else normalized.lower()
    if normalized not in allowed:
        raise PlanValidationError(f"{label}取值无效：{normalized}。")
    return normalized


def _normalize_trigger(value: Any, label: str) -> dict[str, Any]:
    trigger = _mapping(value, label)
    trigger_type = _enum(
        trigger.get("type"),
        f"{label}.type",
        {"immediate", "last_price", "manual_confirmation"},
    )
    if trigger_type == "immediate":
        _exact_fields(trigger, label=label, required={"type"})
        return {"type": "immediate"}
    if trigger_type == "manual_confirmation":
        _exact_fields(trigger, label=label, required={"type", "condition"})
        return {
            "type": "manual_confirmation",
            "condition": _text(trigger["condition"], f"{label}.condition", limit=500),
        }
    _exact_fields(
        trigger,
        label=label,
        required={"type", "operator", "value"},
    )
    return {
        "type": "last_price",
        "operator": _enum(
            trigger["operator"], f"{label}.operator", TRIGGER_OPERATORS
        ),
        "value": _number(trigger["value"], f"{label}.value", minimum=0.000000000001),
    }


def _normalize_order(value: Any, label: str) -> dict[str, Any]:
    order = _mapping(value, label)
    _exact_fields(
        order,
        label=label,
        required={
            "order_id",
            "sequence",
            "side",
            "intent",
            "order_type",
            "size",
        },
        optional={
            "limit_price",
            "stop_price",
            "take_profit",
            "stop_loss",
            "time_in_force",
            "after_order_id",
            "size_expression",
        },
    )
    order_id = _identifier(order["order_id"], f"{label}.order_id")
    sequence = order["sequence"]
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
        raise PlanValidationError(f"{label}.sequence必须是正整数。")
    side = _enum(order["side"], f"{label}.side", SIDES)
    intent = _enum(order["intent"], f"{label}.intent", INTENTS)
    if side == "buy" and intent in {"reduce", "exit"}:
        raise PlanValidationError(f"{label}是现货买单，不能使用 {intent} 意图。")
    if side == "sell" and intent in {"open", "increase"}:
        raise PlanValidationError(f"{label}是现货卖单，不能建立空头仓位。")
    order_type = _enum(order["order_type"], f"{label}.order_type", ORDER_TYPES)
    size = _mapping(order["size"], f"{label}.size")
    _exact_fields(
        size,
        label=f"{label}.size",
        required={"basis", "value"},
    )
    basis = _enum(size["basis"], f"{label}.size.basis", SIZE_BASES)
    maximum = 1.0 if basis.endswith("_pct") else None
    size_value = _number(
        size["value"],
        f"{label}.size.value",
        minimum=0.000000000001,
        maximum=maximum,
    )
    if side == "buy" and basis == "current_position_pct":
        raise PlanValidationError(f"{label}的买单不能按当前持仓比例计算。")
    if side == "sell" and basis == "available_cash_pct":
        raise PlanValidationError(f"{label}的卖单不能按可用现金比例计算。")
    size_expression = _size_expression(basis, size_value)
    supplied_expression = order.get("size_expression")
    if supplied_expression is not None and _text(
        supplied_expression, f"{label}.size_expression", limit=80
    ) != size_expression:
        raise PlanValidationError(f"{label}.size_expression与size不一致。")

    limit_price = _optional_price(order.get("limit_price"), f"{label}.limit_price")
    stop_price = _optional_price(order.get("stop_price"), f"{label}.stop_price")
    if order_type == "limit" and limit_price is None:
        raise PlanValidationError(f"{label}的限价单缺少 limit_price。")
    if order_type == "stop_market" and stop_price is None:
        raise PlanValidationError(f"{label}的止损市价单缺少 stop_price。")
    if order_type == "stop_limit" and (limit_price is None or stop_price is None):
        raise PlanValidationError(f"{label}的止损限价单需要 limit_price 和 stop_price。")
    if order_type == "market" and (limit_price is not None or stop_price is not None):
        raise PlanValidationError(f"{label}的市价单不能包含 limit_price 或 stop_price。")

    after = order.get("after_order_id")
    if after is not None:
        after = _identifier(after, f"{label}.after_order_id")
        if after == order_id:
            raise PlanValidationError(f"{label}不能依赖自身。")
    return {
        "order_id": order_id,
        "sequence": sequence,
        "side": side,
        "intent": intent,
        "order_type": order_type,
        "size": {"basis": basis, "value": size_value},
        "size_expression": size_expression,
        "limit_price": limit_price,
        "stop_price": stop_price,
        "take_profit": _optional_price(order.get("take_profit"), f"{label}.take_profit"),
        "stop_loss": _optional_price(order.get("stop_loss"), f"{label}.stop_loss"),
        "time_in_force": _enum(
            order.get("time_in_force", "GTC"),
            f"{label}.time_in_force",
            TIME_IN_FORCE,
            upper=True,
        ),
        "after_order_id": after,
    }


def normalize_execution_plan_draft(value: Any) -> dict[str, Any]:
    """Validate and normalize the small JSON contract produced by the planner."""

    draft = _mapping(value, "执行计划")
    _exact_fields(
        draft,
        label="执行计划",
        required={"plan_summary", "scenarios"},
        optional={
            "valid_for_hours",
            "risk_limits",
            "previous_plan_id",
            "continuity_action",
            "change_summary",
        },
    )
    valid_for_hours = draft.get("valid_for_hours", 24)
    if (
        isinstance(valid_for_hours, bool)
        or not isinstance(valid_for_hours, int)
        or not 1 <= valid_for_hours <= 168
    ):
        raise PlanValidationError("valid_for_hours必须是 1-168 的整数。")

    risk = _mapping(draft.get("risk_limits", {}), "risk_limits")
    _exact_fields(
        risk,
        label="risk_limits",
        required=set(),
        optional={"max_position_pct", "max_order_cash_pct"},
    )
    normalized_risk = {
        "max_position_pct": _number(
            risk.get("max_position_pct", 1.0),
            "risk_limits.max_position_pct",
            minimum=0.0,
            maximum=1.0,
        ),
        "max_order_cash_pct": _number(
            risk.get("max_order_cash_pct", 0.25),
            "risk_limits.max_order_cash_pct",
            minimum=0.0,
            maximum=1.0,
        ),
    }

    raw_scenarios = draft["scenarios"]
    if not isinstance(raw_scenarios, list) or not 1 <= len(raw_scenarios) <= MAX_SCENARIOS:
        raise PlanValidationError(f"scenarios必须包含 1-{MAX_SCENARIOS} 个场景。")
    scenarios: list[dict[str, Any]] = []
    scenario_ids: set[str] = set()
    priorities: set[int] = set()
    global_order_ids: set[str] = set()
    for scenario_index, raw_scenario in enumerate(raw_scenarios):
        label = f"scenarios[{scenario_index}]"
        scenario = _mapping(raw_scenario, label)
        _exact_fields(
            scenario,
            label=label,
            required={"scenario_id", "priority", "trigger", "orders"},
            optional={"exclusive_group"},
        )
        scenario_id = _identifier(scenario["scenario_id"], f"{label}.scenario_id")
        if scenario_id in scenario_ids:
            raise PlanValidationError(f"场景 ID 重复：{scenario_id}。")
        scenario_ids.add(scenario_id)
        priority = scenario["priority"]
        if isinstance(priority, bool) or not isinstance(priority, int) or priority < 1:
            raise PlanValidationError(f"{label}.priority必须是正整数。")
        if priority in priorities:
            raise PlanValidationError(f"场景优先级重复：{priority}。")
        priorities.add(priority)
        exclusive_group = scenario.get("exclusive_group")
        if exclusive_group is not None:
            exclusive_group = _identifier(
                exclusive_group, f"{label}.exclusive_group"
            )
        raw_orders = scenario["orders"]
        if not isinstance(raw_orders, list) or len(raw_orders) > MAX_ORDERS_PER_SCENARIO:
            raise PlanValidationError(
                f"{label}.orders最多包含 {MAX_ORDERS_PER_SCENARIO} 条委托。"
            )
        orders = [
            _normalize_order(item, f"{label}.orders[{order_index}]")
            for order_index, item in enumerate(raw_orders)
        ]
        order_ids = {order["order_id"] for order in orders}
        if len(order_ids) != len(orders):
            raise PlanValidationError(f"{label}包含重复的 order_id。")
        overlap = global_order_ids & order_ids
        if overlap:
            raise PlanValidationError(f"order_id必须全局唯一：{sorted(overlap)[0]}。")
        global_order_ids.update(order_ids)
        sequences = [order["sequence"] for order in orders]
        if len(sequences) != len(set(sequences)):
            raise PlanValidationError(f"{label}包含重复的执行顺序。")
        sequence_by_id = {order["order_id"]: order["sequence"] for order in orders}
        for order in orders:
            after = order["after_order_id"]
            if after is not None and (
                after not in sequence_by_id
                or sequence_by_id[after] >= order["sequence"]
            ):
                raise PlanValidationError(
                    f"{label}中的 {order['order_id']} 依赖必须指向更早的同场景委托。"
                )
        scenarios.append(
            {
                "scenario_id": scenario_id,
                "priority": priority,
                "exclusive_group": exclusive_group,
                "trigger": _normalize_trigger(scenario["trigger"], f"{label}.trigger"),
                "orders": sorted(orders, key=lambda item: item["sequence"]),
            }
        )

    previous_plan_id = draft.get("previous_plan_id")
    if previous_plan_id is not None and not HEX_ID_RE.fullmatch(str(previous_plan_id)):
        raise PlanValidationError("previous_plan_id格式无效。")
    continuity_action = _enum(
        draft.get("continuity_action", "baseline"),
        "continuity_action",
        CONTINUITY_ACTIONS,
    )
    if previous_plan_id is None and continuity_action != "baseline":
        raise PlanValidationError("没有上一份计划时 continuity_action 必须为 baseline。")
    if previous_plan_id is not None and continuity_action == "baseline":
        raise PlanValidationError("存在上一份计划时 continuity_action 不能为 baseline。")

    return {
        "previous_plan_id": previous_plan_id,
        "continuity_action": continuity_action,
        "change_summary": _text(
            draft.get("change_summary", "首份参数化执行计划。"),
            "change_summary",
            limit=1000,
        ),
        "plan_summary": _text(draft["plan_summary"], "plan_summary"),
        "valid_for_hours": valid_for_hours,
        "risk_limits": normalized_risk,
        "scenarios": sorted(scenarios, key=lambda item: item["priority"]),
    }


def _plan_identity_payload(
    plan: dict[str, Any], *, include_continuity: bool = True
) -> dict[str, Any]:
    scenarios = []
    for scenario in plan["scenarios"]:
        normalized_scenario = dict(scenario)
        normalized_scenario["orders"] = [
            {key: value for key, value in order.items() if key != "size_expression"}
            for order in scenario["orders"]
        ]
        scenarios.append(normalized_scenario)
    payload = {
        "decision_event_id": plan["decision_event_id"],
        "ticker": plan["ticker"],
        "analysis_date": plan["analysis_date"],
        "action": plan["action"],
        "plan_summary": plan["plan_summary"],
        "valid_for_hours": plan["valid_for_hours"],
        "risk_limits": plan["risk_limits"],
        "scenarios": scenarios,
    }
    if include_continuity:
        payload.update(
            {
                "previous_plan_id": plan.get("previous_plan_id"),
                "continuity_action": plan.get("continuity_action", "baseline"),
                "change_summary": plan.get(
                    "change_summary", "首份参数化执行计划。"
                ),
            }
        )
    return payload


def _stable_id(value: dict[str, Any]) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def build_execution_plan(
    draft: dict[str, Any],
    *,
    decision_event_id: str,
    ticker: str,
    analysis_date: str,
    action: str,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Finalize an LLM draft as an immutable, paper-only parameterized plan."""

    normalized = normalize_execution_plan_draft(draft)
    if not HEX_ID_RE.fullmatch(str(decision_event_id)):
        raise PlanValidationError("decision_event_id格式无效。")
    try:
        date.fromisoformat(str(analysis_date))
    except ValueError as exc:
        raise PlanValidationError("analysis_date必须使用 YYYY-MM-DD。") from exc
    normalized_action = str(action).upper()
    if normalized_action not in ACTIONS:
        raise PlanValidationError("action不在支持的评级范围内。")
    created = datetime.fromisoformat(created_at) if created_at else datetime.now().astimezone()
    if created.tzinfo is None:
        raise PlanValidationError("created_at必须包含时区。")
    plan = {
        "schema_version": EXECUTION_PLAN_SCHEMA_VERSION,
        "plan_id": "",
        "decision_event_id": decision_event_id,
        "created_at": created.isoformat(timespec="seconds"),
        "valid_until": (created + timedelta(hours=normalized["valid_for_hours"])).isoformat(
            timespec="seconds"
        ),
        "analysis_date": str(analysis_date),
        "ticker": _text(ticker, "ticker", limit=64),
        "instrument_type": "spot",
        "action": normalized_action,
        "status": "parameterized",
        "execution_mode": "paper",
        "auto_submit": False,
        "daily_user_input_required": False,
        "symbolic_parameters": dict(SYMBOLIC_PARAMETERS),
        "optional_numeric_binding": {
            "required_when_used": list(REQUIRED_RUNTIME_INPUTS),
            "optional": list(OPTIONAL_RUNTIME_INPUTS),
        },
        **normalized,
    }
    plan["plan_id"] = _stable_id(_plan_identity_payload(plan))
    validate_execution_plan(plan)
    return plan


def validate_execution_plan(value: Any) -> dict[str, Any]:
    """Validate a finalized execution-plan file and return a normalized copy."""

    plan = _mapping(value, "执行计划文件")
    has_continuity_identity = any(
        field in plan
        for field in ("previous_plan_id", "continuity_action", "change_summary")
    )
    _exact_fields(
        plan,
        label="执行计划文件",
        required={
            "schema_version",
            "plan_id",
            "decision_event_id",
            "created_at",
            "valid_until",
            "analysis_date",
            "ticker",
            "instrument_type",
            "action",
            "status",
            "execution_mode",
            "auto_submit",
            "plan_summary",
            "valid_for_hours",
            "risk_limits",
            "scenarios",
        },
        optional={
            "daily_user_input_required",
            "symbolic_parameters",
            "previous_plan_id",
            "continuity_action",
            "change_summary",
            "runtime_inputs",
            "optional_numeric_binding",
        },
    )
    if plan["schema_version"] != EXECUTION_PLAN_SCHEMA_VERSION:
        raise PlanValidationError("不支持的执行计划协议版本。")
    if plan["instrument_type"] != "spot":
        raise PlanValidationError("第一版执行计划仅支持 spot。")
    if plan["status"] != "parameterized":
        raise PlanValidationError("执行计划状态必须为 parameterized。")
    if plan["execution_mode"] != "paper" or plan["auto_submit"] is not False:
        raise PlanValidationError("执行计划必须保持 paper 模式且禁止自动提交。")
    if plan.get("daily_user_input_required", False) is not False:
        raise PlanValidationError("参数化执行计划不得要求每日人工输入。")
    if plan.get("symbolic_parameters", SYMBOLIC_PARAMETERS) != SYMBOLIC_PARAMETERS:
        raise PlanValidationError("symbolic_parameters与参数化协议不一致。")
    if not HEX_ID_RE.fullmatch(str(plan["plan_id"])):
        raise PlanValidationError("plan_id格式无效。")
    if not HEX_ID_RE.fullmatch(str(plan["decision_event_id"])):
        raise PlanValidationError("decision_event_id格式无效。")
    try:
        date.fromisoformat(str(plan["analysis_date"]))
        created = datetime.fromisoformat(str(plan["created_at"]))
        valid_until = datetime.fromisoformat(str(plan["valid_until"]))
    except ValueError as exc:
        raise PlanValidationError("执行计划日期或时间格式无效。") from exc
    if created.tzinfo is None or valid_until.tzinfo is None or valid_until <= created:
        raise PlanValidationError("执行计划时间必须包含时区且有效期晚于创建时间。")
    action = str(plan["action"]).upper()
    if action not in ACTIONS:
        raise PlanValidationError("执行计划 action 无效。")
    legacy_binding = plan.get("runtime_inputs")
    optional_binding = plan.get("optional_numeric_binding")
    if (legacy_binding is None) == (optional_binding is None):
        raise PlanValidationError("执行计划必须且只能包含一种数值绑定协议。")
    if legacy_binding is not None:
        binding = _mapping(legacy_binding, "runtime_inputs")
        expected_binding = {
            "required": list(REQUIRED_RUNTIME_INPUTS),
            "optional": list(OPTIONAL_RUNTIME_INPUTS),
        }
    else:
        binding = _mapping(optional_binding, "optional_numeric_binding")
        expected_binding = {
            "required_when_used": list(REQUIRED_RUNTIME_INPUTS),
            "optional": list(OPTIONAL_RUNTIME_INPUTS),
        }
    if binding != expected_binding:
        raise PlanValidationError("可选数值绑定协议不一致。")
    normalized = normalize_execution_plan_draft(
        {
            "plan_summary": plan["plan_summary"],
            "valid_for_hours": plan["valid_for_hours"],
            "risk_limits": plan["risk_limits"],
            "scenarios": plan["scenarios"],
            "previous_plan_id": plan.get("previous_plan_id"),
            "continuity_action": plan.get("continuity_action", "baseline"),
            "change_summary": plan.get(
                "change_summary", "首份参数化执行计划。"
            ),
        }
    )
    result = dict(plan)
    result.update(normalized)
    result["daily_user_input_required"] = False
    result["symbolic_parameters"] = dict(SYMBOLIC_PARAMETERS)
    expected_valid_until = created + timedelta(hours=normalized["valid_for_hours"])
    if valid_until != expected_valid_until:
        raise PlanValidationError("执行计划有效期与 valid_for_hours 不一致。")
    result["ticker"] = _text(plan["ticker"], "ticker", limit=64)
    result["action"] = action
    if result["plan_id"] != _stable_id(
        _plan_identity_payload(
            result, include_continuity=has_continuity_identity
        )
    ):
        raise PlanValidationError("执行计划内容与 plan_id 不一致。")
    return result


def _decimal(value: Any, label: str, *, positive: bool = False) -> Decimal:
    if isinstance(value, bool):
        raise PlanValidationError(f"{label}必须是数字。")
    try:
        normalized = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise PlanValidationError(f"{label}必须是数字。") from exc
    if not normalized.is_finite() or normalized < 0 or (positive and normalized <= 0):
        qualifier = "正数" if positive else "非负数"
        raise PlanValidationError(f"{label}必须是{qualifier}。")
    return normalized


def _decimal_text(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return "0" if text in {"-0", ""} else text


@dataclass(frozen=True)
class RuntimeState:
    """Minimal account snapshot required to bind a spot paper plan."""

    position_qty: Decimal
    available_cash: Decimal
    last_price: Decimal
    open_buy_qty: Decimal = Decimal("0")
    open_sell_qty: Decimal = Decimal("0")

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "RuntimeState":
        state = _mapping(value, "运行时状态")
        _exact_fields(
            state,
            label="运行时状态",
            required=set(REQUIRED_RUNTIME_INPUTS),
            optional=set(OPTIONAL_RUNTIME_INPUTS),
        )
        result = cls(
            position_qty=_decimal(state["position_qty"], "position_qty"),
            available_cash=_decimal(state["available_cash"], "available_cash"),
            last_price=_decimal(state["last_price"], "last_price", positive=True),
            open_buy_qty=_decimal(state.get("open_buy_qty", 0), "open_buy_qty"),
            open_sell_qty=_decimal(state.get("open_sell_qty", 0), "open_sell_qty"),
        )
        if result.open_sell_qty > result.position_qty:
            raise PlanValidationError("open_sell_qty不能超过当前持仓。")
        return result

    def to_dict(self) -> dict[str, str]:
        return {
            "position_qty": _decimal_text(self.position_qty),
            "available_cash": _decimal_text(self.available_cash),
            "last_price": _decimal_text(self.last_price),
            "open_buy_qty": _decimal_text(self.open_buy_qty),
            "open_sell_qty": _decimal_text(self.open_sell_qty),
        }


@dataclass(frozen=True)
class InstrumentRules:
    """Static paper-market rules; these are not realtime inputs."""

    quantity_step: Decimal = Decimal("0.00000001")
    min_notional: Decimal = Decimal("0.01")

    def __post_init__(self) -> None:
        if self.quantity_step <= 0 or self.min_notional < 0:
            raise PlanValidationError("静态市场规则无效。")


def _order_reference_price(order: dict[str, Any], state: RuntimeState) -> Decimal:
    if order["order_type"] == "limit":
        return Decimal(str(order["limit_price"]))
    if order["order_type"] == "stop_market":
        return Decimal(str(order["stop_price"]))
    if order["order_type"] == "stop_limit":
        return Decimal(str(order["limit_price"]))
    return state.last_price


def _triggered(trigger: dict[str, Any], state: RuntimeState) -> bool:
    if trigger["type"] == "immediate":
        return True
    if trigger["type"] == "manual_confirmation":
        return False
    threshold = Decimal(str(trigger["value"]))
    if trigger["operator"] == "gte":
        return state.last_price >= threshold
    return state.last_price <= threshold


def bind_execution_plan(
    plan: dict[str, Any],
    runtime_state: RuntimeState | dict[str, Any],
    *,
    rules: InstrumentRules | None = None,
    bound_at: str | None = None,
) -> dict[str, Any]:
    """Resolve a parameterized plan against five-or-fewer account values."""

    normalized_plan = validate_execution_plan(plan)
    state = (
        runtime_state
        if isinstance(runtime_state, RuntimeState)
        else RuntimeState.from_dict(runtime_state)
    )
    rules = rules or InstrumentRules()
    now = datetime.fromisoformat(bound_at) if bound_at else datetime.now().astimezone()
    if now.tzinfo is None:
        raise PlanValidationError("bound_at必须包含时区。")
    expired = now >= datetime.fromisoformat(normalized_plan["valid_until"])
    errors: list[str] = []
    bound_scenarios: list[dict[str, Any]] = []
    ready_orders = 0
    active_scenarios = 0
    equity = state.available_cash + state.position_qty * state.last_price
    max_position_pct = Decimal(str(normalized_plan["risk_limits"]["max_position_pct"]))
    max_order_cash_pct = Decimal(
        str(normalized_plan["risk_limits"]["max_order_cash_pct"])
    )
    selected_exclusive_groups: dict[str, str] = {}

    for scenario in normalized_plan["scenarios"]:
        condition_met = False if expired else _triggered(scenario["trigger"], state)
        active = condition_met
        scenario_reason = None
        exclusive_group = scenario["exclusive_group"]
        if active and exclusive_group:
            selected = selected_exclusive_groups.get(exclusive_group)
            if selected is not None:
                active = False
                scenario_reason = f"互斥组已由更高优先级场景 {selected} 命中"
            else:
                selected_exclusive_groups[exclusive_group] = scenario["scenario_id"]
        if active:
            active_scenarios += 1
        cash_remaining = state.available_cash
        position_remaining = state.position_qty
        projected_position = state.position_qty
        pending = {"buy": state.open_buy_qty, "sell": state.open_sell_qty}
        resolved_orders: list[dict[str, Any]] = []
        for order in scenario["orders"]:
            reference_price = _order_reference_price(order, state)
            basis = order["size"]["basis"]
            value = Decimal(str(order["size"]["value"]))
            if basis == "current_position_pct":
                desired = state.position_qty * value
            elif basis == "available_cash_pct":
                desired = (state.available_cash * value) / reference_price
            else:
                desired = value

            covered = min(desired, pending[order["side"]])
            pending[order["side"]] -= covered
            quantity = desired - covered
            quantity = (quantity / rules.quantity_step).to_integral_value(
                rounding=ROUND_DOWN
            ) * rules.quantity_step
            status = "ready" if active else (
                "suppressed"
                if scenario_reason
                else (
                    "waiting_confirmation"
                    if scenario["trigger"]["type"] == "manual_confirmation"
                    else "waiting_trigger"
                )
            )
            reason = scenario_reason
            dependency = order["after_order_id"]
            if dependency and active:
                status = "waiting_dependency"
                reason = "等待前置委托成交后重新绑定"
            if active:
                if desired <= 0:
                    status = "no_quantity"
                    reason = "当前持仓或可用现金为零"
                elif quantity <= 0:
                    status = "covered_by_open_orders"
                    reason = "已有未成交委托已覆盖计划数量"
                elif quantity * reference_price < rules.min_notional:
                    status = "below_minimum"
                    reason = "委托金额低于静态模拟市场下限"

            if status == "ready":
                if order["side"] == "buy":
                    notional = quantity * reference_price
                    if notional > cash_remaining:
                        status = "blocked"
                        reason = "可用现金不足"
                    elif notional > state.available_cash * max_order_cash_pct:
                        status = "blocked"
                        reason = "超过单笔现金比例上限"
                    elif equity <= 0 or (
                        (projected_position + quantity) * state.last_price / equity
                        > max_position_pct
                    ):
                        status = "blocked"
                        reason = "超过计划持仓比例上限"
                    else:
                        cash_remaining -= notional
                        projected_position += quantity
                else:
                    if quantity > position_remaining:
                        status = "blocked"
                        reason = "可卖数量超过当前持仓"
                    else:
                        position_remaining -= quantity
                        projected_position -= quantity

            if status == "blocked":
                errors.append(f"{scenario['scenario_id']}.{order['order_id']}: {reason}")
            if status == "ready":
                ready_orders += 1
            resolved_orders.append(
                {
                    "order_id": order["order_id"],
                    "sequence": order["sequence"],
                    "side": order["side"],
                    "intent": order["intent"],
                    "order_type": order["order_type"],
                    "quantity": _decimal_text(quantity),
                    "reference_price": _decimal_text(reference_price),
                    "limit_price": order["limit_price"],
                    "stop_price": order["stop_price"],
                    "take_profit": order["take_profit"],
                    "stop_loss": order["stop_loss"],
                    "time_in_force": order["time_in_force"],
                    "after_order_id": dependency,
                    "status": status,
                    "reason": reason,
                }
            )
        bound_scenarios.append(
            {
                "scenario_id": scenario["scenario_id"],
                "priority": scenario["priority"],
                "exclusive_group": exclusive_group,
                "trigger": scenario["trigger"],
                "triggered": condition_met,
                "selected": active,
                "reason": scenario_reason,
                "orders": resolved_orders,
            }
        )

    if expired:
        status = "expired"
    elif errors:
        status = "blocked"
    elif ready_orders:
        status = "ready"
    elif active_scenarios:
        status = "no_action"
    else:
        status = "waiting"
    snapshot = state.to_dict()
    instance_id = _stable_id(
        {
            "plan_id": normalized_plan["plan_id"],
            "bound_at": now.isoformat(timespec="seconds"),
            "runtime_snapshot": snapshot,
        }
    )
    return {
        "schema_version": EXECUTION_INSTANCE_SCHEMA_VERSION,
        "instance_id": instance_id,
        "plan_id": normalized_plan["plan_id"],
        "decision_event_id": normalized_plan["decision_event_id"],
        "bound_at": now.isoformat(timespec="seconds"),
        "ticker": normalized_plan["ticker"],
        "status": status,
        "execution_mode": "paper",
        "auto_submit": False,
        "ready_for_simulation": status == "ready",
        "runtime_snapshot": snapshot,
        "scenarios": bound_scenarios,
        "errors": errors,
    }
