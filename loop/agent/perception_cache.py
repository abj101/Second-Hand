"""Frame-level cache for OCR words and combat enemy marks."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from PIL import Image

from loop.os.aim import AimMark, merge_cached_enemy_marks
from loop.os.layout_adapter import LayoutAdapter, default_layout_adapter
from loop.os.profile import mean_absdiff, roi_thumb

__all__ = [
    "PerceptionCache",
    "PerceptionReuse",
    "enemy_marks_from",
    "enemy_roster_changed",
    "enemy_roster_signature",
    "merge_cached_enemy_marks",
    "ocr_uncertain_for_reuse",
]

_UNCERTAIN_OCR_REASONS = frozenset(
    {
        "screen-change",
        "forced",
        "inspect",
        "no-screen",
        "enemy-roster-change",
        "unknown-needs-inspect",
        "refresh",
        "size-change",
    }
)

_FRAME_MAD_THRESHOLD = 0.02


def enemy_roster_signature(host_stats: dict[str, Any] | None) -> tuple[tuple[Any, ...], ...]:
    """Enemy count / max-HP fingerprint — HP drift does not invalidate sprite cache."""
    rows = (host_stats or {}).get("enemies") or []
    parts: list[tuple[Any, ...]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        hp_max = row.get("hp_max")
        if not isinstance(hp_max, (int, float)) or hp_max <= 0:
            continue
        parts.append(
            (
                int(hp_max),
                str(row.get("label") or "").strip().lower() or None,
            )
        )
    return tuple(sorted(parts))


def enemy_roster_changed(
    prev_host: dict[str, Any] | None,
    host_stats: dict[str, Any] | None,
) -> bool:
    return enemy_roster_signature(prev_host) != enemy_roster_signature(host_stats)


def enemy_marks_from(
    marks: list[AimMark] | tuple[AimMark, ...],
    *,
    layout_adapter: LayoutAdapter | None = None,
) -> list[AimMark]:
    adapter = layout_adapter or default_layout_adapter()
    if not adapter.enables_combat_role_stamps("combat"):
        return []
    return [
        m
        for m in marks
        if adapter.in_combat_sprite_band(m.y, m.x) and (m.label or "").strip()
    ]


@dataclass(frozen=True)
class PerceptionReuse:
    reuse_ocr: bool = False
    reuse_enemies: bool = False
    reason: str = ""


def ocr_uncertain_for_reuse(reuse: PerceptionReuse) -> bool:
    """True when region OCR must fall back to full-frame."""
    return (reuse.reason or "") in _UNCERTAIN_OCR_REASONS


@dataclass
class PerceptionCache:
    last_screen: str = ""
    last_ocr: list[Any] = field(default_factory=list)
    last_entities: list[dict[str, Any]] = field(default_factory=list)
    last_enemy_marks: list[AimMark] = field(default_factory=list)
    last_frame_hash: str = ""
    _last_thumb: Image.Image | None = field(default=None, repr=False)
    _last_enemy_thumb: Image.Image | None = field(default=None, repr=False)
    _last_roster_sig: tuple[tuple[Any, ...], ...] = field(default_factory=tuple, repr=False)
    _last_size: tuple[int, int] | None = field(default=None, repr=False)

    def reset(self) -> None:
        self.last_screen = ""
        self.last_ocr.clear()
        self.last_entities.clear()
        self.last_enemy_marks.clear()
        self.last_frame_hash = ""
        self._last_thumb = None
        self._last_enemy_thumb = None
        self._last_roster_sig = ()
        self._last_size = None
        from loop.os.ocr import clear_ocr_region_cache

        clear_ocr_region_cache()

    def invalidate_enemies(self) -> None:
        self.last_entities.clear()
        self.last_enemy_marks.clear()
        self._last_enemy_thumb = None
        self._last_roster_sig = ()

    def compute_frame_hash(self, image: Image.Image, *, size: int = 64) -> str:
        thumb = roi_thumb(image, size)
        return hashlib.md5(thumb.tobytes()).hexdigest()

    def _enemy_thumb(self, image: Image.Image, *, layout_adapter: LayoutAdapter | None = None) -> Image.Image:
        adapter = layout_adapter or default_layout_adapter()
        w, h = image.size
        x0, y0, x1, y1 = (0.0, adapter.hud_chrome_y, 1.0, adapter.hand_y)
        crop = image.crop((int(x0 * w), int(y0 * h), int(x1 * w), int(y1 * h)))
        return roi_thumb(crop, 64)

    def evaluate(
        self,
        *,
        view: Image.Image,
        screen: str,
        host_stats: dict[str, Any] | None,
        prev_host_stats: dict[str, Any] | None,
        entity_tracker: Any | None = None,
        force: bool = False,
        inspect: bool = False,
        layout_adapter: LayoutAdapter | None = None,
    ) -> PerceptionReuse:
        if force:
            return PerceptionReuse(reason="forced")
        if inspect:
            return PerceptionReuse(reason="inspect")

        screen_key = (screen or "").strip().lower()
        if not screen_key:
            return PerceptionReuse(reason="no-screen")

        if self._last_size is not None and view.size != self._last_size:
            return PerceptionReuse(reason="size-change")

        if screen_key != (self.last_screen or "").strip().lower():
            return PerceptionReuse(reason="screen-change")

        frame_hash = self.compute_frame_hash(view)
        if frame_hash == self.last_frame_hash and self.last_ocr:
            return PerceptionReuse(
                reuse_ocr=True,
                reuse_enemies=bool(self.last_enemy_marks),
                reason="frame-identical",
            )

        thumb = roi_thumb(view, 64)
        if self._last_thumb is not None and self.last_ocr:
            mad = mean_absdiff(self._last_thumb, thumb)
            if mad <= _FRAME_MAD_THRESHOLD:
                return PerceptionReuse(
                    reuse_ocr=True,
                    reuse_enemies=bool(self.last_enemy_marks),
                    reason="frame-stable",
                )

        if screen_key == "combat" and self.last_enemy_marks:
            from loop.agent.perceive import _tracker_needs_inspect

            if _tracker_needs_inspect(entity_tracker):
                return PerceptionReuse(reason="unknown-needs-inspect")
            roster = enemy_roster_signature(host_stats)
            if self._last_roster_sig and roster != self._last_roster_sig:
                return PerceptionReuse(reason="enemy-roster-change")
            if enemy_roster_changed(prev_host_stats, host_stats):
                return PerceptionReuse(reason="enemy-roster-change")
            enemy_thumb = self._enemy_thumb(view, layout_adapter=layout_adapter)
            if self._last_enemy_thumb is not None:
                enemy_mad = mean_absdiff(self._last_enemy_thumb, enemy_thumb)
                if enemy_mad <= _FRAME_MAD_THRESHOLD:
                    return PerceptionReuse(reuse_enemies=True, reason="combat-enemies")
            else:
                return PerceptionReuse(reuse_enemies=True, reason="combat-enemies")

        return PerceptionReuse(reason="refresh")

    def update(
        self,
        *,
        view: Image.Image,
        screen: str,
        ocr_words: list[Any],
        marks: list[AimMark] | tuple[AimMark, ...],
        entity_rows: list[dict[str, Any]] | None = None,
        host_stats: dict[str, Any] | None = None,
        layout_adapter: LayoutAdapter | None = None,
    ) -> None:
        self.last_screen = (screen or "").strip().lower()
        self.last_ocr = list(ocr_words)
        self.last_enemy_marks = enemy_marks_from(list(marks), layout_adapter=layout_adapter)
        self.last_entities = list(entity_rows or [])
        self.last_frame_hash = self.compute_frame_hash(view)
        self._last_thumb = roi_thumb(view, 64)
        self._last_enemy_thumb = self._enemy_thumb(view, layout_adapter=layout_adapter)
        self._last_roster_sig = enemy_roster_signature(host_stats)
        self._last_size = view.size
