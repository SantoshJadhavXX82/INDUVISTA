#!/usr/bin/env python3
"""
Period-window smoke test (pure, offline — no DB, no running stack).

Validates app.services.report_period.compute_window against hand-computed
windows in the plant timezone (Asia/Kolkata, UTC+5:30, no DST). Run it anywhere:

  python scripts/report_period_smoke.py        # host venv is fine

Exit code = number of failed checks (0 = green).
"""
from __future__ import annotations
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

# Make app.* importable whether run from repo root or elsewhere.
_BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from app.services.report_period import compute_window  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")
PASS = FAIL = 0


def U(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=UTC)


def ist(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=IST)


def check(name, rule, ref, exp_start, exp_end):
    global PASS, FAIL
    try:
        s, e = compute_window(rule, ref, "Asia/Kolkata")
        if s == exp_start and e == exp_end:
            PASS += 1
            print(f"  [PASS] {name}: {s.isoformat()} .. {e.isoformat()}")
        else:
            FAIL += 1
            print(f"  [FAIL] {name}\n         got {s.isoformat()} .. {e.isoformat()}"
                  f"\n         exp {exp_start.isoformat()} .. {exp_end.isoformat()}")
    except Exception as ex:  # noqa: BLE001
        FAIL += 1
        print(f"  [FAIL] {name}: raised {type(ex).__name__}: {ex}")


def check_raises(name, rule, ref):
    global PASS, FAIL
    try:
        compute_window(rule, ref, "Asia/Kolkata")
        FAIL += 1
        print(f"  [FAIL] {name}: expected ValueError, none raised")
    except ValueError:
        PASS += 1
        print(f"  [PASS] {name}: raised ValueError as expected")
    except Exception as ex:  # noqa: BLE001
        FAIL += 1
        print(f"  [FAIL] {name}: wrong exception {type(ex).__name__}: {ex}")


def main() -> int:
    print("Period-window smoke (plant tz = Asia/Kolkata)")

    # 1. Hourly previous_completed — the canonical 10:05 -> 09:00..10:00 case.
    check("hourly/previous @10:05",
          {"period_type": "hourly", "period_rule": "previous_completed"},
          ist(2026, 5, 27, 10, 5),
          U(2026, 5, 27, 3, 30), U(2026, 5, 27, 4, 30))

    # 2. Hourly current — the in-progress hour.
    check("hourly/current @10:05",
          {"period_type": "hourly", "period_rule": "current"},
          ist(2026, 5, 27, 10, 5),
          U(2026, 5, 27, 4, 30), U(2026, 5, 27, 5, 30))

    # 3. Daily previous_completed, 06:00 boundary, ref after boundary.
    check("daily/previous 06:00 @10:05",
          {"period_type": "daily", "period_rule": "previous_completed", "boundary_offset_min": 360},
          ist(2026, 5, 27, 10, 5),
          U(2026, 5, 26, 0, 30), U(2026, 5, 27, 0, 30))

    # 4. Daily previous_completed, 06:00 boundary, ref BEFORE boundary (04:00).
    check("daily/previous 06:00 @04:00",
          {"period_type": "daily", "period_rule": "previous_completed", "boundary_offset_min": 360},
          ist(2026, 5, 27, 4, 0),
          U(2026, 5, 25, 0, 30), U(2026, 5, 26, 0, 30))

    # 5. Monthly previous_completed (April), no boundary offset.
    check("monthly/previous @2026-05-27",
          {"period_type": "monthly", "period_rule": "previous_completed"},
          ist(2026, 5, 27, 10, 5),
          U(2026, 3, 31, 18, 30), U(2026, 4, 30, 18, 30))

    # 6. Monthly January wrap -> previous = December of prior year.
    check("monthly/previous Jan wrap",
          {"period_type": "monthly", "period_rule": "previous_completed"},
          ist(2026, 1, 15, 9, 0),
          U(2025, 11, 30, 18, 30), U(2025, 12, 31, 18, 30))

    # 7. Weekly previous_completed (ref Wed 2026-05-27 -> prev ISO week Mon..Mon).
    check("weekly/previous @Wed",
          {"period_type": "weekly", "period_rule": "previous_completed"},
          ist(2026, 5, 27, 10, 5),
          U(2026, 5, 17, 18, 30), U(2026, 5, 24, 18, 30))

    # 8. Custom: last 2 hours up to the hour anchor.
    check("custom -120..0 @10:05",
          {"period_type": "custom", "custom_start_offset_min": -120, "custom_end_offset_min": 0},
          ist(2026, 5, 27, 10, 5),
          U(2026, 5, 27, 2, 30), U(2026, 5, 27, 4, 30))

    # 9. shift not implemented yet -> ValueError.
    check_raises("shift not implemented",
                 {"period_type": "shift", "period_rule": "previous_completed"},
                 ist(2026, 5, 27, 10, 5))

    # 10. unknown period_type -> ValueError.
    check_raises("unknown period_type",
                 {"period_type": "fortnightly", "period_rule": "current"},
                 ist(2026, 5, 27, 10, 5))

    print("-" * 60)
    print(f"PASS {PASS}   FAIL {FAIL}   " + ("GREEN" if FAIL == 0 else "FAILURE(S)"))
    return min(FAIL, 120)


if __name__ == "__main__":
    sys.exit(main())
