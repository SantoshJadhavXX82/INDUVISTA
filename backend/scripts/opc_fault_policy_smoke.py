"""Deterministic smoke for the OPC read-path fault policy (Phase 2e, increment 1).

Constructs a _SourceContext directly and exercises apply_fault_policy across
every mode, with no DB and no network. Run inside the OPC worker container
(which has the asyncua dependency the module imports):

    docker cp scripts/opc_fault_policy_smoke.py svj_opc_worker:/tmp/ofp.py
    docker exec -e PYTHONPATH=/app svj_opc_worker python /tmp/ofp.py

Verifies the user-chosen semantics: anything not-Good (OPC Uncertain=96 or
Bad=0) triggers the policy; Good (192) passes through and is recorded as
last-good. st bytes + st_reason match modbus so downstream is identical.
"""
import sys

from app.workers.opc_supervisor import _SampleBuffer, _SourceContext
from app.modbus.status import ST_HOLD_LAST, ST_SUBSTITUTED

GOOD = 192       # OPC Good band
UNCERTAIN = 96   # OPC Uncertain
BAD = 0          # OPC Bad

failures: list[str] = []


def check(name: str, cond: bool) -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")
    if not cond:
        failures.append(name)


def ctx(mode: str, **kw) -> _SourceContext:
    return _SourceContext(
        source_id=1, source_name="SMOKE", device_id=1,
        tag_by_node={}, buffer=_SampleBuffer(),
        fault_mode=mode, **kw,
    )


print("hold_last:")
c = ctx("hold_last")
vd, vt, st, r = c.apply_fault_policy(10, 123.0, None, GOOD, None, now=0.0)
check("good passes through + recorded", vd == 123.0 and st == GOOD)
vd, vt, st, r = c.apply_fault_policy(10, None, None, BAD, "0x8000", now=1.0)
check("bad -> held last good", vd == 123.0 and st == ST_HOLD_LAST and r == "HOLD_LAST")
vd, vt, st, r = c.apply_fault_policy(10, 999.0, None, UNCERTAIN, "uncertain", now=2.0)
check("uncertain -> held (not-Good)", vd == 123.0 and st == ST_HOLD_LAST)
vd, vt, st, r = c.apply_fault_policy(99, None, None, BAD, "0x8000", now=3.0)
check("no prior good -> bad stands", st == BAD)

print("hold_last (max_age):")
c = ctx("hold_last", hold_mode="max_age", max_hold_sec=10.0)
c.apply_fault_policy(10, 50.0, None, GOOD, None, now=100.0)
vd, vt, st, r = c.apply_fault_policy(10, None, None, BAD, "x", now=105.0)
check("within max_age -> held", vd == 50.0 and st == ST_HOLD_LAST)
vd, vt, st, r = c.apply_fault_policy(10, None, None, BAD, "x", now=120.0)
check("beyond max_age -> failure stands", st == BAD)

print("substitute:")
c = ctx("substitute", substitute_value=42.0)
c.apply_fault_policy(10, 7.0, None, GOOD, None, now=0.0)
vd, vt, st, r = c.apply_fault_policy(10, None, None, BAD, "x", now=1.0)
check("bad -> substitute value", vd == 42.0 and st == ST_SUBSTITUTED and r == "SUBSTITUTED")
c = ctx("substitute", substitute_value=None)
vd, vt, st, r = c.apply_fault_policy(10, None, None, BAD, "x", now=0.0)
check("substitute null -> 0.0", vd == 0.0 and st == ST_SUBSTITUTED)

print("missing (default):")
c = ctx("missing")
c.apply_fault_policy(10, 5.0, None, GOOD, None, now=0.0)
vd, vt, st, r = c.apply_fault_policy(10, None, None, BAD, "0x8000", now=1.0)
check("missing -> bad untouched", st == BAD and vd is None)

c = ctx(None)  # NULL fault_mode behaves as 'missing'
vd, vt, st, r = c.apply_fault_policy(10, None, None, BAD, "0x8000", now=1.0)
check("NULL mode -> bad untouched", st == BAD)

print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("ALL PASS")
