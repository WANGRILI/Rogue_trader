"""Backfill parameterized plans from completed historical decisions."""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import date, datetime
import json
from pathlib import Path
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from roguetrader.execution.models import (
    PlanValidationError,
    build_execution_plan,
    validate_execution_plan,
)
from roguetrader.execution.planner import ExecutionPlanner, PlannerOutputError
from roguetrader.llm_clients.agent_registry import AgentLLMRegistry
from roguetrader.output_paths import parse_run_directory_name
from roguetrader.publisher.execution_csv import ExecutionPlanCsvSink
from roguetrader.publisher.execution_plan_message import load_execution_plan
from roguetrader.publisher.loader import load_completed_run
from roguetrader.publisher.models import DecisionRecord, PublicationError, now_iso
from roguetrader.publisher.service import completed_run_directories
from roguetrader.publisher.sinks import CSV_FIELDS, decision_record_from_row
from roguetrader.run_outputs import _atomic_json_write


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RESULTS_ROOT = PROJECT_ROOT / "my_results"
DEFAULT_ORDER_CSV_NAME = "参数化委托.csv"
MAX_JSON_BYTES = 2 * 1024 * 1024
HISTORICAL_TIMEZONE = ZoneInfo("Asia/Shanghai")


class HistoricalPlanBackfillError(ValueError):
    """Raised when historical plan generation cannot proceed safely."""


@dataclass(frozen=True)
class HistoricalDecision:
    run_dir: Path
    record: DecisionRecord
    decision_text: str
    plan_created_at: str
    existing_plan: dict[str, Any] | None

    @property
    def lane_key(self) -> tuple[str, str]:
        runtime_lane = (
            "development"
            if self.record.runtime_mode.lower() in {"dev", "development"}
            else "official"
        )
        return (runtime_lane, self.record.ticker)


@dataclass(frozen=True)
class BackfillItem:
    run_id: str
    analysis_date: str
    ticker: str
    status: str
    order_rows_added: int = 0
    error_type: str | None = None
    error_detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "analysis_date": self.analysis_date,
            "ticker": self.ticker,
            "status": self.status,
            "order_rows_added": self.order_rows_added,
            "error_type": self.error_type,
            "error_detail": self.error_detail,
        }


def _safe_error_detail(exc: Exception) -> str | None:
    if isinstance(
        exc,
        (HistoricalPlanBackfillError, PlanValidationError, PlannerOutputError),
    ):
        return str(exc)[:500]
    return None


def normalize_results_root(value: str | Path) -> Path:
    root = Path(value).expanduser().resolve()
    return root.parent if root.name == "运行结果" else root


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise HistoricalPlanBackfillError(f"缺少历史结果文件：{path.name}")
    if path.stat().st_size > MAX_JSON_BYTES:
        raise HistoricalPlanBackfillError(f"历史结果文件过大：{path.name}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HistoricalPlanBackfillError(f"无法读取 {path.name}：{exc}") from exc
    if not isinstance(value, dict):
        raise HistoricalPlanBackfillError(f"{path.name} 必须是 JSON 对象。")
    return value


def _timestamp_with_timezone(*values: Any) -> str:
    for value in values:
        if not value:
            continue
        try:
            parsed = datetime.fromisoformat(str(value))
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=HISTORICAL_TIMEZONE)
        return parsed.isoformat(timespec="seconds")
    raise HistoricalPlanBackfillError("历史结果没有可用于执行计划的生成时间。")


def _historical_decision(run_dir: Path, record: DecisionRecord) -> HistoricalDecision:
    decision = _load_json(run_dir / "最终决策.json")
    manifest_path = run_dir / "运行清单.json"
    manifest = _load_json(manifest_path) if manifest_path.is_file() else {}
    decision_text = decision.get("final_trade_decision_text")
    if not isinstance(decision_text, str) or not decision_text.strip():
        raise HistoricalPlanBackfillError("历史最终决策缺少完整决策正文。")
    plan = load_execution_plan(run_dir.parent.parent, record)
    return HistoricalDecision(
        run_dir=run_dir,
        record=record,
        decision_text=decision_text.strip(),
        plan_created_at=_timestamp_with_timezone(
            decision.get("generated_at"),
            manifest.get("completed_at"),
            manifest.get("created_at"),
        ),
        existing_plan=plan,
    )


