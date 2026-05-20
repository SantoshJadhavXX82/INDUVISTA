-- Phase 17 — clean up writable tags that the old stale sweeper wrongly
-- demoted to ST_STALE.  After this runs, the new code in
-- modbus_supervisor._run_stale_check prevents recurrence.
--
-- This sets st back to 192 (GOOD_NON_SPECIFIC) with a clarifying reason.
-- The historical tag_values row stays unchanged — only the
-- latest_tag_values view is rewritten so the UI stops showing red.
--
-- Run this once after applying the backend patches:
--   docker compose exec postgres psql -U induvista_admin -d induvista \
--     -f /tmp/fix-writable-stale.sql
-- (or paste into your DB client of choice).

BEGIN;

-- Show the rows we're about to fix
SELECT
  t.id           AS tag_id,
  t.name         AS tag_name,
  d.name         AS device_name,
  lv.st,
  lv.st_reason,
  EXTRACT(EPOCH FROM (NOW() - lv.time))::int AS age_seconds
FROM latest_tag_values lv
JOIN tags t    ON t.id = lv.tag_id
JOIN devices d ON d.id = t.device_id
WHERE t.writable = true
  AND lv.st < 128;

-- Reset writable-tag rows that were wrongly demoted
UPDATE latest_tag_values lv
SET
  st = 192,
  st_reason = 'WRITABLE_AT_REST'
FROM tags t
WHERE lv.tag_id = t.id
  AND t.writable = true
  AND lv.st < 128
  AND lv.st_reason IN ('STALE', 'stuck')
RETURNING lv.tag_id, t.name;

COMMIT;
