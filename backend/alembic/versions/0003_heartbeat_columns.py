"""Phase 7 E1 — heartbeat watch columns on tags

Two new columns let a tag be designated as a "heartbeat watch":

  is_heartbeat              BOOLEAN, default FALSE
  heartbeat_max_stale_sec   INTEGER, nullable

When is_heartbeat is true, the worker tracks the tag's value across
poll cycles. If the value stays the same for longer than
heartbeat_max_stale_sec, the worker marks subsequent samples with
ST_COMM_TIMEOUT and reason 'HEARTBEAT_FROZEN'. This catches the
classic "TCP still connected but device firmware froze" fault that
plain comm-timeout doesn't see.

Backward compatible — existing tags default to is_heartbeat=FALSE
and behave exactly as before. The worker only does extra work for
tags that opt in.
"""
from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "0003_heartbeat_columns"
down_revision: Union[str, None] = "0002_worker_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tags",
        sa.Column(
            "is_heartbeat",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "tags",
        sa.Column(
            "heartbeat_max_stale_sec",
            sa.Integer(),
            nullable=True,
        ),
    )
    # Sanity check: stale threshold must be positive when present
    op.create_check_constraint(
        "tags_heartbeat_stale_positive",
        "tags",
        "heartbeat_max_stale_sec IS NULL OR heartbeat_max_stale_sec > 0",
    )


def downgrade() -> None:
    op.drop_constraint("tags_heartbeat_stale_positive", "tags", type_="check")
    op.drop_column("tags", "heartbeat_max_stale_sec")
    op.drop_column("tags", "is_heartbeat")
