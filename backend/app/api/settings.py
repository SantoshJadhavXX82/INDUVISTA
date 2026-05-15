"""System-wide settings — Phase 12.2.

Currently only duty/standby value convention is exposed. Future
settings (default scan intervals, retention windows, etc.) can be
added under the same generic key/value model.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session

router = APIRouter(prefix="/api", tags=["settings"])


class DutyStandbySettings(BaseModel):
    duty_value: int = Field(..., description="Numeric value meaning 'this device is currently duty'")
    standby_value: int = Field(..., description="Numeric value meaning 'this device is currently standby'")


@router.get("/settings/duty-standby", response_model=DutyStandbySettings)
def get_duty_standby_settings(db: Annotated[Session, Depends(get_session)]):
    """Return the system-wide duty and standby value conventions.

    Defaults are 1 (duty) and 0 (standby), but the worker reads these
    from system_settings every cycle so an operator change takes effect
    on the next poll without a worker restart.
    """
    rows = db.execute(
        text("SELECT key, value FROM system_settings "
             "WHERE key IN ('duty_standby.duty_value', 'duty_standby.standby_value')")
    ).mappings().all()
    settings = {r["key"]: int(r["value"]) for r in rows}
    return {
        "duty_value": settings.get("duty_standby.duty_value", 1),
        "standby_value": settings.get("duty_standby.standby_value", 0),
    }


@router.patch("/settings/duty-standby", response_model=DutyStandbySettings)
def update_duty_standby_settings(
    body: DutyStandbySettings,
    db: Annotated[Session, Depends(get_session)],
):
    """Update the duty/standby value convention.

    Both values must be distinct integers. The worker picks up the
    change within one polling cycle (typically <5 seconds)."""
    if body.duty_value == body.standby_value:
        raise HTTPException(
            400,
            f"duty_value and standby_value must be different (both got {body.duty_value})",
        )

    # UPSERT both rows in one transaction.
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
