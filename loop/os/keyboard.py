"""Allowlisted scancode keyboard input via Win32 SendInput."""

from __future__ import annotations

import threading
import time
from typing import Callable

from loop.os.wininput import send_input as _default_send_input
from loop.os.wininput import key_event

# USB HID set-1 scan codes (trusted first-slice allowlist).
_KEY_SCAN: dict[str, tuple[int, bool]] = {
    "Space": (0x39, False),
    "Enter": (0x1C, False),
    "Escape": (0x01, False),
    "Tab": (0x0F, False),
    "Backspace": (0x0E, False),
    "KeyA": (0x1E, False),
    "KeyB": (0x30, False),
    "KeyC": (0x2E, False),
    "KeyD": (0x20, False),
    "KeyE": (0x12, False),
    "KeyF": (0x21, False),
    "KeyG": (0x22, False),
    "KeyH": (0x23, False),
    "KeyI": (0x17, False),
    "KeyJ": (0x24, False),
    "KeyK": (0x25, False),
    "KeyL": (0x26, False),
    "KeyM": (0x32, False),
    "KeyN": (0x31, False),
    "KeyO": (0x18, False),
    "KeyP": (0x19, False),
    "KeyQ": (0x10, False),
    "KeyR": (0x13, False),
    "KeyS": (0x1F, False),
    "KeyT": (0x14, False),
    "KeyU": (0x16, False),
    "KeyV": (0x2F, False),
    "KeyW": (0x11, False),
    "KeyX": (0x2D, False),
    "KeyY": (0x15, False),
    "KeyZ": (0x2C, False),
    "Digit0": (0x0B, False),
    "Digit1": (0x02, False),
    "Digit2": (0x03, False),
    "Digit3": (0x04, False),
    "Digit4": (0x05, False),
    "Digit5": (0x06, False),
    "Digit6": (0x07, False),
    "Digit7": (0x08, False),
    "Digit8": (0x09, False),
    "Digit9": (0x0A, False),
    "ArrowUp": (0x48, True),
    "ArrowDown": (0x50, True),
    "ArrowLeft": (0x4B, True),
    "ArrowRight": (0x4D, True),
}

ARMED_KEY_ALLOWLIST: frozenset[str] = frozenset(_KEY_SCAN)


def is_allowlisted_key(code: str) -> bool:
    return code in _KEY_SCAN


def compile_key(code: str) -> tuple[int, bool]:
    if code not in _KEY_SCAN:
        raise ValueError(f"key not allowlisted: {code!r}")
    return _KEY_SCAN[code]


SendInputFn = Callable[..., None]


class Keyboard:
    """Win32 keyboard with held-key tracking and duplicate-down suppression."""

    def __init__(self, *, send_input_fn: SendInputFn | None = None) -> None:
        self._send = send_input_fn or _default_send_input
        self._held: set[str] = set()
        self._timers: list[threading.Timer] = []
        self._lock = threading.Lock()

    def tap(self, code: str, *, duration_ms: int) -> None:
        scan, extended = compile_key(code)
        with self._lock:
            if code in self._held:
                return
            self._down(scan, extended)
            self._held.add(code)
        time.sleep(max(0.0, duration_ms / 1000.0))
        with self._lock:
            self._up(scan, extended)
            self._held.discard(code)

    def hold(self, code: str, *, duration_ms: int) -> None:
        scan, extended = compile_key(code)
        with self._lock:
            if code in self._held:
                return
            self._down(scan, extended)
            self._held.add(code)

        def _release() -> None:
            with self._lock:
                self._up(scan, extended)
                self._held.discard(code)

        timer = threading.Timer(max(0.0, duration_ms / 1000.0), _release)
        timer.daemon = True
        with self._lock:
            self._timers.append(timer)
        timer.start()

    def release_all(self) -> None:
        with self._lock:
            for timer in self._timers:
                timer.cancel()
            self._timers.clear()
            held = list(self._held)
        for code in held:
            scan, extended = compile_key(code)
            with self._lock:
                self._up(scan, extended)
                self._held.discard(code)

    def safe_startup_release(self, allowlist: frozenset[str] = ARMED_KEY_ALLOWLIST) -> None:
        for code in sorted(allowlist):
            scan, extended = compile_key(code)
            self._send(key_event(scan, extended=extended, up=True))
        with self._lock:
            self._held.clear()

    def _down(self, scan: int, extended: bool) -> None:
        self._send(key_event(scan, extended=extended, up=False))

    def _up(self, scan: int, extended: bool) -> None:
        self._send(key_event(scan, extended=extended, up=True))
