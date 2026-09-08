from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from PIL import ImageDraw, ImageFont

from avatar.gsm.rule.detector import DetectedBox
from loop.os.saliency import prompt_glyph, prompt_inset, prompt_visible, walk_dir_for_point

STS2_VISUAL_LABELS = [
    "Got it",
    "Next",
    "Confirm",
    "Back",
    "End Turn",
    "Strike",
    "Defend",
    "Cards",
    "Map",
    "Continue",
    "Resume",
    "Singleplayer",
]

# Short visual phrases. person/fog wall were noisy on chapel frames.
ELDEN_VISUAL_LABELS = [
    "doorway",
    "stone arch",
    "golden trail",
    "glowing item",
]

# Inset-only. Grounding DINO caption is period-separated.
PROMPT_LABELS = [
    "interact prompt",
    "button prompt",
    "xbox button",
    "A button",
    "Y button",
    "Open",
    "Talk",
    "Need item",
    "white HUD text",
]

DEFAULT_DINO_MODEL = "IDEA-Research/grounding-dino-base"
PROMPT_BOX_THRESHOLD = 0.18
PROMPT_TEXT_THRESHOLD = 0.15

MIN_SCORE = 0.40
MIN_TRAIL_SCORE = 0.35
MIN_PROMPT_SCORE = 0.40
MAX_BOXES = 3
MAX_PROMPT_BOXES = 1
MAX_AREA = 0.40
MAX_AREA_HARD = 0.85
MAX_PROMPT_AREA = 0.55
AT_TARGET_CX = 0.22
AT_TARGET_CY = (0.15, 0.80)
NEAR_AREA = 0.18
MID_AREA = 0.06
NEAR_Y = 0.62
MID_Y = 0.48
WALK_MS = {"near": 400, "mid": 900, "far": 1600}
NMS_IOU = 0.45
_KIND_RANK = {"golden trail": 0, "doorway": 1, "stone arch": 2}
_ARRIVAL_RANK = {"golden trail": 0, "doorway": 1, "stone arch": 2}
_AT_TARGET_KINDS = {"doorway", "stone arch"}
_FACE_GLYPHS = ("a", "b", "x", "y")
_PROMPT_HINTS = ("prompt", "button", "xbox", "open", "talk", "need item", "hud text", "white text")

_COLORS = [
    (0, 255, 180),
    (255, 220, 0),
    (80, 180, 255),
    (255, 80, 180),
    (180, 255, 80),
]


@dataclass(frozen=True)
class InteractHint:
    """CV bar and/or DINO inset box. Glyph is a face when on, else None."""

    on: bool
    glyph: str | None = None
    source: str = ""
    n_raw: int = 0
    n_kept: int = 0


class UnavailableDetector:
    """Stand-in when torch/transformers/weights are missing. Empty boxes."""

    family = "unavailable"

    def __init__(self, last_error: str = "") -> None:
        self.last_error = last_error

    def detect(self, frame: Any, labels: list[str], **_kwargs: Any) -> list[Any]:
        _ = frame, labels
        return []


def visual_labels_for(fixture_id: str) -> list[str]:
    fid = (fixture_id or "").strip().lower()
    if fid == "elden":
        return list(ELDEN_VISUAL_LABELS)
    if fid == "sts2":
        return list(STS2_VISUAL_LABELS)
    return []


def load_detector(*, device: str = "cpu", model_id: str = "") -> Any:
    """Load Grounding DINO for the collect overlay. Torch is imported only here."""
    try:
        from avatar.gsm.rule.detector import OpenVocabDetector
    except Exception as exc:  # noqa: BLE001 — overlay must not abort collect
        return UnavailableDetector(f"{type(exc).__name__}: {exc}")
    try:
        return OpenVocabDetector(
            model_id=model_id or DEFAULT_DINO_MODEL, device=device or "cpu"
        )
    except Exception as exc:  # noqa: BLE001
        return UnavailableDetector(f"{type(exc).__name__}: {exc}")


