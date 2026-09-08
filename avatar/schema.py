from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

HarnessLevel = Literal["L0", "L1", "L2", "L3", "L4", "GSM"]
ScreenKind = Literal["ui", "gameplay"]
Verdict = Literal["success", "failure", "partial"]
OutcomeVerdict = Literal["success", "failure", "partial", "noop"]
PromptMode = Literal["pixels", "manual"]
GsmKind = Literal["rule", "rl"]
GsmStateSource = Literal["extracted", "pixels"]


class CaptureSettings(BaseModel):
    longest_side: int | Literal["native"] = "native"
    jpeg_quality: int = 85
    format: str = "jpeg"
    width: int | None = None
    height: int | None = None
    bytes: int | None = None
    crop: list[float] | None = None
    preset: str | None = None
    scout: bool = False
    settle_ms: float | None = None
    skipped_inference: bool = False
    layout_source: str | None = None


class ActionStep(BaseModel):
    name: str
    details: str = ""
    duration_ms: int | None = None


class SeeResponse(BaseModel):
    screen_kind: ScreenKind
    board: list[list[int | None]] | None = None
    score: int | None = None
    status: str = ""
    reasoning: str = ""
    presence: str = ""  # closed piece/enemy line
    camera: str = ""
    scene: str = ""


class PreviousTaskReflection(BaseModel):
    verdict: Verdict
    evidence: str
    optimizations: str = ""


class HistoricalSummary(BaseModel):
    narrative: str


class ActionDesignReflection(BaseModel):
    timing: str = ""
    sequencing: str = ""
    gsm_critique: str = ""
    proposed_labels: list[str] = Field(default_factory=list)


class GsmLabelRewrite(BaseModel):
    labels: list[str] = Field(default_factory=list)


class UnderstandResponse(BaseModel):
    screen_kind: ScreenKind
    goal: str
    next_move: str
    success_criteria: str = ""
    screen_position: str = "N/A"
    reasoning: str = ""


class ActResponse(BaseModel):
    goal: str = ""
    success_criteria: str = ""
    screen_position: str = "N/A"
    actions: list[ActionStep] = Field(default_factory=list)
    invoke_skill: str | None = None
    invoke_gsm: str | None = None
    reasoning: str = ""


class GsmSpec(BaseModel):
    """Closed-loop controller spec. Kinds are frozen per fixture at init."""

    name: str
    kind: GsmKind = "rule"
    fixture: str = ""
    recognize: str = ""
    end: str = ""
    labels: list[str] = Field(default_factory=list)
    state_space: dict[str, Any] = Field(default_factory=dict)
    action_space: list[str] = Field(default_factory=list)
    reward_ref: dict[str, Any] = Field(default_factory=dict)
    rewrites: int = 0
    enabled: bool = False
    embedding: list[float] | None = None
    state_source: GsmStateSource = "extracted"
    traces: list[str] = Field(default_factory=list)
    invoke_count: int = 0
    detector_family: str = ""
    detector_device: str = "cpu"


class QaNote(BaseModel):
    title: str
    would_file: bool
    evidence: str = ""


class ReflectResponse(BaseModel):
    visual: str = ""
    verdict: Verdict = "partial"
    task_design: str = ""
    action_plan: str = ""
    execution: str = ""
    qa_notes: list[QaNote] = Field(default_factory=list)
    next_step: str = ""


class TurnOutcomeRecord(BaseModel):
    """Per-turn feedback for L2 memory. Distinct from session-level ReflectResponse."""

    verdict: OutcomeVerdict = "partial"
    pixel_changed: bool = False
    board_changed: bool | None = None
    score_delta: int | None = None
    banned: list[str] = Field(default_factory=list)
    evidence: str = ""
    actions: list[str] = Field(default_factory=list)
    goal: str = ""
    state_key: str = ""


class TurnLog(BaseModel):
    screenshot_ref: str
    capture_settings: CaptureSettings
    model: str
    harness_level: HarnessLevel
    fixture: str
    paused: bool = True
    prompt_mode: PromptMode = "pixels"
    skills: Literal["on", "off"] = "off"
    reasoning: str | None = None
    action: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    latency_ms: float | None = None
    cost_usd: float | None = None
    stage: str = "see"
