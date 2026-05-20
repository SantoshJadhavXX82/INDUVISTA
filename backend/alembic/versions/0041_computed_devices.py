"""computed devices refactor

Revision ID: 0041_computed_devices
Revises: 0040_calc_arithmetic_tier_e
Create Date: 2026-05-19

Phase 17.0a - replace calc_definitions with computed_tags.

DESIGN (Option C of three considered):

A computed tag is BOTH a row in tags (with protocol='computed' on its
device) AND a row in computed_tags (definition only). They share the
same id. This keeps all 11 existing FKs to tags(id) working unchanged
for computed tags - alarms, history, latest_tag_values, tag_values
hypertable, pair_tags, write_journal, etc. all "just work".

The user creates the first Computed Device explicitly (no auto-seed).
Tags hosted on a Computed Device need a matching row in computed_tags
to actually compute anything; without it they exist as inert rows.
That's intentional - you can stage the tag config (name, data_type,
engineering_unit) before defining the calculation.

DESTRUCTIVE: This migration drops calc_definitions, calc_block_state,
and calc_execution_stats. All existing calc data is permanently lost.
Per Phase 17.0a Q1 ("recreate existing calcs"), this is intentional.
After the migration, the user creates a Computed Device, then creates
computed tags + computed_tags entries to rebuild whatever calcs they need.

DOWNGRADE: Recreates the dropped tables empty. Data is NOT restored.
"""
from alembic import op


