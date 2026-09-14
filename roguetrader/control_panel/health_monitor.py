"""Lightweight daily health auditing for scheduled analysis and publication."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import json
import logging
from pathlib import Path
import threading
from typing import Any, Callable
from zoneinfo import ZoneInfo

from roguetrader.control_panel.scheduler import ProjectScheduler
from roguetrader.control_panel.storage import (
    ConfigStore,
    RunHistoryStore,
    atomic_json_write,
)
from roguetrader.output_paths import RUN_RESULTS_DIR, safe_symbol
from roguetrader.publisher.feishu import (
    FeishuError,
    FeishuNotificationManager,
)
from roguetrader.publisher.feishu_sheet import FeishuSheetError, FeishuSheetManager
from roguetrader.publisher.loader import load_completed_run
from roguetrader.publisher.models import DecisionRecord, PublicationError
from roguetrader.publisher.service import PublisherWatcher
from roguetrader.publisher.state import PublicationState


LOGGER = logging.getLogger(__name__)
Clock = Callable[[ZoneInfo], datetime]

HEALTH_SCHEMA_VERSION = 1
AUDIT_OFFSETS_MINUTES = (15, 30, 45, 60)
MAX_RETRIES = len(AUDIT_OFFSETS_MINUTES) - 1

ISSUE_LABELS = {
    "task_not_started": "未发现计划任务",
    "analysis_running": "分析仍在运行",
    "analysis_timeout": "分析超过健康检查截止时间",
    "analysis_failed": "分析进程执行失败",
    "result_missing": "未找到符合规则的结果目录",
    "result_incomplete": "结果目录缺少完成标记",
    "result_invalid": "结果文件或阶段节点不完整",
    "publisher_unavailable": "结果发布器未运行",
    "csv_pending": "本地 CSV 尚未写入",
    "csv_failed": "本地 CSV 写入失败",
    "local_message_pending": "本地消息尚未生成",
    "local_message_failed": "本地消息生成失败",
    "feishu_not_ready": "飞书群通知配置不可用",
    "feishu_pending": "飞书群消息尚未送达",
    "feishu_failed": "飞书群消息发送失败",
    "feishu_sheet_not_ready": "飞书表格配置不可用",
    "feishu_sheet_pending": "飞书表格尚未同步",
    "feishu_sheet_failed": "飞书表格同步失败",
}


@dataclass(frozen=True)
class HealthIssue:
    code: str
    symbol: str | None = None
    detail: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "code": self.code,
            "label": ISSUE_LABELS.get(self.code, "未知异常"),
            "symbol": self.symbol,
            "detail": self.detail,
        }


class DailyHealthStateStore:
    """Persist only the latest daily audit; no credentials or result text."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        self._lock = threading.RLock()

    def load(self) -> dict[str, Any]:
        with self._lock:
            if not self.path.exists():
                return {}
            try:
                value = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return {}
            if not isinstance(value, dict):
                return {}
            if value.get("schema_version") != HEALTH_SCHEMA_VERSION:
                return {}
            return value

    def save(self, value: dict[str, Any]) -> None:
        payload = dict(value)
        payload["schema_version"] = HEALTH_SCHEMA_VERSION
        with self._lock:
            atomic_json_write(self.path, payload)


