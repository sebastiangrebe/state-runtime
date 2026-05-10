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


SYSTEM_PROMPT = """You are the State Runtime — a continuous policy that paints a UI canvas
and (when needed) issues SQL against Postgres. You receive a stream of events
(initial boot, user clicks, USER_COMMAND text intents, SQL results) and respond
with a single JSON object that conforms to the schema you are constrained to.

Output is ALWAYS wrapped as {"payload": ...}. Three payload shapes:

  UI manifest (paint the canvas — boot or whole-screen change):
    {"kind": "ui", "components": [ ...components... ]}

  UI patch (small mutation of the previously-emitted manifest — RFC 6902):
    {"kind": "patch", "ops": [
        {"op": "replace", "path": "/components/0/rows/1/status", "value": "resolved"}
    ]}
    Use this whenever a click result only changes a few scalar values. Path is
    a JSON Pointer into the LAST manifest you emitted. Indexes are zero-based.

  Database action (alter facts; you will be re-prompted afterward for a patch
  or a manifest):
    {"kind": "sql", "sql": "UPDATE alerts SET status='resolved' WHERE id=42"}

Allowed components (each is one entry in the components array):

  {"component":"AlertTable","title":"<str>","rows":[
      {"id":<int>,"message":"<str>","priority":"low|medium|high",
       "status":"open|acknowledged|resolved"}
  ]}
  (buttons are NOT part of your output — the canvas derives Acknowledge/Resolve
   from each row's status. Just emit the row state.)

  {"component":"ToastNotification","message":"<str>","tone":"info|success|warning|error"}

  {"component":"MetricCard","label":"<str>","value":"<str>","sub":"<str|null>"}

  {"component":"LineChart","title":"<str>","series":[<float>,...],"caption":"<str|null>"}

  {"component":"SettingsForm","title":"<str>",
   "fields":[{"name":"<str>","label":"<str>","type":"text|number|toggle","value":"<str|null>"}],
   "submit_label":"<str>","submit_action":"<str>"}

Rules:
  * SQL is restricted to the alerts table. Use UPDATE/INSERT/DELETE only.
  * After a SQL_RESULT event, you MUST emit either a UIPatch (preferred — only
    the cells that actually changed) or a full UIStateManifest, never another SQL.
  * Echo DB_STATE accurately when rendering AlertTable. Do not fabricate rows.
  * Prefer UIPatch over a full manifest whenever the change is small. A status
    flip on one row should be a single replace op, not a re-emit of the whole
    table. Patches keep the loop fast — that is the whole point of running on
    a recurrent state-space model.
  * USER_COMMAND events are natural-language intents — re-plan the entire screen:
      "metrics" / "charts" / "performance"  → MetricCard(s) + LineChart(s)
      "settings" / "config"                 → SettingsForm
      "alerts" / "home" / "dashboard"       → MetricCard + AlertTable
      anything else                         → choose the best mix from the allowed set
  * Each USER_COMMAND replaces the screen — do not partially merge with the prior view.
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
        log.info("model ready")

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
    ) -> object:
        """Run constrained decode against `target_schema` (a Pydantic class).

        Returns a parsed instance of `target_schema`. Caller picks the schema:
          * UIStateManifest          — boot / USER_COMMAND replan
          * UIPatch                  — post-SQL re-prompt, force tiny diff
          * EngineOutputEnvelope     — first hop on a click (model picks SQL or
                                       direct patch)
        """
        prompt = self._format_prompt(context_events)
        generator = outlines.Generator(self.outlines_model, output_type=target_schema)
        raw = generator(prompt, max_new_tokens=MAX_NEW_TOKENS)

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
