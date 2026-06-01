"""report_tags: which tags each report includes (ordered).

The trigger engine renders scheduled reports with no query string, so it needs
to know each report's tag set from the DB. The scheduler's _report_tag_ids()
already reads exactly this table:

    SELECT tag_id FROM report_tags WHERE report_id = :rid ORDER BY position

One row per (report, tag). `position` preserves display order. Deleting a report
or a tag cascades.

revision id <= 32 chars (alembic_version.version_num is VARCHAR(32)).
"""
from alembic import op
import sqlalchemy as sa

revision = "0063_report_tags"
down_revision = "0062_report_trigger_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "report_tags",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("report_id", sa.BigInteger(),
                  sa.ForeignKey("report_definitions.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("tag_id", sa.BigInteger(),
                  sa.ForeignKey("tags.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("report_id", "tag_id", name="uq_report_tag"),
    )
    op.create_index("ix_report_tags_report", "report_tags",
                    ["report_id", "position"])


def downgrade() -> None:
    op.drop_index("ix_report_tags_report", table_name="report_tags")
    op.drop_table("report_tags")
