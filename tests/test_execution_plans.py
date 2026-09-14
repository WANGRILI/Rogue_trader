from __future__ import annotations

import json
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from roguetrader.execution import (
    ExecutionPlanner,
    PlanValidationError,
    PlannerOutputError,
    RuntimeState,
    bind_execution_plan,
    build_execution_plan,
    validate_execution_plan,
)
from roguetrader.execution.planner import find_previous_execution_plan
from roguetrader.execution.__main__ import main as bind_main
from roguetrader.output_paths import make_run_output_paths
from roguetrader.run_outputs import write_run_outputs


EVENT_ID = "a" * 64
CREATED_AT = "2026-09-13T05:10:00+08:00"


def execution_draft() -> dict:
    return {
        "plan_summary": "先执行基础减仓，跌破条件触发后继续处理。",
        "valid_for_hours": 24,
        "risk_limits": {
            "max_position_pct": 1.0,
            "max_order_cash_pct": 0.25,
        },
        "scenarios": [
            {
                "scenario_id": "base_rebalance",
                "priority": 1,
                "exclusive_group": None,
                "trigger": {"type": "immediate"},
                "orders": [
                    {
                        "order_id": "reduce_1",
                        "sequence": 1,
                        "side": "sell",
                        "intent": "reduce",
                        "order_type": "market",
                        "size": {"basis": "current_position_pct", "value": 0.3},
                        "take_profit": None,
                        "stop_loss": None,
                        "time_in_force": "IOC",
                        "after_order_id": None,
                    },
                    {
                        "order_id": "reduce_2",
                        "sequence": 2,
                        "side": "sell",
                        "intent": "reduce",
                        "order_type": "limit",
                        "size": {"basis": "current_position_pct", "value": 0.2},
                        "limit_price": 110.0,
                        "take_profit": None,
                        "stop_loss": None,
                        "time_in_force": "GTC",
                        "after_order_id": "reduce_1",
                    },
                ],
            },
            {
                "scenario_id": "lower_entry",
                "priority": 2,
                "exclusive_group": "price_branch",
                "trigger": {"type": "last_price", "operator": "lte", "value": 90.0},
                "orders": [
                    {
                        "order_id": "entry_1",
                        "sequence": 1,
                        "side": "buy",
                        "intent": "increase",
                        "order_type": "limit",
                        "size": {"basis": "available_cash_pct", "value": 0.1},
                        "limit_price": 88.0,
                        "take_profit": 105.0,
                        "stop_loss": 80.0,
                        "time_in_force": "GTC",
                        "after_order_id": None,
                    }
                ],
            },
        ],
    }


def built_plan() -> dict:
    return build_execution_plan(
        execution_draft(),
        decision_event_id=EVENT_ID,
        ticker="BTC-USD",
        analysis_date="2026-09-13",
        action="UNDERWEIGHT",
        created_at=CREATED_AT,
    )


class FakeResponse:
    def __init__(self, content: str):
        self.content = content


class FakeLlm:
    def __init__(self, content: str):
        self.content = content
        self.prompts: list[str] = []

    def invoke(self, prompt: str):
        self.prompts.append(prompt)
        return FakeResponse(self.content)


