"""Point-in-time eligibility and deterministic research-plan projection.

Publication artifacts are an audit log.  Research inputs are a stricter view over
that log: delayed repairs without an immutable source snapshot are excluded, while
the original files remain untouched.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timedelta
import json
import math
import os
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from roguetrader.execution.models import build_execution_plan, validate_execution_plan
from roguetrader.output_paths import parse_run_directory_name
from roguetrader.publisher.execution_csv import EXECUTION_CSV_FIELDS, execution_plan_rows
from roguetrader.publisher.models import DecisionRecord


RESEARCH_POLICY_VERSION = "1.0"
MAX_UNSNAPSHOTTED_REPAIR_DELAY = timedelta(hours=6)
SHANGHAI = ZoneInfo("Asia/Shanghai")
ELIGIBILITY_FIELDS = (
    "event_id",
    "run_id",
    "analysis_date",
    "ticker",
    "action",
    "eligible",
    "reason_code",
    "reason",
    "generated_at",
    "run_started_at",
    "source_snapshot_status",
    "policy_version",
)


class ResearchIntegrityError(ValueError):
    """Raised when a research projection cannot be built safely."""


@dataclass(frozen=True)
class ResearchEligibility:
    eligible: bool
    reason_code: str
    reason: str
    generated_at: str
    run_started_at: str | None
    source_snapshot_status: str

    def row(self, record: DecisionRecord) -> dict[str, str]:
        return {
            "event_id": record.event_id,
            "run_id": record.run_id,
            "analysis_date": record.trade_date,
            "ticker": record.ticker,
            "action": record.action,
            "eligible": "true" if self.eligible else "false",
            "reason_code": self.reason_code,
            "reason": self.reason,
            "generated_at": self.generated_at,
            "run_started_at": self.run_started_at or "",
            "source_snapshot_status": self.source_snapshot_status,
            "policy_version": RESEARCH_POLICY_VERSION,
        }


def _aware(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed.replace(tzinfo=SHANGHAI) if parsed.tzinfo is None else parsed


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ResearchIntegrityError(f"无法读取研究元数据 {path.name}：{exc}") from exc
    if not isinstance(value, dict):
        raise ResearchIntegrityError(f"研究元数据 {path.name} 必须是对象。")
    return value


def _run_started(record: DecisionRecord, manifest: dict[str, Any]) -> datetime | None:
    started = _aware(manifest.get("started_at"))
    if started is not None:
        return started
    parsed = parse_run_directory_name(record.run_id)
    if parsed is None:
        return None
    value = parsed.get("started_at")
    return value.replace(tzinfo=SHANGHAI) if isinstance(value, datetime) else None


def assess_research_eligibility(
    record: DecisionRecord,
    run_dir: str | Path,
) -> ResearchEligibility:
    """Apply the hard point-in-time gate used by all historical research."""

    root = Path(run_dir).expanduser().resolve()
    manifest = _load_json(root / "运行清单.json")
    explicit = manifest.get("research_eligibility")
    if explicit is not None and not isinstance(explicit, dict):
        raise ResearchIntegrityError("research_eligibility 必须是对象。")

    generated = _aware(record.generated_at)
    started = _run_started(record, manifest)
    generated_text = generated.isoformat(timespec="seconds") if generated else record.generated_at
    started_text = started.isoformat(timespec="seconds") if started else None
    lineage = _load_json(root / "数据血缘.json")
    snapshot_ok = bool(lineage.get("point_in_time_complete"))
    snapshot_status = "verified" if snapshot_ok else "not_available"

    if explicit and str(explicit.get("status", "")).lower() in {"excluded", "quarantined"}:
        return ResearchEligibility(
            False,
            str(explicit.get("reason_code") or "explicit_quarantine"),
            str(explicit.get("reason") or "该结果已被显式隔离。"),
            generated_text,
            started_text,
            snapshot_status,
        )
    if manifest.get("data_governance_required") is True and not snapshot_ok:
        return ResearchEligibility(
            False,
            "required_data_lineage_missing",
            "该版本要求数据血缘，但运行目录缺少完整快照清单。",
            generated_text,
            started_text,
            snapshot_status,
        )
    if generated is None:
        return ResearchEligibility(False, "invalid_generated_at", "决策生成时间无效。", generated_text, started_text, snapshot_status)
    if started is not None and generated < started:
        return ResearchEligibility(False, "generated_before_run", "决策生成时间早于任务开始时间。", generated_text, started_text, snapshot_status)
    repair_like = (
        record.runtime_mode.lower() == "legacy"
        or record.trigger.lower() in {"unknown", "recovery"}
    )
    if (
        repair_like
        and started is not None
        and generated - started > MAX_UNSNAPSHOTTED_REPAIR_DELAY
        and not snapshot_ok
    ):
        return ResearchEligibility(
            False,
            "delayed_repair_without_snapshot",
            "历史修复结果晚于原任务超过 6 小时，且缺少当时数据快照。",
            generated_text,
            started_text,
            snapshot_status,
        )
    return ResearchEligibility(
        True,
        "point_in_time_eligible" if snapshot_ok else "legacy_same_run_time",
        "数据快照已验证。" if snapshot_ok else "生成时间与任务时间一致，按历史兼容规则纳入。",
        generated_text,
        started_text,
        snapshot_status,
    )


def _plan_draft(
    plan: dict[str, Any],
    previous_plan_id: str | None,
    *,
    reconnected: bool,
    valid_for_hours: int | None = None,
) -> dict[str, Any]:
    draft = {
        "plan_summary": plan["plan_summary"],
        "valid_for_hours": valid_for_hours or plan["valid_for_hours"],
        "risk_limits": plan["risk_limits"],
        "scenarios": plan["scenarios"],
        "previous_plan_id": previous_plan_id,
        "continuity_action": plan.get("continuity_action", "replace") if previous_plan_id else "baseline",
        "change_summary": plan.get("change_summary", "延续上一份参数化计划。"),
    }
    if reconnected:
        draft["change_summary"] = (
            "研究链重接：中间决策因缺少时点安全证据被视为当日无新交易；"
            "本计划直接承接最近一份合格计划。"
        )
    if previous_plan_id is None:
        draft["change_summary"] = "研究窗口内首份合格参数化执行计划。"
    return draft


def build_research_projection(
    decisions: Iterable[Any],
    *,
    output_root: str | Path,
) -> dict[str, Any]:
    """Build an immutable research-only ledger without mutating source plans."""

    root = Path(output_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    items = sorted(decisions, key=lambda item: (item.record.ticker, item.record.trade_date, item.record.generated_at, item.record.event_id))
    evaluated = [(item, assess_research_eligibility(item.record, item.run_dir)) for item in items]
    eligibility_rows: list[dict[str, str]] = []
    plan_chain: list[dict[str, Any]] = []
    order_rows: list[dict[str, str]] = []
    previous_by_ticker: dict[str, dict[str, Any]] = {}
    excluded_since_previous: dict[str, bool] = {}

    for item_index, (item, eligibility) in enumerate(evaluated):
        eligibility_rows.append(eligibility.row(item.record))
        ticker = item.record.ticker
        if not eligibility.eligible:
            excluded_since_previous[ticker] = True
            plan_chain.append({
                "event_id": item.record.event_id,
                "run_id": item.record.run_id,
                "analysis_date": item.record.trade_date,
                "status": "excluded",
                "reason_code": eligibility.reason_code,
                "source_plan_id": item.existing_plan.get("plan_id") if item.existing_plan else None,
            })
            continue
        if item.existing_plan is None:
            raise ResearchIntegrityError(f"合格决策缺少执行计划：{item.record.run_id}")
        source_plan = validate_execution_plan(item.existing_plan)
        previous = previous_by_ticker.get(ticker)
        previous_id = previous["plan_id"] if previous else None
        source_previous = source_plan.get("previous_plan_id")
        excluded_gap = bool(excluded_since_previous.get(ticker))
        reconnect = excluded_gap or source_previous != previous_id
        validity_extension: int | None = None
        saw_excluded = False
        for future_item, future_eligibility in evaluated[item_index + 1:]:
            if future_item.record.ticker != ticker:
                break
            if not future_eligibility.eligible:
                saw_excluded = True
                continue
            if saw_excluded:
                created = _aware(source_plan["created_at"])
                next_generated = _aware(future_item.record.generated_at)
                if created is not None and next_generated is not None:
                    required_hours = math.ceil((next_generated - created).total_seconds() / 3600)
                    validity_extension = min(168, max(source_plan["valid_for_hours"], required_hours))
            break
        extends_validity = validity_extension is not None and validity_extension > source_plan["valid_for_hours"]
        if not reconnect and not extends_validity:
            projected = source_plan
        else:
            projected = build_execution_plan(
                _plan_draft(
                    source_plan,
                    previous_id,
                    reconnected=excluded_gap,
                    valid_for_hours=validity_extension,
                ),
                decision_event_id=item.record.event_id,
                ticker=ticker,
                analysis_date=item.record.trade_date,
                action=item.record.action,
                created_at=source_plan["created_at"],
            )
        order_rows.extend(execution_plan_rows(projected, item.record))
        plan_chain.append({
            "event_id": item.record.event_id,
            "run_id": item.record.run_id,
            "analysis_date": item.record.trade_date,
            "status": "included",
            "source_plan_id": source_plan["plan_id"],
            "research_plan_id": projected["plan_id"],
            "previous_research_plan_id": previous_id,
            "lineage_reconnected": reconnect,
            "validity_extended_due_to_excluded_gap": extends_validity,
            "source_valid_for_hours": source_plan["valid_for_hours"],
            "research_valid_for_hours": projected["valid_for_hours"],
        })
        previous_by_ticker[ticker] = projected
        excluded_since_previous[ticker] = False

    eligibility_path = root / "研究资格.csv"
    with eligibility_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=ELIGIBILITY_FIELDS)
        writer.writeheader()
        writer.writerows(eligibility_rows)
    ledger_path = root / "参数化委托.csv"
    with ledger_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=EXECUTION_CSV_FIELDS)
        writer.writeheader()
        writer.writerows(order_rows)
    manifest = {
        "schema_version": RESEARCH_POLICY_VERSION,
        "policy": "point_in_time_strict_with_legacy_same_run_compatibility",
        "source_artifacts_mutated": False,
        "eligible_decisions": sum(row["eligible"] == "true" for row in eligibility_rows),
        "excluded_decisions": sum(row["eligible"] == "false" for row in eligibility_rows),
        "order_rows": len(order_rows),
        "files": {"eligibility": eligibility_path.name, "ledger": ledger_path.name},
        "plan_chain": plan_chain,
    }
    (root / "研究投影.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for path in (eligibility_path, ledger_path, root / "研究投影.json"):
        os.chmod(path, 0o600)
    return {"output": str(root), **{key: manifest[key] for key in ("eligible_decisions", "excluded_decisions", "order_rows")}}
