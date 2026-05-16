# InduVista — Modbus quality reference

Authoritative table-form reference for the `st` (status) byte set by the
Modbus worker, the conditions that produce each value, and the resulting
behaviour in the frontend, reports, and ROC/σ calculations.

- **Source of truth**: `backend/app/modbus/status.py`
- **Worker emission points**: `backend/app/workers/modbus_supervisor.py`
- **Frontend GOOD threshold**: `ST_READ_OK = 128` (see `frontend/src/lib/trendRoc.ts`, `TrendChart.tsx`, `Dashboard.tsx`, `TagExplorer.tsx`)

---

## 1. Quality tiers (top-level)

| ST range | Hex | `st_class` | Plain meaning | Trusted for reporting / ROC / σ? |
|---|---|---|---|---|
| 192 – 255 | C0 – FF | `VALID_EXTENDED` | Good with extras (manual override, limit reached, etc.) | ✅ Yes |
| 128 – 191 | 80 – BF | `VALID` | Plain good | ✅ Yes |
|  64 – 127 | 40 – 7F | `SUSPECT` | Reading exists but its trustworthiness is in doubt | ⚠️ Excluded from ROC / σ; shown in chart with marker |
|   0 – 63 | 00 – 3F | `INVALID` | Reading is not usable | ❌ Excluded everywhere |

Boundary rule: a sample with `st >= 128` is GOOD for all downstream maths. Anything below 128 is filtered.

---

## 2. Every defined ST value and what produces it

| ST | Hex | Constant | Tier | Trigger condition | Typical `st_reason` |
|---:|---|---|---|---|---|
| **128** | `80` | `ST_READ_OK` | VALID | Poll succeeded, response within deadline, CRC valid, function code matched the request, value decoded to a finite number, no range violation. | *(empty)* |
| **68** | `44` | `ST_RANGE_WARN` | SUSPECT | Read succeeded and value decoded fine, but it falls **outside the operator-configured min/max**. The real value is still stored. | `RANGE_LOW`, `RANGE_HIGH` |
| **64** | `40` | `ST_STALE` | SUSPECT | A previously-good tag has not been refreshed within its staleness window. Set by the supervisor (not the worker) during a periodic sweep. | `STALE` |
|  **0** | `00` | `ST_NEVER_READ` | INVALID | Tag is configured but has not yet been successfully read since worker start, channel enable, or tag enable. | *(empty)* or `NEVER_READ` |
|  **4** | `04` | `ST_COMM_TIMEOUT` | INVALID | Transport-level failure. Device is unreachable: TCP connect refused, TCP RST, no response within the request timeout, broken pipe, DNS resolution failure, serial port silence. | `TIMEOUT`, `CONN_LOST`, `CONN_BACKOFF` |
|  **8** | `08` | `ST_DECODE_FAIL` | INVALID | Got bytes back, but they could not be decoded to the configured `data_type` / `byte_order` / register width. Device IS reachable; config is suspect. | `ENRON_WIDTH_INVALID`, decode-specific text |
| **12** | `0C` | `ST_MODBUS_EXCEPTION` | INVALID | Slave returned an exception PDU (`function_code \| 0x80`). Device is alive and refusing the request. See §3 for the 10 named subcodes. | one of `MODBUS_EXCEPTION_NAMES` |
| **16** | `10` | `ST_MODBUS_IO_ERROR` | INVALID | Wire-level corruption: CRC check failed on RTU, malformed MBAP header on TCP, unexpected transaction ID, partial frame, unexpected length. | `IO_ERROR`, `IO_ERROR: <detail>` |
| **20** | `14` | `ST_TRANSPORT_UNSUPPORTED` | INVALID | Channel's `transport` field is `rtu`/`serial` but the running worker is TCP-only (or vice versa). Configuration error, surfaced as periodic samples so the operator sees it. | `TRANSPORT_UNSUPPORTED_RTU`, `TRANSPORT_UNSUPPORTED_TCP` |
| **24** | `18` | `ST_RETRY_EXHAUSTED` | INVALID | A `TIMEOUT` or `IO_ERROR` persisted after all configured retries (default 3). The worker upgrades the original code to this for clarity. | `RETRY_EXHAUSTED: <original reason>` |

