"""Generic adaptive collect session (no game id)."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

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
    ) -> bool:
        if before is None:
            raise ValueError("before snapshot required for act()")
        invoke(before, invocation)
        from loop.adaptive.runtime import execute

        execute(invocation, self._schema, runner)
        scene = after_scene() if callable(after_scene) else after_scene
        after = project(scene, self._schema)
        spec = next(
            (a for a in self._schema.actions if a.action_id == invocation.action_id),
            None,
        )
        if spec is None:
            return False
        return verify(before, after, spec, bindings=invocation.bindings)

    def write_step_log(self, path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")
