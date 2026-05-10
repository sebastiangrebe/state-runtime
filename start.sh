#!/usr/bin/env bash
# Boot Postgres (docker), the Python engine (uvicorn), and the Vite canvas.
# Logs go to ./.logs/. Trap stops everything on Ctrl-C.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

LOG_DIR="$ROOT/.logs"
mkdir -p "$LOG_DIR"

echo "[start] using project root: $ROOT"

# --- 1. Postgres ----------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
  echo "[start] docker not found — install Docker Desktop or set DATABASE_URL to a running Postgres." >&2
  exit 1
fi

echo "[start] booting Postgres via docker compose"
docker compose up -d postgres

echo -n "[start] waiting for Postgres health"
for _ in $(seq 1 60); do
  if docker compose exec -T postgres pg_isready -U runtime -d runtime >/dev/null 2>&1; then
    echo " — ready"
    break
  fi
  echo -n "."
  sleep 1
done

# --- 2. Python engine -----------------------------------------------------
VENV="$ROOT/.venv"
PY_REQ="3.11"

pick_python() {
  if command -v uv >/dev/null 2>&1; then
    echo "uv"
    return
  fi
  for cand in python3.12 python3.11 python3.10; do
    if command -v "$cand" >/dev/null 2>&1; then
      echo "$cand"
      return
    fi
  done
  echo "python3"  # last resort
}

PY_TOOL="$(pick_python)"

if [ ! -d "$VENV" ]; then
  echo "[start] creating venv at $VENV  (using: $PY_TOOL)"
  if [ "$PY_TOOL" = "uv" ]; then
    uv venv --python "$PY_REQ" "$VENV"
  else
    "$PY_TOOL" -m venv "$VENV"
  fi
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"

PY_VERSION="$(python -c 'import sys;print("%d.%d"%sys.version_info[:2])')"
echo "[start] venv python: $PY_VERSION"
case "$PY_VERSION" in
  3.10|3.11|3.12|3.13) ;;
  *)
    echo "[start] WARNING: outlines requires Python >= 3.10. Got $PY_VERSION." >&2
    echo "[start] Install via 'brew install python@3.11' or use uv, then rerun." >&2
    ;;
esac

echo "[start] installing engine deps (cached after first run)"
if command -v uv >/dev/null 2>&1; then
  uv pip install -r engine/requirements.txt || {
    echo "[start] some engine deps failed — STUB_MODE will engage if model libs missing." >&2
  }
else
  pip install -q --upgrade pip
  pip install -q -r engine/requirements.txt || {
    echo "[start] some engine deps failed — STUB_MODE will engage if model libs missing." >&2
  }
fi

export DATABASE_URL="${DATABASE_URL:-postgresql+psycopg://runtime:runtime@localhost:5432/runtime}"
export MODEL_ID="${MODEL_ID:-ai21labs/AI21-Jamba2-3B}"
export STUB_MODE="${STUB_MODE:-0}"

echo "[start] launching FastAPI engine on :8000  (MODEL_ID=$MODEL_ID  STUB_MODE=$STUB_MODE)"
python -m uvicorn engine.main:app --host 0.0.0.0 --port 8000 --log-level info \
  >"$LOG_DIR/engine.log" 2>&1 &
ENGINE_PID=$!

# --- 3. Vite canvas -------------------------------------------------------
pushd canvas >/dev/null
if [ ! -d node_modules ]; then
  echo "[start] installing canvas deps"
  if command -v pnpm >/dev/null 2>&1; then pnpm install
  elif command -v yarn >/dev/null 2>&1; then yarn install
  else npm install
  fi
fi
echo "[start] launching Vite canvas on :5173"
if command -v pnpm >/dev/null 2>&1; then pnpm dev >"$LOG_DIR/canvas.log" 2>&1 &
elif command -v yarn >/dev/null 2>&1; then yarn dev >"$LOG_DIR/canvas.log" 2>&1 &
else npm run dev >"$LOG_DIR/canvas.log" 2>&1 &
fi
CANVAS_PID=$!
popd >/dev/null

cleanup() {
  echo
  echo "[start] shutting down…"
  kill "$ENGINE_PID" "$CANVAS_PID" 2>/dev/null || true
  wait 2>/dev/null || true
  echo "[start] (Postgres still running — 'docker compose down' to stop)"
}
trap cleanup INT TERM

cat <<EOF

  State Runtime is up.
    canvas   http://localhost:5173
    engine   http://localhost:8000/health
    db       postgres://runtime:runtime@localhost:5432/runtime

    logs     $LOG_DIR/{engine,canvas}.log
    stop     Ctrl-C  (then 'docker compose down' for Postgres)

EOF

wait "$ENGINE_PID" "$CANVAS_PID"
