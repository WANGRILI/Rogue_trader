#!/usr/bin/env python3
"""Run RogueTrader with a local processed Parquet data summary."""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from local_data_summary import DEFAULT_DATA_ROOT, build_summary, parquet_path  # noqa: E402
from roguetrader.default_config import DEFAULT_CONFIG  # noqa: E402
from roguetrader.graph.trading_graph import RogueTraderGraph  # noqa: E402
from roguetrader.output_paths import make_run_output_paths  # noqa: E402


DEFAULT_CATALOG_PATH = Path(
    os.getenv(
        "ROGUETRADER_LOCAL_CATALOG",
        str(PROJECT_ROOT / "data" / "processed" / "catalog" / "local_data_catalog.json"),
    )
)
DEFAULT_RAW_ROOT = Path(os.getenv("ROGUETRADER_LOCAL_RAW_ROOT", str(PROJECT_ROOT / "data" / "raw")))
DEFAULT_ARCHIVE_RAW_ROOT = Path(
    os.getenv("ROGUETRADER_LOCAL_ARCHIVE_RAW_ROOT", str(PROJECT_ROOT / "data" / "raw_archive"))
)
DEFAULT_PROCESSED_ROOT = Path(
    os.getenv("ROGUETRADER_LOCAL_PROCESSED_ROOT", str(PROJECT_ROOT / "data" / "processed"))
)


def parse_analysts(value: str) -> list[str]:
    analysts = [item.strip() for item in value.split(",") if item.strip()]
    return analysts or ["onchain"]


def load_catalog_entry(catalog_path: str | Path, runtime_path: Path) -> dict | None:
    path = Path(catalog_path)
    if not path.exists():
        return None

    with path.open("r", encoding="utf-8") as f:
        catalog = json.load(f)

    runtime = str(runtime_path)
    for dataset in catalog.get("datasets", []):
        if dataset.get("processed_path") == runtime:
            return dataset
    return None


def safe_json_state(final_state: dict) -> dict:
    keys = [
        "company_of_interest",
        "trade_date",
        "market_report",
        "sentiment_report",
        "news_report",
        "fundamentals_report",
        "onchain_report",
        "trader_investment_plan",
        "investment_plan",
        "final_trade_decision",
    ]
    return {key: final_state.get(key) for key in keys if key in final_state}


def normalize_action(decision: str | None, final_state: dict | None) -> str | None:
    text_parts = [str(decision or "")]
    if final_state:
        text_parts.extend(
            str(final_state.get(key, ""))
            for key in ["final_trade_decision", "investment_plan", "trader_investment_plan"]
        )
    text = "\n".join(text_parts).upper()
    for action in ["UNDERWEIGHT", "OVERWEIGHT", "SELL", "BUY", "HOLD"]:
        if action in text:
            return action
    return None


def extract_signal_points(final_state: dict | None) -> tuple[list[str], list[str]]:
    if not final_state:
        return [], []

    text = "\n".join(
        str(final_state.get(key, ""))
        for key in ["onchain_report", "investment_plan", "trader_investment_plan", "final_trade_decision"]
    )
    reasons = []
    invalidations = []
    for raw_line in text.splitlines():
        line = raw_line.strip(" -•\t")
        if len(line) < 20:
            continue
        lower = line.lower()
        if len(reasons) < 8 and any(word in lower for word in ["because", "bullish", "bearish", "signal", "trend", "risk", "support", "resistance", "volume", "liquidity"]):
            reasons.append(line[:500])
        if len(invalidations) < 5 and any(word in lower for word in ["if ", "unless", "invalid", "risk", "drawdown", "break below", "break above", "stop"]):
            invalidations.append(line[:500])
    return reasons, invalidations


def build_structured_signal(
    args,
    runtime_path: Path,
    catalog_entry: dict | None,
    final_state: dict | None,
    decision: str | None,
) -> dict:
    key_reasons, invalidations = extract_signal_points(final_state)
    return {
        "ticker": args.ticker,
        "analysis_date": args.date,
        "action": normalize_action(decision, final_state),
        "confidence": args.confidence,
        "time_horizon": args.time_horizon,
        "risk_level": args.risk_level,
        "source": args.source,
        "timeframe": args.timeframe,
        "data_source": {
            "runtime_path": str(runtime_path),
            "upstream_raw_source": catalog_entry.get("source_path") if catalog_entry else None,
            "catalog_path": str(args.catalog_path),
        },
        "key_reasons": key_reasons,
        "stop_loss": None,
        "take_profit": None,
        "invalidations": invalidations,
    }


def write_report(
    report_path: Path,
    args,
    local_summary: str,
    catalog_entry: dict | None,
    runtime_path: Path,
    final_state: dict | None,
    decision: str | None,
    structured_signal: dict,
):
    report_path.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        f"# RogueTrader Local Data Report — {args.ticker} {args.date}",
        "",
        f"Generated at: {datetime.now().isoformat(timespec='seconds')}",
        "",
        "## Run Parameters",
        "",
        f"- Ticker: `{args.ticker}`",
        f"- Date: `{args.date}`",
        f"- Source: `{args.source}`",
        f"- Timeframe: `{args.timeframe}`",
        f"- Days: `{args.days}`",
        f"- Analysts: `{args.analysts}`",
        f"- Output language: `{args.output_language}`",
        f"- Runtime parquet: `{runtime_path}`",
        f"- Catalog path: `{args.catalog_path}`",
        "",
        "## Data Source Policy",
        "",
        "- `raw2` is the preferred upstream source of truth.",
        "- RogueTrader runtime reads `processed/parquet`, not raw2 directly.",
        "",
    ]

    if catalog_entry:
        lines.extend([
            "## Catalog Entry",
            "",
            "```json",
            json.dumps(catalog_entry, ensure_ascii=False, indent=2),
            "```",
            "",
        ])
    else:
        lines.extend([
            "## Catalog Entry",
            "",
            "No matching catalog entry found for the runtime parquet path.",
            "",
        ])

    lines.extend([
        "## Local Data Summary",
        "",
        local_summary,
        "",
        "## Structured Signal",
        "",
        "```json",
        json.dumps(structured_signal, ensure_ascii=False, indent=2),
        "```",
        "",
        "## RogueTrader Decision",
        "",
        str(decision) if decision is not None else "RogueTrader execution was skipped.",
        "",
    ])

    if final_state:
        lines.extend([
            "## Final Trade Decision Text",
            "",
            str(final_state.get("final_trade_decision", "")),
            "",
        ])

    report_path.write_text("\n".join(lines), encoding="utf-8")


