from __future__ import annotations

import csv
from datetime import datetime, timedelta
import json
from pathlib import Path
import tempfile
import unittest
from zoneinfo import ZoneInfo

from roguetrader.execution.backfill import (
    HistoricalPlanBackfillError,
    backfill_historical_execution_plans,
    discover_historical_decisions,
    initial_previous_plans,
)
from roguetrader.output_paths import make_run_output_paths
from roguetrader.publisher.execution_csv import (
    EXECUTION_CSV_FIELDS,
    EXECUTION_CSV_FIELDS_V1,
    ExecutionPlanCsvSink,
)
from roguetrader.publisher.loader import load_completed_run
from roguetrader.publisher.service import LocalPublisher
from roguetrader.publisher.sinks import CsvDecisionSink
from roguetrader.publisher.state import PublicationState
from roguetrader.run_outputs import write_run_outputs


def execution_draft(*, summary: str = "按既定条件分批调整仓位。") -> dict:
    return {
        "plan_summary": summary,
        "valid_for_hours": 48,
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
                        "size": {
                            "basis": "current_position_pct",
                            "value": 0.25,
                        },
                        "time_in_force": "IOC",
                    }
                ],
            },
            {
                "scenario_id": "observe",
                "priority": 2,
                "trigger": {
                    "type": "manual_confirmation",
                    "condition": "等待下一次日线确认",
                },
                "orders": [],
            },
        ],
    }


def make_run(
    results_root: Path,
    *,
    trade_date: str,
    stamp: str,
    ticker: str = "BTC-USD",
    runtime_mode: str = "prod",
    trigger: str = "scheduled",
    with_plan: bool = False,
) -> Path:
    paths = make_run_output_paths(
        results_root,
        ticker,
        stamp,
        analysis_date=trade_date,
        runtime_mode=runtime_mode,
        trigger=trigger,
    )
    write_run_outputs(
        paths=paths,
        ticker=ticker,
        trade_date=trade_date,
        final_state={
            "company_of_interest": ticker,
            "trade_date": trade_date,
            "final_trade_decision": "UNDERWEIGHT；按明确价格和条件分批减仓。",
        },
        decision="UNDERWEIGHT",
        execution_plan_requested=with_plan,
        execution_plan_draft=execution_draft() if with_plan else None,
    )
    generated = (
        datetime.strptime(stamp, "%Y%m%d_%H%M%S")
        .replace(tzinfo=ZoneInfo("Asia/Shanghai"))
        + timedelta(minutes=15)
    ).isoformat(timespec="seconds")
    for filename in ("最终决策.json", "运行索引.json"):
        path = paths.root / filename
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["generated_at"] = generated
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return paths.root


def mark_official(results_root: Path, run_dir: Path) -> None:
    CsvDecisionSink(results_root / "汇总" / "每日决策.csv").persist(
        load_completed_run(run_dir)
    )


class SequencePlanner:
    def __init__(self, *, fail_on_call: int | None = None):
        self.calls: list[dict | None] = []
        self.fail_on_call = fail_on_call

    def create_draft(self, **kwargs):
        self.calls.append(kwargs.get("previous_plan"))
        if self.fail_on_call == len(self.calls):
            raise RuntimeError("synthetic provider failure")
        draft = execution_draft()
        if kwargs.get("previous_plan") is not None:
            draft["previous_plan_id"] = kwargs["previous_plan"]["plan_id"]
            draft["continuity_action"] = "amend"
            draft["change_summary"] = "承接上一日计划，并按本日决策更新。"
        return draft


