"""Per-user data directory resolution.

Windows:  %APPDATA%\\InduVista\\DataHub\\
Linux:    ~/.local/share/induvista-datahub/        (dev runs)
macOS:    ~/Library/Application Support/InduVista/DataHub/  (dev runs)

We don't use QStandardPaths because the bootstrap in app.py needs the
data dir resolved BEFORE QApplication exists (for logging setup), and
QStandardPaths requires QCoreApplication to be alive.

Production target is Windows only; the Linux/macOS branches exist so
contributors can dev-run on those platforms without surprises.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


# Org / app names — must match what app.py sets on QApplication, since
# Windows treats both as part of the per-user AppData layout.
ORG_NAME = "InduVista"
APP_NAME = "DataHub"


@dataclass(frozen=True)
class Paths:
    """All filesystem locations used by the DataHub client."""
    data_dir: Path
    logs_dir: Path
    config_path: Path
    store_forward_path: Path


def _platform_data_dir() -> Path:
    """Resolve the per-user data dir for the current OS."""
    if sys.platform == "win32":
        # %APPDATA% always exists on Windows. Use the raw env var
        # rather than `Path.home() / "AppData/Roaming"` to respect
        # any user redirection (folder redirection, OneDrive backup).
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / ORG_NAME / APP_NAME
        # Fallback if APPDATA is missing (shouldn't happen on Windows
        # but the type system makes us handle it).
        return Path.home() / "AppData" / "Roaming" / ORG_NAME / APP_NAME

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / ORG_NAME / APP_NAME

    # Linux / BSD — follow XDG. Falls back to ~/.local/share if XDG isn't set.
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "induvista-datahub"


def get_paths() -> Paths:
    """Compute the paths bundle for this user. Does NOT create any
    directories — see ensure_data_dirs() for that."""
    data = _platform_data_dir()
    return Paths(
        data_dir=data,
        logs_dir=data / "logs",
        config_path=data / "config.toml",
        store_forward_path=data / "store_forward.db",
    )


def ensure_data_dirs() -> Paths:
    """Compute paths AND mkdir -p the directories that need to exist
    before anything tries to write to them (the data dir itself and
    its logs/ subdir). Returns the Paths bundle for further use.

    Safe to call on every startup — mkdir uses exist_ok=True.
    """
    paths = get_paths()
    paths.data_dir.mkdir(parents=True, exist_ok=True)
    paths.logs_dir.mkdir(parents=True, exist_ok=True)
    return paths
