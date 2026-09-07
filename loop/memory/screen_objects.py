from __future__ import annotations

import sqlite3
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from loop.os.aim import GRID, AimMark, label_kind


def _bbox_iou(a: ScreenObject | AimMark, b: ScreenObject | AimMark) -> float:
    ix0, iy0 = max(a.x0, b.x0), max(a.y0, b.y0)
    ix1, iy1 = min(a.x1, b.x1), min(a.y1, b.y1)
    inter = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
    if inter <= 0:
        return 0.0
    union = max(1e-9, (a.x1 - a.x0) * (a.y1 - a.y0) + (b.x1 - b.x0) * (b.y1 - b.y0) - inter)
    return inter / union


def _is_mark_object_id(object_id: str) -> bool:
    from loop.agent.entities import is_mark_derived_object_id

    return is_mark_derived_object_id(object_id)


def _stable_object_id(mark: AimMark) -> str:
    raw = str(mark.object_id or "").strip()
    if raw and not _is_mark_object_id(raw):
        return raw
    return ""


_UNTYPED_SPRITE_ROLES = frozenset({"unknown", "icon", "body"})
_SPRITE_ROLE_RANK = {
    "enemy": 3,
    "target": 3,
    "unknown_character": 2,
    "character": 2,
    "unknown": 1,
    "icon": 1,
    "body": 1,
}


def _canonical_sprite_id(object_id: str) -> bool:
    raw = str(object_id or "").strip()
    if not raw or _is_mark_object_id(raw):
        return False
    lowered = raw.lower()
    return lowered.startswith("enemy_") or lowered.startswith("player_")


def _mark_confidence(mark: AimMark) -> float:
    return float(mark.confidence or mark.score or 0.0)


def _sprite_role_rank(mark: AimMark) -> int:
    return _SPRITE_ROLE_RANK.get((mark.role or "").strip().lower(), 0)


def _prefer_target_row(kept_mark: AimMark, challenger: AimMark) -> AimMark:
    kept_rank = _sprite_role_rank(kept_mark)
    chall_rank = _sprite_role_rank(challenger)
    if kept_rank != chall_rank:
        return kept_mark if kept_rank > chall_rank else challenger
    kept_canon = _canonical_sprite_id(kept_mark.object_id)
    chall_canon = _canonical_sprite_id(challenger.object_id)
    if kept_canon != chall_canon:
        return kept_mark if kept_canon else challenger
    kept_conf = _mark_confidence(kept_mark)
    chall_conf = _mark_confidence(challenger)
    if chall_conf != kept_conf:
        return challenger if chall_conf > kept_conf else kept_mark
    return kept_mark


def _prepare_frame_marks(marks: list[AimMark], *, context: Any | None = None) -> list[AimMark]:
    from loop.os.aim import _copy_mark
    from loop.os.frame_model import _TARGET_ROLES

    def enemy_like(mark: AimMark) -> bool:
        return (mark.role or "").strip().lower() in {"enemy", "target"}

    def player_like(mark: AimMark) -> bool:
        return (mark.role or "").strip().lower() == "player"

    def sprite_like(mark: AimMark) -> bool:
        role = (mark.role or "").strip().lower()
        if role == "player":
            return False
        return role in _TARGET_ROLES or role in _UNTYPED_SPRITE_ROLES

    kept: list[AimMark] = []
    for mark in sorted(marks, key=lambda item: (item.x, item.y, item.id)):
        if sprite_like(mark) or player_like(mark):
            dup_idx = next(
                (
                    index
                    for index, other in enumerate(kept)
                    if (
                        (sprite_like(mark) and sprite_like(other))
                        or (player_like(mark) and player_like(other))
                    )
                    and _bbox_iou(mark, other) >= 0.45
                ),
                None,
            )
            if dup_idx is not None:
                kept[dup_idx] = _prefer_target_row(kept[dup_idx], mark)
                continue
        kept.append(mark)

    cards = sorted(
        [
            mark
            for mark in kept
            if (mark.role or "").strip().lower() == "card"
        ],
        key=lambda item: item.x,
    )
    enemies = sorted(
        [mark for mark in kept if enemy_like(mark)],
        key=lambda item: item.x,
    )
    players = sorted(
        [mark for mark in kept if player_like(mark)],
        key=lambda item: item.x,
    )
    assigned: dict[int, str] = {}
    used_ids: set[str] = set()

    def assign_unique(mark: AimMark, prefix: str, ordinal: int) -> str:
        preferred = _stable_object_id(mark)
        if preferred and preferred not in used_ids:
            used_ids.add(preferred)
            return preferred
        candidate_index = max(1, ordinal)
        candidate = f"{prefix}_{candidate_index}"
        while candidate in used_ids:
            candidate_index += 1
            candidate = f"{prefix}_{candidate_index}"
        used_ids.add(candidate)
        return candidate

    for index, card in enumerate(cards, start=1):
        assigned[card.id] = assign_unique(card, "card", index)
    for index, enemy in enumerate(enemies, start=1):
        assigned[enemy.id] = assign_unique(enemy, "enemy", index)
    for index, player in enumerate(players, start=1):
        assigned[player.id] = assign_unique(player, "player", index)

    out: list[AimMark] = []
    for mark in kept:
        object_id = assigned.get(mark.id, _stable_object_id(mark))
        if player_like(mark):
            mark = _copy_mark(
                mark,
                object_id=object_id,
                role="player",
            )
        elif enemy_like(mark):
            mark = _copy_mark(mark, object_id=object_id, role="enemy")
        elif mark in cards:
            mark = _copy_mark(mark, object_id=object_id, role="card")
        elif object_id != (mark.object_id or ""):
            mark = _copy_mark(mark, object_id=object_id)
        out.append(mark)
    return out


