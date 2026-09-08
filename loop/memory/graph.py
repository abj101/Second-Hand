"""Sensor-grounded play graph: screens, click edges, tutorial OCR claims.

Not a planner. The 4B still picks the click. This answers what we have proven
and what is still unknown on the current screen.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, fields
from typing import Any, Iterable

from loop.labels import progress_index

_EDGE_CAP = 48
_CLAIM_CAP = 16
_BUTTON = frozenset(
    {
        "yes",
        "no",
        "got it",
        "continue",
        "confirm",
        "resume",
        "close",
        "back",
        "singleplayer",
        "embark",
        "proceed",
        "end turn",
        "quit",
        "play",
    }
)
_OCR_JUNK = frozenset(
    {
        "points",
        "obtained",
        "share",
        "legend",
        "hp",
        "gold",
        "floor",
        "paused",
        "give up",
        "save and quit",
    }
)
_PLAY_WORDS = frozenset(
    {
        "energy",
        "block",
        "card",
        "cards",
        "turn",
        "enemy",
        "damage",
        "exhaust",
        "intent",
        "hand",
        "draw",
        "discard",
    }
)
_PLACEHOLDER = frozenset({"icon", "button", "buttons", "menu", "locked", "character"})
_PROGRESS = (
    "continue",
    "confirm",
    "end turn",
    "got it",
    "yes",
    "resume",
    "singleplayer",
    "embark",
)


def _norm(text: str) -> str:
    return " ".join((text or "").lower().split())


def _clip(text: str, n: int = 80) -> str:
    blob = " ".join((text or "").split())
    if len(blob) <= n:
        return blob
    return blob[: n - 1] + "…"


def _load_row(cls: type, row: object) -> Any | None:
    if not isinstance(row, dict):
        return None
    allowed = {item.name for item in fields(cls)}
    payload = {key: value for key, value in row.items() if key in allowed}
    try:
        return cls(**payload)
    except TypeError:
        return None


def _heading(x: float | None, y: float | None) -> str | None:
    from loop.memory.journal import click_heading

    return click_heading(x, y)


def listing_parts(aim_listing: str | None) -> list[tuple[int, str]]:
    """(mark_id, compact_label) from Marks listing."""
    out: list[tuple[int, str]] = []
    for part in (aim_listing or "").split():
        if "@" not in part:
            continue
        head, _, rest = part.partition("@")
        try:
            mid = int(head)
        except ValueError:
            continue
        label = ""
        if ":" in rest:
            label = rest.split(":", 1)[1].replace("_", " ")
        out.append((mid, _norm(label)))
    return out


@dataclass
class Edge:
    key: str
    src: str
    heading: str
    label: str
    dst: str
    outcome: str
    n: int = 1


@dataclass
class Claim:
    key: str
    text: str
    screen: str
    n: int = 1


class PlayGraph:
    """(screen, heading/label) click outcomes + OCR tutorial claims."""

    def __init__(self) -> None:
        self.edges: dict[str, Edge] = {}
        self.claims: dict[str, Claim] = {}
        self._claim_order: list[str] = []

    def to_dict(self) -> dict[str, Any]:
        return {
            "edges": [asdict(row) for row in self.edges.values()],
            "claims": [asdict(row) for row in self.claims.values()],
            "_claim_order": list(self._claim_order),
        }

    def load_dict(self, data: object) -> None:
        self.edges = {}
        self.claims = {}
        self._claim_order = []
        if not isinstance(data, dict):
            return
        for row in data.get("edges") or []:
            edge = _load_row(Edge, row)
            if edge is None or not edge.key:
                continue
            self.edges[edge.key] = edge
        for row in data.get("claims") or []:
            claim = _load_row(Claim, row)
            if claim is None or not claim.key:
                continue
            self.claims[claim.key] = claim
        order = data.get("_claim_order")
        if isinstance(order, list):
            self._claim_order = [str(key) for key in order if str(key) in self.claims]
        for key in self.claims:
            if key not in self._claim_order:
                self._claim_order.append(key)

    def record_edge(
        self,
        *,
        src: str | None,
        dst: str | None,
        x: float | None = None,
        y: float | None = None,
        heading: str | None = None,
        label: str = "",
        outcome: str = "",
    ) -> Edge | None:
        kind = (src or "").strip().lower() or "other"
        nxt = (dst or kind).strip().lower() or kind
        head = heading or _heading(x, y)
        if head is None:
            return None
        verb = _clip(label, 32)
        key = f"{kind}|{head}|{_norm(verb)[:24]}"
        row = self.edges.get(key)
        if row is None:
            self._evict_edges()
            row = Edge(
                key=key,
                src=kind,
                heading=head,
                label=verb,
                dst=nxt,
                outcome=outcome or "noop",
                n=1,
            )
            self.edges[key] = row
            return row
        row.n += 1
        row.outcome = outcome or row.outcome
        row.dst = nxt
        if verb:
            row.label = verb
        return row

    def ingest_ocr(self, screen: str | None, text: str, *, dialog: bool = False) -> int:
        kind = (screen or "").strip().lower() or "other"
        if kind not in {"tutorial", "other"} and not dialog:
            return 0
        added = 0
        for sent in _claim_sentences(text):
            digest = hashlib.sha1(sent.encode("utf-8")).hexdigest()[:12]
            existing = self.claims.get(digest)
            if existing is not None:
                existing.n += 1
                existing.screen = kind
                continue
            self._evict_claims()
            self.claims[digest] = Claim(key=digest, text=sent, screen=kind, n=1)
            self._claim_order.append(digest)
            added += 1
            if added >= 3:
                break
        return added

    def has_outcome(self, screen: str | None, label: str, *, ok: Iterable[str] = ("moved", "tooltip")) -> bool:
        kind = (screen or "").strip().lower() or "other"
        want = _norm(label)
        keep = frozenset(ok)
        for row in self.edges.values():
            if row.src != kind:
                continue
            if want and _norm(row.label) != want:
                continue
            if row.outcome in keep:
                return True
        return False

    def hovered_heading(self, screen: str | None, heading: str | None) -> bool:
        kind = (screen or "").strip().lower() or "other"
        if not heading:
            return False
        for row in self.edges.values():
            if row.src == kind and row.heading == heading and row.outcome in {"tooltip", "moved"}:
                return True
        return False

    def best_edge_line(self, screen: str | None) -> str:
        kind = (screen or "").strip().lower() or "other"
        hits = [
            row
            for row in self.edges.values()
            if row.src == kind and row.outcome in {"moved", "tooltip"}
        ]
        if not hits:
            return ""
        hits.sort(key=lambda row: (-row.n, progress_index(row.label) is None, row.key))
        row = hits[0]
        label = row.label or row.heading
        if row.outcome == "tooltip":
            return f'{label} tooltip on {kind}'
        if row.dst and row.dst != kind:
            return f"{label} on {kind} → {row.dst}"
        return f"{label} on {kind} → {row.outcome}"

    def claim_line(self, screen: str | None) -> str:
        kind = (screen or "").strip().lower() or "other"
        ranked = list(self.claims.values())
        if not ranked:
            return ""
        if kind in {"combat", "map"}:
            play = [c for c in ranked if any(word in _norm(c.text) for word in _PLAY_WORDS)]
            if play:
                ranked = play
        elif kind in {"tutorial", "other"}:
            ranked = [c for c in ranked if c.screen in {"tutorial", "other"}] or ranked
        ranked.sort(key=lambda c: (-c.n, -len(c.text)))
        return ranked[0].text

    def slice(
        self,
        screen: str | None,
        aim_listing: str | None,
        *,
        cap: int = 6,
    ) -> list[str]:
        lines: list[str] = []
        unseen = _unseen_line(aim_listing)
        if unseen:
            lines.append(unseen)
        edge = self.best_edge_line(screen)
        if edge:
            lines.append(f"Known path: {edge}")
        claim = self.claim_line(screen)
        if claim:
            lines.append(f"Lesson: {claim}")
        return lines[: max(0, cap)]

    def _evict_edges(self) -> None:
        while len(self.edges) >= _EDGE_CAP:
            victim = min(self.edges.items(), key=lambda item: (item[1].n, item[0]))
            del self.edges[victim[0]]

    def _evict_claims(self) -> None:
        while len(self.claims) >= _CLAIM_CAP and self._claim_order:
            key = self._claim_order.pop(0)
            self.claims.pop(key, None)


def _claim_sentences(text: str) -> list[str]:
    blob = " ".join((text or "").split())
    if not blob:
        return []
    bits = re.split(r"(?<=[.!?])\s+|\n+", blob)
    out: list[str] = []
    for raw in bits:
        sent = _clip(raw, 120)
        compact = _norm(sent)
        if len(compact) < 20:
            continue
        if compact in _BUTTON or compact in _OCR_JUNK:
            continue
        if compact.isdigit():
            continue
        words = compact.split()
        if words and all(w in _BUTTON or w in _OCR_JUNK or w.isdigit() for w in words):
            continue
        if compact in {_norm(x) for x in out}:
            continue
        out.append(sent)
        if len(out) >= 3:
            break
    return out


def _unseen_line(aim_listing: str | None) -> str:
    del aim_listing
    return ""


def pick_focus(
    graph: PlayGraph,
    *,
    screen: str | None,
    aim_listing: str | None,
    hovered: Iterable[int] | None = None,
    energy: float | int | None = None,
    selected: str | None = None,
) -> str:
    """One current play move. Not the standing mission."""
    kind = (screen or "").strip().lower() or "other"
    del hovered
    parts = listing_parts(aim_listing)
    labels = {lab for _, lab in parts if lab}
    energy_n: float | None = None
    if isinstance(energy, (int, float)) and not isinstance(energy, bool):
        energy_n = float(energy)

    if kind == "tutorial" or labels & {"yes", "no", "got it"}:
        for word in ("got it", "yes", "no", "close", "back"):
            if word not in labels:
                continue
            pretty = "Got it" if word == "got it" else word.title()
            return f"click printed :{pretty}"
        return "play the next move"

    if kind == "combat":
        if selected:
            return "click a hand card or a creature"
        play = [
            (mid, lab)
            for mid, lab in parts
            if lab and lab not in _PLACEHOLDER and lab != "end turn"
        ]
        if energy_n is None or energy_n > 0:
            play.sort(key=lambda item: (item[1] in _PROGRESS, item[0]))
            for _mid, lab in play:
                if graph.has_outcome(kind, lab, ok=("moved", "tooltip", "cleared")):
                    continue
                pretty = "Got it" if lab == "got it" else lab.title()
                return f"click printed :{pretty}"
            return "click a hand card or a creature"
        if "end turn" in labels:
            return "click printed :End Turn"
        return "click a hand card or a creature"

    for word in _PROGRESS:
        if word == "end turn" or word not in labels:
            continue
        if graph.has_outcome(kind, word, ok=("moved", "cleared")):
            pretty = "Got it" if word == "got it" else word.title()
            return f"click printed :{pretty}"

    labeled = [
        (mid, lab)
        for mid, lab in parts
        if lab and lab not in _PLACEHOLDER and lab != "end turn"
    ]
    labeled.sort(
        key=lambda item: (
            item[1] not in _PROGRESS,
            _PROGRESS.index(item[1]) if item[1] in _PROGRESS else 99,
            item[0],
        )
    )
    for _mid, lab in labeled:
        if graph.has_outcome(kind, lab, ok=("moved", "tooltip", "cleared")):
            continue
        pretty = "Got it" if lab == "got it" else lab.title()
        return f"click printed :{pretty}"

    if kind == "map":
        return "take a map node"
    if kind in {"title", "select"}:
        return "start a run"
    return "play the next move"
