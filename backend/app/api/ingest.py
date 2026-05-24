"""Generic ingest endpoint for external clients (Phase OPC.1).

POST /api/ingest accepts batches of samples from any external client
(OPC pushers, custom Python pushers, MQTT bridges, etc.) authenticated
via an API key. Source-agnostic on purpose — the same endpoint serves
every type of integrator.

WHY A GENERIC INGEST ENDPOINT
=============================

  INDUVISTA's primary data path is the Modbus worker, which polls
  devices on a fixed schedule and writes directly to the historian.
  But many plants have data sources Modbus can't reach — vendor OPC
  servers, MQTT brokers, IoT gateways, custom Python collectors. For
  those we need a PUSH model: the external client sends samples to
  INDUVISTA when it has them.

  Rather than build N protocol-specific endpoints, a single ingest
  endpoint receives a uniform sample payload. The external client is
  responsible for whatever local protocol it speaks; INDUVISTA only
  sees clean (tag_id, time, value, quality) tuples.

SAMPLE SHAPE
============

  {
    "samples": [
      {
        "tag_id": 1234,
        "time": "2026-05-24T10:15:30.123Z",
        "value_double": 3.14159,
        "st": 192
      },
      ...
    ]
  }

  - `tag_id` must reference a tag the API key is allowed to write
    (per `api_keys.allowed_tag_ids`; NULL = any tag).
  - `time` ISO 8601 UTC. Backend stores in UTC always; the client
    converts from its local time before sending.
  - `value_double` for numeric tags, OR `value_text` for string-typed
    samples. Exactly one of the two must be set per sample (the other
    NULL or omitted).
  - `st` quality byte (0-255). Optional; defaults to 192 (GOOD) if
    omitted. The client should explicitly mark UNCERTAIN/BAD when it
    knows the upstream source has degraded.

RANGE CHECK (Phase 18.0 style)
==============================

  Mirrors what calc_evaluator does for computed-tag outputs:

  1. Non-finite values (NaN, +/-Inf) -> st downgraded to ST_BAD (0),
     value_double set to NULL. The row still gets written at the
     supplied timestamp so quality panels see the event.
  2. Value outside the tag's engineering range (min_value / max_value)
     -> st downgraded to ST_RANGE_WARN (64), value_double set to NULL.
     Same reasoning: the row exists for quality tracking but the
     out-of-range number doesn't pollute continuous aggregates.

  This is critical because external clients can be buggy or
  misconfigured. A rogue OPC client streaming sensor values from the
  wrong scale (e.g. raw counts instead of engineering units) could
  silently corrupt years of cagg data. The range check is the safety
  net that catches the bad data at the door.

BULK INSERT
===========

  All samples in a batch insert via a single executemany. ON CONFLICT
  (tag_id, time) DO NOTHING handles duplicate-time scenarios cleanly
  (e.g. client retries after a network blip — the duplicate quietly
  no-ops rather than erroring). The latest_tag_values table is also
  updated so the dashboard sees the freshest value immediately.

  No upper bound on batch size enforced at the endpoint, but practical
  limits (request body size, lock contention) make ~1000 samples the
  comfortable max. Clients should chunk larger pushes themselves.

RESPONSE
========

  {
    "accepted": 487,        # samples that wrote successfully
    "rejected": 13,         # samples that were filtered (auth scope, etc)
    "warned":   42,         # samples whose value was nulled due to range
    "errors": [
      {"index": 3, "reason": "tag_id 9999 not permitted for this key"},
      {"index": 17, "reason": "tag_id 5678 does not exist"},
      ...
    ]
  }

  Per-sample errors include the index into the original batch so the
  client can identify exactly which samples were rejected. A wholly-
  failed request (auth error, malformed JSON) returns HTTP 4xx with
  the standard FastAPI error envelope and no body.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.utils.api_key_auth import ApiKeyInfo, verify_api_key


log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ingest", tags=["ingest"])


# Quality tier constants — keep in sync with modbus/calc_evaluator.
# 0-63   = BAD, 64-127 = UNCERTAIN, 128-255 = GOOD.
ST_GOOD_DEFAULT = 192     # mid-GOOD range; client doesn't have to know exact code
ST_RANGE_WARN   = 64      # Phase 18.0: out-of-engineering-range
ST_BAD          = 0       # Phase 18.0: non-finite values


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class IngestSample(BaseModel):
    """One sample in an ingest batch."""
    tag_id: int = Field(..., ge=1)
    time: datetime
    value_double: float | None = None
    value_text: str | None = None
    # Quality byte. Default 192 ('good' mid-range) so simple clients that
    # don't track upstream quality don't have to know the exact value.
    st: int = Field(default=ST_GOOD_DEFAULT, ge=0, le=255)
    st_reason: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def _exactly_one_value(self) -> "IngestSample":
        # Allow both to be NULL — that's a "tag is offline" sample with
        # quality < GOOD. But disallow both being SET, which is ambiguous.
        if self.value_double is not None and self.value_text is not None:
            raise ValueError("value_double and value_text are mutually exclusive")
        return self


class IngestBatch(BaseModel):
    """POST /api/ingest body."""
    samples: list[IngestSample] = Field(..., min_length=1)


class IngestError(BaseModel):
    """Per-sample rejection detail."""
    index: int            # zero-based position in the original batch
    tag_id: int | None
    reason: str


class IngestResponse(BaseModel):
    accepted: int         # successfully written
    rejected: int         # filtered before write (auth scope, unknown tag, etc.)
    warned: int           # written but value nulled due to range check
    errors: list[IngestError]


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.post("", response_model=IngestResponse)
def ingest_samples(
    body: IngestBatch,
    db: Annotated[Session, Depends(get_session)],
    api_key: Annotated[ApiKeyInfo, Depends(verify_api_key)],
) -> IngestResponse:
    """Accept a batch of samples from an authenticated external client.

    Per-sample errors don't fail the whole batch — successful samples
    write through and the response lists which ones were rejected.
    """
    samples = body.samples

    # ── Step 1: Look up tag metadata for all referenced tag_ids ──────
    # One query, all tags at once. We need data_type to know which
    # value column applies, and min_value/max_value for the Phase 18.0
    # range check.
    distinct_tag_ids = list({s.tag_id for s in samples})
    tag_rows = db.execute(
        text("""
            SELECT id, data_type, min_value, max_value, register_block_id
            FROM tags
            WHERE id = ANY(:ids)
        """),
        {"ids": distinct_tag_ids},
    ).all()
    tag_meta: dict[int, dict[str, Any]] = {
        r.id: {
            "data_type": r.data_type,
            "min_value": r.min_value,
            "max_value": r.max_value,
            "register_block_id": r.register_block_id,
        }
        for r in tag_rows
    }

    # Resolve device_id for each tag via register_block. tag_values has
    # a device_id NOT NULL constraint so we need this even for external-
    # source samples. JOIN runs against the small `register_blocks`
    # table and is in-memory anyway after the first call.
    rb_ids = {m["register_block_id"] for m in tag_meta.values() if m["register_block_id"] is not None}
    device_lookup: dict[int, int] = {}
    if rb_ids:
        for r in db.execute(
            text("SELECT id, device_id FROM register_blocks WHERE id = ANY(:ids)"),
            {"ids": list(rb_ids)},
        ).all():
            device_lookup[r.id] = r.device_id

    # ── Step 2: Validate + range-check each sample ───────────────────
    rows_to_insert: list[dict[str, Any]] = []
    errors: list[IngestError] = []
    accepted = 0
    rejected = 0
    warned = 0
    # Phase OPC.1.1 — source is a TYPE not an INSTANCE, and the column
    # is varchar(16) with a CHECK constraint allow-list. We use the
    # plain "ingest" tag for every external client; per-row lineage
    # (which API key / which client_name wrote a given sample) is
    # tracked via the api_keys table's last_used_at + backend log lines
    # rather than encoded into source.
    source_label = "ingest"

    for idx, s in enumerate(samples):
        meta = tag_meta.get(s.tag_id)
        if meta is None:
            errors.append(IngestError(
                index=idx, tag_id=s.tag_id,
                reason=f"tag_id {s.tag_id} does not exist",
            ))
            rejected += 1
            continue

        # API key scope check
        if not api_key.can_write_tag(s.tag_id):
            errors.append(IngestError(
                index=idx, tag_id=s.tag_id,
                reason=f"tag_id {s.tag_id} not permitted for this API key",
            ))
            rejected += 1
            continue

        # Resolve device_id (required by tag_values FK)
        device_id = device_lookup.get(meta["register_block_id"])
        if device_id is None:
            errors.append(IngestError(
                index=idx, tag_id=s.tag_id,
                reason=f"tag_id {s.tag_id} has no register_block / device mapping",
            ))
            rejected += 1
            continue

        v_double = s.value_double
        v_text = s.value_text
        st = s.st
        st_reason = s.st_reason

        # Phase 18.0-style range check — applies only to numeric samples
        # with GOOD st. Bad/uncertain samples with explicit st < 128 are
        # taken at face value (the client already knows the value is
        # suspect; we don't escalate further).
        if v_double is not None and st >= 128:
            if not math.isfinite(v_double):
                v_double = None
                st = ST_BAD
                st_reason = st_reason or "non-finite value rejected by ingest"
                warned += 1
            else:
                lo = meta["min_value"]
                hi = meta["max_value"]
                out_of_range = False
                if lo is not None and v_double < lo:
                    out_of_range = True
                    rng_reason = f"below min_value {lo:g}"
                elif hi is not None and v_double > hi:
                    out_of_range = True
                    rng_reason = f"above max_value {hi:g}"
                else:
                    rng_reason = ""
                if out_of_range:
                    # Null the value so cagg avg/min/max stay clean.
                    # Keep the row so the quality event is visible.
                    v_double = None
                    st = ST_RANGE_WARN
                    st_reason = st_reason or f"ingest range check: {rng_reason}"
                    warned += 1

        rows_to_insert.append({
            "time": s.time,
            "tag_id": s.tag_id,
            "device_id": device_id,
            "register_block_id": meta["register_block_id"],
            "value_double": v_double,
            "value_text": v_text,
            "st": st,
            "st_reason": st_reason,
            "source": source_label,
        })
        accepted += 1

    # ── Step 3: Bulk insert into tag_values ──────────────────────────
    # ON CONFLICT (tag_id, time) DO NOTHING — duplicate samples on the
    # same (tag, time) silently no-op. This is the right semantics for
    # client retries after a network blip: the dup write doesn't error
    # and doesn't overwrite the existing row.
    if rows_to_insert:
        try:
            db.execute(
                text("""
                    INSERT INTO tag_values
                        (time, tag_id, device_id, register_block_id,
                         value_double, value_text, st, st_reason, source)
                    VALUES
                        (:time, :tag_id, :device_id, :register_block_id,
                         :value_double, :value_text, :st, :st_reason, :source)
                    ON CONFLICT (tag_id, time) DO NOTHING
                """),
                rows_to_insert,
            )

            # Update latest_tag_values too — dashboard reads from here.
            # The WHERE clause ensures an older retry can't overwrite a
            # newer live value (e.g. modbus worker already wrote a fresher
            # sample for this tag).
            db.execute(
                text("""
                    INSERT INTO latest_tag_values
                        (tag_id, device_id, register_block_id, time,
                         value_double, value_text, st, st_reason, source, updated_at)
                    VALUES
                        (:tag_id, :device_id, :register_block_id, :time,
                         :value_double, :value_text, :st, :st_reason, :source, now())
                    ON CONFLICT (tag_id) DO UPDATE SET
                        device_id          = EXCLUDED.device_id,
                        register_block_id  = EXCLUDED.register_block_id,
                        time               = EXCLUDED.time,
                        value_double       = EXCLUDED.value_double,
                        value_text         = EXCLUDED.value_text,
                        st                 = EXCLUDED.st,
                        st_reason          = EXCLUDED.st_reason,
                        source             = EXCLUDED.source,
                        updated_at         = now()
                    WHERE latest_tag_values.time < EXCLUDED.time
                """),
                rows_to_insert,
            )
            db.commit()
        except Exception as e:
            db.rollback()
            log.exception("Ingest batch write failed for client %s", api_key.client_name)
            # Surface the failure to the client so it can retry.
            raise HTTPException(
                status_code=500,
                detail=f"Ingest write failed: {type(e).__name__}",
            )

    log.info(
        "Ingest from %s: accepted=%d rejected=%d warned=%d",
        api_key.client_name, accepted, rejected, warned,
    )

    return IngestResponse(
        accepted=accepted,
        rejected=rejected,
        warned=warned,
        errors=errors,
    )
