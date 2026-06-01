# INDUVISTA — Project Knowledge

## Quick read for Claude
Read this whole file before doing anything else in a new chat. Most of
what you'd otherwise have to ask is here. When something below conflicts
with what you'd guess from training data, this file wins.

---

## 1. What this project is

**Product**: INDUVISTA — industrial reporting / SCADA-adjacent tool for
plant operators. Collects sensor data from PLCs over Modbus and OPC UA,
stores in a time-series DB, evaluates calc tags and alarms, renders
heatmaps and dashboards, exports PDF/Excel reports.

**Status**: Active development. Reports render pipeline live (Phase 21+),
on top of Phase OPC-web.2.3 (server clock drift + trust_server_timestamp).
Current HEAD: `84291a3` ("feat(reports): render pipeline live (Jinja2 +
WeasyPrint 65.1) + block builder + calc engine; migration 0061").

**User context**: Sole developer. Host is Windows, PowerShell, IST
timezone. Postgres + workers run UTC internally.

---

## 2. Stack & infrastructure

**Backend**: FastAPI + SQLAlchemy + Alembic, Python 3.11+ (3.12 supported)
**Database**: PostgreSQL with TimescaleDB extension (hypertables + CAGGs)
**Cache**: Valkey (Redis-compatible)
**Frontend**: React 19 + Vite 6 + TypeScript + TanStack Query + Tailwind 4
**Reports**: Jinja2 + WeasyPrint 65.1 (Pango 1.56 compat fix in 84291a3)
**Orchestration**: Docker Compose (single docker-compose.yml at repo root)

**Container roster**:
| Container | Role |
|---|---|
| svj_postgres | TimescaleDB |
| svj_valkey | cache |
| svj_backend | FastAPI app, alembic, scheduler |
| svj_opc_worker | OPC UA subscription handler |
| svj_modbus_worker | Modbus polling |
| svj_modbus_simulator | dev-only Modbus device sim |
| svj_calc_evaluator | calc tag evaluator |
| svj_alarm_evaluator | alarm evaluator |

**DB credentials** (dev): user `induvista_admin`, db `induvista`.

**Repo root on host**: `D:\INDUVISTA`
**GitHub repo** (public): https://github.com/SantoshJadhavXX82/INDUVISTA · `main` and `phase-17c-tag-or-constant` both at `84291a3`.

---

## 3. Codebase reference

**Gist root (this file)**: https://gist.github.com/SantoshJadhavXX82/26428773b18277ac6267a65017cb99a9
**Source bundles INDEX** (16 single-file gists, each fetchable via HTML view): https://gist.github.com/SantoshJadhavXX82/86fc406f2c3a8961c04d133ef692958f
**Graphify knowledge-graph gist** (`GRAPH_REPORT.md` + downloadable `graph.html`): https://gist.github.com/SantoshJadhavXX82/706a13c839e2db1a331ec74e5c1537b2

**Fallback read paths if `gist.githubusercontent.com` is blocked:**
1. Each bundle gist's HTML page (works — one file per gist).
2. GitHub REST API: `GET https://api.github.com/repos/SantoshJadhavXX82/INDUVISTA/contents/<path>` (60/hr unauthenticated, 5000/hr with token).
3. `git clone https://gist.github.com/<gist-id>.git` (uses `gist.github.com`, not the `*githubusercontent.com` CDN).
4. `git clone https://github.com/SantoshJadhavXX82/INDUVISTA.git` (public repo).

**Key files** — read directly from gist when planning a patch. NEVER patch from memory.

