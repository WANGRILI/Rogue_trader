#!/usr/bin/env python3
"""Summarize local processed Parquet OHLCV data for RogueTrader."""

import argparse
import os
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_ROOT = Path(
    os.getenv(
        "ROGUETRADER_LOCAL_PARQUET_ROOT",
        str(PROJECT_ROOT / "data" / "processed" / "parquet"),
    )
)


def parquet_path(data_root: str | Path, ticker: str, source: str, timeframe: str) -> Path:
    return (
        Path(data_root)
        / "ohlcv"
        / f"source={source}"
        / f"symbol={ticker}"
        / f"timeframe={timeframe}"
        / "data.parquet"
    )


def max_drawdown(close: pd.Series) -> float | None:
    if close.empty:
        return None
    running_max = close.cummax()
    drawdown = close / running_max - 1
    return float(drawdown.min() * 100)


def realized_volatility(close: pd.Series, periods_per_year: int) -> float | None:
    returns = close.pct_change().dropna()
    if returns.empty:
        return None
    return float(returns.std() * (periods_per_year ** 0.5) * 100)


def period_return(df: pd.DataFrame, days: int) -> float | None:
    if df.empty:
        return None
    end_time = df["time"].max()
    start_time = end_time - pd.Timedelta(days=days)
    window = df[df["time"] >= start_time]
    if len(window) < 2:
        return None
    first = window.iloc[0]["close"]
    last = window.iloc[-1]["close"]
    if pd.isna(first) or first == 0 or pd.isna(last):
        return None
    return float((last / first - 1) * 100)


def format_pct(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "N/A"
    return f"{value:+.2f}%"


def format_number(value) -> str:
    if value is None or pd.isna(value):
        return "N/A"
    return f"{float(value):,.4f}".rstrip("0").rstrip(".")


def infer_periods_per_year(timeframe: str) -> int:
    if timeframe.endswith("m"):
        minutes = int(timeframe[:-1])
        return int(365 * 24 * 60 / minutes)
    if timeframe.endswith("h"):
        hours = int(timeframe[:-1])
        return int(365 * 24 / hours)
    return 365


def parse_reference_date(reference_date: str | None) -> pd.Timestamp | None:
    if not reference_date:
        return None
    parsed = pd.to_datetime(reference_date, errors="coerce")
    if pd.isna(parsed):
        raise ValueError(f"Invalid reference date: {reference_date}")
    if parsed.tzinfo is not None:
        parsed = parsed.tz_convert(None)
    return parsed


def load_local_ohlcv(
    ticker: str,
    source: str,
    timeframe: str,
    data_root: str | Path = DEFAULT_DATA_ROOT,
) -> tuple[pd.DataFrame, Path]:
    path = parquet_path(data_root, ticker, source, timeframe)
    if not path.exists():
        raise FileNotFoundError(f"Local OHLCV parquet not found: {path}")

    df = pd.read_parquet(path)
    required = {"time", "open", "high", "low", "close", "volume"}
    missing = sorted(required - set(df.columns))
    if missing:
        raise ValueError(f"Missing required columns in {path}: {missing}")

    df = df.copy()
    df["time"] = pd.to_datetime(df["time"], errors="coerce")
    df = df.dropna(subset=["time"]).sort_values("time").reset_index(drop=True)
    return df, path


def build_summary(
    ticker: str,
    source: str,
    timeframe: str,
    days: int,
    data_root: str | Path = DEFAULT_DATA_ROOT,
    reference_date: str | None = None,
) -> str:
    df, path = load_local_ohlcv(ticker, source, timeframe, data_root)
    if df.empty:
        raise ValueError(f"No rows found in {path}")

    reference = parse_reference_date(reference_date)
    if reference is not None:
        df = df[df["time"] <= reference].copy()
        if df.empty:
            raise ValueError(f"No rows available on or before {reference_date} in {path}")

    end_time = df["time"].max()
    start_filter = end_time - pd.Timedelta(days=days)
    window = df[df["time"] >= start_filter].copy()
    if window.empty:
        window = df.copy()

    close = window["close"].dropna()
    periods_per_year = infer_periods_per_year(timeframe)

    raw_paths = sorted(str(v) for v in window.get("raw_path", pd.Series(dtype=str)).dropna().unique())
    quality_flags = sorted(
        str(v) for v in window.get("quality_flags", pd.Series(dtype=str)).dropna().unique() if str(v)
    )

    latest = window.iloc[-1]
    average_volume = window["volume"].dropna().mean()

    lines = [
        f"# Local OHLCV Summary for {ticker}",
        "",
        f"Source: {source}",
        f"Timeframe: {timeframe}",
        f"Data root: {data_root}",
        f"Reference date: {reference_date or end_time}",
        f"Runtime parquet: {path}",
        f"Rows in full dataset: {len(df)}",
        f"Rows in summary window: {len(window)}",
        f"Full time range: {df['time'].min()} ~ {df['time'].max()}",
        f"Summary window: {window['time'].min()} ~ {window['time'].max()}",
        "",
        "## Latest Bar",
        "",
        f"Time: {latest['time']}",
        f"Open: {format_number(latest['open'])}",
        f"High: {format_number(latest['high'])}",
        f"Low: {format_number(latest['low'])}",
        f"Close: {format_number(latest['close'])}",
        f"Volume: {format_number(latest['volume'])}",
        "",
        "## Performance",
        "",
        f"7d return: {format_pct(period_return(df, 7))}",
        f"30d return: {format_pct(period_return(df, 30))}",
        f"365d return: {format_pct(period_return(df, 365))}",
        f"Max drawdown: {format_pct(max_drawdown(close))}",
        f"Realized volatility: {format_pct(realized_volatility(close, periods_per_year))}",
        f"Average volume: {format_number(average_volume)}",
        "",
        "## Data Lineage",
        "",
        "Upstream raw paths:",
    ]

    if raw_paths:
        lines.extend(f"- {raw_path}" for raw_path in raw_paths)
    else:
        lines.append("- N/A")

    lines.extend([
        "",
        "Quality flags:",
    ])
    if quality_flags:
        lines.extend(f"- {flag}" for flag in quality_flags)
    else:
        lines.append("- None")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Summarize processed local OHLCV Parquet data")
    parser.add_argument("--ticker", default="BTC-USD", help="Ticker/symbol, e.g. BTC-USD")
    parser.add_argument("--source", default="manual_or_investing", help="Data source")
    parser.add_argument("--timeframe", default="1d", help="Timeframe, e.g. 1d, 1m")
    parser.add_argument("--days", type=int, default=365, help="Summary lookback window in days")
    parser.add_argument("--data-root", default=str(DEFAULT_DATA_ROOT), help="Processed parquet root")
    parser.add_argument("--date", help="Reference date; excludes rows after this date")
    args = parser.parse_args()

    try:
        print(
            build_summary(
                ticker=args.ticker,
                source=args.source,
                timeframe=args.timeframe,
                days=args.days,
                data_root=args.data_root,
                reference_date=args.date,
            )
        )
    except Exception as exc:
        raise SystemExit(f"Error: {exc}")


if __name__ == "__main__":
    main()
