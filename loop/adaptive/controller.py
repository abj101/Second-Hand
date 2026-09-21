"""Product react loop controller; benchmark adapters plug in via ``Environment.step``."""

from __future__ import annotations

import asyncio
import io
import time
from dataclasses import asdict, dataclass, replace

from PIL import Image

from loop.adaptive.candidates import (
    CandidateAction,
    Directive,
    compile_mask,
    select_if_unambiguous,
)
from loop.adaptive.environment import StepResult
from loop.adaptive.executor import Executor
from loop.adaptive.policy import StructuredDecision, validate_decision
from loop.adaptive.snapshot import assert_no_privileged, snapshot_payload
from loop.adaptive.telemetry import DecisionRow, TelemetryStore, percentile


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
