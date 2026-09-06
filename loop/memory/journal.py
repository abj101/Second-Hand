from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

from loop.config import project_root
from loop.labels import bind_verb_ok, progress_index
from loop.memory.graph import PlayGraph
from loop.memory.lexicon import MarkLexicon, Slot
from loop.os.saliency import prompt_looks_locked, walk_dir_for_point

FACE = frozenset({"a", "b", "x", "y"})
_KEEP = frozenset({"lock", "blocked", "cleared", "moved", "tooltip"})
_CLEAR_MAD = 0.015
_CLICK_BIN = 50
_RIGHT_FRAC = 0.82
_NOTE_CAP = 2
_MISS = frozenset({"noop", "blocked"})
def _progress_verb(text: str) -> int | None:
    return progress_index(text)


def _bind_rank(row: Binding) -> tuple[int, int, int]:
    idx = _progress_verb(row.verb)
    if idx is not None:
        return (0, idx, -row.n)
    return (1, 9, -row.n)


def journal_path(fixture: str, root: Path | None = None) -> Path:
    return (root or project_root()) / "data" / "loop" / fixture / "journal.json"


def run_journal_path(run_dir: Path) -> Path:
    """Run-scoped journal for STS2 collect (one file per runs/<stamp>_sts2_collect/)."""
    return run_dir / "journal.json"


def _load_row(cls: type, row: object) -> Any | None:
    if not isinstance(row, dict):
        return None
    allowed = {item.name for item in fields(cls)}
    payload = {key: value for key, value in row.items() if key in allowed}
    try:
        return cls(**payload)
    except TypeError:
        return None


def _norm(text: str) -> str:
    return " ".join((text or "").lower().split())


def _clip(text: str, n: int = 48) -> str:
    blob = " ".join((text or "").split())
    if len(blob) <= n:
        return blob
    return blob[: n - 1] + "…"


def _ui_bind_key(kind: str, device: str, label: str) -> str:
    token = (device or "").strip().lower()
    verb = _norm(label)
    return f"ui|{kind}|{token}|{verb[:32]}"


def _stable_bind_key(*, kind: str, heading: str | None, role: str) -> str:
    return f"stable|{(kind or 'other').strip().lower()}|{heading or '?'}|{(role or 'other').strip().lower()}"


def _ui_role(device: str, label: str) -> str:
    token = (device or "").strip().lower()
    if ">" in token:
        return "card_pair"
    if progress_index(label) is not None:
        return "control"
    return "control" if bind_verb_ok(label) else "label"


_EVIDENCE_RANK = {
    "combat_won": 5,
    "transaction": 4,
    "transition": 3,
    "tooltip": 2,
    "pixel": 1,
    "observation": 0,
    "hunch": 0,
}


def _evidence_rank(evidence: str, outcome: str) -> int:
    base = _EVIDENCE_RANK.get((evidence or "").strip().lower(), 0)
    if outcome in {"moved", "cleared"} and base < _EVIDENCE_RANK["transition"]:
        return _EVIDENCE_RANK["pixel"]
    return base


def _click_bind_verified(*, outcome: str, kind: str, next_screen: str | None, hovered: bool, label: str) -> bool:
    if outcome == "tooltip" and hovered:
        return True
    nxt = (next_screen or "").strip().lower()
    cur = (kind or "").strip().lower() or "other"
    if nxt and nxt != cur:
        return True
    if outcome == "moved" and progress_index(label) is not None:
        return True
    return False


def _contradictory_verbs(left: str, right: str) -> bool:
    a = _norm(left)
    b = _norm(right)
    return bool(a and b and a != b)


def prompt_key(text: str, *, locked: bool = False) -> str:
    """Collapse lock toasts so the same door does not fork on OCR flicker."""
    if locked or prompt_looks_locked(text):
        return "lock"
    return _norm(text)[:48]


def kind_from_boxes(boxes: list[Any] | None) -> str | None:
    for box in boxes or []:
        label = str(getattr(box, "label", "") or "").strip().lower()
        if label:
            return label
    return None


