-- Phase 12.2 — Device-led duty/standby tracking
--
-- Field devices typically own their own duty/standby state machine and
-- report their self-assessed role via a Modbus register. This migration
-- adds the infrastructure for InduVista to read that signal and keep
-- devices.duty_role in sync with what the field actually says.
--
-- Two new pieces:
--   1. system_settings  — global key/value store. We seed two rows for
--                         the numeric meanings of duty/standby (defaults
--                         1 and 0, but configurable for vendors that
--                         use other conventions).
--   2. devices.duty_status_tag_id — FK to the tag on this device that
--                         carries the self-assessed role signal.
--
-- Idempotent — safe to re-run.

CREATE TABLE IF NOT EXISTS system_settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    description TEXT,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO system_settings (key, value, description) VALUES
    ('duty_standby.duty_value', '1',
     'Numeric value reported by a device''s duty status tag that means this device is currently the duty side of a duty/standby pair.'),
    ('duty_standby.standby_value', '0',
     'Numeric value reported by a device''s duty status tag that means this device is currently the standby side of a duty/standby pair.')
ON CONFLICT (key) DO NOTHING;

-- duty_status_tag_id: which tag on this device reports its self-assessed
-- duty/standby role. Worker polls it like any other tag, then reconciles
-- devices.duty_role to match. NULL = manual-only operation (current
-- behavior, backward compatible).
ALTER TABLE devices ADD COLUMN IF NOT EXISTS duty_status_tag_id BIGINT
    REFERENCES tags(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS ix_devices_duty_status_tag
    ON devices(duty_status_tag_id)
    WHERE duty_status_tag_id IS NOT NULL;

-- Add 'device_reported' to the duty history reason enum so we can
-- distinguish device-driven swaps from operator-driven ones in audit logs.
ALTER TABLE device_duty_history DROP CONSTRAINT IF EXISTS ck_ddh_reason;
ALTER TABLE device_duty_history ADD CONSTRAINT ck_ddh_reason CHECK (
    reason IN (
        'manual', 'primary_failed', 'partner_channel_failover',
        'scheduled', 'failback', 'startup',
        'device_reported'
    )
);