class HistoricalExecutionPlanBackfillTests(unittest.TestCase):
    def test_zero_risk_limit_is_valid_for_full_exit_plans(self):
        from roguetrader.execution import build_execution_plan

        draft = execution_draft()
        draft["risk_limits"] = {
            "max_position_pct": 0,
            "max_order_cash_pct": 0,
        }
        plan = build_execution_plan(
            draft,
            decision_event_id="a" * 64,
            ticker="BTC-USD",
            analysis_date="2026-07-12",
            action="SELL",
            created_at="2026-07-12T16:03:46+08:00",
        )

        self.assertEqual(plan["risk_limits"]["max_position_pct"], 0)
        self.assertEqual(plan["risk_limits"]["max_order_cash_pct"], 0)

    def test_default_discovery_uses_only_official_csv_and_orders_chronologically(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "my_results"
            later = make_run(
                root, trade_date="2026-09-12", stamp="20260912_050000"
            )
            earlier = make_run(
                root, trade_date="2026-09-11", stamp="20260911_050000"
            )
            make_run(
                root,
                trade_date="2026-09-10",
                stamp="20260910_220000",
                runtime_mode="dev",
                trigger="manual",
            )
            mark_official(root, later)
            mark_official(root, earlier)
            decision_path = earlier / "最终决策.json"
            decision = json.loads(decision_path.read_text(encoding="utf-8"))
            decision["generated_at"] = "2026-09-11T05:08:00"
            decision_path.write_text(json.dumps(decision), encoding="utf-8")

            selected = discover_historical_decisions(root)

            self.assertEqual(
                [item.record.trade_date for item in selected],
                ["2026-09-11", "2026-09-12"],
            )
            self.assertTrue(
                all(item.record.runtime_mode == "prod" for item in selected)
            )
            self.assertEqual(selected[0].plan_created_at, "2026-09-11T05:08:00+08:00")

    def test_dry_run_never_calls_planner_or_writes_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "my_results"
            run_dir = make_run(
                root, trade_date="2026-09-11", stamp="20260911_050000"
            )
            mark_official(root, run_dir)
            planner = SequencePlanner()
            sink = ExecutionPlanCsvSink(root / "汇总" / "参数化委托.csv", root)

            report = backfill_historical_execution_plans(
                discover_historical_decisions(root),
                planner=planner,
                order_csv_sink=sink,
                apply=False,
            )

            self.assertEqual(report["pending_generation"], 1)
            self.assertEqual(report["llm_calls"], 0)
            self.assertEqual(planner.calls, [])
            self.assertFalse((run_dir / "执行计划.json").exists())
            self.assertFalse(sink.path.exists())

    def test_external_official_csv_selects_copied_development_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / "source"
            development = base / "development"
            source_run = make_run(
                source, trade_date="2026-09-11", stamp="20260911_050000"
            )
            mark_official(source, source_run)
            copied_run = development / "运行结果" / source_run.name
            copied_run.parent.mkdir(parents=True)
            import shutil

            shutil.copytree(source_run, copied_run)

            selected = discover_historical_decisions(
                development,
                official_csv=source / "汇总" / "每日决策.csv",
            )

            self.assertEqual(len(selected), 1)
            self.assertEqual(selected[0].run_dir, copied_run.resolve())

    def test_v2_directory_wins_over_identical_legacy_path_alias(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "my_results"
            v2_run = make_run(
                root, trade_date="2026-09-11", stamp="20260911_050000"
            )
            mark_official(root, v2_run)
            legacy_alias = root / "运行结果" / "20260911_050000_BTC_USD"
            import shutil

            shutil.copytree(v2_run, legacy_alias)

            selected = discover_historical_decisions(root)

            self.assertEqual(len(selected), 1)
            self.assertEqual(selected[0].run_dir, v2_run.resolve())

    def test_official_legacy_history_flows_into_first_production_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "my_results"
            legacy = make_run(
                root,
                trade_date="2026-09-10",
                stamp="20260910_050000",
                runtime_mode="legacy",
                trigger="unknown",
            )
            production = make_run(
                root, trade_date="2026-09-11", stamp="20260911_050000"
            )
            mark_official(root, legacy)
            mark_official(root, production)
            planner = SequencePlanner()

            report = backfill_historical_execution_plans(
                discover_historical_decisions(root),
                planner=planner,
                order_csv_sink=ExecutionPlanCsvSink(
                    root / "汇总" / "参数化委托.csv", root
                ),
                apply=True,
                max_plans=2,
            )

            self.assertEqual(report["generated"], 2)
            first_plan = json.loads(
                (legacy / "执行计划.json").read_text(encoding="utf-8")
            )
            second_plan = json.loads(
                (production / "执行计划.json").read_text(encoding="utf-8")
            )
            self.assertEqual(second_plan["previous_plan_id"], first_plan["plan_id"])

    def test_apply_requires_an_explicit_positive_model_call_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "my_results"
            run_dir = make_run(
                root, trade_date="2026-09-11", stamp="20260911_050000"
            )
            mark_official(root, run_dir)
            from roguetrader.execution.backfill import HistoricalPlanBackfillError

            with self.assertRaises(HistoricalPlanBackfillError):
                backfill_historical_execution_plans(
                    discover_historical_decisions(root),
                    planner=SequencePlanner(),
                    order_csv_sink=ExecutionPlanCsvSink(
                        root / "汇总" / "参数化委托.csv", root
                    ),
                    apply=True,
                )

    def test_apply_backfills_continuity_and_idempotent_order_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "my_results"
            first_dir = make_run(
                root, trade_date="2026-09-11", stamp="20260911_050000"
            )
            second_dir = make_run(
                root, trade_date="2026-09-12", stamp="20260912_050000"
            )
            mark_official(root, first_dir)
            mark_official(root, second_dir)
            sink = ExecutionPlanCsvSink(root / "汇总" / "参数化委托.csv", root)
            planner = SequencePlanner()

            first_report = backfill_historical_execution_plans(
                discover_historical_decisions(root),
                planner=planner,
                order_csv_sink=sink,
                apply=True,
                max_plans=2,
            )

            self.assertEqual(first_report["generated"], 2)
            self.assertEqual(first_report["llm_calls"], 2)
            self.assertEqual(first_report["order_rows_added"], 4)
            first_plan = json.loads(
                (first_dir / "执行计划.json").read_text(encoding="utf-8")
            )
            second_plan = json.loads(
                (second_dir / "执行计划.json").read_text(encoding="utf-8")
            )
            self.assertIsNone(first_plan["previous_plan_id"])
            self.assertEqual(second_plan["previous_plan_id"], first_plan["plan_id"])
            self.assertEqual(second_plan["continuity_action"], "amend")
            self.assertIsNone(planner.calls[0])
            self.assertEqual(planner.calls[1]["plan_id"], first_plan["plan_id"])
            index = json.loads(
                (first_dir / "运行索引.json").read_text(encoding="utf-8")
            )
            self.assertEqual(index["files"]["execution_plan"], "执行计划.json")
            self.assertEqual(index["execution_plan"]["origin"], "historical_backfill")

            second_report = backfill_historical_execution_plans(
                discover_historical_decisions(root),
                planner=None,
                order_csv_sink=sink,
                apply=True,
                max_plans=1,
            )
            self.assertEqual(second_report["llm_calls"], 0)
            self.assertEqual(second_report["order_rows_added"], 0)
            with sink.path.open("r", encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 4)
            self.assertEqual(tuple(rows[0]), EXECUTION_CSV_FIELDS)
            self.assertEqual({row["row_type"] for row in rows}, {"order", "no_order"})

    def test_filtered_retry_is_seeded_with_immediate_previous_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "my_results"
            first_dir = make_run(
                root,
                trade_date="2026-09-11",
                stamp="20260911_050000",
                with_plan=True,
            )
            second_dir = make_run(
                root, trade_date="2026-09-12", stamp="20260912_050000"
            )
            mark_official(root, first_dir)
            mark_official(root, second_dir)
            all_candidates = discover_historical_decisions(root)
            selected = discover_historical_decisions(root, date_from="2026-09-12")
            initial = initial_previous_plans(all_candidates, selected)
            planner = SequencePlanner()

            report = backfill_historical_execution_plans(
                selected,
                planner=planner,
                order_csv_sink=ExecutionPlanCsvSink(
                    root / "汇总" / "参数化委托.csv", root
                ),
                apply=True,
                max_plans=1,
                initial_previous_by_lane=initial,
            )

            self.assertEqual(report["generated"], 1)
            first_plan = json.loads(
                (first_dir / "执行计划.json").read_text(encoding="utf-8")
            )
            second_plan = json.loads(
                (second_dir / "执行计划.json").read_text(encoding="utf-8")
            )
            self.assertEqual(second_plan["previous_plan_id"], first_plan["plan_id"])
            self.assertEqual(planner.calls[0]["plan_id"], first_plan["plan_id"])

    def test_existing_discontinuous_lineage_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "my_results"
            for day in ("11", "12"):
                run_dir = make_run(
                    root,
                    trade_date=f"2026-09-{day}",
                    stamp=f"202609{day}_050000",
                    with_plan=True,
                )
                mark_official(root, run_dir)

            with self.assertRaisesRegex(
                HistoricalPlanBackfillError, "执行计划谱系不连续"
            ):
                backfill_historical_execution_plans(
                    discover_historical_decisions(root),
                    planner=None,
                    order_csv_sink=ExecutionPlanCsvSink(
                        root / "汇总" / "参数化委托.csv", root
                    ),
                    apply=False,
                )

    def test_provider_failure_blocks_only_later_items_in_same_lane(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "my_results"
            for day in ("11", "12"):
                run_dir = make_run(
                    root,
                    trade_date=f"2026-09-{day}",
                    stamp=f"202609{day}_050000",
                )
                mark_official(root, run_dir)
            other_symbol = make_run(
                root,
                trade_date="2026-09-11",
                stamp="20260911_051000",
                ticker="ETH-USD",
            )
            mark_official(root, other_symbol)
            planner = SequencePlanner(fail_on_call=1)

            report = backfill_historical_execution_plans(
                discover_historical_decisions(root),
                planner=planner,
                order_csv_sink=ExecutionPlanCsvSink(
                    root / "汇总" / "参数化委托.csv", root
                ),
                apply=True,
                max_plans=2,
            )

            self.assertEqual(report["llm_calls"], 2)
            self.assertEqual(report["generated"], 1)
            self.assertEqual(
                [item["status"] for item in report["items"]],
                ["error", "blocked_by_previous_error", "generated"],
            )
            self.assertEqual(report["items"][0]["error_type"], "RuntimeError")

    def test_daily_publisher_appends_execution_csv_automatically(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "my_results"
            run_dir = make_run(
                root,
                trade_date="2026-09-13",
                stamp="20260913_050000",
                with_plan=True,
            )
            order_csv = root / "汇总" / "参数化委托.csv"
            publisher = LocalPublisher(
                PublicationState(Path(directory) / "publisher.sqlite3"),
                (
                    CsvDecisionSink(root / "汇总" / "每日决策.csv"),
                    ExecutionPlanCsvSink(order_csv, root),
                ),
            )

            first = publisher.publish_run(run_dir)
            second = publisher.publish_run(run_dir)

            self.assertTrue(first.successful)
            self.assertEqual(first.deliveries["execution_csv"], "success")
            self.assertFalse(second.attempted)
            with order_csv.open("r", encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 2)
            self.assertTrue(rows[0]["size_expression"].endswith("X_POSITION"))

    def test_execution_csv_neutralizes_formula_cells(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "my_results"
            run_dir = make_run(
                root,
                trade_date="2026-09-13",
                stamp="20260913_050000",
                with_plan=False,
            )
            mark_official(root, run_dir)
            record = load_completed_run(run_dir)
            from roguetrader.execution import build_execution_plan

            plan = build_execution_plan(
                execution_draft(summary="=cmd()"),
                decision_event_id=record.event_id,
                ticker=record.ticker,
                analysis_date=record.trade_date,
                action=record.action,
                created_at="2026-09-13T05:10:00+08:00",
            )
            sink = ExecutionPlanCsvSink(root / "汇总" / "参数化委托.csv", root)
            sink.persist(plan, record)

            with sink.path.open("r", encoding="utf-8-sig", newline="") as handle:
                row = next(csv.DictReader(handle))
            self.assertEqual(row["plan_summary"], "'=cmd()")

    def test_execution_csv_upgrades_v1_from_source_plans(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "my_results"
            run_dir = make_run(
                root,
                trade_date="2026-09-13",
                stamp="20260913_050000",
                with_plan=True,
            )
            record = load_completed_run(run_dir)
            plan = json.loads((run_dir / "执行计划.json").read_text(encoding="utf-8"))
            sink = ExecutionPlanCsvSink(root / "汇总" / "参数化委托.csv", root)
            sink.persist(plan, record)
            with sink.path.open("r", encoding="utf-8-sig", newline="") as handle:
                current = list(csv.DictReader(handle))
            with sink.path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=EXECUTION_CSV_FIELDS_V1)
                writer.writeheader()
                writer.writerows(
                    [
                        {field: row[field] for field in EXECUTION_CSV_FIELDS_V1}
                        for row in current
                    ]
                )

            added = sink.persist(plan, record)

            self.assertEqual(added, 0)
            with sink.path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                upgraded = list(reader)
                self.assertEqual(tuple(reader.fieldnames or ()), EXECUTION_CSV_FIELDS)
            self.assertTrue(all(row["order_generated_at"] for row in upgraded))
            self.assertEqual({row["schema_version"] for row in upgraded}, {"2.0"})


if __name__ == "__main__":
    unittest.main()
