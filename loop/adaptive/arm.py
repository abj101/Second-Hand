"""Bounded native arming stages (Wave 6): lease-gated ``Session.act`` only."""

from __future__ import annotations

import json
import sys
import time
import uuid
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Callable

from loop.adaptive.candidates import (
    CatalogCompileError,
    Directive,
    MaskGateContext,
    compile_catalog_from_surface,
    compile_mask,
    mask_digest,
)
from loop.adaptive.contracts import ExecutionReceipt, ObservationQuality
from loop.adaptive.controller import (
    GenerationStore,
    NativeShadowConfig,
    _action_risk_map,
    _bootstrap_directive,
    _filtered_candidates,
    _v2_from_native_obs,
)
from loop.adaptive.executor import Executor, NativeDispatchContext
from loop.adaptive.orchestrate import Trace
from loop.adaptive.policy import DeterministicPolicy, StructuredDecision, reactive_state_from_native
from loop.adaptive.policy_worker import PolicyTurnStatus, PolicyWorker
from loop.adaptive.runtime import select_invocation
from loop.adaptive.schema import ActionInvocation, StateSnapshot
from loop.adaptive.session import Session
from loop.adaptive.snapshot import build_native_observation


ObserveFn = Callable[[], tuple[Any, Any, StateSnapshot, list]]


@dataclass(frozen=True)
class ArmConfig:
    episode_generation: int = 0
    policy_revision: str = "armed-policy"
    decision_deadline_s: float = 0.25
    snapshot_max_age_s: float = 0.5
    directive_ttl_s: float = 30.0


def create_owner(target: Any) -> Any:
    from loop.os.input_owner import NativeInputOwner

    return NativeInputOwner.for_target(target)


def invocation_for_action(snapshot: StateSnapshot, action_id: str) -> ActionInvocation | None:
    if len(snapshot.surface.actions) == 1 and snapshot.surface.actions[0].action_id == action_id:
        inv = select_invocation(snapshot)
        return inv if inv is not None and inv.action_id == action_id else None
    for inst in snapshot.surface.actions:
        if inst.action_id != action_id:
            continue
        domain_by_name = {d.name: d for d in inst.domains}
        bindings: dict[str, Any] = {}
        for param in inst.parameters:
            if not param.required:
                continue
            domain = domain_by_name.get(param.name)
            if param.name == "target" and inst.bound_node_id:
                val: Any = inst.bound_node_id
            elif param.name == "target":
                return None
            elif domain and len(domain.candidates) == 1:
                val = domain.candidates[0]
            else:
                return None
            if domain and domain.candidates and val not in domain.candidates:
                return None
            bindings[param.name] = val
        return ActionInvocation(
            inst.action_id,
            bindings,
            snapshot.snapshot_id,
            schema_revision_id=snapshot.schema_revision_id,
        )
    return None


def _bind_lease_context(session: Session, lease) -> None:
    session._episode_generation = lease.episode_generation  # noqa: SLF001
    session._catalog_revision = lease.catalog_revision  # noqa: SLF001
    session._directive_revision = lease.directive_revision  # noqa: SLF001
    session._policy_revision = lease.policy_revision  # noqa: SLF001


def _append_arm_receipt(workspace: Path, payload: dict) -> None:
    path = workspace / "arm_executions.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, sort_keys=True) + "\n")