def heading_from_boxes(boxes: list[Any] | None, last_walk: str | None = None) -> str | None:
    if last_walk:
        return last_walk
    if not boxes:
        return None
    box = boxes[0]
    x = float(getattr(box, "x", 0.0)) + float(getattr(box, "w", 0.0)) / 2.0
    y = float(getattr(box, "y", 0.0)) + float(getattr(box, "h", 0.0)) / 2.0
    return walk_dir_for_point(x, y)


def face_try_outcome(
    *,
    prompt_on: bool,
    prompt_cleared: bool,
    prompt_after: bool,
    confirmed: bool,
    pixel_mad: float | None,
    view_changed: bool,
) -> str:
    """Journal label for a face tap. Flicker / unconfirmed clear is a jump, not Open."""
    if not prompt_on:
        return "jump"
    if prompt_cleared:
        flicker = (pixel_mad or 0.0) < _CLEAR_MAD and not view_changed
        if flicker or not confirmed:
            return "jump"
        return "cleared"
    if prompt_after:
        return "lock"
    return "noop"


def click_heading(x: float | None, y: float | None) -> str | None:
    """Quantized 0-1000 cell, or 'right' for the far strip. Sensor coords only."""
    if x is None or y is None:
        return None
    try:
        nx = max(0.0, min(1.0, float(x)))
        ny = max(0.0, min(1.0, float(y)))
    except (TypeError, ValueError):
        return None
    if nx >= _RIGHT_FRAC:
        return "right"
    gx = int(round(nx * 1000 / _CLICK_BIN) * _CLICK_BIN)
    gy = int(round(ny * 1000 / _CLICK_BIN) * _CLICK_BIN)
    gx = max(0, min(1000, gx))
    gy = max(0, min(1000, gy))
    return f"g{gx},g{gy}"


def click_query(*, screen: str | None, x: float | None, y: float | None) -> str:
    kind = (screen or "").strip().lower() or "other"
    heading = click_heading(x, y)
    if heading is None:
        return kind
    return f"{kind} {heading.replace(',', ' ')}"


def face_device(action: Any) -> str | None:
    tapped = list(getattr(action, "tapped", None) or [])
    held = list(getattr(action, "held", None) or [])
    for name in tapped + held:
        token = str(name).strip().lower()
        if token in FACE:
            return token
    derived = ""
    if hasattr(action, "derive_name"):
        derived = str(action.derive_name() or "")
    name = str(getattr(action, "name", "") or derived).strip().lower()
    return name if name in FACE else None


@dataclass
class Fact:
    key: str
    kind: str
    heading: str | None
    prompt: str
    glyph: str | None
    device: str | None
    outcome: str
    step_id: int = 0
    provenance: str = ""
    confidence: float = 0.5
    evidence: str = "observation"
    source_step: int = 0

    def line(self) -> str:
        bits = [self.kind]
        if self.heading:
            bits.append(f"heading={self.heading}")
        if self.prompt:
            cap = 80 if self.kind == "note" else 40
            bits.append(f'prompt="{_clip(self.prompt, cap)}"')
        elif self.outcome == "lock":
            bits.append("prompt=lock")
        if self.glyph:
            bits.append(f"glyph={self.glyph}")
        if self.device:
            bits.append(f"tried={self.device}")
        bits.append(self.outcome)
        return " ".join(bits)


@dataclass
class Binding:
    key: str
    glyph: str
    verb: str
    device: str
    outcome: str
    n: int = 1
    kind: str = ""
    heading: str | None = None
    role: str = ""
    provenance: str = ""
    confidence: float = 0.5
    evidence: str = "observation"
    source_step: int = 0

    def line(self) -> str:
        verb = _clip(self.verb, 24) if self.verb else "(no copy)"
        return f'glyph={self.glyph} "{verb}" → {self.device} → {self.outcome} ×{self.n}'


