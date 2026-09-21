"""Generic adaptive collect session (no game id)."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from loop.adaptive.contracts import ActuationLease, ExecutionReceipt, ExpectedEffectOutcome
from loop.adaptive.runtime import PrimitivesRunner, invoke, project, verify
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
    ) -> bool | ExecutionReceipt:
        if before is None:
            raise ValueError("before snapshot required for act()")
        from loop.adaptive.runtime import InvocationError, execute

        invoke(before, invocation)
        primitive_results: tuple[dict[str, Any], ...] = ()
        release_status = "skipped"
        accepted = True
        reason = "ok"
        after_hwnd: int | None = None
        after_pid: int | None = None
        after_provenance: tuple[str, ...] = ()

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
            if lease is not None:
                reject = _lease_reject_reason(lease, self, before, invocation)
                if reject:
                    return ExecutionReceipt(
                        accepted=False,
                        reason=reject,
                        primitive_results=(),
                        release_status="skipped",
                        after_hwnd=None,
                        after_pid=None,
                        after_capture_provenance=(),
                        expected_effect=ExpectedEffectOutcome(False, reject),
                    )
                if owner is None:
                    return ExecutionReceipt(
                        accepted=False,
                        reason="owner required with lease",
                        primitive_results=(),
                        release_status="skipped",
                        after_hwnd=None,
                        after_pid=None,
                        after_capture_provenance=(),
                        expected_effect=ExpectedEffectOutcome(False, "owner required"),
                    )
                if pre_dispatch is not None:
                    ok, msg = pre_dispatch()
                    if not ok:
                        return ExecutionReceipt(
                            accepted=False,
                            reason=msg,
                            primitive_results=(),
                            release_status="skipped",
                            after_hwnd=None,
                            after_pid=None,
                            after_capture_provenance=(),
                            expected_effect=ExpectedEffectOutcome(False, msg),
                        )

                def leased_runner(steps, bindings):
                    scene = get_scene() if get_scene is not None else None
                    owner.run_plan(steps, bindings, scene=scene, guard=_guard)

                execute(invocation, self._schema, leased_runner)
                primitive_results = owner.drain_primitive_results()
            else:
                execute(invocation, self._schema, runner)
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

        scene = after_scene() if callable(after_scene) else after_scene
        after = project(scene, self._schema)
        spec = next(
            (a for a in self._schema.actions if a.action_id == invocation.action_id),
            None,
        )
        if spec is None:
            if lease is None:
                return False
            effect = ExpectedEffectOutcome(False, "unknown action")
            return ExecutionReceipt(
                accepted=accepted and False,
                reason=reason if reason != "ok" else "unknown action",
                primitive_results=primitive_results,
                release_status=release_status,
                after_hwnd=after_hwnd,
                after_pid=after_pid,
                after_capture_provenance=after_provenance,
                expected_effect=effect,
            )
        effect_ok = verify(before, after, spec, bindings=invocation.bindings)
        if lease is None:
            return effect_ok
        effect = ExpectedEffectOutcome(
            verified=effect_ok,
            reason="verified" if effect_ok else "expected effect not observed",
        )
        if self.target is not None:
            after_hwnd = int(self.target.get("hwnd", 0)) or None
            after_pid = int(self.target.get("pid", 0)) or None
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
