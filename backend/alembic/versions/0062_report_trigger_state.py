"""Trigger-engine state: track last-fire time + last tag value per (report, trigger).

The report scheduler worker needs durable state so that:
  * timed triggers do not double-fire after a restart (it remembers the last
    scheduled instant it already ran),
  * missed schedules can be detected (last_fired_at far behind 'now'),
  * tag triggers can detect an edge (compare current value to last_seen_value).

One row per (report_id, trigger_id) link. Created lazily by the worker, but the
table is defined here so the schema is explicit and reversible.

revision id kept <= 32 chars (alembic_version.version_num is VARCHAR(32)).
"""
from alembic import op
import sqlalchemy as sa

revision = "0062_report_trigger_state"
down_revision = "0061_report_blocks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "report_trigger_state",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("report_id", sa.BigInteger(),
                  sa.ForeignKey("report_definitions.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("trigger_id", sa.BigInteger(),
                  sa.ForeignKey("report_triggers.id", ondelete="CASCADE"),
                  nullable=False),
        # timed: the last scheduled instant we successfully fired for.
        sa.Column("last_fired_at", sa.DateTime(timezone=True), nullable=True),
        # tag: the last numeric value we observed (for edge detection).
        sa.Column("last_seen_value", sa.Float(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("report_id", "trigger_id",
                            name="uq_report_trigger_state"),
    )
    op.create_index("ix_report_trigger_state_lookup",
                    "report_trigger_state", ["report_id", "trigger_id"])


def downgrade() -> None:
    op.drop_index("ix_report_trigger_state_lookup",
                  table_name="report_trigger_state")
    op.drop_table("report_trigger_state")
