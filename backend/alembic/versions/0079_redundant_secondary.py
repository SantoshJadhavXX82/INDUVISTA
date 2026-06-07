"""device connection redundancy — repoint to existing secondary_* endpoint

Phase 0078 introduced redundant_host / redundant_port, which duplicated the
device backup-endpoint columns that have existed since the baseline schema
(devices.secondary_host / secondary_port / secondary_unit_id — the documented
"dual-NIC PLC" backup endpoint). This migration removes that duplication:

  * drops redundant_host / redundant_port and their constraints (added in 0078)
  * keeps connection_mode (the explicit simplex/redundant toggle, genuinely new)
  * re-points the "redundant requires a backup endpoint" CHECK at the existing
    secondary_host / secondary_port columns, which the Modbus worker now uses
    for connection-level failover (primary -> secondary, with failback).

Behaviour is unchanged for existing devices: connection_mode defaults to
'simplex' and secondary_* stay NULL until an operator opts a device in.

Revision ID: 0079_redundant_secondary
Revises: 0078_device_connection_mode
"""
from alembic import op
import sqlalchemy as sa


revision = "0079_redundant_secondary"
down_revision = "0078_device_connection_mode"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the 0078 duplicate columns + their constraints.
    op.drop_constraint("ck_devices_redundant_port_range", "devices", type_="check")
    op.drop_constraint("ck_devices_redundant_endpoint", "devices", type_="check")
    op.drop_column("devices", "redundant_port")
    op.drop_column("devices", "redundant_host")

    # Re-point "redundant requires a backup endpoint" at the existing secondary_*.
    op.create_check_constraint(
        "ck_devices_redundant_endpoint",
        "devices",
        "connection_mode <> 'redundant' "
        "OR (secondary_host IS NOT NULL AND secondary_port IS NOT NULL)",
    )
    # Range guard for the secondary port (baseline left it unconstrained at the
    # DB level; the API already enforces 1..65535).
    op.create_check_constraint(
        "ck_devices_secondary_port_range",
        "devices",
        "secondary_port IS NULL OR (secondary_port BETWEEN 1 AND 65535)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_devices_secondary_port_range", "devices", type_="check")
    op.drop_constraint("ck_devices_redundant_endpoint", "devices", type_="check")

    # Recreate the 0078 columns + constraints (return to the 0078 schema state).
    op.add_column(
        "devices",
        sa.Column("redundant_host", sa.String(255), nullable=True),
    )
    op.add_column(
        "devices",
        sa.Column("redundant_port", sa.Integer(), nullable=True),
    )
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
