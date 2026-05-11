"""Phase 5b: worker_device_status + worker_buffer_status

Two operational-state tables that surface "what's the worker actually
doing right now?" to the API. Both are written by modbus_supervisor and
read by /api/diagnostics/worker-status and /api/diagnostics/buffer-health.

worker_device_status
  One row per device. Updated by DeviceWorker at the end of every poll
  cycle. FK to devices(id) with ON DELETE CASCADE so deleting a device
  cleans up its status row.

worker_buffer_status
  Singleton (id always equals 1, enforced by CHECK). Updated by a
  buffer_status_loop in the supervisor every ~10 seconds. Reports the
  current backlog count and the time of the oldest stuck sample.

No changes to existing tables. Safe to downgrade — DROP TABLE in reverse.
"""
from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "0002_worker_status"
down_revision: Union[str, None] = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "worker_device_status",
        sa.Column(
            "device_id", sa.BigInteger(), nullable=False,
        ),
        sa.Column("last_cycle_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_cycle_samples_total", sa.Integer(), nullable=True),
        sa.Column("last_cycle_samples_good", sa.Integer(), nullable=True),
        sa.Column(
            "consecutive_failures", sa.Integer(),
            nullable=False, server_default="0",
        ),
        sa.Column(
            "connection_state", sa.Text(),
            nullable=False, server_default="disconnected",
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("device_id"),
        sa.ForeignKeyConstraint(
            ["device_id"], ["devices.id"], ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "connection_state IN ('connected', 'disconnected', 'reconnecting')",
            name="worker_device_status_connection_state_chk",
        ),
    )

    op.create_table(
        "worker_buffer_status",
        sa.Column("id", sa.SmallInteger(), nullable=False),
        sa.Column(
            "backlog", sa.Integer(),
            nullable=False, server_default="0",
        ),
        sa.Column("oldest_sample_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_replay_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_replay_count", sa.Integer(), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("id = 1", name="worker_buffer_status_singleton_chk"),
    )

    # Seed the singleton row so UPSERTs always have something to UPDATE on.
    op.execute(
        "INSERT INTO worker_buffer_status (id, backlog) VALUES (1, 0)"
    )


def downgrade() -> None:
    op.drop_table("worker_buffer_status")
    op.drop_table("worker_device_status")
