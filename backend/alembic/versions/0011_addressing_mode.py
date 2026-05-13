"""Phase 9.1 — Enron addressing mode on register_blocks.

Adds an addressing_mode column to register_blocks so blocks can declare
whether their addresses follow standard Modbus (1 address = 1 physical
16-bit register, current behavior) or Enron Modbus (1 address = 1
logical value of the block's data width).

Enron addressing is used by Daniel SIM 2251 / Rosemount Analytical /
Emerson FB107 gas chromatographs and most fiscal flow computers. In the
Daniel SIM 2251 map, registers 7001-7016 hold 16 float32 mole-% values
(one float per address), 7033 holds a single float32 BTU value, etc.
Standard Modbus would require 7001-7032 for 16 floats.

This migration only adds the column + constraints. The wire-level
Modbus PDU is unchanged — block.count remains the physical-register
count the master asks for. What changes is how the worker translates
each tag's logical address into a byte offset within the block-read
response. See _decode_block in modbus_supervisor.py.

CHECK constraints:
  - addressing_mode IN ('STANDARD', 'ENRON_HOLDING', 'ENRON_INPUT')
  - ENRON_HOLDING only valid with function_code = 3 (Read Holding Registers)
  - ENRON_INPUT  only valid with function_code = 4 (Read Input Registers)
  - STANDARD valid with any function_code (1, 2, 3, or 4)

DI/Coil (FC 1, 2) blocks are inherently bit-addressed and have no
notion of multi-byte values; Enron addressing doesn't apply to them.
"""
from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "0011_addressing_mode"
down_revision: Union[str, None] = "0010_widen_st_reason"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "register_blocks",
        sa.Column(
            "addressing_mode",
            sa.String(20),
            nullable=False,
            server_default="STANDARD",
        ),
    )
    op.create_check_constraint(
        "ck_register_blocks_addressing_mode_values",
        "register_blocks",
        "addressing_mode IN ('STANDARD', 'ENRON_HOLDING', 'ENRON_INPUT')",
    )
    op.create_check_constraint(
        "ck_register_blocks_addressing_mode_fc",
        "register_blocks",
        "addressing_mode = 'STANDARD' "
        "OR (addressing_mode = 'ENRON_HOLDING' AND function_code = 3) "
        "OR (addressing_mode = 'ENRON_INPUT'   AND function_code = 4)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_register_blocks_addressing_mode_fc",
                       "register_blocks", type_="check")
    op.drop_constraint("ck_register_blocks_addressing_mode_values",
                       "register_blocks", type_="check")
    op.drop_column("register_blocks", "addressing_mode")
