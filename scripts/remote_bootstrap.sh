#!/usr/bin/env bash
# Runs ON the remote CUDA box. Idempotent.
# Sets up Postgres natively, builds Python deps + Mamba CUDA kernels,
# launches engine + canvas in tmux, waits for /health.
set -euo pipefail

cd /root/state-runtime

SUDO=""
[ "$(id -u)" -ne 0 ] && SUDO=sudo

echo "[remote] OS deps"
if ! command -v psql >/dev/null 2>&1 || ! command -v node >/dev/null 2>&1 || ! command -v tmux >/dev/null 2>&1; then
  $SUDO apt-get update
  $SUDO apt-get install -y --no-install-recommends \
    postgresql postgresql-contrib \
    nodejs npm \
    python3.11 python3.11-venv python3.11-dev python3-pip \
    build-essential ninja-build tmux curl ca-certificates
fi

echo "[remote] Postgres"
$SUDO service postgresql start || true
$SUDO -u postgres psql -tc "SELECT 1 FROM pg_user WHERE usename='runtime'" | grep -q 1 || \
  $SUDO -u postgres psql -c "CREATE USER runtime WITH PASSWORD 'runtime' SUPERUSER;"
$SUDO -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='runtime'" | grep -q 1 || \
  $SUDO -u postgres psql -c "CREATE DATABASE runtime OWNER runtime;"

echo "[remote] Python venv"
[ -d venv ] || python3.11 -m venv venv
# shellcheck disable=SC1091
source venv/bin/activate
pip install --upgrade pip wheel setuptools

if ! python -c "import torch" 2>/dev/null; then
  echo "[remote] installing torch (CUDA 12.4 wheel)"
  pip install torch --index-url https://download.pytorch.org/whl/cu124
fi

if ! python -c "import transformers, outlines, fastapi, sqlalchemy, psycopg2" 2>/dev/null; then
  echo "[remote] installing engine deps"
  pip install -r engine/requirements.txt
fi

if ! python -c "import mamba_ssm, causal_conv1d" 2>/dev/null; then
  echo "[remote] compiling Mamba CUDA kernels (one-time, ~5 min)"
  pip install --no-build-isolation packaging
  pip install --no-build-isolation causal-conv1d>=1.4.0 mamba-ssm>=2.2.0
fi

echo "[remote] canvas build"
if [ ! -d canvas/node_modules ]; then
  (cd canvas && npm install)
fi
(cd canvas && npm run build)

mkdir -p .logs

echo "[remote] (re)launching engine in tmux (engine also serves canvas/dist at /)"
tmux kill-session -t runtime 2>/dev/null || true
tmux kill-session -t canvas  2>/dev/null || true

tmux new-session -d -s runtime \
  "cd /root/state-runtime && source venv/bin/activate && \
   DATABASE_URL=postgresql+psycopg2://runtime:runtime@localhost:5432/runtime \
   MODEL_ID=\${MODEL_ID:-ai21labs/AI21-Jamba2-3B} \
   MAX_NEW_TOKENS=\${MAX_NEW_TOKENS:-512} \
   python -m uvicorn engine.main:app --host 0.0.0.0 --port 8000 --log-level info \
     --ws-ping-interval 60 --ws-ping-timeout 60 |& tee .logs/engine.log"

echo "[remote] waiting for engine health (model load + Mamba kernel compile may take ~30s on warm cache)"
for i in $(seq 1 180); do
  if curl -sf http://localhost:8000/health >/dev/null 2>&1; then
    echo "[remote] engine OK: $(curl -s http://localhost:8000/health)"
    if grep -q "Fast Mamba kernels are not available" .logs/engine.log 2>/dev/null; then
      echo "[remote] WARNING: slow Mamba path detected — kernels did not engage"
    else
      echo "[remote] Mamba fast path engaged"
    fi
    exit 0
  fi
  sleep 2
done

echo "[remote] FAIL: engine never came up. last 50 lines:"
tail -n 50 .logs/engine.log || true
exit 1
