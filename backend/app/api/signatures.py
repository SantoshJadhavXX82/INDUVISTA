"""Electronic signatures (21 CFR Part 11) — generic signing foundation + API.

Increment B-ESIG.1. A protocol-agnostic signing service:

  * binds a signature to a specific record via SHA-256 of that record's exact
    content (report_revisions.config now; control writes / recipes later),
  * enforces an Authored -> Reviewed -> Approved workflow, in order, once each,
  * requires the signer to RE-AUTHENTICATE at the moment of signing (password
    re-entry, provider-agnostic — Part 11 §11.200),
  * enforces segregation of duties (a signer may not apply two meanings on the
    same record) — configurable,
  * gates each meaning by role (authored/reviewed >= engineer, approved >= approver),
  * writes a per-record tamper-evident hash chain, durably (synchronous_commit on),
  * emits a parallel append-only audit event for every accepted AND denied attempt.

Record-type-specific rules (what content is hashed, who may sign) live in the
small adapters below, so adding control_write / recipe_download later is local.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.auth.deps import get_current_user, CurrentUser
from app.auth.roles import Role, role_at_least
from app.auth.providers import authenticate
from app.utils.audit import audit, AuditEvent

router = APIRouter(tags=["signatures"])

# --- workflow definition --------------------------------------------------
# Ordered signing meanings; a record is signed in this sequence, once each.
MEANINGS: list[str] = ["authored", "reviewed", "approved"]

# Minimum role required to apply each meaning. APPROVER exists specifically to
# gate report-revision approval/activation.
_MEANING_MIN_ROLE: dict[str, str] = {
    "authored": Role.ENGINEER.value,
    "reviewed": Role.ENGINEER.value,
    "approved": Role.APPROVER.value,
}

# Record types the foundation knows. report_revision is wired now;
# control_write / recipe_download are reserved for the controlling-SCADA phase.
RECORD_TYPES: list[str] = ["report_revision", "control_write", "recipe_download"]

# Segregation of duties: a user may not apply more than one meaning on the same
# record (reviewer != author, approver != reviewer/author). Default ON per the
# GxP electronic-signature spec.
SEGREGATION_OF_DUTIES = True


# --- hashing --------------------------------------------------------------
def _canonical(content: Any) -> str:
    return json.dumps(content, sort_keys=True, separators=(",", ":"), default=str)


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _content_hash(content: Any) -> str:
    return _sha256(_canonical(content))


def _chain_hash(prev_chain_hash: str | None, fields: dict) -> str:
    return _sha256((prev_chain_hash or "") + _canonical(fields))


# --- record-type adapters -------------------------------------------------
def _record_content_hash(db: Session, record_type: str, record_id: int) -> str | None:
    """Hash of the target record's exact signable content, or None if missing.

    Each record type defines what 'content' means. report_revision hashes the
    immutable config snapshot, so any later edit invalidates prior signatures.
    """
    if record_type == "report_revision":
        row = db.execute(
            text("SELECT config FROM report_revisions WHERE id = :id"),
            {"id": record_id},
        ).first()
        return None if row is None else _content_hash(row[0])
    # control_write / recipe_download wired in the controlling-SCADA phase.
    raise HTTPException(400, f"record_type '{record_type}' is not signable yet")


# --- schemas --------------------------------------------------------------
class SignRequest(BaseModel):
    record_type: str
    record_id: int
    meaning: str
    password: str = Field(min_length=1, max_length=200)
    reason_code: str | None = Field(default=None, max_length=48)
    comment: str | None = Field(default=None, max_length=2000)


class SignatureOut(BaseModel):
    id: int
    record_type: str
    record_id: int
    record_hash: str
    meaning: str
    signer_user_id: int
    signer_username: str
    signer_role: str
    reason_code: str | None
    comment: str | None
    signed_at: str
    prev_signature_id: int | None


class VerifyOut(BaseModel):
    record_type: str
    record_id: int
    signatures: list[SignatureOut]
    chain_intact: bool          # the per-record hash chain recomputes correctly
    content_unchanged: bool     # current record content still matches what was signed
    fully_signed: bool          # all meanings applied, in order
    next_meaning: str | None    # the next meaning required, or None if complete


# --- helpers --------------------------------------------------------------
def _existing(db: Session, record_type: str, record_id: int) -> list[dict]:
    rows = db.execute(text("""
        SELECT id, record_type, record_id, record_hash, meaning,
               signer_user_id, signer_username, signer_role,
               reason_code, comment, signed_at, prev_signature_id, chain_hash
        FROM signatures
        WHERE record_type = :rt AND record_id = :rid
        ORDER BY id
    """), {"rt": record_type, "rid": record_id}).mappings().all()
    return [dict(r) for r in rows]


def _out(r: dict) -> SignatureOut:
    return SignatureOut(
        id=r["id"], record_type=r["record_type"], record_id=r["record_id"],
        record_hash=r["record_hash"], meaning=r["meaning"],
        signer_user_id=r["signer_user_id"], signer_username=r["signer_username"],
        signer_role=r["signer_role"], reason_code=r["reason_code"],
        comment=r["comment"],
        signed_at=r["signed_at"].isoformat() if hasattr(r["signed_at"], "isoformat") else str(r["signed_at"]),
        prev_signature_id=r["prev_signature_id"],
    )


def _deny(request: Request, user: CurrentUser, body: SignRequest, why: str) -> None:
    audit(AuditEvent(
        action="signature.denied",
        target_type=body.record_type, target_id=body.record_id,
        target_label=f"{body.meaning} by {user.username}",
        summary=f"Signing denied: {why}",
        details={"meaning": body.meaning, "reason_code": body.reason_code},
        status="denied", error_message=why,
    ), request)


# --- endpoints ------------------------------------------------------------
@router.post("/api/signatures", response_model=SignatureOut, status_code=201)
def create_signature(
    body: SignRequest,
    request: Request,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_session)],
):
    if body.record_type not in RECORD_TYPES:
        raise HTTPException(422, f"unknown record_type '{body.record_type}'")
    if body.meaning not in MEANINGS:
        raise HTTPException(422, f"unknown meaning '{body.meaning}'")

    # 1) authority — role gate for this meaning
    min_role = _MEANING_MIN_ROLE.get(body.meaning, Role.ENGINEER.value)
    if not role_at_least(user.role, min_role):
        _deny(request, user, body, f"requires role >= {min_role}")
        raise HTTPException(403, f"Signing '{body.meaning}' requires '{min_role}' role or higher.")

    # 2) target must exist; compute the content hash to bind to
    record_hash = _record_content_hash(db, body.record_type, body.record_id)
    if record_hash is None:
        raise HTTPException(404, f"{body.record_type} {body.record_id} not found")

    # 3) re-authenticate the signer (provider-agnostic password re-entry)
    authed = authenticate(db, user.username, body.password)
    if authed is None or int(getattr(authed, "id", -1)) != int(user.id):
        _deny(request, user, body, "re-authentication failed")
        raise HTTPException(401, "Re-authentication failed. Signature not applied.")

    existing = _existing(db, body.record_type, body.record_id)

    # 4) workflow order — meanings applied in sequence, once each
    applied = [s["meaning"] for s in existing]
    if len(applied) >= len(MEANINGS):
        _deny(request, user, body, "record already fully signed")
        raise HTTPException(409, "Record is already fully signed.")
    expected = MEANINGS[len(applied)]
    if body.meaning != expected:
        _deny(request, user, body, f"out-of-order; next required is '{expected}'")
        raise HTTPException(409, f"Out-of-order signing: next required meaning is '{expected}'.")

    # 5) segregation of duties — distinct signer per meaning
    if SEGREGATION_OF_DUTIES and any(s["signer_user_id"] == user.id for s in existing):
        _deny(request, user, body, "segregation of duties: already signed a prior step")
        raise HTTPException(409, "Segregation of duties: you already signed a prior step on this record.")

    # 6) reason mandatory on approval
    if body.meaning == "approved" and not (body.reason_code or (body.comment or "").strip()):
        _deny(request, user, body, "reason required for approval")
        raise HTTPException(422, "A reason_code or comment is required to approve.")

    # 7) tamper-evident chain + durable insert
    prev = existing[-1] if existing else None
    prev_id = prev["id"] if prev else None
    prev_chain = prev["chain_hash"] if prev else None
    corr = str(uuid.uuid4())
    row_fields = {
        "record_type": body.record_type, "record_id": body.record_id,
        "record_hash": record_hash, "meaning": body.meaning,
        "signer_user_id": user.id, "signer_username": user.username,
        "signer_role": user.role, "reason_code": body.reason_code,
        "comment": body.comment, "prev_signature_id": prev_id,
    }
    chain_hash = _chain_hash(prev_chain, row_fields)

    db.execute(text("SET LOCAL synchronous_commit = on"))
    inserted = db.execute(text("""
        INSERT INTO signatures (
            record_type, record_id, record_hash, meaning,
            signer_user_id, signer_username, signer_role,
            reason_code, comment, prev_signature_id, chain_hash, audit_correlation_id
        ) VALUES (
            :record_type, :record_id, :record_hash, :meaning,
            :signer_user_id, :signer_username, :signer_role,
            :reason_code, :comment, :prev_signature_id, :chain_hash, CAST(:corr AS UUID)
        ) RETURNING id, signed_at
    """), {**row_fields, "chain_hash": chain_hash, "corr": corr}).first()
    db.commit()

    audit(AuditEvent(
        action="signature.sign",
        target_type=body.record_type, target_id=body.record_id,
        target_label=f"{body.meaning} by {user.username}",
        summary=f"{body.meaning} signed",
        details={"meaning": body.meaning, "reason_code": body.reason_code,
                 "record_hash": record_hash, "signature_id": inserted[0]},
        status="success", correlation_id=corr,
    ), request)

    return _out({**row_fields, "id": inserted[0], "signed_at": inserted[1]})


@router.get("/api/signatures", response_model=list[SignatureOut])
def list_signatures(
    record_type: str,
    record_id: int,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_session)],
):
    return [_out(r) for r in _existing(db, record_type, record_id)]


@router.get("/api/signatures/verify", response_model=VerifyOut)
def verify_signatures(
    record_type: str,
    record_id: int,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_session)],
):
    """Recompute the per-record chain and re-check the bound content hash.

    chain_intact      = every row's chain_hash recomputes from its predecessor
    content_unchanged = the current record content still hashes to what was signed
                        (False => the record changed after signing; signatures stale)
    """
    rows = _existing(db, record_type, record_id)

    chain_intact = True
    prev_chain: str | None = None
    for r in rows:
        fields = {
            "record_type": r["record_type"], "record_id": r["record_id"],
            "record_hash": r["record_hash"], "meaning": r["meaning"],
            "signer_user_id": r["signer_user_id"], "signer_username": r["signer_username"],
            "signer_role": r["signer_role"], "reason_code": r["reason_code"],
            "comment": r["comment"], "prev_signature_id": r["prev_signature_id"],
        }
        if _chain_hash(prev_chain, fields) != r["chain_hash"]:
            chain_intact = False
            break
        prev_chain = r["chain_hash"]

    content_unchanged = True
    if rows:
        try:
            current = _record_content_hash(db, record_type, record_id)
            content_unchanged = (current is not None and current == rows[0]["record_hash"])
        except HTTPException:
            content_unchanged = False

    applied = [r["meaning"] for r in rows]
    fully = len(applied) >= len(MEANINGS)
    nxt = None if fully else MEANINGS[len(applied)]

    return VerifyOut(
        record_type=record_type, record_id=record_id,
        signatures=[_out(r) for r in rows],
        chain_intact=chain_intact, content_unchanged=content_unchanged,
        fully_signed=fully, next_meaning=nxt,
    )
