-- Runs ONCE, on first initialization of the Postgres data directory.
-- For all subsequent schema changes, use Alembic migrations.
--
-- The TimescaleDB image already has the extension binary installed; this
-- statement just registers it inside the database created by POSTGRES_DB.

CREATE EXTENSION IF NOT EXISTS timescaledb;
