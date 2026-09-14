#!/usr/bin/env python3
"""Safely migrate RogueTrader run directories to naming schema v2."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import tempfile
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from roguetrader.output_paths import (  # noqa: E402
    RUN_MANIFEST_FILE,
    RUN_NAMING_SCHEMA_VERSION,
    parse_run_directory_name,
    safe_symbol,
)
from roguetrader.publisher.models import stable_event_id  # noqa: E402


LEGACY_NAME_RE = re.compile(r"^(?P<stamp>\d{8}_\d{6})_(?P<symbol>.+)$")
MIGRATION_VERSION = "run-naming-v2"
LEGACY_LOG_DIR = "历史日志"


class MigrationError(RuntimeError):
    pass


def atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, default=str)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def load_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def load_history(path: Path, lane: str) -> list[dict[str, Any]]:
    value = load_json(path)
    if value:
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(raw, list):
        return []
    return [dict(item, _lane=lane) for item in raw if isinstance(item, dict)]


def file_digest(run_dir: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in run_dir.rglob("*") if item.is_file()):
        if path.name == RUN_MANIFEST_FILE:
            continue
        digest.update(str(path.relative_to(run_dir)).encode("utf-8"))
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def single_file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_started(name: str) -> datetime:
    match = LEGACY_NAME_RE.fullmatch(name)
    if not match:
        raise MigrationError(f"无法识别历史目录名：{name}")
    return datetime.strptime(match.group("stamp"), "%Y%m%d_%H%M%S").astimezone()


def infer_ticker(name: str, index: dict[str, Any], decision: dict[str, Any]) -> str:
    for payload in (decision, index):
        value = payload.get("ticker")
        if isinstance(value, str) and value.strip():
            return value.strip()
    match = LEGACY_NAME_RE.fullmatch(name)
    if not match:
        raise MigrationError(f"无法识别历史标的：{name}")
    return match.group("symbol").replace("_", "-")


def infer_analysis_date(
    name: str, index: dict[str, Any], decision: dict[str, Any]
) -> str:
    for payload in (decision, index):
        value = payload.get("trade_date") or payload.get("analysis_date")
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value).date().isoformat()
            except ValueError:
                pass
    return parse_started(name).date().isoformat()


def matching_history(
    histories: list[dict[str, Any]],
    *,
    started: datetime,
    ticker: str,
    analysis_date: str,
) -> dict[str, Any] | None:
    matches: list[tuple[float, dict[str, Any]]] = []
    for record in histories:
        if record.get("symbol") != ticker or record.get("trade_date") != analysis_date:
            continue
        try:
            recorded = datetime.fromisoformat(str(record.get("started_at")))
            if recorded.tzinfo is None:
                recorded = recorded.astimezone()
        except ValueError:
            continue
        distance = abs((started - recorded).total_seconds())
        if distance <= 10 * 60:
            matches.append((distance, record))
    return min(matches, default=(0, None), key=lambda item: item[0])[1]


def inspect_run(
    run_dir: Path,
    csv_event_ids: set[str],
    histories: list[dict[str, Any]],
) -> dict[str, Any]:
    index = load_json(run_dir / "运行索引.json")
    decision = load_json(run_dir / "最终决策.json")
    ticker = infer_ticker(run_dir.name, index, decision)
    analysis_date = infer_analysis_date(run_dir.name, index, decision)
    started = parse_started(run_dir.name)
    history = matching_history(
        histories,
        started=started,
        ticker=ticker,
        analysis_date=analysis_date,
    )
    runtime_mode = str(history.get("_lane")) if history else "legacy"
    trigger = "scheduled" if history else "unknown"
    scheduled_for = str(history.get("scheduled_for")) if history else None
    if not scheduled_for or scheduled_for == "None":
        scheduled_for = None
    action = decision.get("action")
    if not index:
        status = "failed" if history and history.get("status") == "failed" else "incomplete"
    elif action == "INCOMPLETE" or not decision:
        status = "incomplete"
    else:
        status = "completed"
    old_event_id = stable_event_id(run_dir.name, decision) if decision else None
    publication_role = (
        "official"
        if old_event_id in csv_event_ids
        else ("candidate" if status == "completed" else "excluded")
    )
    return {
        "old_name": run_dir.name,
        "ticker": ticker,
        "safe_symbol": safe_symbol(ticker),
        "analysis_date": analysis_date,
        "started_at": started.isoformat(timespec="seconds"),
        "runtime_mode": runtime_mode,
        "trigger": trigger,
        "scheduled_for": scheduled_for,
        "status": status,
        "publication_role": publication_role,
        "publication_event_id": old_event_id,
        "run_uid": "legacy-" + hashlib.sha256(run_dir.name.encode("utf-8")).hexdigest()[:24],
        "content_digest": file_digest(run_dir),
    }


def assign_recoveries(entries: list[dict[str, Any]]) -> None:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for entry in entries:
        groups.setdefault((entry["analysis_date"], entry["safe_symbol"]), []).append(entry)
    for group in groups.values():
        ordered = sorted(group, key=lambda item: item["started_at"])
        failed_prod = next(
            (
                item
                for item in ordered
                if item["runtime_mode"] == "prod"
                and item["trigger"] == "scheduled"
                and item["status"] != "completed"
            ),
            None,
        )
        if failed_prod:
            for item in ordered:
                if (
                    item["started_at"] > failed_prod["started_at"]
                    and item["status"] == "completed"
                    and item["publication_role"] == "official"
                ):
                    item["runtime_mode"] = "prod"
                    item["trigger"] = "recovery"
                    item["parent_run_id"] = failed_prod["old_name"]
                    break


def assign_attempts(entries: list[dict[str, Any]]) -> None:
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for entry in entries:
        key = (entry["analysis_date"], entry["safe_symbol"], entry["runtime_mode"])
        groups.setdefault(key, []).append(entry)
    for group in groups.values():
        for attempt, entry in enumerate(
            sorted(group, key=lambda item: item["started_at"]), start=1
        ):
            entry["attempt"] = attempt
            stamp = datetime.fromisoformat(entry["started_at"]).strftime("%Y%m%d_%H%M%S")
            analysis = entry["analysis_date"].replace("-", "")
            entry["new_name"] = (
                f"{stamp}__asof-{analysis}__{entry['runtime_mode']}__"
                f"{entry['trigger']}-a{attempt:02d}__{entry['safe_symbol']}"
            )


def build_manifest(args: argparse.Namespace) -> dict[str, Any]:
    run_root = args.results_root / "运行结果"
    if not run_root.is_dir():
        raise MigrationError(f"运行结果目录不存在：{run_root}")
    csv_rows = load_csv_rows(args.csv)
    csv_event_ids = {row.get("event_id", "") for row in csv_rows if row.get("event_id")}
    histories = load_history(args.production_history, "prod") + load_history(
        args.development_history, "dev"
    )
    entries = []
    for run_dir in sorted(item for item in run_root.iterdir() if item.is_dir()):
        if parse_run_directory_name(run_dir.name):
            continue
        entries.append(inspect_run(run_dir, csv_event_ids, histories))
    legacy_logs = []
    for path in sorted(item for item in run_root.iterdir() if item.is_file()):
        if path.suffix != ".log":
            continue
        target = args.results_root / LEGACY_LOG_DIR / path.name
        if target.exists():
            raise MigrationError(f"历史日志归档目标已经存在：{target.name}")
        legacy_logs.append(
            {
                "name": path.name,
                "sha256": single_file_digest(path),
            }
        )
    assign_recoveries(entries)
    assign_attempts(entries)
    targets = [entry["new_name"] for entry in entries]
    if len(targets) != len(set(targets)):
        raise MigrationError("迁移目标目录名发生冲突。")
    for entry in entries:
        target = run_root / entry["new_name"]
        if target.exists() and target.name != entry["old_name"]:
            raise MigrationError(f"迁移目标已经存在：{target.name}")
        if entry.get("publication_event_id") and load_json(
            run_root / entry["old_name"] / "最终决策.json"
        ):
            decision = load_json(run_root / entry["old_name"] / "最终决策.json")
            entry["legacy_alias_event_id"] = stable_event_id(entry["new_name"], decision)
    return {
        "migration_version": MIGRATION_VERSION,
        "naming_schema_version": RUN_NAMING_SCHEMA_VERSION,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "results_root": str(args.results_root.resolve()),
        "csv_path": str(args.csv.resolve()),
        "csv_sha256_before": hashlib.sha256(args.csv.read_bytes()).hexdigest(),
        "csv_rows_before": len(csv_rows),
        "entries": entries,
        "legacy_logs": legacy_logs,
        "status": "planned",
    }


def ensure_state_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS official_results (
            analysis_date TEXT NOT NULL,
            ticker TEXT NOT NULL,
            event_id TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (analysis_date, ticker)
        );
        """
    )


