"""Phase 15.4b - Arithmetic blocks (Tier E).

Adds 20 stateless blocks across 3 new categories. No schema changes
needed - existing calc_block_types table just gets more rows. All
blocks reuse the standard stateless worker dispatch from Phase 15.2,
so calc_evaluator.py stays untouched.

Rank layout (full picture after this drop):
    5-17:    aggregation (Tier A)
    20-25:   selection (Tier B)
    30-35:   comparison (Tier C)
    40-43:   logical (Tier C)
    50-58:   stateful (Tier D) - timer/edge_detector/latch/counter
    60-67:   arithmetic (Tier E binary)
    68-73:   unary_math (Tier E unary)
    74-79:   transcendental (Tier E)
    100:     conditional (IF_THEN_ELSE)

Standards: IEC 61131-3 sect 6.6.2 (numerical functions).
"""

from alembic import op
import sqlalchemy as sa


revision = "0040_calc_arithmetic_tier_e"
down_revision = "0039_calc_stateful_tier_d"
branch_labels = None
depends_on = None


# (code, label, category, rank, description)
NEW_BLOCK_TYPES = [
    # --- Arithmetic (binary, 8) ---
    ('ADD',        'Add',         'arithmetic', 60,
     'Sum of left + right. Right can be a tag (key "right") or a '
     'numeric constant (key "value").'),
    ('SUB',        'Subtract',    'arithmetic', 61,
     'Difference left - right.'),
    ('MUL',        'Multiply',    'arithmetic', 62,
     'Product left * right.'),
    ('DIV',        'Divide',      'arithmetic', 63,
     'Quotient left / right. Division by zero produces BAD quality.'),
    ('MOD',        'Modulo',      'arithmetic', 64,
     'IEEE fmod(left, right). Result sign follows left (C-style). '
     'Modulo by zero produces BAD quality.'),
    ('POW',        'Power',       'arithmetic', 65,
     'Exponentiation left ** right. Complex or non-finite results '
     '(e.g. negative base with fractional exponent, 0^negative, '
     'overflow) produce BAD quality.'),
    ('MIN_OF_TWO', 'Min of Two',  'arithmetic', 66,
     'Lesser of left and right.'),
    ('MAX_OF_TWO', 'Max of Two',  'arithmetic', 67,
     'Greater of left and right.'),

    # --- Unary math (6) ---
    ('ABS',        'Absolute Value', 'unary_math', 68,
     'Magnitude |x|.'),
    ('NEG',        'Negate',         'unary_math', 69,
     'Unary negation -x.'),
    ('SQRT',       'Square Root',    'unary_math', 70,
     'Principal square root. Negative input produces BAD quality.'),
    ('FLOOR',      'Floor',          'unary_math', 71,
     'Largest integer <= x.'),
    ('CEIL',       'Ceiling',        'unary_math', 72,
     'Smallest integer >= x.'),
    ('ROUND',      'Round',          'unary_math', 73,
     'Round to nearest integer; ties go to even (banker''s rounding).'),

    # --- Transcendental (6) ---
    ('EXP',   'Exponential',  'transcendental', 74,
     'e^x. Overflow produces BAD quality.'),
    ('LN',    'Natural Log',  'transcendental', 75,
     'log base e. x <= 0 produces BAD quality.'),
    ('LOG10', 'Log Base 10',  'transcendental', 76,
     'log base 10. x <= 0 produces BAD quality.'),
    ('SIN',   'Sine',         'transcendental', 77,
     'sin(x), x in radians.'),
    ('COS',   'Cosine',       'transcendental', 78,
     'cos(x), x in radians.'),
    ('TAN',   'Tangent',      'transcendental', 79,
     'tan(x), x in radians. Asymptotic blow-ups (|result| > 1e15) '
     'produce BAD quality.'),
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
