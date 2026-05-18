"""Phase 15.3 - Tier B selection blocks.

Adds 6 selection blocks to calc_block_types. The Python classes
implementing them ship in this same drop under
app/workers/calc_blocks/selection_tier_b.py.

Block catalog:

  FIRST_GOOD        Return value of first input with quality >= GOOD.
  LAST_GOOD         Same but scanning in reverse.
  HOT_STANDBY       Two-input failover with named primary/standby keys.
  HIGHEST_QUALITY   Argmax over input quality byte; ties go to first.
  VOTING_M_OF_N     Median of largest cluster within tolerance band
                    (triple-modular-redundancy pattern; IEC 61511 P1
                    sect 9.4).
  MUX_INDEX         Index-controlled multiplexer (IEC 61131-3 MUX FB).

Standards:
  - IEC 61131-3 sect 6.6.1 (selection function blocks: SEL, MUX,
    MAX, MIN, LIMIT)
  - IEC 61511 Part 1 sect 9.4 (voting for safety instrumented systems)
  - OPC UA Part 8 sect 5.2.2 (quality byte semantics)
"""

from alembic import op
import sqlalchemy as sa


revision = "0037_calc_selection_tier_b"
down_revision = "0036_alarm_types_frozen_spike"
branch_labels = None
depends_on = None


# Ranks 20-25 sit between aggregation (5-17) and IF_THEN_ELSE (100),
# leaving room for future tier expansions in the 26-99 range without
# renumbering existing rows.
#
# Format: (code, label, category, rank, description)
NEW_BLOCK_TYPES = [
    ('FIRST_GOOD', 'First Good', 'selection', 20,
     'Return value of first input with quality >= GOOD. Scans inputs '
     'in declared order. IEC 61131-3 sect 6.6.1 SEL extended to N inputs.'),
    ('LAST_GOOD', 'Last Good', 'selection', 21,
     'Return value of last input with quality >= GOOD. Scans in reverse. '
     'Useful for chronologically-ordered inputs where the freshest GOOD wins.'),
    ('HOT_STANDBY', 'Hot Standby', 'selection', 22,
     'Two-input failover with named primary/standby keys. Returns primary '
     'if GOOD, else standby. Stateless; bumpless failback is future work.'),
    ('HIGHEST_QUALITY', 'Highest Quality', 'selection', 23,
     'Return value of input with highest quality byte. Output quality '
     'mirrors the chosen input, so "best of all bad" stays BAD downstream.'),
    ('VOTING_M_OF_N', 'Voting M-of-N', 'selection', 24,
     'Median of largest cluster of inputs that agree within tolerance. '
     'Triple-modular-redundancy pattern per IEC 61511 Part 1 sect 9.4. '
     'Defaults to strict majority (floor(N/2)+1) for min_agreement.'),
    ('MUX_INDEX', 'Index Multiplexer', 'selection', 25,
     'Index-controlled selector: separate index tag plus 1..64 value tags. '
     'IEC 61131-3 sect 6.6.1 MUX function block.'),
]


def upgrade():
    op.bulk_insert(
        sa.table(
            "calc_block_types",
            sa.column("code", sa.String),
            sa.column("label", sa.String),
            sa.column("category", sa.String),
            sa.column("description", sa.Text),
            sa.column("rank", sa.Integer),
            sa.column("is_evaluable", sa.Boolean),
        ),
        [
            {"code": code, "label": label, "category": cat,
             "rank": rank, "description": desc, "is_evaluable": True}
            for (code, label, cat, rank, desc) in NEW_BLOCK_TYPES
        ],
    )


def downgrade():
    new_codes = ', '.join(repr(r[0]) for r in NEW_BLOCK_TYPES)
    op.execute(f"DELETE FROM calc_block_types WHERE code IN ({new_codes})")
