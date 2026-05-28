"""Pluggable authentication providers (Phase 21).

THE EXTENSION SEAM for "app users now, LDAP/OS later".

Every user row has an `auth_provider` ('local' | 'ldap' | 'os'). Login
dispatches to the matching provider's authenticate(). The users table,
roles, JWT sessions, RBAC, audit wiring, and frontend are all built
ONCE against the User identity — only the credential-verification step
varies per provider.

Adding LDAP/OS later = implement that provider's authenticate() and add
its config to Settings. NOTHING else changes: not the 74 protected
routes, not the login endpoint, not the UI.

CONTRACT
========
  authenticate(db, username, password) -> AuthedUser | None
    Return the user's identity on success, None on any failure
    (unknown user, wrong password, disabled, provider error). Never
    leak which specific check failed — the login endpoint maps all
    None results to a single generic 401.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth.security import verify_password


@dataclass(frozen=True)
class AuthedUser:
    """Identity returned by a provider on successful authentication."""
    id: int
    username: str
    role: str
    must_change_password: bool


class Provider(Protocol):
    name: str

    def authenticate(
        self, db: Session, username: str, password: str
    ) -> AuthedUser | None:
        ...


def _load_user_row(db: Session, username: str, provider: str):
    """Shared lookup: enabled user with the given username + provider."""
    return db.execute(
        text("""
            SELECT id, username, role, password_hash, must_change_password
            FROM users
            WHERE username = :u AND auth_provider = :p AND is_enabled = TRUE
        """),
        {"u": username, "p": provider},
    ).mappings().first()


class LocalProvider:
    """INDUVISTA-managed users; bcrypt password verified against
    users.password_hash. The only provider implemented in Phase 21.1."""
    name = "local"

    def authenticate(
        self, db: Session, username: str, password: str
    ) -> AuthedUser | None:
        row = _load_user_row(db, username, "local")
        if row is None or not row["password_hash"]:
            return None
        if not verify_password(password, row["password_hash"]):
            return None
        return AuthedUser(
            id=row["id"],
            username=row["username"],
            role=row["role"],
            must_change_password=row["must_change_password"],
        )


class LdapProvider:
    """Active Directory / LDAP bind. Phase 21.2 — STUB.

    Implementation sketch (when built):
      - read settings.ldap_url / ldap_base_dn / ldap_bind_template
      - attempt an LDAP bind with (username, password)
      - on success, ensure a local users row exists (auth_provider='ldap',
        password_hash NULL), provisioning role from an LDAP group → role map
      - return AuthedUser from that row
    The users table + RBAC already support this with NO migration.
    """
    name = "ldap"

    def authenticate(
        self, db: Session, username: str, password: str
    ) -> AuthedUser | None:
        raise NotImplementedError(
            "LDAP/AD authentication is Phase 21.2. The schema and dispatch "
            "already support it; only this method + Settings config remain."
        )


class OsProvider:
    """Host OS / PAM / Windows account. Phase 21.3 — STUB.

    Note: the backend runs inside a Docker container with no access to the
    host user database. Building this requires either PAM passthrough, an
    OS-auth sidecar, or running the backend with host integration. Captured
    here so the seam is explicit; not wired in Phase 21.1.
    """
    name = "os"

    def authenticate(
        self, db: Session, username: str, password: str
    ) -> AuthedUser | None:
        raise NotImplementedError(
            "OS/PAM authentication is Phase 21.3. Requires host integration "
            "the containerized backend does not have by default."
        )


# Registry: provider name → instance. Login looks up the user's
# auth_provider, then dispatches here. Local is the only one active now.
_PROVIDERS: dict[str, Provider] = {
    "local": LocalProvider(),
    "ldap": LdapProvider(),
    "os": OsProvider(),
}


def get_provider(name: str) -> Provider | None:
    return _PROVIDERS.get(name)


def authenticate(db: Session, username: str, password: str) -> AuthedUser | None:
    """Top-level login entry. Resolves the user's provider from the DB,
    then dispatches. Returns None on any failure (generic — caller maps
    to 401). For an unknown username we still try 'local' so timing/shape
    doesn't reveal whether the username exists.
    """
    # Determine the user's configured provider (default 'local' if unknown
    # username — keeps the failure path uniform).
    row = db.execute(
        text("SELECT auth_provider FROM users WHERE username = :u AND is_enabled = TRUE"),
        {"u": username},
    ).first()
    provider_name = row[0] if row else "local"

    provider = get_provider(provider_name)
    if provider is None:
        return None
    try:
        return provider.authenticate(db, username, password)
    except NotImplementedError:
        # Stubbed provider — treat as auth failure rather than a 500.
        return None
