"""Trend module — widen CA refresh policy windows.

Revision ID: 0027_widen_ca_policies
Revises: 0026_trend_aggregates_realtime
Create Date: 2026-05-15

(Same content as the earlier 0027_trend_aggregates_widen_policies, but
the revision string fits inside alembic_version.version_num's
varchar(32). The earlier name was 36 chars and triggered a
StringDataRightTruncation on commit, leaving the DB in a half-applied
state — SQL effects landed but alembic's bookkeeping didn't.)

Background
----------
The 0024/0025 CA policies had narrow start_offsets:
    1m CA: 2 hours
    1h CA: 3 days
    1d CA: 60 days

This works in steady state where CAs have been running for days,
but it fails immediately after a recreation: only buckets within
start_offset get refreshed by the policy, so historical queries
beyond that range return empty.

Right offsets — wide enough to cover the query range routed to each:
    1m CA picks up:  30 min  to  4 h     → start_offset =  8 hours
    1h CA picks up:   4 h    to  7 d     → start_offset =  8 days
    1d CA picks up:   7 d    to  365 d   → start_offset =  400 days

Backfill of historical data is NOT part of this migration — that
needs `CALL refresh_continuous_aggregate(...)` outside a transaction.
Deploy notes alongside this file cover the bounded-window calls.
"""
from alembic import op


revision = "0027_widen_ca_policies"
down_revision = "0026_trend_aggregates_realtime"
branch_labels = None
depends_on = None


NEW_POLICIES = [
    ("tag_values_1m", "8 hours",  "1 minute",  "1 minute"),
    ("tag_values_1h", "8 days",   "1 hour",    "10 minutes"),
    ("tag_values_1d", "400 days", "1 day",     "1 hour"),
]

OLD_POLICIES = [
    ("tag_values_1m", "2 hours",  "1 minute",  "30 seconds"),
    ("tag_values_1h", "3 days",   "1 hour",    "5 minutes"),
    ("tag_values_1d", "60 days",  "1 day",     "1 hour"),
]


def upgrade() -> None:
    # The half-applied state from the previous broken migration may have
    # already applied these. remove_continuous_aggregate_policy with
    # if_exists => true is idempotent, so re-running here is safe.
    with op.get_context().autocommit_block():
        for view_name, start_off, end_off, schedule in NEW_POLICIES:
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
