from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from roguetrader.control_panel.scheduler import ProjectScheduler
from roguetrader.control_panel.server import ControlPanelServer
from roguetrader.control_panel.storage import ConfigStore, RunHistoryStore
from roguetrader.publisher.feishu_sheet import (
    FEISHU_APP_ID_ENV,
    FEISHU_APP_SECRET_ENV,
    FEISHU_SHEET_FIELDS,
    FEISHU_SHEET_ID_ENV,
    FEISHU_SPREADSHEET_TOKEN_ENV,
    FeishuSheetClient,
    FeishuSheetConfigurationError,
    FeishuSheetCredentials,
    FeishuSheetDeliveryError,
    FeishuSheetManager,
    FeishuSheetSettingsStore,
    FeishuSheetSink,
)
from roguetrader.publisher.models import DecisionRecord
from roguetrader.publisher.service import LocalPublisher, PublisherWatcher
from roguetrader.publisher.sinks import CsvDecisionSink, LocalMessageSink
from roguetrader.publisher.state import PublicationState


APP_ID = "cli_roguetrader_test"
APP_SECRET = "test-app-secret"
SPREADSHEET_TOKEN = "shtcnTestSpreadsheet"
SHEET_ID = "sheet01"
TENANT_TOKEN = "test-tenant-token"
FIXED_NOW = datetime(2026, 9, 11, 19, 0, tzinfo=timezone(timedelta(hours=8)))
ENVIRONMENT = {
    FEISHU_APP_ID_ENV: APP_ID,
    FEISHU_APP_SECRET_ENV: APP_SECRET,
    FEISHU_SPREADSHEET_TOKEN_ENV: SPREADSHEET_TOKEN,
    FEISHU_SHEET_ID_ENV: SHEET_ID,
}


class FakeResponse:
    def __init__(self, payload: dict, status: int = 200):
        self.status = status
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def getcode(self):
        return self.status

    def read(self, _size: int = -1):
        return self.payload


class CapturingOpener:
    def __init__(self, responses: list[FakeResponse]):
        self.responses = responses
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append((request, timeout))
        return self.responses.pop(0)


def token_response() -> FakeResponse:
    return FakeResponse(
        {
            "code": 0,
            "tenant_access_token": TENANT_TOKEN,
            "expire": 7200,
        }
    )


def values_response(values: list[list[object]]) -> FakeResponse:
    return FakeResponse({"code": 0, "data": {"valueRange": {"values": values}}})


def success_response() -> FakeResponse:
    return FakeResponse({"code": 0, "data": {}})


def append_response() -> FakeResponse:
    return FakeResponse({"code": 0, "data": {"updates": {"updatedRows": 1}}})


def find_response(*matched_cells: str) -> FakeResponse:
    return FakeResponse(
        {
            "code": 0,
            "data": {
                "find_result": {
                    "matched_cells": list(matched_cells),
                    "matched_formula_cells": [],
                    "rows_count": len(matched_cells),
                }
            },
        }
    )


def decision(event_id: str, ticker: str, *, entry_plan: str = "等待确认") -> DecisionRecord:
    return DecisionRecord(
        event_id=event_id,
        run_id=f"20260911_050000_{ticker.replace('-', '_')}",
        source_schema_version="1.0",
        generated_at="2026-09-11T05:08:00+08:00",
        trade_date="2026-09-11",
        ticker=ticker,
        action="UNDERWEIGHT",
        action_source="SignalProcessor",
        confidence=None,
        risk_level="high",
        time_horizon=None,
        entry_plan=entry_plan,
        stop_loss=None,
        take_profit=None,
        key_reasons=("risk",),
        invalidations=(),
        decision_summary=f"{ticker} 降低战术敞口。",
    )


