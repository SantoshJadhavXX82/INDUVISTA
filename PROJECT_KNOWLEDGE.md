# INDUVISTA — Project Knowledge

> **Chat-priming doc.** Paste this (or its gist URL) first in any new chat,
> then name the files the task touches. Claude reads the real code on the
> branch — it does NOT patch from memory.
>
> **Refreshed:** 2026-06-26 · **Branch:** `phase-17c-tag-or-constant`
> · **HEAD:** `48422f2` · **Alembic head:** `0083_report_job_stages`

## Kickoff snippet (paste at top of a new chat)

> You are my Senior Architect + Lead Dev for InduVista. Learn the project
> from this PROJECT_KNOWLEDGE.md + the repo/gists below, follow the
> Operating rules (§6–§10) exactly, and **don't guess — read the real code
> on branch `phase-17c-tag-or-constant` first**. Task: ‹…›.

---

## Quick read for Claude
Read this whole file before doing anything else in a new chat. Most of
what you'd otherwise have to ask is here. When something below conflicts
with what you'd guess from training data, **this file wins**.

---

## 1. What this project is

**Product**: INDUVISTA — industrial reporting / SCADA-adjacent tool for
plant operators. Collects sensor data from PLCs over Modbus and OPC UA,
stores in a time-series DB, evaluates calc tags and alarms, renders
heatmaps and dashboards, exports PDF/Excel reports.

**Status**: Active development. Latest line of work is the **report
observability stack** — per-stage report telemetry, run-history health
panel, missed-report detection, and a Reports×Time / Reports×Stage
**heatmap with click-to-detail** (HEAD `48422f2`). This sits on top of
the BI Explorer + Reports trigger engine (Phase 22+), the Reports render
pipeline (Phase 21), and Phase OPC-web.2.3 (server clock drift +
`trust_server_timestamp`).

**User context**: Sole developer. Host is **Windows + PowerShell, IST
timezone**. Postgres + workers run **UTC internally**. The user decides
when to stop — never propose deferring.

---

## 2. Stack & infrastructure

**Backend**: FastAPI + SQLAlchemy + Alembic, Python 3.11+ (3.12 supported)
**Database**: PostgreSQL with TimescaleDB extension (hypertables + CAGGs)
**Cache**: Valkey (Redis-compatible)
**Frontend**: React 19 + Vite 6 + TypeScript + TanStack Query + Tailwind 4
**Reports**: Jinja2 + WeasyPrint 65.1 (Pango 1.56 compat fix in 84291a3)
**Orchestration**: Docker Compose (single `docker-compose.yml` at repo root)

**Container roster**:
| Container | Role |
|---|---|
| svj_postgres | TimescaleDB |
| svj_valkey | Redis-compatible cache |
| svj_backend | FastAPI app, alembic, scheduler |
| svj_opc_worker | OPC UA subscription handler |
| svj_modbus_worker | Modbus polling |
| svj_modbus_simulator | dev-only Modbus device sim |
| svj_calc_evaluator | calc tag evaluator |
| svj_alarm_evaluator | alarm evaluator |
| svj_report_scheduler | timed + tag-triggered report rendering & delivery |

Workers are **profile-gated** in compose — bring up only what the task needs.

**DB credentials** (dev): user `induvista_admin`, db `induvista`.

**Repo root on host**: `C:\INDUVISTA`  *(PowerShell primary; Bash tool also available)*
**GitHub repo** (public): https://github.com/SantoshJadhavXX82/INDUVISTA

---

## 3. Codebase reference & sharing layers

The repo is **public**, so the fastest path is usually: paste this doc,
name the files, and let Claude `git clone --branch phase-17c-tag-or-constant`
and read them directly. Gists are a convenience for pinning an exact
snapshot; the **branch name is the one thing that must be correct**.

**Three layers**
1. **PROJECT_KNOWLEDGE.md** (this file) — the priming doc. Small, stable;
   refresh only the volatile bits (HEAD, alembic head, phase, gist URLs).
2. **Gists** — one per key file, for an exact pinned snapshot. Refresh to
   match disk before a session that depends on them.
