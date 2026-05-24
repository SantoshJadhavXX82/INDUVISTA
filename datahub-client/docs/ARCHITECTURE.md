# InduVista DataHub — Architecture

This document captures the design decisions made in OPC.2. Future
sessions implement against these contracts; deviations should be
discussed before the fact.

## Goals

InduVista DataHub is a **Windows-targeted desktop application** that
runs on a plant's edge box (or operator workstation) and bridges
**plant-floor OPC servers** with the **INDUVISTA backend**. It must:

1. Read tag values from one or more OPC servers (UA + DA both)
2. Buffer samples locally during network outages
3. Push samples to INDUVISTA over the authenticated `/api/ingest` endpoint
4. Survive being killed, the network being unplugged, the server
   being down for hours, and resume cleanly when conditions recover
5. Look professional enough for an operator to configure without
   needing developer help

## Non-goals (v1)

- Multi-platform GUI (Windows only — OPC DA is COM-based and the
  installed base is Windows. Linux dev runs are supported but
  unsupported for production.)
- Real-time control or write-back to OPC servers
- Embedded SCADA replacement — this is a collector, not a HMI

## Top-level layout

```
datahub-client/
├── README.md
├── pyproject.toml          # package metadata + deps
├── requirements.txt        # pinned versions
├── .gitignore
├── docs/
│   └── ARCHITECTURE.md     # this file
├── src/induvista_datahub/
│   ├── __main__.py         # `python -m induvista_datahub` entry
│   ├── app.py              # QApplication bootstrap
│   ├── core/
│   │   ├── paths.py        # %APPDATA% resolution, cross-platform
│   │   └── logging_setup.py
│   ├── config/
│   │   ├── schema.py       # Pydantic models for config.toml
│   │   └── manager.py      # load/save/merge with defaults
│   ├── ui/
│   │   ├── main_window.py  # QMainWindow + 3 tabs
│   │   ├── status_tab.py
│   │   ├── tags_tab.py
│   │   └── settings_tab.py
│   ├── opc/
│   │   ├── base.py         # OpcReaderBase abstract class
│   │   ├── ua_reader.py    # asyncua UA client
│   │   └── da_reader.py    # DA bridge subprocess client
│   ├── ingest/
│   │   ├── pusher.py       # httpx POST to /api/ingest
│   │   └── store_forward.py  # SQLite buffer
│   └── workers/
│       └── pipeline.py     # orchestrator thread
└── tests/
    └── test_smoke.py
```

## Threading model

```
┌────────────────────────────────────────────────────────────────┐
│ Main thread (Qt event loop)                                    │
│   QApplication + MainWindow + tabs                             │
│   Receives signals from worker threads → updates UI            │
└────────────────────────────────────────────────────────────────┘
        ▲                              ▲
        │ Qt signal                    │ Qt signal
        │                              │
┌───────┴──────────────┐    ┌──────────┴──────────────────────┐
│ OPC reader thread(s) │    │ Pipeline / pusher thread        │
│ asyncio loop each    │    │ Reads from store_forward,       │
│ asyncua / DA bridge  │    │ batches, POSTs to /api/ingest,  │
│                      │    │ updates status counters         │
└──────────────────────┘    └─────────────────────────────────┘
        │                              │
        └─────► store_forward.db ◄─────┘
              (SQLite, WAL mode)
```

**Why threads, not just asyncio?**
- PySide6's event loop is the main loop. Mixing it with asyncio
  requires the `qasync` glue layer, which works but adds complexity
- `asyncua` IS asyncio-native, but the DA path uses synchronous COM
  calls (pywin32) that can't share an event loop
- One asyncio loop per reader thread keeps the asyncua path async-clean
  and lets the DA path block freely without affecting UI responsiveness

**Cross-thread communication** is exclusively via Qt signals (for
UI updates) and the SQLite database (for sample handoff between
readers and the pusher). No shared in-memory queues — the database
IS the queue, which gives us crash safety for free.

## OPC DA — the 32-bit bridge

OPC DA is a COM/DCOM API. Python's `pywin32` can talk to COM, but the
Python process must match the bitness of the COM server. Most legacy
OPC DA servers (Matrikon, RSLinx Gateway, Honeywell PHD) are 32-bit.
A 64-bit Python process cannot directly host a 32-bit COM client.

**The solution: two PyInstaller builds, IPC over a local socket.**

```
┌──────────────────────────────┐         ┌────────────────────────────┐
│ 64-bit main app              │ socket  │ 32-bit DA bridge           │
│ (Qt UI, asyncua, pusher)     │◄───────►│ subprocess                 │
│                              │         │ pywin32 → COM → OPC DA srv │
└──────────────────────────────┘         └────────────────────────────┘
```

The bridge is a small Python program that:
1. Spawns at app start (or first DA connection attempt)
2. Listens on `127.0.0.1:<ephemeral>` (loopback only — never bound publicly)
3. Accepts JSON commands: `{"cmd":"connect","prog_id":"Matrikon.OPC.Simulation.1"}`, `{"cmd":"read","items":[...]}`, etc.
4. Returns JSON responses with values + quality
5. Is shipped in the installer as a separate `.exe` next to the main app

