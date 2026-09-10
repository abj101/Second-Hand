from __future__ import annotations

import logging
import json
import re
from dataclasses import dataclass, field
from typing import Any

_LOG = logging.getLogger("loop.perceive")

from loop.agent.entities import is_discovery_role, is_unknown_entity_label
from loop.agent.prompts import SCENE_PERCEIVE_SCHEMA, scene_perceive_system, scene_perceive_user
from loop.model.gateway import EpisodeThread, ModelCall, VlmGateway
from loop.os.affordance import AffordanceSignal, merge_legal_actions
from loop.os.capture import EncodedFrame

_HP_LIKE = frozenset({"/", "hp", "energy"})
_KNOWN_CONTROL_MARKERS = (
    "got it",
    "yes",
    "no",
    "confirm",
    "back",
    "close",
    "end turn",
    "resume",
    "singleplayer",
    "continue",
    "strike",
    "defend",
)
_DIALOG_BUTTONS = frozenset(
    {"yes", "no", "got it", "confirm", "back", "close", "continue", "singleplayer", "multiplayer", "resume"}
)
_STALE_DIALOG_SCREENS = frozenset({"combat", "tutorial", "map"})
_MAP_LEGEND = frozenset({"unknown", "merchant", "enemy", "treasure", "elite", "event", "rest", "shop"})
_MAP_CHROME = frozenset({"share"})
_COMBAT_OCR_CONTROL_BLOCK = frozenset({"next", "confirm", "back", "prev", "got it"})
_UNIDENTIFIED_ENEMY = "unidentified_enemy"
_DECK_OVERLAY_MARKERS = frozenset(
    {
        "view upgrades",
        "deck (d)",
        "view all of the cards",
        "view all deck",
        "obtained",
        "card type",
        "you start each combat with all the cards",
    }
)
_COMBAT_SCREEN_MARKERS = frozenset({"end turn", "enemy turn"})
_PAGINATED_TUTORIAL_NAV = frozenset({"prev", "next", "back", "arrow"})
_NAME_SOURCES = frozenset({"ocr", "dino", "game_state", "unresolved"})
_NAME_SOURCE_ALIASES = {"detector": "dino"}
_UNKNOWN_ENEMY = "unknown_enemy"


def _observed_name_set(
    *,
    hud: dict[str, Any] | None,
    host_stats: dict[str, Any] | None,
    clickable_labels: list[str] | None,
) -> set[str]:
    """Names supported by this frame's explicit observation channels only."""
    observed: set[str] = set()
    ocr = str((hud or {}).get("ocr") or "")
    observed.update(_compact(value) for value in (clickable_labels or []) if value)
    observed.update(_compact(value) for value in (hud or {}).get("markers", []) if value)
    stats = host_stats or {}
    observed.update(_compact(value) for value in stats.get("markers", []) if value)
    for key in ("enemies", "hand_cards", "cards", "relics", "characters"):
        for row in stats.get(key) or []:
            if isinstance(row, dict):
                value = row.get("name") or row.get("label")
            else:
                value = row
            if value:
                observed.add(_compact(str(value)))
    # Keep both phrases and OCR tokens: OCR often separates a multi-word name.
    observed.update(_ocr_tokens(ocr))
    observed.update(_compact(value) for value in re.findall(r"[^\n]+", ocr) if value.strip())
    return {value for value in observed if value}


def _entity_name_source(row: dict[str, Any], observed: set[str]) -> str:
    label = _entity_label(row) or ""
    compact = _compact(label)
    source = str(row.get("name_source") or "").strip().lower()
    if compact and compact in observed:
        if source in {"ocr", "dino", "game_state"}:
            return source
        return "ocr"
    if source in _NAME_SOURCES:
        return source
    source_hint = str(row.get("source") or "").strip().lower()
    if source_hint == "detector":
        return "dino"
    if source_hint in {"ocr", "host_ocr"}:
        return "ocr"
    if source_hint in {"game_state", "host", "state"}:
        return "game_state"
    return "unresolved"


def normalize_perceive_provenance(parsed: dict[str, Any]) -> dict[str, Any]:
    """Normalize only known producer aliases before schema validation.

    LLM1 may echo the detector's raw provenance as ``detector`` while the
    perception contract calls that source ``dino``.  Normalize the known alias
    without widening the accepted schema for arbitrary model-provided values.
    """
    out = dict(parsed)
    for slot in ("characters", "enemies", "hand_cards", "hud", "controls"):
        rows = out.get(slot)
        if not isinstance(rows, list):
            continue
        normalized: list[Any] = []
        for row in rows:
            if not isinstance(row, dict):
                normalized.append(row)
                continue
            copy = dict(row)
            source = str(copy.get("name_source") or "").strip().lower()
            if source in _NAME_SOURCE_ALIASES:
                copy["name_source"] = _NAME_SOURCE_ALIASES[source]
            normalized.append(copy)
        out[slot] = normalized
    return out


def _ground_entity_row(row: dict[str, Any], *, observed: set[str], role: str) -> dict[str, Any]:
    """Prevent model-only names from crossing the perception boundary."""
    out = dict(row)
    label = _entity_label(out) or ""
    compact = _compact(label)
    source = _entity_name_source(out, observed)
    if role == "enemy" and (not label or compact not in observed or source == "unresolved"):
        fallback = _UNKNOWN_ENEMY
        out["label"] = fallback
        out["name"] = fallback
        out["name_source"] = "unresolved"
        out["confidence"] = "low"
    elif role in {"card", "relic"} and (not label or compact not in observed or source == "unresolved"):
        fallback = "unknown_card" if role == "card" else "unknown_relic"
        out["label"] = fallback
        out["name"] = fallback
        out["name_source"] = "unresolved"
        out["confidence"] = "low"
    else:
        out["label"] = label or ("unknown_character" if role != "enemy" else _UNKNOWN_ENEMY)
        out["name"] = out["label"]
        out["name_source"] = source
    return out


def _explanatory_tutorial_overlay(
    tutorial: dict[str, Any],
    *,
    host_stats: dict[str, Any] | None = None,
) -> bool:
    """Middle tutorial steps with explanatory overlay (dimmed bg; nav-only controls)."""
    tut: dict[str, Any] = {}
    if isinstance(tutorial, dict) and tutorial:
        tut = tutorial
    elif isinstance((host_stats or {}).get("tutorial"), dict):
        tut = host_stats["tutorial"]  # type: ignore[index]
    if not tut.get("active"):
        return False
    step, total = tut.get("step"), tut.get("total")
    return isinstance(step, int) and isinstance(total, int) and 1 < step < total


def _tutorial_overlay_chrome(controls: list[dict[str, Any]] | None) -> bool:
    """True when typed chrome is a real dialog/page, not a leftover Next."""
    labels = {
        _compact(str(row.get("label") or ""))
        for row in (controls or [])
        if isinstance(row, dict)
    }
    if labels & {"yes", "no", "got it"}:
        return True
    return bool(labels & {"prev", "previous", "back"}) and "next" in labels


_TUTORIAL_STEP_RE = re.compile(r"\((\d{1,2})\s+of\s+(\d{1,2})\)", re.I)
_TOKEN_RE = re.compile(r"[a-z0-9+]+", re.I)


def _looks_like_stat(label: str) -> bool:
    blob = (label or "").strip().lower()
    if not blob:
        return True
    if "/" in blob and any(ch.isdigit() for ch in blob):
        return True
    return blob in _HP_LIKE


