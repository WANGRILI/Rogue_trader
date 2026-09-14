"""Shared output path helpers for RogueTrader."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import os
from pathlib import Path
import re
import uuid


RUN_RESULTS_DIR = "运行结果"
EVAL_RESULTS_DIR = "评估结果"
GRAPH_STATE_DIR = "图状态日志"
RUN_MANIFEST_FILE = "运行清单.json"
RUN_NAMING_SCHEMA_VERSION = "2.0"

RUNTIME_MODES = {"prod", "dev", "legacy"}
RUN_TRIGGERS = {"scheduled", "recovery", "manual", "unknown"}
V2_RUN_NAME_RE = re.compile(
    r"^(?P<started>\d{8}_\d{6})__asof-(?P<analysis>\d{8})__"
    r"(?P<mode>prod|dev|legacy)__(?P<trigger>scheduled|recovery|manual|unknown)-"
    r"a(?P<attempt>\d{2,})__(?P<symbol>.+)$"
)


@dataclass(frozen=True)
class RunIdentity:
    run_uid: str
    analysis_date: str
    started_at: str
    runtime_mode: str
    trigger: str
    attempt: int
    scheduled_for: str | None = None
    parent_run_id: str | None = None
    naming_schema_version: str = RUN_NAMING_SCHEMA_VERSION

    def directory_name(self, ticker: str) -> str:
        started = datetime.fromisoformat(self.started_at).strftime("%Y%m%d_%H%M%S")
        analysis = self.analysis_date.replace("-", "")
        return (
            f"{started}__asof-{analysis}__{self.runtime_mode}__"
            f"{self.trigger}-a{self.attempt:02d}__{safe_symbol(ticker)}"
        )

    def to_dict(self, ticker: str) -> dict[str, object]:
        return {
            "naming_schema_version": self.naming_schema_version,
            "run_uid": self.run_uid,
            "run_id": self.directory_name(ticker),
            "ticker": ticker,
            "analysis_date": self.analysis_date,
            "started_at": self.started_at,
            "runtime_mode": self.runtime_mode,
            "trigger": self.trigger,
            "attempt": self.attempt,
            "scheduled_for": self.scheduled_for,
            "parent_run_id": self.parent_run_id,
        }


@dataclass(frozen=True)
class RunOutputPaths:
    root: Path
    index_path: Path
    report_path: Path
    state_path: Path
    decision_path: Path
    execution_plan_path: Path
    execution_instance_path: Path
    config_path: Path
    log_path: Path
    section_dir: Path
    manifest_path: Path
    identity: RunIdentity | None = None


@dataclass(frozen=True)
class EvaluationOutputPaths:
    root: Path
    report_path: Path
    state_path: Path
    reflection_path: Path


def safe_symbol(symbol: str) -> str:
    """Make a symbol safe enough for cross-platform path components."""
    value = re.sub(r"[^\w\u4e00-\u9fff]+", "_", str(symbol), flags=re.UNICODE)
    value = re.sub(r"_+", "_", value).strip("_")
    return value or "未命名标的"


def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def normalize_runtime_mode(value: str | None = None) -> str:
    raw = str(value or os.getenv("ROGUETRADER_RUNTIME_MODE") or "development").strip().lower()
    aliases = {"production": "prod", "development": "dev"}
    normalized = aliases.get(raw, raw)
    if normalized not in RUNTIME_MODES:
        raise ValueError(f"不支持的运行环境：{raw}")
    return normalized


def normalize_trigger(value: str | None = None) -> str:
    normalized = str(value or "manual").strip().lower()
    if normalized not in RUN_TRIGGERS:
        raise ValueError(f"不支持的运行触发类型：{normalized}")
    return normalized


def validate_analysis_date(value: str) -> str:
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError as exc:
        raise ValueError("分析日期必须使用 YYYY-MM-DD。") from exc


def parse_run_directory_name(name: str) -> dict[str, object] | None:
    match = V2_RUN_NAME_RE.fullmatch(str(name))
    if not match:
        return None
    values = match.groupdict()
    started = datetime.strptime(values["started"], "%Y%m%d_%H%M%S")
    analysis = datetime.strptime(values["analysis"], "%Y%m%d").date()
    return {
        "started_at": started,
        "analysis_date": analysis.isoformat(),
        "runtime_mode": values["mode"],
        "trigger": values["trigger"],
        "attempt": int(values["attempt"]),
        "safe_symbol": values["symbol"],
    }


def next_run_attempt(
    root: str | Path,
    ticker: str,
    analysis_date: str,
    runtime_mode: str,
) -> int:
    run_root = Path(root) / RUN_RESULTS_DIR
    symbol = safe_symbol(ticker)
    maximum = 0
    if run_root.is_dir():
        for candidate in run_root.iterdir():
            parsed = parse_run_directory_name(candidate.name)
            if not parsed:
                continue
            if (
                parsed["analysis_date"] == analysis_date
                and parsed["runtime_mode"] == runtime_mode
                and parsed["safe_symbol"] == symbol
            ):
                maximum = max(maximum, int(parsed["attempt"]))
    return maximum + 1


def make_run_output_paths(
    root: str | Path,
    ticker: str,
    stamp: str | None = None,
    *,
    analysis_date: str | None = None,
    runtime_mode: str | None = None,
    trigger: str | None = None,
    attempt: int | None = None,
    scheduled_for: str | None = None,
    parent_run_id: str | None = None,
) -> RunOutputPaths:
    identity = None
    if analysis_date is None:
        run_root = Path(root) / RUN_RESULTS_DIR / f"{stamp or timestamp()}_{safe_symbol(ticker)}"
    else:
        normalized_date = validate_analysis_date(analysis_date)
        normalized_mode = normalize_runtime_mode(runtime_mode)
        normalized_trigger = normalize_trigger(trigger)
        started = (
            datetime.strptime(stamp, "%Y%m%d_%H%M%S").astimezone()
            if stamp
            else datetime.now().astimezone()
        )
        selected_attempt = attempt or next_run_attempt(
            root, ticker, normalized_date, normalized_mode
        )
        if selected_attempt < 1:
            raise ValueError("运行尝试次数必须大于零。")
        identity = RunIdentity(
            run_uid=uuid.uuid4().hex,
            analysis_date=normalized_date,
            started_at=started.isoformat(timespec="seconds"),
            runtime_mode=normalized_mode,
            trigger=normalized_trigger,
            attempt=selected_attempt,
            scheduled_for=scheduled_for,
            parent_run_id=parent_run_id,
        )
        run_root = Path(root) / RUN_RESULTS_DIR / identity.directory_name(ticker)
    return RunOutputPaths(
        root=run_root,
        index_path=run_root / "运行索引.json",
        report_path=run_root / "报告.md",
        state_path=run_root / "状态.json",
        decision_path=run_root / "最终决策.json",
        execution_plan_path=run_root / "执行计划.json",
        execution_instance_path=run_root / "执行实例.json",
        config_path=run_root / "运行配置.json",
        log_path=run_root / "终端日志.log",
        section_dir=run_root / "分段报告",
        manifest_path=run_root / RUN_MANIFEST_FILE,
        identity=identity,
    )


def make_evaluation_output_paths(root: str | Path, ticker: str, stamp: str | None = None) -> EvaluationOutputPaths:
    eval_root = Path(root) / EVAL_RESULTS_DIR / f"{stamp or timestamp()}_{safe_symbol(ticker)}"
    return EvaluationOutputPaths(
        root=eval_root,
        report_path=eval_root / "评估报告.md",
        state_path=eval_root / "评估结果.json",
        reflection_path=eval_root / "反思候选.json",
    )


def graph_state_log_path(root: str | Path, ticker: str, trade_date: str) -> Path:
    return Path(root) / GRAPH_STATE_DIR / safe_symbol(ticker) / f"完整状态_{trade_date}.json"
