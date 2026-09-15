"""Writers for normalized RogueTrader run outputs."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from roguetrader.execution.models import PlanValidationError, build_execution_plan
from roguetrader.output_paths import RunOutputPaths


RUN_OUTPUT_SCHEMA_VERSION = "2.0"

REPORT_SECTION_FILES = {
    "market_report": ("市场分析", "市场分析.md"),
    "sentiment_report": ("社交情绪", "社交情绪.md"),
    "news_report": ("新闻分析", "新闻分析.md"),
    "fundamentals_report": ("基本面分析", "基本面分析.md"),
    "onchain_report": ("链上分析", "链上分析.md"),
    "investment_plan": ("研究决策", "研究决策.md"),
    "trader_investment_plan": ("交易计划", "交易计划.md"),
    "final_trade_decision": ("最终决策", "最终决策.md"),
}


def relative_to_root(path, root) -> str:
    return str(path.relative_to(root))


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, default=str)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _publication_event_id(run_uid: str, decision_payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"run_uid": run_uid, "decision": decision_payload},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def default_publication_role(paths: RunOutputPaths) -> str:
    identity = paths.identity
    if (
        identity is not None
        and identity.runtime_mode == "prod"
        and identity.trigger in {"scheduled", "recovery"}
    ):
        return "eligible"
    return "candidate"


def write_run_manifest(
    paths: RunOutputPaths,
    ticker: str,
    *,
    status: str,
    publication_event_id: str | None = None,
    publication_role: str | None = None,
    error_type: str | None = None,
    execution_plan_status: str | None = None,
    execution_plan_error_type: str | None = None,
    data_mode: str | None = None,
) -> dict[str, Any]:
    existing: dict[str, Any] = {}
    if paths.manifest_path.is_file():
        try:
            loaded = json.loads(paths.manifest_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                existing = loaded
        except (OSError, json.JSONDecodeError):
            existing = {}
    identity = paths.identity
    payload = dict(existing)
    if identity is not None:
        payload.update(identity.to_dict(ticker))
    payload.update(
        {
            "schema_version": RUN_OUTPUT_SCHEMA_VERSION,
            "run_id": paths.root.name,
            "ticker": ticker,
            "status": status,
            "publication_role": publication_role
            or str(existing.get("publication_role") or default_publication_role(paths)),
            "updated_at": _now_iso(),
        }
    )
    payload.setdefault("created_at", _now_iso())
    if data_mode is not None:
        payload["data_mode"] = data_mode
        payload["data_governance_required"] = True
    if publication_event_id:
        payload["publication_event_id"] = publication_event_id
    if execution_plan_status is not None:
        payload["execution_plan_status"] = execution_plan_status
        if execution_plan_error_type:
            payload["execution_plan_error_type"] = execution_plan_error_type
        else:
            payload.pop("execution_plan_error_type", None)
    if status == "completed":
        payload["completed_at"] = _now_iso()
        payload.pop("error_type", None)
    elif error_type:
        payload["error_type"] = error_type
    _atomic_json_write(paths.manifest_path, payload)
    return payload


def state_snapshot(final_state: dict[str, Any]) -> dict[str, Any]:
    return {
        "company_of_interest": final_state.get("company_of_interest"),
        "trade_date": final_state.get("trade_date"),
        "market_report": final_state.get("market_report", ""),
        "sentiment_report": final_state.get("sentiment_report", ""),
        "news_report": final_state.get("news_report", ""),
        "fundamentals_report": final_state.get("fundamentals_report", ""),
        "onchain_report": final_state.get("onchain_report", ""),
        "investment_debate_state": final_state.get("investment_debate_state", {}),
        "trader_investment_plan": final_state.get("trader_investment_plan", ""),
        "risk_debate_state": final_state.get("risk_debate_state", {}),
        "investment_plan": final_state.get("investment_plan", ""),
        "final_trade_decision": final_state.get("final_trade_decision", ""),
    }


def build_markdown_report(ticker: str, trade_date: str, final_state: dict[str, Any], decision: str) -> str:
    parts = [
        f"# RogueTrader 运行报告：{ticker}",
        "",
        f"- 分析日期：`{trade_date}`",
        f"- 生成时间：`{_now_iso()}`",
        f"- 最终动作：`{decision}`",
        "",
    ]

    for key, (title, _) in REPORT_SECTION_FILES.items():
        content = final_state.get(key)
        if content:
            parts.extend([f"## {title}", "", str(content), ""])

    return "\n".join(parts).rstrip() + "\n"


def structured_decision_payload(
    ticker: str,
    trade_date: str,
    decision: str,
    final_decision_text: str,
) -> dict[str, Any]:
    return {
        "schema_version": RUN_OUTPUT_SCHEMA_VERSION,
        "generated_at": _now_iso(),
        "ticker": ticker,
        "trade_date": trade_date,
        "action": decision,
        "action_source": "SignalProcessor",
        "confidence": None,
        "time_horizon": None,
        "risk_level": None,
        "entry_plan": None,
        "stop_loss": None,
        "take_profit": None,
        "key_reasons": [],
        "invalidations": [],
        "final_trade_decision_text": final_decision_text,
    }


def run_index_payload(
    paths: RunOutputPaths,
    ticker: str,
    trade_date: str,
    decision: str,
    selected_analysts: list[str] | None,
    execution_plan_status: str = "disabled",
    execution_plan_error_type: str | None = None,
) -> dict[str, Any]:
    section_files = {
        key: relative_to_root(paths.section_dir / filename, paths.root)
        for key, (_, filename) in REPORT_SECTION_FILES.items()
        if (paths.section_dir / filename).exists()
    }
    files = {
        "report": relative_to_root(paths.report_path, paths.root),
        "state": relative_to_root(paths.state_path, paths.root),
        "decision": relative_to_root(paths.decision_path, paths.root),
        "config": relative_to_root(paths.config_path, paths.root),
        "sections_dir": relative_to_root(paths.section_dir, paths.root),
        "sections": section_files,
    }
    if paths.log_path.exists():
        files["terminal_log"] = relative_to_root(paths.log_path, paths.root)
    if paths.execution_plan_path.exists():
        files["execution_plan"] = relative_to_root(
            paths.execution_plan_path, paths.root
        )

    payload = {
        "schema_version": RUN_OUTPUT_SCHEMA_VERSION,
        "generated_at": _now_iso(),
        "ticker": ticker,
        "trade_date": trade_date,
        "analysis_date": trade_date,
        "action": decision,
        "selected_analysts": selected_analysts or [],
        "files": files,
        "execution_plan": {"status": execution_plan_status},
    }
    if execution_plan_error_type:
        payload["execution_plan"]["error_type"] = execution_plan_error_type
    return payload


def write_run_outputs(
    paths: RunOutputPaths,
    ticker: str,
    trade_date: str,
    final_state: dict[str, Any],
    decision: str,
    config: dict[str, Any] | None = None,
    selected_analysts: list[str] | None = None,
    execution_plan_requested: bool = False,
    execution_plan_draft: dict[str, Any] | None = None,
    execution_plan_error_type: str | None = None,
) -> None:
    paths.root.mkdir(parents=True, exist_ok=True)
    paths.section_dir.mkdir(parents=True, exist_ok=True)

    snapshot = state_snapshot(final_state)

    paths.report_path.write_text(
        build_markdown_report(ticker, trade_date, snapshot, decision),
        encoding="utf-8",
    )
    paths.state_path.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    decision_payload = structured_decision_payload(
        ticker=ticker,
        trade_date=trade_date,
        decision=decision,
        final_decision_text=str(snapshot.get("final_trade_decision", "")),
    )
    paths.decision_path.write_text(
        json.dumps(decision_payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    paths.config_path.write_text(
        json.dumps(
            {
                "generated_at": _now_iso(),
                "ticker": ticker,
                "trade_date": trade_date,
                "selected_analysts": selected_analysts or [],
                "config": config or {},
                "output_dir": str(paths.root),
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )

    for key, (_, filename) in REPORT_SECTION_FILES.items():
        content = snapshot.get(key)
        if content:
            (paths.section_dir / filename).write_text(str(content), encoding="utf-8")

    if decision == "INCOMPLETE":
        if paths.index_path.exists():
            paths.index_path.unlink()
        write_run_manifest(paths, ticker, status="incomplete")
        return

    manifest = write_run_manifest(paths, ticker, status="completed")
    run_uid = str(manifest.get("run_uid") or paths.root.name)
    event_id = _publication_event_id(run_uid, decision_payload)
    plan_status = "disabled"
    plan_error_type = execution_plan_error_type
    if execution_plan_requested:
        plan_status = "failed"
        if execution_plan_draft is not None:
            try:
                execution_plan = build_execution_plan(
                    execution_plan_draft,
                    decision_event_id=event_id,
                    ticker=ticker,
                    analysis_date=trade_date,
                    action=decision,
                )
                _atomic_json_write(paths.execution_plan_path, execution_plan)
                plan_status = "parameterized"
                plan_error_type = None
            except (PlanValidationError, OSError) as exc:
                plan_error_type = type(exc).__name__
    write_run_manifest(
        paths,
        ticker,
        status="completed",
        publication_event_id=event_id,
        execution_plan_status=plan_status,
        execution_plan_error_type=plan_error_type,
    )
    index_payload = run_index_payload(
        paths=paths,
        ticker=ticker,
        trade_date=trade_date,
        decision=decision,
        selected_analysts=selected_analysts,
        execution_plan_status=plan_status,
        execution_plan_error_type=plan_error_type,
    )
    index_payload["publication_event_id"] = event_id
    if paths.identity is not None:
        index_payload.update(paths.identity.to_dict(ticker))
    _atomic_json_write(paths.index_path, index_payload)
