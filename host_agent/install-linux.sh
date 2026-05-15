#!/usr/bin/env bash
# Install the InduVista host-stats agent as a systemd service.
#
# Writes /etc/systemd/system/induvista-host-agent.service, enables it (so it
# starts on boot), and starts it immediately. After this, the agent runs
# independently of any login session -- closing your SSH connection does not
# affect it.
#
# Requires sudo (writes to /etc/systemd and reloads the daemon).
#
# Usage:
#   cd /path/to/induvista/host_agent
#   sudo ./install-linux.sh
#
# To uninstall:  sudo ./uninstall-linux.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT="$SCRIPT_DIR/agent.py"
REQS="$SCRIPT_DIR/requirements.txt"
UNIT_NAME="induvista-host-agent"
UNIT_FILE="/etc/systemd/system/${UNIT_NAME}.service"

echo
echo "=== InduVista host agent -- install (systemd) ==="
echo

# --- 1. Sanity --------------------------------------------------------------
if [[ ! -f "$AGENT" ]]; then
    echo "ERROR: agent.py not found at $AGENT" >&2
    exit 1
fi
if [[ ! -f "$REQS" ]]; then
    echo "ERROR: requirements.txt not found at $REQS" >&2
    exit 1
fi
if [[ $EUID -ne 0 ]]; then
    echo "ERROR: must be run as root (sudo). Need to write to /etc/systemd." >&2
    exit 1
fi

# --- 2. Find Python ---------------------------------------------------------
# Prefer python3 -- most Linux distros ship that as the canonical name.
PYTHON="$(command -v python3 || true)"
if [[ -z "$PYTHON" ]]; then
    echo "ERROR: python3 not found on PATH. Install it first." >&2
    exit 1
fi
echo "Using Python: $PYTHON ($($PYTHON --version 2>&1))"

# --- 3. Install dependencies ------------------------------------------------
# Install system-wide so the systemd service (running as root or a service
# account) can find them. On distros that block global pip with PEP 668,
# fall back to --break-system-packages -- fine for a controlled deployment.
echo "Installing dependencies..."
if ! "$PYTHON" -m pip install -r "$REQS" 2>/dev/null; then
    echo "(retrying with --break-system-packages for PEP-668 distros)"
    "$PYTHON" -m pip install --break-system-packages -r "$REQS"
fi

# --- 4. Pick the user the service runs as -----------------------------------
# Default to the user who invoked sudo. If that's root (someone logged in as
# root directly), keep running as root.
RUN_USER="${SUDO_USER:-root}"
echo "Service will run as user: $RUN_USER"

# --- 5. Write the unit file -------------------------------------------------
cat > "$UNIT_FILE" <<EOF
[Unit]
Description=InduVista host-stats agent -- posts CPU/RAM/disk/GPU to backend
Documentation=https://github.com/induvista/host_agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$RUN_USER
WorkingDirectory=$SCRIPT_DIR
ExecStart=$PYTHON $AGENT

# Restart policy: always come back, with a 10s cooldown so we don't
# hammer a broken backend.
Restart=always
RestartSec=10

# Resource clamps -- the agent's footprint should be tiny.
MemoryMax=200M
CPUQuota=50%

# Logging goes to journald by default; view with:  journalctl -u $UNIT_NAME -f
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

chmod 644 "$UNIT_FILE"
echo "Wrote $UNIT_FILE"

# --- 6. Enable + start ------------------------------------------------------
systemctl daemon-reload
systemctl enable "$UNIT_NAME" >/dev/null
systemctl restart "$UNIT_NAME"

sleep 5

# --- 7. Show status + verify backend ---------------------------------------
echo
systemctl --no-pager --lines=10 status "$UNIT_NAME" || true

echo
if curl -sf "http://localhost:8000/api/diagnostics/system-stats" >/dev/null 2>&1; then
    SCOPE=$(curl -s "http://localhost:8000/api/diagnostics/system-stats" \
            | python3 -c "import sys,json; print(json.load(sys.stdin)['scope'])")
    if [[ "$SCOPE" == "host" ]]; then
        echo "[OK] Backend now reads scope=host"
    else
        echo "[!] Backend still reports scope=$SCOPE. Wait ~10s and try:"
        echo "    curl -s http://localhost:8000/api/diagnostics/system-stats | jq .scope"
    fi
else
    echo "[!] Backend on http://localhost:8000 is unreachable. The service is registered"
    echo "  and will keep retrying -- once the backend is up, scope flips to 'host'."
fi

echo
echo "What you've got now:"
echo "  - Agent auto-starts on boot (no login required)."
echo "  - Restarts within 10s if it crashes."
echo "  - Survives SSH disconnects, user logouts, anything short of system shutdown."
echo
echo "Useful commands:"
echo "  Status : systemctl status $UNIT_NAME"
echo "  Logs   : journalctl -u $UNIT_NAME -f"
echo "  Stop   : sudo systemctl stop $UNIT_NAME"
echo "  Start  : sudo systemctl start $UNIT_NAME"
echo "  Remove : sudo ./uninstall-linux.sh"
echo