> The supervisor sets `ST_STALE` via a side-channel UPDATE; all other values are written by the worker as part of normal cycle processing.

---

## 3. Modbus exception subcodes (st_reason values when ST = 12)

When `st = ST_MODBUS_EXCEPTION` (12), the `st_reason` field carries the named exception code.

| Code | Name | Typical scenario |
|---:|---|---|
| 1 | `ILLEGAL_FUNCTION` | Asked for FC4 on a device that only supports FC3, or FC1 on a holding-register address. |
| 2 | `ILLEGAL_DATA_ADDRESS` | **Most common.** Requested register doesn't exist on this slave: off-by-one, wrong block (holding vs input), wrong addressing mode (Modicon vs 0-based), wrong slave_id. |
| 3 | `ILLEGAL_DATA_VALUE` | Write value out of acceptable range for the register. |
| 4 | `SLAVE_DEVICE_FAILURE` | Device-internal error (vendor-specific). |
| 5 | `ACKNOWLEDGE` | Long-running request; slave acknowledged but not yet complete. |
| 6 | `SLAVE_DEVICE_BUSY` | Retry later; slave processing a long command. |
| 7 | `NEGATIVE_ACKNOWLEDGE` | Slave cannot perform requested function. |
| 8 | `MEMORY_PARITY_ERROR` | Device internal RAM parity check failed. |
| 10 | `GATEWAY_PATH_UNAVAILABLE` | Modbus gateway can't route to the addressed slave. |
| 11 | `GATEWAY_TARGET_NO_RESPONSE` | Gateway reached the slave's bus but the slave didn't respond in time. |

---

## 4. Worker decision flow

The order in which `modbus_supervisor.py` resolves a poll to an `st` value:

```
poll cycle starts
    │
    ▼
connect to device  ───── fails ─────▶  ST_COMM_TIMEOUT (CONN_LOST / CONN_BACKOFF)
    │
    ▼
transport supported on this worker?  ── no ─▶  ST_TRANSPORT_UNSUPPORTED  (20)
    │
    ▼
send request  ───── timeout ────────▶  ST_COMM_TIMEOUT  (4, "TIMEOUT")
    │
    ▼
response received
    │
    ├── CRC / framing bad ─────────▶  ST_MODBUS_IO_ERROR  (16)
    │
    ├── exception PDU received ────▶  ST_MODBUS_EXCEPTION  (12)
    │                                  st_reason = exception name
    │
    └── normal response
            │
            ▼
        decode to data_type
            │
            ├── decode fails ─────▶  ST_DECODE_FAIL  (8)
            │
            ▼
        value decoded
            │
            ├── outside range ────▶  ST_RANGE_WARN  (68) + value still stored
            │
            ▼
        ST_READ_OK  (128) ✓

(retry layer wraps the timeout / IO paths)
    if retries exhausted ──────────▶  ST_RETRY_EXHAUSTED  (24)
                                       st_reason = "RETRY_EXHAUSTED: <original>"

(separate sweep — runs every staleness interval)
    no successful read for a tag in window ─▶  ST_STALE  (64)
```

---

## 5. Symptom → root-cause table

Use this table when scanning the Diagnostics page or the trend chart's quality markers.