def _compact(text: str) -> str:
    return " ".join(
        "".join(ch if ch.isalnum() else " " for ch in (text or "").lower()).split()
    )


def _ocr_tokens(blob: str) -> set[str]:
    return {m.group(0).lower() for m in _TOKEN_RE.finditer(blob or "")}


def _marker_present(marker: str, blob: str, tokens: set[str] | None = None) -> bool:
    """Whole-word / whole-phrase match — avoids 'no' inside 'unknown'."""
    needle = _compact(marker)
    if not needle:
        return False
    if " " in needle:
        return needle in _compact(blob)
    tok = tokens if tokens is not None else _ocr_tokens(blob)
    return needle in tok


def _grounded_label_set(
    *,
    hud: dict[str, Any] | None,
    host_stats: dict[str, Any] | None,
    clickable_labels: list[str] | None,
) -> set[str]:
    grounded: set[str] = set()
    ocr = str((hud or {}).get("ocr") or "")
    grounded |= _ocr_tokens(ocr)
    grounded |= {_compact(t) for t in _TOKEN_RE.findall(ocr)}
    for label in clickable_labels or []:
        grounded.add(_compact(label))
    stats = host_stats or {}
    for marker in stats.get("markers") or []:
        grounded.add(_compact(str(marker)))
    tutorial = stats.get("tutorial")
    if isinstance(tutorial, dict):
        title = str(tutorial.get("title") or "").strip()
        if title:
            grounded.add(_compact(title))
    return {g for g in grounded if g}


def _role_for_marker(label: str) -> str:
    low = _compact(label)
    if low in {"strike", "defend", "bash"} or low.endswith("+"):
        return "card"
    if low in {"yes", "no", "got it", "confirm", "back", "close", "end turn", "resume"}:
        return "button"
    if low in {"singleplayer", "multiplayer", "settings", "quit", "continue"}:
        return "menu"
    return "button"


def _control_labels(controls: list[dict[str, str]]) -> set[str]:
    return {_compact(row.get("label") or "") for row in controls if row.get("label")}


def _promote_known_controls(
    controls: list[dict[str, str]],
    *,
    visible_text: list[str],
    hud: dict[str, Any] | None,
    host_stats: dict[str, Any] | None = None,
    clickable_labels: list[str] | None = None,
) -> list[dict[str, str]]:
    """Promote same-frame OCR/aim labels into controls when the VLM omitted them."""
    have = _control_labels(controls)
    ocr = str((hud or {}).get("ocr") or "")
    tokens = _ocr_tokens(ocr)
    blob = " ".join(
        part
        for part in (
            ocr,
            " ".join(visible_text),
            " ".join(clickable_labels or []),
        )
        if part
    )
    grounded = _grounded_label_set(hud=hud, host_stats=host_stats, clickable_labels=clickable_labels)
    screen_key = str((host_stats or {}).get("screen") or (hud or {}).get("screen") or "").strip().lower()
    for marker in _KNOWN_CONTROL_MARKERS:
        if screen_key == "combat" and marker in _COMBAT_OCR_CONTROL_BLOCK:
            continue
        if marker in have:
            continue
        if not _marker_present(marker, blob, tokens) and marker not in grounded:
            continue
        label = marker.title() if marker == "got it" else marker.replace("end turn", "End Turn").title()
        if marker == "end turn":
            label = "End Turn"
        elif marker == "got it":
            label = "Got it"
        elif marker in {"yes", "no", "back", "close"}:
            label = marker.capitalize()
        controls.append({"label": label, "role": _role_for_marker(label)})
        have.add(marker)
    return controls


def _ground_controls(
    controls: list[dict[str, str]],
    *,
    vlm_native: set[str],
    visible_text: list[str],
    hud: dict[str, Any] | None,
    host_stats: dict[str, Any] | None,
    clickable_labels: list[str] | None,
    screen: str,
) -> list[dict[str, str]]:
    """Drop hallucinated / stale labels; menu buttons must match this frame's OCR/aim."""
    grounded = _grounded_label_set(hud=hud, host_stats=host_stats, clickable_labels=clickable_labels)
    visible_compact = {_compact(t) for t in visible_text}
    screen_key = (screen or "").strip().lower()
    tutorial = (host_stats or {}).get("tutorial") if isinstance((host_stats or {}).get("tutorial"), dict) else {}
    explanatory_tutorial = _explanatory_tutorial_overlay(tutorial, host_stats=host_stats)
    out: list[dict[str, str]] = []
    clickable_compact = {_compact(str(l)) for l in (clickable_labels or [])}
    for row in controls:
        label = str(row.get("label") or "").strip()
        compact = _compact(label)
        if not compact:
            continue
        if screen_key == "map" and compact in _MAP_LEGEND:
            if compact in clickable_compact:
                out.append(row)
            continue
        if screen_key == "map" and compact in _MAP_CHROME:
            continue
        if compact in grounded or compact in visible_compact:
            out.append(row)
            continue
        if screen_key == "combat" and compact in _COMBAT_OCR_CONTROL_BLOCK and not explanatory_tutorial:
            if compact not in clickable_compact:
                continue
        if compact in vlm_native:
            if compact in _DIALOG_BUTTONS and screen_key in _STALE_DIALOG_SCREENS:
                if compact in grounded:
                    out.append(row)
                continue
            out.append(row)
    return out


def _merge_host_screen(screen: str, hud: dict[str, Any] | None) -> str:
    host = str((hud or {}).get("screen") or "").strip().lower()
    current = (screen or "").strip().lower()
    if host in {"", "other", "null"}:
        return screen
    if not current or current in {"other", "null"}:
        return host
    if current == "combat" and host == "tutorial":
        return host
    return screen


def _merge_host_tutorial(tutorial: dict[str, Any], hud: dict[str, Any] | None) -> dict[str, Any]:
    if tutorial.get("active"):
        return tutorial
    ocr = str((hud or {}).get("ocr") or "")
    step_match = _TUTORIAL_STEP_RE.search(ocr)
    low = ocr.lower()
    if not step_match and "combat basics" not in low and "map basics" not in low:
        return tutorial
    out = dict(tutorial)
    out["active"] = True
    if not out.get("title"):
        if "combat basics" in low:
            out["title"] = "Combat Basics"
        elif "map basics" in low:
            out["title"] = "Map Basics"
    if step_match:
        out["step"] = int(step_match.group(1))
        out["total"] = int(step_match.group(2))
    return out


def _is_unidentified_enemy(label: str) -> bool:
    return is_unknown_entity_label(label) or _compact(label) in {
        _UNIDENTIFIED_ENEMY,
        "unidentified enemy",
    }


def _has_valid_bbox(bbox: Any) -> bool:
    if isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
        return True
    if isinstance(bbox, str) and bbox.strip():
        parts = [p.strip() for p in bbox.split(",") if p.strip()]
        return len(parts) >= 4
    return False


def _entity_label(row: dict[str, Any]) -> str | None:
    """Return label/name from an entity row; None when absent or JSON null."""
    if "label" in row:
        raw = row.get("label")
    elif "name" in row:
        raw = row.get("name")
    else:
        return None
    if raw is None:
        return None
    text = str(raw).strip()
    if not text or text.lower() in {"null", "none"}:
        return None
    return text


def _enemy_row_present(row: dict[str, str]) -> bool:
    label = str(row.get("label") or "").strip()
    if label and not _looks_like_stat(label):
        return True
    return _has_valid_bbox(row.get("bbox"))


