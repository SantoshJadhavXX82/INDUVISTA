"""Destinations like triggers: per-report scope + multi-format defaults + override.

  report_destinations:
    + owner_report_id  -> null = Global (shared), set = private to one report
    + default_fmts     -> comma-separated default format set ("pdf,json")
  report_destination_links:
    fmt widened 8 -> 64 -> now holds a per-report OVERRIDE set; empty = use default.

revision id <= 32 chars.
"""
from alembic import op
import sqlalchemy as sa

revision = "0066_dest_scope_fmts"
down_revision = "0065_tag_trigger_cond"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("report_destinations",
                  sa.Column("owner_report_id", sa.BigInteger(), nullable=True))
    op.add_column("report_destinations",
                  sa.Column("default_fmts", sa.String(64), nullable=False, server_default="pdf"))
    op.create_foreign_key(
        "fk_rd_owner", "report_destinations",
        "report_definitions", ["owner_report_id"], ["id"], ondelete="CASCADE")

    # widen the link fmt to hold a comma-separated override set; allow NULL = no override
    op.alter_column("report_destination_links", "fmt",
                    existing_type=sa.String(8), type_=sa.String(64),
                    existing_nullable=False, nullable=True,
                    existing_server_default="pdf", server_default=None)


def downgrade() -> None:
    op.alter_column("report_destination_links", "fmt",
                    existing_type=sa.String(64), type_=sa.String(8),
                    existing_nullable=True, nullable=False, server_default="pdf")
    op.drop_constraint("fk_rd_owner", "report_destinations", type_="foreignkey")
    op.drop_column("report_destinations", "default_fmts")
    op.drop_column("report_destinations", "owner_report_id")
