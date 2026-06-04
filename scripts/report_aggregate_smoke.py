#!/usr/bin/env python3
"""
Aggregation-engine smoke test (runs INSIDE a backend container — real DB).

Validates app.services.report_aggregate against live historian data without
touching the render/scheduler paths. It discovers a tag that actually has
samples, picks a window covering them, and checks every data function plus
build_period_context (including a deliberately-missing tag).

  docker cp scripts/report_aggregate_smoke.py svj_backend:/tmp/agg_smoke.py
  docker exec -e PYTHONPATH=/app svj_backend python /tmp/agg_smoke.py

Exit code = number of failed checks (0 = green).
"""
from __future__ import annotations
import sys
from datetime import timedelta

from sqlalchemy import text

from app.db import SessionLocal
from app.services.report_aggregate import (
    aggregate_value, build_period_context, derive_display_st, GOOD_ST, SUSPECT_ST,
)

PASS = FAIL = 0


def ok(name, detail=""):
    global PASS
    PASS += 1
    print(f"  [PASS] {name}" + (f" — {detail}" if detail else ""))


def bad(name, detail=""):
    global FAIL
    FAIL += 1
    print(f"  [FAIL] {name}" + (f" — {detail}" if detail else ""))


def main() -> int:
    print("Aggregation-engine smoke (real historian data)")

    # Pure helper checks first (no DB).
    (ok if derive_display_st(0, 0) is None else bad)("display_st: no samples -> Missing")
    (ok if derive_display_st(10, 10) == GOOD_ST else bad)("display_st: all good -> Good")
    (ok if derive_display_st(10, 4) == SUSPECT_ST else bad)("display_st: some good -> Uncertain")
    (ok if derive_display_st(10, 0) == 0 else bad)("display_st: none good -> Bad")

    db = SessionLocal()
    try:
        # Discover a tag with the most recent numeric samples.
        row = db.execute(text("""
            SELECT tag_id, count(*) AS c, min(time) AS mn, max(time) AS mx
            FROM tag_values
            WHERE value_double IS NOT NULL AND time > now() - interval '6 hours'
            GROUP BY tag_id ORDER BY c DESC LIMIT 1
        """)).mappings().first()
        if not row:
            row = db.execute(text("""
                SELECT tag_id, count(*) AS c, min(time) AS mn, max(time) AS mx
                FROM tag_values WHERE value_double IS NOT NULL
                GROUP BY tag_id ORDER BY c DESC LIMIT 1
            """)).mappings().first()
        if not row or not row["c"]:
            bad("discover tag with data", "no numeric samples found in tag_values")
            print("-" * 60); print(f"PASS {PASS}   FAIL {FAIL}   FAILURE(S)")
            return 1

        tag_id = row["tag_id"]
        end = row["mx"] + timedelta(seconds=1)
        start = max(row["mn"], row["mx"] - timedelta(minutes=30))
        window = (start, end)
        ok("discover tag with data",
           f"tag_id={tag_id}, {row['c']} samples, window {start.isoformat()}..{end.isoformat()}")

        vals = {f: aggregate_value(db, tag_id, f, start, end, "all")
                for f in ("latest", "first", "last", "average", "min", "max",
                          "sum", "count", "delta", "availability", "missing_pct")}

        cnt = vals["count"]["value"]
        (ok if (cnt or 0) > 0 else bad)("count > 0", f"count={cnt}")

        mn, av, mx = vals["min"]["value"], vals["average"]["value"], vals["max"]["value"]
        if None not in (mn, av, mx):
            (ok if mn <= av <= mx else bad)("min <= average <= max",
                                            f"{mn} <= {av} <= {mx}")
        else:
            bad("min <= average <= max", f"got min={mn} avg={av} max={mx}")

        for f in ("latest", "first", "last"):
            v = vals[f]["value"]
            (ok if isinstance(v, (int, float)) else bad)(f"{f} returns a number", f"{f}={v}")

        # carry-forward (sample-and-hold): a window entirely AFTER the last
        # sample still yields the value-in-effect for 'latest', while window-
        # bound functions (average) and first/last return nothing.
        cf_start = row["mx"] + timedelta(minutes=1)
        cf_end = row["mx"] + timedelta(hours=1)
        lat_cf = aggregate_value(db, tag_id, "latest", cf_start, cf_end, "all")["value"]
        avg_cf = aggregate_value(db, tag_id, "average", cf_start, cf_end, "all")["value"]
        first_cf = aggregate_value(db, tag_id, "first", cf_start, cf_end, "all")["value"]
        (ok if lat_cf is not None else bad)("latest carries forward into an empty window",
                                            f"latest={lat_cf}")
        (ok if avg_cf is None else bad)("average does NOT carry forward (empty -> none)",
                                        f"avg={avg_cf}")
        (ok if first_cf is None else bad)("first stays window-bound (empty -> none)",
                                          f"first={first_cf}")

        avail = vals["availability"]["value"]
        (ok if (avail is not None and 0.0 <= avail <= 100.0) else bad)(
            "availability in [0,100]", f"availability={avail}")

        miss = vals["missing_pct"]["value"]
        (ok if (miss is not None and 0.0 <= miss <= 100.0) else bad)(
            "missing_pct in [0,100]", f"missing_pct={miss}")
        # missing_pct is the exact complement of availability when the window
        # has samples (which it does here, since count > 0).
        if avail is not None and miss is not None:
            (ok if abs((avail + miss) - 100.0) < 1e-6 else bad)(
                "availability + missing_pct == 100", f"{avail} + {miss}")
        else:
            bad("availability + missing_pct == 100", f"avail={avail} miss={miss}")

        # delta should equal last - first.
        d, fst, lst = vals["delta"]["value"], vals["first"]["value"], vals["last"]["value"]
        if None not in (d, fst, lst):
            (ok if abs(d - (lst - fst)) < 1e-6 else bad)(
                "delta == last - first", f"{d} vs {lst - fst}")
        else:
            bad("delta == last - first", f"delta={d} first={fst} last={lst}")

        # good_only filter should never count more than all.
        c_all = aggregate_value(db, tag_id, "count", start, end, "all")["value"] or 0
        c_good = aggregate_value(db, tag_id, "count", start, end, "good_only")["value"] or 0
        (ok if c_good <= c_all else bad)("good_only count <= all count",
                                         f"good={c_good} all={c_all}")

        # build_period_context with a real binding + a missing tag.
        bindings = [
            {"tag_id": tag_id, "position": 0, "data_function": "average",
             "quality_rule": "all", "alias": "A"},
            {"tag_id": 999999999, "position": 1, "data_function": "latest",
             "quality_rule": "all", "alias": "MISSING"},
        ]
        ctx = build_period_context(db, bindings, window, "Asia/Kolkata")
        tl = ctx["tags_list"]
        (ok if len(tl) == 2 else bad)("context has 2 tags", f"got {len(tl)}")
        (ok if tl[0].value is not None else bad)("first binding has a value",
                                                 f"value={tl[0].value}")
        (ok if tl[1].value is None and not tl[1].quality_good else bad)(
            "missing tag -> no value, not good")
        (ok if "period_start" in ctx["report"] and "period_end" in ctx["report"] else bad)(
            "context carries period window")

    finally:
        db.close()

    print("-" * 60)
    print(f"PASS {PASS}   FAIL {FAIL}   " + ("GREEN" if FAIL == 0 else "FAILURE(S)"))
    return min(FAIL, 120)


if __name__ == "__main__":
    sys.exit(main())
