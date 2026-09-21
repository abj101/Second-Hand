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
    mask_digest,
    select_if_unambiguous,
)
from loop.adaptive.contracts import ObservationQuality
from loop.adaptive.environment import StepResult
from loop.adaptive.executor import Executor, NativeDispatchContext
from loop.adaptive.policy import (
    DecisionError,
    DeterministicPolicy,
    StructuredDecision,
    reactive_state_from_native,
    validate_decision,
)
from loop.adaptive.policy_worker import PolicyTurnStatus, PolicyWorker
from loop.adaptive.snapshot import assert_no_privileged, build_native_observation, snapshot_payload
from loop.adaptive.schema import AdaptiveSchema, StateSnapshot
from loop.adaptive.telemetry import DecisionRow, NativeShadowRow, TelemetryStore, percentile


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


def _filtered_candidates(catalog, mask) -> tuple[CandidateAction, ...]:
    allowed = set(mask.allowed_ids)
    return tuple(c for c in catalog.candidates if c.action_id in allowed)


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


async def orchestrator_worker(
    events: asyncio.Queue,
    directive_store: GenerationStore,
    *,
    episode_generation: int,
    config: ReactConfig,
    stop: asyncio.Event,
) -> None:
    revision = 1

    def _publish() -> None:
        directive_store.publish(
            episode_generation,
            Directive(
                revision=revision,
                episode_generation=episode_generation,
                intent=config.default_intent,
                ttl_s=config.directive_ttl_s,
                published_at=time.monotonic(),
            ),
        )

    _publish()
    while not stop.is_set():
        try:
            ev = await asyncio.wait_for(events.get(), timeout=0.25)
        except asyncio.TimeoutError:
            _publish()
            continue
        kind = ev.get("kind") if isinstance(ev, dict) else ""
        if kind in ("halt", "timeout", "terminal", "unfocused", "low_confidence"):
            revision += 1
        _publish()


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