def write_state(
    state_path: Path,
    catalog_entry: dict | None,
    final_state: dict | None,
    structured_signal: dict,
):
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        **structured_signal,
        "catalog_entry": catalog_entry,
        "final_state": safe_json_state(final_state or {}),
    }
    state_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def add_local_files_to_index(output_paths, report_path: Path, state_path: Path):
    index_path = output_paths.index_path
    if not index_path.exists():
        return

    index = json.loads(index_path.read_text(encoding="utf-8"))
    files = index.setdefault("files", {})
    files["local_data_report"] = str(report_path.relative_to(output_paths.root))
    files["local_signal"] = str(state_path.relative_to(output_paths.root))
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Run RogueTrader with local processed parquet context")
    parser.add_argument("--ticker", default="BTC-USD")
    parser.add_argument("--date", default="2014-12-31")
    parser.add_argument("--source", default="manual_or_investing")
    parser.add_argument("--timeframe", default="1d")
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--output-language", default="Chinese")
    parser.add_argument("--data-root", default=str(DEFAULT_DATA_ROOT))
    parser.add_argument("--catalog-path", default=str(DEFAULT_CATALOG_PATH))
    parser.add_argument("--analysts", default="onchain")
    parser.add_argument("--max-debate-rounds", type=int, default=1)
    parser.add_argument("--max-recur-limit", type=int, default=50)
    parser.add_argument("--time-horizon", default="30d", help="Structured signal horizon, e.g. 7d, 30d")
    parser.add_argument("--confidence", type=float, help="Optional manual confidence for structured signal")
    parser.add_argument("--risk-level", help="Optional manual risk level for structured signal")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--skip-roguetrader", action="store_true", help="Only build local summary/report, skip LLM run")
    args = parser.parse_args()

    load_dotenv(PROJECT_ROOT / ".env")

    runtime_path = parquet_path(args.data_root, args.ticker, args.source, args.timeframe)
    local_summary = build_summary(
        ticker=args.ticker,
        source=args.source,
        timeframe=args.timeframe,
        days=args.days,
        data_root=args.data_root,
        reference_date=args.date,
    )
    catalog_entry = load_catalog_entry(args.catalog_path, runtime_path)

    print("=" * 80)
    print("LOCAL DATA SUMMARY")
    print("=" * 80)
    print(local_summary)
    print()

    final_state = None
    decision = None
    graph_output_paths = None

    if args.skip_roguetrader:
        print("Skipping RogueTrader execution (--skip-roguetrader).")
    else:
        config = DEFAULT_CONFIG.copy()
        config["llm_provider"] = "deepseek"
        config["backend_url"] = "https://api.deepseek.com"
        config["deep_think_llm"] = "deepseek-reasoner"
        config["quick_think_llm"] = "deepseek-chat"
        config["output_language"] = args.output_language
        config["max_debate_rounds"] = args.max_debate_rounds
        config["max_recur_limit"] = args.max_recur_limit
        config["local_crypto_data"] = {
            "raw_root": str(DEFAULT_RAW_ROOT),
            "archive_raw_root": str(DEFAULT_ARCHIVE_RAW_ROOT),
            "processed_root": str(DEFAULT_PROCESSED_ROOT),
            "parquet_root": args.data_root,
            "catalog_path": args.catalog_path,
            "preferred_raw_source": "raw2",
            "runtime_parquet": str(runtime_path),
        }

        selected_analysts = parse_analysts(args.analysts)
        rt = RogueTraderGraph(
            debug=args.debug,
            config=config,
            selected_analysts=selected_analysts,
        )
        final_state, decision = rt.propagate(args.ticker, args.date)
        graph_output_paths = rt.current_output_paths
        print("=" * 80)
        print("ROGUETRADER DECISION")
        print("=" * 80)
        print(decision)

    structured_signal = build_structured_signal(
        args=args,
        runtime_path=runtime_path,
        catalog_entry=catalog_entry,
        final_state=final_state,
        decision=decision,
    )

    output_paths = graph_output_paths or make_run_output_paths(PROJECT_ROOT / "my_results", args.ticker)
    report_path = output_paths.root / "本地数据报告.md" if graph_output_paths else output_paths.report_path
    state_path = output_paths.root / "本地信号.json" if graph_output_paths else output_paths.state_path

    write_report(
        report_path=report_path,
        args=args,
        local_summary=local_summary,
        catalog_entry=catalog_entry,
        runtime_path=runtime_path,
        final_state=final_state,
        decision=decision,
        structured_signal=structured_signal,
    )
    write_state(
        state_path=state_path,
        catalog_entry=catalog_entry,
        final_state=final_state,
        structured_signal=structured_signal,
    )
    if graph_output_paths:
        add_local_files_to_index(output_paths, report_path, state_path)

    print()
    print(f"Output directory: {output_paths.root}")
    print(f"Report written:  {report_path}")
    print(f"State written:   {state_path}")


if __name__ == "__main__":
    main()
