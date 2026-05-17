"""Phase 14.6b — Alarm rule types master table.

Replaces the hardcoded CHECK constraint on alarm_rules.rule_type with a
proper master table operators can extend from a Setup page.

Design (mirrors 0029_alarm_severities + one extra flag):
  - Rule types identified by their `code` string. Existing alarm_rules
    rows already carry these codes, so the FK can be added with zero
    data migration.
  - Seeded with the 6 system types: 4 evaluable level-checks
    (hi_hi, hi, lo, lo_lo) plus 2 currently-inert types (deviation,
    rate_of_change) that the evaluator skips until phase 14.7 ships.
  - `is_evaluable` is a system flag, not editable via API. It signals
    to the UI whether the evaluator has real logic for this type, so
    operators don't accidentally configure rules that will never fire.
    Migrations flip it as evaluator support lands.
  - `is_system` protects the seeded 6 from deletion.
  - Uses the same name-agnostic CHECK-drop pattern as 0030 so we don't
    repeat the alarm_rules_severity_check → alarm_rules_severity_chk
    mismatch we hit yesterday.

Downgrade restores the original CHECK constraint and drops the master.
"""

from alembic import op
import sqlalchemy as sa


# Alembic identifiers
revision = "0031_alarm_rule_types"
down_revision = "0030_drop_severity_chk"
branch_labels = None
depends_on = None


# (code, label, rank, is_evaluable, description)
SEED_RULE_TYPES = [
    ("hi_hi",          "High-High",      1, True,
     "Value crosses an upper-upper threshold. Most urgent level alarm; "
     "typically triggers shutdown or operator escalation."),
    ("hi",             "High",           2, True,
     "Value crosses an upper threshold. Standard high alarm — operator "
     "attention required but not necessarily a shutdown condition."),
    ("lo",             "Low",            3, True,
     "Value drops below a lower threshold. Standard low alarm."),
    ("lo_lo",          "Low-Low",        4, True,
     "Value drops below a lower-lower threshold. Most urgent low alarm; "
     "typically triggers shutdown or operator escalation."),
    ("deviation",      "Deviation",      5, False,
     "Value deviates from a setpoint or rolling mean by more than the "
     "threshold. Detects drift even when absolute value is within hi/lo "
     "bounds. Evaluator support arrives in phase 14.7."),
    ("rate_of_change", "Rate of Change", 6, False,
     "Value changes faster than the threshold (units per second / minute). "
     "Detects runaway conditions before absolute limits are breached. "
     "Evaluator support arrives in phase 14.7."),
]


def upgrade() -> None:
    # ---- touch_updated_at() should already exist from 0029 ---------------
    # CREATE OR REPLACE so this migration is self-sufficient even when
    # someone replays it on a fresh-but-broken DB.

    op.execute("""
        CREATE OR REPLACE FUNCTION touch_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)

    # ---- alarm_rule_types table ------------------------------------------

    op.create_table(
        "alarm_rule_types",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.String(50), nullable=False),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("rank", sa.Integer, nullable=False),
        sa.Column("is_system", sa.Boolean, nullable=False,
                  server_default=sa.text("false")),
        sa.Column("is_evaluable", sa.Boolean, nullable=False,
                  server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
    )

    op.create_unique_constraint(
        "alarm_rule_types_code_uniq", "alarm_rule_types", ["code"]
    )
    op.create_unique_constraint(
        "alarm_rule_types_rank_uniq", "alarm_rule_types", ["rank"]
    )

    # Same code regex we use for severities
    op.create_check_constraint(
        "alarm_rule_types_code_format",
        "alarm_rule_types",
        "code ~ '^[a-z][a-z0-9_]*$'",
    )

    # ---- Seed system rule types ------------------------------------------

    rule_types_table = sa.table(
        "alarm_rule_types",
        sa.column("code", sa.String),
        sa.column("label", sa.String),
        sa.column("description", sa.Text),
        sa.column("rank", sa.Integer),
        sa.column("is_system", sa.Boolean),
        sa.column("is_evaluable", sa.Boolean),
    )
    op.bulk_insert(rule_types_table, [
        {"code": code, "label": label, "description": desc,
         "rank": rank, "is_system": True, "is_evaluable": evaluable}
        for (code, label, rank, evaluable, desc) in SEED_RULE_TYPES
    ])

    # ---- Drop residual CHECK on alarm_rules.rule_type --------------------
    # Name-agnostic: walks pg_constraint and drops any CHECK on
    # alarm_rules whose definition mentions rule_type. Avoids the
    # same naming-assumption bug that bit 0029.

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
                  AND pg_get_constraintdef(con.oid) ILIKE '%rule_type%'
            LOOP
                EXECUTE 'ALTER TABLE alarm_rules DROP CONSTRAINT '
                    || quote_ident(cons_name);
            END LOOP;
        END $$;
    """)

    # ---- Add FK from alarm_rules.rule_type to alarm_rule_types.code ------

    op.create_foreign_key(
        "alarm_rules_rule_type_fk",
        "alarm_rules", "alarm_rule_types",
        ["rule_type"], ["code"],
        onupdate="CASCADE",
        ondelete="RESTRICT",
    )

    # ---- updated_at trigger ----------------------------------------------

    op.execute("""
        CREATE TRIGGER alarm_rule_types_touch_updated_at
        BEFORE UPDATE ON alarm_rule_types
        FOR EACH ROW EXECUTE FUNCTION touch_updated_at()
    """)


def downgrade() -> None:
    op.execute("""
        DROP TRIGGER IF EXISTS alarm_rule_types_touch_updated_at
        ON alarm_rule_types
    """)

    op.drop_constraint(
        "alarm_rules_rule_type_fk", "alarm_rules", type_="foreignkey"
    )

    # Restore the original CHECK with the short `_chk` name that
    # PostgreSQL was using before 0031 ran.
    op.execute("""
        ALTER TABLE alarm_rules
        ADD CONSTRAINT alarm_rules_type_chk
        CHECK (rule_type IN (
            'hi_hi','hi','lo','lo_lo','deviation','rate_of_change'
        ))
    """)

    op.drop_table("alarm_rule_types")
