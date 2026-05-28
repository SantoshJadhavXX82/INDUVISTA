"""Phase 21 — wire auth into main.py: register routers + bootstrap admin.

Adds:
  - app.include_router(auth.router)         /api/auth/*
  - app.include_router(users_admin.router)  /api/admin/users/*
  - a startup hook that bootstraps the first admin from env vars
"""
import sys, pathlib
TARGET = pathlib.Path("backend/app/main.py")
MARKER = "Phase 21 — auth routers"

# Register the two new routers right after the api_keys router (end of the
# router block, before the startup event).
OLD_1 = '''from app.api import ingest as _ingest
from app.api import api_keys as _api_keys
app.include_router(_ingest.router)
app.include_router(_api_keys.router)'''
NEW_1 = '''from app.api import ingest as _ingest
from app.api import api_keys as _api_keys
app.include_router(_ingest.router)
app.include_router(_api_keys.router)

# Phase 21 — auth routers: login/me/change-password + admin user management.
from app.api import auth as _auth
from app.api import users_admin as _users_admin
app.include_router(_auth.router)
app.include_router(_users_admin.router)'''

# Add bootstrap to startup, alongside the existing audit-schema hook.
OLD_2 = '''    try:
        ensure_audit_schema()
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(
            "AUDIT SCHEMA SETUP FAILED at startup: %s. "
            "Backend continues, but audit will not be recorded until resolved.",
            e,
        )'''
NEW_2 = '''    try:
        ensure_audit_schema()
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(
            "AUDIT SCHEMA SETUP FAILED at startup: %s. "
            "Backend continues, but audit will not be recorded until resolved.",
            e,
        )


@app.on_event("startup")
def _auth_bootstrap_startup() -> None:
    # Phase 21 — create the first admin from INDUVISTA_ADMIN_USER /
    # INDUVISTA_ADMIN_PASSWORD if no admin exists yet. Idempotent and
    # non-fatal (logs and continues on any error).
    from app.auth.bootstrap import bootstrap_admin
    bootstrap_admin()'''

def main():
    if not TARGET.exists():
        print(f"  ERROR: {TARGET} not found."); return 1
    t = TARGET.read_text(encoding="utf-8")
    if MARKER in t:
        print("  [SKIP] already applied."); return 0
    for label, o in (("router block", OLD_1), ("startup hook", OLD_2)):
        if t.count(o) != 1:
            print(f"  ERROR: {label} anchor found {t.count(o)}x. Aborting."); return 1
    bak = TARGET.with_suffix(".py.bak_phase21")
    if not bak.exists(): bak.write_text(t, encoding="utf-8"); print(f"  Backup: {bak.name}")
    t = t.replace(OLD_1, NEW_1, 1).replace(OLD_2, NEW_2, 1)
    TARGET.write_text(t, encoding="utf-8")
    print("  Applied: auth + users_admin routers, admin bootstrap startup hook")
    return 0

if __name__ == "__main__":
    sys.exit(main())