# revision identifiers, used by Alembic
revision = "0041_computed_devices"
down_revision = "0040_calc_arithmetic_tier_e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Drop the calc_* tables.
    # ------------------------------------------------------------------
    # calc_block_state and calc_execution_stats FK to calc_definitions
    # with ON DELETE CASCADE, so dropping calc_definitions cascades them.
    # We drop them explicitly for clarity.
    op.execute("DROP TABLE IF EXISTS calc_execution_stats CASCADE")
    op.execute("DROP TABLE IF EXISTS calc_block_state CASCADE")
    op.execute("DROP TABLE IF EXISTS calc_definitions CASCADE")

    # ------------------------------------------------------------------
    # 2. Add 'computed' to devices.protocol CHECK constraint.
    # ------------------------------------------------------------------
    op.execute("ALTER TABLE devices DROP CONSTRAINT IF EXISTS ck_devices_protocol")
    op.execute("""
        ALTER TABLE devices ADD CONSTRAINT ck_devices_protocol
        CHECK (
            protocol::text = ANY (ARRAY[
                'modbus_tcp'::character varying,
                'csv'::character varying,
                'manual'::character varying,
                'opc_ua'::character varying,
                'mqtt'::character varying,
                'computed'::character varying
            ]::text[])
        )
    """)

    # ------------------------------------------------------------------
    # 3. Create computed_tags table.
    # ------------------------------------------------------------------
    # NOTE: id has NO sequence/default - it's expected to be set to an
    # existing tags.id when inserted. The FK enforces the row exists in
    # tags first. The trigger below enforces that the tag lives on a
    # protocol='computed' device.
    op.execute("""
        CREATE TABLE computed_tags (
            id                  INTEGER PRIMARY KEY,
            block_type          VARCHAR(64) NOT NULL,
            block_config        JSONB NOT NULL DEFAULT '{}'::jsonb,
            execution_rate_ms   INTEGER NOT NULL DEFAULT 1000,
            enabled             BOOLEAN NOT NULL DEFAULT TRUE,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

            CONSTRAINT computed_tags_id_fk
                FOREIGN KEY (id) REFERENCES tags(id) ON DELETE CASCADE,

            CONSTRAINT ck_computed_tags_execution_rate
                CHECK (execution_rate_ms IN
                    (100, 250, 500, 1000, 5000, 10000, 30000,
                     60000, 300000, 900000, 3600000))
        )
    """)

    # Indexes - mirror what the evaluator needs.
    op.execute("CREATE INDEX ix_computed_tags_enabled ON computed_tags (enabled)")
    op.execute("CREATE INDEX ix_computed_tags_block_type ON computed_tags (block_type)")

    # Updated_at trigger (function already exists from earlier migrations).
    op.execute("""
        CREATE TRIGGER computed_tags_touch_updated_at
        BEFORE UPDATE ON computed_tags
        FOR EACH ROW EXECUTE FUNCTION touch_updated_at()
    """)

    # ------------------------------------------------------------------
    # 4. Trigger: enforce computed_tags.id references a tag on a
    #    'computed' protocol device.
    # ------------------------------------------------------------------
    # CHECK constraints can't cross-reference tables in Postgres, so
    # we use a BEFORE INSERT/UPDATE trigger. Note: this does NOT fire
    # when the tags or devices row changes underneath - the API layer
    # must guard against "user moves a computed tag's underlying tag
    # to a non-computed device". For now: documented, not enforced at
    # DB level. The protocol of a device is admin-only state anyway.
    op.execute("""
        CREATE OR REPLACE FUNCTION computed_tags_validate_device_protocol()
        RETURNS TRIGGER AS $$
        DECLARE
            v_protocol TEXT;
            v_device_id INTEGER;
        BEGIN
            SELECT d.protocol, d.id INTO v_protocol, v_device_id
            FROM tags t
            JOIN devices d ON d.id = t.device_id
            WHERE t.id = NEW.id;

            IF v_protocol IS NULL THEN
                RAISE EXCEPTION 'computed_tags.id=% does not reference an existing tag', NEW.id
                    USING ERRCODE = 'foreign_key_violation';
            END IF;

            IF v_protocol <> 'computed' THEN
                RAISE EXCEPTION 'computed_tags.id=% references a tag on device %, which has protocol=''%''; computed tags must live on a device with protocol=''computed''',
                    NEW.id, v_device_id, v_protocol
                    USING ERRCODE = 'check_violation';
            END IF;

            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER computed_tags_validate_device
        BEFORE INSERT OR UPDATE OF id ON computed_tags
        FOR EACH ROW EXECUTE FUNCTION computed_tags_validate_device_protocol()
    """)

    # ------------------------------------------------------------------
    # 5. computed_tag_state - parallel to dropped calc_block_state.
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE computed_tag_state (
            id          INTEGER PRIMARY KEY,
            state       JSONB NOT NULL DEFAULT '{}'::jsonb,
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),

            CONSTRAINT computed_tag_state_id_fk
                FOREIGN KEY (id) REFERENCES computed_tags(id) ON DELETE CASCADE
        )
    """)

    # ------------------------------------------------------------------
    # 6. computed_tag_execution_stats - parallel to dropped table.
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE computed_tag_execution_stats (
            id                      INTEGER PRIMARY KEY,
            last_executed_at        TIMESTAMPTZ,
            last_duration_ms        DOUBLE PRECISION,
            last_status             VARCHAR(16) NOT NULL DEFAULT 'pending',
            last_error_message      TEXT,
            next_scheduled_at       TIMESTAMPTZ,
            consecutive_overruns    INTEGER NOT NULL DEFAULT 0,
            consecutive_errors      INTEGER NOT NULL DEFAULT 0,
            total_executions        BIGINT NOT NULL DEFAULT 0,
            total_overruns          BIGINT NOT NULL DEFAULT 0,
            total_errors            BIGINT NOT NULL DEFAULT 0,
            total_skips             BIGINT NOT NULL DEFAULT 0,

            CONSTRAINT computed_tag_exec_stats_id_fk
                FOREIGN KEY (id) REFERENCES computed_tags(id) ON DELETE CASCADE,

            CONSTRAINT ck_computed_tag_exec_stats_status
                CHECK (last_status IN
                    ('pending', 'ok', 'overrun', 'error', 'killed'))
        )
    """)


