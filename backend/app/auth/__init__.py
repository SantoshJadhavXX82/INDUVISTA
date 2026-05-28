"""Authentication & RBAC (Phase 21).

Public surface:
    from app.auth import (
        get_current_user, require_role, Role, CurrentUser,
        issue_token, hash_password, verify_password,
    )
"""
from app.auth.roles import Role, ROLE_ORDER, role_at_least
from app.auth.security import hash_password, verify_password, issue_token, decode_token
from app.auth.deps import get_current_user, require_role, CurrentUser

__all__ = [
    "Role", "ROLE_ORDER", "role_at_least",
    "hash_password", "verify_password", "issue_token", "decode_token",
    "get_current_user", "require_role", "CurrentUser",
]
