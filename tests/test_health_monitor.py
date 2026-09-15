from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest
from zoneinfo import ZoneInfo

from roguetrader.control_panel.health_monitor import (
    DailyHealthMonitor,
    DailyHealthStateStore,
)
from roguetrader.control_panel.storage import ConfigStore, RunHistoryStore
from roguetrader.publisher.loader import load_completed_run
from roguetrader.publisher.state import PublicationState


SHANGHAI = ZoneInfo("Asia/Shanghai")


class FakeScheduler:
    def __init__(self):
        self.job_running = False

    def status(self):
        return {
            "service_running": True,
            "job_running": self.job_running,
            "current_symbol": "BTC-USD" if self.job_running else None,
        }


class FakePublisherWatcher:
    def __init__(self):
        self.running = True
        self.scan_calls = 0
        self.retry_calls = []

    def status(self):
        return {"service_running": self.running}

    def scan_now(self):
        self.scan_calls += 1

    def retry_sink_now(self, sink):
        self.retry_calls.append(sink)
        return 1


class FakeFeishuManager:
    def __init__(self, *, enabled=False, ready=False, fail_alert=False):
        self.enabled = enabled
        self.ready = ready
        self.fail_alert = fail_alert
        self.alerts = []

    def is_enabled(self):
        return self.enabled

    def is_ready(self):
        return self.ready

    def send_operational_alert(self, **payload):
        if self.fail_alert:
            from roguetrader.publisher.feishu import FeishuDeliveryError

            raise FeishuDeliveryError("无法连接飞书，请稍后重试。")
        self.alerts.append(payload)
        return payload["checked_at"]


class FakeSheetManager:
    def __init__(self, *, enabled=False, ready=False):
        self.enabled = enabled
        self.ready = ready

    def is_enabled(self):
        return self.enabled

    def is_ready(self):
        return self.ready


def make_completed_run(
    results_root: Path,
    run_id: str = "20260914_050500__asof-20260914__prod__scheduled-a01__BTC_USD",
    *,
    ticker: str = "BTC-USD",
    trade_date: str = "2026-09-14",
) -> Path:
    run_dir = results_root / "运行结果" / run_id
    section_dir = run_dir / "分段报告"
    section_dir.mkdir(parents=True)
    decision = {
        "schema_version": "1.0",
        "generated_at": f"{trade_date}T05:10:00+08:00",
        "ticker": ticker,
        "trade_date": trade_date,
        "action": "HOLD",
        "action_source": "SignalProcessor",
        "confidence": None,
        "time_horizon": None,
        "risk_level": None,
        "entry_plan": None,
        "stop_loss": None,
        "take_profit": None,
        "key_reasons": [],
        "invalidations": [],
        "final_trade_decision_text": "保持观察。",
    }
    ordinary = {
        "报告.md": "完整报告",
        "状态.json": "{}",
        "运行配置.json": "{}",
        "终端日志.log": "done",
    }
    for name, content in ordinary.items():
        (run_dir / name).write_text(content, encoding="utf-8")
    (run_dir / "最终决策.json").write_text(
        json.dumps(decision, ensure_ascii=False), encoding="utf-8"
    )
    (section_dir / "最终决策.md").write_text("HOLD", encoding="utf-8")
    index = {
        "schema_version": "1.0",
        "generated_at": f"{trade_date}T05:10:01+08:00",
        "ticker": ticker,
        "trade_date": trade_date,
        "action": "HOLD",
        "files": {
            "report": "报告.md",
            "state": "状态.json",
            "decision": "最终决策.json",
            "config": "运行配置.json",
            "terminal_log": "终端日志.log",
            "sections_dir": "分段报告",
            "sections": {"final_trade_decision": "分段报告/最终决策.md"},
        },
    }
    (run_dir / "运行索引.json").write_text(
        json.dumps(index, ensure_ascii=False), encoding="utf-8"
    )
    return run_dir


