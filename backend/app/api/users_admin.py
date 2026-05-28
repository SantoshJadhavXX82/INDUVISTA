"""Admin user management (Phase 21). All endpoints require ADMIN role.

  GET    /api/admin/users            list users (no password hashes)
  POST   /api/admin/users            create a user
  PATCH  /api/admin/users/{id}       update role/name/email/enabled
  POST   /api/admin/users/{id}/reset-password   set a new password + force change
  DELETE /api/admin/users/{id}       disable (soft — never hard-delete an actor)

Local users require a password at creation. ldap/os users are created
WITHOUT a password (verified externally) — provided here so an admin can
pre-provision roles before those providers are wired in Phase 21.2/21.3.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth import CurrentUser, Role, hash_password, require_role
from app.db import get_session

router = APIRouter(prefix="/api/admin/users", tags=["admin", "users"])

_VALID_ROLES = {r.value for r in Role}
_VALID_PROVIDERS = {"local", "ldap", "os"}


class UserOut(BaseModel):
    id: int
    username: str
    auth_provider: str
    role: str
    full_name: str | None
    email: str | None
    is_enabled: bool
    must_change_password: bool
    last_login_at: str | None


class UserCreate(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    role: str = "viewer"
    auth_provider: str = "local"
    password: str | None = Field(default=None, min_length=8, max_length=200)
    full_name: str | None = None
    email: str | None = None
    must_change_password: bool = True


class UserUpdate(BaseModel):
    role: str | None = None
    full_name: str | None = None
    email: str | None = None
    is_enabled: bool | None = None


class ResetPasswordRequest(BaseModel):
    new_password: str = Field(min_length=8, max_length=200)


def _validate_role(role: str) -> None:
    if role not in _VALID_ROLES:
        raise HTTPException(400, f"Invalid role. Must be one of: {sorted(_VALID_ROLES)}")


@router.get("", response_model=list[UserOut])
def list_users(
    _: Annotated[CurrentUser, Depends(require_role(Role.ADMIN))],
    db: Annotated[Session, Depends(get_session)],
):
    rows = db.execute(text("""
        SELECT id, username, auth_provider, role, full_name, email,
               is_enabled, must_change_password,
               to_char(last_login_at, 'YYYY-MM-DD"T"HH24:MI:SSOF') AS last_login_at
        FROM users ORDER BY username
    """)).mappings().all()
    return [UserOut(**dict(r)) for r in rows]


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(
    body: UserCreate,
    admin: Annotated[CurrentUser, Depends(require_role(Role.ADMIN))],
    db: Annotated[Session, Depends(get_session)],
):
    _validate_role(body.role)
    if body.auth_provider not in _VALID_PROVIDERS:
        raise HTTPException(400, f"Invalid auth_provider. One of: {sorted(_VALID_PROVIDERS)}")

    # Local users need a password; external-provider users must not have one.
    pw_hash = None
    if body.auth_provider == "local":
        if not body.password:
            raise HTTPException(400, "Local users require a password.")
        pw_hash = hash_password(body.password)
    elif body.password:
        raise HTTPException(
            400, f"{body.auth_provider} users are verified externally; omit password."
        )

    exists = db.execute(
        text("SELECT 1 FROM users WHERE username = :u"), {"u": body.username}
    ).first()
    if exists:
        raise HTTPException(409, f"Username {body.username!r} already exists.")

    row = db.execute(text("""
        INSERT INTO users (username, auth_provider, password_hash, role,
                           full_name, email, must_change_password, created_by)
        VALUES (:u, :p, :h, :r, :fn, :em, :mc, :cb)
        RETURNING id, username, auth_provider, role, full_name, email,
                  is_enabled, must_change_password,
                  to_char(last_login_at, 'YYYY-MM-DD"T"HH24:MI:SSOF') AS last_login_at
    """), {
        "u": body.username, "p": body.auth_provider, "h": pw_hash, "r": body.role,
        "fn": body.full_name, "em": body.email,
        "mc": body.must_change_password if body.auth_provider == "local" else False,
        "cb": admin.username,
    }).mappings().first()
    db.commit()
    return UserOut(**dict(row))


@router.patch("/{user_id}", response_model=UserOut)
def update_user(
    user_id: int,
    body: UserUpdate,
    admin: Annotated[CurrentUser, Depends(require_role(Role.ADMIN))],
    db: Annotated[Session, Depends(get_session)],
):
    if body.role is not None:
        _validate_role(body.role)

    # Guard: don't let an admin disable or demote the LAST enabled admin
    # (lockout protection).
    if (body.is_enabled is False or (body.role and body.role != Role.ADMIN.value)):
        target = db.execute(
            text("SELECT role, is_enabled FROM users WHERE id = :id"), {"id": user_id}
        ).mappings().first()
        if target and target["role"] == Role.ADMIN.value and target["is_enabled"]:
            admin_count = db.execute(
                text("SELECT COUNT(*) FROM users WHERE role = 'admin' AND is_enabled = TRUE")
            ).scalar()
            if admin_count is not None and admin_count <= 1:
                raise HTTPException(
                    400, "Cannot disable or demote the last enabled admin."
                )

    sets, params = [], {"id": user_id}
    for col, val in (("role", body.role), ("full_name", body.full_name),
                     ("email", body.email), ("is_enabled", body.is_enabled)):
        if val is not None:
            sets.append(f"{col} = :{col}")
            params[col] = val
    if not sets:
        raise HTTPException(400, "No fields to update.")
    sets.append("updated_at = NOW()")

    row = db.execute(
        text(f"UPDATE users SET {', '.join(sets)} WHERE id = :id "
             "RETURNING id, username, auth_provider, role, full_name, email, "
             "is_enabled, must_change_password, "
             "to_char(last_login_at, 'YYYY-MM-DD\"T\"HH24:MI:SSOF') AS last_login_at"),
        params,
    ).mappings().first()
    if row is None:
        raise HTTPException(404, "User not found.")
    db.commit()
    return UserOut(**dict(row))


@router.post("/{user_id}/reset-password", status_code=status.HTTP_204_NO_CONTENT)
def reset_password(
    user_id: int,
    body: ResetPasswordRequest,
    admin: Annotated[CurrentUser, Depends(require_role(Role.ADMIN))],
    db: Annotated[Session, Depends(get_session)],
):
    row = db.execute(
        text("SELECT auth_provider FROM users WHERE id = :id"), {"id": user_id}
    ).mappings().first()
    if row is None:
        raise HTTPException(404, "User not found.")
    if row["auth_provider"] != "local":
        raise HTTPException(400, "Only local users have app-managed passwords.")
    db.execute(
        text("UPDATE users SET password_hash = :h, must_change_password = TRUE, "
             "updated_at = NOW() WHERE id = :id"),
        {"h": hash_password(body.new_password), "id": user_id},
    )
    db.commit()


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def disable_user(
    user_id: int,
    admin: Annotated[CurrentUser, Depends(require_role(Role.ADMIN))],
    db: Annotated[Session, Depends(get_session)],
):
    # Soft-disable, never hard-delete (audit log references actors by id/name).
    target = db.execute(
        text("SELECT role, is_enabled FROM users WHERE id = :id"), {"id": user_id}
    ).mappings().first()
    if target is None:
        raise HTTPException(404, "User not found.")
    if target["role"] == Role.ADMIN.value and target["is_enabled"]:
        admin_count = db.execute(
            text("SELECT COUNT(*) FROM users WHERE role = 'admin' AND is_enabled = TRUE")
        ).scalar()
        if admin_count is not None and admin_count <= 1:
            raise HTTPException(400, "Cannot disable the last enabled admin.")
    db.execute(
        text("UPDATE users SET is_enabled = FALSE, updated_at = NOW() WHERE id = :id"),
        {"id": user_id},
    )
    db.commit()
