"""Durable local buffer for samples that couldn't reach Postgres.

A SQLite file on a mounted volume. Survives worker restart. Independent
of the main Postgres so a Postgres outage doesn't cost samples.

Design:
  * One file: /data/sf_buffer.db (path overridable via constructor)
  * WAL journal mode — concurrent reads/writes are safe
  * Schema mirrors tag_values columns. Datetime stored as ISO 8601 text
    (SQLite has no native timezone-aware datetime; ISO 8601 preserves it).
  * PRIMARY KEY (tag_id, time_iso) — same idempotency contract as tag_values
  * No size cap in Phase 2. A future phase will add bounded retention.

The replay loop in modbus_supervisor reads in oldest-first order, writes to
Postgres via HistorianWriter.write_history_only (no latest_tag_values
overwrite), and deletes drained rows on success.
"""
from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import List, Sequence

from app.historian import Sample

log = logging.getLogger(__name__)


class LocalBuffer:
    """Durable on-disk queue for samples awaiting Postgres availability."""

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _init_schema(self) -> None:
        # PRAGMA journal_mode=WAL must run outside a transaction, so we use
        # a bare connection here rather than our normal _conn() context manager
        # (which begins a transaction automatically).
        bare = sqlite3.connect(str(self.db_path), timeout=30.0)
        try:
            bare.execute("PRAGMA journal_mode = WAL")
            bare.execute("PRAGMA synchronous = NORMAL")  # durable, not paranoid
            bare.commit()
        finally:
            bare.close()

        # Now the schema, inside a normal transaction.
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS buffered_samples (
                    tag_id            INTEGER NOT NULL,
                    time_iso          TEXT    NOT NULL,
                    device_id         INTEGER NOT NULL,
                    register_block_id INTEGER,
                    value_double      REAL,
                    value_text        TEXT,
                    st                INTEGER NOT NULL,
                    st_reason         TEXT,
                    source            TEXT    NOT NULL DEFAULT 'modbus',
                    PRIMARY KEY (tag_id, time_iso)
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_buffered_time "
                "ON buffered_samples(time_iso)"
            )

    @contextmanager
    def _conn(self):
        # New connection per call. SQLite connections aren't thread-safe by
        # default and `asyncio.to_thread` may run us on different threads.
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=30.0,
            isolation_level=None,  # autocommit; we manage txns explicitly
        )
        try:
            conn.execute("BEGIN")
            yield conn
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            conn.close()

    def append(self, samples: Sequence[Sample]) -> int:
        """Append samples. Returns the count submitted (some may be dups)."""
        if not samples:
            return 0
        rows = [
            (
                s.tag_id, s.time.isoformat(),
                s.device_id, s.register_block_id,
                s.value_double, s.value_text,
                s.st, s.st_reason, s.source,
            )
            for s in samples
        ]
        with self._conn() as conn:
            conn.executemany("""
                INSERT INTO buffered_samples (
                    tag_id, time_iso, device_id, register_block_id,
                    value_double, value_text, st, st_reason, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (tag_id, time_iso) DO NOTHING
            """, rows)
        return len(rows)

    def peek(self, limit: int = 500) -> List[Sample]:
        """Read the oldest `limit` samples. Does not remove them."""
        with self._conn() as conn:
            cursor = conn.execute("""
                SELECT tag_id, time_iso, device_id, register_block_id,
                       value_double, value_text, st, st_reason, source
                FROM buffered_samples
                ORDER BY time_iso
                LIMIT ?
            """, (limit,))
            rows = cursor.fetchall()
        return [
            Sample(
                tag_id=r[0],
                time=datetime.fromisoformat(r[1]),
                device_id=r[2],
                register_block_id=r[3],
                value_double=r[4],
                value_text=r[5],
                st=r[6],
                st_reason=r[7],
                source=r[8],
            )
            for r in rows
        ]

    def delete(self, samples: Sequence[Sample]) -> int:
        """Remove the given samples from the buffer."""
        if not samples:
            return 0
        keys = [(s.tag_id, s.time.isoformat()) for s in samples]
        with self._conn() as conn:
            conn.executemany(
                "DELETE FROM buffered_samples "
                "WHERE tag_id = ? AND time_iso = ?",
                keys,
            )
        return len(keys)

    def count(self) -> int:
        """Number of samples in the buffer."""
        with self._conn() as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM buffered_samples")
            return cursor.fetchone()[0]

    def oldest_time(self) -> datetime | None:
        """Time of the oldest sample in the buffer, or None if empty.

        Used by Phase 5b's buffer_status_loop to report how stuck the
        store-and-forward buffer is. A backlog with a recent oldest_time
        is just a brief outage; a backlog with an oldest_time hours ago
        means Postgres has been unreachable for a long time.
        """
        with self._conn() as conn:
            cursor = conn.execute(
                "SELECT MIN(time_iso) FROM buffered_samples"
            )
            row = cursor.fetchone()
        if not row or not row[0]:
            return None
        return datetime.fromisoformat(row[0])

    def is_empty(self) -> bool:
        return self.count() == 0
