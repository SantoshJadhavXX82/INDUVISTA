"""baseline schema (InduVista — connector hierarchy, CV/ST, redundancy, duty)

Creates the locked Phase 0 foundation:

  protocol_connectors
    └── channels                  (paired via partner_channel_id for network redundancy)
         └── devices              (paired via redundant_device_id for device redundancy
              │                    and tagged with duty_role for hot-standby)
              ├── register_blocks
              │    └── tags ─────────────── tag_group_memberships ──── groups
              │                                                       (parent_group_id
              │                                                        for hierarchy)
              ├── tag_values                (TimescaleDB hypertable)
              ├── latest_tag_values
              ├── store_forward_queue
              └── device_duty_history       (time-aware duty resolution for reports)

CV/ST STATUS MODEL
  Each value is a CV (current value: value_double or value_text) paired with
  an integer status `st` in 0-255.

    192-255 (0xC0-0xFF) → VALID_EXTENDED   usable, with sub-source detail
    128-191 (0x80-0xBF) → VALID            usable
     64-127 (0x40-0x7F) → SUSPECT          present, needs attention
      0- 63 (0x00-0x3F) → INVALID          unusable

  st_hex and st_class are STORED generated columns on the value tables, so they
  are derived from `st` at write time and physically cannot drift.

  STANDARDS NOTE: the byte and the 64/128/192 boundaries are borrowed from
  OPC DA's QQSSSSLL byte. OPC DA marks 128-191 as RESERVED; InduVista
  repurposes that band as a real "VALID" tier, so the four-class scheme is
  *not* OPC DA compliant. Never describe this as "OPC-compatible" to
  integrators — the tier names will mislead them. When OPC UA enters the
  protocol_connectors lane, a translation layer will map StatusCode severity
  → class and StatusCode subcode → st_reason.

REDUNDANCY MODEL
  Two layers compose at the worker level:

  - Network-level: channels.partner_channel_id pairs two channels. When the
    primary channel's link is wholesale failing, the worker fails polling
    over to the partner channel.
  - Device-level: devices.secondary_host/_port/_unit_id give a single device
    a backup endpoint (typical of dual-NIC PLCs). When the primary endpoint
    is unreachable, the worker tries the secondary on the same channel.

  Worker precedence (code, not schema):
    primary endpoint on primary channel
      → secondary endpoint on primary channel
      → primary endpoint on partner channel
      → secondary endpoint on partner channel
  Failback walks back in reverse, gated by per-layer failback_delay_sec.

DUTY / STANDBY
  Some redundant pairs are "hot standby" PLCs where only one is the
  authoritative source at any moment. devices.duty_role is one of
  {'duty', 'standby', 'none'}, with 'none' for devices not in a pair.
  device_duty_history records every transition. Reports must use it to
  resolve "who was duty at timestamp T" by finding the most recent switch
  ≤ T, segmenting the requested period accordingly, and pulling tag_values
  from the duty device for each segment.

WORKER CONTRACTS (not enforced by the schema, must be honored in code)
  - device_id and register_block_id are denormalized onto tag_values,
    latest_tag_values, and store_forward_queue. Writers must populate them
    from the tag's parent rows at write time. tags.device_id and
    tags.register_block_id are treated as immutable — re-home a tag by
    creating a new tag, never by updating the FKs.
  - Replay worker drains store_forward_queue → tag_values only. It must NOT
    update latest_tag_values, since buffered rows are by definition older
    than what the live path has already written for that tag.
  - source = 'store_forward' is reserved for the rare case where the queue
    has no original source to reference. Per CV/ST spec §12, replay
    normally preserves the original source ('modbus', 'csv', etc.).
  - A duty switch is one transaction: update both devices' duty_role AND
    insert one device_duty_history row. The check constraint
    ck_devices_duty_role_consistency enforces that duty_role and
    redundant_device_id stay aligned.

FUTURE PROTOCOL EXPANSION (Phase 13)
  Source enums and devices.protocol pre-widened to accept 'opc_ua' and 'mqtt'
  so the high-volume value tables won't need a check-constraint migration when
  those connectors arrive. The configuration side will get new tables added
  alongside register_blocks (e.g. opcua_subscriptions); existing Modbus tables
  stay untouched.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-05-10
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "0001_baseline"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# --- Shared SQL expressions ------------------------------------------------
#
# Defined once so every table that references them stays in sync.

ST_CLASS_EXPR = (
    "CASE "
    "WHEN st >= 192 THEN 'VALID_EXTENDED' "
    "WHEN st >= 128 THEN 'VALID' "
    "WHEN st >= 64 THEN 'SUSPECT' "
    "ELSE 'INVALID' "
    "END"
)
ST_HEX_EXPR = "lpad(to_hex(st::integer), 2, '0')"

VALID_SOURCES = (
    "('modbus', 'csv', 'manual', 'estimated', "
    "'store_forward', 'opc_ua', 'mqtt')"
)
VALID_PROTOCOLS = "('modbus_tcp', 'csv', 'manual', 'opc_ua', 'mqtt')"
VALID_TRANSPORTS = (
    "('tcp', 'tls', 'rtu', 'rtu_over_tcp', "
    "'serial', 'https', 'wss', 'ws')"
)
VALID_PROTOCOL_CODES = "('modbus', 'opc_ua', 'mqtt')"
VALID_GROUP_TYPES = (
    "('AREA', 'EQUIPMENT', 'UNIT', 'PACKAGE', 'REPORT', 'CUSTOM')"
)
VALID_DUTY_ROLES = "('duty', 'standby', 'none')"
VALID_NETWORK_PRIORITIES = "('high', 'normal', 'low')"
VALID_DUTY_REASONS = (
    "('manual', 'primary_failed', 'partner_channel_failover', "
    "'scheduled', 'failback', 'startup')"
)


def upgrade() -> None:
    # Defensive: the init script also runs this. IF NOT EXISTS makes it idempotent.
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")

    # ====================================================== protocol_connectors
    # One row per protocol family. Phase 0-2 has exactly one: 'modbus'.
    # 'opc_ua' and 'mqtt' rows arrive in Phase 13 alongside their workers.
    op.create_table(
        "protocol_connectors",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("code", sa.String(length=32), nullable=False, unique=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        # Free-form driver settings: max concurrent connections, library version,
        # connector-wide defaults, etc.
        sa.Column("settings", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(f"code IN {VALID_PROTOCOL_CODES}", name="ck_protocol_connectors_code"),
    )

    # ================================================================ channels
    # A logical transport segment under a protocol connector. For Modbus TCP
    # this is a network/LAN; for RTU it's a serial line; for OPC UA it's an
    # endpoint pool. partner_channel_id pairs two channels for network-level
    # redundancy (whole-network failover).
    op.create_table(
        "channels",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "protocol_connector_id",
            sa.Integer(),
            sa.ForeignKey("protocol_connectors.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=128), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("transport", sa.String(length=16), nullable=False, server_default="tcp"),
        sa.Column(
            "network_priority",
            sa.String(length=8),
            nullable=False,
            server_default="normal",
        ),
        # Defaults the worker uses; devices may override with their own values.
        sa.Column("default_request_timeout_ms", sa.Integer(), nullable=False, server_default="1000"),
        sa.Column("default_connect_timeout_s", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("default_retry_count", sa.Integer(), nullable=False, server_default="3"),
        # Auto-grouper hint (Phase 3.5): max contiguous unwanted registers the
        # grouper may include in order to merge two adjacent tag clusters.
        sa.Column("gap_tolerance", sa.Integer(), nullable=False, server_default="0"),
        # Network-redundancy partner. Nullable: standalone channels have no
        # partner. Operator/app maintains the symmetric A.partner=B / B.partner=A
        # invariant.
        sa.Column(
            "partner_channel_id",
            sa.Integer(),
            sa.ForeignKey("channels.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("failback_delay_sec", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(f"transport IN {VALID_TRANSPORTS}", name="ck_channels_transport"),
        sa.CheckConstraint(
            f"network_priority IN {VALID_NETWORK_PRIORITIES}",
            name="ck_channels_network_priority",
        ),
        sa.CheckConstraint(
            "partner_channel_id IS NULL OR partner_channel_id <> id",
            name="ck_channels_partner_not_self",
        ),
    )
    op.create_index("ix_channels_protocol_connector", "channels", ["protocol_connector_id"])

    # ================================================================= devices
    # A specific endpoint (PLC, RTU, flow computer, simulator). Belongs to
    # exactly one channel. Optionally paired via redundant_device_id with a
    # peer device on the partner channel for explicit hot-standby tracking,
    # in which case duty_role distinguishes the active half.
    op.create_table(
        "devices",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "channel_id",
            sa.Integer(),
            sa.ForeignKey("channels.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=128), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("protocol", sa.String(length=32), nullable=False, server_default="modbus_tcp"),
        # Primary endpoint.
        sa.Column("host", sa.String(length=255), nullable=True),
        sa.Column("port", sa.Integer(), nullable=True),
        sa.Column("unit_id", sa.Integer(), nullable=True, server_default="1"),
        # Per-device override. NULL = inherit channel default scan timing.
        sa.Column("scan_interval_ms", sa.Integer(), nullable=False, server_default="1000"),
        # Stale threshold: if no fresh good read for this many seconds, the
        # worker marks tags from this device as ST=64 (SUSPECT, reason='STALE').
        sa.Column("stale_after_sec", sa.Integer(), nullable=False, server_default="30"),
        # Device-level redundancy: secondary endpoint (typical of dual-NIC PLCs).
        # NULL fields fall back to primary at runtime.
        sa.Column("secondary_host", sa.String(length=255), nullable=True),
        sa.Column("secondary_port", sa.Integer(), nullable=True),
        sa.Column("secondary_unit_id", sa.Integer(), nullable=True),
        sa.Column("failback_delay_sec", sa.Integer(), nullable=False, server_default="60"),
        # Explicit redundant pairing: links to the peer device on the partner
        # channel. Operator/app maintains A.redundant=B / B.redundant=A symmetry.
        sa.Column(
            "redundant_device_id",
            sa.Integer(),
            sa.ForeignKey("devices.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # Duty state for hot-standby pairs. 'none' for devices not in a pair.
        sa.Column("duty_role", sa.String(length=8), nullable=False, server_default="none"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(f"protocol IN {VALID_PROTOCOLS}", name="ck_devices_protocol"),
        sa.CheckConstraint(f"duty_role IN {VALID_DUTY_ROLES}", name="ck_devices_duty_role"),
        sa.CheckConstraint(
            "redundant_device_id IS NULL OR redundant_device_id <> id",
            name="ck_devices_redundant_not_self",
        ),
        # duty_role must be 'none' for unpaired devices, 'duty'/'standby' for paired.
        sa.CheckConstraint(
            "(redundant_device_id IS NULL AND duty_role = 'none') OR "
            "(redundant_device_id IS NOT NULL AND duty_role IN ('duty', 'standby'))",
            name="ck_devices_duty_role_consistency",
        ),
    )
    op.create_index("ix_devices_channel", "devices", ["channel_id"])
    op.create_index(
        "ix_devices_redundant",
        "devices",
        ["redundant_device_id"],
        postgresql_where=sa.text("redundant_device_id IS NOT NULL"),
    )

    # ========================================================= register_blocks
    # Modbus polling unit: a contiguous register range read in one request.
    # Phase 13 will add parallel tables for OPC UA subscriptions, etc.
    op.create_table(
        "register_blocks",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "device_id",
            sa.Integer(),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("function_code", sa.SmallInteger(), nullable=False),
        sa.Column("start_address", sa.Integer(), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        # NULL = inherit device's scan_interval_ms.
        sa.Column("scan_interval_ms", sa.Integer(), nullable=True),
        # Phase offset to stagger block polls so a multi-block device doesn't
        # fire all reads at the same tick.
        sa.Column("phase_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("function_code IN (1, 2, 3, 4)", name="ck_register_blocks_function_code"),
        sa.CheckConstraint("count > 0 AND count <= 125", name="ck_register_blocks_count"),
        sa.UniqueConstraint(
            "device_id", "function_code", "start_address",
            name="uq_register_blocks_dev_fc_addr",
        ),
        sa.UniqueConstraint("device_id", "name", name="uq_register_blocks_dev_name"),
    )

    # ==================================================================== tags
    op.create_table(
        "tags",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "device_id",
            sa.Integer(),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "register_block_id",
            sa.Integer(),
            sa.ForeignKey("register_blocks.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("data_type", sa.String(length=16), nullable=False),
        # The four byte_order values cover all combinations of byte-swap and
        # word-swap: ABCD = big-endian normal, CDAB = word-swap only,
        # BADC = byte-swap only, DCBA = little-endian fully swapped.
        sa.Column("byte_order", sa.String(length=8), nullable=False, server_default="ABCD"),
        sa.Column("function_code", sa.SmallInteger(), nullable=False),
        sa.Column("address", sa.Integer(), nullable=False),
        sa.Column("register_count", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("engineering_unit", sa.String(length=32), nullable=True),
        sa.Column("scale", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("offset", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("min_value", sa.Float(), nullable=True),
        sa.Column("max_value", sa.Float(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "data_type IN ('int16', 'uint16', 'int32', 'uint32', "
            "'int64', 'uint64', 'float32', 'float64', 'bool', 'string')",
            name="ck_tags_data_type",
        ),
        sa.CheckConstraint(
            "byte_order IN ('ABCD', 'CDAB', 'BADC', 'DCBA')",
            name="ck_tags_byte_order",
        ),
        sa.CheckConstraint("function_code IN (1, 2, 3, 4)", name="ck_tags_function_code"),
        sa.UniqueConstraint("device_id", "name", name="uq_tags_device_name"),
    )
    op.create_index("ix_tags_device_enabled", "tags", ["device_id", "enabled"])
    op.create_index("ix_tags_register_block", "tags", ["register_block_id"])

    # ================================================================= groups
    # User-defined logical organization of tags. A tag belongs to exactly one
    # register_block (polling), but may belong to many groups. Groups can
    # nest via parent_group_id.
    op.create_table(
        "groups",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=128), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("group_type", sa.String(length=16), nullable=False, server_default="CUSTOM"),
        sa.Column(
            "parent_group_id",
            sa.Integer(),
            sa.ForeignKey("groups.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(f"group_type IN {VALID_GROUP_TYPES}", name="ck_groups_group_type"),
        sa.CheckConstraint(
            "parent_group_id IS NULL OR parent_group_id <> id",
            name="ck_groups_parent_not_self",
        ),
    )
    op.create_index("ix_groups_parent", "groups", ["parent_group_id"])

    # ================================================== tag_group_memberships
    # Many-to-many: each tag can be in multiple groups; each group holds many tags.
    op.create_table(
        "tag_group_memberships",
        sa.Column(
            "tag_id",
            sa.Integer(),
            sa.ForeignKey("tags.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "group_id",
            sa.Integer(),
            sa.ForeignKey("groups.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    # "What tags are in group X" is the hot read pattern.
    op.create_index("ix_tgm_group", "tag_group_memberships", ["group_id"])

    # ========================================================== tag_values
    # Time-series historian. Composite PK (tag_id, time) makes
    # ON CONFLICT DO NOTHING work for idempotent replay. device_id and
    # register_block_id denormalized for dashboard/diagnostics scopes.
    op.create_table(
        "tag_values",
        sa.Column("time", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "tag_id",
            sa.Integer(),
            sa.ForeignKey("tags.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "device_id",
            sa.Integer(),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "register_block_id",
            sa.Integer(),
            sa.ForeignKey("register_blocks.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("value_double", sa.Float(), nullable=True),
        sa.Column("value_text", sa.Text(), nullable=True),
        sa.Column("st", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column(
            "st_hex",
            sa.String(length=2),
            sa.Computed(ST_HEX_EXPR, persisted=True),
            nullable=False,
        ),
        sa.Column(
            "st_class",
            sa.String(length=16),
            sa.Computed(ST_CLASS_EXPR, persisted=True),
            nullable=False,
        ),
        sa.Column("st_reason", sa.String(length=32), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="modbus"),
        # Database insert time. With S&F replay, `time` keeps the original
        # acquisition timestamp and `created_at` reveals the replay lag.
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("tag_id", "time", name="pk_tag_values"),
        sa.CheckConstraint("st BETWEEN 0 AND 255", name="ck_tag_values_st_range"),
        sa.CheckConstraint(f"source IN {VALID_SOURCES}", name="ck_tag_values_source"),
    )
    op.execute(
        "SELECT create_hypertable('tag_values', 'time', "
        "chunk_time_interval => INTERVAL '7 days', "
        "if_not_exists => TRUE)"
    )
    op.execute("CREATE INDEX ix_tag_values_tag_time_desc ON tag_values (tag_id, time DESC)")
    op.execute("CREATE INDEX ix_tag_values_device_time_desc ON tag_values (device_id, time DESC)")
    op.execute("CREATE INDEX ix_tag_values_block_time_desc ON tag_values (register_block_id, time DESC)")

    # ===================================================== latest_tag_values
    # Powers live dashboard, tag browser, report designer, diagnostics, and
    # trend's current-value display. Updated only by the live write path —
    # the replay worker must skip this table.
    op.create_table(
        "latest_tag_values",
        sa.Column(
            "tag_id",
            sa.Integer(),
            sa.ForeignKey("tags.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "device_id",
            sa.Integer(),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "register_block_id",
            sa.Integer(),
            sa.ForeignKey("register_blocks.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("value_double", sa.Float(), nullable=True),
        sa.Column("value_text", sa.Text(), nullable=True),
        sa.Column("st", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column(
            "st_hex",
            sa.String(length=2),
            sa.Computed(ST_HEX_EXPR, persisted=True),
            nullable=False,
        ),
        sa.Column(
            "st_class",
            sa.String(length=16),
            sa.Computed(ST_CLASS_EXPR, persisted=True),
            nullable=False,
        ),
        sa.Column("st_reason", sa.String(length=32), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="modbus"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("st BETWEEN 0 AND 255", name="ck_latest_tag_values_st_range"),
        sa.CheckConstraint(f"source IN {VALID_SOURCES}", name="ck_latest_tag_values_source"),
    )
    op.create_index("ix_latest_tag_values_device", "latest_tag_values", ["device_id"])
    op.create_index("ix_latest_tag_values_block", "latest_tag_values", ["register_block_id"])
    op.create_index("ix_latest_tag_values_st_class", "latest_tag_values", ["st_class"])

    # ===================================================== store_forward_queue
    # Buffered writes used only when the historian write fails. No generated
    # st_hex/st_class here — the queue is short-lived and the replay worker
    # can compute them at insert time into tag_values.
    op.create_table(
        "store_forward_queue",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "tag_id",
            sa.Integer(),
            sa.ForeignKey("tags.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "device_id",
            sa.Integer(),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "register_block_id",
            sa.Integer(),
            sa.ForeignKey("register_blocks.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # Original acquisition time, never the replay time.
        sa.Column("timestamp_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("value_double", sa.Float(), nullable=True),
        sa.Column("value_text", sa.Text(), nullable=True),
        sa.Column("st", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("st_reason", sa.String(length=32), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="modbus"),
        # Raw protocol payload for traceability/audit.
        sa.Column("payload_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('pending', 'sent', 'failed')", name="ck_sfq_status"),
        sa.CheckConstraint(f"source IN {VALID_SOURCES}", name="ck_sfq_source"),
        sa.CheckConstraint("st BETWEEN 0 AND 255", name="ck_sfq_st_range"),
        # Deduplicates re-enqueues: same tag + same original timestamp = one row.
        sa.UniqueConstraint("tag_id", "timestamp_utc", name="uq_sfq_tag_timestamp"),
    )
    # Hot path: the replay worker scans pending rows ordered by acquisition time.
    op.create_index(
        "ix_sfq_pending_timestamp",
        "store_forward_queue",
        ["timestamp_utc"],
        postgresql_where=sa.text("status = 'pending'"),
    )

    # ===================================================== device_duty_history
    # Records every duty switch within a redundant pair. Reports use this to
    # answer "who was duty at timestamp T" by finding the most recent row
    # where switched_at <= T involving the device of interest, then segmenting
    # the requested period accordingly.
    op.create_table(
        "device_duty_history",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        # The device that BECAME duty.
        sa.Column(
            "device_id",
            sa.Integer(),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # The device that BECAME standby (the previous duty holder).
        sa.Column(
            "paired_device_id",
            sa.Integer(),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("switched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(f"reason IN {VALID_DUTY_REASONS}", name="ck_ddh_reason"),
        sa.CheckConstraint("device_id <> paired_device_id", name="ck_ddh_devices_distinct"),
    )
    # "Find duty at time T for device X": filter by either side of the pair,
    # ordered by switched_at desc.
    op.create_index("ix_ddh_device_switched", "device_duty_history", ["device_id", "switched_at"])
    op.create_index("ix_ddh_paired_switched", "device_duty_history", ["paired_device_id", "switched_at"])


def downgrade() -> None:
    op.drop_index("ix_ddh_paired_switched", table_name="device_duty_history")
    op.drop_index("ix_ddh_device_switched", table_name="device_duty_history")
    op.drop_table("device_duty_history")

    op.drop_index("ix_sfq_pending_timestamp", table_name="store_forward_queue")
    op.drop_table("store_forward_queue")

    op.drop_index("ix_latest_tag_values_st_class", table_name="latest_tag_values")
    op.drop_index("ix_latest_tag_values_block", table_name="latest_tag_values")
    op.drop_index("ix_latest_tag_values_device", table_name="latest_tag_values")
    op.drop_table("latest_tag_values")

    op.execute("DROP INDEX IF EXISTS ix_tag_values_block_time_desc")
    op.execute("DROP INDEX IF EXISTS ix_tag_values_device_time_desc")
    op.execute("DROP INDEX IF EXISTS ix_tag_values_tag_time_desc")
    op.drop_table("tag_values")

    op.drop_index("ix_tgm_group", table_name="tag_group_memberships")
    op.drop_table("tag_group_memberships")

    op.drop_index("ix_groups_parent", table_name="groups")
    op.drop_table("groups")

    op.drop_index("ix_tags_register_block", table_name="tags")
    op.drop_index("ix_tags_device_enabled", table_name="tags")
    op.drop_table("tags")

    op.drop_table("register_blocks")

    op.drop_index("ix_devices_redundant", table_name="devices")
    op.drop_index("ix_devices_channel", table_name="devices")
    op.drop_table("devices")

    op.drop_index("ix_channels_protocol_connector", table_name="channels")
    op.drop_table("channels")

    op.drop_table("protocol_connectors")
    # Intentionally do NOT drop the timescaledb extension; other schemas may use it.
