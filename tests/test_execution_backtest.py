from __future__ import annotations

import csv
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from roguetrader.backtest.execution_ledger import (
    PlanSpec,
    load_execution_ledger,
    run_backtest,
    select_evaluation_window,
    write_backtest_bundle,
)
from roguetrader.execution import build_execution_plan
from roguetrader.publisher.execution_csv import (
    EXECUTION_CSV_FIELDS,
    execution_plan_rows,
    resolve_order_generated_at,
)
from roguetrader.publisher.models import DecisionRecord
from roguetrader.publisher.models import PublicationError


def decision_record(*, generated_at: str = "2026-09-11T05:15:00+08:00") -> DecisionRecord:
    return DecisionRecord(
        event_id="a" * 64,
        run_id=(
            "20260911_050000__asof-20260911__prod__scheduled-a01__BTC_USD"
        ),
        source_schema_version="1.0",
        generated_at=generated_at,
        trade_date="2026-09-11",
        ticker="BTC-USD",
        action="UNDERWEIGHT",
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
        runtime_mode="prod",
        trigger="scheduled",
    )


def plan_for(record: DecisionRecord) -> dict:
    return build_execution_plan(
        {
            "plan_summary": "先降低一半风险，其余条件等待确认。",
            "valid_for_hours": 24,
            "risk_limits": {
                "max_position_pct": 1,
                "max_order_cash_pct": 1,
            },
            "scenarios": [
                {
                    "scenario_id": "reduce_now",
                    "priority": 1,
                    "trigger": {"type": "immediate"},
                    "orders": [
                        {
                            "order_id": "sell_half",
                            "sequence": 1,
                            "side": "sell",
                            "intent": "reduce",
                            "order_type": "market",
                            "size": {
                                "basis": "current_position_pct",
                                "value": 0.5,
                            },
                            "time_in_force": "GTC",
                        }
                    ],
                },
                {
                    "scenario_id": "manual_buy",
                    "priority": 2,
                    "trigger": {
                        "type": "manual_confirmation",
                        "condition": "等待外部指标确认",
                    },
                    "orders": [
                        {
                            "order_id": "buy_after_confirmation",
                            "sequence": 1,
                            "side": "buy",
                            "intent": "increase",
                            "order_type": "market",
                            "size": {
                                "basis": "available_cash_pct",
                                "value": 0.5,
                            },
                            "time_in_force": "GTC",
                        }
                    ],
                },
            ],
        },
        decision_event_id=record.event_id,
        ticker=record.ticker,
        analysis_date=record.trade_date,
        action=record.action,
        created_at=record.generated_at,
    )


