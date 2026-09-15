"""Run-scoped temporal governance for agent data tools."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import date, datetime
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Callable, Iterator


class TemporalDataError(RuntimeError):
    """Raised before a historical run can read present-day data."""


SENSITIVE_MARKERS = ("secret", "token", "password", "api_key", "authorization")


def _safe_value(value: Any, key: str = "") -> Any:
    if any(marker in key.lower() for marker in SENSITIVE_MARKERS):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): _safe_value(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


@dataclass
class DataSession:
    run_dir: Path
    analysis_date: date
    mode: str
    replay_snapshot_dir: Path | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)


_SESSION: ContextVar[DataSession | None] = ContextVar("roguetrader_data_session", default=None)


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
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


def _identity(tool_name: str, arguments: dict[str, Any]) -> str:
    raw = json.dumps({"tool": tool_name, "arguments": _safe_value(arguments)}, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _validate_dates(arguments: dict[str, Any], analysis_date: date) -> None:
    for key, value in arguments.items():
        if key not in {"curr_date", "end_date"} or value in {None, ""}:
            continue
        try:
            supplied = date.fromisoformat(str(value)[:10])
        except ValueError as exc:
            raise TemporalDataError(f"{key} 不是有效日期。") from exc
        if supplied > analysis_date:
            raise TemporalDataError(f"{key} 晚于分析截止日期 {analysis_date.isoformat()}。")


def governed_call(
    tool_name: str,
    arguments: dict[str, Any],
    capability: str,
    supplier: Callable[[], str],
) -> str:
    """Execute or replay a tool under point-in-time rules.

    ``bounded`` sources accept an explicit historical cutoff. ``snapshot_required``
    sources are current-state APIs and may only be replayed from an earlier capture.
    """

    session = _SESSION.get()
    if session is None:
        return supplier()
    if capability not in {"bounded", "snapshot_required"}:
        raise TemporalDataError(f"未知数据时点能力：{capability}")
    safe_arguments = _safe_value(arguments)
    key = _identity(tool_name, arguments)
    _validate_dates(arguments, session.analysis_date)

    if session.mode == "historical_strict" and capability == "snapshot_required":
        if session.replay_snapshot_dir is None:
            raise TemporalDataError(f"历史严格模式禁止实时调用 {tool_name}；缺少数据快照。")
        snapshot_path = session.replay_snapshot_dir / f"{key}.json"
        if not snapshot_path.is_file():
            raise TemporalDataError(f"历史严格模式缺少 {tool_name} 的匹配快照。")
        payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
        if payload.get("analysis_date") != session.analysis_date.isoformat() or payload.get("arguments") != safe_arguments:
            raise TemporalDataError(f"{tool_name} 快照与分析截止日期或调用参数不匹配。")
        result = payload.get("result")
        if not isinstance(result, str):
            raise TemporalDataError(f"{tool_name} 快照内容无效。")
        session.calls.append({"tool": tool_name, "capability": capability, "mode": "replay", "snapshot_key": key, "arguments": safe_arguments, "result_sha256": hashlib.sha256(result.encode()).hexdigest()})
        return result

    result = supplier()
    if not isinstance(result, str):
        result = str(result)
    snapshot_dir = session.run_dir / "数据快照"
    snapshot_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(snapshot_dir, 0o700)
    payload = {
        "schema_version": "1.0",
        "tool": tool_name,
        "analysis_date": session.analysis_date.isoformat(),
        "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "capability": capability,
        "arguments": safe_arguments,
        "result": result,
    }
    snapshot_path = snapshot_dir / f"{key}.json"
    _atomic_json_write(snapshot_path, payload)
    session.calls.append({"tool": tool_name, "capability": capability, "mode": "capture", "snapshot_key": key, "arguments": safe_arguments, "result_sha256": hashlib.sha256(result.encode()).hexdigest()})
    return result


@contextmanager
def data_access_session(
    run_dir: str | Path,
    analysis_date: str,
    *,
    mode: str,
    replay_snapshot_dir: str | Path | None = None,
) -> Iterator[DataSession]:
    if mode not in {"live", "historical_strict"}:
        raise TemporalDataError("data_mode 必须是 live 或 historical_strict。")
    session = DataSession(
        Path(run_dir).resolve(),
        date.fromisoformat(str(analysis_date)),
        mode,
        Path(replay_snapshot_dir).expanduser().resolve() if replay_snapshot_dir else None,
    )
    token = _SESSION.set(session)
    status = "completed"
    try:
        yield session
    except BaseException:
        status = "failed"
        raise
    finally:
        _SESSION.reset(token)
        current_only = sum(call["capability"] == "snapshot_required" for call in session.calls)
        lineage = {
            "schema_version": "1.0",
            "analysis_date": session.analysis_date.isoformat(),
            "data_mode": session.mode,
            "status": status,
            "point_in_time_complete": status == "completed",
            "current_state_source_calls": current_only,
            "tool_call_count": len(session.calls),
            "calls": session.calls,
        }
        _atomic_json_write(session.run_dir / "数据血缘.json", lineage)
