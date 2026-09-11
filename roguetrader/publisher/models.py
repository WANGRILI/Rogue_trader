"""Stable local publication models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import re
from typing import Any


PUBLICATION_SCHEMA_VERSION = "1.0"
SUMMARY_LIMIT = 6000
MESSAGE_SUMMARY_LIMIT = 6000


class PublicationError(ValueError):
    """Raised when a run cannot safely be published."""


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def compact_text(value: str, limit: int = SUMMARY_LIMIT) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(
        re.sub(r"[^\S\n]+", " ", line).strip() for line in text.split("\n")
    )
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def stable_event_id(run_id: str, decision_payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"run_id": run_id, "decision": decision_payload},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


@dataclass(frozen=True)
class DecisionRecord:
    event_id: str
    run_id: str
    source_schema_version: str
    generated_at: str
    trade_date: str
    ticker: str
    action: str
    action_source: str
    confidence: Any
    risk_level: Any
    time_horizon: Any
    entry_plan: Any
    stop_loss: Any
    take_profit: Any
    key_reasons: tuple[Any, ...]
    invalidations: tuple[Any, ...]
    decision_summary: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "publication_schema_version": PUBLICATION_SCHEMA_VERSION,
            "event_id": self.event_id,
            "run_id": self.run_id,
            "source_schema_version": self.source_schema_version,
            "generated_at": self.generated_at,
            "trade_date": self.trade_date,
            "ticker": self.ticker,
            "action": self.action,
            "action_source": self.action_source,
            "confidence": self.confidence,
            "risk_level": self.risk_level,
            "time_horizon": self.time_horizon,
            "entry_plan": self.entry_plan,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "key_reasons": list(self.key_reasons),
            "invalidations": list(self.invalidations),
            "decision_summary": self.decision_summary,
        }


@dataclass(frozen=True)
class PreparedMessage:
    event_id: str
    title: str
    level: str
    text: str
    fields: tuple[tuple[str, str], ...]
    run_id: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": PUBLICATION_SCHEMA_VERSION,
            "event_id": self.event_id,
            "title": self.title,
            "level": self.level,
            "text": self.text,
            "fields": [
                {"label": label, "value": value} for label, value in self.fields
            ],
            "source": {
                "run_id": self.run_id,
                "decision_file": "最终决策.json",
            },
            "created_at": self.created_at,
        }


def render_message(record: DecisionRecord) -> PreparedMessage:
    level = {
        "BUY": "positive",
        "OVERWEIGHT": "positive",
        "SELL": "negative",
        "UNDERWEIGHT": "negative",
        "HOLD": "neutral",
        "NEUTRAL": "neutral",
    }.get(record.action, "info")
    fields = (
        ("分析日期", record.trade_date),
        ("标的", record.ticker),
        ("动作", record.action),
        ("置信度", display_value(record.confidence)),
        ("风险等级", display_value(record.risk_level)),
        ("时间周期", display_value(record.time_horizon)),
    )
    return PreparedMessage(
        event_id=record.event_id,
        title=f"RogueTrader 每日决策 · {record.ticker}",
        level=level,
        text=compact_text(record.decision_summary, MESSAGE_SUMMARY_LIMIT),
        fields=fields,
        run_id=record.run_id,
        created_at=now_iso(),
    )


def display_value(value: Any) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)