def migrate_state(path: Path, entries: list[dict[str, Any]], backup: Path) -> None:
    if not path.is_file():
        return
    backup.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with sqlite3.connect(path) as source, sqlite3.connect(backup) as destination:
        source.backup(destination)
    os.chmod(backup, 0o600)
    timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
    with sqlite3.connect(path) as connection:
        ensure_state_schema(connection)
        for entry in entries:
            baseline_path_key = "baseline-run:" + hashlib.sha256(
                str(Path(entry["new_path"]).resolve()).encode("utf-8")
            ).hexdigest()
            connection.execute(
                "INSERT INTO metadata(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (baseline_path_key, timestamp),
            )
            event_id = entry.get("publication_event_id")
            if event_id:
                connection.execute(
                    "UPDATE events SET run_id = ?, run_path = ? WHERE event_id = ?",
                    (
                        entry["new_name"],
                        str((Path(entry["new_path"])).resolve()),
                        event_id,
                    ),
                )
                if entry["publication_role"] == "official":
                    connection.execute(
                        "INSERT INTO official_results"
                        "(analysis_date, ticker, event_id, updated_at) VALUES(?, ?, ?, ?) "
                        "ON CONFLICT(analysis_date, ticker) DO UPDATE SET "
                        "event_id = excluded.event_id, updated_at = excluded.updated_at",
                        (entry["analysis_date"], entry["ticker"], event_id, timestamp),
                    )
            alias = entry.get("legacy_alias_event_id")
            if alias and alias != event_id:
                connection.execute(
                    "INSERT OR IGNORE INTO events"
                    "(event_id, run_id, run_path, disposition, discovered_at) "
                    "VALUES(?, ?, ?, 'baseline', ?)",
                    (alias, entry["new_name"], entry["new_path"], timestamp),
                )
                for sink in ("csv", "local_message", "feishu", "feishu_sheet"):
                    connection.execute(
                        "INSERT OR IGNORE INTO deliveries"
                        "(event_id, sink, status, attempts, updated_at) "
                        "VALUES(?, ?, 'skipped', 0, ?)",
                        (alias, sink, timestamp),
                    )


