"""InduVista FastAPI app.

Phase 0–2 deliverables:  /, /health
Phase 3:                 /api/protocol-connectors, /api/channels,
                          /api/devices, /api/register-blocks, /api/tags
Phase 5:                 /api/diagnostics/*
Phase 6 (slice 2):       /api/live, /api/live/groups

Interactive docs at /docs (Swagger UI) and /redoc (ReDoc).
"""

import time
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api import (
    channels,
    devices,
    diagnostics,
    engineering_units,
    frames,
    groups,
    live,
    named_sets,
    register_blocks,
    tags,
    writes,
)


# Phase 7 E1d — system heartbeat. Captured at module import so it persists
# across requests. Each /health call increments the cycle counter, giving
# external monitors a monotonic value to watch (so they can detect when the
# API process freezes vs when it's simply unreachable).
_APP_STARTED_AT_MONO = time.monotonic()
_APP_STARTED_AT_ISO = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
_HEALTH_CYCLE_COUNT = 0
from app.config import settings
from app.db import get_session

app = FastAPI(
    title=settings.app_name,
    description="Industrial data acquisition and reporting tool.",
    version="0.3.0",
)


@app.get("/")
def root() -> dict[str, str]:
    return {
        "name": settings.app_name,
        "env": settings.app_env,
        "version": app.version,
        "docs": "/docs",
        "health": "/health",
    }


@app.get("/health")
def health(db: Annotated[Session, Depends(get_session)]) -> dict[str, object]:
    """Liveness + readiness in one endpoint.

    - Confirms the DB pool can hand out a working connection.
    - Reports DB round-trip latency.
    - Reports the current Alembic revision.
    Returns HTTP 503 if the DB is unreachable; otherwise 200.
    """
    start = time.perf_counter()
    try:
        db.execute(text("SELECT 1"))
        db_latency_ms = (time.perf_counter() - start) * 1000
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"database unreachable: {exc.__class__.__name__}",
        ) from exc

    try:
        migration_version = db.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar()
    except SQLAlchemyError:
        migration_version = None

    return {
        "status": "ok",
        "app_name": settings.app_name,
        "app_env": settings.app_env,
        "app_timezone": settings.app_timezone,
        "role": settings.node_role,
        "db_latency_ms": round(db_latency_ms, 2),
        "migration_version": migration_version,
        # Phase 7 E1d — system heartbeat. uptime_sec increases monotonically;
        # cycle_count increments on every /health call. External monitors can
        # detect a frozen API process by watching cycle_count.
        "uptime_sec": round(time.monotonic() - _APP_STARTED_AT_MONO, 1),
        "started_at": _APP_STARTED_AT_ISO,
        "cycle_count": _next_health_cycle(),
    }


def _next_health_cycle() -> int:
    global _HEALTH_CYCLE_COUNT
    _HEALTH_CYCLE_COUNT += 1
    return _HEALTH_CYCLE_COUNT


# Phase 3 routers
app.include_router(channels.router)
app.include_router(devices.router)
app.include_router(register_blocks.router)
app.include_router(tags.router)

# Phase 5 router
app.include_router(diagnostics.router)
# Phase 6 router
app.include_router(live.router)
# Phase 7 Batch 2 router — Frame Inspector
app.include_router(frames.router)
# Phase 8.1 router — engineering units master
app.include_router(engineering_units.router)
# Phase 8.2 router — groups master
app.include_router(groups.router)
# Phase 8.3 router — named sets master
app.include_router(named_sets.router)
# Phase 8.5 router — tag writes + audit journal
app.include_router(writes.router)
