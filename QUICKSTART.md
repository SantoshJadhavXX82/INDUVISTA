# InduVista — Quick Start

How to take all the files I've shared and turn them into a running system. Written for someone new to Docker and Python — if any step doesn't make sense, ask.

## What you need first

- **Docker Desktop** must be installed and **running**. Open the Docker Desktop app and confirm the bottom-left status indicator says "Engine running" (a green dot).
- A **terminal**: PowerShell, Windows Terminal, CMD, or WSL Ubuntu — any of them work. Open it on demand; you'll only run a few commands.
- A **text editor**: VS Code is the easy choice. Notepad works in a pinch.

If Docker Desktop isn't installed: https://docs.docker.com/desktop/install/windows-install/

## Step 1 — Set up the project folder

Pick a location on disk. Two options:

- **Faster (recommended):** Inside WSL Ubuntu's home directory, like `~/projects/induvista`. You can access it from File Explorer via `\\wsl$\Ubuntu\home\<your-user>\projects\induvista`. This is faster because Docker Desktop runs through WSL2 and reading from inside WSL is much quicker than reading from `/mnt/c` or `/mnt/d`.
- **Easier to find but slower:** A regular Windows folder like `D:\projects\induvista`.

Either works. The first one is what I'd pick.

## Step 2 — Place the files

Your project folder needs this exact structure:

```
induvista/
├── .env                              ← config with secrets, never commit
├── .env.example                      ← template, safe to commit
├── .gitignore
├── README.md
├── docker-compose.yml
├── db/
│   └── init/
│       └── 01-extensions.sql
└── backend/
    ├── Dockerfile
    ├── requirements.txt
    ├── alembic.ini
    ├── alembic/
    │   ├── env.py
    │   ├── script.py.mako
    │   └── versions/
    │       └── 0001_baseline.py
    └── app/
        ├── __init__.py
        ├── config.py
        ├── db.py
        └── main.py
```

Download each file I've shared and put it in the matching path. The `__init__.py` file is empty — that's intentional, it marks `app` as a Python package.

## Step 3 — Set a real password

Open `.env` in your editor. Find these two lines:

```
POSTGRES_PASSWORD=change_this_password
DATABASE_URL=postgresql+psycopg2://svj_admin:change_this_password@postgres:5432/induvista
```

Replace `change_this_password` with a real password — **in both places**. They must match exactly, or the backend won't be able to log into Postgres. Example:

```
POSTGRES_PASSWORD=MyStr0ngPwd2026
DATABASE_URL=postgresql+psycopg2://svj_admin:MyStr0ngPwd2026@postgres:5432/induvista
```

Keep `.env` out of git — `.gitignore` already excludes it.

## Step 4 — First run

Open a terminal **inside the project folder** (the folder that has `docker-compose.yml` directly in it). Then run:

```
docker compose up --build
```

What that does:

- `--build` builds your backend image from the Dockerfile. Only needed the first time and whenever `requirements.txt` changes.
- `up` starts the services that aren't behind a profile: `postgres`, `migrate`, `backend`.

The first run takes 3–5 minutes. Docker downloads the TimescaleDB image, builds your backend image, starts Postgres, waits for it to report healthy, runs the Alembic migration (creates all eleven tables), then starts the FastAPI backend.

You'll see logs scrolling. The lines that confirm success:

```
svj_postgres  | LOG:  database system is ready to accept connections
svj_migrate   | INFO  [alembic.runtime.migration] Running upgrade -> 0001_baseline, baseline schema (...)
svj_migrate exited with code 0
svj_backend   | INFO:     Uvicorn running on http://0.0.0.0:8000
svj_backend   | INFO:     Application startup complete.
```

The key line is `svj_migrate exited with code 0` — that means the schema was created successfully.

## Step 5 — Verify it's working

Open your browser to:

**http://localhost:8000/health**

You should see something like:

