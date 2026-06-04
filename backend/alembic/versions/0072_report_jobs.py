"""report jobs (trigger-level run log)

Phase B4a. A stateful, trigger-level record of report runs — one row per render
trigger (on-demand now; scheduled in B4b), with status, the revision used, the
formats requested, and timing. Additive: the existing per-format `report_records`
history is untouched; `report_jobs` is the higher-level run view.

report_id / revision_id use ON DELETE SET NULL so run history survives the
deletion of a report or revision (mirrors report_records).

Revision ID: 0072_report_jobs
Revises: 0071_report_revisions
"""
from alembic import op
import sqlalchemy as sa


revision = "0072_report_jobs"
down_revision = "0071_report_revisions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "report_jobs",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("report_id", sa.BigInteger,
                  sa.ForeignKey("report_definitions.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("report_name", sa.String(120), nullable=True),
        sa.Column("trigger_kind", sa.String(16), nullable=False),
        sa.Column("revision_id", sa.BigInteger,
                  sa.ForeignKey("report_revisions.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("formats", sa.String(64), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer, nullable=True),
        sa.CheckConstraint(
            "status IN ('queued','running','succeeded','failed','partial')",
            name="ck_report_jobs_status"),
        sa.CheckConstraint(
            "trigger_kind IN ('on_demand','timed','tag','manual')",
            name="ck_report_jobs_trigger_kind"),
    )
    op.create_index("ix_report_jobs_report", "report_jobs", ["report_id", "id"])
    op.create_index("ix_report_jobs_created", "report_jobs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_report_jobs_created", table_name="report_jobs")
    op.drop_index("ix_report_jobs_report", table_name="report_jobs")
    op.drop_table("report_jobs")
