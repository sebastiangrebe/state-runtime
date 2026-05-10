"""FastAPI WebSocket server hosting the Governor Loop.

Two output modes from the model:
  * UIStateManifest — full screen replace (boot, USER_COMMAND, structural changes)
  * UIPatch        — tiny RFC-6902 diff applied to the previously-emitted manifest

The model picks via a discriminated union (EngineOutputEnvelope) on click turns.
On boot / USER_COMMAND we force the schema to UIStateManifest. After SQL we
force UIPatch so the click-loop stays cheap.
"""

from __future__ import annotations

import asyncio
import json
import logging
import pathlib
import re
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .db import execute_sql, init_db, list_alerts
from .jsonpatch import PatchError, apply_patch
from .runtime import HybridRuntime
from .schemas import (
    DatabaseAction,
    EngineOutputEnvelope,
    PatchOp,
    UIPatch,
    UIStateManifest,
)


# Cheap deterministic patch builder. The model already decided WHICH alert to
# mutate (it emitted the SQL); reflecting that on the canonical manifest is
# mechanical, so we skip a second model call (~0.6 s saved per click).
_SQL_UPDATE = re.compile(
    r"UPDATE\s+alerts\s+SET\s+status\s*=\s*'(?P<value>[^']+)'\s+WHERE\s+id\s*=\s*(?P<id>\d+)",
    re.IGNORECASE,
)


def patch_from_sql(sql: str, manifest: UIStateManifest) -> UIPatch | None:
    m = _SQL_UPDATE.search(sql.strip().rstrip(";"))
    if not m:
        return None
    target_id = int(m.group("id"))
    new_value = m.group("value")
    for ci, comp in enumerate(manifest.components):
        rows = getattr(comp, "rows", None)
        if not rows:
            continue
        for ri, row in enumerate(rows):
            if getattr(row, "id", None) == target_id:
                return UIPatch(
                    kind="patch",
                    ops=[PatchOp(op="replace",
                                 path=f"/components/{ci}/rows/{ri}/status",
                                 value=new_value)],
                )
    return None

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("hybrid.engine")

runtime: HybridRuntime | None = None
generation_lock: asyncio.Lock | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global runtime, generation_lock
    log.info("init_db")
    init_db()
    log.info("loading runtime (will crash if model fails to load)")
    runtime = HybridRuntime()
    generation_lock = asyncio.Lock()

    # Warm the constrainer cache so first click doesn't eat schema compile.
    for schema in (UIStateManifest, UIPatch, DatabaseAction):
        runtime._compile(schema)

    log.info("ready device=%s", runtime.device)
    yield


