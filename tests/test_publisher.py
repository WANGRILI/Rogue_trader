from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import tempfile
import time
import unittest

from roguetrader.publisher.loader import load_completed_run
from roguetrader.publisher.__main__ import results_root_for_args
from roguetrader.publisher.models import PublicationError, render_message
from roguetrader.publisher.service import LocalPublisher, PublisherWatcher, baseline_key
from roguetrader.publisher.sinks import (
    CSV_FIELDS,
    CsvDecisionSink,
    LocalMessageSink,
)
from roguetrader.publisher.state import PublicationState


def make_completed_run(
    results_root: Path,
    run_id: str,
    *,
    ticker: str = "BTC-USD",
    trade_date: str = "2026-09-11",
    action: str = "BUY",
    decision_text: str = "建议分批买入，并严格设置止损。",
) -> Path:
    run_dir = results_root / "运行结果" / run_id
    run_dir.mkdir(parents=True)
    decision = {
        "schema_version": "1.0",
        "generated_at": f"{trade_date}T05:08:00",
        "ticker": ticker,
        "trade_date": trade_date,
        "action": action,
        "action_source": "SignalProcessor",
        "confidence": None,
        "time_horizon": None,
        "risk_level": None,
        "entry_plan": None,
        "stop_loss": None,
        "take_profit": None,
        "key_reasons": [],
        "invalidations": [],
        "final_trade_decision_text": decision_text,
    }
    index = {
        "schema_version": "1.0",
        "generated_at": f"{trade_date}T05:08:01",
        "ticker": ticker,
        "trade_date": trade_date,
        "action": action,
        "files": {"decision": "最终决策.json"},
    }
    (run_dir / "最终决策.json").write_text(
        json.dumps(decision, ensure_ascii=False), encoding="utf-8"
    )
    (run_dir / "运行索引.json").write_text(
        json.dumps(index, ensure_ascii=False), encoding="utf-8"
    )
    return run_dir


