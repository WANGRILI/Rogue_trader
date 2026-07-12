"""Shared output path helpers for RogueTrader."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re


RUN_RESULTS_DIR = "运行结果"
EVAL_RESULTS_DIR = "评估结果"
GRAPH_STATE_DIR = "图状态日志"


@dataclass(frozen=True)
class RunOutputPaths:
    root: Path
    index_path: Path
    report_path: Path
    state_path: Path
    decision_path: Path
    config_path: Path
    log_path: Path
    section_dir: Path


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


def make_run_output_paths(root: str | Path, ticker: str, stamp: str | None = None) -> RunOutputPaths:
    run_root = Path(root) / RUN_RESULTS_DIR / f"{stamp or timestamp()}_{safe_symbol(ticker)}"
    return RunOutputPaths(
        root=run_root,
        index_path=run_root / "运行索引.json",
        report_path=run_root / "报告.md",
        state_path=run_root / "状态.json",
        decision_path=run_root / "最终决策.json",
        config_path=run_root / "运行配置.json",
        log_path=run_root / "终端日志.log",
        section_dir=run_root / "分段报告",
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