class ExecutionPlanTests(unittest.TestCase):
    def test_plan_is_spot_paper_only_and_requires_no_daily_user_input(self):
        plan = built_plan()

        self.assertEqual(plan["instrument_type"], "spot")
        self.assertEqual(plan["execution_mode"], "paper")
        self.assertFalse(plan["auto_submit"])
        self.assertFalse(plan["daily_user_input_required"])
        self.assertEqual(
            plan["symbolic_parameters"]["X_POSITION"],
            "执行时的当前现货持仓数量",
        )
        self.assertEqual(
            plan["scenarios"][0]["orders"][0]["size_expression"],
            "0.3 × X_POSITION",
        )
        self.assertEqual(
            plan["scenarios"][1]["orders"][0]["size_expression"],
            "0.1 × X_CASH",
        )
        # These inputs belong only to the optional numeric binding tool.
        self.assertEqual(
            plan["optional_numeric_binding"]["required_when_used"],
            ["position_qty", "available_cash", "last_price"],
        )
        self.assertEqual(len(plan["plan_id"]), 64)
        self.assertEqual(validate_execution_plan(plan), plan)

    def test_binding_resolves_position_pct_and_accounts_for_open_sell_qty(self):
        instance = bind_execution_plan(
            built_plan(),
            RuntimeState.from_dict(
                {
                    "position_qty": "1",
                    "available_cash": "1000",
                    "last_price": "100",
                    "open_sell_qty": "0.1",
                }
            ),
            bound_at="2026-09-13T05:15:00+08:00",
        )

        self.assertEqual(instance["status"], "ready")
        self.assertTrue(instance["ready_for_simulation"])
        first, second = instance["scenarios"][0]["orders"]
        self.assertEqual(first["quantity"], "0.2")
        self.assertEqual(first["status"], "ready")
        self.assertEqual(second["status"], "waiting_dependency")
        self.assertFalse(instance["scenarios"][1]["triggered"])
        self.assertFalse(instance["scenarios"][1]["selected"])
        self.assertEqual(
            instance["scenarios"][1]["orders"][0]["status"],
            "waiting_trigger",
        )
        self.assertFalse(instance["auto_submit"])

    def test_exclusive_scenarios_select_only_the_highest_priority_match(self):
        draft = execution_draft()
        draft["scenarios"] = [
            {
                "scenario_id": "first",
                "priority": 1,
                "exclusive_group": "direction",
                "trigger": {"type": "immediate"},
                "orders": [],
            },
            {
                "scenario_id": "second",
                "priority": 2,
                "exclusive_group": "direction",
                "trigger": {"type": "immediate"},
                "orders": [],
            },
        ]
        plan = build_execution_plan(
            draft,
            decision_event_id=EVENT_ID,
            ticker="BTC-USD",
            analysis_date="2026-09-13",
            action="HOLD",
            created_at=CREATED_AT,
        )
        instance = bind_execution_plan(
            plan,
            {"position_qty": 0, "available_cash": 1000, "last_price": 100},
            bound_at="2026-09-13T05:15:00+08:00",
        )

        self.assertTrue(instance["scenarios"][0]["selected"])
        self.assertTrue(instance["scenarios"][1]["triggered"])
        self.assertFalse(instance["scenarios"][1]["selected"])
        self.assertIn("更高优先级", instance["scenarios"][1]["reason"])

    def test_non_realtime_condition_waits_for_confirmation_without_more_inputs(self):
        draft = execution_draft()
        draft["scenarios"] = [
            {
                "scenario_id": "daily_close_break",
                "priority": 1,
                "trigger": {
                    "type": "manual_confirmation",
                    "condition": "Daily close is below the stated support level.",
                },
                "orders": draft["scenarios"][0]["orders"][:1],
            }
        ]
        plan = build_execution_plan(
            draft,
            decision_event_id=EVENT_ID,
            ticker="BTC-USD",
            analysis_date="2026-09-13",
            action="UNDERWEIGHT",
            created_at=CREATED_AT,
        )
        instance = bind_execution_plan(
            plan,
            {"position_qty": 1, "available_cash": 1000, "last_price": 100},
            bound_at="2026-09-13T05:15:00+08:00",
        )

        self.assertEqual(instance["status"], "waiting")
        self.assertEqual(
            instance["scenarios"][0]["orders"][0]["status"],
            "waiting_confirmation",
        )

    def test_missing_runtime_value_and_spot_short_are_rejected(self):
        with self.assertRaisesRegex(PlanValidationError, "last_price"):
            RuntimeState.from_dict({"position_qty": 1, "available_cash": 100})

        draft = execution_draft()
        order = draft["scenarios"][0]["orders"][0]
        order.update({"side": "sell", "intent": "open"})
        with self.assertRaisesRegex(PlanValidationError, "空头"):
            build_execution_plan(
                draft,
                decision_event_id=EVENT_ID,
                ticker="BTC-USD",
                analysis_date="2026-09-13",
                action="SELL",
                created_at=CREATED_AT,
            )

    def test_plan_identity_detects_mutation_and_expiry_is_safe(self):
        plan = built_plan()
        plan["plan_summary"] = "mutated"
        with self.assertRaisesRegex(PlanValidationError, "plan_id"):
            validate_execution_plan(plan)

        plan = built_plan()
        plan["valid_until"] = "2026-09-20T05:10:00+08:00"
        with self.assertRaisesRegex(PlanValidationError, "valid_for_hours"):
            validate_execution_plan(plan)

        instance = bind_execution_plan(
            built_plan(),
            {"position_qty": 1, "available_cash": 1000, "last_price": 100},
            bound_at="2026-09-15T05:11:00+08:00",
        )
        self.assertEqual(instance["status"], "expired")
        self.assertFalse(instance["ready_for_simulation"])

    def test_execution_planner_accepts_fenced_json_and_rejects_unknown_fields(self):
        llm = FakeLlm("```json\n" + json.dumps(execution_draft()) + "\n```")
        result = ExecutionPlanner(llm).create_draft(
            ticker="BTC-USD",
            analysis_date="2026-09-13",
            action="UNDERWEIGHT",
            final_decision_text="Reduce exposure at stated levels.",
        )
        self.assertEqual(result["scenarios"][0]["scenario_id"], "base_rebalance")
        self.assertIn("SPOT and PAPER only", llm.prompts[0])

        bad = execution_draft()
        bad["account_balance"] = 1000
        with self.assertRaises(PlannerOutputError):
            ExecutionPlanner(FakeLlm(json.dumps(bad))).create_draft(
                ticker="BTC-USD",
                analysis_date="2026-09-13",
                action="HOLD",
                final_decision_text="Hold.",
            )

    def test_previous_plan_is_linked_without_account_state(self):
        previous = built_plan()
        draft = execution_draft()
        draft["continuity_action"] = "amend"
        draft["change_summary"] = "保留减仓主线，调整条件买入。"
        llm = FakeLlm(json.dumps(draft))
        result = ExecutionPlanner(llm).create_draft(
            ticker="BTC-USD",
            analysis_date="2026-09-14",
            action="UNDERWEIGHT",
            final_decision_text="Continue to reduce exposure.",
            previous_plan=previous,
        )

        self.assertEqual(result["previous_plan_id"], previous["plan_id"])
        self.assertEqual(result["continuity_action"], "amend")
        self.assertIn("保留减仓", result["change_summary"])
        self.assertIn("complete authoritative snapshot", llm.prompts[0])

    def test_find_previous_plan_stays_in_same_runtime_lane(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prod_dir = root / "运行结果" / "prod"
            dev_dir = root / "运行结果" / "dev"
            prod_dir.mkdir(parents=True)
            dev_dir.mkdir(parents=True)
            plan = built_plan()
            for run_dir, lane in ((prod_dir, "prod"), (dev_dir, "dev")):
                (run_dir / "执行计划.json").write_text(
                    json.dumps(plan, ensure_ascii=False), encoding="utf-8"
                )
                (run_dir / "运行清单.json").write_text(
                    json.dumps({"status": "completed", "runtime_mode": lane}),
                    encoding="utf-8",
                )

            found = find_previous_execution_plan(
                root,
                ticker="BTC-USD",
                analysis_date="2026-09-14",
                runtime_mode="development",
            )

            self.assertIsNotNone(found)
            self.assertEqual(found["plan_id"], plan["plan_id"])

    def test_run_output_adds_optional_execution_plan_without_changing_decision(self):
        final_state = {
            "company_of_interest": "BTC-USD",
            "trade_date": "2026-09-13",
            "market_report": "market",
            "sentiment_report": "",
            "news_report": "",
            "fundamentals_report": "",
            "onchain_report": "",
            "investment_debate_state": {"judge_decision": "research"},
            "trader_investment_plan": "trader",
            "risk_debate_state": {"judge_decision": "risk"},
            "investment_plan": "plan",
            "final_trade_decision": "UNDERWEIGHT with staged reductions.",
        }
        with tempfile.TemporaryDirectory() as tmp:
            paths = make_run_output_paths(Path(tmp), "BTC-USD", "20260913_051000")
            write_run_outputs(
                paths=paths,
                ticker="BTC-USD",
                trade_date="2026-09-13",
                final_state=final_state,
                decision="UNDERWEIGHT",
                execution_plan_requested=True,
                execution_plan_draft=execution_draft(),
            )

            self.assertTrue(paths.execution_plan_path.is_file())
            self.assertFalse(paths.execution_instance_path.exists())
            index = json.loads(paths.index_path.read_text(encoding="utf-8"))
            self.assertEqual(index["execution_plan"]["status"], "parameterized")
            self.assertEqual(index["files"]["execution_plan"], "执行计划.json")
            decision = json.loads(paths.decision_path.read_text(encoding="utf-8"))
            self.assertEqual(decision["action"], "UNDERWEIGHT")

    def test_invalid_optional_plan_does_not_invalidate_completed_research(self):
        final_state = {
            "company_of_interest": "BTC-USD",
            "trade_date": "2026-09-13",
            "final_trade_decision": "HOLD",
        }
        invalid = execution_draft()
        invalid["unknown"] = True
        with tempfile.TemporaryDirectory() as tmp:
            paths = make_run_output_paths(Path(tmp), "BTC-USD", "20260913_051001")
            write_run_outputs(
                paths=paths,
                ticker="BTC-USD",
                trade_date="2026-09-13",
                final_state=final_state,
                decision="HOLD",
                execution_plan_requested=True,
                execution_plan_draft=invalid,
            )

            self.assertTrue(paths.index_path.is_file())
            self.assertTrue(paths.decision_path.is_file())
            self.assertFalse(paths.execution_plan_path.exists())
            index = json.loads(paths.index_path.read_text(encoding="utf-8"))
            self.assertEqual(index["execution_plan"]["status"], "failed")
            self.assertEqual(
                index["execution_plan"]["error_type"], "PlanValidationError"
            )
            manifest = json.loads(paths.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "completed")

    def test_bind_cli_writes_instance_and_updates_index(self):
        current = datetime.now().astimezone().isoformat(timespec="seconds")
        plan = build_execution_plan(
            execution_draft(),
            decision_event_id=EVENT_ID,
            ticker="BTC-USD",
            analysis_date="2026-09-13",
            action="UNDERWEIGHT",
            created_at=current,
        )
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "执行计划.json").write_text(
                json.dumps(plan), encoding="utf-8"
            )
            (run_dir / "运行索引.json").write_text(
                json.dumps({"files": {"decision": "最终决策.json"}}),
                encoding="utf-8",
            )
            argv = [
                "roguetrader-execution",
                str(run_dir),
                "--position-qty",
                "1",
                "--available-cash",
                "1000",
                "--last-price",
                "100",
            ]
            with patch.object(sys, "argv", argv), redirect_stdout(io.StringIO()):
                bind_main()

            instance = json.loads(
                (run_dir / "执行实例.json").read_text(encoding="utf-8")
            )
            self.assertEqual(instance["execution_mode"], "paper")
            index = json.loads(
                (run_dir / "运行索引.json").read_text(encoding="utf-8")
            )
            self.assertEqual(index["files"]["execution_instance"], "执行实例.json")


if __name__ == "__main__":
    unittest.main()
