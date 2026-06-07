"""device connection redundancy — simplex / redundant (primary + backup endpoint)

Adds a per-device connection mode for Modbus TCP devices:

  connection_mode = 'simplex'   -> single endpoint (host:port). Default; existing
                                   devices keep today's behaviour exactly.
  connection_mode = 'redundant' -> primary endpoint (host:port) plus a backup
                                   endpoint (redundant_host:redundant_port). The
                                   worker tries the primary first on every connect
                                   attempt and fails over to the backup when the
                                   primary is unreachable; because each fresh
                                   reconnect starts at the primary, recovery of the
                                   primary causes automatic failback.

This is connection-level failover for a single logical device, distinct from the
existing device-level duty/standby pairing. Defaults keep all current devices in
simplex mode with no redundant endpoint, so behaviour is unchanged until an
operator opts a device into redundant mode and supplies a backup host + port.

A CHECK constraint backs the API/UI rule "redundant requires both host and port",
so the invariant holds even for direct SQL edits.

Revision ID: 0078_device_connection_mode
Revises: 0077_device_fault_policy
"""
from alembic import op
import sqlalchemy as sa


revision = "0078_device_connection_mode"
down_revision = "0077_device_fault_policy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "devices",
        sa.Column("connection_mode", sa.String(16), nullable=False,
                  server_default=sa.text("'simplex'")),
    )
    op.add_column(
        "devices",
        sa.Column("redundant_host", sa.String(255), nullable=True),
    )
    op.add_column(
        "devices",
        sa.Column("redundant_port", sa.Integer(), nullable=True),
    )
    op.create_check_constraint(
        "ck_devices_connection_mode",
        "devices",
        "connection_mode IN ('simplex', 'redundant')",
    )
    # When a device is redundant, the backup endpoint must be fully specified.
    # Simplex devices may leave it NULL.
    op.create_check_constraint(
        "ck_devices_redundant_endpoint",
        "devices",
        "connection_mode <> 'redundant' "
        "OR (redundant_host IS NOT NULL AND redundant_port IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_devices_redundant_port_range",
        "devices",
        "redundant_port IS NULL OR (redundant_port BETWEEN 1 AND 65535)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_devices_redundant_port_range", "devices", type_="check")
    op.drop_constraint("ck_devices_redundant_endpoint", "devices", type_="check")
    op.drop_constraint("ck_devices_connection_mode", "devices", type_="check")
    op.drop_column("devices", "redundant_port")
    op.drop_column("devices", "redundant_host")
    op.drop_column("devices", "connection_mode")
