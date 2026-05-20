"""computed_tag_output_target

Revision ID: 0043_computed_tag_output
Revises: 0042_computed_internal_channel
Create Date: 2026-05-19

Phase 17.0b - dual output mode for computed tags.

Before this migration, every computed tag wrote its value to the internal
tag matching computed_tags.id (Option C anchor row). This is "internal
mode" and remains the default behavior.

After this migration, a computed tag may optionally specify output_tag_id
pointing to ANY OTHER tag (typically on a Modbus device). When set, the
calc evaluator writes results to that external tag instead of the
internal anchor row. The internal anchor row continues to exist as
metadata only and receives no values in external mode.

Schema changes:
  1. ADD COLUMN computed_tags.output_tag_id BIGINT NULL
     REFERENCES tags(id) ON DELETE SET NULL
     (If the external tag is deleted, the calc reverts to internal mode.)
  2. CHECK constraint: output_tag_id IS NULL OR output_tag_id <> id
     (Can't externalize to your own anchor tag.)
  3. Partial unique index on output_tag_id WHERE NOT NULL
     (At most one calc per external tag - no contention.)

Idempotent: safe to re-run on a partially-migrated DB.

NOT enforced at schema level:
  - "External tag's device must not be protocol='computed'"
    (no chaining computed-to-computed in v1). This is enforced in the
    API layer (computed_tags.py validation) since adding a trigger for
    a single-writer field is unnecessary complexity. If a future
    backfill bypasses the API, that script must respect this rule.
"""
from alembic import op


revision = "0043_computed_tag_output"
down_revision = "0042_computed_internal_channel"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add the column (nullable, FK with ON DELETE SET NULL)
    op.execute("""
        ALTER TABLE computed_tags
        ADD COLUMN IF NOT EXISTS output_tag_id BIGINT NULL
            REFERENCES tags(id) ON DELETE SET NULL
    """)

    # 2. CHECK: can't externalize to your own anchor tag
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'ck_computed_tags_no_self_externalize'
            ) THEN
                ALTER TABLE computed_tags
                ADD CONSTRAINT ck_computed_tags_no_self_externalize
                CHECK (output_tag_id IS NULL OR output_tag_id <> id);
            END IF;
        END $$;
    """)

    # 3. Partial unique index: one calc per external tag
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_computed_tags_output_tag_id
        ON computed_tags (output_tag_id)
        WHERE output_tag_id IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ux_computed_tags_output_tag_id")
    op.execute("""
        ALTER TABLE computed_tags
        DROP CONSTRAINT IF EXISTS ck_computed_tags_no_self_externalize
    """)
    op.execute("ALTER TABLE computed_tags DROP COLUMN IF EXISTS output_tag_id")