def _center(box: Any) -> tuple[float, float]:
    cx = getattr(box, "cx", None)
    if isinstance(cx, (int, float)):
        x = float(cx)
    else:
        x = float(getattr(box, "x", 0.0)) + float(getattr(box, "w", 0.0)) / 2.0
    y = float(getattr(box, "y", 0.0)) + float(getattr(box, "h", 0.0)) / 2.0
    return x, y


def _canon(label: str) -> str | None:
    text = (label or "").strip().lower()
    if not text or "fog" in text or "person" in text or text == "ground":
        return None
    if "door" in text:
        return "doorway"
    if "arch" in text:
        return "stone arch"
    if "trail" in text or "golden" in text or "glow" in text or "loot" in text:
        return "golden trail"
    return None


def _iou(a: Any, b: Any) -> float:
    ax0, ay0 = float(a.x), float(a.y)
    ax1, ay1 = ax0 + float(a.w), ay0 + float(a.h)
    bx0, by0 = float(b.x), float(b.y)
    bx1, by1 = bx0 + float(b.w), by0 + float(b.h)
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    iw, ih = max(0.0, ix1 - ix0), max(0.0, iy1 - iy0)
    inter = iw * ih
    union = float(a.w) * float(a.h) + float(b.w) * float(b.h) - inter
    return inter / union if union > 1e-9 else 0.0


def _rewrite(box: Any, label: str) -> DetectedBox:
    return DetectedBox(
        label=label,
        x=float(getattr(box, "x", 0.0)),
        y=float(getattr(box, "y", 0.0)),
        w=float(getattr(box, "w", 0.0)),
        h=float(getattr(box, "h", 0.0)),
        score=float(getattr(box, "score", 0.0) or 0.0),
        tint=getattr(box, "tint", None),
    )


def _at_target(kind: str, box: Any, area: float) -> bool:
    """Centered doorway/arch filling the frame is arrival, not clutter."""
    if kind not in _AT_TARGET_KINDS or area > MAX_AREA_HARD:
        return False
    cx, cy = _center(box)
    y0, y1 = AT_TARGET_CY
    return abs(cx - 0.5) <= AT_TARGET_CX and y0 <= cy <= y1


def _box_arrival(box: Any) -> bool:
    kind = _canon(str(getattr(box, "label", "") or "")) or ""
    area = float(getattr(box, "w", 0.0)) * float(getattr(box, "h", 0.0))
    return kind in _AT_TARGET_KINDS and area > MAX_AREA


def is_arrival(boxes: Sequence[Any]) -> bool:
    """True when any kept world box is a close (large) doorway/arch."""
    return any(_box_arrival(box) for box in boxes)


def box_range(box: Any) -> str:
    """near / mid / far from 2D box size and height. Not true 3D."""
    area = float(getattr(box, "w", 0.0)) * float(getattr(box, "h", 0.0))
    cx, cy = _center(box)
    kind = _canon(str(getattr(box, "label", "") or "")) or ""
    if kind == "golden trail":
        if area >= 0.10 or cy >= NEAR_Y:
            return "near"
        if area >= 0.035 or cy >= MID_Y:
            return "mid"
        return "far"
    if area > MAX_AREA or (area >= NEAR_AREA and abs(cx - 0.5) <= AT_TARGET_CX):
        return "near"
    if area >= MID_AREA:
        return "mid"
    return "far"


def boxes_are_near(boxes: Sequence[Any]) -> bool:
    """True when any kept box is already in interact / last-step range."""
    if is_arrival(boxes):
        return True
    return any(box_range(box) == "near" for box in boxes)


def walk_ms_for_range(band: str) -> int:
    return WALK_MS.get(band, WALK_MS["mid"])


def _is_prompt_label(label: str) -> bool:
    text = (label or "").strip().lower()
    if not text:
        return False
    if any(hint in text for hint in _PROMPT_HINTS):
        return True
    return any(f"{face} button" in text or text == face for face in _FACE_GLYPHS)


def _glyph_from_label(label: str) -> str | None:
    text = (label or "").strip().lower()
    if not text:
        return None
    for face in ("y", "x", "b", "a"):
        if f"{face} button" in text:
            return face
        if f"button {face}" in text:
            return face
    if "green" in text:
        return "a"
    if "red" in text and "green" not in text:
        return "b"
    if "blue" in text:
        return "x"
    if "yellow" in text:
        return "y"
    return None