@dataclass
class NativeShadowConfig:
    decision_deadline_s: float = 0.25
    snapshot_max_age_s: float = 0.5
    directive_ttl_s: float = 30.0
    default_intent: str = "observe and qualify promoted actions"
    policy_revision: str = "shadow-policy"


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
    if store.latest() is None:
        now = time.monotonic()
        store.publish(
            episode_generation,
            Directive(
                revision=1,
                episode_generation=episode_generation,
                intent=config.default_intent,
                ttl_s=config.directive_ttl_s,
                published_at=now,
            ),
        )
    directive = store.latest()
    assert directive is not None
    now = time.monotonic()
    if now - directive.published_at > directive.ttl_s:
        store.publish(
            episode_generation,
            replace(
                directive,
                revision=directive.revision + 1,
                published_at=now,
            ),
        )
        directive = store.latest()
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
    ) -> None:
        self._session = session
        self._worker = worker
        self._executor = executor
        self._telemetry = telemetry
        self._config = config or NativeShadowConfig()
        self._directive_store = GenerationStore()
        self._episode_id = episode_id or uuid.uuid4().hex
        self._episode_generation = episode_generation
        self._frame_index = 0
        self._pending: _PendingTurn | None = None
        self._deterministic = DeterministicPolicy()
        self._executor.snapshot_max_age_s = self._config.snapshot_max_age_s
        self._executor.decision_max_age_s = self._config.decision_deadline_s
        self._dispatch_accepted = 0
        self._dispatch_rejected = 0
        self._policy_unavailable = 0

    def close(self) -> None:
        self._worker.shutdown()

    def metrics(self) -> dict:
        return {
            "shadow_dispatch_accepted": self._dispatch_accepted,
            "shadow_dispatch_rejected": self._dispatch_rejected,
            "shadow_policy_unavailable": self._policy_unavailable,
            "shadow_frames": self._frame_index,
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
    ) -> None:
        t0 = time.monotonic()
        self._frame_index += 1
        now = time.monotonic()
        publication = now
        capture_ts = max(0.0, publication - 0.001)

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
        )
        reactive = reactive_state_from_native(native_obs)
        assert_no_privileged(reactive)

        directive = _bootstrap_directive(
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

        recovery_locked = bool(self._session.recovery_state())
        try:
            catalog = compile_catalog_from_surface(schema, snap)
        except CatalogCompileError:
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
        if getattr(self._worker._policy, "catalog_revision", None) == "":  # noqa: SLF001
            self._worker._policy.catalog_revision = catalog.revision  # noqa: SLF001

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
        filtered = _filtered_candidates(catalog, mask)

        if self._pending is not None:
            self._drain_pending(focused=focused, now=now)

        if not allowed:
            dispatch_reason = "release_observe"
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
            )
            return

        if len(filtered) >= 2:
            deterministic_id = self._deterministic.score(
                reactive, directive, filtered
            ).candidate_id

        t_pol = time.monotonic()
        turn_id = self._worker.submit(
            reactive,
            directive,
            filtered,
            timeout_s=self._config.decision_deadline_s,
            now=now,
        )
        policy_s = time.monotonic() - t_pol
        if turn_id <= 0:
            decision_source = "unavailable"
            dispatch_reason = "decider_requires_at_least_two_candidates"
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
        )
        result = self._worker.poll(turn_id, now=now)
        if result.status is PolicyTurnStatus.READY and result.decision is not None:
            (
                decision_source,
                selected_id,
                dispatch_accepted,
                dispatch_reason,
                plan_digest,
            ) = self._apply_decider_decision(
                self._pending,
                result.decision,
                focused=focused,
                now=now,
            )
            self._pending = None
        elif result.status is PolicyTurnStatus.UNAVAILABLE:
            decision_source = "unavailable"
            dispatch_reason = result.reason or "policy_unavailable"
            self._policy_unavailable += 1
            self._pending = None
        else:
            decision_source = "decider_pending"

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
            self._policy_unavailable += 1
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
            _source,
            selected_id,
            dispatch_accepted,
            dispatch_reason,
            plan_digest,
        ) = self._apply_decider_decision(pending, result.decision, focused=focused, now=now)
        self._emit_row(
            snap,
            obs,
            pending.directive,
            pending.catalog.revision,  # type: ignore[union-attr]
            mask_d,
            mask.allowed_ids,  # type: ignore[union-attr]
            "decider",
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
        )

    def _apply_decider_decision(
        self,
        pending: _PendingTurn,
        decision: StructuredDecision,
        *,
        focused: bool,
        now: float,
    ) -> tuple[str, str | None, bool, str, str | None]:
        obs = pending.observation
        snap = obs.snapshot  # type: ignore[union-attr]
        try:
            validate_decision(
                decision,
                pending.mask,  # type: ignore[arg-type]
                pending.catalog,  # type: ignore[arg-type]
                reactive_state_from_native(obs),  # type: ignore[arg-type]
                pending.directive,
            )
        except DecisionError as exc:
            self._policy_unavailable += 1
            return "unavailable", None, False, f"decision_invalid:{exc}", None

        accepted, reason, plan_digest = self._hypothetical_dispatch(
            decision,
            obs,  # type: ignore[arg-type]
            pending.catalog,  # type: ignore[arg-type]
            pending.directive,
            pending.mask,  # type: ignore[arg-type]
            focused=focused,
            now=now,
        )
        return "decider", decision.candidate_id, accepted, reason, plan_digest

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
    ) -> tuple[bool, str, str | None]:
        v2 = _v2_from_native_obs(native_obs)
        aged = replace(
            v2,
            observation_age_s=max(0.0, now - v2.captured_at),
        )
        ctx = NativeDispatchContext(
            observation=native_obs,
            mask=mask,
            expected_mask_digest=mask_digest(mask),
            policy_revision=self._config.policy_revision,
            surface_action_ids=frozenset(native_obs.snapshot.surface.action_ids),
            recovery_locked=bool(self._session.recovery_state()),
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
        plan_digest = _primitive_plan_digest(self._session.schema, decision.candidate_id)
        if receipt.accepted:
            self._dispatch_accepted += 1
        else:
            self._dispatch_rejected += 1
        return receipt.accepted, receipt.reason, plan_digest

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
    ) -> None:
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
            )
        )
