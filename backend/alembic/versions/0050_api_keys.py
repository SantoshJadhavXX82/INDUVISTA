"""Phase OPC.1 — API keys for external integrators.

Revision ID: 0050_api_keys
Revises: 0049_tag_decimal_places
Create Date: 2026-05-24

Adds an api_keys table for authenticating external clients that push
samples into INDUVISTA via the generic /api/ingest endpoint. Designed
to be source-agnostic — same table serves the future InduVista OPC
Client, custom Python pushers, MQTT bridges, anything else.

KEY HASH STRATEGY
=================

  Raw keys are SHA-256 hashed before storage. SHA-256 (not bcrypt)
  because API keys are 32 bytes / 256 bits of random data; brute-forcing
  them takes 2^256 attempts regardless of hash function, so the
  slow-hash protection bcrypt offers is wasted. SHA-256 gives O(1)
  lookup via a unique index, ~10us per verify, vs bcrypt's ~100ms.

  key_prefix stores the first 12 chars of the raw key (e.g. "inv_abc12345")
  for display purposes only. SHA-256 is one-way so the full key cannot
  be recovered after creation; the prefix is what admins see in
  /api/admin/api-keys list responses to identify which key is which.

ALLOWED TAG IDS
===============

  Per-key allowlist for fine-grained permission. NULL = client can push
  to any tag. An array of tag IDs restricts the client to those tags
  only — ingest rejects samples for other tags. Useful for multi-tenant
  setups where Plant A's OPC client shouldn't touch Plant B's tags.

LAST USED TRACKING
==================

  last_used_at and last_used_ip are updated on every successful ingest
  call. Useful for diagnosing "is this client still alive" and for audit
  trails. The update is best-effort (failure doesn't fail the ingest
  request itself).
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic
revision = "0050_api_keys"
down_revision = "0049_tag_decimal_places"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS api_keys (
            id              SERIAL PRIMARY KEY,
            client_name     TEXT UNIQUE NOT NULL,
            key_hash        TEXT UNIQUE NOT NULL,
            key_prefix      TEXT NOT NULL,
            description     TEXT,
            is_enabled      BOOLEAN NOT NULL DEFAULT TRUE,
            allowed_tag_ids INTEGER[] DEFAULT NULL,
            last_used_at    TIMESTAMPTZ,
            last_used_ip    INET,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            created_by      TEXT
        );
    """)

    # Partial index on enabled keys — ingest auth path queries this
    # constantly, and disabled keys are typically <1% of rows. Skipping
    # them in the index makes lookups touch only live entries.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_api_keys_hash_enabled
        ON api_keys(key_hash) WHERE is_enabled = TRUE;
    """)

    # Convenience index for the admin list view (sorted by creation).
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_api_keys_created_at
        ON api_keys(created_at DESC);
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_api_keys_created_at;")
    op.execute("DROP INDEX IF EXISTS idx_api_keys_hash_enabled;")
    op.execute("DROP TABLE IF EXISTS api_keys;")
