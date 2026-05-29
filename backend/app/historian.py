"""HistorianWriter — the single write path for tag values.

Phase 1 contract: synchronous write to tag_values (insert) and
latest_tag_values (upsert). A failed write raises; the worker logs and the
sample is lost.

Phase 2: BufferedHistorianWriter wraps the direct path with a local SQLite
store-and-forward fallback. A replay loop in modbus_supervisor drains the
buffer when Postgres returns.

Phase 3.5: tag rows can now disappear under us between sample acquisition
and write (because the CRUD API allows DELETE on a tag mid-cycle). Both
write paths detect FK violations on tag_values.tag_id → tags.id, identify
which tag_ids are gone, drop their samples, and retry. This applies to
both live writes and replay drains — a buffer must never get permanently
stuck because of one orphaned sample.

Design notes:
  * Live writes go to BOTH tag_values and latest_tag_values.
  * The latest upsert has a guard `WHERE latest.time < EXCLUDED.time` so
    out-of-order replay cannot overwrite a newer live value. The replay
    worker MUST NOT call write_samples — it writes only to tag_values
    directly via write_history_only to avoid this race entirely.
  * tag_values uses ON CONFLICT DO NOTHING on the (tag_id, time) PK so
    replays of already-stored points are idempotent.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Sequence

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

log = logging.getLogger(__name__)


@dataclass
class Sample:
    """One acquired value, ready to write."""
    tag_id: int
    device_id: int
    register_block_id: Optional[int]
    time: datetime              # UTC, aware
    value_double: Optional[float]
    value_text: Optional[str]
    st: int                     # 0-255
    st_reason: Optional[str]    # free-form, e.g. 'READ_OK', 'COMM_TIMEOUT'
    source: str = "modbus"


# We never insert st_hex or st_class — they're STORED GENERATED columns that
# Postgres computes from `st`. Inserting them raises a generated-column error.
_INSERT_HISTORY_SQL = text("""
    INSERT INTO tag_values (
        time, tag_id, device_id, register_block_id,
        value_double, value_text, st, st_reason, source
    ) VALUES (
        :time, :tag_id, :device_id, :register_block_id,
        :value_double, :value_text, :st, :st_reason, :source
    )
    ON CONFLICT (tag_id, time) DO NOTHING
""")

_UPSERT_LATEST_SQL = text("""
    INSERT INTO latest_tag_values (
        tag_id, device_id, register_block_id, time,
        value_double, value_text, st, st_reason, source, updated_at
    ) VALUES (
        :tag_id, :device_id, :register_block_id, :time,
        :value_double, :value_text, :st, :st_reason, :source, now()
    )
    ON CONFLICT (tag_id) DO UPDATE SET
        device_id          = EXCLUDED.device_id,
        register_block_id  = EXCLUDED.register_block_id,
        time               = EXCLUDED.time,
        value_double       = EXCLUDED.value_double,
        value_text         = EXCLUDED.value_text,
        st                 = EXCLUDED.st,
        st_reason          = EXCLUDED.st_reason,
        source             = EXCLUDED.source,
        updated_at         = now()
    WHERE latest_tag_values.time < EXCLUDED.time
