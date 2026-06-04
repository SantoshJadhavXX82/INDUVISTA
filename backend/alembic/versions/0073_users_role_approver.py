"""allow 'approver' role on users

Phase B5. The role ladder gained an `approver` rung (between engineer and
admin), but users.role still carried the original CHECK from 0057 that only
permitted viewer/operator/engineer/admin — so assigning 'approver' failed at
the database. Widen the CHECK to match app.auth.roles.Role.

Revision ID: 0073_users_role_approver
Revises: 0072_report_jobs
"""
from alembic import op


revision = "0073_users_role_approver"
down_revision = "0072_report_jobs"
branch_labels = None
depends_on = None

_ROLES_NEW = "('viewer', 'operator', 'engineer', 'approver', 'admin')"
_ROLES_OLD = "('viewer', 'operator', 'engineer', 'admin')"


def upgrade() -> None:
    op.drop_constraint("ck_users_role", "users", type_="check")
    op.create_check_constraint("ck_users_role", "users", f"role IN {_ROLES_NEW}")


def downgrade() -> None:
    # Any approver users would violate the narrower constraint — fold them down
    # to engineer first so the downgrade is safe.
    op.execute("UPDATE users SET role = 'engineer' WHERE role = 'approver'")
    op.drop_constraint("ck_users_role", "users", type_="check")
    op.create_check_constraint("ck_users_role", "users", f"role IN {_ROLES_OLD}")
