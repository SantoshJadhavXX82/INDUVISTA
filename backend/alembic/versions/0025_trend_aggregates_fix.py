"""Trend module — fix continuous aggregates to count bad samples with NULL values.

Revision ID: 0025_trend_aggregates_fix
Revises: 0024_trend_aggregates
Create Date: 2026-05-15

Background
----------
Migration 0024 created the 1m / 1h / 1d continuous aggregates with the
clause `WHERE value_double IS NOT NULL`. That filter was intended to skip
text-only tag readings from the numeric aggregations, but it has an
unintended side effect: for modbus failures the worker writes a row with
`st < 64` (bad quality) and NULL value_double — there's no value to
record. The CA filters those rows out before counting, so `bad_count`
only tracks bad readings that *did* have a value.

Result: the chart's quality markers severely under-represent real
quality issues. /api/trends/summary queries tag_values directly and sees
the truth; the chart fetches from the CA and sees a heavily filtered
view. Operators see "Bad: 4680" in the summary panel but only one or
two red dots on a 7-day chart.

Fix
---
Drop and recreate the CAs without the WHERE clause. The numeric
aggregations (avg, min, max, first, last) already ignore NULL values
naturally, so removing the filter doesn't change those — only the
counts become accurate.

After this migration, manually force a backfill if older buckets need
to be recomputed:
    CALL refresh_continuous_aggregate('tag_values_1m', NULL, NULL);
    CALL refresh_continuous_aggregate('tag_values_1h', NULL, NULL);
    CALL refresh_continuous_aggregate('tag_values_1d', NULL, NULL);
"""
from alembic import op


revision = "0025_trend_aggregates_fix"
down_revision = "0024_trend_aggregates"
branch_labels = None
depends_on = None


CAGG_DEFS = [
    ("tag_values_1m", "1 minute", "2 hours",  "1 minute",  "30 seconds"),
    ("tag_values_1h", "1 hour",   "3 days",   "1 hour",    "5 minutes"),
    ("tag_values_1d", "1 day",    "60 days",  "1 day",     "1 hour"),
]


def _cagg_create_sql(view_name: str, bucket: str) -> str:
    """CA definition — NO WHERE clause this time."""
    return f"""
CREATE MATERIALIZED VIEW {view_name}
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('{bucket}', time)               AS bucket,
    tag_id,
    first(value_double, time)                   AS first_value,
    last(value_double, time)                    AS last_value,
    min(value_double)                           AS min_value,
    max(value_double)                           AS max_value,
    avg(value_double)                           AS avg_value,
    count(*)                                    AS sample_count,
    count(*) FILTER (WHERE st >= 128)           AS good_count,
    count(*) FILTER (WHERE st >= 64 AND st < 128) AS uncertain_count,
    count(*) FILTER (WHERE st < 64)             AS bad_count
FROM tag_values
GROUP BY bucket, tag_id
WITH NO DATA;
"""


def _cagg_policy_sql(view_name: str, start_off: str, end_off: str, schedule: str) -> str:
    return f"""
SELECT add_continuous_aggregate_policy('{view_name}',
    start_offset => INTERVAL '{start_off}',
    end_offset   => INTERVAL '{end_off}',
    schedule_interval => INTERVAL '{schedule}'
);
"""


def upgrade() -> None:
    # ---- 1. Drop old CAs (idempotent, in dependency order) ----------------
    with op.get_context().autocommit_block():
        # Reverse order so dependent ones don't block parent drops.
        for view_name, *_ in reversed(CAGG_DEFS):
            op.execute(f"DROP MATERIALIZED VIEW IF EXISTS {view_name} CASCADE")

    # ---- 2. Recreate CAs without the WHERE filter --------------------------
    with op.get_context().autocommit_block():
        for view_name, bucket, start_off, end_off, schedule in CAGG_DEFS:
            op.execute(_cagg_create_sql(view_name, bucket))
            op.execute(_cagg_policy_sql(view_name, start_off, end_off, schedule))

        # As in migration 0024, no manual refresh — the attached policies
        # will materialize buckets automatically on their schedule:
        #   1m CA: refreshes every 30s, covers last 2h
        #   1h CA: refreshes every 5min, covers last 3d
        #   1d CA: refreshes every 1h, covers last 60d
        #
        # If immediate backfill of older windows is needed, run from psql:
        #   CALL refresh_continuous_aggregate('tag_values_1m', NULL, NULL);
        # The NULL,NULL form materializes all available buckets.


def downgrade() -> None:
    # Recreate the 0024 versions (with the WHERE filter) so we can roll
    # back cleanly.
    with op.get_context().autocommit_block():
        for view_name, *_ in reversed(CAGG_DEFS):
            op.execute(f"DROP MATERIALIZED VIEW IF EXISTS {view_name} CASCADE")

    with op.get_context().autocommit_block():
        for view_name, bucket, start_off, end_off, schedule in CAGG_DEFS:
            old_sql = f"""
CREATE MATERIALIZED VIEW {view_name}
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('{bucket}', time)               AS bucket,
    tag_id,
    first(value_double, time)                   AS first_value,
    last(value_double, time)                    AS last_value,
    min(value_double)                           AS min_value,
    max(value_double)                           AS max_value,
    avg(value_double)                           AS avg_value,
    count(*)                                    AS sample_count,
    count(*) FILTER (WHERE st >= 128)           AS good_count,
    count(*) FILTER (WHERE st >= 64 AND st < 128) AS uncertain_count,
    count(*) FILTER (WHERE st < 64)             AS bad_count
FROM tag_values
WHERE value_double IS NOT NULL
GROUP BY bucket, tag_id
WITH NO DATA;
"""
            op.execute(old_sql)
            op.execute(_cagg_policy_sql(view_name, start_off, end_off, schedule))