3. **GitHub repo + branch** — source of truth.
4. **Graphify** — dependency neighborhood of a change (who calls what).
   Artifacts live in `graphify-out/` (`GRAPH_REPORT.md`, `graph.json`).

**Live links**
| Layer | URL |
|---|---|
| GitHub repo (public) | https://github.com/SantoshJadhavXX82/INDUVISTA |
| This branch | https://github.com/SantoshJadhavXX82/INDUVISTA/tree/phase-17c-tag-or-constant |
| Priming-doc gist (this file) | https://gist.github.com/SantoshJadhavXX82/7668d53db0c711093d1bfc2395287492 |
| Graphify graph gist (GRAPH_REPORT.md) | https://gist.github.com/SantoshJadhavXX82/0361d9d2976fd9d70d851907a3eb4e9e |
| Source-bundle INDEX gist | ‹optional: regenerate per-file bundles if you want pinned snapshots› |

> ⚠️ The per-file gist URLs in older copies of this doc are a **2026-06-04
> snapshot** and predate ~134 commits of work. Treat them as historical.
> **Read from the branch**, not those gists, unless you've just refreshed
> them. `gh` 2.95.0 is installed and authed on this host as
> `SantoshJadhavXX82` (scopes: `gist`, `repo`); refresh a gist with
> `gh gist edit <id> <file>` or create one with `gh gist create <file> --public`.

**Fallback read paths if a CDN is blocked**
1. `git clone https://github.com/SantoshJadhavXX82/INDUVISTA.git` (public).
2. GitHub REST: `GET https://api.github.com/repos/SantoshJadhavXX82/INDUVISTA/contents/<path>?ref=phase-17c-tag-or-constant`.
3. Each gist's HTML page (one file per gist).

**Key code locations** (read from branch before planning a patch)
| Purpose | Path |
|---|---|
| OPC worker | `backend/app/workers/opc_supervisor.py` |
| Modbus worker | `backend/app/workers/modbus_supervisor.py` |
| Report scheduler / trigger engine | `backend/app/workers/report_scheduler.py`, `report_schedule.py` |
| Reports API | `backend/app/api/reports.py`, `reports_config.py` |
| BI Explorer API / service | `backend/app/api/bi.py`, `backend/app/services/bi_query.py` |
| OPC sources API | `backend/app/api/opc_sources.py` |
| Diagnostics API (report health) | `backend/app/api/diagnostics.py` |
| DB session | `backend/app/db.py` |
| Historian (Sample, HistorianWriter) | `backend/app/historian.py` |
| Frontend Reports pages | `frontend/src/pages/Reports.tsx`, `ReportsConfig.tsx`, `ReportTriggers.tsx`, `ReportDestinations.tsx` |
| Frontend BI Explorer | `frontend/src/pages/Explorer.tsx` |
| Frontend Diagnostics (heatmap/health) | `frontend/src/pages/Diagnostics.tsx` |
| Frontend types | `frontend/src/types/api.ts` |
| Alembic migrations | `backend/alembic/versions/` (head: `0083_report_job_stages`) |

---

## 4. Domain conventions (do not assume from training data)

### Quality status byte
InduVista uses **OPC DA Quality** convention, NOT the OPC UA spec:
- `0-63` INVALID (red, cell=1)
- `64-127` SUSPECT (amber, cell=2)
- `128-191` VALID (green, cell=3)
- `192-255` VALID_EXTENDED (green, cell=3)
- `st >= 128` is GOOD

Internal codes: `ST_NEVER_READ=0, ST_COMM_TIMEOUT=4, ST_DECODE_FAIL=8,
ST_RETRY_EXHAUSTED=24, ST_STALE=64, ST_RANGE_WARN=68, ST_READ_OK=128,
OPC_QUALITY_GOOD=192`.

UA severity mapping in `_ua_status_to_st`: `Good → 192, Uncertain → 96,
Bad → 0`. Full StatusCode hex preserved in `st_reason`.

