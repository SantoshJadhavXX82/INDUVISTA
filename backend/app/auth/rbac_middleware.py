"""Global RBAC middleware (Phase 21).

Protects every API route by HTTP method + path, in ONE auditable place,
instead of decorating 74 endpoints across 25 router files.

POLICY (least-privilege, deny-by-default for writes)
====================================================
  Public (no auth):
    POST /api/auth/login            — how you obtain a token
    GET  /health, GET /             — liveness
    GET  /docs, /redoc, /openapi.json — API docs (dev convenience)

  API-key auth (handled by the route itself, middleware skips):
    /api/ingest                     — verify_api_key dependency

  Authenticated, by tier:
    GET/HEAD  (reads)               → viewer+   (any logged-in user)
    /api/admin/*  (any method)      → admin
    POST /api/tags/{id}/write       → operator+ (Modbus command/setpoint)
    POST /api/alarms/**/ack         → operator+ (acknowledge alarms)
    other writes (POST/PUT/PATCH/DELETE) → engineer+

HOW IT WORKS
  ASGI middleware reads Authorization, decodes the JWT, checks the tier
  for (method, path). On failure returns 401/403 JSON directly (never
  reaching the handler). On success the request proceeds; handlers can
  still inject get_current_user / require_role for finer control or to
  read the identity.

  Path matching is prefix/pattern based and intentionally simple — the
  policy table is the single source of truth and easy to review.
"""
from __future__ import annotations

import re
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.auth.roles import Role, role_at_least
from app.auth.security import decode_token


# Paths that need NO authentication at all.
_PUBLIC_EXACT = {"/", "/health", "/docs", "/redoc", "/openapi.json"}
_PUBLIC_PREFIXES = ("/docs", "/redoc")  # swagger assets

# Login is public (it mints the token).
_PUBLIC_POST = {"/api/auth/login"}

# Routes authenticated by API key (their own dependency); skip user RBAC.
_APIKEY_PREFIXES = ("/api/ingest",)

# Operator-tier write patterns (regex on path). Checked before the generic
# engineer-tier write rule.
_OPERATOR_WRITE_PATTERNS = [
    re.compile(r"^/api/tags/\d+/write/?$"),          # Modbus command/setpoint write
    re.compile(r"^/api/alarms/.*/ack/?$"),           # acknowledge alarm
    re.compile(r"^/api/alarms/active/\d+/ack/?$"),
]

_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _required_role(method: str, path: str) -> str | None:
    """Return the minimum role for (method, path), or None if no auth needed.

    Raises nothing; pure policy lookup.
    """
    # Public.
    if path in _PUBLIC_EXACT or any(path.startswith(p) for p in _PUBLIC_PREFIXES):
        return None
    if method == "POST" and path in _PUBLIC_POST:
        return None
    # API-key routes — not user-RBAC'd here.
    if any(path.startswith(p) for p in _APIKEY_PREFIXES):
        return None
    # Non-/api paths we don't manage (shouldn't exist, but be permissive).
    if not path.startswith("/api/"):
        return None

    # Admin namespace — everything requires admin.
    if path.startswith("/api/admin/"):
        return Role.ADMIN.value

    # Reads — any authenticated user.
    if method in ("GET", "HEAD", "OPTIONS"):
        return Role.VIEWER.value

    # Writes.
    if method in _WRITE_METHODS:
        for pat in _OPERATOR_WRITE_PATTERNS:
            if pat.match(path):
                return Role.OPERATOR.value
        return Role.ENGINEER.value

    # Unknown method — require engineer to be safe.
    return Role.ENGINEER.value


def _json(status_code: int, detail: str) -> JSONResponse:
    headers = {"WWW-Authenticate": "Bearer"} if status_code == 401 else None
    return JSONResponse({"detail": detail}, status_code=status_code, headers=headers)


class RBACMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        method = request.method
        path = request.url.path

        min_role = _required_role(method, path)
        if min_role is None:
            return await call_next(request)

        # Decode bearer token.
        authz = request.headers.get("authorization")
        if not authz:
            return _json(401, "Not authenticated.")
        parts = authz.split(maxsplit=1)
        if len(parts) != 2 or parts[0].lower() != "bearer":
            return _json(401, "Not authenticated.")
        payload = decode_token(parts[1].strip())
        if payload is None:
            return _json(401, "Invalid or expired token.")

        role = payload.get("role")
        if not role or not role_at_least(role, min_role):
            return _json(403, f"Requires '{min_role}' role or higher.")

        return await call_next(request)
