"""SQLite-backed store-and-forward buffer.

Phase OPC.2: schema initialization + a few read helpers (pending_count,
close) so the UI can show real numbers. The enqueue / drain / dead-
letter logic lands in OPC.4 when the real pusher arrives.

The database file lives at %APPDATA%\\InduVista\\DataHub\\store_forward.db
on Windows; see core/paths.py for cross-platform resolution.

Concurrency: WAL mode lets a reader and a writer operate at the same
time without locking each other out. Each thread that needs a
connection opens its own (sqlite3 connections aren't thread-safe to
share, even with check_same_thread=False).
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from threading import Lock


log = logging.getLogger(__name__)


# Bump this when the schema changes; the StoreForward.initialize()
# routine will apply migrations between versions in future phases.
SCHEMA_VERSION = 1


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS pending_samples (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tag_id          INTEGER NOT NULL,
    time            TEXT NOT NULL,
    value_double    REAL,
    value_text      TEXT,
    st              INTEGER NOT NULL DEFAULT 192,
    st_reason       TEXT,
    inserted_at     TEXT NOT NULL DEFAULT (datetime('now')),
    retry_count     INTEGER NOT NULL DEFAULT 0,
    last_error      TEXT
);

CREATE INDEX IF NOT EXISTS idx_pending_inserted
    ON pending_samples(inserted_at);

CREATE TABLE IF NOT EXISTS metadata (
    key             TEXT PRIMARY KEY,
    value           TEXT NOT NULL,
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


class StoreForward:
    """Thin wrapper over the SQLite buffer file.

    Threading: this class holds one connection on the main thread for
    quick reads (pending_count etc). Worker threads should call
    `open_connection()` to get their own; never share an instance's
    `_conn` across threads.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._lock = Lock()
        self._conn: sqlite3.Connection | None = None

    # ── Lifecycle ─────────────────────────────────────────────────────

    def initialize(self) -> None:
        """Create the database file if it doesn't exist, ensure the
        schema is up to date, and record the schema version in
        metadata. Idempotent."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(self.db_path, isolation_level=None)
        try:
            # WAL + sane sync — durable enough for our use case and
            # avoids most reader/writer contention.
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute("PRAGMA foreign_keys=ON;")
            conn.executescript(_SCHEMA_SQL)
            conn.execute(
                "INSERT OR REPLACE INTO metadata(key, value, updated_at) "
                "VALUES(?, ?, datetime('now'))",
                ("schema_version", str(SCHEMA_VERSION)),
            )
            log.info("Store-forward initialized at %s (schema v%d)",
                     self.db_path, SCHEMA_VERSION)
        finally:
            conn.close()

        # Open the main-thread connection now so subsequent reads
        # don't pay the connect cost.
        self._conn = self.open_connection()

    def open_connection(self) -> sqlite3.Connection:
        """Mint a new sqlite3.Connection. Caller owns the lifetime —
        close it before the owning thread exits."""
        conn = sqlite3.connect(self.db_path, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.row_factory = sqlite3.Row
        return conn

    def close(self) -> None:
        """Release the main-thread connection. Worker threads close
        their own."""
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception:
                    log.exception("Error closing store-forward connection")
                self._conn = None

    # ── Read helpers ──────────────────────────────────────────────────

    def pending_count(self) -> int:
        """Number of samples currently waiting to be pushed."""
        with self._lock:
            conn = self._conn or self.open_connection()
            try:
                row = conn.execute("SELECT COUNT(*) FROM pending_samples").fetchone()
                return int(row[0]) if row else 0
            except sqlite3.DatabaseError:
                log.exception("pending_count failed")
                return 0

    def get_metadata(self, key: str) -> str | None:
        """Look up a single metadata value. Returns None if not set."""
        with self._lock:
            conn = self._conn or self.open_connection()
            row = conn.execute(
                "SELECT value FROM metadata WHERE key = ?", (key,),
            ).fetchone()
            if row is None:
                return None
            return row["value"] if isinstance(row, sqlite3.Row) else row[0]

    # OPC.4 will add: enqueue(samples), claim_batch(limit), mark_pushed(ids),
    # mark_failed(ids, error), move_to_dead_letter(ids), etc.
