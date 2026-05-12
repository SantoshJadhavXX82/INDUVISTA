"""Phase 8.5 — Modbus TCP hardening: timeouts, retries, reconnect backoff,
write journal, and response-time metrics.

Addresses pre-fiscal hardening items identified after the Phase 8.4 audit:

  1. Per-device request timeout — currently hardcoded to pymodbus default
  2. Retry-once-on-failure — currently a single read failure poisons the block
  3. Reconnect backoff — currently retries every scan interval (DoS-like)
  4. Per-block scan intervals — column exists, worker ignores it (this
     migration is no-op for that; the worker refactor in 0007 picks it up)
  5. Write journal — every write via CLI or REST must be auditable
  6. Response-time metrics — for triage beyond "is it alive?"

Tag-side changes:
  - none

Device-side changes:
  devices.request_timeout_ms INT  default 3000
  devices.retry_count SMALLINT    default 1
  devices.reconnect_initial_ms INT default 1000
  devices.reconnect_max_ms INT    default 30000

Worker observability:
  worker_device_status.last_cycle_response_ms_avg DOUBLE PRECISION NULL
  worker_device_status.last_cycle_response_ms_max DOUBLE PRECISION NULL
  worker_device_status.cumulative_response_ms_avg DOUBLE PRECISION NULL

New table:
  write_journal — id, time, tag_id, source ('cli'|'rest'), user_label,
                  function_code, address, requested_value, success,
                  error, verify_value, latency_ms
"""
from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "0007_modbus_hardening"
down_revision: Union[str, None] = "0006_named_sets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------- devices: timeout / retry / reconnect-backoff -----------------
    op.add_column("devices", sa.Column(
        "request_timeout_ms", sa.Integer(),
        nullable=False, server_default="3000",
        comment="Per-request timeout in ms. pymodbus default is ~3s; "
                "raise for slow links, lower for LAN.",
    ))
    op.add_column("devices", sa.Column(
        "retry_count", sa.SmallInteger(),
        nullable=False, server_default="1",
        comment="Number of retries on a failed block read. 0 = no retry.",
    ))
    op.add_column("devices", sa.Column(
        "reconnect_initial_ms", sa.Integer(),
        nullable=False, server_default="1000",
        comment="Initial reconnect backoff after a connection failure (ms).",
    ))
    op.add_column("devices", sa.Column(
        "reconnect_max_ms", sa.Integer(),
        nullable=False, server_default="30000",
        comment="Maximum reconnect backoff after exponential doubling (ms).",
    ))

    # CHECK constraints — sanity bounds. Caught by validation, but DB-level
    # is a safety net.
    op.create_check_constraint(
        "ck_devices_request_timeout_ms_pos",
        "devices", "request_timeout_ms > 0 AND request_timeout_ms <= 60000",
    )
    op.create_check_constraint(
        "ck_devices_retry_count_range",
        "devices", "retry_count >= 0 AND retry_count <= 10",
    )
    op.create_check_constraint(
        "ck_devices_reconnect_backoff_order",
        "devices", "reconnect_initial_ms > 0 AND reconnect_initial_ms <= reconnect_max_ms",
    )

    # ------- worker_device_status: response-time metrics -------------------
    op.add_column("worker_device_status", sa.Column(
        "last_cycle_response_ms_avg", sa.Float(),
        nullable=True,
        comment="Average block-read latency across the last polling cycle (ms).",
    ))
    op.add_column("worker_device_status", sa.Column(
        "last_cycle_response_ms_max", sa.Float(),
        nullable=True,
        comment="Slowest block-read latency in the last cycle (ms).",
    ))
    op.add_column("worker_device_status", sa.Column(
        "cumulative_response_ms_avg", sa.Float(),
        nullable=True,
        comment="Running average response time since worker restart (ms).",
    ))

    # ------- write_journal: audit trail for every write --------------------
    op.create_table(
        "write_journal",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("time", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("NOW()")),
        sa.Column("tag_id", sa.BigInteger(),
                  sa.ForeignKey("tags.id", ondelete="SET NULL"),
                  nullable=True,
                  comment="The tag being written. Nullable so deleting a tag "
                          "later doesn't lose audit history."),
        sa.Column("tag_name_snapshot", sa.String(255), nullable=False,
                  comment="Tag name at the time of the write — preserved even "
                          "if the tag is later renamed or deleted."),
        sa.Column("source", sa.String(16), nullable=False,
                  comment="'cli' = command-line writer.py, 'rest' = API call"),
        sa.Column("user_label", sa.String(128), nullable=True,
                  comment="Who initiated. CLI passes $USER; REST passes the "
                          "authenticated user (or 'anonymous' until auth lands)."),
        sa.Column("function_code", sa.SmallInteger(), nullable=False,
                  comment="5/6/15/16 — the Modbus write function code used."),
        sa.Column("address", sa.Integer(), nullable=False,
                  comment="PDU address written (matches tag.address)."),
        sa.Column("requested_value", sa.Text(), nullable=False,
                  comment="Stringified value the user asked to write."),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True,
                  comment="Error message if success=false."),
        sa.Column("verify_value", sa.Text(), nullable=True,
                  comment="Read-back value when verify=true. Null if not verified."),
        sa.Column("latency_ms", sa.Float(), nullable=True,
                  comment="Round-trip time for the write request (ms)."),
    )
    op.create_index("ix_write_journal_tag_time", "write_journal",
                    ["tag_id", "time"])
    op.create_index("ix_write_journal_time", "write_journal", ["time"])
    op.create_check_constraint(
        "ck_write_journal_source",
        "write_journal", "source IN ('cli', 'rest')",
    )
    op.create_check_constraint(
        "ck_write_journal_fc",
        "write_journal", "function_code IN (5, 6, 15, 16)",
    )


def downgrade() -> None:
    op.drop_index("ix_write_journal_time", table_name="write_journal")
    op.drop_index("ix_write_journal_tag_time", table_name="write_journal")
    op.drop_table("write_journal")

    for col in ("cumulative_response_ms_avg", "last_cycle_response_ms_max",
                "last_cycle_response_ms_avg"):
        op.drop_column("worker_device_status", col)

    op.drop_constraint("ck_devices_reconnect_backoff_order", "devices",
                       type_="check")
    op.drop_constraint("ck_devices_retry_count_range", "devices", type_="check")
    op.drop_constraint("ck_devices_request_timeout_ms_pos", "devices",
                       type_="check")
    for col in ("reconnect_max_ms", "reconnect_initial_ms",
                "retry_count", "request_timeout_ms"):
        op.drop_column("devices", col)