def reconcile_combat_screen(screen: str, *, ocr: str | None = None) -> str:
    """Downgrade stale combat when OCR shows a deck/card overlay instead."""
    current = (screen or "").strip().lower()
    if current != "combat":
        return screen
    blob = (ocr or "").strip().lower()
    if not blob:
        return screen
    if any(marker in blob for marker in _COMBAT_SCREEN_MARKERS):
        return screen
    deck_hits = sum(1 for marker in _DECK_OVERLAY_MARKERS if marker in blob)
    if deck_hits >= 2 or ("prev" in blob and "deck" in blob):
        return "unknown"
    return screen


def _row_from_slot(row: Any, *, default_role: str) -> dict[str, str] | None:
    if not isinstance(row, dict):
        return None
    label = _entity_label(row)
    has_bbox = _has_valid_bbox(row.get("bbox"))
    role = str(row.get("role") or default_role).strip()
    if not label and role in {"enemy", "unknown_character", "character"} and has_bbox:
        label = _UNIDENTIFIED_ENEMY if role == "enemy" else "unknown_character"
    if not label and not has_bbox:
        return None
    if label and _looks_like_stat(label):
        return None
    if not label:
        label = "unknown_character"
    confidence = str(row.get("confidence") or "").strip().lower()
    if confidence not in {"high", "low"}:
        confidence = "low" if _is_unidentified_enemy(label) or role == "unknown_character" else "high"
    item: dict[str, str] = {"label": label, "role": role, "confidence": confidence}
    item["name"] = str(row.get("name") or label)
    item["name_source"] = str(row.get("name_source") or "unresolved")
    bbox = row.get("bbox")
    if has_bbox:
        if isinstance(bbox, str):
            item["bbox"] = bbox.strip()
        else:
            item["bbox"] = ",".join(str(v) for v in bbox[:4])  # type: ignore[index]
    entity_id = str(row.get("entity_id") or row.get("id") or "").strip()
    if entity_id:
        item["entity_id"] = entity_id
    inspectable = row.get("inspectable")
    if inspectable is not None:
        if isinstance(inspectable, bool):
            item["inspectable"] = "true" if inspectable else "false"
        else:
            item["inspectable"] = str(inspectable).strip().lower()
    elif role == "unknown_character" or _is_unidentified_enemy(label):
        item["inspectable"] = "true"
    for key in ("source", "playable", "targetable"):
        value = row.get(key)
        if value is not None:
            item[key] = str(value).strip().lower() if isinstance(value, bool) else str(value).strip()
    return item


def _merge_typed_slots(
    parsed: dict[str, Any],
    controls: list[dict[str, str]],
    *,
    uncertain_compact: set[str],
) -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, str]], list[dict[str, str]], list[str]]:
    """Merge hud/hand_cards/enemies typed slots into controls; return typed lists."""
    hud_rows: list[dict[str, str]] = []
    hand_rows: list[dict[str, str]] = []
    enemy_rows: list[dict[str, str]] = []
    overlays: list[str] = []
    if parsed.get("hand_cards"):
        # The typed slot is instance-addressable; mirrored label-only controls
        # would collapse duplicates and must not become a second card inventory.
        controls[:] = [row for row in controls if str(row.get("role") or "") != "card"]
    have = {
        str(c.get("entity_id") or _compact(c["label"]))
        for c in controls if c.get("label")
    }
    for raw in parsed.get("overlays_active") or []:
        s = str(raw or "").strip()
        if s and s not in overlays:
            overlays.append(s)
    character_rows: list[dict[str, str]] = []
    for slot, default_role, bucket in (
        (parsed.get("hud") or [], "button", hud_rows),
        (parsed.get("hand_cards") or [], "card", hand_rows),
        (parsed.get("enemies") or [], "enemy", enemy_rows),
        (parsed.get("characters") or [], "unknown_character", character_rows),
    ):
        for row in slot:
            item = _row_from_slot(row, default_role=default_role)
            if item is None:
                continue
            bucket.append(item)
            compact = _compact(item["label"])
            identity = str(item.get("entity_id") or compact) if default_role == "card" else compact
            if identity in have:
                continue
            have.add(identity)
            if item["confidence"] == "low" or compact in uncertain_compact:
                item = dict(item)
                if item["confidence"] != "low":
                    item["confidence"] = "low"
            controls.append(item)
    return hud_rows, hand_rows, enemy_rows, character_rows, overlays


def _host_enemy_hp_present(host_stats: dict[str, Any] | None) -> bool:
    for row in (host_stats or {}).get("enemies") or []:
        if not isinstance(row, dict):
            continue
        hp = row.get("hp")
        hp_max = row.get("hp_max")
        if isinstance(hp, (int, float)) and isinstance(hp_max, (int, float)) and hp_max > 0:
            return True
    return False


def validate_combat_perceive(
    desc: SceneDescription,
    host_stats: dict[str, Any] | None = None,
) -> list[str]:
    """Hard-fail when combat screen lacks enemies or OCR/LLM1 disagree."""
    screen = str(desc.screen or (host_stats or {}).get("screen") or "").strip().lower()
    if screen != "combat":
        return []
    combat = desc.combat if isinstance(desc.combat, dict) else {}
    if combat.get("active") is False:
        return []
    def _discovery_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
        return [
            row
            for row in rows
            if is_discovery_role(str(row.get("role") or "")) or str(row.get("role") or "") == "enemy"
        ]

    discovery = _discovery_rows(list(desc.controls)) + _discovery_rows(list(desc.enemies))
    enemy_labels = [
        str(row.get("label") or "").strip()
        for row in discovery
        if _enemy_row_present(row)
    ]
    high_enemies = [
        str(row.get("label") or "").strip()
        for row in discovery
        if str(row.get("confidence") or "").strip().lower() == "high"
        and _enemy_row_present(row)
        and not _is_unidentified_enemy(str(row.get("label") or ""))
    ]
    if not enemy_labels:
        for row in desc.enemies:
            if _enemy_row_present(row):
                enemy_labels.append(str(row.get("label") or _UNIDENTIFIED_ENEMY).strip())
    if not high_enemies:
        for row in desc.enemies:
            label = str(row.get("label") or "").strip()
            conf = str(row.get("confidence") or "high").strip().lower()
            if not _enemy_row_present(row):
                continue
            if _is_unidentified_enemy(label):
                continue
            if label and conf == "high":
                high_enemies.append(label)
    unnamed_present = any(
        _is_unidentified_enemy(str(row.get("label") or ""))
        or str(row.get("entity_id") or "").startswith("unknown_character")
        for row in discovery
    )
    errors: list[str] = []
    if not enemy_labels:
        errors.append("combat active but no enemy labels in perception")
    elif not high_enemies and not unnamed_present:
        errors.append("combat enemies present but none high-confidence")
    if _host_enemy_hp_present(host_stats) and not enemy_labels:
        errors.append("host OCR shows enemy HP but LLM1 returned no enemies")
    return errors


def should_skip_combat_perceive_rescan(
    errors: list[str],
    host_stats: dict[str, Any] | None,
) -> bool:
    """Skip expensive LLM1 rescan when host OCR already proves enemies are present."""
    if not errors or not _host_enemy_hp_present(host_stats):
        return False
    return all("enemy" in e.lower() or "host ocr" in e.lower() for e in errors)


