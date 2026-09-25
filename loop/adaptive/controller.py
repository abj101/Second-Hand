"""Product react loop controller; benchmark adapters plug in via ``Environment.step``."""

from __future__ import annotations

import asyncio
import io
import json
import time
import uuid
from dataclasses import asdict, dataclass, replace
from hashlib import sha256
from PIL import Image

from loop.adaptive.candidates import (
    CandidateAction,
    CatalogCompileError,
    Directive,
    MaskGateContext,
    compile_catalog_from_surface,
    compile_mask,
    TransientClickPlan,
    mask_digest,
    select_if_unambiguous,
)
from loop.adaptive.contracts import (
    ObservationQuality,
    OperatingState,
    OperatingTransition,
    TransitionFacts,
    transition_operating_state,
)
from loop.adaptive.environment import StepResult
from loop.adaptive.executor import Executor, NativeDispatchContext
from loop.adaptive.policy import (
    DecisionError,
    DeterministicPolicy,
    StructuredDecision,
    reactive_state_from_native,
    validate_decision,
)
from loop.adaptive.decider_policy import DeciderPolicy, scoring_policy_source
from loop.adaptive.qwen_policy import QwenReactionPolicy, prepare_qwen_worker_crop
from loop.adaptive.schema import SceneGraph, SceneNode
from loop.adaptive.policy_worker import PolicyTurnStatus, PolicyWorker
from loop.adaptive.snapshot import assert_no_privileged, build_native_observation, snapshot_payload
from loop.adaptive.schema import AdaptiveSchema, StateSnapshot
from loop.adaptive.orchestrator import (
    OrchestratorConfig,
    SyncOrchestratorRunner,
    ensure_directive,
    maybe_screen_steer_wake,
    orchestrator_worker,
)
from loop.adaptive.telemetry import DecisionRow, NativeShadowRow, TelemetryStore, percentile

# Marks a synthetic caller that supplied no acquisition contract (no clock).
# The live shadow route and cli always supply capture acquisition metadata; the
# armed/experiment routes are wired in B3.  Legacy fixtures keep prior behavior.

__all__ = [
    "GenerationStore",
    "NativeShadowConfig",
    "NativeShadowLoop",
    "ReactConfig",
    "orchestrator_worker",
    "reset_barrier",
    "run_episode",
]


class GenerationStore:
    def __init__(self) -> None:
        self._value = None
        self._generation = -1

    def publish(self, generation: int, value) -> None:
        self._generation = generation
        self._value = value

    def latest(self):
        return self._value

    def invalidate_prior_generation(self, generation: int) -> None:
        self._value = None
        self._generation = generation


async def reset_barrier(
    env,
    perception,
    executor: Executor,
    directive_store: GenerationStore,
    decision_store: GenerationStore,
    *,
    seed: int,
    episode_id: str,
    episode_generation: int,
) -> StepResult:
    step_result = await env.reset(seed, episode_id, episode_generation)
    perception.reset()
    executor.reset()
    executor.release_all(env, count=False)
    directive_store.invalidate_prior_generation(episode_generation)
    decision_store.invalidate_prior_generation(episode_generation)
    return step_result


@dataclass
class ReactConfig:
    decision_deadline_s: float = 0.25
    snapshot_max_age_s: float = 0.5
    directive_ttl_s: float = 30.0
    default_intent: str = "progress while surviving"
    max_steps: int | None = None


def _directive_dict(d: Directive) -> dict:
    return asdict(d)


def _decision_dict(d: StructuredDecision) -> dict:
    return asdict(d)


def _filtered_candidates(
    catalog,
    mask,
    *,
    include_transient: bool = False,
) -> tuple[CandidateAction, ...]:
    allowed = set(mask.allowed_ids)
    transient_ids = (
        set()
        if include_transient
        else {plan.action_id for plan in getattr(catalog, "transient_plans", ())}
    )
    return tuple(
        c for c in catalog.candidates if c.action_id in allowed and c.action_id not in transient_ids
    )


def _wait_id(catalog) -> str | None:
    ids = {c.action_id for c in catalog.candidates if c.available}
    for name in ("wait", "noop"):
        if name in ids:
            return name
    return None


def _frame_bytes(observation: object) -> bytes | None:
    if isinstance(observation, Image.Image):
        buf = io.BytesIO()
        observation.save(buf, format="PNG")
        return buf.getvalue()
    if isinstance(observation, (bytes, bytearray)):
        return bytes(observation)
    return None


