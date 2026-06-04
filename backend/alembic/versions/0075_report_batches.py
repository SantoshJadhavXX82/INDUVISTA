"""report_batches — batch-run records for the 'batch' period type

The reporting 'batch' period covers one production batch run. Batch runs are
recorded here. Today they are created via the manual override endpoints
(POST /report-config/batches/start | /stop) so batches can be simulated; later
a worker can insert source='tag' runs by comparing an integer tag against a
threshold. report_period.load_batch_window reads this table:
  previous_completed -> latest row with ended_at set
  current            -> the single open row (ended_at NULL), ending "now"

A partial unique index enforces at most one OPEN batch at a time.

Revision ID: 0075_report_batches
Revises: 0074_report_tag_missing_pct
"""
from alembic import op
import sqlalchemy as sa


revision = "0075_report_batches"
down_revision = "0074_report_tag_missing_pct"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "report_batches",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("batch_no", sa.String(64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source", sa.String(16), nullable=False, server_default="manual"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_check_constraint(
        "ck_report_batches_source", "report_batches",
        "source IN ('manual', 'tag')",
    )
    op.create_check_constraint(
        "ck_report_batches_end_after_start", "report_batches",
        "ended_at IS NULL OR ended_at > started_at",
    )
    op.create_index("ix_report_batches_ended_at", "report_batches",
                    [sa.text("ended_at DESC")])
    op.create_index("ix_report_batches_started_at", "report_batches",
                    [sa.text("started_at DESC")])
    # At most one open batch at a time.
    op.create_index(
        "uq_report_batches_one_open", "report_batches",
        [sa.text("(ended_at IS NULL)")], unique=True,
        postgresql_where=sa.text("ended_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_report_batches_one_open", table_name="report_batches")
    op.drop_index("ix_report_batches_started_at", table_name="report_batches")
    op.drop_index("ix_report_batches_ended_at", table_name="report_batches")
    op.drop_constraint("ck_report_batches_end_after_start", "report_batches", type_="check")
    op.drop_constraint("ck_report_batches_source", "report_batches", type_="check")
    op.drop_table("report_batches")
