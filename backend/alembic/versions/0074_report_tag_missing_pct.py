"""allow 'missing_pct' data_function on report_tags

Phase D-prep. report_aggregate gained a `missing_pct` data function (the exact
complement of `availability` — percent of the window's samples that are not
Good, 100% when the window is empty), but report_tags still carried the CHECK
from 0069 that only permitted up to `availability`, so binding a tag with
data_function='missing_pct' failed at the database. Widen the CHECK to match
app.services.report_aggregate.DATA_FUNCTIONS.

Revision ID: 0074_report_tag_missing_pct
Revises: 0073_users_role_approver
"""
from alembic import op


revision = "0074_report_tag_missing_pct"
down_revision = "0073_users_role_approver"
branch_labels = None
depends_on = None

_DF_NEW = ("data_function IN ('latest','first','last','average','min','max',"
           "'sum','count','delta','availability','missing_pct')")
_DF_OLD = ("data_function IN ('latest','first','last','average','min','max',"
           "'sum','count','delta','availability')")


def upgrade() -> None:
    op.drop_constraint("ck_rt_data_function", "report_tags", type_="check")
    op.create_check_constraint("ck_rt_data_function", "report_tags", _DF_NEW)


def downgrade() -> None:
    # Fold any missing_pct bindings to availability so the narrower CHECK holds.
    op.execute("UPDATE report_tags SET data_function = 'availability' "
               "WHERE data_function = 'missing_pct'")
    op.drop_constraint("ck_rt_data_function", "report_tags", type_="check")
    op.create_check_constraint("ck_rt_data_function", "report_tags", _DF_OLD)
