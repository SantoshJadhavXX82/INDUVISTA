"""Phase 15.2 - Execution rate scheduling + aggregation tier blocks.

Adds:

  1. execution_rate_ms column on calc_definitions with a CHECK
     constraint limiting values to the 11 industrial-standard rates
     defined in the standards reference PDF section 2.7.

  2. calc_execution_stats sidecar table for high-frequency per-tick
     metrics (last_executed_at, last_duration_ms, last_status, etc.).
     Kept off calc_definitions because per-tick updates would fire
     touch_updated_at and pollute the updated_at semantics for
     actual config changes.

  3. Catalog updates: flips AVG_OF / MIN_OF / MAX_OF to
     is_evaluable=true (Python classes ship in this same drop), adds
     12 new tier-A aggregation blocks, and moves IF_THEN_ELSE from
     rank 10 to rank 100 to give the aggregation category a
     contiguous rank range for future expansion.

Standards alignment:
  - IEC 61131-3 sect 2.7 task model: execution_rate_ms maps to
    TASK INTERVAL. CHECK constraint enforces the rate set.
  - IEC 61131-3 sect 2.7.3: diagnostic reporting of overrun
    implemented via calc_execution_stats.last_status field.
  - OPC UA Part 8 sect 5.2 quality codes referenced by stats
    propagation through the worker (no schema impact).
"""

from alembic import op
import sqlalchemy as sa


revision = "0035_calc_execution_rate"
down_revision = "0034_calc_foundation"
branch_labels = None
depends_on = None


# Industrial-standard execution rates allowed by the CHECK constraint.
# These align with IEC 61131-3 task class conventions:
#   100/250 ms     - fast loop (motion, fast safety, edge detection)
#   500/1000 ms    - medium (PID, regulatory control)
#   5/10/30 s      - slow (slow analog, totalizers)
#   1/5/15 min     - background (reporting, daily aggregates)
#   1 hour         - slow reports
ALLOWED_RATES_MS = (100, 250, 500, 1000, 5000, 10000, 30000,
                    60000, 300000, 900000, 3600000)


# Existing taxonomy rows whose is_evaluable flag flips to true.
# Their Python classes ship in this same drop under
# app/workers/calc_blocks/aggregation_tier_a.py.
FLIP_EVALUABLE = ['AVG_OF', 'MIN_OF', 'MAX_OF']


# New block types to add. Ranks 5-9 and 11-17 (skip 10 = IF_THEN_ELSE
# moved away by this migration).
#
# Format: (code, label, category, rank, description)
NEW_BLOCK_TYPES = [
    ('MEDIAN_OF', 'Median Of', 'aggregation', 5,
     'Median (50th percentile) of inputs. NIST/SEMATECH eHB sect 1.3.5.4.'),
    ('MODE_OF', 'Mode Of', 'aggregation', 6,
     'Most-frequent value among inputs. Returns smallest value on ties.'),
    ('RANGE_OF', 'Range Of', 'aggregation', 7,
     'Max minus min. NIST/SEMATECH eHB sect 1.3.5.3.'),
    ('STDDEV_OF', 'Standard Deviation Of', 'aggregation', 8,
     'Sample standard deviation (n-1 divisor). Per ASME PTC 19.1 sect 4.3.'),
    ('VARIANCE_OF', 'Variance Of', 'aggregation', 9,
     'Sample variance (n-1 divisor). Per ASME PTC 19.1 sect 4.3.'),
    ('PRODUCT_OF', 'Product Of', 'aggregation', 11,
     'Product of all inputs. IEC 61131-3 MUL extended to N inputs.'),
    ('GEOMETRIC_MEAN', 'Geometric Mean', 'aggregation', 12,
     'Nth root of product of inputs. All inputs must be positive.'),
    ('HARMONIC_MEAN', 'Harmonic Mean', 'aggregation', 13,
     'N divided by sum of reciprocals. All inputs must be non-zero.'),
    ('WEIGHTED_AVG', 'Weighted Average', 'aggregation', 14,
     'Sum(w_i * x_i) / Sum(w_i). Weights in block_config.weights.'),
    ('RMS_OF', 'Root Mean Square', 'aggregation', 15,
     'Sqrt of mean of squares. Used in power, signal processing.'),
    ('COUNT_GOOD', 'Count GOOD Inputs', 'aggregation', 16,
     'Number of inputs with quality >= 128 (GOOD or better).'),
    ('COUNT_NONZERO', 'Count Non-Zero Inputs', 'aggregation', 17,
     'Number of inputs with value != 0 regardless of quality.'),
]


