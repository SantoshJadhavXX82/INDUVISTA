"""API key authentication for the /api/ingest endpoint (Phase OPC.1).

This module exposes a FastAPI dependency `verify_api_key` that
external clients (OPC pushers, custom integrators, MQTT bridges) use
to authenticate their POSTs to /api/ingest. The dependency:

  1. Parses the `Authorization: Bearer <key>` header
  2. SHA-256 hashes the supplied key
  3. Looks up the hash in api_keys (only enabled rows)
  4. Updates last_used_at / last_used_ip (best-effort)
  5. Returns an ApiKeyInfo dataclass with id, client_name, allowed_tag_ids

Authentication failures (missing header, malformed key, no matching
row, disabled key) all raise HTTPException(401). The error messages
are intentionally generic — we don't leak whether a given key existed
historically vs never existed at all, to slow down enumeration attacks.

KEY FORMAT
==========

  Raw keys are 32 bytes of cryptographically random data, base64url-
  encoded, prefixed with "inv_". Example:

    inv_xN3KqLp7mZ8RsT4vWxYzAbCdEfGhIjKlMnOpQrStUv0

  Total length ~47 chars. 256 bits of entropy in the random portion;
  the "inv_" prefix is a recognition marker (like Stripe's "sk_live_")
  not security data.

  The key_prefix column stores the first 12 chars of the raw key for
  admin display, so list views can show "inv_xN3KqLp7..." to help
  identify which key is which. Bcrypt-style cost is unnecessary here
  because the raw keys have 256 bits of entropy — brute-forcing them
  is computationally impossible regardless of hash function.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session


log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ApiKeyInfo:
    """The verified caller's identity, passed to endpoint handlers."""
    id: int
    client_name: str
    allowed_tag_ids: list[int] | None  # None = unrestricted

    def can_write_tag(self, tag_id: int) -> bool:
        """Returns True if this caller is permitted to write the given tag."""
        if self.allowed_tag_ids is None:
            return True
        return tag_id in self.allowed_tag_ids


# Common error response — kept generic to slow enumeration attacks.
# Don't differentiate between "no key" / "bad key" / "disabled key".
_AUTH_ERROR = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid or missing API key.",
    headers={"WWW-Authenticate": 'Bearer realm="induvista-ingest"'},
)


def hash_key(raw_key: str) -> str:
    """SHA-256 of the raw key, hex-encoded (lowercase). Stored in
    api_keys.key_hash and used as the lookup index."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def generate_raw_key() -> tuple[str, str, str]:
    """Mint a new raw API key.

    Returns (raw_key, key_hash, key_prefix) tuple. The raw_key is
    returned to the caller ONCE — only key_hash and key_prefix persist
    to the database. Losing the raw_key means rotating the key
    entirely (revoke + mint new).
    """
    raw = "inv_" + secrets.token_urlsafe(32)
    return raw, hash_key(raw), raw[:12]


def verify_api_key(
    request: Request,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_session),
) -> ApiKeyInfo:
    """FastAPI dependency. Validates the bearer token and returns the
    caller's identity. Raises 401 on any failure.

    Side effect on success: updates last_used_at + last_used_ip
    asynchronously (best-effort; doesn't block the request if the
    update fails).
    """
    if not authorization:
        raise _AUTH_ERROR

    parts = authorization.split(maxsplit=1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise _AUTH_ERROR
    raw_key = parts[1].strip()
    if not raw_key:
        raise _AUTH_ERROR

    key_hash = hash_key(raw_key)

    # Constant-time string comparison is not necessary here because we
    # query by hash, not by string equality — the DB lookup is already
    # an O(1) hash-index probe that doesn't leak timing information
    # about which characters matched.
    row = db.execute(
        text("""
            SELECT id, client_name, allowed_tag_ids
            FROM api_keys
            WHERE key_hash = :key_hash AND is_enabled = TRUE
        """),
        {"key_hash": key_hash},
    ).first()

    if row is None:
        raise _AUTH_ERROR

    # Update last_used_at + last_used_ip best-effort. We deliberately
    # swallow exceptions here because failing to record this stat
    # shouldn't fail the ingest request itself.
    try:
        client_ip = request.client.host if request.client else None
        db.execute(
            text("""
                UPDATE api_keys
                SET last_used_at = NOW(),
                    last_used_ip = :ip
                WHERE id = :id
            """),
            {"id": row[0], "ip": client_ip},
        )
        db.commit()
    except Exception as e:
        log.debug("api_keys last_used update failed for id=%s: %s", row[0], e)
        try:
            db.rollback()
        except Exception:
            pass

    return ApiKeyInfo(
        id=row[0],
        client_name=row[1],
        allowed_tag_ids=list(row[2]) if row[2] is not None else None,
    )