class ExecutionLedgerBacktestTests(unittest.TestCase):
    def test_fixed_window_requires_exact_hourly_coverage(self):
        plan = PlanSpec(
            plan_id="window-plan",
            decision_event_id="window-decision",
            analysis_date="2026-09-01",
            ticker="BTC-USD",
            action="HOLD",
            generated_at=pd.Timestamp("2026-09-01T00:00:00Z"),
            generated_at_source="decision_generated_at",
            valid_until=pd.Timestamp("2026-09-03T00:00:00Z"),
            max_position_pct=1.0,
            max_order_cash_pct=1.0,
            scenarios=(),
        )
        market = pd.DataFrame(
            {"open": 100, "high": 101, "low": 99, "close": 100, "volume": 1},
            index=pd.date_range("2026-09-01T00:00:00Z", periods=48, freq="1h"),
        )
        plans, selected, metadata = select_evaluation_window(
            (plan,), market, window_start="2026-09-01T00:00:00Z", window_days=2
        )
        self.assertEqual(len(plans), 1)
        self.assertEqual(len(selected), 48)
        self.assertEqual(metadata["requested_window_hours"], 48)

    def test_fixed_window_aligns_to_complete_hour_grid(self):
        plan = PlanSpec(
            plan_id="window-plan",
            decision_event_id="window-decision",
            analysis_date="2026-09-01",
            ticker="BTC-USD",
            action="HOLD",
            generated_at=pd.Timestamp("2026-09-01T00:26:03Z"),
            generated_at_source="decision_generated_at",
            valid_until=pd.Timestamp("2026-09-03T01:00:00Z"),
            max_position_pct=1.0,
            max_order_cash_pct=1.0,
            scenarios=(),
        )
        market = pd.DataFrame(
            {"open": 100, "high": 101, "low": 99, "close": 100, "volume": 1},
            index=pd.date_range("2026-09-01T00:00:00Z", periods=50, freq="1h"),
        )
        _, selected, metadata = select_evaluation_window(
            (plan,), market, window_start="2026-09-01T00:26:03Z", window_days=2
        )
        self.assertEqual(len(selected), 48)
        self.assertEqual(selected.index[0], pd.Timestamp("2026-09-01T01:00:00Z"))
        self.assertEqual(selected.index[-1], pd.Timestamp("2026-09-03T00:00:00Z"))
        self.assertEqual(metadata["bar_window_end_exclusive"], "2026-09-03T01:00:00+00:00")

    def test_order_time_uses_real_timestamp_and_rejects_delayed_repair(self):
        record = decision_record()
        self.assertEqual(
            resolve_order_generated_at(record),
            ("2026-09-11T05:15:00+08:00", "decision_generated_at"),
        )

        repaired = replace(
            record,
            generated_at="2026-09-15T05:15:00+08:00",
            runtime_mode="legacy",
            trigger="unknown",
        )
        with self.assertRaisesRegex(PublicationError, "禁止把委托时间回填"):
            resolve_order_generated_at(repaired)

        slow_scheduled_run = replace(
            record,
            generated_at="2026-09-11T12:15:00+08:00",
        )
        self.assertEqual(
            resolve_order_generated_at(slow_scheduled_run),
            ("2026-09-11T12:15:00+08:00", "decision_generated_at"),
        )

    def test_backtest_uses_next_complete_bar_and_skips_manual_condition(self):
        record = decision_record()
        plan = plan_for(record)
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "orders.csv"
            with ledger.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=EXECUTION_CSV_FIELDS)
                writer.writeheader()
                writer.writerows(execution_plan_rows(plan, record))
            plans = load_execution_ledger(ledger, "BTC-USD")
            market = pd.DataFrame(
                [
                    {"open": 200, "high": 200, "low": 50, "close": 100, "volume": 1},
                    {"open": 100, "high": 105, "low": 95, "close": 100, "volume": 1},
                    {"open": 90, "high": 95, "low": 85, "close": 90, "volume": 1},
                ],
                index=pd.to_datetime(
                    [
                        "2026-09-10T21:00:00Z",
                        "2026-09-10T22:00:00Z",
                        "2026-09-10T23:00:00Z",
                    ]
                ),
            )

            result = run_backtest(
                plans,
                market,
                initial_balance=10_000,
                fee_rate=0,
                slippage_rate=0,
            )

            metrics = result["metrics"]
            self.assertEqual(metrics["fill_count"], 1)
            self.assertAlmostEqual(metrics["total_return"], -0.05)
            self.assertAlmostEqual(metrics["benchmark_return"], -0.10)
            self.assertEqual(metrics["ohlcv_evaluation_coverage"], 0.5)
            statuses = set(result["order_status"]["status"])
            self.assertEqual(statuses, {"filled", "not_evaluated_manual_confirmation"})
            self.assertEqual(result["fills"].iloc[0]["time"], "2026-09-10T22:00:00+00:00")

    def test_report_bundle_contains_auditable_outputs(self):
        record = decision_record()
        plan = plan_for(record)
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "orders.csv"
            with ledger.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=EXECUTION_CSV_FIELDS)
                writer.writeheader()
                writer.writerows(execution_plan_rows(plan, record))
            plans = load_execution_ledger(ledger, "BTC-USD")
            market = pd.DataFrame(
                [{"open": 100, "high": 101, "low": 99, "close": 100, "volume": 1}],
                index=pd.to_datetime(["2026-09-10T22:00:00Z"]),
            )
            result = run_backtest(
                plans,
                market,
                initial_balance=10_000,
                fee_rate=0,
                slippage_rate=0,
            )
            quality = {
                "source": "test.csv",
                "start": "2026-09-10T22:00:00+00:00",
                "end": "2026-09-10T22:00:00+00:00",
                "confirmed_rows": 1,
                "unconfirmed_rows_filtered": 0,
                "coverage_ratio": 1.0,
            }
            target = write_backtest_bundle(
                result, quality, Path(directory) / "output", "BTC-USD"
            )

            expected = {
                "回测指标.json",
                "权益曲线.csv",
                "成交明细.csv",
                "委托回测状态.csv",
                "回测报告.md",
                "回测报告.html",
            }
            self.assertEqual({path.name for path in target.iterdir()}, expected)
            metrics = json.loads((target / "回测指标.json").read_text())
            self.assertEqual(metrics["fill_count"], 1)
            self.assertIn("OHLCV", (target / "回测报告.md").read_text())
            html_report = (target / "回测报告.html").read_text()
            self.assertIn("<svg", html_report)
            self.assertNotIn("**", html_report)


if __name__ == "__main__":
    unittest.main()
