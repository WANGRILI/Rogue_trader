"""Validated data models for the project-local scheduler."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
import re
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


SCHEMA_VERSION = 1
DEFAULT_TIMEZONE = "Asia/Shanghai"
SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9.^=_-]{0,31}$")
TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


class ValidationError(ValueError):
    """Raised when control-panel input is invalid."""


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def normalize_symbol(value: str) -> str:
    if not isinstance(value, str):
        raise ValidationError("标的必须是字符串。")
    symbol = value.strip().upper()
    if not SYMBOL_RE.fullmatch(symbol):
        raise ValidationError(
            "标的必须为 1-32 位，只能包含字母、数字、点、^、=、下划线或连字符。"
        )
    return symbol


def validate_daily_time(value: str) -> str:
    if not isinstance(value, str):
        raise ValidationError("执行时间必须是字符串。")
    daily_time = value.strip()
    if not TIME_RE.fullmatch(daily_time):
        raise ValidationError("执行时间必须使用 24 小时制 HH:MM。")
    return daily_time


def validate_timezone(value: str) -> str:
    if not isinstance(value, str):
        raise ValidationError("时区必须是字符串。")
    timezone = value.strip()
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValidationError(f"未知时区：{timezone}") from exc
    return timezone


@dataclass(frozen=True)
class TickerConfig:
    symbol: str
    enabled: bool = True

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "TickerConfig":
        if not isinstance(value, dict):
            raise ValidationError("标的配置必须是对象。")
        enabled = value.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ValidationError("标的开关必须是布尔值。")
        return cls(symbol=normalize_symbol(value.get("symbol", "")), enabled=enabled)

    def to_dict(self) -> dict[str, Any]:
        return {"symbol": self.symbol, "enabled": self.enabled}


@dataclass(frozen=True)
class ScheduleConfig:
    enabled: bool
    daily_time: str
    timezone: str
    tickers: tuple[TickerConfig, ...]
    revision: int
    updated_at: str

    @classmethod
    def default(cls) -> "ScheduleConfig":
        return cls(
            enabled=False,
            daily_time="05:00",
            timezone=DEFAULT_TIMEZONE,
            tickers=(TickerConfig("BTC-USD"),),
            revision=1,
            updated_at=now_iso(),
        )

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ScheduleConfig":
        if not isinstance(value, dict):
            raise ValidationError("调度配置必须是对象。")
        if value.get("schema_version", SCHEMA_VERSION) != SCHEMA_VERSION:
            raise ValidationError("不支持的调度配置版本。")
        enabled = value.get("enabled", False)
        if not isinstance(enabled, bool):
            raise ValidationError("总开关必须是布尔值。")
        raw_tickers = value.get("tickers", [])
        if not isinstance(raw_tickers, list):
            raise ValidationError("标的列表必须是数组。")
        tickers = tuple(TickerConfig.from_dict(item) for item in raw_tickers)
        symbols = [ticker.symbol for ticker in tickers]
        if len(symbols) != len(set(symbols)):
            raise ValidationError("标的列表不能包含重复项。")
        revision = value.get("revision", 1)
        if not isinstance(revision, int) or revision < 1:
            raise ValidationError("配置修订号无效。")
        return cls(
            enabled=enabled,
            daily_time=validate_daily_time(value.get("daily_time", "05:00")),
            timezone=validate_timezone(value.get("timezone", DEFAULT_TIMEZONE)),
            tickers=tickers,
            revision=revision,
            updated_at=str(value.get("updated_at", now_iso())),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "enabled": self.enabled,
            "daily_time": self.daily_time,
            "timezone": self.timezone,
            "tickers": [ticker.to_dict() for ticker in self.tickers],
            "revision": self.revision,
            "updated_at": self.updated_at,
        }

    def evolve(self, **changes: Any) -> "ScheduleConfig":
        return replace(
            self,
            **changes,
            revision=self.revision + 1,
            updated_at=now_iso(),
        )

    @property
    def enabled_symbols(self) -> tuple[str, ...]:
        return tuple(ticker.symbol for ticker in self.tickers if ticker.enabled)