def manifest_payload(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "2.0",
        "naming_schema_version": RUN_NAMING_SCHEMA_VERSION,
        "run_uid": entry["run_uid"],
        "run_id": entry["new_name"],
        "legacy_run_id": entry["old_name"],
        "ticker": entry["ticker"],
        "analysis_date": entry["analysis_date"],
        "started_at": entry["started_at"],
        "runtime_mode": entry["runtime_mode"],
        "trigger": entry["trigger"],
        "attempt": entry["attempt"],
        "scheduled_for": entry.get("scheduled_for"),
        "parent_run_id": entry.get("parent_run_id"),
        "status": entry["status"],
        "publication_role": entry["publication_role"],
        "publication_event_id": entry.get("publication_event_id"),
        "migrated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def apply_migration(args: argparse.Namespace, plan: dict[str, Any]) -> None:
    run_root = args.results_root / "运行结果"
    migration_dir = args.migration_file.parent
    migration_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    plan["status"] = "applying"
    plan["applied_directories"] = []
    plan["archived_logs"] = []
    atomic_json_write(args.migration_file, plan)
    for entry in plan["entries"]:
        source = run_root / entry["old_name"]
        target = run_root / entry["new_name"]
        if not source.is_dir() or target.exists():
            raise MigrationError(f"目录状态已变化，拒绝迁移：{entry['old_name']}")
        atomic_json_write(source / RUN_MANIFEST_FILE, manifest_payload(entry))
        source.rename(target)
        entry["new_path"] = str(target.resolve())
        if file_digest(target) != entry["content_digest"]:
            raise MigrationError(f"迁移后内容校验失败：{entry['new_name']}")
        plan["applied_directories"].append(entry["new_name"])
        atomic_json_write(args.migration_file, plan)
    log_archive = args.results_root / LEGACY_LOG_DIR
    for item in plan.get("legacy_logs", []):
        source = run_root / item["name"]
        target = log_archive / item["name"]
        if not source.is_file() or target.exists():
            raise MigrationError(f"历史日志状态已变化：{item['name']}")
        log_archive.mkdir(parents=True, exist_ok=True, mode=0o700)
        source.rename(target)
        if single_file_digest(target) != item["sha256"]:
            raise MigrationError(f"历史日志校验失败：{item['name']}")
        plan["archived_logs"].append(item["name"])
        atomic_json_write(args.migration_file, plan)
    for position, state_path in enumerate(args.publisher_state):
        migrate_state(
            state_path,
            plan["entries"],
            migration_dir / f"publisher-{position}.sqlite3.bak",
        )
    if hashlib.sha256(args.csv.read_bytes()).hexdigest() != plan["csv_sha256_before"]:
        raise MigrationError("迁移期间 CSV 内容发生变化。")
    plan["status"] = "applied"
    plan["applied_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    atomic_json_write(args.migration_file, plan)


def rollback_migration(args: argparse.Namespace, plan: dict[str, Any]) -> None:
    if plan.get("status") not in {"applying", "applied"}:
        raise MigrationError("没有可回滚的已应用迁移。")
    run_root = args.results_root / "运行结果"
    log_archive = args.results_root / LEGACY_LOG_DIR
    archived_logs = set(
        plan.get("archived_logs")
        or [item["name"] for item in plan.get("legacy_logs", [])]
    )
    for item in reversed(plan.get("legacy_logs", [])):
        if item["name"] not in archived_logs:
            continue
        source = log_archive / item["name"]
        target = run_root / item["name"]
        if not source.is_file() or target.exists():
            raise MigrationError(f"历史日志状态不允许回滚：{item['name']}")
        source.rename(target)
        if single_file_digest(target) != item["sha256"]:
            raise MigrationError(f"历史日志回滚校验失败：{item['name']}")
    if log_archive.is_dir() and not any(log_archive.iterdir()):
        log_archive.rmdir()
    applied_directories = set(
        plan.get("applied_directories")
        or [entry["new_name"] for entry in plan.get("entries", [])]
    )
    for entry in reversed(plan["entries"]):
        if entry["new_name"] not in applied_directories:
            continue
        source = run_root / entry["new_name"]
        target = run_root / entry["old_name"]
        if not source.is_dir() or target.exists():
            raise MigrationError(f"目录状态不允许回滚：{entry['new_name']}")
        source.rename(target)
        manifest_path = target / RUN_MANIFEST_FILE
        if manifest_path.exists():
            manifest_path.unlink()
        if file_digest(target) != entry["content_digest"]:
            raise MigrationError(f"回滚后内容校验失败：{entry['old_name']}")
    for position, state_path in enumerate(args.publisher_state):
        backup = args.migration_file.parent / f"publisher-{position}.sqlite3.bak"
        if state_path.is_file() and backup.is_file():
            shutil.copy2(backup, state_path)
    plan["status"] = "rolled_back"
    plan["rolled_back_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    atomic_json_write(args.migration_file, plan)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--results-root", type=Path, default=PROJECT_ROOT / "my_results"
    )
    parser.add_argument("--csv", type=Path)
    parser.add_argument("--publisher-state", type=Path, action="append")
    parser.add_argument("--production-history", type=Path)
    parser.add_argument("--development-history", type=Path)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--apply", action="store_true")
    action.add_argument("--rollback", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.results_root = args.results_root.expanduser().resolve()
    args.csv = (args.csv or args.results_root / "汇总" / "每日决策.csv").resolve()
    args.publisher_state = args.publisher_state or [
        PROJECT_ROOT / ".runtime" / "production" / "publisher" / "publisher.sqlite3"
    ]
    args.publisher_state = [path.expanduser().resolve() for path in args.publisher_state]
    args.production_history = (
        args.production_history
        or PROJECT_ROOT / ".runtime" / "production" / "control-panel" / "runs.json"
    ).resolve()
    args.development_history = (
        args.development_history
        or PROJECT_ROOT / ".runtime" / "development" / "worktree" / ".runtime"
        / "control-panel" / "runs.json"
    ).resolve()
    args.migration_file = (
        args.results_root / ".migrations" / MIGRATION_VERSION / "manifest.json"
    )
    if args.rollback:
        plan = load_json(args.migration_file)
        rollback_migration(args, plan)
        print(f"status=rolled_back directories={len(plan.get('entries', []))}")
        return
    if args.migration_file.exists():
        previous = load_json(args.migration_file)
        if previous.get("status") == "applied":
            raise MigrationError("迁移已经应用。")
        if previous.get("status") == "applying":
            raise MigrationError("发现未完成迁移，请先使用 --rollback。")
    plan = build_manifest(args)
    if args.apply:
        apply_migration(args, plan)
        print(f"status=applied directories={len(plan['entries'])}")
    else:
        status_counts: dict[str, int] = {}
        for entry in plan["entries"]:
            status_counts[entry["status"]] = status_counts.get(entry["status"], 0) + 1
        print(
            "status=dry_run "
            f"directories={len(plan['entries'])} "
            f"legacy_logs={len(plan.get('legacy_logs', []))} "
            f"completed={status_counts.get('completed', 0)} "
            f"incomplete={status_counts.get('incomplete', 0)} "
            f"failed={status_counts.get('failed', 0)}"
        )


if __name__ == "__main__":
    try:
        main()
    except MigrationError as exc:
        raise SystemExit(f"migration error: {exc}") from None
