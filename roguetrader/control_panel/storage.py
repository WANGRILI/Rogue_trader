"""Atomic JSON persistence for scheduler configuration and run history."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import threading
from typing import Any, Callable

from roguetrader.control_panel.models import (
    ScheduleConfig,
    TickerConfig,
    ValidationError,
    normalize_symbol,
    validate_daily_time,
)


def atomic_json_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


class ConfigStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.RLock()

    def load(self) -> ScheduleConfig:
        with self._lock:
            if not self.path.exists():
                config = ScheduleConfig.default()
                atomic_json_write(self.path, config.to_dict())
                return config
            try:
                value = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValidationError(f"无法读取调度配置：{exc}") from exc
            return ScheduleConfig.from_dict(value)

    def save(self, config: ScheduleConfig) -> ScheduleConfig:
        with self._lock:
            validated = ScheduleConfig.from_dict(config.to_dict())
            atomic_json_write(self.path, validated.to_dict())
            return validated

    def update(self, mutation: Callable[[ScheduleConfig], ScheduleConfig]) -> ScheduleConfig:
        with self._lock:
            return self.save(mutation(self.load()))

    def set_schedule(
        self,
        *,
        enabled: bool | None = None,
        daily_time: str | None = None,
    ) -> ScheduleConfig:
        if enabled is not None and not isinstance(enabled, bool):
            raise ValidationError("总开关必须是布尔值。")
        validated_time = validate_daily_time(daily_time) if daily_time is not None else None

        def mutate(config: ScheduleConfig) -> ScheduleConfig:
            changes: dict[str, Any] = {}
            if enabled is not None:
                changes["enabled"] = enabled
            if validated_time is not None:
                changes["daily_time"] = validated_time
            return config.evolve(**changes)

        return self.update(mutate)

    def set_execution_plan_enabled(self, enabled: bool) -> ScheduleConfig:
        if not isinstance(enabled, bool):
            raise ValidationError("执行计划开关必须是布尔值。")
        return self.update(
            lambda config: config.evolve(execution_plan_enabled=enabled)
        )

    def add_ticker(self, symbol: str) -> ScheduleConfig:
        normalized = normalize_symbol(symbol)

        def mutate(config: ScheduleConfig) -> ScheduleConfig:
            if any(ticker.symbol == normalized for ticker in config.tickers):
                raise ValidationError(f"标的已存在：{normalized}")
            return config.evolve(tickers=(*config.tickers, TickerConfig(normalized)))

        return self.update(mutate)

    def set_ticker_enabled(self, symbol: str, enabled: bool) -> ScheduleConfig:
        normalized = normalize_symbol(symbol)
        if not isinstance(enabled, bool):
            raise ValidationError("标的开关必须是布尔值。")

        def mutate(config: ScheduleConfig) -> ScheduleConfig:
            found = False
            tickers = []
            for ticker in config.tickers:
                if ticker.symbol == normalized:
                    found = True
                    tickers.append(TickerConfig(ticker.symbol, enabled))
                else:
                    tickers.append(ticker)
            if not found:
                raise ValidationError(f"标的不存在：{normalized}")
            return config.evolve(tickers=tuple(tickers))

        return self.update(mutate)

    def remove_ticker(self, symbol: str) -> ScheduleConfig:
        normalized = normalize_symbol(symbol)

        def mutate(config: ScheduleConfig) -> ScheduleConfig:
            tickers = tuple(ticker for ticker in config.tickers if ticker.symbol != normalized)
            if len(tickers) == len(config.tickers):
                raise ValidationError(f"标的不存在：{normalized}")
            return config.evolve(tickers=tickers)

        return self.update(mutate)


class RunHistoryStore:
    def __init__(self, path: str | Path, limit: int = 100):
        self.path = Path(path)
        self.limit = limit
        self._lock = threading.RLock()

    def load(self) -> list[dict[str, Any]]:
        with self._lock:
            if not self.path.exists():
                return []
            try:
                value = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return []
            return value if isinstance(value, list) else []

    def append(self, record: dict[str, Any]) -> None:
        with self._lock:
            records = self.load()
            records.insert(0, record)
            atomic_json_write(self.path, records[: self.limit])
