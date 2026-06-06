"""device fault-handling policy — hold-last-good / substitute / missing

Adds a per-device acquisition fault policy. When a tag read fails, the worker
consults the owning device and decides what value (and quality) to emit:

  fault_mode = 'missing'    -> no value written; renders as Missing (default,
                               i.e. today's behaviour — never fabricates data).
  fault_mode = 'hold_last'  -> reuse the last good value, flagged Held
                               (st_reason='HOLD_LAST'). Decays to Bad once
                               max_hold_sec is exceeded if hold_mode='max_age';
                               held indefinitely until the next good read if
                               hold_mode='indefinite'.
  fault_mode = 'substitute' -> write substitute_value, flagged Substituted
                               (st_reason='SUBSTITUTED').

A substituted or held value always carries a non-good quality, so the report
layer colours it and totals can exclude it. Defaults are chosen so existing
devices behave exactly as before (missing, no fabrication).

Revision ID: 0077_device_fault_policy
Revises: 0076_users_can_audit
"""
from alembic import op
import sqlalchemy as sa


revision = "0077_device_fault_policy"
down_revision = "0076_users_can_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "devices",
        sa.Column("fault_mode", sa.String(16), nullable=False,
                  server_default=sa.text("'missing'")),
    )
    op.add_column(
        "devices",
        sa.Column("substitute_value", sa.Float(), nullable=True),
    )
    op.add_column(
        "devices",
        sa.Column("hold_mode", sa.String(16), nullable=False,
                  server_default=sa.text("'indefinite'")),
    )
    op.add_column(
        "devices",
        sa.Column("max_hold_sec", sa.Integer(), nullable=True),
    )
    op.create_check_constraint(
        "ck_devices_fault_mode",
        "devices",
        "fault_mode IN ('missing', 'hold_last', 'substitute')",
    )
    op.create_check_constraint(
        "ck_devices_hold_mode",
        "devices",
        "hold_mode IN ('indefinite', 'max_age')",
    )
    op.create_check_constraint(
        "ck_devices_max_hold_sec_positive",
        "devices",
        "max_hold_sec IS NULL OR max_hold_sec > 0",
    )


def downgrade() -> None:
    op.drop_constraint("ck_devices_max_hold_sec_positive", "devices", type_="check")
    op.drop_constraint("ck_devices_hold_mode", "devices", type_="check")
    op.drop_constraint("ck_devices_fault_mode", "devices", type_="check")
    op.drop_column("devices", "max_hold_sec")
    op.drop_column("devices", "hold_mode")
    op.drop_column("devices", "substitute_value")
    op.drop_column("devices", "fault_mode")
