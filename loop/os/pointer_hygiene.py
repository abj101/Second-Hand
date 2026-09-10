"""Harness-owned pointer hygiene: park off UI before observation.

The model never chooses a park action. CollectAgent parks after completed
clicks and recaptures once when a leftover hover overlay occludes the frame.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

IN_CLIENT_FALLBACK = (0.50, 0.92)
PARK_DWELL_S = 0.05
_LETTERBOX = 0.03
_OCCLUSION_TOKENS = (
    "tooltip",
    "vulnerable",
    "exhaust",
    "removed until the end",
    "more damage from",
    "creatures take",
)


@dataclass
class PointerHygieneState:
    parked: bool = True
    hover_intentional: bool = False
    selection_held: bool = False
    last_xy: tuple[float, float] | None = None
    last_action_xy: tuple[float, float] | None = None
    last_park_xy: tuple[float, float] | None = None
    last_trigger: str = ""
    last_probe_delta: float = 0.0
    recapture_used: int = 0

    def metrics(self) -> dict[str, Any]:
        return {
            "pointer_parked": bool(self.parked),
            "pointer_park_trigger": self.last_trigger or None,
            "pointer_park_probe_delta": round(float(self.last_probe_delta), 6),
            "pointer_park_recapture": int(self.recapture_used),
        }


def _box4(click_box: Sequence[float] | None) -> tuple[float, float, float, float]:
    if click_box is None or len(tuple(click_box)) != 4:
        return (0.0, 0.0, 1.0, 1.0)
    x0, y0, x1, y1 = (float(v) for v in click_box)
    return (x0, y0, x1, y1)


def neutral_park_xy(click_box: Sequence[float] | None) -> tuple[float, float]:
    """Prefer a letterbox margin outside the content rect; else a client-edge fallback."""
    x0, y0, x1, y1 = _box4(click_box)
    if x0 > _LETTERBOX:
        return (max(0.0, x0 * 0.5), 0.50)
    if x1 < 1.0 - _LETTERBOX:
        return (min(1.0, x1 + (1.0 - x1) * 0.5), 0.50)
    if y0 > _LETTERBOX:
        return (0.50, max(0.0, y0 * 0.5))
    if y1 < 1.0 - _LETTERBOX:
        return (0.50, min(1.0, y1 + (1.0 - y1) * 0.5))
    return IN_CLIENT_FALLBACK


def should_park_after_pointer(
    *,
    hover: bool,
    has_click: bool,
    mid_transaction: bool = False,
    drag: bool = False,
    combat_selection_held: bool = False,
    park_after: bool | None = None,
) -> bool:
    """True when a completed pointer gesture should leave the target."""
    if park_after is False:
        return False
    if hover or mid_transaction or drag:
        return False
    if park_after is True:
        return bool(has_click)
    if combat_selection_held:
        return False
    return bool(has_click)


def pointer_near(
    pointer: tuple[float, float] | None,
    target: tuple[float, float] | None,
    *,
    radius: float = 0.08,
) -> bool:
    if pointer is None or target is None:
        return False
    dx = float(pointer[0]) - float(target[0])
    dy = float(pointer[1]) - float(target[1])
    return (dx * dx + dy * dy) ** 0.5 <= radius


def _blob(scene: Any) -> str:
    parts: list[str] = []
    for raw in list(getattr(scene, "overlays_active", None) or []):
        parts.append(str(raw))
    for raw in list(getattr(scene, "visible_text", None) or []):
        parts.append(str(raw))
    ocr = getattr(scene, "ocr", None)
    if ocr:
        parts.append(str(ocr))
    return " ".join(parts).casefold()


def hover_occlusion_from_scene(
    scene: Any,
    *,
    pointer: tuple[float, float] | None = None,
    last_click: tuple[float, float] | None = None,
) -> bool:
    """Heuristic: leftover hover glossary/overlay near the last pointer action."""
    if scene is None:
        return False
    blob = _blob(scene)
    if not blob:
        return False
    if not any(token in blob for token in _OCCLUSION_TOKENS):
        return False
    if pointer is None and last_click is None:
        return True
    return pointer_near(pointer, last_click, radius=0.12) or last_click is not None
