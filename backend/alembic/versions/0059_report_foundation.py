"""Reporting foundation — report definitions, triggers, destinations (Phase 20.x).

Implements the DanPac/DanPacUI reporting model: three orthogonal concepts that
combine to produce reports —

  report_definitions   WHAT a report is: name, category (event/periodic/
                       on_demand), a Jinja2 HTML template (the body), and
                       page/layout metadata. Configured in one place.

  report_triggers      WHEN a report fires:
                         - 'timed'  → a cron-like schedule (hourly/daily/
                           monthly/yearly, contract hour, etc.)  → category B
                         - 'tag'    → fires when a tag transitions to non-zero
                           (an S600+ event/status bit)            → category A
                       Triggers are GLOBAL (reusable, owner_report_id NULL) or
                       CUSTOM (private to one report, owner_report_id set).

  report_destinations  WHERE a rendered report goes: file folder / network
                       drive / printer. Global, reusable across reports.

Many-to-many wiring (a report uses N triggers + N destinations; a global
trigger/destination serves many reports):

  report_trigger_links      (report_id, trigger_id)
  report_destination_links  (report_id, destination_id, fmt)

On-demand "Current" reports (category C) need no trigger row — the API renders
them immediately. Event/periodic reports are driven by the trigger engine
(built next).

This migration only creates the schema + seeds a few example timed triggers and
a local-archive destination. No behavior changes until the trigger engine and
render pipeline are added. Fully reversible.
"""
from alembic import op
import sqlalchemy as sa


