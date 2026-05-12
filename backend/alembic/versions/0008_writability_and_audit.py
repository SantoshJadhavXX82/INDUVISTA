"""Phase 8.5.1 — Writability flags and audit completeness.

Phase 8.5 made writes possible and auditable. This migration makes them
EXPLICITLY GOVERNED:

  1. register_blocks.writable  — engineering policy ("this address range
                                  is RW capable, not measurement-only")
  2. tags.writable             — granular per-tag opt-in (defaults FALSE
                                  even within a writable block)

Both default to FALSE. After applying this migration, ALL existing tags
become read-only until explicitly marked writable. This is intentional
and safe — fiscal-grade systems require explicit opt-in for writes.

CHECK constraints enforce the protocol-level reality that DI (FC 2) and
IR (FC 4) areas are ALWAYS read-only — no policy can make them writable
because the Modbus spec doesn't permit it.

Audit:
  3. write_journal.value_before — value from latest_tag_values captured
                                   immediately before the Modbus write
                                   went out. Lets the audit trail answer
                                   "what was it before this write?", not
                                   just "what did the user request?"
"""
from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "0008_writability_and_audit"
down_revision: Union[str, None] = "0007_modbus_hardening"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ----- register_blocks.writable -------------------------------------
    op.add_column("register_blocks", sa.Column(
        "writable", sa.Boolean(),
        nullable=False, server_default=sa.false(),
        comment="Engineering policy: is this block RW capable? Only "
                "meaningful for FC 1 (Coil) and FC 3 (Holding Register). "
                "FC 2 (DI) and FC 4 (IR) are always read-only per spec.",
    ))
    # FC 2 (DI) and FC 4 (IR) areas are ALWAYS read-only per Modbus spec.
    op.create_check_constraint(
        "ck_register_blocks_writable_fc",
        "register_blocks",
        "writable = FALSE OR function_code IN (1, 3)",
    )

    # ----- tags.writable -------------------------------------------------
    op.add_column("tags", sa.Column(
        "writable", sa.Boolean(),
        nullable=False, server_default=sa.false(),
        comment="Per-tag write opt-in. For tags with a block, the parent "
                "block.writable must also be TRUE. Defaults FALSE — every "
                "tag is read-only until explicitly enabled.",
    ))
    op.create_check_constraint(
        "ck_tags_writable_fc",
        "tags",
        "writable = FALSE OR function_code IN (1, 3)",
    )

    # ----- write_journal.value_before ------------------------------------
    op.add_column("write_journal", sa.Column(
        "value_before", sa.Text(),
        nullable=True,
        comment="Value from latest_tag_values at the moment the write "
                "was issued. Lets audit answer 'what changed?' rather "
                "than just 'what was requested?'. Null if the tag had "
                "never been read.",
    ))


def downgrade() -> None:
    op.drop_column("write_journal", "value_before")
    op.drop_constraint("ck_tags_writable_fc", "tags", type_="check")
    op.drop_column("tags", "writable")
    op.drop_constraint("ck_register_blocks_writable_fc",
                       "register_blocks", type_="check")
    op.drop_column("register_blocks", "writable")
