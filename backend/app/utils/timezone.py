"""DB-backed app timezone with env fallback (Phase 27d MVP).

The plant timezone is stored in system_settings under the key
'app.timezone'. This module provides a thread-safe, 60s-cached
lookup that backends should use anywhere they currently reference
settings.app_timezone — heatmap endpoints, calendar views, future
report scheduler.

  HEATMAP CALLER PATTERN
  ----------------------
    from app.utils.timezone import get_app_timezone
    tz = get_app_timezone()
    # ... use in extract(... AT TIME ZONE :tz), time_bucket(..., :tz), ...

  CACHE
  -----
    TTL: 60 seconds. Reasonable for an operator-facing setting that
    rarely changes. Cache invalidation happens automatically (TTL) or
    on explicit invalidate_timezone_cache() after a PATCH.

  FALLBACK
  --------
    If the DB is unreachable (e.g. during a Postgres restart or before
    migrations have run), falls back to settings.app_timezone from
    the env var. Logged at DEBUG so it's diagnosable without spamming
    the log.

  THREAD SAFETY
  -------------
    The cache is protected by a Lock. FastAPI workers, modbus workers,
    and calc workers all use this helper safely under concurrent
    requests. The lock is only held for the duration of a single
    SELECT, so contention is negligible.
"""

from __future__ import annotations

import logging
import threading
import time

from sqlalchemy import text

from app.config import settings


log = logging.getLogger(__name__)


_CACHE_TTL_SEC = 60.0
_cache_lock = threading.Lock()
_cache_value: str | None = None
_cache_expires_at: float = 0.0


def get_app_timezone() -> str:
    """Return the current app timezone (IANA name, e.g. 'Asia/Kolkata').

    Resolution order:
      1. In-memory cache, if fresh (TTL 60s)
      2. system_settings.app.timezone in DB
      3. settings.app_timezone env-loaded fallback

    Never raises — if anything goes wrong, the env fallback wins.
    """
    global _cache_value, _cache_expires_at

    now = time.monotonic()
    with _cache_lock:
        if now < _cache_expires_at and _cache_value is not None:
            return _cache_value

        tz_value: str | None = None
        try:
            # Lazy import to avoid a circular dep at app boot time
            from app.db import engine

            with engine.connect() as conn:
                row = conn.execute(
                    text(
                        "SELECT value FROM system_settings "
                        "WHERE key = 'app.timezone'"
                    )
                ).first()
            if row and row[0]:
                tz_value = row[0]
        except Exception as e:
            # DB unreachable or query failed — fall back to env. We
            # don't elevate this past DEBUG because during startup or
            # a brief Postgres blip this can fire repeatedly and would
            # otherwise drown the log.
            log.debug(
                "system_settings.app.timezone lookup failed: %s; "
                "falling back to settings.app_timezone",
                e,
            )

        if not tz_value:
            tz_value = settings.app_timezone

        _cache_value = tz_value
        _cache_expires_at = now + _CACHE_TTL_SEC
        return tz_value


def invalidate_timezone_cache() -> None:
    """Force the next get_app_timezone() to re-read from DB.

    Called from PATCH /api/settings after the timezone is updated, so
    operators see the change in heatmaps and trend charts within the
    next request rather than after the 60s TTL.
    """
    global _cache_value, _cache_expires_at
    with _cache_lock:
        _cache_value = None
        _cache_expires_at = 0.0
