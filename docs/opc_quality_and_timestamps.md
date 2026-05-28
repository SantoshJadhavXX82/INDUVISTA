# InduVista — OPC UA quality & timestamp reference

Companion to `docs/modbus_quality_reference.md`. Explains how the OPC UA worker maps server-side StatusCodes and timestamps into InduVista's internal model.

- **Worker**: `backend/app/workers/opc_supervisor.py`
- **API**: `backend/app/api/opc_sources.py`
- **Migrations**: `0052_opc_sources.py`, `0055_opc_trust_server_timestamp.py`, `0056_opc_server_clock_drift.py`

---

## 1. Quality (`StatusCode` → `st`)

InduVista's `st` byte uses the **OPC DA Quality** convention (see `modbus_quality_reference.md` for the full table). The OPC UA worker maps every `DataValue.StatusCode` into that space via `_ua_status_to_st` in `opc_supervisor.py`:

| UA severity | `st` value | InduVista tier |
|---|---:|---|
| `Good` | 192 (`OPC_QUALITY_GOOD`) | VALID_EXTENDED |
| `Uncertain` | 96 | SUSPECT |
| `Bad` | 0 | INVALID |

The full UA `StatusCode` hex is preserved in `st_reason` for forensics (e.g. `"BadSubscriptionIdInvalid 0x80B30000"`).

---

## 2. Timestamps — per-source policy

Each row in `opc_sources` has `trust_server_timestamp BOOLEAN` (default `FALSE`).

| Setting | Worker behaviour | When to use |
|---|---|---|
| `FALSE` (default, safe) | `time = datetime.now(timezone.utc)` at ingest. SourceTimestamp/ServerTimestamp from the server are **ignored**. | Default. The worker's own UTC clock is the source of truth; eliminates drift / wrong-timezone risk. |
| `TRUE` (opt-in) | `time = DataValue.SourceTimestamp` if present, else `ServerTimestamp`, else now(UTC). Naive datetimes are assumed UTC. | When the OPC server is the authoritative time source (e.g. a downstream historian needs original sensor timestamps) and you've confirmed its clock is reliable. |

The toggle is exposed in the OPC source edit modal (`CreateOpcSourceModal.tsx`) as a radio between "Worker time" and "Server time".

### Server-clock-drift probe (Phase OPC-web.2.3)

Every time the worker activates a subscription, it reads `Server_ServerStatus_CurrentTime` from the UA server and compares to the worker's UTC clock. The result is persisted on the source row:

- `last_server_clock_drift_sec` — signed seconds; positive means server is ahead.
- `last_server_clock_check_at` — when the probe ran.

The API surfaces both fields in `OpcSourceResponse`, and the modal shows a severity callout:

| `abs(drift_sec)` | Severity |
|---|---|
| `< 2` | OK |
| `2 – 60` | warning |
| `> 60` | error |

This is informational — InduVista never silently adjusts a server's timestamps; it only warns the operator that turning on `trust_server_timestamp` would shift data by that amount.

---

## 3. Subscription configuration

`opc_supervisor.py` creates the subscription with the source's `publishing_interval_ms` and uses asyncua's default `TimestampsToReturn.Both`, so the server is asked for both `SourceTimestamp` and `ServerTimestamp`; the handler prefers Source when `trust_server_timestamp=TRUE`.

A per-source watchdog uses `max(WATCHDOG_MIN_SEC, WATCHDOG_PUBLISH_MULTIPLIER × publishing_interval_ms)` to detect silent stalls (no DataChange notifications). On stall, the worker tears the session down and reconnects with exponential backoff.

---

## 4. Browse & import (Phase OPC-web.2.2)

The OPC sources page can drill the address space (`Objects → ...`) and bulk-import selected nodes as InduVista tags in one call. Integration tests live at `backend/tests/integration/test_opc_browse_import.py` and target a live Kepware server (`KEPWARE_OPC_UA_02`).

---

## 5. Source state derivation (UI)

Shown on the OPC Sources page; derived from `is_enabled` + `last_sample_at`:

| State | Condition |
|---|---|
| `Disabled` | `is_enabled = false` |
| `Idle` | enabled, no `last_sample_at` yet |
| `Live` | last sample ≤ 30 s ago |
| `Stale` | last sample ≤ 5 min ago |
| `Lost` | last sample > 5 min ago |

---

## 6. Soft-delete interaction

Migrations `0053` (tags) and `0054` (devices) added soft-delete flags. The OPC worker and `opc_sources` API skip soft-deleted rows in subscription setup and CRUD listings. Soft-deleted tags are also excluded from heatmap cells.