def inject_host_enemies_into_scene(
    desc: SceneDescription,
    host_stats: dict[str, Any] | None,
) -> SceneDescription:
    """Backfill enemy rows from host OCR when LLM1 omitted them (HP count, not HP→name)."""
    rows = (host_stats or {}).get("enemies") or []
    if not rows:
        return desc
    have = {
        _compact(str(row.get("label") or ""))
        for row in desc.controls
        if str(row.get("role") or "") == "enemy" and str(row.get("label") or "").strip()
    }
    have |= {
        _compact(str(row.get("label") or ""))
        for row in desc.enemies
        if str(row.get("label") or "").strip()
    }
    for row in rows:
        if not isinstance(row, dict):
            continue
        hp_max = row.get("hp_max")
        if not isinstance(hp_max, (int, float)) or hp_max <= 0:
            continue
        label = str(row.get("label") or "").strip()
        if not label:
            label = _UNIDENTIFIED_ENEMY
        key = _compact(label)
        if key in have:
            continue
        conf = "high" if label != _UNIDENTIFIED_ENEMY else "low"
        entry: dict[str, str] = {
            "label": label,
            "role": "enemy",
            "confidence": conf,
            "source": "host_ocr",
        }
        desc.enemies.append(entry)
        desc.controls.append(dict(entry))
        have.add(key)
    return desc


