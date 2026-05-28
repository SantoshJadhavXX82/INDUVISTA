"""FastAPI auth dependencies (Phase 21).

  get_current_user   — requires a valid JWT; returns CurrentUser. 401 otherwise.
  require_role(role)  — dependency factory; admits users at >= role, else 403.

Usage in routers:

    from app.auth import require_role, Role, get_current_user, CurrentUser

    # read endpoint — any authenticated user
    @router.get("/things")
    def list_things(user: CurrentUser = Depends(get_current_user)): ...

    # config write — engineer or admin
    @router.post("/devices", dependencies=[Depends(require_role(Role.ENGINEER))])
    def create_device(...): ...

    # admin only
    @router.post("/admin/users", dependencies=[Depends(require_role(Role.ADMIN))])
    def create_user(...): ...

The CurrentUser is also injectable for audit (records WHICH user acted).
"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, status

from app.auth.roles import Role, role_at_least
from app.auth.security import decode_token


@dataclass(frozen=True)
class CurrentUser:
    id: int
    username: str
    role: str


_UNAUTH = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated.",
    headers={"WWW-Authenticate": "Bearer"},
)


def get_current_user(
    authorization: str | None = Header(default=None),
) -> CurrentUser:
    """Decode the bearer JWT into a CurrentUser. 401 on missing/invalid/expired."""
    if not authorization:
        raise _UNAUTH
    parts = authorization.split(maxsplit=1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise _UNAUTH
    payload = decode_token(parts[1].strip())
    if payload is None:
        raise _UNAUTH
    try:
        return CurrentUser(
            id=int(payload["sub"]),
            username=payload["username"],
            role=payload["role"],
        )
    except (KeyError, ValueError, TypeError):
        raise _UNAUTH


def require_role(min_role: str | Role):
    """Dependency factory: admit users whose role >= min_role, else 403.

    Returns the CurrentUser so handlers can also use the identity. Use as
    either a gate (`dependencies=[Depends(require_role(Role.ENGINEER))]`)
    or an injected value (`user = Depends(require_role(Role.ENGINEER))`).
    """
    min_role_str = min_role.value if isinstance(min_role, Role) else str(min_role)

    def _dep(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if not role_at_least(user.role, min_role_str):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires '{min_role_str}' role or higher.",
            )
        return user

    return _dep
