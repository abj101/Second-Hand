"""Structured STS2 HUD stats from ROI OCR. Authoritative numbers for hud_read MCP."""

from __future__ import annotations

import re
from typing import Any, Literal

import numpy as np
from PIL import Image

from loop.os.ocr import read_text
from loop.reward.sts import (
    SCREEN_KINDS,
    _BLOCK,
    _ENERGY_WORD,
    _GOLD,
    _FLOOR,
    _HP_PAIR,
    _best_hp_pair,
    _energy_pair,
    _fail_from_ocr,
    _ocr_turn_roi,
    _screen_from_ocr,
    parse_turn_gate,
)
from loop.reward.hud import is_black_frame

OcrConfidence = Literal["high", "partial"]
TurnGate = Literal["ours", "theirs", "unknown"]

_TUTORIAL_STEP = re.compile(r"\((\d{1,2})\s+of\s+(\d{1,2})\)", re.I)
_TUTORIAL_TITLE = re.compile(
    r"\b(combat basics|map basics|potion slot|view upgrades|hit points \(hp\))\b",
    re.I,
)
_KNOWN_MARKERS = (
    "end turn",
    "combat basics",
    "map basics",
    "got it",
    "singleplayer",
    "confirm",
    "resume",
    "give up",
    "legend",
    "choose a relic",
    "choose a card",
    "paused",
    "enable tutorials",
)


def _crop_text(image: Image.Image, box: tuple[float, float, float, float]) -> str:
    w, h = image.size
    if w < 8 or h < 8:
        return ""
    x0, y0, x1, y1 = box
    crop = image.crop((int(x0 * w), int(y0 * h), int(x1 * w), int(y1 * h)))
    try:
        return read_text(crop)
    except Exception:  # noqa: BLE001 — stats must never abort collect
        return ""


def _hp_pairs_in(blob: str) -> list[tuple[int, int]]:
    pairs: list[tuple[int, int]] = []
    for match in _HP_PAIR.finditer(blob or ""):
        cur, mx = int(match.group(1)), int(match.group(2))
        if mx <= 0 or cur > mx or mx > 999:
            continue
        pairs.append((cur, mx))
    return pairs


def _player_hp_from_blob(blob: str) -> tuple[int, int] | None:
    """Largest max HP pair — player bar is typically the highest max on screen."""
    return _best_hp_pair(blob)