| Pattern observed | Most likely cause | First thing to check |
|---|---|---|
| Sudden spike in `ST 4` for one device | Network cable, switch port, PLC powered off, IP change, firewall rule | `ping <device>` from edge agent; check switch port LED |
| Same on TCP, but with `CONN_BACKOFF` reason | Device rejecting connections faster than the retry timer | Look at PLC's open-connection limit; another client may be hogging it |
| `ST 12 / ILLEGAL_DATA_ADDRESS` on one tag | Off-by-one register, wrong addressing mode, wrong block, wrong slave_id | Cross-check the tag config against the vendor's Modbus map |
| `ST 12 / ILLEGAL_FUNCTION` on every tag of a device | Device supports a different FC than configured | Try FC3 vs FC4, FC1 vs FC2 |
| `ST 16 (IO_ERROR)` bursting on an RTU channel | EMI from VFD/motor starter, missing termination resistor, A/B swapped, wrong baud rate, cable too long | Scope the line, verify 120Ω terminators at both ends |
| `ST 16` sporadic on a TCP channel | NAT box mangling frames, MTU mismatch, switch buffer overruns | `wireshark` or `tcpdump` at edge; look for fragmented Modbus frames |
| `ST 8 (DECODE_FAIL)` on one tag after firmware update | Register layout changed; `data_type` or `byte_order` no longer matches | Re-read vendor docs for the new firmware; toggle byte order |
| `ST 8` only on dual-register tags (FLOAT32, INT32) | Word order misconfigured | Try the other word-order option |
| `ST 68 (RANGE_WARN)` on a temperature signal | Sensor drift, instrument out of calibration, 4-20mA loop reading 25mA (sensor fault), or a genuine process excursion | Compare against neighbouring instrument; check loop current with a clamp meter |
| `ST 64 (STALE)` plant-wide for a few minutes | Worker process restart, supervisor pause, channel disabled briefly | Check `svj_backend` / `svj_modbus_worker` container logs |
| `ST 0 (NEVER_READ)` on a newly added tag | First poll hasn't completed yet — usually clears within one cycle | Wait 1 poll cycle; if persistent, the block containing this tag may be paused |
| `ST 24 (RETRY_EXHAUSTED)` on a specific device | Device intermittently failing; marginal cable; EMI bursts; CPU overload on PLC | Lower the device's poll rate, increase timeout, or move to a dedicated channel |
| `ST 20 (TRANSPORT_UNSUPPORTED)` | Worker build doesn't support this channel's transport (e.g. RTU on TCP-only worker) | Either change the channel transport or run the right worker variant |

---

## 6. Where each tier flows through the system

| Subsystem | VALID_EXTENDED (192-255) | VALID (128-191) | SUSPECT (64-127) | INVALID (0-63) |
|---|---|---|---|---|
| Trend chart main series | drawn | drawn | drawn with quality marker overlay | drawn with quality marker overlay |
| ROC computation | included | included | **excluded** | **excluded** |
| σ / mean / stddev | included | included | **excluded** | **excluded** |
| Min / Max envelope | included | included | excluded | excluded |
| Raw data table | shown | shown | shown (yellow row) | shown (red row) |
| Diagnostics good_pct | counted as good | counted as good | counted as suspect | counted as bad |
| Hide-bad filter | shown | shown | shown | hidden |
| Good-only filter | shown | shown | **hidden** | hidden |
| Live tile QUALITY chip | `GOOD` (green) | `GOOD` (green) | `UNCERTAIN` (amber) | `BAD` (red) |
| Reports (PDF, Excel) | exported | exported | exported with flag | exported with flag |
| Phase 14 alarm evaluator (planned) | evaluated | evaluated | excluded from rule check; latch held | excluded from rule check; latch held |

---

## 7. Versioning / change log

| Date | Change | Rationale |
|---|---|---|
| Phase 8.5 | Refined INVALID tier into distinct numeric constants (4, 8, 12, 16, 20, 24) | Diagnostics needs to distinguish transport vs slave vs wire faults |
| Phase 12.6 | Added `ST_RANGE_WARN = 68` (SUSPECT) | Operator-defined min/max excursions should still record the value |
| Phase 13.10 | Frontend quality filter aligned on `ST >= 128` as the GOOD boundary | Consolidate across Dashboard, Trend, Reports |
| Phase 13.12 | ROC calculator uses `ST >= 128` (was incorrectly `>= 192`) | Match the rest of the codebase; UNCERTAIN (64-127) still excluded |

---

## 8. Quick rule of thumb for operators

| If you see... | It means... |
|---|---|
| Green numbers everywhere | All good — sensors and comms are healthy. |
| Yellow chip on one tile | Sensor is reading but value is unreliable; investigate at end of shift. |
| Red chip on one tile | This sensor / device is not giving us usable data right now. |
| Red chip across a whole device | Comms or power problem at the device level. |
| Red chip across multiple devices on the same switch | Switch / network problem. |
| Sudden plant-wide red, then recovery | Worker process or supervisor restart. |