### Heatmap cell values
`0 = no data (grey), 1 = invalid, 2 = suspect, 3 = valid`. Soft-deleted
tags excluded.

### Report job/stage status (report observability stack)
Report runs are tracked in `report_jobs` / `report_job_stages`. A stale
skip is recorded as **`missed`** (no backfill — a missed window stays
missed). The Diagnostics page surfaces run history, a missed badge, and
the Reports×Time / Reports×Stage heatmap with click-to-detail.

### Timestamp policy
- Modbus: ingest-time UTC (worker clock) — no server timestamp exists.
- OPC UA: configurable per-source via `opc_sources.trust_server_timestamp`
  - `FALSE` (default, safe): worker uses `datetime.now(timezone.utc)` at ingest
  - `TRUE` (opt-in): worker uses `DataValue.SourceTimestamp` from server
- Server clock drift measured at every subscription activation and
  persisted in `opc_sources.last_server_clock_drift_sec` +
  `last_server_clock_check_at`.
- `app_timezone` setting is **display-only** — used in heatmap/calendar SQL
  bucketing, never in ingestion timestamp coercion.

---

## 5. Devices and sources currently configured

### OPC UA
| Source | id | device_id | channel_id | endpoint | tag IDs | notes |
|---|---|---|---|---|---|---|
| Plant-A-UA | 1 | 82 | 12 | opc.tcp://host.docker.internal:14840 | 2269-2271, 2280-2290 (14 tags) | AGG SoftBus sim. SourceTimestamp historically drifted. `trust_server_timestamp = FALSE`. |

### Modbus
| Source | notes |
|---|---|
| FLOWCOMP (ActiveAlarmCode tag id=134) | working, ingest-time UTC. Use as control when debugging OPC. |

---

## 6. Operating rules — patch & deploy

### Patch script pattern (CRLF-aware, idempotent, anchored)
Every backend/frontend modification ships as a Python patch script that:
1. **Reads actual disk content first** and validates anchor strings against
   it (Windows = CRLF; gist HTML normalizes whitespace differently — verify
   anchors against a raw `Get-Content` dump, never against memory).
2. Writes a `.bak_<phase>` backup on first run.
3. Applies via `str.replace(anchor, replacement, 1)`.
4. Embeds an **idempotency marker** in the replacement so re-runs skip cleanly.
5. Reports each applied patch by label.

Phase numbering convention: backup suffix `bak_<phase>`, marker
`# Phase <phase> <description>`.

### Branching policy — ⚠️ main has DIVERGED
Intended policy (option 1): work on **`phase-17c-tag-or-constant`**, then
fast-forward `main` after every verified commit so a fresh clone of the
default branch stays current:
```
git push origin phase-17c-tag-or-constant:main
```
**Current reality (2026-06-26):** `main` is at `be446af` and has DIVERGED
— the branch is ~134 commits ahead while `main` carries ~42 commits not on
the branch. A plain FF-push will be rejected. Before relying on `main`,
reconcile the histories deliberately (merge or a force-update you've
confirmed is safe) — do **not** silently `--force` without flagging it.
Until reconciled, **`phase-17c-tag-or-constant` is the only current branch**;
clone with `--branch phase-17c-tag-or-constant`.

### Deploy order (restart vs rebuild)
1. Drop alembic migration into `backend/alembic/versions/`.
2. `docker exec svj_backend alembic upgrade head`.
3. Run patch scripts on host (they modify `C:\INDUVISTA\...` files).
4. **Restart** if only interpreted code changed:
   `docker restart svj_backend svj_opc_worker` (or whichever).
   **Rebuild** (`docker compose build … && up -d`) only if deps,
   Dockerfile, or compiled assets changed.
5. Verify within 30s: worker log shows expected lines.
6. Verify within 60s: samples landing as expected.
7. **Build-gate the commit** — only commit after the relevant build/tests
   pass (`smoke_test` / health / `psql`), never before.

### Verification queries — always time-filter `tag_values`
**Never** run `SELECT COUNT(*) FROM tag_values` without
`WHERE time > NOW() - INTERVAL ...` — it scans every chunk and can wedge
the table.
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
`MAX(time)` alone is misleading when samples may be future-stamped —
check MIN **and** MAX, or use windowed queries on real time.