class DailyHealthMonitor:
    """Audit one scheduled batch without ever rerunning paid analysis.

    The first check is 15 minutes after the configured schedule. Three later
    checks occur at 30, 45, and 60 minutes. With the normal 05:00 schedule,
    the final decision and failure alert therefore happen at 06:00.
    """

    def __init__(
        self,
        *,
        config_store: ConfigStore,
        history_store: RunHistoryStore,
        scheduler: ProjectScheduler,
        publisher_watcher: PublisherWatcher,
        publication_state: PublicationState,
        feishu_manager: FeishuNotificationManager,
        feishu_sheet_manager: FeishuSheetManager,
        results_root: str | Path,
        state_store: DailyHealthStateStore,
        poll_interval: float = 30.0,
        clock: Clock | None = None,
    ):
        self.config_store = config_store
        self.history_store = history_store
        self.scheduler = scheduler
        self.publisher_watcher = publisher_watcher
        self.publication_state = publication_state
        self.feishu_manager = feishu_manager
        self.feishu_sheet_manager = feishu_sheet_manager
        self.results_root = Path(results_root).expanduser().resolve()
        self.state_store = state_store
        self.poll_interval = poll_interval
        self.clock = clock or (lambda timezone: datetime.now(timezone))
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._status_lock = threading.RLock()
        self._snapshot = self._base_snapshot("starting")

    def _base_snapshot(self, state: str) -> dict[str, Any]:
        return {
            "schema_version": HEALTH_SCHEMA_VERSION,
            "service_running": False,
            "state": state,
            "trade_date": None,
            "scheduled_for": None,
            "deadline_at": None,
            "next_check_at": None,
            "last_check_at": None,
            "checks_completed": 0,
            "max_checks": len(AUDIT_OFFSETS_MINUTES),
            "retries_completed": 0,
            "max_retries": MAX_RETRIES,
            "issues": [],
            "alert_status": "not_required",
            "alert_sent_at": None,
            "alert_error": None,
        }

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="roguetrader-daily-health-monitor",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        self._wake_event.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    def status(self) -> dict[str, Any]:
        with self._status_lock:
            snapshot = deepcopy(self._snapshot)
        snapshot["service_running"] = bool(self._thread and self._thread.is_alive())
        return snapshot

    def check_now(self) -> dict[str, Any]:
        config = self.config_store.load()
        timezone = ZoneInfo(config.timezone)
        now = self.clock(timezone)
        if not config.enabled or not config.enabled_symbols:
            return self._publish_snapshot(self._base_snapshot("inactive"))

        hour, minute = (int(part) for part in config.daily_time.split(":"))
        scheduled_for = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        previous_schedule = scheduled_for - timedelta(days=1)
        previous_deadline = previous_schedule + timedelta(
            minutes=AUDIT_OFFSETS_MINUTES[-1]
        )
        if now < scheduled_for and now <= previous_deadline:
            scheduled_for = previous_schedule
        deadline = scheduled_for + timedelta(minutes=AUDIT_OFFSETS_MINUTES[-1])
        checkpoints = tuple(
            scheduled_for + timedelta(minutes=offset)
            for offset in AUDIT_OFFSETS_MINUTES
        )
        audit_key = self._audit_key(scheduled_for, config.enabled_symbols)
        persisted = self.state_store.load()

        common = {
            "schema_version": HEALTH_SCHEMA_VERSION,
            "audit_key": audit_key,
            "trade_date": scheduled_for.date().isoformat(),
            "scheduled_for": scheduled_for.isoformat(timespec="seconds"),
            "deadline_at": deadline.isoformat(timespec="seconds"),
            "max_checks": len(checkpoints),
            "max_retries": MAX_RETRIES,
        }

        if (
            now >= deadline
            and persisted.get("audit_key") != audit_key
            and not self._has_audit_evidence(
                config.enabled_symbols,
                scheduled_for.date().isoformat(),
                scheduled_for,
            )
        ):
            snapshot = self._base_snapshot("skipped")
            snapshot.update(common)
            next_schedule = scheduled_for + timedelta(days=1)
            snapshot["next_check_at"] = (
                next_schedule + timedelta(minutes=AUDIT_OFFSETS_MINUTES[0])
            ).isoformat(timespec="seconds")
            return self._save_and_publish(snapshot)

        if now < scheduled_for:
            snapshot = self._base_snapshot("waiting")
            snapshot.update(common)
            snapshot["next_check_at"] = checkpoints[0].isoformat(timespec="seconds")
            return self._save_and_publish(snapshot)

        due_checks = sum(1 for checkpoint in checkpoints if now >= checkpoint)
        if due_checks == 0:
            snapshot = self._base_snapshot("waiting")
            snapshot.update(common)
            snapshot["next_check_at"] = checkpoints[0].isoformat(timespec="seconds")
            return self._save_and_publish(snapshot)

        if persisted.get("audit_key") == audit_key:
            terminal = persisted.get("state") in {"healthy", "alerted", "skipped"}
            already_checked = int(persisted.get("checks_completed", 0)) >= due_checks
            if terminal or already_checked:
                return self._publish_snapshot(persisted)

        final_check = now >= deadline
        issues = self._evaluate(
            symbols=config.enabled_symbols,
            trade_date=scheduled_for.date().isoformat(),
            scheduled_for=scheduled_for,
            final_check=final_check,
        )
        checked_at = now.isoformat(timespec="seconds")
        snapshot = self._base_snapshot("healthy" if not issues else "retrying")
        snapshot.update(common)
        snapshot.update(
            {
                "last_check_at": checked_at,
                "checks_completed": due_checks,
                "retries_completed": max(0, due_checks - 1),
                "issues": [issue.to_dict() for issue in issues],
            }
        )

        if not issues:
            snapshot["alert_status"] = "not_required"
            return self._save_and_publish(snapshot)

        self._wake_publication_retries(issues)
        next_check = next((item for item in checkpoints if item > now), None)
        snapshot["next_check_at"] = (
            next_check.isoformat(timespec="seconds") if next_check else None
        )
        snapshot["alert_status"] = "pending"
        if final_check:
            snapshot["state"] = "failed"
            previous_alert = (
                persisted.get("alert_status")
                if persisted.get("audit_key") == audit_key
                else None
            )
            if previous_alert == "sent":
                snapshot["state"] = "alerted"
                snapshot["alert_status"] = "sent"
                snapshot["alert_sent_at"] = persisted.get("alert_sent_at")
            else:
                self._send_failure_alert(snapshot, issues, now)
        return self._save_and_publish(snapshot)

    def _evaluate(
        self,
        *,
        symbols: tuple[str, ...],
        trade_date: str,
        scheduled_for: datetime,
        final_check: bool,
    ) -> list[HealthIssue]:
        issues: list[HealthIssue] = []
        publisher_status = self.publisher_watcher.status()
        if not publisher_status.get("service_running"):
            issues.append(HealthIssue("publisher_unavailable"))

        scheduler_status = self.scheduler.status()
        history = self.history_store.load()
        for symbol in symbols:
            record, result_issue = self._find_completed_result(
                symbol, trade_date, scheduled_for
            )
            if record is None:
                scheduled_record = self._scheduled_record(
                    history, symbol, trade_date, scheduled_for
                )
                if scheduler_status.get("job_running"):
                    code = "analysis_timeout" if final_check else "analysis_running"
                    issues.append(HealthIssue(code, symbol))
                elif scheduled_record and scheduled_record.get("status") == "failed":
                    exit_code = scheduled_record.get("exit_code")
                    detail = f"退出码 {exit_code}" if exit_code is not None else None
                    issues.append(HealthIssue("analysis_failed", symbol, detail))
                elif scheduled_record is None and (
                    result_issue is None or result_issue.code == "result_missing"
                ):
                    issues.append(HealthIssue("task_not_started", symbol))
                else:
                    issues.append(result_issue or HealthIssue("task_not_started", symbol))
                continue
            issues.extend(self._delivery_issues(record))
        return issues

    def _has_audit_evidence(
        self,
        symbols: tuple[str, ...],
        trade_date: str,
        scheduled_for: datetime,
    ) -> bool:
        if self.scheduler.status().get("job_running"):
            return True
        history = self.history_store.load()
        if any(
            self._scheduled_record(history, symbol, trade_date, scheduled_for)
            for symbol in symbols
        ):
            return True
        run_root = self.results_root / RUN_RESULTS_DIR
        if not run_root.is_dir():
            return False
        for symbol in symbols:
            pattern = f"{scheduled_for:%Y%m%d}_*_{safe_symbol(symbol)}"
            for path in run_root.glob(pattern):
                try:
                    stamp = datetime.strptime(
                        path.name[:15], "%Y%m%d_%H%M%S"
                    ).replace(tzinfo=scheduled_for.tzinfo)
                except ValueError:
                    continue
                if path.is_dir() and stamp >= scheduled_for:
                    return True
        return False

    def _find_completed_result(
        self,
        symbol: str,
        trade_date: str,
        scheduled_for: datetime,
    ) -> tuple[DecisionRecord | None, HealthIssue | None]:
        run_root = self.results_root / RUN_RESULTS_DIR
        pattern = f"{scheduled_for:%Y%m%d}_*_{safe_symbol(symbol)}"
        candidates: list[tuple[datetime, Path]] = []
        for path in run_root.glob(pattern) if run_root.is_dir() else ():
            try:
                stamp = datetime.strptime(path.name[:15], "%Y%m%d_%H%M%S").replace(
                    tzinfo=scheduled_for.tzinfo
                )
            except ValueError:
                continue
            if path.is_dir() and stamp >= scheduled_for:
                candidates.append((stamp, path))
        candidates.sort(reverse=True)
        saw_incomplete = False
        saw_invalid = False
        for _stamp, run_dir in candidates:
            if not (run_dir / "运行索引.json").is_file():
                saw_incomplete = True
                continue
            try:
                record = load_completed_run(run_dir)
                self._validate_stage_files(run_dir)
            except (PublicationError, OSError, ValueError, json.JSONDecodeError):
                saw_invalid = True
                continue
            if record.ticker == symbol and record.trade_date == trade_date:
                return record, None
            saw_invalid = True
        if saw_invalid:
            return None, HealthIssue("result_invalid", symbol)
        if saw_incomplete:
            return None, HealthIssue("result_incomplete", symbol)
        return None, HealthIssue("result_missing", symbol)

    @staticmethod
    def _validate_stage_files(run_dir: Path) -> None:
        index = json.loads((run_dir / "运行索引.json").read_text(encoding="utf-8"))
        files = index.get("files")
        if not isinstance(files, dict):
            raise ValueError("missing files manifest")
        required = ("report", "state", "decision", "config", "terminal_log")
        for key in required:
            DailyHealthMonitor._require_manifest_path(run_dir, files.get(key), False)
        DailyHealthMonitor._require_manifest_path(
            run_dir, files.get("sections_dir"), True
        )
        sections = files.get("sections")
        if not isinstance(sections, dict) or not sections:
            raise ValueError("missing stage manifest")
        for relative in sections.values():
            DailyHealthMonitor._require_manifest_path(run_dir, relative, False)

    @staticmethod
    def _require_manifest_path(run_dir: Path, relative: Any, directory: bool) -> None:
        if not isinstance(relative, str) or not relative.strip():
            raise ValueError("invalid manifest path")
        target = (run_dir / relative).resolve()
        if not target.is_relative_to(run_dir.resolve()):
            raise ValueError("manifest path escapes run directory")
        valid = target.is_dir() if directory else target.is_file()
        if not valid:
            raise ValueError("manifest target missing")

    @staticmethod
    def _scheduled_record(
        history: list[dict[str, Any]],
        symbol: str,
        trade_date: str,
        scheduled_for: datetime,
    ) -> dict[str, Any] | None:
        expected = scheduled_for.isoformat(timespec="seconds")
        for record in history:
            if (
                record.get("symbol") == symbol
                and record.get("trade_date") == trade_date
                and record.get("scheduled_for") == expected
            ):
                return record
        return None

    def _delivery_issues(self, record: DecisionRecord) -> list[HealthIssue]:
        issues: list[HealthIssue] = []
        required = (("csv", "csv"), ("local_message", "local_message"))
        for sink, prefix in required:
            status = self.publication_state.delivery_status(record.event_id, sink)
            if status != "success":
                suffix = "failed" if status in {"failed", "expired"} else "pending"
                issues.append(HealthIssue(f"{prefix}_{suffix}", record.ticker))

        try:
            feishu_enabled = self.feishu_manager.is_enabled()
            feishu_ready = self.feishu_manager.is_ready() if feishu_enabled else False
        except FeishuError:
            issues.append(HealthIssue("feishu_not_ready", record.ticker))
        else:
            if feishu_enabled and not feishu_ready:
                issues.append(HealthIssue("feishu_not_ready", record.ticker))
            elif feishu_enabled:
                status = self.publication_state.delivery_status(record.event_id, "feishu")
                if status != "success":
                    suffix = "failed" if status in {"failed", "expired"} else "pending"
                    issues.append(HealthIssue(f"feishu_{suffix}", record.ticker))

        try:
            sheet_enabled = self.feishu_sheet_manager.is_enabled()
            sheet_ready = (
                self.feishu_sheet_manager.is_ready() if sheet_enabled else False
            )
        except FeishuSheetError:
            issues.append(HealthIssue("feishu_sheet_not_ready", record.ticker))
        else:
            if sheet_enabled and not sheet_ready:
                issues.append(HealthIssue("feishu_sheet_not_ready", record.ticker))
            elif sheet_enabled:
                status = self.publication_state.delivery_status(
                    record.event_id, "feishu_sheet"
                )
                if status != "success":
                    suffix = "failed" if status in {"failed", "expired"} else "pending"
                    issues.append(
                        HealthIssue(f"feishu_sheet_{suffix}", record.ticker)
                    )
        return issues

    def _wake_publication_retries(self, issues: list[HealthIssue]) -> None:
        codes = {issue.code for issue in issues}
        self.publisher_watcher.scan_now()
        if "feishu_failed" in codes:
            self.publisher_watcher.retry_sink_now("feishu")
        if "feishu_sheet_failed" in codes:
            self.publisher_watcher.retry_sink_now("feishu_sheet")

    def _send_failure_alert(
        self,
        snapshot: dict[str, Any],
        issues: list[HealthIssue],
        now: datetime,
    ) -> None:
        try:
            if not self.feishu_manager.is_enabled():
                raise FeishuError("飞书群通知未开启，告警仅保存在本地。")
            if not self.feishu_manager.is_ready():
                raise FeishuError("飞书群通知配置不可用，告警仅保存在本地。")
            sent_at = self.feishu_manager.send_operational_alert(
                alert_id=snapshot["audit_key"],
                trade_date=snapshot["trade_date"],
                scheduled_for=snapshot["scheduled_for"],
                checked_at=now.isoformat(timespec="seconds"),
                issues=tuple(issue.to_dict() for issue in issues),
                checks_completed=int(snapshot["checks_completed"]),
            )
        except FeishuError as exc:
            snapshot["alert_status"] = "failed"
            snapshot["alert_error"] = str(exc)[:300]
        except Exception as exc:  # pragma: no cover - defensive boundary
            LOGGER.exception("每日任务失败告警发送异常")
            snapshot["alert_status"] = "failed"
            snapshot["alert_error"] = f"告警发送异常（{type(exc).__name__}）。"
        else:
            snapshot["state"] = "alerted"
            snapshot["alert_status"] = "sent"
            snapshot["alert_sent_at"] = sent_at
            snapshot["alert_error"] = None

    @staticmethod
    def _audit_key(scheduled_for: datetime, symbols: tuple[str, ...]) -> str:
        raw = f"{scheduled_for.isoformat()}\0{'|'.join(symbols)}".encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def _save_and_publish(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        self.state_store.save(snapshot)
        return self._publish_snapshot(snapshot)

    def _publish_snapshot(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        with self._status_lock:
            self._snapshot = deepcopy(snapshot)
            self._snapshot["service_running"] = bool(
                self._thread and self._thread.is_alive()
            )
        return self.status()

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.check_now()
            except Exception as exc:  # pragma: no cover - defensive boundary
                LOGGER.exception("每日健康审计失败")
                snapshot = self._base_snapshot("monitor_error")
                snapshot["last_check_at"] = datetime.now().astimezone().isoformat(
                    timespec="seconds"
                )
                snapshot["alert_error"] = f"健康审计异常（{type(exc).__name__}）。"
                self._publish_snapshot(snapshot)
            self._wake_event.wait(self.poll_interval)
            self._wake_event.clear()