async def run_episode(
    env,
    perception,
    policy,
    executor: Executor,
    telemetry: TelemetryStore,
    directive_store: GenerationStore,
    decision_store: GenerationStore,
    *,
    seed: int,
    episode_id: str,
    episode_generation: int,
    config: ReactConfig | None = None,
    events: asyncio.Queue | None = None,
    focused: bool = True,
) -> dict:
    cfg = config or ReactConfig()
    mode = "observe"
    events = events or asyncio.Queue()
    executor.snapshot_max_age_s = cfg.snapshot_max_age_s
    executor.decision_max_age_s = cfg.decision_deadline_s

    obs = await reset_barrier(
        env,
        perception,
        executor,
        directive_store,
        decision_store,
        seed=seed,
        episode_id=episode_id,
        episode_generation=episode_generation,
    )
    if getattr(policy, "catalog_revision", None) == "":
        policy.catalog_revision = env.actions().revision

    now = time.monotonic()
    if directive_store.latest() is None:
        directive_store.publish(
            episode_generation,
            Directive(
                revision=1,
                episode_generation=episode_generation,
                intent=cfg.default_intent,
                ttl_s=cfg.directive_ttl_s,
                published_at=now,
            ),
        )

    lat_p: list[float] = []
    lat_pol: list[float] = []
    lat_e2e: list[float] = []
    invalidation_failures = 0
    loop_steps = 0
    hit_max_steps = False
    halt_reason: str | None = None

    def _emit(
        snap,
        directive,
        mask,
        filtered,
        decision,
        policy_s: float,
        t0: float,
        *,
        dispatched_id: str | None,
        executed_id: str | None,
        next_snap_dict: dict | None,
    ) -> None:
        lat_e2e.append(time.monotonic() - t0)
        telemetry.append(
            DecisionRow(
                episode_id=obs.episode_id,
                episode_generation=episode_generation,
                seed=seed,
                step=obs.step,
                environment_revision=obs.environment_revision,
                action_catalog_revision=obs.action_catalog_revision,
                frame_digest=snap.frame_digest,
                frame_history_digests=snap.frame_history_digests,
                schema_revision_id=snap.schema_revision_id,
                snapshot=snapshot_payload(snap),
                perception_confidence=snap.confidence,
                observation_age_s=snap.observation_age_s,
                directive=_directive_dict(directive),
                mask_allowed_ids=mask.allowed_ids if mask is not None else (),
                candidates=tuple(asdict(c) for c in filtered),
                decision=_decision_dict(decision) if decision is not None else {},
                selected_action_id=decision.candidate_id if decision is not None else None,
                dispatched_action_id=dispatched_id,
                executed_action_id=executed_id,
                next_snapshot=next_snap_dict,
                score=obs.score,
                score_delta=obs.score_delta,
                reward=obs.reward,
                terminated=obs.terminated,
                truncated=obs.truncated,
                termination_reason=obs.termination_reason,
                capture_ts=obs.capture_ts,
                dispatch_ts=obs.dispatch_ts,
                latency_perception_s=lat_p[-1] if lat_p else 0.0,
                latency_policy_s=policy_s,
                latency_e2e_s=lat_e2e[-1],
            )
        )

    async def _safe_step(catalog):
        wid = _wait_id(catalog)
        if wid is None:
            return None
        result = await env.step(wid)
        return wid, result

    while not (obs.terminated or obs.truncated):
        if cfg.max_steps is not None and loop_steps >= cfg.max_steps:
            hit_max_steps = True
            break
        mode = "qualify"
        t0 = time.monotonic()
        snap = perception.qualify(obs)
        assert_no_privileged(snap)
        lat_p.append(time.monotonic() - t0)
        raw = _frame_bytes(obs.observation)
        if raw:
            telemetry.put_frame(snap.frame_digest, raw)

        directive = directive_store.latest()
        if directive is None:
            mode = "recover"
            halt_reason = "directive_missing"
            catalog = env.actions()
            await events.put({"kind": "halt"})
            executor.release_all(env)
            stepped = await _safe_step(catalog)
            if stepped:
                _, obs = stepped
                loop_steps += 1
            break

        catalog = env.actions()
        mask = compile_mask(catalog, directive)
        filtered = _filtered_candidates(catalog, mask)
        decision = None
        policy_s = 0.0

        if snap.confidence < 0.05:
            mode = "recover"
            await events.put({"kind": "low_confidence"})
            executor.release_all(env)
            stepped = await _safe_step(catalog)
            executed = None
            if stepped:
                executed, obs = stepped
                loop_steps += 1
            _emit(
                snap,
                directive,
                mask,
                filtered,
                decision,
                policy_s,
                t0,
                dispatched_id=None,
                executed_id=executed,
                next_snap_dict=None,
            )
            if stepped is None:
                break
            continue

        mode = "map"
        if len(mask.allowed_ids) == 0:
            mode = "recover"
            executor.release_all(env)
            await events.put({"kind": "halt"})
            _emit(
                snap,
                directive,
                mask,
                filtered,
                decision,
                policy_s,
                t0,
                dispatched_id=None,
                executed_id=None,
                next_snap_dict=None,
            )
            break

        mode = "react"
        t_pol = time.monotonic()
        unambig = select_if_unambiguous(mask)
        if unambig is not None:
            decision = StructuredDecision(
                candidate_id=unambig,
                confidence=1.0,
                probabilities={unambig: 1.0},
                decided_at=time.monotonic(),
                episode_generation=snap.episode_generation,
                snapshot_id=snap.snapshot_id,
                directive_revision=directive.revision,
                catalog_revision=catalog.revision,
            )
        else:
            decision = policy.score(snap, directive, filtered)
            validate_decision(decision, mask, catalog, snap, directive)
        policy_s = time.monotonic() - t_pol
        lat_pol.append(policy_s)
        decision_store.publish(episode_generation, decision)

        executed_id: str | None = None
        dispatched_id: str | None = None
        next_snap_dict = None

        if policy_s > cfg.decision_deadline_s:
            mode = "recover"
            executor.release_all(env)
            await events.put({"kind": "timeout"})
            stepped = await _safe_step(catalog)
            if stepped:
                executed_id, obs = stepped
                loop_steps += 1
            _emit(
                snap,
                directive,
                mask,
                filtered,
                decision,
                policy_s,
                t0,
                dispatched_id=None,
                executed_id=executed_id,
                next_snap_dict=None,
            )
            if stepped is None:
                break
            continue

        now_d = time.monotonic()
        aged = replace(
            snap,
            observation_age_s=max(0.0, now_d - snap.captured_at),
        )
        receipt = executor.dispatch(
            decision,
            aged,
            catalog,
            directive,
            now=now_d,
            focused=focused,
            terminal=obs.terminated or obs.truncated,
        )
        if not receipt.accepted:
            invalidation_failures += 1
            executor.release_all(env)
            await events.put({"kind": receipt.reason})
            stepped = await _safe_step(catalog)
            if stepped:
                executed_id, obs = stepped
                loop_steps += 1
            _emit(
                snap,
                directive,
                mask,
                filtered,
                decision,
                policy_s,
                t0,
                dispatched_id=None,
                executed_id=executed_id,
                next_snap_dict=None,
            )
            if stepped is None:
                break
            continue

        obs = await env.step(receipt.action_id)
        dispatched_id = receipt.action_id
        executed_id = receipt.action_id
        loop_steps += 1
        if not (obs.terminated or obs.truncated):
            ns = perception.qualify(obs)
            assert_no_privileged(ns)
            next_snap_dict = snapshot_payload(ns)

        _emit(
            snap,
            directive,
            mask,
            filtered,
            decision,
            policy_s,
            t0,
            dispatched_id=dispatched_id,
            executed_id=executed_id,
            next_snap_dict=next_snap_dict,
        )

        if obs.terminated or obs.truncated:
            mode = "evaluate"
            break

    reason = obs.termination_reason
    truncated = obs.truncated
    if halt_reason and reason is None:
        reason = halt_reason
        mode = "recover"
    if hit_max_steps and not obs.terminated:
        truncated = True
        if reason is None:
            reason = "max_steps"
        mode = "evaluate"

    return {
        "score": obs.score,
        "steps": loop_steps,
        "ticks": obs.tick,
        "terminated": obs.terminated,
        "truncated": truncated,
        "termination_reason": reason,
        "watchdog_releases": executor.release_count,
        "invalidation_failures": invalidation_failures,
        "latency_perception_p50": percentile(lat_p, 50),
        "latency_perception_p95": percentile(lat_p, 95),
        "latency_policy_p50": percentile(lat_pol, 50),
        "latency_policy_p95": percentile(lat_pol, 95),
        "latency_e2e_p50": percentile(lat_e2e, 50),
        "latency_e2e_p95": percentile(lat_e2e, 95),
        "mode": mode,
        "environment_revision": obs.environment_revision,
    }