@dataclass
class SceneDescription:
    scene: str = ""
    screen: str = ""
    controls: list[dict[str, str]] = field(default_factory=list)
    dialog: dict[str, Any] = field(default_factory=dict)
    combat: dict[str, Any] = field(default_factory=dict)
    tutorial: dict[str, Any] = field(default_factory=dict)
    tutorial_text: str = ""
    turn: str = ""
    visible_text: list[str] = field(default_factory=list)
    uncertain: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    legal_actions: list[dict[str, Any]] = field(default_factory=list)
    elements: list[dict[str, str]] = field(default_factory=list)
    hud: list[dict[str, str]] = field(default_factory=list)
    hand_cards: list[dict[str, str]] = field(default_factory=list)
    enemies: list[dict[str, str]] = field(default_factory=list)
    characters: list[dict[str, str]] = field(default_factory=list)
    overlays_active: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
    fallback: bool = False
    schema_valid: bool = True
    llm1_raw_text: str = ""
    llm1_raw: dict[str, Any] = field(default_factory=dict)
    validated_perception: dict[str, Any] = field(default_factory=dict)
    llm1_attempts: list[dict[str, Any]] = field(default_factory=list)
    llm1_first_pass_valid: bool = False
    llm1_final_schema_valid: bool = False
    llm1_schema_attempts: int = 0

    @classmethod
    def from_parsed(
        cls,
        parsed: dict[str, Any] | None,
        *,
        hud: dict[str, Any] | None = None,
        host_stats: dict[str, Any] | None = None,
        clickable_labels: list[str] | None = None,
        cv_verdicts: dict[str, AffordanceSignal] | None = None,
    ) -> SceneDescription:
        if not parsed:
            screen = str((hud or {}).get("screen") or "")
            return cls(scene="", screen=screen, fallback=True)
        # Normalize model output against same-frame evidence before any scene
        # text, controls, or typed slots are assembled.
        parsed = dict(parsed)
        observed_names = _observed_name_set(
            hud=hud, host_stats=host_stats, clickable_labels=clickable_labels
        )
        for slot, default_role in (("controls", "other"), ("enemies", "enemy"), ("characters", "unknown_character"),
                                   ("hand_cards", "card"), ("hud", "button")):
            rows = parsed.get(slot)
            if isinstance(rows, list):
                normalized: list[Any] = []
                for row in rows:
                    if isinstance(row, dict) and (slot != "controls" or str(row.get("role") or "") in {"enemy", "character", "unknown_character"}):
                        normalized.append(_ground_entity_row(
                            row, observed=observed_names,
                            role=str(row.get("role") or default_role).strip(),
                        ))
                    else:
                        normalized.append(row)
                parsed[slot] = normalized
        combat_value = parsed.get("combat")
        if isinstance(combat_value, dict) and isinstance(combat_value.get("enemies"), list):
            combat_copy = dict(combat_value)
            def _ground_combat_enemy(value: Any) -> Any:
                if isinstance(value, str) and _looks_like_stat(value):
                    return value
                row = value if isinstance(value, dict) else {"label": value, "role": "enemy"}
                return _ground_entity_row(row, observed=observed_names, role="enemy")["label"]
            combat_copy["enemies"] = [
                _ground_combat_enemy(value)
                for value in combat_value["enemies"]
            ]
            parsed["combat"] = combat_copy
        screen = str(parsed.get("screen") or (hud or {}).get("screen") or "").strip()
        controls: list[dict[str, str]] = []
        vlm_native: set[str] = set()
        uncertain: list[str] = []
        for row in parsed.get("uncertain") or []:
            s = str(row or "").strip()
            if s and s not in uncertain:
                uncertain.append(s)
        uncertain_compact = {_compact(u) for u in uncertain}
        for row in parsed.get("controls") or parsed.get("elements") or []:
            if isinstance(row, dict):
                label = _entity_label(row) or ""
                role = str(row.get("role") or row.get("region") or "other").strip()
                has_bbox = _has_valid_bbox(row.get("bbox"))
                if not label and role in {"enemy", "unknown_character", "character"} and has_bbox:
                    label = "unknown_character"
                if not label and not has_bbox:
                    continue
                if label and _looks_like_stat(label):
                    continue
                if not label:
                    label = "unknown_character"
                confidence = str(row.get("confidence") or "").strip().lower()
                if confidence not in {"high", "low"}:
                    confidence = "low" if _compact(label) in uncertain_compact else "high"
                entry: dict[str, str] = {
                    "label": label, "name": str(row.get("name") or label),
                    "name_source": str(row.get("name_source") or "unresolved"),
                    "role": role, "confidence": confidence,
                }
                if has_bbox:
                    bbox = row.get("bbox")
                    if isinstance(bbox, str):
                        entry["bbox"] = bbox.strip()
                    else:
                        entry["bbox"] = ",".join(str(v) for v in bbox[:4])  # type: ignore[index]
                inspectable = row.get("inspectable")
                if inspectable is not None:
                    if isinstance(inspectable, bool):
                        entry["inspectable"] = "true" if inspectable else "false"
                    else:
                        entry["inspectable"] = str(inspectable).strip().lower()
                controls.append(entry)
                vlm_native.add(_compact(label))
        dialog_raw = parsed.get("dialog")
        dialog: dict[str, Any] = {}
        if isinstance(dialog_raw, dict):
            dialog = dict(dialog_raw)
        combat_raw = parsed.get("combat")
        combat: dict[str, Any] = {}
        if isinstance(combat_raw, dict):
            combat = dict(combat_raw)
        enemy_labels = {
            _compact(c["label"]) for c in controls if c.get("role") == "enemy"
        }
        for raw in combat.get("enemies") or []:
            label = str(raw or "").strip()
            if not label or _looks_like_stat(label):
                continue
            compact = _compact(label)
            if compact in enemy_labels:
                continue
            enemy_labels.add(compact)
            confidence = "low" if compact in uncertain_compact else "high"
            controls.append({
                "label": label, "name": label,
                "name_source": "unresolved" if _is_unidentified_enemy(label) else "ocr",
                "role": "enemy", "confidence": confidence,
            })
        hud_rows, hand_rows, enemy_rows, character_rows, overlays = _merge_typed_slots(
            parsed,
            controls,
            uncertain_compact=uncertain_compact,
        )
        tutorial_raw = parsed.get("tutorial")
        tutorial: dict[str, Any] = {}
        if isinstance(tutorial_raw, dict):
            tutorial = {k: v for k, v in tutorial_raw.items() if v is not None}
        tutorial_text = ""
        if tutorial.get("active"):
            title = str(tutorial.get("title") or "").strip()
            step, total = tutorial.get("step"), tutorial.get("total")
            if title and isinstance(step, int) and isinstance(total, int):
                tutorial_text = f"{title} ({step} of {total})"
            elif title:
                tutorial_text = title
        legacy = parsed.get("tutorial_text")
        if legacy is not None and str(legacy).strip().lower() not in {"", "null", "none"}:
            tutorial_text = tutorial_text or str(legacy).strip()
        scene = str(parsed.get("scene") or "").strip()
        if not scene:
            bits = [f"screen={screen or 'other'}"]
            if tutorial_text:
                bits.append(f"tutorial={tutorial_text}")
            labels = [c["label"] for c in controls[:8]]
            if labels:
                bits.append("controls=" + ", ".join(labels))
            scene = "; ".join(bits)
        turn = str(parsed.get("turn") or "").strip()
        if turn.lower() in {"null", "none"}:
            turn = ""
        visible: list[str] = []
        for row in parsed.get("visible_text") or []:
            s = str(row or "").strip()
            if s and s not in visible and not _looks_like_stat(s):
                visible.append(s)
        screen = _merge_host_screen(screen, hud)
        ocr_blob = str((hud or {}).get("ocr") or "")
        screen = reconcile_combat_screen(screen, ocr=ocr_blob)
        host_screen = str((host_stats or {}).get("screen") or (hud or {}).get("screen") or "").strip().lower()
        if host_screen and host_screen not in {"", "other", "null"}:
            parsed_screen = (screen or "").strip().lower()
            if not parsed_screen or parsed_screen in {"other", "null"}:
                screen = host_screen
            elif host_screen == "tutorial" and parsed_screen in {"combat", "map"}:
                screen = host_screen
        tutorial = _merge_host_tutorial(tutorial, hud)
        merged_host_stats = dict(host_stats or {})
        if tutorial:
            merged_host_stats["tutorial"] = tutorial
        controls = _promote_known_controls(
            controls,
            visible_text=visible,
            hud=hud,
            host_stats=merged_host_stats,
            clickable_labels=clickable_labels,
        )
        controls = _ground_controls(
            controls,
            vlm_native=vlm_native,
            visible_text=visible,
            hud=hud,
            host_stats=merged_host_stats,
            clickable_labels=clickable_labels,
            screen=screen,
        )
        real_tutorial = _tutorial_overlay_chrome(controls)
        if tutorial.get("active") and real_tutorial and screen not in {"", "tutorial"}:
            if screen in {"combat", "map", "other"}:
                screen = "tutorial"
        if _explanatory_tutorial_overlay(tutorial, host_stats=host_stats) and real_tutorial:
            controls = [
                row
                for row in controls
                if _compact(str(row.get("label") or "")) in _PAGINATED_TUTORIAL_NAV
            ]
        if tutorial.get("active") and not tutorial_text:
            title = str(tutorial.get("title") or "").strip()
            step, total = tutorial.get("step"), tutorial.get("total")
            if title and isinstance(step, int) and isinstance(total, int):
                tutorial_text = f"{title} ({step} of {total})"
            elif title:
                tutorial_text = title
        elements = [{"name": c["label"], "region": c.get("role", "")} for c in controls]
        unresolved: list[str] = []
        for row in parsed.get("unresolved") or []:
            s = str(row or "").strip()
            if s and s not in unresolved:
                unresolved.append(s)
        legal_actions = merge_legal_actions(
            parsed.get("legal_actions") if isinstance(parsed.get("legal_actions"), list) else None,
            cv_verdicts or {},
        )
        return cls(
            scene=scene,
            screen=screen,
            controls=controls,
            dialog=dialog,
            combat=combat,
            tutorial=tutorial,
            tutorial_text=tutorial_text,
            turn=turn,
            visible_text=visible,
            uncertain=uncertain,
            unresolved=unresolved,
            legal_actions=legal_actions,
            elements=elements,
            hud=hud_rows,
            hand_cards=hand_rows,
            enemies=enemy_rows,
            characters=character_rows,
            overlays_active=overlays,
            raw=dict(parsed),
        )

    def interactive_labels(self) -> list[str]:
        """Clickable / playable labels only — never tutorial prose."""
        labels: list[str] = []
        for row in self.controls:
            lab = str(row.get("label") or "").strip()
            if lab and lab not in labels:
                labels.append(lab)
        opts = self.dialog.get("options")
        if isinstance(opts, list):
            for opt in opts:
                s = str(opt or "").strip()
                if s and s not in labels:
                    labels.append(s)
        for key in ("enemies", "hand"):
            vals = self.combat.get(key)
            if isinstance(vals, list):
                for val in vals:
                    s = str(val or "").strip()
                    if s and s not in labels and not _looks_like_stat(s):
                        labels.append(s)
        return labels

    def control_labels(self) -> list[str]:
        """Backward-compatible alias for interactive_labels()."""
        return self.interactive_labels()

    def informational_text(self) -> list[str]:
        return list(self.visible_text)

    def dialog_options(self) -> list[str]:
        opts = self.dialog.get("options")
        if not isinstance(opts, list):
            return []
        return [str(o).strip() for o in opts if str(o or "").strip()]

    def as_text(self) -> str:
        parts: list[str] = []
        if self.screen:
            parts.append(f"screen={self.screen}")
        if self.turn:
            parts.append(f"turn={self.turn}")
        if self.tutorial_text:
            parts.append(f"tutorial={self.tutorial_text}")
        dialog_q = str(self.dialog.get("prompt") or "").strip()
        if dialog_q:
            parts.append(f"dialog={dialog_q}")
        opts = self.dialog_options()
        if opts:
            parts.append("options=" + ", ".join(opts))
        labels = self.interactive_labels()
        if labels:
            parts.append("interactive=" + ", ".join(labels[:12]))
        if self.visible_text:
            parts.append("visible=" + ", ".join(self.visible_text[:8]))
        if self.uncertain:
            parts.append("uncertain=" + ", ".join(self.uncertain[:6]))
        if self.unresolved:
            parts.append("unresolved=" + ", ".join(self.unresolved[:6]))
        if self.legal_actions:
            legal_bits = [
                f"{row.get('mark')}:{row.get('verdict')}"
                for row in self.legal_actions[:8]
                if isinstance(row, dict) and row.get("mark")
            ]
            if legal_bits:
                parts.append("legal=" + ", ".join(legal_bits))
        if not parts and self.scene:
            return self.scene
        return "; ".join(parts)


