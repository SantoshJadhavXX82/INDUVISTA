"""Phase 23.8 — add decimal_places column to tags.

Revision ID: 0049_tag_decimal_places
Revises: 0048_cagg_tag_id_indexes
Create Date: 2026-05-24

Adds an optional `decimal_places` smallint column to the tags table for
controlling DISPLAY precision in the UI — how many digits after the
decimal point to show. Independent of storage precision (which is set
by `data_type` = float32 or float64).

NULLABLE: NULL means "auto" — UI uses its existing magnitude-based
heuristic (~3 sig figs scaled to value). Setting an explicit value
overrides the heuristic for that tag.

RANGE CHECK: 0 to 15 inclusive. 0-7 covers single-precision (float32)
meaningful digits; 0-15 covers double-precision (float64). Values
beyond data-type precision get rounded by the formatter — no harm in
storing higher numbers, but more than 7 on a float32 displays trailing
noise.

WHY SMALLINT: 1 byte sufficient (range 0-15), saves space vs INT.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0049_tag_decimal_places"
down_revision = "0048_cagg_tag_id_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tags",
        sa.Column(
            "decimal_places",
            sa.SmallInteger(),
            nullable=True,
            comment="Display precision in the UI. NULL = auto. Valid 0..15.",
        ),
    )
    # CHECK constraint — enforce 0..15 range at the DB level.
    # IF EXISTS guard so re-running this migration after a partial
    # rollback won't fail.
    op.execute("""
        ALTER TABLE tags
        ADD CONSTRAINT chk_tags_decimal_places_range
        CHECK (decimal_places IS NULL
               OR (decimal_places >= 0 AND decimal_places <= 15))
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE tags DROP CONSTRAINT IF EXISTS chk_tags_decimal_places_range")
    op.drop_column("tags", "decimal_places")
