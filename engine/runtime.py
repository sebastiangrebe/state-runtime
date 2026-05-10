"""HybridRuntime — native Jamba2 + outlines.

Strict mode: no stubs, no fallback rendering, no STUB_MODE. If the model or
constrained decoding fails, the exception propagates so the operator can see
the real PyTorch / kernel error.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time

import outlines
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

from .schemas import EngineOutputEnvelope, UIPatch, UIStateManifest

_TRAILING_COMMA = re.compile(r",(\s*[}\]])")

log = logging.getLogger("hybrid.runtime")

MODEL_ID = os.environ.get("MODEL_ID", "ai21labs/AI21-Jamba2-3B")
MAX_NEW_TOKENS = int(os.environ.get("MAX_NEW_TOKENS", "768"))


def _pick_device() -> tuple[str, torch.dtype]:
    if torch.cuda.is_available():
        return "cuda", torch.bfloat16
    if torch.backends.mps.is_available():
        return "mps", torch.float16
    return "cpu", torch.float32


SYSTEM_PROMPT = """You are State Runtime. You read events and emit ONE JSON object matching the schema you are constrained to.

Components: AlertTable(title,rows[{id,message,priority,status}]) | ToastNotification(message,tone) | MetricCard(label,value,sub) | LineChart(title,series,caption) | SettingsForm(title,fields[],submit_label,submit_action).
priority: low|medium|high. status: open|acknowledged|resolved.

Boot/USER_COMMAND → emit a UIStateManifest.
Click → emit DatabaseAction (SQL UPDATE/INSERT/DELETE on `alerts` only).
After SQL_RESULT → emit UIPatch with RFC6902 ops against the last manifest you emitted (e.g. {"op":"replace","path":"/components/0/rows/1/status","value":"resolved"}). Prefer a single small patch.

Always echo DB_STATE accurately. Never invent rows.
"""


class HybridRuntime:
    def __init__(self) -> None:
        self.device, self.dtype = _pick_device()
        log.info("HybridRuntime device=%s dtype=%s model=%s", self.device, self.dtype, MODEL_ID)

        log.info("loading tokenizer %s", MODEL_ID)
        self.tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, trust_remote_code=True)

        log.info("loading model %s on %s", MODEL_ID, self.device)
        kwargs: dict = {"torch_dtype": self.dtype, "trust_remote_code": True}
        if self.device == "cuda":
            kwargs["device_map"] = "auto"
        self.model = AutoModelForCausalLM.from_pretrained(MODEL_ID, **kwargs)
        if self.device != "cuda":
            self.model = self.model.to(self.device)
        self.model.eval()

        self.outlines_model = outlines.from_transformers(self.model, self.tokenizer)
        # Cache one Generator per schema class — outlines compiles a JSON-schema
        # regex/FSM on construction. Compiling once at boot saves 0.5-1 s/turn.
        self._generators: dict[type, object] = {}
        log.info("model ready")

    def _generator_for(self, target_schema: type):
        gen = self._generators.get(target_schema)
        if gen is None:
            t = time.perf_counter()
            gen = outlines.Generator(self.outlines_model, output_type=target_schema)
            log.info("compiled outlines FSM for %s in %.2fs",
                     target_schema.__name__, time.perf_counter() - t)
            self._generators[target_schema] = gen
        return gen

    def _format_prompt(self, context_events: list[str]) -> str:
        joined = "\n".join(f"- {e}" for e in context_events)
        return (
            f"{SYSTEM_PROMPT}\n\n"
            f"=== EVENT STREAM (most recent last) ===\n{joined}\n\n"
            f"=== RESPONSE (single JSON object matching the schema) ===\n"
        )

    def generate(
        self,
        context_events: list[str],
        target_schema: type,
        max_new_tokens: int | None = None,
    ) -> object:
        """Run constrained decode against `target_schema` (a Pydantic class).

        Returns a parsed instance of `target_schema`. Caller picks the schema:
          * UIStateManifest          — boot / USER_COMMAND replan
          * UIPatch                  — post-SQL re-prompt, force tiny diff
          * DatabaseAction           — click turn (SQL only)
        """
        budget = max_new_tokens or MAX_NEW_TOKENS
        prompt = self._format_prompt(context_events)

        t_total = time.perf_counter()
        generator = self._generator_for(target_schema)

        t_decode = time.perf_counter()
        raw = generator(prompt, max_new_tokens=budget)
        decode_s = time.perf_counter() - t_decode
        total_s = time.perf_counter() - t_total
        out_len = len(raw) if isinstance(raw, str) else -1
        log.info(
            "GEN %s prompt_chars=%d out_chars=%d decode=%.2fs total=%.2fs",
            target_schema.__name__, len(prompt), out_len, decode_s, total_s,
        )

        log.info("RAW_OUTPUT (target=%s, len=%d): %r",
                 target_schema.__name__, len(raw) if isinstance(raw, str) else -1, raw)

        if isinstance(raw, str):
            # Outlines 1.x JSON FSM is relaxed (permits trailing commas the model
            # learned from training data). json.loads is strict — clean first.
            cleaned = _TRAILING_COMMA.sub(r"\1", raw)
            try:
                data = json.loads(cleaned)
            except json.JSONDecodeError as e:
                raise RuntimeError(
                    f"Outlines produced invalid JSON (likely truncated at "
                    f"MAX_NEW_TOKENS={MAX_NEW_TOKENS}). "
                    f"Decoder error: {e}. Raw: {raw!r} | Cleaned: {cleaned!r}"
                ) from e
        else:
            data = raw

        return target_schema.model_validate(data)
