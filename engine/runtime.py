"""HybridRuntime — native Jamba2 + xgrammar-constrained decoding.

Two backends:
  * xgrammar (default, ~0 ms/tok overhead) — used when `xgrammar` is importable.
  * outlines (fallback) — for hosts without xgrammar wheels.

Strict mode: no stubs, no fallback rendering. If the model or constrained
decoding fails, the exception propagates so the operator sees the real error.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time

import torch
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    LogitsProcessorList,
    StoppingCriteria,
    StoppingCriteriaList,
)

from .schemas import EngineOutputEnvelope, UIPatch, UIStateManifest

log = logging.getLogger("hybrid.runtime")

MODEL_ID = os.environ.get("MODEL_ID", "ai21labs/AI21-Jamba2-3B")
MAX_NEW_TOKENS = int(os.environ.get("MAX_NEW_TOKENS", "512"))
BACKEND = os.environ.get("CONSTRAINER", "xgrammar")  # xgrammar | outlines

_TRAILING_COMMA = re.compile(r",(\s*[}\]])")


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
        log.info("HybridRuntime device=%s dtype=%s model=%s backend=%s",
                 self.device, self.dtype, MODEL_ID, BACKEND)

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

        self._cache: dict[type, object] = {}
        self._backend_name = self._init_backend()
        log.info("model ready (constrainer=%s)", self._backend_name)

    # ------------------------------------------------------------------
    # backend selection
    # ------------------------------------------------------------------

    def _init_backend(self) -> str:
        if BACKEND == "xgrammar":
            try:
                import xgrammar as xgr  # type: ignore
                self._xgr = xgr
                tok_info = xgr.TokenizerInfo.from_huggingface(
                    self.tokenizer,
                    vocab_size=self.model.config.vocab_size,
                )
                self._xgr_compiler = xgr.GrammarCompiler(tok_info)
                return "xgrammar"
            except Exception as e:
                log.warning("xgrammar init failed (%s) — falling back to outlines", e)

        # outlines fallback
        import outlines  # type: ignore
        self._outlines = outlines
        self._outlines_model = outlines.from_transformers(self.model, self.tokenizer)
        return "outlines"

    # ------------------------------------------------------------------
    # cached compile per Pydantic schema class
    # ------------------------------------------------------------------

    def _compile(self, target_schema: type):
        cached = self._cache.get(target_schema)
        if cached is not None:
            return cached
        t = time.perf_counter()
        if self._backend_name == "xgrammar":
            schema_str = json.dumps(target_schema.model_json_schema())
            tight = False
            for kwargs in (
                {"any_whitespace": False, "strict_mode": True},
                {"any_whitespace": False},
                {"strict_mode": True},
                {},
            ):
                try:
                    cached = self._xgr_compiler.compile_json_schema(schema_str, **kwargs)
                    tight = bool(kwargs)
                    log.info("xgrammar.compile_json_schema accepted kwargs=%s", kwargs)
                    break
                except TypeError:
                    continue
            else:
                raise RuntimeError("xgrammar.compile_json_schema rejected all kwargs")
            if not tight:
                log.warning("xgrammar grammar permits any whitespace — output will pad")
        else:
            cached = self._outlines.Generator(self._outlines_model, output_type=target_schema)
        log.info("compiled %s grammar for %s in %.2fs",
                 self._backend_name, target_schema.__name__, time.perf_counter() - t)
        self._cache[target_schema] = cached
        return cached

    # ------------------------------------------------------------------
    # prompt + generate
    # ------------------------------------------------------------------

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
        budget = max_new_tokens or MAX_NEW_TOKENS
        prompt = self._format_prompt(context_events)
        compiled = self._compile(target_schema)

        t = time.perf_counter()
        if self._backend_name == "xgrammar":
            raw = self._generate_xgrammar(prompt, compiled, budget)
        else:
            raw = self._generate_outlines(prompt, compiled, budget)
        decode_s = time.perf_counter() - t

        out_len = len(raw) if isinstance(raw, str) else -1
        log.info(
            "GEN %s prompt_chars=%d out_chars=%d decode=%.2fs (%s)",
            target_schema.__name__, len(prompt), out_len, decode_s, self._backend_name,
        )
        log.info("RAW_OUTPUT (target=%s, len=%d): %r",
                 target_schema.__name__, out_len, raw)

        cleaned = _TRAILING_COMMA.sub(r"\1", raw) if isinstance(raw, str) else raw
        try:
            data = json.loads(cleaned) if isinstance(cleaned, str) else cleaned
        except json.JSONDecodeError as e:
            raise RuntimeError(
                f"Constrainer produced invalid JSON. backend={self._backend_name} "
                f"target={target_schema.__name__} err={e} raw={raw!r}"
            ) from e

        return target_schema.model_validate(data)

    # ------------------------------------------------------------------
    # xgrammar inference path
    # ------------------------------------------------------------------

    def _generate_xgrammar(self, prompt: str, compiled_grammar, budget: int) -> str:
        import xgrammar as xgr  # type: ignore

        ids = self.tokenizer(prompt, return_tensors="pt").to(self.model.device)
        prompt_len = int(ids["input_ids"].shape[-1])
        vocab_size = int(self.model.config.vocab_size)

        matcher = xgr.GrammarMatcher(compiled_grammar)
        bitmask = xgr.allocate_token_bitmask(1, vocab_size)
        state = {"first": True, "done": False}

        def processor(input_ids: torch.Tensor, scores: torch.Tensor) -> torch.Tensor:
            if state["first"]:
                state["first"] = False
            elif not state["done"]:
                last_tok = int(input_ids[0, -1].item())
                if not matcher.accept_token(last_tok):
                    state["done"] = True

            if state["done"] or matcher.is_terminated():
                state["done"] = True
                return scores

            matcher.fill_next_token_bitmask(bitmask)
            xgr.apply_token_bitmask_inplace(scores, bitmask.to(scores.device))
            return scores

        class GrammarTerminated(StoppingCriteria):
            """Stop the moment the JSON FSM reports a complete match.

            Without this, the grammar accepts trailing whitespace forever and
            decode runs to max_new_tokens — every turn pads with ~100 newlines.
            """

            def __call__(self, input_ids: torch.Tensor, scores: torch.Tensor, **_) -> bool:  # noqa: D401, ARG002
                return state["done"] or matcher.is_terminated()

        with torch.no_grad():
            out = self.model.generate(
                **ids,
                max_new_tokens=budget,
                do_sample=False,
                use_cache=True,
                logits_processor=LogitsProcessorList([processor]),
                stopping_criteria=StoppingCriteriaList([GrammarTerminated()]),
                pad_token_id=self.tokenizer.eos_token_id,
            )
        new_tokens = out[0, prompt_len:]
        return self.tokenizer.decode(new_tokens, skip_special_tokens=True)

    # ------------------------------------------------------------------
    # outlines fallback
    # ------------------------------------------------------------------

    def _generate_outlines(self, prompt: str, generator, budget: int) -> str:
        return generator(prompt, max_new_tokens=budget)
