"""Persistent entity IDs for unknown → identified sprite discovery."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from loop.memory.lexicon import MarkLexicon

_UNKNOWN_ENTITY_RE = re.compile(
    r"^(unknown_character(?:_\d+)?|unidentified_enemy|unidentified enemy|"
    r"unknown_enemy|unknown enemy)$",
    re.I,
)
# Player/ally sprites are never enemy-discovery candidates. Role ``character`` alone
# used to pull the player into the tracker and force explore loops.
_KNOWN_ENEMY_ROLES = frozenset({"enemy", "unknown_character"})
_PLAYER_SIDES = frozenset({"player", "ally", "self", "friendly", "hero"})
_ENEMY_SIDES = frozenset({"enemy", "hostile", "foe"})
_PLAYER_LABELS = frozenset(
    {
        "player",
        "ironclad",
        "silent",
        "defect",
        "watcher",
        "necrobinder",
        "the ironclad",
        "the silent",
        "the defect",
        "the watcher",
    }
)
_MATCH_RADIUS = 0.14  # normalized center distance for same sprite across frames
_STALE_CYCLES = 2  # drop tracker rows unseen for this many combat cycles


def _compact(text: str) -> str:
    return " ".join((text or "").lower().split())


def is_unknown_entity_label(label: str) -> bool:
    return bool(_UNKNOWN_ENTITY_RE.match(_compact(label)))


def is_anonymous_enemy_label(label: str) -> bool:
    """True when the label is a placeholder, not a proper enemy name."""
    compact = _compact(label).replace("_", " ")
    if not compact or compact in {"unknown", "icon", ":icon"}:
        return True
    if is_unknown_entity_label(label):
        return True
    return compact.startswith("unknown ")


def is_prompt_instance_id(value: str) -> bool:
    """True for LLM1/tracker placeholders that must not become store object ids."""
    raw = str(value or "").strip()
    if not raw:
        return False
    if is_unknown_entity_label(raw):
        return True
    compact = _compact(raw).replace("_", " ")
    return compact.startswith(("unknown character", "unidentified enemy", "unknown enemy"))


def is_discovery_role(role: str) -> bool:
    return _compact(role) in _KNOWN_ENEMY_ROLES


def player_display_label(label: str) -> str:
    """Stable overlay/target name for a recognized player sprite."""
    raw = str(label or "").strip()
    blob = _compact(raw).replace("_", " ")
    if blob in _PLAYER_LABELS and blob not in {"player"}:
        return raw
    if blob == "player" or blob.startswith("player "):
        return "player"
    return "player"


def is_player_row(row: dict[str, Any]) -> bool:
    """Deterministically exclude the player/ally from enemy discovery."""
    side = _compact(str(row.get("side") or ""))
    if side in _PLAYER_SIDES:
        return True
    label = _compact(str(row.get("label") or row.get("name") or ""))
    if label in _PLAYER_LABELS or label.startswith("player"):
        return True
    role = _compact(str(row.get("role") or ""))
    # Bare ``character`` without an enemy side is the player/hero sprite.
    if role == "character" and side not in _ENEMY_SIDES:
        return True
    return False


def is_unanchored_character_row(row: dict[str, Any]) -> bool:
    """Hero-like unknown_character/character with no geometry and no enemy side.

    LLM1 often dumps the player into controls as unknown_character without a bbox.
    Those rows are not inspectable hostiles.
    """
    if not isinstance(row, dict):
        return False
    if _row_center(row) is not None:
        return False
    side = _compact(str(row.get("side") or ""))
    if side in _ENEMY_SIDES:
        return False
    role = _compact(str(row.get("role") or ""))
    if role == "enemy":
        return False
    label = _compact(str(row.get("label") or row.get("name") or ""))
    blob = label.replace("_", " ")
    return role in {"character", "unknown_character"} or blob in {
        "unknown character",
        "character",
    } or (is_unknown_entity_label(label) and "enemy" not in blob)


def _row_center(row: dict[str, Any]) -> tuple[float, float] | None:
    bbox = _parse_bbox(row)
    center = _center(bbox)
    if center is not None:
        return center
    try:
        cx = float(row.get("x")) if row.get("x") is not None else None
        cy = float(row.get("y")) if row.get("y") is not None else None
    except (TypeError, ValueError):
        return None
    if cx is None or cy is None:
        return None
    if max(abs(cx), abs(cy)) > 1.0:
        cx, cy = cx / 1000.0, cy / 1000.0
    return (cx, cy)


def is_enemy_discovery_row(row: dict[str, Any]) -> bool:
    """Whether a perception row may enter the enemy entity tracker.

    Always needs a real center (never invent geometry). Player/ally rows are
    never candidates. A typed enemy/target role or hostile side is required;
    anonymous character-like rows cannot be promoted based on their position.
    """
    if not isinstance(row, dict) or is_player_row(row):
        return False
    center = _row_center(row)
    if center is None:
        return False
    role = _compact(str(row.get("role") or ""))
    side = _compact(str(row.get("side") or ""))
    return side in _ENEMY_SIDES or role in {"enemy", "target", "unknown_character"}


def is_targetable_anonymous_enemy(row: dict[str, Any]) -> bool:
    """Anonymous hostile sprite with real board geometry — still a valid target.

    Player/ally never qualify. Needs an anonymous label and a real center.
    Hostile role/side is targetable only when current-frame geometry exists.
    """
    if not isinstance(row, dict) or is_player_row(row):
        return False
    role = _compact(str(row.get("role") or ""))
    side = _compact(str(row.get("side") or ""))
    hostile = role in {"enemy", "target", "unknown_character"} or side in _ENEMY_SIDES
    if not hostile:
        return False
    label = str(row.get("label") or row.get("name") or row.get("entity_id") or "")
    if not is_anonymous_enemy_label(label):
        return False
    center = _row_center(row)
    if center is None:
        return False
    return True


_FRAME_MARK_RE = re.compile(r"^(?:mark|mk)_?\d+$", re.I)
_MARK_DERIVED_OID_RE = re.compile(r"^object:\d+$", re.I)
_STAT_FRACTION_RE = re.compile(r"^\d+\s*/\s*\d+$")


def is_frame_mark_token(text: str) -> bool:
    """True for frame-local mark ids such as mark_9 / mk_14 — never executable identity."""
    compact = re.sub(r"[\s-]+", "_", (text or "").strip())
    return bool(_FRAME_MARK_RE.match(compact))


def is_mark_derived_object_id(text: str) -> bool:
    """True for mark_N, mk_N, and object:N — never executable identity."""
    raw = str(text or "").strip()
    if not raw:
        return False
    if is_frame_mark_token(raw):
        return True
    return bool(_MARK_DERIVED_OID_RE.match(raw))


def is_hp_anchor_label(label: str) -> bool:
    """HP/energy OCR fragments must never become a second enemy."""
    blob = " ".join((label or "").lower().replace(":", "").split())
    if not blob:
        return False
    if _STAT_FRACTION_RE.match(blob.replace(" ", "")):
        return True
    return blob in {"hp", "energy", "/"}


def is_identified_row(row: dict[str, Any]) -> bool:
    """Verified name/identity, not a placeholder and not unresolved."""
    if not isinstance(row, dict) or is_player_row(row):
        return False
    source = str(row.get("name_source") or "").strip().lower()
    if source == "unresolved":
        return False
    label = str(row.get("label") or row.get("name") or "").strip()
    if not label or is_anonymous_enemy_label(label):
        return False
    return True


def is_hostile_inspectable_row(row: dict[str, Any]) -> bool:
    if not isinstance(row, dict) or is_player_row(row):
        return False
    role = _compact(str(row.get("role") or ""))
    side = _compact(str(row.get("side") or ""))
    hostile = role == "enemy" or side in _ENEMY_SIDES or role == "unknown_character"
    if not hostile:
        return False
    inspectable = str(row.get("inspectable") or "").strip().lower() in {"true", "1", "yes"}
    if inspectable:
        return True
    return not is_identified_row(row) and is_anonymous_enemy_label(
        str(row.get("label") or row.get("name") or "")
    )


def _row_matches_unresolved(row: dict[str, Any], label: str, unresolved: set[str]) -> bool:
    if not unresolved:
        return False
    keys = [
        label,
        str(row.get("entity_id") or ""),
        str(row.get("object_id") or ""),
        str(row.get("id") or ""),
    ]
    return any(key.strip().casefold() in unresolved for key in keys if key and str(key).strip())


def entity_route_facts(
    rows: list[dict[str, Any]],
    *,
    identify_before_act: bool = True,
    identity_unresolved: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Per-entity routing audit: targetable vs identified vs blocker."""
    unresolved = {key.strip().casefold() for key in (identity_unresolved or ()) if str(key).strip()}
    facts: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        label = str(row.get("label") or row.get("name") or row.get("entity_id") or "").strip()
        role = _compact(str(row.get("role") or ""))
        player = is_player_row(row)
        unanchored = is_unanchored_character_row(row)
        blob = _compact(label).replace("_", " ").lstrip(":")
        chrome = (
            role in {"hud", "icon", "other", "button", "card"}
            or is_hp_anchor_label(label)
            or (
                blob in {"", "icon", "unknown", "button"}
                and role not in {"enemy", "unknown_character", "target"}
            )
        )
        identified = (not player) and is_identified_row(row)
        inspectable = str(row.get("inspectable") or "").strip().lower() in {"true", "1", "yes"}
        center = _row_center(row)
        if (
            not inspectable
            and not player
            and not unanchored
            and not chrome
            and center is not None
            and (
                role in {"unknown_character"}
                or (
                    not identified
                    and is_anonymous_enemy_label(label)
                    and role in {"enemy", "unknown_character", "target", ""}
                )
            )
        ):
            inspectable = True
        targetable = (not player) and (not unanchored) and (not chrome) and (
            is_targetable_anonymous_enemy(row)
            or (role == "enemy" and center is not None)
        )
        blocker = False
        rule = "confirmed"
        if player or unanchored:
            rule = "player-excluded"
            targetable = False
            inspectable = False
            player = True
        elif chrome:
            rule = "non-enemy-surface"
            targetable = False
            inspectable = False
        elif _row_matches_unresolved(row, label, unresolved) and (
            role in {"enemy", "unknown_character", "target"} or is_anonymous_enemy_label(label)
        ):
            blocker = False
            inspectable = False
            rule = "identity-unresolved"
        elif identify_before_act and inspectable and not identified and center is not None and (
            role in {"enemy", "unknown_character", "target"} or is_anonymous_enemy_label(label)
        ):
            blocker = True
            rule = "identify-before-act"
        elif not targetable and (inspectable or is_anonymous_enemy_label(label)) and role in {
            "enemy", "unknown_character", "target",
        }:
            blocker = True
            rule = "unsafe-geometry"
        facts.append({
            "label": label or role,
            "role": role,
            "targetable": targetable,
            "inspectable": inspectable,
            "identified": identified,
            "blocker": blocker,
            "rule": rule,
            "player": player,
        })
    by_key: dict[str, dict[str, Any]] = {}
    for fact in facts:
        key = str(fact.get("label") or "").casefold()
        prev = by_key.get(key)
        if prev is None:
            by_key[key] = fact
            continue
        prev_score = (bool(prev["identified"]), bool(prev["targetable"]), not bool(prev["blocker"]))
        new_score = (bool(fact["identified"]), bool(fact["targetable"]), not bool(fact["blocker"]))
        if new_score > prev_score:
            by_key[key] = fact
    return list(by_key.values())