def _completed_by_event(results_root: Path) -> dict[str, HistoricalDecision]:
    discovered: dict[str, HistoricalDecision] = {}
    for run_dir in completed_run_directories(results_root):
        try:
            record = load_completed_run(run_dir)
            item = _historical_decision(run_dir, record)
        except (PublicationError, HistoricalPlanBackfillError, PlanValidationError):
            continue
        previous = discovered.get(record.event_id)
        if previous is None:
            discovered[record.event_id] = item
            continue
        same_decision = (
            previous.record.ticker == item.record.ticker
            and previous.record.trade_date == item.record.trade_date
            and previous.record.action == item.record.action
            and previous.decision_text == item.decision_text
        )
        if not same_decision:
            raise HistoricalPlanBackfillError(
                f"多个内容不同的结果目录共享 publication_event_id：{record.event_id}"
            )
        previous_v2 = parse_run_directory_name(previous.run_dir.name) is not None
        item_v2 = parse_run_directory_name(item.run_dir.name) is not None
        if previous_v2 == item_v2:
            raise HistoricalPlanBackfillError(
                f"多个同等级结果目录共享 publication_event_id：{record.event_id}"
            )
        if item_v2:
            discovered[record.event_id] = item
    return discovered


def _official_event_ids(
    results_root: Path, official_csv: str | Path | None = None
) -> list[str]:
    csv_path = (
        Path(official_csv).expanduser().resolve()
        if official_csv is not None
        else results_root / "汇总" / "每日决策.csv"
    )
    if not csv_path.is_file():
        raise HistoricalPlanBackfillError(
            "默认只回填每日决策.csv 中的正式结果；未找到该文件。"
            "如需处理开发或候选结果，请显式使用 --all-completed。"
        )
    try:
        with csv_path.open("r", encoding="utf-8-sig", newline="") as csv_file:
            reader = csv.DictReader(csv_file)
            if tuple(reader.fieldnames or ()) != CSV_FIELDS:
                raise HistoricalPlanBackfillError("每日决策.csv 表头与发布协议不一致。")
            records = [decision_record_from_row(dict(row)) for row in reader]
    except (OSError, csv.Error, PublicationError) as exc:
        raise HistoricalPlanBackfillError(f"无法读取正式决策 CSV：{exc}") from exc

    seen_keys: dict[tuple[str, str], str] = {}
    event_ids: list[str] = []
    for record in records:
        key = (record.trade_date, record.ticker)
        previous = seen_keys.get(key)
        if previous is not None and previous != record.event_id:
            raise HistoricalPlanBackfillError(
                f"每日决策.csv 中 {record.trade_date} {record.ticker} 存在多个正式结果。"
            )
        seen_keys[key] = record.event_id
        event_ids.append(record.event_id)
    return event_ids


def _date_filter(value: str | None, label: str) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise HistoricalPlanBackfillError(f"{label} 必须使用 YYYY-MM-DD。") from exc


def discover_historical_decisions(
    results_root: str | Path,
    *,
    tickers: Iterable[str] = (),
    date_from: str | None = None,
    date_to: str | None = None,
    official_only: bool = True,
    official_csv: str | Path | None = None,
) -> tuple[HistoricalDecision, ...]:
    """Return safe, validated historical decisions in chronological lane order."""

    root = normalize_results_root(results_root)
    if not official_only and official_csv is not None:
        raise HistoricalPlanBackfillError(
            "--official-csv 与 --all-completed 不能同时使用。"
        )
    completed = _completed_by_event(root)
    if official_only:
        selected: list[HistoricalDecision] = []
        for event_id in _official_event_ids(root, official_csv):
            item = completed.get(event_id)
            if item is None:
                raise HistoricalPlanBackfillError(
                    f"正式决策 {event_id} 没有可读取的完整结果目录。"
                )
            selected.append(item)
    else:
        selected = list(completed.values())

    selected_tickers = {value.strip().upper() for value in tickers if value.strip()}
    start = _date_filter(date_from, "起始日期")
    end = _date_filter(date_to, "结束日期")
    if start and end and start > end:
        raise HistoricalPlanBackfillError("起始日期不能晚于结束日期。")

    filtered: list[HistoricalDecision] = []
    for item in selected:
        item_date = date.fromisoformat(item.record.trade_date)
        if selected_tickers and item.record.ticker.upper() not in selected_tickers:
            continue
        if start and item_date < start:
            continue
        if end and item_date > end:
            continue
        filtered.append(item)
    return tuple(
        sorted(
            filtered,
            key=lambda item: (
                item.lane_key[0],
                item.record.ticker,
                item.record.trade_date,
                item.plan_created_at,
                item.run_dir.name,
            ),
        )
    )


