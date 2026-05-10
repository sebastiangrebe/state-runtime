#!/usr/bin/env bash
# Single-container entrypoint.
# Boots Postgres locally, then uvicorn on :8000 which serves both the WS API
# and the prebuilt canvas at the root.
set -euo pipefail

: "${DATABASE_URL:=postgresql+psycopg2://runtime:runtime@localhost:5432/runtime}"
: "${MODEL_ID:=ai21labs/AI21-Jamba2-3B}"
: "${MAX_NEW_TOKENS:=512}"
: "${PGDATA:=/var/lib/postgresql/data}"

echo "[entrypoint] MODEL_ID=$MODEL_ID  PGDATA=$PGDATA"

# --- Postgres -------------------------------------------------------------
if [ ! -s "$PGDATA/PG_VERSION" ]; then
  echo "[entrypoint] initialising Postgres cluster at $PGDATA"
  mkdir -p "$PGDATA"
  chown -R postgres:postgres "$PGDATA"
  sudo -u postgres /usr/lib/postgresql/14/bin/initdb -D "$PGDATA" >/dev/null
fi
chown -R postgres:postgres "$PGDATA"

echo "[entrypoint] starting Postgres"
sudo -u postgres /usr/lib/postgresql/14/bin/pg_ctl \
  -D "$PGDATA" -l /tmp/pg.log -w start

if ! sudo -u postgres psql -tc "SELECT 1 FROM pg_user WHERE usename='runtime'" | grep -q 1; then
  sudo -u postgres psql -c "CREATE USER runtime WITH PASSWORD 'runtime' SUPERUSER;"
fi
if ! sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='runtime'" | grep -q 1; then
  sudo -u postgres psql -c "CREATE DATABASE runtime OWNER runtime;"
fi
echo "[entrypoint] Postgres ready"

# --- Engine (also serves the canvas at /) --------------------------------
export DATABASE_URL MODEL_ID MAX_NEW_TOKENS
echo "[entrypoint] launching uvicorn on :8000  (CUDA path — Mamba kernels enabled)"
exec python -m uvicorn engine.main:app \
    --host 0.0.0.0 --port 8000 --log-level info \
    --ws-ping-interval 60 --ws-ping-timeout 60
