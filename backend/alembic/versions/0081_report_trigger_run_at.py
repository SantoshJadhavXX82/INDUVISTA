"""report trigger run_at — one-shot (fire-once) triggers

Adds a nullable run_at TIMESTAMPTZ to report_triggers. When set, the trigger
fires exactly once at that instant (handled as top precedence in
report_schedule.due_instant) and never again. NULL preserves all existing
recurring/tag behaviour untouched. Additive + nullable: safe, no backfill,
no data loss.

Revision ID: 0081_report_trigger_run_at
Revises: 0080_signatures
"""
from alembic import op
import sqlalchemy as sa

revision = "0081_report_trigger_run_at"
down_revision = "0080_signatures"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "report_triggers",
        sa.Column("run_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("report_triggers", "run_at")
