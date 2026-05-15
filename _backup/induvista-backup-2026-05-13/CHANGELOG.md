# InduVista — Changelog (this session)

All changes shipped May 13, 2026. Listed in order of deploy, with the
specific files each phase touched.

## Phase 9.1.1 — Enron wire-level support

**Problem**: Daniel/Emerson 700XA Gas Chromatographs use Enron Modbus
addressing — one logical address per value, with `byte_count` headers
that pymodbus's strict parser rejects. The worker could not poll any
Daniel-family device.

**Fix**:
- New `backend/app/workers/enron_channel.py` — persistent async socket per
  device, permissive byte_count parser (accepts `4N + trailing` framing),
  FC=3 and FC=4 supported, widths 2/4/8 bytes per logical address.
- `backend/app/workers/modbus_supervisor.py` — dispatch to new method
  `_poll_enron_block_once` when `block.addressing_mode` is
  `ENRON_HOLDING` or `ENRON_INPUT`. Width inferred from the first tag's
  `register_count × 2`.

## Phase 9.1.2 — register_count auto-derive + Enron-aware validation

**Problem**:
1. The Tag form required users to set register_count manually even though
   the canonical value is fully determined by data_type.
2. The byte-range overlap detector flagged consecutive float32 tags in an
   Enron block as overlapping — but in Enron addressing each tag occupies
   exactly 1 logical address.

**Fix**:
- New `backend/app/modbus/datatypes.py` with `CANONICAL_REGISTER_COUNT`:
  bool/int16/uint16 → 1, int32/uint32/float32 → 2, int64/uint64/float64 → 4.
- `backend/app/api/tags.py` — `_resolve_register_count` accepts
  `is_enron_block` flag; auto-derives when client omits it; rejects
  inconsistent (data_type, register_count) pairs.
- `_validate_addressing` accepts `is_enron_block`; uses
  `address_span = 1` for Enron blocks (same-address-only collision
  detection instead of byte-range overlap).
- `frontend/src/pages/TagExplorer.tsx` — `register_count` input shows
  "AUTO" badge, disabled by default; "Override (advanced)" checkbox
  unlocks for experts.
- `frontend/src/lib/format.ts` — `formatFloat` strips trailing zeros and
  only goes scientific for `abs ≥ 1e7` or `0 < abs < 1e-4`. Fixed
  `0.01` displaying as `1.00e-2`.

## Phase 9.1.2-hotfix-diagnostics — Enron-aware summary counts

**Problem**: The `/diagnostics/summary` endpoint's `overlap_count` and
`block_fit_issue_count` still used the old byte-range overlap query,
showing "15 issues" even after the per-row validator was fixed.

**Fix**:
- `backend/app/api/diagnostics.py` — both summary queries use
  `CASE WHEN addressing_mode IN ('ENRON_HOLDING','ENRON_INPUT') THEN 1
       ELSE register_count END AS effective_span` so the counts match the
  per-row validator's semantics.
- Connection state in `modbus_supervisor` now treats a device as
  `connected` if EITHER pymodbus OR the Enron channel is up (previously
  Enron-only devices showed "disconnected").

## Phase 10.2 — Register Browser Enron support

**Problem**: The Register Browser's scan endpoint hit pymodbus directly,
so scans against a Daniel GC failed with "Modbus Error: [Connection]
Connection unexpectedly closed".

**Fix**:
- `backend/app/api/devices.py` — `ScanRangeRequest` gained optional
  `addressing_mode` and `value_width_bytes`. New `_scan_enron` helper
  routes Enron requests through `EnronChannel` in chunks of 50 logical
  addresses; returns one row per logical address with full-width hex
  plus pre-decoded `decoded_float32_abcd`, `decoded_float32_dcba`,
  `decoded_int32`, `decoded_uint32`, `decoded_float64_abcd`.
- `frontend/src/pages/RegisterBrowser.tsx` — new checkbox "Enron read
  (one value per address)" + value width dropdown (2/4/8 bytes). When
  checked, the scan call passes addressing_mode + width. Interpretation
  panel prefers backend-decoded floats over consecutive-row pairing in
  Enron mode.
- `frontend/src/types/api.ts` — `ScanRow` extended with optional
  decoded fields.

## Phase 10.2-hotfix-scan-retry

**Problem**: First scan after page load sometimes failed with
"connection lost: 0 bytes read on a total of 7 expected bytes" — Daniel
GC drops the first new TCP connection within ~100 ms when the worker
already holds a persistent connection. Subsequent clicks worked.

