"""Phase 13.3d smoke test — quality marker pipeline.

Validates that bad/uncertain quality information flows end-to-end:
    tag_values  →  continuous aggregate  →  /api/trends/history  →  chart

For a real tag with known bad samples (e.g. FC001_S1_TempTx_mA), we
compare three numbers:
  1. tag_values:        actual bad count (st < 64) in the window
  2. tag_values:        bad count split by value_double NULL vs NOT NULL
  3. tag_values_1m:     SUM(bad_count) across buckets in the window

If #3 ≪ #1, the continuous aggregate is dropping NULL-value bad rows
and the chart will under-report quality issues. That's the bug.

Run from host:
  docker compose cp ./smoke_test_trend_quality.py backend:/tmp/sq.py
  docker compose exec backend python /tmp/sq.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

import psycopg2

API = os.environ.get("INDUVISTA_API", "http://backend:8000/api")
TAG_NAME = os.environ.get("SMOKE_TAG", "FC001_S1_TempTx_mA")
WINDOW_HOURS = int(os.environ.get("SMOKE_HOURS", "6"))

# Build psycopg2 DSN from the backend's DATABASE_URL.
_db_url = os.environ.get("DATABASE_URL", "")
if _db_url.startswith(("postgresql://", "postgresql+psycopg2://")):
    from urllib.parse import urlparse
    _u = urlparse(_db_url.replace("postgresql+psycopg2://", "postgresql://"))
    PG_DSN = (
        f"host={_u.hostname or 'postgres'} port={_u.port or 5432} "
        f"dbname={(_u.path or '/').lstrip('/')} "
        f"user={_u.username} password={_u.password}"
    )
else:
    PG_DSN = "host=postgres user=induvista_admin dbname=induvista"


def http_get(path: str, params: dict | None = None) -> tuple[int, dict]:
    url = f"{API}/{path}"
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        try:    return e.code, json.loads(body)
        except: return e.code, {"detail": body}


def section(t: str):
    print(f"\n{t}\n" + "─" * 76)


def main() -> int:
    print(f"InduVista trend quality smoke test")
    print(f"  Tag:    {TAG_NAME}")
    print(f"  Window: last {WINDOW_HOURS}h")
    print("=" * 76)

    end = datetime.now(timezone.utc).replace(microsecond=0)
    start = end - timedelta(hours=WINDOW_HOURS)

    conn = psycopg2.connect(PG_DSN)
    cur = conn.cursor()

    # ---- 1. Find tag --------------------------------------------------------
    section("1. Resolve test tag")
    cur.execute(
        "SELECT id, device_id, register_block_id, data_type FROM tags "
        "WHERE name = %s LIMIT 1", (TAG_NAME,))
    row = cur.fetchone()
    if not row:
        print(f"  Tag '{TAG_NAME}' not found. Set SMOKE_TAG to a real tag name.")
        # Suggest one
        cur.execute("SELECT name FROM tags WHERE enabled = TRUE LIMIT 5")
        print("  Suggestions:", [r[0] for r in cur.fetchall()])
        return 1
    tag_id, device_id, reg_block_id, data_type = row
    print(f"  Tag id: {tag_id}  device_id: {device_id}  type: {data_type}")

    # ---- 2. Count actual quality distribution in tag_values ----------------
    section("2. Raw counts from tag_values (ground truth)")
    cur.execute("""
        SELECT
          count(*)                                      AS total_rows,
          count(*) FILTER (WHERE st >= 128)             AS good_rows,
          count(*) FILTER (WHERE st >= 64 AND st < 128) AS uncertain_rows,
          count(*) FILTER (WHERE st < 64)               AS bad_rows,
          count(*) FILTER (WHERE st < 64 AND value_double IS NULL)     AS bad_null_value,
          count(*) FILTER (WHERE st < 64 AND value_double IS NOT NULL) AS bad_with_value
        FROM tag_values
        WHERE tag_id = %s AND time >= %s AND time < %s
    """, (tag_id, start, end))
    total, good, uncertain, bad, bad_null, bad_with = cur.fetchone()
    print(f"  total rows:        {total:>8}")
    print(f"  good (st≥128):     {good:>8}")
    print(f"  uncertain (64-127):{uncertain:>8}")
    print(f"  bad (st<64):       {bad:>8}")
    print(f"    └─ value=NULL:   {bad_null:>8}  ← typical modbus failures")
    print(f"    └─ value=set:    {bad_with:>8}  ← bad-quality but readable")

    # ---- 3. Count bad in continuous aggregate ------------------------------
    section("3. tag_values_1m: SUM(bad_count) across buckets in window")
    cur.execute("""
        SELECT
          count(*)             AS bucket_count,
          coalesce(sum(sample_count), 0)    AS total_samples_in_buckets,
          coalesce(sum(good_count), 0)      AS good_in_buckets,
          coalesce(sum(uncertain_count), 0) AS uncertain_in_buckets,
          coalesce(sum(bad_count), 0)       AS bad_in_buckets,
          count(*) FILTER (WHERE bad_count > 0) AS buckets_with_bad
        FROM tag_values_1m
        WHERE tag_id = %s AND bucket >= %s AND bucket < %s
    """, (tag_id, start, end))
    bcount, total_b, good_b, unc_b, bad_b, buckets_with_bad = cur.fetchone()
    print(f"  bucket count:                {bcount:>8}")
    print(f"  total samples in buckets:    {total_b:>8}  (vs {total} in raw)")
    print(f"  good in buckets:             {good_b:>8}  (vs {good} in raw)")
    print(f"  uncertain in buckets:        {unc_b:>8}  (vs {uncertain} in raw)")
    print(f"  bad in buckets:              {bad_b:>8}  (vs {bad} in raw)")
    print(f"  buckets with any bad:        {buckets_with_bad:>8}")

    # ---- 4. Diagnose --------------------------------------------------------
    section("4. Diagnosis")
    delta = bad - bad_b
    if bad == 0:
        print(f"  ✓ No bad samples in window — chart correctly showing all green.")
        verdict = "no_bad_data"
    elif delta == 0:
        print(f"  ✓ CA bad_count matches raw — markers should appear correctly.")
        verdict = "ok"
    elif bad_b == 0 and bad > 0:
        print(f"  ✗ BUG: {bad} bad samples in raw, but CA bad_count is 0.")
        print(f"    CA's WHERE value_double IS NOT NULL filter is dropping all of them.")
        verdict = "bug_complete_loss"
    else:
        print(f"  ✗ BUG: CA undercounts bad by {delta} ({100*delta/bad:.1f}% missing).")
        print(f"    CA's WHERE value_double IS NOT NULL filter is dropping {bad_null} NULL-value bad rows.")
        verdict = "bug_partial_loss"

    # ---- 5. Verify against /api/trends/history -----------------------------
    section("5. /api/trends/history response shape")
    status, body = http_get("trends/history", {
        "tag_ids": str(tag_id),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "aggregation": "auto",
    })
    if status != 200:
        print(f"  ✗ API call failed: {status} {body}")
        return 1

    series = body["series"][0]
    api_agg = body["aggregation"]
    print(f"  aggregation chosen: {api_agg}")
    print(f"  raw_count:          {series['raw_count']}")
    print(f"  returned_count:     {series['returned_count']}")
    if series["points"]:
        p = series["points"][0]
        print(f"  sample point keys:  {sorted(p.keys())}")
        # Count points with bad data per chart's logic
        if api_agg == "raw":
            bad_pts = sum(1 for p in series["points"]
                          if p.get("st") is not None and p["st"] < 64)
        else:
            bad_pts = sum(1 for p in series["points"]
                          if p.get("b") is not None and p["b"] > 0)
        print(f"  points the chart will mark as bad: {bad_pts}")
        if bad_pts == 0 and bad > 0:
            print(f"  ⇒ Chart will show NO red markers despite {bad} actual bad samples.")
        elif bad_pts > 0:
            print(f"  ⇒ Chart will show {bad_pts} red marker(s).")

    cur.close()
    conn.close()

    # ---- Summary ------------------------------------------------------------
    print("\n" + "=" * 76)
    if verdict == "ok" or verdict == "no_bad_data":
        print("RESULT: Quality marker pipeline is working correctly.")
        return 0
    print("RESULT: Quality marker pipeline is BROKEN — CA needs to be fixed.")
    print()
    print("Remediation: redefine the continuous aggregates to count quality bands")
    print("regardless of value_double, not just rows where value_double IS NOT NULL.")
    print("Migration 0025 will be supplied alongside this smoke test.")
    return 2


if __name__ == "__main__":
    sys.exit(main())
