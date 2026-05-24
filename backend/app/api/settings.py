"""System-wide settings — Phase 12.2 + 27d MVP.

Originally just duty/standby (Phase 12.2). Phase 27d MVP adds:
- Generic GET /api/settings           returns all key/value pairs
- Generic PATCH /api/settings         updates one or more keys
- GET   /api/settings/timezones       IANA timezone list for picker

The DB schema (system_settings table) is unchanged — same simple
key/value store. Validation logic lives in the route handlers so
the table can stay completely generic. Add a new validator below
when introducing a new key.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Callable
from zoneinfo import ZoneInfo, available_timezones

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.utils.timezone import invalidate_timezone_cache


router = APIRouter(prefix="/api", tags=["settings"])


# ---------------------------------------------------------------------------
# Generic settings — Phase 27d MVP
# ---------------------------------------------------------------------------

class SettingsResponse(BaseModel):
    """Flat dict of all settings."""
    settings: dict[str, str]


class SettingsPatchBody(BaseModel):
    """Partial update — any keys provided are upserted; absent keys
    are left untouched. Values are always strings (the underlying table
    column is TEXT)."""
    updates: dict[str, str] = Field(
        ...,
        description="Map of setting key to new value.",
        examples=[{"app.timezone": "Asia/Singapore"}],
    )


# Settings whose values must pass a custom validator. Add new keys here
# as the surface grows. Validators raise ValueError on bad input; the
# route converts that into HTTP 400.
def _validate_timezone(value: str) -> None:
    """Confirm a string is a valid IANA timezone identifier."""
    try:
        ZoneInfo(value)
    except Exception as e:
        raise ValueError(
            f"'{value}' is not a valid IANA timezone identifier. "
            f"Examples: Asia/Kolkata, Europe/London, US/Eastern. "
            f"(zoneinfo says: {e})"
        )


def _validate_duty_value(value: str) -> None:
    try:
        int(value)
    except ValueError:
        raise ValueError(
            f"'{value}' is not an integer. duty/standby values must be integers."
        )


SETTING_VALIDATORS: dict[str, Callable[[str], None]] = {
    "app.timezone": _validate_timezone,
    "duty_standby.duty_value": _validate_duty_value,
    "duty_standby.standby_value": _validate_duty_value,
}

# Callbacks invoked after a successful PATCH for that key, so any
# in-memory caches downstream can be invalidated immediately rather
# than waiting for their TTL to expire. Keep these lightweight —
# they run inside the request handler.
SETTING_POST_UPDATE_HOOKS: dict[str, Callable[[], None]] = {
    "app.timezone": invalidate_timezone_cache,
}


@router.get("/settings", response_model=SettingsResponse)
def get_all_settings(db: Annotated[Session, Depends(get_session)]):
    """Return every system_settings row as a flat dict."""
    rows = db.execute(
        text("SELECT key, value FROM system_settings ORDER BY key")
    ).mappings().all()
    return {"settings": {r["key"]: r["value"] for r in rows}}


@router.patch("/settings", response_model=SettingsResponse)
def patch_settings(
    body: SettingsPatchBody,
    db: Annotated[Session, Depends(get_session)],
):
    """Upsert one or more settings.

    Each key's value is validated by its registered validator (if any).
    Validation failures return HTTP 400 with a useful message; nothing
    is written when any key fails.

    After a successful write, post-update hooks fire — for example,
    patching app.timezone triggers invalidate_timezone_cache so the
    next heatmap query sees the new value immediately.
    """
    # 1. Validate everything FIRST (atomic-ish: don't partially update).
    for key, value in body.updates.items():
        validator = SETTING_VALIDATORS.get(key)
        if validator is None:
            # Unknown keys are allowed; new keys may be introduced
            # without backend updates. (No-op here.)
            continue
        try:
            validator(value)
        except ValueError as e:
            raise HTTPException(400, f"Invalid value for {key!r}: {e}")

    # 2. Upsert in one transaction.
    for key, value in body.updates.items():
        db.execute(
            text("""
                INSERT INTO system_settings (key, value, updated_at)
                VALUES (:key, :value, NOW())
                ON CONFLICT (key) DO UPDATE
                  SET value = EXCLUDED.value,
                      updated_at = NOW()
            """),
            {"key": key, "value": value},
        )
    db.commit()

    # 3. Run post-update hooks for keys that have them.
    for key in body.updates:
        hook = SETTING_POST_UPDATE_HOOKS.get(key)
        if hook is not None:
            try:
                hook()
            except Exception:
                # Hook failures shouldn't fail the PATCH itself; the
                # value is already persisted and the cache will refresh
                # on its next TTL expiry.
                pass

    # 4. Return the latest snapshot — frontend re-renders from this.
    rows = db.execute(
        text("SELECT key, value FROM system_settings ORDER BY key")
    ).mappings().all()
    return {"settings": {r["key"]: r["value"] for r in rows}}


# ---------------------------------------------------------------------------
# Timezone list endpoint
# ---------------------------------------------------------------------------

class TimezoneOption(BaseModel):
    value: str = Field(..., description="IANA name, e.g. 'Asia/Kolkata'")
    label: str = Field(..., description="Display label, e.g. 'Asia/Kolkata (UTC+05:30)'")
    offset_minutes: int = Field(..., description="UTC offset in minutes (positive = east of UTC)")


class TimezoneListResponse(BaseModel):
    timezones: list[TimezoneOption]
    current: str = Field(..., description="The currently-active timezone (what get_app_timezone() returns)")


@router.get("/settings/timezones", response_model=TimezoneListResponse)
def list_timezones(db: Annotated[Session, Depends(get_session)]):
    """Return every IANA timezone with its current UTC offset, plus the
    currently-active timezone. Frontend uses this to populate a
    searchable picker.

    The offset is computed at request time, so DST-affected timezones
    show the correct offset for "now" (which is what operators expect
    when picking).
    """
    utc_now = datetime.now(ZoneInfo("UTC"))

    options: list[TimezoneOption] = []
    for tz_name in sorted(available_timezones()):
        try:
            tz = ZoneInfo(tz_name)
            local_now = utc_now.astimezone(tz)
            offset = local_now.utcoffset()
            if offset is None:
                continue
            total_minutes = int(offset.total_seconds() // 60)
            hours, minutes = divmod(abs(total_minutes), 60)
            sign = "+" if total_minutes >= 0 else "-"
            label = f"{tz_name} (UTC{sign}{hours:02d}:{minutes:02d})"
            options.append(TimezoneOption(
                value=tz_name,
                label=label,
                offset_minutes=total_minutes,
            ))
        except Exception:
            # Skip any timezone the runtime can't resolve (very rare).
            continue

    row = db.execute(
        text("SELECT value FROM system_settings WHERE key = 'app.timezone'")
    ).first()
    current = row[0] if row and row[0] else "Asia/Kolkata"

    return TimezoneListResponse(timezones=options, current=current)


# ---------------------------------------------------------------------------
# Duty/standby — unchanged from Phase 12.2 (kept for backwards compat).
# Operators can also PATCH via the generic endpoint above; this typed
# variant is kept because the worker reads it directly.
# ---------------------------------------------------------------------------

class DutyStandbySettings(BaseModel):
    duty_value: int = Field(..., description="Numeric value meaning 'this device is currently duty'")
    standby_value: int = Field(..., description="Numeric value meaning 'this device is currently standby'")


@router.get("/settings/duty-standby", response_model=DutyStandbySettings)
def get_duty_standby_settings(db: Annotated[Session, Depends(get_session)]):
    """Return the system-wide duty and standby value conventions."""
    rows = db.execute(
        text("SELECT key, value FROM system_settings "
             "WHERE key IN ('duty_standby.duty_value', 'duty_standby.standby_value')")
    ).mappings().all()
    settings_map = {r["key"]: int(r["value"]) for r in rows}
    return {
        "duty_value": settings_map.get("duty_standby.duty_value", 1),
        "standby_value": settings_map.get("duty_standby.standby_value", 0),
    }


@router.patch("/settings/duty-standby", response_model=DutyStandbySettings)
def update_duty_standby_settings(
    body: DutyStandbySettings,
    db: Annotated[Session, Depends(get_session)],
):
    """Update the duty/standby value convention."""
    if body.duty_value == body.standby_value:
        raise HTTPException(
            400,
            f"duty_value and standby_value must be different (both got {body.duty_value})",
        )

    db.execute(
        text("""
            INSERT INTO system_settings (key, value, updated_at)
            VALUES ('duty_standby.duty_value', :duty, NOW()),
                   ('duty_standby.standby_value', :standby, NOW())
            ON CONFLICT (key) DO UPDATE
              SET value = EXCLUDED.value,
                  updated_at = NOW()
        """),
        {"duty": str(body.duty_value), "standby": str(body.standby_value)},
    )
    db.commit()
    return body
