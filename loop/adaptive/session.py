"""Generic adaptive collect session (no game id)."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from loop.adaptive.contracts import (
    EFFECT_STATUS_NOT_EXECUTED,
    EFFECT_STATUS_NO_EFFECT,
    EFFECT_STATUS_UNOBSERVABLE,
    EFFECT_STATUS_VERIFIED,
    EFFECT_STATUS_WRONG_EFFECT,
    ActuationLease,
    ExecutionReceipt,
    ExpectedEffectOutcome,
    PostActionObservationPolicy,
)
from loop.adaptive.runtime import (
    PrimitivesRunner,
    contradicted,
    effect_evaluable,
    invoke,
    project,
    verify,
)
from loop.adaptive.schema import (
    ActionInvocation,
    AdaptiveSchema,
    EvidenceLedger,
    HEAD_FILENAME,
    SceneGraph,
    StateSnapshot,
    atomic_write_revision,
    empty_schema,
    load_head,
    load_revision,
    revision_files,
    write_head,
)

TARGET_FILENAME = "target.json"
RECOVERY_FILENAME = "RECOVERY.lock"


def _stable_identity(identity: dict) -> dict:
    return {
        "selector": str(identity.get("selector") or ""),
        "exe": str(identity.get("exe") or ""),
        "window_class": str(identity.get("window_class") or ""),
    }


def identities_compatible(saved: dict, current: dict) -> bool:
    a, b = _stable_identity(saved), _stable_identity(current)
    if a["selector"] and b["selector"] and a["selector"] != b["selector"]:
        return False
    if a["exe"] and b["exe"] and a["exe"] != b["exe"]:
        return False
    if a["window_class"] and b["window_class"] and a["window_class"] != b["window_class"]:
        return False
    return True


def _as_float(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def _after_capture_trust(
    lease: ActuationLease,
    executed_ts: float,
    evidence: dict,
    *,
    previous_ts: float | None,
    previous_sequence: int | None,
) -> tuple[bool, str]:
    """One after-capture must be the same target, later than execution and independent.

    A repeated acquisition marker is not a new observation; unknown markers are
    accepted (synthetic routes) rather than invented.
    """

    integrity = str(evidence.get("integrity") or "trusted")
    if integrity != "trusted":
        return False, f"after-capture integrity {integrity}"
    hwnd = _as_int(evidence.get("hwnd"))
    if hwnd and lease.hwnd and hwnd != int(lease.hwnd):
        return False, "after-capture target changed"
    pid = _as_int(evidence.get("pid"))
    if pid and lease.pid and pid != int(lease.pid):
        return False, "after-capture process changed"
    ts = _as_float(evidence.get("capture_ts_monotonic"))
    if ts is None:
        ts = _as_float(evidence.get("grab_requested_ts_monotonic"))
    if ts is not None and ts <= executed_ts:
        return False, "after-capture not later than execution"
    sequence = _as_int(evidence.get("observation_sequence"))
    if sequence is not None and previous_sequence is not None and sequence <= previous_sequence:
        return False, "repeated observation sequence"
    if ts is not None and previous_ts is not None and ts <= previous_ts:
        return False, "repeated acquisition"
    return True, "ok"


def _classify_after(
    before: StateSnapshot,
    after: StateSnapshot,
    spec: Any,
    bindings: dict | None,
) -> str:
    """One trusted after-frame: verified, wrong_effect, no_effect or unobservable."""

    if verify(before, after, spec, bindings=bindings):
        return EFFECT_STATUS_VERIFIED
    if contradicted(before, after, spec, bindings=bindings):
        return EFFECT_STATUS_WRONG_EFFECT
    if effect_evaluable(before, after, spec, bindings=bindings):
        return EFFECT_STATUS_NO_EFFECT
    return EFFECT_STATUS_UNOBSERVABLE


class Session:
    def __init__(self, workspace: Path, window_selector: str | None = None) -> None:
        self.workspace = Path(workspace)
        self.window_selector = window_selector
        self.schema_dir = self.workspace / "schema"
        self.schema_dir.mkdir(parents=True, exist_ok=True)
        self.ledger = EvidenceLedger(self.workspace / "ledger.jsonl")
        self._schema = self._load_or_bootstrap_schema()
        self._target: dict | None = self._load_target()

    @property
    def schema(self) -> AdaptiveSchema:
        return self._schema

    def _target_path(self) -> Path:
        return self.workspace / TARGET_FILENAME

    def _recovery_path(self) -> Path:
        return self.workspace / RECOVERY_FILENAME

    def _load_target(self) -> dict | None:
        path = self._target_path()
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def bind_target(self, identity: dict) -> dict:
        current = dict(identity)
        saved = self._target
        if saved is not None and not identities_compatible(saved, current):
            raise ValueError("workspace target identity mismatch")
        merged = dict(saved or {})
        merged.update(current)
        if saved is not None:
            for key in ("selector", "exe", "window_class"):
                if saved.get(key) and not current.get(key):
                    merged[key] = saved[key]
        dest = self._target_path()
        tmp = dest.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(merged, sort_keys=True, indent=2), encoding="utf-8")
        tmp.replace(dest)
        self._target = merged
        return merged

    @property
    def target(self) -> dict | None:
        return self._target

    def recovery_lock(self, payload: dict) -> None:
        dest = self._recovery_path()
        tmp = dest.with_suffix(".lock.tmp")
        tmp.write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")
        tmp.replace(dest)

    def recovery_clear(self) -> None:
        path = self._recovery_path()
        if path.exists():
            path.unlink()

    def recovery_state(self) -> dict | None:
        path = self._recovery_path()
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def _load_or_bootstrap_schema(self) -> AdaptiveSchema:
        if (self.schema_dir / HEAD_FILENAME).exists():
            return load_head(self.schema_dir)
        revs = revision_files(self.schema_dir)
        if len(revs) == 1:
            schema = load_revision(revs[0])
            write_head(self.schema_dir, schema.revision.revision_id)
            return schema
        if len(revs) > 1:
            raise ValueError("schema store missing HEAD.json")
        schema = empty_schema()
        atomic_write_revision(self.schema_dir, schema)
        return schema

    def accept_schema(self, schema: AdaptiveSchema) -> None:
        self._schema = schema

    def set_schema(self, schema: AdaptiveSchema) -> None:
        atomic_write_revision(self.schema_dir, schema)
        self._schema = schema

    def observe(self, scene: SceneGraph) -> StateSnapshot:
        return project(scene, self._schema)

    def act(
        self,
        invocation: ActionInvocation,
        runner: PrimitivesRunner,
        after_scene: SceneGraph | Callable[[], SceneGraph],
        *,
        before: StateSnapshot | None = None,
        lease: ActuationLease | None = None,
        owner: Any | None = None,
        pre_dispatch: Callable[[], tuple[bool, str]] | None = None,
        get_scene: Callable[[], SceneGraph | None] | None = None,
        after_capture: Callable[[], tuple[int | None, int | None, tuple[str, ...]]]
        | None = None,
        observation_policy: PostActionObservationPolicy | None = None,
        after_evidence: Callable[[], dict] | None = None,
    ) -> bool | ExecutionReceipt:
        if before is None:
            raise ValueError("before snapshot required for act()")
        from loop.adaptive.runtime import InvocationError, execute

        def _rejected(reason: str) -> ExecutionReceipt:
            return ExecutionReceipt(
                accepted=False,
                reason=reason,
                primitive_results=(),
                release_status="skipped",
                after_hwnd=None,
                after_pid=None,
                after_capture_provenance=(),
                expected_effect=ExpectedEffectOutcome(
                    False, reason, status=EFFECT_STATUS_NOT_EXECUTED
                ),
            )

        if lease is None:
            return _rejected("lease required")

        invoke(before, invocation)
        primitive_results: tuple[dict[str, Any], ...] = ()
        release_status = "skipped"
        accepted = True
        reason = "ok"
        after_hwnd: int | None = None
        after_pid: int | None = None
        after_provenance: tuple[str, ...] = ()
        # Lower bound for "strictly later than execution"; refreshed when the
        # primitives actually run so a slow plan cannot be compared to a stale bound.
        executed_ts = time.monotonic()

        def _guard() -> None:
            nonlocal accepted, reason
            if lease is None:
                return
            reject = _lease_reject_reason(lease, self, before, invocation)
            if reject:
                accepted = False
                reason = reject
                raise InvocationError(reject)
            if pre_dispatch is not None:
                ok, msg = pre_dispatch()
                if not ok:
                    accepted = False
                    reason = msg
                    raise InvocationError(msg)
            if owner is not None:
                ok, msg = owner.check_target()
                if not ok:
                    accepted = False
                    reason = msg
                    raise InvocationError(msg)

        try:
            reject = _lease_reject_reason(lease, self, before, invocation)
            if reject:
                return _rejected(reject)
            if owner is None:
                return _rejected("owner required with lease")
            if pre_dispatch is not None:
                ok, msg = pre_dispatch()
                if not ok:
                    return _rejected(msg)

            def leased_runner(steps, bindings):
                scene = get_scene() if get_scene is not None else None
                owner.run_plan(steps, bindings, scene=scene, guard=_guard)

            execute(invocation, self._schema, leased_runner)
            executed_ts = time.monotonic()
            primitive_results = owner.drain_primitive_results()
        except InvocationError as exc:
            if accepted:
                accepted = False
                reason = str(exc)
        finally:
            if owner is not None and lease is not None:
                release_status = owner.release_all(reason if not accepted else "act_complete")
                if release_status.startswith("failed:"):
                    self.recovery_lock(
                        {"reason": "release_failure", "detail": release_status}
                    )

        policy = observation_policy or PostActionObservationPolicy()
        spec = next(
            (a for a in self._schema.actions if a.action_id == invocation.action_id),
            None,
        )

        def _next_after() -> tuple[StateSnapshot, dict]:
            scene = after_scene() if callable(after_scene) else after_scene
            snap_after = project(scene, self._schema)
            evidence: dict[str, Any] = {}
            if after_evidence is not None:
                declared = after_evidence()
                if isinstance(declared, dict):
                    evidence.update(declared)
            if after_capture is not None and "hwnd" not in evidence:
                hwnd, pid, provenance = after_capture()
                evidence["hwnd"] = hwnd
                evidence["pid"] = pid
                if provenance:
                    evidence["source"] = str(provenance[0])
            return snap_after, evidence

        status = ""
        effect_reason = ""
        records: list[dict[str, Any]] = []
        negative_frames = 0
        attempts = 0
        previous_ts: float | None = None
        previous_sequence: int | None = None
        deadline = time.monotonic() + max(0.0, float(policy.max_window_s))
        max_frames = max(1, int(policy.max_frames))
        while True:
            after, evidence = _next_after()
            attempts += 1
            trusted, trust_reason = _after_capture_trust(
                lease,
                executed_ts,
                evidence,
                previous_ts=previous_ts,
                previous_sequence=previous_sequence,
            )
            frame_ts = _as_float(evidence.get("capture_ts_monotonic"))
            if frame_ts is None:
                frame_ts = _as_float(evidence.get("grab_requested_ts_monotonic"))
            frame_sequence = _as_int(evidence.get("observation_sequence"))
            verdict = ""
            if trusted:
                if frame_ts is not None:
                    previous_ts = frame_ts
                if frame_sequence is not None:
                    previous_sequence = frame_sequence
                verdict = (
                    EFFECT_STATUS_UNOBSERVABLE
                    if spec is None
                    else _classify_after(before, after, spec, invocation.bindings)
                )
                if verdict == EFFECT_STATUS_VERIFIED:
                    status, effect_reason = verdict, "verified"
                elif verdict == EFFECT_STATUS_WRONG_EFFECT:
                    status, effect_reason = verdict, "expected effect contradicted"
                elif verdict == EFFECT_STATUS_NO_EFFECT:
                    negative_frames += 1
            records.append(
                {
                    "before_snapshot_id": before.snapshot_id,
                    "after_snapshot_id": after.snapshot_id,
                    "trusted": trusted,
                    "trust_reason": trust_reason,
                    "verdict": verdict or "insufficient",
                    "hwnd": evidence.get("hwnd"),
                    "pid": evidence.get("pid"),
                    "source": evidence.get("source"),
                    "integrity": str(evidence.get("integrity") or "trusted"),
                    "capture_ts_monotonic": frame_ts,
                    "observation_sequence": frame_sequence,
                }
            )
            after_hwnd = _as_int(evidence.get("hwnd"))
            after_pid = _as_int(evidence.get("pid"))
            source = evidence.get("source")
            if source:
                after_provenance = (str(source),)
            if status:
                break
            if attempts >= max_frames or time.monotonic() >= deadline:
                break
            if policy.cadence_s > 0:
                time.sleep(policy.cadence_s)
        if not status:
            if spec is None:
                status, effect_reason = EFFECT_STATUS_UNOBSERVABLE, "unknown action"
            elif negative_frames >= max(1, int(policy.required_negative_frames)):
                status, effect_reason = EFFECT_STATUS_NO_EFFECT, "expected effect not observed"
            else:
                status, effect_reason = (
                    EFFECT_STATUS_UNOBSERVABLE,
                    "insufficient trusted after-capture evidence",
                )
        if spec is None:
            accepted = False
            reason = reason if reason != "ok" else "unknown action"
        effect = ExpectedEffectOutcome(
            verified=status == EFFECT_STATUS_VERIFIED,
            reason=effect_reason,
            status=status,
            evidence=tuple(records),
        )
        return ExecutionReceipt(
            accepted=accepted,
            reason=reason,
            primitive_results=primitive_results,
            release_status=release_status,
            after_hwnd=after_hwnd,
            after_pid=after_pid,
            after_capture_provenance=after_provenance,
            expected_effect=effect,
        )

    def write_step_log(self, path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")


def _lease_reject_reason(
    lease: ActuationLease,
    session: Session,
    before: StateSnapshot,
    invocation: ActionInvocation,
    *,
    now: float | None = None,
) -> str | None:
    for field in ("catalog_revision", "policy_revision", "hard_mask_digest"):
        if not str(getattr(lease, field, "") or "").strip():
            return f"{field} missing"
    for session_attr in (
        "_catalog_revision",
        "_directive_revision",
        "_policy_revision",
        "_episode_generation",
        "_hard_mask_digest",
    ):
        if not hasattr(session, session_attr):
            return "lease context missing"
    current_digest = str(getattr(session, "_hard_mask_digest", "") or "")
    if current_digest != lease.hard_mask_digest:
        return "mask digest stale"
    now = time.monotonic() if now is None else now
    if now >= lease.expires_at_monotonic:
        return "lease expired"
    if lease.snapshot_id != before.snapshot_id:
        return "snapshot stale"
    if lease.schema_revision_id != session.schema.revision.revision_id:
        return "schema revision changed"
    if lease.selected_action_id != invocation.action_id:
        return "selected action mismatch"
    if invocation.snapshot_id != before.snapshot_id:
        return "invocation snapshot mismatch"
    if invocation.action_id not in before.surface.action_ids:
        return "missing surface membership"
    saved = session.target
    if saved is not None:
        if int(saved.get("hwnd", lease.hwnd)) != lease.hwnd:
            return "hwnd mismatch"
        if int(saved.get("pid", lease.pid)) != lease.pid:
            return "pid mismatch"
        if not identities_compatible(lease.target_identity, saved):
            return "target identity mismatch"
    for session_attr, lease_field in (
        ("_catalog_revision", "catalog_revision"),
        ("_directive_revision", "directive_revision"),
        ("_policy_revision", "policy_revision"),
        ("_episode_generation", "episode_generation"),
    ):
        if hasattr(session, session_attr):
            if getattr(session, session_attr) != getattr(lease, lease_field):
                return f"{lease_field} changed"
    return None
