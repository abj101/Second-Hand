"""Harness-owned overlay leave: labeled dismiss, then scrim, then fixture key.

The model never chooses a dismiss coordinate. CollectAgent runs this ladder
when a blocking overlay (or dimmed combat board) has already eaten two dead
labeled clicks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

MAD_NOOP = 0.015
_LUMA_DELTA = 18.0
_SCRIM_CANDIDATES = (
    (0.06, 0.12),
    (0.94, 0.12),
    (0.06, 0.50),
    (0.94, 0.50),
    (0.06, 0.88),
    (0.94, 0.88),
)
_DISMISS_LABELS = ("back", "close", "got it")
_SKIP_LABELS = frozenset({"next", "yes", "confirm", "continue"})
_NAV_PREV = frozenset({"nav_prev", "prev", "previous"})
_BBOX_PAD = 0.02


def _compact(value: Any) -> str:
    return " ".join(str(value or "").casefold().replace("_", " ").replace("-", " ").split())


@dataclass
class OverlayEscapeState:
    armed: bool = False
    exhausted: bool = False
    rung: int = 0
    last_rung: int = 0
    scrim_tried: bool = False
    last_mad: float = 0.0
    last_trigger: str = ""

    def reset(self) -> None:
        self.armed = False
        self.exhausted = False
        self.rung = 0
        self.last_rung = 0
        self.scrim_tried = False
        self.last_mad = 0.0
        self.last_trigger = ""

    def arm(self, *, trigger: str = "") -> None:
        if self.exhausted or self.armed:
            return
        self.armed = True
        self.rung = 1
        self.last_trigger = trigger

    def note_success(self, mad: float) -> None:
        self.last_mad = float(mad)
        self.reset()

    def note_failure(self, mad: float, *, used_scrim: bool = False) -> None:
        self.last_mad = float(mad)
        if used_scrim:
            self.scrim_tried = True
        nxt = max(self.last_rung, self.rung, 1) + 1
        if nxt > 3:
            self.armed = False
            self.exhausted = True
            self.rung = 4
            return
        self.rung = nxt

    def metrics(self) -> dict[str, Any]:
        return {
            "overlay_escape_armed": bool(self.armed),
            "overlay_escape_rung": int(self.last_rung or self.rung or 0) or None,
            "overlay_escape_trigger": self.last_trigger or None,
            "overlay_escape_scrim_tried": bool(self.scrim_tried),
        }


@dataclass(frozen=True)
class OverlayEscapeAction:
    rung: int
    kind: str
    control_id: str = ""
    label: str = ""
    x: float | None = None
    y: float | None = None
    key: str | None = None


def overlay_looks_dimmed(image: Any) -> bool:
    """True when a bright panel sits on a darker surround (scrim + overlay).

    # ponytail: corner-vs-center luma; per-fixture mask if night maps false-positive
    """
    if image is None:
        return False
    try:
        gray = image.convert("L")
    except Exception:
        return False
    w, h = gray.size
    if w < 16 or h < 16:
        return False

    def _mean(x0: float, y0: float, x1: float, y1: float) -> float:
        crop = gray.crop((int(x0 * w), int(y0 * h), max(int(x0 * w) + 1, int(x1 * w)), max(int(y0 * h) + 1, int(y1 * h))))
        hist = crop.histogram()
        total = sum(hist) or 1
        return sum(i * n for i, n in enumerate(hist)) / total

    corners = (
        _mean(0.0, 0.0, 0.18, 0.18),
        _mean(0.82, 0.0, 1.0, 0.18),
        _mean(0.0, 0.82, 0.18, 1.0),
        _mean(0.82, 0.82, 1.0, 1.0),
    )
    center = _mean(0.30, 0.28, 0.70, 0.72)
    return center - (sum(corners) / 4.0) >= _LUMA_DELTA


def _row_map(row: Any) -> dict[str, Any]:
    if isinstance(row, Mapping):
        return dict(row)
    return {
        "control_id": getattr(row, "control_id", None) or getattr(row, "object_id", "") or "",
        "object_id": getattr(row, "object_id", "") or "",
        "label": getattr(row, "label", "") or "",
        "role": getattr(row, "role", "") or "",
    }


def pick_dismiss_control(
    controls: Sequence[Any],
    *,
    skip_ids: Sequence[str] | None = None,
) -> dict[str, Any] | None:
    """Prefer OCR Back/Close/Got it, then nav_prev. Never Next/Yes."""
    blocked = {str(item).strip() for item in (skip_ids or ()) if str(item).strip()}
    rows: list[tuple[str, str, str, dict[str, Any]]] = []
    for raw in controls:
        item = _row_map(raw)
        cid = str(item.get("control_id") or item.get("object_id") or "").strip()
        if not cid or cid in blocked:
            continue
        compact = _compact(item.get("label"))
        role = _compact(item.get("role")).replace(" ", "_")
        if compact in _SKIP_LABELS or role == "nav_next":
            continue
        rows.append((cid, compact, role, item))
    for needle in _DISMISS_LABELS:
        for cid, compact, role, item in rows:
            if compact == needle:
                return {"control_id": cid, "label": str(item.get("label") or needle), "role": role}
    for cid, compact, role, item in rows:
        if role == "nav_prev" or compact in _NAV_PREV:
            return {"control_id": cid, "label": str(item.get("label") or "back"), "role": role or "nav_prev"}
    return None


def _inside(x: float, y: float, bbox: Sequence[float], *, pad: float = _BBOX_PAD) -> bool:
    if len(bbox) < 4:
        return False
    x0, y0, x1, y1 = (float(bbox[0]) - pad, float(bbox[1]) - pad, float(bbox[2]) + pad, float(bbox[3]) + pad)
    return x0 <= x <= x1 and y0 <= y <= y1


def _luma_at(gray: Any, x: float, y: float) -> float:
    w, h = gray.size
    px = gray.getpixel((int(max(0, min(w - 1, x * w))), int(max(0, min(h - 1, y * h)))))
    return float(px)


def pick_scrim_point(
    image: Any,
    bboxes: Sequence[Sequence[float]],
    *,
    tried: bool = False,
) -> tuple[float, float] | None:
    """One dim point outside every interactive bbox. Not a letterbox park."""
    if tried or image is None:
        return None
    try:
        gray = image.convert("L")
    except Exception:
        return None
    center = _luma_at(gray, 0.50, 0.50)
    best: tuple[float, tuple[float, float]] | None = None
    for x, y in _SCRIM_CANDIDATES:
        if any(_inside(x, y, box) for box in bboxes):
            continue
        luma = _luma_at(gray, x, y)
        if luma >= center - 4.0:
            continue
        score = center - luma
        if best is None or score > best[0]:
            best = (score, (float(x), float(y)))
    return None if best is None else best[1]


def plan_overlay_escape(
    state: OverlayEscapeState,
    *,
    controls: Sequence[Any] = (),
    image: Any | None = None,
    interactive_bboxes: Sequence[Sequence[float]] = (),
    dismiss_key: str | None = None,
    skip_ids: Sequence[str] | None = None,
) -> OverlayEscapeAction | None:
    """Return the next real input, skipping empty rungs. None if the ladder is done."""
    start = max(1, int(state.rung or 1))
    for rung in range(start, 4):
        if rung == 1:
            hit = pick_dismiss_control(controls, skip_ids=skip_ids)
            if hit is not None:
                return OverlayEscapeAction(
                    rung=1,
                    kind="click_control",
                    control_id=str(hit["control_id"]),
                    label=str(hit.get("label") or ""),
                )
            continue
        if rung == 2:
            if state.scrim_tried:
                continue
            point = pick_scrim_point(image, interactive_bboxes, tried=state.scrim_tried)
            if point is not None:
                return OverlayEscapeAction(rung=2, kind="scrim", x=point[0], y=point[1])
            continue
        key = str(dismiss_key or "").strip()
        if key:
            return OverlayEscapeAction(rung=3, kind="key", key=key)
    return None
