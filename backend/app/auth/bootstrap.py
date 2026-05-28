"""First-admin bootstrap (Phase 21).

Env-var bootstrap — the standard for containerized apps. On startup, if
INDUVISTA_ADMIN_USER + INDUVISTA_ADMIN_PASSWORD are set AND no enabled
admin exists yet, create the admin. Idempotent: does nothing once an
admin exists, so leaving the vars set is harmless across restarts.

No default password is ever shipped. If the vars are unset and no admin
exists, the app logs a clear warning and starts (so a fresh deploy isn't
dead-on-arrival); all mutating routes simply remain inaccessible until an
admin is bootstrapped.

The seeded admin is created with must_change_password=TRUE, so the
operator is forced to set a real password on first login.
"""
from __future__ import annotations

import logging
import os

from sqlalchemy import text

from app.auth.security import hash_password
from app.db import SessionLocal

log = logging.getLogger(__name__)


def bootstrap_admin() -> None:
    """Create the first admin from env vars if none exists. Idempotent."""
    try:
        with SessionLocal() as db:
            existing = db.execute(
                text("SELECT COUNT(*) FROM users WHERE role = 'admin' AND is_enabled = TRUE")
            ).scalar()
            if existing and existing > 0:
                log.info("auth bootstrap: admin already present; skipping.")
                return

            user = os.environ.get("INDUVISTA_ADMIN_USER")
            password = os.environ.get("INDUVISTA_ADMIN_PASSWORD")
            if not user or not password:
                log.warning(
                    "auth bootstrap: no admin exists and INDUVISTA_ADMIN_USER / "
                    "INDUVISTA_ADMIN_PASSWORD are not set. Set them and restart "
                    "to create the first admin. Mutating API routes will reject "
                    "all requests until an admin is created."
                )
                return

            db.execute(
                text("""
                    INSERT INTO users (username, auth_provider, password_hash,
                                       role, must_change_password, created_by)
                    VALUES (:u, 'local', :h, 'admin', TRUE, 'bootstrap')
                    ON CONFLICT (username) DO NOTHING
                """),
                {"u": user, "h": hash_password(password)},
            )
            db.commit()
            log.info(
                "auth bootstrap: created admin %r (must change password on first login).",
                user,
            )
    except Exception as e:
        # Never let bootstrap crash app startup (e.g. migration not yet run).
        log.error("auth bootstrap failed (non-fatal): %s", e)
