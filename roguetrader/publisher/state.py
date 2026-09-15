"""SQLite state for idempotent, independently retryable publication sinks."""

from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import sqlite3
from typing import Iterator

from roguetrader.publisher.models import DecisionRecord, now_iso


class PublicationState:
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.path.parent, 0o700)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    run_path TEXT NOT NULL,
                    disposition TEXT NOT NULL,
                    discovered_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS deliveries (
                    event_id TEXT NOT NULL,
                    sink TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    updated_at TEXT NOT NULL,
                    first_attempt_at TEXT,
                    next_attempt_at TEXT,
                    expires_at TEXT,
                    PRIMARY KEY (event_id, sink),
                    FOREIGN KEY (event_id) REFERENCES events(event_id)
                );
                CREATE TABLE IF NOT EXISTS official_results (
                    analysis_date TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (analysis_date, ticker),
                    FOREIGN KEY (event_id) REFERENCES events(event_id)
                );
                """
            )
            columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(deliveries)").fetchall()
            }
            for name in ("first_attempt_at", "next_attempt_at", "expires_at"):
                if name not in columns:
                    connection.execute(f"ALTER TABLE deliveries ADD COLUMN {name} TEXT")
        os.chmod(self.path, 0o600)

    def metadata(self, key: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM metadata WHERE key = ?", (key,)
            ).fetchone()
        return str(row["value"]) if row else None

    def set_metadata(self, key: str, value: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO metadata(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def register_event(
        self,
        record: DecisionRecord,
        run_path: str | Path,
        disposition: str,
    ) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO events"
                "(event_id, run_id, run_path, disposition, discovered_at) "
                "VALUES(?, ?, ?, ?, ?)",
                (
                    record.event_id,
                    record.run_id,
                    str(Path(run_path).resolve()),
                    disposition,
                    now_iso(),
                ),
            )
        return cursor.rowcount == 1

    def disposition(self, event_id: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT disposition FROM events WHERE event_id = ?", (event_id,)
            ).fetchone()
        return str(row["disposition"]) if row else None

    def set_disposition(self, event_id: str, disposition: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE events SET disposition = ? WHERE event_id = ?",
                (disposition, event_id),
            )

    def update_event_location(
        self, event_id: str, *, run_id: str, run_path: str | Path
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE events SET run_id = ?, run_path = ? WHERE event_id = ?",
                (run_id, str(Path(run_path).resolve()), event_id),
            )

    def official_event(self, analysis_date: str, ticker: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT event_id FROM official_results "
                "WHERE analysis_date = ? AND ticker = ?",
                (analysis_date, ticker),
            ).fetchone()
        return str(row["event_id"]) if row else None

    def claim_official(self, record: DecisionRecord) -> bool:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO official_results"
                "(analysis_date, ticker, event_id, updated_at) VALUES(?, ?, ?, ?)",
                (record.trade_date, record.ticker, record.event_id, now_iso()),
            )
            row = connection.execute(
                "SELECT event_id FROM official_results "
                "WHERE analysis_date = ? AND ticker = ?",
                (record.trade_date, record.ticker),
            ).fetchone()
        return bool(row and str(row["event_id"]) == record.event_id)

    def promote_official(self, record: DecisionRecord) -> str | None:
        previous = self.official_event(record.trade_date, record.ticker)
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO official_results"
                "(analysis_date, ticker, event_id, updated_at) VALUES(?, ?, ?, ?) "
                "ON CONFLICT(analysis_date, ticker) DO UPDATE SET "
                "event_id = excluded.event_id, updated_at = excluded.updated_at",
                (record.trade_date, record.ticker, record.event_id, now_iso()),
            )
            if previous and previous != record.event_id:
                connection.execute(
                    "UPDATE events SET disposition = 'candidate' WHERE event_id = ?",
                    (previous,),
                )
        return previous

    def register_legacy_alias(
        self,
        event_id: str,
        *,
        run_id: str,
        run_path: str | Path,
    ) -> None:
        timestamp = now_iso()
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO events"
                "(event_id, run_id, run_path, disposition, discovered_at) "
                "VALUES(?, ?, ?, 'baseline', ?)",
                (event_id, run_id, str(Path(run_path).resolve()), timestamp),
            )
            for sink in (
                "csv",
                "execution_csv",
                "local_message",
                "feishu",
                "feishu_sheet",
            ):
                connection.execute(
                    "INSERT OR IGNORE INTO deliveries"
                    "(event_id, sink, status, attempts, updated_at) "
                    "VALUES(?, ?, 'skipped', 0, ?)",
                    (event_id, sink, timestamp),
                )

    def events_for_disposition(self, disposition: str) -> list[dict[str, str]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT event_id, run_id, run_path FROM events "
                "WHERE disposition = ? ORDER BY discovered_at, event_id",
                (disposition,),
            ).fetchall()
        return [
            {
                "event_id": str(row["event_id"]),
                "run_id": str(row["run_id"]),
                "run_path": str(row["run_path"]),
            }
            for row in rows
        ]

    def delivery_status(self, event_id: str, sink: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT status FROM deliveries WHERE event_id = ? AND sink = ?",
                (event_id, sink),
            ).fetchone()
        return str(row["status"]) if row else None

    def delivery_details(self, event_id: str, sink: str) -> dict[str, object] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT status, attempts, last_error, updated_at, first_attempt_at, "
                "next_attempt_at, expires_at FROM deliveries "
                "WHERE event_id = ? AND sink = ?",
                (event_id, sink),
            ).fetchone()
        return dict(row) if row else None

    def begin_delivery(
        self,
        event_id: str,
        sink: str,
        *,
        attempted_at: str | None = None,
        expires_at: str | None = None,
    ) -> None:
        timestamp = attempted_at or now_iso()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO deliveries(
                    event_id, sink, status, attempts, updated_at,
                    first_attempt_at, next_attempt_at, expires_at
                )
                VALUES(?, ?, 'running', 1, ?, ?, NULL, ?)
                ON CONFLICT(event_id, sink) DO UPDATE SET
                    status = 'running',
                    attempts = deliveries.attempts + 1,
                    last_error = NULL,
                    updated_at = excluded.updated_at,
                    first_attempt_at = COALESCE(
                        deliveries.first_attempt_at, excluded.first_attempt_at
                    ),
                    next_attempt_at = NULL,
                    expires_at = COALESCE(deliveries.expires_at, excluded.expires_at)
                """,
                (event_id, sink, timestamp, timestamp, expires_at),
            )

    def finish_delivery(
        self,
        event_id: str,
        sink: str,
        *,
        success: bool,
        error: str | None = None,
        finished_at: str | None = None,
        next_attempt_at: str | None = None,
    ) -> None:
        timestamp = finished_at or now_iso()
        with self._connect() as connection:
            connection.execute(
                "UPDATE deliveries SET status = ?, last_error = ?, updated_at = ?, "
                "next_attempt_at = ? "
                "WHERE event_id = ? AND sink = ?",
                (
                    "success" if success else "failed",
                    None if success else str(error or "unknown error")[:1000],
                    timestamp,
                    None if success else next_attempt_at,
                    event_id,
                    sink,
                ),
            )

    def expire_delivery(self, event_id: str, sink: str, *, expired_at: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE deliveries SET status = 'expired', next_attempt_at = NULL, "
                "updated_at = ? WHERE event_id = ? AND sink = ?",
                (expired_at, event_id, sink),
            )

    def baseline_sink(self, sink: str) -> int:
        timestamp = now_iso()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO deliveries(
                    event_id, sink, status, attempts, updated_at
                )
                SELECT event_id, ?, 'skipped', 0, ? FROM events
                """,
                (sink, timestamp),
            )
        return cursor.rowcount

    def retry_sink_now(self, sink: str, *, timestamp: str | None = None) -> int:
        when = timestamp or now_iso()
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE deliveries SET next_attempt_at = ? "
                "WHERE sink = ? AND status = 'failed' "
                "AND (expires_at IS NULL OR expires_at > ?)",
                (when, sink, when),
            )
        return cursor.rowcount

    def reset_delivery(self, event_id: str, sink: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM deliveries WHERE event_id = ? AND sink = ? "
                "AND status != 'success'",
                (event_id, sink),
            )
        return cursor.rowcount == 1

    def force_retry_delivery(
        self, event_id: str, sink: str, *, timestamp: str | None = None
    ) -> bool:
        when = timestamp or now_iso()
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE deliveries SET status = 'failed', last_error = NULL, "
                "attempts = 0, updated_at = ?, first_attempt_at = NULL, "
                "next_attempt_at = ?, expires_at = NULL "
                "WHERE event_id = ? AND sink = ?",
                (when, when, event_id, sink),
            )
        return cursor.rowcount == 1

    def sink_status(self, sink: str) -> dict[str, object]:
        timestamp = now_iso()
        with self._connect() as connection:
            counts = {
                str(row["status"]): int(row["count"])
                for row in connection.execute(
                    "SELECT status, COUNT(*) AS count FROM deliveries "
                    "WHERE sink = ? GROUP BY status",
                    (sink,),
                ).fetchall()
            }
            success = connection.execute(
                "SELECT updated_at FROM deliveries WHERE sink = ? AND status = 'success' "
                "ORDER BY updated_at DESC LIMIT 1",
                (sink,),
            ).fetchone()
            error = connection.execute(
                "SELECT last_error FROM deliveries WHERE sink = ? "
                "AND status IN ('failed', 'expired') AND last_error IS NOT NULL "
                "ORDER BY updated_at DESC LIMIT 1",
                (sink,),
            ).fetchone()
            pending = connection.execute(
                "SELECT COUNT(*) AS count FROM deliveries WHERE sink = ? "
                "AND status IN ('running', 'failed') "
                "AND (expires_at IS NULL OR expires_at > ?)",
                (sink, timestamp),
            ).fetchone()
        return {
            "last_success_at": str(success["updated_at"]) if success else None,
            "last_error": str(error["last_error"]) if error else None,
            "pending": int(pending["count"]) if pending else 0,
            "expired": counts.get("expired", 0),
        }

    def delivery_summary(self, event_id: str) -> dict[str, str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT sink, status FROM deliveries WHERE event_id = ? ORDER BY sink",
                (event_id,),
            ).fetchall()
        return {str(row["sink"]): str(row["status"]) for row in rows}
