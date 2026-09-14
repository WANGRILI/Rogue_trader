from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from ops.migrate_run_layout import (
    apply_migration,
    build_manifest,
    rollback_migration,
)
from roguetrader.publisher.loader import load_completed_run
from roguetrader.publisher.service import LocalPublisher
from roguetrader.publisher.sinks import CsvDecisionSink
from roguetrader.publisher.state import PublicationState


def completed_run(root: Path) -> Path:
    run_dir = root / "运行结果" / "20260913_115207_BTC_USD"
    run_dir.mkdir(parents=True)
    decision = {
        "schema_version": "1.0",
        "generated_at": "2026-09-13T12:02:31+08:00",
        "ticker": "BTC-USD",
        "trade_date": "2026-09-13",
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
    index = {
        "schema_version": "1.0",
        "generated_at": "2026-09-13T12:02:32+08:00",
        "ticker": "BTC-USD",
        "trade_date": "2026-09-13",
        "action": "HOLD",
        "files": {"decision": "最终决策.json"},
    }
    (run_dir / "最终决策.json").write_text(
        json.dumps(decision, ensure_ascii=False), encoding="utf-8"
    )
    (run_dir / "运行索引.json").write_text(
        json.dumps(index, ensure_ascii=False), encoding="utf-8"
    )
    return run_dir


class RunLayoutMigrationTests(unittest.TestCase):
    def test_apply_preserves_event_and_csv_then_rollback_restores_name(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            results = root / "my_results"
            old_run = completed_run(results)
            legacy_log = results / "运行结果" / "20260913_050000_cron.log"
            legacy_log.write_text("historical scheduler log", encoding="utf-8")
            csv_path = results / "汇总" / "每日决策.csv"
            state_path = root / "publisher.sqlite3"
            publisher = LocalPublisher(
                PublicationState(state_path), (CsvDecisionSink(csv_path),)
            )
            original_event = load_completed_run(old_run).event_id
            publisher.publish_run(old_run)
            csv_digest = hashlib.sha256(csv_path.read_bytes()).hexdigest()
            args = argparse.Namespace(
                results_root=results,
                csv=csv_path,
                publisher_state=[state_path],
                production_history=root / "missing-production.json",
                development_history=root / "missing-development.json",
                migration_file=results / ".migrations" / "run-naming-v2" / "manifest.json",
            )

            plan = build_manifest(args)
            apply_migration(args, plan)
            new_run = results / "运行结果" / plan["entries"][0]["new_name"]

            self.assertTrue(new_run.is_dir())
            self.assertFalse(old_run.exists())
            self.assertEqual(load_completed_run(new_run).event_id, original_event)
            self.assertEqual(hashlib.sha256(csv_path.read_bytes()).hexdigest(), csv_digest)
            self.assertFalse(legacy_log.exists())
            self.assertTrue((results / "历史日志" / legacy_log.name).is_file())

            rollback_migration(args, plan)
            self.assertTrue(old_run.is_dir())
            self.assertFalse((old_run / "运行清单.json").exists())
            self.assertEqual(load_completed_run(old_run).event_id, original_event)
            self.assertTrue(legacy_log.is_file())


if __name__ == "__main__":
    unittest.main()
