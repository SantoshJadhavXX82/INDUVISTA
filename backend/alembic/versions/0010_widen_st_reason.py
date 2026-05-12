"""Phase 8.5.1 hotfix #2 — widen st_reason from VARCHAR(32) to VARCHAR(64).

Phase 8.5 introduced richer error reasons in the Modbus worker —
RETRY_EXHAUSTED-with-original-cause, IO_ERROR with truncated exception
text, TRANSPORT_UNSUPPORTED_<transport>, etc. Several of these exceed
the original 32-char column width:

  RETRY_EXHAUSTED (2 attempts): TIMEOUT   →  38 chars
  IO_ERROR: <pymodbus exception text>     →  up to 58 chars
  UNKNOWN: <exception text>               →  up to 41 chars

Live writes from the worker happen to succeed today because they go
through SQLAlchemy bind parameters that silently truncate per column
metadata. The store-and-forward replay path reads from SQLite (which
stores TEXT verbatim, no truncation) and INSERTs without that safety
net, hitting StringDataRightTruncation. The replay loop catches the
error, retries the SAME batch on the next tick, fails identically,
forever — so the buffer never drains.

Widening the column to 64 chars accommodates every reason string the
worker generates today (RETRY_EXHAUSTED-with-cause being the longest at
about 38–58 chars depending on the nested original reason). Once this
migration runs, the SF buffer drains on its own within one replay tick
(~5 seconds) because the existing samples in the buffer are simply
re-attempted and now succeed.

Storage cost: trivial. 32 extra bytes per row on samples (a hypercore
TimescaleDB table) is dwarfed by the value columns already there.
"""
from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "0010_widen_st_reason"
down_revision: Union[str, None] = "0009_widen_write_journal_fc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Both tables carry st_reason. Live data has been silently truncating at
    # 32 chars via SQLAlchemy binds — those rows are fine, just less detailed
    # than they could have been. New writes after this migration will preserve
    # the full reason.
    op.alter_column(
        "tag_values", "st_reason",
        existing_type=sa.String(32),
        type_=sa.String(64),
        existing_nullable=True,
    )
    op.alter_column(
        "latest_tag_values", "st_reason",
        existing_type=sa.String(32),
        type_=sa.String(64),
        existing_nullable=True,
    )


def downgrade() -> None:
    # If any existing rows have st_reason longer than 32 chars, this will
    # fail with the same StringDataRightTruncation that motivated the widen.
    # Run an UPDATE to truncate first if you really need to downgrade:
    #   UPDATE tag_values SET st_reason = SUBSTRING(st_reason FROM 1 FOR 32);
    #   UPDATE latest_tag_values SET st_reason = SUBSTRING(st_reason FROM 1 FOR 32);
    op.alter_column(
        "tag_values", "st_reason",
        existing_type=sa.String(64),
        type_=sa.String(32),
        existing_nullable=True,
    )
    op.alter_column(
        "latest_tag_values", "st_reason",
        existing_type=sa.String(64),
        type_=sa.String(32),
        existing_nullable=True,
    )