def pending_inspect_target(entity_tracker: "EntityTracker | None") -> str | None:
    """Entity id to hover when host OCR merge left sprites unidentified."""
    if entity_tracker is None:
        return None
    for ent in entity_tracker.entities:
        if not ent.inspectable or ent.identified:
            continue
        if is_player_row({
            "label": ent.label,
            "role": ent.role,
            "x": ent.cx,
            "y": ent.cy,
            "x0": ent.bbox[0] if ent.bbox else ent.cx,
            "y0": ent.bbox[1] if ent.bbox else ent.cy,
            "x1": ent.bbox[2] if ent.bbox else ent.cx,
            "y1": ent.bbox[3] if ent.bbox else ent.cy,
        }):
            continue
        return ent.entity_id
    return None


def _row_entity_id(row: dict[str, Any]) -> str:
    return str(row.get("entity_id") or row.get("id") or "").strip()


def _reserve_instance_id(tracker: "EntityTracker", eid: str) -> None:
    """Keep _next_unknown ahead of encounter ids like enemy_3."""
    m = re.match(r"^enemy_(\d+)$", eid, re.I)
    if not m:
        return
    n = int(m.group(1))
    if n >= tracker._next_unknown:
        tracker._next_unknown = n + 1


def _is_encounter_instance_id(eid: str) -> bool:
    return bool(re.match(r"^enemy_\d+$", str(eid or "").strip(), re.I))


