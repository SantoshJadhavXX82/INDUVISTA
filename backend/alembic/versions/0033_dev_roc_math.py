"""Phase 14.7 - Deviation and rate-of-change evaluator math.

Adds:
  - alarm_rules.window_seconds (INTEGER NULL) - rolling window for
    deviation and rate_of_change rules. NULL means "use evaluator
    default" (60 seconds). Check constraint enforces 1..86400.
  - Flips is_evaluable=true for the two previously-inert system types
    (deviation, rate_of_change). The evaluator gets matching logic
    in this same phase (alarm_evaluator.py: evaluate_condition
    extended with the two new branches).

window_seconds is left at NULL for existing rules (all of them are
hi/lo/bool today, none of which use the window). Future rules with
deviation or rate_of_change type SHOULD set window_seconds, but the
evaluator falls back to 60s if NULL to avoid breaking partially-
configured rules.

Downgrade restores is_evaluable=false for the two types and drops
the column.
"""

from alembic import op
import sqlalchemy as sa


revision = "0033_dev_roc_math"
down_revision = "0032_boolean_alarms"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---- Add window_seconds column ----------------------------------
    op.add_column(
        "alarm_rules",
        sa.Column("window_seconds", sa.Integer(), nullable=True),
    )
    op.create_check_constraint(
        "ck_alarm_rules_window_seconds",
        "alarm_rules",
        "window_seconds IS NULL OR (window_seconds BETWEEN 1 AND 86400)",
    )

    # ---- Mark deviation + rate_of_change as evaluable ---------------
    # Evaluator code in this phase adds the matching branches in
    # alarm_evaluator.evaluate_condition. The frontend already shows
    # them in the dropdown (loaded from alarm_rule_types), so flipping
    # is_evaluable here is the last gate.
    op.execute("""
        UPDATE alarm_rule_types
        SET is_evaluable = true
        WHERE code IN ('deviation', 'rate_of_change')
    """)


def downgrade() -> None:
    op.execute("""
        UPDATE alarm_rule_types
        SET is_evaluable = false
        WHERE code IN ('deviation', 'rate_of_change')
    """)
    op.drop_constraint("ck_alarm_rules_window_seconds", "alarm_rules", type_="check")
    op.drop_column("alarm_rules", "window_seconds")
