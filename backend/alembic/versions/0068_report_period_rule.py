"""report period rule (data window) + job period columns

Phase A1 of the Reports module roadmap: store a per-report data-window rule,
decoupled from the trigger, and record the computed window on each generated
record. Dormant until the render/scheduler integration (A2) reads it — existing
reports are unaffected (no row in report_period_rule => current behavior).

CHECK value sets MUST match app.services.report_period constants.

Revision ID: 0068_report_period_rule
Revises: 0067_rdl_fmt_override
"""
from alembic import op
import sqlalchemy as sa


revision = "0068_report_period_rule"
down_revision = "0067_rdl_fmt_override"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "report_period_rule",
        # One rule per report (PK = report_id). Absence => legacy snapshot mode.
        sa.Column("report_id", sa.BigInteger(), primary_key=True),
        sa.Column("period_type", sa.String(16), nullable=False),
        sa.Column("period_rule", sa.String(24), nullable=False,
                  server_default="previous_completed"),
        # Minutes after midnight that the day/week/month boundary starts
        # (e.g. 360 => 06:00 day boundary).
        sa.Column("boundary_offset_min", sa.Integer(), nullable=False,
                  server_default="0"),
        # Used only when period_type = 'custom'.
        sa.Column("custom_start_offset_min", sa.Integer(), nullable=True),
        sa.Column("custom_end_offset_min", sa.Integer(), nullable=True),
        sa.Column("allow_partial", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
        sa.Column("late_data_wait_sec", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("grace_sec", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("missing_period_handling", sa.String(8), nullable=False,
                  server_default="warn"),
        sa.Column("label_format", sa.String(32), nullable=False,
                  server_default="yyyy-MM-dd HH:mm"),
        sa.Column("filename_format", sa.String(32), nullable=False,
                  server_default="yyyyMMdd_HHmm"),
        sa.Column("enabled", sa.Boolean(), nullable=False,
                  server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        # Value sets mirror app.services.report_period.
        sa.CheckConstraint(
            "period_type IN "
            "('hourly','daily','weekly','monthly','shift','batch','custom')",
            name="ck_rpr_period_type",
        ),
        sa.CheckConstraint(
            "period_rule IN ('previous_completed','current','custom')",
            name="ck_rpr_period_rule",
        ),
        sa.CheckConstraint(
            "missing_period_handling IN ('warn','hold','fail')",
            name="ck_rpr_missing_handling",
        ),
    )
    op.create_foreign_key(
        "fk_report_period_rule_report", "report_period_rule",
        "report_definitions", ["report_id"], ["id"], ondelete="CASCADE",
    )

    # Record the computed data window on each generated record (audit + spec).
    op.add_column("report_records",
                  sa.Column("period_start", sa.DateTime(timezone=True), nullable=True))
    op.add_column("report_records",
                  sa.Column("period_end", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("report_records", "period_end")
    op.drop_column("report_records", "period_start")
    op.drop_constraint("fk_report_period_rule_report", "report_period_rule",
                       type_="foreignkey")
    op.drop_table("report_period_rule")