def filter_prompt_boxes(boxes: Sequence[Any]) -> list[DetectedBox]:
    """Keep inset HUD prompt boxes. Drop huge / low-score clutter."""
    kept: list[DetectedBox] = []
    for box in boxes:
        label = str(getattr(box, "label", "") or "").strip()
        if not _is_prompt_label(label):
            continue
        score = float(getattr(box, "score", 0.0) or 0.0)
        if score < MIN_PROMPT_SCORE:
            continue
        area = float(getattr(box, "w", 0.0)) * float(getattr(box, "h", 0.0))
        if area <= 0.0 or area > MAX_PROMPT_AREA:
            continue
        kept.append(_rewrite(box, label))
    kept.sort(key=lambda b: -b.score)
    return kept[:MAX_PROMPT_BOXES]


def _detector_detect(
    detector: Any,
    frame: Any,
    labels: list[str],
    *,
    box_threshold: float | None = None,
    text_threshold: float | None = None,
) -> list[Any]:
    try:
        return list(
            detector.detect(
                frame,
                labels,
                box_threshold=box_threshold,
                text_threshold=text_threshold,
            )
            or []
        )
    except TypeError:
        return list(detector.detect(frame, labels) or [])


def detect_interact(
    image: Any,
    *,
    detector: Any | None = None,
    cv_on: bool | None = None,
    cv_glyph: str | None = None,
) -> InteractHint:
    """CV prompt bar OR DINO on the zoomed PROMPT inset. No default face glyph."""
    if cv_on is None:
        cv_on = bool(image is not None) and prompt_visible(image)
    glyph = (cv_glyph or "").strip().lower() or None
    if glyph not in _FACE_GLYPHS:
        glyph = None
    if glyph is None and cv_on and image is not None:
        guessed = prompt_glyph(image)
        if guessed in _FACE_GLYPHS:
            glyph = guessed

    raw: list[Any] = []
    kept: list[DetectedBox] = []
    family = str(getattr(detector, "family", "") or "") if detector is not None else ""
    if detector is not None and family != "unavailable" and image is not None:
        try:
            inset = prompt_inset(image) if hasattr(image, "size") else image
            raw = _detector_detect(
                detector,
                inset,
                list(PROMPT_LABELS),
                box_threshold=PROMPT_BOX_THRESHOLD,
                text_threshold=PROMPT_TEXT_THRESHOLD,
            )
        except Exception:  # noqa: BLE001 — inset DINO is OR with CV, never abort
            raw = []
        kept = filter_prompt_boxes(raw)

    dino_on = bool(kept)
    on = bool(cv_on or dino_on)
    if glyph is None and kept:
        glyph = _glyph_from_label(kept[0].label)
    if cv_on and dino_on:
        source = "cv+dino"
    elif cv_on:
        source = "cv"
    elif dino_on:
        source = "dino"
    else:
        source = ""
        glyph = None
    if not on:
        glyph = None
    elif glyph not in _FACE_GLYPHS:
        glyph = None
    return InteractHint(
        on=on,
        glyph=glyph,
        source=source,
        n_raw=len(raw),
        n_kept=len(kept),
    )


def prompt_confirmed(hint: InteractHint, prompt_text: str = "") -> bool:
    """True when OCR has copy or DINO found a prompt box — not CV contrast alone."""
    if (prompt_text or "").strip():
        return True
    src = hint.source or ""
    return "dino" in src and int(hint.n_kept or 0) > 0


def interrupt_ready(
    hint: InteractHint, *, prompt_text: str = "", dino_streak: int = 0
) -> bool:
    """Skip the VLM for a CV bar with copy, or a labeled DINO face after two hits.

    Empty OCR on a CV contrast strip is not a bar — loot glows / robe hems
    must not auto-tap. A DINO face on the inset can still confirm.
    """
    if not hint.on or hint.glyph not in _FACE_GLYPHS:
        return False
    src = hint.source or ""
    if src == "dino":
        return dino_streak >= 2
    if "cv" in src:
        if (prompt_text or "").strip():
            return True
        return "dino" in src and int(hint.n_kept or 0) > 0
    return False


