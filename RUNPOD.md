# Running on RunPod (single URL, full UI)

Goal: spin a GPU pod, get a public HTTPS URL, click around in the canvas with Mamba CUDA kernels active. End-to-end click → DB → patch should land in <500 ms.

There are two ways. Pick one.

---

## Option A — Custom Docker image (cleanest)

Builds the whole thing into one image: Postgres + engine + prebuilt canvas. RunPod runs it. One port (`8000`) serves everything.

### 1. Build the image (on a Linux + amd64 host with Docker)

If your dev host is a Mac, you'll need `docker buildx` to cross-build amd64:

```bash
docker buildx create --use
docker buildx build --platform linux/amd64 \
  -f Dockerfile.cuda \
  -t YOUR_DOCKERHUB_USER/state-runtime:cuda \
  --push .
```

(Or build directly on the CUDA Linux host: `docker build -f Dockerfile.cuda -t state-runtime:cuda .`)

### 2. Deploy to RunPod

1. Open <https://runpod.io/console/deploy>.
2. Pod template: **"Custom"**.
3. Container image: `YOUR_DOCKERHUB_USER/state-runtime:cuda`.
4. GPU: any 16-24GB card (RTX 3090, 4090, A4500, A10).
5. Container disk: 30 GB. Volume disk: 30 GB at `/root/.cache/huggingface` (persists model weights).
6. Expose HTTP port: `8000`.
7. Env (defaults are fine):
   - `MODEL_ID=ai21labs/AI21-Jamba2-3B`
   - `MAX_NEW_TOKENS=512`
8. Deploy.

### 3. Open the UI

Pod page → **Connect** → **HTTP Service [8000]** → opens `https://<pod-id>-8000.proxy.runpod.net`.

The canvas is served from the engine itself. The browser connects WebSocket back to the same origin (`wss://<pod-id>-8000.proxy.runpod.net/ws`) — the URL derivation in `UniversalCanvas.jsx` handles this automatically.

---

## Option B — Vanilla pod + remote bootstrap (no image build)

For when you want to iterate quickly and don't feel like rebuilding/pushing an image each time.

### 1. Spin a pod with SSH

1. Pod template: **"PyTorch 2.x CUDA 12"** (community templates work).
2. GPU: same as above.
3. Volume: `/workspace` 30 GB.
4. Expose **TCP port 22** (SSH) and **HTTP port 8000**.
5. Deploy. Wait for SSH ready.

Copy the SSH command RunPod gives you, e.g. `ssh root@1.2.3.4 -p 22001 -i ~/.ssh/id_ed25519`.

### 2. From your Mac

```bash
scripts/remote_test.sh root@1.2.3.4 -p 22001
```

This:
- rsyncs the repo to `/root/state-runtime/` on the pod.
- runs `scripts/remote_bootstrap.sh` there: apt-installs Postgres + Node + Python 3.11, builds Mamba CUDA kernels, builds the canvas, launches uvicorn in a tmux session.
- waits for `/health`.
- forwards `localhost:8000` on your Mac to port 8000 on the pod.

Open <http://localhost:8000> on your Mac. Same UI, same single port, just tunnelled.

If you'd rather hit the pod's public proxy URL instead of tunnelling, skip the local SSH forward — once the bootstrap finishes, the canvas is reachable at `https://<pod-id>-8000.proxy.runpod.net`.

---

## Verifying the fast path

After the engine logs `model ready`, tail the engine log:

```bash
tmux attach -t runtime    # on the pod
# or, if using Option A:
docker logs <container>
```

Look for the **absence** of `Fast Mamba kernels are not available...`. If absent, you're on the CUDA fast path. Run the bench:

```bash
DATABASE_URL=postgresql+psycopg2://runtime:runtime@localhost:5432/runtime \
python -m engine.bench_turn
```

Expected on a 4090:
- Model load: ~10s
- Full manifest turn: ~0.3-0.6 s
- Patch turn (1 op): ~0.05-0.1 s

That's the 50 ms target you asked about. To shave further (Mamba state reuse across turns rather than re-prefilling) is a separate refactor — see the bench output for current numbers first.

---

## Costs

- vast.ai RTX 3090: ~$0.20/hr
- RunPod RTX 4090: ~$0.40/hr
- Lambda A10: ~$0.60/hr

A demo session is well under $1.
