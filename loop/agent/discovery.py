"""Bounded, evidence-first discovery for combat objects.

This module deliberately does not choose game actions.  It turns one safe,
buttonless hover into object-bound evidence that ACT can consume on a later,
fresh scene revision.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

import numpy as np
from PIL import Image, ImageEnhance, ImageOps

from loop.agent.entities import is_anonymous_enemy_label, is_hp_anchor_label, is_player_row
from loop.memory.screen_objects import ScreenObject
from loop.os.ocr import OcrWord, read_words

Box = tuple[float, float, float, float]
_UNKNOWN = frozenset({"", "unknown", "icon", ":icon", "unknown card", "unknown enemy"})
_FIELD_WORDS = frozenset({"attack", "skill", "power", "status", "curse", "hp", "block", "damage"})
_INTENT_WORDS = frozenset({
    "aggressive", "defensive", "attacking", "intends", "intend",
    "sleeping", "awake", "buff", "debuff",
})
_HP_FRACTION_RE = re.compile(r"^\d+\s*/\s*\d+$")
_UNSAFE_PATH = re.compile(r'[<>:"/\\|?*]')
_HUD_CHROME = frozenset({
    "deck", "cards", "settings", "options", "menu", "map", "share", "legend",
    "potion", "relic", "icon", ":icon", "back",
})
# STS2 HP/intent text sits ~0.35; live sprites sit ~0.58–0.71.
_SPRITE_Y_MIN = 0.50


class DiscoveryState(str, Enum):
    INVENTORY_UNKNOWN = "INVENTORY_UNKNOWN"
    CHOOSE_UNKNOWN = "CHOOSE_UNKNOWN"
    RELEASE_INPUT = "RELEASE_INPUT"
    CAPTURE_BEFORE = "CAPTURE_BEFORE"
    HOVER_OBJECT = "HOVER_OBJECT"
    WAIT_FOR_TOOLTIP = "WAIT_FOR_TOOLTIP"
    CAPTURE_AFTER = "CAPTURE_AFTER"
    LOCALIZE_CHANGE = "LOCALIZE_CHANGE"
    BUILD_CROPS = "BUILD_CROPS"
    OCR_CROPS = "OCR_CROPS"
    OPTIONAL_VLM_READ = "OPTIONAL_VLM_READ"
    VERIFY_AND_BIND = "VERIFY_AND_BIND"
    NEXT_UNKNOWN = "NEXT_UNKNOWN"
    DISCOVERY_COMPLETE = "DISCOVERY_COMPLETE"


@dataclass(frozen=True)
class InspectionContract:
    run_id: str
    step_id: int
    object_id: str
    mark_id: int
    role: str
    bbox: Box
    target: tuple[float, float]


@dataclass
class DiscoveryObject:
    object_id: str
    mark_id: int
    role: str
    bbox: Box
    target: tuple[float, float]
    label: str = ""
    confidence: float = 0.0
    identified: bool = False
    inspectable: bool = True
    evidence: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class DiscoveryResult:
    contract: InspectionContract
    state: DiscoveryState
    status: str
    name: str | None = None
    failure: str | None = None
    crops: dict[str, Box] = field(default_factory=dict)
    ocr: dict[str, list[OcrWord]] = field(default_factory=dict)
    evidence: list[dict[str, Any]] = field(default_factory=list)

    def payload(self) -> dict[str, Any]:
        return {
            "contract": asdict(self.contract),
            "state": self.state.value,
            "status": self.status,
            "name": self.name,
            "failure": self.failure,
            "crops": {key: list(value) for key, value in self.crops.items()},
            "ocr": {
                key: [asdict(word) for word in words] for key, words in self.ocr.items()
            },
            "evidence": self.evidence,
        }


def _compact(value: str) -> str:
    return " ".join(str(value or "").casefold().split())


def _box(obj: Any) -> Box:
    return (float(obj.x0), float(obj.y0), float(obj.x1), float(obj.y1))


def _iou(a: Box, b: Box) -> float:
    x0, y0, x1, y1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    overlap = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    if not overlap:
        return 0.0
    aa = max(1e-6, (a[2] - a[0]) * (a[3] - a[1]))
    ab = max(1e-6, (b[2] - b[0]) * (b[3] - b[1]))
    return overlap / (aa + ab - overlap)


def _pad(box: Box, amount: float) -> Box:
    return (
        max(0.0, box[0] - amount), max(0.0, box[1] - amount),
        min(1.0, box[2] + amount), min(1.0, box[3] + amount),
    )


def _is_unknown(label: str) -> bool:
    text = _compact(label).replace("_", " ")
    return text in _UNKNOWN or text.startswith("unknown ") or text.startswith("unidentified ")


def _is_hud_chrome(label: str) -> bool:
    text = _compact(label).replace("_", " ")
    return text in _HUD_CHROME or text.startswith("deck ")


def _player_dict(item: ScreenObject) -> dict[str, Any]:
    return {
        "label": str(item.label or ""),
        "role": str(item.role or ""),
        "x": item.x,
        "y": item.y,
        "x0": item.x0,
        "y0": item.y0,
        "x1": item.x1,
        "y1": item.y1,
    }


def inventory_unknowns(
    objects: Sequence[ScreenObject], *, energy: int | None = None
) -> list[DiscoveryObject]:
    """Build stable, decision-first queue without turning OCR anchors into sprites.

    Screen objects already hold fused provenance.  Enemy duplicates are collapsed
    only when their boxes overlap; cards intentionally remain distinct instances.
    """
    queued: list[DiscoveryObject] = []
    seen_enemy: list[Box] = []
    for item in objects:
        label = str(item.label or "")
        role = _compact(item.role)
        box = _box(item)
        if is_hp_anchor_label(label) or _is_hud_chrome(label) or is_player_row(_player_dict(item)):
            continue
        card = role == "card" or (not role and item.y >= 0.70)
        enemy = role in {"enemy", "unknown character", "unknown_character"}
        control = role in {"button", "control"} or item.kind == "progress"
        unknown = _is_unknown(label)
        if enemy:
            if any(_iou(box, prior) >= 0.55 for prior in seen_enemy):
                continue
            # HP/name OCR sits above the sprite band; do not hover it as a second enemy.
            if float(item.y) < _SPRITE_Y_MIN:
                continue
            seen_enemy.append(box)
            unknown = unknown or is_anonymous_enemy_label(label)
        if not unknown or not (card or enemy or control):
            continue
        if card and energy is not None and energy <= 0:
            continue
        if enemy:
            priority = 0
        elif card:
            priority = 1
        else:
            priority = 3
        object_id = str(item.object_id or f"mark_{item.mark_id}")
        queued.append(
            DiscoveryObject(
                object_id=object_id,
                mark_id=int(item.mark_id),
                role="enemy" if enemy else "card" if card else "control",
                bbox=box,
                target=(float(item.x), float(item.y)),
                label=label,
                confidence=float(item.confidence),
                identified=False,
                inspectable=True,
                evidence=[{"source": item.source, "provenance": item.provenance}],
            )
        )
        queued[-1].evidence[0]["priority"] = priority
    return sorted(queued, key=lambda item: (item.evidence[0]["priority"], item.target[0], item.object_id))


def rebind_unknowns(queue: Sequence[DiscoveryObject], current: Sequence[ScreenObject]) -> list[DiscoveryObject]:
    """Rebind stable IDs after hover changed frame-local mark numbers."""
    by_id = {str(item.object_id): item for item in current if item.object_id}
    rebound: list[DiscoveryObject] = []
    for item in queue:
        now = by_id.get(item.object_id)
        if now is None:
            continue
        copy = DiscoveryObject(**{**asdict(item), "mark_id": int(now.mark_id), "bbox": _box(now), "target": (now.x, now.y)})
        rebound.append(copy)
    return rebound


def inspection_contract(*, run_id: str, step_id: int, item: DiscoveryObject) -> InspectionContract:
    return InspectionContract(
        run_id=str(run_id), step_id=int(step_id), object_id=item.object_id,
        mark_id=int(item.mark_id), role=item.role, bbox=item.bbox, target=item.target,
    )


def validate_contract(contract: InspectionContract, current: Sequence[ScreenObject]) -> bool:
    """Reject stale marks. Object ID plus overlapping fresh geometry is required."""
    for item in current:
        if str(item.object_id) != contract.object_id:
            continue
        return int(item.mark_id) == contract.mark_id and _iou(contract.bbox, _box(item)) >= 0.35
    return False


def _pixel_box(box: Box, image: Image.Image) -> tuple[int, int, int, int]:
    w, h = image.size
    x0, y0, x1, y1 = box
    left, top = int(max(0.0, min(1.0, x0)) * w), int(max(0.0, min(1.0, y0)) * h)
    right, bottom = int(max(0.0, min(1.0, x1)) * w), int(max(0.0, min(1.0, y1)) * h)
    return left, top, max(left + 1, right), max(top + 1, bottom)


def crop(image: Image.Image, box: Box) -> Image.Image:
    return image.convert("RGB").crop(_pixel_box(box, image))


def difference_box(
    before: Image.Image,
    after: Image.Image,
    *,
    ignore: Sequence[Box] = (),
    roi: Box | None = None,
    union: bool = False,
) -> Box | None:
    """Return a changed region, optionally limited to an ROI.

    The default remains the largest meaningful component.  ``union=True``
    returns the enclosing box of every meaningful component, which is useful
    when a compacting control stack moves in several disconnected rows.  ROI
    and returned coordinates are normalized against the full frame.
    """
    a = np.asarray(before.convert("L").resize((320, 180)), dtype=np.int16)
    b = np.asarray(after.convert("L").resize((320, 180)), dtype=np.int16)
    mask = np.abs(a - b) >= 22
    h, w = mask.shape
    for box in ignore:
        x0, y0, x1, y1 = _pixel_box(box, Image.new("L", (w, h)))
        mask[y0:y1, x0:x1] = False
    if roi is not None:
        x0, y0, x1, y1 = _pixel_box(roi, Image.new("L", (w, h)))
        inside = np.zeros_like(mask, dtype=bool)
        inside[y0:y1, x0:x1] = True
        mask &= inside
    # Tiny components are cursor antialiasing/noise; retain panel-like change.
    visited = np.zeros_like(mask, dtype=bool)
    best: tuple[int, int, int, int, int] | None = None
    envelope: tuple[int, int, int, int] | None = None
    for sy, sx in zip(*np.nonzero(mask)):
        if visited[sy, sx]:
            continue
        stack = [(int(sy), int(sx))]
        visited[sy, sx] = True
        x0 = x1 = int(sx)
        y0 = y1 = int(sy)
        count = 0
        while stack:
            y, x = stack.pop()
            count += 1
            x0, x1, y0, y1 = min(x0, x), max(x1, x), min(y0, y), max(y1, y)
            for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not visited[ny, nx]:
                    visited[ny, nx] = True
                    stack.append((ny, nx))
        if count >= 36 and (best is None or count > best[4]):
            best = (x0, y0, x1 + 1, y1 + 1, count)
        if count >= 36 and union:
            component = (x0, y0, x1 + 1, y1 + 1)
            if envelope is None:
                envelope = component
            else:
                envelope = (
                    min(envelope[0], component[0]), min(envelope[1], component[1]),
                    max(envelope[2], component[2]), max(envelope[3], component[3]),
                )
    if union and envelope is not None:
        x0, y0, x1, y1 = envelope
        return (x0 / w, y0 / h, x1 / w, y1 / h)
    if best is None:
        return None
    x0, y0, x1, y1, _ = best
    return (x0 / w, y0 / h, x1 / w, y1 / h)


def build_crops(before: Image.Image, after: Image.Image, contract: InspectionContract) -> dict[str, Box]:
    change = difference_box(before, after, ignore=(contract.bbox,))
    boxes: dict[str, Box] = {
        "object_crop": _pad(contract.bbox, 0.02),
        "local_context_crop": _pad(contract.bbox, 0.11),
    }
    if change is not None:
        boxes["change_crop"] = _pad(change, 0.01)
        boxes["tooltip_crop"] = _pad(change, 0.02)
    if contract.role == "card":
        # Lifted cards differ from their hand bbox; a local change is stronger evidence.
        boxes["enlarged_card_crop"] = _pad(change, 0.02) if change else boxes["local_context_crop"]
    elif contract.role == "enemy":
        boxes["sprite_name_crop"] = boxes["object_crop"]
        boxes["hp_status_crop"] = boxes["local_context_crop"]
    return boxes


def _words_to_lines(words: Sequence[OcrWord]) -> list[str]:
    rows: list[list[OcrWord]] = []
    for word in sorted(words, key=lambda item: (item.y, item.x)):
        target = next((row for row in rows if abs(sum(w.y for w in row) / len(row) - word.y) <= 0.035), None)
        if target is None:
            rows.append([word])
        else:
            target.append(word)
    return [" ".join(word.text for word in sorted(row, key=lambda item: item.x)) for row in rows]


def parse_card_fields(words: Sequence[OcrWord]) -> dict[str, str]:
    lines = [line.strip() for line in _words_to_lines(words) if line.strip()]
    title = next((line for line in lines if _plausible_name(line)), "")
    cost = next((match.group(0) for line in lines for match in [re.search(r"\b\d+\b", line)] if match), "")
    card_type = next((line for line in lines if _compact(line) in _FIELD_WORDS), "")
    description = " ".join(line for line in lines if line not in {title, cost, card_type})
    return {"title": title, "cost": cost, "type": card_type, "description": description}


def parse_enemy_fields(words: Sequence[OcrWord]) -> dict[str, str]:
    lines = [line.strip() for line in _words_to_lines(words) if line.strip()]
    name = next((line for line in lines if _plausible_name(line)), "")
    hp = next((match.group(0) for line in lines for match in [re.search(r"\b\d+\s*/\s*\d+\b", line)] if match), "")
    return {"name": name, "hp": hp, "intent": " ".join(line for line in lines if line not in {name, hp})}


def _plausible_name(text: str) -> bool:
    compact = _compact(text)
    if not compact or compact in _UNKNOWN or compact in _FIELD_WORDS or compact in _INTENT_WORDS:
        return False
    if compact.startswith("this enemy") or "intends" in compact:
        return False
    if compact.isdigit() or _HP_FRACTION_RE.match(compact.replace(" ", "")):
        return False
    if len(compact) <= 1:
        return False
    return len(compact) <= 48


def _ocr_variants(image: Image.Image, reader: Callable[[Image.Image], list[OcrWord]]) -> list[OcrWord]:
    original = image.convert("RGB")
    out: list[OcrWord] = []
    seen: set[tuple[str, int, int]] = set()

    def _add(words: Sequence[OcrWord]) -> None:
        for word in words:
            key = (_compact(word.text), round(word.x * 20), round(word.y * 20))
            if key not in seen:
                out.append(word)
                seen.add(key)

    _add(reader(original))
    if any(_plausible_name(word.text) for word in out):
        return out
    scaled = original.resize((max(1, original.width * 2), max(1, original.height * 2)), Image.Resampling.LANCZOS)
    _add(reader(scaled))
    if any(_plausible_name(word.text) for word in out):
        return out
    enhanced = ImageEnhance.Sharpness(ImageOps.autocontrast(original, cutoff=1)).enhance(1.5)
    _add(reader(enhanced.resize((max(1, enhanced.width * 2), max(1, enhanced.height * 2)), Image.Resampling.LANCZOS)))
    return out


def _novel_words(after: Sequence[OcrWord], before: Sequence[OcrWord]) -> list[OcrWord]:
    prior = {_compact(word.text) for word in before if word.text}
    return [word for word in after if _compact(word.text) and _compact(word.text) not in prior]


class DiscoveryWorkflow:
    """Small state machine for one inspection; caller owns scheduling and ACT."""

    def __init__(self, *, artifacts_root: Path | None = None, ocr_reader: Callable[[Image.Image], list[OcrWord]] = read_words) -> None:
        self.artifacts_root = artifacts_root
        self.ocr_reader = ocr_reader
        self.state = DiscoveryState.INVENTORY_UNKNOWN

    def inspect_frames(
        self,
        contract: InspectionContract,
        *,
        before: Image.Image,
        after_frames: Sequence[Image.Image],
        vlm_read: Callable[[Image.Image, str], dict[str, str] | None] | None = None,
    ) -> DiscoveryResult:
        """Analyze bounded post-hover captures; no click/drag capability exists here."""
        self.state = DiscoveryState.WAIT_FOR_TOOLTIP
        if not after_frames:
            result = self._finish(contract, "unresolved", "no-after-frame")
            self._write_artifacts(contract, before, before, result)
            return result
        after = after_frames[-1]
        if len(after_frames) >= 2:
            a, b = after_frames[-2:]
            delta = float(np.abs(np.asarray(a.resize((64, 36)), dtype=np.int16) - np.asarray(b.resize((64, 36)), dtype=np.int16)).mean())
            if delta > 8.0:
                return self._finish(contract, "unresolved", "unstable-animation", after=after)
        self.state = DiscoveryState.LOCALIZE_CHANGE
        crops = build_crops(before, after, contract)
        self.state = DiscoveryState.OCR_CROPS
        after_ocr: dict[str, list[OcrWord]] = {
            name: _ocr_variants(crop(after, box), self.ocr_reader) for name, box in crops.items()
        }
        before_ocr: dict[str, list[OcrWord]] = {
            name: _ocr_variants(crop(before, box), self.ocr_reader) for name, box in crops.items()
        }
        ocr = after_ocr
        evidence = [{"crop": key, "text": " ".join(_words_to_lines(words))} for key, words in ocr.items() if words]
        if contract.role == "card":
            focus_key = next(
                (key for key in ("enlarged_card_crop", "tooltip_crop", "object_crop") if after_ocr.get(key)),
                "object_crop",
            )
            focus_after = after_ocr.get(focus_key) or []
            focus_before = before_ocr.get(focus_key) or []
            novel = _novel_words(focus_after, focus_before)
            parse_words = novel or focus_after
            name = parse_card_fields(parse_words).get("title") or ""
        else:
            # STS2 name sits on the HP plate (object crop). Hover opens an intent
            # tooltip (Aggressive / "intends to Attack") — never use that as identity.
            plate = after_ocr.get("sprite_name_crop") or after_ocr.get("object_crop") or []
            name = parse_enemy_fields(plate).get("name") or ""
            if not name:
                tooltip = after_ocr.get("tooltip_crop") or after_ocr.get("change_crop") or []
                tooltip_before = before_ocr.get("tooltip_crop") or before_ocr.get("change_crop") or []
                novel = _novel_words(tooltip, tooltip_before)
                name = parse_enemy_fields(novel or tooltip).get("name") or ""
        # VLM is transcription-only fallback. It cannot invent identity: require
        # its proposed name to appear in crop OCR from any focused crop.
        if not name and vlm_read is not None:
            self.state = DiscoveryState.OPTIONAL_VLM_READ
            focused = crop(after, crops.get("tooltip_crop", crops["object_crop"]))
            fallback = vlm_read(focused, contract.role) or {}
            proposed = str(fallback.get("name") or fallback.get("title") or "").strip()
            all_text = _compact(" ".join(word.text for words in ocr.values() for word in words))
            if _plausible_name(proposed) and _compact(proposed) in all_text:
                name = proposed
                evidence.append({"crop_vlm": proposed, "supported_by_ocr": True})
        if name and _plausible_name(name):
            result = DiscoveryResult(contract, DiscoveryState.VERIFY_AND_BIND, "identified", name=name, crops=crops, ocr=ocr, evidence=evidence)
        else:
            reason = "empty-ocr" if not evidence else "ambiguous-attribution"
            result = DiscoveryResult(contract, DiscoveryState.NEXT_UNKNOWN, "unresolved", failure=reason, crops=crops, ocr=ocr, evidence=evidence)
        self._write_artifacts(contract, before, after, result)
        self.state = result.state
        return result

    def _finish(self, contract: InspectionContract, status: str, failure: str, *, after: Image.Image | None = None) -> DiscoveryResult:
        result = DiscoveryResult(contract, DiscoveryState.NEXT_UNKNOWN, status, failure=failure)
        if after is not None:
            self._write_artifacts(contract, after, after, result)
        self.state = result.state
        return result

    def _write_artifacts(self, contract: InspectionContract, before: Image.Image, after: Image.Image, result: DiscoveryResult) -> None:
        if self.artifacts_root is None:
            return
        dest = (
            self.artifacts_root
            / str(contract.run_id)
            / str(contract.step_id)
            / "discovery"
            / (_UNSAFE_PATH.sub("_", str(contract.object_id or "object")) or "object")
        )
        dest.mkdir(parents=True, exist_ok=True)
        before.convert("RGB").save(dest / "before.jpg", quality=95)
        after.convert("RGB").save(dest / "after_full.jpg", quality=95)
        for name, box in result.crops.items():
            crop(after, box).save(dest / f"{name}.jpg", quality=95)
        (dest / "ocr.json").write_text(json.dumps(result.payload()["ocr"], indent=2), encoding="utf-8")
        (dest / "result.json").write_text(json.dumps(result.payload(), indent=2), encoding="utf-8")
