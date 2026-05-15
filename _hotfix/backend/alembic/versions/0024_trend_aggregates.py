"""Trend module — continuous aggregates and saved views.

Phase 13.1 schema additions, fix iteration:

  - Quality bands compute from `st` (integer), NOT `st_class` (varchar).
    `st_class` holds the symbolic status label ('READ_OK', 'RANGE_WARN',
    etc.). For numeric band classification (0-63 Bad, 64-127 Uncertain,
    128+ Good), we always use the integer `st` column.

  - Idempotent cleanup at the top of upgrade() so retries after a partial
    failure (CA create failed mid-loop, trend_views already committed)
    work without manual DB surgery.

Revision ID: 0024_trend_aggregates
Revises: 0011_addressing_mode
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0024_trend_aggregates"
down_revision = "0011_addressing_mode"
branch_labels = None
depends_on = None


CAGG_DEFS = [
    ("tag_values_1m", "1 minute", "2 hours",  "1 minute",  "30 seconds"),
    ("tag_values_1h", "1 hour",   "3 days",   "1 hour",    "5 minutes"),
    ("tag_values_1d", "1 day",    "60 days",  "1 day",     "1 hour"),
]


def _cagg_create_sql(view_name: str, bucket: str) -> str:
    """CA definition — bands derive from the INTEGER `st` column.

    Note: we use `st`, not `st_class`. `st_class` is a varchar symbolic
    label ('READ_OK', 'RANGE_WARN', etc.) and won't support numeric band
    comparisons.
    """
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
WHERE value_double IS NOT NULL
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
    # ---- 0. Defensive cleanup of partial state from prior failed runs -----
    # When the autocommit_block exits the surrounding transaction, anything
    # committed before it (trend_views + its index) stays even if a later
    # statement raises. Make retries cleanly idempotent.
    with op.get_context().autocommit_block():
        op.execute("DROP MATERIALIZED VIEW IF EXISTS tag_values_1m CASCADE")
        op.execute("DROP MATERIALIZED VIEW IF EXISTS tag_values_1h CASCADE")
        op.execute("DROP MATERIALIZED VIEW IF EXISTS tag_values_1d CASCADE")
        op.execute("DROP TABLE IF EXISTS trend_views CASCADE")

    # ---- 1. trend_views table ---------------------------------------------
    op.create_table(
        "trend_views",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("owner_user_id", sa.Integer(), nullable=True),
        sa.Column("config_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index(
        "ix_trend_views_owner_name",
        "trend_views",
        ["owner_user_id", "name"],
        unique=True,
    )

    # ---- 2. Continuous aggregates (autocommit required) -------------------
    with op.get_context().autocommit_block():
        for view_name, bucket, start_off, end_off, schedule in CAGG_DEFS:
            op.execute(_cagg_create_sql(view_name, bucket))
            op.execute(_cagg_policy_sql(view_name, start_off, end_off, schedule))

        # No explicit one-time backfill — `CALL refresh_continuous_aggregate(
        # 'tag_values_1d', NOW() - INTERVAL '60 days', NOW())` triggers a
        # known overflow on pg16 + TimescaleDB when the requested window is
        # wider than what the hypertable can supply. The attached policies
        # (created above) will materialize buckets automatically on their
        # schedule:
        #   1m CA: refreshes every 30s, covers last 2h
        #   1h CA: refreshes every 5min, covers last 3d
        #   1d CA: refreshes every 1h, covers last 60d
        # For an immediate backfill of an explicit window, call the procedure
        # manually from psql with a narrow range you know contains data.


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for view_name, *_ in reversed(CAGG_DEFS):
            op.execute(f"DROP MATERIALIZED VIEW IF EXISTS {view_name} CASCADE;")

    op.drop_index("ix_trend_views_owner_name", table_name="trend_views")
    op.drop_table("trend_views")
