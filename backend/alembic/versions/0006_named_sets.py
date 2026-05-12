"""Phase 8.3 — named_sets master + named_set_values + tag FK

Adds a reusable lookup-table system for translating raw register values
into human-readable text. Examples:

  Tag value 1 → "Running" (if the tag uses the MOTOR_STATE named set)
  Tag value 0 → "Closed"  (if the tag uses the OPEN_CLOSED named set)

The raw value is still stored as the CV; the named set only changes how
the value is displayed in dashboards, reports, trends, and diagnostics.

Schema:
  named_sets        — the master (name, description, is_system, enabled)
  named_set_values  — value→text rows, cascade-deleted with the parent
  tags.named_set_id — optional FK; SET NULL on delete

Seed data: 26 starter sets covering common boolean and integer patterns
used across Chemical, Oil & Gas, Water, and Pharma. Boolean sets (2 values)
and integer sets (3+ values) are mixed; the UI filters by data_type when
narrowing the dropdown for a specific tag.

Tags using float/double types should NOT have a named_set assigned —
that's an API-level validation, not a DB constraint, because a tag's
data_type can change between integer types and we don't want to break
existing FKs over a schema technicality.
"""
from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "0006_named_sets"
down_revision: Union[str, None] = "0005_engineering_units"
branch_labels = None
depends_on = None


# ---------------------------------------------------------------------------
# Seed data — (set_name, description, [(raw_value, display_text, display_order), ...])
# ---------------------------------------------------------------------------
SEED_SETS: list[tuple[str, str, list[tuple[int, str, int]]]] = [
    # ============ Boolean (2-value) sets — common in discrete I/O ============
    ("ON_OFF", "Generic on/off discrete state",
     [(0, "OFF", 0), (1, "ON", 1)]),
    ("OPEN_CLOSED", "Valve / damper / breaker position",
     [(0, "Closed", 0), (1, "Open", 1)]),
    ("RUN_STOP", "Motor / pump running state",
     [(0, "Stopped", 0), (1, "Running", 1)]),
    ("NORMAL_TRIPPED", "Protective trip status",
     [(0, "Normal", 0), (1, "Tripped", 1)]),
    ("NORMAL_ALARM", "Generic alarm flag",
     [(0, "Normal", 0), (1, "Alarm", 1)]),
    ("HEALTHY_FAULT", "Equipment health flag",
     [(0, "Healthy", 0), (1, "Fault", 1)]),
    ("ENABLED_DISABLED", "Enable/disable flag",
     [(0, "Disabled", 0), (1, "Enabled", 1)]),
    ("MAN_AUTO", "Control mode: manual or automatic",
     [(0, "Manual", 0), (1, "Auto", 1)]),
    ("LOCAL_REMOTE", "Control location: local panel or remote",
     [(0, "Local", 0), (1, "Remote", 1)]),
    ("READY_NOT_READY", "System readiness flag",
     [(0, "Not Ready", 0), (1, "Ready", 1)]),
    ("ACTIVE_INACTIVE", "Activity flag",
     [(0, "Inactive", 0), (1, "Active", 1)]),
    ("PRESENT_ABSENT", "Material presence sensor",
     [(0, "Absent", 0), (1, "Present", 1)]),
    ("VALID_INVALID", "Data validity flag",
     [(0, "Invalid", 0), (1, "Valid", 1)]),
    ("ACK_NACK", "Alarm acknowledgement",
     [(0, "Not Acknowledged", 0), (1, "Acknowledged", 1)]),
    ("SAFE_UNSAFE", "Safety interlock state",
     [(0, "Unsafe", 0), (1, "Safe", 1)]),
    ("HI_LO", "High/low threshold crossing",
     [(0, "Low", 0), (1, "High", 1)]),

    # ============ Integer (multi-value) sets — typical state machines ============
    ("MOTOR_STATE", "Motor state machine — typical PLC pattern",
     [(0, "Stopped", 0), (1, "Starting", 1), (2, "Running", 2),
      (3, "Stopping", 3), (4, "Tripped", 4), (5, "Maintenance", 5)]),
    ("VALVE_STATE", "Valve position state machine with intermediate states",
     [(0, "Closed", 0), (1, "Opening", 1), (2, "Open", 2),
      (3, "Closing", 3), (4, "Partial", 4), (5, "Stuck", 5)]),
    ("PUMP_STATE", "Pump state machine including duty/standby logic",
     [(0, "Stopped", 0), (1, "Starting", 1), (2, "Running", 2),
      (3, "Stopping", 3), (4, "Tripped", 4), (5, "Standby", 5)]),
    ("ALARM_SEVERITY", "Standard six-level alarm priority",
     [(0, "None", 0), (1, "Info", 1), (2, "Low", 2),
      (3, "Medium", 3), (4, "High", 4), (5, "Critical", 5)]),
    ("CONTROL_MODE", "PID controller mode (manual / auto / cascade / remote / local)",
     [(0, "Manual", 0), (1, "Auto", 1), (2, "Cascade", 2),
      (3, "Remote", 3), (4, "Local", 4)]),
    ("BATCH_STATE", "ISA-88 batch state model",
     [(0, "Idle", 0), (1, "Running", 1), (2, "Held", 2),
      (3, "Restarting", 3), (4, "Stopping", 4),
      (5, "Aborting", 5), (6, "Complete", 6)]),
    ("FLOW_DIRECTION", "Flow direction indicator",
     [(0, "None", 0), (1, "Forward", 1), (2, "Reverse", 2)]),
    ("POSITION_STATE", "Multi-position indicator",
     [(0, "Unknown", 0), (1, "Position A", 1),
      (2, "Position B", 2), (3, "Intermediate", 3)]),
    ("SHIFT", "Work shift designator",
     [(0, "Off shift", 0), (1, "Morning", 1), (2, "Day", 2),
      (3, "Evening", 3), (4, "Night", 4)]),
    ("WEEKDAY", "Day of week (Sunday=0 convention)",
     [(0, "Sunday", 0), (1, "Monday", 1), (2, "Tuesday", 2),
      (3, "Wednesday", 3), (4, "Thursday", 4),
      (5, "Friday", 5), (6, "Saturday", 6)]),
]