### Cleanup conventions
- Prefer **TRUNCATE over DELETE** on hypertables when workers can be stopped.
- Stop workers before a bulk DELETE: `docker stop svj_modbus_worker svj_opc_worker`.
- Never `pg_terminate_backend` a multi-hour DELETE without expecting an
  autovacuum storm afterward.

---

## 7. Conversational preferences

- **I decide when to stop.** Don't suggest "let's pick this up tomorrow."
- **Read full files before patching.** Snippets / grep output aren't enough.
- **Validate anchors against raw `Get-Content`** before writing patch scripts.
- **Diagnostic discipline:** 1–3 targeted diagnostics, one hypothesis at a
  time. No "diagnostic theater" of ten adjacent queries.
- **Verify after deploy:** worker logs within 30s, then sample flow within
  60s via a windowed query. If either fails, revert immediately — don't
  stack patches.

---

## 8. Active phase status

### Current — report observability stack (`48422f2` and ancestors)
- **`48422f2`** — Reports×Time + Reports×Stage heatmap with click-to-detail
- **`6a8cc10`** — per-stage report telemetry + `next_instant` (countdown source) + endpoints
- **`f60ecc1`** — Report health panel on Diagnostics (run history + missed badge, per-report drill-down)
- **`3c2f658`** — missed-report detection (record stale skips as `missed`; no backfill)
- **`51a5787`** — store-and-forward replay isolates poison rows (no stuck backlog)
- Supporting migrations: `0081_report_trigger_run_at`,
  `0082_report_jobs_missed_status`, `0083_report_job_stages`
- UI extras nearby: command palette (Ctrl/Cmd+K) + header Search,
  one-shot/future report trigger UI, row-level delete on Channels/Devices,
  route-level code-splitting + skeleton loaders + toasts.

### Recent history
- **BI Explorer + Reports trigger engine** (Phase 22+): `bi.py` /
  `bi_query.py`, `Explorer.tsx`; trigger builder v2/v3, output formats,
  destinations model; report_tags so scheduled reports render with their
  tag data (migrations 0062–0079 area).
- **Phase 21 — Reports render pipeline** (`84291a3`): Jinja2 + WeasyPrint
  65.1, Pango 1.56 compat fix, block builder + calc engine, migration
  `0061_report_blocks`.
- **Phase OPC-web.2.3**: migrations 0055/0056, server clock drift probe,
  `trust_server_timestamp` toggle, `docs/opc_quality_and_timestamps.md`.
- Phase OPC-web.2.2: OPC UA address-space browse & bulk import (Kepware).
- Phase 19: dark mode, mobile nav, theming, tag-quality CAGG (migration 0044).

### Known open items
- `main` diverged from the working branch (see §6 branching policy) — reconcile.
- A 432 MB DB dump (`induvista-db-20260514-1101.dump`) lives in the repo
  tree; consider purging from history with `git filter-repo` and rotating
  any credentials it exposed during prior public windows.
- SF buffer replay path edge cases (poison-row isolation landed in `51a5787`).

---

## 9. Things I want Claude to do automatically
- Read this file at chat start, then the branch files relevant to the task.
- Search past conversations when I reference "the X we did" — don't re-ask.
- Verify anchors against raw disk content before writing patch scripts.
- Default to TRUNCATE over DELETE on hypertables when workers can be stopped.
- Use time-windowed queries when checking sample flow.
- Keep `main` reconciled with the working branch — flag drift, don't hide it.

## 10. Things I want Claude to NOT do
- Suggest deferring work to tomorrow (I decide when to stop).
- Run unfiltered `SELECT COUNT(*)` on hypertables.
- Patch from memory without reading the file.
- Roll back a patch on a single ambiguous data point — verify with a window first.
- Stack multiple patches before verifying each.
- Run 10 diagnostic queries when 2 would answer the question.
- Report "synced" without confirming `main`'s actual state.
