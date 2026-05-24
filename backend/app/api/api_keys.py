"""Admin endpoints for API key management (Phase OPC.1).

Provides CRUD over the api_keys table. These endpoints are
intentionally NOT protected by an API key themselves — they're
admin-only operations that need a different auth model. Phase 21
(Auth/RBAC) will wrap them in proper RBAC; until then they're
exposed unauthenticated, and operators are expected to run INDUVISTA
behind a private network or VPN.

ENDPOINTS
=========

  POST   /api/admin/api-keys             Mint a new key
  GET    /api/admin/api-keys             List all keys (no plaintext)
  PATCH  /api/admin/api-keys/{id}        Update name/description/scope
  DELETE /api/admin/api-keys/{id}        Revoke (sets is_enabled=FALSE)

THE "MINT ONCE" PATTERN
=======================

  POST /api/admin/api-keys returns the freshly-minted raw key exactly
  once, in the response body. The raw key is NEVER queryable again —
  only its SHA-256 hash is stored. If the operator misplaces it,
  they must revoke and mint a new one. This is the same pattern
  Stripe, AWS, GitHub, and every other API platform uses.

  The response includes a "raw_key" field with the plaintext value
  AND a one-line copy snippet ready to paste into the OPC client's
  config. Subsequent GET responses never include raw_key — just
  key_prefix for identification.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.utils.api_key_auth import generate_raw_key


router = APIRouter(prefix="/api/admin/api-keys", tags=["admin", "api-keys"])


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class ApiKeyCreateBody(BaseModel):
    """Body for POST /api/admin/api-keys."""
    client_name: str = Field(
        ...,
        min_length=3,
        max_length=64,
        description="Short identifier for this client (e.g. 'opc_client_plant1'). Appears in tag_values.source.",
    )
    description: str | None = Field(
        None,
        max_length=512,
        description="Optional notes about what this key is for.",
    )
    allowed_tag_ids: list[int] | None = Field(
        None,
        description="If set, restricts the key to writing only these tag IDs. NULL = all tags allowed.",
    )


class ApiKeyPatchBody(BaseModel):
    """Body for PATCH /api/admin/api-keys/{id}. All fields optional."""
    client_name: str | None = Field(None, min_length=3, max_length=64)
    description: str | None = Field(None, max_length=512)
    allowed_tag_ids: list[int] | None = None
    is_enabled: bool | None = None


class ApiKeySummary(BaseModel):
    """Public view of an api_key (no plaintext, no hash)."""
    id: int
    client_name: str
    key_prefix: str
    description: str | None
    is_enabled: bool
    allowed_tag_ids: list[int] | None
    last_used_at: str | None  # ISO; None if never used
    last_used_ip: str | None
    created_at: str


class ApiKeyMintResponse(ApiKeySummary):
    """Response for POST — includes raw_key ONCE."""
    raw_key: str = Field(
        ...,
        description="The plaintext API key. STORE THIS NOW — it cannot be retrieved later.",
    )
    bearer_header: str = Field(
        ...,
        description="Ready-to-paste 'Authorization: Bearer <key>' header value.",
    )


def _row_to_summary(row: Any) -> ApiKeySummary:
    """Project a DB row to ApiKeySummary."""
    return ApiKeySummary(
        id=row["id"],
        client_name=row["client_name"],
        key_prefix=row["key_prefix"],
        description=row["description"],
        is_enabled=row["is_enabled"],
        allowed_tag_ids=(
            list(row["allowed_tag_ids"]) if row["allowed_tag_ids"] is not None else None
        ),
        last_used_at=row["last_used_at"].isoformat() if row["last_used_at"] else None,
        last_used_ip=str(row["last_used_ip"]) if row["last_used_ip"] else None,
        created_at=row["created_at"].isoformat(),
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("", response_model=ApiKeyMintResponse, status_code=201)
def mint_api_key(
    body: ApiKeyCreateBody,
    db: Annotated[Session, Depends(get_session)],
):
    """Create a new API key. Returns the plaintext raw_key ONCE.

    The caller MUST save the raw_key from this response — it is the
    only time the plaintext value is ever revealed. The database
    stores only a SHA-256 hash. If lost, the operator must revoke
    and mint a new key.
    """
    # Reject duplicate client_name early (also enforced by UNIQUE constraint)
    existing = db.execute(
        text("SELECT 1 FROM api_keys WHERE client_name = :n"),
        {"n": body.client_name},
    ).first()
    if existing is not None:
        raise HTTPException(
            409,
            f"An API key with client_name='{body.client_name}' already exists. "
            f"Choose a different client_name, or revoke the existing key first.",
        )

    # Validate allowed_tag_ids if provided — ensure all tags exist
    if body.allowed_tag_ids:
        existing_ids = db.execute(
            text("SELECT id FROM tags WHERE id = ANY(:ids)"),
            {"ids": body.allowed_tag_ids},
        ).scalars().all()
        existing_set = set(existing_ids)
        missing = [tid for tid in body.allowed_tag_ids if tid not in existing_set]
        if missing:
            raise HTTPException(
                400,
                f"allowed_tag_ids contains tag IDs that don't exist: {missing}",
            )

    raw_key, key_hash, key_prefix = generate_raw_key()

    row = db.execute(
        text("""
            INSERT INTO api_keys (
                client_name, key_hash, key_prefix,
                description, is_enabled, allowed_tag_ids
            )
            VALUES (
                :name, :hash, :prefix,
                :desc, TRUE, :allowed
            )
            RETURNING id, client_name, key_prefix, description,
                      is_enabled, allowed_tag_ids,
                      last_used_at, last_used_ip, created_at
        """),
        {
            "name": body.client_name,
            "hash": key_hash,
            "prefix": key_prefix,
            "desc": body.description,
            "allowed": body.allowed_tag_ids,
        },
    ).mappings().first()
    db.commit()

    summary = _row_to_summary(row)
    return ApiKeyMintResponse(
        **summary.model_dump(),
        raw_key=raw_key,
        bearer_header=f"Authorization: Bearer {raw_key}",
    )


@router.get("", response_model=list[ApiKeySummary])
def list_api_keys(db: Annotated[Session, Depends(get_session)]):
    """List all keys (enabled and disabled), most recent first.

    Plaintext is NOT included; only the key_prefix for identification.
    """
    rows = db.execute(text("""
        SELECT id, client_name, key_prefix, description,
               is_enabled, allowed_tag_ids,
               last_used_at, last_used_ip, created_at
        FROM api_keys
        ORDER BY created_at DESC
    """)).mappings().all()
    return [_row_to_summary(r) for r in rows]


@router.patch("/{key_id}", response_model=ApiKeySummary)
def patch_api_key(
    key_id: int,
    body: ApiKeyPatchBody,
    db: Annotated[Session, Depends(get_session)],
):
    """Update key metadata (name, description, allowed_tag_ids, is_enabled).

    Cannot rotate the underlying secret — for that, revoke this key
    and mint a new one. Operators must update the client's config
    with the new raw_key anyway, so rotation is functionally the
    same as revoke + mint.
    """
    existing = db.execute(
        text("SELECT id FROM api_keys WHERE id = :id"),
        {"id": key_id},
    ).first()
    if existing is None:
        raise HTTPException(404, f"No API key with id={key_id}")

    # Validate allowed_tag_ids if provided
    if body.allowed_tag_ids:
        existing_ids = db.execute(
            text("SELECT id FROM tags WHERE id = ANY(:ids)"),
            {"ids": body.allowed_tag_ids},
        ).scalars().all()
        existing_set = set(existing_ids)
        missing = [tid for tid in body.allowed_tag_ids if tid not in existing_set]
        if missing:
            raise HTTPException(
                400,
                f"allowed_tag_ids contains tag IDs that don't exist: {missing}",
            )

    # Build dynamic UPDATE — only set fields that were provided
    updates: dict[str, Any] = {}
    if body.client_name is not None:
        updates["client_name"] = body.client_name
    if body.description is not None:
        updates["description"] = body.description
    if body.allowed_tag_ids is not None:
        updates["allowed_tag_ids"] = body.allowed_tag_ids
    if body.is_enabled is not None:
        updates["is_enabled"] = body.is_enabled

    if updates:
        set_clauses = ", ".join(f"{col} = :{col}" for col in updates)
        params = {**updates, "id": key_id}
        db.execute(
            text(f"UPDATE api_keys SET {set_clauses} WHERE id = :id"),
            params,
        )
        db.commit()

    row = db.execute(text("""
        SELECT id, client_name, key_prefix, description,
               is_enabled, allowed_tag_ids,
               last_used_at, last_used_ip, created_at
        FROM api_keys WHERE id = :id
    """), {"id": key_id}).mappings().first()
    return _row_to_summary(row)


@router.delete("/{key_id}", status_code=204)
def revoke_api_key(
    key_id: int,
    db: Annotated[Session, Depends(get_session)],
):
    """Revoke a key by setting is_enabled = FALSE.

    We don't physically DELETE the row because:
      1. last_used_at / last_used_ip is useful forensic data
      2. tag_values.source references the client_name; keeping the row
         preserves the audit trail of which client wrote which sample

    To permanently delete a revoked key (forensics-clean), run
    DELETE manually in pgAdmin after revoking via this endpoint.
    """
    result = db.execute(
        text("UPDATE api_keys SET is_enabled = FALSE WHERE id = :id"),
        {"id": key_id},
    )
    if result.rowcount == 0:
        raise HTTPException(404, f"No API key with id={key_id}")
    db.commit()
    return None