def upgrade() -> None:
    # ---------------------------- named_sets ---------------------------------
    op.create_table(
        "named_sets",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(128), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.text("FALSE")),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("TRUE")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("NOW()")),
    )

    # ------------------------- named_set_values ------------------------------
    # CASCADE delete: if a named_set is removed, its values go with it.
    # UNIQUE on (named_set_id, raw_value) prevents two rows mapping the same
    # integer to different labels within the same set.
    op.create_table(
        "named_set_values",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "named_set_id",
            sa.BigInteger(),
            sa.ForeignKey("named_sets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("raw_value", sa.Integer(), nullable=False),
        sa.Column("display_text", sa.String(128), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("color", sa.String(16), nullable=True,
                  comment="Optional CSS color hint for UI (e.g. 'red', '#ef4444')"),
        sa.UniqueConstraint("named_set_id", "raw_value",
                            name="uq_named_set_values_set_value"),
    )
    op.create_index(
        "ix_named_set_values_set",
        "named_set_values",
        ["named_set_id", "display_order"],
    )

    # ---------------------- seed the master with starter library -------------
    for set_name, description, values in SEED_SETS:
        ns_id = op.get_bind().execute(sa.text("""
            INSERT INTO named_sets (name, description, is_system)
            VALUES (:name, :description, TRUE)
            RETURNING id
        """), {"name": set_name, "description": description}).scalar_one()

        for raw, text_, order in values:
            op.get_bind().execute(sa.text("""
                INSERT INTO named_set_values
                    (named_set_id, raw_value, display_text, display_order)
                VALUES (:ns_id, :raw, :text, :order)
            """), {"ns_id": ns_id, "raw": raw, "text": text_, "order": order})

    # ------------------------- tags.named_set_id -----------------------------
    op.add_column(
        "tags",
        sa.Column("named_set_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_tags_named_set",
        "tags",
        "named_sets",
        ["named_set_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_tags_named_set_id", "tags", ["named_set_id"])


def downgrade() -> None:
    op.drop_index("ix_tags_named_set_id", table_name="tags")
    op.drop_constraint("fk_tags_named_set", "tags", type_="foreignkey")
    op.drop_column("tags", "named_set_id")
    op.drop_index("ix_named_set_values_set", table_name="named_set_values")
    op.drop_table("named_set_values")
    op.drop_table("named_sets")