def _receipt_payload(
    *,
    stage: int,
    snap: StateSnapshot,
    native_obs,
    catalog_revision: str,
    directive: Directive,
    mask_d: str,
    decision_source: str,
    dispatch_receipt,
    receipt: ExecutionReceipt | None,
    capture_provenance: tuple[str, ...],
) -> dict:
    row: dict[str, Any] = {
        "stage": stage,
        "snapshot_id": snap.snapshot_id,
        "schema_revision_id": snap.schema_revision_id,
        "catalog_revision": catalog_revision,
        "directive_revision": directive.revision,
        "policy_revision": getattr(dispatch_receipt.lease, "policy_revision", None)
        if dispatch_receipt.lease
        else None,
        "decision_action_id": dispatch_receipt.action_id,
        "dispatch_accepted": dispatch_receipt.accepted,
        "dispatch_reason": dispatch_receipt.reason,
        "capture_integrity": native_obs.capture_integrity,
        "capture_provenance": list(capture_provenance),
        "hwnd": native_obs.hwnd,
        "pid": native_obs.pid,
    }
    if dispatch_receipt.lease is not None:
        row["lease"] = asdict(dispatch_receipt.lease)
    if receipt is not None:
        row["execution"] = {
            "accepted": receipt.accepted,
            "reason": receipt.reason,
            "release_status": receipt.release_status,
            "primitive_results": list(receipt.primitive_results),
            "after_hwnd": receipt.after_hwnd,
            "after_pid": receipt.after_pid,
            "after_capture_provenance": list(receipt.after_capture_provenance),
            "expected_effect_verified": receipt.expected_effect.verified,
            "expected_effect_reason": receipt.expected_effect.reason,
        }
    return row


def _pre_dispatch_from_observe(observe: ObserveFn) -> tuple[bool, str]:
    prov = getattr(observe, "provenance", {}) or {}
    if str(prov.get("integrity") or "trusted") != "trusted":
        return False, "untrusted capture"
    return True, "ok"


def _compile_frame(
    session: Session,
    snap: StateSnapshot,
    *,
    episode_id: str,
    episode_generation: int,
    hwnd: int,
    pid: int,
    capture_integrity: str,
    capture_provenance: tuple[str, ...],
    directive_store: GenerationStore,
    config: ArmConfig,
):
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
        episode_id=episode_id,
        episode_generation=episode_generation,
        capture_ts_monotonic=capture_ts,
        publication_ts_monotonic=publication,
        capture_integrity=capture_integrity,
        capture_provenance=capture_provenance,
        hwnd=int(hwnd),
        pid=int(pid),
        target_identity=dict(session.target or {}),
        quality=quality,
    )
    directive = _bootstrap_directive(
        directive_store,
        episode_generation=episode_generation,
        config=NativeShadowConfig(
            directive_ttl_s=config.directive_ttl_s,
            policy_revision=config.policy_revision,
        ),
    )
    catalog = compile_catalog_from_surface(session.schema, snap)
    gate = MaskGateContext(
        quality=quality,
        recovery_locked=bool(session.recovery_state()),
        terminal=False,
        snapshot_mode=snap.mode_id,
        snapshot_confidence=1.0 if quality is ObservationQuality.trusted else 0.0,
        surface_ids=frozenset(snap.surface.action_ids),
        action_risk=_action_risk_map(session.schema),
    )
    mask = compile_mask(catalog, directive, gate=gate)
    return native_obs, catalog, directive, mask


def _dispatch_decision(
    session: Session,
    decision: StructuredDecision,
    native_obs,
    catalog,
    directive: Directive,
    mask,
    *,
    executor: Executor,
    config: ArmConfig,
    focused: bool,
    now: float | None = None,
):
    now = time.monotonic() if now is None else now
    v2 = _v2_from_native_obs(native_obs)
    aged = replace(v2, observation_age_s=max(0.0, now - v2.captured_at))
    ctx = NativeDispatchContext(
        observation=native_obs,
        mask=mask,
        expected_mask_digest=mask_digest(mask),
        policy_revision=config.policy_revision,
        surface_action_ids=frozenset(native_obs.snapshot.surface.action_ids),
        recovery_locked=bool(session.recovery_state()),
    )
    return executor.dispatch(
        decision,
        aged,
        catalog,
        directive,
        now=now,
        focused=focused,
        terminal=False,
        native=ctx,
    )


def _stable_surface(observe: ObserveFn, n: int = 3) -> tuple[StateSnapshot | None, list, str | None]:
    stable: list[tuple[str, ...]] = []
    snap = None
    scene_holder: list = []
    for _ in range(n):
        _recs, _graph, snap, scene_holder = observe()
        stable.append(tuple(snap.surface.action_ids))
    if len(set(stable)) != 1:
        return snap, scene_holder, "unstable legal action surface"
    return snap, scene_holder, None


