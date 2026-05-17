"""Phase 15.1 - Calc block library foundation.

Sets up the schema for compute tags (a.k.a. calculation tags). A calc
tag is a regular `tags` row whose values are produced by the
`calc_evaluator` worker rather than the Modbus poller. Each calc tag
references one block definition (block_type + block_config JSON) in
`calc_definitions`.

Design decisions:

  - **Separate `calc_definitions` table** rather than columns on
    `tags`. The tags table is shared with modbus tags whose schema
    requires NOT NULL device_id / data_type / function_code /
    address. Keeping calc-specific config in its own table preserves
    those constraints for real modbus tags while giving calc its own
    evolution path.

  - **Virtual device sentinel.** tags.device_id is NOT NULL, so
    calc tags need a device row. The migration creates one synthetic
    device named "Calculations" with sentinel attributes (port 0,
    enabled=false) that no real Modbus gateway would use.

  - **`tag_values.source`** column distinguishes 'modbus' (the
    existing data path) from 'calc' (this phase). Defaults to
    'modbus' so existing rows keep their semantics with no
    migration cost. Dashboards / charts / alarms keep working
    unchanged because they query tag_values without source filtering.

  - **`block_config JSONB`** is the per-block parameter bag.
    Validation and shape are owned by the block registry in Python,
    not enforced at the schema level. Allows new block types to
    be added without migrations.

  - **`is_evaluable` flag** mirrors the alarm_rule_types pattern.
    When the registry has a Python class for the block_type, the
    catalog row is marked evaluable. Operators can save configs for
    upcoming block types (taxonomy) but the worker only runs
    evaluable ones.

  - **Cycle detection** lives in the API / save path (Python),
    not the DB. The DB just stores edges via JSONB.

This migration does NOT seed any calc tags. The 15.1 smoke creates
its own synthetic calc tag to verify the round-trip.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0034_calc_foundation"
down_revision = "0033_dev_roc_math"
branch_labels = None
depends_on = None


SEED_BLOCK_TYPES = [
    ("SUM_OF",       "Sum Of",        "aggregation", 1, True,
     "Sum the latest GOOD values of N input tags. Inputs are "
     "configured as a list of tag IDs in block_config.inputs."),
    ("AVG_OF",       "Average Of",    "aggregation", 2, False,
     "Arithmetic mean of input tag values. Ships in next 15.x drop."),
    ("MIN_OF",       "Minimum Of",    "aggregation", 3, False,
     "Minimum of input values. Ships in next 15.x drop."),
    ("MAX_OF",       "Maximum Of",    "aggregation", 4, False,
     "Maximum of input values. Ships in next 15.x drop."),
    ("IF_THEN_ELSE", "If/Then/Else",  "conditional", 10, False,
     "Branch on a boolean condition. Ships in 15.x conditional tier."),
]


def upgrade() -> None:
    # ---- 1. Source column on tag_values ----------------------------------
    # Distinguishes modbus-poll-produced rows from calc-evaluator-produced
    # rows. Defaults to 'modbus' so existing rows are correct without
    # backfill.
    #
    # NOTE: We do NOT add a CHECK constraint on this column. TimescaleDB
    # rejects ALTER TABLE ADD CONSTRAINT CHECK on hypertables in plain
    # form (SQLAlchemy NotSupportedError / "operation not supported").
    # Both writers (modbus_worker, calc_evaluator) hardcode their
    # source value, so the column's VARCHAR(16) NOT NULL DEFAULT 'modbus'
    # is enough. If we later need DB-level enforcement, we can revisit
    # via NOT VALID + VALIDATE CONSTRAINT or use a domain type.
    op.execute("""
        ALTER TABLE tag_values
        ADD COLUMN IF NOT EXISTS source VARCHAR(16) NOT NULL DEFAULT 'modbus'
    """)

    # ---- 2. Virtual device sentinel for calc tags ------------------------
    # Calc tags need a device_id (NOT NULL FK). Create one synthetic
    # row called "Calculations" with protocol='manual' (per
    # ck_devices_protocol, that value signals "not polled by any
    # protocol worker") and enabled=false. Reuses the first available
    # channel since devices.channel_id is NOT NULL FK to channels.
    op.execute("""
        INSERT INTO devices (channel_id, name, protocol, enabled)
        SELECT
            (SELECT id FROM channels ORDER BY id LIMIT 1),
            'Calculations',
            'manual',
            false
        WHERE NOT EXISTS (
            SELECT 1 FROM devices WHERE name = 'Calculations'
        )
    """)

    # ---- 3. calc_definitions table ---------------------------------------
    op.create_table(
        "calc_definitions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("tag_id", sa.Integer, nullable=False),
        sa.Column("block_type", sa.String(64), nullable=False),
        sa.Column("block_config", postgresql.JSONB,
                  nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("enabled", sa.Boolean, nullable=False,
                  server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
    )
    op.create_unique_constraint(
        "calc_definitions_tag_id_uniq", "calc_definitions", ["tag_id"]
    )
    op.create_foreign_key(
        "calc_definitions_tag_id_fk",
        "calc_definitions", "tags",
        ["tag_id"], ["id"],
        ondelete="CASCADE",
    )

    # ---- 4. calc_block_types catalog (mirrors alarm_rule_types) ----------
    op.create_table(
        "calc_block_types",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("label", sa.String(128), nullable=False),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("rank", sa.Integer, nullable=False),
        sa.Column("is_evaluable", sa.Boolean, nullable=False,
                  server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
    )
    op.create_unique_constraint(
        "calc_block_types_code_uniq", "calc_block_types", ["code"]
    )
    op.create_unique_constraint(
        "calc_block_types_rank_uniq", "calc_block_types", ["rank"]
    )
    op.create_check_constraint(
        "calc_block_types_code_format",
        "calc_block_types",
        "code ~ '^[A-Z][A-Z0-9_]*$'",
    )

    # Seed: SUM_OF is the one block this phase ships with code support.
    # The rest are taxonomy entries so the admin UI shows what's
    # coming next.
    op.bulk_insert(
        sa.table(
            "calc_block_types",
            sa.column("code", sa.String),
            sa.column("label", sa.String),
            sa.column("category", sa.String),
            sa.column("description", sa.Text),
            sa.column("rank", sa.Integer),
            sa.column("is_evaluable", sa.Boolean),
        ),
        [
            {"code": code, "label": label, "category": cat,
             "rank": rank, "is_evaluable": evaluable, "description": desc}
            for (code, label, cat, rank, evaluable, desc) in SEED_BLOCK_TYPES
        ],
    )

    # ---- 5. Triggers -----------------------------------------------------
    op.execute("""
        CREATE OR REPLACE FUNCTION touch_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER calc_definitions_touch_updated_at
        BEFORE UPDATE ON calc_definitions
        FOR EACH ROW EXECUTE FUNCTION touch_updated_at()
    """)
    op.execute("""
        CREATE TRIGGER calc_block_types_touch_updated_at
        BEFORE UPDATE ON calc_block_types
        FOR EACH ROW EXECUTE FUNCTION touch_updated_at()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS calc_block_types_touch_updated_at ON calc_block_types")
    op.execute("DROP TRIGGER IF EXISTS calc_definitions_touch_updated_at ON calc_definitions")
    op.drop_table("calc_block_types")
    op.drop_constraint("calc_definitions_tag_id_fk", "calc_definitions", type_="foreignkey")
    op.drop_table("calc_definitions")
    op.execute("DELETE FROM devices WHERE name = 'Calculations'")
    op.execute("ALTER TABLE tag_values DROP COLUMN IF EXISTS source")
