"""users.can_audit — per-user read-only audit-log capability

The audit log (GET /api/audit-log) was readable by any authenticated user.
This adds an 'auditor' capability: a per-user boolean that, together with the
admin role, is now required to read the audit log. An auditor is typically a
viewer (read-only base role) with can_audit = TRUE. The flag is carried as a
JWT claim, so granting/revoking takes effect on the user's next login.

Revision ID: 0076_users_can_audit
Revises: 0075_report_batches
"""
from alembic import op
import sqlalchemy as sa


revision = "0076_users_can_audit"
down_revision = "0075_report_batches"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("can_audit", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("users", "can_audit")
