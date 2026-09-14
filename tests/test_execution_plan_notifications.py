from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from roguetrader.output_paths import make_run_output_paths
from roguetrader.publisher.execution_plan_message import (
    load_execution_plan,
    render_execution_plan_message,
)
from roguetrader.publisher.feishu import (
    FEISHU_SECRET_ENV,
    FEISHU_WEBHOOK_ENV,
    FeishuNotificationManager,
    FeishuSettingsStore,
    FeishuWebhookSink,
)
from roguetrader.publisher.loader import load_completed_run
from roguetrader.publisher.service import LocalPublisher
from roguetrader.publisher.sinks import CsvDecisionSink
from roguetrader.publisher.state import PublicationState
from roguetrader.run_outputs import write_run_outputs


WEBHOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/test-hook-id"
SECRET = "test-signing-secret"
FIXED_NOW = datetime(2026, 9, 13, 22, 30, tzinfo=timezone(timedelta(hours=8)))


class FakeResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def getcode(self):
        return self.status

    def read(self, _size=-1):
        return b'{"code":0}'


class CapturingOpener:
    def __init__(self):
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append((request, timeout))
        return FakeResponse()


def execution_draft() -> dict:
    return {
        "plan_summary": "先降低风险敞口，再等待条件型买入。",
        "valid_for_hours": 72,
        "scenarios": [
            {
                "scenario_id": "reduce_now",
                "priority": 1,
                "trigger": {"type": "immediate"},
                "orders": [
                    {
                        "order_id": "sell_now",
                        "sequence": 1,
                        "side": "sell",
                        "intent": "reduce",
                        "order_type": "market",
                        "size": {"basis": "current_position_pct", "value": 0.35},
                    }
                ],
            },
            {
                "scenario_id": "rebound_sell",
                "priority": 2,
                "trigger": {"type": "immediate"},
                "orders": [
                    {
                        "order_id": "sell_78600",
                        "sequence": 1,
                        "side": "sell",
                        "intent": "reduce",
                        "order_type": "limit",
                        "size": {"basis": "current_position_pct", "value": 0.1},
                        "limit_price": 78600,
                        "time_in_force": "GTC",
                    },
                    {
                        "order_id": "sell_80500",
                        "sequence": 2,
                        "side": "sell",
                        "intent": "reduce",
                        "order_type": "limit",
                        "size": {"basis": "current_position_pct", "value": 0.1},
                        "limit_price": 80500,
                        "time_in_force": "GTC",
                    },
                ],
            },
            {
                "scenario_id": "fear_buy",
                "priority": 3,
                "trigger": {
                    "type": "manual_confirmation",
                    "condition": "Price within 70100-70900 AND Fear & Greed <25",
                },
                "orders": [
                    {
                        "order_id": "buy_fear",
                        "sequence": 1,
                        "side": "buy",
                        "intent": "increase",
                        "order_type": "limit",
                        "size": {"basis": "available_cash_pct", "value": 0.1},
                        "limit_price": 70900,
                        "stop_loss": 67200,
                        "time_in_force": "GTC",
                    }
                ],
            },
        ],
    }


def completed_run(results_root: Path, *, with_plan: bool = True) -> Path:
    paths = make_run_output_paths(
        results_root,
        "BTC-USD",
        "20260913_223000",
        analysis_date="2026-09-13",
        runtime_mode="production",
        trigger="scheduled",
    )
    final_state = {
        "company_of_interest": "BTC-USD",
        "trade_date": "2026-09-13",
        "final_trade_decision": "UNDERWEIGHT and reduce exposure.",
    }
    write_run_outputs(
        paths=paths,
        ticker="BTC-USD",
        trade_date="2026-09-13",
        final_state=final_state,
        decision="UNDERWEIGHT",
        execution_plan_requested=with_plan,
        execution_plan_draft=execution_draft() if with_plan else None,
    )
    return paths.root


class ExecutionPlanNotificationTests(unittest.TestCase):
    def test_message_explains_state_meaning_and_exact_orders(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "my_results"
            run_dir = completed_run(root)
            record = load_completed_run(run_dir)
            plan = load_execution_plan(root, record)
            self.assertIsNotNone(plan)

            message = render_execution_plan_message(plan, record)

            self.assertIn("**1 · 立即执行意图**", message.text)
            self.assertIn("**2–3 · 现在就可挂出的 GTC 限价单**", message.text)
            self.assertIn("含义：现在先减仓 35%", message.text)
            self.assertIn("等待复合条件", message.text)
            self.assertIn("0.1 × X_CASH", message.text)
            self.assertIn("并非全部同时挂单", message.text)
            self.assertEqual(message.source_file, "执行计划.json")

    def test_plan_and_decision_share_one_delivery_and_one_card(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {FEISHU_WEBHOOK_ENV: WEBHOOK, FEISHU_SECRET_ENV: SECRET},
            clear=True,
        ):
            root = Path(directory) / "my_results"
            run_dir = completed_run(root)
            record = load_completed_run(run_dir)
            opener = CapturingOpener()
            manager = FeishuNotificationManager(
                FeishuSettingsStore(Path(directory) / "feishu.json"),
                opener=opener,
                clock=lambda: FIXED_NOW,
            )
            manager.send_test()
            manager.set_enabled(True)
            state = PublicationState(Path(directory) / "publisher.sqlite3")
            publisher = LocalPublisher(
                state,
                (
                    CsvDecisionSink(root / "汇总" / "每日决策.csv"),
                    FeishuWebhookSink(manager, root),
                ),
                clock=lambda: FIXED_NOW,
            )

            result = publisher.publish_run(run_dir)

            self.assertTrue(result.successful)
            self.assertEqual(result.deliveries["feishu"], "success")
            self.assertNotIn("feishu_execution_plan", result.deliveries)
            self.assertEqual(len(opener.requests), 2)  # test + merged card
            plan_payload = json.loads(opener.requests[-1][0].data)
            self.assertIn(
                "执行指令与决策",
                plan_payload["card"]["header"]["title"]["content"],
            )
            sections = [
                element.get("text", {}).get("content", "")
                for element in plan_payload["card"]["elements"]
                if element.get("tag") == "div" and "text" in element
            ]
            self.assertIn("**执行指令**", sections[0])
            self.assertIn("**决策摘要**", sections[1])
            self.assertNotIn(SECRET, json.dumps(plan_payload, ensure_ascii=False))

    def test_missing_plan_is_a_successful_noop(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {FEISHU_WEBHOOK_ENV: WEBHOOK, FEISHU_SECRET_ENV: SECRET},
            clear=True,
        ):
            root = Path(directory) / "my_results"
            run_dir = completed_run(root, with_plan=False)
            record = load_completed_run(run_dir)
            opener = CapturingOpener()
            manager = FeishuNotificationManager(
                FeishuSettingsStore(Path(directory) / "feishu.json"),
                opener=opener,
                clock=lambda: FIXED_NOW,
            )
            manager.send_test()

            FeishuWebhookSink(manager, root, automatic=False).write(record)

            self.assertEqual(len(opener.requests), 2)
            payload = json.loads(opener.requests[-1][0].data)
            self.assertIn(
                "每日决策", payload["card"]["header"]["title"]["content"]
            )


if __name__ == "__main__":
    unittest.main()