def _validate_entity_rows(slot: str, rows: Any, errors: list[str]) -> None:
    if not isinstance(rows, list):
        errors.append(f"{slot} must be a list")
        return
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            errors.append(f"{slot}[{i}] must be an object")
            continue
        if "label" not in row:
            errors.append(f"{slot}[{i}] missing label")
        if "name" not in row:
            errors.append(f"{slot}[{i}] missing name")
        label = _entity_label(row)
        role = str(row.get("role") or "").strip()
        if not role:
            errors.append(f"{slot}[{i}] missing role")
        conf = str(row.get("confidence") or "").strip().lower()
        if conf not in {"high", "low"}:
            errors.append(f"{slot}[{i}] missing or invalid confidence")
        if slot in {"characters", "enemies"} and "inspectable" not in row:
            errors.append(f"{slot}[{i}] missing inspectable")
        name_source = str(row.get("name_source") or "").strip().lower()
        if not name_source:
            errors.append(f"{slot}[{i}] missing name_source")
        elif name_source not in _NAME_SOURCES:
            errors.append(f"{slot}[{i}] invalid name_source")
        if name_source == "unresolved" and label and not is_unknown_entity_label(label):
            errors.append(f"{slot}[{i}] named entity has unresolved name_source")
        needs_bbox = slot in {"hand_cards", "characters", "enemies"}
        if needs_bbox and not _has_valid_bbox(row.get("bbox")):
            errors.append(f"{slot}[{i}] missing bbox (required for actionable geometry)")
        if slot == "hand_cards":
            if not str(row.get("id") or "").strip():
                errors.append(f"{slot}[{i}] missing id")
            if role != "card":
                errors.append(f"{slot}[{i}] role must be card")
            if "playable" not in row:
                errors.append(f"{slot}[{i}] missing playable")


def schema_repair_hint(errors: list[str]) -> str:
    """Compact repair prompt from the actual validation errors."""
    if not errors:
        return ""
    lines = "\n".join(f"- {item}" for item in errors[:12])
    return (
        "\n\nYour prior JSON failed validation. Fix ONLY these errors:\n"
        f"{lines}\n"
        "Unknown names: label=null, name=null, name_source=\"unresolved\". "
        "Required lists: legal_actions, unresolved, hand_cards, characters, enemies. "
        "Compact JSON only."
    )


def classify_perceive_parse_failure(raw_text: str | None) -> str:
    """Distinguish blank output from truncated / malformed JSON.

    ``validate_perceive_schema(None)`` used to always report ``empty parse``, but
    Combat Basics failures are usually mid-object truncation under ``num_predict``.
    """
    text = (raw_text or "").strip()
    if not text:
        return "empty parse"
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].lstrip()
    opens = text.count("{")
    closes = text.count("}")
    if opens > closes or not text.rstrip().endswith(("}", "]")):
        return "truncated json"
    return "json_parse_failed"


def validate_perceive_schema(
    parsed: dict[str, Any] | None,
    *,
    raw_text: str | None = None,
) -> list[str]:
    """Return validation errors for required perceive fields."""
    if not parsed:
        return [classify_perceive_parse_failure(raw_text)]
    errors: list[str] = []
    if "legal_actions" not in parsed:
        errors.append("missing legal_actions")
    elif not isinstance(parsed.get("legal_actions"), list):
        errors.append("legal_actions must be a list")
    if "unresolved" not in parsed:
        errors.append("missing unresolved")
    elif not isinstance(parsed.get("unresolved"), list):
        errors.append("unresolved must be a list")
    for slot in ("characters", "enemies", "hand_cards"):
        if slot not in parsed:
            errors.append(f"missing {slot}")
        else:
            _validate_entity_rows(slot, parsed.get(slot), errors)
    return errors


def revalidate_grounded_perception(desc: SceneDescription) -> list[str]:
    """Catch label/name_source contradictions after OCR/entity fusion."""
    errors: list[str] = []
    for slot, rows in (
        ("hand_cards", desc.hand_cards),
        ("characters", desc.characters),
        ("enemies", desc.enemies),
    ):
        for i, row in enumerate(rows or []):
            if not isinstance(row, dict):
                continue
            label = _entity_label(row) or ""
            source = str(row.get("name_source") or "").strip().lower()
            if source == "unresolved" and label and not is_unknown_entity_label(label):
                errors.append(f"grounded {slot}[{i}] named entity has unresolved name_source")
                row["label"] = "unknown_card" if slot == "hand_cards" else (
                    "unknown_enemy" if slot == "enemies" else "unknown_character"
                )
                row["name"] = row["label"]
                row["name_source"] = "unresolved"
    return errors


def _call_perceive_json(
    gateway: VlmGateway,
    *,
    system: str,
    user: str,
    frame: EncodedFrame,
    max_tokens: int | None,
    timeout_s: float | None = None,
    history: list[dict[str, str]] | None = None,
) -> ModelCall:
    kwargs: dict[str, Any] = {
        "system": system,
        "user": user,
        "frames": [frame],
        "max_tokens": max_tokens,
    }
    if timeout_s is not None:
        kwargs["timeout_s"] = timeout_s
    if history:
        kwargs["history"] = history
    try:
        return gateway.complete_json(**kwargs, json_schema=SCENE_PERCEIVE_SCHEMA)
    except TypeError:
        try:
            return gateway.complete_json(**kwargs)
        except TypeError:
            kwargs.pop("timeout_s", None)
            try:
                return gateway.complete_json(**kwargs)
            except TypeError:
                kwargs.pop("history", None)
                return gateway.complete_json(**kwargs)


def perceive_scene(
    gateway: VlmGateway,
    frame: EncodedFrame,
    *,
    hud: dict[str, Any] | None = None,
    host_stats: dict[str, Any] | None = None,
    clickable_labels: list[str] | None = None,
    detector_labels: list[str] | None = None,
    max_tokens: int | None = None,
    perceive_system: str | None = None,
    perceive_user: str | None = None,
    cv_verdicts: dict[str, AffordanceSignal] | None = None,
    max_schema_retries: int = 2,
    timeout_s: float | None = None,
    thread: EpisodeThread | None = None,
) -> tuple[SceneDescription, ModelCall]:
    system = perceive_system or scene_perceive_system()
    user = perceive_user or scene_perceive_user(
        hud=hud,
        host_stats=host_stats,
        detector_labels=detector_labels if detector_labels is not None else clickable_labels,
        affordance_hints=cv_verdicts,
    )
    last_call: ModelCall | None = None
    parsed: dict[str, Any] | None = None
    last_candidate: dict[str, Any] | None = None
    last_errors: list[str] = []
    attempts: list[dict[str, Any]] = []
    first_pass_valid = False
    truncate_retry_hint = (
        "\n\nYour prior JSON was CUT OFF mid-object (truncated). "
        "Emit COMPACT single-line JSON with no pretty-print whitespace. "
        "Still include legal_actions, unresolved, hand_cards, characters, and enemies "
        "(lists may be empty). Finish every brace."
    )
    for attempt in range(max_schema_retries + 1):
        attempt_max_tokens = max_tokens
        if attempt == 0:
            attempt_user = user
        elif "truncated json" in last_errors:
            attempt_user = user + truncate_retry_hint + schema_repair_hint(last_errors)
            if attempt_max_tokens is not None:
                attempt_max_tokens *= 2
        else:
            attempt_user = user + schema_repair_hint(last_errors)
        call = _call_perceive_json(
            gateway,
            system=system,
            user=attempt_user,
            frame=frame,
            max_tokens=attempt_max_tokens,
            timeout_s=timeout_s,
            history=thread.messages(system=system, user=attempt_user) if thread is not None else None,
        )
        last_call = call
        candidate = (
            normalize_perceive_provenance(call.parsed)
            if isinstance(call.parsed, dict)
            else None
        )
        if candidate is not None:
            last_candidate = candidate
        errors = validate_perceive_schema(candidate, raw_text=call.text)
        last_errors = list(errors)
        accepted = not errors
        if attempt == 0:
            first_pass_valid = accepted
        attempts.append({
            "attempt": attempt + 1,
            "raw": call.text,
            "errors": list(errors),
            "token_budget": attempt_max_tokens,
            "latency_ms": call.latency_ms,
            "accepted": accepted,
        })
        if errors:
            level = _LOG.warning if attempt + 1 < max_schema_retries + 1 else _LOG.error
            level(
                "LLM1 schema validation failed attempt=%d/%d first_pass=%s errors=%s",
                attempt + 1,
                max_schema_retries + 1,
                first_pass_valid,
                errors,
            )
        else:
            _LOG.info(
                "LLM1 schema valid attempt=%d model=%s latency_ms=%.1f",
                attempt + 1,
                call.model,
                call.latency_ms,
            )
            parsed = candidate
            break
    grounded = detector_labels if detector_labels is not None else clickable_labels
    cv = cv_verdicts or {}
    if parsed is not None:
        desc = SceneDescription.from_parsed(
            parsed,
            hud=hud,
            host_stats=host_stats,
            clickable_labels=grounded,
            cv_verdicts=cv_verdicts,
        )
        desc.schema_valid = True
        revalidate_grounded_perception(desc)
    elif isinstance(last_candidate, dict):
        screen = str(
            last_candidate.get("screen") or (hud or {}).get("screen") or ""
        )
        desc = SceneDescription(
            scene=f"screen={screen or 'other'}; perceive_schema_invalid",
            screen=screen,
            fallback=True,
            schema_valid=False,
            unresolved=[],
            legal_actions=merge_legal_actions([], cv),
        )
    else:
        desc = SceneDescription.from_parsed(
            None,
            hud=hud,
            host_stats=host_stats,
            clickable_labels=grounded,
            cv_verdicts=cv_verdicts,
        )
        desc.schema_valid = False
    if desc.fallback and hud and parsed is None and not isinstance(last_candidate, dict):
        screen = str(hud.get("screen") or "")
        ocr = str(hud.get("ocr") or "").strip()
        scene = f"OCR screen={screen}" if screen else ""
        if ocr:
            scene = (scene + " " + " ".join(ocr.split())[:300]).strip()
        desc = SceneDescription(
            scene=scene,
            screen=screen,
            fallback=True,
            schema_valid=False,
            unresolved=[],
            legal_actions=merge_legal_actions(None, cv),
        )
    assert last_call is not None
    desc.llm1_raw_text = last_call.text
    desc.llm1_raw = dict(last_candidate) if isinstance(last_candidate, dict) else {}
    desc.validated_perception = dict(parsed) if isinstance(parsed, dict) else {}
    desc.llm1_attempts = attempts
    desc.llm1_first_pass_valid = first_pass_valid
    desc.llm1_final_schema_valid = bool(parsed is not None and desc.schema_valid)
    desc.llm1_schema_attempts = len(attempts)
    if thread is not None and parsed is not None and desc.schema_valid:
        thread.commit("llm1", json.dumps(parsed, ensure_ascii=False, separators=(",", ":")))
    return desc, last_call


