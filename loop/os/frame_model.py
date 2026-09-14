"""Game-agnostic frame object graph — single source for detect → perceive → act.

The generic layer deliberately does not infer game entities from screen bands.
Fixtures may annotate marks with domain roles before they enter this module;
generic controls and navigation always take precedence over those annotations.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, replace
from typing import Any, Literal, Sequence

from loop.os.aim import (
    AimMark,
    _compact_label,
    _hud_chrome,
    _is_placeholder_label,
    is_legend_label,
    label_kind,
)

ObjectKind = Literal["interactive", "informational", "chrome", "unknown"]
LayoutArchetype = Literal[
    "modal_dialog",
    "paginated_overlay",
    "spatial_map",
    "hand_and_board",
    "menu",
    "unknown",
]


@dataclass(frozen=True)
class FrameContext:
    """Resolved, immutable interpretation of the UI surrounding one frame."""

    screen: str = "other"
    activity: str = "unknown"
    layout: LayoutArchetype = "unknown"
    blocking_overlay: bool = False
    map_tool: str | None = None
    overlay: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "screen", (self.screen or "other").strip().lower() or "other")
        object.__setattr__(self, "activity", (self.activity or "unknown").strip().lower() or "unknown")
        tool = str(self.map_tool or "").strip()
        object.__setattr__(self, "map_tool", tool or None)
        overlay = str(self.overlay or "").strip().lower() or None
        object.__setattr__(self, "overlay", overlay)

    @property
    def allows_domain_entities(self) -> bool:
        return self.activity == "combat" and not self.blocking_overlay

_NAV_LABELS = frozenset({"prev", "previous", "next", "arrow"})
_DIALOG_LABELS = frozenset({
    "yes", "no", "got it", "close", "confirm", "continue", "back",
    "draw pile", "discard pile", "exhaust pile",
})
GENERIC_CONTROL_ROLES = frozenset({
    "button", "control", "dialog", "menu", "nav_prev", "nav_next",
})
_GENERIC_CONTROL_ROLES = GENERIC_CONTROL_ROLES
_TARGET_ROLES = frozenset({"unknown_character", "character", "target", "enemy", "player"})
_QUANTIZE_STEP = 0.025
_MOVE_TOLERANCE = 0.05
_DOMAIN_ROLES = frozenset({"card", "enemy", "player", "target"})
_ACTIONABLE_ROLES = frozenset({"button", "nav_prev", "nav_next", "map_node", "card", "enemy", "player"})
_PILE_OVERLAYS = frozenset({"draw_pile", "discard_pile", "exhaust_pile"})
_COMBAT_CHROME_LABELS = frozenset({
    "settings", "options", "menu", "map", "deck", "cards", "share", "legend",
    "relic", "relics", "potion", "potions", "back",
})


def quantize_coord(value: float) -> float:
    step = _QUANTIZE_STEP
    return round(round(float(value) / step) * step, 3)


def paginated_tutorial_active(
    tutorial_info: dict[str, Any] | None = None,
    *,
    host_stats: dict[str, Any] | None = None,
) -> bool:
    """True on multi-step tutorial overlays before the final dismiss step."""
    tut: dict[str, Any] = {}
    if isinstance(tutorial_info, dict):
        tut = tutorial_info
    elif isinstance((host_stats or {}).get("tutorial"), dict):
        tut = host_stats["tutorial"]  # type: ignore[index]
    if not tut.get("active"):
        return False
    step, total = tut.get("step"), tut.get("total")
    return isinstance(step, int) and isinstance(total, int) and step < total


def _named_overlay_from(host_stats: dict[str, Any] | None) -> str | None:
    stats = host_stats or {}
    rows: list[Any] = []
    for key in ("overlays_active", "overlays"):
        value = stats.get(key)
        if isinstance(value, str):
            rows.append(value)
        elif isinstance(value, (list, tuple, set)):
            rows.extend(value)
    for raw in rows:
        slug = re.sub(r"[^a-z0-9]+", "_", str(raw or "").strip().lower()).strip("_")
        if slug in _PILE_OVERLAYS:
            return slug
    return None


def infer_overlay_nav(context: FrameContext, *, host_stats: dict[str, Any] | None = None) -> bool:
    """True for an unfinished numbered tutorial, or its last page while screen is tutorial."""
    if paginated_tutorial_active(host_stats=host_stats):
        return True
    if context.screen != "tutorial":
        return False
    tut = (host_stats or {}).get("tutorial")
    if not isinstance(tut, dict) or not tut.get("active"):
        return False
    step, total = tut.get("step"), tut.get("total")
    return isinstance(step, int) and isinstance(total, int)


def row_bounds(row: dict[str, Any]) -> tuple[float, float, float, float] | None:
    from loop.agent.entities import _parse_bbox

    parsed = _parse_bbox(row)
    if parsed is not None:
        return parsed
    keys = ("x0", "y0", "x1", "y1")
    if all(row.get(key) is not None for key in keys):
        return (
            float(row["x0"]),
            float(row["y0"]),
            float(row["x1"]),
            float(row["y1"]),
        )
    x, y = row.get("x"), row.get("y")
    if isinstance(x, (int, float)) and isinstance(y, (int, float)):
        qx, qy = quantize_coord(float(x)), quantize_coord(float(y))
        return (qx, qy, qx, qy)
    return None


def quantized_bounds_key(row: dict[str, Any]) -> str:
    bounds = row_bounds(row)
    if bounds is None:
        return ""
    return ",".join(str(quantize_coord(part)) for part in bounds)


_SPATIAL_IOU_THRESHOLD = 0.1


def stable_object_id_from_row(row: dict[str, Any]) -> str:
    """Return durable object_id for suppression keys; omit mark-local tokens."""
    raw = str(row.get("object_id") or "").strip()
    if not raw:
        return ""
    from loop.agent.entities import is_mark_derived_object_id

    if is_mark_derived_object_id(raw):
        return ""
    row_id = row.get("id")
    if row_id is not None and raw == str(row_id):
        return ""
    return raw


def observation_context_from_row(row: dict[str, Any]) -> str:
    """Observation/snapshot id when present; omitted for backward compatibility."""
    for field in ("observation_id", "snapshot_id"):
        value = str(row.get(field) or "").strip()
        if value:
            return value
    return ""


def bounds_iou(
    bounds_a: tuple[float, float, float, float],
    bounds_b: tuple[float, float, float, float],
) -> float:
    ix0, iy0 = max(bounds_a[0], bounds_b[0]), max(bounds_a[1], bounds_b[1])
    ix1, iy1 = min(bounds_a[2], bounds_b[2]), min(bounds_a[3], bounds_b[3])
    inter = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
    if inter <= 0:
        return 0.0
    area_a = max(1e-9, (bounds_a[2] - bounds_a[0]) * (bounds_a[3] - bounds_a[1]))
    area_b = max(1e-9, (bounds_b[2] - bounds_b[0]) * (bounds_b[3] - bounds_b[1]))
    return inter / (area_a + area_b - inter)


def center_in_bounds(
    point: tuple[float, float],
    bounds: tuple[float, float, float, float],
) -> bool:
    x, y = point
    return bounds[0] <= x <= bounds[2] and bounds[1] <= y <= bounds[3]


def rows_spatially_overlap(
    row_a: dict[str, Any],
    row_b: dict[str, Any],
    *,
    iou_threshold: float = _SPATIAL_IOU_THRESHOLD,
) -> bool:
    """True when quantized boxes overlap or either center lies in the other box."""
    bounds_a = row_bounds(row_a)
    bounds_b = row_bounds(row_b)
    if bounds_a is None or bounds_b is None:
        return False
    if bounds_iou(bounds_a, bounds_b) >= iou_threshold:
        return True
    center_a = ((bounds_a[0] + bounds_a[2]) / 2.0, (bounds_a[1] + bounds_a[3]) / 2.0)
    center_b = ((bounds_b[0] + bounds_b[2]) / 2.0, (bounds_b[1] + bounds_b[3]) / 2.0)
    return center_in_bounds(center_a, bounds_b) or center_in_bounds(center_b, bounds_a)


def find_control_row_for_suppression(
    row: dict[str, Any],
    candidates: Sequence[dict[str, Any]],
    *,
    screen: str = "",
) -> dict[str, Any] | None:
    """Pick a prior row by stable object_id, then bbox, then label."""
    if not candidates:
        return None
    object_id = stable_object_id_from_row(row)
    if object_id:
        for candidate in candidates:
            if stable_object_id_from_row(candidate) == object_id:
                return candidate
    spatial = [candidate for candidate in candidates if rows_spatially_overlap(row, candidate)]
    if len(spatial) == 1:
        return spatial[0]
    compact = _compact_label(str(row.get("label") or ""))
    if not compact:
        return spatial[0] if spatial else None
    label_matches = [
        candidate
        for candidate in candidates
        if _compact_label(str(candidate.get("label") or "")) == compact
    ]
    if len(label_matches) == 1:
        return label_matches[0]
    labeled_spatial = [candidate for candidate in label_matches if rows_spatially_overlap(row, candidate)]
    if len(labeled_spatial) == 1:
        return labeled_spatial[0]
    if label_matches:
        return label_matches[0]
    return spatial[0] if spatial else None


def control_key_from_row(row: dict[str, Any], *, screen: str = "") -> str:
    screen_key = (screen or str(row.get("screen") or "")).strip().lower()
    bounds = quantized_bounds_key(row)
    parts: list[str] = [screen_key]
    observation = observation_context_from_row(row)
    if observation:
        parts.append(observation)
    object_id = stable_object_id_from_row(row)
    if object_id:
        parts.append(f"oid:{object_id}")
    parts.append(bounds)
    return "|".join(parts)


def surface_fingerprint(
    *,
    screen: str,
    rows: Sequence[dict[str, Any]] | None,
) -> str:
    """Stable surface identity from screen + typed action objects (no mark ids)."""
    screen_key = (screen or "").strip().lower()
    entries: list[tuple[str, str]] = []
    for row in rows or ():
        if not isinstance(row, dict):
            continue
        role = str(row.get("role") or "").strip().lower()
        if role not in GENERIC_CONTROL_ROLES and role not in _TARGET_ROLES and role not in {"card", "map_node"}:
            continue
        bounds = quantized_bounds_key(row)
        if not bounds:
            continue
        entries.append((role, bounds))
    entries.sort()
    body = "|".join(f"{role}:{bounds}" for role, bounds in entries)
    return f"{screen_key}|{body}"


def _looks_like_prose(label: str) -> bool:
    text = (label or "").strip()
    if len(text) > 48:
        return True
    words = text.split()
    return len(words) > 6


def _role_for_label(
    label: str,
    *,
    mark: AimMark,
    context: FrameContext,
    host_stats: dict[str, Any] | None = None,
) -> str:
    compact = _compact_label(label)
    raw_role = str(mark.role or "").strip().lower()
    explicit = _compact_label(raw_role)
    overlay_nav = infer_overlay_nav(context, host_stats=host_stats)

    if explicit in {"suppressed", "background", "hand candidate"}:
        return "suppressed"
    if context.blocking_overlay and explicit in _DOMAIN_ROLES:
        return "suppressed"
    # Fixture map roles are current-frame geometry, not scene-prose promotion.
    if raw_role in {"map_annotation_tool", "map_node"}:
        return raw_role
    # A placeholder label cannot inherit button/nav from an upstream generic row.
    # Reward phrases stay executable because OCR replaces `icon` before this runs.
    if _is_placeholder_label(label):
        if explicit in {"hud", "body", "icon", "unknown"}:
            return explicit
        return "icon"
    # Runtime Arrow chevrons keep label "Arrow"; overlay/tutorial promotes geometry to nav.
    if overlay_nav or context.screen == "tutorial":
        if compact == "next" or (compact == "arrow" and float(mark.x) >= 0.5):
            return "nav_next"
        if compact in {"prev", "previous"} or (compact == "arrow" and float(mark.x) < 0.5):
            return "nav_prev"
    # Grounded generic semantics beat every positional or fixture hint.
    if explicit in {"nav_next", "next"}:
        return "nav_next" if overlay_nav else "unknown"
    if explicit in {"nav_prev", "prev", "previous"}:
        return "nav_prev" if overlay_nav else "unknown"
    if explicit in _GENERIC_CONTROL_ROLES:
        if compact == "next":
            return "nav_next" if overlay_nav else "unknown"
        if compact in {"prev", "previous"}:
            return "nav_prev" if overlay_nav else "unknown"
        return "button"
    if compact == "next":
        return "nav_next" if overlay_nav else "unknown"
    if compact in {"prev", "previous"}:
        return "nav_prev" if overlay_nav else "unknown"
    if compact in _DIALOG_LABELS or label_kind(label) in {"progress", "danger"}:
        return "button"

    # Domain roles are accepted only in a fixture-authorized activity.  The
    # generic layer never manufactures them from geometry.
    if explicit in _DOMAIN_ROLES:
        return "enemy" if explicit == "target" else explicit
    if explicit in {"hud", "body", "icon", "unknown"}:
        return explicit
    return "unknown"


def _kind_for_mark(mark: AimMark, label: str, *, role: str) -> ObjectKind:
    compact = _compact_label(label)
    if role == "suppressed":
        return "unknown"
    if role == "map_annotation_tool":
        return "chrome"
    if _hud_chrome(mark):
        return "informational" if compact and not _is_placeholder_label(label) else "chrome"
    if is_legend_label(label) or _looks_like_prose(label):
        return "informational"
    if label_kind(label) in {"progress", "danger"}:
        return "interactive"
    if role in _ACTIONABLE_ROLES or compact in _DIALOG_LABELS or compact in _NAV_LABELS:
        return "interactive"
    if _is_placeholder_label(label):
        return "interactive"
    if not compact:
        return "chrome"
    # A concise, grounded label is still an interaction candidate even when
    # the generic layer cannot name its domain role.  This preserves useful
    # menu/tutorial objects without promoting them to cards or enemies.
    if len(compact) <= 24:
        return "interactive"
    return "unknown"


def _slug(label: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "_", _compact_label(label)).strip("_")
    return value or "control"


def _control_ids(objects: list[FrameObject]) -> list[FrameObject]:
    """Assign deterministic, unique semantic IDs after role resolution."""
    from loop.agent.entities import is_mark_derived_object_id

    used: set[str] = set()
    out: list[FrameObject] = []
    ordinals: dict[str, int] = {}
    for obj in objects:
        object_id = str(obj.object_id or "").strip()
        if obj.role in {"button", "nav_prev", "nav_next", "map_node"}:
            if obj.role == "nav_next":
                base = "control:next"
            elif obj.role == "nav_prev":
                base = "control:prev"
            else:
                base = f"control:{_slug(obj.label)}"
            ordinal = ordinals.get(base, 0) + 1
            ordinals[base] = ordinal
            object_id = base if ordinal == 1 else f"{base}:{ordinal}"
        elif obj.role in {"card", "enemy", "player"}:
            if not object_id or object_id.startswith("mark_") or is_mark_derived_object_id(object_id):
                base = obj.role
                ordinal = ordinals.get(base, 0) + 1
                ordinals[base] = ordinal
                object_id = f"{base}_{ordinal}"
            if object_id in used:
                base = obj.role
                ordinal = ordinals.get(base, 0) + 1
                while f"{base}_{ordinal}" in used:
                    ordinal += 1
                ordinals[base] = ordinal
                object_id = f"{base}_{ordinal}"
        elif not object_id:
            object_id = f"object:{obj.id}"
        if object_id in used:
            object_id = f"{object_id}:{obj.id}"
        used.add(object_id)
        out.append(replace(obj, object_id=object_id))
    return out


@dataclass(frozen=True)
class FrameObject:
    id: int
    label: str
    kind: ObjectKind
    role: str = "unknown"
    x: float = 0.0
    y: float = 0.0
    x0: float = 0.0
    y0: float = 0.0
    x1: float = 0.0
    y1: float = 0.0
    source: str = "aim"
    confidence: float = 1.0
    object_id: str = ""
    provenance: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "kind": self.kind,
            "role": self.role,
            "x": round(self.x, 3),
            "y": round(self.y, 3),
            "source": self.source,
            "object_id": self.object_id,
            "confidence": round(self.confidence, 3),
            "provenance": self.provenance,
        }


@dataclass
class FrameModel:
    objects: list[FrameObject] = field(default_factory=list)
    layout: LayoutArchetype = "unknown"
    screen_hint: str = ""

    def interactive(self) -> list[FrameObject]:
        return [o for o in self.objects if o.kind == "interactive"]

    def interactive_labels(self) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for obj in self.interactive():
            lab = (obj.label or "").strip()
            if not lab:
                continue
            key = lab.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(lab)
        return out

    def informational_text(self) -> list[str]:
        return [o.label for o in self.objects if o.kind == "informational" and o.label.strip()]

    def prompt_block(self) -> str:
        lines = [f"Layout: {self.layout}"]
        if self.screen_hint:
            lines.append(f"Screen hint: {self.screen_hint}")
        interactive = self.interactive_labels()
        if interactive:
            lines.append("Interactive labels: " + ", ".join(interactive[:20]))
        info = self.informational_text()
        if info:
            lines.append("Visible text: " + " | ".join(info[:4]))
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "layout": self.layout,
            "screen_hint": self.screen_hint,
            "objects": [o.to_dict() for o in self.objects],
        }


@dataclass(frozen=True)
class ActionSurface:
    """Canonical, executable inventory for exactly one observed frame."""

    run_id: str
    step_id: int
    snapshot_id: str
    context: FrameContext
    objects: tuple[FrameObject, ...] = ()
    legal_intents: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "objects", tuple(self.objects))
        object.__setattr__(self, "legal_intents", tuple(self.legal_intents))

    def interactive(self) -> tuple[FrameObject, ...]:
        return tuple(obj for obj in self.objects if obj.kind == "interactive")

    def object_by_id(self, object_id: str) -> FrameObject | None:
        matches = [obj for obj in self.objects if obj.object_id == object_id]
        return matches[0] if len(matches) == 1 else None

    def frame_model(self) -> FrameModel:
        return FrameModel(
            objects=list(self.objects),
            layout=self.context.layout,
            screen_hint=self.context.screen,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "step_id": self.step_id,
            "snapshot_id": self.snapshot_id,
            "context": {
                "screen": self.context.screen,
                "activity": self.context.activity,
                "layout": self.context.layout,
                "blocking_overlay": self.context.blocking_overlay,
                "map_tool": self.context.map_tool,
                "overlay": self.context.overlay,
            },
            "objects": [obj.to_dict() for obj in self.objects],
            "legal_intents": list(self.legal_intents),
        }


def infer_layout(
    objects: list[FrameObject],
    *,
    screen_hint: str = "",
    activity: str | None = None,
    host_stats: dict[str, Any] | None = None,
) -> LayoutArchetype:
    interactives = [o for o in objects if o.kind == "interactive"]
    roles = {o.role for o in interactives}
    labels = {_compact_label(o.label) for o in interactives}
    screen = (screen_hint or "").strip().lower()
    activity_key = str(
        activity or (host_stats or {}).get("activity") or ""
    ).strip().lower()
    tutorial_open = paginated_tutorial_active(host_stats=host_stats)
    nav_signal = bool(
        roles & {"nav_prev", "nav_next"} or labels & _NAV_LABELS or tutorial_open
    )
    # Combat leftovers of Prev/Next/Back must not invent an overlay. Same gate
    # as infer_overlay_nav: only an unfinished tutorial page paginates combat.
    combat_board = screen == "combat" and activity_key in {"", "combat"}
    if combat_board and not tutorial_open:
        nav_signal = False
    # A stale Prev/Next label must not mint paginated_overlay on an ordinary map.
    if screen == "map" and not tutorial_open:
        explicit_overlay = (host_stats or {}).get("blocking_overlay")
        if not (isinstance(explicit_overlay, bool) and explicit_overlay):
            nav_signal = False
    if nav_signal:
        return "paginated_overlay"
    if labels >= {"yes", "no"} or (labels & {"yes", "no", "got it"} and screen in {"select", "tutorial", "other"}):
        return "modal_dialog"
    if any(o.role == "card" for o in interactives) or screen == "combat":
        return "hand_and_board"
    if screen == "map":
        return "spatial_map"
    if screen in {"title", "select"}:
        return "menu"
    return "unknown"


def build_frame_model(
    marks: tuple[AimMark, ...] | list[AimMark],
    *,
    screen: str | None = None,
    host_stats: dict[str, Any] | None = None,
    informational: list[str] | None = None,
) -> FrameModel:
    """Build the legacy frame view from the canonical action surface."""
    return build_action_surface(
        marks,
        screen=screen,
        host_stats=host_stats,
        informational=informational,
    ).frame_model()


def _objects_from_marks(
    marks: tuple[AimMark, ...] | list[AimMark],
    *,
    context: FrameContext,
    informational: list[str] | None = None,
    host_stats: dict[str, Any] | None = None,
) -> list[FrameObject]:
    objects: list[FrameObject] = []
    for mark in marks:
        label = str(mark.label or "").strip() or "icon"
        role = _role_for_label(label, mark=mark, context=context, host_stats=host_stats)
        kind = _kind_for_mark(mark, label, role=role)
        objects.append(
            FrameObject(
                id=int(mark.id),
                label=label,
                kind=kind,
                role=role,
                x=float(mark.x),
                y=float(mark.y),
                x0=float(mark.x0),
                y0=float(mark.y0),
                x1=float(mark.x1),
                y1=float(mark.y1),
                source=str(mark.source or "aim"),
                confidence=float(mark.confidence or mark.score or 1.0),
                object_id=str(mark.object_id or ""),
                provenance=str(mark.source or "aim"),
            )
        )
    for i, text in enumerate(informational or []):
        blob = str(text or "").strip()
        if not blob:
            continue
        if any(_compact_label(blob) == _compact_label(o.label) for o in objects):
            continue
        objects.append(
            FrameObject(
                id=10_000 + i,
                label=blob,
                kind="informational",
                role="body",
                source="ocr",
            )
        )
    return _control_ids(objects)


def _blocking_overlay_from(
    *,
    screen: str,
    activity: str,
    layout: LayoutArchetype,
    host_stats: dict[str, Any] | None,
) -> bool:
    stats = host_stats or {}
    if _named_overlay_from(stats):
        return True
    explicit = stats.get("blocking_overlay")
    if isinstance(explicit, bool):
        return explicit
    overlays = stats.get("overlays")
    if isinstance(overlays, (list, tuple, set)) and bool(overlays):
        return True
    if layout in {"modal_dialog", "paginated_overlay"}:
        return True
    return activity == "combat" and screen not in {"combat", "unknown", "other", ""}


def _legal_intents(objects: list[FrameObject], *, context: FrameContext) -> tuple[str, ...]:
    intents: list[str] = []
    roles = {obj.role for obj in objects if obj.kind == "interactive"}
    labels = {_compact_label(obj.label) for obj in objects if obj.kind == "interactive"}
    if context.allows_domain_entities and "card" in roles:
        intents.append("play_card")
    if context.allows_domain_entities and any("end turn" in label for label in labels):
        intents.append("end_turn")
    if roles & {"button", "nav_prev", "nav_next", "map_node"}:
        intents.append("click_control")
    if (
        not context.blocking_overlay
        and any(obj.kind == "interactive" and obj.role in {"unknown", "icon"} for obj in objects)
    ):
        intents.append("inspect")
    if context.allows_domain_entities and any(_anonymous_combat_target(obj) for obj in objects):
        if "inspect" not in intents:
            intents.append("inspect")
    return tuple(intents)


def _anonymous_combat_target(obj: FrameObject) -> bool:
    if obj.role != "enemy":
        return False
    if float(obj.confidence or 1.0) < 0.75:
        return True
    compact = _compact_label(obj.label).replace("_", " ")
    return compact.startswith("unknown") or compact in {"", "icon", "unidentified enemy"}


def build_action_surface(
    marks: tuple[AimMark, ...] | list[AimMark],
    *,
    run_id: str = "",
    step_id: int = 0,
    screen: str | None = None,
    activity: str | None = None,
    layout: LayoutArchetype | None = None,
    blocking_overlay: bool | None = None,
    host_stats: dict[str, Any] | None = None,
    informational: list[str] | None = None,
) -> ActionSurface:
    """Build the immutable action inventory used by reasoning and execution.

    Marks must already be reconciled with any scene description.  In
    particular, a label without a current-frame mark never enters this
    executable surface.
    """
    stats = host_stats or {}
    screen_key = str(screen or stats.get("screen") or "other").strip().lower() or "other"
    if activity is None:
        from loop.os.activity import activity_from_state

        activity_key = activity_from_state(screen=screen_key, host_stats=stats)
    else:
        activity_key = str(activity or "unknown").strip().lower() or "unknown"

    # First resolve generic controls.  Their labels determine whether a modal
    # is present; a second pass then suppresses fixture domain roles behind it.
    provisional_context = FrameContext(screen=screen_key, activity=activity_key)
    provisional = _objects_from_marks(
        marks, context=provisional_context, informational=informational, host_stats=stats,
    )
    resolved_layout = layout or infer_layout(
        provisional,
        screen_hint=screen_key,
        activity=activity_key,
        host_stats=stats,
    )
    overlay = _named_overlay_from(stats)
    blocked = True if overlay else (
        bool(blocking_overlay)
        if blocking_overlay is not None
        else _blocking_overlay_from(
            screen=screen_key,
            activity=activity_key,
            layout=resolved_layout,
            host_stats=stats,
        )
    )
    tool = stats.get("map_tool")
    map_tool = str(tool).strip() if tool else None
    context = FrameContext(
        screen=screen_key,
        activity=activity_key,
        layout=resolved_layout,
        blocking_overlay=blocked,
        map_tool=map_tool,
        overlay=overlay,
    )
    objects = _objects_from_marks(
        marks, context=context, informational=informational, host_stats=stats,
    )
    if context.activity == "combat" and not context.blocking_overlay:
        # Menu chrome is visible around the combat board, but is not part of
        # the gameplay action surface.  Publishing it as a generic button lets
        # ACT choose Settings/Map instead of a card or End Turn after an
        # overlay closes.  Inspectable piles remain available, and overlay
        # controls bypass this gate while their overlay is actually active.
        objects = [
            obj for obj in objects
            if not (
                obj.kind == "interactive"
                and obj.role in {"button", "control", "nav_prev", "nav_next", "map_node"}
                and _compact_label(obj.label) in _COMBAT_CHROME_LABELS
            )
        ]
    payload = {
        "run_id": str(run_id),
        "step_id": int(step_id),
        "context": {
            "screen": context.screen,
            "activity": context.activity,
            "layout": context.layout,
            "blocking_overlay": context.blocking_overlay,
            "map_tool": context.map_tool,
            "overlay": context.overlay,
        },
        "objects": [obj.to_dict() for obj in objects],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    snapshot_id = hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]
    return ActionSurface(
        run_id=str(run_id),
        step_id=int(step_id),
        snapshot_id=snapshot_id,
        context=context,
        objects=tuple(objects),
        legal_intents=_legal_intents(objects, context=context),
    )


def fuse_scene_with_frame(
    scene_controls: list[str],
    frame: FrameModel | None,
) -> list[str]:
    """Interactive labels for the reasoner: frame objects win over VLM list."""
    if frame is None:
        return list(scene_controls)
    labels = frame.interactive_labels()
    if labels:
        return labels
    return list(scene_controls)
