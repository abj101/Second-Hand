from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from loop.log.schema import ActionLog
from loop.os.layout_adapter import (
    ENEMY_Y_MAX,
    ENEMY_Y_MIN,
    HAND_Y,
    HUD_CHROME_Y,
    LayoutAdapter,
    default_layout_adapter,
)

GRID = 1000
_MAX_MARKS = 16
# Re-exported from layout_adapter for heuristic / perception_cache callers.
ENEMY_Y = ENEMY_Y_MAX  # legacy alias (was 0.55)
_ENEMY_HP_SPRITE_OFFSET = 0.35
_UNKNOWN_ENTITY_RE = re.compile(
    r"^(unknown_character(?:_\d+)?|unidentified_enemy|unidentified enemy)$",
    re.I,
)
_HAND_TITLE_Y0 = 0.76
_HAND_TITLE_Y1 = 0.92
_HAND_X0 = 0.18
_HAND_X1 = 0.82
_HAND_CARD_HALF_W = 0.07
_HAND_CARD_TITLE_PAD = 0.055
_HAND_CARD_CLICK_DY = 0.085
_MASK_W = 320
_MIN_PX = 8
_INK_MIN_PX = 16
_INK_CAP = 12
_MAX_FRAC = 0.08
_MAX_ASPECT = 4.0
_INK_TAU = 16.0
_PAPER_L = 70.0
_WARM_GAP = 10.0
_UI_CHROMA = 42.0
_UI_MAX_L = 125.0
_SPREAD = 0.048
_TEXT_MIN_L = 155.0
_TEXT_ASPECT = 16.0
_TEXT_MIN_PX = 8
_OCR_MIN_CONF = 60.0
_OCR_MIN_LEN = 3
_DARK_LUMA = 70.0
_INK_SOLID = 35.0
_LEGEND_WORDS = frozenset(
    {
        "legend",
        "unknown",
        "merchant",
        "treasure",
        "rest",
        "res",
        "enemy",
        "elite",
        "campfire",
        "chest",
    }
)
_PROGRESS_WORDS = (
    "continue",
    "confirm",
    "proceed",
    "embark",
    "start",
    "begin",
    "select",
    "next",
    "back",
    "got it",
    "resume",
    "singleplayer",
    "end turn",
)
_DANGER_WORDS = ("quit", "abandon", "exit", "give up", "save and quit")
_CORNER_CONFIRM = frozenset({"confirm", "embark"})
_CORNER_UI = frozenset({"confirm", "embark", "back"})
# YOLO class names / unlabeled icons. Corner pass still overwrites Confirm/Back.
_PLACEHOLDER_LABELS = frozenset(
    {
        "icon",
        "button",
        "buttons",
        "menu",
        "menus",
        "input",
        "input elements",
        "input_elements",
        "navigation",
        "sidebar",
        "sidebars",
        "visual",
        "visual elements",
        "visual_elements",
        "interactable",
        "character",
        "locked",
        "relic",
        "unknown relic",
    }
)
_STEER_SKIP_LABELS = _PLACEHOLDER_LABELS
_LOCKED_FLAVOR = frozenset({"play", "unlock", "single"})
# Lower-right check fuses with floor glow; click the pennant, not y=1.
_CORNER_MAX_H = 0.14
_CORNER_CLICK_T = 0.20  # center of the upper 40% of the (clipped) box
_CORNER_CONFIRM_X = 0.88
_CORNER_CONFIRM_Y = 0.68
_DIALOG_HINTS = frozenset({"tutorial", "tutorials", "got it", "tut"})
_DIALOG_BUTTONS = frozenset({"yes", "no", "got it"})
_SNAP_R2 = 0.08 * 0.08
_DIALOG_X0 = 0.22
_DIALOG_X1 = 0.72
_DIALOG_Y = 0.78
_CV_MAX_EDGE = 0.28
_GUI_ATTACH_IOU = 0.2
_GUI_PLACEHOLDER_CAP = 6
# Complementary fusion: keep non-overlapping CV even when GUI found something.
_FUSION_IOU = 0.25
_CV_COVERAGE_RESERVE = 4
_NEAR_CENTER = 0.04
_HUD_CHROME_Y = 0.12
_TOP_HUD_Y = 0.15
_HUD_ONLY_LABELS = frozenset(
    {
        "end turn",
        "cards",
        "card",
        "deck",
        "potion",
        "potions",
        "map",
        "play",
        "points",
        "obtained",
        "share",
        "hp",
    }
)
_CARD_NAME_HINTS = frozenset({"strike", "defend", "bash", "anger", "cleave", "survivor"})
_HAND_FORBIDDEN = frozenset({"end turn", "cards", "card", "deck"})
_OCR_BODY = frozenset(
    {
        "deal",
        "gain",
        "apply",
        "damage",
        "block",
        "vulnerable",
        "shuffled",
        "obtained",
        "share",
    }
)
_OCR_JUNK = frozenset(
    {
        "the",
        "click",
        "and",
        "for",
        "you",
        "this",
        "that",
        "with",
        "from",
        "are",
        "was",
        "not",
        "but",
    }
)
_NAV_LABELS_AIM = frozenset({"prev", "next", "arrow"})
_OVERLAY_DISMISS = frozenset({"yes", "no", "got it", "close", "back"})
_VERSION_RE = re.compile(r"^(?:v\d|\d{4}\s+\d{2})", re.I)
_YEAR_RE = re.compile(r"\b20\d{2}\b")
_MENU_ONE_CLICK = frozenset(
    {
        "singleplayer",
        "multiplayer",
        "settings",
        "continue",
        "confirm",
        "resume",
        "got it",
        "back",
        "embark",
        "proceed",
        "start",
        "begin",
        "next",
        "end turn",
        "quit",
        "cards",
        "play",
        "map",
        "deck",
        "yes",
        "no",
        "close",
    }
)
# Dialog / menu words that must be OCR-verified near the mark (blocks lexicon drift).
_OCR_GATED_LABELS = frozenset(
    {
        "got it",
        "yes",
        "no",
        "close",
        "confirm",
        "back",
        "resume",
        "continue",
        "singleplayer",
        "multiplayer",
        "settings",
        "embark",
        "proceed",
        "start",
        "begin",
        "quit",
    }
)
_OCR_VERIFY_R = 0.14
_BUTTON_ROIS = (
    (0.28, 0.60, 0.72, 0.94),  # bottom-center — Got it / Yes / No
    (0.74, 0.0, 1.0, 0.20),  # top-right — Confirm / Close pennant
)


@dataclass(frozen=True)
class AimMark:
    id: int
    x: float
    y: float
    x0: float
    y0: float
    x1: float
    y1: float
    label: str = ""
    score: float = 0.0
    object_id: str = ""
    role: str = ""
    source: str = ""
    confidence: float = 0.0


@dataclass(frozen=True)
class AimGuide:
    marks: tuple[AimMark, ...]
    last_xy: tuple[float, float] | None
    image: Image.Image

    @classmethod
    def from_marks(
        cls,
        image: Image.Image,
        marks: tuple[AimMark, ...] | list[AimMark],
        *,
        last_xy: tuple[float, float] | None = None,
    ) -> AimGuide:
        """Raw frame + marks; no grid/badges painted on image."""
        seq = tuple(marks)
        return cls(marks=seq, last_xy=last_xy, image=image.convert("RGB"))

    def by_id(self, mark_id: int) -> AimMark | None:
        for mark in self.marks:
            if mark.id == mark_id:
                return mark
        return None

    def listing(self) -> str:
        if not self.marks:
            return ""
        parts: list[str] = []
        for m in self.marks:
            cell = f"{m.id}@{int(round(m.x * GRID))},{int(round(m.y * GRID))}"
            if m.label:
                cell += ":" + m.label.replace(" ", "_")
            parts.append(cell)
        return " ".join(parts)


def _compact_label(label: str) -> str:
    return " ".join("".join(ch if ch.isalnum() else " " for ch in (label or "").lower()).split())


def _phrase_in(hay: str, phrase: str) -> bool:
    if not hay or not phrase:
        return False
    return re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", hay) is not None


def label_kind(label: str) -> str:
    compact = _compact_label(label)
    if any(_phrase_in(compact, word) for word in _DANGER_WORDS):
        return "danger"
    if any(_phrase_in(compact, word) for word in _PROGRESS_WORDS):
        return "progress"
    return "other"


def _ocr_junk(label: str) -> bool:
    compact = _compact_label(label)
    if not compact or compact in _OCR_JUNK:
        return True
    parts = compact.split()
    if parts and all(p.isdigit() for p in parts):
        return True
    if _VERSION_RE.search(compact) or _YEAR_RE.search(compact):
        return True
    # CJK / digit-glyph soup (live: "12入" from "12×") is never a card or enemy name.
    if not any("a" <= ch <= "z" for ch in compact):
        return True
    return False


def _ocr_click_label(label: str) -> bool:
    """True only when an OCR box is shaped like a complete control label.

    ``label_kind`` intentionally recognizes progress words inside longer text,
    which is useful for semantic classification but unsafe for creating click
    targets.  Tutorial prose such as "start of your next turn" must not become
    a Start/Next control merely because OCR split the sentence into fragments.
    """
    compact = _compact_label(label)
    if compact in _MENU_ONE_CLICK:
        return True
    if compact in {_compact_label(word) for word in _DANGER_WORDS}:
        return True
    # Some HUDs append the turn number to the otherwise exact button label.
    return re.fullmatch(r"end turn(?: \d+)?", compact) is not None


_TUTORIAL_OCR_ONLY_CONTROLS = frozenset(
    {
        "yes",
        "no",
        "got it",
        "confirm",
        "continue",
        "resume",
        "close",
        "back",
        "prev",
        "previous",
        "next",
        "end turn",
    }
)


def _unattached_ocr_click_label(mark: AimMark, *, screen: str | None) -> bool:
    """Allow OCR-only controls conservatively on tutorial overlays.

    Start/Begin are valid menu labels elsewhere, but in an active tutorial they
    commonly come from instructional prose.  They remain usable when attached
    to independently detected GUI geometry.
    """
    if not _ocr_click_label(mark.label):
        return False
    if (screen or "").strip().lower() != "tutorial":
        return True
    return _compact_label(mark.label) in _TUTORIAL_OCR_ONLY_CONTROLS


def _prefer_menu_label(raw: str) -> str:
    compact = _compact_label(raw)
    if re.fullmatch(r"end turn(?: \d+)?", compact):
        return "End Turn"
    for word in sorted(_MENU_ONE_CLICK, key=len, reverse=True):
        if compact == word:
            if word == "got it":
                return "Got it"
            if word == "end turn":
                return "End Turn"
            return word.title()
    return " ".join((raw or "").split())


def _ocr_unit_label(mark: AimMark) -> bool:
    """Hand / board unit names (Strike, Bash, enemy) that are not body copy."""
    compact = _compact_label(mark.label)
    parts = compact.split()
    if not (1 <= len(parts) <= 2):
        return False
    if any(p in _OCR_BODY for p in parts) or any(p.isdigit() for p in parts):
        return False
    if _ocr_click_label(mark.label) or _ocr_junk(mark.label):
        return False
    if mark.y >= HAND_Y:
        return True
    return 0.14 <= mark.y <= 0.52 and mark.x < 0.82


def _is_end_turn_label(label: str) -> bool:
    return "end turn" in _compact_label(label)


def _eligible_label_for_region(
    mark: AimMark,
    label: str,
    *,
    screen: str | None = None,
    layout_adapter: LayoutAdapter | None = None,
) -> bool:
    """Reject labels that belong in a different screen region (HUD vs hand vs enemy)."""
    lab = _compact_label(label)
    if not lab or _is_placeholder_label(label):
        return True
    x, y = float(mark.x), float(mark.y)
    kind = (screen or "").strip().lower()
    combatish = kind in {"combat", "tutorial", "other", ""}
    adapter = layout_adapter or default_layout_adapter()

    if adapter.in_corner_hud(x, y):
        return (
            lab in _HUD_ONLY_LABELS
            or label_kind(label) in {"progress", "danger"}
            or _is_end_turn_label(label)
        )

    if y < _TOP_HUD_Y:
        if combatish and (lab in _CARD_NAME_HINTS or label_kind(label) == "card"):
            return False
        if lab in _HUD_ONLY_LABELS or label_kind(label) in {"progress", "danger"}:
            return True
        return not combatish

    if _is_end_turn_label(label):
        return x >= _HAND_X1

    if y >= adapter.hand_y:
        return lab not in _HAND_FORBIDDEN

    if adapter.in_combat_sprite_band(y, x):
        if lab in _HAND_FORBIDDEN:
            return False
        if combatish and lab in _CARD_NAME_HINTS:
            return False
        return True

    if combatish and lab in _CARD_NAME_HINTS:
        return False
    return True


def _strip_region_invalid_labels(
    marks: list[AimMark],
    *,
    screen: str | None = None,
    layout_adapter: LayoutAdapter | None = None,
) -> list[AimMark]:
    out: list[AimMark] = []
    for mark in marks:
        label = str(mark.label or "").strip()
        if label and not _eligible_label_for_region(
            mark, label, screen=screen, layout_adapter=layout_adapter
        ):
            out.append(_copy_mark(mark, label=":icon"))
        else:
            out.append(mark)
    return out


def in_enemy_band(
    y: float,
    x: float | None = None,
    *,
    layout_adapter: LayoutAdapter | None = None,
) -> bool:
    """True when normalized coords are in the combat enemy sprite band."""
    adapter = layout_adapter or default_layout_adapter()
    return adapter.in_enemy_band(y, x)


