# InduVista DataHub

Edge collector for [INDUVISTA](../README.md). Polls plant-floor OPC servers (OPC UA + OPC DA), buffers samples locally, and pushes them to the INDUVISTA backend over the authenticated `/api/ingest` endpoint.

## Status

Phase OPC.2 — **skeleton only**. The window opens, the config persists, the SQLite cache initializes. No OPC connection or push logic yet (those land in OPC.3 / OPC.4).

## Quick start

```powershell
cd D:\INDUVISTA\datahub-client

# 1. Create a virtual environment (Python 3.11+ required)
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. Install in editable mode + dependencies
pip install --upgrade pip
pip install -e .

# 3. Run
python -m induvista_datahub
```

The first run creates `%APPDATA%\InduVista\DataHub\` containing:

- `config.toml` — server URL, API key, OPC connection list, tag mappings
- `store_forward.db` — SQLite buffer of samples awaiting push
- `logs\datahub.log` — rotating log (10 MB × 5 backups)

## What the UI looks like

Three tabs:

- **Status** — connection health, sample counters, buffer size (placeholders for now)
- **Tags** — per-tag mapping table OPC NodeId/ItemId → INDUVISTA tag_id (placeholder)
- **Settings** — INDUVISTA server URL + API key. Persists to `config.toml`.

## Architecture

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the threading model, the 32-bit DA bridge subprocess, and the store-and-forward semantics.

## Roadmap

- **OPC.2** ✅ Skeleton (this)
- **OPC.3** OPC read layer — asyncua + 32-bit DA bridge
- **OPC.4** Push + store-and-forward — httpx client with retry, buffer drain
- **OPC.5** UI polish — onboarding wizard, OPC tag browser, live status
- **OPC.6** Packaging — PyInstaller + Inno Setup MSI, optional Windows service

## License

Internal — part of the INDUVISTA product. Not for redistribution.
