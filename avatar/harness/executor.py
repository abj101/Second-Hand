from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Sequence

from avatar.harness.gamepad import Gamepad, GamepadUnavailable
from avatar.harness.keyboard import Keyboard
from avatar.harness.mouse import Mouse
from avatar.harness.primitives import GamepadPrimitive, KeyPrimitive, MousePrimitive, Primitive
from avatar.harness.tables import clip_plan, resolve
from avatar.harness.profile import CaptureProfile
from avatar.harness.window import WindowCapture, client_to_screen, focus_window
from avatar.schema import ActResponse, ActionStep


class FocusLost(RuntimeError):
    """Target window is not foreground; refusing to type into another app."""


@dataclass
class CycleResult:
    parsed: ActResponse
    executed: list[str]
    invoked_skill: str | None
    raw: dict[str, Any]
    invoked_gsm: str | None = None
    gsm_ticks: int = 0
    detector_family: str | None = None
    dino_labels: list[str] | None = None
    state_source: str | None = None


class OsController:
    """Single OS computer-use controller: capture + keys + mouse + optional pad."""

    def __init__(
        self,
        title_substr: str,
        *,
        require_focus: bool = True,
        enable_gamepad: bool = True,
        skip_unchanged: bool | None = None,
        profile: CaptureProfile | None = None,
        hwnd: int | None = None,
    ) -> None:
        self.capture = WindowCapture(
            title_substr,
            skip_unchanged=skip_unchanged,
            profile=profile,
            hwnd=hwnd,
        )
        self.keyboard = Keyboard()
        self.mouse = Mouse()
        self.require_focus = require_focus
        self.gamepad: Gamepad | None = None
        self.gamepad_error: str | None = None
        if enable_gamepad:
            try:
                self.gamepad = Gamepad()
            except GamepadUnavailable as exc:
                self.gamepad_error = str(exc)

    @property
    def target(self):
        return self.capture.target

    def focus(self) -> None:
        self.capture.refresh()
        focus_window(self.capture.target)

    def _ensure_focus(self) -> None:
        if not self.require_focus:
            return
        if self.capture.is_foreground():
            return
        self.focus()
        if not self.capture.is_foreground():
            raise FocusLost(
                f"Window {self.capture.target.title!r} is not in the foreground. "
                "Refusing to send input (would hit another app)."
            )

    def release_all(self) -> None:
        self.keyboard.release_all()
        self.mouse.release_all()
        if self.gamepad is not None:
            self.gamepad.reset()

    def execute(self, primitives: Sequence[Primitive]) -> None:
        try:
            self._ensure_focus()
            for prim in primitives:
                self._dispatch(prim)
        except Exception:
            self.release_all()
            raise

    def execute_named(
        self,
        table_name: str,
        action_name: str,
        *,
        duration_ms: int | None = None,
    ) -> None:
        action = resolve(table_name, action_name)
        primitives = action.primitives
        # VLM often hallucinates duration_ms on taps (2048 swipes). Only honor it
        # when the table entry is already a hold, or the caller asked for a hold.
        if duration_ms is not None and primitives:
            first = primitives[0]
            if isinstance(first, KeyPrimitive) and first.op in {"hold", "down"}:
                first = replace(first, op="hold", duration_ms=duration_ms)
                primitives = (first,) + primitives[1:]
        self.execute(primitives)

    def run_gsm(
        self,
        spec: Any,
        table_name: str,
        *,
        plan: ActResponse | None = None,
        runtime: Any = None,
        max_ticks: int = 64,
    ) -> CycleResult:
        from avatar.gsm.runtime import run_gsm_loop

        def _grab() -> Any:
            frame, _skipped = self.capture.grab(fresh=True)
            return frame

        try:
            return run_gsm_loop(
                spec,
                table_name,
                self.execute_named,
                grab=_grab,
                max_ticks=max_ticks,
                runtime=runtime,
                plan=plan,
            )
        except Exception:
            self.release_all()
            raise

    def execute_plan(
        self,
        plan: ActResponse,
        table_name: str,
        *,
        max_actions: int = 5,
        memory: Any = None,
        skills: Any = None,
        gsms: Any = None,
        runtime: Any = None,
    ) -> CycleResult:
        executed: list[str] = []
        if plan.invoke_gsm and gsms is not None:
            spec = gsms.get(plan.invoke_gsm)
            if spec is not None and getattr(spec, "enabled", False):
                if hasattr(gsms, "record_invoke"):
                    gsms.record_invoke(plan.invoke_gsm)
                rt = runtime if runtime is not None else getattr(gsms, "runtime", None)
                if rt is not None and memory is not None and hasattr(rt, "banned"):
                    rt.banned = set(getattr(memory, "banned", ()) or ())
                return self.run_gsm(spec, table_name, plan=plan, runtime=rt)
            try:
                resolve(table_name, plan.invoke_gsm)
                plan = replace(
                    plan,
                    invoke_gsm=None,
                    actions=[ActionStep(name=plan.invoke_gsm, details="invoke_gsm alias")],
                )
            except KeyError:
                pass
        invoked = plan.invoke_skill
        if invoked:
            skill = skills.get(invoked) if skills is not None else None
            if skill is not None and getattr(skill, "enabled", False):
                for name in skill.do:
                    self.execute_named(table_name, name)
                    executed.append(name)
                    if hasattr(skills, "record_invoke"):
                        skills.record_invoke(invoked)
                return CycleResult(plan, executed, invoked, plan.model_dump())
            try:
                resolve(table_name, invoked)
                plan = replace(
                    plan,
                    invoke_skill=None,
                    actions=[ActionStep(name=invoked, details="invoke_skill alias")],
                )
            except KeyError:
                pass
        for step in clip_plan(plan.actions, max_actions):
            self.execute_named(table_name, step.name, duration_ms=step.duration_ms)
            executed.append(step.name)
        return CycleResult(plan, executed, None, plan.model_dump())

    def close(self) -> None:
        self.release_all()
        if self.gamepad is not None:
            self.gamepad.close()

    def __enter__(self) -> OsController:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _dispatch(self, prim: Primitive) -> None:
        if isinstance(prim, KeyPrimitive):
            self.keyboard.execute(prim)
            return
        if isinstance(prim, MousePrimitive):
            screen_xy = None
            screen_xy2 = None
            if prim.x is not None and prim.y is not None:
                screen_xy = client_to_screen(self.capture.target, prim.x, prim.y)
            if prim.x2 is not None and prim.y2 is not None:
                screen_xy2 = client_to_screen(self.capture.target, prim.x2, prim.y2)
            self.mouse.execute(prim, screen_xy=screen_xy, screen_xy2=screen_xy2)
            return
        if isinstance(prim, GamepadPrimitive):
            if self.gamepad is None:
                raise GamepadUnavailable(
                    self.gamepad_error
                    or "Gamepad requested but ViGEmBus/vgamepad is not available."
                )
            self.gamepad.execute(prim)
            return
        raise TypeError(f"Unknown primitive: {prim!r}")
