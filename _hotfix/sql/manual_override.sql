-- Phase 12.5 — Manual override flag for duty/standby pairs.
--
-- When TRUE on either side of a duty/standby pair, worker reconciliation
-- is suspended for that pair. This lets operators perform sticky manual
-- swaps during commissioning, maintenance, or controlled failover tests
-- without the worker undoing them on the next cycle.
--
-- The flag is per-device but the UI toggles both sides of a pair together
-- via POST /devices/{id}/set-pair-override. The worker's reconciliation
-- query checks both d.manual_override AND p.manual_override so an
-- asymmetric setting (e.g. set on one side only) is still safe — the
-- pair is excluded as soon as either side opts out.
--
-- Idempotent — safe to re-run.

ALTER TABLE devices
    ADD COLUMN IF NOT EXISTS manual_override BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS ix_devices_manual_override
    ON devices(manual_override)
    WHERE manual_override = TRUE;
