"""Add block-based template storage to report definitions (no-code builder).

report_definitions.template_html holds the (compiled or hand-written) Jinja2.
This adds template_blocks JSONB — the structured block list the visual builder
edits. When present, it is the source of truth and template_html is its
compiled output; when absent, template_html is used directly (advanced mode).

Also adds template_mode to record which authoring path a report uses.

Additive + nullable; existing reports keep working (mode defaults to 'html').
Reversible.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0061_report_blocks"
down_revision = "0060_report_periods"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "report_definitions",
        sa.Column("template_blocks", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "report_definitions",
        sa.Column("template_mode", sa.String(8), nullable=False, server_default="html"),
    )
    op.create_check_constraint(
        "ck_report_def_template_mode",
        "report_definitions",
        "template_mode IN ('html', 'blocks')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_report_def_template_mode", "report_definitions", type_="check")
    op.drop_column("report_definitions", "template_mode")
    op.drop_column("report_definitions", "template_blocks")
