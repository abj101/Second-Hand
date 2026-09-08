from __future__ import annotations

import ctypes
import time
from typing import TYPE_CHECKING, Any

from .primitives import DPadDir, GamepadButton, GamepadPrimitive

if TYPE_CHECKING:
    from vgamepad import VX360Gamepad

TAP_MS = 40
DEFAULT_DPAD_MS = 80
_ERROR_DEVICE_NOT_CONNECTED = 1167

_UNAVAILABLE_MSG = (
    "ViGEmBus is not installed (or vgamepad failed). "
    "Install Nefarius ViGEmBus, then pip install vgamepad. "
    "Keyboard/mouse still work."
)

_DPAD_BUTTONS: tuple[str, ...] = ("up", "down", "left", "right")

_gamepad_singleton: Gamepad | None = None
_gamepad_singleton_checked = False


class _XINPUT_GAMEPAD(ctypes.Structure):
    _fields_ = [
        ("wButtons", ctypes.c_ushort),
        ("bLeftTrigger", ctypes.c_ubyte),
        ("bRightTrigger", ctypes.c_ubyte),
        ("sThumbLX", ctypes.c_short),
        ("sThumbLY", ctypes.c_short),
        ("sThumbRX", ctypes.c_short),
        ("sThumbRY", ctypes.c_short),
    ]


class _XINPUT_STATE(ctypes.Structure):
    _fields_ = [("dwPacketNumber", ctypes.c_uint), ("Gamepad", _XINPUT_GAMEPAD)]


class GamepadUnavailable(RuntimeError):
    """Raised when ViGEmBus or vgamepad is missing or cannot open a virtual pad."""


def gamepad_available() -> bool:
    """True when the process can own the single virtual pad. Does not probe-create a second one."""
    return get_gamepad() is not None


def get_gamepad() -> Gamepad | None:
    """Process-level singleton so Steam/Elden see one XInput device, not an idle leftover."""
    global _gamepad_singleton, _gamepad_singleton_checked
    if _gamepad_singleton is not None:
        return _gamepad_singleton
    if _gamepad_singleton_checked:
        return None
    _gamepad_singleton_checked = True
    try:
        _gamepad_singleton = Gamepad()
    except GamepadUnavailable:
        _gamepad_singleton = None
    return _gamepad_singleton


def _xinput_dll() -> Any | None:
    for name in ("xinput1_4.dll", "xinput1_3.dll", "xinput9_1_0.dll"):
        try:
            return ctypes.WinDLL(name)
        except OSError:
            continue
    return None


def xinput_state(index: int) -> dict[str, int] | None:
    dll = _xinput_dll()
    if dll is None or not 0 <= index <= 3:
        return None
    state = _XINPUT_STATE()
    fn = dll.XInputGetState
    fn.argtypes = [ctypes.c_uint, ctypes.POINTER(_XINPUT_STATE)]
    fn.restype = ctypes.c_uint
    code = fn(index, ctypes.byref(state))
    if code == _ERROR_DEVICE_NOT_CONNECTED:
        return None
    if code != 0:
        return None
    pad = state.Gamepad
    return {
        "lx": int(pad.sThumbLX),
        "ly": int(pad.sThumbLY),
        "rx": int(pad.sThumbRX),
        "ry": int(pad.sThumbRY),
        "buttons": int(pad.wButtons),
    }


def xinput_connected() -> list[int]:
    return [i for i in range(4) if xinput_state(i) is not None]


def identify_our_slot(gamepad: Gamepad) -> int | None:
    """Deflect left stick and see which XInput user index moves."""
    gamepad._set_stick("left", 0.0, 1.0)
    time.sleep(0.08)
    hit: int | None = None
    for index in range(4):
        state = xinput_state(index)
        if state is not None and state["ly"] > 10000:
            hit = index
            break
    gamepad.reset()
    return hit


def pad_debug_line(gamepad: Gamepad | None) -> str:
    if gamepad is None:
        return "pad=none"
    slot = identify_our_slot(gamepad)
    connected = xinput_connected()
    bits = [f"pad xinput_slot={slot}", f"connected={connected}"]
    if slot is None:
        bits.append("WARNING: XInput did not see our stick wiggle")
    elif slot != 0:
        bits.append("WARNING: we are not slot 0; Elden/Steam may be reading an idle pad")
    elif len(connected) > 1:
        bits.append("WARNING: extra pads connected — unplug them so Elden uses ours")
    return "  ".join(bits)


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
            self._pad.reset()
            self._pad.update()
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

    def play_chord(
        self,
        *,
        stick_l: tuple[float, float] | None = None,
        stick_r: tuple[float, float] | None = None,
        held_buttons: tuple[str, ...] = (),
        tapped_buttons: tuple[str, ...] = (),
        held_triggers: tuple[tuple[str, float], ...] = (),
        tapped_dpad: tuple[str, ...] = (),
        held_dpad: tuple[str, ...] = (),
        duration_ms: int = 400,
    ) -> None:
        """Hold sticks/buttons together, tap extras, then reset."""
        if stick_l is not None:
            self._set_stick("left", stick_l[0], stick_l[1])
        if stick_r is not None:
            self._set_stick("right", stick_r[0], stick_r[1])
        for button in held_buttons:
            self._pad.press_button(_button_flag(self._vg, button))  # type: ignore[arg-type]
        for side, value in held_triggers:
            self._set_trigger(side, value)
        for direction in held_dpad:
            self._pad.press_button(_dpad_flag(self._vg, direction))
        self._pad.update()
        for button in tapped_buttons:
            xbtn = _button_flag(self._vg, button)  # type: ignore[arg-type]
            self._pad.press_button(xbtn)
            self._pad.update()
            time.sleep(TAP_MS / 1000)
            self._pad.release_button(xbtn)
            self._pad.update()
        for direction in tapped_dpad:
            flag = _dpad_flag(self._vg, direction)
            self._pad.press_button(flag)
            self._pad.update()
            time.sleep(TAP_MS / 1000)
            self._pad.release_button(flag)
            self._pad.update()
        deadline = time.perf_counter() + max(0, duration_ms) / 1000.0
        while time.perf_counter() < deadline:
            self._pad.update()
            time.sleep(0.016)
        self.reset()

    def reset(self) -> None:
        self._pad.reset()
        self._release_dpad()
        self._pad.update()

    def close(self) -> None:
        global _gamepad_singleton, _gamepad_singleton_checked
        self.reset()
        close_fn = getattr(self._pad, "close", None)
        if callable(close_fn):
            close_fn()
        if _gamepad_singleton is self:
            _gamepad_singleton = None
            _gamepad_singleton_checked = False

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
        self._set_stick(prim.stick, x, y)
        if prim.duration_ms:
            time.sleep(prim.duration_ms / 1000)
            self._set_stick(prim.stick, 0.0, 0.0)

    def _set_stick(self, stick: str, x: float, y: float) -> None:
        if stick == "left":
            self._pad.left_joystick_float(x_value_float=x, y_value_float=y)
        else:
            self._pad.right_joystick_float(x_value_float=x, y_value_float=y)
        self._pad.update()

    def _execute_trigger(self, prim: GamepadPrimitive) -> None:
        if prim.trigger is None or prim.value is None:
            raise ValueError("gamepad trigger requires trigger side and value")

        value = float(prim.value)
        self._set_trigger(prim.trigger, value)
        if prim.duration_ms:
            time.sleep(prim.duration_ms / 1000)
            self._set_trigger(prim.trigger, 0.0)

    def _set_trigger(self, trigger: str, value: float) -> None:
        if trigger == "left":
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
