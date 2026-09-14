from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from roguetrader.control_panel.__main__ import acquire_instance_lock, publisher_state_path
from roguetrader.control_panel.models import ScheduleConfig, ValidationError
from roguetrader.control_panel.scheduler import ProjectScheduler, next_execution
from roguetrader.control_panel.server import ControlPanelServer
from roguetrader.control_panel.storage import ConfigStore, RunHistoryStore


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = PROJECT_ROOT / "roguetrader" / "control_panel" / "static"
SHANGHAI = ZoneInfo("Asia/Shanghai")


class ConfigStoreTests(unittest.TestCase):
    def test_publisher_state_follows_selected_runtime_state(self):
        state_dir = Path("/project/.runtime/production/control-panel")

        self.assertEqual(
            publisher_state_path(state_dir),
            Path("/project/.runtime/production/publisher/publisher.sqlite3"),
        )

    def test_defaults_are_disabled_and_persisted_privately(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            config = ConfigStore(path).load()

            self.assertFalse(config.enabled)
            self.assertFalse(config.execution_plan_enabled)
            self.assertEqual(config.daily_time, "05:00")
            self.assertEqual(config.enabled_symbols, ("BTC-USD",))
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_schedule_and_ticker_controls_are_independent(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ConfigStore(Path(directory) / "config.json")

            store.set_schedule(enabled=True, daily_time="07:35")
            store.set_execution_plan_enabled(True)
            store.add_ticker("eth-usd")
            store.set_ticker_enabled("BTC-USD", False)
            config = store.load()

            self.assertTrue(config.enabled)
            self.assertTrue(config.execution_plan_enabled)
            self.assertEqual(config.daily_time, "07:35")
            self.assertEqual(config.enabled_symbols, ("ETH-USD",))

            store.remove_ticker("ETH-USD")
            self.assertEqual([item.symbol for item in store.load().tickers], ["BTC-USD"])

    def test_invalid_inputs_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ConfigStore(Path(directory) / "config.json")
            with self.assertRaises(ValidationError):
                store.set_schedule(daily_time="25:00")
            with self.assertRaises(ValidationError):
                store.add_ticker("BTC/USD")
            with self.assertRaises(ValidationError):
                store.add_ticker(None)
            with self.assertRaises(ValidationError):
                store.add_ticker("BTC-USD")

    def test_instance_lock_prevents_duplicate_schedulers(self):
        with tempfile.TemporaryDirectory() as directory:
            lock_path = Path(directory) / "instance.lock"
            first = acquire_instance_lock(lock_path)
            try:
                with self.assertRaises(SystemExit):
                    acquire_instance_lock(lock_path)
            finally:
                first.close()

            replacement = acquire_instance_lock(lock_path)
            replacement.close()


class SchedulerTests(unittest.TestCase):
    def test_next_execution_uses_next_future_shanghai_time(self):
        config = ScheduleConfig.default().evolve(enabled=True, daily_time="09:30")

        before = datetime(2026, 9, 9, 9, 29, tzinfo=SHANGHAI)
        after = datetime(2026, 9, 9, 9, 31, tzinfo=SHANGHAI)

        self.assertEqual(next_execution(config, before).isoformat(), "2026-09-09T09:30:00+08:00")
        self.assertEqual(next_execution(config, after).isoformat(), "2026-09-10T09:30:00+08:00")
        self.assertIsNone(next_execution(config.evolve(enabled=False), before))

    def test_batch_records_each_symbol_without_external_api(self):
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory)
            history = RunHistoryStore(state_dir / "runs.json")
            scheduler = ProjectScheduler(
                config_store=ConfigStore(state_dir / "config.json"),
                history_store=history,
                project_root=PROJECT_ROOT,
                state_dir=state_dir,
                command_factory=lambda symbol, trade_date: [
                    sys.executable,
                    "-c",
                    f"print({symbol!r}, {trade_date!r})",
                ],
            )

            scheduler._run_batch(
                ("BTC-USD", "ETH-USD"),
                datetime(2026, 9, 9, 5, 0, tzinfo=SHANGHAI),
                True,
            )

            records = history.load()
            self.assertEqual({record["symbol"] for record in records}, {"BTC-USD", "ETH-USD"})
            self.assertTrue(all(record["status"] == "ok" for record in records))
            self.assertTrue(all(record["execution_plan_enabled"] for record in records))
            self.assertTrue(all(Path(record["log_path"]).is_file() for record in records))
            for record in records:
                log = Path(record["log_path"]).read_text(encoding="utf-8")
                self.assertIn("scheduled", log)


class ControlPanelApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        state_dir = Path(self.temp_dir.name)
        self.config_store = ConfigStore(state_dir / "config.json")
        self.history_store = RunHistoryStore(state_dir / "runs.json")
        self.scheduler = ProjectScheduler(
            config_store=self.config_store,
            history_store=self.history_store,
            project_root=PROJECT_ROOT,
            state_dir=state_dir,
        )
        self.server = ControlPanelServer(
            ("127.0.0.1", 0),
            config_store=self.config_store,
            history_store=self.history_store,
            scheduler=self.scheduler,
            static_dir=STATIC_DIR,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp_dir.cleanup()

    def request(self, path: str, method: str = "GET", body: dict | None = None):
        payload = json.dumps(body).encode() if body is not None else None
        headers = {"X-RogueTrader-Control": "1"}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        request = Request(self.base_url + path, data=payload, method=method, headers=headers)
        with urlopen(request, timeout=2) as response:
            return response.status, json.load(response)

    def test_status_and_all_mutations(self):
        status, snapshot = self.request("/api/status")
        self.assertEqual(status, 200)
        self.assertFalse(snapshot["config"]["enabled"])
        self.assertEqual(snapshot["publisher"], {"service_running": False})

        self.request("/api/schedule", "POST", {"enabled": True, "daily_time": "06:45"})
        self.request("/api/execution-plan", "POST", {"enabled": True})
        self.request("/api/tickers", "POST", {"symbol": "eth-usd"})
        self.request("/api/tickers/BTC-USD", "PATCH", {"enabled": False})
        _, snapshot = self.request("/api/status")

        self.assertTrue(snapshot["config"]["enabled"])
        self.assertTrue(snapshot["config"]["execution_plan_enabled"])
        self.assertEqual(snapshot["config"]["daily_time"], "06:45")
        self.assertEqual(
            [item for item in snapshot["config"]["tickers"] if item["enabled"]],
            [{"symbol": "ETH-USD", "enabled": True}],
        )

        self.request("/api/tickers/ETH-USD", "DELETE")
        self.assertEqual(len(self.config_store.load().tickers), 1)

    def test_mutation_requires_control_header(self):
        request = Request(
            self.base_url + "/api/schedule",
            data=b'{"enabled": true}',
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with self.assertRaises(HTTPError) as caught:
            urlopen(request, timeout=2)
        self.assertEqual(caught.exception.code, 403)

    def test_schedule_rejects_null_values(self):
        for body in ({"enabled": None}, {"daily_time": None}):
            with self.assertRaises(HTTPError) as caught:
                self.request("/api/schedule", "POST", body)
            self.assertEqual(caught.exception.code, 400)

    def test_only_explicit_static_files_are_served(self):
        with urlopen(self.base_url + "/", timeout=2) as response:
            self.assertNotIn("Python", response.headers.get("Server", ""))

        request = Request(self.base_url + "/.env", method="GET")
        with self.assertRaises(HTTPError) as caught:
            urlopen(request, timeout=2)
        self.assertEqual(caught.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
