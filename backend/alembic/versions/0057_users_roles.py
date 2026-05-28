"""Users + roles for human authentication (Phase 21 — Auth/RBAC).

Introduces app-managed user accounts with role-based access control.

PROVIDER-AGNOSTIC BY DESIGN
===========================
  The `auth_provider` column lets a user be authenticated by different
  identity backends WITHOUT changing this schema, the RBAC logic, the
  audit integration, or the frontend:

    'local' — INDUVISTA verifies the password (bcrypt hash here).   [Phase 21.1, now]
    'ldap'  — Active Directory / LDAP verifies; password_hash stays NULL. [Phase 21.2]
    'os'    — Host OS / PAM / Windows verifies; password_hash stays NULL.  [Phase 21.3]

  Roles, sessions (JWT), audit actor wiring, and the UI are built ONCE
  against the users table. Adding a new provider later is purely
  "implement Provider.authenticate() + add config" — no migration.

ROLES (least → most privilege)
==============================
    viewer    read-only (dashboards, trends, diagnostics)
    operator  viewer + acknowledge alarms, write command/setpoint tags
    engineer  operator + configure devices/tags/blocks/alarms/calc/OPC
    admin     engineer + manage users, API keys, system settings

PASSWORD HASHING
================
  bcrypt (passlib) — UNLIKE the api_keys table (SHA-256). API keys are
  256-bit random tokens where slow hashing is wasted; user passwords are
  low-entropy human-chosen secrets where bcrypt's deliberate slowness is
  exactly the protection we want against offline cracking.

Revision ID: 0057_users_roles
Revises: 0056_opc_server_clock_drift
Create Date: 2026-05-28
"""
from __future__ import annotations

from alembic import op


revision = "0057_users_roles"
down_revision = "0056_opc_server_clock_drift"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id                   SERIAL PRIMARY KEY,
            username             TEXT UNIQUE NOT NULL,
            auth_provider        TEXT NOT NULL DEFAULT 'local',
            -- NULL for non-local providers (ldap/os verify externally).
            password_hash        TEXT,
            role                 TEXT NOT NULL DEFAULT 'viewer',
            full_name            TEXT,
            email                TEXT,
            is_enabled           BOOLEAN NOT NULL DEFAULT TRUE,
            -- Force a password change on next login (seeded/admin-reset users).
            must_change_password BOOLEAN NOT NULL DEFAULT FALSE,
            last_login_at        TIMESTAMPTZ,
            last_login_ip        INET,
            created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            created_by           TEXT,
            CONSTRAINT ck_users_role
                CHECK (role IN ('viewer', 'operator', 'engineer', 'admin')),
            CONSTRAINT ck_users_auth_provider
                CHECK (auth_provider IN ('local', 'ldap', 'os')),
            -- Local users MUST have a password hash; external providers MUST NOT
            -- store one (the external system is the source of truth).
            CONSTRAINT ck_users_local_has_hash
                CHECK (
                    (auth_provider = 'local'  AND password_hash IS NOT NULL)
                 OR (auth_provider <> 'local' AND password_hash IS NULL)
                )
        );
    """)

    # Hot-path lookup: login resolves username among enabled users.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_users_username_enabled
        ON users (username) WHERE is_enabled = TRUE;
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_users_username_enabled;")
    op.execute("DROP TABLE IF EXISTS users;")
