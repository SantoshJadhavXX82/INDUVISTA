"""Fully automated smoke for the OPC device fault policy (Phase 2e).

Exhaustively exercises ``_SourceContext.apply_fault_policy`` — the decision
core shared by BOTH the read path (datachange_notification, 2e.1) and the
disconnect-hold loop (2e.2b) — across every fault_mode, every OPC status band,
the max_age decay boundary, and the not-Good -> Good recovery transition.

No DB, no network: it constructs the context directly (like the standalone
``scripts/opc_fault_policy_smoke.py``) but as pytest so it runs in CI:

    # in the backend container (has asyncua) or any env with the deps:
    docker exec -e PYTHONPATH=/app svj_opc_worker python -m pytest \
        /app/tests/test_opc_fault_policy.py -v
    # or locally:
    cd backend && PYTHONPATH=. python -m pytest tests/test_opc_fault_policy.py -v

Status bands under test (what _ua_status_to_st emits, plus the disconnect feed):
    GOOD=192 (OPC Good)   UNCERTAIN=96   BAD=0   STALE=64 (disconnect-hold feed)
The policy treats anything not-Good as a failure, matching modbus exactly.
"""
import os

import pytest

# The supervisor imports app.db -> app.config.Settings, which requires a
# DATABASE_URL. This test never touches the DB (pure policy logic), so provide
# a placeholder if one isn't already set. setdefault means the real container
# env still wins; create_engine is lazy so nothing actually connects.
os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg2://localhost:5432/induvista_test"
)

# Importing the supervisor pulls in asyncua transitively; skip cleanly if the
# OPC stack isn't installed in this environment rather than erroring collection.
pytest.importorskip("asyncua")

from app.modbus.status import (  # noqa: E402
    ST_HOLD_LAST,
    ST_RANGE_WARN,
    ST_READ_OK,
    ST_STALE,
    ST_SUBSTITUTED,
)
from app.workers.opc_supervisor import _SampleBuffer, _SourceContext  # noqa: E402

GOOD = 192        # OPC Good band
UNCERTAIN = 96    # OPC Uncertain
BAD = 0           # OPC Bad
STALE = ST_STALE  # 64 — the status the disconnect-hold loop feeds in
TAG = 10


def ctx(mode, **kw) -> _SourceContext:
    """A bare source context with the given fault policy, no DB/network."""
    return _SourceContext(
        source_id=1,
        source_name="TEST",
        device_id=1,
        tag_by_node={},
        buffer=_SampleBuffer(),
        fault_mode=mode,
        **kw,
    )


# --- Good readings: pass through unchanged and are recorded as last-good -----

@pytest.mark.parametrize("good_st", [GOOD, ST_READ_OK, ST_RANGE_WARN])
def test_good_passes_through_and_is_recorded(good_st):
    c = ctx("hold_last")
    vd, vt, st, reason = c.apply_fault_policy(TAG, 12.5, None, good_st, None, now=0.0)
    assert (vd, st) == (12.5, good_st)
    assert reason is None
    assert c.last_good_by_tag[TAG][0] == 12.5  # available for a later hold


# --- hold_last: ANY not-Good status holds the last good value ----------------

@pytest.mark.parametrize("bad_st", [UNCERTAIN, BAD, STALE])
def test_hold_last_holds_on_any_not_good(bad_st):
    c = ctx("hold_last")
    c.apply_fault_policy(TAG, 100.0, None, GOOD, None, now=0.0)  # seed last-good
    vd, vt, st, reason = c.apply_fault_policy(TAG, None, None, bad_st, "x", now=1.0)
    assert vd == 100.0
    assert st == ST_HOLD_LAST
    assert reason == "HOLD_LAST"


def test_hold_last_with_no_prior_good_lets_failure_stand():
    c = ctx("hold_last")
    vd, vt, st, reason = c.apply_fault_policy(TAG, None, None, BAD, "x", now=0.0)
    assert st == BAD  # nothing good to hold -> the failure is reported honestly


# --- max_age decay -----------------------------------------------------------

