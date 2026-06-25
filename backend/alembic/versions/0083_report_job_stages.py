"""report_job_stages: per-stage generation telemetry

Records the report-generation pipeline as discrete stages
(resolve -> data -> render -> deliver), each with a status and three
integer metrics: n (count), bytes (size), ms (timing). Additive — the
scheduler writes these after each fire; nothing reads them yet besides
the diagnostics endpoint. No CHECK on status so new stage outcomes
(e.g. 'partial', 'skipped') don't need a migration.

Revision ID: 0083_report_job_stages
Revises: 0082_report_jobs_missed_status
"""
import sqlalchemy as sa
from alembic import op

revision = "0083_report_job_stages"
down_revision = "0082_report_jobs_missed_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "report_job_stages",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("job_id", sa.BigInteger,
                  sa.ForeignKey("report_jobs.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("seq", sa.Integer, nullable=False),          # stage order 1..N
        sa.Column("stage", sa.String(24), nullable=False),     # resolve|data|render|deliver
        sa.Column("status", sa.String(16), nullable=False),    # ok|error|partial|skipped
        sa.Column("n", sa.Integer, nullable=True),             # count metric
        sa.Column("bytes", sa.BigInteger, nullable=True),      # size metric
        sa.Column("ms", sa.Integer, nullable=True),            # timing metric
        sa.Column("detail", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_report_job_stages_job", "report_job_stages", ["job_id"])


def downgrade() -> None:
    op.drop_index("ix_report_job_stages_job", table_name="report_job_stages")
    op.drop_table("report_job_stages")