def _mark_cell(mark: AimMark) -> str:
    cell = f"{mark.id}@{int(round(mark.x * GRID))},{int(round(mark.y * GRID))}"
    if mark.label:
        cell += ":" + mark.label.replace(" ", "_")
    return cell


@dataclass(frozen=True)
class ScreenObject:
    mark_id: int
    x: float
    y: float
    x0: float
    y0: float
    x1: float
    y1: float
    label: str
    kind: str
    source: str
    screen: str
    listing: str
    object_id: str = ""
    role: str = ""
    confidence: float = 0.0
    provenance: str = ""

    @classmethod
    def from_mark(cls, mark: AimMark, *, screen: str = "", source: str = "ocr") -> ScreenObject:
        return cls(
            mark_id=int(mark.id),
            x=float(mark.x),
            y=float(mark.y),
            x0=float(mark.x0),
            y0=float(mark.y0),
            x1=float(mark.x1),
            y1=float(mark.y1),
            label=str(mark.label or ""),
            kind=label_kind(mark.label),
            source=str(mark.source or source),
            screen=(screen or "").strip().lower() or "other",
            listing=_mark_cell(mark),
            object_id=str(mark.object_id or ""),
            role=str(mark.role or ""),
            confidence=float(mark.confidence or mark.score or 0.0),
            provenance=str(mark.source or source),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "mark_id": self.mark_id,
            "x": self.x,
            "y": self.y,
            "x0": self.x0,
            "y0": self.y0,
            "x1": self.x1,
            "y1": self.y1,
            "label": self.label,
            "kind": self.kind,
            "source": self.source,
            "screen": self.screen,
            "listing": self.listing,
            "object_id": self.object_id,
            "role": self.role,
            "confidence": self.confidence,
            "provenance": self.provenance,
        }