For OPC.2 (this session), the DA bridge is a stub — `da_reader.py`
raises `NotImplementedError`. The IPC protocol gets fleshed out in
OPC.3.

## Store-and-forward

A SQLite database at `%APPDATA%\InduVista\DataHub\store_forward.db`
buffers samples between read and push.

**Tables:**

```sql
CREATE TABLE pending_samples (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tag_id          INTEGER NOT NULL,      -- INDUVISTA tag_id (mapped from OPC node)
    time            TEXT NOT NULL,         -- ISO 8601 UTC
    value_double    REAL,                  -- numeric sample
    value_text      TEXT,                  -- string sample (rare)
    st              INTEGER NOT NULL,      -- 0-255 quality byte
    st_reason       TEXT,                  -- optional human reason
    inserted_at     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    retry_count     INTEGER NOT NULL DEFAULT 0,
    last_error      TEXT                   -- truncated error from the most recent push attempt
);

CREATE INDEX idx_pending_inserted ON pending_samples(inserted_at);

CREATE TABLE metadata (
    key             TEXT PRIMARY KEY,
    value           TEXT NOT NULL,
    updated_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
-- e.g.: ('last_successful_push', '2026-05-24T12:34:56Z'),
--       ('schema_version', '1'),
--       ('total_pushed_lifetime', '142387')
```

**WAL mode** enabled (`PRAGMA journal_mode=WAL`) so the reader and
pusher threads don't block each other.

**Drain semantics:**
- Pusher polls for rows older than N seconds (configurable, default 5)
- Reads up to `batch_size` rows (default 500) ordered by `inserted_at` ASC
- POSTs them, on `accepted > 0` rows, deletes those rows by id
- On HTTP error or `rejected > 0`, increments `retry_count` and updates `last_error`
- After `max_retries` (default 10) consecutive failures on a row, moves
  it to a `dead_letter_samples` table (added in OPC.4) and logs WARN

**Backpressure:**
- If `pending_samples` row count exceeds `max_buffered` (default 1,000,000),
  the OPC readers pause and surface a warning in the Status tab. This
  prevents the disk from filling on an indefinitely-disconnected client.

## Config file shape

`%APPDATA%\InduVista\DataHub\config.toml`:

```toml
[server]
url = "http://localhost:8000"
api_key = ""                    # paste from POST /api/admin/api-keys
push_interval_sec = 5
batch_size = 500

[opc]
# A list of OPC connections (UA endpoints + DA prog-IDs).
# Empty list is valid — the app starts but reads nothing.
connections = []

# Example:
# [[opc.connections]]
# kind = "ua"
# name = "Plant A — Compressors"
# endpoint = "opc.tcp://10.0.1.50:4840"
# security_policy = "Basic256Sha256"
# username = ""
# password = ""

# [[opc.connections]]
# kind = "da"
# name = "Plant B — Legacy"
# prog_id = "Matrikon.OPC.Simulation.1"
# host = "localhost"

[tag_mappings]
# Per-OPC-node mapping to INDUVISTA tag_id. Built via the Tag Browser
# tab (added in OPC.5). For now, hand-edited or empty.
# Example: { connection = "Plant A", node_id = "ns=2;s=Pressure", induvista_tag_id = 42 }
mappings = []

[logging]
level = "INFO"                  # DEBUG / INFO / WARNING / ERROR
max_bytes = 10485760            # 10 MB per log file
backup_count = 5
```

The Pydantic schema in `config/schema.py` validates this and provides
defaults so a fresh install with no file Just Works.

## Logging

Rotating file handler + console handler. Files at
`%APPDATA%\InduVista\DataHub\logs\datahub.log`. Log records include
thread name so cross-thread issues are debuggable.

```
2026-05-24 12:34:56,789 INFO  [MainThread]    induvista_datahub.app: Starting InduVista DataHub v0.1.0
2026-05-24 12:34:57,001 INFO  [pipeline]      induvista_datahub.workers.pipeline: Pipeline thread started
```

## Versioning

`src/induvista_datahub/__init__.py` carries `__version__`. The
PyInstaller spec in OPC.6 reads this. CI bumps via `hatch version`
or manually for now.

## What OPC.2 ships vs defers

| Component | OPC.2 (this) | OPC.3 | OPC.4 | OPC.5 | OPC.6 |
|---|---|---|---|---|---|
| Window + 3 tabs |  ✅ |   |   | polish |   |
| Config TOML load/save | ✅ |   |   |   |   |
| Logging setup | ✅ |   |   |   |   |
| SQLite schema init | ✅ |   |   |   |   |
| OPC UA reader | stub | ✅ |   |   |   |
| OPC DA bridge | stub | ✅ |   |   |   |
| HTTP pusher | stub |   | ✅ |   |   |
| Store-forward drain |   |   | ✅ |   |   |
| Onboarding wizard |   |   |   | ✅ |   |
| Tag browser |   |   |   | ✅ |   |
| PyInstaller spec |   |   |   |   | ✅ |
| Inno Setup MSI |   |   |   |   | ✅ |
| Windows service mode |   |   |   |   | ✅ |