def _parse_bbox(row: dict[str, Any]) -> tuple[float, float, float, float] | None:
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
        return None
    x0, y0, x1, y1 = parts[:4]
    if max(x0, y0, x1, y1) > 1.0:
        x0, y0, x1, y1 = x0 / 1000.0, y0 / 1000.0, x1 / 1000.0, y1 / 1000.0
    return (x0, y0, x1, y1)


def _center(bbox: tuple[float, float, float, float] | None) -> tuple[float, float] | None:
    if bbox is None:
        return None
    x0, y0, x1, y1 = bbox
    return ((x0 + x1) * 0.5, (y0 + y1) * 0.5)


def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def _host_enemy_count(host_stats: dict[str, Any] | None) -> int | None:
    """HP-plate count from host OCR. ``None`` when the host did not report enemies."""
    if not host_stats:
        return None
    raw = host_stats.get("enemies")
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return int(raw)
    if not isinstance(raw, list):
        return None
    n = 0
    for row in raw:
        if isinstance(row, dict):
            n += 1
    return n


@dataclass
class TrackedEntity:
    entity_id: str
    label: str
    role: str
    cx: float
    cy: float
    bbox: tuple[float, float, float, float] | None = None
    confidence: float = 0.5
    inspectable: bool = True
    identified: bool = False
    canonical_name: str = ""
    first_seen_cycle: int = 0
    last_seen_cycle: int = 0

    def display_label(self) -> str:
        if self.identified and self.canonical_name:
            return self.canonical_name
        if is_anonymous_enemy_label(self.label) or is_unknown_entity_label(self.label):
            return "unknown enemy"
        return self.label

    def to_row(self) -> dict[str, str]:
        conf = "high" if self.identified or self.confidence >= 0.75 else "low"
        role = "enemy" if self.role in _KNOWN_ENEMY_ROLES or self.identified else self.role
        row: dict[str, str] = {
            "label": self.display_label(),
            "role": role,
            "confidence": conf,
            "entity_id": self.entity_id,
            "inspectable": "true" if self.inspectable and not self.identified else "false",
        }
        if self.bbox is not None:
            x0, y0, x1, y1 = self.bbox
            row["bbox"] = ",".join(str(int(v * 1000)) for v in (x0, y0, x1, y1))
        if self.identified and self.canonical_name:
            row["source"] = "identified"
        return row


