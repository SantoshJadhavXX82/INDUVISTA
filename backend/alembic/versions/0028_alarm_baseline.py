"""Phase 14.1 — Alarms & Events baseline.

Adds three tables to support alarm rule definition, current-state
tracking, and historical event logging:

    alarm_rules    — config: tag_id, rule_type, threshold, deadband,
                     delays, severity, message_template. One row per
                     configured rule. Currently 4 basic types per tag
                     (hi_hi / hi / lo / lo_lo); deviation and
                     rate_of_change allowed in multiples.

    alarm_state    — current state per rule (1:1 with alarm_rules).
                     Maintained by the evaluator (Phase 14.3). Carries
                     ISA-18.2-style state machine + on/off-delay pending
                     timestamps + shelving info.

    alarm_events   — append-only event log. Hypertable on event_time
                     with 7-day chunks, compressed after 30 days,
                     retained 1 year. NO FK to alarm_rules so
                     historical events survive rule deletion (audit).

Triggers:
    - INSERT on alarm_rules auto-creates a `normal` alarm_state row
    - UPDATE on alarm_rules bumps updated_at

Severity vocabulary: critical / high / medium / low / info.
ISA-18.2's urgent/journal terms are technically correct but less
familiar to plant operators; the underlying state machine still
matches ISA-18.2 semantics.

Revision ID: 0028_alarm_baseline
Revises: 0027_widen_ca_policies
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0028_alarm_baseline"
down_revision = "0027_widen_ca_policies"
branch_labels = None
depends_on = None


# ---------------------------------------------------------------------------
# Allowed values, kept in sync with the API layer (Phase 14.2) and the
# evaluator (Phase 14.3). CHECK constraints in SQL below mirror these.
# ---------------------------------------------------------------------------

RULE_TYPES = ("hi_hi", "hi", "lo", "lo_lo", "deviation", "rate_of_change")
SEVERITIES = ("critical", "high", "medium", "low", "info")
EVENT_TYPES = (
    "activated", "cleared",
    "acked", "shelved", "unshelved",
    "disabled", "enabled",
)
STATE_VALUES = (
    "normal",
    "active_unack", "active_ack",
    "inactive_unack",
    "shelved", "disabled",
)


def _check_in(name: str, column: str, values: tuple[str, ...]) -> sa.CheckConstraint:
    quoted = ", ".join(f"'{v}'" for v in values)
    return sa.CheckConstraint(f"{column} IN ({quoted})", name=name)


# ---------------------------------------------------------------------------
# Upgrade
# ---------------------------------------------------------------------------

def upgrade() -> None:
    # ---- 0. Defensive cleanup so retries are idempotent --------------------
    # Drop in reverse-dependency order. CASCADE handles the trigger + state
    # row dependencies that would otherwise block table-drops.
    with op.get_context().autocommit_block():
        op.execute("DROP TRIGGER IF EXISTS alarm_rules_touch_updated ON alarm_rules")
        op.execute("DROP TRIGGER IF EXISTS alarm_rules_insert_state ON alarm_rules")
        op.execute("DROP FUNCTION IF EXISTS alarm_rules_touch_updated_at()")
        op.execute("DROP FUNCTION IF EXISTS alarm_rules_create_state()")
        op.execute("DROP TABLE IF EXISTS alarm_events CASCADE")
        op.execute("DROP TABLE IF EXISTS alarm_state  CASCADE")
        op.execute("DROP TABLE IF EXISTS alarm_rules  CASCADE")

    # ---- 1. alarm_rules ----------------------------------------------------
    op.create_table(
        "alarm_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "tag_id", sa.Integer(),
            sa.ForeignKey("tags.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("rule_type", sa.String(length=16), nullable=False),
        sa.Column("severity",  sa.String(length=16), nullable=False,
                  server_default="high"),
        sa.Column("threshold", sa.Float(), nullable=False),
        sa.Column("deadband",  sa.Float(), nullable=False,
                  server_default="0"),
        sa.Column("on_delay_sec",  sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("off_delay_sec", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("latched", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
        sa.Column("enabled", sa.Boolean(), nullable=False,
                  server_default=sa.text("true")),
        sa.Column("message_template", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        _check_in("alarm_rules_type_chk",     "rule_type", RULE_TYPES),
        _check_in("alarm_rules_severity_chk", "severity",  SEVERITIES),
        sa.CheckConstraint("deadband >= 0",         name="alarm_rules_deadband_chk"),
        sa.CheckConstraint("on_delay_sec >= 0",     name="alarm_rules_on_delay_chk"),
        sa.CheckConstraint("off_delay_sec >= 0",    name="alarm_rules_off_delay_chk"),
    )

    # The four "level" rule types are mutually exclusive per tag: at most
    # one hi_hi, one hi, one lo, one lo_lo. deviation and rate_of_change
    # may have multiples (different windows / sides).
    op.create_index(
        "alarm_rules_unique_basic",
        "alarm_rules",
        ["tag_id", "rule_type"],
        unique=True,
        postgresql_where=sa.text(
            "rule_type IN ('hi_hi', 'hi', 'lo', 'lo_lo')"
        ),
    )
    op.create_index(
        "alarm_rules_tag_enabled",
        "alarm_rules",
        ["tag_id"],
        postgresql_where=sa.text("enabled = true"),
    )

    # ---- 2. alarm_state (1:1 with alarm_rules) -----------------------------
    op.create_table(
        "alarm_state",
        sa.Column(
            "rule_id", sa.Integer(),
            sa.ForeignKey("alarm_rules.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("state", sa.String(length=24), nullable=False,
                  server_default="normal"),
        sa.Column("last_change_time", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        # Pending timestamps drive the on/off-delay logic in the evaluator.
        # NULL when no pending transition is being timed.
        sa.Column("pending_active_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pending_clear_since",  sa.DateTime(timezone=True), nullable=True),
        sa.Column("current_value",   sa.Float(),    nullable=True),
        sa.Column("current_quality", sa.SmallInteger(), nullable=True),
        sa.Column("last_ack_user_id", sa.Integer(), nullable=True),
        sa.Column("last_ack_time",    sa.DateTime(timezone=True), nullable=True),
        sa.Column("shelved_until",    sa.DateTime(timezone=True), nullable=True),
        sa.Column("shelve_user_id",   sa.Integer(), nullable=True),
        _check_in("alarm_state_state_chk", "state", STATE_VALUES),
    )

    op.create_index(
        "alarm_state_active",
        "alarm_state",
        ["state"],
        postgresql_where=sa.text(
            "state IN ('active_unack', 'active_ack', 'inactive_unack')"
        ),
    )

    # ---- 3. alarm_events (hypertable, append-only audit log) ---------------
    # NOTE: hypertable partitioning column MUST be part of the primary key.
    # We use (event_time, id) so id is still globally unique within a chunk
    # while event_time partitions across chunks.
    op.create_table(
        "alarm_events",
        sa.Column("id", sa.BigInteger(),
                  sa.Identity(always=False), nullable=False),
        sa.Column("rule_id", sa.Integer(), nullable=False),
        sa.Column("tag_id",  sa.Integer(), nullable=False),
        sa.Column("event_time", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("event_type", sa.String(length=16), nullable=False),
        sa.Column("value",   sa.Float(), nullable=True),
        sa.Column("quality", sa.SmallInteger(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("event_time", "id",
                                name="alarm_events_pkey"),
        _check_in("alarm_events_type_chk", "event_type", EVENT_TYPES),
    )

    # Indexes for the two dominant query patterns:
    #   - "what happened on this rule lately"  → (rule_id, event_time desc)
    #   - "what alarms have fired for this tag" → (tag_id,  event_time desc)
    op.create_index(
        "alarm_events_rule_time",
        "alarm_events",
        ["rule_id", sa.text("event_time DESC")],
    )
    op.create_index(
        "alarm_events_tag_time",
        "alarm_events",
        ["tag_id", sa.text("event_time DESC")],
    )

    # TimescaleDB hypertable conversion + compression + retention.
    # The autocommit_block is required because TimescaleDB DDL can't run
    # inside a transaction that also includes regular table operations.
    with op.get_context().autocommit_block():
        op.execute("""
            SELECT create_hypertable(
                'alarm_events', 'event_time',
                chunk_time_interval => INTERVAL '7 days',
                if_not_exists => TRUE
            )
        """)

        op.execute("""
            ALTER TABLE alarm_events SET (
                timescaledb.compress,
                timescaledb.compress_segmentby = 'tag_id, rule_id'
            )
        """)

        # Compress chunks older than 30 days; retain everything for 1 year.
        op.execute("""
            SELECT add_compression_policy(
                'alarm_events', INTERVAL '30 days',
                if_not_exists => TRUE
            )
        """)
        op.execute("""
            SELECT add_retention_policy(
                'alarm_events', INTERVAL '365 days',
                if_not_exists => TRUE
            )
        """)

    # ---- 4. Triggers -------------------------------------------------------
    # Auto-create the alarm_state row when a rule is inserted. ON CONFLICT
    # is defensive: if a state row already exists (manual seed, etc.) we
    # don't error out.
    op.execute("""
        CREATE OR REPLACE FUNCTION alarm_rules_create_state()
        RETURNS TRIGGER AS $$
        BEGIN
            INSERT INTO alarm_state (rule_id, state)
            VALUES (NEW.id, 'normal')
            ON CONFLICT (rule_id) DO NOTHING;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER alarm_rules_insert_state
        AFTER INSERT ON alarm_rules
        FOR EACH ROW
        EXECUTE FUNCTION alarm_rules_create_state();
    """)

    # Bump updated_at on any rule mutation so the API can return a fresh
    # `updated_at` without the caller having to set it.
    op.execute("""
        CREATE OR REPLACE FUNCTION alarm_rules_touch_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER alarm_rules_touch_updated
        BEFORE UPDATE ON alarm_rules
        FOR EACH ROW
        EXECUTE FUNCTION alarm_rules_touch_updated_at();
    """)


# ---------------------------------------------------------------------------
# Downgrade
# ---------------------------------------------------------------------------

def downgrade() -> None:
    # Reverse-dependency order. Hypertable DROP TABLE handles its own
    # chunks; the retention/compression jobs unregister automatically.
    op.execute("DROP TRIGGER IF EXISTS alarm_rules_touch_updated ON alarm_rules")
    op.execute("DROP TRIGGER IF EXISTS alarm_rules_insert_state ON alarm_rules")
    op.execute("DROP FUNCTION IF EXISTS alarm_rules_touch_updated_at()")
    op.execute("DROP FUNCTION IF EXISTS alarm_rules_create_state()")

    with op.get_context().autocommit_block():
        # Belt-and-braces — these would also auto-clean on DROP TABLE,
        # but being explicit avoids leaving orphaned job rows visible
        # in timescaledb_information.jobs after a partial failure.
        op.execute("""
            SELECT remove_retention_policy('alarm_events', if_exists => TRUE)
        """)
        op.execute("""
            SELECT remove_compression_policy('alarm_events', if_exists => TRUE)
        """)

    op.drop_index("alarm_events_tag_time",  table_name="alarm_events")
    op.drop_index("alarm_events_rule_time", table_name="alarm_events")
    op.drop_table("alarm_events")

    op.drop_index("alarm_state_active", table_name="alarm_state")
    op.drop_table("alarm_state")

    op.drop_index("alarm_rules_tag_enabled",  table_name="alarm_rules")
    op.drop_index("alarm_rules_unique_basic", table_name="alarm_rules")
    op.drop_table("alarm_rules")
