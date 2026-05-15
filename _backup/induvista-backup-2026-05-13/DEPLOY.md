# InduVista — Apply this backup

Two paths: (A) **full restore** if you're recovering from a broken state,
or (B) **selective apply** if you just want to verify nothing's missing
from a working install.

## A — Full restore (broken or fresh install)

```powershell
# 0. Stop the stack so file copies aren't fighting live processes.
cd D:\INDUVISTA
docker compose down

# 1. Unzip the backup. It mirrors the project layout — files land in the
#    right places automatically.
Expand-Archive `
  -Path "$env:USERPROFILE\Downloads\induvista-backup-2026-05-13.zip" `
  -DestinationPath ".\_restore" -Force

# 2. Copy backend/frontend source over the existing tree.
Copy-Item -Path ".\_restore\induvista-backup-2026-05-13\backend\*" `
          -Destination ".\backend\" -Recurse -Force
Copy-Item -Path ".\_restore\induvista-backup-2026-05-13\frontend\*" `
          -Destination ".\frontend\" -Recurse -Force

# 3. Rebuild and start. The migrate sidecar runs alembic upgrade head
#    automatically on startup.
docker compose up -d --build

# 4. Wait for healthy and run the smoke test.
Start-Sleep -Seconds 30
docker compose cp `
  ".\_restore\induvista-backup-2026-05-13\smoke_tests\smoke_test_all.py" `
  backend:/tmp/smoke.py
docker compose exec backend python /tmp/smoke.py
docker compose exec backend rm /tmp/smoke.py
```

Expect "All checks passed" with possibly some SKIPs for live-device
scenarios if your GC is offline at test time.

## B — Selective apply (verify a working stack)

If your stack is already running and you just want to re-apply specific
files (or compare against the backup), copy only what you need from
`backend/` or `frontend/`. After any change:

```powershell
# Backend changes
docker compose build --no-cache backend modbus_worker
docker compose up -d --force-recreate backend modbus_worker

# Frontend changes
docker compose build --no-cache frontend
docker compose up -d --force-recreate frontend
```

Hard-refresh the browser (Ctrl+F5) after a frontend rebuild to bust the
asset cache.

## SQL — when to re-run

The migrations under `sql/` are **NOT** part of the regular alembic chain
— they're one-shot scripts that fix existing data:

- **`fix_mole_addressing.sql`** — apply *only* if you have 16 mole-%
  tags configured at gap=2 addresses (7001, 7003, 7005, ..., 7031) and
  want to migrate them to gap=1 (7001, 7002, 7003, ..., 7016). Safe to
  re-run; the formula `address = (address + 7001) / 2` is idempotent
  after the first application (a tag at 7001 stays at 7001).
- **`tags_core.sql`** — applies the 108 fiscal-essential 700XA tags.
  Idempotent via `ON CONFLICT (device_id, name) DO NOTHING`. Re-running
  on a populated DB is a no-op.

Apply either via:
```powershell
docker compose cp .\_restore\induvista-backup-2026-05-13\sql\<file>.sql `
  postgres:/tmp/x.sql
docker compose exec postgres psql -U induvista_admin -d induvista -f /tmp/x.sql
docker compose exec postgres rm /tmp/x.sql
```

## Disaster recovery (full point-in-time restore)

This backup contains code only. For full DR, combine with a database
dump:

```powershell
# Snapshot the DB (any time, while running):
docker compose exec -T postgres pg_dump `
  -U induvista_admin -d induvista -Fc `
  > "induvista-db-$(Get-Date -Format yyyyMMdd-HHmm).dump"

# Restore (with stack stopped, into a clean volume):
docker compose down -v
docker compose up -d postgres
Start-Sleep -Seconds 8
Get-Content induvista-db-YYYYMMDD-HHMM.dump | `
  docker compose exec -T postgres pg_restore `
    -U induvista_admin -d induvista --clean --if-exists
docker compose up -d
```

Keep the code backup and the DB dump together in your DR archive.

## Rollback

Each Phase shipped as a small zip — see the project's deploy history.
The Phase-by-Phase rollback path: re-apply the previous phase's zip
from `~/.induvista-deploys/` (or wherever your deploy zips live).
Worker and backend each restart cleanly without manual intervention;
the historian state in Postgres is unaffected by code rollbacks.

If you need to roll back a SQL migration, the right approach depends
on the specific migration:
- `fix_mole_addressing.sql` can be undone by inverting the address
  remap: `UPDATE tags SET address = (address - 7001) * 2 + 7001 WHERE
  ...` — but only meaningful if you'd already migrated.
- `tags_core.sql` rolls back via `DELETE FROM tags WHERE name LIKE
  'WEIGHT_%' OR name LIKE 'ISO_%' ...` — list out the names you want
  removed. Safer: just disable the tags rather than deleting them.

## What this backup does NOT include

- The `.env` file (secrets — keep that backed up separately)
- The `svj_postgres_data` volume (use `pg_dump`)
- Frontend `node_modules` (rebuilds from `package.json`)
- The actual Daniel/Emerson `700XA_-_UK_MODBUS_Listing.xls` source file
  (we ship the derived tag pack, not the original XLS — that one's a
  vendor document, not something to redistribute)