def in_combat_sprite_band(
    y: float,
    x: float | None = None,
    *,
    layout_adapter: LayoutAdapter | None = None,
) -> bool:
    """True when coords sit on the combat board, not HUD chrome or the hand."""
    adapter = layout_adapter or default_layout_adapter()
    return adapter.in_combat_sprite_band(y, x)


def in_hand_band(x: float, y: float, *, layout_adapter: LayoutAdapter | None = None) -> bool:
    """True when a click sits on the combat hand, not the energy orb or board."""
    adapter = layout_adapter or default_layout_adapter()
    return _HAND_X0 <= float(x) <= _HAND_X1 and float(y) >= float(adapter.hand_y)


def _is_unknown_entity_label(label: str) -> bool:
    raw = (label or "").strip().lower()
    if _UNKNOWN_ENTITY_RE.match(raw):
        return True
    compact = _compact_label(label)
    if compact in {"unknown character", "unidentified enemy", "unidentified enemy"}:
        return True
    if re.fullmatch(r"unknown character \d+", compact):
        return True
    return False


def combat_name_list(raw: Any) -> list[str]:
    """Normalize LLM1 / host combat names while preserving card instances."""
    out: list[str] = []
    if not isinstance(raw, (list, tuple)):
        return out
    for item in raw:
        label = ""
        if isinstance(item, str):
            label = item.strip()
        elif isinstance(item, dict):
            label = str(item.get("label") or item.get("name") or "").strip()
        if not label or _ocr_junk(label) or _looks_like_stat_label(label):
            continue
        key = _compact_label(label)
        if not key or _is_end_turn_label(label):
            continue
        if label_kind(label) in {"progress", "danger"}:
            continue
        if _compact_label(label) == "unidentified_enemy" or _is_unknown_entity_label(label):
            out.append(label)
            continue
        out.append(label)
    return out


def propose_hand_cards(
    words: Sequence[AimMark] | None,
    *,
    screen: str | None,
    cap: int = 10,
    layout_adapter: LayoutAdapter | None = None,
) -> list[AimMark]:
    """Turn one OCR title per visible card into non-executable hand candidates.

    STS cards fan and overlap, so a generic connected-component detector tends
    to fuse the whole hand.  The title remains visible once per physical card;
    use it as an anchor inside a strict hand crop.  These are *candidates*,
    not cards: only current-frame typed geometry can promote one to an
    executable card with an object id.
    """
    adapter = layout_adapter or default_layout_adapter()
    if not adapter.enables_hand_card_proposals(screen):
        return []
    candidates: list[AimMark] = []
    for word in words or ():
        label = str(word.label or "").strip()
        compact = _compact_label(label)
        if not (_HAND_X0 <= word.x <= _HAND_X1 and _HAND_TITLE_Y0 <= word.y <= _HAND_TITLE_Y1):
            continue
        if not compact or _ocr_junk(label) or _looks_like_stat_label(label):
            continue
        # Body copy and OCR fragments are not card titles.  In particular,
        # never turn "Deal 6 damage" or "Apply" into a clickable card.
        if any(part in _OCR_BODY for part in compact.split()) or any(
            part.isdigit() for part in compact.split()
        ):
            continue
        if any(
            compact != name
            and len(compact) >= 4
            and (name.startswith(compact) or name.endswith(compact))
            for name in _CARD_NAME_HINTS
        ):
            continue
        if _is_end_turn_label(label) or compact in _HAND_FORBIDDEN:
            continue
        if compact in {"attack", "skill", "power", "status", "curse", "exhaust", "ethereal"}:
            continue
        if any(
            _compact_label(existing.label) == compact
            and abs(existing.x - word.x) < 0.025
            and abs((existing.y0 + _HAND_CARD_TITLE_PAD) - word.y) < 0.025
            for existing in candidates
        ):
            continue
        x0 = max(_HAND_X0, word.x - _HAND_CARD_HALF_W)
        x1 = min(_HAND_X1, word.x + _HAND_CARD_HALF_W)
        y0 = max(HAND_Y, word.y - _HAND_CARD_TITLE_PAD)
        y1 = 1.0
        click_y = min(0.95, max(word.y + _HAND_CARD_CLICK_DY, y0 + 0.04))
        candidates.append(
            AimMark(
                id=0,
                x=float(word.x),
                y=click_y,
                x0=x0,
                y0=y0,
                x1=x1,
                y1=y1,
                label=label,
                score=max(float(word.score), 0.75),
                role="hand_candidate",
                source="ocr-hand",
                confidence=0.0,
            )
        )
    candidates.sort(key=lambda mark: mark.x)
    return candidates[: max(0, cap)]


def _looks_like_stat_label(label: str) -> bool:
    compact = _compact_label(label)
    if not compact:
        return True
    if re.fullmatch(r"\d+(?:/\d+)?", compact.replace(" ", "")):
        return True
    return False


def strip_misplaced_progress(
    marks: Sequence[AimMark],
    *,
    layout_adapter: LayoutAdapter | None = None,
) -> list[AimMark]:
    """Drop End Turn / menu OCR ghosts that land in the enemy band (top chrome).

    Real End Turn sits lower-right near the hand; top-of-screen End Turn labels are
    OCR bleed from the button and steal enemy-band slots.
    """
    adapter = layout_adapter or default_layout_adapter()
    kept: list[AimMark] = []
    for mark in marks:
        if _is_end_turn_label(mark.label) and adapter.in_enemy_band(mark.y, mark.x):
            continue
        if label_kind(mark.label) == "progress" and mark.y < adapter.hud_chrome_y:
            continue
        kept.append(mark)
    return kept if kept else list(marks)


def _synthetic_mark(
    *,
    mid: int,
    label: str,
    x: float,
    y: float,
    half_w: float = 0.06,
    half_h: float = 0.08,
    score: float = 0.55,
) -> AimMark:
    x = float(max(0.05, min(0.95, x)))
    y = float(max(0.08, min(0.95, y)))
    return AimMark(
        id=mid,
        x=x,
        y=y,
        x0=max(0.0, x - half_w),
        y0=max(0.0, y - half_h),
        x1=min(1.0, x + half_w),
        y1=min(1.0, y + half_h),
        label=label,
        score=score,
    )


def seed_enemies_from_host(
    marks: Sequence[AimMark] | None,
    *,
    host_stats: dict[str, Any] | None,
    image: Image.Image | None = None,
    cap: int = _MAX_MARKS,
    lexicon: Any | None = None,
    screen: str | None = None,
    layout_adapter: LayoutAdapter | None = None,
) -> list[AimMark]:
    """Seed enemy-band marks from host HP pairs; names come from object library, not HP tables."""
    adapter = layout_adapter or default_layout_adapter()
    rows = (host_stats or {}).get("enemies") or []
    if not rows:
        return list(marks or ())
    screen_key = str(screen or (host_stats or {}).get("screen") or "").strip().lower()
    if screen_key not in {"combat", "tutorial", ""}:
        return list(marks or ())
    if not adapter.enables_combat_role_stamps(screen_key or None):
        return list(marks or ())

    out = list(marks or ())
    have = {_compact_label(m.label) for m in out if (m.label or "").strip()}
    player = (host_stats or {}).get("player") or {}
    player_max = player.get("hp_max") if isinstance(player, dict) else None

    hp_positions: list[tuple[float, float, int]] = []
    if image is not None:
        try:
            from loop.os.ocr import read_words

            pair_re = re.compile(r"^(\d+)\s*/\s*(\d+)$")
            for word in read_words(image):
                m = pair_re.match((word.text or "").strip())
                if not m:
                    continue
                cur, mx = int(m.group(1)), int(m.group(2))
                if mx <= 0 or cur > mx or mx > 200:
                    continue
                if player_max is not None and mx == int(player_max):
                    continue
                if word.y < adapter.hud_chrome_y:
                    continue
                hp_positions.append((float(word.x), float(word.y), mx))
        except Exception:  # noqa: BLE001 — seeding must never abort collect
            hp_positions = []

    for row in rows:
        if not isinstance(row, dict):
            continue
        hp_max = row.get("hp_max")
        if not isinstance(hp_max, (int, float)) or hp_max <= 0:
            continue
        x, y = 0.55, 0.0
        found_hp = False
        for wx, wy, mx in hp_positions:
            if mx == int(hp_max):
                x = wx
                y = wy - _ENEMY_HP_SPRITE_OFFSET
                if not in_combat_sprite_band(y, x, layout_adapter=adapter):
                    y = max(adapter.hud_chrome_y + 0.02, min(adapter.hand_y - 0.02, float(wy)))
                found_hp = True
                break
        label = str(row.get("label") or "").strip()
        if not label and lexicon is not None:
            lib = lexicon.label_at(x, y, screen=screen or (host_stats or {}).get("screen"))
            if lib and not _is_unknown_entity_label(lib):
                label = lib
        if not label:
            label = "unidentified_enemy"
        # Bbox-less unidentified seeds at y=0.32 collide with live mid-board sprites.
        if not found_hp and _is_unknown_entity_label(label):
            continue
        if _compact_label(label) in have:
            continue
        if len(out) >= cap:
            break
        out.append(
            _synthetic_mark(
                mid=len(out) + 1,
                label=label,
                x=x,
                y=y,
                score=0.78,
            )
        )
        have.add(_compact_label(label))
    return out


def merge_cached_enemy_marks(
    marks: Sequence[AimMark] | None,
    cached: Sequence[AimMark] | None,
    *,
    layout_adapter: LayoutAdapter | None = None,
) -> list[AimMark]:
    """Drop fresh enemy-band OCR and reattach cached sprite marks."""
    adapter = layout_adapter or default_layout_adapter()
    if not cached:
        return list(marks or ())
    if not adapter.enables_combat_role_stamps("combat"):
        return list(marks or ())
    from loop.memory.screen_objects import same_sprite_cluster

    def in_band(mark: AimMark) -> bool:
        return in_combat_sprite_band(mark.y, mark.x, layout_adapter=adapter)

    out = [m for m in (marks or ()) if not in_band(m)]
    have = {_compact_label(m.label) for m in out if (m.label or "").strip()}
    kept_band: list[AimMark] = []
    for em in cached:
        key = _compact_label(em.label)
        if key and key in have:
            continue
        if in_band(em) and any(same_sprite_cluster(em, other) for other in kept_band):
            continue
        out.append(em)
        if key:
            have.add(key)
        if in_band(em):
            kept_band.append(em)
    return out


def apply_enemy_bboxes(
    marks: Sequence[AimMark] | None,
    enemy_rows: Sequence[dict[str, Any]] | None,
    *,
    cap: int = _MAX_MARKS,
    role: str = "enemy",
) -> list[AimMark]:
    """Place synthetic combat-sprite marks from LLM1 bbox entries."""
    from loop.agent.entities import is_prompt_instance_id

    stamp_role = (role or "enemy").strip().lower() or "enemy"
    prefix = "player" if stamp_role == "player" else "enemy"
    out = list(marks or ())

    def compatible(mark: AimMark) -> bool:
        mark_role = str(mark.role or "").strip().lower()
        return mark_role == stamp_role or (stamp_role == "enemy" and mark_role == "target")

    def overlaps(mark: AimMark, bounds: tuple[float, float, float, float]) -> bool:
        bx0, by0, bx1, by1 = bounds
        return (
            max(float(mark.x0), bx0) < min(float(mark.x1), bx1)
            and max(float(mark.y0), by0) < min(float(mark.y1), by1)
        ) or (
            float(mark.x0) <= (bx0 + bx1) * .5 <= float(mark.x1)
            and float(mark.y0) <= (by0 + by1) * .5 <= float(mark.y1)
        )

    for enemy_index, row in enumerate(enemy_rows or [], start=1):
        if not isinstance(row, dict):
            continue
        row_role = str(row.get("role") or stamp_role).strip().lower()
        if stamp_role == "enemy" and row_role not in {"enemy", "target", "unknown_character", "character"}:
            continue
        if stamp_role == "player" and row_role not in {"player", "character"}:
            continue
        default_label = "player" if stamp_role == "player" else "unidentified_enemy"
        label = str(row.get("label") or default_label).strip() or default_label
        bbox_raw = row.get("bbox")
        parts: list[float] = []
        if isinstance(bbox_raw, str) and bbox_raw.strip():
            try:
                parts = [float(p) for p in bbox_raw.split(",") if p.strip()]
            except ValueError:
                parts = []
        elif isinstance(bbox_raw, (list, tuple)):
            try:
                parts = [float(p) for p in bbox_raw[:4]]
            except (TypeError, ValueError):
                parts = []
        if len(parts) < 4:
            continue
        x0, y0, x1, y1 = parts[:4]
        if max(x0, y0, x1, y1) > 1.0:
            x0, y0, x1, y1 = x0 / GRID, y0 / GRID, x1 / GRID, y1 / GRID
        x = (x0 + x1) * 0.5
        y = (y0 + y1) * 0.5
        raw_id = str(row.get("entity_id") or row.get("id") or "").strip()
        stamp_id = "" if is_prompt_instance_id(raw_id) else raw_id
        compatible_hits = [
            mark for mark in out
            if compatible(mark) and overlaps(mark, (x0, y0, x1, y1))
        ]
        if len(compatible_hits) == 1:
            nearby = compatible_hits[0]
            # LLM instance tokens are aliases, never executable object IDs.
            # Keep the observed mark untouched; the current action surface
            # assigns canonical IDs and the store may bind an alias afterward.
            if not stamp_id and raw_id:
                continue
            out = [
                _copy_mark(
                    mark,
                    label=label,
                    object_id=stamp_id or str(mark.object_id or f"{prefix}_{enemy_index}"),
                    role=stamp_role,
                    source=f"{mark.source}+llm1-bbox" if mark.source else "cv+llm1-bbox",
                    confidence=max(mark.confidence, 0.72),
                ) if mark.id == nearby.id else mark
                for mark in out
            ]
            continue
        # A same-label card/control/unknown mark is not enemy evidence and
        # cannot suppress a current typed enemy bbox.  Multiple compatible
        # objects are ambiguous and fail closed rather than picking one.
        if len(compatible_hits) > 1:
            continue
        if len(out) >= cap:
            break
        out.append(
            _synthetic_mark(
                mid=len(out) + 1,
                label=label,
                x=x,
                y=y,
                score=0.72,
            )
        )
        out[-1] = _copy_mark(
            out[-1],
            object_id=stamp_id or f"{prefix}_{enemy_index}",
            role=stamp_role,
            source=str(row.get("source") or "llm1-bbox"),
            confidence=0.72,
        )
    return out


