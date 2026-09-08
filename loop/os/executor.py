from __future__ import annotations

import time
from dataclasses import replace
from typing import Sequence

from loop.log.schema import ActionLog
from loop.os.chords import chord_name, is_chord
from loop.os.gamepad import Gamepad, GamepadUnavailable, get_gamepad
from loop.os.keyboard import Keyboard
from loop.os.mouse import Mouse
from loop.os.primitives import GamepadPrimitive, KeyPrimitive, MousePrimitive, Primitive, mouse_click
from loop.os.profile import CaptureProfile
from loop.os.tables import _STICK_DIRS, names, resolve
from loop.os.window import WindowCapture, client_to_screen, focus_window, foreground_title
from loop.safety.panic import PanicFocus, should_panic


class FocusLost(RuntimeError):
    """Target window is not foreground; refusing to type into another app."""


class OsController:
    """Capture + keys + mouse + optional pad. No GSM, no game verbs."""

    def __init__(
        self,
        title_substr: str,
        *,
        require_focus: bool = True,
        enable_gamepad: bool = True,
        profile: CaptureProfile | None = None,
        hwnd: int | None = None,
        panic_titles: tuple[str, ...] = ("Cursor",),
    ) -> None:
        self.capture = WindowCapture(title_substr, profile=profile, hwnd=hwnd)
        self.keyboard = Keyboard()
        self.mouse = Mouse()
        self.require_focus = require_focus
        self.panic_titles = panic_titles
        self.gamepad: Gamepad | None = None
        self.gamepad_error: str | None = None
        if enable_gamepad:
            self.gamepad = get_gamepad()
            if self.gamepad is None:
                self.gamepad_error = (
                    "ViGEmBus is not installed (or vgamepad failed). "
                    "Install Nefarius ViGEmBus, then pip install vgamepad."
                )

    @property
    def target(self):
        return self.capture.target

    def focus(self) -> bool:
        self.capture.refresh()
        return bool(focus_window(self.capture.target))

    def _ensure_focus(self) -> None:
        """Steal focus onto the game, then refuse if we still landed on a panic title."""
        self.focus()
        time.sleep(0.05)
        title = foreground_title()
        if should_panic(title, self.panic_titles):
            self.release_all()
            raise PanicFocus(
                f"Foreground {title!r} is a panic title after focus; input released."
            )
        if not self.require_focus:
            return
        if self.capture.is_foreground():
            return
        self.focus()
        time.sleep(0.08)
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
        if duration_ms is not None and primitives:
            first = primitives[0]
            if isinstance(first, KeyPrimitive) and first.op in {"hold", "down", "tap"}:
                first = replace(first, op="hold", duration_ms=duration_ms)
                primitives = (first,) + primitives[1:]
            elif isinstance(first, GamepadPrimitive) and first.op in {
                "button_tap",
                "button_hold",
                "stick",
                "trigger",
                "dpad",
            }:
                first = replace(first, duration_ms=duration_ms)
                primitives = (first,) + primitives[1:]
        self.execute(primitives)

    def execute_chord(self, table_name: str, action: ActionLog) -> str:
        duration_ms = int(action.duration_ms or 400)
        name = chord_name(action) or action.name or "idle"
        token = (action.name or name or "").split("+")[0]
        table = set(names(table_name))
        self._ensure_focus()
        try:
            if action.click_x is not None and action.click_y is not None:
                self._dispatch(mouse_click(float(action.click_x), float(action.click_y)))
                return chord_name(action) or "pt"
            named = token in table and token not in {"idle", "end"}
            if named and not is_chord(action):
                self.execute_named(table_name, token, duration_ms=duration_ms)
                return token
            if self.gamepad is not None and is_chord(action):
                stick_l = _STICK_DIRS.get(action.stick_l) if action.stick_l else None
                stick_r = _STICK_DIRS.get(action.stick_r) if action.stick_r else None
                held = tuple(b for b in action.held if b in {"a", "b", "x", "y", "lb", "rb", "ls", "rs", "start", "back"})
                tapped = tuple(b for b in action.tapped if b in {"a", "b", "x", "y", "lb", "rb", "ls", "rs", "start", "back"})
                tapped_dpad = tuple(b.removeprefix("dpad_") for b in action.tapped if b.startswith("dpad_"))
                held_dpad = tuple(b.removeprefix("dpad_") for b in action.held if b.startswith("dpad_"))
                triggers: list[tuple[str, float]] = []
                if "lt" in action.held:
                    triggers.append(("left", 1.0))
                if "rt" in action.held:
                    triggers.append(("right", 1.0))
                self.gamepad.play_chord(
                    stick_l=stick_l,
                    stick_r=stick_r,
                    held_buttons=held,
                    tapped_buttons=tapped,
                    held_triggers=tuple(triggers),
                    tapped_dpad=tapped_dpad,
                    held_dpad=held_dpad,
                    duration_ms=duration_ms,
                )
                return name
            if named:
                self.execute_named(table_name, token, duration_ms=duration_ms)
                return token
            return name
        except Exception:
            self.release_all()
            raise

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
            self.capture.refresh()
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