| Purpose | Path on host | Gist URL |
|---|---|---|
| OPC worker | backend/app/workers/opc_supervisor.py | https://gist.github.com/SantoshJadhavXX82/b19578357148ed9f522cd2afce180a35 |
| Modbus worker | backend/app/workers/modbus_supervisor.py | https://gist.github.com/SantoshJadhavXX82/86e0086201f5ce0cddde6f003b2c9ce1 |
| OPC sources API | backend/app/api/opc_sources.py | https://gist.github.com/SantoshJadhavXX82/6da515152b2abfbe004c4ed5cc60c32c |
| Frontend OPC page | frontend/src/pages/OpcSources.tsx | https://gist.github.com/SantoshJadhavXX82/54dc54e21cdd5bb623444ae404063907 |
| Frontend modal | frontend/src/components/opc/CreateOpcSourceModal.tsx | https://gist.github.com/SantoshJadhavXX82/cd58b9b031433cf4a8a73cdeb530e08c |
| Frontend types | frontend/src/types/api.ts | https://gist.github.com/SantoshJadhavXX82/5a67bbe1f1c9b25607843726474a9c23 |
| Migration 0055 (trust_server_timestamp) | backend/alembic/versions/0055_opc_trust_server_timestamp.py | https://gist.github.com/SantoshJadhavXX82/ae3a52e05f5fb135bcbce977b2890340 |
| Migration 0056 (clock drift) | backend/alembic/versions/0056_opc_server_clock_drift.py | https://gist.github.com/SantoshJadhavXX82/0b9072dcb30199c79ffa1a9146bf913b |
| db.py (SessionLocal) | backend/app/db.py | https://gist.github.com/SantoshJadhavXX82/a3e30439893e1bf3c68aabd0049163ae |
| historian.py (Sample, HistorianWriter) | backend/app/historian.py | https://gist.github.com/SantoshJadhavXX82/13fd126f0ea44932efa7d4922e509caa |

**Important**: When a NEW gist URL is shared in chat, add it here so the
next session sees it. Gist HTML can normalize whitespace differently
than disk — verify anchors against raw `Get-Content` dump before
shipping patch scripts.

---

## 4. Domain conventions (do not assume from training data)

### Quality status byte
InduVista uses **OPC DA Quality** convention, NOT OPC UA spec:
- `0-63` INVALID (red, cell=1)
- `64-127` SUSPECT (amber, cell=2)
- `128-191` VALID (green, cell=3)
- `192-255` VALID_EXTENDED (green, cell=3)
- `st >= 128` is GOOD

Internal codes: `ST_NEVER_READ=0, ST_COMM_TIMEOUT=4, ST_DECODE_FAIL=8, ST_RETRY_EXHAUSTED=24, ST_STALE=64, ST_RANGE_WARN=68, ST_READ_OK=128, OPC_QUALITY_GOOD=192`.

UA severity mapping in `_ua_status_to_st`: `Good → 192, Uncertain → 96, Bad → 0`. Full StatusCode hex preserved in `st_reason`.

### Heatmap cell values
`0 = no data (grey), 1 = invalid, 2 = suspect, 3 = valid`. Soft-deleted tags excluded.

### Timestamp policy
- Modbus: ingest-time UTC (worker clock) — no server timestamp exists
- OPC UA: configurable per-source via `opc_sources.trust_server_timestamp`
  - `FALSE` (default, safe): worker uses `datetime.now(timezone.utc)` at ingest
  - `TRUE` (opt-in): worker uses `DataValue.SourceTimestamp` from server
- Server clock drift measured at every subscription activation and persisted in `opc_sources.last_server_clock_drift_sec` + `last_server_clock_check_at`
- `app_timezone` setting (system_settings + env) is **display-only** — used in heatmap/calendar SQL bucketing, never in ingestion timestamp coercion.

---

## 5. Devices and sources currently configured

### OPC UA
| Source | id | device_id | channel_id | endpoint | tag IDs | notes |
|---|---|---|---|---|---|---|
| Plant-A-UA | 1 | 82 | 12 | opc.tcp://host.docker.internal:14840 | 2269-2271, 2280-2290 (14 tags) | AGG SoftBus simulator. SourceTimestamp historically drifted. trust_server_timestamp = FALSE. |

### Modbus
| Source | notes |
|---|---|
| FLOWCOMP (ActiveAlarmCode tag id=134) | working, ingest-time UTC. Use as control when debugging OPC. |

---

## 6. Process conventions

### Patch script pattern
Every backend/frontend modification ships as a Python script that:
1. Validates anchor strings against disk content
2. Writes `.bak_<phase>` backup on first run
3. Applies via `str.replace(anchor, replacement, 1)`
4. Idempotency marker in replacement so re-runs skip cleanly
5. Reports each applied patch by label

Phase numbering convention: `bak_opcweb_2_3`, marker `# Phase OPC-web.2.3 <description>`.

### Branching policy (option 1)
- Work happens on **`phase-17c-tag-or-constant`** (current working branch).
- After each verified commit, **fast-forward push to `main`** so the default branch your agent clones stays current:
  ```
  git push origin phase-17c-tag-or-constant:main
  ```
- This avoids a recurring bug where `main` falls behind the working branch silently.

