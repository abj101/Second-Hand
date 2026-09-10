from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Terminal = Literal["alive", "fail", "win", "timeout"]
ResetKind = Literal["in_game", "snapshot", "abort"]
PausedKind = Literal["off", "menu"]
DeviceKind = Literal["pad", "keyboard", "mouse"]
ArmKind = Literal["no_skill", "dump_all", "ratchet"]
StickDir = Literal["n", "ne", "e", "se", "s", "sw", "w", "nw"]
PerceptionSource = Literal["llm1", "ocr_cache", "scene_cache", "forced_rescan", "other"]
ExecutionDisposition = Literal[
    "not_attempted", "rejected_pre_dispatch", "partially_dispatched", "dispatched", "unknown"
]
EpisodeOutcome = Literal["win", "loss", "inconclusive"]


class Observation(BaseModel):
    screenshot_ref: str
    exec_screenshot_ref: str | None = None
    after_screenshot_ref: str | None = None
    ui_screenshot_ref: str | None = None
    peak_screenshot_ref: str | None = None
    burst_screenshot_ref: str | None = None
    dino_screenshot_ref: str | None = None
    vlm_screenshot_ref: str | None = None
    # Capture provenance is essential when diagnosing cropped/letterboxed UI.
    layout_source: str | None = None
    layout_crop: tuple[float, float, float, float] | None = None
    capture_source: str | None = None
    integrity_reason: str | None = None
    raw_client_w: int | None = None
    raw_client_h: int | None = None
    vision_box: tuple[float, float, float, float] | None = None
    click_box: tuple[float, float, float, float] | None = None
    epoch_reason: str | None = None
    epoch_step: int | None = None
    logical_x: float | None = None
    logical_y: float | None = None
    logical_x2: float | None = None
    logical_y2: float | None = None
    click_client_x: float | None = None
    click_client_y: float | None = None
    click_client_x2: float | None = None
    click_client_y2: float | None = None
    raw_screenshot_ref: str | None = None


class ActionLog(BaseModel):
    name: str | None = None
    duration_ms: int | None = None
    tool: str | None = None
    stick_l: StickDir | None = None
    stick_r: StickDir | None = None
    held: list[str] = Field(default_factory=list)
    tapped: list[str] = Field(default_factory=list)
    click_x: float | None = None
    click_y: float | None = None
    mark: int | None = None
    hover: bool = False
    click_x2: float | None = None
    click_y2: float | None = None
    target: int | None = None
    source_role: str | None = None
    target_role: str | None = None
    source_object_id: str | None = None
    target_object_id: str | None = None

    def derive_name(self) -> str | None:
        prefix = "hv" if self.hover else None
        first: str | None = None
        if self.mark is not None:
            first = f"{prefix or 'mk'}_{self.mark}"
        elif self.click_x is not None and self.click_y is not None:
            tag = prefix or "pt"
            first = f"{tag}_{self.click_x:.2f}_{self.click_y:.2f}"
        second: str | None = None
        if not self.hover:
            if self.target is not None:
                second = f"mk_{self.target}"
            elif self.click_x2 is not None and self.click_y2 is not None:
                second = f"pt_{self.click_x2:.2f}_{self.click_y2:.2f}"
        if first and second:
            return f"{first}>{second}"
        if first:
            return first
        parts: list[str] = []
        if self.stick_l:
            parts.append(f"stick_l_{self.stick_l}")
        if self.stick_r:
            parts.append(f"stick_r_{self.stick_r}")
        parts.extend(self.held)
        parts.extend(self.tapped)
        if parts:
            return "+".join(parts)
        return self.name


class PrimitiveLog(BaseModel):
    """One OS primitive known to have been dispatched.

    ``executed`` remains a compact legacy list.  This additive record is the
    source of truth for multi-stage mouse actions.
    """

    phase: Literal["action", "card", "target"] = "action"
    name: str
    x: float | None = None
    y: float | None = None


