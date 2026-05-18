"""Phase 15.4a - Conditional, comparison, and logical blocks.

Flips IF_THEN_ELSE to is_evaluable=true (Python class ships in this
drop under app/workers/calc_blocks/conditional_logic_tier_c.py) and
adds 10 new blocks across two categories.

Rank layout:
    20-25:   selection (Tier B, Phase 15.3)
    30-35:   comparison (this drop)        GT, LT, GTE, LTE, EQ, NE
    40-43:   logical (this drop)           AND_OF, OR_OF, XOR_OF, NOT
    100:     conditional - IF_THEN_ELSE (existing row, flip only)

Standards:
  - IEC 61131-3 sect 6.6.1 (SEL / IF-THEN-ELSE)
  - IEC 61131-3 sect 6.6.3 (logical operators: AND, OR, XOR, NOT)
  - IEC 61131-3 sect 6.6.4 (comparison operators: GT, LT, GE, LE, EQ, NE)
"""

from alembic import op
import sqlalchemy as sa


revision = "0038_calc_conditional_logic"
down_revision = "0037_calc_selection_tier_b"
branch_labels = None
depends_on = None


# Format: (code, label, category, rank, description)
NEW_BLOCK_TYPES = [
    # --- Comparison (sect 6.6.4) ---
    ('GT', 'Greater Than', 'comparison', 30,
     'left > right. Right may be a tag (right=tag_id) or constant '
     '(value=number). Output 1.0/0.0. IEC 61131-3 sect 6.6.4.'),
    ('LT', 'Less Than', 'comparison', 31,
     'left < right. Right may be a tag or constant. IEC 61131-3 sect 6.6.4.'),
    ('GTE', 'Greater Than or Equal', 'comparison', 32,
     'left >= right. IEC 61131-3 sect 6.6.4.'),
    ('LTE', 'Less Than or Equal', 'comparison', 33,
     'left <= right. IEC 61131-3 sect 6.6.4.'),
    ('EQ', 'Equal', 'comparison', 34,
     '|left - right| <= tolerance (default 0). Use tolerance to avoid '
     'floating-point equality pitfalls. IEC 61131-3 sect 6.6.4.'),
    ('NE', 'Not Equal', 'comparison', 35,
     '|left - right| > tolerance. IEC 61131-3 sect 6.6.4.'),

    # --- Logical (sect 6.6.3) ---
    ('AND_OF', 'Logical AND', 'logical', 40,
     'Logical AND of N inputs. Inputs >0 are treated as TRUE. Any '
     'BAD input -> BAD output. IEC 61131-3 sect 6.6.3.'),
    ('OR_OF', 'Logical OR', 'logical', 41,
     'Logical OR of N inputs. Inputs >0 are TRUE. IEC 61131-3 sect 6.6.3.'),
    ('XOR_OF', 'Logical XOR', 'logical', 42,
     'Logical XOR of N inputs - TRUE if an odd number of inputs are '
     'TRUE (parity). IEC 61131-3 sect 6.6.3.'),
    ('NOT', 'Logical NOT', 'logical', 43,
     'Single-input logical NOT. >0 inverts to 0; <=0 inverts to 1. '
     'IEC 61131-3 sect 6.6.3.'),
]


def upgrade():
    # 1. Flip IF_THEN_ELSE to evaluable - its Python class ships in this drop.
    op.execute("""
        UPDATE calc_block_types
        SET is_evaluable = true
        WHERE code = 'IF_THEN_ELSE'
    """)

    # 2. Insert the 10 new block-type rows.
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

    op.execute("""
        UPDATE calc_block_types
        SET is_evaluable = false
        WHERE code = 'IF_THEN_ELSE'
    """)
