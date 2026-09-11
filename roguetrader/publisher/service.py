"""Publication orchestration and filesystem watcher."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import logging
from pathlib import Path
import threading
from typing import Any, Callable, Iterable

from roguetrader.output_paths import RUN_RESULTS_DIR
from roguetrader.publisher.loader import load_completed_run
from roguetrader.publisher.models import DecisionRecord, PublicationError, now_iso
from roguetrader.publisher.sinks import CsvDecisionSink, PublicationSink
from roguetrader.publisher.state import PublicationState


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class PublicationResult:
    event_id: str
    run_id: str
    deliveries: dict[str, str]
    attempted: bool

    @property
    def successful(self) -> bool:
        return bool(self.deliveries) and all(
            status == "success" for status in self.deliveries.values()
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "run_id": self.run_id,
            "deliveries": self.deliveries,
            "attempted": self.attempted,
            "successful": self.successful,
        }


def completed_run_directories(results_root: str | Path) -> list[Path]:
    root = Path(results_root).expanduser().resolve()
    run_root = root if root.name == RUN_RESULTS_DIR else root / RUN_RESULTS_DIR
    if not run_root.is_dir():
        return []
    return sorted(
        path.parent for path in run_root.glob("*/运行索引.json") if path.is_file()
    )


def baseline_key(results_root: str | Path) -> str:
    resolved = str(Path(results_root).expanduser().resolve()).encode("utf-8")
    return "baseline:" + hashlib.sha256(resolved).hexdigest()


def baseline_manifest_key(results_root: str | Path) -> str:
    return baseline_key(results_root) + ":run-manifest:v1"


def baseline_run_key(run_dir: str | Path) -> str:
    resolved = str(Path(run_dir).expanduser().resolve()).encode("utf-8")
    return "baseline-run:" + hashlib.sha256(resolved).hexdigest()


class LocalPublisher:
    def __init__(
        self,
        state: PublicationState,
        sinks: Iterable[PublicationSink],
        *,
        clock: Callable[[], datetime] | None = None,
    ):
        self.state = state
        self.sinks = tuple(sinks)
        if not self.sinks:
            raise ValueError("至少需要一个发布目标。")
        names = [sink.name for sink in self.sinks]
        if len(names) != len(set(names)):
            raise ValueError("发布目标名称不能重复。")
        if not isinstance(self.sinks[0], CsvDecisionSink) or names[0] != "csv":
            raise ValueError("本地 CSV 必须是发布器的第一个且唯一主目标。")
        if names.count("csv") != 1:
            raise ValueError("发布器只能配置一个本地 CSV 主目标。")
        self.csv_sink = self.sinks[0]
        self.downstream_sinks = self.sinks[1:]
        self.clock = clock or (lambda: datetime.now().astimezone())

    def publish_run(self, run_dir: str | Path) -> PublicationResult:
        record = load_completed_run(run_dir)
        self.state.register_event(record, run_dir, "publish")
        self.state.set_disposition(record.event_id, "publish")
        return self._deliver(record)

    def initialize_results_root(self, results_root: str | Path) -> int:
        key = baseline_key(results_root)
        if self.state.metadata(key):
            return 0
        baseline_count = 0
        run_dirs = completed_run_directories(results_root)
        self._record_baseline_manifest(results_root, run_dirs)
        for run_dir in run_dirs:
            try:
                record = load_completed_run(run_dir)
            except PublicationError as exc:
                LOGGER.warning("跳过不可发布的历史结果 %s：%s", run_dir.name, exc)
                continue
            if self.state.register_event(record, run_dir, "baseline"):
                baseline_count += 1
        self.state.set_metadata(key, now_iso())
        return baseline_count

    def _record_baseline_manifest(
        self,
        results_root: str | Path,
        run_dirs: Iterable[Path] | None = None,
    ) -> None:
        manifest_key = baseline_manifest_key(results_root)
        if self.state.metadata(manifest_key):
            return
        timestamp = now_iso()
        for run_dir in run_dirs or completed_run_directories(results_root):
            self.state.set_metadata(baseline_run_key(run_dir), timestamp)
        self.state.set_metadata(manifest_key, timestamp)

    def scan(
        self,
        results_root: str | Path,
        *,
        backfill: bool = False,
    ) -> dict[str, Any]:
        baseline_count = 0
        key = baseline_key(results_root)
        baseline_initialized = False
        if not self.state.metadata(key):
            if backfill:
                self.state.set_metadata(key, now_iso())
            else:
                baseline_count = self.initialize_results_root(results_root)
                baseline_initialized = True
        elif not self.state.metadata(baseline_manifest_key(results_root)):
            # Add path-level baseline markers when upgrading an existing state DB.
            self._record_baseline_manifest(results_root)

        published: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        processed_event_ids: set[str] = set()
        for run_dir in completed_run_directories(results_root):
            try:
                record = load_completed_run(run_dir)
                disposition = self.state.disposition(record.event_id)
                if disposition is None:
                    self.state.register_event(record, run_dir, "publish")
                    disposition = "publish"
                elif disposition == "baseline" and backfill:
                    self.state.set_disposition(record.event_id, "publish")
                    disposition = "publish"
                if disposition != "publish":
                    continue
                processed_event_ids.add(record.event_id)
                result = self._deliver(record)
                if not result.attempted:
                    continue
                if not result.successful:
                    errors.append(
                        {"run_id": record.run_id, "error": "部分发布目标失败。"}
                    )
                published.append(result.to_dict())
            except PublicationError as exc:
                if not backfill and self.state.metadata(baseline_run_key(run_dir)):
                    continue
                errors.append({"run_id": run_dir.name, "error": str(exc)})
        for event in self.state.events_for_disposition("publish"):
            event_id = event["event_id"]
            if event_id in processed_event_ids:
                continue
            try:
                result = self._deliver_from_csv(event_id)
                if not result.attempted:
                    continue
                if not result.successful:
                    errors.append(
                        {"run_id": result.run_id, "error": "部分发布目标失败。"}
                    )
                published.append(result.to_dict())
            except PublicationError as exc:
                errors.append({"run_id": event["run_id"], "error": str(exc)})
        return {
            "baseline_initialized": baseline_initialized,
            "baseline_count": baseline_count,
            "published": published,
            "errors": errors,
        }

    def _deliver(self, record: DecisionRecord) -> PublicationResult:
        persisted, csv_attempted = self._persist_csv(record)
        attempted = csv_attempted
        if persisted is not None:
            for sink in self.downstream_sinks:
                if _sink_is_enabled(sink):
                    attempted = self._deliver_sink(persisted, sink) or attempted
        active_sinks = (self.csv_sink,) + tuple(
            sink for sink in self.downstream_sinks if _sink_is_enabled(sink)
        )
        return PublicationResult(
            event_id=record.event_id,
            run_id=persisted.run_id if persisted is not None else record.run_id,
            deliveries={
                sink.name: (
                    self.state.delivery_status(record.event_id, sink.name)
                    or ("blocked_by_csv" if sink.name != "csv" and persisted is None else "unknown")
                )
                for sink in active_sinks
            },
            attempted=attempted,
        )

    def _deliver_from_csv(self, event_id: str) -> PublicationResult:
        if self.state.delivery_status(event_id, "csv") != "success":
            raise PublicationError("本地 CSV 尚未完成，不能执行下游投递。")
        record = self.csv_sink.read(event_id)
        return self._deliver(record)

    def _persist_csv(self, record: DecisionRecord) -> tuple[DecisionRecord | None, bool]:
        details = self.state.delivery_details(record.event_id, "csv")
        status = str(details["status"]) if details else None
        if status == "success":
            return self.csv_sink.read(record.event_id), False
        if status in {"skipped", "expired"}:
            return None, False
        current = self.clock()
        self.state.begin_delivery(
            record.event_id,
            "csv",
            attempted_at=current.isoformat(timespec="seconds"),
        )
        try:
            persisted = self.csv_sink.persist(record)
        except Exception as exc:
            LOGGER.exception("本地 CSV 写入或回读失败")
            self.state.finish_delivery(
                record.event_id,
                "csv",
                success=False,
                error=str(exc),
                finished_at=current.isoformat(timespec="seconds"),
            )
            return None, True
        self.state.finish_delivery(
            record.event_id,
            "csv",
            success=True,
            finished_at=current.isoformat(timespec="seconds"),
        )
        return persisted, True

    def _deliver_sink(self, record: DecisionRecord, sink: PublicationSink) -> bool:
        details = self.state.delivery_details(record.event_id, sink.name)
        status = str(details["status"]) if details else None
        if status in {"success", "skipped", "expired"}:
            return False
        current = self.clock()
        expires_at = _parse_datetime(details.get("expires_at")) if details else None
        if expires_at and current >= expires_at:
            self.state.expire_delivery(
                record.event_id,
                sink.name,
                expired_at=current.isoformat(timespec="seconds"),
            )
            return False
        next_attempt_at = (
            _parse_datetime(details.get("next_attempt_at")) if details else None
        )
        if next_attempt_at and current < next_attempt_at:
            return False
        retry_policy = getattr(sink, "retry_policy", None)
        if retry_policy and expires_at is None:
            expires_at = current + timedelta(seconds=retry_policy.window_seconds)
        self.state.begin_delivery(
            record.event_id,
            sink.name,
            attempted_at=current.isoformat(timespec="seconds"),
            expires_at=(expires_at.isoformat(timespec="seconds") if expires_at else None),
        )
        try:
            sink.write(record)
        except Exception as exc:
            LOGGER.exception("发布目标 %s 写入失败", sink.name)
            next_retry = None
            if retry_policy and expires_at:
                latest = self.state.delivery_details(record.event_id, sink.name) or {}
                attempts = int(latest.get("attempts", 1))
                candidate = current + timedelta(
                    seconds=retry_policy.delay_after_attempt(attempts)
                )
                next_retry = min(candidate, expires_at).isoformat(timespec="seconds")
            self.state.finish_delivery(
                record.event_id,
                sink.name,
                success=False,
                error=str(exc),
                finished_at=current.isoformat(timespec="seconds"),
                next_attempt_at=next_retry,
            )
        else:
            self.state.finish_delivery(
                record.event_id,
                sink.name,
                success=True,
                finished_at=current.isoformat(timespec="seconds"),
            )
        return True


def _sink_is_enabled(sink: PublicationSink) -> bool:
    checker = getattr(sink, "is_enabled", None)
    return bool(checker()) if callable(checker) else True


def _parse_datetime(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.astimezone()


class PublisherWatcher:
    def __init__(
        self,
        publisher: LocalPublisher,
        results_root: str | Path,
        *,
        poll_interval: float = 15.0,
    ):
        self.publisher = publisher
        self.results_root = Path(results_root).expanduser().resolve()
        self.poll_interval = poll_interval
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._scan_lock = threading.RLock()
        self._status_lock = threading.RLock()
        self._last_scan_at: str | None = None
        self._last_error: str | None = None
        self._last_published = 0
        self._baseline_count = 0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="roguetrader-local-publisher",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        self._wake_event.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    def scan_now(self) -> None:
        self._wake_event.set()

    def activate_sink(self, sink: str, activation: Callable[[], None]) -> int:
        with self._scan_lock:
            self.publisher.scan(self.results_root)
            baseline_count = self.publisher.state.baseline_sink(sink)
            activation()
        self.scan_now()
        return baseline_count

    def retry_sink_now(self, sink: str) -> int:
        count = self.publisher.state.retry_sink_now(sink)
        self.scan_now()
        return count

    def status(self) -> dict[str, Any]:
        with self._status_lock:
            return {
                "service_running": bool(self._thread and self._thread.is_alive()),
                "last_scan_at": self._last_scan_at,
                "last_error": self._last_error,
                "last_published": self._last_published,
                "baseline_count": self._baseline_count,
            }

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                with self._scan_lock:
                    report = self.publisher.scan(self.results_root)
                with self._status_lock:
                    self._last_scan_at = datetime.now().astimezone().isoformat(
                        timespec="seconds"
                    )
                    self._last_error = (
                        f"{len(report['errors'])} 个结果待处理"
                        if report["errors"]
                        else None
                    )
                    self._last_published = sum(
                        1
                        for item in report["published"]
                        if item["successful"] and item["attempted"]
                    )
                    self._baseline_count += int(report["baseline_count"])
            except Exception as exc:
                LOGGER.exception("本地结果发布扫描失败")
                with self._status_lock:
                    self._last_error = str(exc)[:1000]
            self._wake_event.wait(self.poll_interval)
            self._wake_event.clear()
