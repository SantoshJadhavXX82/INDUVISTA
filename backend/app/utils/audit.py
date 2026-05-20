"""Phase 16.0g - Audit event recording helpers.

Three pieces:

  ensure_audit_schema()
    Idempotent setup of the audit_log table + hypertable + indexes +
    retention policy. Called once at backend startup. Safe to re-run.

  audit(...)
    Record a single audit event. Auto-extracts actor info from the
    FastAPI Request if provided. NEVER raises - if the audit write
    fails for any reason, we log a warning and continue. Audit
    failures must not break business operations.

  AuditEvent
    Dataclass for explicit call sites:

        audit(AuditEvent(
            action="calc.create",
            target_type="calc_definition",
            target_id=str(new_calc.id),
            target_label=f"{block_type} -> tag #{tag_id}",
            summary=f"Created {block_type} calc",
            details={"request": body.dict()},
        ), request)

Standard action naming convention: "<resource>.<verb>".
  calc.create / calc.update / calc.delete / calc.toggle
  tag.create / tag.update / tag.delete / tag.write
  alarm.ack / alarm.silence / alarm.clear
  device.create / device.update / device.delete / device.enable / device.disable
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from fastapi import Request
from sqlalchemy import text

from app.db_audit import engine_audit, AuditSessionLocal, AUDIT_RETENTION_DAYS


log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Schema setup (one-time, idempotent, runs at backend startup)
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS audit_log (
    id            BIGSERIAL NOT NULL,
    occurred_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    actor_type    VARCHAR(32) NOT NULL DEFAULT 'system',
    actor_id      VARCHAR(128),
    actor_ip      INET,
    action        VARCHAR(64) NOT NULL,
    target_type   VARCHAR(64),
    target_id     VARCHAR(64),
    target_label  VARCHAR(256),
    summary       TEXT,
    details       JSONB,
    status        VARCHAR(16) NOT NULL DEFAULT 'success',
    error_message TEXT,
    correlation_id UUID,
    PRIMARY KEY (id, occurred_at)
);
"""

_HYPERTABLE_SQL = """
SELECT create_hypertable(
    'audit_log',
    'occurred_at',
    if_not_exists => true,
    migrate_data  => true
);
"""

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS audit_log_action_time_idx ON audit_log (action, occurred_at DESC);",
    "CREATE INDEX IF NOT EXISTS audit_log_target_time_idx ON audit_log (target_type, target_id, occurred_at DESC);",
    "CREATE INDEX IF NOT EXISTS audit_log_actor_time_idx ON audit_log (actor_type, actor_id, occurred_at DESC);",
    "CREATE INDEX IF NOT EXISTS audit_log_status_time_idx ON audit_log (status, occurred_at DESC) WHERE status != 'success';",
]


def ensure_audit_schema() -> None:
    """Create table + hypertable + indexes + retention policy if missing.
    Safe to call on every backend startup."""
    with engine_audit.begin() as conn:
        # Table.
        conn.execute(text(_SCHEMA_SQL))

        # Hypertable (TimescaleDB).
        try:
            conn.execute(text(_HYPERTABLE_SQL))
        except Exception as e:
            # Already a hypertable, or timescaledb not installed yet.
            log.warning("audit_log hypertable conversion: %s", e)

        # Indexes.
        for sql in _INDEXES:
            conn.execute(text(sql))

        # Retention policy (drop chunks older than AUDIT_RETENTION_DAYS).
        try:
            conn.execute(text(
                f"SELECT add_retention_policy('audit_log', "
                f"INTERVAL '{AUDIT_RETENTION_DAYS} days', "
                f"if_not_exists => true);"
            ))
        except Exception as e:
            log.warning("audit_log retention policy: %s", e)

    log.info("audit_log schema ready (retention=%dd)", AUDIT_RETENTION_DAYS)


# ---------------------------------------------------------------------------
# AuditEvent dataclass + audit() recorder
# ---------------------------------------------------------------------------

@dataclass
class AuditEvent:
    """Describes one auditable action. Pass to audit() to record it."""
    action: str
    target_type: str | None = None
    target_id: str | int | None = None
    target_label: str | None = None
    summary: str | None = None
    details: dict[str, Any] | None = None
    status: str = "success"             # 'success' | 'denied' | 'error'
    error_message: str | None = None
    correlation_id: str | None = None


def _extract_actor(request: Request | None) -> tuple[str, str | None, str | None]:
    """Pull actor_type, actor_id, actor_ip from the request. Today we
    only have IP (no auth). When auth lands, this is the one place to
    extract user from a JWT or session cookie."""
    actor_type = "system"
    actor_id = None
    actor_ip = None
    if request is not None:
        actor_type = "api_client"
        actor_ip = request.client.host if request.client else None
        # Future: actor_id = decoded_jwt.user_id, actor_type = "user"
    return actor_type, actor_id, actor_ip


def audit(event: AuditEvent, request: Request | None = None) -> None:
    """Record an audit event. Always succeeds (failures logged, not raised).
    The audit write is a separate transaction in a separate database,
    so it does NOT join the caller's business transaction - audit
    durability is independent of business operation success."""
    try:
        actor_type, actor_id, actor_ip = _extract_actor(request)

        details_json = None
        if event.details is not None:
            try:
                details_json = json.dumps(event.details, default=str)
            except Exception as e:
                details_json = json.dumps({"_serialization_error": str(e)})

        with AuditSessionLocal() as db:
            db.execute(text("""
                INSERT INTO audit_log (
                    actor_type, actor_id, actor_ip,
                    action, target_type, target_id, target_label, summary,
                    details, status, error_message, correlation_id
                ) VALUES (
                    :actor_type, :actor_id, :actor_ip,
                    :action, :target_type, :target_id, :target_label, :summary,
                    CAST(:details AS JSONB), :status, :error_message, :correlation_id
                )
            """), {
                "actor_type":    actor_type,
                "actor_id":      actor_id,
                "actor_ip":      actor_ip,
                "action":        event.action,
                "target_type":   event.target_type,
                "target_id":     str(event.target_id) if event.target_id is not None else None,
                "target_label":  event.target_label,
                "summary":       event.summary,
                "details":       details_json,
                "status":        event.status,
                "error_message": event.error_message,
                "correlation_id": event.correlation_id,
            })
            db.commit()
    except Exception as e:
        # Audit failure must not propagate to the caller.
        log.warning("audit write failed for action=%s: %s",
                    event.action, e, exc_info=True)
