"""Root-logger configuration: rotating file handler + console handler.

Format includes the thread name because the DataHub has multiple
worker threads (UI / OPC reader / pusher) and tracing a bug across
them is much easier when you can see who logged what.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path


_FORMAT = (
    "%(asctime)s %(levelname)-5s [%(threadName)-12s] "
    "%(name)s: %(message)s"
)
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(
    logs_dir: Path,
    *,
    level: int = logging.INFO,
    max_bytes: int = 10 * 1024 * 1024,  # 10 MB
    backup_count: int = 5,
) -> None:
    """Install handlers on the root logger.

    Idempotent: calling twice (e.g. after a config reload changed the
    level) clears prior handlers and reinstalls — no duplicate log
    lines, no orphaned file descriptors.
    """
    root = logging.getLogger()
    root.setLevel(level)

    # Tear down any previously-installed handlers so this call is
    # idempotent. We explicitly close file handlers to release locks
    # on Windows.
    for h in list(root.handlers):
        root.removeHandler(h)
        try:
            h.close()
        except Exception:
            pass

    formatter = logging.Formatter(_FORMAT, datefmt=_DATE_FORMAT)

    # File handler — rotates at max_bytes, keeps backup_count old files.
    log_path = logs_dir / "datahub.log"
    fh = logging.handlers.RotatingFileHandler(
        log_path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    fh.setFormatter(formatter)
    fh.setLevel(level)
    root.addHandler(fh)

    # Console handler — stderr is the conventional choice for log
    # output (stdout reserved for program output).
    ch = logging.StreamHandler(sys.stderr)
    ch.setFormatter(formatter)
    ch.setLevel(level)
    root.addHandler(ch)

    # Tame third-party noise. asyncua in particular is chatty at INFO.
    for noisy in ("asyncua", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
