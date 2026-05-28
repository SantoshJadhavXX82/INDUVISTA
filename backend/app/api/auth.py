"""Authentication endpoints (Phase 21).

  POST /api/auth/login            { username, password } → { access_token, ... }
  GET  /api/auth/me               current user (requires token)
  POST /api/auth/change-password  { current_password, new_password }

Login dispatches through app.auth.providers.authenticate(), so the same
endpoint serves local users now and LDAP/OS users later with no change.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth import (
    CurrentUser,
    get_current_user,
    hash_password,
    issue_token,
    verify_password,
)
from app.auth.providers import authenticate
from app.db import get_session

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str
    role: str
    must_change_password: bool


class MeResponse(BaseModel):
    id: int
    username: str
    role: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=200)


_LOGIN_FAILED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid username or password.",
)


@router.post("/login", response_model=LoginResponse)
def login(
    body: LoginRequest,
    request: Request,
    db: Annotated[Session, Depends(get_session)],
):
    authed = authenticate(db, body.username, body.password)
    if authed is None:
        raise _LOGIN_FAILED

    # Best-effort last-login telemetry (never fails the login).
    try:
        ip = request.client.host if request.client else None
        db.execute(
            text("UPDATE users SET last_login_at = NOW(), last_login_ip = :ip "
                 "WHERE id = :id"),
            {"id": authed.id, "ip": ip},
        )
        db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass

    token = issue_token(authed.id, authed.username, authed.role)
    return LoginResponse(
        access_token=token,
        username=authed.username,
        role=authed.role,
        must_change_password=authed.must_change_password,
    )


@router.get("/me", response_model=MeResponse)
def me(user: Annotated[CurrentUser, Depends(get_current_user)]):
    return MeResponse(id=user.id, username=user.username, role=user.role)


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    body: ChangePasswordRequest,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_session)],
):
    row = db.execute(
        text("SELECT password_hash, auth_provider FROM users WHERE id = :id"),
        {"id": user.id},
    ).mappings().first()
    if row is None:
        raise HTTPException(404, "User not found.")
    if row["auth_provider"] != "local":
        raise HTTPException(
            400,
            "Password is managed by the external identity provider "
            f"('{row['auth_provider']}'); change it there.",
        )
    if not row["password_hash"] or not verify_password(
        body.current_password, row["password_hash"]
    ):
        raise HTTPException(400, "Current password is incorrect.")

    db.execute(
        text("UPDATE users SET password_hash = :h, must_change_password = FALSE, "
             "updated_at = NOW() WHERE id = :id"),
        {"h": hash_password(body.new_password), "id": user.id},
    )
    db.commit()
