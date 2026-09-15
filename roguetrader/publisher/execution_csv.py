"""CSV audit sink for parameterized execution-plan orders."""

from __future__ import annotations

import csv
from datetime import datetime, timedelta
import fcntl
import hashlib
import os
from pathlib import Path
import re
from typing import Any
from zoneinfo import ZoneInfo

from roguetrader.execution import validate_execution_plan
from roguetrader.output_paths import parse_run_directory_name
from roguetrader.publisher.execution_plan_message import load_execution_plan
from roguetrader.publisher.loader import load_completed_run
from roguetrader.publisher.models import DecisionRecord, PublicationError
from roguetrader.publisher.sinks import serialize_cell


EXECUTION_CSV_SCHEMA_VERSION = "2.0"
MAX_TRUSTED_GENERATION_DELAY = timedelta(hours=6)
SHANGHAI_TIMEZONE = ZoneInfo("Asia/Shanghai")
LEGACY_RUN_START_RE = re.compile(r"^(\d{8}_\d{6})(?:_|$)")
EXECUTION_CSV_FIELDS = (
    "order_event_id",
    "analysis_date",
    "ticker",
    "action",
    "instruction_state",
    "scenario_priority",
    "scenario_id",
    "exclusive_group",
    "scenario_condition",
    "trigger_type",
    "trigger_operator",
    "trigger_value",
    "order_sequence",
    "order_id",
    "order_summary",
    "side",
    "intent",
    "order_type",
    "size_basis",
    "size_value",
    "size_expression",
    "limit_price",
    "stop_price",
    "take_profit",
    "stop_loss",
    "time_in_force",
    "after_order_id",
    "plan_id",
    "previous_plan_id",
    "continuity_action",
    "change_summary",
    "plan_summary",
    "decision_event_id",
    "run_id",
    "order_generated_at",
    "order_generated_at_source",
    "order_valid_until",
    "created_at",
    "valid_until",
    "valid_for_hours",
    "max_position_pct",
    "max_order_cash_pct",
    "instrument_type",
    "execution_mode",
    "auto_submit",
    "row_type",
    "schema_version",
)
EXECUTION_CSV_V2_FIELDS = {
    "trigger_type",
    "trigger_operator",
    "trigger_value",
    "order_generated_at",
    "order_generated_at_source",
    "order_valid_until",
    "valid_for_hours",
    "max_position_pct",
    "max_order_cash_pct",
}
EXECUTION_CSV_FIELDS_V1 = tuple(
    field for field in EXECUTION_CSV_FIELDS if field not in EXECUTION_CSV_V2_FIELDS
)


def _private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)


def _stable_order_event_id(plan_id: str, scenario_id: str, order_id: str) -> str:
    raw = f"{plan_id}\0{scenario_id}\0{order_id}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _number(value: Any) -> str:
    if value is None:
        return ""
    numeric = float(value)
    if numeric.is_integer():
        return str(int(numeric))
    return f"{numeric:.12f}".rstrip("0").rstrip(".")


def _aware_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SHANGHAI_TIMEZONE)
    return parsed


def _run_started_at(run_id: str) -> datetime | None:
    parsed = parse_run_directory_name(run_id)
    if parsed is not None:
        started = parsed["started_at"]
        if isinstance(started, datetime):
            return started.replace(tzinfo=SHANGHAI_TIMEZONE)
    match = LEGACY_RUN_START_RE.match(str(run_id))
    if match is None:
        return None
    return datetime.strptime(match.group(1), "%Y%m%d_%H%M%S").replace(
        tzinfo=SHANGHAI_TIMEZONE
    )


def resolve_order_generated_at(record: DecisionRecord) -> tuple[str, str]:
    """Return a point-in-time-safe order availability timestamp and its source."""

    started = _run_started_at(record.run_id)
    generated = _aware_datetime(record.generated_at)
    repair_like_record = (
        str(record.runtime_mode).lower() == "legacy"
        or str(record.trigger).lower() in {"unknown", "recovery"}
    )
    if generated is None:
        raise PublicationError("无法确定参数化委托的真实生成时间。")
    if started is not None and generated < started:
        raise PublicationError("参数化委托生成时间早于任务开始时间。")
    if (
        started is not None
        and repair_like_record
        and generated - started > MAX_TRUSTED_GENERATION_DELAY
    ):
        raise PublicationError(
            "历史修复决策缺少当时数据快照，禁止把委托时间回填到原任务时点。"
        )
    if generated is not None:
        return generated.isoformat(timespec="seconds"), "decision_generated_at"
    raise PublicationError("无法确定参数化委托的真实生成时间。")


def _scenario_condition(trigger: dict[str, Any]) -> str:
    trigger_type = trigger["type"]
    if trigger_type == "immediate":
        return "立即"
    if trigger_type == "last_price":
        operator = {
            "lt": "<",
            "lte": "<=",
            "gt": ">",
            "gte": ">=",
        }[trigger["operator"]]
        return f"last_price {operator} {_number(trigger['value'])}"
    return str(trigger["condition"])


