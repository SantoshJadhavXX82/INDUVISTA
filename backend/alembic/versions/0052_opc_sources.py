"""Phase OPC-web.1 — add opc_sources + opc_tag_mappings.

Revision ID: 0052_opc_sources
Revises: 0051_source_allow_ingest
Create Date: 2026-05-24

WHY THESE TWO TABLES
====================

The pivot from DataHub-client-only to backend-managed OPC UA needs a
durable place to store connection details and node→tag mappings. The
existing config DOESN'T cover this: devices+register_blocks are
Modbus-shaped (function code, register address, byte order) and can't
naturally carry OPC concepts (endpoint URL, security policy, NodeId
string).

  opc_sources         — one row per OPC UA endpoint the backend
                        subscribes to. Holds connection details,
                        auth, and tuning knobs.

  opc_tag_mappings    — many rows per source. Each maps one OPC NodeId
                        on that source to one INDUVISTA tag_id.

CHANNEL + DEVICE BACKING
========================

Per the baseline schema's contract, every tag_values row carries a
device_id (NOT NULL). The existing INSERT path in app/historian.py
resolves device_id via the tag's register_block — but OPC tags don't
have a register_block (those are Modbus-specific).

We solve this by creating SYNTHETIC channels + devices for each OPC
source. When the user creates an opc_source through the API, the
endpoint auto-creates:
  - a channel  with protocol_connector = 'opc_ua', transport = 'tcp'
  - a device   with protocol = 'opc_ua', channel_id = ..., and
               request_timeout_ms / scan_interval_ms copied from
               the OPC source's tuning knobs
  - the opc_source row, FK-bound to both
Tags created for OPC nodes use the synthetic device_id. tag_values
inserts work unchanged — no DB-schema special-casing.

CASCADE BEHAVIOR
================

Deleting an opc_source cascades to:
  - its opc_tag_mappings rows (ON DELETE CASCADE)
  - the synthetic device (FK with ON DELETE CASCADE on the device side)
  - the synthetic channel (cascades from device)
  - the tags using the synthetic device (cascades from device)
  - tag_values rows for those tags (cascades from tag deletion in
    the existing schema)

That's by design: deleting an OPC source means "this data source is
gone", and the historian rows attached to it become orphans without
the source. Operators who want to retain history should disable the
source (is_enabled = FALSE) instead of deleting it.

SECURITY: PASSWORD STORAGE
==========================

For Phase OPC-web.1, the password column stores plaintext. This is
intentionally crude — TODO Phase 21 (Auth/RBAC) wraps the OPC source
endpoints in RBAC + adds proper secret encryption at rest (KMS or
sodium-style envelope encryption). For now, operators are expected
to keep the backend deployment behind a private network, exactly
like how api_keys are exposed unauthenticated.

The column is named `password` not `password_encrypted` because
it's plaintext and lying about it would mislead future readers.
The TODO is loud and visible.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0052_opc_sources"
down_revision = "0051_source_allow_ingest"
branch_labels = None
depends_on = None


# Same allow-list as devices.protocol — keeps things consistent.
VALID_SECURITY_POLICIES = (
    "'None', 'Basic128Rsa15', 'Basic256', 'Basic256Sha256', "
    "'Aes128_Sha256_RsaOaep', 'Aes256_Sha256_RsaPss'"
)


def upgrade() -> None:
    # ── opc_sources ─────────────────────────────────────────────────
    op.create_table(
        "opc_sources",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "name", sa.String(length=100), nullable=False, unique=True,
            comment="Human-readable name. Shown in dashboards + logs.",
        ),
        sa.Column(
            "description", sa.Text(), nullable=True,
        ),
        sa.Column(
            "endpoint", sa.String(length=512), nullable=False,
            comment="opc.tcp://host:port/path",
        ),
        sa.Column(
            "security_policy", sa.String(length=32), nullable=False,
            server_default="None",
        ),
        sa.Column(
            "username", sa.String(length=128), nullable=False,
            server_default="",
        ),
        sa.Column(
            "password", sa.String(length=256), nullable=False,
            server_default="",
            comment="Plaintext. TODO Phase 21 — encrypt at rest.",
        ),
        sa.Column(
            "publishing_interval_ms", sa.Integer(), nullable=False,
            server_default="1000",
            comment="How often the server publishes data changes to us.",
        ),
        sa.Column(
            "reconnect_min_sec", sa.Float(), nullable=False,
            server_default="1.0",
        ),
        sa.Column(
            "reconnect_max_sec", sa.Float(), nullable=False,
            server_default="60.0",
        ),
        sa.Column(
            "is_enabled", sa.Boolean(), nullable=False,
            server_default=sa.text("TRUE"),
            comment="When FALSE, the supervisor skips this source.",
        ),
        # FK to the synthetic channel + device this source created.
        # Deleting the source cascades to delete the device (which
        # cascades further to tags/tag_values for that device).
        sa.Column(
            "channel_id", sa.BigInteger(),
            sa.ForeignKey("channels.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "device_id", sa.BigInteger(),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            nullable=False, unique=True,
            comment="The synthetic device that backs this OPC source. "
                    "1:1 — each opc_source has its own dedicated device.",
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            f"security_policy IN ({VALID_SECURITY_POLICIES})",
            name="ck_opc_sources_security_policy",
        ),
        sa.CheckConstraint(
            "publishing_interval_ms BETWEEN 50 AND 60000",
            name="ck_opc_sources_publishing_interval",
        ),
        sa.CheckConstraint(
            "reconnect_min_sec > 0 AND reconnect_max_sec >= reconnect_min_sec",
            name="ck_opc_sources_reconnect_range",
        ),
    )
    op.create_index(
        "ix_opc_sources_is_enabled", "opc_sources", ["is_enabled"],
    )

    # ── opc_tag_mappings ────────────────────────────────────────────
    op.create_table(
        "opc_tag_mappings",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "opc_source_id", sa.BigInteger(),
            sa.ForeignKey("opc_sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "node_id", sa.String(length=512), nullable=False,
            comment="OPC UA NodeId string, e.g. 'ns=1;s=DoubleValue'",
        ),
        sa.Column(
            "tag_id", sa.BigInteger(),
            sa.ForeignKey("tags.id", ondelete="CASCADE"),
            nullable=False, unique=True,
            comment="The INDUVISTA tag this OPC node feeds. "
                    "1:1 — one tag is fed by exactly one OPC node.",
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        # A given NodeId on a given source maps to exactly one tag.
        # We don't allow the same node to feed multiple tags — if
        # someone wants two tags backed by the same value, they
        # should use a computed_tag downstream.
        sa.UniqueConstraint(
            "opc_source_id", "node_id",
            name="uq_opc_tag_mappings_source_node",
        ),
    )
    op.create_index(
        "ix_opc_tag_mappings_source", "opc_tag_mappings", ["opc_source_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_opc_tag_mappings_source", table_name="opc_tag_mappings")
    op.drop_table("opc_tag_mappings")
    op.drop_index("ix_opc_sources_is_enabled", table_name="opc_sources")
    op.drop_table("opc_sources")
