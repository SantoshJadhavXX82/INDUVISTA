-- Phase 12.3 — Pair tags
--
-- A pair_tag is a virtual tag derived from two physical tags that share a
-- name across the two halves of a duty/standby pair. When operators view
-- a pair tag, the displayed value resolves to whichever side is currently
-- duty (per devices.duty_role). When duty changes, the resolution flips
-- automatically — no UI refresh needed beyond the normal polling cycle.
--
-- Auto-generation: when POST /api/devices/{id}/pair completes, the API
-- inserts one pair_tags row per (name, data_type)-matching tag pair across
-- the two devices. POST /unpair deletes them. POST /pair-tags/regenerate
-- exists for the case where tags are added/removed after pairing.
--
-- Idempotent — re-running this script on a DB that already has the table
-- is a no-op.

CREATE TABLE IF NOT EXISTS pair_tags (
    id                  BIGSERIAL PRIMARY KEY,
    name                TEXT NOT NULL,
    primary_tag_id      BIGINT NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    partner_tag_id      BIGINT NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    primary_device_id   INT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    partner_device_id   INT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    auto_generated      BOOLEAN NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- One pair tag per (pair, name). The pair is symmetric — we always
    -- store (primary_device_id, partner_device_id) with primary_device_id
    -- being the LOWER of the two ids, so the unique constraint catches
    -- accidental duplicate insertion attempts from either side.
    CONSTRAINT uq_pair_tags_pair_name UNIQUE (primary_device_id, partner_device_id, name),
    CONSTRAINT ck_pair_tags_devices_distinct CHECK (primary_device_id <> partner_device_id),
    CONSTRAINT ck_pair_tags_tags_distinct CHECK (primary_tag_id <> partner_tag_id),
    -- Normalization: primary_device_id < partner_device_id (canonical order).
    -- Without this, a pair (A=5, B=7) could be stored as either (5,7) or
    -- (7,5); the unique constraint would treat them as different rows.
    CONSTRAINT ck_pair_tags_canonical_order CHECK (primary_device_id < partner_device_id)
);

CREATE INDEX IF NOT EXISTS ix_pair_tags_primary_device ON pair_tags(primary_device_id);
CREATE INDEX IF NOT EXISTS ix_pair_tags_partner_device ON pair_tags(partner_device_id);
CREATE INDEX IF NOT EXISTS ix_pair_tags_primary_tag ON pair_tags(primary_tag_id);
CREATE INDEX IF NOT EXISTS ix_pair_tags_partner_tag ON pair_tags(partner_tag_id);
