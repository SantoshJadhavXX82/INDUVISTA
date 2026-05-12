"""Phase 8.5.1 hotfix — widen write_journal.function_code CHECK.

The Phase 8.5 (migration 0007) introduced write_journal with a CHECK
constraint that only allowed function_code IN (5, 6, 15, 16) — the
Modbus WRITE function codes.

This was an oversight. The writer journals attempts that FAIL BEFORE
the write function code is determined (e.g. tag not found, tag not
configured writable, read-only area, invalid value). Those journal
entries carry either the READ function_code (1/2/3/4) or 0, which
violate the CHECK. The resulting IntegrityError is caught and logged
as "audit lost" — silently dropping the exact entries auditors care
about most (the failed attempts).

Fix: widen the CHECK to allow function_code BETWEEN 0 AND 16. Zero
means "the write FC could not be determined" (tag-not-found path);
1-16 covers every Modbus FC the system might journal.

The Phase 8.5.1 enforcement (writability flags) magnified this bug
because more failure paths now journal. Before the hotfix, every
'not writable' rejection was unrecorded — a serious compliance gap.
"""
from typing import Union

from alembic import op


revision: str = "0009_widen_write_journal_fc"
down_revision: Union[str, None] = "0008_writability_and_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_write_journal_fc", "write_journal", type_="check")
    op.create_check_constraint(
        "ck_write_journal_fc",
        "write_journal",
        "function_code >= 0 AND function_code <= 16",
    )


def downgrade() -> None:
    # Restore the original (over-restrictive) CHECK. Note this will fail
    # if rows with function_code outside (5,6,15,16) exist — same caveat
    # as the original migration.
    op.drop_constraint("ck_write_journal_fc", "write_journal", type_="check")
    op.create_check_constraint(
        "ck_write_journal_fc",
        "write_journal",
        "function_code IN (5, 6, 15, 16)",
    )