def apply_hand_bboxes(
    marks: Sequence[AimMark] | None,
    hand_rows: Sequence[dict[str, Any]] | None,
    *,
    cap: int = _MAX_MARKS,
) -> list[AimMark]:
    """Promote uniquely matched, current-frame hand candidates to cards.

    Typed in-hand geometry may create a click target only when there is no
    current-frame candidate to bind. Unique-match still fail-closes when
    candidates exist but are missing, ambiguous, or not 1:1.
    """
    out = list(marks or ())

    def in_full_hand(mark: AimMark) -> bool:
        return (
            _HAND_X0 <= mark.x0 <= mark.x1 <= _HAND_X1
            and HAND_Y <= mark.y0 <= mark.y1 <= 1.0
            and in_hand_band(mark.x, mark.y)
        )

    def current_card_candidate(mark: AimMark) -> bool:
        if not in_full_hand(mark):
            return False
        if (mark.role or "").strip().lower() not in {"hand_candidate", "card"}:
            return False
        # Cached cards must be re-observed before they are executable again.
        return "cache" not in str(mark.source or "").lower()

    def geometry_matches(mark: AimMark, proposal: AimMark) -> bool:
        # Fanned cards physically overlap their neighbours. Require substantial
        # agreement rather than any intersection, then enforce a one-to-one
        # match across the whole typed hand below.
        return _mark_iou(mark, proposal) >= 0.30 and abs(mark.x - proposal.x) <= 0.10

    proposals: list[tuple[int, AimMark]] = []
    for card_index, row in enumerate(hand_rows or (), start=1):
        if not isinstance(row, dict):
            continue
        label = str(row.get("label") or "").strip()
        bbox_raw = row.get("bbox")
        try:
            parts = (
                [float(p) for p in bbox_raw.split(",") if p.strip()]
                if isinstance(bbox_raw, str)
                else [float(p) for p in bbox_raw[:4]]
            )
        except (TypeError, ValueError):
            continue
        if not label or len(parts) < 4:
            continue
        x0, y0, x1, y1 = parts[:4]
        if max(x0, y0, x1, y1) > 1.0:
            x0, y0, x1, y1 = x0 / GRID, y0 / GRID, x1 / GRID, y1 / GRID
        x, y = (x0 + x1) * 0.5, (y0 + y1) * 0.5
        if not (
            _HAND_X0 <= x0 <= x1 <= _HAND_X1
            and HAND_Y <= y0 <= y1 <= 1.0
            and in_hand_band(x, y)
        ) or _is_end_turn_label(label):
            continue
        proposal = AimMark(
            id=0, x=x, y=y, x0=x0, y0=max(HAND_Y, y0), x1=x1, y1=min(1.0, y1),
            label=label, score=0.8,
            object_id=str(row.get("entity_id") or row.get("id") or f"card_{card_index}"),
            role="card", source=str(row.get("source") or "llm1-bbox"), confidence=0.8,
        )
        proposals.append((card_index, proposal))

    candidate_indices = [
        index for index, mark in enumerate(out) if current_card_candidate(mark)
    ]
    row_hits: dict[int, list[int]] = {
        row_index: [
            index for index in candidate_indices
            if geometry_matches(out[index], proposal)
        ]
        for row_index, proposal in proposals
    }
    candidate_hits: dict[int, list[int]] = {
        index: [
            row_index for row_index, proposal in proposals
            if geometry_matches(out[index], proposal)
        ]
        for index in candidate_indices
    }
    for row_index, proposal in proposals:
        hits = row_hits[row_index]
        if len(hits) != 1 or len(candidate_hits[hits[0]]) != 1:
            continue
        index = hits[0]
        match = out[index]
        incoming_unknown = _compact_label(proposal.label) in {
            "unknown", "unknown card", "unknown_card", "icon", "card"
        }
        grounded_ocr = (
            "ocr-hand" in str(match.source or "")
            and not _is_placeholder_label(match.label)
            and _compact_label(match.label) not in {"unknown", "unknown card", "unknown_card"}
        )
        resolved_label = match.label if incoming_unknown or grounded_ocr else proposal.label
        out[index] = _copy_mark(
            match, label=resolved_label, object_id=proposal.object_id, role="card",
            source=f"{match.source}+llm1-bbox" if match.source else "llm1-bbox",
            confidence=max(match.confidence, 0.8),
        )
    if not candidate_indices:
        for _, proposal in proposals:
            if len(out) >= cap:
                break
            out.append(proposal)
    return [_copy_mark(mark, mid=i) for i, mark in enumerate(out[:cap], start=1)]


def _slot_xs(n: int, *, x0: float, x1: float) -> list[float]:
    if n <= 0:
        return []
    if n == 1:
        return [(x0 + x1) * 0.5]
    return [x0 + (x1 - x0) * i / (n - 1) for i in range(n)]


def _claimable_enemy_slot(mark: AimMark, claimed: set[int]) -> bool:
    if mark.id in claimed:
        return False
    if not in_combat_sprite_band(mark.y, mark.x):
        return False
    # Left combat lane is the hero. Do not bind enemy names onto it.
    if float(mark.x) <= 0.36:
        return False
    if (mark.role or "").strip().lower() == "player":
        return False
    if _is_end_turn_label(mark.label) or label_kind(mark.label) in {"progress", "danger"}:
        return False
    lab = _compact_label(mark.label)
    if lab and not _is_placeholder_label(mark.label) and not _ocr_junk(mark.label):
        # Already a named unit — only claim placeholders / empty for new names.
        return False
    return True


def _claimable_hand_slot(mark: AimMark, claimed: set[int]) -> bool:
    if mark.id in claimed:
        return False
    if (mark.role or "").strip().lower() != "card" or not mark.object_id:
        return False
    if not (
        _HAND_X0 <= mark.x0 <= mark.x1 <= _HAND_X1
        and HAND_Y <= mark.y0 <= mark.y1 <= 1.0
        and in_hand_band(mark.x, mark.y)
    ):
        return False
    if _is_end_turn_label(mark.label) or label_kind(mark.label) in {"progress", "danger"}:
        return False
    lab = _compact_label(mark.label)
    if lab and not _is_placeholder_label(mark.label) and not _ocr_junk(mark.label):
        return False
    return True


def ground_combat_labels(
    marks: Sequence[AimMark] | None,
    *,
    enemies: Sequence[Any] | None = None,
    hand: Sequence[Any] | None = None,
    screen: str | None = None,
    cap: int = _MAX_MARKS,
) -> list[AimMark]:
    """Attach semantic names only to already observed geometry.

    Labels are never allowed to manufacture executable coordinates. Duplicate
    hand names are retained and matched as separate instances.
    """
    base = strip_misplaced_progress(list(marks or ()))
    enemy_names = combat_name_list(enemies)
    hand_names = combat_name_list(hand)
    kind = (screen or "").strip().lower()
    if not enemy_names and not hand_names:
        return [
            _copy_mark(m, mid=i) for i, m in enumerate(base[: max(1, cap)], start=1)
        ] if base else []
    if kind and kind not in {"combat", "tutorial", "other"}:
        return [
            _copy_mark(m, mid=i) for i, m in enumerate(base[: max(1, cap)], start=1)
        ] if base else []

    claimed: set[int] = set()
    out = list(base)

    def _unmatched(names: list[str], *, role: str) -> list[str]:
        missing: list[str] = []
        for name in names:
            hit = next(
                (
                    mark for mark in out
                    if mark.id not in claimed
                    and _compact_label(mark.label) == _compact_label(name)
                    and ((role == "card" and _claimable_hand_slot(mark, claimed)) or
                         (role == "enemy" and in_combat_sprite_band(mark.y, mark.x)))
                ),
                None,
            )
            if hit is None:
                missing.append(name)
            else:
                claimed.add(hit.id)
        return missing

    missing_enemies = _unmatched(enemy_names, role="enemy")
    if missing_enemies:
        slots = [m for m in out if _claimable_enemy_slot(m, claimed)]
        slots.sort(key=lambda m: (m.x, m.y))
        for name, mark in zip(missing_enemies, slots):
            claimed.add(mark.id)
            out = [
                _copy_mark(
                    m, label=name, score=max(m.score, 0.7), role="enemy",
                    source=m.source or "semantic-bind", confidence=max(m.confidence, 0.7),
                ) if m.id == mark.id else m
                for m in out
            ]

    missing_hand = _unmatched(hand_names, role="card")
    if missing_hand:
        slots = [m for m in out if _claimable_hand_slot(m, claimed)]
        slots.sort(key=lambda m: m.x)
        for name, mark in zip(missing_hand, slots):
            claimed.add(mark.id)
            out = [
                _copy_mark(
                    m, label=name, score=max(m.score, 0.65), role="card",
                    source=m.source or "semantic-bind", confidence=max(m.confidence, 0.65),
                ) if m.id == mark.id else m
                for m in out
            ]

    out = strip_misplaced_progress(out)
    return [_copy_mark(m, mid=i) for i, m in enumerate(out[: max(1, cap)], start=1)]


_NODE_LABEL_RE = re.compile(r"^node_\d+$", re.I)
_MAP_HUD_Y = 0.85
_MAP_LEGEND_X_FALLBACK = 0.78


def number_spatial_nodes(
    marks: Sequence[AimMark] | list[AimMark],
    *,
    screen: str | None = None,
    ocr_words: Sequence[AimMark] | None = None,
) -> list[AimMark]:
    """Relabel map placeholder marks as node_0..N for VLM1 detector candidates."""
    kind = (screen or "").strip().lower()
    if kind != "map":
        return list(marks)
    xmin = _legend_xmin(list(ocr_words or ()))
    if xmin is None:
        xmin = _MAP_LEGEND_X_FALLBACK

    def _spatial_candidate(mark: AimMark) -> bool:
        if mark.y < 0.12 or mark.y > _MAP_HUD_Y:
            return False
        if mark.x >= xmin:
            return False
        lab = (mark.label or "").strip()
        if lab and not _is_placeholder_label(lab) and not _NODE_LABEL_RE.match(lab):
            return False
        return True

    out = list(marks)
    candidates = [m for m in out if _spatial_candidate(m)]
    candidates.sort(key=lambda m: (m.y, m.x))
    relabeled = {m.id: f"node_{i}" for i, m in enumerate(candidates)}
    result: list[AimMark] = []
    for mark in out:
        label = relabeled.get(mark.id)
        result.append(_copy_mark(mark, label=label) if label else mark)
    return [
        _copy_mark(m, mid=i) for i, m in enumerate(result[: max(1, _MAX_MARKS)], start=1)
    ]


def ground_spatial_labels(
    marks: Sequence[AimMark] | None,
    controls: Sequence[dict[str, Any]] | None,
    *,
    screen: str | None = None,
    cap: int = _MAX_MARKS,
) -> list[AimMark]:
    """Attach VLM1 high-confidence node_N controls onto aim marks; strip unconfirmed labels."""
    kind = (screen or "").strip().lower()
    base = list(marks or ())
    if kind != "map" or not base:
        return [_copy_mark(m, mid=i) for i, m in enumerate(base[: max(1, cap)], start=1)]

    allowed_high: set[str] = set()
    node_labels: list[str] = []
    for row in controls or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("confidence") or "").strip().lower() != "high":
            continue
        label = str(row.get("label") or "").strip()
        if not label:
            continue
        allowed_high.add(_compact_label(label))
        if _NODE_LABEL_RE.match(label):
            node_labels.append(label)

    out = list(base)
    claimed: set[int] = set()
    numbered = sorted(
        [m for m in out if _NODE_LABEL_RE.match(m.label or "")],
        key=lambda m: (m.y, m.x),
    )

    for label in node_labels:
        compact = _compact_label(label)
        matched = next((m for m in out if _compact_label(m.label) == compact), None)
        if matched is None and numbered:
            try:
                idx = int(compact.split("_", 1)[1])
            except (IndexError, ValueError):
                idx = -1
            if 0 <= idx < len(numbered):
                candidate = numbered[idx]
                if candidate.id not in claimed:
                    matched = candidate
        if matched is None:
            slots = [
                m
                for m in out
                if m.id not in claimed
                and 0.12 <= m.y <= _MAP_HUD_Y
                and (
                    _is_placeholder_label(m.label)
                    or _NODE_LABEL_RE.match(m.label or "")
                )
            ]
            if slots:
                matched = min(slots, key=lambda m: (m.y, m.x))
        if matched is None:
            continue
        claimed.add(matched.id)
        out = [
            _copy_mark(m, label=label, score=max(m.score, 0.75), role="button")
            if m.id == matched.id
            else m
            for m in out
        ]

    cleaned: list[AimMark] = []
    for mark in out:
        lab = (mark.label or "").strip()
        if not lab:
            cleaned.append(mark)
            continue
        compact = _compact_label(lab)
        if compact in allowed_high:
            cleaned.append(mark)
            continue
        if _is_placeholder_label(lab) or _NODE_LABEL_RE.match(lab):
            cleaned.append(_copy_mark(mark, label=""))
            continue
        cleaned.append(_copy_mark(mark, label=""))
    return [_copy_mark(m, mid=i) for i, m in enumerate(cleaned[: max(1, cap)], start=1)]