def finish_delivery(state: PublicationState, run_dir: Path, sink: str, success=True):
    record = load_completed_run(run_dir)
    state.register_event(record, run_dir, "publish")
    state.begin_delivery(record.event_id, sink)
    state.finish_delivery(
        record.event_id,
        sink,
        success=success,
        error=None if success else "temporary",
    )


class DailyHealthMonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.results_root = self.root / "my_results"
        self.config_store = ConfigStore(self.root / "config.json")
        self.config_store.set_schedule(enabled=True, daily_time="05:00")
        self.history_store = RunHistoryStore(self.root / "runs.json")
        self.scheduler = FakeScheduler()
        self.watcher = FakePublisherWatcher()
        self.state = PublicationState(self.root / "publisher" / "publisher.sqlite3")
        self.feishu = FakeFeishuManager()
        self.sheet = FakeSheetManager()
        self.current = [datetime(2026, 9, 14, 5, 0, tzinfo=SHANGHAI)]
        self.health_store = DailyHealthStateStore(self.root / "daily-health.json")

    def tearDown(self):
        self.temp_dir.cleanup()

    def monitor(self):
        return DailyHealthMonitor(
            config_store=self.config_store,
            history_store=self.history_store,
            scheduler=self.scheduler,
            publisher_watcher=self.watcher,
            publication_state=self.state,
            feishu_manager=self.feishu,
            feishu_sheet_manager=self.sheet,
            results_root=self.results_root,
            state_store=self.health_store,
            clock=lambda _timezone: self.current[0],
        )

    def complete_local_delivery(self, run_dir):
        finish_delivery(self.state, run_dir, "csv")
        finish_delivery(self.state, run_dir, "execution_csv")
        finish_delivery(self.state, run_dir, "local_message")

    def test_waits_until_first_checkpoint_then_marks_complete_chain_healthy(self):
        run_dir = make_completed_run(self.results_root)
        self.complete_local_delivery(run_dir)
        monitor = self.monitor()

        waiting = monitor.check_now()
        self.assertEqual(waiting["state"], "waiting")
        self.assertEqual(waiting["checks_completed"], 0)

        self.current[0] = datetime(2026, 9, 14, 5, 15, tzinfo=SHANGHAI)
        healthy = monitor.check_now()

        self.assertEqual(healthy["state"], "healthy")
        self.assertEqual(healthy["issues"], [])
        self.assertEqual(healthy["checks_completed"], 1)
        self.assertEqual(self.health_store.path.stat().st_mode & 0o777, 0o600)

    def test_missing_task_is_checked_once_plus_three_retries_then_alerted(self):
        self.feishu.enabled = True
        self.feishu.ready = True
        monitor = self.monitor()

        states = []
        for minute in (15, 30, 45, 60):
            self.current[0] = datetime(2026, 9, 14, 5 + minute // 60, minute % 60, tzinfo=SHANGHAI)
            states.append(monitor.check_now())

        self.assertEqual([item["checks_completed"] for item in states], [1, 2, 3, 4])
        self.assertTrue(all(item["state"] == "retrying" for item in states[:3]))
        self.assertEqual(states[-1]["state"], "alerted")
        self.assertEqual(states[-1]["alert_status"], "sent")
        self.assertEqual(len(self.feishu.alerts), 1)
        self.assertEqual(self.feishu.alerts[0]["checks_completed"], 4)
        self.assertEqual(self.feishu.alerts[0]["issues"][0]["code"], "task_not_started")

        monitor.check_now()
        self.assertEqual(len(self.feishu.alerts), 1)

    def test_failed_analysis_uses_scheduler_exit_status(self):
        self.history_store.append(
            {
                "symbol": "BTC-USD",
                "trade_date": "2026-09-14",
                "scheduled_for": "2026-09-14T05:00:00+08:00",
                "status": "failed",
                "exit_code": 1,
            }
        )
        self.current[0] = datetime(2026, 9, 14, 5, 15, tzinfo=SHANGHAI)

        status = self.monitor().check_now()

        self.assertEqual(status["issues"][0]["code"], "analysis_failed")
        self.assertEqual(status["issues"][0]["detail"], "退出码 1")

    def test_incomplete_and_invalid_result_paths_are_classified(self):
        incomplete = self.results_root / "运行结果" / "20260914_050100_BTC_USD"
        incomplete.mkdir(parents=True)
        self.current[0] = datetime(2026, 9, 14, 5, 15, tzinfo=SHANGHAI)

        first = self.monitor().check_now()
        self.assertEqual(first["issues"][0]["code"], "result_incomplete")

        (incomplete / "运行索引.json").write_text("{}", encoding="utf-8")
        self.current[0] = datetime(2026, 9, 14, 5, 30, tzinfo=SHANGHAI)
        second = self.monitor().check_now()
        self.assertEqual(second["issues"][0]["code"], "result_invalid")

    def test_failed_delivery_is_retried_then_becomes_healthy(self):
        self.feishu.enabled = True
        self.feishu.ready = True
        self.sheet.enabled = True
        self.sheet.ready = True
        run_dir = make_completed_run(self.results_root)
        self.complete_local_delivery(run_dir)
        finish_delivery(self.state, run_dir, "feishu", success=False)
        finish_delivery(self.state, run_dir, "feishu_sheet")
        self.current[0] = datetime(2026, 9, 14, 5, 15, tzinfo=SHANGHAI)
        monitor = self.monitor()

        failed = monitor.check_now()
        self.assertEqual(failed["issues"][0]["code"], "feishu_failed")
        self.assertEqual(self.watcher.retry_calls, ["feishu"])

        record = load_completed_run(run_dir)
        self.state.begin_delivery(record.event_id, "feishu")
        self.state.finish_delivery(record.event_id, "feishu", success=True)
        self.current[0] = datetime(2026, 9, 14, 5, 30, tzinfo=SHANGHAI)
        healthy = monitor.check_now()
        self.assertEqual(healthy["state"], "healthy")

    def test_multi_symbol_audit_reports_only_the_missing_symbol(self):
        self.config_store.add_ticker("ETH-USD")
        run_dir = make_completed_run(self.results_root)
        self.complete_local_delivery(run_dir)
        self.current[0] = datetime(2026, 9, 14, 5, 15, tzinfo=SHANGHAI)

        status = self.monitor().check_now()

        self.assertEqual(len(status["issues"]), 1)
        self.assertEqual(status["issues"][0]["symbol"], "ETH-USD")
        self.assertEqual(status["issues"][0]["code"], "task_not_started")

    def test_alert_failure_is_persisted_without_exposing_credentials(self):
        self.feishu.enabled = True
        self.feishu.ready = True
        self.feishu.fail_alert = True
        monitor = self.monitor()

        for minute in (15, 30, 45):
            self.current[0] = datetime(2026, 9, 14, 5, minute, tzinfo=SHANGHAI)
            monitor.check_now()
        self.current[0] = datetime(2026, 9, 14, 6, 0, tzinfo=SHANGHAI)

        status = monitor.check_now()

        self.assertEqual(status["state"], "failed")
        self.assertEqual(status["alert_status"], "failed")
        self.assertNotIn("secret", status["alert_error"].lower())

    def test_cold_start_after_deadline_skips_missing_development_run(self):
        self.feishu.enabled = True
        self.feishu.ready = True
        self.current[0] = datetime(2026, 9, 14, 12, 0, tzinfo=SHANGHAI)

        status = self.monitor().check_now()

        self.assertEqual(status["state"], "skipped")
        self.assertEqual(status["checks_completed"], 0)
        self.assertEqual(status["alert_status"], "not_required")
        self.assertEqual(self.feishu.alerts, [])
        self.assertEqual(status["next_check_at"], "2026-09-15T05:15:00+08:00")


if __name__ == "__main__":
    unittest.main()