class FactJournal:
    """Keyed interact/spatial facts + glyph bindings. Sensor-written, not thoughts."""

    def __init__(self, *, fact_cap: int = 64, bind_cap: int = 16, path: Path | None = None) -> None:
        self.fact_cap = fact_cap
        self.bind_cap = bind_cap
        self.path = path
        self.facts: dict[str, Fact] = {}
        self.bindings: dict[str, Binding] = {}
        self._order: list[str] = []
        self.goal = ""
        self.lexicon = MarkLexicon()
        self.graph = PlayGraph()
        if path is not None and path.exists():
            self.load(path)

    def make_key(self, *, kind: str, heading: str | None, prompt: str, locked: bool = False) -> str:
        if kind == "note":
            return f"note|?|{_norm(prompt)[:48]}"
        return f"{kind}|{heading or '?'}|{prompt_key(prompt, locked=locked)}"

    def observe(
        self,
        *,
        kind: str,
        heading: str | None = None,
        prompt: str = "",
        glyph: str | None = None,
        outcome: str = "seen",
        locked: bool = False,
        step_id: int = 0,
    ) -> Fact | None:
        kind = (kind or "").strip().lower()
        if not kind:
            return None
        key = self.make_key(kind=kind, heading=heading, prompt=prompt, locked=locked)
        existing = self.facts.get(key)
        if existing and existing.outcome in _KEEP and outcome == "seen":
            existing.step_id = step_id
            if glyph and not existing.glyph:
                existing.glyph = glyph
            self._touch(key)
            return existing
        fact = Fact(
            key=key,
            kind=kind,
            heading=heading,
            prompt=_clip(prompt, 80),
            glyph=glyph or (existing.glyph if existing else None),
            device=existing.device if existing else None,
            outcome=outcome,
            step_id=step_id,
        )
        self.facts[key] = fact
        self._touch(key)
        self._evict_facts()
        self._maybe_autosave(outcome)
        return fact

    def record_try(
        self,
        *,
        kind: str,
        heading: str | None,
        prompt: str,
        glyph: str | None,
        device: str,
        outcome: str,
        locked: bool = False,
        step_id: int = 0,
    ) -> Fact | None:
        fact = self.observe(
            kind=kind,
            heading=heading,
            prompt=prompt,
            glyph=glyph,
            outcome=outcome,
            locked=locked or outcome == "lock",
            step_id=step_id,
        )
        if fact is None:
            return None
        fact.device = device
        fact.glyph = glyph or fact.glyph
        fact.outcome = outcome
        fact.step_id = step_id
        self._touch(fact.key)
        self._bind(glyph=glyph, prompt=prompt, device=device, outcome=outcome, kind=kind)
        self._maybe_autosave(outcome)
        return fact

    def record_click(
        self,
        *,
        screen: str | None,
        x: float | None,
        y: float | None,
        device: str,
        pixel_changed: bool,
        step_id: int = 0,
        label: str = "",
        hovered: bool = False,
        next_screen: str | None = None,
    ) -> Fact | None:
        heading = click_heading(x, y)
        if heading is None:
            return None
        token = (device or "").strip()
        if not token:
            return None
        kind = (screen or "").strip().lower() or "other"
        prompt = (label or "").strip()
        if hovered:
            outcome = "tooltip" if pixel_changed else "blocked"
        elif pixel_changed:
            outcome = "moved"
        else:
            outcome = "blocked" if self._prior_click_miss(
                kind=kind, heading=heading, prompt=prompt, device=token, label=prompt
            ) else "noop"
        fact = self.record_try(
            kind=kind,
            heading=heading,
            prompt=prompt,
            glyph=token if token.startswith("mk_") else None,
            device=token,
            outcome=outcome,
            step_id=step_id,
        )
        if prompt and bind_verb_ok(label):
            role = _ui_role(token, prompt)
            if role != "card_pair" and _click_bind_verified(
                outcome=outcome,
                kind=kind,
                next_screen=next_screen,
                hovered=hovered,
                label=label,
            ):
                evidence = "transition"
                if outcome == "tooltip" and hovered:
                    evidence = "tooltip"
                elif not (
                    next_screen
                    and _norm(next_screen) != _norm(kind)
                ):
                    evidence = "pixel"
                self._bind_ui(
                    device=token,
                    label=label,
                    outcome=outcome,
                    kind=kind,
                    heading=heading,
                    evidence=evidence,
                    step_id=step_id,
                )
        self.graph.record_edge(
            src=kind,
            dst=next_screen or kind,
            heading=heading,
            x=x,
            y=y,
            label=prompt,
            outcome=outcome,
        )
        if outcome in {"lock", "moved", "blocked", "tooltip"} and self.path is not None:
            try:
                self.save()
            except OSError:
                return fact
        return fact

    def ingest_aim(self, screen: str | None, marks: Any) -> None:
        self.lexicon.ingest(screen, marks or ())

    def ingest_ocr(self, screen: str | None, text: str, *, dialog: bool = False) -> int:
        added = self.graph.ingest_ocr(screen, text, dialog=dialog)
        if added and self.path is not None:
            try:
                self.save()
            except OSError:
                return added
        return added

    def discover_overlay(
        self,
        *,
        screen: str | None,
        next_screen: str | None,
        x: float,
        y: float,
        clicked_label: str = "",
        pixel_changed: bool,
        hovered: bool = False,
        words: Any = None,
    ) -> bool:
        learned = self.lexicon.discover_click(
            screen=screen,
            next_screen=next_screen,
            x=x,
            y=y,
            clicked_label=clicked_label,
            pixel_changed=pixel_changed,
            hovered=hovered,
            words=words,
        )
        if learned and self.path is not None:
            try:
                self.save()
            except OSError:
                return learned
        return learned

    def reset_episode_scope(self) -> None:
        """Drop VLM notes and bindings at an episode boundary.

        Journal bindings are run-scoped by default and survive ``reset_episode``
        when memory_scope is ``run`` (one collect run = one journal file).
        """
        for key in list(self.facts.keys()):
            fact = self.facts.get(key)
            if fact is None or fact.kind != "note":
                continue
            self.facts.pop(key, None)
            if key in self._order:
                self._order.remove(key)
        self.bindings.clear()

    def remember_note(self, note: str, *, step_id: int = 0) -> Fact | None:
        """VLM dump_all remember: keep the note as a journal fact, not a cosine thumb."""
        blob = " ".join((note or "").split())
        if not blob:
            return None
        fact = self.observe(
            kind="note",
            heading=None,
            prompt=blob,
            outcome="seen",
            step_id=step_id,
        )
        if self.path is not None:
            try:
                self.save()
            except OSError:
                return fact
        return fact

    def clear_episode_notes(self) -> None:
        """Drop journal notes at episode boundary when memory_scope is episode."""
        drop = [key for key, fact in self.facts.items() if fact.kind == "note"]
        for key in drop:
            del self.facts[key]
            if key in self._order:
                self._order.remove(key)
        if drop and self.path is not None:
            try:
                self.save()
            except OSError:
                return

    def load(self, path: Path | None = None) -> None:
        target = path if path is not None else self.path
        if target is None:
            return
        if not target.exists():
            return
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            self.facts = {}
            self.bindings = {}
            self._order = []
            self.goal = ""
            self.lexicon = MarkLexicon()
            self.graph = PlayGraph()
            return
        if not isinstance(data, dict):
            self.facts = {}
            self.bindings = {}
            self._order = []
            self.goal = ""
            self.lexicon = MarkLexicon()
            self.graph = PlayGraph()
            return
        try:
            facts: dict[str, Fact] = {}
            for row in data.get("facts") or []:
                fact = _load_row(Fact, row)
                if fact is None or not fact.key:
                    continue
                facts[fact.key] = fact
            bindings: dict[str, Binding] = {}
            for row in data.get("bindings") or []:
                bind = _load_row(Binding, row)
                if bind is None or not bind.key:
                    continue
                bindings[bind.key] = bind
            lexicon = MarkLexicon()
            for row in data.get("slots") or []:
                slot = _load_row(Slot, row)
                if slot is None or not slot.key:
                    continue
                lexicon.slots[slot.key] = slot
            order_raw = data.get("_order")
            if not isinstance(order_raw, list):
                order_raw = []
            order = [str(key) for key in order_raw if str(key) in facts]
            for key in facts:
                if key not in order:
                    order.append(key)
        except (TypeError, ValueError, AttributeError):
            self.facts = {}
            self.bindings = {}
            self._order = []
            self.goal = ""
            self.lexicon = MarkLexicon()
            self.graph = PlayGraph()
            return
        self.facts = facts
        self.bindings = bindings
        self._order = order
        self.goal = str(data.get("goal") or "").strip()
        self.lexicon = lexicon
        graph = PlayGraph()
        graph.load_dict(data.get("graph"))
        self.graph = graph

    def save(self, path: Path | None = None) -> None:
        target = path if path is not None else self.path
        if target is None:
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "facts": [asdict(fact) for fact in self.facts.values()],
            "bindings": [asdict(row) for row in self.bindings.values()],
            "slots": [asdict(slot) for slot in self.lexicon.slots.values()],
            "graph": self.graph.to_dict(),
            "_order": list(self._order),
            "goal": self.goal,
        }
        target.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def locked_kinds(self) -> set[str]:
        latest: dict[str, Fact] = {fact.kind: fact for fact in self.facts.values()}
        for key in self._order:
            fact = self.facts.get(key)
            if fact is not None:
                latest[fact.kind] = fact
        return {kind for kind, fact in latest.items() if fact.outcome == "lock"}

    def is_locked(self, kind: str | None, heading: str | None = None) -> bool:
        if kind is None:
            return False
        kind = kind.strip().lower()
        if not kind:
            return False
        for fact in self.facts.values():
            if fact.outcome != "lock" or fact.kind != kind:
                continue
            if heading is not None and fact.heading != heading:
                continue
            return True
        return False

    def _maybe_autosave(self, outcome: str) -> None:
        if outcome not in {"lock", "moved", "blocked", "tooltip"} or self.path is None:
            return
        try:
            self.save()
        except OSError:
            return

    def prompt_block(self, *, limit: int = 10) -> str:
        binds = sorted(self.bindings.values(), key=_bind_rank)
        binds = binds[:4]
        fact_n = max(0, limit - len(binds))
        ranked = self._ranked_facts()
        sensors = [fact for fact in ranked if fact.kind != "note"]
        notes = [fact for fact in ranked if fact.kind == "note"]
        facts = sensors[:fact_n]
        room = max(0, fact_n - len(facts))
        facts = facts + notes[: min(_NOTE_CAP, room)]
        if not facts and not binds:
            return ""
        lines = ["Journal:"]
        for fact in facts:
            lines.append(f"- {fact.line()}")
        if binds:
            lines.append("Bindings:")
            for row in binds:
                lines.append(f"- {row.line()}")
        return "\n".join(lines)

    def recall(self, query: str, *, k: int = 5) -> list[str]:
        tokens = {tok for tok in _norm(query).replace("|", " ").replace("=", " ").split() if tok}
        if not tokens:
            return [row.line() for row in self._ranked_facts()[:k]]
        scored: list[tuple[int, str]] = []
        for fact in self.facts.values():
            hay = _norm(
                f"{fact.kind} {fact.heading or ''} {fact.prompt} "
                f"{fact.glyph or ''} {fact.device or ''} {fact.outcome}"
            )
            score = sum(1 for tok in tokens if tok in hay)
            if score:
                scored.append((score, fact.line()))
        for row in self.bindings.values():
            hay = _norm(f"{row.glyph} {row.verb} {row.device} {row.outcome} bind")
            score = sum(1 for tok in tokens if tok in hay)
            if score:
                scored.append((score, row.line()))
        scored.sort(key=lambda item: (-item[0], item[1]))
        hits = [line for _, line in scored[:k]]
        return hits or [row.line() for row in self._ranked_facts()[:k]]

    def _prior_click_miss(
        self,
        *,
        kind: str,
        heading: str | None,
        prompt: str,
        device: str,
        label: str,
    ) -> bool:
        existing = self.facts.get(self.make_key(kind=kind, heading=heading, prompt=prompt))
        if existing is not None and existing.outcome in _MISS:
            return True
        if label and bind_verb_ok(label):
            role = _ui_role(device, label)
            stable = _stable_bind_key(kind=kind, heading=heading, role=role)
            row = self.bindings.get(stable)
            if row is None:
                row = self.bindings.get(_ui_bind_key(kind, device, label))
            if row is not None and row.n >= 1 and row.outcome in _MISS:
                return True
        return False

    def _bind(self, *, glyph: str | None, prompt: str, device: str, outcome: str, kind: str = "") -> None:
        face = (glyph or device or "").strip().lower()
        if face not in FACE or not device:
            return
        verb = _norm(prompt)
        key = f"{face}|{verb[:32]}|{device}"
        row = self.bindings.get(key)
        if row is None:
            self._evict_binds()
            self.bindings[key] = Binding(
                key=key,
                glyph=face,
                verb=_clip(prompt, 40),
                device=device,
                outcome=outcome,
                n=1,
                kind=kind or "",
            )
            return
        row.n += 1
        row.outcome = outcome
        if prompt:
            row.verb = _clip(prompt, 40)
        if kind:
            row.kind = kind

    def _bind_ui(
        self,
        *,
        device: str,
        label: str,
        outcome: str,
        kind: str = "",
        heading: str | None = None,
        evidence: str = "transition",
        step_id: int = 0,
    ) -> None:
        token = (device or "").strip().lower()
        verb = _norm(label)
        if not token or not verb:
            return
        role = _ui_role(token, label)
        if role == "card_pair":
            return
        stable_key = _stable_bind_key(kind=kind, heading=heading, role=role)
        rank = _evidence_rank(evidence, outcome)
        row = self.bindings.get(stable_key)
        if row is not None and _contradictory_verbs(row.verb, label):
            if rank <= _evidence_rank(row.evidence, row.outcome):
                return
            row.n = 1
        elif row is None:
            self._evict_binds()
            row = Binding(
                key=stable_key,
                glyph=token,
                verb=_clip(label, 40),
                device=token,
                outcome=outcome,
                n=1,
                kind=kind or "",
                heading=heading,
                role=role,
                provenance=f"{kind}:{heading or '?'}",
                confidence=min(1.0, 0.5 + rank * 0.15),
                evidence=evidence,
                source_step=step_id,
            )
            self.bindings[stable_key] = row
            if outcome in _KEEP and self.path is not None:
                try:
                    self.save()
                except OSError:
                    return
            return
        row.n += 1
        row.outcome = outcome
        row.verb = _clip(label, 40)
        row.glyph = token
        row.device = token
        row.confidence = max(row.confidence, min(1.0, 0.5 + rank * 0.15))
        row.evidence = evidence
        row.source_step = step_id
        if kind:
            row.kind = kind
        if heading:
            row.heading = heading
        row.role = role

    def _ranked_facts(self) -> list[Fact]:
        rank = {
            "lock": 0,
            "blocked": 1,
            "moved": 2,
            "tooltip": 3,
            "cleared": 4,
            "jump": 5,
            "noop": 6,
            "seen": 7,
        }

        def _key(fact: Fact) -> tuple[int, int, int, int]:
            if fact.kind == "note":
                return (2, 0, rank.get(fact.outcome, 9), -fact.step_id)
            progress = _progress_verb(fact.prompt)
            labeled = 0 if progress is not None else 1
            return (labeled, progress if progress is not None else 9, rank.get(fact.outcome, 9), -fact.step_id)

        return sorted(self.facts.values(), key=_key)

    def _touch(self, key: str) -> None:
        if key in self._order:
            self._order.remove(key)
        self._order.append(key)

    def _evict_facts(self) -> None:
        while len(self.facts) > self.fact_cap:
            victim = next(
                (key for key in self._order if self.facts.get(key) and self.facts[key].outcome not in _KEEP),
                self._order[0] if self._order else None,
            )
            if victim is None:
                return
            self._order.remove(victim)
            self.facts.pop(victim, None)

    def _evict_binds(self) -> None:
        while len(self.bindings) >= self.bind_cap:
            victim = next(
                (key for key, row in self.bindings.items() if row.outcome not in _KEEP),
                next(iter(self.bindings), None),
            )
            if victim is None:
                return
            del self.bindings[victim]