def _hud_energy(hud: dict | None) -> float | None:
    if not hud:
        return None
    value = hud.get("energy")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _drop_label_fragments(marks: list[AimMark]) -> list[AimMark]:
    labels = {_compact_label(m.label) for m in marks if (m.label or "").strip()}
    out: list[AimMark] = []
    for mark in marks:
        lab = _compact_label(mark.label)
        if lab and any(lab != other and lab in other.split() for other in labels):
            continue
        out.append(mark)
    return out


def _label_inspect_arrows(marks: list[AimMark]) -> list[AimMark]:
    """Two mid-side placeholders on a card inspect are Arrow, not Close. Drop center art."""
    sides = [
        m
        for m in marks
        if _is_placeholder_label(m.label)
        and 0.35 <= m.y <= 0.65
        and (m.x <= 0.16 or m.x >= 0.84)
    ]
    left = [m for m in sides if m.x <= 0.16]
    right = [m for m in sides if m.x >= 0.84]
    if not left or not right:
        return marks
    side_ids = {m.id for m in left} | {m.id for m in right}
    drop: set[int] = set()
    for mark in marks:
        if mark.id in side_ids:
            continue
        area = (mark.x1 - mark.x0) * (mark.y1 - mark.y0)
        if (
            _is_placeholder_label(mark.label)
            and 0.28 <= mark.x <= 0.72
            and 0.18 <= mark.y <= 0.78
            and area >= 0.08
        ):
            drop.add(mark.id)
    out: list[AimMark] = []
    for mark in marks:
        if mark.id in drop:
            continue
        if mark.id in side_ids:
            out.append(_copy_mark(mark, label="Arrow"))
        else:
            out.append(mark)
    return out


def _label_lateral_nav(
    marks: list[AimMark],
    *,
    screen: str | None = None,
) -> list[AimMark]:
    """Mid-height side placeholders on paginated overlays → Prev / Next."""
    kind = (screen or "").strip().lower()
    if kind == "combat":
        return marks
    tutorial_nav = kind == "tutorial"

    def _nav_candidate(mark: AimMark) -> bool:
        if not (0.25 <= mark.y <= 0.75):
            return False
        if _is_placeholder_label(mark.label):
            return True
        # _label_inspect_arrows runs first and mints Arrow; reclaim on tutorial.
        return tutorial_nav and _compact_label(mark.label) == "arrow"

    pool = [m for m in marks if _nav_candidate(m)]
    left = [m for m in pool if m.x <= 0.22]
    right = [m for m in pool if m.x >= 0.78]
    out = list(marks)
    if left:
        pick = min(left, key=lambda m: (m.x, m.y))
        if _compact_label(pick.label) != "prev":
            out = _relabel(out, pick.id, "Prev")
    if right:
        pick = max(right, key=lambda m: (m.x, -m.y))
        if _compact_label(pick.label) != "next":
            out = _relabel(out, pick.id, "Next")
    return out


def _scope_overlay_marks(
    marks: list[AimMark],
    *,
    screen: str | None,
    dialog: bool,
) -> list[AimMark]:
    """Drop overlay noise without removing valid play/nav targets."""
    kind = (screen or "").strip().lower()
    if kind not in {"tutorial", "combat", "map", "other"} and not dialog:
        return marks
    out: list[AimMark] = []
    for mark in marks:
        if (
            _is_placeholder_label(mark.label)
            and 0.25 < mark.x < 0.75
            and 0.18 < mark.y < 0.62
        ):
            area = (mark.x1 - mark.x0) * (mark.y1 - mark.y0)
            if area > 0.12:
                continue
        out.append(mark)
    return out if out else list(marks)


def _fill_is_play(mark: AimMark) -> bool:
    lab = _compact_label(mark.label)
    if not lab or lab == "end turn" or _is_placeholder_label(mark.label):
        return False
    if lab in _DIALOG_BUTTONS or lab in _OVERLAY_DISMISS:
        return False
    return True


def _hud_chrome(
    mark: AimMark,
    *,
    lexicon: object | None = None,
    screen: str | None = None,
) -> bool:
    """Top gold/potion strip is not a click target unless a learned control sits there."""
    if mark.y > _HUD_CHROME_Y:
        return False
    lookup = getattr(lexicon, "label_at", None) if lexicon is not None else None
    if callable(lookup) and lookup(mark.x, mark.y, screen=screen):
        return False
    compact = _compact_label(mark.label)
    if _is_placeholder_label(mark.label) or _ocr_junk(mark.label):
        return True
    return compact in {"points", "obtained", "share", "hp"}


def _bottom_chrome(mark: AimMark) -> bool:
    if mark.y < 0.96:
        return False
    return _compact_label(mark.label) in {"cards", "card", "upgrades"}


def _cv_too_big(mark: AimMark) -> bool:
    return (mark.x1 - mark.x0) > _CV_MAX_EDGE or (mark.y1 - mark.y0) > _CV_MAX_EDGE


def is_menu_label(label: str) -> bool:
    if label_kind(label) in {"progress", "danger"}:
        return True
    return _compact_label(label) in _MENU_ONE_CLICK


def _steer_label_ok(label: str, *, locked_card: bool = False) -> bool:
    compact = _compact_label(label)
    if not compact or compact in _STEER_SKIP_LABELS:
        return False
    if len(compact.split()) > 3:
        return False
    if locked_card and compact in _LOCKED_FLAVOR:
        return False
    return True


def _is_menu_label(label: str) -> bool:
    return is_menu_label(label)


def _labels_have_dialog(marks: list[AimMark]) -> bool:
    labels = {_compact_label(m.label) for m in marks if (m.label or "").strip()}
    if labels & _DIALOG_HINTS:
        return True
    return "yes" in labels and "no" in labels


def _mark_with_label(marks: tuple[AimMark, ...] | list[AimMark], name: str) -> AimMark | None:
    want = _compact_label(name)
    for mark in marks:
        if _compact_label(mark.label) == want:
            return mark
    return None


def _is_placeholder_label(label: str) -> bool:
    compact = _compact_label(label)
    return not compact or compact in _PLACEHOLDER_LABELS


def _ocr_supports_label(
    ocr: Sequence[AimMark],
    label: str,
    x: float,
    y: float,
    *,
    radius: float = _OCR_VERIFY_R,
) -> bool:
    want = _compact_label(label)
    if not want:
        return False
    r2 = radius * radius
    for word in ocr:
        lab = _compact_label(word.label)
        if not lab:
            continue
        if lab != want and not _phrase_in(lab, want) and not _phrase_in(want, lab):
            continue
        if (float(word.x) - x) ** 2 + (float(word.y) - y) ** 2 <= r2:
            return True
    return False


def _needs_ocr_proof(label: str) -> bool:
    compact = _compact_label(label)
    return compact in _OCR_GATED_LABELS


def _corner_heuristic_ok(
    mark: AimMark,
    label: str,
    *,
    screen: str | None = None,
) -> bool:
    """Icon-only pennants labeled without OCR — allowed only in corner bands."""
    compact = _compact_label(label)
    if compact == "confirm" and mark.x >= _CORNER_CONFIRM_X and mark.y >= _CORNER_CONFIRM_Y:
        return True
    # The map toolbar occupies this corner in STS2.  It is not a Back affordance
    # unless OCR independently verifies the label below.
    if (
        compact == "back"
        and (screen or "").strip().lower() != "map"
        and mark.x <= 0.12
        and mark.y >= 0.80
    ):
        return True
    return False


def _lateral_nav_ok(mark: AimMark, label: str) -> bool:
    """Tutorial/combat paginator chevrons — icon-only, no OCR word."""
    compact = _compact_label(label)
    if compact == "prev" and mark.x <= 0.22 and 0.25 <= mark.y <= 0.75:
        return True
    if compact == "next" and mark.x >= 0.78 and 0.25 <= mark.y <= 0.75:
        return True
    return False


def _strip_unverified_menu_labels(
    marks: list[AimMark],
    ocr: Sequence[AimMark],
    *,
    screen: str | None = None,
) -> list[AimMark]:
    """Drop lexicon/heuristic menu labels that OCR did not read near this frame."""
    out: list[AimMark] = []
    for mark in marks:
        if not _needs_ocr_proof(mark.label):
            out.append(mark)
            continue
        if _corner_heuristic_ok(mark, mark.label, screen=screen):
            out.append(mark)
            continue
        if _lateral_nav_ok(mark, mark.label):
            out.append(mark)
            continue
        if _ocr_supports_label(ocr, mark.label, mark.x, mark.y):
            out.append(mark)
            continue
        out.append(_copy_mark(mark, label=""))
    return out


def is_legend_label(label: str) -> bool:
    compact = "".join(ch for ch in (label or "").lower() if ch.isalnum())
    return compact in _LEGEND_WORDS


_LISTING_MARK = re.compile(r"(?P<id>\d+)@(?P<x>\d+),(?P<y>\d+)(?::(?P<label>\S+))?")


def parse_listing(text: str) -> tuple[AimMark, ...]:
    """Rebuild marks from `AimGuide.listing()` (ids, 0-1000 cells, labels)."""
    marks: list[AimMark] = []
    for match in _LISTING_MARK.finditer(text or ""):
        mid = int(match.group("id"))
        x = int(match.group("x")) / GRID
        y = int(match.group("y")) / GRID
        label = (match.group("label") or "").replace("_", " ")
        marks.append(
            AimMark(
                id=mid,
                x=x,
                y=y,
                x0=max(0.0, x - 0.03),
                y0=max(0.0, y - 0.03),
                x1=min(1.0, x + 0.03),
                y1=min(1.0, y + 0.03),
                label=label,
            )
        )
    return tuple(marks)


def gui_click_marks(image: Image.Image, *, cap: int = _MAX_MARKS) -> list[AimMark]:
    """YOLO interactable boxes. Empty when the optional GUI extra is not loaded."""
    from loop.os.gui import detect_gui_marks

    try:
        return list(detect_gui_marks(image, cap=cap) or [])
    except Exception:  # noqa: BLE001 — overlay must never abort collect
        return []


def build_marks(
    image: Image.Image,
    *,
    cap: int = _MAX_MARKS,
    gui_marks: list[AimMark] | None = None,
    words: list[AimMark] | None = None,
    cv_marks: list[AimMark] | None = None,
    lexicon: object | None = None,
    screen: str | None = None,
    proposal_backend: Any | None = None,
    layout_adapter: LayoutAdapter | None = None,
) -> tuple[AimMark, ...]:
    cv = list(cv_marks) if cv_marks is not None else detect_marks(image, cap=cap)
    ocr = list(words) if words is not None else ocr_click_marks(image, cap=cap)
    gui = list(gui_marks) if gui_marks is not None else gui_click_marks(image, cap=cap)
    if proposal_backend is not None:
        try:
            external = list(proposal_backend.propose(image, screen=screen, cap=cap) or [])
        except Exception:  # noqa: BLE001 — optional detector must fail closed
            external = []
        gui.extend(
            _copy_mark(mark, source=mark.source or "proposal-backend")
            for mark in external
            if isinstance(mark, AimMark)
        )
    adapter = layout_adapter or default_layout_adapter()
    ocr, gui, cv = _strip_legend(ocr, cv, gui)
    marks = _merge_aim_marks(
        ocr, cv, cap=cap, gui=gui, lexicon=lexicon, screen=screen, layout_adapter=adapter
    )
    hand = propose_hand_cards(
        ocr, screen=screen, cap=min(10, cap), layout_adapter=adapter
    )
    if hand:
        # Card proposals outrank generic GUI/CV blobs in the hand crop. Keep
        # non-overlapping chrome/board marks, then publish one mark per card.
        hand_y = adapter.hand_y
        kept = [
            mark for mark in marks
            if not any(
                _mark_iou(mark, card) >= 0.08
                or (card.x0 <= mark.x <= card.x1 and mark.y >= hand_y)
                for card in hand
            )
        ]
        marks = hand + kept
        marks = [_copy_mark(mark, mid=i) for i, mark in enumerate(marks[:cap], start=1)]
    if adapter.enables_combat_role_stamps(screen):
        marks = [
            _copy_mark(mark, role="button", source=mark.source or "ocr-chrome")
            if _is_end_turn_label(mark.label)
            else _copy_mark(mark, role="board_candidate", source=mark.source or "cv-board")
            if adapter.in_combat_sprite_band(mark.y, mark.x) and _is_placeholder_label(mark.label)
            else mark
            for mark in marks
        ]
    guessed = _lexicon_screen(marks, fallback=screen)
    relabeled = _relabel_from_lexicon(
        marks, lexicon, guessed, ocr=ocr, layout_adapter=adapter
    )
    region_ok = _strip_region_invalid_labels(
        list(relabeled), screen=screen, layout_adapter=adapter
    )
    return tuple(_strip_unverified_menu_labels(region_ok, ocr, screen=screen))


