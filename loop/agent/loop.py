from __future__ import annotations

import os
import json
import random
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Literal, Sequence

import numpy as np

from loop.agent.heuristic import HAND_Y, HEURISTIC_POLICY_ID, heuristic_action
from loop.agent.decision import (
    ACT_SYSTEM_PROMPT,
    ACT_PROMPT_ID,
    ActReasoner,
    ActReasonerError,
    BoundingBox,
    CardSnapshot,
    ClickControlIntent,
    compact_act_prompt,
    ControlSnapshot,
    EndTurnIntent,
    InspectIntent,
    PlayCardIntent,
    TargetSnapshot,
    TurnSnapshot,
    legal_intents_for_snapshot,
)
from loop.agent.entities import (
    EntityTracker,
    _row_center,
    entity_route_facts,
    is_anonymous_enemy_label,
    is_frame_mark_token,
    is_player_row,
    is_prompt_instance_id,
    is_targetable_anonymous_enemy,
    pending_inspect_target,
    player_display_label,
)
from loop.agent.discovery import (
    DiscoveryObject,
    DiscoveryResult,
    DiscoveryWorkflow,
    _pad,
    crop,
    inventory_unknowns,
    inspection_contract,
    rebind_unknowns,
    validate_contract,
)
from loop.agent.perception_cache import PerceptionCache, PerceptionReuse, ocr_uncertain_for_reuse
from loop.agent.perceive import (
    inject_host_enemies_into_scene,
    perceive_scene,
    reconcile_combat_screen,
    scene_from_ocr,
    should_run_llm1_perceive,
    should_skip_combat_perceive_rescan,
    validate_combat_perceive,
)
from loop.agent.prompts import (
    approach_evidence_line,
    allowed_sets_from_controls,
    clickable_labels_from_list,
    close_prompt_line,
    format_host_hud_block,
    lock_evidence_line,
    prompt_id,
    NOTEBOOK_SCHEMA_VERSION,
    reason_controls_for_turn,
    reason_hud_stats,
    reason_state_from_host,
    reason_turn_user_prompt,
    strip_mark_leaks,
    system_for,
    turn_user_prompt,
    two_stage_system_for,
    allowed_controls_for_turn,
    choice_in_allowed_controls,
    detector_candidates_from_marks,
    detector_typed_rows_from_marks,
    scene_perceive_user,
    attack_cards_from_controls,
    instance_ids_from_controls,
    validate_choice,
    inspectable_targets_from_controls,
    user_prompt,
)
# Prompt variants are being introduced alongside the runtime.  Import the
# module rather than the new names directly so an older prompt module remains
# usable during a rolling update.
import loop.agent.prompts as _prompt_defs
from loop.config import DEFAULT_ACT_FALLBACK_MODEL, DEFAULT_CLOUD_MODEL, DEFAULT_MODEL
from loop.eval.outcome import compute_step_outcome
from loop.fixtures.base import FixtureBase
from loop.gsm.approach import ApproachGsmRuntime, approach_target_cx
from loop.gsm.explore import ExploreGsmRuntime, SurveyGsmRuntime
from loop.live import publish, stop_requested
from loop.log.schema import (
    ActionLog,
    CardTransaction,
    EpisodeLog,
    MemoryHit,
    Observation,
    PerceptionSource,
    PrimitiveLog,
    SessionSummary,
    StepLog,
    StepResult,
)
from loop.log.store import EpisodeStore, registry_tool_names
from loop.memory.episodic import WorkingMemory
from loop.memory.journal import (
    FACE,
    FactJournal,
    click_query,
    face_device,
    face_try_outcome,
    heading_from_boxes,
    journal_path,
    run_journal_path,
    kind_from_boxes,
)
from loop.memory.lexicon import screen_from_labels
from loop.memory.overlay_state import ControlSuppressionState, OverlayState
from loop.memory.notebook import Notebook, avoid_devices, listing_has_dialog, overlay_screen
from loop.memory.screen_objects import ScreenObjectStore
from loop.memory.skills import Skill, SkillBank
from loop.memory.store import MemoryStore
from loop.memory.semantic import SemanticMemoryStore
from loop.mcp.orchestrator import ToolOrchestrator
from loop.mcp.registry import ToolRegistry
from loop.mcp.tools.memory import register_reasoner_tools
from loop.mcp.tools.targeting import (
    current_snapshot_identity,
    invalidate_hand_snapshot,
    object_role,
    plan_card_by_id,
    register_targeting_tools,
    resolve_planned_source,
    resolve_planned_target,
    validate_stable_transaction,
)
from loop.model.gateway import VlmGateway
from loop.os.capture import encode_image
from loop.os.frame_model import (
    ActionSurface,
    FrameContext,
    FrameModel,
    GENERIC_CONTROL_ROLES,
    build_action_surface,
    build_frame_model,
    control_key_from_row,
    surface_fingerprint,
)
from loop.state.game_state import GameState, LegalAction, reconcile as reconcile_game_state
from loop.os.primitives import mouse_move
from loop.os.aim import (
    AimGuide,
    AimMark,
    _copy_mark,
    action_from_labels,
    action_inspect_label,
    bind_aim,
    build_aim,
    build_marks,
    detect_marks,
    clamp_to_overlay,
    fill_from_aim,
    apply_enemy_bboxes,
    apply_hand_bboxes,
    ground_combat_labels,
    merge_cached_enemy_marks,
    seed_enemies_from_host,
    ground_spatial_labels,
    gui_click_marks,
    dino_boxes_to_marks,
    fuse_dino_marks,
    in_combat_sprite_band,
    label_kind,
    needs_tooltip,
    number_spatial_nodes,
    ocr_click_marks,
    paint_aim,
    prefer_hover,
    steer_off_danger,
    steer_to_labeled,
)
from loop.os.affordance import (
    affordance_hit_for_action,
    detect_affordance_diff,
    effective_legal_actions,
    merge_legal_actions,
    resolve_legal_chord,
    sub_action_warrants_affordance_refresh,
)
from loop.os.chords import (
    action_from_skill,
    apply_chord_duration,
    bind_prompt_devices,
    extract_prompt_devices,
    fill_empty_chord,
    flatten_parsed,
    interrupt_chord,
    is_chord,
    is_empty_chord,
    parse_action,
    skill_invoke_gsm,
    walk_stall_count,
)
from loop.os.gamepad import identify_our_slot, pad_debug_line, xinput_connected
from loop.os.ground import (
    InteractHint,
    boxes_payload,
    detect_interact,
    filter_grounded,
    grounded_with_loot,
    interrupt_ready,
    is_arrival,
    boxes_are_near,
    load_detector,
    lock_demote_labels,
    paint_boxes,
    prompt_confirmed,
    visual_labels_for,
)
from loop.os.profile import mean_absdiff, roi_thumb, wait_until_quiet
from loop.os.saliency import (
    PeakSampler,
    bright_patches,
    compose_obs,
    format_patches,
    prompt_glyph,
    prompt_inset,
    prompt_visible,
    read_prompt_text,
    result_caption,
    skip_interact_interrupt,
    update_last_interrupt_glyph,
)
from loop.os.tables import names, prompt_listing
from loop.os.window import foreground_title
from loop.reward.audits import audit_episode, audit_step, explore_progress, hud_identity_stuck, score_delta
from loop.reward.hud import HudPoller, hud_visible, is_black_frame
from loop.os.sts_stats import extract_sts_stats, stats_to_score_metrics
from loop.fixtures.sts2 import normalize_host_stats
from loop.reward.sts import combat_exit_evidence, extract_sts_hud, stale_to_act, turn_reward
from loop.eval.export import write_sft_jsonl, write_train_jsonl


def _ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000.0


def _strip_locked_face(action: ActionLog, *, locked: bool) -> ActionLog:
    if not locked or face_device(action) is None:
        return action
    out = action.model_copy(deep=True)
    out.tapped = [tok for tok in out.tapped if str(tok).strip().lower() not in FACE]
    out.held = [tok for tok in out.held if str(tok).strip().lower() not in FACE]
    if (out.name or "").strip().lower() in FACE:
        out.name = None
    if not out.stick_l and not out.stick_r and not out.tapped and not out.held:
        out.stick_r = "e"
    out.name = out.derive_name()
    return out


BLACK_FRAME_HINT = (
    "Live grab is black (exclusive fullscreen?). Use borderless windowed and re-run "
    "`python -m loop perceive --fixture elden` before collect."
)

PAD_SLOT_HINT = (
    "Virtual pad is not XInput slot 0, so Elden Ring will ignore stick/buttons. "
    "Unplug every other controller. In Steam: Elden Ring → Properties → Controller "
    "→ Disable Steam Input. Then re-run collect so this process can take slot 0."
)

ArmName = Literal["no_skill", "dump_all", "ratchet"]
ArchitectureName = Literal["overlay", "two_stage"]


def _say(line: str) -> None:
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        print(line.encode("ascii", errors="replace").decode("ascii"), flush=True)


def _combat_trace(category: str, msg: str) -> None:
    """Structured Phase-6 combat validation log line."""
    _say(f"  {category:<16} {msg}")


def _one_line(text: str, limit: int = 280) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"


_MAX_REASON_USER_CHARS = 6000
_MAX_REASON_JOURNAL_CHARS = 3200


def _bounded_reason_journal(parts: list[str]) -> str:
    """Preserve authoritative state first; trim historical prose deterministically."""
    kept: list[str] = []
    remaining = _MAX_REASON_JOURNAL_CHARS
    for index, part in enumerate(parts):
        text = str(part or "")
        if not text or remaining <= 0:
            continue
        limit = remaining if index == 0 else min(1200, remaining)
        clipped = text[:limit]
        kept.append(clipped)
        remaining -= len(clipped)
    return "\n".join(kept)


def _bounded_reason_user(prompt: str) -> str:
    """State, legal controls, and current observation lead this prompt."""
    return str(prompt or "")[:_MAX_REASON_USER_CHARS]


def _bounded_visible_text(items: list[str] | tuple[str, ...] | Any, limit: int = 1200) -> list[str]:
    """Current OCR/VLM observation is useful, but cannot crowd out state."""
    kept: list[str] = []
    remaining = limit
    for item in items or []:
        text = str(item or "").strip()
        if not text or remaining <= 0:
            continue
        clipped = text[:remaining]
        kept.append(clipped)
        remaining -= len(clipped)
    return kept


def explore_owns_pad(
    *,
    interrupt: bool,
    yield_lock: bool,
    stall: bool,
    gsm_busy: bool,
) -> bool:
    """Explore motor on stall/busy. Lock-yield and interrupt give the turn back."""
    if interrupt or yield_lock:
        return False
    return gsm_busy or stall


def format_chord(action: ActionLog) -> str:
    parts: list[str] = []
    if action.mark is not None:
        parts.append(f"mark={action.mark}")
    if action.hover:
        parts.append("hover")
    if action.click_x is not None and action.click_y is not None:
        parts.append(f"xy={action.click_x:.2f},{action.click_y:.2f}")
    if action.target is not None:
        parts.append(f"target={action.target}")
    if action.click_x2 is not None and action.click_y2 is not None:
        parts.append(f"xy2={action.click_x2:.2f},{action.click_y2:.2f}")
    if action.stick_l:
        parts.append(f"stick_l={action.stick_l}")
    if action.stick_r:
        parts.append(f"stick_r={action.stick_r}")
    if action.held:
        parts.append("held=" + "+".join(action.held))
    if action.tapped:
        parts.append("tapped=" + "+".join(action.tapped))
    if action.name and not parts:
        parts.append(action.name)
    if action.duration_ms is not None:
        parts.append(f"{action.duration_ms}ms")
    return " ".join(parts) if parts else "(none)"


def _hud_line(metrics: dict[str, Any] | None) -> str:
    if not metrics:
        return "hud=off"
    bits: list[str] = []
    hp, hp_max = metrics.get("hp"), metrics.get("hp_max")
    if hp is not None:
        bits.append(f"hp={hp}" + (f"/{hp_max}" if hp_max is not None else ""))
    for key in ("gold", "floor", "energy", "screen"):
        value = metrics.get(key)
        if value is not None and value != "":
            bits.append(f"{key}={value}")
    if metrics.get("fail_screen"):
        bits.append("fail")
    if metrics.get("win_screen"):
        bits.append("win")
    if bits:
        return " ".join(bits)
    if not hud_visible(metrics):
        extra = []
        if metrics.get("fail_screen"):
            extra.append("fail")
        if metrics.get("win_screen"):
            extra.append("win")
        return "hud=off" + ((" " + " ".join(extra)) if extra else "")
    for key in ("health_frac", "stamina_frac", "runes"):
        value = metrics.get(key)
        if value is not None:
            bits.append(f"{key}={value:.2f}" if isinstance(value, float) else f"{key}={value}")
    if metrics.get("fail_screen"):
        bits.append("fail")
    if metrics.get("win_screen"):
        bits.append("win")
    return " ".join(bits) if bits else "hud=off"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def fill_return_to_go(steps: list[StepLog], *, gamma: float = 0.99) -> None:
    g = 0.0
    for step in reversed(steps):
        g = float(step.score_delta or 0.0) + gamma * g
        step.return_to_go = g


@dataclass
class _CardExecution:
    """One verified card -> target operation, owned by the turn harness."""

    action: ActionLog
    executed: list[str]
    after: Any
    primitives: list[PrimitiveLog]
    transaction: CardTransaction
    error: str | None = None


@dataclass(frozen=True)
class _PostClickProbe:
    """Bounded raw-frame evidence captured without OCR or scene grounding."""

    pre_frame: Any
    fresh_frame: Any
    frames: tuple[Any, ...]
    deltas: tuple[float, ...]
    elapsed_ms: float


@dataclass
class CollectConfig:
    lives: int = 1
    max_steps: int = 32
    timeout_s: float = 600.0
    burst_ms: int = 400
    stick_ms: int = 2000
    arm: ArmName = "no_skill"
    memory: bool = False
    skip_models: bool = False
    policy_id: str = DEFAULT_MODEL
    paused: str = "off"
    skill_off_frac: float = 0.5
    rng_seed: int | None = None
    gate_black: bool = True
    eval_snapshot: bool = False
    snapshot_on_softlock: bool = True
    hud_poll_s: float = 1.0
    peak_hz: float = 10.0
    peak_trail_s: float = 0.5
    dino: bool = False
    # A production collection is invalid without a loaded Grounding DINO
    # model.  Unit fixtures can opt out explicitly.
    dino_required: bool = False
    dino_device: str = "cpu"
    dino_model: str = "IDEA-Research/grounding-dino-base"
    dino_labels: list[str] | None = None
    gui: bool = True
    gui_device: str = "cpu"
    gui_model: str = "gpa"
    gui_download: bool = False
    debug_frames: bool = True
    survey: bool = False
    until_fail: bool = False
    stuck_window: int = 24
    policy: str = "vlm"
    architecture: ArchitectureName = "overlay"
    max_tool_rounds: int = 3
    tool_budget_s: float = 8.0
    # Perception may be verbose; ACT is a compact stable-ID intent only.
    perceive_max_tokens: int = 1536
    perceive_timeout_s: float = 15.0
    reason_max_tokens: int = 96
    reason_cloud_timeout_s: float = 8.0
    reason_local_timeout_s: float = 15.0
    reason_fallback_model: str = DEFAULT_ACT_FALLBACK_MODEL
    # Unknown-first: inspect unidentified hostiles before ACT.
    identify_before_act: bool = True
    debug_overlay: bool = False
    infer_budget_s: float = 30.0
    memory_scope: Literal["run", "episode"] = "run"
    # Default LLM1 is cloud Gemma; CLI selects cloud ACT for STS2 v2.
    perceive_model: str = DEFAULT_CLOUD_MODEL

    def __post_init__(self) -> None:
        if not self.perceive_model:
            self.perceive_model = DEFAULT_CLOUD_MODEL


