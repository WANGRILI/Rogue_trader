"""Local processed Parquet data access for crypto analysis."""

import os
from pathlib import Path

import pandas as pd

from roguetrader.dataflows.config import get_config


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PARQUET_ROOT = Path(
    os.getenv(
        "ROGUETRADER_LOCAL_PARQUET_ROOT",
        str(PROJECT_ROOT / "data" / "processed" / "parquet"),
    )
)


def _get_local_config() -> dict:
    return get_config().get("local_crypto_data", {})


def _parquet_root() -> Path:
    configured = _get_local_config().get("parquet_root")
    return Path(configured) if configured else DEFAULT_PARQUET_ROOT


def _build_path(ticker: str, source: str, timeframe: str) -> Path:
    return (
        _parquet_root()
        / "ohlcv"
        / f"source={source}"
        / f"symbol={ticker}"
        / f"timeframe={timeframe}"
        / "data.parquet"
    )


def _max_drawdown(close: pd.Series) -> float | None:
    if close.empty:
        return None
    drawdown = close / close.cummax() - 1
    return float(drawdown.min() * 100)


def _period_return(df: pd.DataFrame, days: int) -> float | None:
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


def _realized_volatility(close: pd.Series, timeframe: str) -> float | None:
    returns = close.pct_change().dropna()
    if returns.empty:
        return None
    periods = 365
    if timeframe.endswith("m"):
        periods = int(365 * 24 * 60 / int(timeframe[:-1]))
    elif timeframe.endswith("h"):
        periods = int(365 * 24 / int(timeframe[:-1]))
    return float(returns.std() * (periods ** 0.5) * 100)


def _parse_reference_date(curr_date: str) -> pd.Timestamp | None:
    reference = pd.to_datetime(curr_date, errors="coerce")
    if pd.isna(reference):
        return None
    if reference.tzinfo is not None:
        reference = reference.tz_convert(None)
    return reference


def _fmt_pct(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "N/A"
    return f"{value:+.2f}%"


def _fmt_num(value) -> str:
    if value is None or pd.isna(value):
        return "N/A"
    return f"{float(value):,.4f}".rstrip("0").rstrip(".")


def get_local_ohlcv_report(
    ticker: str,
    curr_date: str,
    days: int = 365,
    timeframe: str = "1d",
    source: str = "manual_or_investing",
) -> str:
    """Return a Markdown summary of local processed OHLCV Parquet data."""
    path = _build_path(ticker, source, timeframe)
    if not path.exists():
        return (
            f"# Local OHLCV Data Not Found\n\n"
            f"Ticker: {ticker}\n"
            f"Source: {source}\n"
            f"Timeframe: {timeframe}\n"
            f"Expected runtime parquet: {path}\n\n"
            "RogueTrader local data policy: raw2 is the upstream source, but runtime tools only read processed/parquet."
        )

    try:
        df = pd.read_parquet(path)
    except Exception as exc:
        return f"# Local OHLCV Read Error\n\nPath: {path}\nError: {exc}"

    required = {"time", "open", "high", "low", "close", "volume"}
    missing = sorted(required - set(df.columns))
    if missing:
        return f"# Local OHLCV Schema Error\n\nPath: {path}\nMissing columns: {missing}"

    df = df.copy()
    df["time"] = pd.to_datetime(df["time"], errors="coerce")
    df = df.dropna(subset=["time"]).sort_values("time").reset_index(drop=True)
    if df.empty:
        return f"# Local OHLCV Empty Dataset\n\nPath: {path}"

    reference_date = _parse_reference_date(curr_date)
    if reference_date is None:
        return f"# Local OHLCV Date Error\n\nInvalid reference date: {curr_date}"

    df = df[df["time"] <= reference_date].copy()
    if df.empty:
        return (
            f"# Local OHLCV No Historical Data\n\n"
            f"Path: {path}\n"
            f"Reference date: {curr_date}\n"
            "No rows are available on or before the requested reference date."
        )

    end_time = df["time"].max()
    start_time = end_time - pd.Timedelta(days=days)
    window = df[df["time"] >= start_time].copy()
    if window.empty:
        window = df.copy()

    close = window["close"].dropna()
    latest = window.iloc[-1]
    raw_paths = sorted(str(v) for v in window.get("raw_path", pd.Series(dtype=str)).dropna().unique())
    quality_flags = sorted(
        str(v) for v in window.get("quality_flags", pd.Series(dtype=str)).dropna().unique() if str(v)
    )

    lines = [
        f"# Local Processed OHLCV Summary for {ticker}",
        "",
        f"Reference date: {curr_date}",
        f"Source: {source}",
        f"Timeframe: {timeframe}",
        f"Runtime parquet: {path}",
        f"Rows in full dataset: {len(df)}",
        f"Rows in summary window: {len(window)}",
        f"Full time range: {df['time'].min()} ~ {df['time'].max()}",
        f"Summary window: {window['time'].min()} ~ {window['time'].max()}",
        "",
        "## Latest Bar",
        f"Time: {latest['time']}",
        f"Open: {_fmt_num(latest['open'])}",
        f"High: {_fmt_num(latest['high'])}",
        f"Low: {_fmt_num(latest['low'])}",
        f"Close: {_fmt_num(latest['close'])}",
        f"Volume: {_fmt_num(latest['volume'])}",
        "",
        "## Performance",
        f"7d return: {_fmt_pct(_period_return(df, 7))}",
        f"30d return: {_fmt_pct(_period_return(df, 30))}",
        f"365d return: {_fmt_pct(_period_return(df, 365))}",
        f"Max drawdown: {_fmt_pct(_max_drawdown(close))}",
        f"Realized volatility: {_fmt_pct(_realized_volatility(close, timeframe))}",
        f"Average volume: {_fmt_num(window['volume'].dropna().mean())}",
        "",
        "## Data Lineage",
        "Runtime policy: raw2 is upstream source of truth; this tool reads processed/parquet only.",
        "Upstream raw paths:",
    ]

    lines.extend(f"- {raw_path}" for raw_path in raw_paths) if raw_paths else lines.append("- N/A")
    lines.append("Quality flags:")
    lines.extend(f"- {flag}" for flag in quality_flags) if quality_flags else lines.append("- None")
    return "\n".join(lines)