def run_armed_collect(
    session: Session,
    target: Any,
    observe: ObserveFn,
    *,
    n_steps: int,
    owner_factory: Callable[[Any], Any] | None = None,
    config: ArmConfig | None = None,
    is_focused: Callable[[], bool] | None = None,
) -> tuple[int, dict]:
    from loop.adaptive.cli import _shadow_schema_abort, _try_promote
    from loop.os.window import is_foreground

    cfg = config or ArmConfig()
    owner_factory = owner_factory or create_owner
    focused_fn = is_focused if is_focused is not None else (lambda: is_foreground(target))
    owner = owner_factory(target)
    executor = Executor(
        snapshot_max_age_s=cfg.snapshot_max_age_s,
        decision_max_age_s=cfg.decision_deadline_s,
    )
    directive_store = GenerationStore()
    episode_id = uuid.uuid4().hex
    identity = session.target or {}
    pid = int(identity.get("pid") or 0)
    stages_done: list[int] = []
    verified = 0
    worker: PolicyWorker | None = None

    def stop(reason: str, *, stage: int | None = None) -> tuple[int, dict]:
        owner.release_all(reason)
        payload: dict[str, Any] = {
            "mode": "armed",
            "error": reason,
            "stages_completed": stages_done,
            "verified": verified,
            "revision_id": session.schema.revision.revision_id,
            "workspace": str(session.workspace.resolve()),
        }
        if stage is not None:
            payload["failed_stage"] = stage
        print(json.dumps(payload), file=sys.stderr, flush=True)
        return 2, payload

    try:
        if session.recovery_state():
            return stop("recovery_locked")

        # Stage 1 — boot + release-only (no primitive downs)
        owner.enable_actuation()
        release_status = owner.release_all("armed_boot")
        if str(release_status).startswith("failed:"):
            session.recovery_lock({"reason": "release_failure", "detail": release_status})
            return stop("release_failure", stage=1)
        if owner.drain_primitive_results():
            return stop("boot emitted primitives", stage=1)
        stages_done.append(1)

        warm = max(3, min(n_steps, max(3, session.schema.policy.min_agreeing_reads)))
        traces: list[Trace] = []
        recs = ()
        for _ in range(warm):
            recs, graph, snap, scene_holder = observe()
            reason = _shadow_schema_abort(session, graph)
            if reason:
                return stop(reason)
            traces.append(Trace(dict(snap.fields), dict(snap.fields), recs, recs))
        _try_promote(session, traces, recs)

        if not session.schema.actions:
            owner.release_all("no_promoted_action")
            return stop("no promoted action")

        snap, scene_holder, surf_err = _stable_surface(observe)
        if surf_err:
            owner.release_all(surf_err)
            return stop(surf_err)

        assert snap is not None

        def focused_now() -> bool:
            return focused_fn()

        def run_leased_act(
            stage: int,
            *,
            decision_source: str,
            candidate_id: str,
        ) -> tuple[bool, str]:
            nonlocal verified
            if session.recovery_state():
                owner.release_all("recovery_locked")
                return False, "recovery_locked"

            recs, graph, snap, scene_holder = observe()
            reason = _shadow_schema_abort(session, graph)
            if reason:
                owner.release_all(reason)
                return False, reason

            prov = getattr(observe, "provenance", {}) or {}
            integrity = str(prov.get("integrity") or "trusted")
            hwnd_val = int(prov.get("hwnd") or target.hwnd)
            try:
                native_obs, catalog, directive, mask = _compile_frame(
                    session,
                    snap,
                    episode_id=episode_id,
                    episode_generation=cfg.episode_generation,
                    hwnd=hwnd_val,
                    pid=pid,
                    capture_integrity=integrity,
                    capture_provenance=(str(prov.get("source") or "window"),),
                    directive_store=directive_store,
                    config=cfg,
                )
            except CatalogCompileError as exc:
                owner.release_all("catalog_compile_error")
                return False, f"catalog_compile_error:{exc}"

            if candidate_id not in snap.surface.action_ids:
                owner.release_all("not_on_surface")
                return False, "not_on_surface"

            now = time.monotonic()
            decision = StructuredDecision(
                candidate_id=candidate_id,
                confidence=1.0,
                probabilities={candidate_id: 1.0},
                decided_at=now,
                episode_generation=cfg.episode_generation,
                snapshot_id=snap.snapshot_id,
                directive_revision=directive.revision,
                catalog_revision=catalog.revision,
            )
            dispatch_receipt = _dispatch_decision(
                session,
                decision,
                native_obs,
                catalog,
                directive,
                mask,
                executor=executor,
                config=cfg,
                focused=focused_now(),
                now=now,
            )
            inv = (
                invocation_for_action(snap, decision.candidate_id)
                if dispatch_receipt.accepted
                else None
            )
            receipt: ExecutionReceipt | None = None
            if dispatch_receipt.accepted and dispatch_receipt.lease is not None and inv is not None:
                _bind_lease_context(session, dispatch_receipt.lease)
                receipt = session.act(
                    inv,
                    lambda _s, _b: None,
                    lambda: observe()[1],
                    before=snap,
                    lease=dispatch_receipt.lease,
                    owner=owner,
                    pre_dispatch=lambda: _pre_dispatch_from_observe(observe),
                    get_scene=lambda: scene_holder[0],
                )
                assert isinstance(receipt, ExecutionReceipt)

            _append_arm_receipt(
                session.workspace,
                _receipt_payload(
                    stage=stage,
                    snap=snap,
                    native_obs=native_obs,
                    catalog_revision=catalog.revision,
                    directive=directive,
                    mask_d=mask_digest(mask),
                    decision_source=decision_source,
                    dispatch_receipt=dispatch_receipt,
                    receipt=receipt,
                    capture_provenance=native_obs.capture_provenance,
                ),
            )

            if not dispatch_receipt.accepted:
                owner.release_all(dispatch_receipt.reason)
                return False, dispatch_receipt.reason
            if receipt is None:
                owner.release_all("no_invocation")
                return False, "no_invocation"
            if not receipt.accepted:
                owner.release_all(receipt.reason)
                return False, receipt.reason
            if str(receipt.release_status).startswith("failed:"):
                session.recovery_lock(
                    {"reason": "release_failure", "detail": receipt.release_status}
                )
                owner.release_all("release_failure")
                return False, "release_failure"
            if not receipt.expected_effect.verified:
                session.ledger.append_event(
                    "negative_evidence",
                    {
                        "action_id": inv.action_id,
                        "reason": receipt.expected_effect.reason,
                        "stage": stage,
                    },
                )
                owner.release_all("expected_effect_failed")
                return False, "expected effect not observed"
            verified += 1
            return True, "ok"

        # Stage 2 — one deterministic action on one-candidate surface
        if len(snap.surface.actions) != 1:
            owner.release_all("surface_not_unique")
            return stop("legal action surface is not unique", stage=2)

        candidate = snap.surface.action_ids[0]
        ok, msg = run_leased_act(2, decision_source="deterministic", candidate_id=candidate)
        if not ok:
            return stop(msg, stage=2)
        stages_done.append(2)

        # Stage 3 — repeat deterministic (no blind retry on failure)
        repeats = max(1, min(n_steps, 2))
        for _ in range(repeats):
            snap, scene_holder, surf_err = _stable_surface(observe, n=1)
            if surf_err or snap is None or len(snap.surface.actions) != 1:
                owner.release_all("surface_changed")
                return stop(surf_err or "surface not unique", stage=3)
            ok, msg = run_leased_act(
                3,
                decision_source="deterministic",
                candidate_id=snap.surface.action_ids[0],
            )
            if not ok:
                return stop(msg, stage=3)
        stages_done.append(3)

        # Stages 4–5 — Decider path when >=2 legal promoted actions on surface
        if len(session.schema.actions) >= 2:
            snap, scene_holder, surf_err = _stable_surface(observe)
            if surf_err or snap is None or len(snap.surface.actions) < 2:
                owner.release_all("multi_surface_unavailable")
            else:
                worker = PolicyWorker(
                    DeterministicPolicy(), inference_timeout_s=cfg.decision_deadline_s
                )
                try:
                    native_obs, catalog, directive, mask = _compile_frame(
                        session,
                        snap,
                        episode_id=episode_id,
                        episode_generation=cfg.episode_generation,
                        hwnd=int(getattr(observe, "provenance", {}).get("hwnd") or target.hwnd),
                        pid=pid,
                        capture_integrity="trusted",
                        capture_provenance=("window",),
                        directive_store=directive_store,
                        config=cfg,
                    )
                    filtered = _filtered_candidates(catalog, mask)
                    reactive = reactive_state_from_native(native_obs)
                    turn_id = worker.submit(
                        reactive,
                        directive,
                        filtered,
                        timeout_s=cfg.decision_deadline_s,
                    )
                    result = worker.poll(turn_id, now=time.monotonic())
                    if (
                        result.status is not PolicyTurnStatus.READY
                        or result.decision is None
                    ):
                        owner.release_all("policy_unavailable")
                        return stop("policy_unavailable", stage=4)
                    chosen = result.decision.candidate_id
                    ok, msg = run_leased_act(
                        4,
                        decision_source="decider",
                        candidate_id=chosen,
                    )
                    if not ok:
                        return stop(msg, stage=4)
                    stages_done.append(4)

                    snap, scene_holder, surf_err = _stable_surface(observe, n=1)
                    if surf_err or snap is None or len(snap.surface.actions) < 2:
                        owner.release_all("multi_surface_lost")
                        return stop(surf_err or "multi surface lost", stage=5)
                    native_obs, catalog, directive, mask = _compile_frame(
                        session,
                        snap,
                        episode_id=episode_id,
                        episode_generation=cfg.episode_generation,
                        hwnd=int(
                            getattr(observe, "provenance", {}).get("hwnd") or target.hwnd
                        ),
                        pid=pid,
                        capture_integrity="trusted",
                        capture_provenance=("window",),
                        directive_store=directive_store,
                        config=cfg,
                    )
                    filtered = _filtered_candidates(catalog, mask)
                    turn_id = worker.submit(
                        reactive_state_from_native(native_obs),
                        directive,
                        filtered,
                        timeout_s=cfg.decision_deadline_s,
                    )
                    result2 = worker.poll(turn_id, now=time.monotonic())
                    if (
                        result2.status is not PolicyTurnStatus.READY
                        or result2.decision is None
                    ):
                        owner.release_all("policy_unavailable")
                        return stop("policy_unavailable", stage=5)
                    ok, msg = run_leased_act(
                        5,
                        decision_source="decider",
                        candidate_id=result2.decision.candidate_id,
                    )
                    if not ok:
                        return stop(msg, stage=5)
                    stages_done.append(5)
                finally:
                    worker.shutdown()

        # Stage 6 — directive revision stub (observe must not stall)
        directive = _bootstrap_directive(
            directive_store,
            episode_generation=cfg.episode_generation,
            config=NativeShadowConfig(directive_ttl_s=cfg.directive_ttl_s),
        )
        directive_store.publish(
            cfg.episode_generation,
            replace(
                directive,
                revision=directive.revision + 1,
                published_at=time.monotonic(),
            ),
        )
        _recs, _graph, _snap, _holder = observe()
        stages_done.append(6)

        summary = {
            "mode": "armed",
            "stages_completed": stages_done,
            "verified": verified,
            "revision_id": session.schema.revision.revision_id,
            "workspace": str(session.workspace.resolve()),
        }
        return 0, summary
    finally:
        owner.release_all("armed_finally")