def dino_boxes_to_marks(boxes: Sequence[Any], *, cap: int = 12) -> list[AimMark]:
    """Convert Grounding DINO boxes to aim marks (normalized client coords)."""
    marks: list[AimMark] = []
    for box in boxes:
        label = str(getattr(box, "label", "") or "").strip()
        if not label:
            continue
        x0 = float(getattr(box, "x", 0.0))
        y0 = float(getattr(box, "y", 0.0))
        w = float(getattr(box, "w", 0.1))
        h = float(getattr(box, "h", 0.1))
        x1, y1 = x0 + w, y0 + h
        marks.append(
            AimMark(
                id=0,
                x=x0 + w * 0.5,
                y=y0 + h * 0.5,
                x0=x0,
                y0=y0,
                x1=x1,
                y1=y1,
                label=label,
                score=float(getattr(box, "score", 1.0) or 1.0),
            )
        )
        if len(marks) >= cap:
            break
    return marks


def dino_ground_labels(
    image: Image.Image,
    labels: Sequence[str],
    detector: Any | None,
    *,
    cap: int = 12,
) -> list[AimMark]:
    """Run DINO on a bounded query list (LLM1 allowed controls)."""
    if detector is None or image is None or not labels:
        return []
    family = str(getattr(detector, "family", "") or "")
    if family == "unavailable":
        return []
    query = [str(lab).strip() for lab in labels if str(lab or "").strip()][:cap]
    if not query:
        return []
    try:
        from loop.os.ground import _detector_detect

        boxes = _detector_detect(detector, image, query)
    except Exception:  # noqa: BLE001 — overlay hint only
        return []
    return dino_boxes_to_marks(boxes, cap=cap)


def _allowed_compact(allowed_controls: set[str] | None) -> set[str]:
    if not allowed_controls:
        return set()
    return {_compact_label(str(lab)) for lab in allowed_controls if str(lab or "").strip()}


def _dino_mark_verified(
    mark: AimMark,
    *,
    ocr: Sequence[AimMark],
    gui: Sequence[AimMark],
    existing: Sequence[AimMark],
) -> bool:
    if _ocr_supports_label(ocr, mark.label, mark.x, mark.y):
        return True
    for other in ocr:
        if _mark_iou(mark, other) >= _GUI_ATTACH_IOU:
            return True
    for other in gui:
        if _mark_iou(mark, other) >= _GUI_ATTACH_IOU:
            return True
    want = _compact_label(mark.label)
    for other in existing:
        if _compact_label(other.label) == want and _mark_iou(mark, other) >= _GUI_ATTACH_IOU:
            return True
    return False


def fuse_dino_marks(
    aim: AimGuide,
    dino_marks: Sequence[AimMark],
    *,
    ocr_words: Sequence[AimMark] | None = None,
    gui_marks: Sequence[AimMark] | None = None,
    allowed_controls: set[str] | None = None,
    screen: str | None = None,
) -> AimGuide:
    """Merge DINO boxes into aim marks; never keep lexicon-only menu labels."""
    ocr = list(ocr_words or ())
    gui = list(gui_marks or ())
    allowed = _allowed_compact(allowed_controls)
    merged: list[AimMark] = list(aim.marks)

    def _take(mark: AimMark) -> None:
        if any(abs(mark.x - o.x) < 0.04 and abs(mark.y - o.y) < 0.04 for o in merged):
            return
        merged.append(mark)

    for dmark in dino_marks:
        compact = _compact_label(dmark.label)
        if not compact:
            continue
        if allowed and compact not in allowed:
            continue
        if not _dino_mark_verified(dmark, ocr=ocr, gui=gui, existing=merged):
            if not _ocr_supports_label(ocr, dmark.label, dmark.x, dmark.y):
                continue
        _take(dmark)

    cleaned: list[AimMark] = []
    for mark in merged:
        lab = (mark.label or "").strip()
        if not lab:
            cleaned.append(mark)
            continue
        compact = _compact_label(lab)
        if allowed and compact not in allowed and _needs_ocr_proof(lab):
            cleaned.append(_copy_mark(mark, label=""))
            continue
        if (
            _needs_ocr_proof(lab)
            and not _corner_heuristic_ok(mark, lab, screen=screen)
            and not _lateral_nav_ok(mark, lab)
        ):
            if not _ocr_supports_label(ocr, lab, mark.x, mark.y) and not _dino_mark_verified(
                mark, ocr=ocr, gui=gui, existing=()
            ):
                cleaned.append(_copy_mark(mark, label=""))
                continue
        cleaned.append(mark)

    numbered = [_copy_mark(m, mid=i) for i, m in enumerate(cleaned[:_MAX_MARKS], start=1)]
    return AimGuide.from_marks(aim.image, numbered, last_xy=aim.last_xy)


def build_aim(
    image: Image.Image,
    *,
    last_xy: tuple[float, float] | None = None,
    cap: int = _MAX_MARKS,
    gui_marks: list[AimMark] | None = None,
    words: list[AimMark] | None = None,
    cv_marks: list[AimMark] | None = None,
    lexicon: object | None = None,
    screen: str | None = None,
    proposal_backend: Any | None = None,
    layout_adapter: LayoutAdapter | None = None,
) -> AimGuide:
    marks = build_marks(
        image,
        cap=cap,
        gui_marks=gui_marks,
        words=words,
        cv_marks=cv_marks,
        lexicon=lexicon,
        screen=screen,
        proposal_backend=proposal_backend,
        layout_adapter=layout_adapter,
    )
    painted = paint_aim(image, marks=marks, last_xy=last_xy)
    return AimGuide(marks=marks, last_xy=last_xy, image=painted)


def detect_marks(image: Image.Image, *, cap: int = _MAX_MARKS) -> list[AimMark]:
    """Compact clickable islands. Not a game-verb detector.

    Contrast is not the gap: bilinear downsample plus a single edge/chroma mask
    fuses dashed paths and paper grain into one giant component and drops it.
    Thin ink also averages away. Keep two un-ORed channels: min-pooled dark ink
    on a warm bright field, and compact colorful shapes that are not that field.
    """
    w, h = image.size
    if w < 8 or h < 8:
        return []
    mask_w = _MASK_W
    mask_h = max(8, int(round(mask_w * h / w)))
    rgb = image.convert("RGB")
    small = rgb.resize((mask_w, mask_h), Image.Resampling.BILINEAR)
    arr = np.asarray(small, dtype=np.float32)
    gray = arr.mean(axis=2)
    field_l = np.asarray(
        small.convert("L").filter(ImageFilter.GaussianBlur(6.0)), dtype=np.float32
    )
    chroma = arr.max(axis=2) - arr.min(axis=2)
    warm = arr[:, :, 0] > (arr[:, :, 2] + _WARM_GAP)
    field = (field_l > _PAPER_L) & warm
    full_gray = np.asarray(rgb.convert("L"), dtype=np.float32)
    gmin = _min_resize(full_gray, mask_w, mask_h)
    ink = ((field_l - gmin) > _INK_TAU) & field
    ui = (chroma > _UI_CHROMA) & (gray < _UI_MAX_L) & ~field
    color = chroma > _UI_CHROMA
    ink_boxes = _compact_boxes(ink, min_px=_INK_MIN_PX, width=mask_w, height=mask_h)
    solid_ink: list[tuple[int, int, int, int, int]] = []
    for x0, y0, x1, y1, n in ink_boxes:
        score = _score_mask_box(full_gray, x0, y0, x1, y1, mask_w, mask_h)
        if score < _INK_SOLID:
            continue
        solid_ink.append((x0, y0, x1, y1, max(n, int(score * 10))))
    ui_boxes = _compact_boxes(ui, min_px=_MIN_PX, width=mask_w, height=mask_h)
    ui_boxes.extend(_compact_boxes(color, min_px=_MIN_PX, width=mask_w, height=mask_h))
    ink_n = min(_INK_CAP, cap)
    ink_kept = _spread(
        _nms(solid_ink, iou=0.45, cap=ink_n * 3),
        cap=ink_n,
        min_frac=_SPREAD,
        width=mask_w,
        height=mask_h,
    )
    ui_kept = _spread(
        _nms(ui_boxes, iou=0.45, cap=max(4, cap) * 3),
        cap=max(0, cap - len(ink_kept)),
        min_frac=_SPREAD,
        width=mask_w,
        height=mask_h,
    )
    text = (gray > _TEXT_MIN_L) & ~field
    text_boxes = _compact_boxes(
        text,
        min_px=_TEXT_MIN_PX,
        width=mask_w,
        height=mask_h,
        max_aspect=_TEXT_ASPECT,
    )
    text_kept = _spread(
        _nms(text_boxes, iou=0.45, cap=max(4, cap) * 3),
        cap=max(4, min(8, cap)),
        min_frac=_SPREAD,
        width=mask_w,
        height=mask_h,
    )
    kept = _nms(text_kept + ink_kept + ui_kept, iou=0.45, cap=cap)
    kept.sort(key=lambda b: (b[1], b[0]))
    return _marks_from_boxes(
        kept,
        src_w=w,
        src_h=h,
        mask_w=mask_w,
        mask_h=mask_h,
        gray=full_gray,
    )


def ocr_click_marks(image: Image.Image, *, cap: int = _MAX_MARKS) -> list[AimMark]:
    """Word boxes from RapidOCR (Tesseract fallback). Menu labels first."""
    from loop.os.ocr import read_words, read_words_in_roi

    if cap < 1:
        return []
    w, h = image.size
    if w < 16 or h < 16:
        return []

    def _collect(words: list[Any]) -> list[AimMark]:
        found: list[AimMark] = []
        for word in words:
            label = _prefer_menu_label(word.text)
            if len(_compact_label(label)) < _OCR_MIN_LEN or _ocr_junk(label):
                continue
            if word.score < (_OCR_MIN_CONF / 100.0) and not _ocr_click_label(label):
                continue
            bw = word.x1 - word.x0
            bh = word.y1 - word.y0
            if bw * w < 8 or bh * h < 8:
                continue
            found.append(
                AimMark(
                    id=len(found) + 1,
                    x=float(word.x),
                    y=float(word.y),
                    x0=float(word.x0),
                    y0=float(word.y0),
                    x1=float(word.x1),
                    y1=float(word.y1),
                    label=label,
                    score=float(word.score),
                    source="ocr",
                )
            )
            if len(found) >= cap * 3:
                break
        return found

    try:
        words = read_words(image)
    except Exception:  # noqa: BLE001 — overlay must never abort collect
        return []
    found = _collect(words)
    have_menu = {_compact_label(m.label) for m in found if _needs_ocr_proof(m.label)}
    if len(have_menu) < 3:
        for box in _BUTTON_ROIS:
            try:
                roi_words = read_words_in_roi(image, box)
            except Exception:  # noqa: BLE001
                continue
            for mark in _collect(roi_words):
                key = _compact_label(mark.label)
                if key and key not in have_menu:
                    found.append(mark)
                    have_menu.add(key)
                if len(found) >= cap * 3:
                    break
            if len(found) >= cap * 3:
                break
    return found


def _mark_iou(a: AimMark, b: AimMark) -> float:
    ix0, iy0 = max(a.x0, b.x0), max(a.y0, b.y0)
    ix1, iy1 = min(a.x1, b.x1), min(a.y1, b.y1)
    iw, ih = max(0.0, ix1 - ix0), max(0.0, iy1 - iy0)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(1e-9, (a.x1 - a.x0) * (a.y1 - a.y0))
    area_b = max(1e-9, (b.x1 - b.x0) * (b.y1 - b.y0))
    return inter / (area_a + area_b - inter)


def _center_in(word: AimMark, box: AimMark) -> bool:
    return box.x0 <= word.x <= box.x1 and box.y0 <= word.y <= box.y1


def _copy_mark(
    mark: AimMark,
    *,
    mid: int | None = None,
    x: float | None = None,
    y: float | None = None,
    x0: float | None = None,
    y0: float | None = None,
    x1: float | None = None,
    y1: float | None = None,
    label: str | None = None,
    score: float | None = None,
    object_id: str | None = None,
    role: str | None = None,
    source: str | None = None,
    confidence: float | None = None,
) -> AimMark:
    return AimMark(
        id=mark.id if mid is None else mid,
        x=mark.x if x is None else x,
        y=mark.y if y is None else y,
        x0=mark.x0 if x0 is None else x0,
        y0=mark.y0 if y0 is None else y0,
        x1=mark.x1 if x1 is None else x1,
        y1=mark.y1 if y1 is None else y1,
        label=mark.label if label is None else label,
        score=mark.score if score is None else score,
        object_id=mark.object_id if object_id is None else object_id,
        role=mark.role if role is None else role,
        source=mark.source if source is None else source,
        confidence=mark.confidence if confidence is None else confidence,
    )


def _with_source(mark: AimMark, source: str) -> AimMark:
    if str(mark.source or "").strip():
        return mark
    return _copy_mark(mark, source=source)


def _proposal_overlap_reason(mark: AimMark, others: Sequence[AimMark]) -> str | None:
    for other in others:
        if abs(mark.x - other.x) < _NEAR_CENTER and abs(mark.y - other.y) < _NEAR_CENTER:
            return "overlap"
        if _mark_iou(mark, other) >= _FUSION_IOU:
            return "overlap"
    return None


