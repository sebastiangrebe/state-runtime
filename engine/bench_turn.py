"""Standalone two-turn benchmark.

Turn 1: full UIStateManifest from boot.
Turn 2: simulate a click → SQL → forced UIPatch.
Reports per-turn duration so we can see the patch-vs-full delta.
"""

from __future__ import annotations

import json
import time

from engine.db import execute_sql, init_db, list_alerts
from engine.jsonpatch import apply_patch
from engine.runtime import HybridRuntime
from engine.schemas import EngineOutputEnvelope, UIPatch, UIStateManifest


def main() -> None:
    init_db()
    db_state = list_alerts()
    print(f"[bench] db rows: {len(db_state)}")

    t0 = time.perf_counter()
    rt = HybridRuntime()
    print(f"[bench] model loaded in {time.perf_counter() - t0:.1f}s on {rt.device}")

    context = [
        "BOOT: canvas connected, render initial dashboard",
        f"DB_STATE: {json.dumps(db_state)}",
    ]

    # === Turn 1: full manifest ===
    print("[bench] T1 generating full UIStateManifest...")
    t1 = time.perf_counter()
    manifest = rt.generate(context, UIStateManifest)
    print(f"[bench] T1 DONE in {time.perf_counter() - t1:.2f}s")
    print(json.dumps(manifest.model_dump(), indent=2))  # type: ignore[union-attr]

    # === Turn 2: simulate click → SQL → forced patch ===
    print("[bench] T2 simulating click on alert 1, force UIPatch...")
    context.append('USER_ACTION: {"user_action":"resolve_alert","alert_id":1}')
    sql_result = execute_sql("UPDATE alerts SET status='resolved' WHERE id=1")
    context.append(f"SQL_RESULT: {json.dumps(sql_result)}")
    new_state = list_alerts()
    context.append(f"DB_STATE: {json.dumps(new_state)}")

    t2 = time.perf_counter()
    patch = rt.generate(context, UIPatch)
    elapsed = time.perf_counter() - t2
    print(f"[bench] T2 DONE in {elapsed:.2f}s  ({len(patch.ops)} ops)")  # type: ignore[union-attr]
    print(json.dumps(patch.model_dump(), indent=2))  # type: ignore[union-attr]

    try:
        merged = apply_patch(manifest.model_dump(), [op.model_dump() for op in patch.ops])  # type: ignore[union-attr]
        print("[bench] patch applied — merged manifest:")
        print(json.dumps(merged, indent=2))
    except Exception as e:
        print(f"[bench] patch apply failed: {e}")

    # cleanup
    execute_sql("UPDATE alerts SET status='open' WHERE id=1")


if __name__ == "__main__":
    main()
