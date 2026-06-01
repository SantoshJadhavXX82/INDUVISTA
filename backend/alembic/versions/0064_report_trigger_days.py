"""Weekly multi-day triggers: add days_of_week to report_triggers.

The original DanPac design (and 0059/0060) stored a single day_of_week (0-6).
To support "every Mon, Wed, Fri" as ONE trigger, we add a days_of_week column
holding a comma-separated set of weekday indices (e.g. "0,2,4" = Mon,Wed,Fri).

Why a string, not a PG array: keeps the raw-SQL CRUD and the engine simple, no
array-type plumbing, trivially round-trips through JSON. The single day_of_week
column is kept for backward compatibility; the engine prefers days_of_week when
present and falls back to day_of_week.

revision id <= 32 chars.
"""
from alembic import op
import sqlalchemy as sa

revision = "0064_report_trigger_days"
down_revision = "0063_report_tags"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "report_triggers",
        sa.Column("days_of_week", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("report_triggers", "days_of_week")
