"""Writers for normalized RogueTrader run outputs."""

from __future__ import annotations

from datetime import datetime
import json
from typing import Any

from roguetrader.output_paths import RunOutputPaths


RUN_OUTPUT_SCHEMA_VERSION = "1.0"

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
        f"- 生成时间：`{datetime.now().isoformat(timespec='seconds')}`",
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
        "generated_at": datetime.now().isoformat(timespec="seconds"),
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

    return {
        "schema_version": RUN_OUTPUT_SCHEMA_VERSION,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "ticker": ticker,
        "trade_date": trade_date,
        "action": decision,
        "selected_analysts": selected_analysts or [],
        "files": files,
    }


def write_run_outputs(
    paths: RunOutputPaths,
    ticker: str,
    trade_date: str,
    final_state: dict[str, Any],
    decision: str,
    config: dict[str, Any] | None = None,
    selected_analysts: list[str] | None = None,
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
                "generated_at": datetime.now().isoformat(timespec="seconds"),
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

    paths.index_path.write_text(
        json.dumps(
            run_index_payload(
                paths=paths,
                ticker=ticker,
                trade_date=trade_date,
                decision=decision,
                selected_analysts=selected_analysts,
            ),
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
