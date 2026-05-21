"""tag_quality_5m continuous aggregate

Revision ID: 0044_tag_quality_cagg
Revises: 0043_computed_tag_output
Create Date: 2026-05-21

Phase 19.3 — fast quality heatmap.

Creates a TimescaleDB continuous aggregate over tag_values that pre-bins
the worst (`min`) quality byte per tag per 5-minute window. The
Diagnostics quality heatmap reads from this view instead of the raw
hypertable.

IMPORTANT — autocommit_block:
  TimescaleDB's CREATE MATERIALIZED VIEW WITH (timescaledb.continuous),
  add_continuous_aggregate_policy(), and refresh_continuous_aggregate()
  all REQUIRE autocommit — they cannot run inside a transaction.
  Alembic wraps each upgrade() call in a transaction by default. We use
  op.get_context().autocommit_block() to temporarily switch the
  connection to autocommit for these statements.

  If we don't do this, refresh_continuous_aggregate() fails with:
    psycopg2.errors.ActiveSqlTransaction:
      refresh_continuous_aggregate() cannot run inside a transaction block

Backfill scope:
  We refresh only the last 14 days, not "all time". This is sufficient
  for the heatmap (whose longest window is 1 week) and keeps the upgrade
  fast even if you have months of historian data.
"""
from alembic import op


revision = "0044_tag_quality_cagg"
down_revision = "0043_computed_tag_output"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # autocommit_block: temporarily disable transactional DDL so the
    # TimescaleDB-specific statements below can run.
    with op.get_context().autocommit_block():
        # Idempotent guard if a prior partial install left a stub.
        op.execute("DROP MATERIALIZED VIEW IF EXISTS tag_quality_5m_cagg CASCADE")

        # The continuous aggregate. WITH NO DATA means we don't backfill
        # at creation; the explicit refresh below handles that with a
        # bounded time range so it stays quick.
        op.execute("""
            CREATE MATERIALIZED VIEW tag_quality_5m_cagg
            WITH (timescaledb.continuous) AS
            SELECT
                tag_id,
                time_bucket(INTERVAL '5 minutes', time) AS bucket,
                min(st)  AS min_st,
                count(*) AS sample_count
            FROM tag_values
            GROUP BY tag_id, bucket
            WITH NO DATA
        """)

        # Refresh policy:
        #   start_offset = 7 days  → never refresh data older than this
        #   end_offset   = 5 min   → never refresh the trailing 5 minutes
        #                            (those are still being written to)
        #   schedule     = 1 min   → run every minute in the background
        op.execute("""
            SELECT add_continuous_aggregate_policy('tag_quality_5m_cagg',
                start_offset      => INTERVAL '7 days',
                end_offset        => INTERVAL '5 minutes',
                schedule_interval => INTERVAL '1 minute'
            )
        """)

        # Bounded initial backfill: materialize the last 14 days only.
        # The heatmap's longest window is 1 week, so 14 days covers it
        # with margin. Larger ranges would slow this migration on systems
        # with months of historian data, and the policy will catch up
        # naturally for the older parts if anyone ever asks for them.
        op.execute("""
            CALL refresh_continuous_aggregate(
                'tag_quality_5m_cagg',
                now() - INTERVAL '14 days',
                NULL
            )
        """)

        # Read pattern: WHERE bucket >= X, ordered scan.
        op.execute("""
            CREATE INDEX IF NOT EXISTS ix_tag_quality_5m_cagg_bucket
            ON tag_quality_5m_cagg (bucket DESC, tag_id)
        """)


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP MATERIALIZED VIEW IF EXISTS tag_quality_5m_cagg CASCADE")
