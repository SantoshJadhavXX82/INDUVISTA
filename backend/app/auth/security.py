"""Password hashing (bcrypt) + JWT issue/verify (Phase 21).

bcrypt for passwords: human-chosen secrets are low-entropy, so the
deliberate slowness of bcrypt is the protection we want. (Contrast with
api_keys, which are 256-bit random tokens hashed with fast SHA-256.)

JWT for sessions: stateless, no server-side session table. The token
carries the user id, username, and role; every protected request decodes
it. Logout is client-side token discard. Tokens expire after
AUTH_TOKEN_TTL_MIN minutes (default 12h — a plant shift).

SECRET: signed with settings.jwt_secret. MUST be set to a strong random
value in production (see .env.example). The app refuses to start with the
insecure development default when APP_ENV=production.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any

import jwt
from passlib.context import CryptContext

from app.config import settings


# bcrypt with sane defaults. rounds=12 ≈ ~250ms/verify on modern hardware.
_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=12)

_ALGO = "HS256"


def hash_password(raw: str) -> str:
    """bcrypt-hash a plaintext password for storage in users.password_hash."""
    return _pwd.hash(raw)


def verify_password(raw: str, hashed: str) -> bool:
    """Constant-time bcrypt verify. False (never raises) on any mismatch."""
    try:
        return _pwd.verify(raw, hashed)
    except Exception:
        return False


def issue_token(user_id: int, username: str, role: str) -> str:
    """Mint a signed JWT for a successfully authenticated user."""
    now = _dt.datetime.now(_dt.timezone.utc)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "username": username,
        "role": role,
        "iat": now,
        "exp": now + _dt.timedelta(minutes=settings.auth_token_ttl_min),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=_ALGO)


def decode_token(token: str) -> dict[str, Any] | None:
    """Verify + decode a JWT. Returns the payload, or None if invalid/expired."""
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[_ALGO])
    except jwt.PyJWTError:
        return None
