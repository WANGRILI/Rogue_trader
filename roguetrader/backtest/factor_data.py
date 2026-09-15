"""Collect and validate point-in-time factor data for conditional backtests."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from io import StringIO
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any

import pandas as pd
import requests

from roguetrader.backtest.execution_ledger import (
    ExecutionBacktestError,
    discover_external_data_path,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FACTOR_ROOT = PROJECT_ROOT / "my_results" / "回测数据" / "多因子条件版"
DEFAULT_FUNDING_DATA = discover_external_data_path(
    "ROGUETRADER_FUNDING_PATH",
    "crypto_data_lake",
    "data/curated/records/public_funding_rate_history.jsonl",
)
ALTERNATIVE_URL = "https://api.alternative.me/fng/?limit=0&format=json"
FARSIDE_URL = "https://farside.co.uk/bitcoin-etf-flow-all-data/"
COINMETRICS_URL = (
    "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"
)
COINBASE_URL = "https://api.exchange.coinbase.com/products/BTC-USD/candles"
USER_AGENT = "RogueTraderResearch/1.0 (+local point-in-time backtest)"


class FactorDataError(ExecutionBacktestError):
    """Raised when a factor source cannot support a safe replay."""


def _request_json(
    session: requests.Session,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    timeout: float,
) -> Any:
    try:
        response = session.get(url, params=params, timeout=timeout)
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError) as exc:
        raise FactorDataError(f"外部因子请求失败：{url}") from exc


def _number(value: Any) -> float | None:
    text = str(value).strip()
    if text in {"", "-", "nan", "None"}:
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()").replace(",", "")
    try:
        parsed = float(text)
    except ValueError as exc:
        raise FactorDataError(f"无法解析外部因子数值：{value}") from exc
    return -parsed if negative else parsed


def parse_fear_greed(payload: dict[str, Any]) -> pd.DataFrame:
    if payload.get("metadata", {}).get("error"):
        raise FactorDataError("Alternative.me 返回错误。")
    rows = []
    for item in payload.get("data", []):
        timestamp = pd.to_datetime(int(item["timestamp"]), unit="s", utc=True)
        rows.append(
            {
                "time": timestamp,
                "available_at": timestamp,
                "fear_greed": float(item["value"]),
                "classification": str(item.get("value_classification", "")),
            }
        )
    frame = pd.DataFrame(rows)
    return _validate_frame(frame, "恐慌贪婪", ("time",), daily=True)


def parse_farside(html_text: str) -> pd.DataFrame:
    try:
        tables = pd.read_html(StringIO(html_text))
    except ValueError as exc:
        raise FactorDataError("Farside 页面没有可解析的 ETF 表格。") from exc
    candidates = [
        table
        for table in tables
        if {"Date", "Total"}.issubset({str(column) for column in table.columns})
    ]
    if not candidates:
        raise FactorDataError("Farside ETF 表格字段不完整。")
    source = candidates[0].copy()
    source["time"] = pd.to_datetime(source["Date"], format="%d %b %Y", utc=True, errors="coerce")
    source = source[source["time"].notna()].copy()
    source["etf_flow_usd_m"] = source["Total"].map(_number)
    source = source[source["etf_flow_usd_m"].notna()].copy()
    source["available_at"] = source["time"] + pd.Timedelta(days=1)
    frame = source[["time", "available_at", "etf_flow_usd_m"]]
    return _validate_frame(frame, "ETF 资金流", ("time",), daily=False)


def parse_coinmetrics(payload: dict[str, Any], metric: str) -> pd.DataFrame:
    if payload.get("error"):
        message = payload["error"].get("message", "unknown error")
        raise FactorDataError(f"Coin Metrics {metric} 不可用：{message}")
    rows = []
    for item in payload.get("data", []):
        if item.get(metric) is None:
            continue
        timestamp = pd.to_datetime(item["time"], utc=True)
        rows.append(
            {
                "time": timestamp,
                # Community daily metrics can be revised. A one-day lag is the
                # conservative availability convention used by this replay.
                "available_at": timestamp + pd.Timedelta(days=1),
                metric: float(item[metric]),
            }
        )
    frame = pd.DataFrame(rows)
    return _validate_frame(frame, f"Coin Metrics {metric}", ("time",), daily=True)


def parse_coinbase(payload: Any) -> pd.DataFrame:
    if not isinstance(payload, list):
        raise FactorDataError("Coinbase K 线返回格式不正确。")
    rows = []
    for item in payload:
        if not isinstance(item, list) or len(item) < 6:
            raise FactorDataError("Coinbase K 线记录格式不正确。")
        timestamp = pd.to_datetime(int(item[0]), unit="s", utc=True)
        rows.append(
            {
                "time": timestamp,
                "available_at": timestamp + pd.Timedelta(days=1),
                "low": float(item[1]),
                "high": float(item[2]),
                "open": float(item[3]),
                "close": float(item[4]),
                "volume_btc": float(item[5]),
                "volume_usd": float(item[5]) * float(item[4]),
            }
        )
    frame = pd.DataFrame(rows)
    return _validate_frame(frame, "Coinbase 日线", ("time",), daily=True)


def load_local_funding(path: str | Path) -> pd.DataFrame:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FactorDataError(f"本地资金费率数据不存在：{source}")
    rows: dict[int, dict[str, Any]] = {}
    with source.open("r", encoding="utf-8") as handle:
        for line in handle:
            item = json.loads(line)
            data = item.get("data", {})
            if data.get("instId") != "BTC-USDT-SWAP":
                continue
            timestamp_ms = int(data["fundingTime"])
            rate = data.get("realizedRate") or data.get("fundingRate")
            rows[timestamp_ms] = {
                "time": pd.to_datetime(timestamp_ms, unit="ms", utc=True),
                "available_at": pd.to_datetime(timestamp_ms, unit="ms", utc=True),
                "funding_rate": float(rate),
            }
    frame = pd.DataFrame(rows.values())
    return _validate_frame(frame, "OKX 资金费率", ("time",), daily=False)


def _validate_frame(
    frame: pd.DataFrame,
    name: str,
    key: tuple[str, ...],
    *,
    daily: bool,
) -> pd.DataFrame:
    if frame.empty:
        raise FactorDataError(f"{name} 没有有效记录。")
    frame = frame.sort_values(list(key)).reset_index(drop=True)
    if frame.duplicated(list(key)).any():
        raise FactorDataError(f"{name} 包含重复主键。")
    if frame[["time", "available_at"]].isna().any().any():
        raise FactorDataError(f"{name} 包含无效时间。")
    if (frame["available_at"] < frame["time"]).any():
        raise FactorDataError(f"{name} 的可用时间早于观测时间。")
    numeric = frame.select_dtypes(include="number")
    if numeric.isna().any().any():
        raise FactorDataError(f"{name} 包含空数值。")
    return frame


def _trim(
    frame: pd.DataFrame,
    start: pd.Timestamp,
    end: pd.Timestamp,
    *,
    lookback_days: int = 60,
) -> pd.DataFrame:
    lower = start - pd.Timedelta(days=lookback_days)
    upper = end + pd.Timedelta(days=1)
    return frame[(frame["time"] >= lower) & (frame["time"] <= upper)].copy()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _quality(frame: pd.DataFrame, value_columns: list[str]) -> dict[str, Any]:
    maximum_gap = frame["time"].diff().dt.total_seconds().max() if len(frame) > 1 else 0
    return {
        "rows": int(len(frame)),
        "start": frame["time"].min().isoformat(),
        "end": frame["time"].max().isoformat(),
        "available_start": frame["available_at"].min().isoformat(),
        "available_end": frame["available_at"].max().isoformat(),
        "duplicate_keys": int(frame["time"].duplicated().sum()),
        "null_values": int(frame[value_columns].isna().sum().sum()),
        "maximum_gap_hours": float(maximum_gap / 3600),
    }


def _write_csv(frame: pd.DataFrame, path: Path) -> None:
    frame.to_csv(path, index=False, encoding="utf-8-sig")
    os.chmod(path, 0o600)


def _event_calendar() -> dict[str, Any]:
    return {
        "timezone": "UTC",
        "events": [
            {
                "id": "fomc_2026_07",
                "type": "FOMC",
                "starts_at": "2026-07-28T13:00:00+00:00",
                "decision_at": "2026-07-29T18:00:00+00:00",
                "outcome": "hold",
                "source": "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
            },
            {
                "id": "fomc_2026_09",
                "type": "FOMC",
                "starts_at": "2026-09-15T13:00:00+00:00",
                "decision_at": "2026-09-16T18:00:00+00:00",
                "outcome": "unknown_at_backtest_cutoff",
                "source": "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
            },
            {
                "id": "cpi_2026_07",
                "type": "CPI",
                "release_at": "2026-07-14T12:30:00+00:00",
                "source": "https://www.bls.gov/schedule/news_release/cpi.htm",
            },
            {
                "id": "cpi_2026_08",
                "type": "CPI",
                "release_at": "2026-08-12T12:30:00+00:00",
                "source": "https://www.bls.gov/schedule/news_release/cpi.htm",
            },
            {
                "id": "cpi_2026_09",
                "type": "CPI",
                "release_at": "2026-09-11T12:30:00+00:00",
                "source": "https://www.bls.gov/schedule/news_release/cpi.htm",
            },
            {
                "id": "clarity_vote_target_2026_07",
                "type": "CLARITY_VOTE_TARGET",
                "starts_at": "2026-07-23T13:00:00+00:00",
                "confidence": "reported_target_not_completed_vote",
                "source": "https://solanacompass.com/news/bessent-says-clarity-act-is-at-the-1-yard-line-as-senate-eyes-vote-this-week",
            },
        ],
    }


def collect_factor_bundle(
    *,
    start: pd.Timestamp,
    end: pd.Timestamp,
    output_root: str | Path = DEFAULT_FACTOR_ROOT,
    funding_path: str | Path = DEFAULT_FUNDING_DATA,
    timeout: float = 30.0,
) -> Path:
    """Collect a new immutable factor bundle without mutating older bundles."""

    start = pd.Timestamp(start)
    end = pd.Timestamp(end)
    start = start.tz_localize("UTC") if start.tzinfo is None else start.tz_convert("UTC")
    end = end.tz_localize("UTC") if end.tzinfo is None else end.tz_convert("UTC")
    if start >= end:
        raise FactorDataError("因子采集区间无效。")
    fetch_start = (start - pd.Timedelta(days=60)).date().isoformat()
    fetch_end = (end + pd.Timedelta(days=1)).date().isoformat()
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json,text/html"})

    fear = _trim(
        parse_fear_greed(_request_json(session, ALTERNATIVE_URL, timeout=timeout)),
        start,
        end,
    )
    try:
        farside_response = session.get(FARSIDE_URL, timeout=timeout)
        farside_response.raise_for_status()
    except requests.RequestException as exc:
        raise FactorDataError("ETF 历史资金流请求失败。") from exc
    etf = _trim(parse_farside(farside_response.text), start, end)

    metrics: dict[str, pd.DataFrame] = {}
    for metric in ("CapMVRVCur", "HashRate", "volume_reported_spot_usd_1d"):
        payload = _request_json(
            session,
            COINMETRICS_URL,
            params={
                "assets": "btc",
                "metrics": metric,
                "frequency": "1d",
                "start_time": fetch_start,
                "end_time": fetch_end,
                "page_size": 10000,
            },
            timeout=timeout,
        )
        metrics[metric] = _trim(parse_coinmetrics(payload, metric), start, end)

    coinbase = _trim(
        parse_coinbase(
            _request_json(
                session,
                COINBASE_URL,
                params={
                    "granularity": 86400,
                    "start": f"{fetch_start}T00:00:00Z",
                    "end": f"{fetch_end}T00:00:00Z",
                },
                timeout=timeout,
            )
        ),
        start,
        end,
    )
    funding = _trim(load_local_funding(funding_path), start, end)

    output = Path(output_root).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    target = output / f"{stamp}__BTC_USD"
    temporary = Path(tempfile.mkdtemp(prefix=".collecting-", dir=output))
    os.chmod(temporary, 0o700)
    try:
        datasets = {
            "恐慌贪婪.csv": (fear, ["fear_greed"]),
            "ETF资金流.csv": (etf, ["etf_flow_usd_m"]),
            "资金费率.csv": (funding, ["funding_rate"]),
            "MVRV.csv": (metrics["CapMVRVCur"], ["CapMVRVCur"]),
            "算力.csv": (metrics["HashRate"], ["HashRate"]),
            "全市场成交量.csv": (
                metrics["volume_reported_spot_usd_1d"],
                ["volume_reported_spot_usd_1d"],
            ),
            "Coinbase日线.csv": (coinbase, ["volume_usd"]),
        }
        quality: dict[str, Any] = {}
        for filename, (frame, value_columns) in datasets.items():
            path = temporary / filename
            _write_csv(frame, path)
            quality[filename] = _quality(frame, value_columns)

        events_path = temporary / "宏观事件.json"
        events_path.write_text(
            json.dumps(_event_calendar(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.chmod(events_path, 0o600)
        manifest = {
            "schema_version": "1.0",
            "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "requested_start": start.isoformat(),
            "requested_end": end.isoformat(),
            "availability_policy": {
                "fear_greed": "provider timestamp",
                "etf_flow": "US trade date + 24 hours",
                "funding_rate": "funding timestamp",
                "coinmetrics_daily": "metric date + 24 hours",
                "coinbase_daily": "candle start + 24 hours",
            },
            "sources": [
                {"dataset": "恐慌贪婪.csv", "url": ALTERNATIVE_URL},
                {"dataset": "ETF资金流.csv", "url": FARSIDE_URL},
                {"dataset": "资金费率.csv", "path": str(Path(funding_path).resolve())},
                {"dataset": "MVRV.csv", "url": COINMETRICS_URL, "metric": "CapMVRVCur"},
                {"dataset": "算力.csv", "url": COINMETRICS_URL, "metric": "HashRate"},
                {
                    "dataset": "全市场成交量.csv",
                    "url": COINMETRICS_URL,
                    "metric": "volume_reported_spot_usd_1d",
                },
                {"dataset": "Coinbase日线.csv", "url": COINBASE_URL},
            ],
            "quality": quality,
        }
        manifest["files"] = {
            path.name: {"sha256": _sha256(path), "bytes": path.stat().st_size}
            for path in temporary.iterdir()
            if path.is_file()
        }
        manifest_path = temporary / "数据清单.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.chmod(manifest_path, 0o600)
        os.replace(temporary, target)
        os.chmod(target, 0o700)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return target
