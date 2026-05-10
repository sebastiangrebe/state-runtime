"""Pydantic schemas the model output is constrained to.

Two top-level shapes:
  * UIStateManifest — paint the canvas
  * DatabaseAction  — alter Postgres, then we re-prompt for a manifest
"""

from __future__ import annotations

from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, Field


class ActionPayload(BaseModel):
    """Shape of click signals the canvas sends *back* to the engine.

    Not part of the model's constrained output — kept because the WS handler
    and SettingsForm submission both reference it as a payload type hint.
    """

    user_action: str
    alert_id: Optional[int] = None
    note: Optional[str] = None


class AlertRow(BaseModel):
    """Model emits the row state. Canvas derives Acknowledge/Resolve buttons
    from `status` so the constrained decode stays small enough for a 3B
    hybrid on slow-path Mamba."""

    id: int
    message: str
    priority: Literal["low", "medium", "high"]
    status: Literal["open", "acknowledged", "resolved"]


class AlertTableComp(BaseModel):
    component: Literal["AlertTable"]
    title: str
    rows: list[AlertRow]


class ToastComp(BaseModel):
    component: Literal["ToastNotification"]
    message: str
    tone: Literal["info", "success", "warning", "error"] = "info"


class MetricCardComp(BaseModel):
    component: Literal["MetricCard"]
    label: str
    value: str
    sub: Optional[str] = None


class LineChartComp(BaseModel):
    component: Literal["LineChart"]
    title: str
    series: list[float] = Field(default_factory=list)
    caption: Optional[str] = None


class FormField(BaseModel):
    name: str
    label: str
    type: Literal["text", "number", "toggle"]
    value: Optional[str] = None


class SettingsFormComp(BaseModel):
    component: Literal["SettingsForm"]
    title: str
    fields: list[FormField] = Field(default_factory=list)
    submit_label: str = "Save"
    submit_action: str = "save_settings"


Component = Annotated[
    Union[AlertTableComp, ToastComp, MetricCardComp, LineChartComp, SettingsFormComp],
    Field(discriminator="component"),
]


class UIStateManifest(BaseModel):
    kind: Literal["ui"]
    components: list[Component]


class DatabaseAction(BaseModel):
    kind: Literal["sql"]
    sql: str


PatchValue = Union[str, int, float, bool]


class PatchOp(BaseModel):
    """RFC 6902 subset: replace / add / remove on JSON Pointer paths.

    Values are constrained to scalars to keep the outlines FSM small. Bigger
    structural mutations should fall back to a full UIStateManifest.
    """

    op: Literal["replace", "add", "remove"]
    path: str
    value: Optional[PatchValue] = None


class UIPatch(BaseModel):
    """Tiny patch the model emits when only a few cells change.

    Engine applies it to the canonical manifest, ships the same ops to the
    canvas, canvas applies in-place — no full re-render, ~5-15 token decode.
    """

    kind: Literal["patch"]
    ops: list[PatchOp]


EngineOutput = Annotated[
    Union[UIStateManifest, UIPatch, DatabaseAction],
    Field(discriminator="kind"),
]


class EngineOutputEnvelope(BaseModel):
    """Outlines wants a single root model — wrap the union."""

    payload: EngineOutput
