"""electronic signatures (21 CFR Part 11) — generic signing foundation

Phase B-ESIG.1. A protocol-agnostic `signatures` store that binds a
re-authenticated user to a specific record (report revision now; control
writes / recipe downloads later) via a SHA-256 hash of the record's exact
content, with an Authored -> Reviewed -> Approved workflow and a per-record
tamper-evident hash chain.

Design notes:
  * record_id is POLYMORPHIC (no FK) so the same table covers report_revision,
    control_write, and recipe_download. Integrity comes from record_hash (binds
    to the exact content) + the per-record chain_hash.
  * signer_user_id FKs users(id) with the default RESTRICT — a user who has
    signed cannot be deleted, preserving attribution (Part 11 §11.70).
  * Append-only BY POLICY: the application never UPDATEs or DELETEs a row.
    DB-role enforcement (revoke UPDATE/DELETE from the app role) is a Phase-26
    cybersecurity follow-up.
  * audit_correlation_id loosely links to the audit_log event (a separate DB,
    so no cross-DB FK is possible).

Revision ID: 0080_signatures
Revises: 0079_redundant_secondary
"""
from alembic import op


revision = "0080_signatures"
down_revision = "0079_redundant_secondary"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE signatures (
            id                   BIGSERIAL PRIMARY KEY,
            record_type          VARCHAR(32) NOT NULL,
            record_id            BIGINT      NOT NULL,
            record_hash          CHAR(64)    NOT NULL,
            meaning              VARCHAR(16) NOT NULL,
            signer_user_id       INTEGER     NOT NULL REFERENCES users(id),
            signer_username      VARCHAR(64) NOT NULL,
            signer_role          VARCHAR(16) NOT NULL,
            reason_code          VARCHAR(48),
            comment              TEXT,
            signed_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
            prev_signature_id    BIGINT REFERENCES signatures(id),
            chain_hash           CHAR(64)    NOT NULL,
            audit_correlation_id UUID,
            CONSTRAINT ck_signatures_meaning
                CHECK (meaning IN ('authored','reviewed','approved')),
            CONSTRAINT ck_signatures_record_type
                CHECK (record_type IN ('report_revision','control_write','recipe_download'))
        );
    """)
    op.execute("""
        CREATE INDEX ix_signatures_record
        ON signatures (record_type, record_id, id);
    """)
    # One signature per (record, meaning): a record can't be authored twice etc.
    # Defense-in-depth alongside the application-level order check.
    op.execute("""
        CREATE UNIQUE INDEX uq_signatures_record_meaning
        ON signatures (record_type, record_id, meaning);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS signatures;")
