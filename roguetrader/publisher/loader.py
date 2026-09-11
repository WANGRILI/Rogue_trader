"""Load and validate a completed RogueTrader run directory."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from roguetrader.publisher.models import (
    DecisionRecord,
    PublicationError,
    compact_text,
    stable_event_id,
)


MAX_METADATA_BYTES = 2 * 1024 * 1024
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ACTION_RE = re.compile(r"^[A-Z][A-Z0-9_-]{0,31}$")


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise PublicationError(f"缺少结果文件：{path.name}")
    if path.stat().st_size > MAX_METADATA_BYTES:
        raise PublicationError(f"结果文件过大：{path.name}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PublicationError(f"无法读取 {path.name}：{exc}") from exc
    if not isinstance(value, dict):
        raise PublicationError(f"{path.name} 必须是 JSON 对象。")
    return value


def _required_string(payload: dict[str, Any], key: str, label: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise PublicationError(f"{label}缺失或格式无效。")
    return value.strip()


def load_completed_run(run_dir: str | Path) -> DecisionRecord:
    root = Path(run_dir).expanduser().resolve()
    if not root.is_dir():
        raise PublicationError(f"结果目录不存在：{root}")

    index = _load_json(root / "运行索引.json")
    decision = _load_json(root / "最终决策.json")

    ticker = _required_string(decision, "ticker", "标的")
    trade_date = _required_string(decision, "trade_date", "分析日期")
    action = _required_string(decision, "action", "最终动作").upper()
    generated_at = _required_string(decision, "generated_at", "生成时间")
    source_schema_version = _required_string(
        decision, "schema_version", "结果协议版本"
    )
    final_text = _required_string(
        decision, "final_trade_decision_text", "最终决策正文"
    )

    if not DATE_RE.fullmatch(trade_date):
        raise PublicationError("分析日期必须使用 YYYY-MM-DD。")
    if not ACTION_RE.fullmatch(action):
        raise PublicationError("最终动作格式无效。")
    if action == "INCOMPLETE":
        raise PublicationError("分析尚未完整结束，不能发布。")

    for key, expected, label in (
        ("ticker", ticker, "标的"),
        ("trade_date", trade_date, "分析日期"),
        ("action", action, "最终动作"),
    ):
        index_value = index.get(key)
        if not isinstance(index_value, str) or index_value.strip().upper() != expected.upper():
            raise PublicationError(f"运行索引与最终决策的{label}不一致。")

    files = index.get("files")
    if not isinstance(files, dict) or files.get("decision") != "最终决策.json":
        raise PublicationError("运行索引未正确声明最终决策文件。")

    key_reasons = decision.get("key_reasons", [])
    invalidations = decision.get("invalidations", [])
    if not isinstance(key_reasons, list) or not isinstance(invalidations, list):
        raise PublicationError("理由和失效条件必须是数组。")

    run_id = root.name
    event_id = stable_event_id(run_id, decision)
    return DecisionRecord(
        event_id=event_id,
        run_id=run_id,
        source_schema_version=source_schema_version,
        generated_at=generated_at,
        trade_date=trade_date,
        ticker=ticker,
        action=action,
        action_source=str(decision.get("action_source") or ""),
        confidence=decision.get("confidence"),
        risk_level=decision.get("risk_level"),
        time_horizon=decision.get("time_horizon"),
        entry_plan=decision.get("entry_plan"),
        stop_loss=decision.get("stop_loss"),
        take_profit=decision.get("take_profit"),
        key_reasons=tuple(key_reasons),
        invalidations=tuple(invalidations),
        decision_summary=compact_text(final_text),
    )