def _frozen_scene(graph: SceneGraph | None) -> SceneGraph | None:
    """Copy a scene graph for a lease that must not follow later frames.

    ``SceneGraph``/``SceneNode`` are frozen, but ``attributes`` is a plain
    dict, so the copy takes fresh dicts instead of sharing mutable state with
    the live frame the assembler keeps rebuilding.
    """

    if graph is None:
        return None
    return SceneGraph(
        snapshot_id=graph.snapshot_id,
        nodes=tuple(
            SceneNode(node.node_id, node.bbox, node.evidence_ids, dict(node.attributes))
            for node in graph.nodes
        ),
        relations=tuple(graph.relations),
    )


@dataclass
class NativeShadowConfig:
    decision_deadline_s: float = 0.25
    snapshot_max_age_s: float = 0.5
    directive_ttl_s: float = 30.0
    default_intent: str = ""
    game_brief: dict | None = None
    policy_revision: str = "shadow-policy"
    semantic_required: bool = False
    # H1: this flag no longer enables native input.  The route it used to arm
    # called the input owner directly with the leased scene and the current
    # scene set to the same frame graph, so no later capture could reject the
    # binding and no session lease was revalidated at input time.  It is still
    # accepted (cli.py passes it) but the loop now refuses to actuate; see
    # ``NativeShadowLoop._refuse_direct_actuation``.
    actuate_transient_offers: bool = False


@dataclass
class _PendingTurn:
    turn_id: int
    observation: object
    catalog: object
    directive: Directive
    mask: object
    filtered: tuple[CandidateAction, ...]
    deterministic_id: str | None
    submitted_at: float
    # The frame the decision is bound to, kept so the lease can be revalidated
    # against the capture that arrives while inference is still running.
    scene: SceneGraph | None = None


def _primitive_plan_digest(schema: AdaptiveSchema, action_id: str) -> str | None:
    for spec in schema.actions:
        if spec.action_id != action_id:
            continue
        payload = [
            {"op": step.op, "bindings": list(step.bindings), "params": dict(step.params)}
            for step in spec.primitive_plan
        ]
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return sha256(blob.encode("utf-8")).hexdigest()
    return None


def _transient_plan_digest(plan: TransientClickPlan) -> str:
    payload = {
        "action_id": plan.action_id,
        "registry_id": plan.registry_id,
        "node_id": plan.node_id,
        "primitive_plan": [
            {"op": step.op, "bindings": list(step.bindings), "params": dict(step.params)}
            for step in plan.primitive_plan
        ],
        "constraints": {
            "risk": plan.constraints.risk,
            "retry": plan.constraints.retry,
            "cooldown_s": plan.constraints.cooldown_s,
            "max_plan_duration_s": plan.constraints.max_plan_duration_s,
        },
    }
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _registry_forbidden_ids(ledger) -> frozenset[str]:
    """Read only negative evidence; the directive cannot invent an offer."""

    ids: set[str] = set()
    for record in ledger.all():
        if record.kind != "negative_evidence":
            continue
        value = record.payload.get("registry_id") or record.payload.get("action_id")
        if value:
            ids.add(str(value))
    return frozenset(ids)


def registry_offer_diagnostics(
    scene: SceneGraph | None,
    ledger,
    *,
    semantic_required: bool = False,
) -> tuple[tuple[object, ...], dict[str, int], int]:
    """Eligible offers plus exclusion counts. Area and ownership live in offer_decisions."""

    if scene is None:
        return (), {}, 0
    from loop.adaptive.orchestrate import ProvisionalOffer, offer_decisions

    decisions = offer_decisions(
        scene,
        _registry_forbidden_ids(ledger),
        semantic="required" if semantic_required else "off",
    )
    offers = tuple(
        ProvisionalOffer(d.candidate_id, d.label, d.node_id)
        for d in decisions
        if d.status == "eligible"
    )
    exclusions: dict[str, int] = {}
    ambiguity = 0
    for decision in decisions:
        if decision.status == "eligible":
            continue
        exclusions[decision.reason] = exclusions.get(decision.reason, 0) + 1
        if decision.reason == "ambiguous_registry_id":
            ambiguity += 1
    return offers, exclusions, ambiguity


def _registry_offers(
    scene: SceneGraph | None,
    ledger,
    *,
    semantic_required: bool = False,
) -> tuple[object, ...]:
    offers, _, _ = registry_offer_diagnostics(
        scene, ledger, semantic_required=semantic_required
    )
    return offers


def _action_risk_map(schema: AdaptiveSchema) -> dict[str, str]:
    return {
        action.action_id: str(action.constraints.get("risk", "low"))
        for action in schema.actions
    }


def _v2_from_native_obs(native_obs) -> StateSnapshotV2:
    from loop.adaptive.snapshot import Provenanced, StateSnapshotV2

    snap = native_obs.snapshot
    fields = {
        name: Provenanced(value, 1.0, value is None, ())
        for name, value in sorted(snap.fields.items())
    }
    return StateSnapshotV2(
        snapshot_id=native_obs.snapshot_id,
        schema_revision_id=native_obs.schema_revision_id,
        episode_id=native_obs.episode_id,
        episode_generation=native_obs.episode_generation,
        step=0,
        frame_digest=snap.snapshot_id,
        frame_history_digests=(),
        captured_at=native_obs.capture_ts_monotonic,
        observation_age_s=native_obs.observation_age_s,
        mode=snap.mode_id,
        fields=fields,
        entities=(),
        relations=(),
        recent_events=(),
        confidence=1.0 if native_obs.quality is ObservationQuality.trusted else 0.0,
    )


