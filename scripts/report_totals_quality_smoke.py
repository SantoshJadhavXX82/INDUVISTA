#!/usr/bin/env python3
"""Totals-quality smoke (Phase 2d) — runs INSIDE a backend container (real DB).

Proves that Held / Substituted device-fabricated fill values are EXCLUDED from
period aggregates (sum/avg/min/max/count) under every quality_rule, while
genuine good/uncertain readings are still governed by the rule. The decisive
case: a Held sample has st=72 (uncertain *band*) but st_reason='HOLD_LAST', so
it must be excluded under 'good_uncertain' even though its band would include it.

It inserts a tiny controlled sample set into a throwaway far-future window for a
real tag, runs aggregate_value, asserts, and always deletes the rows.

  docker cp scripts/report_totals_quality_smoke.py svj_backend:/tmp/totq.py
  docker exec -e PYTHONPATH=/app svj_backend python /tmp/totq.py

Exit code = number of failed checks (0 = green).
"""
from __future__ import annotations
import sys
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.db import SessionLocal
from app.services.report_aggregate import aggregate_value

PASS = FAIL = 0


def ok(name, detail=""):
    global PASS
    PASS += 1
    print(f"  [PASS] {name}" + (f" — {detail}" if detail else ""))


def bad(name, detail=""):
    global FAIL
    FAIL += 1
    print(f"  [FAIL] {name}" + (f" — {detail}" if detail else ""))


def approx(a, b, eps=1e-6):
    return a is not None and b is not None and abs(a - b) < eps


# Controlled sample set, inserted at distinct times in the window.
#   (offset_seconds, value, st,  st_reason)
SAMPLES = [
    (0,  100.0, 128, "READ_OK"),     # good
    (1,  200.0, 128, "READ_OK"),     # good
    (2,  300.0, 128, "READ_OK"),     # good
    (3,  250.0, 100, "RANGE"),       # GENUINE uncertain (band 64..127, not fabricated)
    (4, 9999.0,  72, "HOLD_LAST"),   # fabricated: Held (uncertain band, excluded by reason)
    (5,   -5.0,  28, "SUBSTITUTED"), # fabricated: Substituted (bad band)
]


def main() -> int:
    print("Totals-quality smoke (Phase 2d: exclude Held/Substituted from totals)")
    db = SessionLocal()
    start = datetime(2099, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    end = start + timedelta(minutes=1)
    window = (start, end)

    tid_row = db.execute(text("""
        SELECT tag_id, device_id, register_block_id
        FROM latest_tag_values
        WHERE device_id IS NOT NULL
        ORDER BY tag_id LIMIT 1
    """)).first()
    if not tid_row:
        bad("find a polled tag for the test", "no latest_tag_values rows with device_id")
        print(f"PASS {PASS} FAIL {FAIL}")
        return 1
    tag_id, device_id, register_block_id = tid_row[0], tid_row[1], tid_row[2]
    print(f"  using tag_id={tag_id} (device_id={device_id}, block={register_block_id}), "
          f"throwaway window {start.isoformat()}..{end.isoformat()}")

    try:
        # ---- insert the controlled set --------------------------------------
        for off, val, st, reason in SAMPLES:
            db.execute(text("""
                INSERT INTO tag_values (time, tag_id, device_id, register_block_id,
                                        value_double, value_text, st, st_reason, source)
                VALUES (:ts, :t, :dev, :blk, :v, NULL, :st, :r, 'modbus')
                ON CONFLICT (tag_id, time) DO UPDATE
                  SET value_double = EXCLUDED.value_double, st = EXCLUDED.st,
                      st_reason = EXCLUDED.st_reason
            """), {"t": tag_id, "dev": device_id, "blk": register_block_id,
                   "ts": start + timedelta(seconds=off),
                   "v": val, "st": st, "r": reason})
        db.commit()

        def agg(func, rule):
            return aggregate_value(db, tag_id, func, start, end, rule)["value"]

        # ---- 'all': genuine good+uncertain counted, fabricated excluded -----
        # good(100,200,300) + genuine uncertain(250) = 850 ; held/subst excluded
        s_all = agg("sum", "all")
        (ok if approx(s_all, 850.0) else bad)(
            "sum 'all' excludes Held+Substituted", f"sum={s_all} (expected 850)")
        (ok if approx(agg("count", "all"), 4) else bad)(
            "count 'all' is 4 (fabricated not counted)", f"count={agg('count','all')}")
        (ok if approx(agg("min", "all"), 100.0) else bad)(
            "min 'all' is 100, not the substituted -5", f"min={agg('min','all')}")
        (ok if approx(agg("max", "all"), 300.0) else bad)(
            "max 'all' is 300, not the held 9999", f"max={agg('max','all')}")
        (ok if approx(agg("average", "all"), 212.5) else bad)(
            "average 'all' is 850/4 = 212.5", f"avg={agg('average','all')}")

        # ---- 'good_only': only st>=128 -------------------------------------
        (ok if approx(agg("sum", "good_only"), 600.0) else bad)(
            "sum 'good_only' is 600 (uncertain+fabricated excluded)",
            f"sum={agg('sum','good_only')}")

        # ---- 'good_uncertain': THE decisive case ---------------------------
        # genuine uncertain(250) included; Held(9999, st=72 uncertain band) excluded
        # by st_reason. Expected 850, NOT 850+9999.
        s_gu = agg("sum", "good_uncertain")
        (ok if approx(s_gu, 850.0) else bad)(
            "sum 'good_uncertain' includes genuine uncertain but EXCLUDES Held",
            f"sum={s_gu} (expected 850; a band-only filter would give {850+9999})")

    except Exception as e:
        bad("run aggregates", f"{type(e).__name__}: {e}")
    finally:
        # ---- always clean up the throwaway rows -----------------------------
        try:
            db.execute(text(
                "DELETE FROM tag_values WHERE tag_id = :t AND time >= :s AND time < :e"),
                {"t": tag_id, "s": start, "e": end})
            db.commit()
            print("  [cleanup] throwaway samples deleted")
        except Exception as e:
            print(f"  [cleanup] FAILED to delete throwaway samples: {e}")
        db.close()

    print("-" * 60)
    print(f"PASS {PASS}   FAIL {FAIL}   {'GREEN' if FAIL == 0 else 'FAILURE(S)'}")
    return FAIL


if __name__ == "__main__":
    sys.exit(main())