**Fix**:
- `backend/app/api/devices.py` — `_scan_enron` wraps each chunk read in
  up to 3 attempts with 0.4 s backoff and forced socket close between
  attempts. Final error message includes attempt count and address.

## Phase 10.2-hotfix-cycle-samples

**Problem**: The Diagnostics page's `Samples` column showed `0 / 0` and
`Good %` showed `—` for GC_SIM_001, despite cumulative samples reading
94,927 (proving the device was polling fine).

**Root cause**: `_report_status_sync_with_latency` (the every-5s writer)
hardcoded `last_cycle_samples_total = 0, last_cycle_samples_good = 0`
on INSERT and didn't update those columns on conflict. So once the row
existed, those numbers froze at zero.

**Fix**:
- `backend/app/workers/modbus_supervisor.py` — added
  `_window_samples_total` and `_window_samples_good` counters in
  `__init__`; block loops increment them on every successful write;
  the 5 s status flush drains them, resets to 0, and now passes them
  through `_report_status_sync_with_latency`. SQL UPDATE clause now
  writes these columns on conflict.

## Phase 11 — Foundation UI improvements

**Multiple deliverables**:

### Tag Quality column
- New `frontend/src/components/tags/tag-quality-badge.tsx` — colored dot
  + age, derived from `LiveTag.st`, `st_reason`, `age_seconds`. Four
  states: good / stale / error / unknown. Tooltip carries full ST detail.
- `frontend/src/pages/TagExplorer.tsx` — new `Quality` column at the
  right edge of the table.

### Block Coverage Map
- New `frontend/src/components/blocks/block-coverage-map.tsx` — SVG
  horizontal bar, one cell per logical address, colored by tag data
  type. Antipattern detector flashes amber when an Enron block has 4+
  tags spaced exactly 2 apart ("gap=2 pattern detected").
- `frontend/src/pages/config/RegisterBlocks.tsx` — new
  `BlockCoveragePanel` rendered at the top of the edit drawer.

### Navigation reorganization
- `frontend/src/components/layout/Nav.tsx` — four workflow-driven
  sections in this order: **Setup** (Engineering Units, Groups,
  Enumerations) at top, **Operate** (Live Dashboard, Diagnostics, Data
  Gaps), **Explore** (Tag Explorer, Register Browser, Frame Inspector,
  Write Console, Write Audit), **Configure** (Channels, Devices,
  Register Blocks).

### Device Picker
- New `frontend/src/components/ui/device-picker.tsx` — searchable
  combobox replacement for `DeviceTabs` in Tag Explorer. Scales to
  any device count. Per-device health dots aggregated from tag-level
  status (worst tag wins). LocalStorage-backed recent-devices list
  floats common picks to the top. Full keyboard navigation.

### Tag and Device rename
- `backend/app/api/tags.py` — `TagUpdate.name` field added; dynamic
  UPDATE SQL builder picks it up automatically. IntegrityError handler
  returns HTTP 409 on duplicate-name.
- `backend/app/api/devices.py` — same for `DeviceUpdate.name`.
- `frontend/src/pages/TagExplorer.tsx` — `Name` input added to the
  Editable section of the tag edit drawer.
- `frontend/src/pages/config/Devices.tsx` — name input no longer
  `disabled`; "(immutable)" label removed.

### CSV upsert
- `backend/app/api/tags.py` — `/tags/bulk` rewritten to upsert by
  `(device_id, name)`. Per-row outcome: `created` (new name),
  `updated` (name matched existing tag, fields refreshed), or
  `error` (validation/integrity failure). `BulkTagResult` gained
  `action` field. Existing tag excluded from overlap checks during
  upsert. New name at occupied address still errors per-row.

## Data fixes applied to the production database

### Mole-% gap=2 → gap=1 migration
`sql/fix_mole_addressing.sql` — corrected 16 tags from addresses
`7001, 7003, ..., 7031` (gap=2) to `7001, 7002, ..., 7016` (gap=1).
Block count reduced from 32 to 16. The mislabeling had been silently
recording every-odd-numbered Mole component (1, 3, 5, …) into rows
labeled 1, 2, 3, … — missing ethane (2.5%), nitrogen (0.7%), etc.
Post-migration the composition sums to 100.00%.

### 700XA tag pack import
`sql/tags_core.sql` — 108 fiscal-essential tags created across 12
enabled blocks covering weight%, ISO calorific value, density, Wobbe,
real-time clock, run-state, and alarms. Generated from the official
Daniel Modbus listing (`700XA_-_UK_MODBUS_Listing.xls`).
