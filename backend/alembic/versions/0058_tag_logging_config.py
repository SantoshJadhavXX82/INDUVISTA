"""Per-tag historian logging configuration (Phase 22).

Until now EVERY successfully-acquired sample was written to tag_values
(history). That works but isn't industrial-historian grade: a 1 Hz tag in
steady state produces 86,400 rows/day even when its value never moves.

This adds per-tag control over WHAT gets written to history. The live value
(latest_tag_values) and alarm evaluation are UNAFFECTED — they always see
every sample. Only the HISTORY write is gated.

COLUMNS
=======
  log_enabled        BOOLEAN   default TRUE
      Master switch. FALSE = never write this tag to history (live value
      still updates; alarms still evaluate). Use for debug/intermediate tags.

  log_mode           VARCHAR   default 'every_sample'
      'every_sample' — write every poll (the historical behavior).
      'on_change'    — write only when the value differs from the last
                       LOGGED value by more than log_deadband.
      'periodic'     — write once every log_interval_sec, value or not.

  log_deadband       FLOAT     default 0.0
      For on_change numeric tags: log only if |new - last_logged| > deadband.
      0.0 = log on ANY change.

  log_deadband_mode  VARCHAR   default 'absolute'
      'absolute' — deadband is in engineering units.
      'percent'  — deadband is a % of (max_value - min_value) range.

  log_interval_sec   INTEGER   nullable
      Force-log: maximum gap between logged samples even if unchanged. Pairs
      with on_change so trends always have anchor points and gaps stay bounded.
      Also the period for 'periodic' mode. NULL = no forced logging.

MIGRATION SAFETY
================
  Every existing tag is set to log_enabled=TRUE, log_mode='every_sample'.
  This is byte-for-byte identical to current behavior. Nothing changes for
  any existing installation until an operator deliberately changes a tag.

Revision ID: 0058_tag_logging_config
Revises: 0057_users_roles
Create Date: 2026-05-29
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0058_tag_logging_config"
down_revision = "0057_users_roles"   # <-- VERIFY this is your real head (see notes)
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tags",
        sa.Column("log_enabled", sa.Boolean(), nullable=False,
                  server_default=sa.text("true")),
    )
    op.add_column(
        "tags",
        sa.Column("log_mode", sa.String(length=16), nullable=False,
                  server_default="every_sample"),
    )
    op.add_column(
        "tags",
        sa.Column("log_deadband", sa.Float(), nullable=False,
                  server_default="0.0"),
    )
    op.add_column(
        "tags",
        sa.Column("log_deadband_mode", sa.String(length=8), nullable=False,
                  server_default="absolute"),
    )
    op.add_column(
        "tags",
        sa.Column("log_interval_sec", sa.Integer(), nullable=True),
    )

    op.create_check_constraint(
        "ck_tags_log_mode",
        "tags",
        "log_mode IN ('every_sample', 'on_change', 'periodic')",
    )
    op.create_check_constraint(
        "ck_tags_log_deadband_nonneg",
        "tags",
        "log_deadband >= 0",
    )
    op.create_check_constraint(
        "ck_tags_log_deadband_mode",
        "tags",
        "log_deadband_mode IN ('absolute', 'percent')",
    )
    op.create_check_constraint(
        "ck_tags_log_interval_positive",
        "tags",
        "log_interval_sec IS NULL OR log_interval_sec > 0",
    )
    # 'periodic' mode is meaningless without an interval; enforce it.
    op.create_check_constraint(
        "ck_tags_periodic_needs_interval",
        "tags",
        "log_mode <> 'periodic' OR log_interval_sec IS NOT NULL",
    )


def downgrade() -> None:
    op.drop_constraint("ck_tags_periodic_needs_interval", "tags", type_="check")
    op.drop_constraint("ck_tags_log_interval_positive", "tags", type_="check")
    op.drop_constraint("ck_tags_log_deadband_mode", "tags", type_="check")
    op.drop_constraint("ck_tags_log_deadband_nonneg", "tags", type_="check")
    op.drop_constraint("ck_tags_log_mode", "tags", type_="check")
    op.drop_column("tags", "log_interval_sec")
    op.drop_column("tags", "log_deadband_mode")
    op.drop_column("tags", "log_deadband")
    op.drop_column("tags", "log_mode")
    op.drop_column("tags", "log_enabled")
