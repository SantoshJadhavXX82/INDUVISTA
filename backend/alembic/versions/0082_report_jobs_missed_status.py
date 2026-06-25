"""report_jobs: allow 'missed' status (missed-report detection)

Extends ck_report_jobs_status to permit a 'missed' run — a scheduled
occurrence that came due while the scheduler was down/behind and was too
stale to run (older than the catch-up window). The scheduler records these
so misses are visible in the run history; it does NOT generate/backfill them.

Revision ID: 0082_report_jobs_missed_status
Revises: 0081_report_trigger_run_at

NOTE: down_revision assumes the one-shot trigger migration (0081) is applied.
If you did NOT apply 0081, change down_revision below to "0080_signatures".
Verify your head with:  docker compose exec backend alembic current
"""
from alembic import op

revision = "0082_report_jobs_missed_status"
down_revision = "0081_report_trigger_run_at"
branch_labels = None
depends_on = None

_OLD = "status IN ('queued','running','succeeded','failed','partial')"
_NEW = "status IN ('queued','running','succeeded','failed','partial','missed')"


def upgrade() -> None:
    op.drop_constraint("ck_report_jobs_status", "report_jobs", type_="check")
    op.create_check_constraint("ck_report_jobs_status", "report_jobs", _NEW)


def downgrade() -> None:
    op.drop_constraint("ck_report_jobs_status", "report_jobs", type_="check")
    op.create_check_constraint("ck_report_jobs_status", "report_jobs", _OLD)
