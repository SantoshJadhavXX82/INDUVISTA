"""Soft delete for devices.

Companion to 0053 (tags soft delete). After tags became soft-delete,
DELETE FROM devices still triggers cascade across all `tags WHERE
device_id = X`, which in turn would walk every tag's tag_values
hypertable rows. We sidestep this the same way: a device that owned
soft-deleted tags is itself soft-deleted, not hard-deleted.

The synthetic OPC + Computed devices that get "removed" with their
source become rows with deleted_at set; workers + reads filter on
deleted_at IS NULL.

Revision ID: 0054_devices_soft_delete
Revises: 0053_tags_soft_delete
Create Date: 2026-05-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0054_devices_soft_delete"
down_revision = "0053_tags_soft_delete"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "devices",
        sa.Column(
            "deleted_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
            comment="Soft-delete timestamp. NULL = active. See migration 0054.",
        ),
    )

    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_devices_active "
            "ON devices (id) WHERE deleted_at IS NULL"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_devices_deleted_at "
            "ON devices (deleted_at) WHERE deleted_at IS NOT NULL"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_devices_deleted_at")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_devices_active")
    op.drop_column("devices", "deleted_at")
