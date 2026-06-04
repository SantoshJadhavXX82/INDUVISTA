"""report identity fields (report_code, area, equipment, owner_dept)

Phase B2. Additive, behavior-neutral metadata for the Overview/identity surface
and filename patterns. report_code is unique among non-null values (Postgres
treats NULLs as distinct, so existing rows — which get NULL — never conflict).

`status` is intentionally NOT added here; it lands in Phase B3 with the revision
lifecycle that gives it meaning. Scheduling continues to be gated by `enabled`.

Revision ID: 0070_report_identity
Revises: 0069_report_binding_columns
"""
from alembic import op
import sqlalchemy as sa


revision = "0070_report_identity"
down_revision = "0069_report_binding_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("report_definitions",
                  sa.Column("report_code", sa.String(40), nullable=True))
    op.add_column("report_definitions",
                  sa.Column("area", sa.String(64), nullable=True))
    op.add_column("report_definitions",
                  sa.Column("equipment", sa.String(64), nullable=True))
    op.add_column("report_definitions",
                  sa.Column("owner_dept", sa.String(64), nullable=True))
    op.create_unique_constraint(
        "uq_report_definitions_code", "report_definitions", ["report_code"])


def downgrade() -> None:
    op.drop_constraint("uq_report_definitions_code", "report_definitions",
                       type_="unique")
    op.drop_column("report_definitions", "owner_dept")
    op.drop_column("report_definitions", "equipment")
    op.drop_column("report_definitions", "area")
    op.drop_column("report_definitions", "report_code")