app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "ok": runtime is not None,
        "device": runtime.device if runtime else None,
        "model": __import__("os").environ.get("MODEL_ID", "ai21labs/AI21-Jamba2-3B"),
    }


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    log.info("ws connect")
    assert runtime is not None
    assert generation_lock is not None
    context: list[str] = ["BOOT: canvas connected, render initial dashboard"]
    current_manifest: UIStateManifest | None = None
    turn_seq = 0

    async def trace(phase: str, data: Any) -> None:
        frame = {
            "kind": "trace",
            "turn": turn_seq,
            "phase": phase,
            "ts": asyncio.get_event_loop().time(),
            "data": data,
        }
        try:
            await ws.send_text(json.dumps(frame, default=str, separators=(",", ":")))
        except Exception:
            pass
        try:
            preview = json.dumps(data, default=str)
        except TypeError:
            preview = repr(data)
        if len(preview) > 600:
            preview = preview[:600] + "…"
        log.info("TRACE turn=%d %s %s", turn_seq, phase, preview)

    async def gen(target_schema: type, max_new_tokens: int | None = None) -> object:
        async with generation_lock:
            return await asyncio.to_thread(
                runtime.generate, context, target_schema, max_new_tokens
            )

    async def emit_full(manifest: UIStateManifest) -> None:
        nonlocal current_manifest
        current_manifest = manifest
        await trace("MANIFEST_SEND", manifest.model_dump())
        await ws.send_text(json.dumps(manifest.model_dump(), separators=(",", ":")))

    async def emit_patch(patch: UIPatch) -> None:
        nonlocal current_manifest
        if current_manifest is None:
            log.warning("model emitted patch before any manifest existed; ignoring")
            return
        ops = [op.model_dump() for op in patch.ops]
        try:
            mutated = apply_patch(current_manifest.model_dump(), ops)
            current_manifest = UIStateManifest.model_validate(mutated)
        except (PatchError, Exception) as e:
            log.warning("patch failed (%s) — falling back to full re-render", e)
            await trace("PATCH_FAILED", {"error": str(e), "ops": ops})
            new_state = list_alerts()
            context.append(f"DB_STATE: {json.dumps(new_state)}")
            envelope = await gen(UIStateManifest)
            await emit_full(envelope)  # type: ignore[arg-type]
            return
        await trace("PATCH_SEND", {"ops": ops})
        await ws.send_text(json.dumps({"kind": "patch", "ops": ops}, separators=(",", ":")))

    async def run_turn(incoming: dict[str, Any] | None) -> None:
        nonlocal turn_seq, current_manifest
        turn_seq += 1

        db_state = list_alerts()
        await trace("DB_STATE_BEFORE", db_state)
        index_hint = "ROWS_INDEX: " + ", ".join(f"rows.{i}=id{r['id']}" for i, r in enumerate(db_state))

        is_command = bool(incoming) and incoming.get("event") == "USER_COMMAND"

        if incoming is None:
            await trace("BOOT", {"note": "initial render"})
        elif is_command:
            intent = str(incoming.get("intent", "")).strip()
            await trace("USER_COMMAND", {"intent": intent})
            context.append(f"USER_COMMAND: {intent}")
        else:
            await trace("USER_ACTION", incoming)
            context.append(f"USER_ACTION: {json.dumps(incoming)}")
        context.append(f"DB_STATE: {json.dumps(db_state)}")
        context.append(index_hint)

        # Boot + USER_COMMAND → full manifest. No prior state to patch (boot)
        # or whole-screen replace intended (command).
        if incoming is None or is_command:
            await trace("MODEL_INPUT", {"target": "UIStateManifest", "events_tail": context[-12:]})
            manifest = await gen(UIStateManifest)
            await trace("MODEL_OUTPUT", manifest.model_dump())  # type: ignore[union-attr]
            await emit_full(manifest)  # type: ignore[arg-type]
            return

        # Click → force DatabaseAction. UI affordances on the canvas (Resolve,
        # Acknowledge, SettingsForm submit) are all DB mutations in this demo,
        # and a 3B model on slow path is unreliable at picking SQL vs patch
        # from a free union. Forcing the schema removes the choice.
        # (To allow direct patches on UI-only clicks, branch on user_action here.)
        await trace("MODEL_INPUT", {"target": "DatabaseAction (forced on click)", "events_tail": context[-12:]})
        action = await gen(DatabaseAction, max_new_tokens=96)
        await trace("MODEL_OUTPUT", action.model_dump())  # type: ignore[union-attr]

        await trace("SQL_INTERCEPT", {"sql": action.sql})  # type: ignore[union-attr]
        result = execute_sql(action.sql)  # type: ignore[union-attr]
        await trace("SQL_RESULT", result)
        context.append(f"SQL_RESULT: {json.dumps(result)}")
        new_state = list_alerts()
        await trace("DB_STATE_AFTER", new_state)
        context.append(f"DB_STATE: {json.dumps(new_state)}")
        new_index = "ROWS_INDEX: " + ", ".join(f"rows.{i}=id{r['id']}" for i, r in enumerate(new_state))
        context.append(new_index)

        # Skip a second model call — derive the patch from the SQL we already
        # ran. The model decided WHICH row to mutate (in the SQL); reflecting
        # that on the canonical manifest is bookkeeping, not a decision.
        patch = patch_from_sql(action.sql, current_manifest) if current_manifest else None  # type: ignore[union-attr]
        if patch is None:
            # SQL didn't fit the simple mutation pattern → fall back to a
            # model-generated UIPatch for safety.
            await trace("MODEL_INPUT", {"target": "UIPatch (fallback)", "events_tail": context[-12:]})
            patch = await gen(UIPatch, max_new_tokens=128)
            await trace("MODEL_OUTPUT_FINAL", patch.model_dump())  # type: ignore[union-attr]
        else:
            await trace("PATCH_FROM_SQL", patch.model_dump())
        await emit_patch(patch)  # type: ignore[arg-type]

    try:
        await run_turn(None)
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                log.warning("non-JSON from client: %r", raw)
                continue
            log.info("ws recv: %s", msg)
            await run_turn(msg)
            if len(context) > 60:
                context = context[-40:]
    except WebSocketDisconnect:
        log.info("ws disconnect")


# Static canvas mount goes LAST so it doesn't shadow /ws or /health.
_DIST = pathlib.Path(__file__).resolve().parent.parent / "canvas" / "dist"
if _DIST.exists():
    app.mount("/", StaticFiles(directory=str(_DIST), html=True), name="canvas")
    log.info("serving canvas from %s", _DIST)
