#!/usr/bin/env python3
"""Evaluate a structured RogueTrader signal against processed Parquet OHLCV."""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

from local_data_summary import DEFAULT_DATA_ROOT, parquet_path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from roguetrader.output_paths import make_evaluation_output_paths  # noqa: E402


def load_signal(path: str | Path) -> dict:
    with Path(path).open("r", encoding="utf-8") as f:
        return json.load(f)


def parse_horizon(value, default_days: int) -> int:
    if not value:
        return default_days
    if isinstance(value, int):
        return value
    text = str(value).strip().lower()
    digits = "".join(ch for ch in text if ch.isdigit())
    if not digits:
        return default_days
    amount = int(digits)
    if "y" in text or "年" in text:
        return amount * 365
    if "m" in text and "min" not in text or "月" in text:
        return amount * 30
    return amount


def find_entry_price_index(df: pd.DataFrame, analysis_date: str) -> int:
    date = pd.to_datetime(analysis_date)
    eligible = df.index[df["time"] >= date]
    if len(eligible) == 0:
        raise ValueError(f"No OHLCV rows on or after analysis_date={analysis_date}")
    return int(eligible[0])


def evaluate_signal(signal: dict, holding_days: int | None, data_root: str | Path) -> tuple[dict, pd.DataFrame]:
    ticker = signal.get("ticker") or signal.get("symbol")
    analysis_date = signal.get("analysis_date") or signal.get("date")
    action = (signal.get("action") or signal.get("decision") or "HOLD").upper()
    source = signal.get("source") or "manual_or_investing"
    timeframe = signal.get("timeframe") or "1d"

    data_source = signal.get("data_source") or {}
    runtime_path = data_source.get("runtime_path")
    if runtime_path:
        path = Path(runtime_path)
    else:
        path = parquet_path(data_root, ticker, source, timeframe)

    if not ticker:
        raise ValueError("Signal JSON must include ticker or symbol")
    if not analysis_date:
        raise ValueError("Signal JSON must include analysis_date or date")
    if not path.exists():
        raise FileNotFoundError(f"Processed parquet not found: {path}")

    df = pd.read_parquet(path).copy()
    df["time"] = pd.to_datetime(df["time"], errors="coerce")
    df = df.dropna(subset=["time"]).sort_values("time").reset_index(drop=True)
    if df.empty:
        raise ValueError(f"No usable OHLCV rows in {path}")

    days = holding_days or parse_horizon(signal.get("time_horizon"), 30)
    entry_idx = find_entry_price_index(df, analysis_date)
    entry = df.iloc[entry_idx]
    end_time = entry["time"] + pd.Timedelta(days=days)
    window = df[(df.index >= entry_idx) & (df["time"] <= end_time)].copy()
    if len(window) < 2:
        raise ValueError(f"Not enough rows after analysis_date={analysis_date} for holding_days={days}")

    entry_close = float(entry["close"])
    exit_row = window.iloc[-1]
    exit_close = float(exit_row["close"])
    buy_hold_return = (exit_close / entry_close - 1) * 100
    max_up = (float(window["high"].max()) / entry_close - 1) * 100
    max_drawdown_from_entry = (float(window["low"].min()) / entry_close - 1) * 100

    if action in {"BUY", "OVERWEIGHT"}:
        signal_return = buy_hold_return
    elif action in {"SELL", "UNDERWEIGHT"}:
        signal_return = -buy_hold_return
    else:
        signal_return = 0.0

    result = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "ticker": ticker,
        "analysis_date": analysis_date,
        "action": action,
        "holding_days": days,
        "source": source,
        "timeframe": timeframe,
        "processed_path": str(path),
        "upstream_raw_source": data_source.get("upstream_raw_source"),
        "catalog_path": data_source.get("catalog_path"),
        "entry_time": str(entry["time"]),
        "entry_close": entry_close,
        "exit_time": str(exit_row["time"]),
        "exit_close": exit_close,
        "buy_hold_return_pct": buy_hold_return,
        "signal_return_pct": signal_return,
        "max_up_pct": max_up,
        "max_drawdown_from_entry_pct": max_drawdown_from_entry,
        "rows_evaluated": len(window),
        "beats_hold_cash": signal_return > 0,
    }
    return result, window


def write_reflection_candidate(result: dict, signal: dict, reflection_path: str | Path) -> Path:
    path = Path(reflection_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "mode": "candidate_only",
        "note": "This file records a reflection candidate. It does not call RogueTraderGraph.reflect_and_remember or write agent memory automatically.",
        "returns_losses": result["signal_return_pct"],
        "evaluation_result": result,
        "original_signal": signal,
        "suggested_next_step": "Review this candidate manually before running any memory-writing reflection workflow.",
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_outputs(result: dict, output_dir: str | Path) -> tuple[Path, Path, Path]:
    output_paths = make_evaluation_output_paths(output_dir, result["ticker"])
    output_paths.root.mkdir(parents=True, exist_ok=True)

    lines = [
        f"# AI Signal Evaluation — {result['ticker']}",
        "",
        f"Generated at: {result['generated_at']}",
        "",
        "## Signal",
        "",
        f"- Analysis date: `{result['analysis_date']}`",
        f"- Action: `{result['action']}`",
        f"- Holding days: `{result['holding_days']}`",
        f"- Source: `{result['source']}`",
        f"- Timeframe: `{result['timeframe']}`",
        "",
        "## Data Source",
        "",
        f"- Processed path: `{result['processed_path']}`",
        f"- Upstream raw source: `{result.get('upstream_raw_source')}`",
        f"- Catalog path: `{result.get('catalog_path')}`",
        "",
        "## Result",
        "",
        f"- Entry: {result['entry_time']} close={result['entry_close']:.4f}",
        f"- Exit: {result['exit_time']} close={result['exit_close']:.4f}",
        f"- Buy/Hold return: {result['buy_hold_return_pct']:+.2f}%",
        f"- Signal return: {result['signal_return_pct']:+.2f}%",
        f"- Max upside: {result['max_up_pct']:+.2f}%",
        f"- Max drawdown from entry: {result['max_drawdown_from_entry_pct']:+.2f}%",
        f"- Rows evaluated: {result['rows_evaluated']}",
        f"- Positive signal result: {result['beats_hold_cash']}",
    ]
    output_paths.report_path.write_text("\n".join(lines), encoding="utf-8")
    output_paths.state_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_paths.report_path, output_paths.state_path, output_paths.reflection_path


def main():
    parser = argparse.ArgumentParser(description="Evaluate structured RogueTrader signal")
    parser.add_argument("--signal", required=True, help="Path to structured signal JSON")
    parser.add_argument("--holding-days", type=int, help="Override signal time_horizon")
    parser.add_argument("--data-root", default=str(DEFAULT_DATA_ROOT))
    parser.add_argument("--output-dir", default=str(PROJECT_ROOT / "my_results"))
    parser.add_argument(
        "--reflect",
        action="store_true",
        help="Write a reflection candidate record. Does not automatically update RogueTrader memory.",
    )
    args = parser.parse_args()

    signal = load_signal(args.signal)
    result, _ = evaluate_signal(signal, args.holding_days, args.data_root)
    md_path, json_path, reflection_candidate_path = write_outputs(result, args.output_dir)
    reflection_path = (
        write_reflection_candidate(result, signal, reflection_candidate_path)
        if args.reflect
        else None
    )

    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"Report written: {md_path}")
    print(f"JSON written:   {json_path}")
    if reflection_path:
        print(f"Reflection candidate written: {reflection_path}")


if __name__ == "__main__":
    main()
