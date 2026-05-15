#!/usr/bin/env bash
# Uninstall the InduVista host-stats agent systemd service.

set -euo pipefail

UNIT_NAME="induvista-host-agent"
UNIT_FILE="/etc/systemd/system/${UNIT_NAME}.service"

if [[ $EUID -ne 0 ]]; then
    echo "ERROR: must be run as root (sudo)." >&2
    exit 1
fi

if [[ ! -f "$UNIT_FILE" ]]; then
    echo "No unit at $UNIT_FILE. Nothing to do."
    exit 0
fi

systemctl stop "$UNIT_NAME" 2>/dev/null || true
systemctl disable "$UNIT_NAME" 2>/dev/null || true
rm -f "$UNIT_FILE"
systemctl daemon-reload

echo "Removed $UNIT_NAME. Diagnostics page will revert to scope='container' shortly."