def test_hold_last_within_max_age_holds():
    c = ctx("hold_last", hold_mode="max_age", max_hold_sec=10.0)
    c.apply_fault_policy(TAG, 50.0, None, GOOD, None, now=100.0)
    vd, vt, st, _ = c.apply_fault_policy(TAG, None, None, STALE, "x", now=109.0)
    assert (vd, st) == (50.0, ST_HOLD_LAST)


def test_hold_last_beyond_max_age_releases_to_failure():
    c = ctx("hold_last", hold_mode="max_age", max_hold_sec=10.0)
    c.apply_fault_policy(TAG, 50.0, None, GOOD, None, now=100.0)
    vd, vt, st, _ = c.apply_fault_policy(TAG, None, None, STALE, "x", now=120.0)
    assert st == STALE  # window elapsed -> the real failure stands


def test_hold_last_indefinite_never_releases():
    c = ctx("hold_last", hold_mode="indefinite", max_hold_sec=None)
    c.apply_fault_policy(TAG, 7.0, None, GOOD, None, now=0.0)
    vd, vt, st, _ = c.apply_fault_policy(TAG, None, None, BAD, "x", now=10_000.0)
    assert (vd, st) == (7.0, ST_HOLD_LAST)


# --- substitute --------------------------------------------------------------

def test_substitute_emits_configured_value():
    c = ctx("substitute", substitute_value=42.0)
    vd, vt, st, reason = c.apply_fault_policy(TAG, None, None, BAD, "x", now=0.0)
    assert (vd, st, reason) == (42.0, ST_SUBSTITUTED, "SUBSTITUTED")


def test_substitute_null_defaults_to_zero():
    c = ctx("substitute", substitute_value=None)
    vd, vt, st, _ = c.apply_fault_policy(TAG, None, None, BAD, "x", now=0.0)
    assert (vd, st) == (0.0, ST_SUBSTITUTED)


# --- missing / None: never fabricate data ------------------------------------

@pytest.mark.parametrize("mode", ["missing", None])
def test_missing_or_none_passes_failure_through(mode):
    c = ctx(mode)
    c.apply_fault_policy(TAG, 5.0, None, GOOD, None, now=0.0)
    vd, vt, st, _ = c.apply_fault_policy(TAG, None, None, BAD, "0x80", now=1.0)
    assert st == BAD
    assert vd is None


# --- recovery + edge-triggered state ----------------------------------------

def test_recovery_to_good_resets_fault_state():
    c = ctx("hold_last")
    c.apply_fault_policy(TAG, 1.0, None, GOOD, None, now=0.0)
    c.apply_fault_policy(TAG, None, None, BAD, "x", now=1.0)
    assert c.fault_state_by_tag[TAG] == "held"
    vd, vt, st, _ = c.apply_fault_policy(TAG, 2.0, None, GOOD, None, now=2.0)
    assert (vd, st) == (2.0, GOOD)
    assert c.fault_state_by_tag[TAG] == "ok"  # cleared on the good read


def test_consecutive_holds_keep_state_held():
    # Two holds in a row keep state 'held' so _note_fault logs only on the
    # transition, not every cycle (the disconnect loop runs every 20s).
    c = ctx("hold_last")
    c.apply_fault_policy(TAG, 1.0, None, GOOD, None, now=0.0)
    c.apply_fault_policy(TAG, None, None, BAD, "x", now=1.0)
    c.apply_fault_policy(TAG, None, None, BAD, "x", now=2.0)
    assert c.fault_state_by_tag[TAG] == "held"


def test_independent_tags_hold_independently():
    c = ctx("hold_last")
    c.apply_fault_policy(1, 10.0, None, GOOD, None, now=0.0)
    c.apply_fault_policy(2, 20.0, None, GOOD, None, now=0.0)
    vd1, _, st1, _ = c.apply_fault_policy(1, None, None, BAD, "x", now=1.0)
    vd2, _, st2, _ = c.apply_fault_policy(2, 20.0, None, GOOD, None, now=1.0)
    assert (vd1, st1) == (10.0, ST_HOLD_LAST)  # tag 1 held
    assert (vd2, st2) == (20.0, GOOD)          # tag 2 unaffected