```json
{
  "status": "ok",
  "app_name": "InduVista",
  "app_env": "development",
  "app_timezone": "Asia/Kolkata",
  "db_latency_ms": 1.4,
  "migration_version": "0001_baseline"
}
```

If `migration_version` reads `"0001_baseline"`, Phase 0 is complete and the foundation is in place.

Also try **http://localhost:8000/docs** — that's FastAPI's auto-generated interactive API documentation. Not much there yet (just `/` and `/health`), but it grows with every phase.

## Day-to-day commands

All run from the project root.

| Goal | Command |
|------|---------|
| Start everything (foreground) | `docker compose up` |
| Start in background | `docker compose up -d` |
| Stop (if foreground) | `Ctrl+C` |
| Stop (if background) | `docker compose down` |
| Restart just the backend | `docker compose restart backend` |
| Watch logs from all services | `docker compose logs -f` |
| Watch logs from one service | `docker compose logs -f backend` |
| See past migration logs | `docker compose logs migrate` |
| Rebuild after dependency change | `docker compose up --build` |
| Open a Postgres shell | `docker compose exec postgres psql -U svj_admin -d induvista` |
| See what's running | `docker compose ps` |
| Wipe everything including data | `docker compose down -v` *(deletes the database)* |

## Troubleshooting

**`docker compose` not recognized.** Older Docker installs use `docker-compose` with a hyphen. Try that. If neither works, Docker Desktop isn't running.

**Build fails with pip/network errors.** Check your internet. The build downloads from `pypi.org`. If you're on a corporate network with a proxy, you'll need to configure Docker's proxy settings.

**Postgres won't start: port 5432 already in use.** Something else (maybe a local Postgres install) is using that port. Two fixes:
- Stop the other Postgres.
- Or edit `docker-compose.yml` and change `"127.0.0.1:5432:5432"` to `"127.0.0.1:5433:5432"`. Leave `DATABASE_URL` alone — it uses the internal Docker hostname `postgres:5432`, not the host port.

**`/health` returns 503.** Backend can't reach Postgres. Two common causes:
- Password mismatch between `POSTGRES_PASSWORD` and `DATABASE_URL` in `.env`. Re-check both.
- Postgres still starting. Wait 30 seconds and refresh.

Also check `docker compose logs backend` — the SQLAlchemy error will say exactly what failed.

**`migration_version` shows `null`.** The migration didn't run. Check `docker compose logs migrate` for the actual error. Most likely a bad `DATABASE_URL`. If the database is in a weird state from a failed first attempt, the cleanest fix is:

```
docker compose down -v
docker compose up --build
```

That wipes the database volume and starts fresh.

**Code changes don't take effect.** Files inside `backend/app/` are bind-mounted into the container with `--reload` enabled — changes to `.py` files should be picked up within a second or two. If they're not:
- Make sure you're editing the file at the *project* path, not inside the container.
- If on Windows, edit via the WSL path (`\\wsl$\Ubuntu\...`) rather than via `/mnt/c` or `/mnt/d`. The file watcher in WSL2 doesn't reliably see Windows-side filesystem events.

**Want to start completely fresh.** This deletes the database and forces everything to rebuild:

```
docker compose down -v
docker compose up --build
```

## What NOT to do yet

Don't run `docker compose --profile sim up` or `--profile workers up`. Those profiles activate the Modbus simulator and the polling/replay workers. Their code doesn't exist yet — those are Phase 1 and Phase 2 work. Activating them now will cause containers to crash-loop trying to import modules that aren't there.

## What's next

When `/health` returns `migration_version="0001_baseline"`, Phase 0 is verified. The next steps when you're ready:

- **Phase 1**: a Modbus simulator service and the first worker that polls it and writes into `tag_values` through the `HistorianWriter` abstraction.
- **Phase 2**: the store-and-forward fallback so the worker survives a Postgres restart with zero data loss.

I'll produce those files when you confirm Phase 0 is solid on your machine.