### Deploy order
1. Drop alembic migration into `backend/alembic/versions/`
2. `docker exec svj_backend alembic upgrade head`
3. Run patch scripts on host (they modify `D:\INDUVISTA\...` files)
4. `docker restart svj_backend svj_opc_worker` (or whichever)
5. Verify within 30s: worker log shows expected lines
6. Verify within 60s: samples landing as expected

### Verification queries — use these, NOT unfiltered queries
Always include a time filter on `tag_values`. **Never** run `SELECT COUNT(*) FROM tag_values` without a `WHERE time > NOW() - INTERVAL ...` clause — it scans every chunk and can wedge on large tables.

```sql
SELECT tag_id, MAX(time), NOW() - MAX(time) AS lag
FROM tag_values
WHERE tag_id = <id> AND time > NOW() - INTERVAL '30 seconds'
GROUP BY tag_id;
```

```sql
SELECT time, NOW() - time AS age FROM tag_values
WHERE tag_id = <id> AND time > NOW() - INTERVAL '20 seconds'
ORDER BY time DESC LIMIT 5;
```

### Cleanup conventions
- TRUNCATE is preferred over DELETE on hypertables when workers are running
- Stop workers before bulk DELETE: `docker stop svj_modbus_worker svj_opc_worker`
- Never `pg_terminate_backend` a multi-hour DELETE without expecting an autovacuum storm afterward

---

## 7. Conversational preferences

**My calling card**: I am the one who decides when to stop. Do not
suggest "let's pick this up tomorrow" unless I bring it up first.

**Reading code**: Read full files from gist before patching. Snippets
and grep output are not enough. If you patch from partial understanding
expect failure.

**Anchor whitespace**: Gist HTML and disk content can differ in
whitespace. Always validate anchors against raw `Get-Content` of the
host file before writing patch scripts.

**Diagnostic philosophy**: Run 1-3 targeted diagnostics, not 10
adjacent ones. Form a single hypothesis, test it, then revise. Avoid
"diagnostic theater" where ten queries give nine partial signals.

**Verification after deploy**: First check worker logs within 30s for
errors. Then check sample flow within 60s using a time-windowed query.
If either fails, revert immediately. Don't stack patches.

**MAX(time) is misleading** when samples may be future-stamped. Always
check both MIN AND MAX, or use windowed queries on real time.

---

## 8. Active phase status

### Current
- **Reports render pipeline live** (`84291a3`, today)
  - Migration 0061 — report blocks schema
  - `backend/app/services/report_render.py` — Jinja2 + WeasyPrint render
  - `backend/app/services/report_blocks.py` — block builder + calc engine
  - Pango 1.56 compatibility fix in WeasyPrint usage

### Recent history
- Phase OPC-web.2.3 (`c7ba4c2`, ancestor of HEAD): migrations 0055/0056, server clock drift probe, trust_server_timestamp toggle, `docs/opc_quality_and_timestamps.md`
- Phase OPC-web.2.2: OPC UA address-space browse & bulk import, integration tests (Kepware)
- Phase 19: dark mode, mobile nav, theming, tag-quality CAGG (migration 0044)
- Phase 17c: calc diagnostics, shared UI component library, screenshots tooling
- Phase 20: server-side PDF export (reportlab) + Download button

### Known open items
- SF buffer (`/data/sf_opc.db`) replay path not wired in OPC-web.2
- A 432 MB DB dump (`induvista-db-20260514-1101.dump`) lives in repo history; consider purging with `git filter-repo` and rotating any credentials it exposed during prior public windows.

---

## 9. Things I want Claude to do automatically

- Search past conversations when I reference "the X we did" — don't ask me to re-explain
- Read PROJECT_KNOWLEDGE.md (this file) at chat start, then the gist files relevant to the task
- Verify anchors against raw disk content before writing patch scripts
- Default to TRUNCATE over DELETE on hypertables when workers can be stopped
- Use time-windowed queries when checking sample flow
- FF-push to `main` after every commit on `phase-17c-tag-or-constant` (don't let main drift silently)

## 10. Things I want Claude to NOT do

- Suggest deferring work to tomorrow (I decide when to stop)
- Run unfiltered `SELECT COUNT(*)` on hypertables
- Patch from memory without reading the file
- Roll back a patch on a single ambiguous data point — verify with a windowed query first
- Stack multiple patches before verifying each
- Read 10 diagnostic queries when 2 would answer the question
- Commit on a feature branch and report "synced" without also confirming `main` is up to date
