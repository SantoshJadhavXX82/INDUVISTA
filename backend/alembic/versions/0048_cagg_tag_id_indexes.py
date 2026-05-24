"""Phase 23.5 — composite indexes on cagg views for /summary perf.

Revision ID: 0048_cagg_tag_id_indexes
Revises: 0046_app_timezone_setting
Create Date: 2026-05-23

PROBLEM
=======

The /api/trends/summary endpoint (powering the "Data quality &
availability" panel) selects a small list of tag_ids over a moderate-to-
wide time window — typically 10-50 tags out of ~300, over 1 day to 1
week. The query pattern is:

    SELECT ... FROM tag_values_1h
    WHERE tag_id = ANY(:ids) AND bucket >= :s AND bucket < :e

TimescaleDB's continuous-aggregate materialized hypertables ship with
ONE auto-created index — on the time-bucket column for chunking. There
is NO secondary index on tag_id. So PG plans this query as:

  1. Scan the cagg's bucket index to find chunks/rows in [s, e)
  2. Filter rows in those chunks where tag_id ∈ ids

For 335 tags × 168 hours (1 week of 1h-bucketed data) that's a 56,280-
row scan even when the request only needs 20 tags' worth (3,360 rows).
The waste compounds for longer windows.

SOLUTION
========

Add a composite (tag_id, bucket DESC) index on each of the three trend
caggs. With this index PG can do a direct index lookup per requested
tag_id, scanning only the rows that actually belong to selected tags.
Expected speedup on /summary: 5-15× for typical multi-tag selections.

Why bucket DESC: matches the common access pattern of "the most recent
N buckets for a tag" used by other endpoints (calendar heatmap,
diagnostics). For the summary's range scan the direction is irrelevant.

CONCURRENT BUILD — NOT AVAILABLE
================================

TimescaleDB hypertables (which all caggs are built on) do not support
CREATE INDEX CONCURRENTLY. The standard CREATE INDEX takes a brief
AccessExclusiveLock on the cagg's underlying hypertable while building.
Concretely that means:

  * tag_values_1d  (~120k rows for 1y)  : <1 second  lock
  * tag_values_1h  (~2M rows for 1y)    : 10-30 seconds lock
  * tag_values_1m  (~6M rows for 14d)   : 30-90 seconds lock

During each lock window the affected cagg cannot be refreshed by its
policy and cannot be queried. Other tables (raw tag_values, app
tables) are unaffected — this is a per-cagg lock, not a global one.

For production deployments with active operators, run during a quiet
window. For dev/staging this is non-eventful: cagg refresh policies
just catch up on the next cycle (5-min cadence for 1m / 1h, longer
for 1d).

Use IF NOT EXISTS for idempotency — re-running this migration after a
partial failure is safe.

CHAIN POSITION
==============

Chains from 0046_app_timezone_setting because OPC.1's 0047_api_keys
migration was drafted but not yet applied to disk when this perf fix
was needed. The api_keys migration, when delivered as part of
OPC.1 resume, will be renumbered (e.g. 0049_api_keys) and chained
from this one.
"""
from __future__ import annotations

from alembic import op


revision = "0048_cagg_tag_id_indexes"
down_revision = "0046_app_timezone_setting"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # tag_values_1m — 14-day retention, biggest cagg. Used for /summary
    # on windows 30min < span <= 4h.
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_tag_values_1m_tag_bucket
        ON tag_values_1m (tag_id, bucket DESC)
    """)

    # tag_values_1h — 1-year retention. Used for /summary on windows
    # 4h < span <= 5d (the most common case for daily/weekly views).
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_tag_values_1h_tag_bucket
        ON tag_values_1h (tag_id, bucket DESC)
    """)

    # tag_values_1d — permanent. Used for /summary on windows > 5d.
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_tag_values_1d_tag_bucket
        ON tag_values_1d (tag_id, bucket DESC)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_tag_values_1d_tag_bucket")
    op.execute("DROP INDEX IF EXISTS ix_tag_values_1h_tag_bucket")
    op.execute("DROP INDEX IF EXISTS ix_tag_values_1m_tag_bucket")
