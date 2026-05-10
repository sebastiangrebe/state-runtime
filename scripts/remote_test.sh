#!/usr/bin/env bash
# From your Mac. Pushes the repo to a remote CUDA box, bootstraps it, then
# tunnels :8000 (engine) and :5173 (canvas) back to localhost so the browser
# on your laptop hits services running on the GPU box.
#
# Usage:
#   scripts/remote_test.sh user@host [-p ssh_port]
#
# Requirements on the remote: SSH access as a user with passwordless sudo
# (or root). Anything else (postgres, node, python3.11, mamba kernels) is
# installed by remote_bootstrap.sh.
set -euo pipefail

if [ $# -lt 1 ]; then
  echo "usage: $0 user@host [-p ssh_port]" >&2
  exit 2
fi

HOST="$1"; shift
SSH_OPTS=()
while [ $# -gt 0 ]; do
  case "$1" in
    -p) SSH_OPTS+=("-p" "$2"); shift 2 ;;
    *) SSH_OPTS+=("$1"); shift ;;
  esac
done

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "[local] rsync source → $HOST:/root/state-runtime"
rsync -az --delete \
  --exclude=.git --exclude=.venv --exclude=venv --exclude=node_modules \
  --exclude=__pycache__ --exclude=.logs --exclude=canvas/dist \
  -e "ssh ${SSH_OPTS[*]:-}" \
  ./ "$HOST:/root/state-runtime/"

echo "[local] bootstrap on remote"
ssh "${SSH_OPTS[@]}" "$HOST" 'bash -s' < scripts/remote_bootstrap.sh

echo
echo "[local] opening SSH tunnel ─ Ctrl-C to disconnect"
echo "        canvas + engine  http://localhost:8000   (single port — engine serves canvas/dist)"
echo "        health           http://localhost:8000/health"
echo
exec ssh "${SSH_OPTS[@]}" -N \
  -L 8000:localhost:8000 \
  "$HOST"
