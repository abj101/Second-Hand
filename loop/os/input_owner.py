"""Process-lifetime native input owner (mouse + keyboard)."""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes
from typing import Any, Callable

from loop.adaptive.schema import PrimitiveStep
from loop.os.keyboard import ARMED_KEY_ALLOWLIST, Keyboard
from loop.os.mouse import Mouse
from loop.os.primitives import KeyPrimitive, MousePrimitive
from loop.os.window import WindowTarget, client_to_screen, is_foreground

GuardFn = Callable[[], None]

MAX_PLAN_S = 10.0
_MAX_SLEEP_S = 5.0


class NativeInputOwner:
    """Single owner for product-path mouse and keyboard input."""

    def __init__(
        self,
        target: WindowTarget,
        *,
        mouse: Mouse | None = None,
        keyboard: Keyboard | None = None,
        armed_keys: frozenset[str] = ARMED_KEY_ALLOWLIST,
    ) -> None:
        self._target = target
        self._expected_hwnd = int(target.hwnd)
        self._expected_pid = self._read_pid()
        self._mouse = mouse or Mouse()
        self._keyboard = keyboard or Keyboard()
        self._armed_keys = armed_keys
        self._actuation_enabled = False
        self._primitive_results: list[dict[str, Any]] = []
        # ponytail: Keyboard.Timer is the monotonic release supervisor; one owner thread if overlapping holds contend.

    @classmethod
    def for_target(cls, target: WindowTarget) -> NativeInputOwner:
        return cls(target)

    def enable_actuation(self) -> None:
        if self._actuation_enabled:
            return
        self._keyboard.safe_startup_release(self._armed_keys)
        self._mouse.release_all()
        self._actuation_enabled = True

    def shutdown(self) -> None:
        self.release_all("shutdown")

    def check_target(self) -> tuple[bool, str]:
        if not ctypes.windll.user32.IsWindow(self._expected_hwnd):
            return False, "target hwnd invalid"
        pid = self._read_pid()
        if pid != self._expected_pid:
            return False, "target pid changed"
        if not is_foreground(self._target):
            return False, "target not foreground"
        return True, "ok"

    def release_all(self, reason: str) -> str:
        try:
            self._keyboard.release_all()
            self._mouse.release_all()
            self._record("release_all", ok=True, reason=reason)
            return "released"
        except OSError as exc:
            self._record("release_all", ok=False, reason=f"{reason}:{exc}")
            return f"failed:{exc}"

    def drain_primitive_results(self) -> tuple[dict[str, Any], ...]:
        out = tuple(self._primitive_results)
        self._primitive_results.clear()
        return out

    def run_plan(
        self,
        steps: tuple[PrimitiveStep, ...],
        bindings: dict[str, Any],
        *,
        scene: Any | None,
        guard: GuardFn | None = None,
    ) -> None:
        if not self._actuation_enabled:
            self.enable_actuation()
        from loop.adaptive.runtime import InvocationError

        plan_started = time.monotonic()
        first = True
        for step in steps:
            if time.monotonic() - plan_started > MAX_PLAN_S:
                raise InvocationError("plan duration cap exceeded")
            if guard is not None and (first or step.op in ("click", "key_tap", "key_hold")):
                guard()
            first = False
            ok, msg = self.check_target()
            if not ok:
                raise InvocationError(msg)
            self._run_step(step, bindings, scene=scene, guard=guard)

    def _run_step(
        self,
        step: PrimitiveStep,
        bindings: dict[str, Any],
        *,
        scene: Any | None,
        guard: GuardFn | None,
    ) -> None:
        from loop.adaptive.runtime import InvocationError, normalized_click_point

        params = dict(step.params)
        if step.op == "sleep":
            duration_s = min(float(params.get("duration_s", 0.0)), _MAX_SLEEP_S)
            if duration_s > 0:
                time.sleep(duration_s)
            self._record("sleep", ok=True, duration_s=duration_s)
            return
        if step.op == "move":
            node_id = _binding_entity(step, bindings)
            if scene is None:
                raise InvocationError("no scene for move")
            nx, ny = normalized_click_point(scene, str(node_id))
            sx, sy = client_to_screen(self._target, nx, ny)
            if guard is not None:
                guard()
            self._mouse.execute(MousePrimitive(op="move", x=nx, y=ny), screen_xy=(sx, sy))
            self._record("move", ok=True, node_id=str(node_id))
            return
        if step.op == "click":
            node_id = _binding_entity(step, bindings)
            if scene is None:
                raise InvocationError("no scene for click")
            button = str(params.get("button", "left"))
            nx, ny = normalized_click_point(scene, str(node_id))
            sx, sy = client_to_screen(self._target, nx, ny)
            if guard is not None:
                guard()
            self._mouse.execute(
                MousePrimitive(op="click", x=nx, y=ny, button=button),  # type: ignore[arg-type]
                screen_xy=(sx, sy),
            )
            self._record("click", ok=True, node_id=str(node_id), button=button)
            return
        if step.op == "key_tap":
            key = str(params["key"])
            duration_ms = int(params.get("duration_ms", 50))
            if guard is not None:
                guard()
            self._keyboard.tap(key, duration_ms=duration_ms)
            self._record("key_tap", ok=True, key=key, duration_ms=duration_ms)
            return
        if step.op == "key_hold":
            key = str(params["key"])
            duration_ms = int(params["duration_ms"])
            if guard is not None:
                guard()
            self._keyboard.hold(key, duration_ms=duration_ms)
            self._record("key_hold", ok=True, key=key, duration_ms=duration_ms)
            return
        raise InvocationError(f"unsupported primitive op: {step.op!r}")

    def _record(self, kind: str, *, ok: bool, **fields: Any) -> None:
        self._primitive_results.append({"kind": kind, "ok": ok, **fields})

    def _read_pid(self) -> int:
        pid = wintypes.DWORD(0)
        ctypes.windll.user32.GetWindowThreadProcessId(int(self._target.hwnd), ctypes.byref(pid))
        return int(pid.value)


def _binding_entity(step: PrimitiveStep, bindings: dict[str, Any]) -> str:
    from loop.adaptive.runtime import InvocationError

    for name in step.bindings:
        if name in bindings:
            return str(bindings[name])
    if "target" in bindings:
        return str(bindings["target"])
    if "node_id" in bindings:
        return str(bindings["node_id"])
    raise InvocationError(f"{step.op} binding missing entity")
