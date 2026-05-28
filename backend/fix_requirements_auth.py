"""Phase 21 — add auth deps to requirements.txt (pyjwt, passlib[bcrypt])."""
import sys, pathlib
TARGET = pathlib.Path("backend/requirements.txt")
MARKER = "Phase 21 - auth"
OLD = '''asyncua>=1.1,<2'''
NEW = '''asyncua>=1.1,<2

# Phase 21 - auth (users + RBAC + JWT sessions).
# pyjwt: stateless session tokens (HS256). passlib[bcrypt]: bcrypt password
# hashing for local users (slow-hash, appropriate for low-entropy passwords).
pyjwt==2.9.0
passlib[bcrypt]==1.7.4
bcrypt==4.2.0'''

def main():
    if not TARGET.exists():
        print(f"  ERROR: {TARGET} not found."); return 1
    t = TARGET.read_text(encoding="utf-8")
    if MARKER in t:
        print("  [SKIP] already applied."); return 0
    if t.count(OLD) != 1:
        print(f"  ERROR: anchor found {t.count(OLD)}x. Aborting."); return 1
    bak = TARGET.with_suffix(".txt.bak_phase21")
    if not bak.exists(): bak.write_text(t, encoding="utf-8"); print(f"  Backup: {bak.name}")
    TARGET.write_text(t.replace(OLD, NEW, 1), encoding="utf-8")
    print("  Applied: pyjwt, passlib[bcrypt], bcrypt")
    return 0

if __name__ == "__main__":
    sys.exit(main())
