"""Point-in-time feature store for the multi-factor execution replay."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

import pandas as pd

from roguetrader.backtest.factor_data import FactorDataError


FACTOR_FILES = {
    "fear": "恐慌贪婪.csv",
    "etf": "ETF资金流.csv",
    "funding": "资金费率.csv",
    "mvrv": "MVRV.csv",
    "hashrate": "算力.csv",
    "market_volume": "全市场成交量.csv",
    "coinbase": "Coinbase日线.csv",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_factor(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    for field in ("time", "available_at"):
        if field not in frame:
            raise FactorDataError(f"因子文件 {path.name} 缺少 {field}。")
        frame[field] = pd.to_datetime(frame[field], utc=True, errors="coerce")
    if frame[["time", "available_at"]].isna().any().any():
        raise FactorDataError(f"因子文件 {path.name} 包含无效时间。")
    if frame["time"].duplicated().any():
        raise FactorDataError(f"因子文件 {path.name} 包含重复时间。")
    if (frame["available_at"] < frame["time"]).any():
        raise FactorDataError(f"因子文件 {path.name} 发生时点穿越。")
    return frame.sort_values("available_at").reset_index(drop=True)


def load_factor_bundle(path: str | Path) -> tuple[dict[str, pd.DataFrame], dict[str, Any]]:
    root = Path(path).expanduser().resolve()
    manifest_path = root / "数据清单.json"
    if not manifest_path.is_file():
        raise FactorDataError(f"多因子数据清单不存在：{manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "1.0":
        raise FactorDataError("多因子数据清单版本不受支持。")
    expected_files = manifest.get("files", {})
    datasets: dict[str, pd.DataFrame] = {}
    for key, filename in FACTOR_FILES.items():
        source = root / filename
        if not source.is_file():
            raise FactorDataError(f"多因子数据缺少 {filename}。")
        expected = expected_files.get(filename, {}).get("sha256")
        if not expected or _sha256(source) != expected:
            raise FactorDataError(f"多因子数据摘要校验失败：{filename}")
        datasets[key] = _read_factor(source)
    events_path = root / "宏观事件.json"
    expected = expected_files.get(events_path.name, {}).get("sha256")
    if not events_path.is_file() or not expected or _sha256(events_path) != expected:
        raise FactorDataError("宏观事件数据摘要校验失败。")
    datasets["events"] = pd.DataFrame(json.loads(events_path.read_text())["events"])
    return datasets, manifest


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    change = close.diff()
    gain = change.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-change.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    relative = gain / loss.replace(0, math.nan)
    result = 100 - (100 / (1 + relative))
    return result.fillna(100.0)


def _positive_streak(values: pd.Series) -> pd.Series:
    count = 0
    result = []
    for value in values:
        count = count + 1 if float(value) > 0 else 0
        result.append(count)
    return pd.Series(result, index=values.index, dtype=float)


def _asof_attach(
    features: pd.DataFrame,
    source: pd.DataFrame,
    columns: list[str],
    *,
    prefix: str = "",
) -> None:
    left = pd.DataFrame({"timestamp": features.index})
    right = source[["available_at", *columns]].copy().sort_values("available_at")
    right = right.rename(columns={"available_at": "timestamp"})
    merged = pd.merge_asof(left, right, on="timestamp", direction="backward")
    for column in columns:
        features[f"{prefix}{column}"] = merged[column].to_numpy()


def _daily_market(market: pd.DataFrame) -> pd.DataFrame:
    working = market.copy()
    if "volume_quote" in working:
        quote = pd.to_numeric(working["volume_quote"], errors="coerce")
        working["volume_usd"] = quote.fillna(working["volume"] * working["close"])
    else:
        working["volume_usd"] = working["volume"] * working["close"]
    daily = working.resample("1D", label="left", closed="left").agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
        volume_usd=("volume_usd", "sum"),
    )
    daily = daily.dropna(subset=["open", "high", "low", "close"])
    daily["previous_close"] = daily["close"].shift(1)
    daily["return_1d"] = daily["close"].pct_change()
    for period in (10, 20, 50, 111, 200, 350):
        daily[f"sma_{period}"] = daily["close"].rolling(period).mean()
    daily["ema_10"] = daily["close"].ewm(span=10, adjust=False).mean()
    daily["ema_50"] = daily["close"].ewm(span=50, adjust=False).mean()
    daily["vwma_20"] = (
        (daily["close"] * daily["volume"]).rolling(20).sum()
        / daily["volume"].rolling(20).sum()
    )
    daily["volume_mean_20"] = daily["volume_usd"].rolling(20).mean()
    daily["volume_ratio_20"] = daily["volume_usd"] / daily["volume_mean_20"]
    daily["rsi_14"] = _rsi(daily["close"])
    previous_low = daily["low"].shift(1).rolling(5).min()
    previous_rsi_low = daily["rsi_14"].shift(1).rolling(5).min()
    daily["bullish_divergence"] = (
        (daily["low"] <= previous_low) & (daily["rsi_14"] > previous_rsi_low)
    ).astype(float)
    daily["stabilized"] = (
        (daily["close"] >= daily["open"])
        & (daily["close"] >= daily["previous_close"])
    ).astype(float)
    daily["bearish_rejection"] = (
        (daily["close"] < daily["open"])
        | (daily["close"] < daily["previous_close"])
    ).astype(float)
    daily = daily.reset_index().rename(columns={daily.index.name or "index": "time"})
    if "time" not in daily:
        daily = daily.rename(columns={daily.columns[0]: "time"})
    daily["available_at"] = daily["time"] + pd.Timedelta(days=1)
    return daily


def _weekly_market(daily: pd.DataFrame) -> pd.DataFrame:
    source = daily.set_index("time")
    weekly = source.resample("W-MON", label="left", closed="left").agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume_usd=("volume_usd", "sum"),
    )
    weekly["volume_mean_8"] = weekly["volume_usd"].rolling(8).mean()
    weekly["volume_ratio_8"] = weekly["volume_usd"] / weekly["volume_mean_8"]
    weekly = weekly.dropna(subset=["close"]).reset_index()
    weekly["available_at"] = weekly["time"] + pd.Timedelta(days=7)
    return weekly


def build_feature_store(
    market: pd.DataFrame,
    bundle_path: str | Path,
    *,
    evaluation_start: pd.Timestamp | None = None,
    evaluation_end: pd.Timestamp | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Build one hourly, as-of joined feature frame with no backward leakage."""

    datasets, manifest = load_factor_bundle(bundle_path)
    features = pd.DataFrame(index=market.index.copy())
    features.index.name = "timestamp"
    for field in ("open", "high", "low", "close", "volume"):
        features[f"hour_{field}"] = pd.to_numeric(market[field], errors="coerce")

    daily = _daily_market(market)
    daily_columns = [
        "open",
        "high",
        "low",
        "close",
        "previous_close",
        "return_1d",
        "volume_usd",
        "volume_ratio_20",
        "rsi_14",
        "bullish_divergence",
        "stabilized",
        "bearish_rejection",
        "sma_10",
        "sma_20",
        "sma_50",
        "sma_111",
        "sma_200",
        "sma_350",
        "ema_10",
        "ema_50",
        "vwma_20",
    ]
    _asof_attach(features, daily, daily_columns, prefix="daily_")
    weekly = _weekly_market(daily)
    _asof_attach(
        features,
        weekly,
        ["open", "high", "low", "close", "volume_usd", "volume_ratio_8"],
        prefix="weekly_",
    )

    etf = datasets["etf"].copy()
    etf["etf_positive_streak"] = _positive_streak(etf["etf_flow_usd_m"])
    etf["etf_negative_streak"] = _positive_streak(-etf["etf_flow_usd_m"])
    etf["etf_rolling_5d_usd_m"] = etf["etf_flow_usd_m"].rolling(5).sum()
    weekly_etf = etf.set_index("time")["etf_flow_usd_m"].resample("W-MON").sum()
    positive_weeks = _positive_streak(weekly_etf)
    etf_week_lookup = positive_weeks.reindex(etf["time"], method="ffill").to_numpy()
    etf["etf_positive_week_streak"] = etf_week_lookup
    _asof_attach(
        features,
        etf,
        [
            "etf_flow_usd_m",
            "etf_positive_streak",
            "etf_negative_streak",
            "etf_rolling_5d_usd_m",
            "etf_positive_week_streak",
        ],
    )
    _asof_attach(features, datasets["fear"], ["fear_greed"])
    _asof_attach(features, datasets["funding"], ["funding_rate"])
    _asof_attach(features, datasets["mvrv"], ["CapMVRVCur"], prefix="cm_")

    hashrate = datasets["hashrate"].copy()
    hashrate["hashrate_peak_30"] = hashrate["HashRate"].rolling(30).max()
    hashrate["hashrate_drop_from_30d_peak"] = (
        hashrate["HashRate"] / hashrate["hashrate_peak_30"] - 1
    )
    hashrate["hashrate_change_3d"] = hashrate["HashRate"].pct_change(3)
    _asof_attach(
        features,
        hashrate,
        ["HashRate", "hashrate_drop_from_30d_peak", "hashrate_change_3d"],
        prefix="cm_",
    )

    volume = datasets["market_volume"].copy()
    field = "volume_reported_spot_usd_1d"
    volume["reported_volume_ratio_20"] = volume[field] / volume[field].rolling(20).mean()
    _asof_attach(features, volume, [field, "reported_volume_ratio_20"], prefix="cm_")
    features["nvt_activity_proxy"] = (
        features["cm_CapMVRVCur"] / features["cm_reported_volume_ratio_20"]
    )

    coinbase = datasets["coinbase"].copy()
    coinbase["volume_ratio_20"] = coinbase["volume_usd"] / coinbase["volume_usd"].rolling(20).mean()
    _asof_attach(
        features,
        coinbase,
        ["close", "volume_usd", "volume_ratio_20"],
        prefix="coinbase_",
    )

    event_rows = datasets["events"].to_dict("records")
    features["pre_fomc"] = 0.0
    features["post_fomc"] = 0.0
    features["event_risk_window"] = 0.0
    features["fed_hold_confirmed"] = 0.0
    for event in event_rows:
        event_type = event["type"]
        point_value = event.get("decision_at") or event.get("release_at") or event.get("starts_at")
        point = pd.Timestamp(point_value).tz_convert("UTC")
        if event_type == "FOMC":
            features.loc[(features.index >= point - pd.Timedelta(days=7)) & (features.index < point), "pre_fomc"] = 1.0
            features.loc[(features.index >= point) & (features.index <= point + pd.Timedelta(days=7)), "post_fomc"] = 1.0
            if event.get("outcome") == "hold":
                features.loc[features.index >= point, "fed_hold_confirmed"] = 1.0
        if event_type in {"FOMC", "CPI"}:
            features.loc[(features.index >= point - pd.Timedelta(days=2)) & (features.index <= point + pd.Timedelta(days=1)), "event_risk_window"] = 1.0
    features["clarity_vote_window"] = (
        (features.index >= pd.Timestamp("2026-07-20T00:00:00Z"))
        & (features.index <= pd.Timestamp("2026-07-23T23:59:59Z"))
    ).astype(float)

    required = [
        "etf_flow_usd_m",
        "fear_greed",
        "funding_rate",
        "cm_CapMVRVCur",
        "cm_HashRate",
        "cm_volume_reported_spot_usd_1d",
        "coinbase_volume_usd",
        "daily_close",
        "daily_rsi_14",
        "daily_ema_10",
        "daily_vwma_20",
        "daily_sma_200",
        "weekly_close",
    ]
    requested_start = (
        pd.Timestamp(evaluation_start).tz_convert("UTC")
        if evaluation_start is not None
        else pd.Timestamp(manifest["requested_start"]).tz_convert("UTC")
    )
    requested_end = (
        pd.Timestamp(evaluation_end).tz_convert("UTC")
        if evaluation_end is not None
        else pd.Timestamp(manifest["requested_end"]).tz_convert("UTC")
    )
    evaluation = features.loc[
        (features.index >= requested_start) & (features.index < requested_end)
    ]
    if evaluation.empty:
        raise FactorDataError("多因子数据包与行情回测区间没有交集。")
    coverage = {field: float(evaluation[field].notna().mean()) for field in required}
    quality = {
        "bundle": str(Path(bundle_path).resolve()),
        "manifest_collected_at": manifest["collected_at"],
        "hourly_rows": int(len(features)),
        "evaluation_rows": int(len(evaluation)),
        "evaluation_start": evaluation.index.min().isoformat(),
        "evaluation_end": evaluation.index.max().isoformat(),
        "start": features.index.min().isoformat(),
        "end": features.index.max().isoformat(),
        "duplicate_timestamps": int(features.index.duplicated().sum()),
        "feature_coverage": coverage,
        "minimum_required_coverage": float(min(coverage.values())),
        "availability_policy": manifest["availability_policy"],
    }
    if quality["duplicate_timestamps"]:
        raise FactorDataError("多因子特征表包含重复时间。")
    return features, quality
