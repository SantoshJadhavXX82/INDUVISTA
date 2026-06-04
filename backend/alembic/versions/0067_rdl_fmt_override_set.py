"""Fix report_destination_links.fmt constraint for override SETS.

Migration 0059 created report_destination_links with:
    fmt String(8), CHECK (fmt IN ('pdf', 'csv'))   -- constraint "ck_rdl_fmt"

Migration 0066 redefined `fmt` as a per-report OVERRIDE SET (comma-separated,
NULL = use the destination's default_fmts) and widened it to String(64) — but
it never replaced the old single-value CHECK. The result: NULL and 'pdf' pass,
but a real override set like 'pdf,html' (or even 'html' alone) violates
ck_rdl_fmt and the INSERT fails with a 400 "invalid field combination".

This migration drops the stale constraint and re-creates it (same name) to
match the documented contract: NULL, or a comma-separated list whose every
token is one of pdf|html|json|xml|csv. App-level validation in
api/reports_config.py already produces exactly this shape; the constraint is
defense-in-depth for any other write path.

revision id <= 32 chars.
"""
from alembic import op

revision = "0067_rdl_fmt_override"
down_revision = "0066_dest_scope_fmts"
branch_labels = None
depends_on = None

# NULL = no override (use destination default), OR a comma-separated set of
# valid lowercase format tokens with no spaces (e.g. "pdf", "pdf,html").
_NEW_CHECK = (
    r"fmt IS NULL OR fmt ~ "
    r"'^(pdf|html|json|xml|csv)(,(pdf|html|json|xml|csv))*$'"
)

# Original single-value constraint from migration 0059.
_OLD_CHECK = "fmt IN ('pdf', 'csv')"


def upgrade() -> None:
    op.drop_constraint("ck_rdl_fmt", "report_destination_links", type_="check")
    op.create_check_constraint(
        "ck_rdl_fmt", "report_destination_links", _NEW_CHECK,
    )


def downgrade() -> None:
    op.drop_constraint("ck_rdl_fmt", "report_destination_links", type_="check")
    # Restore the original strict constraint. NOTE: any existing rows holding a
    # multi-format override or a non-pdf/csv format will violate this — clean
    # report_destination_links.fmt before downgrading.
    op.create_check_constraint(
        "ck_rdl_fmt", "report_destination_links", _OLD_CHECK,
    )
