"""Phase 16.0g - Dedicated audit database connection.

Audit logs live in their own Postgres database (induvista_audit) for
compliance and durability: separate backups, separate retention,
potentially different credentials. The app uses two engines/pools - one
for operational data and one for audit.

If AUDIT_DATABASE_URL is not set, this module will refuse to load.
We don't fall back to the main DB - audit-to-main is a compliance
violation (mixed lifecycle, joint retention, etc.).
"""

import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


_AUDIT_URL = os.environ.get("AUDIT_DATABASE_URL")
if not _AUDIT_URL:
    raise RuntimeError(
        "AUDIT_DATABASE_URL is not set. Phase 16.0g requires a dedicated "
        "audit database. Bootstrap it with setup_audit_db.ps1 and add the "
        "URL to .env, then restart the backend."
    )

engine_audit = create_engine(
    _AUDIT_URL,
    pool_pre_ping=True,    # detect dropped connections after pg restart
    pool_size=5,           # smaller pool than the main DB; audit writes are infrequent
    max_overflow=10,
    future=True,
)

AuditSessionLocal = sessionmaker(
    bind=engine_audit,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)


# Retention defaults to 365 days, overridable via env.
AUDIT_RETENTION_DAYS = int(os.environ.get("AUDIT_RETENTION_DAYS", "365"))
