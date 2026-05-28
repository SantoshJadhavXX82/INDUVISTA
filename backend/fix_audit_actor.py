"""Phase 21 — wire the logged-in user into the audit trail.

_extract_actor() currently only records IP (the code even says "When auth
lands, this is the one place to extract user from a JWT"). This decodes the
bearer JWT and records actor_type='user', actor_id=username when present —
falling back to the old api_client/system behavior otherwise.
"""
import sys, pathlib
TARGET = pathlib.Path("backend/app/utils/audit.py")
MARKER = "Phase 21 - decode JWT actor"
OLD = '''    actor_type = "system"
    actor_id = None
    actor_ip = None
    if request is not None:
        actor_type = "api_client"
        actor_ip = request.client.host if request.client else None
        # Future: actor_id = decoded_jwt.user_id, actor_type = "user"
    return actor_type, actor_id, actor_ip'''
NEW = '''    actor_type = "system"
    actor_id = None
    actor_ip = None
    if request is not None:
        actor_type = "api_client"
        actor_ip = request.client.host if request.client else None
        # Phase 21 - decode JWT actor. If a valid bearer token is present,
        # record the human user as the actor instead of a generic api_client.
        try:
            authz = request.headers.get("authorization")
            if authz:
                parts = authz.split(maxsplit=1)
                if len(parts) == 2 and parts[0].lower() == "bearer":
                    from app.auth.security import decode_token
                    payload = decode_token(parts[1].strip())
                    if payload and payload.get("username"):
                        actor_type = "user"
                        actor_id = payload["username"]
        except Exception:
            # Audit actor resolution must never break the request.
            pass
    return actor_type, actor_id, actor_ip'''

def main():
    if not TARGET.exists():
        print(f"  ERROR: {TARGET} not found."); return 1
    t = TARGET.read_text(encoding="utf-8")
    if MARKER in t:
        print("  [SKIP] already applied."); return 0
    if t.count(OLD) != 1:
        print(f"  ERROR: anchor found {t.count(OLD)}x. Aborting."); return 1
    bak = TARGET.with_suffix(".py.bak_phase21")
    if not bak.exists(): bak.write_text(t, encoding="utf-8"); print(f"  Backup: {bak.name}")
    TARGET.write_text(t.replace(OLD, NEW, 1), encoding="utf-8")
    print("  Applied: JWT actor resolution in _extract_actor")
    return 0

if __name__ == "__main__":
    sys.exit(main())