def initial_previous_plans(
    all_candidates: Iterable[HistoricalDecision],
    selected_candidates: Iterable[HistoricalDecision],
) -> dict[tuple[str, str], dict[str, Any]]:
    """Seed a filtered backfill with each lane's immediate prior official plan."""

    all_items = tuple(all_candidates)
    selected = tuple(selected_candidates)
    first_by_lane: dict[tuple[str, str], HistoricalDecision] = {}
    for item in selected:
        first_by_lane.setdefault(item.lane_key, item)

    seeded: dict[tuple[str, str], dict[str, Any]] = {}
    for lane, first in first_by_lane.items():
        lane_items = [item for item in all_items if item.lane_key == lane]
        try:
            index = next(
                position
                for position, item in enumerate(lane_items)
                if item.record.event_id == first.record.event_id
            )
        except StopIteration as exc:
            raise HistoricalPlanBackfillError(
                "筛选后的历史决策不在完整候选集中。"
            ) from exc
        if index == 0:
            continue
        previous = lane_items[index - 1]
        if previous.existing_plan is None:
            raise HistoricalPlanBackfillError(
                f"{first.record.trade_date} {first.record.ticker} 的前序计划尚未生成；"
                "请从更早的缺口开始回填。"
            )
        seeded[lane] = validate_execution_plan(previous.existing_plan)
    return seeded


def _update_plan_metadata(
    item: HistoricalDecision,
    plan: dict[str, Any],
    *,
    origin: str | None,
) -> None:
    normalized = validate_execution_plan(plan)
    if normalized["decision_event_id"] != item.record.event_id:
        raise HistoricalPlanBackfillError("执行计划与历史决策事件不一致。")
    index_path = item.run_dir / "运行索引.json"
    index = _load_json(index_path)
    files = index.get("files")
    if not isinstance(files, dict):
        raise HistoricalPlanBackfillError("运行索引缺少 files 对象。")
    manifest_path = item.run_dir / "运行清单.json"
    manifest = _load_json(manifest_path) if manifest_path.is_file() else None

    plan_path = item.run_dir / "执行计划.json"
    if plan_path.is_file():
        existing = validate_execution_plan(_load_json(plan_path))
        if existing["plan_id"] != normalized["plan_id"]:
            raise HistoricalPlanBackfillError("历史目录已经存在另一份执行计划。")
    else:
        _atomic_json_write(plan_path, normalized)

    backfilled_at = now_iso()
    files["execution_plan"] = "执行计划.json"
    current_status = index.get("execution_plan")
    plan_status: dict[str, Any] = (
        dict(current_status) if isinstance(current_status, dict) else {}
    )
    plan_status["status"] = "parameterized"
    plan_status.pop("error_type", None)
    if origin:
        plan_status.update({"origin": origin, "backfilled_at": backfilled_at})
    index["execution_plan"] = plan_status
    _atomic_json_write(index_path, index)

    if manifest is not None:
        manifest["execution_plan_status"] = "parameterized"
        manifest.pop("execution_plan_error_type", None)
        if origin:
            manifest["execution_plan_origin"] = origin
            manifest["execution_plan_backfilled_at"] = backfilled_at
        _atomic_json_write(manifest_path, manifest)


