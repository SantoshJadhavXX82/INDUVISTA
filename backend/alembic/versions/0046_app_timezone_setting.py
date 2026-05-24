"""Phase 27d MVP — Seed app.timezone in system_settings.

Revision ID: 0046_app_timezone_setting
Revises: 0045_tsdb_compression
Create Date: 2026-05-23

The system_settings table already exists from Phase 12.2 (used by
duty/standby). This migration only seeds the initial timezone row so
fresh installs and existing deployments both have a defined value.

Subsequent updates to this row happen via PATCH /api/settings; the
worker / API picks up the change within 60 seconds (the helper's
lru_cache TTL — see app/utils/timezone.py).

The row's value is Asia/Kolkata initially (matches the default in
config.py / .env). Operators change it via the UI Settings page.
"""

from alembic import op


# revision identifiers, used by Alembic
revision = "0046_app_timezone_setting"
down_revision = "0045_tsdb_compression"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Idempotent seed: only inserts if the row doesn't exist. Existing
    # deployments that already have an app.timezone row (e.g. from a
    # hotfix) keep their current value.
    op.execute("""
        INSERT INTO system_settings (key, value, updated_at)
        VALUES ('app.timezone', 'Asia/Kolkata', NOW())
        ON CONFLICT (key) DO NOTHING;
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM system_settings WHERE key = 'app.timezone';
    """)
