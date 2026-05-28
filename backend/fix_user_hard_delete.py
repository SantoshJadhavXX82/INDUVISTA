"""Phase 21.1 — add permanent (hard) delete to user management.

The existing DELETE soft-disables (is_enabled=FALSE). That's the right safe
default, but admins also need to PURGE junk accounts (e.g. test users) so
they don't clutter the list forever.

This replaces the DELETE handler with one that accepts ?hard=true to truly
remove the row. Safe because:
  - audit_log stores actor as a username STRING (actor_id), not a FK, so a
    deleted user's audit history is preserved (the name string remains).
  - there are no FK constraints pointing at users.

Guards (both delete modes):
  - cannot disable/delete the LAST enabled admin (lockout protection)
  - cannot delete YOURSELF (the acting admin) — avoids self-lockout
Soft-disable (hard=false, default) keeps the original behavior.

Target: backend/app/api/users_admin.py
"""
import sys, pathlib
TARGET = pathlib.Path("backend/app/api/users_admin.py")
MARKER = "hard delete"

OLD = '''@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def disable_user(
    user_id: int,
    admin: Annotated[CurrentUser, Depends(require_role(Role.ADMIN))],
    db: Annotated[Session, Depends(get_session)],
):
    # Soft-disable, never hard-delete (audit log references actors by id/name).
    target = db.execute(
        text("SELECT role, is_enabled FROM users WHERE id = :id"), {"id": user_id}
    ).mappings().first()
    if target is None:
        raise HTTPException(404, "User not found.")
    if target["role"] == Role.ADMIN.value and target["is_enabled"]:
        admin_count = db.execute(
            text("SELECT COUNT(*) FROM users WHERE role = 'admin' AND is_enabled = TRUE")
        ).scalar()
        if admin_count is not None and admin_count <= 1:
            raise HTTPException(400, "Cannot disable the last enabled admin.")
    db.execute(
        text("UPDATE users SET is_enabled = FALSE, updated_at = NOW() WHERE id = :id"),
        {"id": user_id},
    )
    db.commit()'''

NEW = '''@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def disable_or_delete_user(
    user_id: int,
    admin: Annotated[CurrentUser, Depends(require_role(Role.ADMIN))],
    db: Annotated[Session, Depends(get_session)],
    hard: bool = False,
):
    """Soft-disable a user (default) or permanently delete with ?hard=true.

    hard delete: removes the row entirely. Safe — audit_log stores actor as a
    username string (not a FK), so history is preserved, and no FK constraints
    reference users. Use for purging junk/test accounts.
    """
    target = db.execute(
        text("SELECT username, role, is_enabled FROM users WHERE id = :id"),
        {"id": user_id},
    ).mappings().first()
    if target is None:
        raise HTTPException(404, "User not found.")

    # Guard: never delete/disable yourself (avoids self-lockout).
    if target["username"] == admin.username:
        raise HTTPException(
            400, "You cannot delete or disable your own account while signed in."
        )

    # Guard: never remove the last enabled admin.
    if target["role"] == Role.ADMIN.value and target["is_enabled"]:
        admin_count = db.execute(
            text("SELECT COUNT(*) FROM users WHERE role = 'admin' AND is_enabled = TRUE")
        ).scalar()
        if admin_count is not None and admin_count <= 1:
            raise HTTPException(
                400,
                "Cannot remove the last enabled admin." if hard
                else "Cannot disable the last enabled admin.",
            )

    if hard:
        # Permanent removal (audit history preserved via username string).
        db.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})
    else:
        db.execute(
            text("UPDATE users SET is_enabled = FALSE, updated_at = NOW() WHERE id = :id"),
            {"id": user_id},
        )
    db.commit()'''

def main():
    if not TARGET.exists():
        print(f"  ERROR: {TARGET} not found."); return 1
    t = TARGET.read_text(encoding="utf-8")
    if MARKER in t:
        print("  [SKIP] already applied."); return 0
    if t.count(OLD) != 1:
        print(f"  ERROR: anchor found {t.count(OLD)}x (expected 1). Abort."); return 1
    bak = TARGET.with_suffix(".py.bak_harddelete")
    if not bak.exists(): bak.write_text(t, encoding="utf-8"); print(f"  Backup: {bak.name}")
    TARGET.write_text(t.replace(OLD, NEW, 1), encoding="utf-8")
    print("  Applied: hard delete (?hard=true) + self-delete guard")
    return 0

if __name__ == "__main__":
    sys.exit(main())
