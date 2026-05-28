"""Phase 27e — plant shift definitions (GET/PATCH /api/settings/shifts).

Stores a 3-shift schedule as JSON in the generic system_settings table
(key 'shifts.config') — no new table/migration. Each shift has a code
(A/B/C), a label (Morning/Evening/Night), and a start time HH:MM. Shifts
are assumed contiguous and ordered by start time; a shift runs until the
next shift's start (the last wraps past midnight to the first).

The frontend computes the *current* shift client-side from this config +
the plant timezone, so no per-second server load.

Default (if unset): A 06:00 Morning, B 14:00 Evening, C 22:00 Night.
"""
import sys, pathlib
TARGET = pathlib.Path("backend/app/api/settings.py")
MARKER = "Phase 27e - shift definitions"

# Append after the duty-standby update handler (end of file).
ANCHOR = '''        {"duty": str(body.duty_value), "standby": str(body.standby_value)},
    )
    db.commit()
    return body'''

ADDITION = '''        {"duty": str(body.duty_value), "standby": str(body.standby_value)},
    )
    db.commit()
    return body


# ---------------------------------------------------------------------------
# Phase 27e - shift definitions
# ---------------------------------------------------------------------------
import json as _json


class Shift(BaseModel):
    code: str = Field(..., min_length=1, max_length=4, description="e.g. A / B / C")
    label: str = Field(..., min_length=1, max_length=40, description="e.g. Morning")
    start: str = Field(..., description="Start time HH:MM (24h), plant local time")


class ShiftsConfig(BaseModel):
    enabled: bool = True
    shifts: list[Shift]


_DEFAULT_SHIFTS = {
    "enabled": True,
    "shifts": [
        {"code": "A", "label": "Morning", "start": "06:00"},
        {"code": "B", "label": "Evening", "start": "14:00"},
        {"code": "C", "label": "Night",   "start": "22:00"},
    ],
}

_HHMM_RE = __import__("re").compile(r"^([01]\\d|2[0-3]):[0-5]\\d$")


def _validate_shifts(cfg: ShiftsConfig) -> None:
    if not cfg.shifts:
        raise HTTPException(400, "At least one shift is required.")
    if len(cfg.shifts) > 6:
        raise HTTPException(400, "At most 6 shifts are supported.")
    seen_codes = set()
    seen_starts = set()
    for s in cfg.shifts:
        if not _HHMM_RE.match(s.start):
            raise HTTPException(400, f"Shift {s.code!r} start {s.start!r} must be HH:MM (24h).")
        if s.code in seen_codes:
            raise HTTPException(400, f"Duplicate shift code {s.code!r}.")
        if s.start in seen_starts:
            raise HTTPException(400, f"Duplicate shift start time {s.start!r}.")
        seen_codes.add(s.code)
        seen_starts.add(s.start)


@router.get("/settings/shifts", response_model=ShiftsConfig)
def get_shifts(db: Annotated[Session, Depends(get_session)]):
    """Return the plant shift schedule (or sensible 3-shift default)."""
    row = db.execute(
        text("SELECT value FROM system_settings WHERE key = 'shifts.config'")
    ).first()
    if not row or not row[0]:
        return ShiftsConfig(**_DEFAULT_SHIFTS)
    try:
        data = _json.loads(row[0])
        return ShiftsConfig(**data)
    except Exception:
        # Corrupt/legacy value — fall back to default rather than 500.
        return ShiftsConfig(**_DEFAULT_SHIFTS)


@router.patch("/settings/shifts", response_model=ShiftsConfig)
def update_shifts(
    body: ShiftsConfig,
    db: Annotated[Session, Depends(get_session)],
):
    """Replace the plant shift schedule. Shifts are ordered by start time
    on save so the frontend can assume ascending order."""
    _validate_shifts(body)
    body.shifts.sort(key=lambda s: s.start)
    db.execute(
        text("""
            INSERT INTO system_settings (key, value, updated_at)
            VALUES ('shifts.config', :val, NOW())
            ON CONFLICT (key) DO UPDATE
              SET value = EXCLUDED.value, updated_at = NOW()
        """),
        {"val": _json.dumps(body.model_dump())},
    )
    db.commit()
    return body'''


def main():
    if not TARGET.exists(): print(f"  ERROR: {TARGET} not found."); return 1
    t = TARGET.read_text(encoding="utf-8")
    if MARKER in t: print("  [SKIP] already applied."); return 0
    if t.count(ANCHOR) != 1:
        print(f"  ERROR: anchor found {t.count(ANCHOR)}x (expected 1). Abort."); return 1
    bak = TARGET.with_suffix(".py.bak_shifts")
    if not bak.exists(): bak.write_text(t, encoding="utf-8"); print(f"  Backup: {bak.name}")
    TARGET.write_text(t.replace(ANCHOR, ADDITION, 1), encoding="utf-8")
    print("  Applied: GET/PATCH /api/settings/shifts + ShiftsConfig model")
    return 0

if __name__ == "__main__":
    sys.exit(main())