def backfill_historical_execution_plans(
    candidates: Iterable[HistoricalDecision],
    *,
    planner: ExecutionPlanner | None,
    order_csv_sink: ExecutionPlanCsvSink,
    apply: bool,
    max_plans: int | None = None,
    initial_previous_by_lane: Mapping[tuple[str, str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Generate missing plans in order and synchronize the order-detail CSV."""

    items = tuple(candidates)
    if apply and (max_plans is None or max_plans < 1):
        raise HistoricalPlanBackfillError(
            "实际回填必须通过 --max-plans 明确本次最多调用的快速模型次数。"
        )
    if apply and any(item.existing_plan is None for item in items) and planner is None:
        raise HistoricalPlanBackfillError("实际回填缺少执行规划器。")

    previous_by_lane = {
        lane: validate_execution_plan(plan)
        for lane, plan in (initial_previous_by_lane or {}).items()
    }
    blocked_lanes: set[tuple[str, str]] = set()
    llm_calls = 0
    reports: list[BackfillItem] = []
    for item in items:
        lane = item.lane_key
        plan = item.existing_plan
        if plan is not None:
            expected_previous = previous_by_lane.get(lane)
            expected_previous_id = (
                expected_previous["plan_id"] if expected_previous is not None else None
            )
            if plan.get("previous_plan_id") != expected_previous_id:
                raise HistoricalPlanBackfillError(
                    f"{item.record.trade_date} {item.record.ticker} 的执行计划谱系不连续。"
                )
            rows_added = 0
            status = "existing"
            if apply:
                _update_plan_metadata(item, plan, origin=None)
                rows_added = order_csv_sink.persist(plan, item.record)
                status = "synced" if rows_added else "existing"
            previous_by_lane[lane] = plan
            reports.append(
                BackfillItem(
                    item.record.run_id,
                    item.record.trade_date,
                    item.record.ticker,
                    status,
                    rows_added,
                )
            )
            continue

        if not apply:
            reports.append(
                BackfillItem(
                    item.record.run_id,
                    item.record.trade_date,
                    item.record.ticker,
                    "would_generate",
                )
            )
            continue
        if lane in blocked_lanes:
            reports.append(
                BackfillItem(
                    item.record.run_id,
                    item.record.trade_date,
                    item.record.ticker,
                    "blocked_by_previous_error",
                )
            )
            continue
        if llm_calls >= int(max_plans or 0):
            reports.append(
                BackfillItem(
                    item.record.run_id,
                    item.record.trade_date,
                    item.record.ticker,
                    "limit_reached",
                )
            )
            continue

        try:
            llm_calls += 1
            draft = planner.create_draft(
                ticker=item.record.ticker,
                analysis_date=item.record.trade_date,
                action=item.record.action,
                final_decision_text=item.decision_text,
                previous_plan=previous_by_lane.get(lane),
            )
            plan = build_execution_plan(
                draft,
                decision_event_id=item.record.event_id,
                ticker=item.record.ticker,
                analysis_date=item.record.trade_date,
                action=item.record.action,
                created_at=item.plan_created_at,
            )
            _update_plan_metadata(item, plan, origin="historical_backfill")
            rows_added = order_csv_sink.persist(plan, item.record)
        except Exception as exc:
            blocked_lanes.add(lane)
            reports.append(
                BackfillItem(
                    item.record.run_id,
                    item.record.trade_date,
                    item.record.ticker,
                    "error",
                    error_type=type(exc).__name__,
                    error_detail=_safe_error_detail(exc),
                )
            )
            continue
        previous_by_lane[lane] = plan
        reports.append(
            BackfillItem(
                item.record.run_id,
                item.record.trade_date,
                item.record.ticker,
                "generated",
                rows_added,
            )
        )

    return {
        "mode": "apply" if apply else "dry_run",
        "selected": len(items),
        "llm_calls": llm_calls,
        "generated": sum(item.status == "generated" for item in reports),
        "existing": sum(item.status == "existing" for item in reports),
        "synced": sum(item.status == "synced" for item in reports),
        "pending_generation": sum(
            item.status in {"would_generate", "limit_reached"} for item in reports
        ),
        "failed": sum(
            item.status in {"error", "blocked_by_previous_error"} for item in reports
        ),
        "order_rows_added": sum(item.order_rows_added for item in reports),
        "items": [item.to_dict() for item in reports],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "从已完成历史决策补生成参数化执行计划，并汇总到参数化委托.csv。"
            "默认只预演，不调用模型、不写文件。"
        )
    )
    parser.add_argument("--results-root", type=Path, default=DEFAULT_RESULTS_ROOT)
    parser.add_argument(
        "--official-csv",
        type=Path,
        help="可选的只读正式决策主表；计划仍写入 --results-root。",
    )
    parser.add_argument("--ticker", action="append", default=[])
    parser.add_argument("--date-from")
    parser.add_argument("--date-to")
    parser.add_argument(
        "--all-completed",
        action="store_true",
        help="处理全部完整结果，包括开发和手动候选；默认只认每日决策.csv。",
    )
    parser.add_argument(
        "--apply", action="store_true", help="实际调用快速模型并写入计划和 CSV。"
    )
    parser.add_argument(
        "--max-plans",
        type=int,
        help="本次最多生成多少份新计划；--apply 时必须显式指定。",
    )
    parser.add_argument("--provider")
    parser.add_argument("--quick-model")
    parser.add_argument("--backend-url")
    parser.add_argument("--request-timeout", type=int, default=60)
    parser.add_argument("--max-retries", type=int, default=1)
    parser.add_argument("--order-csv", type=Path)
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="只输出汇总和首个错误，不打印逐条历史记录。",
    )
    return parser


def _make_planner(args: argparse.Namespace) -> ExecutionPlanner:
    # Imported after main() loads this worktree's .env so model/provider overrides
    # are evaluated in the intended runtime rather than at module import time.
    from roguetrader.default_config import DEFAULT_CONFIG

    config = DEFAULT_CONFIG.copy()
    if args.provider:
        config["llm_provider"] = args.provider
    if args.quick_model:
        config["quick_think_llm"] = args.quick_model
    if args.backend_url:
        config["backend_url"] = args.backend_url
    config["agent_config_path"] = str(PROJECT_ROOT / "configs" / "agents.yaml")
    if not 10 <= args.request_timeout <= 300:
        raise HistoricalPlanBackfillError("--request-timeout 必须在 10-300 秒之间。")
    if not 0 <= args.max_retries <= 5:
        raise HistoricalPlanBackfillError("--max-retries 必须在 0-5 之间。")
    registry = AgentLLMRegistry(
        config,
        llm_kwargs={
            "timeout": args.request_timeout,
            "max_retries": args.max_retries,
        },
    )
    return ExecutionPlanner(
        registry.get_llm("execution_planner", "quick"), registry
    )


def main() -> None:
    load_dotenv(PROJECT_ROOT / ".env")
    args = build_parser().parse_args()
    try:
        root = normalize_results_root(args.results_root)
        all_candidates = discover_historical_decisions(
            root,
            tickers=args.ticker,
            official_only=not args.all_completed,
            official_csv=args.official_csv,
        )
        candidates = discover_historical_decisions(
            root,
            tickers=args.ticker,
            date_from=args.date_from,
            date_to=args.date_to,
            official_only=not args.all_completed,
            official_csv=args.official_csv,
        )
        previous_plans = initial_previous_plans(all_candidates, candidates)
        missing = any(item.existing_plan is None for item in candidates)
        planner = _make_planner(args) if args.apply and missing else None
        order_csv = (
            args.order_csv.expanduser().resolve()
            if args.order_csv
            else root / "汇总" / DEFAULT_ORDER_CSV_NAME
        )
        report = backfill_historical_execution_plans(
            candidates,
            planner=planner,
            order_csv_sink=ExecutionPlanCsvSink(order_csv, root),
            apply=args.apply,
            max_plans=args.max_plans,
            initial_previous_by_lane=previous_plans,
        )
    except HistoricalPlanBackfillError as exc:
        raise SystemExit(f"historical execution-plan backfill error: {exc}") from None
    report["order_csv"] = str(order_csv)
    if args.summary_only:
        items = report.pop("items")
        report["first_error"] = next(
            (item for item in items if item["status"] == "error"), None
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
