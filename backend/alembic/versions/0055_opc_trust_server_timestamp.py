"""Per-source flag: trust the OPC UA server's SourceTimestamp.

Phase OPC-web.2.2 — defensive timestamp handling.

Background:
  The AGG SoftBus OPC simulator on the Windows host delivers
  DataValue.SourceTimestamp values that are tagged tzinfo=UTC but
  actually carry local-wall-clock (IST, +5h30m drift) — confirmed
  via direct asyncua probe on 2026-05-27. The Server_ServerStatus_
  CurrentTime node returns correct UTC; only DataValue timestamps
  are shifted. This is a simulator misconfig we don't control.

  Today's hard-fix discarded server timestamps for all OPC UA
  sources globally. This migration restores per-source choice: by
  default new sources still ignore server timestamps (safe), but
  operators can opt-in for production OPC servers with verified
  clock sync.

Default = FALSE because:
  - Plant-A-UA (the AGG simulator) must stay safe
  - New sources are usually simulators or unknown — defensive default
  - Production servers can be flipped explicitly via the API or UI

Revision ID: 0055_opc_trust_server_timestamp
Revises: 0054_devices_soft_delete
Create Date: 2026-05-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0055_opc_trust_server_timestamp"
down_revision = "0054_devices_soft_delete"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "opc_sources",
        sa.Column(
            "trust_server_timestamp",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("FALSE"),
            comment=(
                "If TRUE, worker uses DataValue.SourceTimestamp for ingested "
                "samples; if FALSE (default), worker uses ingest-time UTC. "
                "FALSE is the safe choice for simulators or untrusted servers. "
                "See migration 0055."
            ),
        ),
    )


def downgrade() -> None:
    op.drop_column("opc_sources", "trust_server_timestamp")
