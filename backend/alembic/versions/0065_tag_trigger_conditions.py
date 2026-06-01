"""Tag-trigger conditions: comparison operators + formula expressions.

Until now a tag trigger only had tag_edge (to_nonzero | rising | any_change).
This adds two more ways to express the firing condition:

  * COMPARISON: tag_op (= != > >= < <=) + tag_value (number)  e.g. "Tag > 2"
  * FORMULA:    tag_expr (safe expression, var `x` = the tag value)
                e.g. "x > 50 and x < 90", "x == 4"

The engine fires on the FALSE->TRUE transition of the condition (one report per
crossing), not every tick while true. tag_edge is kept for back-compat; mode is
chosen by which field is set: tag_expr > (tag_op+tag_value) > tag_edge.

revision id <= 32 chars.
"""
from alembic import op
import sqlalchemy as sa

revision = "0065_tag_trigger_cond"
down_revision = "0064_report_trigger_days"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("report_triggers", sa.Column("tag_op", sa.String(length=4), nullable=True))
    op.add_column("report_triggers", sa.Column("tag_value", sa.Float(), nullable=True))
    op.add_column("report_triggers", sa.Column("tag_expr", sa.String(length=200), nullable=True))


def downgrade() -> None:
    op.drop_column("report_triggers", "tag_expr")
    op.drop_column("report_triggers", "tag_value")
    op.drop_column("report_triggers", "tag_op")