def lock_demote_labels(boxes: Sequence[Any]) -> set[str]:
    """Doorway/arch kinds present at a lock-yield. Hint rank only; never a motor."""
    out: set[str] = set()
    for box in boxes:
        kind = _canon(str(getattr(box, "label", "") or "")) or ""
        if kind in _AT_TARGET_KINDS:
            out.add(kind)
    return out


def filter_grounded(
    boxes: Sequence[Any],
    *,
    demote: Sequence[str] | None = None,
    drop: Sequence[str] | None = None,
) -> list[DetectedBox]:
    """Keep a few high-score goal boxes. Drop player/fog/clutter. `drop` is a hard exclude."""
    dropped = {str(label).strip().lower() for label in (drop or []) if label}
    kept: list[DetectedBox] = []
    for box in boxes:
        kind = _canon(str(getattr(box, "label", "") or ""))
        if kind is None:
            continue
        if kind in dropped:
            continue
        score = float(getattr(box, "score", 0.0) or 0.0)
        floor = MIN_TRAIL_SCORE if kind == "golden trail" else MIN_SCORE
        if score < floor:
            continue
        area = float(getattr(box, "w", 0.0)) * float(getattr(box, "h", 0.0))
        if area <= 0.0:
            continue
        if area > MAX_AREA:
            keep_large_trail = kind == "golden trail" and area <= MAX_AREA_HARD
            if not keep_large_trail and not _at_target(kind, box, area):
                continue
        kept.append(_rewrite(box, kind))
    kept.sort(key=lambda b: (_KIND_RANK.get(b.label, 9), -b.score))
    unique: list[DetectedBox] = []
    seen: set[str] = set()
    for box in kept:
        if box.label in seen:
            continue
        if any(_iou(box, prev) >= NMS_IOU for prev in unique):
            continue
        unique.append(box)
        seen.add(box.label)
    rank = _ARRIVAL_RANK if is_arrival(unique) else _KIND_RANK
    lowered = {str(label) for label in (demote or [])}
    unique.sort(
        key=lambda b: (
            1 if b.label in lowered else 0,
            rank.get(b.label, 9),
            -b.score,
        )
    )
    if len(unique) > MAX_BOXES:
        head = unique[:MAX_BOXES]
        if not any(b.label == "golden trail" for b in head):
            trail = next((b for b in unique if b.label == "golden trail"), None)
            if trail is not None:
                head[-1] = trail
        unique = head
    else:
        unique = unique[:MAX_BOXES]
    return unique


def format_grounded(
    boxes: Sequence[Any], *, locked: bool = False, demote: Sequence[str] | None = None
) -> str:
    if not boxes:
        return ""
    arrived = is_arrival(boxes)
    lowered = {str(label) for label in (demote or [])}
    parts: list[str] = []
    for box in boxes:
        cx, cy = _center(box)
        label = str(getattr(box, "label", "") or "object").strip() or "object"
        score = float(getattr(box, "score", 1.0) or 0.0)
        kind = _canon(label) or label
        blocked = locked or kind in lowered
        if kind in _AT_TARGET_KINDS and blocked:
            if arrived:
                parts.append(
                    f"At {kind} — condition (need item), not a tap. "
                    "Look (stick_r) or walk a different heading."
                )
            else:
                prefix = "Hint:" if not parts else "Also:"
                parts.append(
                    f"{prefix} {label} {cx:.2f},{cy:.2f} score={score:.2f} "
                    "(condition; look stick_r or walk away, not stick_l into it)"
                )
            continue
        if arrived and kind in _AT_TARGET_KINDS:
            parts.append(f"At {kind} — near, interact check. Stop walking.")
            continue
        walk = walk_dir_for_point(cx, cy)
        prefix = "Hint:" if not parts else "Also:"
        band = box_range(box)
        ms = walk_ms_for_range(band)
        if kind == "golden trail":
            parts.append(
                f"{prefix} loot glow (golden trail) {cx:.2f},{cy:.2f} {band} "
                f"score={score:.2f} — pickup on a corpse; "
                f"walk stick_l_{walk} ~{ms}ms to it. Not a door."
            )
            continue
        parts.append(
            f"{prefix} {label} {cx:.2f},{cy:.2f} {band} score={score:.2f} "
            f"(optional stick_l_{walk} ~{ms}ms or look stick_r)"
        )
    return " ".join(parts)


