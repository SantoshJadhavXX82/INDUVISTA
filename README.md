# InduVista

Industrial data acquisition and reporting platform by SVJ.

InduVista acquires data from industrial devices over **Modbus TCP** and **OPC UA**, validates and quality-stamps every reading, and stores it in a **TimescaleDB** historian. When the database is briefly unavailable, writes spill into a local store-and-forward buffer and drain back automatically once it returns — no data loss, original timestamps preserved. On top of the historian sits a calculation engine, an alarm engine, and a React operations UI for trends, dashboards, diagnostics, and configuration.

## Highlights

- **Multi-protocol acquisition** — Modbus TCP polling worker and OPC UA subscription worker, plus a generic authenticated `/api/ingest` endpoint for plant-side agents.
- **Lossless historian** — TimescaleDB hypertables with continuous aggregates and compression; store-and-forward buffering with automatic replay.
- **Quality model** — every sample carries a quality tier (Modbus `st` decoding, stale detection, range checks).
- **Calc engine** — 62 computed-block types across five tiers (arithmetic, selection, conditional logic, stateful, aggregation) producing computed tags.
- **Alarm engine** — configurable severities and rule types, rate-of-change / frozen / spike rules, acknowledge & shelve, density heatmap.
- **Trends & analytics** — historical and real-time charts, aggregation modes, rate-of-change, sigma bands, saved views, data-gap analysis.
- **Operations UI** — live dashboard, tag explorer, diagnostics, audit log, Modbus protocol tools (frame inspector, register browser, write console), with dark mode and mobile navigation.
- **OPC UA admin** — manage OPC UA sources and tag mappings from the web UI, browse a server's address space, and import nodes directly into tags.
- **OPC UA timestamp control (latest build, Phase OPC-web.2.3)** — per-source `trust_server_timestamp` toggle (worker time vs `SourceTimestamp`), with automatic server-clock-drift detection at subscription activation, surfaced in the UI with a severity callout.
- **Soft delete** — tags and devices support soft deletion (hidden but retained); supervisors and APIs skip soft-deleted rows automatically.

## Stack

**Backend** — FastAPI · Python 3.11 · SQLAlchemy + Alembic · TimescaleDB/PostgreSQL · pymodbus · asyncua
**Frontend** — React 19 · Vite 6 · TypeScript · Tailwind CSS 4 · TanStack Query · uPlot
**Plant agent** — DataHub client (PySide6 desktop app, parked) — OPC UA/DA reader with store-and-forward push to `/api/ingest`
**Orchestration** — Docker Compose

## Services

| Service | Profile | Role |
|---|---|---|
| `postgres` | default | TimescaleDB historian |
| `migrate` | default | Runs `alembic upgrade head`, then exits |
| `backend` | default | FastAPI app + REST API |
| `modbus_worker` | `workers` | Modbus TCP polling supervisor |
| `opc_worker` | `workers` | OPC UA subscription supervisor |
| `alarm_evaluator` | (always) | Evaluates alarm rules each cycle |
| `calc_evaluator` | (always) | Runs the computed-block engine |
| `modbus_simulator` | `sim` | Modbus TCP simulator (development only) |

## Quick start

Bring up the foundation (Postgres + migrations + backend):

```
docker compose up
```

Add the acquisition workers and a simulator for local development:

```
docker compose --profile sim --profile workers up
```

Then:

- Web UI: http://localhost:5173 (dev) — `cd frontend && npm install && npm run dev`
- API docs: http://localhost:8000/docs
- Health check: http://localhost:8000/health

Configuration is read from `.env` (see `.env.example` for the variables).

## Project layout

```
backend/                 FastAPI app, workers, Alembic migrations, tests
  app/api/               REST routers (tags, trends, alarms, calc, opc_sources, ingest, api_keys, ...)
  app/workers/           modbus_supervisor, opc_supervisor, alarm_evaluator, calc_evaluator, calc_blocks/
  app/utils/             api_key_auth, timezone helpers
  alembic/versions/      database migrations
frontend/                React + Vite operations UI
  src/pages/             Dashboard, Trend, Alarms, Diagnostics, TagExplorer, OpcSources, config/, global/
  src/components/        UI library, opc/, calc/, trend/, alarms/, diagnostics/, layout/
datahub-client/          Parked plant-side agent (PySide6) — OPC UA/DA → /api/ingest
simulators/              Modbus TCP test simulator
db/init/                 First-boot SQL (TimescaleDB extension)
scripts/                 Seed and smoke-test utilities
docs/                    Architecture and reference notes
```

## UI map

- **Operate** — Live Dashboard · Trend · Alarms · Audit Log · Diagnostics · Data Gaps
- **Explore (Modbus)** — Frame Inspector · Register Browser · Write Console · Write Audit
- **Configure** — Channels · Devices · Register Blocks · OPC Sources
- **Setup / Global** — Engineering Units · Alarm Severities · Alarm Types · Calc Blocks · Groups · Enumerations · Duty/Standby Values · Settings

## Acquisition paths

1. **Modbus TCP** — `modbus_worker` polls configured devices/registers, decodes values and `st` quality, writes to the historian.
2. **OPC UA (direct)** — `opc_worker` opens an asyncua client per configured OPC source, subscribes to mapped nodes, buffers samples, and flushes to the historian. Each source has `trust_server_timestamp`: when `false` (default), the worker uses its own UTC clock at ingest; when `true`, it uses the server's `SourceTimestamp`. Server clock drift is measured at every subscription activation and persisted on the source row so the UI can surface a warning.
3. **Plant agent ingest** — a DataHub client (or any agent) authenticates with an API key and POSTs samples to `/api/ingest`; the backend range-checks and bulk-inserts them with `source = 'ingest'`.

## Roadmap

- **Done:** Modbus acquisition + store-and-forward, configuration UI, calc engine, alarms, trends/diagnostics/audit, reference data, dark mode + mobile, OPC UA ingestion and web admin (sources, mappings, address-space browse & import), per-source timestamp source + server clock drift detection (Phase OPC-web.2.3), soft delete for tags and devices, integration tests for OPC browse/import.
- **In progress:** SF-buffer replay path for OPC samples, further OPC integration test coverage.
- **Deferred:** OPC DA bridge (32-bit subprocess), DataHub push pipeline productionization, MQTT and REST/SQL connectors, reporting engine and scheduler.
