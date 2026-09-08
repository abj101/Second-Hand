from __future__ import annotations

import time
from typing import TYPE_CHECKING

from .primitives import DPadDir, GamepadButton, GamepadPrimitive

if TYPE_CHECKING:
    from vgamepad import VX360Gamepad

TAP_MS = 40
DEFAULT_DPAD_MS = 80

_UNAVAILABLE_MSG = (
    "ViGEmBus is not installed (or vgamepad failed). "
    "Install Nefarius ViGEmBus, then pip install vgamepad. "
    "Keyboard/mouse still work."
)

_DPAD_BUTTONS: tuple[str, ...] = ("up", "down", "left", "right")

_gamepad_singleton: Gamepad | None = None
_gamepad_singleton_checked = False


class GamepadUnavailable(RuntimeError):
    """Raised when ViGEmBus or vgamepad is missing or cannot open a virtual pad."""


def gamepad_available() -> bool:
    """Return True if a virtual Xbox 360 pad can be constructed."""
    try:
        import vgamepad as vg

        pad = vg.VX360Gamepad()
        pad.reset()
        pad.update()
        return True
    except Exception:
        return False


def get_gamepad() -> Gamepad | None:
    """Process-level singleton; None when ViGEm/vgamepad is unavailable."""
    global _gamepad_singleton, _gamepad_singleton_checked
    if not _gamepad_singleton_checked:
        _gamepad_singleton_checked = True
        try:
            _gamepad_singleton = Gamepad()
        except GamepadUnavailable:
            _gamepad_singleton = None
    return _gamepad_singleton


class Gamepad:
    """Virtual Xbox 360 controller via vgamepad (ViGEmBus)."""

    def __init__(self) -> None:
        try:
            import vgamepad as vg
        except Exception as exc:
            raise GamepadUnavailable(_UNAVAILABLE_MSG) from exc

        self._vg = vg
        try:
            self._pad: VX360Gamepad = vg.VX360Gamepad()
        except Exception as exc:
            raise GamepadUnavailable(_UNAVAILABLE_MSG) from exc

    def execute(self, prim: GamepadPrimitive) -> None:
        op = prim.op
        if op in {"button_down", "button_up", "button_tap", "button_hold"}:
            self._execute_button(prim, op)
        elif op == "dpad":
            self._execute_dpad(prim)
        elif op == "stick":
            self._execute_stick(prim)
        elif op == "trigger":
            self._execute_trigger(prim)
        else:
            raise ValueError(f"unsupported gamepad op: {op!r}")

    def reset(self) -> None:
        self._pad.reset()
        self._release_dpad()
        self._pad.update()

    def close(self) -> None:
        self.reset()
        close_fn = getattr(self._pad, "close", None)
        if callable(close_fn):
            close_fn()

    def __enter__(self) -> Gamepad:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _execute_button(self, prim: GamepadPrimitive, op: str) -> None:
        if prim.button is None:
            raise ValueError(f"gamepad {op} requires button")
        xbtn = _button_flag(self._vg, prim.button)

        if op == "button_down":
            self._pad.press_button(xbtn)
            self._pad.update()
            return

        if op == "button_up":
            self._pad.release_button(xbtn)
            self._pad.update()
            return

        if op == "button_tap":
            self._pad.press_button(xbtn)
            self._pad.update()
            time.sleep(TAP_MS / 1000)
            self._pad.release_button(xbtn)
            self._pad.update()
            return

        # button_hold
        duration_ms = prim.duration_ms if prim.duration_ms is not None else TAP_MS
        self._pad.press_button(xbtn)
        self._pad.update()
        time.sleep(duration_ms / 1000)
        self._pad.release_button(xbtn)
        self._pad.update()

    def _execute_dpad(self, prim: GamepadPrimitive) -> None:
        if prim.dpad is None:
            raise ValueError("gamepad dpad requires dpad direction")

        direction = prim.dpad
        if direction != "neutral":
            self._pad.press_button(_dpad_flag(self._vg, direction))
            self._pad.update()

        duration_ms = prim.duration_ms if prim.duration_ms is not None else DEFAULT_DPAD_MS
        if direction != "neutral":
            time.sleep(duration_ms / 1000)

        self._release_dpad()
        self._pad.update()

    def _execute_stick(self, prim: GamepadPrimitive) -> None:
        if prim.stick is None or prim.x is None or prim.y is None:
            raise ValueError("gamepad stick requires stick, x, and y")

        x = float(prim.x)
        y = float(prim.y)
        if prim.stick == "left":
            self._pad.left_joystick_float(x_value_float=x, y_value_float=y)
        else:
            self._pad.right_joystick_float(x_value_float=x, y_value_float=y)
        self._pad.update()

    def _execute_trigger(self, prim: GamepadPrimitive) -> None:
        if prim.trigger is None or prim.value is None:
            raise ValueError("gamepad trigger requires trigger side and value")

        value = float(prim.value)
        if prim.trigger == "left":
            self._pad.left_trigger_float(value)
        else:
            self._pad.right_trigger_float(value)
        self._pad.update()

    def _release_dpad(self) -> None:
        for direction in _DPAD_BUTTONS:
            self._pad.release_button(_dpad_flag(self._vg, direction))


def _button_flag(vg: object, button: GamepadButton) -> object:
    mapping: dict[GamepadButton, str] = {
        "a": "XUSB_GAMEPAD_A",
        "b": "XUSB_GAMEPAD_B",
        "x": "XUSB_GAMEPAD_X",
        "y": "XUSB_GAMEPAD_Y",
        "lb": "XUSB_GAMEPAD_LEFT_SHOULDER",
        "rb": "XUSB_GAMEPAD_RIGHT_SHOULDER",
        "back": "XUSB_GAMEPAD_BACK",
        "start": "XUSB_GAMEPAD_START",
        "ls": "XUSB_GAMEPAD_LEFT_THUMB",
        "rs": "XUSB_GAMEPAD_RIGHT_THUMB",
    }
    name = mapping[button]
    return getattr(vg.XUSB_BUTTON, name)


def _dpad_flag(vg: object, direction: DPadDir) -> object:
    mapping: dict[str, str] = {
        "up": "XUSB_GAMEPAD_DPAD_UP",
        "down": "XUSB_GAMEPAD_DPAD_DOWN",
        "left": "XUSB_GAMEPAD_DPAD_LEFT",
        "right": "XUSB_GAMEPAD_DPAD_RIGHT",
    }
    if direction not in mapping:
        raise ValueError(f"unsupported dpad direction: {direction!r}")
    return getattr(vg.XUSB_BUTTON, mapping[direction])
