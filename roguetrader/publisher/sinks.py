"""Local CSV and message-outbox sinks."""

from __future__ import annotations

import csv
import fcntl
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Protocol

from roguetrader.publisher.models import (
    PUBLICATION_SCHEMA_VERSION,
    DecisionRecord,
    PublicationError,
    render_message,
)


CSV_FIELDS = (
    "event_id",
    "run_id",
    "trade_date",
    "generated_at",
    "ticker",
    "action",
    "action_source",
    "confidence",
    "risk_level",
    "time_horizon",
    "entry_plan",
    "stop_loss",
    "take_profit",
    "key_reasons",
    "invalidations",
    "decision_summary",
    "source_schema_version",
    "publication_schema_version",
)
FORMULA_PREFIXES = ("=", "+", "-", "@")


class PublicationSink(Protocol):
    name: str

    def write(self, record: DecisionRecord) -> None: ...


def _private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)


def serialize_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    else:
        text = str(value)
    if text.startswith(FORMULA_PREFIXES):
        return "'" + text
    return text


def decision_row(record: DecisionRecord) -> dict[str, str]:
    raw = record.to_dict()
    return {field: serialize_cell(raw.get(field)) for field in CSV_FIELDS}


def _required_csv_cell(row: dict[str, str], field: str) -> str:
    value = row.get(field, "")
    if not isinstance(value, str) or not value:
        raise PublicationError(f"本地 CSV 的 {field} 字段缺失。")
    return value


def _csv_list(row: dict[str, str], field: str) -> tuple[Any, ...]:
    raw = row.get(field, "")
    if raw == "":
        return ()
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        raise PublicationError(f"本地 CSV 的 {field} 字段格式无效。") from None
    if not isinstance(value, list):
        raise PublicationError(f"本地 CSV 的 {field} 字段格式无效。")
    return tuple(value)


def decision_record_from_row(row: dict[str, str]) -> DecisionRecord:
    """Rebuild the downstream record from one persisted CSV record."""

    if set(row) != set(CSV_FIELDS):
        raise PublicationError("本地 CSV 记录字段与发布协议不一致。")
    publication_version = _required_csv_cell(row, "publication_schema_version")
    if publication_version != PUBLICATION_SCHEMA_VERSION:
        raise PublicationError("本地 CSV 的发布协议版本不受支持。")
    event_id = _required_csv_cell(row, "event_id")
    if len(event_id) != 64 or any(
        character not in "0123456789abcdef" for character in event_id
    ):
        raise PublicationError("本地 CSV 的 event_id 格式无效。")
    return DecisionRecord(
        event_id=event_id,
        run_id=_required_csv_cell(row, "run_id"),
        source_schema_version=_required_csv_cell(row, "source_schema_version"),
        generated_at=_required_csv_cell(row, "generated_at"),
        trade_date=_required_csv_cell(row, "trade_date"),
        ticker=_required_csv_cell(row, "ticker"),
        action=_required_csv_cell(row, "action"),
        action_source=row.get("action_source") or "",
        confidence=row.get("confidence") or None,
        risk_level=row.get("risk_level") or None,
        time_horizon=row.get("time_horizon") or None,
        entry_plan=row.get("entry_plan") or None,
        stop_loss=row.get("stop_loss") or None,
        take_profit=row.get("take_profit") or None,
        key_reasons=_csv_list(row, "key_reasons"),
        invalidations=_csv_list(row, "invalidations"),
        decision_summary=_required_csv_cell(row, "decision_summary"),
    )


class CsvDecisionSink:
    name = "csv"

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")

    def write(self, record: DecisionRecord) -> None:
        self.persist(record)

    def persist(self, record: DecisionRecord) -> DecisionRecord:
        """Durably append and return the exact logical record stored in CSV."""

        _private_directory(self.path.parent)
        with self.lock_path.open("a+", encoding="utf-8") as lock_file:
            os.chmod(self.lock_path, 0o600)
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            if not self._contains_unlocked(record.event_id):
                is_empty = not self.path.exists() or self.path.stat().st_size == 0
                with self.path.open("a", encoding="utf-8-sig", newline="") as csv_file:
                    writer = csv.DictWriter(csv_file, fieldnames=CSV_FIELDS)
                    if is_empty:
                        writer.writeheader()
                    writer.writerow(decision_row(record))
                    csv_file.flush()
                    os.fsync(csv_file.fileno())
                os.chmod(self.path, 0o600)
            return self._read_unlocked(record.event_id)

    def read(self, event_id: str) -> DecisionRecord:
        _private_directory(self.path.parent)
        with self.lock_path.open("a+", encoding="utf-8") as lock_file:
            os.chmod(self.lock_path, 0o600)
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_SH)
            return self._read_unlocked(event_id)

    def _contains_unlocked(self, event_id: str) -> bool:
        if not self.path.is_file() or self.path.stat().st_size == 0:
            return False
        with self.path.open("r", encoding="utf-8-sig", newline="") as csv_file:
            reader = csv.DictReader(csv_file)
            if tuple(reader.fieldnames or ()) != CSV_FIELDS:
                raise PublicationError("现有 CSV 表头与发布协议不一致。")
            return any(row.get("event_id") == event_id for row in reader)

    def _read_unlocked(self, event_id: str) -> DecisionRecord:
        if not self.path.is_file() or self.path.stat().st_size == 0:
            raise PublicationError("本地 CSV 尚不存在或为空。")
        try:
            with self.path.open("r", encoding="utf-8-sig", newline="") as csv_file:
                reader = csv.DictReader(csv_file)
                if tuple(reader.fieldnames or ()) != CSV_FIELDS:
                    raise PublicationError("现有 CSV 表头与发布协议不一致。")
                matches = [row for row in reader if row.get("event_id") == event_id]
        except OSError as exc:
            raise PublicationError("无法读取本地 CSV。") from exc
        if not matches:
            raise PublicationError("本地 CSV 中找不到对应 event_id。")
        if len(matches) != 1:
            raise PublicationError("本地 CSV 中存在重复 event_id。")
        return decision_record_from_row(matches[0])


class LocalMessageSink:
    name = "local_message"

    def __init__(self, directory: str | Path):
        self.directory = Path(directory).expanduser().resolve()

    def message_path(self, event_id: str) -> Path:
        return self.directory / f"{event_id}.json"

    def write(self, record: DecisionRecord) -> None:
        _private_directory(self.directory)
        target = self.message_path(record.event_id)
        if target.is_file():
            try:
                existing = json.loads(target.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError("现有本地消息包无法读取。") from exc
            if not isinstance(existing, dict) or existing.get("event_id") != record.event_id:
                raise ValueError("现有本地消息包与事件 ID 不一致。")
            return
        payload = render_message(record).to_dict()
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{record.event_id}.", suffix=".tmp", dir=self.directory
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, target)
        finally:
            if temporary.exists():
                temporary.unlink()
