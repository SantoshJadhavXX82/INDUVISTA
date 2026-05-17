"""Phase 14.9 — Boolean alarm rule types.

Adds two new system rule types to alarm_rule_types:
  - bool_true: alarm when value is non-zero (logical TRUE)
  - bool_false: alarm when value is zero (logical FALSE)

Both are is_evaluable=true — the evaluator gains matching logic in
this phase (alarm_evaluator.py: evaluate_condition extended).

Inserts use WHERE NOT EXISTS so the migration is idempotent and safe
to re-run. Ranks are computed as max(rank)+1 to avoid conflicting with
any custom rule types operators may have added between 14.6b and now.

This migration does NOT change schema — alarm_rules already accepts
any rule_type code (FK to alarm_rule_types). The work is pure data
seeding plus an entry in the evaluator's EVALUABLE_TYPES set, which
lives in code rather than migration land.

Downgrade removes the two seed rows but only if no alarm_rules
reference them (otherwise the FK ON DELETE RESTRICT will block the
delete, signalling that downgrade isn't safe).
"""

from alembic import op


revision = "0032_boolean_alarms"
down_revision = "0031_alarm_rule_types"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # bool_true — active when value != 0
    op.execute("""
        INSERT INTO alarm_rule_types
            (code, label, description, rank, is_system, is_evaluable)
        SELECT 'bool_true',
               'Boolean True',
               'Active when value is non-zero (logical TRUE). '
               'Useful for fault bits, alarm contacts, and status flags '
               'that signal a condition by going to 1. Threshold and '
               'deadband fields are ignored; the rule type IS the '
               'comparison.',
               COALESCE((SELECT MAX(rank) FROM alarm_rule_types), 0) + 1,
               true,
               true
        WHERE NOT EXISTS (
            SELECT 1 FROM alarm_rule_types WHERE code = 'bool_true'
        );
    """)

    # bool_false — active when value == 0
    op.execute("""
        INSERT INTO alarm_rule_types
            (code, label, description, rank, is_system, is_evaluable)
        SELECT 'bool_false',
               'Boolean False',
               'Active when value is zero (logical FALSE). Useful for '
               '"running confirmation absent" cases — alarm when a '
               'pump-running feedback drops, when a watchdog stops, or '
               'when a normally-on signal disappears. Threshold and '
               'deadband fields are ignored.',
               COALESCE((SELECT MAX(rank) FROM alarm_rule_types), 0) + 1,
               true,
               true
        WHERE NOT EXISTS (
            SELECT 1 FROM alarm_rule_types WHERE code = 'bool_false'
        );
    """)


def downgrade() -> None:
    # FK ON DELETE RESTRICT will block these if any alarm_rules
    # reference them. That's intentional — downgrade should fail loudly
    # if operators have started using the new types.
    op.execute("DELETE FROM alarm_rule_types WHERE code = 'bool_false'")
    op.execute("DELETE FROM alarm_rule_types WHERE code = 'bool_true'")