revision = "0059_report_foundation"
down_revision = "0058_tag_logging_config"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ----------------------------------------------------------------- defs
    op.create_table(
        "report_definitions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(120), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=True),
        # 'event' (A), 'periodic' (B), 'on_demand' (C)
        sa.Column("category", sa.String(16), nullable=False,
                  server_default="periodic"),
        # Optional sub-type for known flow-computer reports (gc, calibration,
        # verification, proving, batch, meter_verification, hourly, daily,
        # monthly, yearly, current). Free-form; UI offers known ones.
        sa.Column("report_type", sa.String(32), nullable=True),
        # The Jinja2 HTML template (the report body). Rendered with a context
        # built from the bound tags at trigger time, then HTML->PDF.
        sa.Column("template_html", sa.Text(), nullable=False,
                  server_default=""),
        # Page setup for WeasyPrint (A4/Letter, portrait/landscape, margins).
        sa.Column("page_size", sa.String(16), nullable=False,
                  server_default="A4"),
        sa.Column("orientation", sa.String(16), nullable=False,
                  server_default="portrait"),
        sa.Column("enabled", sa.Boolean(), nullable=False,
                  server_default=sa.text("TRUE")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_check_constraint(
        "ck_report_def_category",
        "report_definitions",
        "category IN ('event', 'periodic', 'on_demand')",
    )
    op.create_check_constraint(
        "ck_report_def_orientation",
        "report_definitions",
        "orientation IN ('portrait', 'landscape')",
    )
    op.create_index(
        "ix_report_definitions_category_enabled",
        "report_definitions", ["category", "enabled"],
    )

    # ------------------------------------------------------------- triggers
    op.create_table(
        "report_triggers",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        # 'timed' | 'tag'
        sa.Column("trigger_type", sa.String(16), nullable=False),
        # GLOBAL when NULL; CUSTOM (private to one report) when set.
        sa.Column("owner_report_id", sa.BigInteger(), nullable=True),
        # --- timed config ---
        # Period granularity: 'hourly'|'daily'|'monthly'|'yearly'|'custom_cron'
        sa.Column("period", sa.String(16), nullable=True),
        # Minutes past the hour (hourly), or HH:MM contract time stored as
        # minute-of-day for daily/monthly/yearly. Kept simple + explicit.
        sa.Column("at_minute", sa.Integer(), nullable=True),     # 0-59 for hourly
        sa.Column("at_time_min", sa.Integer(), nullable=True),   # 0-1439 minute-of-day
        sa.Column("day_of_month", sa.Integer(), nullable=True),  # monthly (1-28)
        sa.Column("month_of_year", sa.Integer(), nullable=True), # yearly (1-12)
        sa.Column("cron_expr", sa.String(120), nullable=True),   # escape hatch
        # --- tag config ---
        sa.Column("tag_id", sa.BigInteger(), nullable=True),
        # Edge to fire on: 'to_nonzero' (default, DanPac semantics),
        # 'rising' (0->1), 'any_change'.
        sa.Column("tag_edge", sa.String(16), nullable=True,
                  server_default="to_nonzero"),
        sa.Column("enabled", sa.Boolean(), nullable=False,
                  server_default=sa.text("TRUE")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_foreign_key(
        "fk_report_triggers_owner", "report_triggers",
        "report_definitions", ["owner_report_id"], ["id"], ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_report_triggers_tag", "report_triggers",
        "tags", ["tag_id"], ["id"], ondelete="CASCADE",
    )
    op.create_check_constraint(
        "ck_report_trigger_type",
        "report_triggers",
        "trigger_type IN ('timed', 'tag')",
    )
    op.create_check_constraint(
        "ck_report_trigger_tag_needs_tagid",
        "report_triggers",
        "trigger_type <> 'tag' OR tag_id IS NOT NULL",
    )
    op.create_check_constraint(
        "ck_report_trigger_timed_needs_period",
        "report_triggers",
        "trigger_type <> 'timed' OR period IS NOT NULL",
    )
    op.create_check_constraint(
        "ck_report_trigger_edge",
        "report_triggers",
        "tag_edge IN ('to_nonzero', 'rising', 'any_change')",
    )
    op.create_index(
        "ix_report_triggers_type_enabled",
        "report_triggers", ["trigger_type", "enabled"],
    )
    # Global trigger names should be unique; custom ones are scoped per-report.
    op.create_index(
        "ux_report_triggers_global_name",
        "report_triggers", ["name"],
        unique=True,
        postgresql_where=sa.text("owner_report_id IS NULL"),
    )

    # --------------------------------------------------------- destinations
    op.create_table(
        "report_destinations",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(120), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=True),
        # 'folder' | 'network_drive' | 'printer'
        sa.Column("dest_type", sa.String(16), nullable=False),
        # For folder/network: the path. For printer: the printer name/URI.
        sa.Column("target", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False,
                  server_default=sa.text("TRUE")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_check_constraint(
        "ck_report_dest_type",
        "report_destinations",
        "dest_type IN ('folder', 'network_drive', 'printer')",
    )

    # ----------------------------------------------------- link (M:N) tables
    op.create_table(
        "report_trigger_links",
        sa.Column("report_id", sa.BigInteger(), nullable=False),
        sa.Column("trigger_id", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("report_id", "trigger_id"),
    )
    op.create_foreign_key(
        "fk_rtl_report", "report_trigger_links",
        "report_definitions", ["report_id"], ["id"], ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_rtl_trigger", "report_trigger_links",
        "report_triggers", ["trigger_id"], ["id"], ondelete="CASCADE",
    )

    op.create_table(
        "report_destination_links",
        sa.Column("report_id", sa.BigInteger(), nullable=False),
        sa.Column("destination_id", sa.BigInteger(), nullable=False),
        # Output format for THIS report at THIS destination ('pdf'|'csv').
        sa.Column("fmt", sa.String(8), nullable=False, server_default="pdf"),
        sa.PrimaryKeyConstraint("report_id", "destination_id"),
    )
    op.create_foreign_key(
        "fk_rdl_report", "report_destination_links",
        "report_definitions", ["report_id"], ["id"], ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_rdl_dest", "report_destination_links",
        "report_destinations", ["destination_id"], ["id"], ondelete="CASCADE",
    )
    op.create_check_constraint(
        "ck_rdl_fmt", "report_destination_links",
        "fmt IN ('pdf', 'csv')",
    )

    # ---------------------------------------------- report records (history)
    # Every generated report is recorded here (audit + retrieval). The rendered
    # bytes can live on disk (the destination) but we keep the metadata + an
    # optional inline copy for the local archive.
    op.create_table(
        "report_records",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("report_id", sa.BigInteger(), nullable=True),
        sa.Column("report_name", sa.String(120), nullable=False),
        sa.Column("category", sa.String(16), nullable=False),
        sa.Column("trigger_id", sa.BigInteger(), nullable=True),
        sa.Column("trigger_kind", sa.String(16), nullable=True),  # timed|tag|manual
        # The instant the report's data snapshot was taken (UTC).
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("NOW()")),
        sa.Column("fmt", sa.String(8), nullable=False, server_default="pdf"),
        sa.Column("file_path", sa.Text(), nullable=True),   # where it was written
        sa.Column("byte_size", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False,
                  server_default="ok"),                      # ok|error
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_foreign_key(
        "fk_report_records_def", "report_records",
        "report_definitions", ["report_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index(
        "ix_report_records_report_time",
        "report_records", ["report_id", "generated_at"],
    )
    op.create_index(
        "ix_report_records_generated",
        "report_records", ["generated_at"],
    )

    # ------------------------------------------------------------- seed data
    # A local-archive destination + the canonical periodic triggers, so the
    # UI has sensible starting points. All disabled-by-default? No — triggers
    # are enabled but harmless until a report is linked AND the engine runs.
    op.bulk_insert(
        sa.table(
            "report_destinations",
            sa.column("name", sa.String),
            sa.column("description", sa.Text),
            sa.column("dest_type", sa.String),
            sa.column("target", sa.Text),
        ),
        [{
            "name": "Local Archive",
            "description": "Default on-disk archive for generated reports.",
            "dest_type": "folder",
            "target": "/var/lib/induvista/reports",
        }],
    )
    op.bulk_insert(
        sa.table(
            "report_triggers",
            sa.column("name", sa.String),
            sa.column("description", sa.Text),
            sa.column("trigger_type", sa.String),
            sa.column("period", sa.String),
            sa.column("at_minute", sa.Integer),
            sa.column("at_time_min", sa.Integer),
            sa.column("day_of_month", sa.Integer),
            sa.column("month_of_year", sa.Integer),
        ),
        [
            {"name": "Hourly (on the hour)", "description": "Every hour at :00.",
             "trigger_type": "timed", "period": "hourly", "at_minute": 0,
             "at_time_min": None, "day_of_month": None, "month_of_year": None},
            {"name": "Daily (contract 06:00)", "description": "Every day at 06:00 local.",
             "trigger_type": "timed", "period": "daily", "at_minute": None,
             "at_time_min": 360, "day_of_month": None, "month_of_year": None},
            {"name": "Monthly (1st 06:00)", "description": "1st of each month at 06:00.",
             "trigger_type": "timed", "period": "monthly", "at_minute": None,
             "at_time_min": 360, "day_of_month": 1, "month_of_year": None},
            {"name": "Yearly (Jan 1 06:00)", "description": "Jan 1 at 06:00.",
             "trigger_type": "timed", "period": "yearly", "at_minute": None,
             "at_time_min": 360, "day_of_month": 1, "month_of_year": 1},
        ],
    )


def downgrade() -> None:
    op.drop_table("report_records")
    op.drop_table("report_destination_links")
    op.drop_table("report_trigger_links")
    op.drop_table("report_destinations")
    op.drop_table("report_triggers")
    op.drop_table("report_definitions")