def _bootstrap_directive(
    store: GenerationStore,
    *,
    episode_generation: int,
    config: NativeShadowConfig,
) -> Directive:
    directive = ensure_directive(
        store,
        episode_generation=episode_generation,
        config=config,
    )
    assert directive is not None
    return directive


class NativeShadowLoop:
    """Observe-only shadow react: hypothetical ``Executor.dispatch`` only."""

    def __init__(
        self,
        session,
        worker: PolicyWorker,
        executor: Executor,
        telemetry: TelemetryStore,
        *,
        config: NativeShadowConfig | None = None,
        episode_id: str | None = None,
        episode_generation: int = 0,
        owner: object | None = None,
        operating_state: OperatingState | None = None,
    ) -> None:
        self._session = session
        self._worker = worker
        self._executor = executor
        self._telemetry = telemetry
        self._config = config or NativeShadowConfig()
        self._directive_store = GenerationStore()
        self._orchestrator: SyncOrchestratorRunner | None = None
        self._episode_id = episode_id or uuid.uuid4().hex
        self._episode_generation = episode_generation
        self._owner = owner
        self._operating_state = operating_state or OperatingState.ShadowReact
        self._frame_index = 0
        self._pending: _PendingTurn | None = None
        self._deterministic = DeterministicPolicy()
        self._scoring_source = scoring_policy_source(worker._policy)  # noqa: SLF001
        self._executor.snapshot_max_age_s = self._config.snapshot_max_age_s
        self._executor.decision_max_age_s = self._config.decision_deadline_s
        self._dispatch_accepted = 0
        self._dispatch_rejected = 0
        self._policy_unavailable = 0
        self._valid_inferences = 0
        self._screen_registry_signature: frozenset[str] | None = None
        self._screen_unkeyed_count: int | None = None
        self._screen_steer_ledger_len = 0
        self._shadow_frame_bytes: bytes | None = None
        self._shadow_frame_digest = ""
        self._shadow_scene_graph = None
        self._refused_transient_ids: set[str] = set()
        self._transient_actuations = 0
        self._transient_actuation_failures = 0
        self._transient_actuations_disabled = 0
        self._orchestrator_events: list[dict[str, object]] = []

    @property
    def operating_state(self) -> OperatingState:
        return self._operating_state

    def _release_owner(self, reason: str) -> None:
        if self._owner is not None:
            self._owner.release_all(reason)

    def interrupt_for_capture_transition(self) -> None:
        """A dark transition invalidates the previous frame's pending choice."""
        self._pending = None
        self._release_owner("black_transition")

    def _land_dispatch_fault(self, dispatch_reason: str) -> None:
        self._release_owner(dispatch_reason)
        facts = TransitionFacts(inputs_released=True)
        if dispatch_reason == "unfocused":
            self._operating_state = transition_operating_state(
                self._operating_state,
                OperatingTransition.armed_fault_halt,
                replace(facts, focus_loss=True),
            )
        elif dispatch_reason == "directive_expired":
            self._operating_state = transition_operating_state(
                self._operating_state,
                OperatingTransition.quality_lost_or_empty,
                replace(facts, quality_actionable=False),
            )

    def _land_policy_unavailable(self) -> None:
        self._release_owner("policy_unavailable")
        self._operating_state = transition_operating_state(
            self._operating_state,
            OperatingTransition.quality_lost_or_empty,
            TransitionFacts(inputs_released=True, quality_actionable=True),
        )

    def close(self) -> None:
        self._release_owner("controller_close")
        if self._orchestrator is not None:
            self._orchestrator.close()
            self._orchestrator = None
        self._worker.shutdown()

    def _ensure_orchestrator(self) -> SyncOrchestratorRunner:
        if self._orchestrator is None:
            self._orchestrator = SyncOrchestratorRunner(
                self._directive_store,
                episode_generation=self._episode_generation,
                config=OrchestratorConfig(
                    default_intent=self._config.default_intent,
                    directive_ttl_s=self._config.directive_ttl_s,
                    game_brief=self._config.game_brief,
                ),
            )
        return self._orchestrator

    def _orchestrator_notify(self, kind: str, **payload: object) -> None:
        self._orchestrator_events.append(
            {"kind": kind, "at_monotonic": time.monotonic(), **payload}
        )
        del self._orchestrator_events[:-64]
        self._ensure_orchestrator().notify(kind, **payload)

    def _note_valid_inference(self, accepted: bool) -> None:
        if accepted and self._scoring_source == "QwenReactionPolicy":
            self._valid_inferences += 1

    def metrics(self) -> dict:
        return {
            "shadow_dispatch_accepted": self._dispatch_accepted,
            "shadow_dispatch_rejected": self._dispatch_rejected,
            "shadow_policy_unavailable": self._policy_unavailable,
            "shadow_frames": self._frame_index,
            "valid_inferences": self._valid_inferences,
            "transient_actuations": self._transient_actuations,
            "transient_actuation_failures": self._transient_actuation_failures,
            "transient_actuations_disabled": self._transient_actuations_disabled,
        }

    def process_frame(
        self,
        snap: StateSnapshot,
        *,
        hwnd: int,
        pid: int,
        capture_integrity: str,
        capture_provenance: tuple[str, ...] = (),
        focused: bool = True,
        frame_image: Image.Image | None = None,
        scene_graph: SceneGraph | None = None,
        capture_ts_monotonic: float | None = None,
        capture_age_known: bool | None = None,
        acquisition_origin: str | None = None,
        observation_sequence: int | None = None,
    ) -> None:
        t0 = time.monotonic()
        self._frame_index += 1
        now = time.monotonic()
        publication = now
        # Acquisition time is the real capture bound carried from the observer,
        # never a synthetic near-now value.  A caller that supplies no contract
        # (or one that cannot prove age) fails closed with the 0.0 sentinel and
        # is rejected for actuation regardless of that numeric value.
        age_known = bool(capture_age_known) and capture_ts_monotonic is not None
        capture_ts = float(capture_ts_monotonic) if age_known else 0.0
        origin = acquisition_origin or (
            "pre-grab-sync" if age_known else "buffered-unknown"
        )

        if frame_image is not None:
            self._shadow_frame_bytes = frame_image.tobytes()
            self._shadow_frame_digest = snap.snapshot_id or sha256(
                self._shadow_frame_bytes
            ).hexdigest()
        else:
            self._shadow_frame_bytes = None
            self._shadow_frame_digest = ""
        self._shadow_scene_graph = scene_graph

        quality = (
            ObservationQuality.trusted
            if capture_integrity == "trusted"
            else ObservationQuality.untrusted
        )
        native_obs = build_native_observation(
            snap,
            episode_id=self._episode_id,
            episode_generation=self._episode_generation,
            capture_ts_monotonic=capture_ts,
            publication_ts_monotonic=publication,
            capture_integrity=capture_integrity,
            capture_provenance=capture_provenance,
            hwnd=int(hwnd),
            pid=int(pid),
            target_identity=dict(self._session.target or {}),
            quality=quality,
            capture_age_known=age_known,
            acquisition_origin=origin,
            observation_sequence=observation_sequence,
        )
        reactive = reactive_state_from_native(native_obs)
        assert_no_privileged(reactive)

        if scene_graph is not None:
            ledger = self._session.ledger.all()
            new_records = ledger[self._screen_steer_ledger_len :]
            self._screen_steer_ledger_len = len(ledger)
            neg_evidence = any(r.kind == "negative_evidence" for r in new_records)
            forbid_from_neg = tuple(
                sorted(
                    {
                        str(r.payload["registry_id"])
                        for r in new_records
                        if r.kind == "negative_evidence"
                        and r.payload.get("registry_id")
                    }
                )
            )
            sig, unkeyed, wake_ev = maybe_screen_steer_wake(
                scene_graph,
                previous_signature=self._screen_registry_signature,
                previous_unkeyed=self._screen_unkeyed_count,
                negative_evidence_recorded=neg_evidence,
                forbidden_ids=forbid_from_neg,
            )
            self._screen_registry_signature = sig
            self._screen_unkeyed_count = unkeyed
            if wake_ev is not None:
                kind = str(wake_ev.pop("kind"))
                self._orchestrator_notify(kind, **wake_ev)

            # Rebuild the immutable snapshot with the current negative
            # evidence applied.  A caller-provided snapshot may have been
            # projected before the ledger was updated; it must not resurrect
            # a forbidden registry offer for this frame.
            from loop.adaptive.runtime import project as project_scene

            snap = project_scene(
                scene_graph,
                self._session.schema,
                registry_offers=_registry_offers(
                    scene_graph,
                    self._session.ledger,
                    semantic_required=self._config.semantic_required,
                ),
            )
            native_obs = build_native_observation(
                snap,
                episode_id=self._episode_id,
                episode_generation=self._episode_generation,
                capture_ts_monotonic=capture_ts,
                publication_ts_monotonic=publication,
                capture_integrity=capture_integrity,
                capture_provenance=capture_provenance,
                hwnd=int(hwnd),
                pid=int(pid),
                target_identity=dict(self._session.target or {}),
                quality=quality,
                capture_age_known=age_known,
                acquisition_origin=origin,
                observation_sequence=observation_sequence,
            )
            reactive = reactive_state_from_native(native_obs)
            assert_no_privileged(reactive)

        self._ensure_orchestrator()
        if self._pending is not None:
            self._drain_pending(focused=focused, now=now)
            if self._pending is not None:
                return

        directive = ensure_directive(
            self._directive_store,
            episode_generation=self._episode_generation,
            config=self._config,
        )
        if directive is None:
            self._orchestrator_notify("directive_expired")
            directive = ensure_directive(
                self._directive_store,
                episode_generation=self._episode_generation,
                config=self._config,
            )

        schema = self._session.schema
        decision_source = "observe"
        selected_id: str | None = None
        deterministic_id: str | None = None
        dispatch_accepted = False
        dispatch_reason = "no_candidates"
        plan_digest: str | None = None
        policy_s = 0.0
        turn_id = 0
        mask_d = ""
        allowed: tuple[str, ...] = ()
        catalog_revision = ""
        decision_scores: dict[str, float] = {}

        recovery_locked = bool(self._session.recovery_state())
        provisional_offers = _registry_offers(
            scene_graph,
            self._session.ledger,
            semantic_required=self._config.semantic_required,
        )
        try:
            catalog = compile_catalog_from_surface(
                schema,
                snap,
                provisional_offers=provisional_offers,
            )
        except CatalogCompileError:
            self._orchestrator_notify("catalog_change")
            self._emit_row(
                snap,
                native_obs,
                directive,
                catalog_revision,
                mask_d,
                allowed,
                decision_source,
                selected_id,
                deterministic_id,
                dispatch_accepted,
                "catalog_compile_error",
                plan_digest,
                turn_id,
                policy_s,
                t0,
                hwnd,
                pid,
                focused,
                capture_integrity,
            )
            return

        catalog_revision = catalog.revision
        self._deterministic.catalog_revision = catalog.revision

        gate = MaskGateContext(
            quality=quality,
            recovery_locked=recovery_locked,
            terminal=False,
            snapshot_mode=snap.mode_id,
            snapshot_confidence=1.0 if quality is ObservationQuality.trusted else 0.0,
            surface_ids=frozenset(snap.surface.action_ids),
            action_risk=_action_risk_map(schema),
        )
        mask = compile_mask(catalog, directive, gate=gate)
        mask_d = mask_digest(mask)
        allowed = mask.allowed_ids
        filtered = _filtered_candidates(catalog, mask, include_transient=True)

        if not allowed:
            dispatch_reason = "release_observe"
            self._release_owner(dispatch_reason)
            self._orchestrator_notify("mask_change")
            self._emit_row(
                snap,
                native_obs,
                directive,
                catalog_revision,
                mask_d,
                allowed,
                decision_source,
                selected_id,
                deterministic_id,
                dispatch_accepted,
                dispatch_reason,
                plan_digest,
                turn_id,
                policy_s,
                t0,
                hwnd,
                pid,
                focused,
                capture_integrity,
            )
            return

        unambig = select_if_unambiguous(mask)
        if unambig is not None:
            decision_source = "deterministic"
            selected_id = unambig
            deterministic_id = unambig
            decision = StructuredDecision(
                candidate_id=unambig,
                confidence=1.0,
                probabilities={unambig: 1.0},
                decided_at=now,
                episode_generation=self._episode_generation,
                snapshot_id=snap.snapshot_id,
                directive_revision=directive.revision,
                catalog_revision=catalog.revision,
            )
            decision_scores = dict(decision.probabilities)
            dispatch_accepted, dispatch_reason, plan_digest = self._hypothetical_dispatch(
                decision,
                native_obs,
                catalog,
                directive,
                mask,
                focused=focused,
                now=now,
            )
            self._emit_row(
                snap,
                native_obs,
                directive,
                catalog_revision,
                mask_d,
                allowed,
                decision_source,
                selected_id,
                deterministic_id,
                dispatch_accepted,
                dispatch_reason,
                plan_digest,
                turn_id,
                policy_s,
                t0,
                hwnd,
                pid,
                focused,
                capture_integrity,
                decision_scores=decision_scores,
            )
            return

        if len(filtered) >= 2:
            deterministic_id = self._deterministic.score(
                reactive, directive, filtered
            ).candidate_id

        if not (
            isinstance(self._worker._policy, DeciderPolicy)  # noqa: SLF001
            or getattr(self._worker._policy, "off_thread", False)
        ):
            if isinstance(self._worker._policy, DeterministicPolicy):  # noqa: SLF001
                self._worker._policy.catalog_revision = catalog.revision  # noqa: SLF001
            t_pol = time.monotonic()
            try:
                decision = self._worker._policy.score(  # noqa: SLF001
                    reactive, directive, filtered
                )
                validate_decision(
                    decision, mask, catalog, reactive, directive
                )
            except DecisionError as exc:
                decision_source = "unavailable"
                dispatch_reason = f"decision_invalid:{exc}"
                self._orchestrator_notify("malformed_inference")
                self._policy_unavailable += 1
                self._land_policy_unavailable()
                policy_s = time.monotonic() - t_pol
                self._emit_row(
                    snap,
                    native_obs,
                    directive,
                    catalog_revision,
                    mask_d,
                    allowed,
                    decision_source,
                    selected_id,
                    deterministic_id,
                    dispatch_accepted,
                    dispatch_reason,
                    plan_digest,
                    turn_id,
                    policy_s,
                    t0,
                    hwnd,
                    pid,
                    focused,
                    capture_integrity,
                )
                return
            policy_s = time.monotonic() - t_pol
            decision_source = self._scoring_source
            selected_id = decision.candidate_id
            decision_scores = dict(decision.probabilities)
            dispatch_accepted, dispatch_reason, plan_digest = self._hypothetical_dispatch(
                decision,
                native_obs,
                catalog,
                directive,
                mask,
                focused=focused,
                now=now,
            )
            self._note_valid_inference(dispatch_accepted)
            self._emit_row(
                snap,
                native_obs,
                directive,
                catalog_revision,
                mask_d,
                allowed,
                decision_source,
                selected_id,
                deterministic_id,
                dispatch_accepted,
                dispatch_reason,
                plan_digest,
                turn_id,
                policy_s,
                t0,
                hwnd,
                pid,
                focused,
                capture_integrity,
                decision_scores=decision_scores,
            )
            return

        job_image = None
        if isinstance(self._worker._policy, QwenReactionPolicy):  # noqa: SLF001
            job_image = prepare_qwen_worker_crop(
                self._worker._policy,  # noqa: SLF001
                filtered,
                frame_image=frame_image,
                scene_graph=scene_graph,
            )

        t_pol = time.monotonic()
        turn_id = self._worker.submit(
            reactive,
            directive,
            filtered,
            catalog_revision=catalog.revision,
            candidate_action_ids=tuple(c.action_id for c in filtered),
            snapshot_id=snap.snapshot_id,
            directive_revision=directive.revision,
            episode_generation=self._episode_generation,
            job_image=job_image,
            timeout_s=self._config.decision_deadline_s,
            now=now,
        )
        policy_s = time.monotonic() - t_pol
        if turn_id <= 0:
            decision_source = "unavailable"
            dispatch_reason = "decider_requires_at_least_two_candidates"
            self._orchestrator_notify("policy_worker_failed")
            self._policy_unavailable += 1
            self._emit_row(
                snap,
                native_obs,
                directive,
                catalog_revision,
                mask_d,
                allowed,
                decision_source,
                selected_id,
                deterministic_id,
                dispatch_accepted,
                dispatch_reason,
                plan_digest,
                turn_id,
                policy_s,
                t0,
                hwnd,
                pid,
                focused,
                capture_integrity,
            )
            return

        self._pending = _PendingTurn(
            turn_id=turn_id,
            observation=native_obs,
            catalog=catalog,
            directive=directive,
            mask=mask,
            filtered=filtered,
            deterministic_id=deterministic_id,
            submitted_at=now,
            scene=self._shadow_scene_graph,
        )
        result = self._worker.poll(turn_id, now=now)
        if result.status is PolicyTurnStatus.READY and result.decision is not None:
            decision_scores = dict(result.decision.probabilities)
            (
                decision_source,
                selected_id,
                dispatch_accepted,
                dispatch_reason,
                plan_digest,
            ) = self._apply_worker_decision(
                self._pending,
                result.decision,
                focused=focused,
                now=now,
            )
            self._pending = None
        elif result.status is PolicyTurnStatus.UNAVAILABLE:
            decision_source = "unavailable"
            dispatch_reason = result.reason or "policy_unavailable"
            kind = (
                "slow_inference"
                if (result.reason or "").startswith("inference_deadline")
                else "malformed_inference"
                if (result.reason or "").startswith("decision_invalid")
                else "policy_worker_failed"
            )
            self._orchestrator_notify(kind)
            self._policy_unavailable += 1
            self._land_policy_unavailable()
            self._pending = None
        else:
            decision_source = f"{self._scoring_source}_pending"

        self._emit_row(
            snap,
            native_obs,
            directive,
            catalog_revision,
            mask_d,
            allowed,
            decision_source,
            selected_id,
            deterministic_id,
            dispatch_accepted,
            dispatch_reason,
            plan_digest,
            turn_id,
            policy_s,
            t0,
            hwnd,
            pid,
            focused,
            capture_integrity,
            decision_scores=decision_scores,
        )

    def _drain_pending(self, *, focused: bool, now: float) -> None:
        pending = self._pending
        if pending is None:
            return
        result = self._worker.poll(pending.turn_id, now=now)
        if result.status is PolicyTurnStatus.PENDING:
            return
        self._pending = None
        obs = pending.observation
        snap = obs.snapshot  # type: ignore[union-attr]
        mask = pending.mask
        mask_d = mask_digest(mask)  # type: ignore[arg-type]
        if result.status is PolicyTurnStatus.UNAVAILABLE:
            kind = (
                "slow_inference"
                if (result.reason or "").startswith("inference_deadline")
                else "malformed_inference"
                if (result.reason or "").startswith("decision_invalid")
                else "policy_worker_failed"
            )
            self._orchestrator_notify(kind)
            self._policy_unavailable += 1
            self._land_policy_unavailable()
            self._emit_row(
                snap,
                obs,
                pending.directive,
                pending.catalog.revision,  # type: ignore[union-attr]
                mask_d,
                mask.allowed_ids,  # type: ignore[union-attr]
                "unavailable",
                None,
                pending.deterministic_id,
                False,
                result.reason or "policy_unavailable",
                None,
                pending.turn_id,
                0.0,
                time.monotonic(),
                obs.hwnd,  # type: ignore[union-attr]
                obs.pid,  # type: ignore[union-attr]
                focused,
                obs.capture_integrity,  # type: ignore[union-attr]
            )
            return
        if result.decision is None:
            return
        (
            decision_source,
            selected_id,
            dispatch_accepted,
            dispatch_reason,
            plan_digest,
        ) = self._apply_worker_decision(pending, result.decision, focused=focused, now=now)
        self._emit_row(
            snap,
            obs,
            pending.directive,
            pending.catalog.revision,  # type: ignore[union-attr]
            mask_d,
            mask.allowed_ids,  # type: ignore[union-attr]
            decision_source,
            selected_id,
            pending.deterministic_id,
            dispatch_accepted,
            dispatch_reason,
            plan_digest,
            pending.turn_id,
            time.monotonic() - pending.submitted_at,
            time.monotonic(),
            obs.hwnd,  # type: ignore[union-attr]
            obs.pid,  # type: ignore[union-attr]
            focused,
            obs.capture_integrity,  # type: ignore[union-attr]
            decision_scores=dict(result.decision.probabilities),
        )

    def _apply_worker_decision(
        self,
        pending: _PendingTurn,
        decision: StructuredDecision,
        *,
        focused: bool,
        now: float,
    ) -> tuple[str, str | None, bool, str, str | None]:
        obs = pending.observation
        try:
            validate_decision(
                decision,
                pending.mask,  # type: ignore[arg-type]
                pending.catalog,  # type: ignore[arg-type]
                reactive_state_from_native(obs),  # type: ignore[arg-type]
                pending.directive,
            )
        except DecisionError as exc:
            self._orchestrator_notify("malformed_inference")
            self._policy_unavailable += 1
            self._land_policy_unavailable()
            return "unavailable", None, False, f"decision_invalid:{exc}", None

        accepted, reason, plan_digest = self._hypothetical_dispatch(
            decision,
            obs,  # type: ignore[arg-type]
            pending.catalog,  # type: ignore[arg-type]
            pending.directive,
            pending.mask,  # type: ignore[arg-type]
            focused=focused,
            now=now,
            leased_scene=pending.scene,
        )
        self._note_valid_inference(accepted)
        if not accepted:
            if reason.startswith("decision_invalid"):
                self._orchestrator_notify("malformed_inference")
                self._policy_unavailable += 1
                self._land_policy_unavailable()
            return "unavailable", None, False, reason, plan_digest

        return self._scoring_source, decision.candidate_id, accepted, reason, plan_digest

    @staticmethod
    def _acquisition_allows_actuation(observation) -> bool:
        """Whether a real input path may use this observation.

        A real input path requires a proven known capture age, an observation
        sequence and trusted quality.  Unknown age is rejected regardless of the
        numeric timestamp sentinel.
        """
        return observation.acquisition_is_actuation_eligible()

    def _hypothetical_dispatch(
        self,
        decision: StructuredDecision,
        native_obs,
        catalog,
        directive: Directive,
        mask,
        *,
        focused: bool,
        now: float,
        leased_scene: SceneGraph | None = None,
    ) -> tuple[bool, str, str | None]:
        """Shadow-gate one decision; this method never sends input.

        ``leased_scene`` is the frame the decision's offer was bound to.  The
        deferred path passes the frame the decision was actually made on, so
        the executor compares a frozen lease against the later capture that
        revalidates it; the synchronous paths leave it unset, where the graph
        really is this capture.  Native input is refused outright (see
        ``_refuse_direct_actuation``).
        """
        transient_plan = catalog.transient_plan_for(decision.candidate_id)
        if transient_plan is not None:
            # Executor copies NativeObservation.target_identity into the lease.
            # Carry the node id there so the accepted lease freezes the exact
            # registry binding selected from this frame.
            native_obs = replace(
                native_obs,
                target_identity={
                    **native_obs.target_identity,
                    "leased_node_id": transient_plan.node_id,
                    "registry_id": transient_plan.registry_id,
                },
            )
        v2 = _v2_from_native_obs(native_obs)
        aged = replace(
            v2,
            observation_age_s=max(0.0, now - v2.captured_at),
        )
        # A leased scene is the frozen frame the offer was bound to; the current
        # scene is the frame that revalidates it.  This pair used to be one live
        # graph twice, so no later capture could ever reject the binding.
        frame = self._shadow_scene_graph if leased_scene is None else leased_scene
        leased = _frozen_scene(frame) if transient_plan is not None else None
        current = self._shadow_scene_graph if transient_plan is not None else None
        ctx = NativeDispatchContext(
            observation=native_obs,
            mask=mask,
            expected_mask_digest=mask_digest(mask),
            policy_revision=self._config.policy_revision,
            surface_action_ids=frozenset(native_obs.snapshot.surface.action_ids),
            recovery_locked=bool(self._session.recovery_state()),
            leased_scene=leased,
            current_scene=current,
        )
        receipt = self._executor.dispatch(
            decision,
            aged,
            catalog,
            directive,
            now=now,
            focused=focused,
            terminal=False,
            native=ctx,
        )
        if (
            receipt.accepted
            and transient_plan is not None
            and self._config.actuate_transient_offers
            and self._acquisition_allows_actuation(native_obs)
        ):
            self._refuse_direct_actuation(transient_plan)
        plan_digest = (
            _transient_plan_digest(transient_plan)
            if transient_plan is not None
            else _primitive_plan_digest(self._session.schema, decision.candidate_id)
        )
        if receipt.accepted:
            self._dispatch_accepted += 1
        else:
            self._dispatch_rejected += 1
            reason = receipt.reason
            if reason in ("stale_snapshot", "untrusted_capture"):
                self._orchestrator_notify("stale_capture")
            elif reason == "directive_expired":
                self._orchestrator_notify("directive_expired")
                self._land_dispatch_fault(reason)
            elif reason == "unfocused":
                self._orchestrator_notify("focus_loss")
                self._land_dispatch_fault(reason)
            elif reason in ("catalog_mismatch", "schema_mismatch"):
                self._orchestrator_notify("schema_change")
            elif reason == "mask_mismatch":
                self._orchestrator_notify("mask_change")
            elif reason == "terminal":
                self._orchestrator_notify("terminal")
        return receipt.accepted, receipt.reason, plan_digest

    def _refuse_direct_actuation(self, transient_plan) -> None:
        """H1: fail closed instead of sending native input from this loop.

        The removed route called ``InputOwner.run_plan`` directly with the
        leased scene and the current scene set to the same frame graph, so no
        later capture could reject the binding and no session lease was
        revalidated at input time.  Product input must go through the
        authorized session/lease boundary with a frozen leased scene and a
        genuinely newer capture used only to confirm or reject that binding;
        running a provisional offer's primitive plan needs a session-level
        plan lease API (session.py/runtime.py), which this package does not
        own.  Until that exists nothing is sent: the refusal is recorded once
        per offer and the owner stays released.
        """

        action_id = transient_plan.action_id
        if action_id in self._refused_transient_ids:
            return
        self._refused_transient_ids.add(action_id)
        self._transient_actuations_disabled += 1
        self._session.ledger.append_event(
            "direct_actuation_disabled",
            {
                "action_id": action_id,
                "registry_id": transient_plan.registry_id,
                "reason": "direct_route_disabled_pending_lease_repair",
            },
        )
        self._orchestrator_notify(
            "direct_actuation_disabled",
            action_id=action_id,
            registry_id=transient_plan.registry_id,
        )
        if self._owner is not None:
            status = str(self._owner.release_all("direct_actuation_disabled"))
            if status.startswith("failed:"):
                self._session.recovery_lock(
                    {"reason": "release_failure", "detail": status}
                )

    def _emit_row(
        self,
        snap,
        native_obs,
        directive: Directive,
        catalog_revision: str,
        mask_d: str,
        allowed: tuple[str, ...],
        decision_source: str,
        selected_id: str | None,
        deterministic_id: str | None,
        dispatch_accepted: bool,
        dispatch_reason: str,
        plan_digest: str | None,
        turn_id: int,
        policy_s: float,
        t0: float,
        hwnd: int,
        pid: int,
        focused: bool,
        capture_integrity: str,
        *,
        decision_scores: dict[str, float] | None = None,
    ) -> None:
        registry_ids: tuple[str, ...] = ()
        offer_ids: tuple[str, ...] = ()
        candidate_labels: dict[str, str] = {}
        ocr_items: tuple[dict, ...] = ()
        graph = self._shadow_scene_graph
        if graph is not None:
            registry_ids = tuple(
                sorted(
                    {
                        str(node.attributes["registry_id"])
                        for node in graph.nodes
                        if node.attributes.get("registry_id")
                    }
                )
            )
            from loop.adaptive.arm import forbidden_ids_from_ledger
            from loop.adaptive.orchestrate import registry_offers

            offers = registry_offers(
                graph,
                forbidden_ids_from_ledger(self._session.ledger),
                semantic="required" if self._config.semantic_required else "off",
            )
            offer_ids = tuple(item.offer_id for item in offers)
            candidate_labels = {item.offer_id: item.label for item in offers}
            ocr_items = tuple(
                {
                    "node_id": node.node_id,
                    "text": str(
                        node.attributes.get("text")
                        or node.attributes.get("label")
                        or ""
                    ),
                    "bbox": tuple(float(value) for value in node.bbox),
                }
                for node in graph.nodes
                if node.node_id.startswith("ocr_")
                and (node.attributes.get("text") or node.attributes.get("label"))
            )

        for action_id in allowed:
            candidate_labels.setdefault(action_id, action_id.replace("_", " "))

        orchestrator_events = tuple(self._orchestrator_events)
        self._orchestrator_events.clear()

        frame_digest = ""
        frame_bytes: bytes | None = None
        if self._shadow_frame_bytes is not None:
            frame_digest = self._shadow_frame_digest
            frame_bytes = self._shadow_frame_bytes

        self._telemetry.append_shadow(
            NativeShadowRow(
                episode_id=self._episode_id,
                episode_generation=self._episode_generation,
                frame_index=self._frame_index,
                schema_revision_id=snap.schema_revision_id,
                snapshot_id=snap.snapshot_id,
                observation_age_s=native_obs.observation_age_s,
                capture_integrity=capture_integrity,
                hwnd=int(hwnd),
                pid=int(pid),
                focused=focused,
                directive_revision=directive.revision,
                catalog_revision=catalog_revision,
                mask_digest=mask_d,
                mask_allowed_ids=allowed,
                decision_source=decision_source,
                selected_action_id=selected_id,
                deterministic_action_id=deterministic_id,
                dispatch_accepted=dispatch_accepted,
                dispatch_reason=dispatch_reason,
                primitive_plan_digest=plan_digest,
                policy_turn_id=turn_id,
                latency_policy_s=policy_s,
                latency_e2e_s=time.monotonic() - t0,
                frame_digest=frame_digest,
                registry_ids=registry_ids,
                offer_ids=offer_ids,
                effect_happened=None,
                candidate_scores=dict(decision_scores or {}),
                candidate_labels=candidate_labels,
                ocr_items=ocr_items,
                directive_intent=directive.intent,
                directive_forbidden_ids=directive.forbidden_ids,
                directive_attention=directive.attention,
                orchestrator_events=orchestrator_events,
                capture_ts_monotonic=native_obs.capture_ts_monotonic,
                publication_ts_monotonic=native_obs.publication_ts_monotonic,
                capture_age_known=native_obs.capture_age_known,
                acquisition_origin=native_obs.acquisition_origin,
                observation_sequence=native_obs.observation_sequence,
                capture_to_row_s=(
                    time.monotonic() - native_obs.capture_ts_monotonic
                    if native_obs.acquisition_is_actuation_eligible()
                    else None
                ),
            ),
            frame=frame_bytes,
        )