def _attach_ocr_to_gui(
    ocr: list[AimMark],
    gui: list[AimMark],
    *,
    screen: str | None = None,
) -> tuple[list[AimMark], list[AimMark], list[AimMark]]:
    """Pair OCR words onto YOLO boxes. Click uses the YOLO box; label uses the word."""
    used: set[int] = set()
    attached: list[AimMark] = []
    leftover_gui: list[AimMark] = []
    combat = (screen or "").strip().lower() == "combat"
    ranked = sorted(gui, key=lambda m: (-m.score, (m.x1 - m.x0) * (m.y1 - m.y0)))
    for box in ranked:
        best_i: int | None = None
        best_score = -1.0
        for i, word in enumerate(ocr):
            if i in used:
                continue
            if not _ocr_click_label(word.label) and (not combat or not _ocr_unit_label(word)):
                continue
            iou = _mark_iou(box, word)
            inside = _center_in(word, box)
            if iou < _GUI_ATTACH_IOU and not inside:
                continue
            score = iou + (0.15 if inside else 0.0)
            if label_kind(word.label) == "progress":
                score += 2.0
            if score > best_score:
                best_score = score
                best_i = i
        if best_i is None:
            leftover_gui.append(
                _with_source(
                    box if (box.label or "").strip() else _copy_mark(box, label="icon"),
                    "gui",
                )
            )
            continue
        used.add(best_i)
        word = ocr[best_i]
        attached.append(
            _copy_mark(
                box,
                label=word.label,
                score=max(box.score, word.score),
                source="ocr-gui",
            )
        )
    leftover_ocr = [word for i, word in enumerate(ocr) if i not in used]
    return attached, leftover_ocr, leftover_gui


def _lexicon_screen(marks: list[AimMark], fallback: str | None = None) -> str:
    from loop.memory.lexicon import screen_from_labels

    return screen_from_labels((m.label for m in marks), fallback=fallback)


def _relabel_from_lexicon(
    marks: list[AimMark],
    lexicon: object | None,
    screen: str | None,
    *,
    ocr: Sequence[AimMark] | None = None,
    layout_adapter: LayoutAdapter | None = None,
) -> list[AimMark]:
    lookup = getattr(lexicon, "label_for", None) if lexicon is not None else None
    if not callable(lookup):
        return marks
    ocr_marks = list(ocr or ())
    out: list[AimMark] = []
    for mark in marks:
        if not _is_placeholder_label(mark.label):
            out.append(mark)
            continue
        learned = lookup(screen, mark.x, mark.y)
        if not learned:
            out.append(mark)
            continue
        if not _eligible_label_for_region(
            mark, learned, screen=screen, layout_adapter=layout_adapter
        ):
            out.append(mark)
            continue
        if (
            _needs_ocr_proof(learned)
            and not _corner_heuristic_ok(mark, learned, screen=screen)
            and not _ocr_supports_label(ocr_marks, learned, mark.x, mark.y)
        ):
            out.append(mark)
            continue
        out.append(_copy_mark(mark, label=learned))
    return out


def _merge_aim_marks(
    ocr: list[AimMark],
    cv: list[AimMark],
    *,
    cap: int,
    gui: list[AimMark] | None = None,
    lexicon: object | None = None,
    screen: str | None = None,
    audit: dict[str, Any] | None = None,
    layout_adapter: LayoutAdapter | None = None,
) -> list[AimMark]:
    picked: list[AimMark] = []
    rejected: list[dict[str, Any]] = []

    def _reject(mark: AimMark, reason: str) -> None:
        if audit is None or len(rejected) >= 48:
            return
        rejected.append(
            {
                "source": str(mark.source or ""),
                "label": mark.label,
                "x": float(mark.x),
                "y": float(mark.y),
                "reason": reason,
            }
        )

    def _take(mark: AimMark) -> bool:
        reason = _proposal_overlap_reason(mark, picked)
        if reason is not None:
            _reject(mark, reason)
            return False
        picked.append(mark)
        return True

    ocr = [
        _with_source(m, "ocr")
        for m in ocr
        if not is_legend_label(m.label) and not _ocr_junk(m.label) and not _bottom_chrome(m)
    ]
    gui_in = [_with_source(m, "gui") for m in (gui or [])]
    gui_kept = [
        m
        for m in gui_in
        if not _cv_too_big(m) and not _hud_chrome(m, lexicon=lexicon, screen=screen)
    ]
    for mark in gui_in:
        if _cv_too_big(mark):
            _reject(mark, "too_big")
        elif _hud_chrome(mark, lexicon=lexicon, screen=screen):
            _reject(mark, "hud_chrome")
    attached, leftover_ocr, leftover_gui = _attach_ocr_to_gui(ocr, gui_kept, screen=screen)
    leftover_gui = [
        m for m in leftover_gui if not _hud_chrome(m, lexicon=lexicon, screen=screen)
    ]
    leftover_ocr = _drop_label_fragments([_with_source(m, "ocr") for m in leftover_ocr])
    dialog = _labels_have_dialog(ocr) or _labels_have_dialog(attached + leftover_gui)
    progress = [_with_source(m, "ocr-gui") for m in attached if label_kind(m.label) == "progress"]
    progress.extend(
        _with_source(m, "ocr")
        for m in leftover_ocr
        if label_kind(m.label) == "progress"
        and _unattached_ocr_click_label(m, screen=screen)
    )
    attached_other = [_with_source(m, "ocr-gui") for m in attached if label_kind(m.label) != "progress"]
    other_ocr = [
        _with_source(m, "ocr")
        for m in leftover_ocr
        if label_kind(m.label) != "progress"
        and _unattached_ocr_click_label(m, screen=screen)
        and not _bottom_chrome(m)
    ]
    named_gui = [m for m in leftover_gui if not _is_placeholder_label(m.label)]
    placeholder_gui = [m for m in leftover_gui if _is_placeholder_label(m.label)]
    gui_ranked = sorted(
        named_gui + placeholder_gui[:_GUI_PLACEHOLDER_CAP],
        key=lambda m: (-m.score, (m.x1 - m.x0) * (m.y1 - m.y0), m.x),
    )
    cv_all = [_with_source(m, "cv") for m in cv]
    cv_ranked = sorted(
        (m for m in cv_all if not _cv_too_big(m)),
        key=lambda m: (-m.score, -m.y, m.x),
    )
    for mark in cv_all:
        if _cv_too_big(mark):
            _reject(mark, "too_big")
    for mark in sorted(progress, key=lambda m: (m.y, m.x)):
        _take(mark)
        if len(picked) >= cap:
            break
    if len(picked) < cap:
        for mark in attached_other:
            _take(mark)
            if len(picked) >= cap:
                break
    if len(picked) < cap:
        for mark in sorted(other_ocr, key=lambda m: (m.y, m.x)):
            _take(mark)
            if len(picked) >= cap:
                break
    complementary_cv = [m for m in cv_ranked if _proposal_overlap_reason(m, picked) is None]
    reserve = min(_CV_COVERAGE_RESERVE, len(complementary_cv), max(0, cap - len(picked)))
    gui_room = max(0, cap - len(picked) - reserve)
    if gui_room and len(picked) < cap:
        taken_gui = 0
        for mark in gui_ranked:
            if taken_gui >= gui_room or len(picked) >= cap:
                if taken_gui >= gui_room:
                    _reject(mark, "budget")
                break
            if _take(mark):
                taken_gui += 1
    if len(picked) < cap:
        for mark in cv_ranked:
            _take(mark)
            if len(picked) >= cap:
                break
    numbered = [
        _copy_mark(m, mid=i)
        for i, m in enumerate(picked[:cap], start=1)
    ]
    labeled = _label_corner_icons(numbered, dialog=dialog, screen=screen)
    labeled = _drop_label_fragments(labeled)
    labeled = _label_inspect_arrows(labeled)
    labeled = _label_lateral_nav(labeled, screen=screen)
    labeled = _scope_overlay_marks(labeled, screen=screen, dialog=dialog)
    labeled = strip_misplaced_progress(labeled, layout_adapter=layout_adapter)
    out = [
        _copy_mark(m, mid=i)
        for i, m in enumerate(labeled[:cap], start=1)
    ]
    if audit is not None:
        sources: dict[str, int] = {}
        for mark in out:
            key = str(mark.source or "") or "unknown"
            sources[key] = sources.get(key, 0) + 1
        audit.clear()
        audit.update(
            {
                "raw_counts": {
                    "ocr": len(ocr),
                    "gui": len(gui_in),
                    "gui_kept": len(gui_kept),
                    "cv": len(cv_all),
                },
                "kept_sources": sources,
                "rejected": rejected,
                "cv_reserve": reserve,
                "cap": cap,
            }
        )
    return out


def _is_corner_confirm(label: str) -> bool:
    return (label or "").strip().lower() in _CORNER_CONFIRM


def _is_corner_ui(label: str) -> bool:
    return _compact_label(label) in _CORNER_UI


def _tighten_corner_control(mark: AimMark) -> AimMark:
    """Keep Confirm/Embark/Back click on the glyph, not fused floor glow."""
    if not _is_corner_ui(mark.label):
        return mark
    y0 = mark.y0
    y1 = mark.y1
    height = y1 - y0
    if height > _CORNER_MAX_H:
        y1 = min(1.0, y0 + _CORNER_MAX_H)
        height = y1 - y0
    x = float(max(0.0, min(1.0, (mark.x0 + mark.x1) * 0.5)))
    y = float(max(0.0, min(1.0, y0 + height * _CORNER_CLICK_T)))
    return _copy_mark(mark, x=x, y=y, y0=y0, y1=y1)


def _relabel(marks: list[AimMark], mark_id: int, label: str) -> list[AimMark]:
    return [_copy_mark(m, label=label) if m.id == mark_id else m for m in marks]


def _label_unlabeled_corner(
    marks: list[AimMark],
    *,
    label: str,
    pool: list[AimMark],
    already: bool,
) -> list[AimMark]:
    if already or not pool:
        return marks
    if label == "Confirm":
        pick = max(pool, key=lambda m: (m.x, m.y))
    else:
        pick = min(pool, key=lambda m: (m.x, -m.y))
    if not _is_placeholder_label(pick.label):
        return marks
    return _relabel(marks, pick.id, label)


def _label_dialog_buttons(marks: list[AimMark], *, dialog: bool) -> list[AimMark]:
    """Bottom-center placeholder pair on a tutorial modal is Yes/No, not body OCR."""
    if not dialog or not marks:
        return marks
    if _mark_with_label(marks, "got it") is not None:
        return marks
    pool = [
        m
        for m in marks
        if _is_placeholder_label(m.label)
        and _DIALOG_X0 <= m.x <= _DIALOG_X1
        and m.y >= _DIALOG_Y
        and m.x < _CORNER_CONFIRM_X
    ]
    if len(pool) < 2:
        return marks
    left = min(pool, key=lambda m: (m.x, m.y))
    right = max(pool, key=lambda m: (m.x, -m.y))
    if left.id == right.id:
        return marks
    labeled = _relabel(marks, left.id, "No")
    return _relabel(labeled, right.id, "Yes")


def _label_corner_icons(
    marks: list[AimMark],
    *,
    dialog: bool = False,
    screen: str | None = None,
) -> list[AimMark]:
    """Icon-only check / back pennants have no OCR word."""
    if not marks:
        return marks
    screen_key = (screen or "").strip().lower()
    labeled = list(marks)
    if screen_key in {"select", "title", "other", ""}:
        labeled = _label_unlabeled_corner(
            labeled,
            label="Confirm",
            pool=[m for m in labeled if m.x >= _CORNER_CONFIRM_X and m.y >= _CORNER_CONFIRM_Y],
            already=any(_is_corner_confirm(m.label) for m in labeled),
        )
    if screen_key != "map":
        labeled = _label_unlabeled_corner(
            labeled,
            label="Back",
            pool=[m for m in labeled if m.x <= 0.12 and m.y >= 0.80],
            already=any(_compact_label(m.label) == "back" for m in labeled),
        )
    labeled = _label_dialog_buttons(labeled, dialog=dialog)
    return [_tighten_corner_control(m) for m in labeled]


