from __future__ import annotations

from typing import Any

from loop.log.schema import ActionLog
from loop.os.chords import chord_name

_TARGET_HINTS = ("trail", "loot", "glow", "item", "doorway", "arch", "prompt")


def _box_label(box: Any) -> str:
    if isinstance(box, dict):
        return str(box.get("label") or "")
    return str(getattr(box, "label", "") or "")


def _box_cx(box: Any) -> float:
    if isinstance(box, dict):
        x = float(box.get("x") or 0.0)
        w = float(box.get("w") or 0.0)
    else:
        x = float(getattr(box, "x", 0.0) or 0.0)
        w = float(getattr(box, "w", 0.0) or 0.0)
    return x + w / 2.0


def approach_target_cx(boxes: list[Any] | None) -> float | None:
    """Center-x of the first loot/doorway/arch/item/prompt box, else first box."""
    if not boxes:
        return None
    for box in boxes:
        label = _box_label(box).lower()
        if any(hint in label for hint in _TARGET_HINTS):
            return _box_cx(box)
    return _box_cx(boxes[0])


class ApproachGsmRuntime:
    """Closed-loop centering motor. No VLM."""

    MAX_TICKS = 4
    CENTER_LO = 0.42
    CENTER_HI = 0.58

    def __init__(self) -> None:
        self.ticks = 0

    def reset(self) -> None:
        self.ticks = 0

    def next_action(
        self,
        *,
        box_cx: float | None,
        confirmed: bool,
        locked: bool,
        burst_ms: int = 400,
    ) -> tuple[ActionLog, bool]:
        if confirmed or locked or box_cx is None:
            action = ActionLog(duration_ms=burst_ms)
            action.name = "idle"
            return action, True
        self.ticks += 1
        done = self.ticks >= self.MAX_TICKS
        if box_cx < self.CENTER_LO:
            action = ActionLog(stick_r="w", duration_ms=burst_ms)
        elif box_cx > self.CENTER_HI:
            action = ActionLog(stick_r="e", duration_ms=burst_ms)
        else:
            action = ActionLog(stick_l="n", duration_ms=burst_ms)
        action.name = chord_name(action)
        return action, done