def format_loot_glows(
    points: Sequence[tuple[float, float]],
    *,
    leading: bool = True,
) -> str:
    """Chroma backup when DINO kept no golden-trail box."""
    if not points:
        return ""
    parts: list[str] = []
    for i, (cx, cy) in enumerate(points):
        walk = walk_dir_for_point(cx, cy)
        prefix = "Hint:" if leading and i == 0 else "Also:"
        band = "near" if cy >= NEAR_Y else "mid" if cy >= MID_Y else "far"
        ms = walk_ms_for_range(band)
        parts.append(
            f"{prefix} loot glow (chroma) {cx:.2f},{cy:.2f} {band} "
            f"— pickup on a corpse; walk stick_l_{walk} ~{ms}ms to it. Not a door."
        )
    return " ".join(parts)


def grounded_with_loot(
    image: Any,
    boxes: Sequence[Any],
    *,
    demote: Sequence[str] | None = None,
    locked: bool = False,
) -> str:
    text = format_grounded(boxes, locked=locked, demote=demote)
    if any((_canon(str(getattr(box, "label", "") or "")) == "golden trail") for box in boxes):
        return text
    if image is None or not hasattr(image, "convert"):
        return text
    from loop.os.saliency import gold_glows

    extra = format_loot_glows(gold_glows(image), leading=not bool(text))
    if not extra:
        return text
    return f"{text} {extra}".strip()


def boxes_payload(boxes: Sequence[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for box in boxes:
        out.append(
            {
                "label": str(getattr(box, "label", "") or "object"),
                "x": float(getattr(box, "x", 0.0)),
                "y": float(getattr(box, "y", 0.0)),
                "w": float(getattr(box, "w", 0.0)),
                "h": float(getattr(box, "h", 0.0)),
                "score": float(getattr(box, "score", 1.0) or 0.0),
            }
        )
    return out


def paint_boxes(image: Image.Image, boxes: Sequence[Any]) -> Image.Image:
    """Copy of *image* with labeled rectangles. Empty boxes → RGB copy, no marks."""
    out = image.convert("RGB").copy()
    if not boxes:
        return out
    draw = ImageDraw.Draw(out)
    width_px, height_px = out.size
    stroke = max(2, min(width_px, height_px) // 180)
    try:
        font = ImageFont.load_default()
    except Exception:  # noqa: BLE001
        font = None
    for i, box in enumerate(boxes):
        color = _COLORS[i % len(_COLORS)]
        x0 = int(round(float(getattr(box, "x", 0.0)) * width_px))
        y0 = int(round(float(getattr(box, "y", 0.0)) * height_px))
        x1 = int(round((float(getattr(box, "x", 0.0)) + float(getattr(box, "w", 0.1))) * width_px))
        y1 = int(round((float(getattr(box, "y", 0.0)) + float(getattr(box, "h", 0.1))) * height_px))
        x0, x1 = max(0, min(x0, x1)), min(width_px - 1, max(x0, x1))
        y0, y1 = max(0, min(y0, y1)), min(height_px - 1, max(y0, y1))
        draw.rectangle((x0, y0, x1, y1), outline=color, width=stroke)
        label = str(getattr(box, "label", "") or "object").strip() or "object"
        if len(label) > 28:
            label = label[:27] + "…"
        score = float(getattr(box, "score", 1.0) or 0.0)
        text = f"{label} {score:.2f}"
        ty = max(0, y0 - 14)
        if font is not None:
            bbox = draw.textbbox((x0, ty), text, font=font)
        else:
            bbox = (x0, ty, x0 + 7 * len(text), ty + 12)
        pad = 1
        draw.rectangle(
            (bbox[0] - pad, bbox[1] - pad, bbox[2] + pad, bbox[3] + pad),
            fill=(0, 0, 0),
        )
        draw.text((x0, ty), text, fill=color, font=font)
    return out
