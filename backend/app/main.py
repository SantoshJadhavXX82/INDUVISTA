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

from app.api import channels, devices, diagnostics, live, register_blocks, tags
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
    }


# Phase 3 routers
app.include_router(channels.router)
app.include_router(devices.router)
app.include_router(register_blocks.router)
app.include_router(tags.router)

# Phase 5 router
app.include_router(diagnostics.router)

# Phase 6 router
app.include_router(live.router)
