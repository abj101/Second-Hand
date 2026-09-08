"""Durable, semantic collect memory.

This store intentionally keeps only facts whose identity is meaningful after a
new screenshot is captured.  Frame-local aim marks (``mk_4``, ``pt_0.2_0.8``)
are rejected: they are observations, not durable identities.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from loop.config import project_root


Scope = Literal["fixture", "game", "episode"]
FactKind = Literal["identity", "tutorial_rule"]
_SCHEMA_VERSION = 1
_FRAME_LOCAL = re.compile(r"\b(?:mk|mark|pt)_[-a-z0-9.]+\b", re.IGNORECASE)


def semantic_memory_path(fixture: str, root: Path | None = None) -> Path:
    """Location shared by separate collect processes for one fixture."""
    return (root or project_root()) / "data" / "loop" / fixture / "semantic_memory.json"


def _norm(value: str) -> str:
    return " ".join((value or "").strip().lower().split())


def _has_frame_local_mark(*values: str) -> bool:
    return any(_FRAME_LOCAL.search(value or "") is not None for value in values)


@dataclass
class SemanticFact:
    """A reconciled fact that can safely be offered to a later process."""

    key: str
    kind: FactKind
    value: dict[str, str]
    scope: Scope
    provenance: str
    confidence: float
    verified: bool
    created_step: int = 0
    last_confirmed_step: int = 0
    conflict_count: int = 0
    decay_count: int = 0


@dataclass
class SemanticConflict:
    key: str
    incumbent: dict[str, str]
    contender: dict[str, str]
    incumbent_confidence: float
    contender_confidence: float
    winner: str
    step_id: int = 0


class SemanticMemoryStore:
    """Explicit JSON-backed semantic memory with conservative reconciliation.

    Call :meth:`save` after one or more updates.  Facts are not implicitly
    flushed so callers can include a successful action observation and its
    verification in the same atomic update.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.facts: dict[str, SemanticFact] = {}
        self.conflicts: list[SemanticConflict] = []
        self.load()

    @classmethod
    def for_fixture(cls, fixture: str, *, root: Path | None = None) -> "SemanticMemoryStore":
        return cls(semantic_memory_path(fixture, root))

    def load(self) -> None:
        """Replace in-memory contents from disk; malformed files fail closed."""
        self.facts = {}
        self.conflicts = []
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(payload, dict) or payload.get("schema_version") != _SCHEMA_VERSION:
            return
        for row in payload.get("facts", []):
            fact = self._fact_from_row(row)
            if fact is not None:
                self.facts[fact.key] = fact
        for row in payload.get("conflicts", []):
            conflict = self._conflict_from_row(row)
            if conflict is not None:
                self.conflicts.append(conflict)

    def save(self) -> None:
        """Atomically persist the currently reconciled store."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": _SCHEMA_VERSION,
            "facts": [asdict(row) for row in self.facts.values()],
            "conflicts": [asdict(row) for row in self.conflicts],
        }
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(temporary, self.path)

    def clear(self) -> None:
        """Clear both the loaded facts and their persisted file."""
        self.facts = {}
        self.conflicts = []
        if self.path.exists():
            self.path.unlink()

    def remember_identity(
        self,
        *,
        screen: str,
        role: str,
        name: str,
        provenance: str,
        confidence: float,
        verified: bool,
        scope: Scope = "fixture",
        step_id: int = 0,
    ) -> SemanticFact | None:
        """Record a verified semantic identity, never a frame mark binding."""
        fields = {"screen": _norm(screen), "role": _norm(role), "name": " ".join(name.split())}
        if not verified or not all(fields.values()) or _has_frame_local_mark(*fields.values(), provenance):
            return None
        key = f"identity|{fields['screen']}|{fields['role']}|{_norm(fields['name'])}"
        return self._reconcile(
            SemanticFact(
                key=key,
                kind="identity",
                value=fields,
                scope=scope,
                provenance=" ".join(provenance.split()),
                confidence=self._confidence(confidence),
                verified=True,
                created_step=step_id,
                last_confirmed_step=step_id,
            ),
            step_id=step_id,
        )

    def remember_tutorial_rule(
        self,
        *,
        rule_id: str,
        trigger: str,
        action: str,
        effect: str,
        provenance: str,
        confidence: float,
        verified: bool,
        scope: Scope = "fixture",
        step_id: int = 0,
    ) -> SemanticFact | None:
        """Store a structured tutorial rule; unverified rules remain marked so."""
        fields = {
            "rule_id": _norm(rule_id),
            "trigger": " ".join(trigger.split()),
            "action": " ".join(action.split()),
            "effect": " ".join(effect.split()),
        }
        if not all(fields.values()) or _has_frame_local_mark(*fields.values(), provenance):
            return None
        return self._reconcile(
            SemanticFact(
                key=f"tutorial_rule|{fields['rule_id']}",
                kind="tutorial_rule",
                value=fields,
                scope=scope,
                provenance=" ".join(provenance.split()),
                confidence=self._confidence(confidence),
                verified=bool(verified),
                created_step=step_id,
                last_confirmed_step=step_id,
            ),
            step_id=step_id,
        )

    def identities(
        self,
        *,
        screen: str | None = None,
        verified_only: bool = True,
        min_confidence: float = 0.75,
    ) -> list[SemanticFact]:
        """Return portable identity hints, never current-frame bindings.

        Callers receive only evidence that is both verified and sufficiently
        confident by default.  This lets a fresh collect process use a name as
        *context* while still requiring the current frame to establish a
        targetable object.
        """
        wanted = _norm(screen) if screen else None
        rows = [
            row for row in self.facts.values()
            if row.kind == "identity"
            and (wanted is None or row.value.get("screen") == wanted)
            and (not verified_only or row.verified)
            and row.confidence >= min_confidence
        ]
        return sorted(rows, key=lambda row: (-row.confidence, row.key))

    def tutorial_rules(self, *, verified_only: bool = False) -> list[SemanticFact]:
        return [
            row for row in self.facts.values()
            if row.kind == "tutorial_rule" and (row.verified or not verified_only)
        ]

    def decay(self, *, step_id: int, stale_after: int = 10, amount: float = 0.1) -> None:
        """Decay stale confidence without silently deleting audit evidence."""
        if stale_after < 0 or amount <= 0:
            return
        for fact in self.facts.values():
            if step_id - fact.last_confirmed_step < stale_after:
                continue
            fact.confidence = max(0.0, fact.confidence - amount)
            fact.decay_count += 1

    def _reconcile(self, contender: SemanticFact, *, step_id: int) -> SemanticFact:
        incumbent = self.facts.get(contender.key)
        if incumbent is None:
            self.facts[contender.key] = contender
            return contender
        if incumbent.value == contender.value:
            incumbent.confidence = max(incumbent.confidence, contender.confidence)
            incumbent.verified = incumbent.verified or contender.verified
            incumbent.provenance = contender.provenance or incumbent.provenance
            incumbent.last_confirmed_step = step_id
            return incumbent
        incoming_wins = contender.verified and (
            not incumbent.verified or contender.confidence > incumbent.confidence
        )
        winner = "contender" if incoming_wins else "incumbent"
        self.conflicts.append(
            SemanticConflict(
                key=contender.key,
                incumbent=incumbent.value,
                contender=contender.value,
                incumbent_confidence=incumbent.confidence,
                contender_confidence=contender.confidence,
                winner=winner,
                step_id=step_id,
            )
        )
        if incoming_wins:
            contender.conflict_count = incumbent.conflict_count + 1
            self.facts[contender.key] = contender
            return contender
        incumbent.conflict_count += 1
        incumbent.confidence = max(0.0, incumbent.confidence - 0.05)
        return incumbent

    @staticmethod
    def _confidence(value: float) -> float:
        try:
            return max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _fact_from_row(row: Any) -> SemanticFact | None:
        if not isinstance(row, dict):
            return None
        try:
            kind = str(row["kind"])
            scope = str(row["scope"])
            value = row["value"]
            if kind not in {"identity", "tutorial_rule"} or scope not in {"fixture", "game", "episode"}:
                return None
            if not isinstance(value, dict):
                return None
            return SemanticFact(
                key=str(row["key"]), kind=kind, value={str(k): str(v) for k, v in value.items()},
                scope=scope, provenance=str(row.get("provenance") or ""),
                confidence=SemanticMemoryStore._confidence(row.get("confidence", 0.0)),
                verified=bool(row.get("verified")), created_step=int(row.get("created_step", 0)),
                last_confirmed_step=int(row.get("last_confirmed_step", 0)),
                conflict_count=int(row.get("conflict_count", 0)), decay_count=int(row.get("decay_count", 0)),
            )
        except (KeyError, TypeError, ValueError):
            return None

    @staticmethod
    def _conflict_from_row(row: Any) -> SemanticConflict | None:
        if not isinstance(row, dict):
            return None
        try:
            return SemanticConflict(
                key=str(row["key"]), incumbent={str(k): str(v) for k, v in row["incumbent"].items()},
                contender={str(k): str(v) for k, v in row["contender"].items()},
                incumbent_confidence=float(row["incumbent_confidence"]),
                contender_confidence=float(row["contender_confidence"]), winner=str(row["winner"]),
                step_id=int(row.get("step_id", 0)),
            )
        except (KeyError, TypeError, ValueError):
            return None
