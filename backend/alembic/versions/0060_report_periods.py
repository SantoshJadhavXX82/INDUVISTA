"""Make report trigger periods extensible (follow-up to 0059).

0059 left `period` as a free String(16) at the DB level (good — no enum to
migrate), but the API regex hardcoded the vocabulary to
hourly/daily/monthly/yearly/custom_cron. The period set should be open:
weekly, every-N-minutes, quarterly, per-shift, etc. should be addable without
a migration each time.

This migration:
  * Adds two generic schedule fields so common new periods need NO future
    migration:
      - day_of_week     (0=Mon .. 6=Sun)  → for 'weekly'
      - interval_minutes (>0)             → for 'every_n_minutes' style
  * Widens `period` from 16 to 32 chars for descriptive names.
  * Adds light, OPEN check constraints (ranges only — NOT a value whitelist),
    so the vocabulary stays open while bad numbers are still rejected.

The existing fields (at_minute, at_time_min, day_of_month, month_of_year) and
the cron_expr escape hatch remain. Between explicit fields and cron_expr, any
schedule is expressible. Fully reversible.
"""
from alembic import op
import sqlalchemy as sa


revision = "0060_report_periods"
down_revision = "0059_report_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Widen period for descriptive names (e.g. 'every_n_minutes', 'quarterly').
    op.alter_column(
        "report_triggers", "period",
        existing_type=sa.String(16),
        type_=sa.String(32),
        existing_nullable=True,
    )

    # Generic schedule fields (additive; nullable).
    op.add_column(
        "report_triggers",
        sa.Column("day_of_week", sa.Integer(), nullable=True),     # 0=Mon..6=Sun
    )
    op.add_column(
        "report_triggers",
        sa.Column("interval_minutes", sa.Integer(), nullable=True),  # every-N
    )

    # OPEN constraints — ranges only, no value whitelist on `period`.
    op.create_check_constraint(
        "ck_report_trigger_dow",
        "report_triggers",
        "day_of_week IS NULL OR (day_of_week >= 0 AND day_of_week <= 6)",
    )
    op.create_check_constraint(
        "ck_report_trigger_interval_pos",
        "report_triggers",
        "interval_minutes IS NULL OR interval_minutes > 0",
    )


def downgrade() -> None:
    op.drop_constraint("ck_report_trigger_interval_pos", "report_triggers", type_="check")
    op.drop_constraint("ck_report_trigger_dow", "report_triggers", type_="check")
    op.drop_column("report_triggers", "interval_minutes")
    op.drop_column("report_triggers", "day_of_week")
    op.alter_column(
        "report_triggers", "period",
        existing_type=sa.String(32),
        type_=sa.String(16),
        existing_nullable=True,
    )
