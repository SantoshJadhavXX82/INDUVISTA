"""Phase 27a — TimescaleDB compression + retention policies.

Revision ID: 0045_tsdb_compression
Revises: 0044_tag_quality_cagg
Create Date: 2026-05-23

Adds compression and retention policies to keep historian growth
bounded over time. All policies are managed by TimescaleDB's
background scheduler; no application code changes required.

POLICIES INSTALLED
==================

  Raw hypertable: tag_values
    - Compression:  chunks > 7 days   →   compressed (10-20x size reduction)
    - Retention:    chunks > 60 days  →   dropped

  Continuous aggregates:
    - tag_values_1m:  retention > 14 days   (high cardinality, low value old)
    - tag_values_1h:  retention > 1 year    (medium cardinality, useful for trends)
    - tag_values_1d:  NO retention          (low cardinality, kept forever)
    - tag_quality_5m_cagg:  NO retention    (diagnostics; small)

EXPECTED IMPACT
===============

  Storage growth bounded to roughly:
    - 60 days raw @ ~1 sample/sec/tag (compressed after 7d)
    - Plus 14 days of 1m caggs (uncompressed, tiny)
    - Plus 1 year of 1h caggs (very small)
    - Plus permanent 1d caggs (~365 rows per tag per year)

  Typical plant (500 tags, 1 Hz polling): ~5 GB raw uncompressed
  becomes ~250-500 MB compressed. Saves ~10x.

WARNINGS
========

  1. Compressed chunks reject INSERTs. The modbus_worker's
     store-and-forward buffer (SQLite-backed) can hold samples
     indefinitely while Postgres is down. If a plant is offline
     for 7+ days and then replays, the SF replay logic must
     handle the failed write gracefully (already does — failed
     samples stay in SQLite for retry).

  2. The 60-day retention WILL drop any tag_values older than
     60 days on first scheduled run. The 1d caggs preserve
     long-term aggregates; raw data older than 60 days is gone.
     If a plant needs >60 days of raw data, edit the policy
     interval before applying this migration.

  3. Downgrade decompresses chunks (potentially slow and
     storage-intensive on large tables). Use downgrade only
     in development.

VERIFICATION
============

  After migration, confirm policies are scheduled:

    SELECT job_id, application_name, schedule_interval, hypertable_name
    FROM timescaledb_information.jobs
    WHERE proc_name IN ('policy_compression', 'policy_retention')
    ORDER BY hypertable_name, proc_name;

  Wait for the first scheduled run (typically within 1 hour) or
  trigger manually:

    CALL run_job((SELECT job_id FROM timescaledb_information.jobs
                  WHERE proc_name='policy_compression'
                    AND hypertable_name='tag_values'));

  Check compression savings (post first run):

    SELECT
      pg_size_pretty(before_compression_total_bytes) AS before,
      pg_size_pretty(after_compression_total_bytes)  AS after,
      ROUND((1.0 - after_compression_total_bytes::numeric
                  / NULLIF(before_compression_total_bytes, 0)) * 100, 1)
        AS pct_saved
    FROM hypertable_compression_stats('tag_values');
"""

from alembic import op


# revision identifiers, used by Alembic
revision = "0045_tsdb_compression"
down_revision = "0044_tag_quality_cagg"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # Step 1 — Enable compression on tag_values.
    #
    # segmentby='tag_id'  : Group rows by tag within each chunk. This
    #                       lets queries that filter by tag_id read only
    #                       the relevant compressed segments, not the
    #                       whole chunk. Massive win for trend queries.
    #
    # orderby='time DESC' : Within each tag's segment, sort by time
    #                       descending. Latest values are accessed
    #                       most often (live page, last-N queries),
    #                       so they end up in the easy-to-decompress
    #                       portion of each segment.
    # ------------------------------------------------------------------
    op.execute("""
        ALTER TABLE tag_values SET (
            timescaledb.compress,
            timescaledb.compress_segmentby = 'tag_id',
            timescaledb.compress_orderby = 'time DESC'
        );
    """)

    # ------------------------------------------------------------------
    # Step 2 — Compression policy: compress chunks older than 7 days.
    #
    # 7 days is chosen because:
    #  - Live operator views (last 24-48h) stay uncompressed -> fast
    #  - Trend queries (last 7-30d) hit a mix; aggregates dominate
    #  - Store-and-forward replay typically completes within hours
    #  - Industry-standard threshold for SCADA historians
    # ------------------------------------------------------------------
    op.execute("""
        SELECT add_compression_policy(
            'tag_values',
            INTERVAL '7 days',
            if_not_exists => true
        );
    """)

    # ------------------------------------------------------------------
    # Step 3 — Retention policies.
    #
    # Each cagg is configured independently based on its value/cost
    # ratio at different lookback windows:
    #
    #   raw (tag_values):  60 days  — operator-facing detail
    #   1m cagg:           14 days  — short-term trend smoothing
    #   1h cagg:           1 year   — medium-term reports & charts
    #   1d cagg:           forever  — long-term KPIs, comparisons
    #   5m quality cagg:   forever  — diagnostics & health audit
    # ------------------------------------------------------------------
    op.execute("""
        SELECT add_retention_policy(
            'tag_values',
            INTERVAL '60 days',
            if_not_exists => true
        );
    """)
    op.execute("""
        SELECT add_retention_policy(
            'tag_values_1m',
            INTERVAL '14 days',
            if_not_exists => true
        );
    """)
    op.execute("""
        SELECT add_retention_policy(
            'tag_values_1h',
            INTERVAL '1 year',
            if_not_exists => true
        );
    """)
    # tag_values_1d:        no retention (kept forever)
    # tag_quality_5m_cagg:  no retention (kept forever; tiny)


def downgrade() -> None:
    # Remove retention policies first (cheap, no data movement).
    op.execute("""
        SELECT remove_retention_policy('tag_values_1h', if_exists => true);
    """)
    op.execute("""
        SELECT remove_retention_policy('tag_values_1m', if_exists => true);
    """)
    op.execute("""
        SELECT remove_retention_policy('tag_values', if_exists => true);
    """)

    # Remove compression policy. New chunks won't be compressed.
    op.execute("""
        SELECT remove_compression_policy('tag_values', if_exists => true);
    """)

    # NB: This intentionally does NOT decompress existing compressed
    # chunks. Decompression is slow and storage-heavy on large tables;
    # if a downgrade really needs uncompressed data, decompress
    # manually with:
    #
    #   SELECT decompress_chunk(chunk_schema || '.' || chunk_name)
    #   FROM timescaledb_information.chunks
    #   WHERE hypertable_name = 'tag_values'
    #     AND is_compressed = true;
    #
    # And only then:
    #
    #   ALTER TABLE tag_values SET (timescaledb.compress = false);
    #
    # Leaving compressed chunks as-is means new writes still work
    # (they go into new uncompressed chunks); old data stays compressed
    # but readable. The downgrade is functionally complete.
