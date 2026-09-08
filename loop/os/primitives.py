from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# OS-facing contract. Named table entries compile to these.
# The VLM never emits primitives in Phase 1 — it picks names.

KeyOp = Literal["down", "up", "tap", "hold"]
MouseOp = Literal["move", "click", "down", "up", "drag", "scroll"]
MouseButton = Literal["left", "right", "middle"]
GamepadOp = Literal[
    "button_down",
    "button_up",
    "button_tap",
    "button_hold",
    "dpad",
    "stick",
    "trigger",
]
GamepadButton = Literal["a", "b", "x", "y", "lb", "rb", "back", "start", "ls", "rs"]
DPadDir = Literal["up", "down", "left", "right", "neutral"]
StickSide = Literal["left", "right"]
TriggerSide = Literal["left", "right"]


@dataclass(frozen=True)
class KeyPrimitive:
    """Virtual-key style codes: ArrowLeft, KeyA, Space, Enter, Up, shift, …"""

    code: str
    op: KeyOp = "tap"
    duration_ms: int | None = None
    chord: tuple[str, ...] = ()


@dataclass(frozen=True)
class MousePrimitive:
    """Client-normalized coordinates in [0, 1]. Executor maps via ClientToScreen."""

    op: MouseOp
    x: float | None = None
    y: float | None = None
    x2: float | None = None
    y2: float | None = None
    button: MouseButton = "left"
    clicks: int = 1
    dy: int = 0
    duration_ms: int | None = None
    hover_s: float | None = None
    hold_s: float | None = None


@dataclass(frozen=True)
class GamepadPrimitive:
    """Virtual Xbox 360 pad. Sticks [-1, 1], triggers [0, 1]."""

    op: GamepadOp
    button: GamepadButton | None = None
    dpad: DPadDir | None = None
    stick: StickSide | None = None
    x: float | None = None
    y: float | None = None
    trigger: TriggerSide | None = None
    value: float | None = None
    duration_ms: int | None = None


Primitive = KeyPrimitive | MousePrimitive | GamepadPrimitive


@dataclass(frozen=True)
class NamedAction:
    name: str
    primitives: tuple[Primitive, ...]
    notes: str = ""


def key_tap(code: str, *, duration_ms: int | None = None, chord: tuple[str, ...] = ()) -> KeyPrimitive:
    op: KeyOp = "hold" if duration_ms else "tap"
    return KeyPrimitive(code=code, op=op, duration_ms=duration_ms, chord=chord)


def mouse_click(
    x: float,
    y: float,
    *,
    button: MouseButton = "left",
    hover_s: float | None = None,
    hold_s: float | None = None,
) -> MousePrimitive:
    return MousePrimitive(op="click", x=x, y=y, button=button, hover_s=hover_s, hold_s=hold_s)


def mouse_move(x: float, y: float) -> MousePrimitive:
    return MousePrimitive(op="move", x=x, y=y)


def pad_button(button: GamepadButton, *, duration_ms: int | None = None) -> GamepadPrimitive:
    op: GamepadOp = "button_hold" if duration_ms else "button_tap"
    return GamepadPrimitive(op=op, button=button, duration_ms=duration_ms)


def pad_dpad(direction: DPadDir, *, duration_ms: int | None = 80) -> GamepadPrimitive:
    return GamepadPrimitive(op="dpad", dpad=direction, duration_ms=duration_ms)


def pad_stick(
    stick: StickSide,
    x: float,
    y: float,
    *,
    duration_ms: int | None = 400,
) -> GamepadPrimitive:
    return GamepadPrimitive(op="stick", stick=stick, x=x, y=y, duration_ms=duration_ms)


def pad_trigger(
    trigger: TriggerSide,
    value: float = 1.0,
    *,
    duration_ms: int | None = 400,
) -> GamepadPrimitive:
    return GamepadPrimitive(op="trigger", trigger=trigger, value=value, duration_ms=duration_ms)
