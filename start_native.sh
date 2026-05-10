#!/usr/bin/env bash
# Native macOS host boot.
#
# Postgres runs in Docker (the only thing in docker-compose.yml).
# The Python engine and the Vite canvas run NATIVELY on the host so PyTorch
# can use the Mac's MPS device — Docker on macOS blocks GPU/MPS passthrough.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

LOG_DIR="$ROOT/.logs"
mkdir -p "$LOG_DIR"

echo "[boot] root=$ROOT"

# 1) Postgres ---------------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
  echo "[boot] docker not found — install Docker Desktop, or set DATABASE_URL to a running Postgres." >&2
  exit 1
fi
echo "[boot] starting Postgres (docker compose)"
docker compose up -d

echo -n "[boot] waiting for Postgres health"
for _ in $(seq 1 60); do
  if docker compose exec -T postgres pg_isready -U runtime -d runtime >/dev/null 2>&1; then
    echo " — ready"
    break
  fi
  echo -n "."
  sleep 1
done

# 2) Python venv (native, NOT in docker) ------------------------------------
VENV="$ROOT/venv"
PY_REQ="3.11"

pick_python() {
  for cand in python3.12 python3.11 python3.10; do
    if command -v "$cand" >/dev/null 2>&1; then
      echo "$cand"
      return
    fi
  done
  if command -v uv >/dev/null 2>&1; then
    echo "uv"
    return
  fi
  echo ""
}

if [ ! -d "$VENV" ]; then
  PY_TOOL="$(pick_python)"
  if [ -z "$PY_TOOL" ]; then
    echo "[boot] FATAL: need Python >= 3.10 (outlines + modern transformers)." >&2
    echo "[boot] Install one of:" >&2
    echo "[boot]   brew install python@3.11" >&2
    echo "[boot]   brew install uv && uv venv --python 3.11 venv" >&2
    exit 1
  fi
  echo "[boot] creating venv at $VENV  (using: $PY_TOOL)"
  if [ "$PY_TOOL" = "uv" ]; then
    uv venv --python "$PY_REQ" "$VENV"
  else
    "$PY_TOOL" -m venv "$VENV"
  fi
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"

PY_VERSION="$(python -c 'import sys;print("%d.%d"%sys.version_info[:2])')"
echo "[boot] venv python: $PY_VERSION"
case "$PY_VERSION" in
  3.10|3.11|3.12|3.13) ;;
  *)
    echo "[boot] FATAL: outlines + modern transformers require Python >= 3.10. Got $PY_VERSION." >&2
    echo "[boot] Recreate: rm -rf venv && ./start_native.sh" >&2
    exit 1
    ;;
esac

echo "[boot] installing native Python deps (this can take a while on first run)"

# Core deps — must install. Mamba kernels are split off below because they
# require CUDA/nvcc and are not buildable on Apple Silicon. Letting their
# build failure abort pip would prevent the engine from ever starting and
# emitting the real PyTorch kernel error you want to see.
CORE_DEPS=(
  torch transformers outlines psycopg2-binary fastapi "uvicorn[standard]"
  sqlalchemy pydantic accelerate sentencepiece numpy
)
if command -v uv >/dev/null 2>&1; then
  uv pip install "${CORE_DEPS[@]}"
else
  pip install --upgrade pip
  pip install "${CORE_DEPS[@]}"
fi

echo "[boot] attempting CUDA-only kernels (mamba-ssm, causal-conv1d) — best-effort"
if command -v uv >/dev/null 2>&1; then
  uv pip install mamba-ssm causal-conv1d 2>/tmp/mamba_install.log || {
    echo "[boot] mamba kernels did not install (expected on Apple Silicon — they require nvcc/CUDA)."
    echo "[boot] Engine will start; Jamba model load will surface the real kernel error in $LOG_DIR/engine.log."
    echo "[boot] (full pip error: /tmp/mamba_install.log)"
  }
else
  pip install mamba-ssm causal-conv1d 2>/tmp/mamba_install.log || {
    echo "[boot] mamba kernels did not install (expected on Apple Silicon — they require nvcc/CUDA)."
    echo "[boot] Engine will start; Jamba model load will surface the real kernel error in $LOG_DIR/engine.log."
    echo "[boot] (full pip error: /tmp/mamba_install.log)"
  }
fi

# 3) Engine (native uvicorn) ------------------------------------------------
export DATABASE_URL="${DATABASE_URL:-postgresql+psycopg2://runtime:runtime@localhost:5432/runtime}"
export MODEL_ID="${MODEL_ID:-ai21labs/AI21-Jamba2-3B}"
echo "[boot] launching FastAPI engine on :8000  (MODEL_ID=$MODEL_ID, native MPS)"
python -m uvicorn engine.main:app --host 0.0.0.0 --port 8000 --log-level info \
  >"$LOG_DIR/engine.log" 2>&1 &
ENGINE_PID=$!

# 4) Vite canvas (native node) ---------------------------------------------
pushd canvas >/dev/null
if [ ! -d node_modules ]; then
  echo "[boot] installing canvas deps"
  npm install
fi
echo "[boot] launching Vite canvas on :5173"
npm run dev >"$LOG_DIR/canvas.log" 2>&1 &
CANVAS_PID=$!
popd >/dev/null

cleanup() {
  echo
  echo "[boot] shutting down…"
  kill "$ENGINE_PID" "$CANVAS_PID" 2>/dev/null || true
  wait 2>/dev/null || true
  echo "[boot] (Postgres still running — 'docker compose down' to stop)"
}
trap cleanup INT TERM

cat <<EOF

  State Runtime is up (native).
    canvas   http://localhost:5173
    engine   http://localhost:8000/health
    db       postgres://runtime:runtime@localhost:5432/runtime

    logs     $LOG_DIR/{engine,canvas}.log
    stop     Ctrl-C  (then 'docker compose down' for Postgres)

EOF

wait "$ENGINE_PID" "$CANVAS_PID"
