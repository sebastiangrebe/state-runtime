# State Runtime — Hybrid SSM-Transformer as Application Logic

Experimental demo. There is no REST router, no React state machine, no business logic. The application is a single text-generation loop where:

- the **canvas** (React/Vite) is a dumb terminal that renders whatever JSON manifest it last received over WebSocket;
- the **engine** (FastAPI + PyTorch + outlines) hosts a Hybrid SSM/Transformer model whose internal KV-cache + SSM state *is* the application's memory;
- **Postgres** is the only durable fact store; the model is never permitted to touch it directly — the engine intercepts SQL the model emits, runs it as ACID, and feeds the result back into the model's prompt stream before the next UI manifest is generated.

The model is run **natively** on the host. Docker on macOS blocks GPU/MPS passthrough, so only Postgres is containerised.

## Layout

```
docker-compose.yml      Postgres 16 only — nothing else belongs here
engine/                 FastAPI WS server, db, runtime, schemas (native)
canvas/                 Vite + React + Tailwind dumb terminal (native)
start_native.sh         Boots Postgres in Docker, engine + Vite on host
```

## Run

```
./start_native.sh
```

Then open http://localhost:5173.

### Env knobs

- `MODEL_ID` (default `ai21labs/AI21-Jamba2-3B`) — any HF causal LM that loads with `trust_remote_code=True`. Swap to `ai21labs/AI21-Jamba-1.5-Mini` if Jamba2 deps are awkward on your host.
- `MAX_NEW_TOKENS` (default `768`) — per-turn decode budget.
- `DATABASE_URL` — override the Postgres DSN.

There is **no** STUB_MODE and **no** fallback renderer. If the model fails to load (missing `mamba-ssm` kernels, MPS issue, OOM, etc.), the engine crashes at startup and prints the full stack trace to `.logs/engine.log`.

### How a click flows

1. User clicks `Resolve` on alert 42.
2. Canvas sends `{"user_action":"resolve_alert","alert_id":42}` over WS.
3. Engine appends it to the per-session event stream and asks the model for the next constrained JSON output (union of `UIStateManifest` ∪ `DatabaseAction`) via outlines.
4. Model emits `{"kind":"sql","sql":"UPDATE alerts SET status='resolved' WHERE id=42"}`. The engine executes it through SQLAlchemy and appends `SQL_RESULT` to the stream.
5. Engine re-prompts the model, this time constrained to `UIStateManifest`. The new manifest reflects the post-mutation DB state and is pushed down the WS.
6. Canvas re-renders. No `useState` for application data — the manifest *is* the UI.

### How the Omnibox flows

1. User types `show me database metrics` and hits Enter.
2. Canvas sends `{"event":"USER_COMMAND","intent":"show me database metrics"}`.
3. Engine forces the schema target to `UIStateManifest` (USER_COMMAND cannot mutate facts) and re-plans the entire screen — drops the AlertTable, returns MetricCards + LineCharts.

### Trace stream

Every governor phase is mirrored as a `{kind:"trace"}` WebSocket frame and a `TRACE turn=N PHASE` server log line. The canvas renders them in a collapsible drawer above the omnibox: `DB_STATE_BEFORE → USER_*  → MODEL_INPUT → MODEL_OUTPUT → SQL_INTERCEPT → SQL_RESULT → DB_STATE_AFTER → MODEL_OUTPUT_FINAL → MANIFEST_SEND`.
