from __future__ import annotations

import ctypes
import os
import threading
import time

class PanicFocus(RuntimeError):
    """Foreground window is Cursor (or another panic title); input was released."""


def should_panic(foreground_title: str, panic_titles: tuple[str, ...] = ("Cursor",)) -> bool:
    title = (foreground_title or "").lower()
    return any(needle.lower() in title for needle in panic_titles)


VK_F12 = 0x7B
_user32 = ctypes.windll.user32


def f12_down() -> bool:
    return bool(_user32.GetAsyncKeyState(VK_F12) & 0x8000)


class PanicHotkey:
    """Poll F12; on press, release_all then hard-exit."""

    def __init__(self, on_panic) -> None:
        self._on_panic = on_panic
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="panic-f12", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(0.05):
            if f12_down():
                try:
                    self._on_panic()
                finally:
                    os._exit(130)
