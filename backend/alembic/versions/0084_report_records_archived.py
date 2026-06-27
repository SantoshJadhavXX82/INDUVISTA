"""report_records: archived_at (Report Manager soft-hide)

Phase RM.1 — Report Manager. Adds a nullable `archived_at` timestamp to
report_records so the Report Manager screen can soft-hide a generated file
without deleting the history row or the file on disk. NULL = active (shown
by default); non-NULL = archived (hidden unless include_archived=true).

Purely additive and reversible:
  * No existing row changes meaning (all start NULL = active).
  * No existing query breaks (the column is optional everywhere).
  * A partial index supports the default list query, which filters
    `archived_at IS NULL` and orders by generated_at.

Revision ID: 0084_report_records_archived
Revises: 0083_report_job_stages
"""
import sqlalchemy as sa
from alembic import op

revision = "0084_report_records_archived"
down_revision = "0083_report_job_stages"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "report_records",
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Partial index: the Report Manager default view is "active records,
    # newest first". Indexing only the active rows keeps it small and fast
    # and leaves archived rows out of the hot path.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_report_records_active_generated "
        "ON report_records (generated_at DESC) "
        "WHERE archived_at IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_report_records_active_generated")
    op.drop_column("report_records", "archived_at")
