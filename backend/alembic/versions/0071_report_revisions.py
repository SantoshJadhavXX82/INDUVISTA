"""report revisions (config snapshots + activation lifecycle)

Phase B3a — additive and runtime-dormant. `report_revisions` stores immutable
config snapshots and an activation lifecycle (draft -> active, prior active ->
superseded). `report_definitions.active_revision_id` points at the active one.

Render and the scheduler still read the LIVE config in B3a; making the active
revision authoritative is Phase B3b. So this migration cannot change behavior —
it only adds a table and a nullable pointer column.

Revision ID: 0071_report_revisions
Revises: 0070_report_identity
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0071_report_revisions"
down_revision = "0070_report_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "report_revisions",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("report_id", sa.BigInteger,
                  sa.ForeignKey("report_definitions.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("revision_no", sa.Integer, nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("config", postgresql.JSONB, nullable=False),
        sa.Column("validation", postgresql.JSONB, nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("created_by", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("activated_by", sa.String(64), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("report_id", "revision_no",
                            name="uq_report_revisions_report_no"),
        sa.CheckConstraint(
            "status IN ('draft','active','superseded','archived')",
            name="ck_report_revisions_status"),
    )
    op.create_index("ix_report_revisions_report", "report_revisions",
                    ["report_id", "status"])

    op.add_column("report_definitions",
                  sa.Column("active_revision_id", sa.BigInteger, nullable=True))
    op.create_foreign_key(
        "fk_report_def_active_rev", "report_definitions", "report_revisions",
        ["active_revision_id"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    op.drop_constraint("fk_report_def_active_rev", "report_definitions",
                       type_="foreignkey")
    op.drop_column("report_definitions", "active_revision_id")
    op.drop_index("ix_report_revisions_report", table_name="report_revisions")
    op.drop_table("report_revisions")
