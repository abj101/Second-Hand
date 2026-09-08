from __future__ import annotations

import time

from .primitives import KeyPrimitive
from .wininput import key_event, send_input

TAP_MS = 30

_LETTER_SCANS: dict[str, int] = {
    "A": 0x1E,
    "B": 0x30,
    "C": 0x2E,
    "D": 0x20,
    "E": 0x12,
    "F": 0x21,
    "G": 0x22,
    "H": 0x23,
    "I": 0x17,
    "J": 0x24,
    "K": 0x25,
    "L": 0x26,
    "M": 0x32,
    "N": 0x31,
    "O": 0x18,
    "P": 0x19,
    "Q": 0x10,
    "R": 0x13,
    "S": 0x1F,
    "T": 0x14,
    "U": 0x16,
    "V": 0x2F,
    "W": 0x11,
    "X": 0x2D,
    "Y": 0x15,
    "Z": 0x2C,
}

SCANCODES: dict[str, tuple[int, bool]] = {
    "ArrowUp": (0x48, True),
    "ArrowDown": (0x50, True),
    "ArrowLeft": (0x4B, True),
    "ArrowRight": (0x4D, True),
    "Up": (0x48, True),
    "Down": (0x50, True),
    "Left": (0x4B, True),
    "Right": (0x4D, True),
    "Space": (0x39, False),
    "Enter": (0x1C, False),
    "Return": (0x1C, False),
    "Escape": (0x01, False),
    "Tab": (0x0F, False),
    "Backspace": (0x0E, False),
    "shift": (0x2A, False),
    "Shift": (0x2A, False),
    **{f"Key{letter}": (scan, False) for letter, scan in _LETTER_SCANS.items()},
}


def scan_for(code: str) -> tuple[int, bool]:
    try:
        return SCANCODES[code]
    except KeyError as exc:
        raise KeyError(f"Unknown key code: {code!r}") from exc


class Keyboard:
    """Hardware-style keyboard input via SendInput scancodes."""

    def __init__(self) -> None:
        self._held: set[tuple[int, bool]] = set()

    def execute(self, prim: KeyPrimitive) -> None:
        modifiers = list(prim.chord)
        for mod in modifiers:
            self._down_key(mod)
        try:
            if prim.op == "down":
                self._down_key(prim.code)
            elif prim.op == "up":
                self._up_key(prim.code)
            elif prim.op == "tap":
                self._down_key(prim.code)
                time.sleep(TAP_MS / 1000)
                self._up_key(prim.code)
            elif prim.op == "hold":
                duration_ms = 80 if prim.duration_ms is None else prim.duration_ms
                self._down_key(prim.code)
                time.sleep(duration_ms / 1000)
                self._up_key(prim.code)
            else:
                raise ValueError(f"Unknown key op: {prim.op!r}")
        finally:
            for mod in reversed(modifiers):
                self._up_key(mod)

    def release_all(self) -> None:
        for scan, extended in list(self._held):
            send_input(key_event(scan, extended=extended, up=True))
        self._held.clear()

    def _down_key(self, code: str) -> None:
        scan, extended = scan_for(code)
        self._down(scan, extended)

    def _up_key(self, code: str) -> None:
        scan, extended = scan_for(code)
        self._up(scan, extended)

    def _down(self, scan: int, extended: bool) -> None:
        key = (scan, extended)
        if key in self._held:
            return
        send_input(key_event(scan, extended=extended, up=False))
        self._held.add(key)

    def _up(self, scan: int, extended: bool) -> None:
        key = (scan, extended)
        if key not in self._held:
            return
        send_input(key_event(scan, extended=extended, up=True))
        self._held.discard(key)
