#!/bin/bash
# Phase 16.0g - Create the dedicated audit database.
#
# Runs on FIRST container start (when /var/lib/postgresql/data is empty).
# Idempotent within the script itself but won't re-run if the volume
# already exists - existing installs use setup_audit_db.ps1 instead.

set -e

echo "Creating induvista_audit database..."

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" <<-EOSQL
    CREATE DATABASE induvista_audit;
    GRANT ALL PRIVILEGES ON DATABASE induvista_audit TO $POSTGRES_USER;
EOSQL

echo "Enabling timescaledb extension in induvista_audit..."

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname induvista_audit <<-EOSQL
    CREATE EXTENSION IF NOT EXISTS timescaledb;
EOSQL

echo "Audit database ready."
