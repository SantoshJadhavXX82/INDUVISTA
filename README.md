# InduVista

Industrial data acquisition and reporting tool by SVJ.

InduVista polls Modbus TCP devices, validates each reading, and stores it in a TimescaleDB historian. When the historian is briefly unavailable, writes spill into a store-and-forward buffer and a replay worker drains them back as soon as the database returns — no data loss, original timestamps preserved.

## Stack (Phase 0–2)

- FastAPI backend
- PostgreSQL with the TimescaleDB extension
- Modbus TCP polling worker
- Store-and-forward replay worker
- Modbus TCP simulator (development only)
- Docker Compose for orchestration

## Quick start

Bring up the foundation (Postgres + migrations + backend):

```
docker compose up
```

Add the simulator and workers when you're ready for Phase 1:

```
docker compose --profile sim --profile workers up
```

Then:

- API docs: http://localhost:8000/docs
- Health check: http://localhost:8000/health

## Project layout

```
backend/        FastAPI app, workers, Alembic migrations
db/init/        First-boot SQL (TimescaleDB extension)
simulators/     Modbus TCP test simulator
```

## Roadmap

- **Phase 0–2 (current scope):** foundation, Modbus acquisition, store-and-forward fallback
- **Phase 3+:** configuration UI, grouped polling, diagnostics, reporting engine, scheduler
- **Future:** OPC UA, MQTT, REST and SQL connectors
