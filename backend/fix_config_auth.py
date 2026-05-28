"""Phase 21 — add auth settings to config.py (jwt_secret, token TTL).

Adds three settings. jwt_secret has an insecure dev default but the app
refuses to start in production with it unchanged.
"""
import sys, pathlib
TARGET = pathlib.Path("backend/app/config.py")
MARKER = "Phase 21 — auth settings"
OLD = '''    # Required: connection string for SQLAlchemy
    database_url: str

    model_config = SettingsConfigDict('''
NEW = '''    # Required: connection string for SQLAlchemy
    database_url: str

    # Phase 21 — auth settings.
    # jwt_secret signs session tokens. The dev default below is INSECURE;
    # set JWT_SECRET to a long random value in production (see .env.example).
    # validate() below refuses to start in production with the default.
    jwt_secret: str = "dev-insecure-change-me-in-production"
    auth_token_ttl_min: int = 720  # 12h — one plant shift + margin

    model_config = SettingsConfigDict('''

GUARD_OLD = '''settings = Settings()  # type: ignore[call-arg]'''
GUARD_NEW = '''settings = Settings()  # type: ignore[call-arg]

# Phase 21 — fail fast if production runs with the insecure default JWT secret.
if settings.app_env == "production" and settings.jwt_secret == "dev-insecure-change-me-in-production":
    raise RuntimeError(
        "JWT_SECRET is still the insecure development default but APP_ENV=production. "
        "Set JWT_SECRET to a strong random value (e.g. `openssl rand -hex 32`)."
    )'''

def main():
    if not TARGET.exists():
        print(f"  ERROR: {TARGET} not found."); return 1
    t = TARGET.read_text(encoding="utf-8")
    if MARKER in t:
        print("  [SKIP] already applied."); return 0
    for label, o in (("settings", OLD), ("guard", GUARD_OLD)):
        if t.count(o) != 1:
            print(f"  ERROR: {label} anchor found {t.count(o)}x. Aborting."); return 1
    bak = TARGET.with_suffix(".py.bak_phase21")
    if not bak.exists(): bak.write_text(t, encoding="utf-8"); print(f"  Backup: {bak.name}")
    t = t.replace(OLD, NEW, 1).replace(GUARD_OLD, GUARD_NEW, 1)
    TARGET.write_text(t, encoding="utf-8")
    print("  Applied: jwt_secret, auth_token_ttl_min, production guard")
    return 0

if __name__ == "__main__":
    sys.exit(main())
