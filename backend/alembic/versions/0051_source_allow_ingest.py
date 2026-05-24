"""Phase OPC.1.1 — extend tag_values.source / latest_tag_values.source
to allow 'ingest' as a valid source type.

Revision ID: 0051_source_allow_ingest
Revises: 0050_api_keys
Create Date: 2026-05-24

The existing CHECK constraint locks `source` to a fixed allow-list of
protocol types (modbus, csv, manual, estimated, store_forward, opc_ua,
mqtt). Phase OPC.1's external-client ingest endpoint needs a new
source value so its rows are distinguishable from polled data — both
in dashboards (when filtering by source) and in audit trails (when
investigating data lineage).

We extend the constraint rather than dropping it: the allow-list still
gives us cheap forward-compatibility against typos and rogue clients
trying to inject arbitrary source strings.

CLIENT IDENTIFICATION
=====================

  We deliberately do NOT encode the API client name into the source
  column (e.g. "ingest:plant_a_opc"). The source column is sized at
  varchar(16) and concatenating client names would either truncate or
  blow out the column. More importantly, source is meant to be a TYPE
  not an INSTANCE — same way 'modbus' doesn't identify which modbus
  channel wrote the row.

  Per-row client lineage, if it becomes a requirement, will get its
  own `api_key_id INTEGER` column on tag_values. For now, the
  api_keys table's last_used_at / last_used_ip plus backend log lines
  ("Ingest from client X: accepted=...") give operators enough trail
  to debug "which client wrote that?" questions.
"""

from alembic import op


# revision identifiers, used by Alembic
revision = "0051_source_allow_ingest"
down_revision = "0050_api_keys"
branch_labels = None
depends_on = None


# Centralized source allow-list (single source of truth).
# Mirrors what's currently in the DB plus 'ingest' as the new entry.
ALLOWED_SOURCES = (
    "modbus",
    "csv",
    "manual",
    "estimated",
    "store_forward",
    "opc_ua",
    "mqtt",
    "ingest",            # Phase OPC.1 — external-client ingest endpoint
)


def _array_literal(values: tuple[str, ...]) -> str:
    """Render a Postgres array literal of varchars for use inside a
    CHECK clause."""
    inner = ", ".join(f"'{v}'::character varying" for v in values)
    return f"ARRAY[{inner}]::text[]"


def upgrade() -> None:
    arr = _array_literal(ALLOWED_SOURCES)

    # tag_values — the hypertable
    op.execute("ALTER TABLE tag_values DROP CONSTRAINT IF EXISTS ck_tag_values_source;")
    op.execute(f"""
        ALTER TABLE tag_values
        ADD CONSTRAINT ck_tag_values_source
        CHECK (source::text = ANY ({arr}));
    """)

    # latest_tag_values — same protocol allow-list. Guarded by IF EXISTS
    # in case some past migration named the constraint differently or
    # the column has no check at all on this deployment.
    op.execute("ALTER TABLE latest_tag_values DROP CONSTRAINT IF EXISTS ck_latest_tag_values_source;")
    op.execute(f"""
        ALTER TABLE latest_tag_values
        ADD CONSTRAINT ck_latest_tag_values_source
        CHECK (source::text = ANY ({arr}));
    """)


def downgrade() -> None:
    # Restore the pre-OPC.1 allow-list (without 'ingest').
    pre_opc1 = tuple(s for s in ALLOWED_SOURCES if s != "ingest")
    arr = _array_literal(pre_opc1)

    op.execute("ALTER TABLE tag_values DROP CONSTRAINT IF EXISTS ck_tag_values_source;")
    op.execute(f"""
        ALTER TABLE tag_values
        ADD CONSTRAINT ck_tag_values_source
        CHECK (source::text = ANY ({arr}));
    """)
    op.execute("ALTER TABLE latest_tag_values DROP CONSTRAINT IF EXISTS ck_latest_tag_values_source;")
    op.execute(f"""
        ALTER TABLE latest_tag_values
        ADD CONSTRAINT ck_latest_tag_values_source
        CHECK (source::text = ANY ({arr}));
    """)
