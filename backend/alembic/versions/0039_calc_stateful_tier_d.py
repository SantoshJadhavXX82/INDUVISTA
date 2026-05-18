"""Phase 15.5 - Tier D stateful blocks.

Adds:

  1. calc_block_state table - per-calc-definition JSONB state row,
     read by the worker before each evaluation and written back after.
     ON DELETE CASCADE from calc_definitions means state is GC'd
     automatically when the owning calc_def is deleted.

  2. 9 stateful block-type rows across 4 categories:

       timer (3):           TON, TOF, TP
       edge_detector (2):   R_TRIG, F_TRIG
       latch (2):           SR, RS
       counter (2):         CTU, CTD

Rank layout:
    20-25:   selection (Tier B)
    30-35:   comparison (Tier C)
    40-43:   logical (Tier C)
    50-58:   stateful (this drop) - timer/edge/latch/counter
    100:     IF_THEN_ELSE (conditional)

Standards: IEC 61131-3 sect 6.6.5 (timers), 6.6.6 (edges), 6.6.7
(bistables), 6.6.8 (counters).
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "0039_calc_stateful_tier_d"
down_revision = "0038_calc_conditional_logic"
branch_labels = None
depends_on = None


# Format: (code, label, category, rank, description)
NEW_BLOCK_TYPES = [
    # --- Timers ---
    ('TON', 'On-Delay Timer', 'timer', 50,
     'Q goes TRUE after input has been TRUE for preset_ms. Falls back '
     'to FALSE immediately when input goes FALSE. IEC 61131-3 sect 6.6.5.'),
    ('TOF', 'Off-Delay Timer', 'timer', 51,
     'Q stays TRUE for preset_ms after input goes FALSE. Goes TRUE '
     'immediately when input is TRUE. IEC 61131-3 sect 6.6.5.'),
    ('TP', 'Pulse Timer', 'timer', 52,
     'Rising edge of input produces Q high for exactly preset_ms. '
     'Non-retriggerable - subsequent rising edges during a pulse are '
     'ignored. IEC 61131-3 sect 6.6.5.'),

    # --- Edge detectors ---
    ('R_TRIG', 'Rising Edge', 'edge_detector', 53,
     'Q is TRUE for one evaluation cycle when input transitions '
     'FALSE -> TRUE. IEC 61131-3 sect 6.6.6.'),
    ('F_TRIG', 'Falling Edge', 'edge_detector', 54,
     'Q is TRUE for one evaluation cycle when input transitions '
     'TRUE -> FALSE. IEC 61131-3 sect 6.6.6.'),

    # --- Latches ---
    ('SR', 'Set-Reset Latch', 'latch', 55,
     'Set-dominant SR latch. S=1 sets Q regardless of R; on tie '
     'S wins. IEC 61131-3 sect 6.6.7.'),
    ('RS', 'Reset-Set Latch', 'latch', 56,
     'Reset-dominant RS latch. R=1 clears Q regardless of S; on tie '
     'R wins. IEC 61131-3 sect 6.6.7.'),

    # --- Counters ---
    ('CTU', 'Up Counter', 'counter', 57,
     'CV increments on rising edge of count_up; reset clears to 0. '
     'Output is current CV. IEC 61131-3 sect 6.6.8.'),
    ('CTD', 'Down Counter', 'counter', 58,
     'CV decrements on rising edge of count_down; load reloads to '
     'configured load_value. Output is current CV. IEC 61131-3 sect 6.6.8.'),
]


def upgrade():
    # ---- 1. calc_block_state table ---------------------------------------
    op.create_table(
        "calc_block_state",
        sa.Column("calc_def_id", sa.Integer, primary_key=True),
        sa.Column("state", JSONB, nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
    )
    op.create_foreign_key(
        "calc_block_state_def_id_fk",
        "calc_block_state", "calc_definitions",
        ["calc_def_id"], ["id"],
        ondelete="CASCADE",
    )

    # ---- 2. Block-type catalog rows --------------------------------------
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

    op.drop_constraint(
        "calc_block_state_def_id_fk",
        "calc_block_state", type_="foreignkey",
    )
    op.drop_table("calc_block_state")