""")


class HistorianWriter:
    """Phase 1+3.5: direct write with FK-violation recovery.

    Live writes are atomic per batch. If a tag was deleted between sample
    acquisition and write, the FK violation is caught, the orphaned samples
    are dropped with a warning, and the remaining valid samples are written.
    """

    def __init__(self, engine: Engine):
        self._engine = engine

    @staticmethod
    def _rows_from(samples: Sequence[Sample]) -> list[dict]:
        return [
            {
                "tag_id": s.tag_id,
                "device_id": s.device_id,
                "register_block_id": s.register_block_id,
                "time": s.time,
                "value_double": s.value_double,
                "value_text": s.value_text,
                "st": s.st,
                "st_reason": s.st_reason,
                "source": s.source,
            }
            for s in samples
        ]

    @staticmethod
    def _is_fk_violation(exc: IntegrityError) -> bool:
        """True if this is a Postgres foreign_key_violation (SQLSTATE 23503)."""
        return getattr(getattr(exc, "orig", None), "pgcode", None) == "23503"

    def _drop_orphaned_rows(self, rows: list[dict]) -> tuple[list[dict], set[int]]:
        """Filter rows to those whose tag_id still exists. Returns (kept, dropped_tag_ids).

        Uses a fresh transaction. Called after an FK violation aborted the
        previous one — the prior connection state can't be reused.
        """
        tag_ids = {r["tag_id"] for r in rows}
        with self._engine.connect() as conn:
            existing_rows = conn.execute(
                text("SELECT id FROM tags WHERE id = ANY(:ids)"),
                {"ids": list(tag_ids)},
            ).scalars().all()
        existing = set(existing_rows)
        missing = tag_ids - existing
        if not missing:
            return rows, set()
        kept = [r for r in rows if r["tag_id"] not in missing]
        return kept, missing

    def _log_dropped(self, dropped_count: int, missing_tag_ids: set[int]) -> None:
        sample_ids = sorted(missing_tag_ids)[:5]
        suffix = ", ..." if len(missing_tag_ids) > 5 else ""
        log.warning(
            "Dropped %d sample(s) for %d deleted tag_id(s) [%s%s]",
            dropped_count, len(missing_tag_ids),
            ", ".join(str(i) for i in sample_ids), suffix,
        )

    def write_samples(self, samples: Sequence[Sample]) -> int:
        """Insert into tag_values + upsert latest_tag_values, atomically per batch.

        On FK violation (tag deleted mid-poll), drop the orphaned samples
        and retry with the remaining ones.

        Returns the count of samples actually attempted (dropped samples
        don't count). ON CONFLICT DO NOTHING in the tag_values insert can
        still swallow some duplicates, but those are counted as written.
        """
        if not samples:
            return 0

        rows = self._rows_from(samples)

        try:
            with self._engine.begin() as conn:
                conn.execute(_INSERT_HISTORY_SQL, rows)
                conn.execute(_UPSERT_LATEST_SQL, rows)
            return len(rows)
        except IntegrityError as e:
            if not self._is_fk_violation(e):
                raise

        # Phase 3.5: tag was deleted between acquisition and write. Drop
        # orphans and retry with the rest.
        kept, missing = self._drop_orphaned_rows(rows)
        if missing:
            self._log_dropped(len(rows) - len(kept), missing)
        if not kept:
            return 0
        with self._engine.begin() as conn:
            conn.execute(_INSERT_HISTORY_SQL, kept)
            conn.execute(_UPSERT_LATEST_SQL, kept)
        return len(kept)

    def write_history_only(self, samples: Sequence[Sample]) -> int:
        """Insert into tag_values ONLY — skip the latest_tag_values upsert.

        Used by the replay loop: buffered samples are old, and we must
        never overwrite a fresher value in latest_tag_values with a stale
        replayed one. tag_values is fine because its primary key includes
        time and the ON CONFLICT DO NOTHING absorbs any genuine duplicates.

        FK-violation recovery is identical to write_samples — drop orphans,
        retry. Critical here: without it, a single orphaned sample in the
        buffer would prevent the replay loop from ever draining.
        """
        if not samples:
            return 0
        rows = self._rows_from(samples)
        try:
            with self._engine.begin() as conn:
                conn.execute(_INSERT_HISTORY_SQL, rows)
            return len(rows)
        except IntegrityError as e:
            if not self._is_fk_violation(e):
                raise

        kept, missing = self._drop_orphaned_rows(rows)
        if missing:
            self._log_dropped(len(rows) - len(kept), missing)
        if not kept:
            return 0
        with self._engine.begin() as conn:
            conn.execute(_INSERT_HISTORY_SQL, kept)
        return len(kept)

    def write_latest_only(self, samples: Sequence[Sample]) -> int:
        """Upsert into latest_tag_values ONLY — skip tag_values (history).

        Used for samples the per-tag logging policy decided NOT to historize
        (e.g. on_change with the value still inside the deadband). The live
        value must still reflect the newest reading, so latest is updated.

        The latest upsert already guards `WHERE latest.time < EXCLUDED.time`,
        so this can never move latest backwards. FK-violation recovery matches
        write_samples: drop orphans (tag deleted mid-cycle), retry.
        """
        if not samples:
            return 0
        rows = self._rows_from(samples)
        try:
            with self._engine.begin() as conn:
                conn.execute(_UPSERT_LATEST_SQL, rows)
            return len(rows)
        except IntegrityError as e:
            if not self._is_fk_violation(e):
                raise
        kept, missing = self._drop_orphaned_rows(rows)
        if missing:
            self._log_dropped(len(rows) - len(kept), missing)
        if not kept:
            return 0
        with self._engine.begin() as conn:
            conn.execute(_UPSERT_LATEST_SQL, kept)
        return len(kept)

    def is_healthy(self, timeout_seconds: float = 2.0) -> bool:
        """Fast probe: can we reach Postgres right now?

        Used by the replay loop to decide whether to attempt a drain.
        Returns False on any connection or query error.
        """
        try:
            with self._engine.connect() as conn:
                conn.exec_driver_sql("SELECT 1")
            return True
        except Exception:
            return False


class BufferedHistorianWriter:
    """Wraps HistorianWriter with a local SQLite fallback.

    On any direct-write failure, samples are appended to the local buffer
    and the call returns successfully — the worker never sees an exception
    and keeps polling. A separate replay task (in modbus_supervisor) drains
    the buffer when Postgres returns.

    Phase 2 semantics:
      * Direct write attempted first on every batch.
      * On failure (any exception from the direct writer): batch goes to
        the local buffer with source unchanged (still 'modbus' or whatever
        the caller set). Replay marks them 'store_forward' on drain.
      * No bounded buffer size yet — a future phase adds eviction.
    """

    def __init__(self, direct: "HistorianWriter", buffer):  # buffer: LocalBuffer (avoid import cycle)
        self._direct = direct
        self._buffer = buffer
        # Used for log throttling so we don't spam during an outage
        self._consecutive_failures = 0

    def write_samples(self, samples: Sequence[Sample]) -> int:
        if not samples:
            return 0
        try:
            n = self._direct.write_samples(samples)
            if self._consecutive_failures > 0:
                log.info(
                    "Direct writes recovered after %d consecutive failures",
                    self._consecutive_failures,
                )
                self._consecutive_failures = 0
            return n
        except Exception as e:
            self._consecutive_failures += 1
            # Log loudly on first failure, then quietly while it persists
            if self._consecutive_failures == 1:
                log.warning(
                    "Direct historian write failed (%s); buffering %d samples locally",
                    e, len(samples),
                )
            elif self._consecutive_failures % 30 == 0:
                # Every ~30 cycles (≈30s at 1Hz) confirm we're still buffering
                log.warning(
                    "Still buffering (cycle %d): %d samples in this batch",
                    self._consecutive_failures, len(samples),
                )
            return self._buffer.append(list(samples))

    def write_latest_only(self, samples: Sequence[Sample]) -> int:
        """Best-effort latest-only update. If Postgres is down, no-op — the
        next successful poll refreshes latest. We never buffer latest-only
        samples (a stale latest must not be replayed over a fresh value)."""
        if not samples:
            return 0
        try:
            return self._direct.write_latest_only(samples)
        except Exception:
            return 0

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures
