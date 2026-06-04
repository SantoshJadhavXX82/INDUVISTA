"""report_tags binding columns (data function, quality rule, units, format)

Phase A2 of the Reports roadmap: evolve report_tags from a bare tag reference
into a data binding. All columns are additive with defaults that preserve
today's behavior (data_function='latest', quality_rule='all'), so existing rows
and the existing tag endpoints are unaffected. Dormant until the aggregation
wiring (A3) reads them.

Also cleans up a cosmetic artifact from 0068: report_period_rule.report_id is an
FK PK, not an identity column, so it should not carry an autoincrement sequence
default.

CHECK value sets MUST match app.services.report_aggregate constants.

Revision ID: 0069_report_binding_columns
Revises: 0068_report_period_rule
"""
from alembic import op
import sqlalchemy as sa


revision = "0069_report_binding_columns"
down_revision = "0068_report_period_rule"
branch_labels = None
depends_on = None


_BINDING_COLUMNS = [
    ("alias", sa.Column("alias", sa.String(64), nullable=True)),
    ("display_name", sa.Column("display_name", sa.String(120), nullable=True)),
    ("unit_id", sa.Column("unit_id", sa.BigInteger(), nullable=True)),
    ("data_function", sa.Column("data_function", sa.String(16), nullable=False,
                                server_default="latest")),
    ("quality_rule", sa.Column("quality_rule", sa.String(16), nullable=False,
                               server_default="all")),
    ("missing_action", sa.Column("missing_action", sa.String(16), nullable=False,
                                 server_default="blank")),
    ("bad_action", sa.Column("bad_action", sa.String(16), nullable=False,
                             server_default="blank")),
    ("decimal_places", sa.Column("decimal_places", sa.Integer(), nullable=True)),
    ("value_format", sa.Column("value_format", sa.String(16), nullable=True)),
    ("low_limit", sa.Column("low_limit", sa.Float(), nullable=True)),
    ("high_limit", sa.Column("high_limit", sa.Float(), nullable=True)),
    ("group_name", sa.Column("group_name", sa.String(64), nullable=True)),
    ("required", sa.Column("required", sa.Boolean(), nullable=False,
                           server_default=sa.text("false"))),
]


def upgrade() -> None:
    for _, col in _BINDING_COLUMNS:
        op.add_column("report_tags", col)

    # Value sets mirror app.services.report_aggregate.
    op.create_check_constraint(
        "ck_rt_data_function", "report_tags",
        "data_function IN ('latest','first','last','average','min','max',"
        "'sum','count','delta','availability')",
    )
    op.create_check_constraint(
        "ck_rt_quality_rule", "report_tags",
        "quality_rule IN ('all','good_only','good_uncertain')",
    )
    op.create_check_constraint(
        "ck_rt_missing_action", "report_tags",
        "missing_action IN ('blank','warning','fail','estimate')",
    )
    op.create_check_constraint(
        "ck_rt_bad_action", "report_tags",
        "bad_action IN ('blank','warning','fail','last_good')",
    )
    op.create_check_constraint(
        "ck_rt_value_format", "report_tags",
        "value_format IS NULL OR value_format IN "
        "('number','text','date','percent','scientific')",
    )
    op.create_foreign_key(
        "fk_report_tags_unit", "report_tags",
        "engineering_units", ["unit_id"], ["id"], ondelete="SET NULL",
    )

    # Cleanup: drop the unintended autoincrement sequence on the FK PK.
    op.alter_column("report_period_rule", "report_id", server_default=None)
    op.execute("DROP SEQUENCE IF EXISTS report_period_rule_report_id_seq")


def downgrade() -> None:
    # Restore the prior (autoincrement) state of report_period_rule.report_id.
    op.execute(
        "CREATE SEQUENCE IF NOT EXISTS report_period_rule_report_id_seq "
        "OWNED BY report_period_rule.report_id"
    )
    op.alter_column(
        "report_period_rule", "report_id",
        server_default=sa.text("nextval('report_period_rule_report_id_seq'::regclass)"),
    )

    op.drop_constraint("fk_report_tags_unit", "report_tags", type_="foreignkey")
    for name in ("ck_rt_value_format", "ck_rt_bad_action", "ck_rt_missing_action",
                 "ck_rt_quality_rule", "ck_rt_data_function"):
        op.drop_constraint(name, "report_tags", type_="check")
    for col_name, _ in reversed(_BINDING_COLUMNS):
        op.drop_column("report_tags", col_name)