class CardTransaction(BaseModel):
    """Observed state of a targeted card transaction."""

    selection_state: Literal["not_applicable", "confirmed", "unconfirmed", "failed"] = (
        "not_applicable"
    )
    target_rebound: bool = False
    # A card source click is distinct from a completed targeted transaction.
    source_dispatched: bool = False
    target_dispatched: bool = False
    source_inspected: bool = False
    source_verified: bool = False
    effect_verified: bool = False
    outcome: Literal[
        "success",
        "auto_resolved",
        "source_unconfirmed",
        "target_required",
        "target_not_found",
        "target_rebind_failed",
        "effect_unverified",
        "stale",
        "dispatch_failed",
    ] | None = None
    snapshot_id: str | None = None
    source_object_id: str | None = None
    target_object_id: str | None = None
    original_card_mark: int | None = None
    original_target_mark: int | None = None
    rebound_target_mark: int | None = None
    probe_samples: int = 0
    probe_elapsed_ms: float = 0.0
    verification_evidence: list[str] = Field(default_factory=list)
    error: str | None = None


class StepResult(BaseModel):
    executed: list[str] = Field(default_factory=list)
    primitives: list[PrimitiveLog] = Field(default_factory=list)
    card_transaction: CardTransaction | None = None
    error: str | None = None
    pixel_changed: bool = False
    pixel_mad: float | None = None
    view_changed: bool = False
    view_mad: float | None = None
    column_shift: int | None = None
    prompt_cleared: bool = False
    stall: bool = False
    executed_as_proposed: bool = True
    invoked_gsm: str | None = None
    gsm_ticks: int = 0
    authoritative_state: dict[str, Any] = Field(default_factory=dict)
    observed_effects: list[str] = Field(default_factory=list)
    # ``unknown`` preserves the meaning of historical rows that predate
    # primitive-level execution accounting.
    execution_disposition: ExecutionDisposition = "unknown"


class MemoryHit(BaseModel):
    ids: list[str] = Field(default_factory=list)
    snippets: list[str] = Field(default_factory=list)


class StepLog(BaseModel):
    session_id: str
    episode_id: str
    step_id: int
    ts: str
    fixture: str
    observation: Observation
    thought: str = ""
    proposed: ActionLog | None = None
    action: ActionLog = Field(default_factory=ActionLog)
    result: StepResult = Field(default_factory=StepResult)
    metrics: dict[str, Any] = Field(default_factory=dict)
    score_delta: float | None = None
    cumulative_score: float = 0.0
    return_to_go: float | None = None
    terminal: Terminal = "alive"
    think_ms: float | None = None
    perceive_ms: float | None = None
    grab_ms: float | None = None
    saliency_ms: float | None = None
    dino_full_ms: float | None = None
    dino_inset_ms: float | None = None
    ocr_ms: float | None = None
    encode_ms: float | None = None
    after_ms: float | None = None
    obs_to_action_ms: float | None = None
    action_to_after_ms: float | None = None
    obs_age_ms: float | None = None
    idle_during_infer: bool = True
    failed_while_thinking: bool = False
    memory: MemoryHit = Field(default_factory=MemoryHit)
    policy_id: str = ""
    paused: PausedKind = "off"
    reset: ResetKind | None = None
    arm: ArmKind = "no_skill"
    skill_on: bool = False
    active_skill_id: str | None = None
    success: bool | None = None
    dino: list[dict[str, Any]] = Field(default_factory=list)
    prompt_on: bool | None = None
    prompt_cv: bool | None = None
    prompt_glyph: str | None = None
    prompt_source: str | None = None
    prompt_source_after: str | None = None
    prompt_text: str = ""
    prompt_id: str = ""
    notebook_schema: str = ""
    user_prompt_text: str = ""
    notebook_snapshot: str = ""
    architecture: str = ""
    # v2 decisions are stable-ID intents.  Legacy action/chord fields above are
    # retained as derived audit data and never need to be execution inputs.
    decision_schema: str = ""
    snapshot_id: str = ""
    turn_snapshot: dict[str, Any] = Field(default_factory=dict)
    intent: dict[str, Any] = Field(default_factory=dict)
    execution_plan: dict[str, Any] = Field(default_factory=dict)
    reason_backend: str = ""
    reason_fallback: str = ""
    reason_call: dict[str, Any] = Field(default_factory=dict)
    # Additive provenance for the unknown-first dual-agent prompt path.
    # Defaults keep historical JSONL records loadable without migration.
    perception_schema_version: str = ""
    reasoner_role: str = ""
    router_reason: str = ""
    epistemic_blockers: list[str] = Field(default_factory=list)
    scene: str = ""
    reason_ms: float | None = None
    discover_ms: float | None = None
    perceive_raw: str = ""
    reason_raw: str = ""
    tool_trace: list[str] = Field(default_factory=list)
    tool_events: list[dict[str, Any]] = Field(default_factory=list)
    llm1_raw: str = ""
    validated_perception: str = ""
    grounded_scene: str = ""
    llm1_schema_attempts: int = 0
    llm1_first_pass_valid: bool | None = None
    llm1_final_schema_valid: bool | None = None
    llm1_attempts: list[dict[str, Any]] = Field(default_factory=list)
    route_before_discovery: str = ""
    route_after_discovery: str = ""
    entity_route_facts: list[dict[str, Any]] = Field(default_factory=list)
    # Additive per-turn call provenance (Batch 5A). Defaults keep old JSONL loadable.
    perception_source: PerceptionSource = "other"
    llm1_called_this_turn: bool = False
    llm1_reused_from_step: int | None = None
    reasoner_called_this_turn: bool = False
    explicit_mcp_tools_called: list[str] = Field(default_factory=list)