def downgrade() -> None:
    """Restore the old calc_* tables (empty - data is NOT recovered)."""

    # Drop the new tables and the trigger function.
    op.execute("DROP TABLE IF EXISTS computed_tag_execution_stats CASCADE")
    op.execute("DROP TABLE IF EXISTS computed_tag_state CASCADE")
    op.execute("DROP TRIGGER IF EXISTS computed_tags_validate_device ON computed_tags")
    op.execute("DROP TRIGGER IF EXISTS computed_tags_touch_updated_at ON computed_tags")
    op.execute("DROP FUNCTION IF EXISTS computed_tags_validate_device_protocol()")
    op.execute("DROP TABLE IF EXISTS computed_tags CASCADE")

    # Revert devices.protocol CHECK constraint to remove 'computed'.
    op.execute("ALTER TABLE devices DROP CONSTRAINT IF EXISTS ck_devices_protocol")
    op.execute("""
        ALTER TABLE devices ADD CONSTRAINT ck_devices_protocol
        CHECK (
            protocol::text = ANY (ARRAY[
                'modbus_tcp'::character varying,
                'csv'::character varying,
                'manual'::character varying,
                'opc_ua'::character varying,
                'mqtt'::character varying
            ]::text[])
        )
    """)

    # Recreate calc_definitions (empty).
    op.execute("""
        CREATE TABLE calc_definitions (
            id                  SERIAL PRIMARY KEY,
            tag_id              INTEGER NOT NULL,
            block_type          VARCHAR(64) NOT NULL,
            block_config        JSONB NOT NULL DEFAULT '{}'::jsonb,
            enabled             BOOLEAN NOT NULL DEFAULT TRUE,
            execution_rate_ms   INTEGER NOT NULL DEFAULT 1000,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

            CONSTRAINT calc_definitions_tag_id_fk
                FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE,

            CONSTRAINT calc_definitions_tag_id_uniq UNIQUE (tag_id),

            CONSTRAINT ck_calc_def_execution_rate
                CHECK (execution_rate_ms IN
                    (100, 250, 500, 1000, 5000, 10000, 30000,
                     60000, 300000, 900000, 3600000))
        )
    """)
    op.execute("""
        CREATE TRIGGER calc_definitions_touch_updated_at
        BEFORE UPDATE ON calc_definitions
        FOR EACH ROW EXECUTE FUNCTION touch_updated_at()
    """)

    # Recreate calc_block_state.
    op.execute("""
        CREATE TABLE calc_block_state (
            calc_def_id INTEGER PRIMARY KEY,
            state       JSONB NOT NULL DEFAULT '{}'::jsonb,
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),

            CONSTRAINT calc_block_state_def_id_fk
                FOREIGN KEY (calc_def_id) REFERENCES calc_definitions(id) ON DELETE CASCADE
        )
    """)

    # Recreate calc_execution_stats.
    op.execute("""
        CREATE TABLE calc_execution_stats (
            calc_def_id             INTEGER PRIMARY KEY,
            last_executed_at        TIMESTAMPTZ,
            last_duration_ms        DOUBLE PRECISION,
            last_status             VARCHAR(16) NOT NULL DEFAULT 'pending',
            last_error_message      TEXT,
            next_scheduled_at       TIMESTAMPTZ,
            consecutive_overruns    INTEGER NOT NULL DEFAULT 0,
            consecutive_errors      INTEGER NOT NULL DEFAULT 0,
            total_executions        BIGINT NOT NULL DEFAULT 0,
            total_overruns          BIGINT NOT NULL DEFAULT 0,
            total_errors            BIGINT NOT NULL DEFAULT 0,
            total_skips             BIGINT NOT NULL DEFAULT 0,

            CONSTRAINT calc_execution_stats_def_id_fk
                FOREIGN KEY (calc_def_id) REFERENCES calc_definitions(id) ON DELETE CASCADE,

            CONSTRAINT ck_calc_exec_stats_status
                CHECK (last_status IN
                    ('pending', 'ok', 'overrun', 'error', 'killed'))
        )
    """)