def _instruction_state(
    trigger: dict[str, Any], orders: list[dict[str, Any]]
) -> str:
    if not orders:
        return "无需执行"
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
    markers = (" AND ", " and ", "同时", "并且", "且", "ETF", "volume")
    return "等待复合条件" if any(marker in condition for marker in markers) else "等待条件确认"


def _order_summary(order: dict[str, Any] | None) -> str:
    if order is None:
        return "该场景无需生成委托"
    side = "买入" if order["side"] == "buy" else "卖出"
    order_type = {
        "market": "市价",
        "limit": "限价",
        "stop_market": "止损市价",
        "stop_limit": "止损限价",
    }[order["order_type"]]
    price = ""
    if order["order_type"] == "limit":
        price = f"，限价 {_number(order['limit_price'])}"
    elif order["order_type"] == "stop_market":
        price = f"，触发价 {_number(order['stop_price'])}"
    elif order["order_type"] == "stop_limit":
        price = (
            f"，触发价 {_number(order['stop_price'])}"
            f"，限价 {_number(order['limit_price'])}"
        )
    return f"{order_type}{side} {order['size_expression']}{price}"


def execution_plan_rows(
    plan: dict[str, Any], record: DecisionRecord
) -> tuple[dict[str, str], ...]:
    """Flatten one validated plan into stable order-level CSV rows."""

    normalized = validate_execution_plan(plan)
    if (
        normalized["decision_event_id"] != record.event_id
        or normalized["ticker"] != record.ticker
        or normalized["analysis_date"] != record.trade_date
        or normalized["action"] != record.action
    ):
        raise PublicationError("执行计划与最终决策元数据不一致。")

    order_generated_at, order_time_source = resolve_order_generated_at(record)
    order_valid_until = (
        datetime.fromisoformat(order_generated_at)
        + timedelta(hours=normalized["valid_for_hours"])
    ).isoformat(timespec="seconds")

    rows: list[dict[str, str]] = []
    for scenario in normalized["scenarios"]:
        trigger = scenario["trigger"]
        orders = scenario["orders"]
        state = _instruction_state(trigger, orders)
        row_orders: list[dict[str, Any] | None] = list(orders) or [None]
        for order in row_orders:
            order_id = str(order["order_id"]) if order is not None else "__NO_ORDER__"
            raw = {
                "order_event_id": _stable_order_event_id(
                    normalized["plan_id"], scenario["scenario_id"], order_id
                ),
                "analysis_date": normalized["analysis_date"],
                "ticker": normalized["ticker"],
                "action": normalized["action"],
                "instruction_state": state,
                "scenario_priority": scenario["priority"],
                "scenario_id": scenario["scenario_id"],
                "exclusive_group": scenario["exclusive_group"],
                "scenario_condition": _scenario_condition(trigger),
                "trigger_type": trigger["type"],
                "trigger_operator": trigger.get("operator"),
                "trigger_value": trigger.get("value"),
                "order_sequence": order["sequence"] if order is not None else "",
                "order_id": order["order_id"] if order is not None else "",
                "order_summary": _order_summary(order),
                "side": order["side"] if order is not None else "",
                "intent": order["intent"] if order is not None else "",
                "order_type": order["order_type"] if order is not None else "",
                "size_basis": order["size"]["basis"] if order is not None else "",
                "size_value": order["size"]["value"] if order is not None else "",
                "size_expression": (
                    order["size_expression"] if order is not None else ""
                ),
                "limit_price": order["limit_price"] if order is not None else "",
                "stop_price": order["stop_price"] if order is not None else "",
                "take_profit": order["take_profit"] if order is not None else "",
                "stop_loss": order["stop_loss"] if order is not None else "",
                "time_in_force": order["time_in_force"] if order is not None else "",
                "after_order_id": order["after_order_id"] if order is not None else "",
                "plan_id": normalized["plan_id"],
                "previous_plan_id": normalized.get("previous_plan_id"),
                "continuity_action": normalized.get(
                    "continuity_action", "baseline"
                ),
                "change_summary": normalized.get("change_summary", ""),
                "plan_summary": normalized["plan_summary"],
                "decision_event_id": normalized["decision_event_id"],
                "run_id": record.run_id,
                "order_generated_at": order_generated_at,
                "order_generated_at_source": order_time_source,
                "order_valid_until": order_valid_until,
                "created_at": normalized["created_at"],
                "valid_until": normalized["valid_until"],
                "valid_for_hours": normalized["valid_for_hours"],
                "max_position_pct": normalized["risk_limits"][
                    "max_position_pct"
                ],
                "max_order_cash_pct": normalized["risk_limits"][
                    "max_order_cash_pct"
                ],
                "instrument_type": normalized["instrument_type"],
                "execution_mode": normalized["execution_mode"],
                "auto_submit": normalized["auto_submit"],
                "row_type": "order" if order is not None else "no_order",
                "schema_version": EXECUTION_CSV_SCHEMA_VERSION,
            }
            rows.append(
                {field: serialize_cell(raw.get(field)) for field in EXECUTION_CSV_FIELDS}
            )
    return tuple(rows)


