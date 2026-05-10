# Running State Runtime on CUDA

The Mac demo falls back to a pure-Python SSM scan because Apple Silicon has no CUDA. On any Linux + NVIDIA GPU box the Mamba CUDA kernels build cleanly and Jamba runs at conversational speed (~1-2s per turn).

## Option 1: build the image locally on a CUDA host

Prereqs on the host:
- NVIDIA driver compatible with CUDA 12.x.
- `nvidia-container-toolkit` installed (`docker run --gpus all` works).
- Postgres reachable (run via `docker compose up -d postgres` — same `docker-compose.yml`).

```bash
docker compose up -d postgres
docker build -f Dockerfile.cuda -t state-runtime:cuda .
docker run --rm -it --gpus all \
    -p 8000:8000 -p 5173:5173 \
    -e DATABASE_URL=postgresql+psycopg2://runtime:runtime@host.docker.internal:5432/runtime \
    state-runtime:cuda
```

Then open `http://<host-ip>:5173`.

## Option 2: RunPod / Lambda / vast.ai

Cheapest interactive box: any 16-24GB VRAM card (RTX 3090/4090, A4500, A10). Jamba2-3B fits in ~8GB at fp16.

### RunPod template

1. Pod template: `nvidia/cuda:12.4.1-cudnn-devel-ubuntu22.04` (or pick "PyTorch 2.x CUDA 12" community template).
2. Expose ports `8000`, `5173`.
3. Volume: 30GB persistent (HF cache).
4. SSH in, then:

```bash
git clone <your-fork-url> /workspace/state-runtime
cd /workspace/state-runtime
apt-get update && apt-get install -y nodejs npm postgresql
service postgresql start
sudo -u postgres psql -c "CREATE USER runtime WITH PASSWORD 'runtime' SUPERUSER;"
sudo -u postgres psql -c "CREATE DATABASE runtime OWNER runtime;"

python -m venv venv && source venv/bin/activate
pip install -r engine/requirements.txt
pip install --no-build-isolation causal-conv1d mamba-ssm

(cd canvas && npm install && npm run build)

DATABASE_URL=postgresql+psycopg2://runtime:runtime@localhost:5432/runtime \
python -m uvicorn engine.main:app --host 0.0.0.0 --port 8000 &

(cd canvas && npx vite preview --host 0.0.0.0 --port 5173)
```

Open the RunPod-published URL for port 5173.

## Sync flow from Mac

Local edits → push to a personal git remote → pull on the box. Or `rsync` directly:

```bash
rsync -av --exclude=.venv --exclude=venv --exclude=node_modules \
    ./ root@<pod-ip>:/workspace/state-runtime/
```

## Verifying you're on the fast path

After `runtime: model ready` appears in `.logs/engine.log`, look for the **absence** of:

```
[transformers] Fast Mamba kernels are not available...
```

If that warning is gone, you're on the CUDA fast path. Bench should report **~1-2s/turn** instead of 13s.

## Cost ballpark

- vast.ai RTX 3090: ~$0.20/hr
- RunPod RTX 4090: ~$0.40/hr
- Lambda A10: ~$0.60/hr

A demo session of 1-2 hours is well under $1.