class LoaderTests(unittest.TestCase):
    def test_cli_output_layout_follows_the_input_results_root(self):
        with tempfile.TemporaryDirectory() as directory:
            results_root = Path(directory) / "my_results"
            run_dir = results_root / "运行结果" / "run-1"
            args = argparse.Namespace(command="publish", run_dir=run_dir)
            self.assertEqual(results_root_for_args(args), results_root.resolve())

    def test_loads_stable_record_and_message(self):
        with tempfile.TemporaryDirectory() as directory:
            run_dir = make_completed_run(Path(directory), "20260911_050003_BTC_USD")

            first = load_completed_run(run_dir)
            second = load_completed_run(run_dir)
            message = render_message(first)

            self.assertEqual(first.event_id, second.event_id)
            self.assertEqual(len(first.event_id), 64)
            self.assertEqual(first.action, "BUY")
            self.assertEqual(message.level, "positive")
            self.assertIn("分批买入", message.text)

    def test_frozen_event_identity_survives_physical_directory_rename(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = make_completed_run(root, "20260913_115207_BTC_USD")
            event_id = load_completed_run(original).event_id
            (original / "运行清单.json").write_text(
                json.dumps(
                    {
                        "schema_version": "2.0",
                        "runtime_mode": "prod",
                        "trigger": "recovery",
                        "attempt": 2,
                        "status": "completed",
                        "publication_role": "official",
                        "publication_event_id": event_id,
                    }
                ),
                encoding="utf-8",
            )
            renamed = original.with_name(
                "20260913_115207__asof-20260913__prod__recovery-a02__BTC_USD"
            )
            original.rename(renamed)

            record = load_completed_run(renamed)
            self.assertEqual(record.event_id, event_id)
            self.assertEqual(record.trigger, "recovery")
            self.assertEqual(record.attempt, 2)

    def test_message_preserves_complete_multiline_decision_within_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            decision_text = (
                "1. **评级** UNDERWEIGHT\n\n"
                "2. **执行摘要**\n"
                + "降低战术敞口并等待风险释放。" * 150
                + "\n\n3. **结束标记** COMPLETE"
            )
            run_dir = make_completed_run(
                Path(directory), "run-complete-summary", decision_text=decision_text
            )

            record = load_completed_run(run_dir)
            message = render_message(record)

            self.assertIn("\n\n2. **执行摘要**\n", message.text)
            self.assertTrue(message.text.endswith("3. **结束标记** COMPLETE"))
            self.assertNotIn("…", message.text)

    def test_requires_completion_marker_and_consistent_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            results_root = Path(directory)
            run_dir = make_completed_run(results_root, "run-1")
            (run_dir / "运行索引.json").unlink()
            with self.assertRaises(PublicationError):
                load_completed_run(run_dir)

            run_dir = make_completed_run(results_root, "run-2")
            index = json.loads((run_dir / "运行索引.json").read_text(encoding="utf-8"))
            index["ticker"] = "ETH-USD"
            (run_dir / "运行索引.json").write_text(json.dumps(index), encoding="utf-8")
            with self.assertRaises(PublicationError):
                load_completed_run(run_dir)

    def test_incomplete_runs_are_not_publishable(self):
        with tempfile.TemporaryDirectory() as directory:
            run_dir = make_completed_run(
                Path(directory), "run-incomplete", action="INCOMPLETE"
            )
            with self.assertRaises(PublicationError):
                load_completed_run(run_dir)


class SinkAndStateTests(unittest.TestCase):
    def test_explicit_publication_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = make_completed_run(root, "run-1")
            csv_path = root / "汇总" / "每日决策.csv"
            message_sink = LocalMessageSink(root / "汇总" / "消息")
            state = PublicationState(root / ".runtime" / "publisher.sqlite3")
            publisher = LocalPublisher(
                state, (CsvDecisionSink(csv_path), message_sink)
            )

            first = publisher.publish_run(run_dir)
            second = publisher.publish_run(run_dir)

            self.assertTrue(first.successful)
            self.assertTrue(first.attempted)
            self.assertTrue(second.successful)
            self.assertFalse(second.attempted)
            with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["event_id"], first.event_id)
            message_path = message_sink.message_path(first.event_id)
            self.assertTrue(message_path.is_file())
            self.assertEqual(
                json.loads(message_path.read_text())["event_id"], first.event_id
            )
            self.assertEqual(csv_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(message_path.stat().st_mode & 0o777, 0o600)

    def test_one_official_result_per_analysis_date_and_ticker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_run = make_completed_run(root, "run-first")
            second_run = make_completed_run(root, "run-second")
            publisher = LocalPublisher(
                PublicationState(root / "publisher.sqlite3"),
                (CsvDecisionSink(root / "decisions.csv"),),
            )

            publisher.publish_run(first_run)
            with self.assertRaises(PublicationError):
                publisher.publish_run(second_run)

            promoted = publisher.publish_run(second_run, promote=True)
            self.assertTrue(promoted.successful)

    def test_csv_neutralizes_formula_prefixes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = make_completed_run(root, "run-formula", decision_text="=cmd()")
            csv_path = root / "每日决策.csv"
            sink = CsvDecisionSink(csv_path)
            sink.write(load_completed_run(run_dir))
            with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
                row = next(csv.DictReader(handle))
            self.assertEqual(row["decision_summary"], "'=cmd()")
            self.assertEqual(sink.read(row["event_id"]).decision_summary, "'=cmd()")

    def test_downstream_consumes_the_exact_persisted_csv_record(self):
        class CapturingSink:
            name = "external"

            def __init__(self):
                self.records = []

            def write(self, record):
                self.records.append(record)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = make_completed_run(root, "run-csv-authoritative")
            record = load_completed_run(run_dir)
            csv_path = root / "每日决策.csv"
            csv_sink = CsvDecisionSink(csv_path)
            csv_sink.write(record)
            with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
                row = next(csv.DictReader(handle))
            row["decision_summary"] = "以 CSV 中已经落盘的记录为准。"
            with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
                writer.writeheader()
                writer.writerow(row)

            external = CapturingSink()
            publisher = LocalPublisher(
                PublicationState(root / "publisher.sqlite3"),
                (csv_sink, external),
            )
            result = publisher.publish_run(run_dir)

            self.assertTrue(result.successful)
            self.assertEqual(len(external.records), 1)
            self.assertEqual(
                external.records[0].decision_summary,
                "以 CSV 中已经落盘的记录为准。",
            )

    def test_csv_failure_blocks_every_downstream_sink(self):
        class FailedCsvSink(CsvDecisionSink):
            def persist(self, _record):
                raise OSError("disk unavailable")

        class CapturingSink:
            name = "external"

            def __init__(self):
                self.calls = 0

            def write(self, _record):
                self.calls += 1

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = make_completed_run(root, "run-csv-failure")
            external = CapturingSink()
            publisher = LocalPublisher(
                PublicationState(root / "publisher.sqlite3"),
                (FailedCsvSink(root / "每日决策.csv"), external),
            )

            with self.assertLogs("roguetrader.publisher.service", level="ERROR"):
                result = publisher.publish_run(run_dir)

            self.assertEqual(
                result.deliveries,
                {"csv": "failed", "external": "blocked_by_csv"},
            )
            self.assertEqual(external.calls, 0)

    def test_each_sink_retries_independently(self):
        class FlakySink:
            name = "flaky"

            def __init__(self):
                self.calls = 0

            def write(self, _record):
                self.calls += 1
                if self.calls == 1:
                    raise OSError("temporary failure")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = make_completed_run(root, "run-retry")
            csv_path = root / "每日决策.csv"
            flaky = FlakySink()
            publisher = LocalPublisher(
                PublicationState(root / "publisher.sqlite3"),
                (CsvDecisionSink(csv_path), flaky),
            )

            with self.assertLogs("roguetrader.publisher.service", level="ERROR"):
                first = publisher.publish_run(run_dir)
            second = publisher.publish_run(run_dir)

            self.assertEqual(first.deliveries, {"csv": "success", "flaky": "failed"})
            self.assertEqual(second.deliveries, {"csv": "success", "flaky": "success"})
            self.assertEqual(flaky.calls, 2)
            with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
                self.assertEqual(len(list(csv.DictReader(handle))), 1)

            csv_only = LocalPublisher(
                publisher.state,
                (CsvDecisionSink(csv_path),),
            ).publish_run(run_dir)
            self.assertTrue(csv_only.successful)
            self.assertEqual(csv_only.deliveries, {"csv": "success"})


class ScanTests(unittest.TestCase):
    def test_existing_state_database_gains_historical_path_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            historical = root / "运行结果" / "legacy-invalid"
            historical.mkdir(parents=True)
            (historical / "运行索引.json").write_text("{}", encoding="utf-8")
            state = PublicationState(root / ".runtime" / "publisher.sqlite3")
            state.set_metadata(baseline_key(root), "2026-09-11T05:00:00+08:00")
            publisher = LocalPublisher(
                state,
                (CsvDecisionSink(root / "汇总" / "每日决策.csv"),),
            )

            report = publisher.scan(root)

            self.assertEqual(report["errors"], [])

    def test_invalid_historical_run_is_baselined_but_new_invalid_run_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            historical = root / "运行结果" / "legacy-invalid"
            historical.mkdir(parents=True)
            (historical / "运行索引.json").write_text("{}", encoding="utf-8")
            publisher = LocalPublisher(
                PublicationState(root / ".runtime" / "publisher.sqlite3"),
                (CsvDecisionSink(root / "汇总" / "每日决策.csv"),),
            )

            with self.assertLogs("roguetrader.publisher.service", level="WARNING"):
                baseline = publisher.scan(root)
            self.assertEqual(baseline["errors"], [])
            self.assertEqual(publisher.scan(root)["errors"], [])

            current = root / "运行结果" / "new-invalid"
            current.mkdir(parents=True)
            (current / "运行索引.json").write_text("{}", encoding="utf-8")
            report = publisher.scan(root)

            self.assertEqual(len(report["errors"]), 1)
            self.assertEqual(report["errors"][0]["run_id"], "new-invalid")

    def test_first_scan_baselines_history_then_publishes_only_new_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old_run = make_completed_run(root, "run-old", trade_date="2026-09-10")
            csv_path = root / "汇总" / "每日决策.csv"
            publisher = LocalPublisher(
                PublicationState(root / ".runtime" / "publisher.sqlite3"),
                (CsvDecisionSink(csv_path), LocalMessageSink(root / "汇总" / "消息")),
            )

            baseline = publisher.scan(root)
            self.assertEqual(baseline["baseline_count"], 1)
            self.assertFalse(csv_path.exists())

            new_run = make_completed_run(root, "run-new", trade_date="2026-09-11")
            report = publisher.scan(root)
            self.assertEqual(len(report["published"]), 1)
            self.assertEqual(report["published"][0]["run_id"], new_run.name)

            backfill = publisher.scan(root, backfill=True)
            attempted = [item for item in backfill["published"] if item["attempted"]]
            self.assertEqual([item["run_id"] for item in attempted], [old_run.name])
            with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
                self.assertEqual(len(list(csv.DictReader(handle))), 2)

    def test_backfill_does_not_promote_candidate_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate = make_completed_run(root, "run-candidate")
            (candidate / "运行清单.json").write_text(
                json.dumps(
                    {
                        "schema_version": "2.0",
                        "status": "completed",
                        "runtime_mode": "dev",
                        "trigger": "manual",
                        "attempt": 1,
                        "publication_role": "candidate",
                    }
                ),
                encoding="utf-8",
            )
            csv_path = root / "汇总" / "每日决策.csv"
            publisher = LocalPublisher(
                PublicationState(root / ".runtime" / "publisher.sqlite3"),
                (CsvDecisionSink(csv_path),),
            )

            publisher.scan(root)
            report = publisher.scan(root, backfill=True)

            self.assertEqual(report["published"], [])
            self.assertFalse(csv_path.exists())

    def test_watcher_publishes_a_result_created_after_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_completed_run(root, "run-old", trade_date="2026-09-10")
            csv_path = root / "汇总" / "每日决策.csv"
            publisher = LocalPublisher(
                PublicationState(root / ".runtime" / "publisher.sqlite3"),
                (CsvDecisionSink(csv_path), LocalMessageSink(root / "汇总" / "消息")),
            )
            watcher = PublisherWatcher(publisher, root, poll_interval=0.02)
            watcher.start()
            try:
                deadline = time.monotonic() + 2
                while (
                    watcher.status()["last_scan_at"] is None
                    and time.monotonic() < deadline
                ):
                    time.sleep(0.01)
                make_completed_run(root, "run-new", trade_date="2026-09-11")
                watcher.scan_now()
                while not csv_path.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
            finally:
                watcher.stop()

            self.assertTrue(csv_path.is_file())
            with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual([row["run_id"] for row in rows], ["run-new"])

    def test_failed_downstream_delivery_recovers_from_csv_without_source_files(self):
        class FlakySink:
            name = "external"

            def __init__(self):
                self.calls = 0

            def write(self, _record):
                self.calls += 1
                if self.calls == 1:
                    raise OSError("temporary")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = make_completed_run(root, "run-csv-recovery")
            external = FlakySink()
            publisher = LocalPublisher(
                PublicationState(root / "publisher.sqlite3"),
                (CsvDecisionSink(root / "每日决策.csv"), external),
            )
            with self.assertLogs("roguetrader.publisher.service", level="ERROR"):
                first = publisher.publish_run(run_dir)
            self.assertEqual(first.deliveries["csv"], "success")
            self.assertEqual(first.deliveries["external"], "failed")
            (run_dir / "运行索引.json").unlink()
            (run_dir / "最终决策.json").unlink()

            report = publisher.scan(root)

            attempted = [item for item in report["published"] if item["attempted"]]
            self.assertEqual(len(attempted), 1)
            self.assertTrue(attempted[0]["successful"])
            self.assertEqual(external.calls, 2)

    def test_manual_csv_rows_are_not_automatically_discovered(self):
        class CapturingSink:
            name = "external"

            def __init__(self):
                self.calls = 0

            def write(self, _record):
                self.calls += 1

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "watched"
            source = Path(directory) / "manual"
            run_dir = make_completed_run(source, "run-manual-csv")
            csv_sink = CsvDecisionSink(root / "每日决策.csv")
            csv_sink.write(load_completed_run(run_dir))
            external = CapturingSink()
            publisher = LocalPublisher(
                PublicationState(root / "publisher.sqlite3"),
                (csv_sink, external),
            )

            report = publisher.scan(root)

            self.assertTrue(report["baseline_initialized"])
            self.assertEqual(report["published"], [])
            self.assertEqual(external.calls, 0)


if __name__ == "__main__":
    unittest.main()