class FeishuSheetClientTests(unittest.TestCase):
    def test_local_csv_keeps_one_row_per_symbol_for_the_same_day(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "每日决策.csv"
            sink = CsvDecisionSink(path)
            btc = decision("a" * 64, "BTC-USD")
            eth = decision("b" * 64, "ETH-USD")

            sink.write(btc)
            sink.write(eth)
            sink.write(btc)

            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 2)
            self.assertEqual({row["trade_date"] for row in rows}, {"2026-09-11"})
            self.assertEqual({row["ticker"] for row in rows}, {"BTC-USD", "ETH-USD"})

    def test_credentials_require_all_four_safe_values(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(FeishuSheetConfigurationError):
                FeishuSheetCredentials.from_environment()
        with patch.dict(os.environ, {FEISHU_APP_ID_ENV: APP_ID}, clear=True):
            with self.assertRaises(FeishuSheetConfigurationError):
                FeishuSheetCredentials.from_environment()
        with patch.dict(os.environ, ENVIRONMENT, clear=True):
            credentials = FeishuSheetCredentials.from_environment()
        self.assertEqual(credentials.spreadsheet_token, SPREADSHEET_TOKEN)
        self.assertNotIn(APP_SECRET, credentials.fingerprint)

    def test_initializes_header_and_appends_multiple_tickers_once_each(self):
        opener = CapturingOpener(
            [
                token_response(),
                values_response([[None] * len(FEISHU_SHEET_FIELDS)]),
                success_response(),
                find_response(),
                append_response(),
                find_response(),
                append_response(),
            ]
        )
        client = FeishuSheetClient(
            FeishuSheetCredentials(APP_ID, APP_SECRET, SPREADSHEET_TOKEN, SHEET_ID),
            opener=opener,
            clock=lambda: FIXED_NOW,
        )
        btc = decision("a" * 64, "BTC-USD", entry_plan="=FORMULA()")
        eth = decision("b" * 64, "ETH-USD")

        self.assertTrue(client.append(btc))
        self.assertFalse(client.append(btc))
        self.assertTrue(client.append(eth))

        self.assertEqual(len(opener.requests), 7)
        token_request = opener.requests[0][0]
        self.assertNotIn("Authorization", token_request.headers)
        token_payload = json.loads(token_request.data)
        self.assertEqual(token_payload["app_id"], APP_ID)

        authorized = opener.requests[1:]
        self.assertTrue(
            all(
                request.get_header("Authorization") == f"Bearer {TENANT_TOKEN}"
                for request, _timeout in authorized
            )
        )
        header_payload = json.loads(opener.requests[2][0].data)
        self.assertEqual(
            header_payload["valueRange"]["values"], [list(FEISHU_SHEET_FIELDS)]
        )

        append_requests = [
            request
            for request, _timeout in opener.requests
            if "/values_append?" in request.full_url
        ]
        self.assertEqual(len(append_requests), 2)
        rows = [json.loads(request.data)["valueRange"] for request in append_requests]
        self.assertTrue(all(item["range"] == f"{SHEET_ID}!A:R" for item in rows))
        btc_row, eth_row = (item["values"][0] for item in rows)
        self.assertEqual((btc_row[0], btc_row[1]), ("2026-09-11", "BTC-USD"))
        self.assertEqual((eth_row[0], eth_row[1]), ("2026-09-11", "ETH-USD"))
        self.assertEqual(btc_row[FEISHU_SHEET_FIELDS.index("event_id")], "a" * 64)
        self.assertEqual(btc_row[FEISHU_SHEET_FIELDS.index("entry_plan")], "'=FORMULA()")
        self.assertNotIn(TENANT_TOKEN, json.dumps(rows, ensure_ascii=False))
        self.assertNotIn(APP_SECRET, json.dumps(rows, ensure_ascii=False))

    def test_remote_event_id_prevents_duplicate_after_local_state_loss(self):
        existing = "c" * 64
        opener = CapturingOpener(
            [
                token_response(),
                values_response([list(FEISHU_SHEET_FIELDS)]),
                find_response("O2"),
            ]
        )
        client = FeishuSheetClient(
            FeishuSheetCredentials(APP_ID, APP_SECRET, SPREADSHEET_TOKEN, SHEET_ID),
            opener=opener,
            clock=lambda: FIXED_NOW,
        )

        self.assertFalse(client.append(decision(existing, "BTC-USD")))
        self.assertFalse(
            any("/values_append?" in request.full_url for request, _ in opener.requests)
        )
        event_query = opener.requests[-1][0]
        self.assertTrue(event_query.full_url.endswith(f"/sheets/{SHEET_ID}/find"))
        find_payload = json.loads(event_query.data)
        self.assertEqual(find_payload["find"], existing)
        self.assertTrue(find_payload["find_condition"]["match_entire_cell"])

    def test_header_mismatch_and_api_errors_are_safe(self):
        mismatch = CapturingOpener(
            [token_response(), values_response([["unexpected"]])]
        )
        client = FeishuSheetClient(
            FeishuSheetCredentials(APP_ID, APP_SECRET, SPREADSHEET_TOKEN, SHEET_ID),
            opener=mismatch,
            clock=lambda: FIXED_NOW,
        )
        with self.assertRaises(FeishuSheetConfigurationError):
            client.verify()

        rejected = CapturingOpener(
            [FakeResponse({"code": 999, "msg": APP_SECRET})]
        )
        client = FeishuSheetClient(
            FeishuSheetCredentials(APP_ID, APP_SECRET, SPREADSHEET_TOKEN, SHEET_ID),
            opener=rejected,
            clock=lambda: FIXED_NOW,
        )
        with self.assertRaisesRegex(FeishuSheetDeliveryError, "999") as caught:
            client.verify()
        self.assertNotIn(APP_SECRET, str(caught.exception))

        def http_failure(request, timeout):
            raise HTTPError(request.full_url, 403, APP_SECRET, {}, None)

        client = FeishuSheetClient(
            FeishuSheetCredentials(APP_ID, APP_SECRET, SPREADSHEET_TOKEN, SHEET_ID),
            opener=http_failure,
            clock=lambda: FIXED_NOW,
        )
        with self.assertRaisesRegex(FeishuSheetDeliveryError, "HTTP 403") as caught:
            client.verify()
        self.assertNotIn(APP_SECRET, str(caught.exception))

        def coded_http_failure(request, timeout):
            body = json.dumps({"code": 1310213, "msg": APP_SECRET}).encode()
            raise HTTPError(request.full_url, 400, APP_SECRET, {}, io.BytesIO(body))

        client = FeishuSheetClient(
            FeishuSheetCredentials(APP_ID, APP_SECRET, SPREADSHEET_TOKEN, SHEET_ID),
            opener=coded_http_failure,
            clock=lambda: FIXED_NOW,
        )
        with self.assertRaisesRegex(
            FeishuSheetDeliveryError, "尚未授予当前应用文档权限"
        ) as caught:
            client.verify()
        self.assertNotIn(APP_SECRET, str(caught.exception))

        def forbidden_document(request, timeout):
            body = json.dumps({"code": 91403, "msg": APP_SECRET}).encode()
            raise HTTPError(request.full_url, 403, APP_SECRET, {}, io.BytesIO(body))

        client = FeishuSheetClient(
            FeishuSheetCredentials(APP_ID, APP_SECRET, SPREADSHEET_TOKEN, SHEET_ID),
            opener=forbidden_document,
            clock=lambda: FIXED_NOW,
        )
        with self.assertRaisesRegex(
            FeishuSheetDeliveryError, "尚未授予当前应用可编辑权限"
        ) as caught:
            client.verify()
        self.assertNotIn(APP_SECRET, str(caught.exception))


class FeishuSheetManagerTests(unittest.TestCase):
    def test_test_before_enable_and_private_settings(self):
        opener = CapturingOpener(
            [
                token_response(),
                values_response([]),
                success_response(),
            ]
        )
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, ENVIRONMENT, clear=True
        ):
            store = FeishuSheetSettingsStore(Path(directory) / "sheet.json")
            manager = FeishuSheetManager(
                store, opener=opener, clock=lambda: FIXED_NOW
            )
            with self.assertRaises(FeishuSheetConfigurationError):
                manager.set_enabled(True)
            manager.test_connection()
            self.assertTrue(manager.is_ready())
            self.assertTrue(manager.set_enabled(True).enabled)
            self.assertEqual(store.path.stat().st_mode & 0o777, 0o600)
            serialized = store.path.read_text(encoding="utf-8")
            for secret in ENVIRONMENT.values():
                self.assertNotIn(secret, serialized)
            status = manager.status()
            serialized_status = json.dumps(status, ensure_ascii=False)
            for secret in ENVIRONMENT.values():
                self.assertNotIn(secret, serialized_status)


class FeishuSheetControlPanelApiTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, ENVIRONMENT, clear=True)
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
        self.opener = CapturingOpener(
            [
                token_response(),
                values_response([]),
                success_response(),
            ]
        )
        self.manager = FeishuSheetManager(
            FeishuSheetSettingsStore(root / "control" / "sheet.json"),
            opener=self.opener,
            clock=lambda: FIXED_NOW,
        )
        state = PublicationState(root / "publisher.sqlite3")
        publisher = LocalPublisher(
            state,
            (
                CsvDecisionSink(root / "汇总" / "每日决策.csv"),
                LocalMessageSink(root / "汇总" / "消息"),
                FeishuSheetSink(self.manager),
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
            feishu_sheet_manager=self.manager,
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

    def request(self, path: str, body: dict):
        payload = json.dumps(body).encode("utf-8")
        request = Request(
            self.base_url + path,
            data=payload,
            method="POST",
            headers={
                "X-RogueTrader-Control": "1",
                "Content-Type": "application/json",
            },
        )
        with urlopen(request, timeout=2) as response:
            return response.status, json.load(response)

    def test_status_test_enable_and_retry_never_return_credentials(self):
        with urlopen(self.base_url + "/api/status", timeout=2) as response:
            before = json.load(response)
        serialized = json.dumps(before, ensure_ascii=False)
        self.assertTrue(before["publisher"]["feishu_sheet"]["configured"])
        self.assertTrue(before["publisher"]["feishu_sheet"]["test_required"])
        for secret in ENVIRONMENT.values():
            self.assertNotIn(secret, serialized)

        with self.assertRaises(HTTPError) as caught:
            self.request("/api/publisher/feishu-sheet", {"enabled": True})
        self.assertEqual(caught.exception.code, 400)

        status, _ = self.request("/api/publisher/feishu-sheet/test", {})
        self.assertEqual(status, 200)
        status, enabled = self.request(
            "/api/publisher/feishu-sheet", {"enabled": True}
        )
        self.assertEqual(status, 200)
        self.assertTrue(enabled["feishu_sheet"]["enabled"])
        status, retry = self.request("/api/publisher/feishu-sheet/retry", {})
        self.assertEqual(status, 200)
        self.assertEqual(retry["queued"], 0)
        serialized = json.dumps(enabled, ensure_ascii=False)
        for secret in ENVIRONMENT.values():
            self.assertNotIn(secret, serialized)


if __name__ == "__main__":
    unittest.main()
