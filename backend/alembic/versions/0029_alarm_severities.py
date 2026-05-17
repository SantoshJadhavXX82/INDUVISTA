"""Phase 14.6 — Alarm severities master table.

Replaces the hardcoded CHECK constraint on alarm_rules.severity with a
proper master table that operators can extend from a Setup page.

Design:
  - Severities are identified by their code string (e.g. 'critical').
    The code is also the FK reference from alarm_rules, so no data
    migration is needed: existing rows already carry the right strings.
  - The 5 original severities are seeded with is_system=true so they
    can't be deleted (they can be re-labelled / re-coloured / re-ranked).
  - Rank is unique — every severity must have a distinct priority.
    Smaller rank = more urgent.
  - color_hex defaults to a sensible palette but is fully editable.

Downgrade restores the old CHECK constraint and drops the master table.
"""

from alembic import op
import sqlalchemy as sa


# Alembic identifiers
revision = "0029_alarm_severities"
down_revision = "0028_alarm_baseline"
branch_labels = None
depends_on = None


SEED_SEVERITIES = [
    # (code, label, color_hex, rank)
    ("critical", "Critical", "#dc2626", 1),
    ("high",     "High",     "#ea580c", 2),
    ("medium",   "Medium",   "#d97706", 3),
    ("low",      "Low",      "#2563eb", 4),
    ("info",     "Info",     "#64748b", 5),
]


def upgrade() -> None:
    # ---- touch_updated_at() helper function ------------------------------
    # CREATE OR REPLACE so this migration is idempotent and doesn't conflict
    # with any other migration that may also define it. We keep it on the
    # downgrade side — many tables may come to depend on it.

    op.execute("""
        CREATE OR REPLACE FUNCTION touch_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)

    # ---- alarm_severities table ------------------------------------------

    op.create_table(
        "alarm_severities",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.String(50), nullable=False),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column("color_hex", sa.String(7), nullable=False,
                  server_default="#888888"),
        sa.Column("rank", sa.Integer, nullable=False),
        sa.Column("is_system", sa.Boolean, nullable=False,
                  server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
    )

    # Code and rank must each be unique
    op.create_unique_constraint(
        "alarm_severities_code_uniq", "alarm_severities", ["code"]
    )
    op.create_unique_constraint(
        "alarm_severities_rank_uniq", "alarm_severities", ["rank"]
    )

    # Constrain color_hex to 7-char hex format (#rrggbb)
    op.create_check_constraint(
        "alarm_severities_color_format",
        "alarm_severities",
        "color_hex ~ '^#[0-9a-fA-F]{6}$'",
    )

    # Constrain code: lowercase + digits + underscore, starting with a letter
    op.create_check_constraint(
        "alarm_severities_code_format",
        "alarm_severities",
        "code ~ '^[a-z][a-z0-9_]*$'",
    )

    # ---- Seed system severities ------------------------------------------

    severities_table = sa.table(
        "alarm_severities",
        sa.column("code", sa.String),
        sa.column("label", sa.String),
        sa.column("color_hex", sa.String),
        sa.column("rank", sa.Integer),
        sa.column("is_system", sa.Boolean),
    )
    op.bulk_insert(severities_table, [
        {"code": code, "label": label, "color_hex": color,
         "rank": rank, "is_system": True}
        for (code, label, color, rank) in SEED_SEVERITIES
    ])

    # ---- Replace CHECK constraint on alarm_rules.severity with FK --------
    #
    # The existing data already uses these codes, so the FK can be added
    # with no migration of values. The CHECK constraint becomes redundant
    # and is dropped.

    op.execute("""
        ALTER TABLE alarm_rules
        DROP CONSTRAINT IF EXISTS alarm_rules_severity_check
    """)

    op.create_foreign_key(
        "alarm_rules_severity_fk",
        "alarm_rules", "alarm_severities",
        ["severity"], ["code"],
        onupdate="CASCADE",   # if a code is renamed (rare), propagate
        ondelete="RESTRICT",  # can't delete a severity in use
    )

    # ---- updated_at trigger ----------------------------------------------
    # touch_updated_at() function already exists from phase 14.1.

    op.execute("""
        CREATE TRIGGER alarm_severities_touch_updated_at
        BEFORE UPDATE ON alarm_severities
        FOR EACH ROW EXECUTE FUNCTION touch_updated_at()
    """)


def downgrade() -> None:
    # Drop trigger, FK, then restore the CHECK constraint, then drop table.
    op.execute("""
        DROP TRIGGER IF EXISTS alarm_severities_touch_updated_at
        ON alarm_severities
    """)

    op.drop_constraint(
        "alarm_rules_severity_fk", "alarm_rules", type_="foreignkey"
    )

    op.create_check_constraint(
        "alarm_rules_severity_check",
        "alarm_rules",
        "severity IN ('critical','high','medium','low','info')",
    )

    op.drop_table("alarm_severities")
