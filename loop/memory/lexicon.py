"""Spatial overlay lexicon: (screen, quantized heading) → learned control name.

Sensor-written only. OCR on this frame wins over memory. VLM choice/note never
writes a slot — hover OCR, labeled marks, and screen transitions do.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from loop.labels import bind_verb_ok

_SLOT_CAP = 48
_SOURCE_RANK = {"ocr": 3, "hover": 2, "transit": 1}
_PLACEHOLDER = frozenset(
    {
        "icon",
        "button",
        "buttons",
        "menu",
        "menus",
        "input",
        "character",
        "locked",
        "relic",
        "unknown relic",
    }
)
_DANGER = frozenset({"quit", "abandon", "exit", "give up", "save and quit"})
_LEGEND = frozenset(
    {
        "legend",
        "unknown",
        "merchant",
        "treasure",
        "rest",
        "enemy",
        "elite",
        "campfire",
        "chest",
    }
)
_CANON = {
    "map": "Map",
    "end turn": "End Turn",
    "got it": "Got it",
    "singleplayer": "Singleplayer",
    "continue": "Continue",
    "confirm": "Confirm",
    "resume": "Resume",
    "embark": "Embark",
    "proceed": "Proceed",
    "back": "Back",
    "close": "Close",
    "yes": "Yes",
    "no": "No",
    "deck": "Deck",
    "settings": "Settings",
}
_TRANSIT = {
    ("combat", "map"): "Map",
    ("reward", "map"): "Map",
    ("event", "map"): "Map",
    ("shop", "map"): "Map",
    ("rest", "map"): "Map",
    ("title", "map"): "Continue",
    ("select", "map"): "Confirm",
    ("pause", "combat"): "Resume",
    ("pause", "map"): "Resume",
    ("tutorial", "map"): "Got it",
    ("tutorial", "combat"): "Got it",
    ("tutorial", "select"): "Got it",
}
_HOVER_R = 0.12
_SCREEN_HINTS: tuple[tuple[str, frozenset[str]], ...] = (
    ("tutorial", frozenset({"yes", "no", "got it"})),
    ("combat", frozenset({"end turn"})),
    ("pause", frozenset({"resume"})),
    ("map", frozenset({"legend"})),
    ("title", frozenset({"singleplayer"})),
    ("select", frozenset({"confirm", "embark"})),
)


def _norm(text: str) -> str:
    return " ".join((text or "").lower().split())


def canon_label(label: str) -> str:
    compact = _norm(label)
    if compact in _CANON:
        return _CANON[compact]
    parts = compact.split()
    if len(parts) == 1:
        return parts[0].title()
    return " ".join((label or "").split())[:24]


def _ok_label(label: str) -> bool:
    compact = _norm(label)
    if not compact or compact in _PLACEHOLDER or compact in _DANGER or compact in _LEGEND:
        return False
    if len(compact) < 3 and compact not in {"no"}:
        return False
    return bind_verb_ok(label)


def _heading(x: float | None, y: float | None) -> str | None:
    from loop.memory.journal import click_heading

    return click_heading(x, y)


def _neighbor_headings(x: float, y: float) -> list[str]:
    seen: list[str] = []
    for dx in (-0.05, 0.0, 0.05):
        for dy in (-0.05, 0.0, 0.05):
            hit = _heading(x + dx, y + dy)
            if hit and hit not in seen:
                seen.append(hit)
    return seen


def screen_from_labels(labels: Iterable[str], fallback: str | None = None) -> str:
    """Classify the overlay from printed marks so slots do not leak across screens."""
    labs = {_norm(item) for item in labels if item}
    for kind, hints in _SCREEN_HINTS:
        if labs & hints:
            return kind
    key = (fallback or "").strip().lower()
    return key or "other"


@dataclass
class Slot:
    key: str
    screen: str
    heading: str
    label: str
    n: int = 1
    source: str = "ocr"
    x: float = 0.5
    y: float = 0.5


class MarkLexicon:
    """Persistent (screen, heading) → label. IDs are per-frame; headings are not."""

    def __init__(self, *, slot_cap: int = _SLOT_CAP) -> None:
        self.slot_cap = slot_cap
        self.slots: dict[str, Slot] = {}

    def remember(
        self,
        screen: str | None,
        x: float,
        y: float,
        label: str,
        *,
        source: str = "ocr",
    ) -> bool:
        pretty = canon_label(label)
        if not _ok_label(pretty):
            return False
        heading = _heading(x, y)
        if heading is None:
            return False
        kind = (screen or "").strip().lower() or "other"
        key = f"{kind}|{heading}"
        row = self.slots.get(key)
        compact = _norm(pretty)
        if row is None:
            self._evict()
            self.slots[key] = Slot(
                key=key,
                screen=kind,
                heading=heading,
                label=pretty,
                n=1,
                source=source,
                x=float(x),
                y=float(y),
            )
            return True
        same = _norm(row.label) == compact
        if not same:
            incoming_rank = _SOURCE_RANK.get(source, 0)
            existing_rank = _SOURCE_RANK.get(row.source, 0)
            if incoming_rank < existing_rank:
                return False
        if source == "ocr":
            row.label = pretty
            row.source = "ocr"
            row.n = row.n + 1 if same else 1
            row.x, row.y = float(x), float(y)
            return True
        if not same and row.source == "ocr":
            return False
        if same:
            row.n += 1
            row.x, row.y = float(x), float(y)
            return True
        if source == "hover" and row.source == "transit":
            row.label = pretty
            row.source = "hover"
            row.n = 1
            row.x, row.y = float(x), float(y)
            return True
        return False

    def label_for(self, screen: str | None, x: float, y: float) -> str | None:
        kind = (screen or "").strip().lower() or "other"
        return self._lookup(kind, x, y)

    def label_at(self, x: float, y: float, screen: str | None = None) -> str | None:
        """Screen-scoped lookup, then a unique heading match on any screen (HUD chrome)."""
        if screen:
            hit = self.label_for(screen, x, y)
            if hit:
                return hit
        heading = _heading(x, y)
        if heading is None:
            return None
        neighbors = set(_neighbor_headings(x, y))
        hits: list[Slot] = []
        for row in self.slots.values():
            if row.heading == heading or row.heading in neighbors:
                hits.append(row)
        if not hits:
            return None
        labels = {_norm(row.label) for row in hits}
        if len(labels) != 1:
            return max(hits, key=lambda row: (row.n, _SOURCE_RANK.get(row.source, 0))).label
        return hits[0].label

    def ingest(self, screen: str | None, marks: Iterable[Any]) -> None:
        for mark in marks:
            label = str(getattr(mark, "label", "") or "")
            try:
                x = float(getattr(mark, "x"))
                y = float(getattr(mark, "y"))
            except (TypeError, ValueError, AttributeError):
                continue
            self.remember(screen, x, y, label, source="ocr")

    def discover_click(
        self,
        *,
        screen: str | None,
        next_screen: str | None,
        x: float,
        y: float,
        clicked_label: str = "",
        pixel_changed: bool,
        hovered: bool = False,
        words: Iterable[tuple[float, float, str]] | None = None,
    ) -> bool:
        if hovered and pixel_changed and words:
            if self._hover_from_words(screen, x, y, words):
                return True
        if not pixel_changed:
            return False
        compact = _norm(clicked_label)
        if compact and compact not in _PLACEHOLDER and _ok_label(clicked_label):
            return False
        prev = (screen or "").strip().lower() or "other"
        nxt = (next_screen or "").strip().lower() or "other"
        if prev == nxt:
            return False
        learned = _TRANSIT.get((prev, nxt))
        if not learned:
            return False
        return self.remember(prev, x, y, learned, source="transit")

    def _hover_from_words(
        self,
        screen: str | None,
        x: float,
        y: float,
        words: Iterable[tuple[float, float, str]],
    ) -> bool:
        best_label = ""
        best_d = _HOVER_R
        for wx, wy, raw in words:
            if not _ok_label(raw):
                continue
            dist = ((float(wx) - x) ** 2 + (float(wy) - y) ** 2) ** 0.5
            if dist < best_d:
                best_d = dist
                best_label = raw
        if not best_label:
            return False
        return self.remember(screen, x, y, best_label, source="hover")

    def forget(self, screen: str | None, x: float, y: float) -> bool:
        """Drop lexicon slot at this heading (and neighbors) after a blocked click."""
        kind = (screen or "").strip().lower() or "other"
        heading = _heading(x, y)
        if heading is None:
            return False
        removed = False
        for h in {heading, *_neighbor_headings(x, y)}:
            key = f"{kind}|{h}"
            if key in self.slots:
                del self.slots[key]
                removed = True
        return removed

    def _lookup(self, screen: str, x: float, y: float) -> str | None:
        heading = _heading(x, y)
        if heading is None:
            return None
        exact = self.slots.get(f"{screen}|{heading}")
        if exact is not None:
            return exact.label
        hits = [
            self.slots[f"{screen}|{h}"]
            for h in _neighbor_headings(x, y)
            if f"{screen}|{h}" in self.slots
        ]
        if not hits:
            return None
        return max(hits, key=lambda row: (row.n, _SOURCE_RANK.get(row.source, 0))).label

    def _evict(self) -> None:
        while len(self.slots) >= self.slot_cap:
            victim = min(
                self.slots.items(),
                key=lambda item: (item[1].n, _SOURCE_RANK.get(item[1].source, 0)),
            )
            del self.slots[victim[0]]