_FRAME_ROLE_MAP = {
    "card": "card",
    "target": "enemy",
    "button": "button",
    "nav_prev": "button",
    "nav_next": "button",
    "icon": "button",
}


def enemy_signature(host_stats: dict[str, Any] | None) -> tuple[tuple[Any, ...], ...]:
    """Stable combat enemy fingerprint for appear/disappear / HP drift detection."""
    rows = (host_stats or {}).get("enemies") or []
    parts: list[tuple[Any, ...]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        parts.append(
            (
                row.get("hp"),
                row.get("hp_max"),
                str(row.get("label") or "").strip().lower() or None,
            )
        )
    return tuple(sorted(parts))


def combat_materially_changed(
    prev_host: dict[str, Any] | None,
    host_stats: dict[str, Any] | None,
) -> bool:
    """True when enemy roster or HP changed between frames."""
    return enemy_signature(prev_host) != enemy_signature(host_stats)


def _has_unidentified_enemies(desc: SceneDescription | None) -> bool:
    if desc is None:
        return False
    for bucket in (desc.enemies, desc.controls, desc.characters):
        for row in bucket:
            if not isinstance(row, dict):
                continue
            label = str(row.get("label") or "")
            role = str(row.get("role") or "")
            if _is_unidentified_enemy(label):
                return True
            if role in {"unknown_character", "character"} and str(
                row.get("inspectable") or ""
            ).strip().lower() in {"true", "1", "yes"}:
                return True
    return False


def _tracker_needs_inspect(entity_tracker: Any | None) -> bool:
    if entity_tracker is None:
        return False
    for ent in getattr(entity_tracker, "entities", []) or []:
        if getattr(ent, "inspectable", False) and not getattr(ent, "identified", False):
            return True
    return False


def _is_candidate_placeholder(label: str) -> bool:
    """True when a mark/candidate label carries no stable identity."""
    from loop.os.aim import _is_placeholder_label, _is_unknown_entity_label

    raw = str(label or "").strip()
    if not raw:
        return True
    if re.fullmatch(r"node_\d+", raw, re.I):
        return True
    if _is_placeholder_label(raw):
        return True
    if _is_unknown_entity_label(raw):
        return True
    compact = _compact(raw)
    if compact.startswith("unidentified ") or compact.startswith("unknown "):
        return True
    return False


def candidates_unidentified_only(labels: list[str] | None) -> bool:
    """True when every supplied candidate label is empty or a placeholder.

    Returns False when ``labels`` is omitted (``None``) so callers must pass an
    explicit candidate list to trigger this uncertainty signal.
    """
    if labels is None:
        return False
    return all(_is_candidate_placeholder(label) for label in labels)


def _is_first_observation(prev_screen: str, prev_host_stats: dict[str, Any] | None) -> bool:
    if (prev_screen or "").strip():
        return False
    if prev_host_stats is None:
        return True
    return not any(prev_host_stats.values())


def _scene_materially_changed(
    *,
    screen_key: str,
    prev_screen_key: str,
    activity: str,
    prev_activity: str,
    prev_screen: str,
    prev_host_stats: dict[str, Any] | None,
) -> bool:
    if _is_first_observation(prev_screen, prev_host_stats):
        return True
    if screen_key != prev_screen_key:
        return True
    return activity != prev_activity


def should_run_llm1_perceive(
    *,
    screen: str,
    prev_screen: str = "",
    host_stats: dict[str, Any] | None = None,
    prev_host_stats: dict[str, Any] | None = None,
    entity_tracker: Any | None = None,
    cached_scene: SceneDescription | None = None,
    force: bool = False,
    unverified_effect: bool = False,
    contradictory: bool = False,
    candidate_labels: list[str] | None = None,
) -> tuple[bool, str]:
    """Gate expensive LLM1 visual perception.

    Runs LLM1 on explicit uncertainty signals (force, unverified dispatch,
    contradiction, scene change, placeholder-only candidates) or combat cache
    misses / material HP-roster drift. Skips when evidence is stable.

    Optional kwargs for orchestrator wiring:

    - ``unverified_effect``: prior action succeeded but no verified pixel/task
      effect was observed.
    - ``contradictory``: OCR/state channels disagree materially.
    - ``candidate_labels``: mark labels from aim/frame; when every label is
      empty or placeholder, triggers ``unidentified-only``. Omitted (``None``)
      does not trigger that path.

    Unidentified combat sprites on an otherwise named cache use mechanical
    hover+OCR, not another Gemma pass (see combat-stable tests).
    """
    if force:
        return True, "forced"
    if unverified_effect:
        return True, "unverified-effect"
    if contradictory:
        return True, "contradictory"

    from loop.os.activity import activity_from_state

    screen_key = (screen or "").strip().lower()
    ocr_blob = str((host_stats or {}).get("ocr") or "")
    screen_key = reconcile_combat_screen(screen_key, ocr=ocr_blob).strip().lower()
    activity = activity_from_state(
        screen=screen_key,
        host_stats=host_stats,
    )

    prev_screen_key = (prev_screen or "").strip().lower()
    prev_ocr = str((prev_host_stats or {}).get("ocr") or "")
    prev_screen_key = reconcile_combat_screen(prev_screen_key, ocr=prev_ocr).strip().lower()
    prev_activity = activity_from_state(
        screen=prev_screen_key,
        host_stats=prev_host_stats,
    )

    if _scene_materially_changed(
        screen_key=screen_key,
        prev_screen_key=prev_screen_key,
        activity=activity,
        prev_activity=prev_activity,
        prev_screen=prev_screen,
        prev_host_stats=prev_host_stats,
    ):
        if prev_activity != "combat" and activity == "combat":
            return True, "entering-combat"
        return True, "scene-change"

    if candidates_unidentified_only(candidate_labels):
        return True, "unidentified-only"

    if activity == "combat":
        if cached_scene is None:
            return True, "no-cached-combat-scene"

        _ = entity_tracker  # unidentified sprites use mechanical hover+OCR, not LLM1

        if combat_materially_changed(prev_host_stats, host_stats):
            return True, "combat-material-change"

        return False, "combat-stable"

    return False, f"ocr-only:{screen_key or activity or 'other'}"


def _controls_from_frame_model(frame_model: Any | None) -> list[dict[str, str]]:
    if frame_model is None:
        return []
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for obj in frame_model.interactive():
        label = str(getattr(obj, "label", "") or "").strip()
        if not label or _looks_like_stat(label):
            continue
        compact = _compact(label)
        if compact in seen:
            continue
        seen.add(compact)
        role = _FRAME_ROLE_MAP.get(str(getattr(obj, "role", "") or ""), "other")
        conf_val = float(getattr(obj, "confidence", 1.0) or 1.0)
        confidence = "high" if conf_val >= 0.75 else "low"
        out.append({"label": label, "role": role, "confidence": confidence})
    return out


def _merge_cached_combat_controls(
    cached: SceneDescription,
    fresh: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Keep LLM1 enemy and hand-card rows; refresh menu controls from OCR."""
    discovery_roles = frozenset({"enemy", "unknown_character", "character"})
    kept = [
        dict(row)
        for row in cached.controls
        if isinstance(row, dict)
        and (
            str(row.get("role") or "") in discovery_roles
            or str(row.get("role") or "") == "card"
            or _is_unidentified_enemy(str(row.get("label") or ""))
        )
    ]
    have = {_compact(str(row.get("label") or "")) for row in kept if row.get("label")}
    merged = list(kept)
    for row in fresh:
        if not isinstance(row, dict):
            continue
        label = str(row.get("label") or "").strip()
        compact = _compact(label)
        if not compact or compact in have:
            continue
        role = str(row.get("role") or "other")
        if role in discovery_roles and any(
            _compact(str(k.get("label") or "")) == compact for k in kept
        ):
            continue
        merged.append(dict(row))
        have.add(compact)
    return merged


def scene_from_ocr(
    *,
    hud: dict[str, Any] | None = None,
    host_stats: dict[str, Any] | None = None,
    frame_model: Any | None = None,
    clickable_labels: list[str] | None = None,
    cached: SceneDescription | None = None,
    cv_verdicts: dict[str, AffordanceSignal] | None = None,
) -> SceneDescription:
    """Build a reasoner-ready scene from host OCR + aim marks (no LLM1)."""
    screen = str((host_stats or {}).get("screen") or (hud or {}).get("screen") or "").strip()
    ocr_blob = str((hud or {}).get("ocr") or (host_stats or {}).get("ocr") or "")
    screen = reconcile_combat_screen(screen, ocr=ocr_blob)
    fresh_controls = _controls_from_frame_model(frame_model)
    if not fresh_controls and clickable_labels:
        for label in clickable_labels:
            text = str(label or "").strip()
            if not text or _looks_like_stat(text):
                continue
            fresh_controls.append({"label": text, "role": "other", "confidence": "high"})
    fresh_controls = _promote_known_controls(
        fresh_controls,
        visible_text=[],
        hud=hud,
        host_stats=host_stats,
        clickable_labels=clickable_labels,
    )
    # Aim/OCR labels are already grounded; skip LLM1 hallucination filter.

    tutorial: dict[str, Any] = {}
    if isinstance((host_stats or {}).get("tutorial"), dict):
        tutorial = dict(host_stats["tutorial"])  # type: ignore[index]
    tutorial = _merge_host_tutorial(tutorial, hud)
    tutorial_text = ""
    if tutorial.get("active"):
        title = str(tutorial.get("title") or "").strip()
        step, total = tutorial.get("step"), tutorial.get("total")
        if title and isinstance(step, int) and isinstance(total, int):
            tutorial_text = f"{title} ({step} of {total})"
        elif title:
            tutorial_text = title

    turn = str((host_stats or {}).get("turn") or "").strip()
    if turn.lower() in {"null", "none", "unknown"}:
        turn = ""

    screen_key = (screen or "").strip().lower()
    use_cache = (
        cached is not None
        and screen_key == "combat"
        and (cached.screen or "").strip().lower() == "combat"
    )
    if use_cache and cached is not None:
        controls = _merge_cached_combat_controls(cached, fresh_controls)
        enemies = [dict(row) for row in cached.enemies if isinstance(row, dict)]
        characters = [dict(row) for row in cached.characters if isinstance(row, dict)]
        hand_cards = [dict(row) for row in cached.hand_cards if isinstance(row, dict)]
        if not hand_cards:
            hand_cards = [
                dict(row)
                for row in cached.controls
                if isinstance(row, dict) and str(row.get("role") or "") == "card"
            ]
        combat = dict(cached.combat) if isinstance(cached.combat, dict) else {}
        uncertain = list(cached.uncertain)
        unresolved = list(cached.unresolved)
        dialog = dict(cached.dialog) if isinstance(cached.dialog, dict) else {}
    else:
        controls = fresh_controls
        enemies = [row for row in fresh_controls if row.get("role") == "enemy"]
        characters = [
            row for row in fresh_controls if row.get("role") in {"unknown_character", "character"}
        ]
        hand_cards = [row for row in fresh_controls if row.get("role") == "card"]
        combat = {"active": screen_key == "combat"}
        uncertain = []
        unresolved = []
        dialog = {}

    desc = SceneDescription(
        scene=f"ocr screen={screen or 'other'}",
        screen=screen,
        controls=controls,
        dialog=dialog,
        combat=combat,
        tutorial=tutorial,
        tutorial_text=tutorial_text,
        turn=turn,
        enemies=enemies,
        characters=characters,
        hand_cards=hand_cards,
        uncertain=uncertain,
        unresolved=unresolved,
        legal_actions=merge_legal_actions(
            list(cached.legal_actions) if use_cache and cached is not None else [],
            cv_verdicts or {},
        ),
        fallback=True,
        schema_valid=True,
    )
    desc = inject_host_enemies_into_scene(desc, host_stats)
    bits = [f"screen={screen or 'other'}"]
    if tutorial_text:
        bits.append(f"tutorial={tutorial_text}")
    labels = [c["label"] for c in controls[:8] if c.get("label")]
    if labels:
        bits.append("controls=" + ", ".join(labels))
    desc.scene = "; ".join(bits)
    return desc
