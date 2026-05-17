"""Phase 14.6 hotfix — drop residual CHECK on alarm_rules.severity.

Migration 0029 issued a `DROP CONSTRAINT IF EXISTS alarm_rules_severity_check`
expecting the constraint to be named with the verbose `_check` suffix.
PostgreSQL had actually named it `alarm_rules_severity_chk` (the auto-
generated short form), so 0029's drop was a silent no-op.

The result was a hybrid state:
  - alarm_severities table exists ✓
  - alarm_rules_severity_fk constraint exists ✓
  - BUT alarm_rules_severity_chk also still exists, rejecting any
    severity not in the original five-element CHECK list.

This patch is name-agnostic: it walks pg_constraint and drops any CHECK
on alarm_rules whose definition mentions the severity column. Idempotent
— running on a clean DB does nothing.

Future cleanup: 0029 should be rewritten with the same name-agnostic
approach so fresh installs don't transit through the broken state. Doing
that here would require a downgrade-upgrade dance on existing installs;
this hotfix is the lower-risk path.
"""

from alembic import op


# Alembic identifiers
revision = "0030_drop_severity_chk"
down_revision = "0029_alarm_severities"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Drop every CHECK constraint on alarm_rules that references the
    severity column. Uses a DO block so the constraint name doesn't
    need to be known up front."""
    op.execute("""
        DO $$
        DECLARE
            cons_name text;
        BEGIN
            FOR cons_name IN
                SELECT con.conname
                FROM pg_constraint con
                JOIN pg_class rel ON rel.oid = con.conrelid
                WHERE rel.relname = 'alarm_rules'
                  AND con.contype = 'c'
                  AND pg_get_constraintdef(con.oid) ILIKE '%severity%'
            LOOP
                EXECUTE 'ALTER TABLE alarm_rules DROP CONSTRAINT '
                    || quote_ident(cons_name);
            END LOOP;
        END $$;
    """)


def downgrade() -> None:
    """Restore a single CHECK constraint with the canonical short name
    that matches PostgreSQL's auto-naming."""
    op.execute("""
        ALTER TABLE alarm_rules
        ADD CONSTRAINT alarm_rules_severity_chk
        CHECK (severity IN ('critical','high','medium','low','info'))
    """)
