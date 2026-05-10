"""Raw-generate benchmark.

Measures unconstrained transformers.generate() speed in isolation, so we can
tell whether the per-turn latency we see in the engine is dominated by:
  * the model's compute path (then this bench is also slow), or
  * outlines' per-token mask sync (then this bench is fast).

Run on the GPU box, with the engine NOT running (so the bench has the device
to itself):

    python scripts/bench_raw_generate.py
"""

from __future__ import annotations

import os
import time

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer


def main() -> None:
    mid = os.environ.get("MODEL_ID", "ai21labs/AI21-Jamba2-3B")
    print(f"[bench] loading {mid}")

    tok = AutoTokenizer.from_pretrained(mid, trust_remote_code=True)
    model = AutoModelForCausalLM.from_pretrained(
        mid, torch_dtype=torch.bfloat16, trust_remote_code=True
    ).to("cuda").eval()
    print(f"[bench] model on {next(model.parameters()).device}, "
          f"dtype {next(model.parameters()).dtype}")

    prompt = "Reply with one short JSON object describing an alert: "
    ids = tok(prompt, return_tensors="pt").to("cuda")

    print("[bench] warm")
    with torch.no_grad():
        model.generate(**ids, max_new_tokens=8, do_sample=False)

    for n in (50, 100, 200):
        torch.cuda.synchronize()
        t = time.perf_counter()
        with torch.no_grad():
            out = model.generate(**ids, max_new_tokens=n, do_sample=False, use_cache=True)
        torch.cuda.synchronize()
        dt = time.perf_counter() - t
        new = int(out.shape[-1] - ids["input_ids"].shape[-1])
        print(f"raw generate {n:>3}: {dt:6.2f}s   {new:>3} new toks   {dt/max(new,1)*1000:6.1f} ms/tok")


if __name__ == "__main__":
    main()
