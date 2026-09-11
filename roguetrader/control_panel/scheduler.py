"""In-process daily scheduler with serialized RogueTrader execution."""

from __future__ import annotations

from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from typing import Any, Callable
from zoneinfo import ZoneInfo

from roguetrader.control_panel.models import ScheduleConfig
from roguetrader.control_panel.storage import ConfigStore, RunHistoryStore


Clock = Callable[[ZoneInfo], datetime]
CommandFactory = Callable[[str, str], list[str]]


def system_clock(timezone: ZoneInfo) -> datetime:
    return datetime.now(timezone)


def next_execution(config: ScheduleConfig, now: datetime) -> datetime | None:
    if not config.enabled or not config.enabled_symbols:
        return None
    hour, minute = (int(part) for part in config.daily_time.split(":"))
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


class ProjectScheduler:
    def __init__(
        self,
        config_store: ConfigStore,
        history_store: RunHistoryStore,
        project_root: str | Path,
        state_dir: str | Path,
        *,
        poll_interval: float = 1.0,
        clock: Clock = system_clock,
        command_factory: CommandFactory | None = None,
    ):
        self.config_store = config_store
        self.history_store = history_store
        self.project_root = Path(project_root).resolve()
        self.state_dir = Path(state_dir).resolve()
        self.poll_interval = poll_interval
        self.clock = clock
        self.command_factory = command_factory or self._default_command
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._worker: threading.Thread | None = None
        self._status_lock = threading.RLock()
        self._revision = -1
        self._next_run: datetime | None = None
        self._running = False
        self._current_symbol: str | None = None
        self._batch_started_at: str | None = None

    def _default_command(self, symbol: str, trade_date: str) -> list[str]:
        return [
            sys.executable,
            str(self.project_root / "my_scripts" / "roguetrader0.py"),
            "--ticker",
            symbol,
            "--date",
            trade_date,
            "--no-debug",
        ]

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="roguetrader-project-scheduler",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        self._wake_event.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    def notify_config_changed(self) -> None:
        with self._status_lock:
            self._revision = -1
        self._wake_event.set()

    def status(self) -> dict[str, Any]:
        with self._status_lock:
            worker_alive = bool(self._worker and self._worker.is_alive())
            return {
                "service_running": bool(self._thread and self._thread.is_alive()),
                "job_running": self._running or worker_alive,
                "current_symbol": self._current_symbol,
                "batch_started_at": self._batch_started_at,
                "next_run_at": self._next_run.isoformat(timespec="seconds")
                if self._next_run
                else None,
            }

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                config = self.config_store.load()
                timezone = ZoneInfo(config.timezone)
                now = self.clock(timezone)
                with self._status_lock:
                    if config.revision != self._revision:
                        self._revision = config.revision
                        self._next_run = next_execution(config, now)
                    due = self._next_run is not None and now >= self._next_run
                    running = self._running
                    if due:
                        scheduled_for = self._next_run
                        self._next_run = next_execution(
                            config,
                            now.replace(microsecond=0) + timedelta(seconds=1),
                        )
                    else:
                        scheduled_for = None
                if due and not running and scheduled_for is not None:
                    self._start_batch(config, scheduled_for)
            except Exception as exc:
                self.history_store.append(
                    {
                        "status": "scheduler_error",
                        "error": str(exc),
                        "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    }
                )
            self._wake_event.wait(self.poll_interval)
            self._wake_event.clear()

    def _start_batch(self, config: ScheduleConfig, scheduled_for: datetime) -> None:
        with self._status_lock:
            if self._running:
                return
            self._running = True
            self._batch_started_at = datetime.now().astimezone().isoformat(timespec="seconds")
        self._worker = threading.Thread(
            target=self._run_batch,
            args=(config.enabled_symbols, scheduled_for),
            name="roguetrader-scheduled-batch",
            daemon=True,
        )
        self._worker.start()

    def _run_batch(self, symbols: tuple[str, ...], scheduled_for: datetime) -> None:
        trade_date = scheduled_for.date().isoformat()
        log_dir = self.state_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        try:
            for symbol in symbols:
                if self._stop_event.is_set():
                    break
                started = datetime.now().astimezone()
                safe_symbol = symbol.replace("^", "_").replace("=", "_").replace(".", "_")
                log_path = log_dir / (
                    f"{started.strftime('%Y%m%d_%H%M%S')}_{safe_symbol}.log"
                )
                log_path.touch(mode=0o600, exist_ok=True)
                os.chmod(log_path, 0o600)
                with self._status_lock:
                    self._current_symbol = symbol
                command = self.command_factory(symbol, trade_date)
                exit_code: int | None = None
                error: str | None = None
                with log_path.open("a", encoding="utf-8") as log_file:
                    log_file.write(
                        json.dumps(
                            {
                                "event": "started",
                                "symbol": symbol,
                                "trade_date": trade_date,
                                "scheduled_for": scheduled_for.isoformat(timespec="seconds"),
                                "started_at": started.isoformat(timespec="seconds"),
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                    log_file.flush()
                    try:
                        completed = subprocess.run(
                            command,
                            cwd=self.project_root,
                            stdout=log_file,
                            stderr=subprocess.STDOUT,
                            check=False,
                            text=True,
                        )
                        exit_code = completed.returncode
                    except OSError as exc:
                        error = f"无法启动分析进程：{exc}"
                        log_file.write(error + "\n")
                finished = datetime.now().astimezone()
                record = {
                    "symbol": symbol,
                    "trade_date": trade_date,
                    "scheduled_for": scheduled_for.isoformat(timespec="seconds"),
                    "started_at": started.isoformat(timespec="seconds"),
                    "finished_at": finished.isoformat(timespec="seconds"),
                    "status": "ok" if exit_code == 0 else "failed",
                    "exit_code": exit_code,
                    "log_path": str(log_path),
                }
                if error:
                    record["error"] = error
                self.history_store.append(record)
        finally:
            with self._status_lock:
                self._running = False
                self._current_symbol = None
                self._batch_started_at = None
