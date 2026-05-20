"""Phase 16.0g - GET /api/audit-log endpoint.

Lists audit events with filtering and pagination. Read-only - audit
rows are written only by the audit() helper in business handlers.
"""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import text

from app.db_audit import AuditSessionLocal


router = APIRouter(tags=["audit"])


@router.get("/api/audit-log")
def list_audit_log(
    action: str | None = Query(None, description="Filter by action prefix (e.g. 'calc.' or 'calc.delete')"),
    target_type: str | None = Query(None),
    target_id: str | None = Query(None),
    actor_ip: str | None = Query(None),
    status: str | None = Query(None, description="'success' | 'denied' | 'error'"),
    since: datetime | None = Query(None, description="ISO-8601 lower bound"),
    until: datetime | None = Query(None, description="ISO-8601 upper bound"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """List audit events. Returns {total, limit, offset, events: [...]}.

    Filters compose with AND. Action filter is a prefix match
    ('calc.' matches calc.create/calc.update/etc.), the rest are exact
    matches.
    """
    where_clauses: list[str] = []
    params: dict[str, Any] = {}

    if action:
        if action.endswith("."):
            where_clauses.append("action LIKE :action_prefix")
            params["action_prefix"] = action + "%"
        else:
            where_clauses.append("action = :action")
            params["action"] = action
    if target_type:
        where_clauses.append("target_type = :target_type")
        params["target_type"] = target_type
    if target_id:
        where_clauses.append("target_id = :target_id")
        params["target_id"] = target_id
    if actor_ip:
        where_clauses.append("host(actor_ip) = :actor_ip")
        params["actor_ip"] = actor_ip
    if status:
        where_clauses.append("status = :status")
        params["status"] = status
    if since:
        where_clauses.append("occurred_at >= :since")
        params["since"] = since
    if until:
        where_clauses.append("occurred_at < :until")
        params["until"] = until

    where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    with AuditSessionLocal() as db:
        total = db.execute(
            text(f"SELECT COUNT(*) FROM audit_log{where_sql}"), params
        ).scalar() or 0

        params_with_paging = {**params, "limit": limit, "offset": offset}
        rows = db.execute(text(f"""
            SELECT id, occurred_at, actor_type, actor_id, host(actor_ip) AS actor_ip,
                   action, target_type, target_id, target_label, summary,
                   details, status, error_message, correlation_id
            FROM audit_log
            {where_sql}
            ORDER BY occurred_at DESC, id DESC
            LIMIT :limit OFFSET :offset
        """), params_with_paging).mappings().all()

    events = [
        {
            "id":             r["id"],
            "occurred_at":    r["occurred_at"].isoformat() if r["occurred_at"] else None,
            "actor_type":     r["actor_type"],
            "actor_id":       r["actor_id"],
            "actor_ip":       r["actor_ip"],
            "action":         r["action"],
            "target_type":    r["target_type"],
            "target_id":      r["target_id"],
            "target_label":   r["target_label"],
            "summary":        r["summary"],
            "details":        r["details"],
            "status":         r["status"],
            "error_message":  r["error_message"],
            "correlation_id": str(r["correlation_id"]) if r["correlation_id"] else None,
        }
        for r in rows
    ]

    return {
        "total":  total,
        "limit":  limit,
        "offset": offset,
        "events": events,
    }


@router.get("/api/audit-log/actions")
def list_distinct_actions() -> list[str]:
    """Returns the distinct action codes seen so far. Used by the UI's
    action filter dropdown."""
    with AuditSessionLocal() as db:
        rows = db.execute(text("""
            SELECT DISTINCT action FROM audit_log ORDER BY action
        """)).scalars().all()
    return list(rows)
