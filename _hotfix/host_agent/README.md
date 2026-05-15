# InduVista host-stats agent

A small background process that runs on the **host OS** (Windows or Linux)
and posts real CPU / RAM / disk / GPU / process stats to the InduVista
backend every 5 seconds. The Diagnostics page picks up these pushes and
shows them in place of the container-level fallback — that's how you get
Task Manager / `top` parity in the UI.

## Install — Windows (one command, permanent)

```powershell
cd D:\INDUVISTA\host_agent
.\install-windows.ps1
```

That's it. The script:

1. Ensures pip is bootstrapped on your existing Python install
   (`python -m ensurepip` — fixes the "No module named pip" error)
2. Installs `psutil` and `nvidia-ml-py` to your user site-packages
   (no admin required)
3. Registers a **Scheduled Task** called `InduVistaHostAgent` that runs
   `pythonw.exe agent.py` (windowless) every time you log in
4. Configures auto-restart on crash (within 1 minute)
5. Starts the task and verifies the backend now reports `scope: host`

After this you can **close every PowerShell window, every terminal, sign
out, sign back in** — the agent stays running. It only stops if you
manually stop it or shut down the machine.

To manage it later:

| Action      | Command                                                |
| ----------- | ------------------------------------------------------ |
| See status  | `Get-ScheduledTask -TaskName InduVistaHostAgent`       |
| Stop temp.  | `Stop-ScheduledTask -TaskName InduVistaHostAgent`      |
| Start again | `Start-ScheduledTask -TaskName InduVistaHostAgent`     |
| Remove      | `.\uninstall-windows.ps1`                              |
| GUI         | `taskschd.msc` → Task Scheduler Library → InduVistaHostAgent |

### Want it running *before* anyone logs in?

The default trigger is "at logon for your user". If you want the agent
running even when no one is signed into the server (typical for a
production deployment), edit the registered task in Task Scheduler:

1. `taskschd.msc` → InduVistaHostAgent → **Properties**
2. General tab → check **Run whether user is logged on or not** and
   **Run with highest privileges**
3. Triggers tab → change "At log on" to **At startup**

## Install — Linux (systemd)

```bash
cd /opt/induvista/host_agent
sudo ./install-linux.sh
```

The script:

1. Installs `psutil` and `nvidia-ml-py` via `pip` (falls back to
   `--break-system-packages` on PEP 668 distros)
2. Writes `/etc/systemd/system/induvista-host-agent.service` running as
   the invoking user (or root if you sudo'd from root)
3. `systemctl enable --now` — the service starts immediately AND on every
   subsequent boot
4. Verifies the backend now reports `scope: host`

To manage it later:

| Action      | Command                                                 |
| ----------- | ------------------------------------------------------- |
| See status  | `systemctl status induvista-host-agent`                 |
| Live logs   | `journalctl -u induvista-host-agent -f`                 |
| Stop        | `sudo systemctl stop induvista-host-agent`              |
| Start       | `sudo systemctl start induvista-host-agent`             |
| Remove      | `sudo ./uninstall-linux.sh`                             |

## Configuration

Override defaults via environment variables. On Windows, set them in your
user environment before running the install script. On Linux, edit the
systemd unit's `[Service]` section and add `Environment=` lines.

| Variable             | Default                 | Purpose                                       |
| -------------------- | ----------------------- | --------------------------------------------- |
| `INDUVISTA_URL`      | `http://localhost:8000` | Backend base URL. Use the LAN IP if running   |
|                      |                         | the agent on a different machine than the backend. |
| `INDUVISTA_PUSH_SEC` | `5.0`                   | Push interval in seconds. Lower = more       |
|                      |                         | responsive UI; higher = lower agent CPU.      |

## Manual run (for testing only — NOT for deployment)

If you just want to see the agent print and verify it works before
installing it permanently:

```powershell
# Windows — needs Python with pip working
python -m ensurepip --upgrade
python -m pip install --user -r requirements.txt
python agent.py
```

```bash
# Linux
python3 -m pip install -r requirements.txt
python3 agent.py
```

**Important:** this binds to your shell. Closing the terminal kills the
agent. For anything beyond a quick test, use the install scripts above.

## What gets sent

One JSON POST every 5 seconds to `/api/diagnostics/host-stats`:

- Hostname + platform (`Windows` / `Linux` / `Darwin`)
- Host OS uptime
- CPU percent, core counts, load averages (Unix only)
- Memory: total / used / available / cached — matches Task Manager numbers
- Every real, non-pseudo filesystem with usage details (one entry per drive)
- NVIDIA GPU stats if present
- Top 10 processes by CPU

No InduVista tag data, no credentials, no secrets — only host metrics.

## GPU support

NVIDIA only today, via `nvidia-ml-py` (a.k.a. pynvml). If you don't have
an NVIDIA GPU, the agent detects the missing driver / library and reports
an empty `gpus` array — everything else works.

To remove GPU support entirely (e.g. on a server with no GPU at all),
delete the `nvidia-ml-py` line from `requirements.txt` before running the
install script.

AMD and Intel GPUs aren't covered today. If you need them, extend
`collect_gpus()` in `agent.py` — the rest of the pipeline (POST, cache,
UI) is GPU-vendor-agnostic.

## Troubleshooting

**Diagnostics page still says "Container fallback" after install**

Check the agent is actually running:

```powershell
# Windows
Get-ScheduledTask -TaskName InduVistaHostAgent | Get-ScheduledTaskInfo
# 'LastTaskResult' should be 267009 (running) or 0 (last run completed OK)
```

```bash
# Linux
systemctl status induvista-host-agent
journalctl -u induvista-host-agent -n 20
```

Then re-check the endpoint:

```powershell
Invoke-RestMethod http://localhost:8000/api/diagnostics/system-stats | Select scope, hostname
```

If `scope=container` but `host_agent_last_seen_sec` is non-null, the
agent IS posting but the cache aged out (>30s). Usually means the agent
crashed and restarted; wait 10s and check again.

**`pynvml.NVMLError_LibraryNotFound` in the agent logs**

`nvidia-ml-py` is installed but no NVIDIA driver is on the system. Either
install the driver, or remove `nvidia-ml-py` from `requirements.txt` and
re-run the install script.

**Permission denied reading some processes (Windows)**

Some Windows system processes require admin to introspect. Either edit
the Scheduled Task to run with highest privileges (see "Want it running
before anyone logs in?" above), or accept that those rows are missing —
the rest of the snapshot is unaffected.
