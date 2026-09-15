from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

import pandas as pd

from roguetrader.backtest.execution_ledger import (
    OrderSpec,
    PlanSpec,
    ScenarioSpec,
    run_backtest,
)
from roguetrader.backtest.signal_quality import evaluate_lifecycles
from roguetrader.dataflows.temporal import TemporalDataError, data_access_session, governed_call
from roguetrader.execution import build_execution_plan
from roguetrader.publisher.models import DecisionRecord
from roguetrader.research_integrity import assess_research_eligibility, build_research_projection


def record(day: str, action: str = "BUY", *, delayed: bool = False) -> DecisionRecord:
    compact = day.replace("-", "")
    generated_day = "2026-08-24" if delayed else day
    return DecisionRecord(
        event_id=(compact[-2:] * 32)[:64],
        run_id=f"{compact}_050000__asof-{day}__legacy__unknown-a01__BTC_USD",
        source_schema_version="2.0",
        generated_at=f"{generated_day}T05:15:00+08:00",
        trade_date=day,
        ticker="BTC-USD",
        action=action,
        action_source="test",
        confidence=None,
        risk_level=None,
        time_horizon=None,
        entry_plan=None,
        stop_loss=None,
        take_profit=None,
        key_reasons=(),
        invalidations=(),
        decision_summary="test",
        runtime_mode="legacy",
        trigger="unknown",
    )


def plan(item: DecisionRecord, previous: str | None = None) -> dict:
    return build_execution_plan(
        {
            "plan_summary": "测试计划",
            "valid_for_hours": 24,
            "previous_plan_id": previous,
            "continuity_action": "replace" if previous else "baseline",
            "change_summary": "替换上一计划" if previous else "首份计划",
            "scenarios": [
                {"scenario_id": "wait", "priority": 1, "trigger": {"type": "immediate"}, "orders": []}
            ],
        },
        decision_event_id=item.event_id,
        ticker=item.ticker,
        analysis_date=item.trade_date,
        action=item.action,
        created_at=(f"{item.trade_date}T05:15:00+08:00" if item.trade_date == "2026-08-20" else item.generated_at),
    )


class TemporalDataTests(unittest.TestCase):
    def test_live_capture_redacts_and_strict_replay_never_calls_network(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory) / "run"
            run.mkdir()
            with data_access_session(run, "2026-09-15", mode="live"):
                result = governed_call(
                    "current_metric",
                    {"ticker": "BTC", "api_key": "private"},
                    "snapshot_required",
                    lambda: "captured-result",
                )
            self.assertEqual(result, "captured-result")
            snapshot = next((run / "数据快照").glob("*.json"))
            self.assertNotIn("private", snapshot.read_text(encoding="utf-8"))
            calls = 0

            def forbidden() -> str:
                nonlocal calls
                calls += 1
                return "network"

            replay = Path(directory) / "replay"
            replay.mkdir()
            with data_access_session(replay, "2026-09-15", mode="historical_strict", replay_snapshot_dir=run / "数据快照"):
                value = governed_call(
                    "current_metric",
                    {"ticker": "BTC", "api_key": "private"},
                    "snapshot_required",
                    forbidden,
                )
            self.assertEqual(value, "captured-result")
            self.assertEqual(calls, 0)

    def test_strict_historical_current_source_requires_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            with self.assertRaisesRegex(TemporalDataError, "缺少数据快照"):
                with data_access_session(run, "2026-08-20", mode="historical_strict"):
                    governed_call("current_metric", {}, "snapshot_required", lambda: "unsafe")


