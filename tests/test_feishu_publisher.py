from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from roguetrader.control_panel.scheduler import ProjectScheduler
from roguetrader.control_panel.server import ControlPanelServer
from roguetrader.control_panel.storage import ConfigStore, RunHistoryStore

from roguetrader.publisher.feishu import (
    FEISHU_SECRET_ENV,
    FEISHU_WEBHOOK_ENV,
    FeishuConfigurationError,
    FeishuCredentials,
    FeishuDeliveryError,
    FeishuNotificationManager,
    FeishuSettingsStore,
    FeishuWebhookClient,
    FeishuWebhookSink,
    RetryPolicy,
    feishu_signature,
)
from roguetrader.publisher.loader import load_completed_run
from roguetrader.publisher.models import PreparedMessage
from roguetrader.publisher.service import LocalPublisher
from roguetrader.publisher.service import PublisherWatcher
from roguetrader.publisher.sinks import CsvDecisionSink, LocalMessageSink
from roguetrader.publisher.state import PublicationState


WEBHOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/test-hook-id"
SECRET = "test-signing-secret"
FIXED_NOW = datetime(2026, 9, 11, 5, 8, tzinfo=timezone(timedelta(hours=8)))


class FakeResponse:
    def __init__(self, payload: dict | bytes, status: int = 200):
        self.status = status
        self.payload = (
            payload
            if isinstance(payload, bytes)
            else json.dumps(payload).encode("utf-8")
        )

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def getcode(self):
        return self.status

    def read(self, _size: int = -1):
        return self.payload


class CapturingOpener:
    def __init__(self, responses: list[FakeResponse] | None = None):
        self.responses = responses or [FakeResponse({"code": 0})]
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append((request, timeout))
        return self.responses.pop(0)


def completed_run(root: Path, run_id: str = "20260911_050000_BTC_USD") -> Path:
    run_dir = root / "运行结果" / run_id
    run_dir.mkdir(parents=True)
    decision = {
        "schema_version": "1.0",
        "generated_at": "2026-09-11T05:08:00+08:00",
        "ticker": "BTC-USD",
        "trade_date": "2026-09-11",
        "action": "UNDERWEIGHT",
        "action_source": "SignalProcessor",
        "confidence": None,
        "time_horizon": None,
        "risk_level": "high",
        "entry_plan": None,
        "stop_loss": None,
        "take_profit": None,
        "key_reasons": [],
        "invalidations": [],
        "final_trade_decision_text": "降低仓位并等待风险释放。",
    }
    index = {
        "schema_version": "1.0",
        "generated_at": "2026-09-11T05:08:01+08:00",
        "ticker": "BTC-USD",
        "trade_date": "2026-09-11",
        "action": "UNDERWEIGHT",
        "files": {"decision": "最终决策.json"},
    }
    (run_dir / "最终决策.json").write_text(
        json.dumps(decision, ensure_ascii=False), encoding="utf-8"
    )
    (run_dir / "运行索引.json").write_text(
        json.dumps(index, ensure_ascii=False), encoding="utf-8"
    )
    return run_dir


def prepared_message() -> PreparedMessage:
    return PreparedMessage(
        event_id="a" * 64,
        title="RogueTrader 每日决策 · BTC-USD",
        level="negative",
        text="降低仓位并等待风险释放。",
        fields=(("分析日期", "2026-09-11"), ("动作", "UNDERWEIGHT")),
        run_id="20260911_050000_BTC_USD",
        created_at=FIXED_NOW.isoformat(),
    )