class ScreenObjectStore:
    """Per-run sqlite: click targets keyed by step + mark id."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._conn = sqlite3.connect(str(path))
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS screen_objects (
                run_id TEXT NOT NULL,
                step_id INTEGER NOT NULL,
                mark_id INTEGER NOT NULL,
                x REAL, y REAL,
                x0 REAL, y0 REAL, x1 REAL, y1 REAL,
                label TEXT,
                kind TEXT,
                source TEXT,
                screen TEXT,
                listing TEXT,
                object_id TEXT,
                role TEXT,
                confidence REAL,
                provenance TEXT,
                PRIMARY KEY (run_id, step_id, mark_id)
            )
            """
        )
        existing = {row[1] for row in self._conn.execute("PRAGMA table_info(screen_objects)")}
        for name, sql_type in (
            ("object_id", "TEXT"),
            ("role", "TEXT"),
            ("confidence", "REAL"),
            ("provenance", "TEXT"),
        ):
            if name not in existing:
                self._conn.execute(
                    f"ALTER TABLE screen_objects ADD COLUMN {name} {sql_type}"
                )
        self._conn.commit()
        self._run_id = ""
        self._step_id = 0
        self._objects: list[ScreenObject] = []
        self._instance_aliases: dict[str, str] = {}

    def close(self) -> None:
        self._conn.close()

    def save_frame(
        self,
        *,
        run_id: str,
        step_id: int,
        marks: tuple[AimMark, ...] | list[AimMark],
        screen: str | None = None,
        source: str = "ocr",
        context: Any | None = None,
        surface: Any | None = None,
    ) -> list[AimMark]:
        self._run_id = str(run_id)
        self._step_id = int(step_id)
        kind = (screen or "").strip().lower() or "other"
        self._conn.execute(
            "DELETE FROM screen_objects WHERE run_id = ? AND step_id = ?",
            (self._run_id, self._step_id),
        )
        self._instance_aliases = {}
        rows: list[ScreenObject] = []
        # OCR/CV/Dino fusion can emit the same mark id more than once.  The
        # database key is (run, step, mark), so collapse duplicates before
        # inserting; keep the last occurrence as it is the most recently
        # fused/refined representation.
        unique_marks: dict[int, AimMark] = {}
        for mark in marks:
            unique_marks[int(mark.id)] = mark
        from loop.os.aim import _copy_mark
        from loop.os.frame_model import FrameContext, build_action_surface

        if context is None:
            context = FrameContext(
                screen=kind,
                activity="combat" if kind == "combat" else kind,
                blocking_overlay=False,
            )
        provisional = _prepare_frame_marks(list(unique_marks.values()), context=context)
        if surface is None:
            surface = build_action_surface(
                provisional,
                run_id=self._run_id,
                step_id=self._step_id,
                screen=getattr(context, "screen", kind),
                activity=getattr(context, "activity", kind),
                layout=getattr(context, "layout", None),
                blocking_overlay=getattr(context, "blocking_overlay", None),
            )
        surface_objects = {
            int(obj.id): obj for obj in tuple(getattr(surface, "objects", ()) or ())
            if int(getattr(obj, "id", -1)) in unique_marks
        }
        prepared = []
        for mark in provisional:
            obj = surface_objects.get(int(mark.id))
            if obj is None:
                prepared.append(mark)
                continue
            prepared.append(_copy_mark(
                mark,
                label=str(obj.label or mark.label),
                object_id=str(obj.object_id or ""),
                role=str(obj.role or ""),
                source=str(obj.source or mark.source or source),
                confidence=float(obj.confidence or mark.confidence or mark.score or 0.0),
            ))
        for mark in prepared:
            obj = ScreenObject.from_mark(mark, screen=kind, source=source)
            rows.append(obj)
            self._conn.execute(
                """
                INSERT INTO screen_objects (
                    run_id, step_id, mark_id, x, y, x0, y0, x1, y1,
                    label, kind, source, screen, listing,
                    object_id, role, confidence, provenance
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    self._run_id,
                    self._step_id,
                    obj.mark_id,
                    obj.x,
                    obj.y,
                    obj.x0,
                    obj.y0,
                    obj.x1,
                    obj.y1,
                    obj.label,
                    obj.kind,
                    obj.source,
                    obj.screen,
                    obj.listing,
                    obj.object_id,
                    obj.role,
                    obj.confidence,
                    obj.provenance,
                ),
            )
        self._conn.commit()
        self._objects = rows
        return prepared

    @property
    def objects(self) -> tuple[ScreenObject, ...]:
        return tuple(self._objects)

    def listing(self) -> str:
        if not self._objects:
            return ""
        return " ".join(obj.listing for obj in self._objects)

    def by_id(self, mark_id: int) -> ScreenObject | None:
        for obj in self._objects:
            if obj.mark_id == int(mark_id):
                return obj
        return None

    def by_object_id(self, object_id: str) -> ScreenObject | None:
        want = str(object_id or "").strip()
        matches = [obj for obj in self._objects if obj.object_id == want]
        return matches[0] if len(matches) == 1 else None

    def stamp_instance_alias(self, instance_id: str, object_id: str) -> None:
        """Bind a prompt-facing instance id to a canonical current-frame object id."""
        want_instance = str(instance_id or "").strip()
        want_object = str(object_id or "").strip()
        if not want_instance or not want_object or want_instance == want_object:
            return
        self._instance_aliases[want_instance] = want_object

    def resolve_instance_id(self, instance_id: str) -> str:
        """Return the canonical object id for a snapshot-scoped instance id."""
        want = str(instance_id or "").strip()
        if not want:
            return ""
        if self.by_object_id(want) is not None:
            return want
        return str(self._instance_aliases.get(want) or want)

    def object_for_instance_id(self, instance_id: str) -> ScreenObject | None:
        """Resolve a prompt/instance id to the current-frame store object."""
        resolved = self.resolve_instance_id(instance_id)
        if not resolved:
            return None
        return self.by_object_id(resolved)

    def relabel(self, object_id: str, name: str) -> None:
        """Bind a verified hover-OCR name onto the current-frame object."""
        want = str(object_id or "").strip()
        label = str(name or "").strip()
        if not want or not label:
            return
        rows: list[ScreenObject] = []
        for obj in self._objects:
            if obj.object_id == want:
                obj = replace(obj, label=label)
            rows.append(obj)
        self._objects = rows

    def enemies(self) -> list[ScreenObject]:
        seen: list[ScreenObject] = []
        out: list[ScreenObject] = []
        for obj in self._objects:
            role = (obj.role or "").strip().lower()
            if role not in {"enemy", "target"}:
                continue
            if any(_bbox_iou(obj, other) >= 0.45 for other in seen):
                continue
            seen.append(obj)
            out.append(obj)
        return out

    def players(self) -> list[ScreenObject]:
        return [obj for obj in self._objects if (obj.role or "").strip().lower() == "player"]

    def hand_cards(self, *, entity_targets_ok: bool = False) -> list[ScreenObject]:
        """Current-frame card objects; fixture enrichment owns card typing."""
        return [
            obj
            for obj in self._objects
            if (obj.screen == "combat" or entity_targets_ok)
            and obj.role == "card"
            and obj.kind not in {"danger", "progress"}
        ]

    def progress_buttons(self) -> list[ScreenObject]:
        return [obj for obj in self._objects if obj.kind == "progress"]

    def to_json(self) -> list[dict[str, Any]]:
        return [obj.to_dict() for obj in self._objects]