class EpisodeLog(BaseModel):
    session_id: str
    episode_id: str
    fixture: str
    steps: int = 0
    return_value: float = 0.0
    return_to_go: float | None = None
    terminal: Terminal = "alive"
    unique_actions: list[str] = Field(default_factory=list)
    audit_flags: list[str] = Field(default_factory=list)
    reset: ResetKind = "in_game"
    arm: ArmKind = "no_skill"
    skill_on: bool = False
    active_skill_id: str | None = None
    success: bool = False
    outcome: EpisodeOutcome | None = None
    outcome_evidence: list[str] = Field(default_factory=list)
    memory_carry: dict[str, Any] = Field(default_factory=dict)

    def model_post_init(self, __context: Any) -> None:
        # Old episode rows have only terminal/success.  Terminal evidence is
        # authoritative: a legacy success=True timeout is still inconclusive.
        if self.outcome is None:
            self.outcome = episode_outcome(self.terminal)
        if not self.outcome_evidence:
            self.outcome_evidence = [f"terminal:{self.terminal}"]
        # Keep the legacy mirror truthful for both old rows and new writes.
        self.success = self.outcome == "win"


class SessionSummary(BaseModel):
    session_id: str
    fixture: str
    episodes: int = 0
    unique_device_names: list[str] = Field(default_factory=list)
    memory_size: int = 0
    mean_return: float = 0.0
    fail_rate: float = 0.0
    mean_think_ms: float | None = None
    audit_flags: list[str] = Field(default_factory=list)
    policy_id: str = ""
    arm: ArmKind = "no_skill"
    skill_on_frac: float | None = None
    success_rate: float | None = None
    wins: int = 0
    losses: int = 0
    inconclusive_episodes: int = 0
    interrupt_cv: int = 0
    interrupt_dino: int = 0
    stop_requested: bool = False
    flush_completed: bool = False


def episode_outcome(terminal: Terminal) -> EpisodeOutcome:
    """Classify an episode from durable terminal evidence, never a legacy flag."""
    if terminal == "win":
        return "win"
    if terminal == "fail":
        return "loss"
    return "inconclusive"


def execution_disposition(
    *,
    executed: list[str],
    transaction: CardTransaction | None,
    error: str | None,
) -> ExecutionDisposition:
    """Report dispatch facts without treating source selection as target success."""
    if transaction is not None:
        if transaction.target_dispatched:
            return "dispatched"
        if transaction.source_dispatched:
            targeted = bool(transaction.target_object_id or transaction.original_target_mark is not None)
            if targeted or transaction.outcome in {
                "target_required", "target_not_found", "target_rebind_failed", "dispatch_failed",
            }:
                return "partially_dispatched"
            return "dispatched"
        if transaction.error or transaction.outcome not in {None, "success", "auto_resolved"}:
            return "rejected_pre_dispatch"
    if executed:
        return "dispatched"
    if error:
        return "rejected_pre_dispatch"
    return "not_attempted"