class CollectAgent:
    def __init__(
        self,
        fixture: FixtureBase,
        store: EpisodeStore,
        *,
        memory: MemoryStore | None = None,
        skills: SkillBank | None = None,
        gateway: VlmGateway | None = None,
        perceive_gateway: VlmGateway | None = None,
        config: CollectConfig | None = None,
        detector: Any | None = None,
    ) -> None:
        self.fixture = fixture
        self.store = store
        self.memory = memory
        self.skills = skills
        self.gateway = gateway
        self.perceive_gateway = perceive_gateway
        self.config = config or CollectConfig()
        self.session_id = uuid.uuid4().hex[:12]
        self._all_actions: set[str] = set()
        self._episode_returns: list[float] = []
        self._episode_success: list[bool] = []
        self._skill_on_flags: list[bool] = []
        self._think_ms: list[float] = []
        self._session_flags: list[str] = []
        self._rr = 0
        self.remember_calls = 0
        self.working = WorkingMemory()
        journal_file = None
        if fixture.id != "fake":
            if fixture.id == "sts2" and getattr(fixture, "pace", "") == "turn":
                journal_file = run_journal_path(store.run_dir)
            else:
                journal_file = journal_path(fixture.id)
        self.journal = FactJournal(
            path=journal_file,
            fact_cap=64,
        )
        self.notebook = Notebook(self.journal, memory_scope=self.config.memory_scope)
        self._grounded_path = store.run_dir / "memory_grounded.json"
        if self._grounded_path.exists():
            self.notebook.load_grounded(self._grounded_path)
        # Durable rules/identities only. Frame marks and current state never persist here.
        self.semantic_memory = SemanticMemoryStore.for_fixture(fixture.id)
        self._last_notebook_snapshot = ""
        self._carry_recall: list[str] = []
        self._last_walk: str | None = None
        self.last_user_prompt = ""
        self.last_system_prompt = system_for(
            self.config.arm, pace=getattr(fixture, "pace", "realtime")
        )
        self.rng = random.Random(self.config.rng_seed)
        self._episode_n = 0
        self._stopped = False
        self._session_finalized = False
        self._final_summary: SessionSummary | None = None
        self._hud_poller: HudPoller | None = None
        self._hud_seen: bool | None = None
        self._carry_after = None
        self._carry_caption = ""
        self._detector = detector
        self._dino_loaded = detector is not None
        self._explore = ExploreGsmRuntime()
        self._survey = SurveyGsmRuntime()
        self._approach = ApproachGsmRuntime()
        self._survey_done = False
        self._last_stall = False
        self._gsm_busy = False
        self._interrupt_cv = 0
        self._interrupt_dino = 0
        self._last_interrupt_glyph: str | None = None
        self._last_click: tuple[float, float] | None = None
        self._last_hover_key: object | None = None
        self._hovered_marks: set[int] = set()
        self._aim = None
        self._selected: str | None = None
        self._step_id = 0
        self._obs_metrics: dict[str, Any] = {}
        self._gui_status = "gui=off"
        self._screen_objects = ScreenObjectStore(store.run_dir / "objects.db")
        self._discovery = DiscoveryWorkflow(artifacts_root=store.run_dir / "frames")
        self._last_discovery_result: DiscoveryResult | None = None
        self._last_discover_ms: float = 0.0
        self._discovery_attempts: dict[str, int] = {}
        self._tool_registry = ToolRegistry()
        self._tool_orchestrator = ToolOrchestrator(
            self._tool_registry,
            max_rounds=self.config.max_tool_rounds,
            budget_s=self.config.tool_budget_s,
        )
        self._last_scene: str = ""
        self._last_tool_trace: list[str] = []
        self._last_tool_events: list[dict[str, Any]] = []
        self._last_decision_schema: str = ""
        self._last_snapshot_id: str = ""
        self._last_turn_snapshot: dict[str, Any] = {}
        self._last_intent: dict[str, Any] = {}
        self._last_execution_plan: dict[str, Any] = {}
        self._planned_hand_signature: tuple[Any, ...] | None = None
        self._stale_source_plan: tuple[Any, ...] | None = None
        self._last_reason_backend: str = ""
        self._last_reason_fallback: str = ""
        self._last_reason_call: dict[str, Any] = {}
        self._last_execution_failure: dict[str, Any] | None = None
        self._last_reason_state_key: str = ""
        self._unchanged_failure_count: int = 0
        self._v2_control_objects: dict[str, Any] = {}
        self._last_perceive_raw: str = ""
        self._last_llm1_raw: str = ""
        self._last_validated_perception: str = ""
        self._last_grounded_scene: str = ""
        self._last_llm1_attempts: list[dict[str, Any]] = []
        self._last_llm1_first_pass_valid: bool | None = None
        self._last_llm1_final_schema_valid: bool | None = None
        self._last_llm1_schema_attempts: int = 0
        self._route_before_discovery: str = ""
        self._route_after_discovery: str = ""
        self._entity_route_facts: list[dict[str, Any]] = []
        self._last_reason_raw: str = ""
        self._reason_ms: float = 0.0
        self._last_reasoner_role: str = ""
        self._last_router_reason: str = ""
        self._last_epistemic_blockers: list[str] = []
        self._last_choice_label: str | None = None
        self._last_outcome: str | None = None
        self._host_stats: dict[str, Any] = {}
        self._scene_desc: Any = None
        self._frame_model: FrameModel | None = None
        self._action_surface: ActionSurface | None = None
        self._empty_action_surface_key: str = ""
        self._empty_action_surface_count: int = 0
        self._decision_softlock: bool = False
        self._game_state: GameState | None = None
        self._last_state_action: LegalAction | None = None
        self._last_state_expected_effects: tuple[str, ...] = ()
        self._last_obs_image: Any = None
        self._last_ocr_words: list[Any] = []
        self._last_quiet_ms: float = 0.0
        self._last_quiet_polls: int = 0
        self._blocked_marks: set[str] = set()
        self._prev_frame_image: Any = None
        self._affordance_verdicts: dict[str, Any] = {}
        self._entity_tracker = EntityTracker()
        self._legal_actions: list[dict[str, Any]] = []
        self._step_recheck_count = 0
        self._step_recheck_image: Any = None
        self._step_illegal_blocked = False
        self._last_legal_reason: str | None = None
        self._pending_exec_error: str | None = None
        self._last_targeting_opts: dict[str, Any] | None = None
        self._perceive_retry_reason: str | None = None
        self._overlay_state = OverlayState()
        self._control_suppression = ControlSuppressionState()
        self._last_surface_screen: str = ""
        self._last_surface_membership: tuple[str, ...] = ()
        self._prev_surface_controls: list[dict[str, Any]] = []
        self._prev_host_stats: dict[str, Any] = {}
        self._stuck_combat_steps = 0
        self._perception_recovery_steps = 0
        self._card_action_attempts = 0
        self._verified_card_actions = 0
        self._false_card_transactions = 0
        self._cached_perceive_scene: Any = None
        self._cached_perceive_scene_step: int | None = None
        self._force_perceive = False
        self._perception_cache = PerceptionCache()
        self._force_inspect = False
        self._turn_perception_source: PerceptionSource = "other"
        self._turn_llm1_called = False
        self._turn_llm1_reused_from_step: int | None = None
        self._turn_reasoner_called = False
        self._combat_enter_logged = False
        self._combat_index = 0
        self._combat_won_keys: set[str] = set()
        self._was_in_combat = False
        self._current_episode_id = ""

    def _read_host_stats(self, image: Any) -> dict[str, Any]:
        stats = extract_sts_stats(image)
        if getattr(self.fixture, "id", "") == "sts2":
            stats = normalize_host_stats(stats)
        return stats

    def _track_combat_index(self) -> None:
        host = getattr(self, "_host_stats", {}) or {}
        screen = str(host.get("screen") or "").strip().lower()
        in_combat = bool(host.get("combat_active")) or screen == "combat"
        if in_combat and not self._was_in_combat:
            self._combat_index += 1
        self._was_in_combat = in_combat

    def _apply_combat_exit_reset(self, *, reason: str) -> None:
        """Idempotent combat-only reset before reward grounding/decision."""
        self._selected = None
        self._entity_tracker = EntityTracker()
        self._perception_cache.reset()
        self._cached_perceive_scene = None
        self._cached_perceive_scene_step = None
        self._scene_desc = None
        self._action_surface = None
        self._frame_model = None
        self._last_execution_plan = {}
        self._last_execution_failure = None
        self._pending_exec_error = None
        self._planned_hand_signature = None
        self._stale_source_plan = None
        self._last_intent = {}
        self._force_perceive = True
        self._combat_enter_logged = False
        suppression = getattr(self, "_control_suppression", None)
        if suppression is not None:
            suppression.reset("")
        self._last_surface_screen = ""
        self._last_surface_membership = ()
        self._prev_surface_controls = []
        store = getattr(self, "_screen_objects", None)
        if store is not None:
            invalidate_hand_snapshot(store, reason=reason)
            store.save_frame(
                run_id=self.session_id,
                step_id=int(self._step_id or 0),
                marks=[],
                screen="reward",
                source="combat-exit-reset",
            )
        self._aim = None

    def _maybe_apply_combat_exit_transition(
        self,
        metrics: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Detect combat→reward and emit durable combat_won metrics once."""
        out = dict(metrics or self._obs_metrics or {})
        if getattr(self.fixture, "id", "") != "sts2":
            return out
        prev = getattr(self, "_prev_host_stats", {}) or {}
        curr = getattr(self, "_host_stats", {}) or {}
        evidence = combat_exit_evidence(prev, curr)
        if not evidence.confirmed:
            return out
        self._apply_combat_exit_reset(reason="combat-won")
        curr = normalize_host_stats(dict(curr))
        self._host_stats = curr
        out["screen"] = "reward"
        out["activity"] = "reward"
        out["combat_active"] = False
        out.pop("energy", None)
        out.pop("energy_max", None)
        out.pop("block", None)
        self._obs_metrics = dict(out)
        transition_id = f"{self._current_episode_id}:{self._combat_index}"
        if transition_id not in self._combat_won_keys:
            self._combat_won_keys.add(transition_id)
            out["combat_won"] = True
            out["combat_won_transition_id"] = transition_id
            out["combat_exit_score"] = evidence.score
            out["combat_exit_reasons"] = list(evidence.reasons)
        else:
            out.pop("combat_won", None)
            out.pop("combat_won_transition_id", None)
            out.pop("combat_exit_score", None)
            out.pop("combat_exit_reasons", None)
        return out

    def _rebuild_reward_inventory(self, view: Any, *, screen: str = "reward") -> None:
        """Publish reward-only controls before the next decision."""
        if view is None:
            return
        reuse = self._perception_reuse(view, aim_screen=screen)
        words, marks, gui_marks, _ = self._build_turn_marks(
            view,
            aim_screen=screen,
            reuse=reuse,
        )
        stamped = self._screen_objects.save_frame(
            run_id=self.session_id,
            step_id=int(self._step_id or 0),
            marks=marks,
            screen=screen,
            source="reward-rebuild",
        )
        self._aim = AimGuide.from_marks(view, stamped, last_xy=self._last_click)
        self._build_frame_model()
        if self._scene_desc is not None:
            self._finalize_action_surface(self._scene_desc, informational=[])

    def _combat_screen_now(self) -> bool:
        """True when underlying activity allows combat/entity play (overlays OK)."""
        host_stats = getattr(self, "_host_stats", {})
        obs_metrics = getattr(self, "_obs_metrics", {})
        return self._allows_entity_targets(
            screen=str(
                host_stats.get("screen")
                or obs_metrics.get("screen")
                or getattr(getattr(self, "_scene_desc", None), "screen", None)
                or ""
            )
            or None,
        )

    def _allows_entity_targets(self, *, screen: str | None) -> bool:
        """Apply the fixture targeting policy, safely defaulting to a combat-only gate.

        The fallback keeps narrow unit-test doubles and partially constructed agents
        fail-closed: only an explicit combat screen may use entity targets.
        """
        fixture = getattr(self, "fixture", None)
        allows = getattr(fixture, "allows_entity_targets", None)
        if callable(allows):
            return bool(allows(
                metrics=getattr(self, "_obs_metrics", {}),
                host_stats=getattr(self, "_host_stats", {}),
                screen=screen,
            ))
        return str(screen or "").strip().lower() == "combat"

    def _maybe_log_combat_enter(self) -> None:
        if self._combat_enter_logged or not self._combat_screen_now():
            return
        self._combat_enter_logged = True
        _combat_trace("PERCEPTION", "entered combat")

    def _log_combat_perception(
        self,
        scene_desc: Any,
        *,
        gate_reason: str,
        ran_llm1: bool,
        combat_errors: list[str] | None = None,
    ) -> None:
        if not self._combat_screen_now() and str(getattr(scene_desc, "screen", "") or "").strip().lower() != "combat":
            return
        self._maybe_log_combat_enter()
        schema_ok = bool(getattr(scene_desc, "schema_valid", True))
        mode = f"llm1 ({gate_reason})" if ran_llm1 else f"ocr-only ({gate_reason})"
        bboxes: list[str] = []
        for bucket in (
            getattr(scene_desc, "enemies", None) or [],
            getattr(scene_desc, "characters", None) or [],
        ):
            for row in bucket:
                if not isinstance(row, dict):
                    continue
                label = str(row.get("label") or row.get("entity_id") or "?")
                bbox = row.get("bbox")
                if bbox:
                    bboxes.append(f"{label}@{bbox}")
        err_s = f" errors={'; '.join(combat_errors)}" if combat_errors else ""
        _combat_trace(
            "PERCEPTION",
            f"{mode} schema_valid={schema_ok} bboxes=[{', '.join(bboxes[:4])}]{err_s}",
        )

    def _log_entity_merge(self) -> None:
        if not self._combat_screen_now():
            return
        tracker = getattr(self, "_entity_tracker", None)
        if tracker is None or not tracker.entities:
            _combat_trace("ENTITY MERGE", "no tracked entities")
            return
        host_rows = (self._host_stats or {}).get("enemies") or []
        host_bits = [
            f"{r.get('label') or '?'} {r.get('hp')}/{r.get('hp_max')}"
            for r in host_rows
            if isinstance(r, dict)
        ]
        ent_bits = [
            f"{e.entity_id}→{e.display_label()} identified={e.identified}"
            + (f" bbox={e.bbox}" if e.bbox else "")
            for e in tracker.entities
        ]
        _combat_trace(
            "ENTITY MERGE",
            f"host=[{', '.join(host_bits)}] entities=[{'; '.join(ent_bits)}]",
        )

    def _log_combat_decision(self, parsed: dict[str, Any]) -> None:
        if not self._combat_screen_now():
            return
        tool = str(parsed.get("tool") or "").strip()
        if tool:
            _combat_trace("DECISION", f"tool={tool} {parsed.get('region_or_entity') or ''}".strip())
            return
        action = str(parsed.get("action") or "").strip().lower()
        choice = str(parsed.get("choice") or "").strip()
        target = str(parsed.get("target") or "").strip()
        hover = parsed.get("hover") in (True, "true", 1, "1", "yes")
        bits = []
        if action:
            bits.append(f"action={action}")
        if choice:
            bits.append(f"choice={choice}")
        if target:
            bits.append(f"target={target}")
        if hover:
            bits.append("hover=true")
        _combat_trace("DECISION", " ".join(bits) or str(parsed)[:120])

    def _log_combat_target(self, action: ActionLog, *, reason: str = "") -> None:
        if not self._combat_screen_now():
            return
        label = self._aim_label(action)
        mark = action.mark
        tgt = action.target
        _combat_trace(
            "TARGET",
            f"{format_chord(action)} label={label!r} mark={mark} target_mark={tgt}"
            + (f" ({reason})" if reason else ""),
        )

    def _log_combat_action_result(
        self,
        *,
        action: ActionLog,
        metrics: dict[str, Any],
        pixel_changed: bool,
        error: str | None,
        executed: list[str],
    ) -> None:
        if not self._combat_screen_now():
            return
        choice = metrics.get("choice") or self._last_choice_label or ""
        energy = metrics.get("energy")
        outcome = "moved" if pixel_changed else "blocked/no-change"
        if error:
            outcome = f"error:{error}"
        _combat_trace(
            "ACTION RESULT",
            f"sent={'+'.join(executed) if executed else '(none)'} "
            f"choice={choice!r} energy={energy} outcome={outcome}",
        )

    def _log_combat_timing(
        self,
        *,
        perceive_ms: float,
        reason_ms: float,
        think_ms: float,
        action_ms: float,
        discover_ms: float = 0.0,
    ) -> None:
        if not self._combat_screen_now():
            return
        _combat_trace(
            "TIMING",
            f"perceive={perceive_ms:.0f}ms discover={discover_ms:.0f}ms "
            f"reason={reason_ms:.0f}ms think={think_ms:.0f}ms action={action_ms:.0f}ms",
        )

    def _promote_repeat_hover(self, action: ActionLog) -> ActionLog:
        """Hover on a known menu word becomes a click; Unity ignores hover-only."""
        if not action.hover:
            self._last_hover_key = None
            return action
        label = ""
        if action.mark is not None and self._aim is not None:
            hit = self._aim.by_id(int(action.mark))
            if hit is not None:
                label = hit.label or ""
        kind = label_kind(label)
        if kind == "progress":
            action = action.model_copy(update={"hover": False})
            action.name = action.derive_name()
            self._last_hover_key = None
            _say(f"  hover    promoted to click ({label or 'menu'})")
            return action
        if kind == "danger":
            self._last_hover_key = None
            return action
        if action.mark is not None:
            key: object = ("mk", int(action.mark))
        elif action.click_x is not None and action.click_y is not None:
            key = ("pt", round(float(action.click_x), 2), round(float(action.click_y), 2))
        else:
            return action
        if key == self._last_hover_key:
            action = action.model_copy(update={"hover": False})
            action.name = action.derive_name()
            self._last_hover_key = None
            _say("  hover    promoted to click (same control)")
            return action
        self._last_hover_key = key
        return action

    def _aim_label(self, action: ActionLog) -> str:
        if action.mark is None or self._aim is None:
            return ""
        hit = self._aim.by_id(int(action.mark))
        if hit is None:
            return ""
        return str(hit.label or "").strip()

    def _gate_targeting(
        self,
        action: ActionLog,
        *,
        choice: str = "",
        tag: str = "",
    ) -> ActionLog:
        """Validate card+target; auto-recover via targeting_options or block execution."""
        from loop.mcp.tools.targeting import targeting_options

        screen = str(
            self._obs_metrics.get("screen")
            or getattr(self, "_host_stats", {}).get("screen")
            or ""
        ).strip().lower()
        # This is the final pre-dispatch state boundary.  The earlier reasoner
        # validation is useful feedback; this check is the one that guarantees
        # a later steering/fill path cannot resurrect an icon-only card click.
        if (
            getattr(self, "_game_state", None) is not None
            and self._game_state.underlying_activity.value == "combat"
            and not self._state_allows_combat_action(str(choice or ""), "")
        ):
            reason = "state-gate: choice is not a typed current-frame card/control"
            self._pending_exec_error = "invalid-target:state-gate"
            self._step_illegal_blocked = True
            self._last_legal_reason = reason
            self._append_collector_audit({
                "tool": "targeting_validate",
                "status": "rejected",
                "reason": "state-gate",
                "choice": str(choice or ""),
                "frame": {
                    "run_id": str(getattr(self, "session_id", "")),
                    "step_id": int(getattr(self, "_step_id", 0) or 0),
                },
            })
            _say(f"  {tag} target   rejected: {reason}")
            return ActionLog(duration_ms=action.duration_ms or self.config.burst_ms)
        if self._guard_non_combat_enemy_action(action, screen=screen):
            self._pending_exec_error = "non-combat-enemy-target"
            self._step_illegal_blocked = True
            self._last_legal_reason = "enemy target requires combat screen"
            _say(f"  {tag} target   rejected: stale card/target off combat")
            try:
                self._rescan_scene(focus="off-combat-target")
            except Exception:  # noqa: BLE001 — rescan is best-effort
                pass
            return ActionLog(duration_ms=action.duration_ms or self.config.burst_ms)

        ok_target, target_reason = self._tool_orchestrator.validate_targeting(
            self._screen_objects,
            action,
            selected=self._selected,
        )
        if ok_target:
            return self._attach_action_identity(action)

        opts = targeting_options(
            self._screen_objects,
            choice=str(choice or "").strip() or None,
            selected=self._selected,
            entity_targets_ok=self._combat_screen_now(),
        )
        self._last_targeting_opts = opts
        targets = opts.get("targets") or []
        if opts.get("requires_target") and len(targets) == 1:
            t = targets[0]
            tid = int(t["mark"])
            target_obj = self._screen_objects.by_id(tid)
            if target_obj is not None:
                action = action.model_copy(
                    update={
                        "target": tid,
                        "click_x2": target_obj.x,
                        "click_y2": target_obj.y,
                    }
                )
                action.name = action.derive_name()
                ok_target, target_reason = self._tool_orchestrator.validate_targeting(
                    self._screen_objects,
                    action,
                    selected=self._selected,
                )
                if ok_target:
                    return self._attach_action_identity(action)

        if (
            self._selected
            and self._aim is not None
            and not (
                getattr(self, "config", None) is not None
                and self._two_stage()
                and self._combat_screen_now()
            )
        ):
            filled = heuristic_action(
                self._aim,
                metrics=self._obs_metrics,
                selected=self._selected,
                recent=[],
                duration_ms=self.config.burst_ms,
            )
            if filled is not None:
                ok_target, target_reason = self._tool_orchestrator.validate_targeting(
                    self._screen_objects,
                    filled,
                    selected=self._selected,
                )
                if ok_target:
                    return self._attach_action_identity(filled)

        self._pending_exec_error = f"invalid-target:{target_reason}"
        self._step_illegal_blocked = True
        self._last_legal_reason = target_reason
        self._append_collector_audit({
            "tool": "targeting_validate",
            "status": "rejected",
            "reason": str(target_reason),
            "choice": str(choice or ""),
            "frame": {
                "run_id": str(getattr(self, "session_id", "")),
                "step_id": int(getattr(self, "_step_id", 0) or 0),
            },
        })
        _say(f"  {tag} target   invalid: {target_reason}")
        # This is an execution boundary.  Recording an error while returning
        # the rejected chord would still dispatch an icon/card-like click.
        return ActionLog(duration_ms=action.duration_ms or self.config.burst_ms)

    def _supersede_rejected_action_with_inspection(self, action: ActionLog, *, tag: str = "") -> bool:
        """Allow one safe hover to recover an icon-only combat proposal.

        A rejected card/click must never be dispatched.  A later replacement
        hover over an unknown enemy is different: it is read-only perception
        required to turn the unknown object into a current-frame identity.
        Keep the original rejection in structured evidence, but remove its
        execution veto so the hover can actually reach the fixture.
        """
        pending = getattr(self, "_pending_exec_error", None)
        if not pending or not self._is_safe_inspection_hover(action):
            return False
        self._pending_exec_error = None
        self._last_legal_reason = f"{pending}; superseded by safe inspection hover"
        self._append_collector_audit({
            "tool": "targeting_validate",
            "status": "superseded",
            "reason": str(pending),
            "replacement": "inspection-hover",
            "mark": int(action.mark),
            "frame": {
                "run_id": str(getattr(self, "session_id", "")),
                "step_id": int(getattr(self, "_step_id", 0) or 0),
            },
        })
        _say(f"  {tag} target   rejected action superseded by safe hover mk_{action.mark}")
        return True

    def _is_safe_inspection_hover(self, action: ActionLog) -> bool:
        """Whether ``action`` is a read-only hover of a current unknown enemy."""
        if not action.hover or action.mark is None:
            return False
        if action.target is not None or action.click_x2 is not None or action.click_y2 is not None:
            return False
        if not self._combat_screen_now() or self._aim is None:
            return False
        hit = self._aim.by_id(int(action.mark))
        return bool(
            hit is not None
            and in_combat_sprite_band(float(hit.y), float(hit.x))
            and needs_tooltip(str(hit.label or ""))
        )

    def _final_combat_dispatch_gate(
        self,
        action: ActionLog,
        *,
        inspect_requested: bool,
        tool_only: bool = False,
        tool_action_contract: dict[str, Any] | None = None,
        tool_inspection_contract: dict[str, Any] | None = None,
        recheck_only: bool = False,
        tag: str = "",
    ) -> ActionLog:
        """Fail closed after every fill/steer fallback, immediately before dispatch."""
        if not self._two_stage() or not self._combat_screen_now() or is_empty_chord(action):
            return action
        # Successful inspect_object is a hover, never a card click. Force hover
        # if fill/steer stripped it, then allow without targeting_validate.
        if tool_inspection_contract is not None and action.mark is not None:
            try:
                if int(tool_inspection_contract.get("mark_id")) == int(action.mark):
                    if (
                        not action.hover
                        or action.target is not None
                        or action.click_x2 is not None
                        or action.click_y2 is not None
                    ):
                        action = action.model_copy(
                            update={
                                "hover": True,
                                "target": None,
                                "click_x2": None,
                                "click_y2": None,
                            }
                        )
                        action.name = action.derive_name()
                    return action
            except (TypeError, ValueError):
                pass
        if inspect_requested and self._is_safe_inspection_hover(action):
            return action
        if self._is_safe_inspection_hover(action):
            return action

        if getattr(self, "_last_decision_schema", "") == "v2":
            plan = getattr(self, "_last_execution_plan", {})
            plan_action = str(plan.get("action") or "")
            if plan_action == "play_card":
                if self._hand_inventory_mutated_since_plan():
                    self._invalidate_hand_inventory(reason="hand-reflow")
                    reason = "stale-snapshot"
                elif self._is_blocked_source_retry(plan):
                    reason = "stale-source-plan"
                else:
                    verdict = validate_stable_transaction(self._screen_objects, plan)
                    source = plan.get("source") if isinstance(plan, dict) else None
                    expected_id = str((source or {}).get("object_id") or "")
                    resolved = resolve_planned_source(
                        self._screen_objects,
                        card_id=expected_id,
                        snapshot=plan.get("snapshot") if isinstance(plan.get("snapshot"), dict) else None,
                        entity_targets_ok=True,
                    )
                    source_obj = resolved.get("source") if resolved.get("ok") else None
                    mark_matches = bool(
                        source_obj is not None
                        and action.mark is not None
                        and int(action.mark) == int(source_obj.mark_id)
                    )
                    identity_matches = bool(
                        source_obj is not None
                        and expected_id
                        and str(action.source_object_id or source_obj.object_id or "") == expected_id
                    )
                    if verdict.get("ok") and source_obj is not None and (mark_matches or identity_matches):
                        return action
                    reason = str(
                        resolved.get("reason") or verdict.get("reason") or "source-not-found"
                    )
                self._last_execution_plan = {}
                self._planned_hand_signature = None
                self._force_perceive = True
            else:
                # The canonical TurnSnapshot id is the control transaction's
                # freshness token. The object store may be restamped by label
                # transfer during the same turn, so its mutable frame hash is
                # diagnostic only; raw pre-dispatch motion is gated separately.
                frame_matches = (
                    str(plan.get("snapshot_id") or "") == str(self._last_snapshot_id or "")
                )
                source_obj = (
                    self._screen_objects.by_id(int(action.mark))
                    if action.mark is not None else None
                )
                expected_control = str(
                    plan.get("control_id") or plan.get("object_id") or ""
                )
                planned_point = plan.get("point") if isinstance(plan.get("point"), dict) else {}
                point_matches = bool(
                    action.click_x is not None
                    and action.click_y is not None
                    and isinstance(planned_point.get("x"), (int, float))
                    and isinstance(planned_point.get("y"), (int, float))
                    and abs(float(action.click_x) - float(planned_point["x"])) <= 0.005
                    and abs(float(action.click_y) - float(planned_point["y"])) <= 0.005
                )
                source_matches = bool(
                    point_matches
                    and (
                        not expected_control
                        or str(action.source_object_id or "") == expected_control
                        or (
                            source_obj is not None
                            and str(source_obj.object_id or "") == expected_control
                        )
                    )
                )
                reason = "stale-snapshot" if not frame_matches else "source-not-found"
                if frame_matches and source_matches:
                    return action
            self._pending_exec_error = reason
            self._last_legal_reason = reason
            _say(f"  {tag} transaction rejected: {reason}")
            return ActionLog(duration_ms=action.duration_ms or self.config.burst_ms)

        from loop.mcp.tools.targeting import targeting_validate

        def reject(reason: str) -> ActionLog:
            self._pending_exec_error = f"invalid-target:{reason}"
            self._step_illegal_blocked = True
            self._last_legal_reason = reason
            self._append_collector_audit({
                "tool": "targeting_validate",
                "status": "rejected",
                "reason": reason,
                "stage": "final-pre-dispatch",
                "frame": {
                    "run_id": str(getattr(self, "session_id", "")),
                    "step_id": int(getattr(self, "_step_id", 0) or 0),
                },
            })
            _say(f"  {tag} target   final gate rejected: {reason}")
            return ActionLog(duration_ms=action.duration_ms or self.config.burst_ms)

        # A recheck is an observation-only turn.  It must be a clean no-op,
        # rather than a rejected synthetic click: callers use this distinction
        # to request another frame without poisoning the next decision turn.
        if recheck_only:
            return ActionLog(duration_ms=action.duration_ms or self.config.burst_ms)
        # Read/recheck/memory tools inspect state; they do not authorize input.
        # A successful play_card_plan is the narrow exception and must still
        # satisfy the current-frame validation below.
        if tool_only and tool_action_contract is None and tool_inspection_contract is None:
            return reject("tool-only-no-action")
        # An unresolved inspect may be a no-op or a safe hover; it can never
        # degrade into a generic fallback click (especially a hand-band icon).
        if inspect_requested:
            return reject("unresolved-inspect")
        if action.mark is None:
            return reject("untyped-combat-action")
        obj = self._screen_objects.by_id(int(action.mark))
        if obj is None:
            return reject("unknown-current-frame-mark")
        role = object_role(obj, entity_targets_ok=True)
        result = targeting_validate(
            self._screen_objects,
            card_mark=int(action.mark),
            target_mark=int(action.target) if action.target is not None else None,
            entity_targets_ok=True,
            run_id=str(getattr(self, "session_id", "")),
            step_id=int(getattr(self, "_step_id", 0) or 0),
        )
        is_end_turn = "end turn" in " ".join(str(obj.label or "").casefold().split()) and any(
            row.kind == "end_turn" for row in (getattr(self, "_game_state", None).legal_actions if getattr(self, "_game_state", None) is not None else ())
        )
        state = getattr(self, "_game_state", None)
        state_card_ok = True
        state_target_ok = True
        if state is not None and role == "card":
            state_card_ok = any(
                card.playable.value is True and card.bbox.value is not None
                and card.bbox.value[0] - .03 <= obj.x <= card.bbox.value[2] + .03
                and card.bbox.value[1] - .03 <= obj.y <= card.bbox.value[3] + .03
                for card in state.hand
            )
            if action.target is not None:
                target_obj = self._screen_objects.by_id(int(action.target))
                state_target_ok = target_obj is not None and any(
                    enemy.targetable.value is True and enemy.bbox.value is not None
                    and enemy.bbox.value[0] - .04 <= target_obj.x <= enemy.bbox.value[2] + .04
                    and enemy.bbox.value[1] - .04 <= target_obj.y <= enemy.bbox.value[3] + .04
                    for enemy in state.enemies
                )
        contract_matches = True
        if tool_action_contract is not None:
            contract_matches = (
                int(tool_action_contract.get("card_mark", -1)) == int(action.mark)
                and tool_action_contract.get("target_mark") == (
                    int(action.target) if action.target is not None else None
                )
            )
        if role == "card" and bool(result.get("ok")) and contract_matches and state_card_ok and state_target_ok:
            return action
        if (role == "button" or is_end_turn) and action.target is None and (bool(result.get("ok")) or is_end_turn):
            return action
        if tool_action_contract is not None and not contract_matches:
            return reject("tool-plan-action-mismatch")
        return reject(str(result.get("reason") or f"{role}-not-dispatchable"))

    def _validated_tool_card_contract(self) -> dict[str, Any] | None:
        """Return only a successful, current-frame terminal play-card command."""
        for event in reversed(getattr(self, "_last_tool_events", [])):
            if str(event.get("tool") or "") not in {"play_card", "play_card_plan"}:
                continue
            try:
                payload = json.loads(str(event.get("result") or ""))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            transaction = payload.get("transaction") if isinstance(payload, dict) else None
            if not bool(payload.get("ok")) or not isinstance(transaction, dict):
                continue
            frame = transaction.get("frame")
            if not isinstance(frame, dict):
                continue
            try:
                current_frame = (
                    str(frame.get("run_id") or "") == str(getattr(self, "session_id", ""))
                    and int(frame.get("step_id", -1)) == int(getattr(self, "_step_id", 0) or 0)
                )
            except (TypeError, ValueError):
                current_frame = False
            if (
                not current_frame
                or transaction.get("action") != "play_card"
                or transaction.get("card_mark") is None
            ):
                continue
            return transaction
        return None

    def _validated_tool_inspection_contract(self) -> dict[str, Any] | None:
        """Return a successful current-frame inspect request, never a stale mark."""
        for event in reversed(getattr(self, "_last_tool_events", [])):
            if str(event.get("tool") or "") != "inspect_object":
                continue
            try:
                payload = json.loads(str(event.get("result") or ""))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            inspection = payload.get("inspection") if isinstance(payload, dict) else None
            if not bool(payload.get("ok")) or not isinstance(inspection, dict):
                continue
            frame = inspection.get("frame")
            if not isinstance(frame, dict):
                continue
            try:
                current = (
                    str(frame.get("run_id") or "") == str(getattr(self, "session_id", ""))
                    and int(frame.get("step_id", -1)) == int(getattr(self, "_step_id", 0) or 0)
                )
                mark = int(inspection.get("mark_id"))
            except (TypeError, ValueError):
                continue
            if current and self._screen_objects.by_id(mark) is not None:
                return inspection
        return None

    def _combat_resources_unchanged(
        self,
        prev_metrics: dict[str, Any],
        metrics: dict[str, Any],
        prev_host: dict[str, Any] | None,
        host_stats: dict[str, Any] | None,
    ) -> bool:
        pe, ce = prev_metrics.get("energy"), metrics.get("energy")
        if isinstance(pe, (int, float)) and isinstance(ce, (int, float)) and float(ce) < float(pe):
            return False
        prev_player = (prev_host or {}).get("player") if isinstance(prev_host, dict) else {}
        curr_player = (host_stats or {}).get("player") if isinstance(host_stats, dict) else {}
        if isinstance(prev_player, dict) and isinstance(curr_player, dict):
            ph, ch = prev_player.get("hp"), curr_player.get("hp")
            if isinstance(ph, (int, float)) and isinstance(ch, (int, float)) and float(ch) < float(ph):
                return False
        prev_enemies = (prev_host or {}).get("enemies") if isinstance(prev_host, dict) else []
        curr_enemies = (host_stats or {}).get("enemies") if isinstance(host_stats, dict) else []
        if isinstance(prev_enemies, list) and isinstance(curr_enemies, list):
            for prev_row, curr_row in zip(prev_enemies, curr_enemies):
                if not isinstance(prev_row, dict) or not isinstance(curr_row, dict):
                    continue
                ph, ch = prev_row.get("hp"), curr_row.get("hp")
                if isinstance(ph, (int, float)) and isinstance(ch, (int, float)) and float(ch) < float(ph):
                    return False
        return True

    def _alternate_combat_action(self, action: ActionLog) -> ActionLog | None:
        from loop.mcp.tools.targeting import targeting_options

        opts = targeting_options(
            self._screen_objects,
            selected=self._selected,
            entity_targets_ok=self._combat_screen_now(),
        )
        targets = opts.get("targets") or []
        if self._selected and targets:
            for row in targets:
                tid = int(row["mark"])
                if action.mark is not None and tid == int(action.mark):
                    continue
                obj = self._screen_objects.by_id(tid)
                if obj is None:
                    continue
                alt = ActionLog(
                    mark=tid,
                    click_x=obj.x,
                    click_y=obj.y,
                    duration_ms=self.config.burst_ms,
                )
                alt.name = alt.derive_name()
                return alt
        for card in self._screen_objects.hand_cards(
            entity_targets_ok=self._combat_screen_now()
        ):
            if action.mark is not None and card.mark_id == int(action.mark):
                continue
            alt = ActionLog(
                mark=card.mark_id,
                click_x=card.x,
                click_y=card.y,
                duration_ms=self.config.burst_ms,
            )
            alt.name = alt.derive_name()
            return alt
        return None

    def _verify_retry_combat(
        self,
        action: ActionLog,
        *,
        before: Any,
        after: Any,
        prev_metrics: dict[str, Any],
        executed: list[str],
        tag: str,
        max_retries: int = 2,
    ) -> tuple[ActionLog, list[str], Any, float, int]:
        current_action = action
        current_after = after
        current_executed = list(executed)
        retries = 0
        while retries < max_retries and current_executed:
            metrics = self.fixture.scorer(current_after)
            host = self._read_host_stats(current_after)
            pixel_mad = mean_absdiff(roi_thumb(before, 256), roi_thumb(current_after, 256))
            if str(metrics.get("screen") or "") != "combat":
                break
            if pixel_mad >= 0.02:
                break
            if not self._combat_resources_unchanged(
                prev_metrics, metrics, self._prev_host_stats, host
            ):
                break
            retries += 1
            _say(f"  {tag} verify   retry {retries} → {format_chord(current_action)}")
            try:
                if hasattr(self.fixture, "pending_aim_label"):
                    self.fixture.pending_aim_label = self._aim_label(current_action)
                current_executed = self.fixture.execute_chord(current_action)
                current_after = self._confirm_after_action(tag=tag)
            except Exception:  # noqa: BLE001 — verify retry is best-effort
                break
        pixel_mad = mean_absdiff(roi_thumb(before, 256), roi_thumb(current_after, 256))
        return current_action, current_executed, current_after, pixel_mad, retries

    def _clear_stale_target_state(self) -> None:
        """Drop selected-card / cached enemy perception after illegal or stale targeting."""
        self._selected = None
        self._perception_cache.invalidate_enemies()
        self._force_perceive = True

    def _hand_inventory_signature(self) -> tuple[Any, ...]:
        """Current-frame hand epoch: screen, energy, ordinal membership, columns."""
        store = getattr(self, "_screen_objects", None)
        objects = tuple(getattr(store, "objects", ()) or ())
        cards = [
            obj for obj in objects
            if str(getattr(obj, "role", "") or "").strip().lower() == "card"
        ]
        cards = sorted(cards, key=lambda obj: (float(obj.x), str(obj.object_id or "")))
        stats = getattr(self, "_host_stats", {}) or {}
        metrics = getattr(self, "_obs_metrics", {}) or {}
        player = stats.get("player") if isinstance(stats.get("player"), dict) else {}
        energy = stats.get("energy")
        if energy is None:
            energy = player.get("energy")
        if energy is None:
            energy = metrics.get("energy")
        screen = str(stats.get("screen") or metrics.get("screen") or "").strip().lower()
        return (
            screen,
            energy,
            tuple(
                (str(obj.object_id or ""), str(obj.label or ""), round(float(obj.x), 2))
                for obj in cards
            ),
        )

    def _invalidate_hand_inventory(self, *, reason: str) -> dict[str, Any]:
        """Bump the hand epoch and force a fresh perceive + new decision."""
        store = getattr(self, "_screen_objects", None)
        identity: dict[str, Any] = {"invalidation_reason": reason}
        if store is not None:
            identity = invalidate_hand_snapshot(store, reason=reason)
        self._force_perceive = True
        cache = getattr(self, "_perception_cache", None)
        invalidate = getattr(cache, "invalidate_enemies", None)
        if callable(invalidate):
            invalidate()
        return identity

    def _source_plan_fingerprint(self, plan: dict[str, Any] | None, card: AimMark | None = None) -> tuple[Any, ...]:
        payload = plan if isinstance(plan, dict) else {}
        snapshot = payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else {}
        source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
        point = source.get("point") if isinstance(source.get("point"), dict) else {}
        x = point.get("x") if isinstance(point.get("x"), (int, float)) else (card.x if card is not None else None)
        y = point.get("y") if isinstance(point.get("y"), (int, float)) else (card.y if card is not None else None)
        mark_id = source.get("mark_id")
        if mark_id is None and card is not None:
            mark_id = card.id
        return (
            str(snapshot.get("snapshot_id") or ""),
            str(source.get("object_id") or (card.object_id if card is not None else "") or ""),
            int(mark_id) if isinstance(mark_id, (int, float)) else -1,
            round(float(x), 3) if isinstance(x, (int, float)) else None,
            round(float(y), 3) if isinstance(y, (int, float)) else None,
        )

    def _is_blocked_source_retry(self, plan: dict[str, Any] | None, card: AimMark | None = None) -> bool:
        stale = getattr(self, "_stale_source_plan", None)
        if not stale:
            return False
        return self._source_plan_fingerprint(plan, card) == tuple(stale)

    def _hand_inventory_mutated_since_plan(self) -> bool:
        planned = getattr(self, "_planned_hand_signature", None)
        if planned is None:
            return False
        current = self._hand_inventory_signature()
        if current[0] != planned[0]:
            return True
        if current[1] != planned[1]:
            return True
        planned_cards = planned[2] if len(planned) > 2 else ()
        current_cards = current[2]
        if tuple(row[0] for row in current_cards) != tuple(row[0] for row in planned_cards):
            return True
        if tuple(row[1] for row in current_cards) != tuple(row[1] for row in planned_cards):
            return True
        if len(current_cards) != len(planned_cards):
            return True
        for left, right in zip(current_cards, planned_cards):
            if abs(float(left[2]) - float(right[2])) >= 0.04:
                return True
        return False

    def _aim_mark_for_object(self, obj: Any) -> AimMark | None:
        """Map a snapshot-scoped store object onto current aim geometry. No coord fallback."""
        if obj is None:
            return None
        guide = getattr(self, "_aim", None)
        oid = str(getattr(obj, "object_id", "") or "")
        if guide is not None and oid:
            hits = [mark for mark in guide.marks if str(mark.object_id or "") == oid]
            if len(hits) == 1:
                return hits[0]
        mark_id = getattr(obj, "mark_id", None)
        if guide is not None and mark_id is not None:
            hit = guide.by_id(int(mark_id))
            if hit is not None:
                return hit
        return AimMark(
            id=int(getattr(obj, "mark_id", 0) or 0),
            x=float(obj.x),
            y=float(obj.y),
            x0=float(obj.x0),
            y0=float(obj.y0),
            x1=float(obj.x1),
            y1=float(obj.y1),
            label=str(getattr(obj, "label", "") or ""),
            object_id=oid,
            role=str(getattr(obj, "role", "") or "card"),
        )

    def _sync_selected_for_screen(self, screen: str | None) -> None:
        kind = (screen or "").strip().lower()
        if kind and kind != "combat":
            self._selected = None
            if getattr(self, "_planned_hand_signature", None) is not None:
                self._invalidate_hand_inventory(reason="screen-transition")
                self._planned_hand_signature = None

    def _action_requires_enemy(self, action: ActionLog) -> bool:
        if self._selected:
            return True
        if action.target is not None:
            return True
        if action.click_x2 is not None and action.click_y2 is not None:
            return True
        screen = str(
            getattr(self, "_obs_metrics", {}).get("screen")
            or getattr(self, "_host_stats", {}).get("screen")
            or ""
        ).strip().lower()
        # Positional enemy inference is meaningful only in combat.  Menu controls
        # such as character-select "Next" live in the same upper-screen band.
        if screen != "combat":
            return False
        y = action.click_y
        x = action.click_x
        if y is not None and in_combat_sprite_band(y, x):
            return True
        if action.mark is not None and self._aim is not None:
            hit = self._aim.by_id(int(action.mark))
            if hit is not None and in_combat_sprite_band(hit.y, hit.x):
                return True
            if (
                hit is not None
                and screen == "combat"
                and hit.y >= HAND_Y
                and action.target is None
                and action.click_x2 is None
            ):
                return True
        return False

    def _guard_non_combat_enemy_action(self, action: ActionLog, *, screen: str | None) -> bool:
        """Reject enemy-targeting when underlying activity disallows it."""
        if self._allows_entity_targets(screen=screen):
            return False
        if not self._action_requires_enemy(action):
            return False
        self._clear_stale_target_state()
        return True

    def _break_nochange_loop(self, *, tag: str = "", chord: str = "", streak: int = 0) -> None:
        """Invalidate perception and rescan after repeated identical no-op actions."""
        self._clear_stale_target_state()
        self._cached_perceive_scene = None
        self._cached_perceive_scene_step = None
        self._scene_desc = None
        label = chord or "?"
        count = streak or 2
        self._last_outcome = f"identical no-change x{count} ({label}) — rescanning"
        _say(f"  {tag} rescan   broke {count}x repeat loop ({label})")
        try:
            ms = self._rescan_scene(focus="noop-repeat")
            if ms:
                _say(f"  {tag} rescan   {ms:.0f}ms")
        except Exception:  # noqa: BLE001 — rescan is best-effort
            pass

    def _maybe_break_nochange_loop(self, *, tag: str = "") -> None:
        streak, chord = self.working.identical_nochange_streak(min_streak=2)
        if streak >= 2:
            self._break_nochange_loop(tag=tag, chord=chord, streak=streak)

    def _update_selected(
        self,
        action: ActionLog,
        *,
        metrics: dict[str, Any],
        prev_metrics: dict[str, Any],
        pixel_mad: float | None,
        pixel_changed: bool,
        selection_state: str | None = None,
    ) -> None:
        del pixel_changed, pixel_mad
        choice = str(metrics.get("choice") or "").strip()
        label = self._aim_label(action) or choice
        compact = label.lower()
        screen = str(metrics.get("screen") or "").strip().lower()
        prev_screen = str(prev_metrics.get("screen") or "").strip().lower()
        token = (action.name or "").strip().lower()
        if token == "escape" or screen == "pause":
            self._selected = None
            return
        if screen and prev_screen and screen != prev_screen:
            self._selected = None
        if screen != "combat":
            self._selected = None
            return
        if "end turn" in compact:
            self._selected = None
            return
        prev_e, curr_e = prev_metrics.get("energy"), metrics.get("energy")
        if (
            isinstance(prev_e, (int, float))
            and isinstance(curr_e, (int, float))
            and curr_e < prev_e
        ):
            self._selected = None
            return
        if action.hover:
            return
        if action.target is not None:
            self._selected = None
            return
        if self._selected and not action.hover:
            if action.click_y is not None and in_combat_sprite_band(action.click_y, action.click_x):
                self._selected = None
                return
            if action.mark is not None and self._aim is not None:
                hit = self._aim.by_id(int(action.mark))
                if hit is not None and in_combat_sprite_band(hit.y, hit.x):
                    self._selected = None
                    return
        if selection_state == "confirmed" and label and not any(
            word in compact for word in ("continue", "got it", "resume", "confirm", "end turn")
        ):
            self._selected = label or "card"
        elif selection_state in {"unconfirmed", "failed"}:
            self._selected = None

    @staticmethod
    def _mark_iou(left: AimMark, right: AimMark) -> float:
        x0, y0 = max(left.x0, right.x0), max(left.y0, right.y0)
        x1, y1 = min(left.x1, right.x1), min(left.y1, right.y1)
        inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
        union = (left.x1 - left.x0) * (left.y1 - left.y0) + (right.x1 - right.x0) * (
            right.y1 - right.y0
        ) - inter
        return inter / union if union > 0 else 0.0

    @staticmethod
    def _same_mark_label(left: AimMark, right: AimMark) -> bool:
        return bool(left.label and right.label and left.label.strip().casefold() == right.label.strip().casefold())

    @staticmethod
    def _nearest_mark(
        guide: AimGuide | None, x: float | None, y: float | None
    ) -> AimMark | None:
        if guide is None or x is None or y is None:
            return None
        return min(guide.marks, key=lambda mark: (mark.x - x) ** 2 + (mark.y - y) ** 2, default=None)

    def _is_targeted_card_transaction(self, action: ActionLog) -> bool:
        """Route every STS2 hand-card play through the transaction executor.

        A target is deliberately optional here.  Whether one is required is an
        observed post-source-click fact, not a card-name heuristic.
        """
        if self.fixture.id != "sts2" or action.hover:
            return False
        if getattr(self, "_last_decision_schema", "") == "v2":
            return bool(
                str(action.source_role or "").strip().lower() == "card"
                and str(getattr(self, "_last_execution_plan", {}).get("action") or "")
                == "play_card"
            )
        card = self._aim.by_id(int(action.mark)) if self._aim is not None and action.mark is not None else None
        card_y = card.y if card is not None else action.click_y
        typed_card = str(action.source_role or "").strip().lower() == "card"
        return (
            (typed_card or (card_y is not None and card_y >= HAND_Y))
            and action.click_x is not None
            and action.click_y is not None
        )

    @staticmethod
    def _roi_mad(left: Any, right: Any, box: tuple[float, float, float, float]) -> float:
        """Cheap normalized luma difference for one normalized image region."""
        if left is None or right is None:
            return 0.0
        if right.size != left.size:
            right = right.resize(left.size)
        width, height = left.size
        x0 = max(0, min(width - 1, int(round(box[0] * width))))
        y0 = max(0, min(height - 1, int(round(box[1] * height))))
        x1 = max(x0 + 1, min(width, int(round(box[2] * width))))
        y1 = max(y0 + 1, min(height, int(round(box[3] * height))))
        return mean_absdiff(left.crop((x0, y0, x1, y1)), right.crop((x0, y0, x1, y1)))

    def _sample_card_post_click(
        self,
        pre_frame: Any,
        *,
        max_ms: int = 750,
        poll_ms: int = 80,
        min_ms: int = 160,
    ) -> _PostClickProbe:
        """Capture a bounded raw-frame burst; never OCR, score, or ground it."""
        started = time.perf_counter()
        deadline = started + max(1, int(max_ms)) / 1000.0
        frames: list[Any] = []
        deltas: list[float] = []
        previous_thumb = roi_thumb(pre_frame, 64)
        quiet = 0
        while True:
            raw = self.fixture.grab_image()
            fresh = self.fixture.observe_image(raw)
            thumb = roi_thumb(fresh, 64)
            delta = mean_absdiff(previous_thumb, thumb)
            frames.append(fresh)
            deltas.append(delta)
            quiet = quiet + 1 if delta <= 0.012 else 0
            previous_thumb = thumb
            now = time.perf_counter()
            elapsed_ms = (now - started) * 1000.0
            if (
                (len(frames) >= 3 and quiet >= 2 and elapsed_ms >= min_ms)
                or now >= deadline
            ):
                break
            time.sleep(min(max(0.0, poll_ms / 1000.0), max(0.0, deadline - now)))
        return _PostClickProbe(
            pre_frame=pre_frame,
            fresh_frame=frames[-1],
            frames=tuple(frames),
            deltas=tuple(deltas),
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
        )

    def _classify_card_probe(
        self,
        card: AimMark,
        target: AimMark | None,
        probe: _PostClickProbe,
    ) -> tuple[Literal["auto_resolved", "selected", "unconfirmed"], list[str]]:
        """Classify a source click from persistent raw-pixel evidence only."""
        samples = probe.frames[-2:] if len(probe.frames) >= 2 else probe.frames
        if not samples:
            return "unconfirmed", []
        source_box = (
            max(0.0, card.x0 - 0.025), max(0.0, card.y0 - 0.025),
            min(1.0, card.x1 + 0.025), min(1.0, card.y1 + 0.025),
        )
        source_diffs = [self._roi_mad(probe.pre_frame, frame, source_box) for frame in samples]
        hand_diffs = [self._roi_mad(probe.pre_frame, frame, (0.12, 0.70, 0.78, 1.0)) for frame in samples]
        source_changed = all(value >= 0.045 for value in source_diffs)
        hand_changed = all(value >= 0.025 for value in hand_diffs)

        effect_regions: list[tuple[str, tuple[float, float, float, float], float]] = [
            ("energy_or_hud_changed", (0.035, 0.72, 0.13, 0.93), 0.055),
            ("turn_control_changed", (0.82, 0.72, 0.98, 0.92), 0.055),
        ]
        # Enemy sprites, HP bars, and intent glyphs animate continuously. A
        # source click cannot safely claim a targeted effect from that region;
        # energy/turn HUD evidence is the only fail-safe auto-resolution proof
        # before the explicit target click.
        effects: list[str] = []
        for name, box, threshold in effect_regions:
            values = [self._roi_mad(probe.pre_frame, frame, box) for frame in samples]
            if len(values) >= 2 and all(value >= threshold for value in values):
                effects.append(name)
        if source_changed and hand_changed and effects:
            return "auto_resolved", effects
        if source_changed and hand_changed:
            return "selected", [
                f"persistent source movement:{min(source_diffs):.3f}",
                f"persistent hand movement:{min(hand_diffs):.3f}",
            ]
        return "unconfirmed", [
            f"source movement:{min(source_diffs):.3f}",
            f"hand movement:{min(hand_diffs):.3f}",
        ]

    def _card_lifted(self, before: AimMark, fresh: AimGuide | None) -> bool:
        """Selection proof from fresh geometry only: lift, enlargement, or highlight."""
        if fresh is None or not self._combat_screen_now() or not before.label:
            return False
        for candidate in fresh.marks:
            if not self._same_mark_label(before, candidate):
                continue
            same_object = bool(
                before.object_id
                and candidate.object_id
                and before.object_id == candidate.object_id
            )
            if same_object and candidate.y < before.y - 0.05:
                # Selected cards can move to the far-left inspection lane, so
                # horizontal overlap is not required when stable identity and
                # a substantial upward move agree.
                return True
            horizontal_overlap = max(0.0, min(before.x1, candidate.x1) - max(before.x0, candidate.x0))
            if horizontal_overlap < min(before.x1 - before.x0, candidate.x1 - candidate.x0) * 0.25:
                continue
            before_area = max(0.0, before.x1 - before.x0) * max(0.0, before.y1 - before.y0)
            candidate_area = max(0.0, candidate.x1 - candidate.x0) * max(0.0, candidate.y1 - candidate.y0)
            lifted = candidate.y < before.y - 0.015 and candidate.y1 < before.y1 - 0.015
            enlarged = before_area > 0 and candidate_area >= before_area * 1.12
            highlighted = (
                float(candidate.confidence or candidate.score or 0.0)
                >= float(before.confidence or before.score or 0.0) + 0.12
            )
            if lifted or enlarged or highlighted:
                return True
        return False

    def _card_left_hand(self, before: AimMark, fresh: AimGuide | None) -> bool:
        """Prove this specific hand slot disappeared; duplicate card names are safe."""
        if fresh is None or not before.label:
            return False
        for candidate in fresh.marks:
            if not self._same_mark_label(before, candidate):
                continue
            horizontal_overlap = max(0.0, min(before.x1, candidate.x1) - max(before.x0, candidate.x0))
            if horizontal_overlap >= min(before.x1 - before.x0, candidate.x1 - candidate.x0) * 0.35:
                return False
        return True

    def _hand_count_decreased(self, fresh: AimGuide | None) -> bool:
        """Card disappearance evidence that is robust to hand fanning."""
        if fresh is None:
            return False

        def _is_card(mark: AimMark) -> bool:
            return str(mark.role or "").strip().lower() == "card" or (
                mark.y >= HAND_Y and bool(str(mark.label or "").strip())
            )

        before_marks = getattr(self, "_aim_before_transaction", ()) or ()
        before_count = sum(1 for mark in before_marks if _is_card(mark))
        fresh_count = sum(1 for mark in fresh.marks if _is_card(mark))
        return before_count > 0 and fresh_count < before_count

    def _auto_target_effect_verified(self, card: AimMark) -> bool:
        """Recognize an immediate card resolution before requiring a card lift.

        Some STS interactions resolve in the source-click frame (including
        automatic/single targets), so there is no durable selected-card lift.
        These sensor-visible effects are sufficient evidence that a target
        click must *not* follow.
        """
        previous = getattr(self, "_prev_host_stats", {}) or {}
        current = getattr(self, "_host_stats", {}) or {}
        turn_ended = (
            str(previous.get("turn") or "") == "ours"
            and str(current.get("turn") or "") == "theirs"
        )
        # End Turn discards the whole hand. Neither a turn transition nor a
        # disappearing card slot can verify a card when that transition occurred.
        if not turn_ended and self._hand_count_decreased(self._aim):
            return True
        prev_player = previous.get("player") or {}
        curr_player = current.get("player") or {}
        prev_energy = previous.get("energy")
        curr_energy = current.get("energy")
        if prev_energy is None and isinstance(prev_player, dict):
            prev_energy = prev_player.get("energy")
        if curr_energy is None and isinstance(curr_player, dict):
            curr_energy = curr_player.get("energy")
        if not turn_ended and isinstance(prev_energy, (int, float)) and isinstance(curr_energy, (int, float)) and curr_energy < prev_energy:
            return True
        prev_block = prev_player.get("block") if isinstance(prev_player, dict) else None
        curr_block = curr_player.get("block") if isinstance(curr_player, dict) else None
        if not turn_ended and isinstance(prev_block, (int, float)) and isinstance(curr_block, (int, float)) and curr_block > prev_block:
            return True
        prev_enemies = previous.get("enemies") or []
        curr_enemies = current.get("enemies") or []
        for prior, fresh in zip(prev_enemies, curr_enemies):
            if not isinstance(prior, dict) or not isinstance(fresh, dict):
                continue
            old_hp, new_hp = prior.get("hp"), fresh.get("hp")
            if not turn_ended and isinstance(old_hp, (int, float)) and isinstance(new_hp, (int, float)) and new_hp < old_hp:
                return True
        return False

    def _rebind_target(
        self,
        before: AimMark | None,
        *,
        pre_frame: Any | None = None,
        fresh_frame: Any | None = None,
    ) -> AimMark | None:
        """Fresh-frame target binding: stable object_id, then unambiguous bbox/center."""
        if before is None:
            return None
        if pre_frame is not None and fresh_frame is not None:
            # Prefer current-frame CV geometry. The old target is only an
            # identity/search seed and never supplies the returned bounds.
            candidates = [
                mark for mark in detect_marks(fresh_frame)
                if in_combat_sprite_band(mark.y, mark.x)
                and (mark.x1 - mark.x0) * (mark.y1 - mark.y0) >= 0.003
            ]
            old_w = max(0.01, before.x1 - before.x0)
            old_h = max(0.01, before.y1 - before.y0)
            ranked = sorted(
                (
                    (
                        ((mark.x - before.x) ** 2 + (mark.y - before.y) ** 2) ** 0.5
                        + 0.25 * (
                            abs((mark.x1 - mark.x0) - old_w)
                            + abs((mark.y1 - mark.y0) - old_h)
                        ),
                        mark,
                    )
                    for mark in candidates
                    if ((mark.x - before.x) ** 2 + (mark.y - before.y) ** 2) ** 0.5 <= 0.12
                ),
                key=lambda row: row[0],
            )
            if ranked and ranked[0][0] <= 0.12 and (
                len(ranked) == 1 or ranked[1][0] - ranked[0][0] >= 0.015
            ):
                mark = ranked[0][1]
                current_aim = getattr(self, "_aim", None)
                return AimMark(
                    id=max((item.id for item in getattr(current_aim, "marks", ())), default=0) + 1,
                    x=mark.x, y=mark.y, x0=mark.x0, y0=mark.y0, x1=mark.x1, y1=mark.y1,
                    label=before.label, score=mark.score,
                    object_id=before.object_id, role="enemy",
                    source="fresh-cv-rebind", confidence=mark.confidence,
                )
            return self._rebind_target_from_fresh_pixels(
                before, pre_frame=pre_frame, fresh_frame=fresh_frame
            )
        if self._aim is None:
            return None
        oid = str(before.object_id or "").strip()
        if oid and not is_frame_mark_token(oid):
            hits = [mark for mark in self._aim.marks if str(mark.object_id or "") == oid]
            if len(hits) == 1:
                return hits[0]
            # Stable ids may be regenerated by a sensor-only refresh. Fall
            # through to current-frame geometry instead of failing early.
        placeholders = {"", "icon", "unknown", "character", "button"}
        candidates = [
            mark for mark in self._aim.marks
            if in_combat_sprite_band(mark.y, mark.x)
            and " ".join(str(mark.label or "").lower().replace(":", "").split()) not in placeholders
            and not is_player_row({"label": mark.label, "role": mark.role})
        ]
        exact = [mark for mark in candidates if self._same_mark_label(before, mark)]
        if len(exact) == 1:
            return exact[0]
        overlap = [mark for mark in candidates if self._mark_iou(before, mark) >= 0.15]
        if len(overlap) == 1:
            return overlap[0]
        nearby = sorted(
            (
                ((mark.x - before.x) ** 2 + (mark.y - before.y) ** 2, mark)
                for mark in candidates
            ),
            key=lambda row: row[0],
        )
        if not nearby or nearby[0][0] > 0.08 * 0.08:
            return self._rebind_target_from_fresh_pixels(before)
        if len(nearby) > 1 and abs(nearby[1][0] - nearby[0][0]) < 0.01 * 0.01:
            return self._rebind_target_from_fresh_pixels(before)
        return nearby[0][1]

    def _rebind_target_from_fresh_pixels(
        self,
        before: AimMark,
        *,
        pre_frame: Any | None = None,
        fresh_frame: Any | None = None,
    ) -> AimMark | None:
        """Re-detect the same target by local template match on the fresh frame.

        The old box is only a search seed.  The returned click geometry comes
        from the best measured location in the post-source raw frame, so stale
        scene geometry is never copied into the executor unchecked.
        """
        old_image = pre_frame if pre_frame is not None else getattr(self, "_transaction_pre_image", None)
        fresh_image = (
            fresh_frame
            if fresh_frame is not None
            else getattr(getattr(self, "_aim", None), "image", None)
        )
        if old_image is None or fresh_image is None or old_image.size != fresh_image.size:
            return None
        old_gray = np.asarray(old_image.convert("L"), dtype=np.float32)
        fresh_gray = np.asarray(fresh_image.convert("L"), dtype=np.float32)
        height, width = old_gray.shape[:2]
        x0 = max(0, min(width - 2, int(round(before.x0 * width))))
        y0 = max(0, min(height - 2, int(round(before.y0 * height))))
        x1 = max(x0 + 2, min(width, int(round(before.x1 * width))))
        y1 = max(y0 + 2, min(height, int(round(before.y1 * height))))
        template = old_gray[y0:y1, x0:x1]
        # Flat regions are not identity-bearing and can produce an arbitrary
        # "best" location when the background is unchanged.
        if template.size < 64 or float(np.std(template)) < 5.0:
            return None
        radius_x = max(8, int(round(width * 0.04)))
        radius_y = max(8, int(round(height * 0.04)))
        matches: list[tuple[float, int, int]] = []
        for dy in range(-radius_y, radius_y + 1, 4):
            yy0, yy1 = y0 + dy, y1 + dy
            if yy0 < 0 or yy1 > height:
                continue
            for dx in range(-radius_x, radius_x + 1, 4):
                xx0, xx1 = x0 + dx, x1 + dx
                if xx0 < 0 or xx1 > width:
                    continue
                patch = fresh_gray[yy0:yy1, xx0:xx1]
                mad = float(np.mean(np.abs(template - patch)) / 255.0)
                matches.append((mad, dx, dy))
        matches.sort(key=lambda row: row[0])
        best = matches[0] if matches else None
        if best is None or best[0] > 0.20:
            return None
        # Nearby samples are one correlation peak. A second, spatially
        # distinct peak with essentially the same score means identity is
        # ambiguous, so fail closed instead of choosing scan order.
        distinct_radius = max(8.0, min(template.shape[:2]) * 0.5)
        competitor = next(
            (
                row for row in matches[1:]
                if ((row[1] - best[1]) ** 2 + (row[2] - best[2]) ** 2) ** 0.5
                >= distinct_radius
            ),
            None,
        )
        if competitor is not None and competitor[0] <= best[0] + 0.01:
            return None
        _, dx, dy = best
        nx0, ny0, nx1, ny1 = x0 + dx, y0 + dy, x1 + dx, y1 + dy
        current_aim = getattr(self, "_aim", None)
        return AimMark(
            id=max((mark.id for mark in getattr(current_aim, "marks", ())), default=0) + 1,
            x=((nx0 + nx1) / 2) / width,
            y=((ny0 + ny1) / 2) / height,
            x0=nx0 / width, y0=ny0 / height, x1=nx1 / width, y1=ny1 / height,
            label=before.label, score=max(0.0, 1.0 - best[0]),
            object_id=before.object_id, role="enemy",
            source="fresh-template-rebind", confidence=max(0.0, 1.0 - best[0]),
        )

    def _execute_card_transaction(
        self,
        action: ActionLog,
        *,
        prev_metrics: dict[str, Any],
        tag: str,
    ) -> _CardExecution:
        """Dispatch card first, prove selection, rebuild aim, then dispatch fresh target."""
        before_aim = self._aim
        plan = getattr(self, "_last_execution_plan", {}) or {}
        snapshot_plan = plan.get("snapshot") if isinstance(plan, dict) else None
        v2_source = (
            isinstance(plan, dict)
            and str(plan.get("action") or "") == "play_card"
            and isinstance(snapshot_plan, dict)
            and getattr(self, "_screen_objects", None) is not None
        )
        evidence: list[str] = []
        card: AimMark | None = None
        reject_reason: str | None = None
        if v2_source:
            if self._hand_inventory_mutated_since_plan():
                identity = self._invalidate_hand_inventory(reason="hand-reflow")
                reject_reason = "stale-snapshot"
                evidence.append(f"hand-reflow:{identity.get('invalidation_reason') or 'hand-reflow'}")
            elif self._is_blocked_source_retry(plan):
                reject_reason = "stale-source-plan"
                evidence.append("blocked-same-coordinate-retry")
            else:
                source_spec = plan.get("source") if isinstance(plan.get("source"), dict) else {}
                resolved = resolve_planned_source(
                    self._screen_objects,
                    card_id=str(source_spec.get("object_id") or action.source_object_id or ""),
                    snapshot=snapshot_plan,
                    entity_targets_ok=True,
                )
                if not resolved.get("ok"):
                    reject_reason = str(resolved.get("reason") or "source-not-found")
                    evidence.append(f"source-resolve:{reject_reason}")
                    if reject_reason == "stale-snapshot":
                        self._invalidate_hand_inventory(reason="stale-snapshot")
                else:
                    card = self._aim_mark_for_object(resolved.get("source"))
                    if card is None:
                        reject_reason = "source-not-found"
                        evidence.append("snapshot-scoped-object-missing")
        else:
            card = before_aim.by_id(int(action.mark)) if before_aim is not None and action.mark is not None else None
            if card is None:
                card = self._nearest_mark(before_aim, action.click_x, action.click_y)
        target = before_aim.by_id(int(action.target)) if before_aim is not None and action.target is not None else None
        if target is None:
            target = self._nearest_mark(before_aim, action.click_x2, action.click_y2)
        planned_target = plan.get("target_candidate") if isinstance(plan, dict) else None
        planned_target_id = (
            str(planned_target.get("object_id") or "")
            if isinstance(planned_target, dict)
            else (action.target_object_id or "")
        )
        snapshot_id = None
        if isinstance(snapshot_plan, dict) and snapshot_plan.get("snapshot_id"):
            snapshot_id = str(snapshot_plan.get("snapshot_id"))
        else:
            snapshot_id = str(getattr(self, "_last_snapshot_id", "") or "") or None
        transaction = CardTransaction(
            selection_state="failed",
            outcome="dispatch_failed",
            snapshot_id=snapshot_id,
            source_object_id=(card.object_id if card is not None else action.source_object_id),
            target_object_id=(target.object_id if target is not None else action.target_object_id),
            original_card_mark=card.id if card is not None else action.mark,
            original_target_mark=target.id if target is not None else action.target,
            error=reject_reason or ("source-not-found" if card is None else None),
            verification_evidence=list(evidence),
        )
        if reject_reason or card is None:
            transaction.outcome = "stale" if reject_reason in {"stale-snapshot", "stale-source-plan"} else "dispatch_failed"
            transaction.error = reject_reason or "source-not-found"
            if not transaction.verification_evidence:
                transaction.verification_evidence.append(transaction.error)
            self._clear_stale_target_state()
            return _CardExecution(action, [], self.fixture.grab_image(), [], transaction, transaction.error)

        card_action = action.model_copy(
            update={
                "mark": card.id,
                "click_x": card.x,
                "click_y": card.y,
                "target": None,
                "click_x2": None,
                "click_y2": None,
                "source_object_id": card.object_id or action.source_object_id,
                "source_role": card.role or action.source_role or "card",
            }
        )
        card_action.name = card_action.derive_name()
        direct_commit = bool(
            target is None and callable(getattr(self.fixture, "execute_card_drag", None))
        )
        executed: list[str] = []
        primitives: list[PrimitiveLog] = []
        try:
            if hasattr(self.fixture, "pending_aim_label"):
                self.fixture.pending_aim_label = card.label
            if direct_commit:
                sent = self.fixture.execute_card_drag(card_action, settle=False)
            else:
                sent = self.fixture.execute_chord(card_action, settle=False)
            executed.extend(sent)
            primitives.extend(
                PrimitiveLog(phase="card", name=name, x=card_action.click_x, y=card_action.click_y)
                for name in sent
            )
        except Exception as exc:  # dispatch failure: target must never follow
            transaction.error = f"card-dispatch:{exc}"
            transaction.outcome = "dispatch_failed"
            return _CardExecution(action, executed, self.fixture.grab_image(), primitives, transaction, transaction.error)

        self._aim_before_transaction = tuple(before_aim.marks) if before_aim is not None else ()
        self._transaction_pre_image = before_aim.image if before_aim is not None else None
        probe = self._sample_card_post_click(self._transaction_pre_image, max_ms=750)
        transaction.probe_samples += len(probe.frames)
        transaction.probe_elapsed_ms += probe.elapsed_ms
        state, evidence = self._classify_card_probe(card, target, probe)
        transaction.verification_evidence.extend(evidence)
        if state == "auto_resolved":
            transaction.selection_state = "not_applicable"
            transaction.source_verified = True
            transaction.effect_verified = True
            transaction.outcome = "auto_resolved"
            return _CardExecution(action, executed, probe.fresh_frame, primitives, transaction)
        if state != "selected":
            transaction.selection_state = "unconfirmed"
            transaction.outcome = "source_unconfirmed"
            transaction.error = f"source-unconfirmed:{card.object_id or card.label or card.id}"
            identity = self._invalidate_hand_inventory(reason="source-unconfirmed")
            self._stale_source_plan = self._source_plan_fingerprint(plan, card)
            self._planned_hand_signature = None
            transaction.verification_evidence.append("source-snapshot-invalidated")
            transaction.verification_evidence.append("blocked-same-coordinate-retry")
            transaction.verification_evidence.append(
                f"hand-generation:{identity.get('hand_generation')}"
            )
            self._clear_stale_target_state()
            return _CardExecution(
                action, executed, probe.fresh_frame, primitives, transaction, transaction.error
            )

        transaction.selection_state = "confirmed"
        transaction.source_verified = True
        transaction.verification_evidence.append("card selected in fresh frame")
        self._selected = card.label or card.object_id or "card"
        if target is None and not planned_target_id:
            if direct_commit:
                # A targetless typed intent is committed with one upward drag.
                # Rich post-transaction reconciliation owns final effect proof.
                transaction.selection_state = "not_applicable"
                transaction.outcome = "effect_unverified"
                transaction.verification_evidence.append("targetless commit gesture")
                self._selected = None
                return _CardExecution(action, executed, probe.fresh_frame, primitives, transaction)
            transaction.outcome = "target_required"
            transaction.error = "target-required"
            return _CardExecution(
                action, executed, probe.fresh_frame, primitives, transaction, transaction.error
            )
        refreshed_inventory = False
        if getattr(getattr(self, "config", None), "architecture", "") == "two_stage":
            try:
                self._refresh_obs_after_action(probe.fresh_frame, prev_metrics=prev_metrics)
                refreshed_inventory = True
                transaction.verification_evidence.append("post-selection-inventory")
            except Exception:
                refreshed_inventory = False
        rebound = None
        if refreshed_inventory and planned_target_id:
            store = getattr(self, "_screen_objects", None)
            fresh_obj = (
                resolve_planned_target(store, planned_target_id, entity_targets_ok=True)
                if store is not None else None
            )
            if fresh_obj is not None:
                rebound = self._aim_mark_for_object(fresh_obj)
                if rebound is not None:
                    transaction.verification_evidence.append(
                        f"post-selection-target:{fresh_obj.object_id}"
                    )
        if rebound is None and not (refreshed_inventory and planned_target_id):
            rebound = self._rebind_target(
                target,
                pre_frame=probe.pre_frame,
                fresh_frame=probe.fresh_frame,
            )
        if rebound is None:
            missing = planned_target_id or (
                target.object_id or target.label or target.id if target is not None else "target"
            )
            transaction.outcome = "target_not_found"
            transaction.error = f"target-not-found:{missing}"
            transaction.verification_evidence.append("missing-stable-target")
            return _CardExecution(
                action, executed, probe.fresh_frame, primitives, transaction, transaction.error
            )
        transaction.target_rebound = True
        transaction.rebound_target_mark = rebound.id
        transaction.verification_evidence.append(
            f"fresh target rebound:{rebound.source or 'typed-frame'}"
        )
        rebound_action = action.model_copy(
            update={"target": rebound.id, "click_x2": rebound.x, "click_y2": rebound.y}
        )
        rebound_action.name = rebound_action.derive_name()
        target_action = ActionLog(mark=rebound.id, click_x=rebound.x, click_y=rebound.y, duration_ms=action.duration_ms)
        target_action.name = target_action.derive_name()
        try:
            if hasattr(self.fixture, "pending_aim_label"):
                self.fixture.pending_aim_label = rebound.label
            sent = self.fixture.execute_chord(target_action, settle=False)
            executed.extend(sent)
            primitives.extend(
                PrimitiveLog(phase="target", name=name, x=target_action.click_x, y=target_action.click_y)
                for name in sent
            )
            transaction.target_dispatched = bool(sent)
        except Exception as exc:  # card click remains logged; no hidden retry
            transaction.outcome = "dispatch_failed"
            transaction.error = f"target-dispatch:{exc}"
            return _CardExecution(
                rebound_action, executed, probe.fresh_frame, primitives, transaction, transaction.error
            )
        target_probe = self._sample_card_post_click(
            probe.fresh_frame, max_ms=750, min_ms=500
        )
        transaction.probe_samples += len(target_probe.frames)
        transaction.probe_elapsed_ms += target_probe.elapsed_ms
        self._selected = None
        transaction.outcome = "effect_unverified"
        transaction.error = None
        return _CardExecution(
            rebound_action, executed, target_probe.fresh_frame, primitives, transaction
        )

    def _retrieved_facts(
        self,
        *,
        screen: str | None,
        snippets: list[str] | None = None,
    ) -> list[str]:
        lx = ly = None
        if self._last_click is not None:
            lx, ly = self._last_click
        hits = self.journal.recall(click_query(screen=screen, x=lx, y=ly), k=5)
        merged: list[str] = []
        seen: set[str] = set()
        for line in list(hits) + list(self._carry_recall or []) + list(snippets or []):
            text = str(line or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            merged.append(text)
            if len(merged) >= 8:
                break
        return merged

    def _live(self, **fields: Any) -> None:
        try:
            publish(
                job="collect",
                fixture=self.fixture.id,
                arm=self.config.arm,
                session_id=self.session_id,
                lives=self.config.lives,
                max_steps=self.config.max_steps,
                run_dir=str(self.store.run_dir),
                episode=self._episode_n,
                policy_id=self.config.policy_id,
                perceive_model=self.config.perceive_model or self.config.policy_id,
                **fields,
            )
        except OSError:
            return

    def _turn_based(self) -> bool:
        return getattr(self.fixture, "pace", "realtime") == "turn"

    def _two_stage(self) -> bool:
        return self._turn_based() and self.config.architecture == "two_stage"

    def _identify_before_act(self) -> bool:
        cfg = getattr(self, "config", None)
        return True if cfg is None else bool(getattr(cfg, "identify_before_act", True))

    def _entity_rows_for_route(self, scene_desc: Any) -> list[dict[str, Any]]:
        return [
            *list(getattr(scene_desc, "controls", []) or []),
            *list(getattr(scene_desc, "hand_cards", []) or []),
            *list(getattr(scene_desc, "enemies", []) or []),
            *list(getattr(scene_desc, "characters", []) or []),
        ]

    def _capture_perception_audit(self, scene_desc: Any) -> None:
        raw_text = str(getattr(scene_desc, "llm1_raw_text", "") or "")
        if raw_text:
            self._last_perceive_raw = raw_text
        raw_obj = getattr(scene_desc, "llm1_raw", None)
        if isinstance(raw_obj, dict) and raw_obj:
            self._last_llm1_raw = json.dumps(raw_obj, ensure_ascii=False, separators=(",", ":"))
        else:
            self._last_llm1_raw = str(getattr(self, "_last_perceive_raw", "") or "")
        validated = getattr(scene_desc, "validated_perception", None)
        self._last_validated_perception = (
            json.dumps(validated, ensure_ascii=False, separators=(",", ":"))
            if isinstance(validated, dict) and validated
            else ""
        )
        grounded = getattr(scene_desc, "raw", None)
        self._last_grounded_scene = (
            json.dumps(grounded, ensure_ascii=False, separators=(",", ":"))
            if isinstance(grounded, dict) and grounded
            else ""
        )
        self._last_llm1_attempts = list(getattr(scene_desc, "llm1_attempts", []) or [])
        self._last_llm1_first_pass_valid = bool(getattr(scene_desc, "llm1_first_pass_valid", False))
        self._last_llm1_final_schema_valid = bool(getattr(scene_desc, "llm1_final_schema_valid", False))
        self._last_llm1_schema_attempts = int(getattr(scene_desc, "llm1_schema_attempts", 0) or 0)

    def _append_collector_audit(self, event: dict[str, Any]) -> None:
        payload = dict(event)
        payload.setdefault("kind", "collector_audit")
        self._last_tool_events = list(getattr(self, "_last_tool_events", [])) + [payload]

    def _reset_turn_telemetry(self) -> None:
        self._turn_perception_source = "other"
        self._turn_llm1_called = False
        self._turn_llm1_reused_from_step = None
        self._turn_reasoner_called = False

    def _record_perception_telemetry(
        self,
        *,
        ran_llm1: bool,
        gate_reason: str,
        forced: bool,
    ) -> None:
        if forced and ran_llm1:
            source: PerceptionSource = "forced_rescan"
        elif ran_llm1:
            source = "llm1"
        elif self._cached_perceive_scene is not None and gate_reason == "combat-stable":
            source = "scene_cache"
        elif str(gate_reason or "").startswith("ocr-only"):
            source = "ocr_cache"
        else:
            source = "other"
        self._turn_perception_source = source
        self._turn_llm1_called = bool(ran_llm1)
        if ran_llm1:
            self._turn_llm1_reused_from_step = None
        elif source == "scene_cache":
            self._turn_llm1_reused_from_step = self._cached_perceive_scene_step
        else:
            self._turn_llm1_reused_from_step = None

    def _attach_action_identity(self, action: ActionLog) -> ActionLog:
        from loop.mcp.tools.targeting import object_role

        store = getattr(self, "_screen_objects", None)
        if store is None:
            return action
        ok = self._combat_screen_now() if hasattr(self, "_combat_screen_now") else False
        src = store.by_id(int(action.mark)) if action.mark is not None else None
        tgt = store.by_id(int(action.target)) if action.target is not None else None
        updates: dict[str, Any] = {}
        if src is not None:
            updates["source_role"] = object_role(src, entity_targets_ok=ok)
            if src.object_id:
                updates["source_object_id"] = src.object_id
        if tgt is not None:
            updates["target_role"] = object_role(tgt, entity_targets_ok=ok)
            if tgt.object_id:
                updates["target_object_id"] = tgt.object_id
        if not updates:
            return action
        out = action.model_copy(update=updates)
        out.name = out.derive_name()
        return out

    def _identity_swap(self, before: ActionLog, after: ActionLog) -> bool:
        if bool(before.hover) != bool(after.hover):
            return True
        for old, new in (
            (before.source_object_id, after.source_object_id),
            (before.target_object_id, after.target_object_id),
            (before.source_role, after.source_role),
            (before.target_role, after.target_role),
        ):
            if old and new and old != new:
                return True
        return False

    def _preserve_steered_identity(self, before: ActionLog, after: ActionLog) -> ActionLog:
        before_id = self._attach_action_identity(before)
        after_id = self._attach_action_identity(after)
        if self._identity_swap(before_id, after_id):
            return before_id
        return after_id

    def _stamp_reason_control_ids(self, controls: list[dict[str, Any]]) -> list[dict[str, Any]]:
        from loop.mcp.tools.targeting import bind_snapshot_target_ids, object_role

        store = getattr(self, "_screen_objects", None)
        if store is None:
            return controls
        ok = self._combat_screen_now() if hasattr(self, "_combat_screen_now") else False
        bind_snapshot_target_ids(store, controls, entity_targets_ok=ok)
        objs = list(getattr(store, "objects", []) or [])
        used: set[str] = set()
        out: list[dict[str, Any]] = []
        for row in controls:
            row = dict(row)
            label = str(row.get("label") or "").strip()
            if is_frame_mark_token(label):
                continue
            eid = str(row.get("entity_id") or "").strip()
            if eid and is_frame_mark_token(eid):
                eid = ""
                row.pop("entity_id", None)
            if not eid:
                role = str(row.get("role") or "")
                compact = " ".join(label.lower().split())
                matches = [
                    obj for obj in objs
                    if obj.object_id
                    and obj.object_id not in used
                    and " ".join(str(obj.label or "").lower().split()) == compact
                    and (
                        (role == "card" and object_role(obj, entity_targets_ok=True) == "card")
                        or (role == "enemy" and object_role(obj, entity_targets_ok=True) == "enemy")
                        or role not in {"card", "enemy"}
                    )
                ]
                if matches:
                    eid = matches[0].object_id
            if eid:
                row["entity_id"] = eid
                used.add(eid)
            out.append(row)
        ok = self._combat_screen_now() if hasattr(self, "_combat_screen_now") else False
        bind_snapshot_target_ids(store, out, entity_targets_ok=ok)
        for obj in objs:
            if not obj.object_id or obj.object_id in used:
                continue
            role = object_role(obj, entity_targets_ok=ok)
            if role == "card":
                out.append({
                    "label": obj.label,
                    "role": "card",
                    "confidence": "high",
                    "entity_id": obj.object_id,
                })
                used.add(obj.object_id)
            elif role == "enemy" and float(obj.y) >= 0.50:
                label = obj.label if obj.label and not is_anonymous_enemy_label(obj.label) else "unidentified_enemy"
                out.append({
                    "label": label,
                    "role": "enemy",
                    "confidence": "high",
                    "entity_id": obj.object_id,
                    "inspectable": is_anonymous_enemy_label(label) or not obj.label,
                })
                used.add(obj.object_id)
        filtered: list[dict[str, Any]] = []
        for row in out:
            if str(row.get("role") or "") != "enemy":
                filtered.append(row)
                continue
            eid = str(row.get("entity_id") or "").strip()
            obj = store.object_for_instance_id(eid) if eid else None
            if obj is None or object_role(obj, entity_targets_ok=ok) != "enemy":
                continue
            if float(obj.y) < 0.50:
                continue
            filtered.append(row)
        return filtered

    def _current_frame_object(self, object_id: str):
        store = getattr(self, "_screen_objects", None)
        if store is None or not object_id:
            return None
        if str(getattr(store, "_run_id", "")) != str(getattr(self, "session_id", "")):
            return None
        if int(getattr(store, "_step_id", -1) or -1) != int(getattr(self, "_step_id", 0) or 0):
            return None
        hit = store.by_object_id(object_id)
        if hit is not None:
            return hit
        hit = store.object_for_instance_id(object_id)
        if hit is not None:
            return hit
        return self._resolve_state_id_to_store_object(str(object_id).strip())

    def _resolve_state_id_to_store_object(self, object_id: str):
        """Map game-state enemy_1_1 / card ids onto this frame's sprite-band object."""
        from loop.mcp.tools.targeting import object_role

        store = getattr(self, "_screen_objects", None)
        state = getattr(self, "_game_state", None)
        if store is None or state is None or not object_id:
            return None
        box = None
        want_role = ""
        for enemy in getattr(state, "enemies", ()) or ():
            if str(getattr(enemy, "id", "") or "") == object_id:
                raw = getattr(getattr(enemy, "bbox", None), "value", None)
                if raw is not None:
                    box = raw
                    want_role = "enemy"
                break
        if box is None:
            for card in getattr(state, "hand", ()) or ():
                if str(getattr(card, "id", "") or "") == object_id:
                    raw = getattr(getattr(card, "bbox", None), "value", None)
                    if raw is not None:
                        box = raw
                        want_role = "card"
                    break
        if box is None or len(box) < 4:
            return None
        cx, cy = (float(box[0]) + float(box[2])) / 2.0, (float(box[1]) + float(box[3])) / 2.0
        candidates = []
        for obj in getattr(store, "objects", ()) or ():
            role = object_role(obj, entity_targets_ok=True)
            if role != want_role:
                continue
            if want_role == "enemy" and float(obj.y) < 0.50:
                continue
            candidates.append(obj)
        if not candidates:
            return None
        return min(candidates, key=lambda obj: (float(obj.x) - cx) ** 2 + (float(obj.y) - cy) ** 2)

    def _skip_labeled_combat_fill(self, action: Any) -> bool:
        """Empty two-stage combat chords must not labeled-click a hand card."""
        return bool(
            self._two_stage()
            and self._combat_screen_now()
            and is_empty_chord(action)
        )

    def _allow_legacy_empty_fill(self) -> bool:
        """Legacy aim/chord synthesis is never an execution input for v2."""
        return self._last_decision_schema != "v2"

    _DISCOVERY_ATTEMPT_LIMIT = 2

    def _identity_unresolved_keys(self) -> set[str]:
        attempts = getattr(self, "_discovery_attempts", None) or {}
        return {
            str(key).strip().casefold()
            for key, count in attempts.items()
            if int(count or 0) >= self._DISCOVERY_ATTEMPT_LIMIT and str(key).strip()
        }

    def _two_stage_epistemic_blockers(self, scene_desc: Any) -> list[str]:
        """Return unresolved objects grounded to an action-bearing role."""
        facts = entity_route_facts(
            self._entity_rows_for_route(scene_desc),
            identify_before_act=self._identify_before_act(),
            identity_unresolved=self._identity_unresolved_keys(),
        )
        self._entity_route_facts = facts
        return [str(row.get("label") or "") for row in facts if row.get("blocker")]

    def _two_stage_reason_route(self, scene_desc: Any) -> tuple[str, str]:
        """Choose Explore or Act from action-relevant uncertainty only."""
        blockers = self._two_stage_epistemic_blockers(scene_desc)
        facts = getattr(self, "_entity_route_facts", [])
        if blockers:
            return "explore", "epistemic-blocker:" + blockers[0]
        if any(row.get("rule") == "identity-unresolved" for row in facts):
            return "act", "identity-unresolved-fallback"
        return "act", "confirmed-action-surface"

    @staticmethod
    def _two_stage_reason_system(route: str, fallback: str) -> str:
        """Use split prompts when available, with a safe legacy fallback."""
        name = "REASON_EXPLORE_SYSTEM" if route == "explore" else "REASON_ACT_SYSTEM"
        candidate = getattr(_prompt_defs, name, None)
        return str(candidate or fallback)

    @staticmethod
    def _perception_schema_version() -> str:
        return str(getattr(_prompt_defs, "PERCEPTION_SCHEMA_VERSION", "legacy"))

    def _perceive_gw(self) -> Any:
        """LLM1 gateway, defaulting to cloud Gemma; falls back to the reasoner."""
        return self.perceive_gateway if self.perceive_gateway is not None else self.gateway

    def _prompt_id_for_step(self) -> str:
        legacy = prompt_id(
            arm=self.config.arm,
            pace=getattr(self.fixture, "pace", "realtime"),
            architecture=self.config.architecture if self._turn_based() else "overlay",
        )
        if self.fixture.id == "sts2" and self._two_stage():
            return ACT_PROMPT_ID
        return legacy

    def _death_lesson(self, steps: list[StepLog]) -> str:
        if not steps:
            return ""
        tail = steps[-8:]
        hp_trend: list[float] = []
        for row in tail:
            hp = (row.metrics or {}).get("hp")
            if isinstance(hp, (int, float)):
                hp_trend.append(float(hp))
        floor: int | None = None
        for row in reversed(tail):
            raw = (row.metrics or {}).get("floor")
            if isinstance(raw, (int, float)):
                floor = int(raw)
                break
        parts: list[str] = []
        if floor is not None:
            parts.append(f"died on floor {floor}")
        if len(hp_trend) >= 2 and hp_trend[-1] <= 0 and hp_trend[0] > hp_trend[-1]:
            parts.append("HP fell to zero — block or heal earlier")
        rewards = [float(row.score_delta or 0) for row in tail if row.score_delta is not None]
        if rewards and sum(rewards) < 0:
            parts.append("last turns were low-reward")
        return "; ".join(parts) if parts else "died without progress — reassess defense"

    def _register_turn_tools(
        self,
        *,
        listing: str | None,
        host_screen: str | None,
    ) -> None:
        self._tool_registry = ToolRegistry()

        def _recheck(region_or_entity: str = "", **_: Any) -> str:
            import json

            focus = str(region_or_entity or "").strip()
            image = self._last_obs_image
            already = int(getattr(self, "_step_recheck_count", 0) or 0)
            last_img = getattr(self, "_step_recheck_image", None)
            if already >= 1 or (last_img is not None and image is not None and last_img is image):
                scene = self._scene_desc
                return json.dumps({
                    "ok": False,
                    "skipped": True,
                    "reason": "identical-frame",
                    "scene": scene.as_text() if scene is not None else "",
                    "legal_actions": getattr(scene, "legal_actions", []) if scene else [],
                    "unresolved": getattr(scene, "unresolved", []) if scene else [],
                    "latency_ms": 0,
                })
            self._step_recheck_count = already + 1
            self._step_recheck_image = image
            ms = self._rescan_scene(focus=focus)
            scene = self._scene_desc
            payload = {
                "ok": True,
                "scene": scene.as_text() if scene is not None else "",
                "legal_actions": getattr(scene, "legal_actions", []) if scene else [],
                "unresolved": getattr(scene, "unresolved", []) if scene else [],
                "latency_ms": ms,
            }
            return json.dumps(payload)

        register_reasoner_tools(
            self._tool_registry,
            journal=self.journal,
            notebook=self.notebook,
            screen=host_screen,
            aim_listing=listing,
            selected=self._selected,
            energy=self._obs_metrics.get("energy"),
            recheck_handler=_recheck if self._two_stage() else None,
        )
        register_targeting_tools(
            self._tool_registry,
            self._screen_objects,
            selected_getter=lambda: self._selected,
            entity_targets_ok_getter=lambda: bool(
                self._game_state is not None
                and self._game_state.underlying_activity.value == "combat"
                and not self._game_state.overlays
            ),
        )
        self._tool_orchestrator = ToolOrchestrator(
            self._tool_registry,
            max_rounds=self.config.max_tool_rounds,
            budget_s=self.config.tool_budget_s,
        )

    def _effective_legal_actions(self) -> list[dict[str, Any]]:
        return effective_legal_actions(
            self._legal_actions,
            getattr(self, "_affordance_verdicts", None) or {},
        )

    def _sync_legal_actions(self) -> None:
        self._legal_actions = self._effective_legal_actions()

    def _update_affordance_from_frames(self, pre: Any, post: Any) -> None:
        """Refresh CV verdicts after a state-changing sub-action (card click, hover, target)."""
        if self._aim is None or not self._aim.marks:
            return
        pre_view = self.fixture.observe_image(pre)
        post_view = self.fixture.observe_image(post)
        verdicts = detect_affordance_diff(pre_view, post_view, self._aim.marks)
        if not verdicts:
            return
        self._affordance_verdicts = verdicts
        self._legal_actions = merge_legal_actions(
            list(getattr(self._scene_desc, "legal_actions", []) or [])
            if self._scene_desc is not None
            else None,
            verdicts,
        )

    def _detector_candidates(self) -> list[dict[str, str]]:
        if self._aim is None:
            return []
        return detector_candidates_from_marks(self._aim.marks)

    def _detector_label_strings(self) -> list[str]:
        return [row["label"] for row in self._detector_candidates() if row.get("label")]

    def _detector_typed_rows(self) -> list[dict[str, Any]]:
        if self._aim is None:
            return []
        return detector_typed_rows_from_marks(self._aim.marks)

    def _current_surface_screen(self) -> str:
        return str(
            self._host_stats.get("screen")
            or self._obs_metrics.get("screen")
            or getattr(getattr(self, "_scene_desc", None), "screen", None)
            or ""
        ).strip().lower()

    def _surface_membership_signature(self, controls: Sequence[dict[str, Any]]) -> tuple[str, ...]:
        screen = self._current_surface_screen()
        keys: list[str] = []
        for row in controls:
            role = str(row.get("role") or "").strip().lower()
            if role in GENERIC_CONTROL_ROLES or role in {"card", "enemy", "player", "target"}:
                keys.append(control_key_from_row(row, screen=screen))
        return tuple(sorted(keys))

    def _apply_control_suppression(self, controls: list[dict[str, Any]]) -> list[dict[str, Any]]:
        suppression = getattr(self, "_control_suppression", None)
        if suppression is None:
            suppression = ControlSuppressionState()
            self._control_suppression = suppression
        screen = self._current_surface_screen()
        fingerprint = surface_fingerprint(screen=screen, rows=controls)
        prev_screen = getattr(self, "_last_surface_screen", "")
        prev_membership = getattr(self, "_last_surface_membership", ())
        current_membership = self._surface_membership_signature(controls)
        membership_changed = current_membership != prev_membership
        screen_changed = bool(screen and prev_screen and screen != prev_screen)
        bounds_moved = (
            fingerprint != suppression.fingerprint
            and not membership_changed
            and not screen_changed
        )
        if screen_changed or membership_changed or bounds_moved or not suppression.fingerprint:
            suppression.reset(fingerprint)
        self._last_surface_screen = screen
        self._last_surface_membership = current_membership
        self._prev_surface_controls = [dict(row) for row in controls]
        return [
            row
            for row in controls
            if not suppression.is_suppressed(control_key_from_row(row, screen=screen))
        ]

    def _record_blocked_control_attempt(self, choice: str) -> None:
        suppression = getattr(self, "_control_suppression", None)
        if suppression is None:
            suppression = ControlSuppressionState()
            self._control_suppression = suppression
        label = str(choice or "").strip()
        if not label:
            return
        screen = self._current_surface_screen()
        controls = list(getattr(self, "_prev_surface_controls", None) or getattr(self, "_reason_controls", None) or [])
        fingerprint = surface_fingerprint(screen=screen, rows=controls)
        compact = " ".join(label.lower().split())
        row: dict[str, Any] | None = None
        for candidate in controls:
            if " ".join(str(candidate.get("label") or "").lower().split()) == compact:
                row = candidate
                break
        if row is None:
            row = {"label": label, "role": "button"}
        key = control_key_from_row(row, screen=screen)
        suppression.record_no_change(fingerprint, key)

    def _finalize_reason_controls(self, controls: list[dict[str, Any]]) -> list[dict[str, Any]]:
        filtered = self._apply_control_suppression(controls)
        return self._stamp_reason_control_ids(filtered)

    def _scene_perceive_user(self, *, focus: str = "") -> str:
        overlay = getattr(self, "_overlay_state", None)
        suppress = None
        if overlay is not None:
            step = int(getattr(self, "_step_id", 0) or 0)
            suppress = lambda text, o=overlay, s=step: o.should_suppress_ocr(  # noqa: E731
                text=text, current_step=s
            )
        return scene_perceive_user(
            hud=self._obs_metrics,
            host_stats=self._host_stats,
            detector_candidates=self._detector_candidates(),
            focus=focus,
            ocr_suppress=suppress,
        )

    def _number_aim_marks(
        self,
        marks: list[Any],
        *,
        screen: str | None,
        ocr_words: list[Any] | None = None,
    ) -> list[Any]:
        return number_spatial_nodes(
            marks,
            screen=screen,
            ocr_words=ocr_words,
        )

    def _perception_reuse(self, view: Any, *, aim_screen: str | None) -> PerceptionReuse:
        screen = str(
            aim_screen
            or self._host_stats.get("screen")
            or self._obs_metrics.get("screen")
            or ""
        )
        return self._perception_cache.evaluate(
            view=view,
            screen=screen,
            host_stats=self._host_stats,
            prev_host_stats=self._prev_host_stats,
            entity_tracker=self._entity_tracker,
            force=self._force_perceive,
            inspect=self._force_inspect,
        )

    def _build_turn_marks(
        self,
        view: Any,
        *,
        aim_screen: str | None,
        reuse: PerceptionReuse,
    ) -> tuple[list[Any], list[Any], list[Any], float]:
        """OCR + aim marks for a turn observation; respects perception cache."""
        from loop.os.ocr import set_ocr_context

        screen_key = str(
            aim_screen
            or self._host_stats.get("screen")
            or self._obs_metrics.get("screen")
            or ""
        ).strip().lower()
        set_ocr_context(
            screen=screen_key,
            uncertain=ocr_uncertain_for_reuse(reuse),
        )
        t_ocr = time.perf_counter()
        try:
            if reuse.reuse_ocr and self._perception_cache.last_ocr:
                words = list(self._perception_cache.last_ocr)
            else:
                words = ocr_click_marks(view)
            ocr_ms = _ms(t_ocr)
            if reuse.reuse_ocr or reuse.reuse_enemies:
                _say(f"  ocr      cache ({reuse.reason})")
            t_gui = time.perf_counter()
            gui_marks = gui_click_marks(view)
            gui_ms = _ms(t_gui)
            marks: list[Any] = []
            if self._two_stage():
                marks = list(
                    build_marks(
                        view,
                        lexicon=self.journal.lexicon,
                        screen=aim_screen,
                        words=words,
                        gui_marks=gui_marks,
                    )
                )
                marks = self._number_aim_marks(
                    marks,
                    screen=aim_screen,
                    ocr_words=words,
                )
                if reuse.reuse_enemies and self._perception_cache.last_enemy_marks:
                    marks = merge_cached_enemy_marks(
                        marks,
                        self._perception_cache.last_enemy_marks,
                    )
                else:
                    marks = seed_enemies_from_host(
                        marks,
                        host_stats=self._host_stats,
                        image=view,
                        lexicon=self.journal.lexicon,
                        screen=aim_screen,
                    )
        finally:
            set_ocr_context(screen="", uncertain=False)
        entity_rows = [
            ent.to_row()
            for ent in getattr(self._entity_tracker, "entities", []) or []
        ]
        screen_key = screen_from_labels(
            (m.label for m in marks) if marks else (w.label for w in words),
            fallback=aim_screen,
        )
        self._perception_cache.update(
            view=view,
            screen=screen_key,
            ocr_words=words,
            marks=marks if marks else words,
            entity_rows=entity_rows,
            host_stats=self._host_stats,
        )
        self._force_inspect = False
        return list(words), marks, list(gui_marks), ocr_ms + gui_ms

    def _rescan_scene(self, *, focus: str = "") -> float:
        """Harness-internal LLM1 rescan when validate_choice rejects choice/target."""
        from loop.agent.prompts import two_stage_system_for
        from loop.os.capture import encode_image

        self._perception_cache.invalidate_enemies()
        image = self._last_obs_image
        perceive_gw = self._perceive_gw()
        if image is None or perceive_gw is None:
            return 0.0
        frame = encode_image(image, longest_side=1280, jpeg_quality=90)
        perceive_system, _ = two_stage_system_for(arm=self.config.arm)
        detector_labels = self._detector_label_strings()
        desc, call = perceive_scene(
            perceive_gw,
            frame,
            hud=self._obs_metrics,
            host_stats=self._host_stats,
            detector_labels=detector_labels,
            max_tokens=self.config.perceive_max_tokens,
            perceive_system=perceive_system,
            perceive_user=self._scene_perceive_user(focus=focus),
            cv_verdicts=getattr(self, "_affordance_verdicts", None) or None,
        )
        self._capture_perception_audit(desc)
        reason_controls = self._apply_perceive_scene(desc)
        if str(desc.screen or "").strip().lower() == "combat":
            self._cached_perceive_scene = desc
            self._cached_perceive_scene_step = int(self._step_id or 0)
        self._reason_controls = reason_controls
        self._legal_actions = list(getattr(desc, "legal_actions", []) or [])
        self._sync_legal_actions()
        return float(call.latency_ms)

    def _clickable_labels(self) -> list[str]:
        if self._frame_model is not None:
            labels = self._frame_model.interactive_labels()
            if labels:
                return labels
        if self._aim is None or not self._aim.marks:
            return []
        return clickable_labels_from_list(
            [str(mark.label or "").strip() for mark in self._aim.marks if (mark.label or "").strip()]
        )

    def _build_frame_model(self, *, informational: list[str] | None = None) -> FrameModel | None:
        if self._aim is None:
            return None
        screen = str(
            self._host_stats.get("screen")
            or self._obs_metrics.get("screen")
            or ""
        ).strip() or None
        model = build_frame_model(
            self._aim.marks,
            screen=screen,
            host_stats=self._host_stats,
            informational=informational,
        )
        self._frame_model = model
        return model

    @staticmethod
    def _surface_key(value: Any) -> str:
        return " ".join(str(value or "").casefold().replace("_", " ").replace("-", " ").split())

    @staticmethod
    def _scene_bbox(value: Any) -> tuple[float, float, float, float] | None:
        if isinstance(value, str):
            try:
                values = [float(part.strip()) for part in value.split(",")]
            except ValueError:
                return None
        elif isinstance(value, (list, tuple)):
            try:
                values = [float(part) for part in value]
            except (TypeError, ValueError):
                return None
        else:
            return None
        if len(values) < 4:
            return None
        x0, y0, x1, y1 = values[:4]
        if max(abs(x0), abs(y0), abs(x1), abs(y1)) > 1.5:
            x0, y0, x1, y1 = (part / 1000.0 for part in (x0, y0, x1, y1))
        if x1 <= x0 or y1 <= y0:
            return None
        return x0, y0, x1, y1

    def _reconcile_scene_marks(self, scene_desc: Any) -> list[AimMark]:
        """Ground semantic scene rows onto current-frame marks without inventing targets."""
        if self._aim is None or scene_desc is None:
            return list(self._aim.marks) if self._aim is not None else []
        rows: list[dict[str, Any]] = []
        for bucket in (
            getattr(scene_desc, "controls", None) or [],
            getattr(scene_desc, "hand_cards", None) or [],
            getattr(scene_desc, "enemies", None) or [],
            getattr(scene_desc, "characters", None) or [],
        ):
            rows.extend(row for row in bucket if isinstance(row, dict))
        marks = list(self._aim.marks)
        used: set[int] = set()
        generic = {"button", "control", "dialog", "menu", "nav_prev", "nav_next", "next", "prev", "previous"}
        domain = {"card", "enemy", "player", "target", "character", "unknown_character"}

        def compatible(mark: AimMark, row_role: str) -> bool:
            mark_role = self._surface_key(mark.role)
            if not mark_role or mark_role in {"unknown", "icon", "other"}:
                return True
            if row_role in generic:
                return True  # foreground/generic semantics take precedence
            if mark_role in generic and row_role in domain:
                return False
            if mark_role in domain and row_role in domain:
                normalized_mark = "enemy" if mark_role in {"target", "character", "unknown character"} else mark_role
                normalized_row = "enemy" if row_role in {"target", "character", "unknown_character"} else row_role
                return normalized_mark == normalized_row
            return True

        for row in rows:
            label = str(row.get("label") or row.get("name") or "").strip()
            row_id = str(row.get("entity_id") or row.get("object_id") or row.get("id") or "").strip()
            if is_frame_mark_token(row_id):
                row_id = ""
            row_role = self._surface_key(row.get("role") or row.get("kind") or "other")
            candidates = [
                (index, mark) for index, mark in enumerate(marks)
                if index not in used and compatible(mark, row_role)
            ]
            match: tuple[int, AimMark] | None = None
            if row_id:
                exact = [(index, mark) for index, mark in candidates if str(mark.object_id or "") == row_id]
                if len(exact) == 1:
                    match = exact[0]
            if match is None and label:
                exact = [
                    (index, mark) for index, mark in candidates
                    if self._surface_key(mark.label) == self._surface_key(label)
                ]
                if len(exact) == 1:
                    match = exact[0]
            bbox = self._scene_bbox(row.get("bbox"))
            if match is None and bbox is not None:
                bx = (bbox[0] + bbox[2]) / 2
                by = (bbox[1] + bbox[3]) / 2
                ranked = sorted(
                    (
                        ((mark.x - bx) ** 2 + (mark.y - by) ** 2, index, mark)
                        for index, mark in candidates
                    ),
                    key=lambda item: item[0],
                )
                if ranked and ranked[0][0] <= 0.12 * 0.12:
                    _, index, mark = ranked[0]
                    match = (index, mark)
            if match is None:
                continue
            index, mark = match
            used.add(index)
            role = str(row.get("role") or "").strip().lower()
            if role in {"next"}:
                role = "nav_next"
            elif role in {"prev", "previous"}:
                role = "nav_prev"
            elif role in {"dialog", "menu"}:
                role = "button"
            object_id = row_id or mark.object_id
            if row_role in {"enemy", "unknown_character", "character", "target"} and is_prompt_instance_id(str(object_id or "")):
                object_id = mark.object_id
            source = "+".join(dict.fromkeys(filter(None, (str(mark.source or ""), "llm1"))))
            marks[index] = _copy_mark(
                mark,
                label=label or mark.label,
                object_id=object_id,
                role=role or mark.role,
                source=source,
                confidence=max(float(mark.confidence or mark.score or 0.0), 0.75),
            )
        return marks

    def _finalize_action_surface(
        self,
        scene_desc: Any,
        *,
        informational: list[str] | None = None,
    ) -> ActionSurface | None:
        """Build and persist the one canonical executable inventory for this frame."""
        if self._aim is None:
            self._action_surface = None
            self._frame_model = None
            return None
        screen = str(
            getattr(scene_desc, "screen", None)
            or self._host_stats.get("screen")
            or self._obs_metrics.get("screen")
            or "other"
        ).strip().lower() or "other"
        activity = self.fixture.resolve_activity(
            metrics=self._obs_metrics,
            host_stats=self._host_stats,
            screen=screen,
        )
        reconciled = self._reconcile_scene_marks(scene_desc)
        provisional = build_action_surface(
            reconciled,
            run_id=self.session_id,
            step_id=int(self._step_id or 0),
            screen=screen,
            activity=activity,
            host_stats=self._host_stats,
            informational=informational,
        )
        enriched = self.fixture.enrich_frame_marks(
            reconciled,
            provisional.context,
            self._host_stats,
        )
        surface = build_action_surface(
            enriched,
            run_id=self.session_id,
            step_id=int(self._step_id or 0),
            screen=screen,
            activity=activity,
            layout=provisional.context.layout,
            blocking_overlay=provisional.context.blocking_overlay,
            host_stats=self._host_stats,
            informational=informational,
        )
        stamped = self._screen_objects.save_frame(
            run_id=self.session_id,
            step_id=int(self._step_id or 0),
            marks=enriched,
            screen=surface.context.screen,
            source="canonical-surface",
            context=surface.context,
            surface=surface,
        )
        self._aim = AimGuide.from_marks(
            self._last_obs_image if self._last_obs_image is not None else self._aim.image,
            stamped,
            last_xy=self._aim.last_xy,
        )
        self._action_surface = surface
        self._frame_model = surface.frame_model()
        return surface

    def _reason_controls_from_surface(self, scene_desc: Any) -> list[dict[str, Any]]:
        surface = self._action_surface
        if surface is None:
            return []
        details: dict[str, dict[str, Any]] = {}
        for row in getattr(scene_desc, "controls", None) or []:
            if isinstance(row, dict):
                details.setdefault(self._surface_key(row.get("label") or row.get("name")), row)
        out: list[dict[str, Any]] = []
        for obj in surface.interactive():
            row = dict(details.get(self._surface_key(obj.label), {}))
            row.update({
                "label": obj.label,
                "role": obj.role,
                "entity_id": obj.object_id,
                "confidence": obj.confidence,
                "source": obj.provenance or obj.source,
            })
            out.append(row)
        return out

    def _reconcile_game_state(self, *, action: LegalAction | None = None, expected_effects: tuple[str, ...] = (), selected_card_id: str | None = None) -> GameState | None:
        """Replace current state from sensors; planner text is deliberately absent."""
        if not hasattr(self, "fixture") or not self._two_stage() or self._frame_model is None:
            return getattr(self, "_game_state", None)
        self._game_state = reconcile_game_state(
            self._game_state, step=int(self._step_id or 0), host_stats=self._host_stats,
            frame=self._frame_model, executed_action=action, expected_effects=expected_effects,
            verified_selected_card_id=selected_card_id,
        )
        self._persist_verified_tutorial_rules()
        return self._game_state

    def _state_prompt_block(self) -> str:
        state = self._game_state
        if state is None:
            return "Authoritative state: unavailable; do not make combat card actions."
        cards = [f"{row.id}:{row.name.value}" for row in state.hand if row.name.known]
        enemies = [f"{row.id}:{row.label.value or 'anonymous target'}" for row in state.enemies if row.targetable.value]
        actions = [f"{row.kind}({row.card_id or row.object_id or ''}{'→' + row.target_id if row.target_id else ''})" for row in state.legal_actions]
        identities = self.semantic_memory.identities(
            screen=str(state.underlying_activity.value or state.phase.value or "")
        )
        identity_line = ", ".join(
            f"{row.value.get('role')}={row.value.get('name')}"
            for row in identities[:6]
        ) or "(none)"
        return "\n".join((
            f"Authoritative state: phase={state.phase.value or 'unknown'} activity={state.underlying_activity.value or 'unknown'} turn={state.turn.value or 'unknown'} overlays={[item.value for item in state.overlays]}",
            "Verified hand: " + (", ".join(cards) or "(none)"),
            "Verified enemies: " + (", ".join(enemies) or "(none)"),
            "Legal state actions (exclusive): " + (", ".join(actions) or "(none)"),
            "Durable identities (context only, never current-frame targets): " + identity_line,
            "Never use an icon, unknown object, journal entry, or stale mark as a card/enemy.",
        ))

    def _state_allows_combat_action(self, choice: str, target: str) -> bool:
        """Fail closed: a card/target request needs a typed current state object."""
        choice = str(choice or "")
        target = str(target or "")
        state = self._game_state
        if state is None:
            return not choice and not target
        if state.overlays:
            return not target and choice.casefold() in {"got it", "next", "prev", "confirm", "close", "resume"}
        # State gating applies only to combat requests.  Non-combat controls
        # retain their own current-frame validation path.
        if state.underlying_activity.value != "combat":
            return not target
        card_names = {str(card.name.value or "").casefold() for card in state.hand if card.playable.value}
        enemy_names = {
            value.casefold()
            for enemy in state.enemies if enemy.targetable.value
            for value in (str(enemy.label.value or ""), str(enemy.id))
        }
        end_turn_ok = any(action.kind == "end_turn" for action in state.legal_actions)
        is_end_turn = choice.casefold() == "end turn" and end_turn_ok
        if choice and choice.casefold() not in card_names and not is_end_turn:
            return False
        if target and target.casefold() not in enemy_names:
            return False
        if target and is_end_turn:
            return False
        return True

    def _state_action_from_labels(self, choice: str, target: str) -> tuple[LegalAction | None, tuple[str, ...]]:
        state = self._game_state
        if state is None or not choice:
            return None, ()
        card = next((row for row in state.hand if str(row.name.value or "").casefold() == choice.casefold()), None)
        if card is None:
            return None, ()
        enemy = next((row for row in state.enemies if (
            str(row.label.value or "").casefold() == target.casefold()
            or row.id.casefold() == target.casefold()
        )), None)
        if enemy is not None:
            return LegalAction("play_card", card_id=card.id, target_id=enemy.id), ("energy_spent", "card_leaves_hand", "enemy_hp_changes")
        return LegalAction("select_card", card_id=card.id), ("selection_confirmed",)

    def _resolve_enemy_alias_to_current_label(self, target: str) -> str:
        """Map a durable EnemyState id to this frame's mark label, never a mark id."""
        state = self._game_state
        if state is None or self._aim is None:
            return target
        enemy = next((row for row in state.enemies if row.id.casefold() == str(target or "").casefold()), None)
        if enemy is None or enemy.bbox.value is None:
            return target
        x0, y0, x1, y1 = enemy.bbox.value
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        candidates = [mark for mark in self._aim.marks if in_combat_sprite_band(mark.y, mark.x)]
        if not candidates:
            return target
        hit = min(candidates, key=lambda mark: (mark.x - cx) ** 2 + (mark.y - cy) ** 2)
        if (hit.x - cx) ** 2 + (hit.y - cy) ** 2 > 0.12 ** 2:
            return target
        return str(hit.label or target)

    def _persist_verified_tutorial_rules(self) -> None:
        """Persist only explicit OCR tutorial statements, never planner summaries."""
        blob = str(self._host_stats.get("ocr") or "").lower()
        rules = []
        if "defeat enemies by playing cards" in blob:
            rules.append(("combat-play-cards", "combat with enemy", "play a hand card", "defeat enemies"))
        if "start each combat" in blob and "energy" in blob:
            rules.append(("combat-energy", "start of combat", "use available energy", "cards consume energy"))
        for rule_id, trigger, action, effect in rules:
            self.semantic_memory.remember_tutorial_rule(
                rule_id=rule_id, trigger=trigger, action=action, effect=effect,
                provenance="host_ocr_tutorial", confidence=0.9, verified=True,
                step_id=int(self._step_id or 0),
            )
        if rules:
            self.semantic_memory.save()

    def _ground_aim_from_scene(self, scene_desc: Any) -> None:
        """Materialize LLM1 controls onto aim marks (combat targets/cards, map nodes)."""
        if self._aim is None or scene_desc is None:
            return
        controls = getattr(scene_desc, "controls", None) or []
        screen = str(
            getattr(scene_desc, "screen", None)
            or self._host_stats.get("screen")
            or self._obs_metrics.get("screen")
            or ""
        ).strip() or None
        before = list(self._aim.marks)
        grounded = before
        screen_key = (screen or "").strip().lower()

        _DISCOVERY_ROLES = frozenset({"enemy", "unknown_character", "character"})

        enemies = [
            str(row.get("label") or "").strip()
            for row in controls
            if isinstance(row, dict)
            and str(row.get("role") or "") in _DISCOVERY_ROLES
            and not is_player_row(row)
        ]
        hand = [
            str(row.get("label") or "").strip()
            for row in controls
            if isinstance(row, dict) and str(row.get("role") or "") == "card"
        ]
        typed_enemies = getattr(scene_desc, "enemies", None) or []
        typed_characters = getattr(scene_desc, "characters", None) or []
        typed_hand = getattr(scene_desc, "hand_cards", None) or []
        for row in typed_enemies + typed_characters:
            if isinstance(row, dict) and not is_player_row(row):
                label = str(row.get("label") or "").strip()
                if label and label not in enemies:
                    enemies.append(label)
        for row in typed_hand:
            if isinstance(row, dict):
                label = str(row.get("label") or "").strip()
                if label and label not in hand:
                    hand.append(label)
        combat = getattr(scene_desc, "combat", None) or {}
        if isinstance(combat, dict):
            if not enemies:
                legacy = combat.get("enemies")
                if isinstance(legacy, list):
                    enemies = [str(v).strip() for v in legacy if str(v or "").strip()]
            if not hand:
                legacy = combat.get("hand")
                if isinstance(legacy, list):
                    hand = [str(v).strip() for v in legacy if str(v or "").strip()]
        if enemies or hand:
            grounded = ground_combat_labels(
                grounded,
                enemies=enemies if isinstance(enemies, list) else None,
                hand=hand if isinstance(hand, list) else None,
                screen=screen,
            )
        hand_grounded = apply_hand_bboxes(grounded, typed_hand)
        if hand_grounded != grounded:
            grounded = hand_grounded
        enemy_rows = list(typed_enemies) + list(typed_characters) + [
            row for row in controls
            if isinstance(row, dict) and str(row.get("role") or "") in _DISCOVERY_ROLES
        ]
        player_rows: list[dict[str, Any]] = []
        hostile_rows: list[dict[str, Any]] = []
        for row in enemy_rows:
            if not isinstance(row, dict):
                continue
            if is_player_row(row):
                stamped = dict(row)
                stamped["label"] = player_display_label(str(stamped.get("label") or ""))
                stamped["role"] = "player"
                player_rows.append(stamped)
            else:
                hostile_rows.append(row)
        bbox_grounded = apply_enemy_bboxes(grounded, hostile_rows)
        if player_rows:
            bbox_grounded = apply_enemy_bboxes(bbox_grounded, player_rows, role="player")
        if bbox_grounded != grounded:
            grounded = bbox_grounded
        if screen_key == "map":
            grounded = ground_spatial_labels(grounded, controls, screen=screen)

        # OCR card titles are pixel-grounded and generally sharper than LLM1's
        # view of the overlapped hand.  Publish those corrected instances back
        # into the scene as well as AimGuide; otherwise unknown_card remains in
        # the reasoner's typed hand and unnecessarily routes the turn to
        # Explore even though Strike/Defend are already visible and clickable.
        if screen_key == "combat":
            trusted_cards = [
                mark for mark in grounded
                if mark.role == "card"
                and "ocr-hand" in str(mark.source or "")
                and " ".join(str(mark.label or "").lower().replace("_", " ").split())
                not in {"", "unknown", "unknown card", "icon", "card"}
            ]
            if trusted_cards:
                card_rows = [
                    {
                        "label": mark.label,
                        "name": mark.label,
                        "name_source": "ocr",
                        "role": "card",
                        "confidence": "high",
                        "playable": "true",
                        "entity_id": mark.object_id or f"card_{index}",
                        "bbox": ",".join(
                            str(int(round(value * 1000)))
                            for value in (mark.x0, mark.y0, mark.x1, mark.y1)
                        ),
                        "source": "ocr-hand",
                    }
                    for index, mark in enumerate(trusted_cards, start=1)
                ]
                scene_desc.hand_cards = card_rows
                scene_desc.controls = [
                    row for row in controls
                    if not isinstance(row, dict) or str(row.get("role") or "") != "card"
                ] + [dict(row) for row in card_rows]

        if grounded == before:
            return
        before_labels = {
            " ".join(str(m.label or "").lower().split())
            for m in before
            if (m.label or "").strip()
        }
        added = [
            m.label
            for m in grounded
            if (m.label or "").strip()
            and " ".join(m.label.lower().split()) not in before_labels
        ]
        image = self._last_obs_image if self._last_obs_image is not None else self._aim.image
        step_id = int(self._step_id or 0)
        if step_id > 0:
            stamped = self._screen_objects.save_frame(
                run_id=self.session_id,
                step_id=step_id,
                marks=grounded,
                screen=screen,
                source="llm1",
            )
            self._aim = AimGuide.from_marks(image, stamped, last_xy=self._aim.last_xy)
        else:
            self._aim = AimGuide.from_marks(image, grounded, last_xy=self._aim.last_xy)
        if added:
            _say(f"  aim      grounded {', '.join(added[:6])}")

    def _apply_perceive_scene(
        self,
        scene_desc: Any,
        *,
        detector_rows: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Post-LLM1: ground combat/map labels, frame model."""
        self._scene_desc = scene_desc
        if scene_desc is not None:
            tracker = getattr(self, "_entity_tracker", None)
            if tracker is None:
                self._entity_tracker = EntityTracker()
                tracker = self._entity_tracker
            tracker.merge_into_scene(
                scene_desc,
                self._host_stats,
                lexicon=self.journal.lexicon,
                screen=str(
                    getattr(scene_desc, "screen", None)
                    or self._host_stats.get("screen")
                    or self._obs_metrics.get("screen")
                    or ""
                ),
            )
            self._log_entity_merge()
        if scene_desc is not None and getattr(self, "_overlay_state", None) is not None:
            host = str(self._host_stats.get("screen") or "")
            perceive = str(getattr(scene_desc, "screen", None) or "")
            self._overlay_state.log_screen_disagreement(
                host_screen=host,
                perceive_screen=perceive,
                step_id=int(getattr(self, "_step_id", 0) or 0),
            )
            resolved = self._overlay_state.resolved_screen(
                host_screen=host,
                perceive_screen=perceive,
            )
            if resolved and resolved != perceive:
                scene_desc.screen = resolved
            ocr_blob = str(self._obs_metrics.get("ocr") or self._host_stats.get("ocr") or "")
            reconciled = reconcile_combat_screen(str(scene_desc.screen or ""), ocr=ocr_blob)
            if reconciled != str(scene_desc.screen or ""):
                scene_desc.screen = reconciled
        self._last_scene = scene_desc.as_text() or scene_desc.scene
        self._ground_aim_from_scene(scene_desc)
        if getattr(getattr(self, "fixture", None), "id", "") == "sts2":
            self._finalize_action_surface(
                scene_desc,
                informational=scene_desc.informational_text(),
            )
            reason_controls = self._reason_controls_from_surface(scene_desc)
            self._reason_controls = reason_controls
            self._reconcile_game_state()
            return reason_controls
        labels = detector_rows if detector_rows is not None else self._detector_typed_rows()
        reason_controls = reason_controls_for_turn(
            controls=scene_desc.controls,
            uncertain=scene_desc.uncertain,
            enemies=(scene_desc.combat or {}).get("enemies"),
            detector_rows=labels,
            tutorial_info=scene_desc.tutorial,
            host_stats=self._host_stats,
        )
        self._build_frame_model(informational=scene_desc.informational_text())
        if getattr(self.config, "dino", False):
            fused = self._dino_fuse_after_perceive(reason_controls)
            if fused is not None:
                reason_controls = fused
        self._reconcile_game_state()
        stamped_controls = self._finalize_reason_controls(reason_controls)
        self._reason_controls = stamped_controls
        return stamped_controls

    def _dino_fuse_after_perceive(self, controls: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Optional DINO fusion hook after LLM1; default is pass-through."""
        return controls

    @staticmethod
    def _field_value(value: Any) -> Any:
        """Read a reconciled Field or a plain sensor value."""
        return getattr(value, "value", value)

    def _build_turn_snapshot(self, scene_desc: Any) -> TurnSnapshot:
        """Freeze the current typed object inventory for one v2 ACT call."""
        from loop.mcp.tools.targeting import bind_snapshot_target_ids

        surface = getattr(self, "_action_surface", None)
        if surface is not None:
            snapshot_id = surface.snapshot_id
        else:
            identity = current_snapshot_identity(self._screen_objects)
            snapshot_id = str(identity["snapshot_id"])
        instance_rows: list[dict[str, Any]] = []
        if scene_desc is not None:
            for bucket in (
                getattr(scene_desc, "enemies", None) or [],
                getattr(scene_desc, "characters", None) or [],
                getattr(scene_desc, "controls", None) or [],
            ):
                for row in bucket:
                    if isinstance(row, dict):
                        instance_rows.append(dict(row))
        tracker = getattr(self, "_entity_tracker", None)
        if tracker is not None:
            for ent in getattr(tracker, "entities", ()) or ():
                instance_rows.append(ent.to_row())
        for row in getattr(self, "_reason_controls", None) or []:
            if isinstance(row, dict):
                instance_rows.append(dict(row))
        bind_snapshot_target_ids(
            self._screen_objects,
            instance_rows,
            entity_targets_ok=self._combat_screen_now(),
        )
        game_state = self._game_state
        player = getattr(game_state, "player", None)
        host_player = self._host_stats.get("player") or {}

        def _resource(name: str) -> int | None:
            raw = self._field_value(getattr(player, name, None)) if player is not None else None
            if raw is None:
                raw = self._host_stats.get(name)
            if raw is None and isinstance(host_player, dict):
                raw = host_player.get(name)
            return int(raw) if isinstance(raw, (int, float)) and not isinstance(raw, bool) else None

        energy = _resource("energy")
        cards: list[CardSnapshot] = []
        targets: list[TargetSnapshot] = []
        controls: list[ControlSnapshot] = []
        self._v2_control_objects = {}
        hand_states = list(getattr(game_state, "hand", ()) or ())
        enemy_states = list(getattr(game_state, "enemies", ()) or ())
        scene_cards = [
            row for row in list(getattr(scene_desc, "hand_cards", None) or [])
            if isinstance(row, dict)
        ]

        def _norm(value: Any) -> str:
            return " ".join(str(value or "").lower().replace("_", " ").split())

        def _matching(items: list[Any], obj: Any, *, name_field: str) -> Any | None:
            oid = str(obj.object_id or "")
            exact = next((row for row in items if str(getattr(row, "id", "")) == oid), None)
            if exact is not None:
                return exact
            label = _norm(obj.label)
            named = [
                row for row in items
                if _norm(self._field_value(getattr(row, name_field, None))) == label
            ]
            if len(named) == 1:
                return named[0]
            best: tuple[float, Any] | None = None
            for row in items:
                bbox = self._field_value(getattr(row, "bbox", None))
                if not isinstance(bbox, (list, tuple)) or len(bbox) < 4:
                    continue
                cx = (float(bbox[0]) + float(bbox[2])) / 2
                cy = (float(bbox[1]) + float(bbox[3])) / 2
                dist = (cx - obj.x) ** 2 + (cy - obj.y) ** 2
                if best is None or dist < best[0]:
                    best = (dist, row)
            return best[1] if best is not None and best[0] <= 0.16 * 0.16 else None

        control_counts: dict[str, int] = {}
        for obj in self._screen_objects.objects:
            # A finalized surface has already applied generic precedence and
            # fixture policy.  Reclassifying persisted objects here recreated
            # the old card-band split brain.
            role = str(obj.role or "").strip().lower()
            if surface is None:
                role = object_role(obj, entity_targets_ok=self._combat_screen_now())
            bbox = BoundingBox(obj.x0, obj.y0, obj.x1, obj.y1)
            if role == "card":
                state = _matching(hand_states, obj, name_field="name")
                state_name = self._field_value(getattr(state, "name", None)) if state is not None else None
                detail = next((
                    row for row in scene_cards
                    if str(row.get("entity_id") or row.get("id") or "") == str(obj.object_id)
                ), None)
                if detail is None:
                    detail = next((
                        row for row in scene_cards
                        if _norm(row.get("label") or row.get("name")) in {_norm(obj.label), _norm(state_name)}
                    ), {})
                scene_name = detail.get("name") or detail.get("label")
                card_name = str(scene_name or state_name or obj.label or "").strip() or None
                cost = self._field_value(getattr(state, "cost", None)) if state is not None else None
                playable = self._field_value(getattr(state, "playable", None)) if state is not None else None
                if playable is None:
                    playable = True
                if isinstance(cost, (int, float)) and energy is not None and int(cost) > energy:
                    playable = False
                cards.append(CardSnapshot(
                    card_id=str(obj.object_id), name=card_name, bbox=bbox,
                    playable=bool(playable),
                    description=str(detail.get("description") or "").strip() or None,
                    card_type=str(detail.get("type") or detail.get("card_type") or "").strip() or None,
                    cost=int(cost) if isinstance(cost, (int, float)) else None,
                ))
            elif role == "enemy":
                target_id = str(obj.object_id or "").strip()
                if not target_id or is_prompt_instance_id(target_id):
                    # A frame-local visual blob is not a canonical target.  It
                    # stays available to perception, but ACT cannot address it
                    # until the object store assigns a stable identity.
                    continue
                state = _matching(enemy_states, obj, name_field="label")
                hp = self._field_value(getattr(state, "hp", None)) if state is not None else None
                intent = self._field_value(getattr(state, "intent", None)) if state is not None else None
                confidence = getattr(getattr(state, "label", None), "confidence", None) if state is not None else obj.confidence
                raw_target_name = (
                    self._field_value(getattr(state, "label", None)) if state is not None else obj.label
                )
                target_name = str(raw_target_name or "").strip()
                compact_target = _norm(target_name)
                if (
                    is_anonymous_enemy_label(target_name)
                    or any(phrase in compact_target for phrase in (
                        "deal ", " damage", "gain ", " block", "draw ", "exhaust",
                    ))
                ):
                    target_name = ""
                targets.append(TargetSnapshot(
                    target_id=target_id, bbox=bbox, name=target_name or None,
                    hp=int(hp) if isinstance(hp, (int, float)) else None,
                    intent=str(intent) if intent is not None else None,
                    identity_confidence=float(confidence) if isinstance(confidence, (int, float)) else None,
                ))
            elif role in {"button", "control", "nav_prev", "nav_next"}:
                slug = "_".join(_norm(obj.label).split()) or "control"
                control_counts[slug] = control_counts.get(slug, 0) + 1
                suffix = f"_{control_counts[slug]}" if control_counts[slug] > 1 else ""
                control_id = str(obj.object_id or f"control:{slug}{suffix}")
                self._v2_control_objects[control_id] = obj
                controls.append(ControlSnapshot(control_id, obj.label or slug, bbox, role=role))
            if obj.object_id:
                self._v2_control_objects.setdefault(str(obj.object_id), obj)

        turn = self._field_value(getattr(game_state, "turn", None)) if game_state is not None else None
        if turn is None:
            turn = self._host_stats.get("turn")
        return TurnSnapshot(
            snapshot_id=snapshot_id,
            screen=(
                surface.context.screen if surface is not None
                else str(getattr(scene_desc, "screen", None) or self._host_stats.get("screen") or "other")
            ),
            turn=str(turn) if turn is not None else None,
            hp=_resource("hp"), block=_resource("block"), energy=energy,
            cards=tuple(cards), targets=tuple(targets), controls=tuple(controls),
            legal_intents=(surface.legal_intents if surface is not None else None),
            last_execution=self._last_execution_failure,
        )

    def _run_v2_act(self, snapshot: TurnSnapshot) -> tuple[dict[str, Any], float]:
        """Choose one typed intent, then deterministically bind it to this frame."""
        state_key = json.dumps({
            "screen": snapshot.screen, "turn": snapshot.turn,
            "hp": snapshot.hp, "block": snapshot.block, "energy": snapshot.energy,
            "cards": [(c.card_id, c.name, c.playable, c.cost) for c in snapshot.cards],
            "targets": [(t.target_id, t.name, t.hp, t.intent) for t in snapshot.targets],
            "controls": [(c.control_id, c.label) for c in snapshot.controls],
        }, sort_keys=True, separators=(",", ":"))
        legal_intents = legal_intents_for_snapshot(snapshot)
        if not legal_intents:
            self._last_decision_schema = "v2"
            self._last_snapshot_id = snapshot.snapshot_id
            snapshot_payload = json.loads(compact_act_prompt(snapshot))
            snapshot_payload.pop("response_schema", None)
            self._last_turn_snapshot = snapshot_payload
            self._last_intent = {}
            self._last_reason_raw = ""
            self._last_reason_backend = "none"
            self._last_reason_fallback = "no-legal-intents"
            self.last_system_prompt = ACT_SYSTEM_PROMPT
            self.last_user_prompt = compact_act_prompt(snapshot)
            turn_key = self._surface_key(snapshot.turn)
            observation_only = (
                turn_key in {"theirs", "opponent", "opponent turn", "waiting", "animation", "terminal"}
                or self._surface_key(snapshot.screen) in {"win", "victory", "fail", "defeat", "death"}
            )
            interactive_evidence = bool(
                getattr(getattr(self, "_action_surface", None), "interactive", lambda: ())()
            )
            if observation_only:
                self._empty_action_surface_key = ""
                self._empty_action_surface_count = 0
                return {}, 0.0
            if state_key == self._empty_action_surface_key:
                self._empty_action_surface_count += 1
            else:
                self._empty_action_surface_key = state_key
                self._empty_action_surface_count = 1
            if interactive_evidence and self._empty_action_surface_count == 1:
                self._pending_exec_error = "inventory-inconsistent"
                self._force_perceive = True
                self._perception_cache.reset()
            else:
                self._pending_exec_error = "empty-action-surface"
                if self._empty_action_surface_count >= 2:
                    self._decision_softlock = True
                    if "decision_softlock" not in self._session_flags:
                        self._session_flags.append("decision_softlock")
            return {}, 0.0
        self._empty_action_surface_key = ""
        self._empty_action_surface_count = 0
        if self._last_execution_failure and state_key == self._last_reason_state_key:
            self._unchanged_failure_count += 1
            if self._unchanged_failure_count >= 2:
                self._pending_exec_error = "decision-softlock"
                self._decision_softlock = True
                self._last_decision_schema = "v2"
                if "decision_softlock" not in self._session_flags:
                    self._session_flags.append("decision_softlock")
                return {}, 0.0
        else:
            self._unchanged_failure_count = 0
        self._last_reason_state_key = state_key

        primary = self.gateway
        assert primary is not None
        primary_model = str(getattr(primary, "model", "") or "gemma4:31b-cloud")

        def _gateway_factory(**kwargs: Any) -> Any:
            if str(kwargs.get("model") or "") == primary_model:
                return primary
            base_url = getattr(primary, "base_url", None)
            return VlmGateway(base_url=base_url, **kwargs)

        reasoner = ActReasoner(
            gateway_factory=_gateway_factory,
            primary_model=primary_model,
            fallback_model=self.config.reason_fallback_model,
        )
        started = time.perf_counter()
        self._turn_reasoner_called = True
        try:
            decision = reasoner.decide(snapshot)
        except ActReasonerError as exc:
            reason_ms = (time.perf_counter() - started) * 1000
            self._last_decision_schema = "v2"
            self._last_snapshot_id = snapshot.snapshot_id
            snapshot_payload = json.loads(compact_act_prompt(snapshot))
            snapshot_payload.pop("response_schema", None)
            self._last_turn_snapshot = snapshot_payload
            self._last_intent = {}
            self._last_reason_backend = "none"
            self._last_reason_fallback = str(exc.fallback_error or exc.primary_error or exc)
            self._last_reason_raw = ""
            self._last_reason_call = {
                "primary_error": exc.primary_error,
                "fallback_error": exc.fallback_error,
                "latency_ms": reason_ms,
            }
            failures = " ".join(filter(None, (str(exc), exc.primary_error, exc.fallback_error or "")))
            self._pending_exec_error = (
                "invalid-act-intent" if "invalid-act-intent" in failures
                else "act-reasoner-failed"
            )
            self._force_perceive = True
            return {}, reason_ms
        reason_ms = (time.perf_counter() - started) * 1000
        intent = decision.intent
        intent_dict = asdict(intent)
        self._last_decision_schema = "v2"
        self._last_snapshot_id = snapshot.snapshot_id
        snapshot_payload = json.loads(compact_act_prompt(snapshot))
        snapshot_payload.pop("response_schema", None)
        self._last_turn_snapshot = snapshot_payload
        self._last_intent = intent_dict
        self._last_reason_backend = decision.backend
        self._last_reason_fallback = decision.fallback_reason or ""
        self._last_reason_call = dict(decision.metrics)
        self._last_reason_call["latency_ms"] = decision.latency_ms
        self._last_reason_raw = json.dumps(intent_dict, separators=(",", ":"))
        self.last_system_prompt = ACT_SYSTEM_PROMPT
        self.last_user_prompt = compact_act_prompt(snapshot)
        self._last_tool_trace = []
        self._last_tool_events = []

        parsed: dict[str, Any]
        if isinstance(intent, PlayCardIntent):
            planned = plan_card_by_id(
                self._screen_objects,
                card_id=intent.card_id,
                target_id=intent.target_id,
                entity_targets_ok=self._combat_screen_now(),
            )
            self._last_execution_plan = dict(planned.get("transaction") or planned)
            if not planned.get("ok"):
                self._pending_exec_error = "action-surface-invariant"
                parsed = {}
            else:
                self._planned_hand_signature = self._hand_inventory_signature()
                self._stale_source_plan = None
                transaction = planned["transaction"]
                source = transaction["source"]
                target = transaction.get("target_candidate")
                card_meta = next((row for row in snapshot.cards if row.card_id == intent.card_id), None)
                target_meta = next((row for row in snapshot.targets if row.target_id == intent.target_id), None)
                parsed = {
                    "action": "play_card", "choice_id": intent.card_id,
                    "choice": (card_meta.name if card_meta is not None else None) or source.get("label") or intent.card_id,
                    "target_id": intent.target_id or "",
                    "target": (target_meta.name if target_meta is not None else None) or (target or {}).get("label") or "",
                }
        elif isinstance(intent, (EndTurnIntent, ClickControlIntent)):
            obj = self._v2_control_objects.get(intent.control_id)
            if obj is None:
                self._pending_exec_error = "action-surface-invariant"
                self._last_execution_plan = {"ok": False, "reason": "action-surface-invariant"}
                parsed = {}
            else:
                frame_identity = current_snapshot_identity(self._screen_objects)
                self._last_execution_plan = {
                    "decision_schema": "v2", "snapshot_id": snapshot.snapshot_id,
                    "frame_snapshot_id": frame_identity["snapshot_id"],
                    "frame_run_id": frame_identity["run_id"],
                    "frame_step_id": frame_identity["step_id"],
                    "action": intent.kind, "control_id": intent.control_id,
                    "label": obj.label,
                    "point": {"x": obj.x, "y": obj.y},
                }
                parsed = {"action": intent.kind, "choice": obj.label}
        elif isinstance(intent, InspectIntent):
            obj = self._v2_control_objects.get(intent.object_id)
            if obj is None:
                self._pending_exec_error = "action-surface-invariant"
                self._last_execution_plan = {"ok": False, "reason": "action-surface-invariant"}
                parsed = {}
            else:
                frame_identity = current_snapshot_identity(self._screen_objects)
                self._last_execution_plan = {
                    "decision_schema": "v2", "snapshot_id": snapshot.snapshot_id,
                    "frame_snapshot_id": frame_identity["snapshot_id"],
                    "frame_run_id": frame_identity["run_id"],
                    "frame_step_id": frame_identity["step_id"],
                    "action": "inspect", "object_id": intent.object_id,
                    "label": obj.label,
                    "point": {"x": obj.x, "y": obj.y},
                }
                parsed = {"action": "inspect", "target": obj.label, "target_id": intent.object_id}
        else:  # pragma: no cover - ActionIntent is exhaustive
            raise RuntimeError("unsupported ActionIntent")
        self._log_combat_decision(parsed)
        return parsed, reason_ms

    def _avoid_for_turn(
        self,
        *,
        host_screen: str | None,
        tutorial_info: dict[str, Any] | None = None,
        listing: str | None = None,
        prev_screen: str | None = None,
    ) -> frozenset[str]:
        screen = overlay_screen(
            str(host_screen or prev_screen or self._obs_metrics.get("screen") or ""),
            listing,
        )
        blocked = set(avoid_devices(self.journal, screen))
        blocked.update(getattr(self, "_blocked_marks", ()))
        tut = tutorial_info or self._host_stats.get("tutorial") or {}
        if isinstance(tut, dict) and tut.get("active") and self._aim is not None:
            resolved = (screen or "").strip().lower()
            if resolved in {"tutorial", "combat"}:
                for mark in self._aim.marks:
                    lab = " ".join(str(mark.label or "").lower().split())
                    if lab == "back":
                        blocked.add(f"mk_{mark.id}")
        return frozenset(blocked)

    def _run_two_stage_vlm(
        self,
        *,
        obs_frame: Any,
        host_screen: str | None,
        notebook_block: str | None,
        blocked: str | None,
        last_pixels_changed: bool | None,
        journal_block: str | None = None,
    ) -> tuple[dict[str, Any], float, float, Any]:
        perceive_ms = 0.0
        reason_ms = 0.0
        recheck_ms: list[float] = []
        perceive_system, reason_system = two_stage_system_for(arm=self.config.arm)
        self.last_system_prompt = reason_system
        detector_labels = self._detector_label_strings()
        perceive_gw = self._perceive_gw()
        if perceive_gw is None or self.gateway is None:
            raise RuntimeError("two_stage requires perceive and reason gateways")
        resolved_screen = str(
            host_screen or self._host_stats.get("screen") or self._obs_metrics.get("screen") or ""
        )
        prev_screen = str(
            self._prev_host_stats.get("screen") or self._obs_metrics.get("screen") or ""
        )
        forced_perceive = bool(self._force_perceive)
        run_llm1, gate_reason = should_run_llm1_perceive(
            screen=resolved_screen,
            prev_screen=prev_screen,
            host_stats=self._host_stats,
            prev_host_stats=self._prev_host_stats,
            entity_tracker=self._entity_tracker,
            cached_scene=self._cached_perceive_scene,
            force=self._force_perceive,
        )
        self._force_perceive = False
        ran_llm1 = run_llm1
        if run_llm1:
            perceive_started = time.perf_counter()
            try:
                scene_desc, perceive_call = perceive_scene(
                    perceive_gw,
                    obs_frame,
                    hud=self._obs_metrics,
                    host_stats=self._host_stats,
                    detector_labels=detector_labels,
                    max_tokens=self.config.perceive_max_tokens,
                    perceive_system=perceive_system,
                    perceive_user=self._scene_perceive_user(),
                    cv_verdicts=getattr(self, "_affordance_verdicts", None) or None,
                    max_schema_retries=0,
                    timeout_s=self.config.perceive_timeout_s,
                )
                perceive_ms = perceive_call.latency_ms
                self._capture_perception_audit(scene_desc)
                if not getattr(scene_desc, "schema_valid", True):
                    raise ValueError("malformed perception output")
                _say(f"  perceive   llm1 ({gate_reason})")
            except Exception as exc:  # cloud timeout/HTTP/schema -> canonical sensors
                perceive_ms = (time.perf_counter() - perceive_started) * 1000
                perceive_call = None
                scene_desc = scene_from_ocr(
                    hud=self._obs_metrics,
                    host_stats=self._host_stats,
                    frame_model=self._frame_model,
                    clickable_labels=self._clickable_labels(),
                    cached=self._cached_perceive_scene,
                    cv_verdicts=getattr(self, "_affordance_verdicts", None) or None,
                )
                gate_reason = f"{gate_reason}; ocr-fallback:{type(exc).__name__}"
                self._last_perceive_raw = ""
                _say(f"  perceive   OCR/frame fallback ({type(exc).__name__})")
        else:
            scene_desc = scene_from_ocr(
                hud=self._obs_metrics,
                host_stats=self._host_stats,
                frame_model=self._frame_model,
                clickable_labels=self._clickable_labels(),
                cached=self._cached_perceive_scene,
                cv_verdicts=getattr(self, "_affordance_verdicts", None) or None,
            )
            self._last_perceive_raw = ""
            _say(f"  perceive   skip llm1 ({gate_reason})")
            perceive_call = None
        combat_errors = validate_combat_perceive(scene_desc, self._host_stats)
        perceive_retry_reason = ""
        if combat_errors:
            perceive_retry_reason = "; ".join(combat_errors)
            _say(f"  perceive   combat fail: {perceive_retry_reason}")
            if should_skip_combat_perceive_rescan(combat_errors, self._host_stats):
                scene_desc = inject_host_enemies_into_scene(scene_desc, self._host_stats)
                combat_errors = validate_combat_perceive(scene_desc, self._host_stats)
                if not combat_errors:
                    _say("  perceive   ocr-anchored enemies (skipped rescan)")
                    perceive_retry_reason = ""
                else:
                    perceive_retry_reason = "; ".join(combat_errors)
            else:
                retry_ms = self._rescan_scene(focus="locate the enemy sprite and its HP bar")
                perceive_ms += retry_ms
                scene_desc = self._scene_desc or scene_desc
                combat_errors = validate_combat_perceive(scene_desc, self._host_stats)
                if combat_errors:
                    perceive_retry_reason = "; ".join(combat_errors)
        self._perceive_retry_reason = perceive_retry_reason or None
        self._record_perception_telemetry(
            ran_llm1=ran_llm1,
            gate_reason=gate_reason,
            forced=forced_perceive,
        )
        self._log_combat_perception(
            scene_desc,
            gate_reason=gate_reason,
            ran_llm1=ran_llm1,
            combat_errors=combat_errors if combat_errors else None,
        )
        reason_controls = self._apply_perceive_scene(scene_desc, detector_rows=self._detector_typed_rows())
        reason_route, route_reason = self._two_stage_reason_route(scene_desc)
        self._last_epistemic_blockers = self._two_stage_epistemic_blockers(scene_desc)
        self._route_before_discovery = f"{reason_route} ({route_reason})"
        # V2 can target an anonymous, typed enemy by stable ID. Pre-ACT hover
        # discovery added seconds and could read a nearby card tooltip as the
        # enemy name, corrupting both identity and target labels.
        discover_ms = 0.0 if self.fixture.id == "sts2" else self._run_mechanical_discovery(scene_desc)
        self._last_discover_ms = discover_ms
        scene_desc = self._scene_desc or scene_desc
        reason_controls = self._finalize_reason_controls(reason_controls_for_turn(
            controls=scene_desc.controls,
            uncertain=scene_desc.uncertain,
            enemies=(scene_desc.combat or {}).get("enemies"),
            detector_rows=self._detector_typed_rows(),
            tutorial_info=scene_desc.tutorial,
            host_stats=self._host_stats,
        ))
        reason_route, route_reason = self._two_stage_reason_route(scene_desc)
        # Sensor work is harness-owned. Live combat always hands ACT a named scene.
        self._last_reasoner_role = "act"
        self._last_router_reason = route_reason
        self._last_epistemic_blockers = self._two_stage_epistemic_blockers(scene_desc)
        self._route_after_discovery = f"act ({route_reason})"
        reason_system = self._two_stage_reason_system("act", reason_system)
        self.last_system_prompt = reason_system
        reason_route = "act"
        # Anonymous enemies have sensor-confirmed role/HP/bbox but no name.
        # Expose a stable entity_id with label unidentified_enemy — never a mark token.
        if self._game_state is not None:
            known_ids = {str(row.get("entity_id") or "").casefold() for row in reason_controls}
            known_labels = {str(row.get("label") or "").casefold() for row in reason_controls}
            for index, enemy in enumerate(self._game_state.enemies, start=1):
                eid = enemy.id
                if is_frame_mark_token(eid):
                    eid = f"enemy_{index}"
                if eid.casefold() in known_ids:
                    continue
                if enemy.targetable.value is True and enemy.label.value is None:
                    reason_controls.append({
                        "label": "unidentified_enemy",
                        "role": "enemy",
                        "confidence": "high",
                        "entity_id": eid,
                        "inspectable": True,
                        "source": "authoritative_state",
                    })
                    known_ids.add(eid.casefold())
                    known_labels.add("unidentified_enemy")
        screen_key = str(scene_desc.screen or resolved_screen or "").strip().lower()
        if ran_llm1 and screen_key == "combat":
            self._cached_perceive_scene = scene_desc
            self._cached_perceive_scene_step = int(self._step_id or 0)
        elif screen_key != "combat":
            self._cached_perceive_scene = None
            self._cached_perceive_scene_step = None
        self._sync_legal_actions()
        if not getattr(scene_desc, "schema_valid", True):
            _say("  perceive schema invalid — skipping reasoner (CV/heuristic fallback)")
            if perceive_call is not None:
                self._capture_perception_audit(scene_desc)
            return {}, perceive_ms, 0.0, scene_desc
        if self.fixture.id == "sts2":
            # ACT consumes one compact typed snapshot.  It never receives the
            # screenshot, frame marks, coordinates, notebook prose, or MCP
            # catalog; planning and input remain deterministic runtime work.
            snapshot = self._build_turn_snapshot(scene_desc)
            parsed_out, reason_ms = self._run_v2_act(snapshot)
            self._think_ms.append(perceive_ms + reason_ms)
            return parsed_out, perceive_ms, reason_ms, self._scene_desc or scene_desc
        listing = self._aim.listing() if self._aim is not None else None
        self._register_turn_tools(
            listing=listing,
            host_screen=host_screen,
        )
        host_hud = format_host_hud_block(hud=self._obs_metrics, host_stats=self._host_stats)
        detector_labels = (
            self._frame_model.interactive_labels() if self._frame_model is not None else []
        )
        self._reason_controls = reason_controls
        state = reason_state_from_host(
            screen=str(scene_desc.screen or host_screen or self._host_stats.get("screen") or ""),
            turn=str(scene_desc.turn or self._host_stats.get("turn") or "unknown"),
            tutorial_info=scene_desc.tutorial,
        )
        hud_stats = reason_hud_stats(hud=self._obs_metrics, host_stats=self._host_stats)
        journal_parts: list[str] = [self._state_prompt_block()]
        semantic_rules = self.semantic_memory.tutorial_rules(verified_only=True)
        if semantic_rules:
            journal_parts.append("Verified tutorial rules: " + " | ".join(
                f"{row.value.get('trigger')} => {row.value.get('action')} ({row.value.get('effect')})"
                for row in semantic_rules[:4]
            ))
        if journal_block:
            journal_parts.append(strip_mark_leaks(journal_block))
        if notebook_block:
            journal_parts.append(strip_mark_leaks(notebook_block))
        reason_user = reason_turn_user_prompt(
            state=state,
            hud_stats=hud_stats,
            controls=reason_controls,
            visible_text=_bounded_visible_text(scene_desc.informational_text()),
            last_outcome=self._last_outcome,
            journal=_bounded_reason_journal(journal_parts) if journal_parts else None,
        )
        reason_user += (
            f"\nPerception schema: {self._perception_schema_version()}."
            f"\nRuntime route: ACT ({route_reason})."
        )
        reason_user = _bounded_reason_user(reason_user)
        self.last_user_prompt = reason_user
        retry_state: dict[str, Any] = {"invalid_choice_count": 0}
        tool_result = None
        reason_frames = getattr(self, "_reason_vlm_frames", None)
        while True:
            tool_result = self._tool_orchestrator.run(
                self.gateway,  # type: ignore[arg-type]
                system=reason_system,
                user=reason_user,
                table_name=self.fixture.action_table,
                reason_max_tokens=self.config.reason_max_tokens,
                host_hud_injected=bool(host_hud),
                frames=reason_frames,
            )
            parsed = tool_result.parsed
            if str(parsed.get("tool") or "").strip():
                break
            allowed_controls, allowed_targets = allowed_sets_from_controls(
                getattr(self, "_reason_controls", None) or reason_controls
            )
            inspectable_targets = inspectable_targets_from_controls(
                getattr(self, "_reason_controls", None) or reason_controls
            )
            attack_cards = attack_cards_from_controls(
                getattr(self, "_reason_controls", None) or reason_controls
            )
            choice_ids, target_ids, dup_labels = instance_ids_from_controls(
                getattr(self, "_reason_controls", None) or reason_controls
            )
            validated = validate_choice(
                parsed,
                allowed_controls,
                allowed_targets,
                retry_state,
                selected=self._selected if str(
                    self._obs_metrics.get("screen")
                    or self._host_stats.get("screen")
                    or ""
                ).strip().lower() == "combat" else None,
                attack_cards=attack_cards,
                inspectable_targets=inspectable_targets,
                choice_ids=choice_ids,
                target_ids=target_ids,
                duplicate_choice_labels=dup_labels,
            )
            if validated["type"] in {"tool_call", "click", "inspect"}:
                break
            if validated["type"] == "abort":
                self._last_outcome = str(validated.get("reason") or "")
                break
            if validated["type"] == "retry":
                reason_user = _bounded_reason_user(
                    reason_user + "\n\n" + str(validated.get("reason") or "")
                )
                continue
            if validated["type"] == "force_rescan":
                focus = str(validated.get("focus") or validated.get("reason") or "")
                self._last_outcome = str(validated.get("reason") or "")
                try:
                    recheck_ms.append(self._rescan_scene(focus=focus))
                except Exception:  # noqa: BLE001 — rescan is best-effort
                    break
                scene_desc = self._scene_desc or scene_desc
                reason_controls = self._finalize_reason_controls(reason_controls_for_turn(
                    controls=scene_desc.controls,
                    uncertain=scene_desc.uncertain,
                    enemies=(scene_desc.combat or {}).get("enemies"),
                    detector_rows=self._detector_typed_rows(),
                    tutorial_info=scene_desc.tutorial,
                    host_stats=self._host_stats,
                ))
                state = reason_state_from_host(
                    screen=str(scene_desc.screen or host_screen or self._host_stats.get("screen") or ""),
                    turn=str(scene_desc.turn or self._host_stats.get("turn") or "unknown"),
                    tutorial_info=scene_desc.tutorial,
                )
                reason_user = reason_turn_user_prompt(
                    state=state,
                    hud_stats=hud_stats,
                    controls=reason_controls,
                    visible_text=_bounded_visible_text(scene_desc.informational_text()),
                    last_outcome=self._last_outcome,
                    journal=_bounded_reason_journal(journal_parts) if journal_parts else None,
                )
                reason_user = _bounded_reason_user(reason_user)
                self.last_user_prompt = reason_user
                continue
            break
        reason_ms = tool_result.latency_ms
        perceive_ms += sum(recheck_ms)
        self._turn_reasoner_called = True
        self._last_reason_raw = tool_result.text
        self._last_tool_trace = list(tool_result.tool_trace)
        self._last_tool_events = list(tool_result.tool_events)
        self._think_ms.append(perceive_ms + reason_ms)
        parsed_out = dict(tool_result.parsed)
        if reason_route == "act":
            tool = ToolRegistry.normalize_tool_name(str(parsed_out.get("tool") or ""))
            if tool in {"inspect_object", "request_recheck"}:
                # Act cannot acquire evidence. Fail closed as a clean recheck
                # turn instead of allowing a malformed action or generic fill.
                self._append_collector_audit({
                    "tool": tool,
                    "status": "rejected",
                    "reason": "act-route-observation-tool",
                    "stage": "reason-route",
                })
                parsed_out = {
                    "tool": "request_recheck",
                    "thought": "ACT requested observation; reroute next turn",
                }
        elif reason_route == "explore":
            tool = ToolRegistry.normalize_tool_name(str(parsed_out.get("tool") or ""))
            event_tools = {
                ToolRegistry.normalize_tool_name(str(event.get("tool") or ""))
                for event in tool_result.tool_events
            }
            # Explore may only inspect or recheck.  If the model answered with
            # a free-form click after a recheck round, preserve the recheck as
            # an observation-only turn rather than allowing empty-chord fill.
            if "request_recheck" in event_tools:
                parsed_out = {"tool": "request_recheck", "thought": parsed_out.get("thought") or "explore: recheck"}
            elif tool not in {"inspect_object", "request_recheck"}:
                # Keep the downstream turn explicitly observation-only.  In
                # particular, do not leave an empty chord that fill_from_aim
                # could turn into a generic click.
                parsed_out = {"tool": "request_recheck", "thought": parsed_out.get("thought") or "explore: no confirmed action"}
            elif tool == "request_recheck":
                parsed_out = {"tool": "request_recheck", "thought": parsed_out.get("thought") or "explore: recheck"}
        self._log_combat_decision(parsed_out)
        return parsed_out, perceive_ms, reason_ms, self._scene_desc or scene_desc

    def _wait_actable(self, t_end: float) -> tuple[Any, str]:
        """Thumb-poll until the screen is quiet and act_gate is not enemy-turn.

        Does not JPEG-encode or call the VLM. Returns the last full grab and gate label.
        """
        image = self.fixture.grab_image()
        gate = "unknown"
        timeout_ms = int(getattr(self.fixture, "quiet_timeout_ms", 8000) or 8000)
        profile = getattr(self.fixture, "profile", None)
        threshold = float(getattr(profile, "diff_threshold", 0.02) or 0.02)
        quiet_threshold = max(threshold, 0.04)

        def grab_thumb() -> Any:
            return roi_thumb(self.fixture.grab_image(), 64)

        while time.perf_counter() < t_end and not stop_requested():
            remain_ms = int(max(0.0, (t_end - time.perf_counter()) * 1000.0))
            if remain_ms < 80:
                break
            result = wait_until_quiet(
                grab_thumb,
                threshold=quiet_threshold,
                timeout_ms=min(timeout_ms, remain_ms),
                poll_ms=80,
                quiet_ms=200,
                should_stop=stop_requested,
            )
            image = self.fixture.grab_image()
            view = self.fixture.observe_image(image)
            gate_fn = getattr(self.fixture, "act_gate", None)
            gate = gate_fn(view) if callable(gate_fn) else "unknown"
            if gate != "theirs":
                if result.polls:
                    self._last_quiet_ms = float(result.elapsed_ms)
                    self._last_quiet_polls = int(result.polls)
                    _say(
                        f"  wait     quiet={result.elapsed_ms:.0f}ms polls={result.polls} gate={gate}"
                    )
                return image, gate
            _say(
                f"  wait     enemy turn  quiet={result.elapsed_ms:.0f}ms — not thinking yet"
            )
            self._live(phase="waiting", message="enemy turn", thought="")
        return image, gate

    def _confirm_after_action(self, *, tag: str = "", max_ms: int | None = None) -> Any:
        """Wait for post-click animation to settle, then grab a confirmation frame."""
        timeout_ms = int(getattr(self.fixture, "quiet_timeout_ms", 8000) or 8000)
        confirm_cap_ms = int(getattr(self.fixture, "confirm_timeout_ms", 3000) or 3000)
        if max_ms is not None:
            confirm_cap_ms = min(confirm_cap_ms, max(1, int(max_ms)))
        profile = getattr(self.fixture, "profile", None)
        threshold = float(getattr(profile, "diff_threshold", 0.02) or 0.02)
        quiet_threshold = max(threshold, 0.04)
        quiet_ms = int(getattr(self.fixture, "confirm_quiet_ms", 400) or 400)

        def grab_thumb() -> Any:
            return roi_thumb(self.fixture.grab_image(), 64)

        result = wait_until_quiet(
            grab_thumb,
            threshold=quiet_threshold,
            timeout_ms=min(timeout_ms, confirm_cap_ms),
            poll_ms=80,
            quiet_ms=quiet_ms,
            should_stop=stop_requested,
        )
        if result.polls and tag:
            _say(f"  {tag} confirm  quiet={result.elapsed_ms:.0f}ms polls={result.polls}")
        return self.fixture.grab_image()

    def _refresh_obs_after_action(self, after: Any, *, prev_metrics: dict[str, Any]) -> dict[str, Any]:
        """Sync _last_obs_image and aim from a post-action confirmation frame."""
        view = self.fixture.observe_image(after)
        self._last_obs_image = view
        if self._two_stage():
            self._prev_host_stats = dict(self._host_stats) if self._host_stats else {}
            self._host_stats = self._read_host_stats(view)
            self._obs_metrics = (
                stats_to_score_metrics(self._host_stats)
                if str(self._host_stats.get("screen") or "") == "combat"
                else self.fixture.scorer(view)
            )
        else:
            self._obs_metrics = self.fixture.scorer(view)
        aim_screen = (
            str(self._obs_metrics.get("screen") or prev_metrics.get("screen") or "") or None
        )
        reuse = self._perception_reuse(view, aim_screen=aim_screen)
        words, marks, gui_marks, _ = self._build_turn_marks(
            view, aim_screen=aim_screen, reuse=reuse
        )
        if self._two_stage():
            # A card transaction changes mark ids and coordinates. Publish the
            # refreshed inventory before target rebinding.
            stamped = self._screen_objects.save_frame(
                run_id=self.session_id,
                step_id=self._step_id,
                marks=marks,
                screen=aim_screen,
                source="post_action",
            )
            self._aim = AimGuide.from_marks(view, stamped, last_xy=self._last_click)
            self._build_frame_model()
            # Never project stale pre-click scene bboxes onto a fresh sensor
            # frame. ScreenObjectStore may carry identity, but geometry remains
            # exclusively owned by this post-action observation.
            if self._combat_screen_now() and self._scene_desc is not None:
                controls = getattr(self._scene_desc, "controls", None) or []
                typed_enemies = list(getattr(self._scene_desc, "enemies", None) or [])
                typed_characters = list(getattr(self._scene_desc, "characters", None) or [])
                enemies = [
                    row for row in typed_enemies + typed_characters + list(controls)
                    if isinstance(row, dict)
                    and str(row.get("role") or "") in {"enemy", "unknown_character", "character"}
                    and not is_player_row(row)
                ]
                labeled = ground_combat_labels(
                    self._aim.marks,
                    enemies=enemies,
                    # The pre-action hand is consumable state. Reusing it can
                    # relabel a remaining card (or transient animation blob)
                    # as the card that just left the hand. Current OCR owns
                    # post-action card labels; only durable enemy identity is
                    # transferred across the transaction boundary.
                    hand=[],
                    screen="combat",
                )
                if labeled != list(self._aim.marks):
                    stamped = self._screen_objects.save_frame(
                        run_id=self.session_id,
                        step_id=self._step_id,
                        marks=labeled,
                        screen=aim_screen,
                        source="post_action_labels",
                    )
                    self._aim = AimGuide.from_marks(view, stamped, last_xy=self._last_click)
                    self._build_frame_model()
        else:
            self._aim = build_aim(
                view,
                last_xy=self._last_click,
                lexicon=self.journal.lexicon,
                screen=aim_screen,
                words=words,
                gui_marks=gui_marks,
            )
        if getattr(self, "_planned_hand_signature", None) is not None and self._hand_inventory_mutated_since_plan():
            planned = self._planned_hand_signature
            current = self._hand_inventory_signature()
            reason = "hand-reflow"
            if current[0] != planned[0]:
                reason = "screen-transition"
            elif current[1] != planned[1]:
                reason = "energy-change"
            else:
                planned_cards = planned[2] if len(planned) > 2 else ()
                current_cards = current[2]
                planned_ids = tuple(row[0] for row in planned_cards)
                current_ids = tuple(row[0] for row in current_cards)
                if current_ids != planned_ids or len(current_cards) != len(planned_cards):
                    reason = "hand-membership"
                elif any(
                    abs(float(left[2]) - float(right[2])) >= 0.04
                    for left, right in zip(current_cards, planned_cards)
                ):
                    reason = "column-shift"
            self._invalidate_hand_inventory(reason=reason)
        self._track_combat_index()
        refreshed = self._maybe_apply_combat_exit_transition(dict(self._obs_metrics))
        self._obs_metrics = refreshed
        if refreshed.get("combat_won") and self._last_obs_image is not None:
            self._rebuild_reward_inventory(self._last_obs_image, screen="reward")
        return dict(refreshed)

    def _discovery_contract_for_action(self, action: ActionLog) -> Any | None:
        """Freeze current mark geometry before a buttonless inspection move."""
        if not action.hover or action.mark is None or self._aim is None:
            return None
        mark = self._aim.by_id(int(action.mark))
        if mark is None:
            return None
        role = str(mark.role or "").strip().lower()
        if role not in {"enemy", "card", "control"}:
            role = "card" if mark.y >= 0.70 else "enemy" if in_combat_sprite_band(mark.y, mark.x) else "control"
        item = DiscoveryObject(
            object_id=str(mark.object_id or mark.label or f"mark_{mark.id}"),
            mark_id=int(mark.id), role=role,
            bbox=(float(mark.x0), float(mark.y0), float(mark.x1), float(mark.y1)),
            target=(float(mark.x), float(mark.y)), label=str(mark.label or ""),
            confidence=float(mark.confidence or mark.score or 0.0),
        )
        contract = inspection_contract(run_id=self.session_id, step_id=self._step_id, item=item)
        # Current ScreenObjectStore is frame-local. Refuse artifact binding if
        # grounding already replaced this mark rather than approximating it.
        if not validate_contract(contract, self._screen_objects.objects):
            return None
        oid = str(item.object_id or "").strip()
        if oid:
            self._discovery_attempts[oid] = self._discovery_attempts.get(oid, 0) + 1
        lab = str(item.label or "").strip()
        if lab and lab.casefold() != oid.casefold():
            self._discovery_attempts[lab] = self._discovery_attempts.get(lab, 0) + 1
        return contract

    _DISCOVERY_BUDGET = 6
    _DISCOVERY_SETTLE_MS = 220
    _DISCOVERY_PARK = (0.50, 0.92)

    def _discovery_hover_to(self, nx: float, ny: float) -> None:
        """Buttonless move. Bypass affordance/legal-chord; CV brands hand cards illegal."""
        controller = getattr(self.fixture, "controller", None)
        if controller is None:
            return
        prim = mouse_move(float(nx), float(ny))
        remap = getattr(self.fixture, "_remap", None)
        if callable(remap):
            prim = remap(prim)
        controller.release_all()
        controller.execute((prim,))

    def _wait_discovery_roi(self, bbox: tuple[float, float, float, float]):
        """Short padded-bbox settle. No full-client quiet wait, no verify retries."""
        grab = self.fixture.grab_image
        observe = self.fixture.observe_image
        settle_ms = int(getattr(self, "_DISCOVERY_SETTLE_MS", 220) or 0)
        if settle_ms <= 0 or getattr(self.fixture, "controller", None) is None:
            return observe(grab())
        timeout = settle_ms / 1000.0
        t0 = time.perf_counter()
        prev = None
        view = observe(grab())
        while (time.perf_counter() - t0) < timeout:
            view = observe(grab())
            try:
                thumb = roi_thumb(crop(view, _pad(bbox, 0.04)), 64)
            except Exception:  # noqa: BLE001 — fall back to full-frame thumb
                thumb = roi_thumb(view, 64)
            if prev is not None and mean_absdiff(prev, thumb) < 0.015:
                return view
            prev = thumb
            time.sleep(0.04)
        return view

    def _patch_scene_identity(self, scene_desc: Any, contract: Any, name: str) -> None:
        oid = str(getattr(contract, "object_id", "") or "")
        cx, cy = getattr(contract, "target", (0.0, 0.0))
        role = str(getattr(contract, "role", "") or "")

        def _hit(row: dict[str, Any]) -> bool:
            eid = str(row.get("entity_id") or row.get("id") or "")
            if oid and eid == oid:
                return True
            center = _row_center(row)
            if center is None:
                return False
            return abs(center[0] - float(cx)) < 0.12 and abs(center[1] - float(cy)) < 0.12

        for bucket_name in ("enemies", "controls", "hand_cards", "characters"):
            bucket = getattr(scene_desc, bucket_name, None) or []
            for row in bucket:
                if not isinstance(row, dict) or not _hit(row):
                    continue
                row["label"] = name
                row["name"] = name
                row["inspectable"] = False
                row["identified"] = True
                row["name_source"] = "hover_ocr"
                if role == "enemy" or bucket_name == "enemies":
                    row["role"] = "enemy"

    def _bind_discovery_name(self, result: DiscoveryResult, scene_desc: Any | None = None) -> None:
        name = str(result.name or "").strip()
        if not name:
            return
        oid = str(result.contract.object_id or "")
        cx, cy = result.contract.target
        tracker = getattr(self, "_entity_tracker", None)
        if tracker is not None:
            if oid:
                tracker.identify(oid, name)
            tracker.identify_at(float(cx), float(cy), name)
        store = getattr(self, "_screen_objects", None)
        if store is not None and oid and hasattr(store, "relabel"):
            store.relabel(oid, name)
        desc = scene_desc or getattr(self, "_scene_desc", None)
        if desc is not None:
            self._patch_scene_identity(desc, result.contract, name)
            if tracker is not None:
                tracker.merge_into_scene(desc, self._host_stats, lexicon=None, screen="combat")
            self._scene_desc = desc
            if str(getattr(desc, "screen", "") or "").strip().lower() == "combat":
                self._cached_perceive_scene = desc
        memory = getattr(self, "semantic_memory", None)
        remember = getattr(memory, "remember_identity", None) if memory is not None else None
        if callable(remember):
            remember(
                screen="combat",
                role=result.contract.role or "enemy",
                name=name,
                provenance="hover_ocr",
                confidence=0.9,
                verified=True,
                step_id=int(getattr(self, "_step_id", 0) or 0),
            )

    def _refresh_reason_after_discovery(self, scene_desc: Any) -> None:
        labels: list[str] = []
        clickable = getattr(self, "_clickable_labels", None)
        if callable(clickable):
            try:
                labels = list(clickable() or [])
            except Exception:  # noqa: BLE001 — labels are optional for ACT stamp
                labels = []
        self._reason_controls = self._finalize_reason_controls(reason_controls_for_turn(
            controls=scene_desc.controls,
            uncertain=getattr(scene_desc, "uncertain", None),
            enemies=(getattr(scene_desc, "combat", None) or {}).get("enemies"),
            detector_rows=self._detector_typed_rows(),
            tutorial_info=getattr(scene_desc, "tutorial", None),
            host_stats=getattr(self, "_host_stats", None) or {},
        ))
        _route, reason = self._two_stage_reason_route(scene_desc)
        self._route_after_discovery = f"act ({reason})"

    def _park_discovery_cursor(self) -> None:
        self._discovery_hover_to(*self._DISCOVERY_PARK)
        try:
            parked = self.fixture.observe_image(self.fixture.grab_image())
        except Exception:  # noqa: BLE001 — park is best-effort
            return
        self._last_obs_image = parked
        try:
            painted = parked
            if self._aim is not None:
                painted = paint_aim(parked, marks=self._aim.marks, last_xy=self._last_click)
            self._reason_vlm_frames = [encode_image(painted, longest_side=1280, jpeg_quality=90)]
        except Exception:  # noqa: BLE001 — ACT can keep the pre-hover frame
            return

    def _run_mechanical_discovery(self, scene_desc: Any) -> float:
        """Same-turn hover+crop OCR. Does not call the reasoner or LLM1."""
        t0 = time.perf_counter()
        if not self._combat_screen_now():
            return 0.0
        store = getattr(self, "_screen_objects", None)
        if store is None:
            return 0.0
        energy = (getattr(self, "_obs_metrics", None) or {}).get("energy")
        try:
            energy_i = int(energy) if energy is not None else None
        except (TypeError, ValueError):
            energy_i = None
        hovered = False
        inspections = 0
        attempts = getattr(self, "_discovery_attempts", None)
        if attempts is None:
            self._discovery_attempts = {}
            attempts = self._discovery_attempts
        limit = self._DISCOVERY_ATTEMPT_LIMIT
        remaining: list[DiscoveryObject] = []
        while inspections < self._DISCOVERY_BUDGET:
            try:
                queue = inventory_unknowns(store.objects, energy=energy_i)
            except (TypeError, ValueError):
                break
            if remaining:
                queue = rebind_unknowns(remaining, store.objects)
            item = None
            for cand in queue:
                oid = str(cand.object_id or "")
                lab = str(cand.label or "")
                if attempts.get(oid, 0) >= limit or (lab and attempts.get(lab, 0) >= limit):
                    continue
                item = cand
                break
            if item is None:
                break
            inspections += 1
            oid = str(item.object_id or "")
            lab = str(item.label or "")
            if oid:
                attempts[oid] = attempts.get(oid, 0) + 1
            if lab and lab.casefold() != oid.casefold():
                attempts[lab] = attempts.get(lab, 0) + 1
            remaining = [cand for cand in queue if cand.object_id != item.object_id]
            try:
                before = self.fixture.observe_image(self.fixture.grab_image())
            except Exception:  # noqa: BLE001 — skip this inspect if capture fails
                continue
            self._discovery_hover_to(item.target[0], item.target[1])
            hovered = True
            after = self._wait_discovery_roi(item.bbox)
            contract = inspection_contract(
                run_id=str(getattr(self, "session_id", "") or "run"),
                step_id=int(getattr(self, "_step_id", 0) or 0),
                item=item,
            )
            result = self._discovery.inspect_frames(
                contract, before=before, after_frames=[after],
            )
            self._last_discovery_result = result
            if result.status == "identified" and result.name:
                self._bind_discovery_name(result, scene_desc)
        if hovered:
            self._park_discovery_cursor()
        if scene_desc is not None:
            self._scene_desc = scene_desc
            if str(getattr(scene_desc, "screen", "") or "").strip().lower() == "combat":
                self._cached_perceive_scene = scene_desc
            self._refresh_reason_after_discovery(scene_desc)
        return (time.perf_counter() - t0) * 1000.0

    def _next_discovery_action(self) -> ActionLog | None:
        """Choose one unresolved, current-frame object; never inspect a selected card."""
        if self._selected:
            return None
        try:
            energy = self._obs_metrics.get("energy")
            queue = inventory_unknowns(
                self._screen_objects.objects,
                energy=int(energy) if energy is not None else None,
            )
        except (TypeError, ValueError):
            return None
        attempts = getattr(self, "_discovery_attempts", None) or {}
        limit = self._DISCOVERY_ATTEMPT_LIMIT
        for item in queue:
            oid = str(item.object_id or "")
            lab = str(item.label or "")
            if attempts.get(oid, 0) >= limit or (lab and attempts.get(lab, 0) >= limit):
                continue
            action = ActionLog(
                mark=item.mark_id, click_x=item.target[0], click_y=item.target[1],
                hover=True, duration_ms=self.config.burst_ms,
            )
            action.name = action.derive_name()
            return action
        return None

    def _record_discovery_hover(self, contract: Any, before: Any, after: Any) -> DiscoveryResult:
        """Persist full frame, targeted crops, and OCR tied to one active object."""
        before_view = self.fixture.observe_image(before)
        after_view = self.fixture.observe_image(after)
        result = self._discovery.inspect_frames(
            contract, before=before_view, after_frames=[after_view]
        )
        self._last_discovery_result = result
        return result

    def _reperceive_after_hover(
        self,
        after: Any,
        *,
        hovered_label: str = "",
        tag: str = "",
        discovery: DiscoveryResult | None = None,
    ) -> None:
        """Bind crop-OCR identity after a leftover hover. Do not re-run LLM1."""
        del after
        self._force_inspect = False
        name = ""
        if discovery is not None and discovery.status == "identified" and discovery.name:
            name = str(discovery.name)
            self._bind_discovery_name(discovery, getattr(self, "_scene_desc", None))
            desc = getattr(self, "_scene_desc", None)
            if desc is not None:
                self._refresh_reason_after_discovery(desc)
        if tag:
            _say(f"  {tag} hover    ocr-bind {name or hovered_label or 'unresolved'}")
        if name:
            _combat_trace("DISCOVERY", f"bound={name} source=hover_ocr")

    def _use_heuristic(self) -> bool:
        if self.config.policy == "heuristic":
            return True
        return bool(self._turn_based() and (self.config.skip_models or self.gateway is None))

    def _step_policy_id(self) -> str:
        if self._use_heuristic():
            return HEURISTIC_POLICY_ID
        if self.config.skip_models:
            return "skip-models"
        return self.config.policy_id

    def _dump_all(self) -> bool:
        return self.config.arm == "dump_all" or (self.config.memory and self.config.arm != "ratchet")

    def _dino_labels(self) -> list[str]:
        if self.config.dino_labels is not None:
            return [lab for lab in self.config.dino_labels if lab]
        return visual_labels_for(self.fixture.id)

    def _ensure_detector(self) -> Any | None:
        if not self.config.dino:
            return None
        if self._detector is not None:
            return self._detector
        if self.config.skip_models or self._dino_loaded:
            return None
        labels = self._dino_labels()
        if not labels and not self._turn_based() and not self.config.dino_required:
            self._dino_loaded = True
            return None
        self._detector = load_detector(
            device=self.config.dino_device, model_id=self.config.dino_model
        )
        self._dino_loaded = True
        family = str(getattr(self._detector, "family", "") or "unavailable")
        if family == "unavailable":
            err = getattr(self._detector, "last_error", "") or ""
            _say(f"dino unavailable; collect continues without overlay. {err}".rstrip())
        else:
            _say(f"dino {family} model={self.config.dino_model} device={self.config.dino_device} labels={len(labels)}")
        return self._detector

    def _ensure_gui(self) -> None:
        from loop.os.gui import configure_gui

        if not self.config.gui:
            self._gui_status = configure_gui(enabled=False)
            return
        self._gui_status = configure_gui(
            enabled=True,
            device=self.config.gui_device,
            model=self.config.gui_model,
            download=self.config.gui_download,
        )

    def _start_hud_poller(self) -> None:
        if self.fixture.id == "fake" or self._turn_based() or self.config.hud_poll_s <= 0:
            return

        def _update(metrics: dict[str, Any], visible: bool) -> None:
            if visible == self._hud_seen:
                return
            self._hud_seen = visible
            _say(f"  hud {'on' if visible else 'off'}  {_hud_line(metrics)}")
            self._live(
                phase="hud",
                metrics=metrics,
                message="hud on" if visible else "hud off",
            )

        self._hud_poller = HudPoller(
            self.fixture.grab_image,
            self.fixture.scorer,
            interval_s=self.config.hud_poll_s,
            on_update=_update,
            should_stop=stop_requested,
        )
        self._hud_poller.start()

    def _build_session_summary(self) -> SessionSummary:
        n = len(self._episode_returns)
        return SessionSummary(
            session_id=self.session_id,
            fixture=self.fixture.id,
            episodes=n,
            unique_device_names=sorted(self._all_actions),
            memory_size=0 if self.memory is None else self.memory.size(),
            mean_return=(sum(self._episode_returns) / n if n else 0.0),
            fail_rate=(sum(1 for v in self._episode_returns if v < 0) / n if n else 0.0),
            mean_think_ms=(sum(self._think_ms) / len(self._think_ms) if self._think_ms else None),
            audit_flags=sorted(set(self._session_flags)),
            policy_id=self._step_policy_id(),
            arm=self.config.arm,
            skill_on_frac=(
                sum(self._skill_on_flags) / len(self._skill_on_flags)
                if self._skill_on_flags
                else None
            ),
            success_rate=(sum(self._episode_success) / n if n else None),
            interrupt_cv=self._interrupt_cv,
            interrupt_dino=self._interrupt_dino,
        )

    def _finalize_session(self, summary: SessionSummary | None = None) -> SessionSummary:
        if self._session_finalized and self._final_summary is not None:
            return self._final_summary

        flush_ok = True
        try:
            self.journal.save()
        except OSError:
            flush_ok = False

        try:
            self.notebook.save_grounded(self._grounded_path)
        except OSError:
            flush_ok = False

        screen_store = self._screen_objects
        if screen_store is not None:
            try:
                screen_store._conn.commit()
                screen_store.close()
            except Exception:
                flush_ok = False
            self._screen_objects = None

        if summary is None:
            summary = self._build_session_summary()
        summary.stop_requested = bool(self._stopped)
        summary.flush_completed = flush_ok
        try:
            self.store.write_summary(summary)
        except OSError:
            summary.flush_completed = False

        self._session_finalized = True
        self._final_summary = summary
        return summary

    def run_session(self) -> SessionSummary:
        if self._turn_based():
            self.config.survey = False
            self.config.hud_poll_s = 0.0
        if self.config.gate_black and self.fixture.id != "fake":
            probe = self.fixture.grab_image()
            if is_black_frame(probe):
                raise RuntimeError(BLACK_FRAME_HINT)
            check = getattr(self.fixture, "check_geometry", None)
            if callable(check):
                check(probe)
            geo = getattr(self.fixture, "geometry_line", None)
            if callable(geo):
                _say("view  " + str(geo(probe)))
        self._stopped = False
        self._session_finalized = False
        self._final_summary = None
        self._start_hud_poller()
        self._ensure_detector()
        if self.config.dino_required:
            family = str(getattr(self._detector, "family", "") or "unavailable")
            if not self.config.dino or family != "grounding-dino":
                detail = str(getattr(self._detector, "last_error", "") or "").strip()
                raise RuntimeError(
                    "Grounding DINO is required for this collection but was not loaded "
                    f"(family={family}). Install the project dependencies and verify "
                    "with `python scripts/smoke_dino.py --window \"Slay the Spire 2\"`. "
                    f"{detail}".rstrip()
                )
        self._ensure_gui()
        self._announce_session()
        self._require_primary_pad()
        self._live(phase="session", message="starting", thought="")
        try:
            for _ in range(self.config.lives):
                if stop_requested():
                    self._stopped = True
                    break
                episode = self.run_episode()
                if self._stopped:
                    break
                if self.config.until_fail and episode.terminal in {"fail", "win"}:
                    break
        finally:
            if self._hud_poller is not None:
                self._hud_poller.stop()
                self._hud_poller = None
            summary = self._finalize_session()
        _say(
            f"{'stopped' if self._stopped else 'session complete'}  "
            f"episodes={summary.episodes}  mean_return={summary.mean_return:.3f}"
        )
        self._live(
            phase="stopped" if self._stopped else "done",
            message="stopped" if self._stopped else "session complete",
            thought="",
        )
        return summary

    def _announce_session(self) -> None:
        ctl = self.fixture.controller
        pad = "keyboard"
        window = "fake"
        size = ""
        if ctl is not None:
            target = ctl.target
            window = getattr(target, "title", None) or "window"
            size = f"  {target.width}x{target.height}"
            pad = "xbox360" if ctl.gamepad is not None else "keyboard"
        _say(
            f"collect {self.fixture.id}  arm={self.config.arm}  "
            f"lives={self.config.lives}  table={self.fixture.action_table}  "
            f"pad={pad}  pace={getattr(self.fixture, 'pace', 'realtime')}"
        )
        _say(f"window={window!r}{size}")
        if self.config.until_fail:
            _say(
                f"until-fail  max_steps={self.config.max_steps}  "
                f"timeout_s={self.config.timeout_s:.0f}  stop on death/win"
            )
        if self._turn_based():
            if self.config.dino:
                family = str(getattr(self._detector, "family", "") or "off") if self._detector else "off"
                _say(
                    f"dino={family} device={self.config.dino_device} "
                    f"model={self.config.dino_model} (STS2 query-list)"
                )
            else:
                _say("dino=off (turn-based — pass --dino to enable query-list grounding)")
        elif self.config.dino:
            family = str(getattr(self._detector, "family", "") or "off") if self._detector else "off"
            _say(
                f"dino={family} device={self.config.dino_device} "
                f"model={self.config.dino_model} "
                f"labels={len(self._dino_labels())}"
            )
        else:
            _say("dino=off")
        _say(self._gui_status)
        if ctl is not None and ctl.gamepad is not None:
            _say(pad_debug_line(ctl.gamepad))
        _say(f"run={self.store.run_dir}")
        from loop.os.ocr import engine_name

        ocr_name = engine_name()
        if ocr_name == "off":
            _say("ocr=off  pip install rapidocr onnxruntime (or Tesseract)")
        else:
            _say(f"ocr={ocr_name}")
        if self._turn_based():
            _say("F12 panics. Game is focused before every click.")
            if self._two_stage():
                if self.fixture.id == "sts2":
                    _say(
                        "architecture=two_stage  decision_schema=v2  "
                        f"debug_overlay={self.config.debug_overlay}"
                    )
                else:
                    _say(
                        f"architecture=two_stage  tool_rounds={self.config.max_tool_rounds}  "
                        f"debug_overlay={self.config.debug_overlay}"
                    )
            else:
                _say("architecture=overlay")
        else:
            _say("F12 panics. Game is focused before every pad chord.")

    def _require_primary_pad(self) -> None:
        ctl = self.fixture.controller
        if self._turn_based() or self.fixture.id == "fake" or ctl is None or ctl.gamepad is None:
            return
        slot = identify_our_slot(ctl.gamepad)
        connected = xinput_connected()
        if slot == 0:
            return
        raise RuntimeError(f"{PAD_SLOT_HINT} (slot={slot}, connected={connected})")

    def _schedule_skill(self) -> tuple[bool, Skill | None]:
        if self.config.arm != "ratchet" or self.skills is None:
            return False, None
        chosen = self.skills.choose_active(self.rng)
        if chosen is None:
            return False, None
        skill_on = self.rng.random() >= self.config.skill_off_frac
        return skill_on, chosen

    def _explore_chord(self, *, stall: bool, prompt_on: bool, lock: bool = False) -> tuple[ActionLog, int]:
        action, done = self._explore.next_action(
            stall=stall,
            prompt_on=prompt_on,
            lock=lock,
            burst_ms=self.config.burst_ms,
        )
        ticks = self._explore.ticks
        if done:
            self._explore.reset()
            self._gsm_busy = False
        else:
            self._gsm_busy = True
        return action, ticks

    def _cancel_explore(self) -> None:
        self._explore.reset()
        self._gsm_busy = False

    def _approach_chord(
        self,
        *,
        box_cx: float | None,
        confirmed: bool,
        locked: bool,
    ) -> tuple[ActionLog, int, bool]:
        action, done = self._approach.next_action(
            box_cx=box_cx,
            confirmed=confirmed,
            locked=locked,
            burst_ms=self.config.burst_ms,
        )
        ticks = self._approach.ticks
        if done:
            self._approach.reset()
        return action, ticks, done

    def _survey_chord(self) -> tuple[ActionLog, int, bool]:
        action, done = self._survey.next_action(burst_ms=self.config.burst_ms)
        ticks = self._survey.ticks
        if done:
            self._survey_done = True
            self._survey.reset()
        return action, ticks, done

    def run_episode(self) -> EpisodeLog:
        episode_id = uuid.uuid4().hex[:10]
        steps: list[StepLog] = []
        cumulative = 0.0
        prev_metrics: dict[str, Any] = {}
        menu_streak = 0
        t_end = time.perf_counter() + self.config.timeout_s
        wall_timeout = False
        terminal: Literal["alive", "fail", "win", "timeout"] = "alive"
        action_round_robin = names(self.fixture.action_table)
        skill_on, active_skill = self._schedule_skill()
        active_skill_id = active_skill.id if active_skill is not None else None
        self._skill_on_flags.append(skill_on)
        dump_all = self._dump_all()
        self._episode_n += 1
        recent_chords: list[str] = []
        last_pixels_changed: bool | None = None
        last_mad: float | None = None
        last_had_prompt = False
        demote_kinds: set[str] = set()
        lock_note = ""
        self._last_stall = False
        self._last_click = None
        self._last_hover_key = None
        self._hovered_marks = set()
        self._discovery_attempts = {}
        self._last_discovery_result = None
        self._last_discover_ms = 0.0
        self._entity_tracker = EntityTracker()
        self._cached_perceive_scene = None
        self._cached_perceive_scene_step = None
        self._force_perceive = False
        self._perception_cache.reset()
        self._action_surface = None
        self._empty_action_surface_key = ""
        self._empty_action_surface_count = 0
        self._decision_softlock = False
        self._force_inspect = False
        self._perception_recovery_steps = 0
        self._card_action_attempts = 0
        self._verified_card_actions = 0
        self._false_card_transactions = 0
        self._aim = None
        self._selected = None
        self._combat_index = 0
        self._combat_won_keys = set()
        self._was_in_combat = False
        self._current_episode_id = episode_id
        self._cancel_explore()
        self._survey.reset()
        self._approach.reset()
        self._survey_done = False
        dino_prompt_streak = 0
        skill_note = f"skill={active_skill_id}" if skill_on else "skill=off"
        _say(f"episode {self._episode_n}/{self.config.lives}  {skill_note}")
        from loop.os import ocr as ocr_mod

        ocr_mod.set_debug_dir(self.store.run_dir / "ocr_debug")
        ocr_mod.warm_up_rapid_engine()
        self._blocked_marks.clear()
        self.working = WorkingMemory()
        memory_carry = self.notebook.reset_episode()
        self._live(
            phase="episode",
            episode_id=episode_id,
            skill_on=skill_on,
            active_skill_id=active_skill_id,
            thought="",
            action="",
            message=f"episode {self._episode_n}/{self.config.lives}",
        )

        for step_id in range(1, self.config.max_steps + 1):
            if self._decision_softlock:
                terminal = "timeout"
                break
            if stop_requested():
                self._stopped = True
                if steps:
                    terminal = "timeout"
                break
            if time.perf_counter() > t_end:
                terminal = "timeout"
                wall_timeout = True
                break
            t_step = time.perf_counter()
            t0 = t_step
            self._step_id = step_id
            tag = f"{self._episode_n}.{step_id}"
            self._step_recheck_count = 0
            self._step_recheck_image = None
            self._step_illegal_blocked = False
            self._last_legal_reason = None
            self._pending_exec_error = None
            self._last_targeting_opts = None
            self._perceive_retry_reason = None
            self._last_scene = ""
            self._last_tool_trace = []
            self._last_tool_events = []
            self._reset_turn_telemetry()
            self._last_decision_schema = ""
            self._last_snapshot_id = ""
            self._last_turn_snapshot = {}
            self._last_intent = {}
            self._last_execution_plan = {}
            self._last_reason_backend = ""
            self._last_reason_fallback = ""
            self._last_reason_call = {}
            self._v2_control_objects = {}
            self._action_surface = None
            self._last_perceive_raw = ""
            self._last_reason_raw = ""
            self._reason_ms = 0.0
            self._scene_desc = None
            turn = self._turn_based()
            obs_gate = "unknown"
            if turn:
                carried = self._carry_after
                if carried is not None:
                    self._carry_after = None
                    view = self.fixture.observe_image(carried)
                    gate_fn = getattr(self.fixture, "act_gate", None)
                    obs_gate = gate_fn(view) if callable(gate_fn) else "unknown"
                    if obs_gate != "theirs":
                        before = carried
                        grab_ms = 0.0
                    else:
                        before, obs_gate = self._wait_actable(t_end)
                        grab_ms = _ms(t0)
                else:
                    before, obs_gate = self._wait_actable(t_end)
                    grab_ms = _ms(t0)
                ocr_settle_ms = int(os.environ.get("AVATAR_OCR_SETTLE_MS", "0") or "0")
                if ocr_settle_ms > 0:
                    profile = getattr(self.fixture, "profile", None)
                    threshold = float(getattr(profile, "diff_threshold", 0.02) or 0.02)
                    quiet_threshold = max(threshold, 0.04)

                    def _ocr_grab_thumb() -> Any:
                        return roi_thumb(self.fixture.grab_image(), 64)

                    wait_until_quiet(
                        _ocr_grab_thumb,
                        threshold=quiet_threshold,
                        timeout_ms=ocr_settle_ms,
                        poll_ms=80,
                        quiet_ms=min(200, ocr_settle_ms),
                        should_stop=stop_requested,
                    )
                    before = self.fixture.grab_image()
                if stop_requested():
                    self._stopped = True
                    if steps:
                        terminal = "timeout"
                    break
                if time.perf_counter() > t_end:
                    terminal = "timeout"
                    wall_timeout = True
                    break
            else:
                before = self.fixture.grab_image()
                grab_ms = _ms(t0)
            if step_id == 1:
                source = "fake"
                if self.fixture.controller is not None:
                    source = getattr(self.fixture.controller.capture, "last_source", "client")
                _say(f"  grab {before.size[0]}x{before.size[1]}  source={source}  (game client, not desktop)")
                geo = getattr(self.fixture, "geometry_line", None)
                if callable(geo):
                    _say(f"  view    {geo(before)}")
            t0 = time.perf_counter()
            if turn:
                patches = []
                patch_s = ""
                cv_on = False
                cv_glyph = None
                before_thumb = roi_thumb(before, 64)
                saliency_ms = _ms(t0)
                dino_boxes: list[Any] = []
                dino_path = None
                grounded_s = ""
                n_raw = 0
                raw_boxes: list[Any] = []
                dino_marks: list[AimMark] = []
                det = None
                family = ""
                dino_ok = False
                labels: list[str] = []
                dino_full_ms = 0.0
                dino_inset_ms = 0.0
                hint = InteractHint(on=False)
                glyph_hint = None
                prompt_text = ""
                ocr_ms = 0.0
                gui_ms = 0.0
                yield_vlm = False
                confirmed = False
                prompt_cv = False
                prompt_on = False
                close = False
                far_goal = False
                approach = False
                close_prompt = False
                lock_note = ""
                journal_kind = None
                journal_heading = None
                locked_target = False
                side = int(getattr(self.fixture, "obs_longest_side", 1280) or 1280)
                t0 = time.perf_counter()
                view = self.fixture.observe_image(before)
                self._last_obs_image = view
                # Turn-based controls are visual objects too. DINO grounds a
                # bounded UI query list before aim construction; fusion below
                # still requires OCR/GUI support before a new mark is exposed.
                det = self._detector if self.config.dino else None
                family = str(getattr(det, "family", "") or "") if det is not None else ""
                dino_ok = det is not None and family == "grounding-dino"
                labels = self._dino_labels() if dino_ok else []
                t_dino = time.perf_counter()
                if dino_ok and labels:
                    try:
                        raw_boxes = list(det.detect(view, labels) or [])
                    except Exception as exc:  # noqa: BLE001 — availability is checked before the session
                        _say(f"  dino     forward failed: {type(exc).__name__}: {exc}")
                        raw_boxes = []
                    dino_boxes = list(raw_boxes)
                    dino_marks = dino_boxes_to_marks(dino_boxes)
                n_raw = len(raw_boxes)
                dino_full_ms = _ms(t_dino)
                if os.environ.get("AVATAR_OCR_DEBUG", "").strip().lower() in {"1", "true", "yes"}:
                    try:
                        debug_dir = self.store.run_dir / "ocr_debug"
                        debug_dir.mkdir(parents=True, exist_ok=True)
                        before.save(debug_dir / f"step{step_id:03d}_obs_raw.jpg")
                        view.save(debug_dir / f"step{step_id:03d}_obs_view.jpg")
                    except OSError:
                        pass
                t_hud = time.perf_counter()
                if self._two_stage():
                    self._prev_host_stats = dict(self._host_stats) if self._host_stats else {}
                    self._host_stats = self._read_host_stats(view)
                    self._track_combat_index()
                    self._obs_metrics = (
                        stats_to_score_metrics(self._host_stats)
                        if str(self._host_stats.get("screen") or "") == "combat"
                        else self.fixture.scorer(view)
                    )
                    self._obs_metrics = self._maybe_apply_combat_exit_transition(self._obs_metrics)
                else:
                    self._obs_metrics = self.fixture.scorer(view)
                hud_ocr_ms = _ms(t_hud)
                aim_screen = (
                    str(self._obs_metrics.get("screen") or prev_metrics.get("screen") or "") or None
                )
                self._sync_selected_for_screen(aim_screen)
                reuse = self._perception_reuse(view, aim_screen=aim_screen)
                words, marks, gui_marks, marks_ms = self._build_turn_marks(
                    view,
                    aim_screen=aim_screen,
                    reuse=reuse,
                )
                self._last_ocr_words = list(words)
                ocr_ms = hud_ocr_ms + marks_ms
                if self._two_stage():
                    if dino_marks:
                        fused = fuse_dino_marks(
                            AimGuide.from_marks(view, marks, last_xy=self._last_click),
                            dino_marks,
                            ocr_words=words,
                            gui_marks=gui_marks,
                        )
                        marks = list(fused.marks)
                    screen_key = screen_from_labels(
                        (m.label for m in marks),
                        fallback=aim_screen,
                    )
                    stamped = self._screen_objects.save_frame(
                        run_id=self.session_id,
                        step_id=step_id,
                        marks=marks,
                        screen=screen_key,
                    )
                    self._aim = AimGuide.from_marks(view, stamped, last_xy=self._last_click)
                    self._build_frame_model()
                    self._reconcile_game_state()
                    self._affordance_verdicts = {}
                    # Unrelated combat frames are not an affordance probe. A
                    # same-object hover/click probe may add advisory evidence
                    # later, but it cannot decide current card legality.
                    if (
                        not self._combat_screen_now()
                        and self._prev_frame_image is not None
                        and self._aim.marks
                    ):
                        self._affordance_verdicts = detect_affordance_diff(
                            self._prev_frame_image,
                            view,
                            self._aim.marks,
                        )
                    self._sync_legal_actions()
                else:
                    self._aim = build_aim(
                        view,
                        last_xy=self._last_click,
                        lexicon=self.journal.lexicon,
                        screen=aim_screen,
                        words=words,
                        gui_marks=gui_marks,
                    )
                    if dino_marks:
                        fused = fuse_dino_marks(
                            self._aim,
                            dino_marks,
                            ocr_words=words,
                            gui_marks=gui_marks,
                        )
                        self._aim = AimGuide.from_marks(
                            paint_aim(view, marks=fused.marks, last_xy=self._last_click),
                            fused.marks,
                            last_xy=self._last_click,
                        )
                self.journal.ingest_aim(
                    screen_from_labels(
                        (m.label for m in self._aim.marks),
                        fallback=aim_screen,
                    ),
                    self._aim.marks,
                )
                t_enc = time.perf_counter()
                obs_frame = encode_image(view, longest_side=side, jpeg_quality=90)
                obs_path = self.store.frame_path(episode_id, step_id, "obs")
                obs_path.write_bytes(obs_frame.data)
                if dino_ok and (self.config.debug_frames or dino_boxes):
                    dino_frame = encode_image(
                        paint_boxes(view, dino_boxes), longest_side=side, jpeg_quality=90
                    )
                    dino_path = self.store.frame_path(episode_id, step_id, "dino")
                    dino_path.write_bytes(dino_frame.data)
                if self._two_stage():
                    painted = paint_aim(view, marks=self._aim.marks, last_xy=self._last_click)
                    vlm_obs = encode_image(painted, longest_side=side, jpeg_quality=90)
                    self._reason_vlm_frames = [vlm_obs]
                    if self.config.debug_overlay:
                        debug_img = paint_aim(view, marks=self._aim.marks, last_xy=self._last_click)
                        debug_frame = encode_image(debug_img, longest_side=side, jpeg_quality=90)
                        debug_path = self.store.frame_path(episode_id, step_id, "debug")
                        debug_path.write_bytes(debug_frame.data)
                else:
                    vlm_obs = encode_image(self._aim.image, longest_side=side, jpeg_quality=90)
                vlm_path = self.store.frame_path(episode_id, step_id, "vlm")
                vlm_path.write_bytes(vlm_obs.data)
                ui_path = None
                encode_ms = _ms(t_enc)
                perceive_ms = _ms(t_step)
                vlm_frames = [vlm_obs]
                if self._two_stage():
                    second_image = (
                        "Single image: cropped client with numbered marks (verification aid for LLM2)."
                    )
                else:
                    second_image = "Single image: cropped client with 0-1000 grid and numbered marks."
                t_obs = time.perf_counter()
            else:
                patches = bright_patches(before)
                patch_s = format_patches(patches)
                cv_on = prompt_visible(before)
                cv_glyph = prompt_glyph(before) if cv_on else None
                before_thumb = roi_thumb(before, 64)
                saliency_ms = _ms(t0)
                dino_boxes: list[Any] = []
                dino_path = None
                grounded_s = ""
                n_raw = 0
                raw_boxes: list[Any] = []
                det = self._detector if self.config.dino else None
                family = str(getattr(det, "family", "") or "") if det is not None else ""
                dino_ok = det is not None and family != "unavailable"
                labels = self._dino_labels() if dino_ok else []
                drop_kinds = self.journal.locked_kinds()
                t0 = time.perf_counter()
                if dino_ok and labels:
                    try:
                        raw_boxes = list(det.detect(before, labels) or [])
                    except Exception:  # noqa: BLE001 — overlay is a hint, never abort the turn
                        raw_boxes = []
                    n_raw = len(raw_boxes)
                    dino_boxes = filter_grounded(
                        raw_boxes, demote=demote_kinds, drop=drop_kinds
                    )
                    grounded_s = grounded_with_loot(
                        before, dino_boxes, demote=demote_kinds
                    )
                dino_full_ms = _ms(t0)
                t0 = time.perf_counter()
                inset_det = det if dino_ok else None
                hint = detect_interact(
                    before,
                    detector=inset_det,
                    cv_on=cv_on,
                    cv_glyph=cv_glyph,
                )
                dino_inset_ms = _ms(t0)
                glyph_hint = hint.glyph
                t0 = time.perf_counter()
                prompt_text = read_prompt_text(before)
                ocr_ms = _ms(t0)
                gui_ms = 0.0
                yield_vlm = skip_interact_interrupt(
                    prompt_text=prompt_text,
                    prompt_on=hint.on,
                    glyph=glyph_hint,
                    last_interrupt_glyph=self._last_interrupt_glyph,
                )
                confirmed = prompt_confirmed(hint, prompt_text)
                if confirmed and (glyph_hint or "") not in FACE:
                    hint = InteractHint(
                        on=hint.on,
                        glyph="a",
                        source=hint.source,
                        n_raw=hint.n_raw,
                        n_kept=hint.n_kept,
                    )
                    glyph_hint = "a"
                prompt_cv = bool(hint.on)
                prompt_on = confirmed
                close = boxes_are_near(dino_boxes)
                far_goal = bool(dino_boxes) and not close
                approach = bool(
                    hint.on
                    and "cv" in (hint.source or "")
                    and not confirmed
                    and not yield_vlm
                    and far_goal
                )
                close_prompt = bool(
                    (hint.on or cv_on)
                    and not confirmed
                    and not yield_vlm
                    and not approach
                )
                if yield_vlm:
                    demote_kinds.update(lock_demote_labels(dino_boxes))
                    drop_kinds = self.journal.locked_kinds() | demote_kinds
                    if raw_boxes:
                        dino_boxes = filter_grounded(
                            raw_boxes, demote=demote_kinds, drop=self.journal.locked_kinds()
                        )
                    grounded_s = grounded_with_loot(
                        before, dino_boxes, demote=demote_kinds, locked=True
                    )
                    lock_note = lock_evidence_line(
                        glyph=glyph_hint,
                        kinds=sorted(demote_kinds),
                    )
                journal_kind = kind_from_boxes(dino_boxes) or (
                    "prompt" if (confirmed or yield_vlm) else None
                )
                journal_heading = heading_from_boxes(dino_boxes, self._last_walk)
                locked_target = self.journal.is_locked(journal_kind, journal_heading) or yield_vlm
                if journal_kind:
                    self.journal.observe(
                        kind=journal_kind,
                        heading=journal_heading,
                        prompt=prompt_text,
                        glyph=glyph_hint,
                        outcome="lock" if yield_vlm else "seen",
                        locked=yield_vlm,
                        step_id=step_id,
                    )
                t0 = time.perf_counter()
                obs_frame = encode_image(before, longest_side=768, jpeg_quality=85)
                obs_path = self.store.frame_path(episode_id, step_id, "obs")
                obs_path.write_bytes(obs_frame.data)
                if dino_ok and labels and (self.config.debug_frames or dino_boxes):
                    painted = paint_boxes(before, dino_boxes)
                    dino_frame = encode_image(painted, longest_side=768, jpeg_quality=85)
                    dino_path = self.store.frame_path(episode_id, step_id, "dino")
                    dino_path.write_bytes(dino_frame.data)
                paint_for_vlm = dino_boxes or None
                composite = compose_obs(before, boxes=paint_for_vlm)
                vlm_obs = encode_image(composite, longest_side=768, jpeg_quality=85)
                vlm_path = self.store.frame_path(episode_id, step_id, "vlm")
                vlm_path.write_bytes(vlm_obs.data)
                inset = prompt_inset(before)
                ui_frame = encode_image(inset, longest_side="native", jpeg_quality=95)
                ui_path = self.store.frame_path(episode_id, step_id, "ui")
                ui_path.write_bytes(ui_frame.data)
                encode_ms = _ms(t0)
                perceive_ms = _ms(t_step)
                vlm_frames = [vlm_obs]
                second_image = (
                    "Single image: full frame plus labeled PROMPT inset banner (zoomed bottom-center bar)."
                )
                if self._carry_caption:
                    second_image += " Last chord result is text only below (not a second image)."
                t_obs = time.perf_counter()

            memory_hit = MemoryHit()
            if dump_all and self.memory is not None and not turn:
                hits = self.memory.recall(before, k=3)
                memory_hit = MemoryHit(
                    ids=[row[0] for row in hits],
                    snippets=[row[1] for row in hits],
                )

            thought = ""
            action = ActionLog(duration_ms=self.config.burst_ms)
            think_ms = 0.0
            failed_while_thinking = False
            inject_skill = active_skill if skill_on else None
            vlm_hud: dict[str, Any] = {}
            saved_note: str | None = None
            # Two-stage VLM fills these; heuristic/skip-models/skills never do.
            # The post-act combat gate below still reads them on every turn.
            is_tool = False
            choice = ""
            target_label = ""
            inspect_requested = False
            tool_only = False
            tool_action_contract: dict[str, Any] | None = None
            tool_inspection_contract: dict[str, Any] | None = None
            terminal_tool_action = False
            recheck_only = False
            self._last_reasoner_role = ""
            self._last_router_reason = ""
            self._last_epistemic_blockers = []

            if self.fixture.controller is not None:
                self.fixture.controller.release_all()

            self.last_system_prompt = system_for(
                self.config.arm, pace=getattr(self.fixture, "pace", "realtime")
            )
            last = self.working.last()
            working_block = None
            if not turn:
                working_block = self.working.prompt_block(
                    glyphs=list(last.glyphs) if last and last.prompt else None,
                    chord=last.chord if last else "",
                    selected=self._selected if turn else None,
                ) or None
            if turn:
                self._maybe_break_nochange_loop(tag=tag)
                last_click_s = None
                if self._last_click is not None:
                    last_click_s = (
                        f"{int(round(self._last_click[0] * 1000))},"
                        f"{int(round(self._last_click[1] * 1000))}"
                    )
                retrieved = []
                if dump_all:
                    retrieved = self._retrieved_facts(
                        screen=str(prev_metrics.get("screen") or "") or None,
                        snippets=memory_hit.snippets if dump_all else None,
                    )
                blocked = self.working.blocked_hint(selected=self._selected)
                listing = self._aim.listing() if self._aim is not None else None
                host_screen = overlay_screen(
                    str(self._obs_metrics.get("screen") or prev_metrics.get("screen") or "") or None,
                    listing,
                )
                self.notebook.decay_grounded(
                    screen=host_screen,
                    energy=self._obs_metrics.get("energy"),
                    aim_listing=listing,
                )
                notebook_block = self.notebook.prompt_block(
                    screen=host_screen,
                    blocked_hint=blocked,
                    aim_listing=listing,
                    hovered=self._hovered_marks,
                    energy=self._obs_metrics.get("energy"),
                    selected=self._selected,
                )
                self._last_notebook_snapshot = notebook_block
                if not self._two_stage():
                    self.last_user_prompt = turn_user_prompt(
                        actions=prompt_listing(self.fixture.action_table),
                        memory_snippets=None,
                        allow_memory=dump_all,
                        skill=inject_skill,
                        arm=self.config.arm,
                        last_pixels_changed=last_pixels_changed,
                        last_mad=last_mad,
                        burst_caption=self._carry_caption or None,
                        notebook_block=notebook_block,
                        recall_hits=retrieved or None,
                        aim_listing=listing,
                        last_click=last_click_s,
                        selected=self._selected,
                        screen=host_screen,
                        hud=self._obs_metrics,
                    )
            else:
                self.last_user_prompt = user_prompt(
                    actions=prompt_listing(self.fixture.action_table),
                    memory_snippets=memory_hit.snippets,
                    allow_memory=dump_all,
                    skill=inject_skill,
                    arm=self.config.arm,
                    recent_chords=recent_chords or None,
                    last_pixels_changed=last_pixels_changed,
                    last_mad=last_mad,
                    last_had_prompt=last_had_prompt,
                    prompt_box=confirmed,
                    prompt_glyph=glyph_hint,
                    bright_patches=None if grounded_s else (patch_s or None),
                    grounded=grounded_s or None,
                    burst_caption=self._carry_caption or None,
                    second_image=second_image,
                    working_block=working_block,
                    prompt_text=prompt_text or None,
                    prompt_locked=yield_vlm,
                    lock_note=lock_note or None,
                    approach_note=approach_evidence_line() if approach else None,
                    close_prompt_note=close_prompt_line() if close_prompt else None,
                    journal_block=self.journal.prompt_block() or None,
                    recall_hits=self._carry_recall or None,
                )
            if patch_s and not grounded_s:
                _say(f"  {tag} bright   {patch_s}")
            if self.config.dino and det is not None:
                _say(
                    f"  {tag} dino     {family or 'off'} kept={len(dino_boxes)}/{n_raw} "
                    f"{grounded_s or 'none'}"
                )
            src_note = f" source={hint.source}" if hint.source else ""
            glyph_note = f" glyph={glyph_hint}" if glyph_hint else ""
            cv_note = " cv" if prompt_cv else ""
            _say(
                f"  {tag} prompt   {'yes' if prompt_on else 'no'}{cv_note}"
                f"{glyph_note}{src_note}"
            )
            if prompt_text:
                _say(f"  {tag} prompt-text {_one_line(prompt_text)}")
            if turn and self._aim is not None:
                n_marks = len(self._aim.marks)
                last_s = last_click_s or "none"
                ocr_n = len(words) if turn else 0
                _say(f"  {tag} aim      marks={n_marks} ocr_words={ocr_n} last={last_s}")
                if turn:
                    from loop.os.ocr import frame_is_blank, last_read_stats

                    stats = last_read_stats()
                    if not stats.get("words") and not frame_is_blank(view):
                        dump = stats.get("dump_prepared") or stats.get("dump_raw") or ""
                        shape = stats.get("shape") or stats.get("prepared_shape") or "?"
                        dtype = stats.get("dtype") or stats.get("prepared_dtype") or "?"
                        _say(
                            f"  {tag} ocr      empty on live frame "
                            f"engine={stats.get('engine', '?')} "
                            f"dtype={dtype} shape={shape} "
                            f"mean={stats.get('mean', 0):.0f} max={stats.get('max', 0):.0f} "
                            f"quiet={self._last_quiet_ms:.0f}ms polls={self._last_quiet_polls}"
                            + (f" dump={dump}" if dump else "")
                        )
            if yield_vlm:
                _say(f"  {tag} lock     yield VLM (condition or stuck bar)")
            if approach:
                _say(f"  {tag} approach no OCR copy — walk closer, do not tap")
            if close_prompt:
                _say(f"  {tag} prompt   bar/inset without OCR — do not walk through")
            if hint.n_raw or hint.n_kept:
                _say(f"  {tag} dino-ui  kept={hint.n_kept}/{hint.n_raw}")
            if self._carry_caption:
                _say(f"  {tag} burst    {self._carry_caption}")
            dino_timing = "off" if turn else f"{dino_full_ms:.0f}/{dino_inset_ms:.0f}ms"
            _say(
                f"  {tag} timing   perceive={perceive_ms:.0f} grab={grab_ms:.0f}"
                f" sal={saliency_ms:.0f} dino_ms={dino_timing}"
                f" ocr={ocr_ms:.0f} gui={gui_ms:.0f} enc={encode_ms:.0f}"
            )

            if hint.source == "dino" and hint.glyph:
                dino_prompt_streak += 1
            else:
                dino_prompt_streak = 0
            interrupt = (
                interrupt_ready(
                    hint, prompt_text=prompt_text, dino_streak=dino_prompt_streak
                )
                and not yield_vlm
                and not locked_target
            )
            stall_n, _ = walk_stall_count(recent_chords)
            stalled = stall_n >= 2 and self._last_stall
            box_cx = approach_target_cx(dino_boxes)
            want_approach = approach and not interrupt and not yield_vlm
            want_survey = (
                self.config.survey
                and not self._survey_done
                and not interrupt
                and not want_approach
                and not confirmed
                and not yield_vlm
            )
            if yield_vlm or approach or want_approach:
                self._cancel_explore()
            auto_explore = (
                explore_owns_pad(
                    interrupt=interrupt,
                    yield_lock=yield_vlm,
                    stall=stalled,
                    gsm_busy=False,
                )
                and not want_approach
                and not want_survey
            )
            gsm_continue = (
                explore_owns_pad(
                    interrupt=interrupt,
                    yield_lock=yield_vlm,
                    stall=False,
                    gsm_busy=self._gsm_busy,
                )
                and not want_approach
                and not want_survey
            )
            skill_action = None
            gsm_from_skill = None
            if skill_on and active_skill is not None and not interrupt:
                gsm_from_skill = skill_invoke_gsm(active_skill)
                skill_action = action_from_skill(
                    active_skill, self.fixture.action_table, burst_ms=self.config.burst_ms
                )
            if turn:
                interrupt = False
                want_approach = False
                want_survey = False
                auto_explore = False
                gsm_continue = False
                gsm_from_skill = None
                skill_action = None if not skill_on else skill_action
            invoked_gsm: str | None = None
            gsm_ticks = 0
            proposed = ActionLog(duration_ms=self.config.burst_ms)

            if interrupt:
                waiting = "interact-interrupt"
                _say(f"  {tag} interrupt glyph={hint.glyph} source={hint.source} skip VLM")
            elif want_approach:
                waiting = "invoke_gsm approach"
                _say(f"  {tag} gsm      approach")
            elif want_survey:
                waiting = "invoke_gsm survey"
                _say(f"  {tag} gsm      survey")
            elif gsm_from_skill or skill_action is not None:
                waiting = f"skill-execute {active_skill_id}"
                _say(f"  {tag} skill    {waiting}")
            elif auto_explore or gsm_continue:
                waiting = "invoke_gsm explore"
                why = "stall → explore" if auto_explore else "explore continue"
                _say(f"  {tag} gsm      {why}")
            elif self._use_heuristic():
                waiting = "heuristic"
                _say(f"  {tag} thinking…  {waiting}")
            elif self.config.skip_models or self.gateway is None:
                waiting = "skip-models"
                _say(f"  {tag} thinking…  {waiting}")
            else:
                waiting = "waiting on VLM"
                _say(f"  {tag} thinking…  {waiting}")
            self._live(
                phase="thinking",
                step=step_id,
                episode_id=episode_id,
                thought="",
                action="",
                skill_on=skill_on,
                active_skill_id=active_skill_id,
                message=waiting,
            )

            if interrupt:
                self._cancel_explore()
                action = interrupt_chord(hint.glyph, burst_ms=self.config.burst_ms)
                proposed = action.model_copy(deep=True)
                thought = f"interact-interrupt glyph={hint.glyph} source={hint.source}"
                if "cv" in (hint.source or ""):
                    self._interrupt_cv += 1
                elif hint.source == "dino":
                    self._interrupt_dino += 1
            elif want_approach:
                cx = box_cx if box_cx is not None else 0.5
                action, gsm_ticks, done = self._approach_chord(
                    box_cx=cx, confirmed=confirmed, locked=locked_target
                )
                if action.name == "idle":
                    action = ActionLog(stick_l="n", duration_ms=self.config.burst_ms)
                    action.name = action.derive_name()
                    done = True
                invoked_gsm = "approach"
                proposed = action.model_copy(deep=True)
                thought = "invoke_gsm approach (center/walk)"
                if done and journal_kind:
                    self.journal.observe(
                        kind=journal_kind,
                        heading=journal_heading or self._last_walk,
                        prompt=prompt_text,
                        glyph=glyph_hint,
                        outcome="blocked",
                        step_id=step_id,
                    )
            elif want_survey:
                action, gsm_ticks, done = self._survey_chord()
                invoked_gsm = "survey"
                proposed = action.model_copy(deep=True)
                thought = "invoke_gsm survey (look)"
            elif gsm_from_skill:
                action, gsm_ticks = self._explore_chord(
                    stall=True, prompt_on=prompt_on, lock=yield_vlm
                )
                invoked_gsm = gsm_from_skill
                proposed = action.model_copy(deep=True)
                thought = f"skill-execute invoke_gsm={gsm_from_skill}"
            elif skill_action is not None:
                self._cancel_explore()
                action = skill_action
                proposed = action.model_copy(deep=True)
                thought = f"skill-execute id={active_skill_id}"
            elif auto_explore or gsm_continue:
                action, gsm_ticks = self._explore_chord(
                    stall=self._last_stall, prompt_on=prompt_on, lock=yield_vlm
                )
                invoked_gsm = "explore"
                proposed = action.model_copy(deep=True)
                thought = (
                    "invoke_gsm explore (stall)"
                    if auto_explore
                    else "invoke_gsm explore (continue)"
                )
            elif self._use_heuristic():
                hud_now = self._obs_metrics if turn else {}
                inspect_id = pending_inspect_target(self._entity_tracker)
                if inspect_id and str(hud_now.get("screen") or "").strip().lower() == "combat":
                    filled = action_inspect_label(
                        self._aim,
                        inspect_id,
                        duration_ms=self.config.burst_ms,
                    )
                else:
                    filled = None
                if filled is None:
                    filled = heuristic_action(
                        self._aim,
                        metrics=hud_now,
                        selected=self._selected,
                        recent=recent_chords,
                        duration_ms=self.config.burst_ms,
                    )
                if filled is not None:
                    action = filled
                thought = "heuristic policy"
                proposed = action.model_copy(deep=True)
            elif self.config.skip_models or self.gateway is None:
                action.name = action_round_robin[self._rr % len(action_round_robin)]
                self._rr += 1
                thought = "skip-models round-robin"
                proposed = action.model_copy(deep=True)
            elif self._two_stage() and turn:
                self._obs_metrics = self._maybe_apply_combat_exit_transition(self._obs_metrics)
                parsed, stage_perceive_ms, stage_reason_ms, _scene = self._run_two_stage_vlm(
                    obs_frame=vlm_frames[0],
                    host_screen=host_screen if turn else None,
                    notebook_block=notebook_block if turn else None,
                    blocked=blocked if turn else None,
                    last_pixels_changed=last_pixels_changed,
                    journal_block=self.journal.prompt_block() or None,
                )
                perceive_ms = stage_perceive_ms
                self._reason_ms = stage_reason_ms
                think_ms = stage_perceive_ms + stage_reason_ms
                thought_raw = parsed.get("thought")
                thought = (
                    thought_raw
                    if isinstance(thought_raw, str)
                    else str(thought_raw or self._last_reason_raw or "")
                )
                choice = str(parsed.get("choice") or "").strip()
                choice_id = str(parsed.get("choice_id") or "").strip()
                target_raw = parsed.get("target")
                target_label = ""
                if isinstance(target_raw, str):
                    target_label = target_raw.strip()
                elif target_raw is not None and not isinstance(target_raw, bool):
                    target_label = str(target_raw).strip()
                target_id = str(parsed.get("target_id") or "").strip()
                action_kind = str(parsed.get("action") or "").strip().lower()
                inspect_target = ""
                if action_kind in {"inspect", "hover"}:
                    inspect_target = target_label or str(parsed.get("inspect") or "").strip()
                elif parsed.get("hover") in (True, "true", 1, "1", "yes") and target_label:
                    inspect_target = target_label
                inspect_requested = bool(action_kind in {"inspect", "hover"} or inspect_target)
                listing = self._aim.listing() if self._aim is not None else None
                avoid = self._avoid_for_turn(
                    host_screen=host_screen,
                    tutorial_info=_scene.tutorial if _scene is not None else None,
                    listing=listing,
                    prev_screen=str(self._obs_metrics.get("screen") or prev_metrics.get("screen") or ""),
                )
                reason_controls = getattr(self, "_reason_controls", None) or reason_controls_for_turn(
                    controls=_scene.controls if _scene is not None else [],
                    uncertain=_scene.uncertain if _scene is not None else [],
                    enemies=(_scene.combat or {}).get("enemies") if _scene is not None else [],
                    detector_rows=self._detector_typed_rows(),
                    tutorial_info=_scene.tutorial if _scene is not None else None,
                    host_stats=self._host_stats,
                )
                allowed_controls, allowed_targets = allowed_sets_from_controls(reason_controls)
                is_tool = bool(str(parsed.get("tool") or "").strip())
                tool_name = ToolRegistry.normalize_tool_name(str(parsed.get("tool") or "")) if is_tool else ""
                # A terminal play_card command is the only tool response that
                # can synthesize controller input. It still goes through the
                # nonce/type checks at the final boundary.
                tool_action_contract = (
                    self._validated_tool_card_contract()
                    if tool_name == "play_card" else None
                )
                tool_inspection_contract = (
                    self._validated_tool_inspection_contract()
                    if tool_name == "inspect_object" else None
                )
                recheck_only = tool_name == "request_recheck"
                terminal_tool_action = (
                    tool_action_contract is not None
                    or tool_inspection_contract is not None
                    or recheck_only
                )
                tool_only = is_tool and not terminal_tool_action
                if tool_inspection_contract is not None:
                    inspect_requested = True
                allowed_choice = (
                    not choice
                    or is_tool
                    or choice_in_allowed_controls(
                        choice,
                        allowed_controls,
                        target=target_label,
                        allowed_targets=allowed_targets,
                    )
                )
                if self._last_decision_schema == "v2" and not self._pending_exec_error:
                    # v2 controls were resolved from the current snapshot;
                    # legacy LLM1 label lists are not an additional authority.
                    allowed_choice = True
                # Reasoner prose can propose only state-legal combat actions.
                # In particular, an unlabeled :Icon must never be converted
                # into a hand card merely because its geometry looks plausible.
                if (
                    self._two_stage()
                    and not is_tool
                    and self._game_state is not None
                    and self._game_state.underlying_activity.value == "combat"
                    and not self._state_allows_combat_action(choice, target_label)
                ):
                    allowed_choice = False
                    self._last_legal_reason = "state-gate: card/target is not a typed current-frame object"
                # The reasoner sees stable EnemyState ids (enemy_1_1) while
                # action synthesis needs the label on this exact aim frame.
                # This resolves only via the reconciled bbox; it never carries
                # a prior mark id into a new frame.
                if is_frame_mark_token(target_label):
                    target_label = ""
                else:
                    target_label = self._resolve_enemy_alias_to_current_label(target_label)
                semantic = None
                from loop.mcp.tools.targeting import object_role as _object_role

                v2_kind = str(self._last_intent.get("kind") or "")
                if self._last_decision_schema == "v2" and v2_kind in {
                    "end_turn", "click_control", "inspect"
                }:
                    object_key = str(
                        self._last_intent.get("control_id")
                        or self._last_intent.get("object_id")
                        or ""
                    )
                    control_obj = self._v2_control_objects.get(object_key)
                    if control_obj is not None:
                        semantic = ActionLog(
                            mark=control_obj.mark_id,
                            click_x=control_obj.x,
                            click_y=control_obj.y,
                            hover=v2_kind == "inspect",
                            duration_ms=self.config.burst_ms,
                            source_role=control_obj.role,
                            source_object_id=control_obj.object_id or object_key,
                        )
                        semantic.name = semantic.derive_name()

                instance_card = self._current_frame_object(choice_id) if choice_id else None
                instance_target = self._current_frame_object(target_id) if target_id else None
                card_ok = (
                    instance_card is not None
                    and _object_role(instance_card, entity_targets_ok=True) == "card"
                )
                target_ok = (
                    instance_target is not None
                    and _object_role(instance_target, entity_targets_ok=True) == "enemy"
                )
                if (
                    not is_tool
                    and card_ok
                    and (not target_id or target_ok)
                ):
                    choice = instance_card.label
                    target_label = instance_target.label if instance_target is not None else target_label
                    semantic = ActionLog(
                        mark=instance_card.mark_id,
                        click_x=instance_card.x,
                        click_y=instance_card.y,
                        target=instance_target.mark_id if instance_target is not None else None,
                        click_x2=instance_target.x if instance_target is not None else None,
                        click_y2=instance_target.y if instance_target is not None else None,
                        duration_ms=self.config.burst_ms,
                        source_role="card",
                        target_role="enemy" if instance_target is not None else None,
                        source_object_id=instance_card.object_id or None,
                        target_object_id=(
                            instance_target.object_id if instance_target is not None else None
                        ),
                    )
                    semantic.name = semantic.derive_name()
                if tool_action_contract is not None:
                    card_obj = self._screen_objects.by_id(int(tool_action_contract["card_mark"]))
                    target_mark = tool_action_contract.get("target_mark")
                    target_obj = self._screen_objects.by_id(int(target_mark)) if target_mark is not None else None
                    if card_obj is not None and (target_mark is None or target_obj is not None):
                        choice = card_obj.label
                        target_label = target_obj.label if target_obj is not None else ""
                        parsed["choice"] = choice
                        if target_label:
                            parsed["target"] = target_label
                        semantic = ActionLog(
                            mark=card_obj.mark_id,
                            click_x=card_obj.x,
                            click_y=card_obj.y,
                            target=target_obj.mark_id if target_obj is not None else None,
                            click_x2=target_obj.x if target_obj is not None else None,
                            click_y2=target_obj.y if target_obj is not None else None,
                            duration_ms=self.config.burst_ms,
                        )
                        semantic.name = semantic.derive_name()
                elif tool_inspection_contract is not None:
                    inspected = self._screen_objects.by_id(int(tool_inspection_contract["mark_id"]))
                    if inspected is not None:
                        semantic = ActionLog(
                            mark=inspected.mark_id,
                            click_x=inspected.x,
                            click_y=inspected.y,
                            hover=True,
                            duration_ms=self.config.burst_ms,
                        )
                        semantic.name = semantic.derive_name()
                elif recheck_only:
                    # Recheck is observation-only. Identity work is mechanical
                    # hover+OCR before ACT; do not auto-hover leftover sprites.
                    if int(getattr(self, "_perception_recovery_steps", 0)) >= 2:
                        semantic = action_from_labels(
                            self._aim,
                            choice="End Turn",
                            thought="bounded perception recovery fallback",
                            hud={"screen": "combat", "energy": self._obs_metrics.get("energy")},
                            duration_ms=self.config.burst_ms,
                        )
                        if semantic is not None:
                            recheck_only = False
                    else:
                        semantic = None
                elif inspect_target and not choice and not is_tool:
                    semantic = action_inspect_label(
                        self._aim,
                        inspect_target,
                        duration_ms=self.config.burst_ms,
                    )
                if semantic is None:
                    if choice and not is_tool and not allowed_choice:
                        unresolved_choice = True
                    else:
                        semantic = action_from_labels(
                            self._aim,
                            choice=choice,
                            target_label=target_label,
                            thought=thought,
                            avoid=avoid,
                            hud={
                                "energy": self._obs_metrics.get("energy"),
                                "screen": host_screen or self._obs_metrics.get("screen"),
                            },
                            duration_ms=self.config.burst_ms,
                        )
                        unresolved_choice = bool(choice) and semantic is None and not is_tool
                elif semantic is not None:
                    unresolved_choice = False
                if semantic is not None:
                    action = self._attach_action_identity(semantic)
                else:
                    action = ActionLog(duration_ms=self.config.burst_ms)
                    if choice and allowed_choice and not is_tool:
                        steered = steer_to_labeled(
                            action,
                            self._aim,
                            thought=thought,
                            choice=choice,
                            avoid=avoid,
                            hud={
                                "energy": self._obs_metrics.get("energy"),
                                "screen": host_screen or self._obs_metrics.get("screen"),
                            },
                        )
                        action = self._preserve_steered_identity(action, steered)
                    elif choice and not is_tool and not allowed_choice:
                        unresolved_choice = True
                vlm_hud = extract_sts_hud(parsed)
                goal = str(parsed.get("goal") or "").strip()
                note = str(parsed.get("note") or "").strip()
                if goal:
                    vlm_hud["goal"] = goal
                if note:
                    vlm_hud["note"] = note
                if choice:
                    vlm_hud["choice"] = choice
                before_bind = self._attach_action_identity(action)
                action = bind_aim(action, self._aim)
                action = clamp_to_overlay(action, self._aim)
                action = self._preserve_steered_identity(before_bind, action)
                if (
                    tool_inspection_contract is not None
                    and action.mark is not None
                    and not action.hover
                ):
                    try:
                        if int(tool_inspection_contract.get("mark_id")) == int(action.mark):
                            action = action.model_copy(
                                update={
                                    "hover": True,
                                    "target": None,
                                    "click_x2": None,
                                    "click_y2": None,
                                }
                            )
                            action.name = action.derive_name()
                    except (TypeError, ValueError):
                        pass
                # Inspect is a hover, not a card play. targeting_validate would
                # treat the mark as a hand card and wipe the chord.
                if (
                    tool_inspection_contract is None
                    and not inspect_requested
                    and self._last_decision_schema != "v2"
                ):
                    action = self._gate_targeting(action, choice=choice, tag=tag)
                if self._combat_screen_now():
                    self._log_combat_target(action)
                proposed = action.model_copy(deep=True)
                listing = self._aim.listing() if self._aim is not None else None
                hud_screen = overlay_screen(
                    str(self._obs_metrics.get("screen") or prev_metrics.get("screen") or ""),
                    listing,
                )
                saved_note = self.notebook.ingest(
                    parsed,
                    thought=thought,
                    step_id=step_id,
                    screen=hud_screen,
                    aim_listing=listing,
                )
                if saved_note:
                    self.remember_calls += 1
                    _say(f"  {tag} note     {len(saved_note)}ch")
                if self._last_tool_trace:
                    _say(f"  {tag} tools    {len(self._last_tool_trace)}")
                if is_empty_chord(action):
                    if terminal_tool_action:
                        _say(f"  {tag} observation-only tool — no click")
                    elif unresolved_choice:
                        if choice and not allowed_choice and not is_tool:
                            _say(
                                f"  {tag} rejected choice {choice!r} — not in LLM1 Allowed controls"
                            )
                        else:
                            _say(f"  {tag} unresolved choice {choice!r} — no fallback click")
                    elif not self._allow_legacy_empty_fill():
                        _say(f"  {tag} empty typed intent — no legacy fill")
                    elif self._two_stage() and self._combat_screen_now():
                        _say(f"  {tag} empty JSON, no combat fill")
                    else:
                        filled = fill_from_aim(
                            self._aim,
                            recent=recent_chords,
                            duration_ms=self.config.burst_ms,
                            hud={
                                "energy": self._obs_metrics.get("energy"),
                                "screen": host_screen or self._obs_metrics.get("screen"),
                            },
                        )
                        if filled is not None:
                            action = filled
                            _say(f"  {tag} filled empty → {format_chord(action)}")
                        else:
                            _say(f"  {tag} empty JSON, no click")
            else:
                call = self.gateway.complete_json(
                    system=self.last_system_prompt,
                    user=self.last_user_prompt,
                    frames=vlm_frames,
                )
                think_ms = call.latency_ms
                self._think_ms.append(think_ms)
                parsed = flatten_parsed(call.parsed or {})
                thought_raw = parsed.get("thought")
                thought = thought_raw if isinstance(thought_raw, str) else str(thought_raw or call.text or "")
                action = parse_action(parsed, self.fixture.action_table)
                if turn:
                    vlm_hud = extract_sts_hud(parsed)
                    goal = str(parsed.get("goal") or "").strip()
                    note = str(parsed.get("note") or "").strip()
                    if goal:
                        vlm_hud["goal"] = goal
                    if note:
                        vlm_hud["note"] = note
                    action = bind_aim(action, self._aim)
                    action = clamp_to_overlay(action, self._aim)
                proposed = action.model_copy(deep=True)
                if turn:
                    listing = self._aim.listing() if self._aim is not None else None
                    hud_screen = overlay_screen(
                        str(self._obs_metrics.get("screen") or prev_metrics.get("screen") or ""),
                        listing,
                    )
                    saved_note = self.notebook.ingest(
                        parsed,
                        thought=thought,
                        step_id=step_id,
                        screen=hud_screen,
                        aim_listing=listing,
                    )
                    if saved_note:
                        self.remember_calls += 1
                        _say(f"  {tag} note     {len(saved_note)}ch")
                invoke = str(parsed.get("invoke_gsm") or "").strip().lower()
                if invoke in {"explore", "explore_gsm"} and not turn:
                    action, gsm_ticks = self._explore_chord(
                        stall=self._last_stall, prompt_on=prompt_on, lock=yield_vlm
                    )
                    invoked_gsm = "explore"
                    _say(f"  {tag} gsm      VLM invoke_gsm explore")
                else:
                    if is_empty_chord(action):
                        if not self._allow_legacy_empty_fill():
                            _say(f"  {tag} empty typed intent — no legacy fill")
                        elif turn:
                            filled = fill_from_aim(
                                self._aim,
                                recent=recent_chords,
                                duration_ms=self.config.burst_ms,
                                hud={
                                    "energy": self._obs_metrics.get("energy"),
                                    "screen": host_screen or self._obs_metrics.get("screen"),
                                },
                            )
                            if filled is not None:
                                action = filled
                                _say(f"  {tag} filled empty → {format_chord(action)}")
                            else:
                                _say(f"  {tag} empty JSON, no click")
                        else:
                            action = fill_empty_chord(
                                action,
                                self.fixture.action_table,
                                recent=recent_chords,
                                burst_ms=self.config.burst_ms,
                                prompt_on=interrupt,
                                glyph=glyph_hint,
                            )
                            _say(f"  {tag} filled empty chord → {format_chord(action)}")
                    if not turn:
                        action, bound = bind_prompt_devices(
                            thought,
                            action,
                            self.fixture.action_table,
                            visible=interrupt,
                            cv_glyph=glyph_hint,
                            yield_lock=yield_vlm,
                        )
                        if bound:
                            _say(f"  {tag} bound UI glyph → {','.join(bound)}")
                        stick_cap = (
                            self.config.burst_ms
                            if (is_arrival(dino_boxes) and not prompt_on)
                            else self.config.stick_ms
                        )
                        action = apply_chord_duration(
                            action, burst_ms=self.config.burst_ms, stick_ms=stick_cap
                        )
                tool = action.tool
                query = str(parsed.get("query") or parsed.get("note") or "")
                allow_recall = self.config.arm in {"dump_all", "ratchet"}
                if allow_recall and tool == "recall":
                    hits = self.journal.recall(query)
                    self._carry_recall = hits
                    memory_hit = MemoryHit(ids=["journal"] * len(hits), snippets=hits)
                    _say(f"  {tag} recall   {len(hits)}  {query or '(all)'}")
                elif dump_all and tool == "remember":
                    note = str(parsed.get("note") or "").strip()
                    if note and not turn:
                        self.remember_calls += 1
                        self.journal.remember_note(note, step_id=step_id)
                        _say(f"  {tag} remember {len(note)}ch")
                    if note and self.memory is not None:
                        self.memory.remember(
                            before,
                            thought=note,
                            episode_id=episode_id,
                            step_id=step_id,
                        )
                if dump_all:
                    action.tool = tool
                elif allow_recall and tool == "recall":
                    action.tool = "recall"
                else:
                    action.tool = None

            if not interrupt:
                action = _strip_locked_face(action, locked=locked_target or yield_vlm)
            if turn:
                action = clamp_to_overlay(action, self._aim)
                two_stage_unresolved = (
                    self._two_stage()
                    and bool(str(vlm_hud.get("choice") or "").strip())
                    and is_empty_chord(action)
                )
                if is_empty_chord(action):
                    skip_fill = (
                        terminal_tool_action
                        or two_stage_unresolved
                        or not self._allow_legacy_empty_fill()
                        or (self._two_stage() and self._combat_screen_now())
                    )
                    if not skip_fill:
                        filled = fill_from_aim(
                            self._aim,
                            recent=recent_chords,
                            duration_ms=self.config.burst_ms,
                            hud={
                                "energy": self._obs_metrics.get("energy"),
                                "screen": host_screen or self._obs_metrics.get("screen"),
                            },
                        )
                        if filled is not None:
                            action = filled
                before_bind = self._attach_action_identity(action)
                listing = self._aim.listing() if self._aim is not None else None
                if (
                    not terminal_tool_action
                    and self._last_decision_schema != "v2"
                    and not self._skip_labeled_combat_fill(before_bind)
                ):
                    action = steer_to_labeled(
                        action,
                        self._aim,
                        thought=thought,
                        choice=str(vlm_hud.get("choice") or ""),
                        avoid=self._avoid_for_turn(
                            host_screen=host_screen,
                            tutorial_info=(
                                self._scene_desc.tutorial
                                if self._scene_desc is not None and hasattr(self._scene_desc, "tutorial")
                                else None
                            ),
                            listing=listing,
                            prev_screen=str(
                                self._obs_metrics.get("screen") or prev_metrics.get("screen") or ""
                            ),
                        ),
                        hud={
                            "energy": self._obs_metrics.get("energy"),
                            "screen": host_screen or self._obs_metrics.get("screen"),
                        },
                    )
                action = self._preserve_steered_identity(before_bind, action)
                if action.mark != before_bind.mark or action.target != before_bind.target:
                    _say(
                        f"  {tag} labeled  {format_chord(before_bind)} → {format_chord(action)}"
                    )
                if (
                    self._two_stage()
                    and not terminal_tool_action
                    and not self._combat_screen_now()
                    and self._last_decision_schema != "v2"
                ):
                    gate_actions = self._effective_legal_actions()
                    if gate_actions:
                        action, legal_reason = resolve_legal_chord(
                            action,
                            self._aim,
                            gate_actions,
                            recent=recent_chords,
                            duration_ms=int(action.duration_ms or self.config.burst_ms),
                            hud={
                                "energy": self._obs_metrics.get("energy"),
                                "screen": host_screen or self._obs_metrics.get("screen"),
                            },
                        )
                        if legal_reason:
                            self._step_illegal_blocked = True
                            self._last_legal_reason = legal_reason
                            self._pending_exec_error = legal_reason
                            _say(f"  {tag} illegal  {legal_reason}")
                executed_label = self._aim_label(action)
                if executed_label:
                    vlm_hud["choice"] = executed_label
                    self._last_choice_label = executed_label
                elif vlm_hud.get("choice"):
                    self._last_choice_label = str(vlm_hud["choice"])
                overlay_note = self.notebook.bind_overlay(
                    mark_id=action.mark,
                    label=executed_label,
                    listing=listing,
                    step_id=step_id,
                    screen=host_screen,
                )
                if overlay_note:
                    if not saved_note:
                        self.remember_calls += 1
                        _say(f"  {tag} note     {len(overlay_note)}ch")
                    if not vlm_hud.get("note"):
                        vlm_hud["note"] = overlay_note
                before_hover = action
                if not terminal_tool_action and self._last_decision_schema != "v2":
                    action = prefer_hover(action, self._aim, hovered=self._hovered_marks)
                if action.hover and not before_hover.hover:
                    _say(f"  {tag} hover    first on mk_{action.mark} (tooltip)")
                    if self._combat_screen_now():
                        _combat_trace(
                            "HOVER",
                            f"mark={action.mark} label={self._aim_label(action)!r} "
                            f"xy={action.click_x},{action.click_y}",
                        )
                if not terminal_tool_action and self._last_decision_schema != "v2":
                    action = self._promote_repeat_hover(action)
                steered = action if terminal_tool_action or self._last_decision_schema == "v2" else steer_off_danger(
                    action,
                    self._aim,
                    recent=recent_chords,
                    duration_ms=int(action.duration_ms or self.config.burst_ms),
                )
                if steered is not action and steered.mark != action.mark:
                    _say(
                        f"  {tag} skip     mk_{action.mark} ({'quit' if action.mark else 'danger'}) "
                        f"→ {format_chord(steered)}"
                    )
                    action = steered
                if not terminal_tool_action:
                    self._supersede_rejected_action_with_inspection(action, tag=tag)
                action = self._final_combat_dispatch_gate(
                    action,
                    inspect_requested=inspect_requested,
                    tool_only=tool_only,
                    tool_action_contract=tool_action_contract,
                    tool_inspection_contract=tool_inspection_contract,
                    recheck_only=recheck_only,
                    tag=tag,
                )
                if action.hover and action.mark is not None:
                    self._hovered_marks.add(int(action.mark))
            act_name = action.derive_name() or action.name or ""
            think_note = f"{think_ms:.0f}ms" if think_ms else ""
            _say(f"  {tag} thought  {think_note}  {_one_line(thought) or '(empty)'}")
            _say(f"  {tag} pad      {format_chord(action)}")
            self._live(
                phase="acting",
                step=step_id,
                episode_id=episode_id,
                thought=thought,
                action=act_name,
                skill_on=skill_on,
                active_skill_id=active_skill_id,
                message=act_name,
            )

            mid = self.fixture.grab_image()
            fast_v2_guard = bool(
                turn and self.fixture.id == "sts2" and self._last_decision_schema == "v2"
            )
            mid_metrics = (
                dict(self._obs_metrics) if fast_v2_guard else self.fixture.scorer(mid)
            )
            if self.fixture.terminal(mid_metrics, mid) == "fail":
                failed_while_thinking = True
                terminal = "fail"
            skip_reason: str | None = None
            if turn and self._pending_exec_error:
                skip_reason = self._pending_exec_error
            if turn and not skip_reason:
                screen_now = str(
                    self._obs_metrics.get("screen")
                    or self._host_stats.get("screen")
                    or ""
                ).strip().lower()
                if self._guard_non_combat_enemy_action(action, screen=screen_now):
                    skip_reason = "non-combat-enemy-target"
                    try:
                        self._rescan_scene(focus="off-combat-target")
                    except Exception:  # noqa: BLE001 — rescan is best-effort
                        pass
            if turn and not skip_reason:
                mid_view = self.fixture.observe_image(mid)
                mid_mad = mean_absdiff(before_thumb, roi_thumb(mid, 64))
                mid_gate = obs_gate if fast_v2_guard else self.fixture.act_gate(mid_view)
                skip_reason = stale_to_act(
                    obs_gate=obs_gate, now_gate=mid_gate, mad=mid_mad
                )

            executed: list[str] = []
            primitive_logs: list[PrimitiveLog] = []
            card_transaction: CardTransaction | None = None
            error: str | None = None
            exec_path = None
            peak_path = None
            burst_path = None
            t_action_start = time.perf_counter()
            t_action_end = t_action_start
            after = before
            peak = before
            discovery_contract = None
            want_act = (
                (is_chord(action) or (action.name and action.name not in {"idle", "end"}))
                and terminal == "alive"
                and not skip_reason
            )
            if skip_reason:
                _say(f"  {tag} stale    skip click ({skip_reason})")
                after = mid
                peak = after
                motion_note = ""
                if self._pending_exec_error:
                    error = self._pending_exec_error
                else:
                    error = f"stale-obs:{skip_reason}"
            elif want_act:
                discovery_contract = self._discovery_contract_for_action(action)
                exec_img = mid if turn else self.fixture.grab_image()
                exec_frame = encode_image(exec_img, longest_side=768, jpeg_quality=85)
                exec_path = self.store.frame_path(episode_id, step_id, "exec")
                exec_path.write_bytes(exec_frame.data)
                sampler: PeakSampler | None = None
                if self.fixture.controller is not None and not turn:
                    sampler = PeakSampler(
                        self.fixture.grab_image,
                        hz=self.config.peak_hz,
                        trail_s=self.config.peak_trail_s,
                    )
                    sampler.start()
                try:
                    if self.fixture.controller is not None:
                        self.fixture.controller.focus()
                        fg = "yes" if self.fixture.controller.capture.is_foreground() else "NO"
                        _say(f"  {tag} focus    {fg}  {foreground_title()!r}")
                    if turn and self._is_targeted_card_transaction(action):
                        if self._last_decision_schema == "v2":
                            current_plan = self._last_execution_plan
                            current_check = validate_stable_transaction(
                                self._screen_objects, current_plan
                            )
                            if not current_check.get("ok"):
                                error = str(current_check.get("reason") or "stale-snapshot")
                                after = self.fixture.grab_image()
                                self._last_execution_plan = {}
                                self._planned_hand_signature = None
                                self._force_perceive = True
                                if error != "stale-snapshot":
                                    self._invalidate_hand_inventory(reason=error)
                            else:
                                transaction = self._execute_card_transaction(
                                    action, prev_metrics=prev_metrics, tag=tag
                                )
                                action = transaction.action
                                executed = transaction.executed
                                after = transaction.after
                                primitive_logs = transaction.primitives
                                card_transaction = transaction.transaction
                                error = transaction.error
                        else:
                            transaction = self._execute_card_transaction(
                                action, prev_metrics=prev_metrics, tag=tag
                            )
                            action = transaction.action
                            executed = transaction.executed
                            after = transaction.after
                            primitive_logs = transaction.primitives
                            card_transaction = transaction.transaction
                            error = transaction.error
                    else:
                        if hasattr(self.fixture, "pending_aim_label"):
                            self.fixture.pending_aim_label = self._aim_label(action)
                        executed = self.fixture.execute_chord(action)
                        primitive_logs = [
                            PrimitiveLog(
                                phase="action",
                                name=name,
                                x=action.click_x,
                                y=action.click_y,
                            )
                            for name in executed
                        ]
                    self._all_actions.update(executed)
                    if action.click_x is not None and action.click_y is not None:
                        self._last_click = (float(action.click_x), float(action.click_y))
                except Exception as exc:  # noqa: BLE001
                    error = str(exc)
                t_action_end = time.perf_counter()
                if sampler is not None:
                    burst = sampler.finish()
                    peak, after = burst.peak, burst.after
                    if burst.sheet is not None and len(burst.frames) >= 2:
                        burst_frame = encode_image(burst.sheet, longest_side=768, jpeg_quality=85)
                        burst_path = self.store.frame_path(episode_id, step_id, "burst")
                        burst_path.write_bytes(burst_frame.data)
                    motion_note = burst.caption
                else:
                    try:
                        if turn and card_transaction is None:
                            after = self._confirm_after_action(tag=tag)
                        elif not turn:
                            after = self.fixture.grab_image()
                    except RuntimeError as exc:
                        error = str(exc)
                        after = before
                    peak = after
                    motion_note = ""
                    if (
                        turn
                        and sub_action_warrants_affordance_refresh(action, self._aim)
                        and error is None
                    ):
                        self._update_affordance_from_frames(exec_img, after)
                        self._sync_legal_actions()
            else:
                after = mid
                peak = after
                motion_note = ""
            verify_retries = 0
            if (
                turn
                and self._two_stage()
                and executed
                and not error
                and want_act
                and card_transaction is None
            ):
                action, executed, after, _verify_mad, verify_retries = self._verify_retry_combat(
                    action,
                    before=before,
                    after=after,
                    prev_metrics=prev_metrics,
                    executed=executed,
                    tag=tag,
                )
            sent = "+".join(executed) if executed else "(none)"
            if error:
                _say(f"  {tag} sent     {sent}  error={error}")
                if "No visible window" in error:
                    terminal = "fail"
                    failed_while_thinking = True
            else:
                _say(f"  {tag} sent     {sent}")
            if act_name:
                recent_chords.append(act_name)
                recent_chords = recent_chords[-8:]
            t_after = time.perf_counter()
            t0 = t_after
            after_thumb = roi_thumb(after, 64)
            mad64 = mean_absdiff(before_thumb, after_thumb)
            pixel_mad = mean_absdiff(roi_thumb(before, 256), roi_thumb(after, 256))
            pixel_changed = mad64 > 0.02
            after_frame = encode_image(after, longest_side=768, jpeg_quality=85)
            after_path = self.store.frame_path(episode_id, step_id, "after")
            after_path.write_bytes(after_frame.data)
            if peak is not after:
                peak_frame = encode_image(peak, longest_side=768, jpeg_quality=85)
                peak_path = self.store.frame_path(episode_id, step_id, "peak")
                peak_path.write_bytes(peak_frame.data)
            rich_turn_refresh = bool(
                turn and self._two_stage() and want_act and not skip_reason
            )
            metrics = (
                self._refresh_obs_after_action(after, prev_metrics=prev_metrics)
                if rich_turn_refresh
                else self.fixture.scorer(after)
            )
            if turn:
                choice = vlm_hud.get("choice")
                if isinstance(choice, str) and choice.strip():
                    metrics["choice"] = choice.strip()
                goal = vlm_hud.get("goal")
                if isinstance(goal, str) and goal.strip():
                    metrics["goal"] = goal.strip()
                note = vlm_hud.get("note")
                if isinstance(note, str) and note.strip():
                    metrics["note"] = note.strip()
                if rich_turn_refresh:
                    hovered_label = self._aim_label(action) if action.hover else ""
                    # Tooltip appearance can be spatially small; inspection is
                    # an explicit request, so OCR/re-perception must not depend
                    # on a global frame-difference threshold.
                    if action.hover:
                        discovery = (
                            self._record_discovery_hover(discovery_contract, before, after)
                            if discovery_contract is not None else None
                        )
                        self._reperceive_after_hover(
                            after, hovered_label=hovered_label, tag=tag, discovery=discovery
                        )
                    state_action, expected_effects = self._state_action_from_labels(
                        str(vlm_hud.get("choice") or ""), str(parsed.get("target") or "") if 'parsed' in locals() else ""
                    )
                    selected_id = None
                    if card_transaction is not None and card_transaction.selection_state == "confirmed" and state_action is not None:
                        selected_id = state_action.card_id
                    current_state = self._reconcile_game_state(
                        action=state_action, expected_effects=expected_effects, selected_card_id=selected_id,
                    )
                    if current_state is not None:
                        metrics["authoritative_state"] = {
                            "phase": current_state.phase.value,
                            "activity": current_state.underlying_activity.value,
                            "turn": current_state.turn.value,
                            "legal_actions": [item.kind for item in current_state.legal_actions],
                            "effect_verified": current_state.last_action.verified,
                            "observed_effects": list(current_state.last_action.observed_effects),
                        }
                        if card_transaction is not None and card_transaction.outcome == "effect_unverified":
                            if current_state.last_action.verified:
                                card_transaction.effect_verified = True
                                card_transaction.outcome = "success"
                                card_transaction.verification_evidence.extend(
                                    current_state.last_action.observed_effects
                                )
                            else:
                                error = error or "effect-unverified"
                # Rejected, tool-only, and no-op turns still need an
                # authoritative snapshot for audit/replay; do not log state
                # only when a controller action happened.
                if self._two_stage() and "authoritative_state" not in metrics:
                    current_state = self._reconcile_game_state()
                    if current_state is not None:
                        metrics["authoritative_state"] = {
                            "phase": current_state.phase.value,
                            "activity": current_state.underlying_activity.value,
                            "turn": current_state.turn.value,
                            "legal_actions": [item.kind for item in current_state.legal_actions],
                            "effect_verified": current_state.last_action.verified,
                            "observed_effects": list(current_state.last_action.observed_effects),
                        }
                if self._aim is not None:
                    metrics["aim_marks"] = self._aim.listing()
                if self._two_stage():
                    metrics["illegal_chord"] = bool(self._step_illegal_blocked)
                    metrics["recheck_count"] = int(self._step_recheck_count)
                    if verify_retries:
                        metrics["verify_retries"] = verify_retries
                    if self._perceive_retry_reason:
                        metrics["perceive_retry_reason"] = self._perceive_retry_reason
                    if self._last_targeting_opts is not None:
                        metrics["targeting_options"] = self._last_targeting_opts
                    scene = self._scene_desc
                    metrics["unresolved_count"] = (
                        len(getattr(scene, "unresolved", []) or []) if scene else 0
                    )
                    tracker = getattr(self, "_entity_tracker", None)
                    metrics["unresolved_object_age"] = (
                        tracker.max_unresolved_age() if tracker is not None else 0
                    )
                    if card_transaction is not None:
                        self._card_action_attempts += 1
                        if card_transaction.effect_verified:
                            self._verified_card_actions += 1
                        elif card_transaction.outcome in {
                            "source_unconfirmed", "target_required", "target_not_found",
                            "target_rebind_failed",
                            "effect_unverified", "stale", "dispatch_failed",
                        }:
                            self._false_card_transactions += 1
                    metrics["false_card_transactions"] = self._false_card_transactions
                    metrics["verified_action_rate"] = (
                        self._verified_card_actions / self._card_action_attempts
                        if self._card_action_attempts else 0.0
                    )
                    metrics["affordance_hit"] = affordance_hit_for_action(
                        action, self._effective_legal_actions()
                    )
            if turn and self._two_stage():
                prev_screen = str(prev_metrics.get("screen") or "")
                new_screen = str(metrics.get("screen") or "")
                outcome = "moved" if pixel_changed else "blocked"
                choice_label = self._last_choice_label or str(metrics.get("choice") or "")
                line = f"choice={choice_label!r} → {outcome}"
                if prev_screen and new_screen and prev_screen != new_screen:
                    line += f", screen {prev_screen}→{new_screen}"
                self._last_outcome = line
                if (
                    turn
                    and outcome == "blocked"
                    and choice_label
                    and not pixel_changed
                ):
                    allowed_controls, _allowed_targets = allowed_sets_from_controls(
                        getattr(self, "_reason_controls", None) or []
                    )
                    if choice_in_allowed_controls(choice_label, allowed_controls):
                        self._record_blocked_control_attempt(choice_label)
                if error:
                    self._last_execution_failure = {
                        "code": str(error),
                        "intent": dict(self._last_intent),
                        "transaction_outcome": (
                            card_transaction.outcome if card_transaction is not None else None
                        ),
                    }
                else:
                    self._last_execution_failure = None
                if not rich_turn_refresh:
                    self._host_stats = self._read_host_stats(after)
                if (
                    prev_screen == "tutorial"
                    and new_screen in {"combat", "map"}
                    and pixel_changed
                ):
                    tut = self._host_stats.get("tutorial") or {}
                    title = str(tut.get("title") or "tutorial")
                    self._overlay_state.record_dismissal(title=title, step_id=step_id)
                if (
                    str(metrics.get("screen") or "") == "combat"
                    and executed
                    and not skip_reason
                    and pixel_mad < 0.02
                    and not pixel_changed
                    and self._combat_resources_unchanged(
                        prev_metrics, metrics, self._prev_host_stats, self._host_stats
                    )
                ):
                    self._stuck_combat_steps += 1
                else:
                    self._stuck_combat_steps = 0
                metrics["stuck_combat_steps"] = self._stuck_combat_steps
                recovering = bool(
                    str(metrics.get("screen") or "") == "combat"
                    and not executed
                    and (self._last_epistemic_blockers or int(self._step_recheck_count) > 0)
                )
                if recovering:
                    self._perception_recovery_steps += 1
                else:
                    self._perception_recovery_steps = 0
                metrics["perception_recovery_steps"] = self._perception_recovery_steps
                if self._overlay_state.screen_disagreements:
                    metrics["screen_disagreement"] = self._overlay_state.screen_disagreements[-1]
                if self._stuck_combat_steps >= 2:
                    self._break_nochange_loop(
                        tag=tag,
                        chord=act_name or "",
                        streak=self._stuck_combat_steps,
                    )
                    self._stuck_combat_steps = 0
                if (
                    not pixel_changed
                    and choice_label
                    and getattr(getattr(self, "_control_suppression", None), "last_log_reason", "")
                ):
                    metrics["control_suppression"] = self._control_suppression.last_log_reason
                if pixel_changed and choice_label:
                    screen_name = new_screen or prev_screen or "other"
                    saved = self.notebook.ingest(
                        {"note": f"{choice_label} on {screen_name} → moved"},
                        thought=thought,
                        step_id=step_id,
                        screen=screen_name,
                        aim_listing=self._aim.listing() if self._aim is not None else None,
                    )
                    if saved:
                        self.remember_calls += 1
            if turn:
                after_hint = InteractHint(on=False)
                after_text = ""
                prompt_after = False
            else:
                cv_after = prompt_visible(after)
                cv_glyph_after = prompt_glyph(after) if cv_after else None
                after_hint = detect_interact(
                    after,
                    detector=det if dino_ok else None,
                    cv_on=cv_after,
                    cv_glyph=cv_glyph_after,
                )
                after_text = read_prompt_text(after)
                prompt_after = prompt_confirmed(after_hint, after_text)
            after_ms = _ms(t0)
            outcome = compute_step_outcome(
                before=before,
                after=after,
                action=action,
                proposed=proposed,
                prompt_before=prompt_on,
                prompt_after=prompt_after,
                pixel_changed=pixel_changed,
            )
            self._carry_after = after.copy()
            if turn:
                self._prev_frame_image = after.copy()
            caption = result_caption(
                chord=act_name or sent,
                mad=pixel_mad,
                pixel_changed=pixel_changed,
                prompt_before=prompt_on,
                prompt_after=prompt_after,
            )
            self._carry_caption = f"{motion_note}; {caption}" if motion_note else caption

            if terminal == "alive":
                terminal = self.fixture.terminal(metrics, after)

            if turn:
                delta = turn_reward(
                    prev_metrics,
                    metrics,
                    action,
                    StepResult(pixel_changed=pixel_changed),
                    terminal=terminal if terminal != "alive" else "alive",
                )
                if delta > 0:
                    reward_listing = self._aim.listing() if self._aim is not None else None
                    reward_screen = overlay_screen(
                        str(prev_metrics.get("screen") or "") or None,
                        reward_listing,
                    ) or "other"
                    reward_label = self._last_choice_label or self._aim_label(action) or ""
                    self.notebook.promote_turn_reward(
                        screen=reward_screen,
                        label=reward_label,
                        device=act_name or action.derive_name() or "pt",
                        delta=delta,
                        prev_metrics=prev_metrics,
                        metrics=metrics,
                        step_id=step_id,
                    )
            else:
                delta = score_delta(prev_metrics, metrics, terminal if terminal != "alive" else "alive")
                delta += explore_progress(
                    view_changed=bool(outcome.view_changed),
                    new_cell=False,
                    new_prompt=bool(confirmed and not last_had_prompt),
                )
            cumulative += delta
            from_screen = str(prev_metrics.get("screen") or "") or "other"
            prev_metrics = metrics

            action_name = action.name
            if action_name in {"start", "back", "escape"}:
                menu_streak += 1
            elif action_name:
                menu_streak = 0

            flags = audit_step(
                metrics=metrics,
                pixel_changed=pixel_changed,
                failed_while_thinking=failed_while_thinking,
                action_name=action_name,
                menu_streak=menu_streak,
            )
            self._session_flags.extend(flags)

            if dump_all and self.memory is not None and action_name and not turn:
                self.remember_calls += 1
                self.memory.remember(
                    after,
                    thought=thought,
                    action=action_name,
                    outcome=terminal,
                    episode_id=episode_id,
                    step_id=step_id,
                )

            record = StepLog(
                session_id=self.session_id,
                episode_id=episode_id,
                step_id=step_id,
                ts=_now(),
                fixture=self.fixture.id,
                observation=Observation(
                    screenshot_ref=str(obs_path.relative_to(self.store.run_dir)),
                    exec_screenshot_ref=(
                        str(exec_path.relative_to(self.store.run_dir)) if exec_path else None
                    ),
                    after_screenshot_ref=str(after_path.relative_to(self.store.run_dir)),
                    ui_screenshot_ref=(
                        str(ui_path.relative_to(self.store.run_dir)) if ui_path else None
                    ),
                    peak_screenshot_ref=(
                        str(peak_path.relative_to(self.store.run_dir)) if peak_path else None
                    ),
                    burst_screenshot_ref=(
                        str(burst_path.relative_to(self.store.run_dir)) if burst_path else None
                    ),
                    dino_screenshot_ref=(
                        str(dino_path.relative_to(self.store.run_dir)) if dino_path else None
                    ),
                    vlm_screenshot_ref=str(vlm_path.relative_to(self.store.run_dir)),
                    layout_source=("sts2-content-box" if turn and self.fixture.id == "sts2" else None),
                    layout_crop=(
                        tuple(float(v) for v in getattr(self.fixture, "_content_box", ()))
                        if turn
                        and self.fixture.id == "sts2"
                        and len(getattr(self.fixture, "_content_box", ())) == 4
                        else None
                    ),
                ),
                dino=boxes_payload(dino_boxes),
                prompt_on=prompt_on,
                prompt_cv=prompt_cv,
                prompt_glyph=glyph_hint,
                prompt_source=hint.source or None,
                prompt_source_after=after_hint.source or None,
                prompt_text=prompt_text,
                thought=thought,
                proposed=proposed,
                action=action,
                result=StepResult(
                    executed=executed,
                    primitives=primitive_logs,
                    card_transaction=card_transaction,
                    error=error,
                    pixel_changed=pixel_changed,
                    pixel_mad=pixel_mad,
                    view_changed=outcome.view_changed,
                    view_mad=outcome.view_mad,
                    column_shift=outcome.column_shift,
                    prompt_cleared=outcome.prompt_cleared,
                    stall=outcome.stall,
                    executed_as_proposed=outcome.executed_as_proposed,
                    invoked_gsm=invoked_gsm,
                    gsm_ticks=gsm_ticks,
                    authoritative_state=dict(metrics.get("authoritative_state") or {}),
                    observed_effects=list((self._game_state.last_action.observed_effects if self._game_state is not None else ())),
                ),
                metrics=metrics,
                score_delta=delta,
                cumulative_score=cumulative,
                terminal=terminal,
                think_ms=think_ms or None,
                perceive_ms=perceive_ms,
                grab_ms=grab_ms,
                saliency_ms=saliency_ms,
                dino_full_ms=dino_full_ms,
                dino_inset_ms=dino_inset_ms,
                ocr_ms=ocr_ms,
                encode_ms=encode_ms,
                after_ms=after_ms,
                obs_to_action_ms=(t_action_start - t_obs) * 1000,
                action_to_after_ms=(t_after - t_action_end) * 1000,
                obs_age_ms=(t_action_start - t_obs) * 1000,
                idle_during_infer=self.fixture.controller is not None,
                failed_while_thinking=failed_while_thinking,
                memory=memory_hit,
                policy_id=self._step_policy_id(),
                prompt_id=self._prompt_id_for_step(),
                notebook_schema=NOTEBOOK_SCHEMA_VERSION,
                user_prompt_text=self.last_user_prompt,
                notebook_snapshot=self._last_notebook_snapshot,
                architecture=self.config.architecture if self._turn_based() else "overlay",
                decision_schema=self._last_decision_schema,
                snapshot_id=self._last_snapshot_id,
                turn_snapshot=dict(self._last_turn_snapshot),
                intent=dict(self._last_intent),
                execution_plan=dict(self._last_execution_plan),
                reason_backend=self._last_reason_backend,
                reason_fallback=self._last_reason_fallback,
                reason_call=dict(self._last_reason_call),
                perception_schema_version=(
                    self._perception_schema_version() if self._two_stage() else ""
                ),
                reasoner_role=self._last_reasoner_role if self._two_stage() else "",
                router_reason=self._last_router_reason if self._two_stage() else "",
                epistemic_blockers=(
                    list(self._last_epistemic_blockers) if self._two_stage() else []
                ),
                scene=self._last_scene if self._two_stage() else "",
                reason_ms=self._reason_ms or None if self._two_stage() else None,
                discover_ms=self._last_discover_ms or None if self._two_stage() else None,
                perceive_raw=self._last_perceive_raw if self._two_stage() else "",
                reason_raw=self._last_reason_raw if self._two_stage() else "",
                tool_trace=list(self._last_tool_trace) if self._two_stage() else [],
                tool_events=list(self._last_tool_events) if self._two_stage() else [],
                llm1_raw=self._last_llm1_raw if self._two_stage() else "",
                validated_perception=self._last_validated_perception if self._two_stage() else "",
                grounded_scene=self._last_grounded_scene if self._two_stage() else "",
                llm1_schema_attempts=self._last_llm1_schema_attempts if self._two_stage() else 0,
                llm1_first_pass_valid=self._last_llm1_first_pass_valid if self._two_stage() else None,
                llm1_final_schema_valid=self._last_llm1_final_schema_valid if self._two_stage() else None,
                llm1_attempts=list(self._last_llm1_attempts) if self._two_stage() else [],
                route_before_discovery=self._route_before_discovery if self._two_stage() else "",
                route_after_discovery=self._route_after_discovery if self._two_stage() else "",
                entity_route_facts=list(self._entity_route_facts) if self._two_stage() else [],
                perception_source=self._turn_perception_source if self._two_stage() else "other",
                llm1_called_this_turn=self._turn_llm1_called if self._two_stage() else False,
                llm1_reused_from_step=(
                    self._turn_llm1_reused_from_step if self._two_stage() else None
                ),
                reasoner_called_this_turn=(
                    self._turn_reasoner_called if self._two_stage() else False
                ),
                explicit_mcp_tools_called=(
                    registry_tool_names(self._last_tool_events) if self._two_stage() else []
                ),
                paused=self.config.paused,  # type: ignore[arg-type]
                arm=self.config.arm,
                skill_on=skill_on,
                active_skill_id=active_skill_id,
            )
            steps.append(record)
            self.store.write_step(record)
            pixels = "yes" if pixel_changed else "no"
            last_pixels_changed = pixel_changed
            last_mad = pixel_mad
            last_had_prompt = confirmed
            self._last_interrupt_glyph = update_last_interrupt_glyph(
                self._last_interrupt_glyph,
                interrupt=interrupt,
                glyph=glyph_hint,
                prompt_on=bool(prompt_after) if prompt_after is not None else prompt_on,
                moved=bool(action.stick_l or action.stick_r),
            )
            self._last_stall = outcome.stall
            glyphs = extract_prompt_devices(thought) if confirmed else []
            if glyph_hint and glyph_hint not in glyphs and confirmed:
                glyphs = [glyph_hint, *glyphs]
            face_only = (act_name or "") in {"a", "b", "x", "y"}
            # Jump/hop while the box stays up is not exploration progress.
            credit_pixels = pixel_changed and not (face_only and prompt_after)
            self.working.add(
                chord=act_name,
                pixels=credit_pixels,
                glyphs=glyphs,
                mad=pixel_mad,
                prompt=confirmed,
                stick_l=action.stick_l,
                stick_r=action.stick_r,
                duration_ms=action.duration_ms,
                column_shift=outcome.column_shift,
            )
            if (
                not turn
                and self.working.pose.visits.get(self.working.pose.cell, 0) == 1
            ):
                bump = explore_progress(new_cell=True)
                if bump:
                    record.score_delta = float(record.score_delta or 0.0) + bump
                    cumulative += bump
                    record.cumulative_score = cumulative
            device = face_device(action)
            learned = False
            if device:
                try_out = face_try_outcome(
                    prompt_on=prompt_on,
                    prompt_cleared=bool(outcome.prompt_cleared),
                    prompt_after=bool(prompt_after),
                    confirmed=confirmed,
                    pixel_mad=pixel_mad,
                    view_changed=bool(outcome.view_changed),
                )
                self.journal.record_try(
                    kind=journal_kind or "prompt",
                    heading=journal_heading,
                    prompt=prompt_text,
                    glyph=glyph_hint or device,
                    device=device,
                    outcome=try_out,
                    locked=yield_vlm or try_out == "lock",
                    step_id=step_id,
                )
            elif turn and action.click_x is not None and action.click_y is not None:
                listing = self._aim.listing() if self._aim is not None else None
                aim_label = self._aim_label(action)
                next_screen = str(metrics.get("screen") or "") or "other"
                click_screen = overlay_screen(from_screen, listing) or "other"
                click_fact = self.journal.record_click(
                    screen=click_screen,
                    x=float(action.click_x),
                    y=float(action.click_y),
                    device=act_name or action.derive_name() or "pt",
                    pixel_changed=pixel_changed,
                    step_id=step_id,
                    label=aim_label,
                    hovered=bool(action.hover),
                    next_screen=next_screen,
                )
                if click_fact is not None:
                    self.notebook.promote_click(
                        screen=click_screen,
                        label=aim_label,
                        device=act_name or action.derive_name() or "pt",
                        outcome=click_fact.outcome,
                        prev_screen=str(from_screen or ""),
                        next_screen=next_screen,
                        prev_metrics=prev_metrics,
                        metrics=metrics,
                        step_id=step_id,
                    )
                if (
                    turn
                    and self._two_stage()
                    and not pixel_changed
                    and pixel_mad < 0.02
                    and action.click_x is not None
                    and action.click_y is not None
                ):
                    clicked_screen = screen_from_labels(
                        (m.label for m in (self._aim.marks if self._aim is not None else ())),
                        fallback=from_screen,
                    )
                    self.journal.lexicon.forget(
                        clicked_screen,
                        float(action.click_x),
                        float(action.click_y),
                    )
                    if action.mark is not None:
                        self._blocked_marks.add(f"mk_{int(action.mark)}")
                    elif self._aim is not None:
                        for mark in self._aim.marks:
                            if abs(mark.x - float(action.click_x)) < 0.05 and abs(
                                mark.y - float(action.click_y)
                            ) < 0.05:
                                self._blocked_marks.add(f"mk_{mark.id}")
                                break
                hover_words = None
                if action.hover and pixel_changed:
                    hover_words = [
                        (m.x, m.y, m.label) for m in ocr_click_marks(after, cap=16)
                    ]
                clicked_screen = screen_from_labels(
                    (m.label for m in (self._aim.marks if self._aim is not None else ())),
                    fallback=from_screen,
                )
                learned = self.journal.discover_overlay(
                    screen=clicked_screen,
                    next_screen=next_screen,
                    x=float(action.click_x),
                    y=float(action.click_y),
                    clicked_label=aim_label,
                    pixel_changed=pixel_changed,
                    hovered=bool(action.hover),
                    words=hover_words,
                )
            elif outcome.stall and action.stick_l:
                self.journal.observe(
                    kind=journal_kind or "walk",
                    heading=action.stick_l,
                    prompt=prompt_text,
                    glyph=glyph_hint,
                    outcome="blocked",
                    step_id=step_id,
                )
            if turn:
                self._update_selected(
                    action,
                    metrics=metrics,
                    prev_metrics=prev_metrics,
                    pixel_mad=pixel_mad,
                    pixel_changed=pixel_changed,
                    selection_state=(
                        card_transaction.selection_state if card_transaction is not None else None
                    ),
                )
                if (
                    not is_tool
                    and str(self._game_state.underlying_activity.value if self._game_state else "") == "combat"
                    and not self._state_allows_combat_action(choice, target_label)
                ):
                    # Frame geometry and LLM prose are insufficient to create a card target.
                    allowed_choice = False
                    self._last_outcome = "state-gate: requested card/target is not a typed current-frame object"
                listing = self._aim.listing() if self._aim is not None else None
                next_screen = str(metrics.get("screen") or "") or "other"
                claims = self.journal.ingest_ocr(
                    overlay_screen(next_screen, listing) or next_screen,
                    str(metrics.get("ocr") or ""),
                    dialog=listing_has_dialog(listing),
                )
                self.notebook.observe_step(
                    screen=overlay_screen(next_screen, listing) or next_screen,
                    prev_screen=overlay_screen(from_screen, listing) or from_screen,
                    reward=float(delta or 0.0),
                    pixel_changed=pixel_changed,
                    new_fact=bool(learned or claims or saved_note),
                    listing=listing,
                    hovered=self._hovered_marks,
                )
            if action.stick_l:
                self._last_walk = action.stick_l
            _say(
                f"  {tag} after    mad={pixel_mad:.3f} pixels={pixels}"
                f" view={int(outcome.view_changed)} stall={int(outcome.stall)}"
                f"  {_hud_line(metrics)}  {terminal}"
            )
            if metrics.get("choice"):
                _say(f"  {tag} choice   {_one_line(str(metrics.get('choice')))}")
            if turn and self._two_stage() and self._combat_screen_now():
                action_ms = (t_action_end - t_action_start) * 1000.0 if want_act else 0.0
                self._log_combat_action_result(
                    action=action,
                    metrics=metrics,
                    pixel_changed=pixel_changed,
                    error=error,
                    executed=executed,
                )
                self._log_combat_timing(
                    perceive_ms=perceive_ms,
                    reason_ms=getattr(self, "_reason_ms", 0.0),
                    think_ms=think_ms,
                    action_ms=action_ms,
                    discover_ms=getattr(self, "_last_discover_ms", 0.0),
                )
            if turn and hud_identity_stuck(steps, window=self.config.stuck_window):
                terminal = "timeout"
                _say(
                    f"  {tag} stuck    HUD frozen "
                    f"{self.config.stuck_window} steps  {_hud_line(metrics)}"
                )
            self._live(
                phase="scored",
                step=step_id,
                episode_id=episode_id,
                thought=thought,
                action=act_name,
                metrics=metrics,
                terminal=terminal,
                skill_on=skill_on,
                active_skill_id=active_skill_id,
                message=terminal,
            )
            if terminal != "alive":
                break

        if terminal == "alive":
            terminal = "timeout"
        if terminal == "fail" and self.fixture.id == "sts2":
            lesson = self._death_lesson(steps)
            if lesson:
                self.notebook.write_lesson(lesson)
        try:
            self.notebook.save_grounded(self._grounded_path)
        except OSError:
            pass
        memory_carry["grounded_end"] = len(self.notebook.grounded.facts)
        memory_carry["lessons_end"] = len(self.notebook.grounded.lessons)
        fill_return_to_go(steps)
        ep_flags = audit_episode(steps=steps)
        if wall_timeout:
            ep_flags.append("wall_clock_timeout")
        elif terminal == "timeout" and "hud_identity_stuck" not in ep_flags:
            ep_flags.append("step_budget_timeout")
        self._session_flags.extend(ep_flags)
        unique = sorted({name for row in steps if row.action.name for name in [row.action.name]})
        success = self.fixture.success(steps, terminal)  # type: ignore[arg-type]
        for record in steps:
            record.success = success
        if steps:
            self.store.replace_episode_steps(episode_id, steps)
        softlock = "stuck_no_pixel_change" in ep_flags or "hud_identity_stuck" in ep_flags
        reset: Literal["in_game", "snapshot", "abort"] = "in_game"
        skip_reset = self.config.until_fail and terminal in {"fail", "win"}
        if not skip_reset and (terminal == "fail" or (softlock and self.config.snapshot_on_softlock)):
            reset = self.fixture.after_fail(
                softlock=softlock,
                eval_mode=self.config.eval_snapshot,
            )
        episode = EpisodeLog(
            session_id=self.session_id,
            episode_id=episode_id,
            fixture=self.fixture.id,
            steps=len(steps),
            return_value=cumulative,
            return_to_go=steps[0].return_to_go if steps else 0.0,
            terminal=terminal,  # type: ignore[arg-type]
            unique_actions=unique,
            audit_flags=sorted(set(ep_flags)),
            reset=reset,
            arm=self.config.arm,
            skill_on=skill_on,
            active_skill_id=active_skill_id,
            success=success,
            memory_carry=memory_carry,
        )
        self.store.write_episode(episode)
        if self.fixture.id == "sts2":
            write_train_jsonl(self.store.run_dir)
            write_sft_jsonl(self.store.run_dir)
        self._episode_returns.append(cumulative)
        self._episode_success.append(success)
        _say(
            f"episode {self._episode_n} ended  {terminal}  "
            f"steps={len(steps)}  success={success}  return={cumulative:.3f}"
        )
        return episode