class ResearchProjectionTests(unittest.TestCase):
    def test_new_governed_run_requires_complete_lineage(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            rec = record("2026-08-19")
            (run / "运行清单.json").write_text(
                json.dumps({"started_at": "2026-08-19T05:00:00+08:00", "data_governance_required": True}),
                encoding="utf-8",
            )
            eligibility = assess_research_eligibility(rec, run)
            self.assertFalse(eligibility.eligible)
            self.assertEqual(eligibility.reason_code, "required_data_lineage_missing")

    def test_delayed_repair_is_excluded_and_lineage_skips_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            records = [record("2026-08-19", "OVERWEIGHT"), record("2026-08-20", "UNDERWEIGHT", delayed=True), record("2026-08-21", "BUY")]
            source_plans = []
            previous = None
            for rec in records:
                run = root / rec.run_id
                run.mkdir()
                (run / "运行清单.json").write_text(json.dumps({"started_at": f"{rec.trade_date}T05:00:00+08:00"}), encoding="utf-8")
                current = plan(rec, previous)
                source_plans.append(current)
                previous = current["plan_id"]
            items = tuple(SimpleNamespace(run_dir=root / rec.run_id, record=rec, existing_plan=current) for rec, current in zip(records, source_plans))
            report = build_research_projection(items, output_root=root / "research")
            self.assertEqual(report["excluded_decisions"], 1)
            projection = json.loads((root / "research" / "研究投影.json").read_text(encoding="utf-8"))
            included = [item for item in projection["plan_chain"] if item["status"] == "included"]
            self.assertTrue(included[0]["validity_extended_due_to_excluded_gap"])
            self.assertEqual(included[1]["previous_research_plan_id"], included[0]["research_plan_id"])
            self.assertNotEqual(included[1]["previous_research_plan_id"], source_plans[1]["plan_id"])

    def test_signal_quality_uses_non_overlapping_plan_lifecycles(self):
        timeline = pd.date_range("2026-08-19T00:00:00Z", periods=48, freq="1h")
        prices = [100.0 - number * 10 / 23 for number in range(24)] + [90.0 + number * 5 / 23 for number in range(24)]
        market = pd.DataFrame(
            {
                "open": prices,
                "high": [value + 0.5 for value in prices],
                "low": [value - 0.5 for value in prices],
                "close": prices,
                "volume": [1.0] * 48,
            },
            index=timeline,
        )
        sell = OrderSpec(
            event_id="sell-event",
            order_id="sell_now",
            sequence=1,
            side="sell",
            intent="exit",
            order_type="market",
            size_basis="current_position_pct",
            size_value=1.0,
            limit_price=None,
            stop_price=None,
            take_profit=None,
            stop_loss=None,
            time_in_force="GTC",
            after_order_id=None,
        )
        buy = OrderSpec(
            event_id="buy-event",
            order_id="buy_now",
            sequence=1,
            side="buy",
            intent="open",
            order_type="market",
            size_basis="available_cash_pct",
            size_value=1.0,
            limit_price=None,
            stop_price=None,
            take_profit=92.0,
            stop_loss=88.0,
            time_in_force="GTC",
            after_order_id=None,
        )
        plans = (
            PlanSpec(
                plan_id="sell-plan",
                decision_event_id="sell-decision",
                analysis_date="2026-08-19",
                ticker="BTC-USD",
                action="SELL",
                generated_at=timeline[0],
                generated_at_source="test",
                valid_until=timeline[-1] + pd.Timedelta(hours=1),
                max_position_pct=1.0,
                max_order_cash_pct=1.0,
                scenarios=(ScenarioSpec("sell", 1, None, "immediate", None, None, "立即", (sell,)),),
            ),
            PlanSpec(
                plan_id="buy-plan",
                decision_event_id="buy-decision",
                analysis_date="2026-08-20",
                ticker="BTC-USD",
                action="BUY",
                generated_at=timeline[24],
                generated_at_source="test",
                valid_until=timeline[-1] + pd.Timedelta(hours=1),
                max_position_pct=1.0,
                max_order_cash_pct=1.0,
                scenarios=(ScenarioSpec("buy", 1, None, "immediate", None, None, "立即", (buy,)),),
            ),
        )
        replay = run_backtest(
            plans,
            market,
            initial_balance=10_000.0,
            fee_rate=0.0,
            slippage_rate=0.0,
        )
        evidence = {
            "metrics": replay["metrics"],
            "statuses": replay["order_status"].fillna(""),
            "fills": replay["fills"].fillna(""),
            "curve": replay["equity"],
            "condition_audit": replay["condition_audit"],
        }
        lifecycles, orders, metrics = evaluate_lifecycles(plans, market, evidence)
        self.assertEqual(list(lifecycles["lifecycle_hours"]), [24, 24])
        self.assertTrue((lifecycles["lifecycle_value_added_return"] > 0).all())
        self.assertEqual(metrics["executed_plan_count"], 2)
        self.assertEqual(metrics["take_profit_exit_count"], 1)
        self.assertEqual(int(orders["filled"].sum()), 2)


if __name__ == "__main__":
    unittest.main()
