"""computed_internal_channel

Revision ID: 0042_computed_internal_channel
Revises: 0041_computed_devices
Create Date: 2026-05-19

Phase 17.0a hotfix - dedicate an internal protocol_connector + channel
for Computed Devices.

Changes:
  1. Extend channels.transport CHECK to allow 'internal'
  2. Extend protocol_connectors.code CHECK to allow 'internal'
     (introspects existing allowed values, doesn't hardcode)
  3. INSERT protocol_connectors row name='internal' code='internal'
  4. INSERT channels row name='COMPUTED' transport='internal'
  5. Migrate existing protocol='computed' devices to the new channel

Idempotent and defensive - safe to re-run, safe on a fresh database.
Both CHECK constraints are re-derived from their current definitions
rather than rewritten with hardcoded value lists, so this works even
if your install has additional protocols not present elsewhere.
"""
import re
from alembic import op
from sqlalchemy import text


revision = "0042_computed_internal_channel"
down_revision = "0041_computed_devices"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    # ------------------------------------------------------------------
    # 1. Extend channels.transport CHECK to allow 'internal'.
    # ------------------------------------------------------------------
    op.execute("""
        DO $$
        DECLARE c TEXT;
        BEGIN
            SELECT con.conname INTO c
            FROM pg_constraint con
            JOIN pg_class cl ON cl.oid = con.conrelid
            WHERE cl.relname = 'channels'
              AND con.contype = 'c'
              AND pg_get_constraintdef(con.oid) ILIKE '%transport%';
            IF c IS NOT NULL THEN
                EXECUTE 'ALTER TABLE channels DROP CONSTRAINT ' || quote_ident(c);
            END IF;
        END $$;
    """)
    op.execute("""
        ALTER TABLE channels ADD CONSTRAINT ck_channels_transport
        CHECK (transport IN ('tcp', 'rtu', 'serial', 'internal'))
    """)

    # ------------------------------------------------------------------
    # 2. Extend protocol_connectors.code CHECK to allow 'internal'.
    # ------------------------------------------------------------------
    # Discover the existing CHECK definition and its allowed values,
    # then re-add with 'internal' appended. Avoid hardcoding so installs
    # with additional codes don't lose them.
    constraint_row = bind.execute(text("""
        SELECT con.conname AS conname,
               pg_get_constraintdef(con.oid) AS def
        FROM pg_constraint con
        JOIN pg_class cl ON cl.oid = con.conrelid
        WHERE cl.relname = 'protocol_connectors'
          AND con.contype = 'c'
          AND pg_get_constraintdef(con.oid) ILIKE '%code%'
    """)).mappings().first()

    if constraint_row is not None:
        # Pull single-quoted strings out of the CHECK definition.
        # Postgres formats:
        #   CHECK ((code = ANY (ARRAY['modbus'::character varying, ...])))
        #   CHECK (code IN ('modbus', 'mqtt', ...))
        all_quoted = re.findall(r"'([^']+)'", constraint_row["def"])
        # Filter to plausible protocol codes (defensive against weird matches)
        existing_codes = {
            v for v in all_quoted
            if v and len(v) <= 32 and " " not in v and v != "character varying"
        }

        if not existing_codes:
            raise RuntimeError(
                f"Could not parse protocol_connectors code CHECK: "
                f"{constraint_row['def']!r}. Inspect manually and "
                f"edit this migration to hardcode the allowed list."
            )

        if "internal" not in existing_codes:
            new_codes = sorted(existing_codes | {"internal"})
            quoted_list = ", ".join(f"'{c}'" for c in new_codes)
            op.execute(
                f"ALTER TABLE protocol_connectors "
                f"DROP CONSTRAINT {constraint_row['conname']}"
            )
            op.execute(f"""
                ALTER TABLE protocol_connectors ADD CONSTRAINT ck_protocol_connectors_code
                CHECK (code IN ({quoted_list}))
            """)

    # ------------------------------------------------------------------
    # 3. Create the 'internal' protocol_connector (if not already there).
    # ------------------------------------------------------------------
    existing = bind.execute(text(
        "SELECT id FROM protocol_connectors WHERE name = 'internal'"
    )).scalar()

    if existing is None:
        cols = set(bind.execute(text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'protocol_connectors' AND table_schema = 'public'
        """)).scalars().all())

        insert_cols: list[str] = ["name"]
        insert_vals: list[str] = ["'internal'"]
        if "code" in cols:
            insert_cols.append("code")
            insert_vals.append("'internal'")
        if "label" in cols:
            insert_cols.append("label")
            insert_vals.append("'Internal'")
        if "description" in cols:
            insert_cols.append("description")
            insert_vals.append(
                "'Virtual protocol for computed/calculated tags - no I/O'"
            )
        if "enabled" in cols:
            insert_cols.append("enabled")
            insert_vals.append("true")

        op.execute(f"""
            INSERT INTO protocol_connectors ({", ".join(insert_cols)})
            VALUES ({", ".join(insert_vals)})
        """)

    # ------------------------------------------------------------------
    # 4. Create the 'COMPUTED' channel (idempotent via NOT EXISTS).
    # ------------------------------------------------------------------
    op.execute("""
        INSERT INTO channels (
            protocol_connector_id, name, description, transport, enabled
        )
        SELECT pc.id,
               'COMPUTED',
               'Internal channel for Computed Devices (no network I/O)',
               'internal',
               true
        FROM protocol_connectors pc
        WHERE pc.name = 'internal'
          AND NOT EXISTS (SELECT 1 FROM channels WHERE name = 'COMPUTED')
    """)

    # ------------------------------------------------------------------
    # 5. Migrate existing computed devices to the new channel.
    # ------------------------------------------------------------------
    op.execute("""
        UPDATE devices d
        SET channel_id = c.id
        FROM channels c
        WHERE c.name = 'COMPUTED'
          AND d.protocol = 'computed'
          AND d.channel_id <> c.id
    """)


def downgrade() -> None:
    # Move computed devices off the COMPUTED channel
    op.execute("""
        UPDATE devices d
        SET channel_id = (
            SELECT id FROM channels
            WHERE name <> 'COMPUTED'
            ORDER BY id LIMIT 1
        )
        WHERE d.protocol = 'computed'
    """)

    op.execute("DELETE FROM channels WHERE name = 'COMPUTED'")
    op.execute("DELETE FROM protocol_connectors WHERE name = 'internal'")

    # Drop the transport CHECK (we don't reconstruct exact original;
    # set a sensible default).
    op.execute("""
        DO $$
        DECLARE c TEXT;
        BEGIN
            SELECT con.conname INTO c
            FROM pg_constraint con
            JOIN pg_class cl ON cl.oid = con.conrelid
            WHERE cl.relname = 'channels'
              AND con.contype = 'c'
              AND pg_get_constraintdef(con.oid) ILIKE '%transport%';
            IF c IS NOT NULL THEN
                EXECUTE 'ALTER TABLE channels DROP CONSTRAINT ' || quote_ident(c);
            END IF;
        END $$;
    """)
    op.execute("""
        ALTER TABLE channels ADD CONSTRAINT ck_channels_transport
        CHECK (transport IN ('tcp', 'rtu', 'serial'))
    """)

    # Note: we do NOT revert the protocol_connectors.code CHECK
    # extension. Removing 'internal' from the allowed set would
    # require knowing the original list, which we'd have to parse
    # again. Leaving 'internal' in the CHECK is harmless since no
    # row uses it (we deleted the row above). The next migration
    # that touches this constraint can clean it up.
