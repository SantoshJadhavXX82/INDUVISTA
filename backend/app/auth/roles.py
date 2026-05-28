"""Role definitions and ordering for RBAC (Phase 21).

A single ordered ladder. require_role(min_role) admits any user whose
role is >= min_role in this ladder.
"""
from __future__ import annotations

from enum import Enum


class Role(str, Enum):
    VIEWER = "viewer"
    OPERATOR = "operator"
    ENGINEER = "engineer"
    ADMIN = "admin"


# Least → most privilege. Index = privilege level.
ROLE_ORDER: list[str] = [Role.VIEWER, Role.OPERATOR, Role.ENGINEER, Role.ADMIN]


def role_at_least(user_role: str, min_role: str) -> bool:
    """True if user_role has at least min_role's privilege.

    Unknown roles are treated as below everything (deny by default).
    """
    try:
        return ROLE_ORDER.index(user_role) >= ROLE_ORDER.index(min_role)
    except ValueError:
        return False