class ExecutionPlanCsvSink:
    """Persist one logical row per parameterized order, without duplicates."""

    name = "execution_csv"

    def __init__(self, path: str | Path, results_root: str | Path | None = None):
        self.path = Path(path).expanduser().resolve()
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self.results_root = (
            Path(results_root).expanduser().resolve() if results_root else None
        )

    def write(self, record: DecisionRecord) -> None:
        if self.results_root is None:
            raise PublicationError("执行委托 CSV 缺少结果根目录。")
        plan = load_execution_plan(self.results_root, record)
        if plan is not None:
            self.persist(plan, record)

    def persist(self, plan: dict[str, Any], record: DecisionRecord) -> int:
        rows = execution_plan_rows(plan, record)
        _private_directory(self.path.parent)
        with self.lock_path.open("a+", encoding="utf-8") as lock_file:
            os.chmod(self.lock_path, 0o600)
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            existing = self._existing_rows_unlocked()
            missing: list[dict[str, str]] = []
            for row in rows:
                event_id = row["order_event_id"]
                previous = existing.get(event_id)
                if previous is not None and previous != row:
                    raise PublicationError("参数化委托 CSV 中的幂等记录内容冲突。")
                if previous is None:
                    missing.append(row)
            if not missing:
                return 0
            write_header = not self.path.exists() or self.path.stat().st_size == 0
            with self.path.open("a", encoding="utf-8-sig", newline="") as csv_file:
                writer = csv.DictWriter(csv_file, fieldnames=EXECUTION_CSV_FIELDS)
                if write_header:
                    writer.writeheader()
                writer.writerows(missing)
                csv_file.flush()
                os.fsync(csv_file.fileno())
            os.chmod(self.path, 0o600)
            return len(missing)

    def _existing_rows_unlocked(self) -> dict[str, dict[str, str]]:
        if not self.path.exists() or self.path.stat().st_size == 0:
            return {}
        try:
            with self.path.open("r", encoding="utf-8-sig", newline="") as csv_file:
                reader = csv.DictReader(csv_file)
                header = tuple(reader.fieldnames or ())
                if header not in {EXECUTION_CSV_FIELDS, EXECUTION_CSV_FIELDS_V1}:
                    raise PublicationError("参数化委托 CSV 表头与协议不一致。")
                raw_rows = [dict(row) for row in reader]
                if header == EXECUTION_CSV_FIELDS_V1:
                    raw_rows = self._upgrade_v1_rows_unlocked(raw_rows)
                rows: dict[str, dict[str, str]] = {}
                for row in raw_rows:
                    event_id = str(row.get("order_event_id") or "")
                    if not event_id:
                        raise PublicationError("参数化委托 CSV 存在缺少幂等键的记录。")
                    if event_id in rows:
                        raise PublicationError("参数化委托 CSV 存在重复幂等记录。")
                    rows[event_id] = dict(row)
                return rows
        except (OSError, csv.Error) as exc:
            raise PublicationError(f"无法读取参数化委托 CSV：{exc}") from exc

    def _upgrade_v1_rows_unlocked(
        self, legacy_rows: list[dict[str, str]]
    ) -> list[dict[str, str]]:
        if self.results_root is None:
            raise PublicationError("旧版参数化委托 CSV 无法在缺少结果目录时升级。")
        expected_ids = {str(row.get("order_event_id") or "") for row in legacy_rows}
        rebuilt: list[dict[str, str]] = []
        seen_runs: set[str] = set()
        for row in legacy_rows:
            run_id = str(row.get("run_id") or "")
            if not run_id or run_id in seen_runs:
                continue
            seen_runs.add(run_id)
            run_dir = self.results_root / "运行结果" / run_id
            try:
                record = load_completed_run(run_dir)
                plan = load_execution_plan(self.results_root, record)
            except (OSError, PublicationError) as exc:
                raise PublicationError("无法从历史结果升级参数化委托 CSV。") from exc
            if plan is None:
                raise PublicationError("旧版参数化委托 CSV 对应的执行计划不存在。")
            rebuilt.extend(execution_plan_rows(plan, record))
        rebuilt_ids = {row["order_event_id"] for row in rebuilt}
        if not expected_ids or rebuilt_ids != expected_ids:
            raise PublicationError("旧版参数化委托 CSV 与历史执行计划不一致。")
        self._rewrite_rows_unlocked(rebuilt)
        return rebuilt

    def _rewrite_rows_unlocked(self, rows: list[dict[str, str]]) -> None:
        temporary = self.path.with_name(self.path.name + ".upgrade.tmp")
        with temporary.open("w", encoding="utf-8-sig", newline="") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=EXECUTION_CSV_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
            csv_file.flush()
            os.fsync(csv_file.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, self.path)
        os.chmod(self.path, 0o600)
