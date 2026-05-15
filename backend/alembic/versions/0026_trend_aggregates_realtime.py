"""Trend module — enable real-time aggregation on trend CAs.

Revision ID: 0026_trend_aggregates_realtime
Revises: 0025_trend_aggregates_fix
Create Date: 2026-05-15

Background
----------
Migration 0025 dropped and recreated the 1m/1h/1d continuous aggregates
WITH NO DATA. They're now empty until either:
  (a) the scheduled refresh policies fire (every 30s / 5min / 1h), and
      even then only for buckets within their start_offset window, OR
  (b) someone manually calls refresh_continuous_aggregate() on each CA.

Either way, until the materialized state catches up, chart queries
return 0 points and operators see "No data in the selected time range"
despite tag_values being full.

Fix
---
Set `timescaledb.materialized_only = false` on each CA. With this flag
off, querying a CA returns:
  - materialized rows for buckets that have been refreshed, AND
  - live-aggregated rows computed from the underlying hypertable for
    buckets that haven't been refreshed yet.

The two are unioned transparently. Operators always see current data;
the refresh policies remain useful as a performance optimization (older
buckets get materialized so query latency stays low), but their absence
no longer blanks the chart.

Performance note: live aggregation for very wide windows could be slow
on hypertables with billions of rows. For our scale (a few million per
year per tag) it's well within acceptable.
"""
from alembic import op


revision = "0026_trend_aggregates_realtime"
down_revision = "0025_trend_aggregates_fix"
branch_labels = None
depends_on = None


CAG_VIEWS = ["tag_values_1m", "tag_values_1h", "tag_values_1d"]


def upgrade() -> None:
    # ALTER for CAs must run outside an explicit transaction.
    with op.get_context().autocommit_block():
        for view in CAG_VIEWS:
            # Defensive — only act if the CA exists. (It should, since 0025
            # created it, but a partial failure of 0025 could leave us in
            # an inconsistent state.)
            op.execute(f"""
                ALTER MATERIALIZED VIEW {view}
                SET (timescaledb.materialized_only = false);
            """)


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for view in CAG_VIEWS:
            op.execute(f"""
                ALTER MATERIALIZED VIEW {view}
                SET (timescaledb.materialized_only = true);
            """)
