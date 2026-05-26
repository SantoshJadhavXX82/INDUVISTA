"""Soft delete for tags.

Hard-deleting a tag triggers cascade DELETE through ON DELETE CASCADE on
tag_values, which is a TimescaleDB hypertable. With ~33GB across weekly
chunks under concurrent live writes from the workers, a single tag delete
holds Postgres backend sessions for hours and exhausts the SQLAlchemy pool.

This migration introduces `tags.deleted_at` (NULL = active). The DELETE
handlers UPDATE this column instead of DELETE-ing rows. Workers and read
endpoints filter `WHERE t.deleted_at IS NULL`. Historical sample data in
tag_values is preserved (industrial audit requirement).

Phase OPC-web.2.2.b — fixes the connection-pool-exhaustion bug observed
when attempting to delete KEPWARE_OPC_UA_02 from the UI.

Revision ID: 0053_tags_soft_delete
Revises: 0052_opc_sources
Create Date: 2026-05-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "0053_tags_soft_delete"
down_revision = "0052_opc_sources"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Phase 1: column. Safe inside the default transactional block —
    # ADD COLUMN with a NULL default takes only an ACCESS EXCLUSIVE lock
    # for metadata update, no table rewrite.
    op.add_column(
        "tags",
        sa.Column(
            "deleted_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
            comment="Soft-delete timestamp. NULL = active. See migration 0053.",
        ),
    )

    # Phase 2: indexes. `CREATE INDEX CONCURRENTLY` cannot run inside a
    # transaction, so we switch to autocommit for the index creation.
    # Both indexes use `IF NOT EXISTS` so a retry after partial failure
    # is safe.
    with op.get_context().autocommit_block():
        # Partial index for the hot-path filter `WHERE deleted_at IS NULL`.
        # Covers ~all rows pre-deletion; remains small after cleanup runs.
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_tags_active "
            "ON tags (id) WHERE deleted_at IS NULL"
        )
        # Partial index on deleted_at itself for cleanup/admin queries
        # like "tags deleted in the last hour". Empty pre-cleanup, grows
        # only with actual deletions.
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_tags_deleted_at "
            "ON tags (deleted_at) WHERE deleted_at IS NOT NULL"
        )


def downgrade() -> None:
    # Mirror the upgrade order in reverse: drop indexes (autocommit),
    # then drop the column (transactional).
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_tags_deleted_at")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_tags_active")
    op.drop_column("tags", "deleted_at")