def badge_box(
    mark: AimMark,
    *,
    width: int,
    height: int,
    tw: int,
    th: int,
    pad: int = 3,
) -> tuple[int, int, int, int, int, int]:
    """Badge rectangle centered on the click (cx, cy)."""
    cx = int(round(mark.x * (width - 1)))
    cy = int(round(mark.y * (height - 1)))
    bw, bh = tw + 2 * pad, th + 2 * pad
    bx0 = max(0, min(width - bw, cx - bw // 2))
    by0 = max(0, min(height - bh, cy - bh // 2))
    return bx0, by0, bx0 + bw, by0 + bh, cx, cy


def paint_aim(
    image: Image.Image,
    *,
    marks: tuple[AimMark, ...] | list[AimMark] = (),
    last_xy: tuple[float, float] | None = None,
) -> Image.Image:
    rgb = image.convert("RGB")
    w, h = rgb.size
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font = _font(max(11, min(22, w // 70)))
    small = _font(max(10, min(18, w // 80)))
    for g in range(0, GRID + 1, 100):
        x = int(round(g / GRID * (w - 1)))
        y = int(round(g / GRID * (h - 1)))
        draw.line([(x, 0), (x, h - 1)], fill=(255, 255, 255, 48), width=1)
        draw.line([(0, y), (w - 1, y)], fill=(255, 255, 255, 48), width=1)
    for g in range(0, GRID + 1, 200):
        x = int(round(g / GRID * (w - 1)))
        y = int(round(g / GRID * (h - 1)))
        label = str(g)
        _text(draw, (x + 2, 2), label, font=small, fill=(255, 230, 80, 220))
        _text(draw, (2, y + 2), label, font=small, fill=(255, 230, 80, 220))
    _text(draw, (w // 2 - 40, 2), "gx→ gy↓ 0-1000", font=small, fill=(255, 230, 80, 230))
    for mark in marks:
        badge = str(mark.id)
        tw, th = _text_size(draw, badge, font)
        pad = 3
        bx0, by0, bx1, by1, cx, cy = badge_box(
            mark, width=w, height=h, tw=tw, th=th, pad=pad
        )
        draw.rectangle((bx0, by0, bx1, by1), fill=(20, 180, 255, 230), outline=(0, 0, 0, 255))
        _text(draw, (bx0 + pad, by0 + pad - 1), badge, font=font, fill=(0, 0, 0, 255))
        r = 3
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=(20, 180, 255, 220), width=2)
        if mark.label:
            _text(draw, (bx1 + 3, by0), mark.label[:18], font=small, fill=(255, 255, 80, 230))
    if last_xy is not None:
        lx = int(round(max(0.0, min(1.0, last_xy[0])) * (w - 1)))
        ly = int(round(max(0.0, min(1.0, last_xy[1])) * (h - 1)))
        arm = max(10, min(w, h) // 28)
        col = (255, 60, 200, 240)
        draw.line([(lx - arm, ly), (lx + arm, ly)], fill=col, width=3)
        draw.line([(lx, ly - arm), (lx, ly + arm)], fill=col, width=3)
        _text(draw, (lx + 6, ly - 16), "last", font=small, fill=col)
    return Image.alpha_composite(rgb.convert("RGBA"), overlay).convert("RGB")


def bind_aim(action: ActionLog, guide: AimGuide | None) -> ActionLog:
    if guide is None:
        return action
    out = action.model_copy()
    if out.mark is None:
        token = (out.name or "").strip().lower()
        if token.startswith("mk_"):
            try:
                out.mark = int(token.split("_", 1)[1])
            except (TypeError, ValueError, IndexError):
                out.mark = None
    if out.mark is not None:
        hit = guide.by_id(int(out.mark))
        if hit is None:
            out.mark = None
            if (out.name or "").strip().lower().startswith("mk_"):
                out.name = None
        else:
            out.click_x = hit.x
            out.click_y = hit.y
    if out.target is not None:
        hit2 = guide.by_id(int(out.target))
        if hit2 is None:
            out.target = None
        else:
            out.click_x2 = hit2.x
            out.click_y2 = hit2.y
    if out.click_x is not None and out.click_y is not None:
        out.name = out.derive_name()
    return out


def _off_scheme_key(name: str | None) -> bool:
    token = (name or "").strip().lower()
    if not token:
        return False
    return not token.startswith(("mk_", "pt_", "hv_"))


def _nearest_mark(guide: AimGuide, x: float, y: float) -> AimMark | None:
    inside: list[AimMark] = []
    best: AimMark | None = None
    best_d = 1e9
    for mark in guide.marks:
        if mark.x0 <= x <= mark.x1 and mark.y0 <= y <= mark.y1:
            inside.append(mark)
        dist = (mark.x - x) ** 2 + (mark.y - y) ** 2
        if dist < best_d:
            best_d = dist
            best = mark
    if inside:
        return min(inside, key=lambda m: (m.x - x) ** 2 + (m.y - y) ** 2)
    if best is not None and best_d <= _SNAP_R2:
        return best
    return None


def clamp_to_overlay(action: ActionLog, guide: AimGuide | None) -> ActionLog:
    """Marks: is the control scheme. Drop keys and unlisted clicks while badges exist."""
    if guide is None or not guide.marks:
        return action
    out = action.model_copy()
    if _off_scheme_key(out.name):
        out.name = None
    if out.mark is not None and guide.by_id(int(out.mark)) is None:
        out.mark = None
        out.click_x = None
        out.click_y = None
    if out.target is not None and guide.by_id(int(out.target)) is None:
        out.target = None
        out.click_x2 = None
        out.click_y2 = None
    if out.mark is not None:
        hit = guide.by_id(int(out.mark))
        if hit is not None:
            out.click_x = hit.x
            out.click_y = hit.y
    elif out.click_x is not None and out.click_y is not None:
        hit = _nearest_mark(guide, float(out.click_x), float(out.click_y))
        if hit is not None:
            out.mark = hit.id
            out.click_x = hit.x
            out.click_y = hit.y
        else:
            out.click_x = None
            out.click_y = None
            out.hover = False
    if out.target is not None:
        hit2 = guide.by_id(int(out.target))
        if hit2 is not None:
            out.click_x2 = hit2.x
            out.click_y2 = hit2.y
    elif out.click_x2 is not None and out.click_y2 is not None:
        hit2 = _nearest_mark(guide, float(out.click_x2), float(out.click_y2))
        if hit2 is not None:
            out.target = hit2.id
            out.click_x2 = hit2.x
            out.click_y2 = hit2.y
        else:
            out.click_x2 = None
            out.click_y2 = None
            out.target = None
    if out.click_x is not None and out.click_y is not None:
        out.name = out.derive_name()
    else:
        out.name = None
    return out


def needs_tooltip(label: str) -> bool:
    """Unlabeled / placeholder / unknown-entity Marks get a tooltip hover before a click."""
    if _is_unknown_entity_label(label):
        return True
    if _compact_label(label) == "arrow":
        return False
    if is_menu_label(label):
        return False
    kind = label_kind(label)
    if kind in {"progress", "danger"}:
        return False
    return _is_placeholder_label(label)


def prefer_hover(
    action: ActionLog,
    guide: AimGuide | None,
    *,
    hovered: frozenset[int] | set[int] | None = None,
) -> ActionLog:
    """First contact with an :icon is hover so the tooltip can name it."""
    if guide is None or not guide.marks:
        return action
    if action.hover:
        return action
    if action.target is not None or (
        action.click_x2 is not None and action.click_y2 is not None
    ):
        return action
    if action.mark is None:
        return action
    mid = int(action.mark)
    if hovered and mid in hovered:
        return action
    hit = guide.by_id(mid)
    if hit is None or not needs_tooltip(hit.label):
        return action
    out = action.model_copy(update={"hover": True})
    out.target = None
    out.click_x2 = None
    out.click_y2 = None
    out.name = out.derive_name()
    return out


def _guide_has_locked(guide: AimGuide) -> bool:
    return any(_compact_label(m.label) == "locked" for m in guide.marks)


def _wants_dismiss(blob: str) -> bool:
    return any(
        _phrase_in(blob, word)
        for word in (
            "got it",
            "dismiss",
            "overlay",
            "close",
            "background",
            "backdrop",
            "darkened",
            "magnified",
        )
    )


def _wants_yes(blob: str) -> bool:
    return any(_phrase_in(blob, word) for word in ("yes", "enable"))


def _dialog_escape(guide: AimGuide, avoid: frozenset[str]) -> AimMark | None:
    for name in ("close", "got it", "no", "yes"):
        hit = _mark_with_label(guide.marks, name)
        if hit is not None and f"mk_{hit.id}" not in avoid:
            return hit
    return None


def _intent_mark(
    guide: AimGuide,
    thought: str,
    choice: str,
    *,
    avoid: frozenset[str] = frozenset(),
) -> AimMark | None:
    labeled = [m for m in guide.marks if (m.label or "").strip()]
    if not labeled:
        return None
    locked_card = _guide_has_locked(guide)

    def _ok(mark: AimMark, *, thought_pass: bool) -> bool:
        if not _steer_label_ok(mark.label, locked_card=locked_card):
            return False
        if f"mk_{mark.id}" in avoid:
            return False
        if thought_pass and not _is_menu_label(mark.label):
            return False
        return True

    def _hits(blob: str, *, thought_pass: bool) -> list[AimMark]:
        found: list[AimMark] = []
        for mark in labeled:
            lab = _compact_label(mark.label)
            if not lab or not _ok(mark, thought_pass=thought_pass):
                continue
            if _phrase_in(blob, lab) or lab == blob:
                found.append(mark)
        return found

    choice_blob = _compact_label(choice)
    if choice_blob:
        hits = _hits(choice_blob, thought_pass=False)
        if hits:
            return max(hits, key=lambda m: len(_compact_label(m.label)))
    thought_blob = _compact_label(thought)
    if thought_blob:
        hits = _hits(thought_blob, thought_pass=True)
        if hits:
            return max(hits, key=lambda m: len(_compact_label(m.label)))
    combo = f"{choice_blob} {thought_blob}".strip()
    if combo:
        close = _mark_with_label(guide.marks, "close")
        if close is not None and _wants_dismiss(combo) and f"mk_{close.id}" not in avoid:
            return close
    if combo and _mark_with_label(guide.marks, "got it") is None:
        if _wants_dismiss(combo):
            no = _mark_with_label(guide.marks, "no")
            if no is not None and f"mk_{no.id}" not in avoid:
                return no
        if _wants_yes(combo):
            yes = _mark_with_label(guide.marks, "yes")
            if yes is not None and f"mk_{yes.id}" not in avoid:
                return yes
    return None


def _retarget(action: ActionLog, mark: AimMark, *, drop_target: bool) -> ActionLog:
    out = action.model_copy()
    out.mark = mark.id
    out.click_x = mark.x
    out.click_y = mark.y
    if drop_target:
        out.target = None
        out.click_x2 = None
        out.click_y2 = None
    out.name = out.derive_name()
    return out


def action_inspect_label(
    guide: AimGuide | None,
    label: str,
    *,
    duration_ms: int = 400,
) -> ActionLog | None:
    """Hover an inspectable / unknown entity to read its tooltip."""
    if guide is None or not guide.marks:
        return None
    want = _compact_label(label)
    hit = _mark_with_label(guide.marks, label)
    if hit is None:
        for mark in guide.marks:
            lab = _compact_label(mark.label)
            if lab == want or want in lab or lab in want:
                hit = mark
                break
    if hit is None:
        return None
    action = ActionLog(
        mark=hit.id,
        click_x=hit.x,
        click_y=hit.y,
        hover=True,
        duration_ms=duration_ms,
    )
    action.name = action.derive_name()
    return action


def action_from_labels(
    guide: AimGuide | None,
    *,
    choice: str = "",
    target_label: str = "",
    thought: str = "",
    avoid: frozenset[str] | set[str] | None = None,
    hud: dict | None = None,
    duration_ms: int = 400,
) -> ActionLog | None:
    """Map visible labels from the reasoner to click marks. No mark ids in JSON."""
    from loop.agent.entities import is_frame_mark_token

    if guide is None or not guide.marks:
        return None
    if is_frame_mark_token(choice) or is_frame_mark_token(target_label):
        return None
    blocked = frozenset(avoid or ())
    # An exact legal choice is an execution contract. Narrative thought often
    # names the enemy too; it must not turn that enemy into the primary card.
    # Intent is only a fallback when the choice lacks an exact current label.
    oid_hits = [
        mark for mark in guide.marks
        if choice.strip() and str(mark.object_id or "") == choice.strip()
    ]
    exact = [
        mark for mark in guide.marks
        if choice.strip() and _compact_label(mark.label) == _compact_label(choice)
    ]
    if len(exact) > 1 and any(mark.role == "card" or mark.y >= HAND_Y for mark in exact):
        # Duplicate cards require a frame-local choice_id. Picking the first
        # matching label silently destroys instance-addressable grounding.
        if len(oid_hits) != 1:
            return None
    primary = oid_hits[0] if len(oid_hits) == 1 else (exact[0] if len(exact) == 1 else None)
    if primary is None:
        primary = _intent_mark(guide, thought, choice, avoid=blocked)
    if primary is None:
        return None
    energy = _hud_energy(hud)
    if (
        energy is not None
        and energy > 0
        and _compact_label(primary.label) == "end turn"
        and choice
        and _compact_label(choice) != "end turn"
    ):
        alt = _intent_mark(guide, thought, choice, avoid=blocked)
        if alt is not None:
            primary = alt
    action = ActionLog(
        mark=primary.id,
        click_x=primary.x,
        click_y=primary.y,
        duration_ms=duration_ms,
    )
    action.name = action.derive_name()
    tgt_label = (target_label or "").strip()
    if tgt_label:
        target = None
        oid_tgt = [mark for mark in guide.marks if str(mark.object_id or "") == tgt_label]
        if len(oid_tgt) == 1:
            target = oid_tgt[0]
        if target is None:
            target = _mark_with_label(guide.marks, tgt_label)
        if target is None:
            target = _intent_mark(guide, tgt_label, tgt_label, avoid=blocked)
        if target is not None and target.id != primary.id:
            action.target = target.id
            action.click_x2 = target.x
            action.click_y2 = target.y
            action.name = action.derive_name()
    return action


def steer_to_labeled(
    action: ActionLog,
    guide: AimGuide | None,
    *,
    thought: str = "",
    choice: str = "",
    avoid: frozenset[str] | set[str] | None = None,
    hud: dict | None = None,
) -> ActionLog:
    """Thought/choice named a labeled word but JSON copied an example mark id."""
    if guide is None or not guide.marks:
        return action
    blocked = frozenset(avoid or ())
    hit = guide.by_id(int(action.mark)) if action.mark is not None else None
    if hit is not None and _compact_label(hit.label) in {"yes", "no", "got it", "close"}:
        if action.target is not None:
            return _retarget(action, hit, drop_target=True)
        return action
    intent = _intent_mark(guide, thought, choice, avoid=blocked)
    energy = _hud_energy(hud)
    if (
        intent is not None
        and energy is not None
        and energy > 0
        and _compact_label(intent.label) == "end turn"
        and not (hit is not None and _compact_label(hit.label) == "end turn")
    ):
        intent = None
    if intent is not None:
        on_intent = hit is not None and hit.id == intent.id
        menu = _is_menu_label(intent.label)
        if not on_intent:
            if action.mark is None and not action.hover and action.click_x is None:
                if choice or thought:
                    return _retarget(action, intent, drop_target=menu)
                return action
            return _retarget(action, intent, drop_target=menu)
        if menu and action.target is not None:
            return _retarget(action, intent, drop_target=True)
        return action
    if hit is not None and f"mk_{hit.id}" in blocked:
        alt = _dialog_escape(guide, blocked)
        if alt is not None:
            return _retarget(action, alt, drop_target=True)
    if hit is not None and _is_menu_label(hit.label) and action.target is not None:
        return _retarget(action, hit, drop_target=True)
    return action


def steer_to_confirm(
    action: ActionLog,
    guide: AimGuide | None,
    *,
    thought: str = "",
    choice: str = "",
    avoid: frozenset[str] | set[str] | None = None,
    hud: dict | None = None,
) -> ActionLog:
    """Back-compat alias for Confirm-era tests."""
    return steer_to_labeled(
        action, guide, thought=thought, choice=choice, avoid=avoid, hud=hud
    )


def fill_from_aim(
    guide: AimGuide | None,
    *,
    recent: list[str],
    duration_ms: int,
    hud: dict | None = None,
    exclude_marks: set[int] | None = None,
) -> ActionLog | None:
    if guide is None or not guide.marks:
        return None
    used: set[int] = set(exclude_marks or ())
    for token in recent:
        if token.startswith("mk_"):
            try:
                used.add(int(token.split("_", 1)[1]))
            except (TypeError, ValueError, IndexError):
                continue
    safe = [m for m in guide.marks if label_kind(m.label) != "danger"]
    unused = [m for m in safe if m.id not in used]
    if not unused:
        unused = safe
    energy = _hud_energy(hud)
    screen = str((hud or {}).get("screen") or "").strip().lower()
    combat_busy = screen == "combat" and energy is not None and energy > 0
    has_play = combat_busy and any(_fill_is_play(m) for m in unused)

    def _rank(mark: AimMark) -> tuple:
        lab = _compact_label(mark.label)
        dialog = 0 if lab in _DIALOG_BUTTONS else 1
        if combat_busy:
            play = _fill_is_play(mark)
            hand = play and mark.y >= HAND_Y
            end_turn = lab == "end turn"
            return (
                dialog,
                0 if hand else 1,
                0 if play else 1,
                1 if (end_turn and has_play) else 0,
                -mark.score,
                -mark.y,
                mark.x,
            )
        return (
            dialog,
            0 if label_kind(mark.label) == "progress" else 1,
            -mark.score,
            -mark.y,
            mark.x,
        )

    unused.sort(key=_rank)
    if not unused:
        return None
    pick = unused[0]
    action = ActionLog(
        mark=pick.id,
        click_x=pick.x,
        click_y=pick.y,
        duration_ms=duration_ms,
    )
    action.name = action.derive_name()
    return action


_CONFIRM_WORDS = {"yes", "ok", "okay", "y"}


def steer_off_danger(
    action: ActionLog,
    guide: AimGuide | None,
    *,
    recent: list[str],
    duration_ms: int,
) -> ActionLog:
    """Do not click Quit / Abandon / Yes-on-quit; send Continue instead."""
    if guide is None or not guide.marks:
        return action
    hit = guide.by_id(int(action.mark)) if action.mark is not None else None
    label = (hit.label if hit is not None else "") or ""
    danger_present = any(label_kind(m.label) == "danger" for m in guide.marks)
    confirm = label.strip().lower() in _CONFIRM_WORDS
    if label_kind(label) != "danger" and not (danger_present and confirm):
        return action
    used = list(recent)
    if action.mark is not None:
        used.append(f"mk_{int(action.mark)}")
    alt = fill_from_aim(guide, recent=used, duration_ms=duration_ms)
    return alt if alt is not None else action


def _score_patch(patch: np.ndarray) -> float:
    if patch.size == 0:
        return 0.0
    dark = float((patch < _DARK_LUMA).mean())
    std = float(patch.std())
    mn = float(patch.min())
    return dark * 120.0 + std + max(0.0, 90.0 - mn) * 0.3


def _score_frac_box(
    gray: np.ndarray,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
) -> float:
    h, w = gray.shape
    ix0 = max(0, int(x0 * w))
    ix1 = min(w, max(ix0 + 1, int(x1 * w)))
    iy0 = max(0, int(y0 * h))
    iy1 = min(h, max(iy0 + 1, int(y1 * h)))
    return _score_patch(gray[iy0:iy1, ix0:ix1])


def _score_mask_box(
    gray: np.ndarray,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    mask_w: int,
    mask_h: int,
) -> float:
    gh, gw = gray.shape
    sx, sy = gw / max(mask_w, 1), gh / max(mask_h, 1)
    return _score_frac_box(
        gray,
        (x0 * sx) / gw,
        (y0 * sy) / gh,
        (x1 * sx) / gw,
        (y1 * sy) / gh,
    )


def _legend_xmin(ocr: list[AimMark]) -> float | None:
    hits = [m for m in ocr if is_legend_label(m.label)]
    right = [m for m in hits if m.x >= 0.70]
    titled = any("legend" in (m.label or "").lower() for m in ocr)
    if not titled and len(right) < 2:
        return None
    xs = [m.x0 for m in (right or hits)]
    if not xs:
        return None
    return max(0.55, min(xs) - 0.04)


def _strip_legend(
    ocr: list[AimMark],
    cv: list[AimMark],
    gui: list[AimMark] | None = None,
) -> tuple[list[AimMark], list[AimMark], list[AimMark]]:
    kept_ocr = [m for m in ocr if not is_legend_label(m.label)]
    kept_gui = list(gui or [])
    xmin = _legend_xmin(ocr)
    if xmin is None:
        return kept_ocr, kept_gui, cv
    def _keep_left(marks: list[AimMark]) -> list[AimMark]:
        return [m for m in marks if m.x < xmin and m.y >= 0.12]
    return kept_ocr, _keep_left(kept_gui), _keep_left(cv)


def _min_resize(gray: np.ndarray, width: int, height: int) -> np.ndarray:
    """Keep the darkest pixel in each block so thin ink survives downsample."""
    src_h, src_w = gray.shape
    ys = np.linspace(0, src_h, height + 1).astype(np.int32)
    xs = np.linspace(0, src_w, width + 1).astype(np.int32)
    out = np.empty((height, width), dtype=np.float32)
    for y in range(height):
        y0, y1 = int(ys[y]), max(int(ys[y]) + 1, int(ys[y + 1]))
        row = gray[y0:y1]
        for x in range(width):
            x0, x1 = int(xs[x]), max(int(xs[x]) + 1, int(xs[x + 1]))
            out[y, x] = row[:, x0:x1].min()
    return out


def _compact_boxes(
    mask: np.ndarray,
    *,
    min_px: int,
    width: int,
    height: int,
    max_aspect: float = _MAX_ASPECT,
) -> list[tuple[int, int, int, int, int]]:
    area = float(width * height)
    max_px = max(min_px * 4, int(area * _MAX_FRAC))
    kept: list[tuple[int, int, int, int, int]] = []
    for x0, y0, x1, y1, n in _components(mask):
        bw, bh = x1 - x0, y1 - y0
        if n < min_px or n > max_px:
            continue
        if bw < 3 or bh < 3:
            continue
        if max(bw / max(bh, 1), bh / max(bw, 1)) > max_aspect:
            continue
        if _touches_too_much_border(x0, y0, x1, y1, width, height):
            continue
        kept.append((x0, y0, x1, y1, n))
    return kept


def _marks_from_boxes(
    boxes: list[tuple[int, int, int, int, int]],
    *,
    src_w: int,
    src_h: int,
    mask_w: int,
    mask_h: int,
    gray: np.ndarray | None = None,
) -> list[AimMark]:
    marks: list[AimMark] = []
    sx, sy = src_w / mask_w, src_h / mask_h
    for i, (x0, y0, x1, y1, _) in enumerate(boxes, start=1):
        px0, py0 = x0 * sx, y0 * sy
        px1, py1 = x1 * sx, y1 * sy
        cx = ((px0 + px1) * 0.5) / src_w
        cy = ((py0 + py1) * 0.5) / src_h
        fx0 = float(max(0.0, min(1.0, px0 / src_w)))
        fy0 = float(max(0.0, min(1.0, py0 / src_h)))
        fx1 = float(max(0.0, min(1.0, px1 / src_w)))
        fy1 = float(max(0.0, min(1.0, py1 / src_h)))
        score = 0.0 if gray is None else _score_frac_box(gray, fx0, fy0, fx1, fy1)
        marks.append(
            AimMark(
                id=i,
                x=float(max(0.0, min(1.0, cx))),
                y=float(max(0.0, min(1.0, cy))),
                x0=fx0,
                y0=fy0,
                x1=fx1,
                y1=fy1,
                score=score,
                source="cv",
            )
        )
    return marks


def _components(mask: np.ndarray) -> list[tuple[int, int, int, int, int]]:
    on = np.asarray(mask, dtype=bool)
    h, w = on.shape
    parent = np.arange(h * w, dtype=np.int32)

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    ys, xs = np.nonzero(on)
    for y, x in zip(ys.tolist(), xs.tolist()):
        i = y * w + x
        if x + 1 < w and on[y, x + 1]:
            union(i, y * w + x + 1)
        if y + 1 < h and on[y + 1, x]:
            union(i, (y + 1) * w + x)
    buckets: dict[int, list[tuple[int, int]]] = {}
    for y, x in zip(ys.tolist(), xs.tolist()):
        root = find(y * w + x)
        buckets.setdefault(root, []).append((x, y))
    out: list[tuple[int, int, int, int, int]] = []
    for pts in buckets.values():
        px = [p[0] for p in pts]
        py = [p[1] for p in pts]
        out.append((min(px), min(py), max(px) + 1, max(py) + 1, len(pts)))
    return out


def _touches_too_much_border(x0: int, y0: int, x1: int, y1: int, w: int, h: int) -> bool:
    edges = int(x0 <= 1) + int(y0 <= 1) + int(x1 >= w - 1) + int(y1 >= h - 1)
    return edges >= 2


def _iou(a: tuple[int, int, int, int, int], b: tuple[int, int, int, int, int]) -> float:
    ax0, ay0, ax1, ay1, _ = a
    bx0, by0, bx1, by1, _ = b
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    iw, ih = max(0, ix1 - ix0), max(0, iy1 - iy0)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(1, (ax1 - ax0) * (ay1 - ay0))
    area_b = max(1, (bx1 - bx0) * (by1 - by0))
    return inter / float(area_a + area_b - inter)


def _spread(
    boxes: list[tuple[int, int, int, int, int]],
    *,
    cap: int,
    min_frac: float,
    width: int,
    height: int,
) -> list[tuple[int, int, int, int, int]]:
    """Keep spatially diverse islands so one panel cannot consume the cap."""
    ranked = sorted(boxes, key=lambda b: b[4], reverse=True)
    keep: list[tuple[int, int, int, int, int]] = []
    centers: list[tuple[float, float]] = []
    min_d2 = min_frac * min_frac
    for box in ranked:
        x0, y0, x1, y1, _ = box
        cx = ((x0 + x1) * 0.5) / max(width, 1)
        cy = ((y0 + y1) * 0.5) / max(height, 1)
        if any((cx - ox) ** 2 + (cy - oy) ** 2 < min_d2 for ox, oy in centers):
            continue
        keep.append(box)
        centers.append((cx, cy))
        if len(keep) >= cap:
            break
    return keep


def _nms(
    boxes: list[tuple[int, int, int, int, int]],
    *,
    iou: float,
    cap: int,
) -> list[tuple[int, int, int, int, int]]:
    ranked = sorted(boxes, key=lambda b: b[4], reverse=True)
    keep: list[tuple[int, int, int, int, int]] = []
    for box in ranked:
        if all(_iou(box, other) < iou for other in keep):
            keep.append(box)
        if len(keep) >= cap:
            break
    return keep


def _font(size: int) -> ImageFont.ImageFont:
    windir = Path(os.environ.get("WINDIR", r"C:\Windows"))
    for name in ("arial.ttf", "segoeui.ttf", "calibri.ttf"):
        path = windir / "Fonts" / name
        if path.is_file():
            try:
                return ImageFont.truetype(str(path), size)
            except OSError:
                continue
    return ImageFont.load_default()


def _text_size(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> tuple[int, int]:
    box = draw.textbbox((0, 0), text, font=font)
    return int(box[2] - box[0]), int(box[3] - box[1])


def _text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    *,
    font: ImageFont.ImageFont,
    fill: tuple[int, int, int, int],
) -> None:
    x, y = xy
    for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        draw.text((x + dx, y + dy), text, font=font, fill=(0, 0, 0, fill[3]))
    draw.text((x, y), text, font=font, fill=fill)