def upgrade():
    # ---- 0. Defensive block_type column repair ---------------------------
    # In some environments, calc_definitions was created without
    # block_type because alembic_version was already at 0034 when
    # the column was added to migration 0034's source. Alembic
    # doesn't re-run a migration whose revision already exists in
    # alembic_version. ADD COLUMN IF NOT EXISTS is a no-op when the
    # column is present, and a clean add when it's missing.
    #
    # The DEFAULT 'UNKNOWN' lets NOT NULL apply cleanly even if there
    # were rows; we immediately drop the default so future INSERTs
    # must specify block_type explicitly (caught at API layer).
    op.execute("""
        ALTER TABLE calc_definitions
        ADD COLUMN IF NOT EXISTS block_type VARCHAR(64) NOT NULL
            DEFAULT 'UNKNOWN'
    """)
    op.execute("""
        ALTER TABLE calc_definitions
        ALTER COLUMN block_type DROP DEFAULT
    """)

    # ---- 1. execution_rate_ms column on calc_definitions -----------------
    op.execute("""
        ALTER TABLE calc_definitions
        ADD COLUMN execution_rate_ms INTEGER NOT NULL DEFAULT 1000
    """)

    # CHECK constraint enforces the industrial-standard rate set.
    # calc_definitions is a normal table (not a hypertable), so
    # ADD CONSTRAINT works without the TimescaleDB NotSupportedError
    # we hit on tag_values yesterday.
    rates_csv = ', '.join(str(r) for r in ALLOWED_RATES_MS)
    op.execute(f"""
        ALTER TABLE calc_definitions
        ADD CONSTRAINT ck_calc_def_execution_rate
        CHECK (execution_rate_ms IN ({rates_csv}))
    """)

    # ---- 2. calc_execution_stats sidecar table ---------------------------
    op.create_table(
        "calc_execution_stats",
        sa.Column("calc_def_id", sa.Integer, primary_key=True),
        sa.Column("last_executed_at", sa.DateTime(timezone=True)),
        sa.Column("last_duration_ms", sa.Float),
        sa.Column("last_status", sa.String(16), nullable=False,
                  server_default=sa.text("'pending'")),
        sa.Column("last_error_message", sa.Text),
        sa.Column("next_scheduled_at", sa.DateTime(timezone=True)),
        sa.Column("consecutive_overruns", sa.Integer, nullable=False,
                  server_default=sa.text("0")),
        sa.Column("consecutive_errors", sa.Integer, nullable=False,
                  server_default=sa.text("0")),
        sa.Column("total_executions", sa.BigInteger, nullable=False,
                  server_default=sa.text("0")),
        sa.Column("total_overruns", sa.BigInteger, nullable=False,
                  server_default=sa.text("0")),
        sa.Column("total_errors", sa.BigInteger, nullable=False,
                  server_default=sa.text("0")),
        sa.Column("total_skips", sa.BigInteger, nullable=False,
                  server_default=sa.text("0")),
    )
    op.create_check_constraint(
        "ck_calc_exec_stats_status",
        "calc_execution_stats",
        "last_status IN ('pending', 'ok', 'overrun', 'error', 'killed')",
    )
    op.create_foreign_key(
        "calc_execution_stats_def_id_fk",
        "calc_execution_stats", "calc_definitions",
        ["calc_def_id"], ["id"],
        ondelete="CASCADE",
    )

    # ---- 3. Catalog updates ----------------------------------------------
    # Move IF_THEN_ELSE to rank 100 to free up the aggregation range.
    # Rank is UNIQUE; moving to a value not currently in use is fine.
    op.execute("UPDATE calc_block_types SET rank = 100 WHERE code = 'IF_THEN_ELSE'")

    # Flip existing aggregation taxonomy entries to evaluable.
    flip_codes = ', '.join(repr(c) for c in FLIP_EVALUABLE)
    op.execute(f"""
        UPDATE calc_block_types
        SET is_evaluable = true
        WHERE code IN ({flip_codes})
    """)

    # Insert new aggregation blocks (all is_evaluable=true since their
    # Python classes ship in this drop).
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
    # Remove new block types
    new_codes = ', '.join(repr(r[0]) for r in NEW_BLOCK_TYPES)
    op.execute(f"DELETE FROM calc_block_types WHERE code IN ({new_codes})")

    # Restore IF_THEN_ELSE rank
    op.execute("UPDATE calc_block_types SET rank = 10 WHERE code = 'IF_THEN_ELSE'")

    # Flip back to taxonomy-only
    flip_codes = ', '.join(repr(c) for c in FLIP_EVALUABLE)
    op.execute(f"""
        UPDATE calc_block_types
        SET is_evaluable = false
        WHERE code IN ({flip_codes})
    """)

    op.drop_constraint(
        "calc_execution_stats_def_id_fk",
        "calc_execution_stats", type_="foreignkey",
    )
    op.drop_table("calc_execution_stats")

    op.execute("""
        ALTER TABLE calc_definitions
        DROP CONSTRAINT IF EXISTS ck_calc_def_execution_rate
    """)
    op.execute("ALTER TABLE calc_definitions DROP COLUMN execution_rate_ms")
