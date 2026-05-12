"""Phase 7 E1c — cumulative rolling counters on worker_device_status

Two new columns track cumulative sample counts per device since worker
startup. BIGINT so a long-running deployment can rack up billions of
samples without overflow (uint64 would still work but BIGINT is the
PostgreSQL-native fit).

  cumulative_samples_total  BIGINT, default 0
  cumulative_samples_good   BIGINT, default 0

The worker increments these in its _report_status_sync at the end of
every poll cycle: total += last_cycle_samples_total, good += last_cycle_samples_good.

Counters reset to zero when the worker restarts — they're intentionally
process-local, not durable. If you need durable-since-day-one counters
later, build a separate aggregation rollup; don't muddle these.

Backward compatible — existing rows get 0 defaults.
"""
from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "0004_cumulative_counters"
down_revision: Union[str, None] = "0003_heartbeat_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "worker_device_status",
        sa.Column(
            "cumulative_samples_total",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "worker_device_status",
        sa.Column(
            "cumulative_samples_good",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )


def downgrade() -> None:
    op.drop_column("worker_device_status", "cumulative_samples_good")
    op.drop_column("worker_device_status", "cumulative_samples_total")
