"""Server clock drift telemetry for OPC sources.

Phase OPC-web.2.3 — server clock validation.

Background:
  Migration 0055 added a per-source trust_server_timestamp toggle so
  operators can opt-in to using the OPC server's DataValue.SourceTimestamp
  for sources with verified clock sync. But "verified" was an unaided
  human judgment — we did not give operators a way to actually MEASURE
  the OPC server's clock against the worker's clock.

  This migration adds two columns the worker populates on each
  subscription activation:
    - last_server_clock_drift_sec: signed seconds offset
                                   (server_time - worker_time)
    - last_server_clock_check_at: when the drift was measured

  The API surfaces both in OpcSourceResponse, and the frontend's
  edit modal displays the drift inline with a warning when the
  absolute value exceeds 60 seconds — letting the operator make an
  informed choice between "Use worker time (default)" vs "Use server
  SourceTimestamp".

  Defaults are NULL: no measurement yet. The worker writes them on
  first successful subscription, then again whenever it reconnects.

Revision ID: 0056_opc_server_clock_drift
Revises: 0055_opc_trust_server_timestamp
Create Date: 2026-05-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0056_opc_server_clock_drift"
down_revision = "0055_opc_trust_server_timestamp"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "opc_sources",
        sa.Column(
            "last_server_clock_drift_sec",
            sa.Float(),
            nullable=True,
            comment=(
                "Signed offset (server_time - worker_time) in seconds, "
                "measured by the worker on each subscription activation "
                "via Server_ServerStatus_CurrentTime. NULL = never measured. "
                "See migration 0056."
            ),
        ),
    )
    op.add_column(
        "opc_sources",
        sa.Column(
            "last_server_clock_check_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
            comment=(
                "When last_server_clock_drift_sec was measured. NULL = never. "
                "See migration 0056."
            ),
        ),
    )


def downgrade() -> None:
    op.drop_column("opc_sources", "last_server_clock_check_at")
    op.drop_column("opc_sources", "last_server_clock_drift_sec")