def _enemy_pairs(
    blob: str,
    player: tuple[int, int] | None,
    energy: tuple[int, int] | None = None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for cur, mx in _hp_pairs_in(blob):
        if player is not None and (cur, mx) == player:
            continue
        if energy is not None and (cur, mx) == energy:
            continue
        if mx > 200:
            continue
        out.append({"hp": cur, "hp_max": mx, "label": None})
    return out


def _plates_from_words(
    image: Image.Image,
    player: tuple[int, int] | None,
) -> tuple[list[dict[str, Any]], tuple[int, int] | None]:
    """HP plates distinguished by value *and* screen position."""
    try:
        from loop.os.ocr import read_words
        words = list(read_words(image) or [])
    except Exception:  # noqa: BLE001 — stats must never abort collect
        return [], None
    pair_re = re.compile(r"^(\d+)\s*/\s*(\d+)$")
    plates: list[dict[str, Any]] = []
    energy: tuple[int, int] | None = None
    for word in words:
        m = pair_re.match((word.text or "").strip())
        if not m:
            continue
        cur, mx = int(m.group(1)), int(m.group(2))
        if mx <= 0 or cur > mx or mx > 200:
            continue
        if player is not None and (cur, mx) == player:
            continue
        x, y = float(word.x), float(word.y)
        if x <= 0.18 and 0.78 <= y <= 0.90 and mx <= 10:
            energy = (cur, mx)
            continue
        if y >= 0.78:
            continue
        dup = next(
            (
                True
                for other in plates
                if other["hp"] == cur
                and other["hp_max"] == mx
                and abs(float(other["x"]) - x) < 0.04
                and abs(float(other["y"]) - y) < 0.04
            ),
            False,
        )
        if dup:
            continue
        plates.append({"hp": cur, "hp_max": mx, "label": None, "x": x, "y": y})
    return plates, energy


def _parse_tutorial(blob: str) -> dict[str, Any] | None:
    low = (blob or "").lower()
    step_match = _TUTORIAL_STEP.search(blob or "")
    title_match = _TUTORIAL_TITLE.search(blob or "")
    active = bool(
        step_match
        or title_match
        or "got it" in low
        or "combat basics" in low
        or "map basics" in low
        or "enable tutorials" in low
        or "you start each combat" in low
    )
    if not active:
        return None
    title = title_match.group(1).title() if title_match else None
    if title is None and "combat basics" in low:
        title = "Combat Basics"
    elif title is None and "map basics" in low:
        title = "Map Basics"
    elif title is None and "enable tutorials" in low:
        title = "Enable Tutorials"
    step = int(step_match.group(1)) if step_match else None
    total = int(step_match.group(2)) if step_match else None
    return {
        "active": True,
        "title": title,
        "step": step,
        "total": total,
    }


def _markers_from_blob(blob: str) -> list[str]:
    low = (blob or "").lower()
    found: list[str] = []
    for marker in _KNOWN_MARKERS:
        if marker in low and marker not in found:
            found.append(marker)
    return found


def _has_combat_ui(blob: str) -> bool:
    return "end turn" in (blob or "").lower()


def _stats_numbers_kind(screen: str | None, blob: str) -> bool:
    """Whether player/enemy HP and energy should be extracted."""
    if screen == "combat":
        return True
    if screen == "tutorial" and _has_combat_ui(blob):
        return True
    return False


def stats_from_blob(blob: str, *, screen: str | None = None) -> dict[str, Any]:
    """Parse structured stats from a joined OCR string (testable without image)."""
    text = " ".join((blob or "").split())
    low = text.lower()
    kind = screen or _screen_from_ocr(low)
    if kind and kind not in SCREEN_KINDS:
        kind = "other"

    turn_blob = low
    turn = parse_turn_gate(turn_blob)

    player: dict[str, Any] = {}
    enemies: list[dict[str, Any]] = []
    block = _BLOCK.search(low)
    if block:
        player["block"] = int(block.group(1))

    if _stats_numbers_kind(kind, low):
        player_pair = _player_hp_from_blob(low)
        if player_pair is not None:
            player["hp"], player["hp_max"] = player_pair
        energy = _energy_pair(low, player_pair)
        if energy is not None:
            player["energy"], player["energy_max"] = energy
        enemies = _enemy_pairs(low, player_pair, energy)

    tutorial = _parse_tutorial(text)
    markers = _markers_from_blob(low)

    meta: dict[str, Any] = {}
    if kind in {"combat", "map", "shop", "rest", "event", "reward"}:
        gold = _GOLD.search(low)
        floor = _FLOOR.search(low)
        if gold:
            meta["gold"] = int(gold.group(1))
        if floor:
            meta["floor"] = int(floor.group(1))

    confidence: OcrConfidence = "high" if text.strip() else "partial"
    if text.strip() and not kind and not player and not tutorial:
        confidence = "partial"

    # Underlying play mode may differ from UI screen (tutorial overlay on combat).
    combat_active = bool(
        kind == "combat"
        or (
            _stats_numbers_kind(kind, low)
            and (
                bool(enemies)
                or isinstance(player.get("energy"), (int, float))
                or isinstance(player.get("hp"), (int, float))
            )
        )
    )
    activity = "combat" if combat_active else (kind or "other")

    out: dict[str, Any] = {
        "screen": kind,
        "activity": activity,
        "combat_active": combat_active,
        "turn": turn,
        "player": player or None,
        "enemies": enemies,
        "tutorial": tutorial,
        "markers": markers,
        "ocr_confidence": confidence,
        "fail_screen": _fail_from_ocr(low),
        "black": False,
        "ocr": text[:500],
    }
    if meta:
        out["meta"] = meta
    return out


def _read_full_frame(image: Image.Image) -> str:
    try:
        from loop.reward.sts import _ocr_full

        return _ocr_full(image)
    except Exception:  # noqa: BLE001
        try:
            return read_text(image)
        except Exception:  # noqa: BLE001
            return ""


def extract_sts_stats(image: Image.Image) -> dict[str, Any]:
    """ROI OCR → structured HUD JSON for hud_read MCP."""
    if image is None:
        return stats_from_blob("")
    full = _read_full_frame(image)

    player_roi = _crop_text(image, (0.0, 0.0, 0.28, 0.14))
    enemy_roi = _crop_text(image, (0.18, 0.08, 0.82, 0.48))
    turn_roi = _ocr_turn_roi(image)

    merged = " ".join(part for part in (full, player_roi, enemy_roi, turn_roi) if part)
    stats = stats_from_blob(merged)
    stats["black"] = is_black_frame(image)

    if _stats_numbers_kind(stats.get("screen"), merged):
        player_pair = _player_hp_from_blob(player_roi) or _player_hp_from_blob(merged)
        player = dict(stats.get("player") or {})
        if player_pair is not None:
            player["hp"], player["hp_max"] = player_pair
        if not player.get("energy"):
            energy = _energy_pair(merged, player_pair)
            if energy is not None:
                player["energy"], player["energy_max"] = energy
        stats["player"] = player or None

        positioned, positioned_energy = _plates_from_words(image, player_pair)
        if positioned_energy is not None:
            player["energy"], player["energy_max"] = positioned_energy
            stats["player"] = player
        enemy_blob = " ".join(part for part in (enemy_roi, merged) if part)
        energy_pair = None
        if isinstance(player.get("energy"), int) and isinstance(player.get("energy_max"), int):
            energy_pair = (player["energy"], player["energy_max"])
        blob_enemies = _enemy_pairs(enemy_blob, player_pair, energy_pair)
        stats["enemies"] = positioned if len(positioned) >= len(blob_enemies) else blob_enemies

        turn = parse_turn_gate(turn_roi or merged)
        if turn != "unknown":
            stats["turn"] = turn

    if str(stats.get("screen") or "").strip().lower() == "map":
        stats["map_tool"] = selected_map_tool(image)
    return stats


# Three glyph slots around the live step-24 click at (0.082, 0.932). Slot 1 is the eraser.
MAP_TOOL_ROIS: tuple[tuple[float, float, float, float], ...] = (
    (0.048, 0.908, 0.072, 0.956),
    (0.070, 0.908, 0.094, 0.956),
    (0.092, 0.908, 0.116, 0.956),
)
_MAP_TOOL_PINK_FRAC = 0.04


def _map_tool_pink_frac(image: Image.Image, box: tuple[float, float, float, float]) -> float:
    w, h = image.size
    if w < 8 or h < 8:
        return 0.0
    x0, y0, x1, y1 = box
    crop = image.crop((int(x0 * w), int(y0 * h), max(int(x0 * w) + 1, int(x1 * w)), max(int(y0 * h) + 1, int(y1 * h))))
    arr = np.asarray(crop.convert("RGB"), dtype=np.int16)
    if arr.size == 0:
        return 0.0
    r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
    pink = (r >= 150) & (b >= 70) & (g <= 130) & ((r - g) >= 40) & (b > g)
    return float(pink.mean())


def selected_map_tool(image: Image.Image) -> str | None:
    """Current-frame selected annotation slot, or None if none/ambiguous."""
    if image is None:
        return None
    hits = [
        index
        for index, box in enumerate(MAP_TOOL_ROIS)
        if _map_tool_pink_frac(image, box) >= _MAP_TOOL_PINK_FRAC
    ]
    if len(hits) != 1:
        return None
    return f"map_tool:{hits[0]}"


def stats_to_score_metrics(stats: dict[str, Any]) -> dict[str, Any]:
    """Flatten extract_sts_stats into score_sts-compatible metrics dict."""
    scored: dict[str, Any] = {
        "fail_screen": bool(stats.get("fail_screen")),
        "win_screen": False,
        "black": bool(stats.get("black")),
        "ocr": str(stats.get("ocr") or "")[:500],
    }
    screen = stats.get("screen")
    if screen:
        scored["screen"] = screen
    activity = stats.get("activity")
    if activity:
        scored["activity"] = activity
    if "combat_active" in stats:
        scored["combat_active"] = bool(stats.get("combat_active"))
    player = stats.get("player") or {}
    if isinstance(player, dict):
        for key in ("hp", "hp_max", "energy", "energy_max", "block"):
            val = player.get(key)
            if val is not None:
                scored[key] = val
    meta = stats.get("meta") or {}
    if isinstance(meta, dict):
        for key in ("gold", "floor"):
            val = meta.get(key)
            if val is not None:
                scored[key] = val
    if stats.get("map_tool"):
        scored["map_tool"] = stats.get("map_tool")
    return scored