@dataclass
class EntityTracker:
    """Track character-like sprites across frames by bbox proximity."""

    entities: list[TrackedEntity] = field(default_factory=list)
    _next_unknown: int = 1
    _cycle: int = 0
    # Canonical names of identified sprites that aged out. Re-appearance
    # re-identifies without a fresh inspect when the bbox is nearby.
    _identified_memory: dict[str, tuple[float, float, float, float] | None] = field(
        default_factory=dict
    )

    def max_unresolved_age(self) -> int:
        ages = [self._cycle - ent.first_seen_cycle + 1 for ent in self.entities if not ent.identified]
        return max(ages, default=0)

    def _allocate_id(self) -> str:
        eid = f"enemy_{self._next_unknown}"
        self._next_unknown += 1
        return eid

    def _match(self, cx: float, cy: float, *, used: set[str] | None = None) -> TrackedEntity | None:
        best: TrackedEntity | None = None
        best_d = _MATCH_RADIUS
        skip = used or set()
        for ent in self.entities:
            if ent.entity_id in skip:
                continue
            d = _distance((cx, cy), (ent.cx, ent.cy))
            if d < best_d:
                best_d = d
                best = ent
        return best

    def _assign_detections(
        self,
        detections: list[tuple[float, float]],
    ) -> list[TrackedEntity | None]:
        """One-to-one nearest assignment; each prior instance and detection used once."""
        assigned: list[TrackedEntity | None] = [None] * len(detections)
        if not detections or not self.entities:
            return assigned
        # ponytail: O(n²) sort of pairs; combat n is tiny.
        pairs: list[tuple[float, int, int]] = []
        for di, (cx, cy) in enumerate(detections):
            for pi, ent in enumerate(self.entities):
                d = _distance((cx, cy), (ent.cx, ent.cy))
                if d < _MATCH_RADIUS:
                    pairs.append((d, pi, di))
        pairs.sort()
        used_prev: set[int] = set()
        used_det: set[int] = set()
        for _d, pi, di in pairs:
            if pi in used_prev or di in used_det:
                continue
            used_prev.add(pi)
            used_det.add(di)
            assigned[di] = self.entities[pi]
        return assigned

    def identify(self, entity_id: str, name: str, *, role: str = "enemy") -> None:
        name = (name or "").strip()
        if not name or is_unknown_entity_label(name):
            return
        from loop.os.aim import _ocr_junk

        if _ocr_junk(name):
            return
        for ent in self.entities:
            if ent.entity_id == entity_id:
                ent.identified = True
                ent.canonical_name = name
                ent.role = role
                ent.inspectable = False
                self._remember_identified(ent)
                return

    def identify_at(self, cx: float, cy: float, name: str, *, role: str = "enemy") -> None:
        hit = self._match(cx, cy)
        if hit is not None:
            self.identify(hit.entity_id, name, role=role)

    def _remember_identified(self, ent: TrackedEntity) -> None:
        name = (ent.canonical_name or "").strip()
        if not name or is_unknown_entity_label(name):
            return
        self._identified_memory[name] = ent.bbox

    def _recall_identified(self, cx: float, cy: float) -> str:
        live_names = {
            ent.canonical_name
            for ent in self.entities
            if ent.identified and ent.last_seen_cycle == self._cycle and ent.canonical_name
        }
        best = ""
        best_d = _MATCH_RADIUS
        for name, last in self._identified_memory.items():
            if last is None or name in live_names:
                continue
            center = _center(last)
            if center is None:
                continue
            d = _distance((cx, cy), center)
            if d < best_d:
                best_d = d
                best = name
        return best

    def _prune_unseen(self, host_stats: dict[str, Any] | None) -> None:
        kept: list[TrackedEntity] = []
        for ent in self.entities:
            if self._cycle - ent.last_seen_cycle >= _STALE_CYCLES:
                if ent.identified:
                    self._remember_identified(ent)
                continue
            kept.append(ent)
        self.entities = kept
        self._cap_unresolved_blockers(host_stats)

    def _cap_unresolved_blockers(self, host_stats: dict[str, Any] | None) -> None:
        """Host HP plates == 1 → at most one unresolved row as a blocker.

        Sprites seen this cycle are never dropped (a real second enemy).
        """
        if _host_enemy_count(host_stats) != 1:
            return
        unresolved = [ent for ent in self.entities if not ent.identified]
        if len(unresolved) <= 1:
            return
        live = [ent for ent in unresolved if ent.last_seen_cycle == self._cycle]
        stale = [ent for ent in unresolved if ent.last_seen_cycle != self._cycle]
        if live:
            drop = {ent.entity_id for ent in stale}
        else:
            drop = {ent.entity_id for ent in stale[1:]}
        if not drop:
            return
        self.entities = [ent for ent in self.entities if ent.entity_id not in drop]

    def apply_object_library(
        self,
        lexicon: "MarkLexicon | None",
        *,
        screen: str | None = None,
    ) -> None:
        """Name unidentified sprites from spatial object library (hover/OCR memory)."""
        if lexicon is None:
            return
        kind = (screen or "").strip().lower() or "other"
        for ent in self.entities:
            if ent.identified:
                continue
            name = lexicon.label_at(ent.cx, ent.cy, screen=kind)
            if name and not is_unknown_entity_label(name):
                self.identify(ent.entity_id, name)

    def update_rows(
        self,
        rows: list[dict[str, Any]],
        *,
        host_stats: dict[str, Any] | None = None,
        lexicon: "MarkLexicon | None" = None,
        screen: str | None = None,
    ) -> list[dict[str, str]]:
        """Assign persistent encounter IDs. Names never mint, merge, or replace IDs."""
        self._cycle += 1
        detections: list[tuple[dict[str, Any], str, str, float, float, tuple[float, float, float, float] | None]] = []
        for row in rows:
            if not isinstance(row, dict) or not is_enemy_discovery_row(row):
                continue
            role = str(row.get("role") or "unknown_character").strip()
            if _compact(role) == "character":
                role = "enemy"
            label = str(row.get("label") or "").strip()
            if not label:
                name = str(row.get("name") or "").strip()
                name_source = _compact(str(row.get("name_source") or ""))
                if name and name_source not in {"", "unresolved"} and not is_unknown_entity_label(name):
                    label = name
            center = _row_center(row)
            if center is None:
                continue
            detections.append((row, role, label, center[0], center[1], _parse_bbox(row)))

        assigned = self._assign_detections([(cx, cy) for _r, _role, _lab, cx, cy, _b in detections])
        used_ids = {ent.entity_id for ent in assigned if ent is not None}

        for det, ent in zip(detections, assigned):
            row, role, label, cx, cy, bbox = det
            known = bool(label) and not is_unknown_entity_label(label)
            if ent is None:
                provided = _row_entity_id(row)
                if (
                    _is_encounter_instance_id(provided)
                    and provided not in used_ids
                    and all(existing.entity_id != provided for existing in self.entities)
                ):
                    eid = provided
                    _reserve_instance_id(self, eid)
                else:
                    eid = self._allocate_id()
                recalled = self._recall_identified(cx, cy) if not known else ""
                named = label if known else recalled
                ent = TrackedEntity(
                    entity_id=eid,
                    label=named or "unknown enemy",
                    role=role if role in _KNOWN_ENEMY_ROLES else "unknown_character",
                    cx=cx,
                    cy=cy,
                    bbox=bbox,
                    confidence=0.85 if known or recalled else 0.55,
                    inspectable=not bool(named),
                    identified=bool(named),
                    canonical_name=named,
                    first_seen_cycle=self._cycle,
                    last_seen_cycle=self._cycle,
                )
                if named:
                    self._remember_identified(ent)
                self.entities.append(ent)
                used_ids.add(eid)
            else:
                ent.cx, ent.cy = cx, cy
                ent.last_seen_cycle = self._cycle
                if bbox is not None:
                    ent.bbox = bbox
                if known and not ent.identified:
                    ent.identified = True
                    ent.canonical_name = label
                    ent.label = label
                    ent.role = "enemy"
                    ent.inspectable = False
                    self._remember_identified(ent)

        live = [ent for ent in self.entities if ent.last_seen_cycle == self._cycle]
        host_named: list[tuple[tuple[float, float], str]] = []
        for row in (host_stats or {}).get("enemies") or []:
            if not isinstance(row, dict):
                continue
            name = str(row.get("label") or "").strip()
            if not name or is_unknown_entity_label(name):
                continue
            center = _row_center(row)
            if center is not None:
                host_named.append((center, name))
        if host_named and live:
            pairs: list[tuple[float, int, int, str]] = []
            for hi, (center, name) in enumerate(host_named):
                for ei, ent in enumerate(live):
                    d = _distance(center, (ent.cx, ent.cy))
                    if d < _MATCH_RADIUS:
                        pairs.append((d, ei, hi, name))
            pairs.sort()
            used_ent: set[int] = set()
            used_host: set[int] = set()
            for _d, ei, hi, name in pairs:
                if ei in used_ent or hi in used_host:
                    continue
                used_ent.add(ei)
                used_host.add(hi)
                self.identify(live[ei].entity_id, name)

        del lexicon, screen
        self._prune_unseen(host_stats)

        out: list[dict[str, str]] = []
        seen_ids: set[str] = set()
        for ent in self.entities:
            if ent.entity_id in seen_ids:
                continue
            seen_ids.add(ent.entity_id)
            out.append(ent.to_row())
        return out

    def merge_into_scene(
        self,
        desc: Any,
        host_stats: dict[str, Any] | None = None,
        *,
        lexicon: "MarkLexicon | None" = None,
        screen: str | None = None,
    ) -> Any:
        """Backfill desc.enemies/controls with tracked entity rows."""
        discovery_rows: list[dict[str, Any]] = []
        for bucket in (getattr(desc, "enemies", None) or [], getattr(desc, "characters", None) or []):
            for row in bucket:
                if isinstance(row, dict) and is_enemy_discovery_row(row):
                    discovery_rows.append(dict(row))
        for row in getattr(desc, "controls", None) or []:
            if isinstance(row, dict) and is_enemy_discovery_row(row):
                discovery_rows.append(dict(row))

        merged = self.update_rows(
            discovery_rows,
            host_stats=host_stats,
            lexicon=lexicon,
            screen=screen or str((host_stats or {}).get("screen") or getattr(desc, "screen", None) or ""),
        )
        # Tracker retention is for identity continuity, not evidence that a
        # sprite is still visible.  Publishing a one-cycle-old row into this
        # frame creates a phantom ACT target when a combat animation was once
        # misread as an enemy.
        live_ids = {
            ent.entity_id
            for ent in self.entities
            if ent.last_seen_cycle == self._cycle
        }
        merged = [row for row in merged if str(row.get("entity_id") or "") in live_ids]
        if not merged:
            return desc

        have_ids = {
            str(r.get("entity_id") or "")
            for r in desc.controls
            if isinstance(r, dict) and str(r.get("entity_id") or "").strip()
        }
        desc.enemies = merged
        if not hasattr(desc, "characters"):
            desc.characters = []
        for row in merged:
            eid = str(row.get("entity_id") or "").strip()
            if eid and eid not in have_ids:
                desc.controls.append(dict(row))
                have_ids.add(eid)
            elif not eid:
                desc.controls.append(dict(row))
        grounded = any(_row_center(r) is not None for r in merged)
        if grounded:
            desc.controls = [
                r
                for r in desc.controls
                if not (
                    isinstance(r, dict)
                    and is_anonymous_enemy_label(
                        str(r.get("label") or r.get("entity_id") or "")
                    )
                    and _row_center(r) is None
                )
            ]
        return desc
