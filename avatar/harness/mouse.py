from __future__ import annotations

import ctypes
import time

from .primitives import MouseButton, MousePrimitive
from .wininput import (
    MOUSEEVENTF_ABSOLUTE,
    MOUSEEVENTF_LEFTDOWN,
    MOUSEEVENTF_LEFTUP,
    MOUSEEVENTF_MIDDLEDOWN,
    MOUSEEVENTF_MIDDLEUP,
    MOUSEEVENTF_MOVE,
    MOUSEEVENTF_RIGHTDOWN,
    MOUSEEVENTF_RIGHTUP,
    MOUSEEVENTF_VIRTUALDESK,
    MOUSEEVENTF_WHEEL,
    mouse_event,
    send_input,
)

WHEEL_DELTA = 120
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

_BUTTON_DOWN: dict[MouseButton, int] = {
    "left": MOUSEEVENTF_LEFTDOWN,
    "right": MOUSEEVENTF_RIGHTDOWN,
    "middle": MOUSEEVENTF_MIDDLEDOWN,
}
_BUTTON_UP: dict[MouseButton, int] = {
    "left": MOUSEEVENTF_LEFTUP,
    "right": MOUSEEVENTF_RIGHTUP,
    "middle": MOUSEEVENTF_MIDDLEUP,
}


class Mouse:
    """Absolute mouse input via SendInput; executor supplies screen coordinates."""

    def __init__(self) -> None:
        self._user32 = ctypes.windll.user32
        self._buttons_down: set[MouseButton] = set()

    def execute(
        self,
        prim: MousePrimitive,
        *,
        screen_xy: tuple[int, int] | None = None,
        screen_xy2: tuple[int, int] | None = None,
    ) -> None:
        needs_xy = prim.op in {"move", "click", "down", "up", "drag"}
        if needs_xy and prim.x is not None and prim.y is not None and screen_xy is None:
            raise ValueError(
                f"MousePrimitive {prim.op!r} requires screen_xy; "
                "map prim.x/prim.y via window.client_to_screen"
            )
        if prim.op == "drag" and prim.x2 is not None and prim.y2 is not None and screen_xy2 is None:
            raise ValueError(
                "MousePrimitive drag requires screen_xy2; "
                "map prim.x2/prim.y2 via window.client_to_screen"
            )

        if prim.op == "move":
            assert screen_xy is not None
            self._move(*screen_xy)
        elif prim.op == "click":
            assert screen_xy is not None
            self._move(*screen_xy)
            time.sleep(0.02)
            for _ in range(max(1, prim.clicks)):
                self._button_down(prim.button)
                time.sleep(0.02)
                self._button_up(prim.button)
        elif prim.op == "down":
            assert screen_xy is not None
            self._move(*screen_xy)
            self._button_down(prim.button)
        elif prim.op == "up":
            assert screen_xy is not None
            self._move(*screen_xy)
            self._button_up(prim.button)
        elif prim.op == "drag":
            assert screen_xy is not None
            assert screen_xy2 is not None
            self._drag(screen_xy, screen_xy2, prim.button, prim.duration_ms)
        elif prim.op == "scroll":
            self._scroll(prim.dy)
        else:
            raise ValueError(f"Unknown mouse op: {prim.op!r}")

    def release_all(self) -> None:
        for button in list(self._buttons_down):
            send_input(mouse_event(dx=0, dy=0, mouse_data=0, flags=_BUTTON_UP[button]))
            self._buttons_down.discard(button)

    def _virt_screen(self) -> tuple[int, int, int, int]:
        left = self._user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
        top = self._user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
        width = self._user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
        height = self._user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
        return left, top, width, height

    def _normalize(self, screen_x: int, screen_y: int) -> tuple[int, int]:
        virt_left, virt_top, virt_width, virt_height = self._virt_screen()
        width_denom = max(virt_width - 1, 1)
        height_denom = max(virt_height - 1, 1)
        abs_x = int((screen_x - virt_left) * 65535 / width_denom)
        abs_y = int((screen_y - virt_top) * 65535 / height_denom)
        return abs_x, abs_y

    def _move(self, screen_x: int, screen_y: int) -> None:
        abs_x, abs_y = self._normalize(screen_x, screen_y)
        send_input(
            mouse_event(
                dx=abs_x,
                dy=abs_y,
                mouse_data=0,
                flags=MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK,
            )
        )

    def _button_down(self, button: MouseButton) -> None:
        if button in self._buttons_down:
            return
        send_input(mouse_event(dx=0, dy=0, mouse_data=0, flags=_BUTTON_DOWN[button]))
        self._buttons_down.add(button)

    def _button_up(self, button: MouseButton) -> None:
        if button not in self._buttons_down:
            return
        send_input(mouse_event(dx=0, dy=0, mouse_data=0, flags=_BUTTON_UP[button]))
        self._buttons_down.discard(button)

    def _scroll(self, dy: int) -> None:
        data = ctypes.c_int32(dy * WHEEL_DELTA).value & 0xFFFFFFFF
        send_input(mouse_event(dx=0, dy=0, mouse_data=data, flags=MOUSEEVENTF_WHEEL))

    def _drag(
        self,
        start: tuple[int, int],
        end: tuple[int, int],
        button: MouseButton,
        duration_ms: int | None,
    ) -> None:
        self._move(*start)
        self._button_down(button)
        try:
            total_ms = 100 if duration_ms is None else max(0, duration_ms)
            steps = max(3, min(20, total_ms // 10 or 3))
            step_delay = (total_ms / 1000) / steps if steps else 0.0
            sx, sy = start
            ex, ey = end
            for i in range(1, steps + 1):
                t = i / steps
                x = int(sx + (ex - sx) * t)
                y = int(sy + (ey - sy) * t)
                self._move(x, y)
                if step_delay:
                    time.sleep(step_delay)
            self._move(ex, ey)
        finally:
            self._button_up(button)