class CredentialAndClientTests(unittest.TestCase):
    def test_credentials_require_official_https_hook_and_signing_secret(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(FeishuConfigurationError):
                FeishuCredentials.from_environment()
        with patch.dict(
            os.environ,
            {FEISHU_WEBHOOK_ENV: "https://example.com/hook", FEISHU_SECRET_ENV: SECRET},
            clear=True,
        ):
            with self.assertRaises(FeishuConfigurationError):
                FeishuCredentials.from_environment()

    def test_signature_and_card_request(self):
        expected = base64.b64encode(
            hmac.new(
                f"{int(FIXED_NOW.timestamp())}\n{SECRET}".encode(),
                b"",
                hashlib.sha256,
            ).digest()
        ).decode()
        self.assertEqual(feishu_signature(int(FIXED_NOW.timestamp()), SECRET), expected)

        opener = CapturingOpener()
        client = FeishuWebhookClient(
            FeishuCredentials(WEBHOOK, SECRET),
            opener=opener,
            clock=lambda: FIXED_NOW,
        )
        client.send(prepared_message())

        request, timeout = opener.requests[0]
        payload = json.loads(request.data)
        self.assertEqual(timeout, 10.0)
        self.assertEqual(request.full_url, WEBHOOK)
        self.assertEqual(payload["msg_type"], "interactive")
        self.assertEqual(payload["card"]["header"]["template"], "red")
        self.assertNotIn(SECRET, json.dumps(payload, ensure_ascii=False))
        self.assertNotIn(WEBHOOK, json.dumps(payload, ensure_ascii=False))

        message = prepared_message()
        hostile = PreparedMessage(
            event_id=message.event_id,
            title=message.title,
            level=message.level,
            text="<at id=all>所有人</at>",
            fields=message.fields,
            run_id=message.run_id,
            created_at=message.created_at,
        )
        opener.responses.append(FakeResponse({"StatusCode": 0}))
        client.send(hostile)
        hostile_payload = json.loads(opener.requests[-1][0].data)
        self.assertNotIn("<at id=all>", json.dumps(hostile_payload, ensure_ascii=False))

        markdown = PreparedMessage(
            event_id=message.event_id,
            title=message.title,
            level=message.level,
            text="1. **评级** UNDERWEIGHT\n- 保留原始列表",
            fields=message.fields,
            run_id=message.run_id,
            created_at=message.created_at,
        )
        opener.responses.append(FakeResponse({"code": 0}))
        client.send(markdown)
        markdown_payload = json.loads(opener.requests[-1][0].data)
        serialized_markdown = json.dumps(markdown_payload, ensure_ascii=False)
        self.assertIn("**评级**", serialized_markdown)
        self.assertNotIn("\\\\*\\\\*评级", serialized_markdown)

    def test_business_error_and_http_error_are_sanitized(self):
        client = FeishuWebhookClient(
            FeishuCredentials(WEBHOOK, SECRET),
            opener=CapturingOpener([FakeResponse({"code": 19001, "msg": SECRET})]),
            clock=lambda: FIXED_NOW,
        )
        with self.assertRaisesRegex(FeishuDeliveryError, "19001") as caught:
            client.send(prepared_message())
        self.assertNotIn(SECRET, str(caught.exception))

        def http_failure(request, timeout):
            raise HTTPError(request.full_url, 429, SECRET, {}, None)

        client = FeishuWebhookClient(
            FeishuCredentials(WEBHOOK, SECRET),
            opener=http_failure,
            clock=lambda: FIXED_NOW,
        )
        with self.assertRaisesRegex(FeishuDeliveryError, "HTTP 429") as caught:
            client.send(prepared_message())
        self.assertNotIn(SECRET, str(caught.exception))


class SettingsAndRetryTests(unittest.TestCase):
    def test_operational_alert_uses_failure_card_without_result_content(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {FEISHU_WEBHOOK_ENV: WEBHOOK, FEISHU_SECRET_ENV: SECRET},
            clear=True,
        ):
            opener = CapturingOpener(
                [FakeResponse({"code": 0}), FakeResponse({"code": 0})]
            )
            manager = FeishuNotificationManager(
                FeishuSettingsStore(Path(directory) / "feishu.json"),
                opener=opener,
                clock=lambda: FIXED_NOW,
            )
            manager.send_test()
            manager.set_enabled(True)

            sent_at = manager.send_operational_alert(
                alert_id="h" * 64,
                trade_date="2026-09-11",
                scheduled_for="2026-09-11T05:00:00+08:00",
                checked_at="2026-09-11T06:00:00+08:00",
                issues=(
                    {
                        "code": "analysis_failed",
                        "label": "分析进程执行失败",
                        "symbol": "BTC-USD",
                        "detail": "退出码 1",
                    },
                ),
                checks_completed=4,
            )

            self.assertEqual(sent_at, FIXED_NOW.isoformat(timespec="seconds"))
            payload = json.loads(opener.requests[-1][0].data)
            serialized = json.dumps(payload, ensure_ascii=False)
            self.assertIn("RogueTrader 每日任务失败告警", serialized)
            self.assertIn("失败摘要", serialized)
            self.assertIn("系统没有自动重跑付费分析", serialized)
            self.assertNotIn(WEBHOOK, serialized)
            self.assertNotIn(SECRET, serialized)

    def test_existing_publication_database_is_migrated_additively(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "publisher.sqlite3"
            with sqlite3.connect(path) as connection:
                connection.executescript(
                    """
                    CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                    CREATE TABLE events (
                        event_id TEXT PRIMARY KEY,
                        run_id TEXT NOT NULL,
                        run_path TEXT NOT NULL,
                        disposition TEXT NOT NULL,
                        discovered_at TEXT NOT NULL
                    );
                    CREATE TABLE deliveries (
                        event_id TEXT NOT NULL,
                        sink TEXT NOT NULL,
                        status TEXT NOT NULL,
                        attempts INTEGER NOT NULL DEFAULT 0,
                        last_error TEXT,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY (event_id, sink)
                    );
                    """
                )

            PublicationState(path)
            with sqlite3.connect(path) as connection:
                columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(deliveries)")
                }
            self.assertTrue(
                {"first_attempt_at", "next_attempt_at", "expires_at"} <= columns
            )

    def test_test_is_required_before_enable_and_settings_are_private(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {FEISHU_WEBHOOK_ENV: WEBHOOK, FEISHU_SECRET_ENV: SECRET},
            clear=True,
        ):
            store = FeishuSettingsStore(Path(directory) / "feishu.json")
            manager = FeishuNotificationManager(
                store, opener=CapturingOpener(), clock=lambda: FIXED_NOW
            )
            with self.assertRaises(FeishuConfigurationError):
                manager.set_enabled(True)
            manager.send_test()
            self.assertTrue(manager.is_ready())
            self.assertTrue(manager.set_enabled(True).enabled)
            self.assertEqual(store.path.stat().st_mode & 0o777, 0o600)
            serialized = store.path.read_text(encoding="utf-8")
            self.assertNotIn(WEBHOOK, serialized)
            self.assertNotIn(SECRET, serialized)

            with patch.dict(os.environ, {}, clear=True):
                self.assertFalse(manager.set_enabled(False).enabled)

    def test_failed_external_sink_uses_backoff_then_succeeds(self):
        class FlakySink:
            name = "feishu"
            retry_policy = RetryPolicy(delays=(60,), window_seconds=120)

            def __init__(self):
                self.calls = 0

            def write(self, _record):
                self.calls += 1
                if self.calls == 1:
                    raise OSError("temporary")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = completed_run(root)
            state = PublicationState(root / "publisher.sqlite3")
            current = [FIXED_NOW]
            sink = FlakySink()
            publisher = LocalPublisher(
                state,
                (CsvDecisionSink(root / "每日决策.csv"), sink),
                clock=lambda: current[0],
            )

            with self.assertLogs("roguetrader.publisher.service", level="ERROR"):
                first = publisher.publish_run(run_dir)
            waiting = publisher.publish_run(run_dir)
            current[0] += timedelta(seconds=60)
            succeeded = publisher.publish_run(run_dir)

            self.assertTrue(first.attempted)
            self.assertFalse(waiting.attempted)
            self.assertTrue(succeeded.successful)
            self.assertEqual(sink.calls, 2)

    def test_failed_external_sink_expires_after_retry_window(self):
        class FailedSink:
            name = "feishu"
            retry_policy = RetryPolicy(delays=(60,), window_seconds=120)

            def write(self, _record):
                raise OSError("temporary")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = completed_run(root)
            state = PublicationState(root / "publisher.sqlite3")
            current = [FIXED_NOW]
            publisher = LocalPublisher(
                state,
                (CsvDecisionSink(root / "每日决策.csv"), FailedSink()),
                clock=lambda: current[0],
            )
            with self.assertLogs("roguetrader.publisher.service", level="ERROR"):
                publisher.publish_run(run_dir)
            current[0] += timedelta(seconds=120)
            expired = publisher.publish_run(run_dir)
            event_id = load_completed_run(run_dir).event_id

            self.assertFalse(expired.attempted)
            self.assertEqual(state.delivery_status(event_id, "feishu"), "expired")

    def test_force_retry_resets_a_successful_delivery(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = completed_run(root)
            state = PublicationState(root / "publisher.sqlite3")
            record = load_completed_run(run_dir)
            state.register_event(record, run_dir, "publish")
            state.begin_delivery(record.event_id, "feishu")
            state.finish_delivery(record.event_id, "feishu", success=True)

            self.assertTrue(
                state.force_retry_delivery(
                    record.event_id, "feishu", timestamp=FIXED_NOW.isoformat()
                )
            )
            state.begin_delivery(
                record.event_id,
                "feishu",
                attempted_at=FIXED_NOW.isoformat(),
            )

            with sqlite3.connect(state.path) as connection:
                attempts = connection.execute(
                    "SELECT attempts FROM deliveries "
                    "WHERE event_id = ? AND sink = 'feishu'",
                    (record.event_id,),
                ).fetchone()[0]
            self.assertEqual(attempts, 1)

    def test_initial_enable_skips_known_results_and_sends_future_result(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {FEISHU_WEBHOOK_ENV: WEBHOOK, FEISHU_SECRET_ENV: SECRET},
            clear=True,
        ):
            root = Path(directory)
            old_run = completed_run(root, "20260910_050000_BTC_USD")
            opener = CapturingOpener([FakeResponse({"code": 0}), FakeResponse({"code": 0})])
            manager = FeishuNotificationManager(
                FeishuSettingsStore(root / "feishu.json"),
                opener=opener,
                clock=lambda: FIXED_NOW,
            )
            manager.send_test()
            state = PublicationState(root / "publisher.sqlite3")
            publisher = LocalPublisher(
                state,
                (
                    CsvDecisionSink(root / "汇总" / "每日决策.csv"),
                    LocalMessageSink(root / "汇总" / "消息"),
                    FeishuWebhookSink(manager),
                ),
                clock=lambda: FIXED_NOW,
            )
            watcher = PublisherWatcher(publisher, root)
            watcher.activate_sink("feishu", lambda: manager.set_enabled(True))
            old_event = load_completed_run(old_run).event_id
            self.assertEqual(state.delivery_status(old_event, "feishu"), "skipped")

            new_run = completed_run(root, "20260911_060000_BTC_USD")
            publisher.publish_run(new_run)
            new_event = load_completed_run(new_run).event_id
            self.assertEqual(state.delivery_status(new_event, "feishu"), "success")
            self.assertEqual(len(opener.requests), 2)


class FeishuControlPanelApiTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(
            os.environ,
            {FEISHU_WEBHOOK_ENV: WEBHOOK, FEISHU_SECRET_ENV: SECRET},
            clear=True,
        )
        self.environment.start()
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.config_store = ConfigStore(root / "control" / "config.json")
        self.history_store = RunHistoryStore(root / "control" / "runs.json")
        self.scheduler = ProjectScheduler(
            config_store=self.config_store,
            history_store=self.history_store,
            project_root=root,
            state_dir=root / "control",
        )
        self.opener = CapturingOpener()
        self.manager = FeishuNotificationManager(
            FeishuSettingsStore(root / "control" / "feishu.json"),
            opener=self.opener,
            clock=lambda: FIXED_NOW,
        )
        state = PublicationState(root / "publisher.sqlite3")
        publisher = LocalPublisher(
            state,
            (
                CsvDecisionSink(root / "汇总" / "每日决策.csv"),
                LocalMessageSink(root / "汇总" / "消息"),
                FeishuWebhookSink(self.manager),
            ),
            clock=lambda: FIXED_NOW,
        )
        self.watcher = PublisherWatcher(publisher, root)
        static_dir = Path(__file__).resolve().parents[1] / "roguetrader/control_panel/static"
        self.server = ControlPanelServer(
            ("127.0.0.1", 0),
            config_store=self.config_store,
            history_store=self.history_store,
            scheduler=self.scheduler,
            static_dir=static_dir,
            publisher_watcher=self.watcher,
            feishu_manager=self.manager,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp_dir.cleanup()
        self.environment.stop()

    def request(self, path: str, body: dict | None = None):
        data = json.dumps(body).encode() if body is not None else None
        headers = {"X-RogueTrader-Control": "1"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = Request(self.base_url + path, data=data, method="POST", headers=headers)
        with urlopen(request, timeout=2) as response:
            return response.status, json.load(response)

    def test_status_test_and_enable_never_return_credentials(self):
        with urlopen(self.base_url + "/api/status", timeout=2) as response:
            before = json.load(response)
        serialized = json.dumps(before, ensure_ascii=False)
        self.assertTrue(before["publisher"]["feishu"]["configured"])
        self.assertTrue(before["publisher"]["feishu"]["test_required"])
        self.assertNotIn(WEBHOOK, serialized)
        self.assertNotIn(SECRET, serialized)

        with self.assertRaises(HTTPError) as caught:
            self.request("/api/publisher/feishu", {"enabled": True})
        self.assertEqual(caught.exception.code, 400)

        status, _ = self.request("/api/publisher/feishu/test", {})
        self.assertEqual(status, 200)
        status, enabled = self.request("/api/publisher/feishu", {"enabled": True})
        self.assertEqual(status, 200)
        self.assertTrue(enabled["feishu"]["enabled"])
        self.assertNotIn(WEBHOOK, json.dumps(enabled, ensure_ascii=False))
        self.assertNotIn(SECRET, json.dumps(enabled, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
