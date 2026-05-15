"""Trend module — widen CA refresh policy windows.

Revision ID: 0027_trend_aggregates_widen_policies
Revises: 0026_trend_aggregates_realtime
Create Date: 2026-05-15

Background
----------
Migrations 0024 and 0025 set up CA policies with narrow start_offsets:
    1m CA: 2 hours
    1h CA: 3 days
    1d CA: 60 days

These offsets define how far back the policy looks each schedule run.
After 0025 dropped and recreated the CAs WITH NO DATA, only buckets
WITHIN start_offset get refreshed automatically. So the 1m CA only
ever has the last 2 hours of buckets — operators querying a 6-hour
trend hit the 1h CA, which itself only goes back 3 days from its own
last refresh, and so on.

That's fine in steady state where the CAs have been running for days,
but it fails immediately after any recreation, and silently — queries
return 0 points but throw no error.

The right offset for each CA is "wide enough to cover the query range
that auto-aggregation routes to it":
    1m CA picks up:  30 min  to  4 h     → start_offset =  8 hours
    1h CA picks up:   4 h    to  7 d     → start_offset =  8 days
    1d CA picks up:   7 d    to  365 d   → start_offset =  400 days

Plus a safety margin so the policy can still keep up when the system
has been offline briefly.

Operationally
-------------
Wider start_offsets mean each policy run scans more bucket metadata.
For the workload here (a few tags, a few million rows), the cost is
negligible — refresh checks an invalidation log, not the raw data.
The actual recompute only fires for buckets that were modified since
the last run.

Backfill of historical data is NOT part of this migration — that
needs `CALL refresh_continuous_aggregate(...)` with explicit bounded
windows, which can't run inside a transaction. Run those from psql
after this migration applies. See the deploy notes alongside.
"""
from alembic import op


revision = "0027_trend_aggregates_widen_policies"
down_revision = "0026_trend_aggregates_realtime"
branch_labels = None
depends_on = None


# (view_name, start_offset, end_offset, schedule_interval)
NEW_POLICIES = [
    ("tag_values_1m", "8 hours",  "1 minute",  "1 minute"),
    ("tag_values_1h", "8 days",   "1 hour",    "10 minutes"),
    ("tag_values_1d", "400 days", "1 day",     "1 hour"),
]

# (view_name, start_offset, end_offset, schedule_interval) — the 0024/0025 values
OLD_POLICIES = [
    ("tag_values_1m", "2 hours",  "1 minute",  "30 seconds"),
    ("tag_values_1h", "3 days",   "1 hour",    "5 minutes"),
    ("tag_values_1d", "60 days",  "1 day",     "1 hour"),
]


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for view_name, start_off, end_off, schedule in NEW_POLICIES:
            # if_exists => true makes the remove idempotent — safe to re-run.
            op.execute(
                f"SELECT remove_continuous_aggregate_policy('{view_name}', if_exists => true);"
            )
            op.execute(f"""
                SELECT add_continuous_aggregate_policy('{view_name}',
                    start_offset => INTERVAL '{start_off}',
                    end_offset   => INTERVAL '{end_off}',
                    schedule_interval => INTERVAL '{schedule}'
                );
            """)


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for view_name, start_off, end_off, schedule in OLD_POLICIES:
            op.execute(
                f"SELECT remove_continuous_aggregate_policy('{view_name}', if_exists => true);"
            )
            op.execute(f"""
                SELECT add_continuous_aggregate_policy('{view_name}',
                    start_offset => INTERVAL '{start_off}',
                    end_offset   => INTERVAL '{end_off}',
                    schedule_interval => INTERVAL '{schedule}'
                );
            """)
