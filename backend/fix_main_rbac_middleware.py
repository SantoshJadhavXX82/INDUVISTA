"""Phase 21 — register the global RBAC middleware in main.py."""
import sys, pathlib
TARGET = pathlib.Path("backend/app/main.py")
MARKER = "Phase 21 - RBAC middleware"
OLD = '''app = FastAPI(
    title=settings.app_name,
    description="Industrial data acquisition and reporting tool.",
    version="0.3.0",
)'''
NEW = '''app = FastAPI(
    title=settings.app_name,
    description="Industrial data acquisition and reporting tool.",
    version="0.3.0",
)

# Phase 21 - RBAC middleware. Gates every /api route by HTTP method + path
# against the user's JWT role, in one auditable place. Public routes
# (login, /health, docs) and API-key routes (/api/ingest) are exempted
# inside the middleware's policy table. See app/auth/rbac_middleware.py.
from app.auth.rbac_middleware import RBACMiddleware
app.add_middleware(RBACMiddleware)'''

def main():
    if not TARGET.exists():
        print(f"  ERROR: {TARGET} not found."); return 1
    t = TARGET.read_text(encoding="utf-8")
    if MARKER in t:
        print("  [SKIP] already applied."); return 0
    if t.count(OLD) != 1:
        print(f"  ERROR: anchor found {t.count(OLD)}x. Aborting."); return 1
    # Note: this patch must run AFTER fix_main_auth_wiring.py (independent anchors).
    bak = TARGET.with_suffix(".py.bak_phase21_mw")
    if not bak.exists(): bak.write_text(t, encoding="utf-8"); print(f"  Backup: {bak.name}")
    TARGET.write_text(t.replace(OLD, NEW, 1), encoding="utf-8")
    print("  Applied: RBACMiddleware registered")
    return 0

if __name__ == "__main__":
    sys.exit(main())
