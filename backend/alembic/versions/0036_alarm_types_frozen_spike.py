"""Phase 14.10 - Promote frozen + add spike as system alarm rule types.

The 'frozen' row was seeded as is_evaluable=false (taxonomy-only) in
an earlier phase as a footgun: rules referencing it would never fire
because no Python evaluator existed. This migration ships the Python
evaluator (in app/workers/alarm_evaluator.py) and flips is_evaluable
to true. The label was also stored lowercase; capitalising for UI
consistency with the other rule types.

'spike' is new: sample-to-sample jump detection. Fires when the
absolute difference between the latest two GOOD samples exceeds
threshold. No window needed (works on consecutive samples).

Semantics:
  - frozen: alarm when (max - min) <= threshold over window_seconds
            with >= 2 GOOD samples; clears when delta exceeds
            threshold + deadband.
  - spike:  alarm when |latest - prior| > threshold; clears when
            next sample-to-sample delta is <= threshold - deadband.
"""

from alembic import op


revision = "0036_alarm_types_frozen_spike"
down_revision = "0035_calc_execution_rate"
branch_labels = None
depends_on = None


def upgrade():
    # Promote frozen to evaluable and fix the lowercase label.
    op.execute("""
        UPDATE alarm_rule_types
        SET label = 'Frozen',
            description = 'Fires when value barely changes (max - min <= threshold) '
                          'over window_seconds. Requires >= 2 GOOD samples in the window.',
            is_evaluable = true
        WHERE code = 'frozen'
    """)

    # Add spike. Rank 10 puts it immediately after frozen in catalog order.
    op.execute("""
        INSERT INTO alarm_rule_types (
            code, label, description, rank, is_system, is_evaluable
        ) VALUES (
            'spike',
            'Spike',
            'Fires when |latest_value - prior_value| exceeds threshold between two consecutive GOOD samples.',
            10,
            true,
            true
        )
        ON CONFLICT (code) DO NOTHING
    """)


def downgrade():
    # Re-demote frozen and remove spike. We don't delete rules that
    # reference these types (FK ON DELETE RESTRICT would block us
    # anyway); that's a manual cleanup if downgrade is ever needed.
    op.execute("""
        UPDATE alarm_rule_types
        SET label = 'frozen',
            is_evaluable = false
        WHERE code = 'frozen'
    """)
    op.execute("DELETE FROM alarm_rule_types WHERE code = 'spike'")
