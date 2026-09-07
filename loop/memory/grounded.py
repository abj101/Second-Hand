from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from loop.log.schema import StepLog

SourceKind = Literal["outcome", "vlm_note"]
ScopeKind = Literal["episode", "run"]
EvidenceKind = Literal["observation", "hunch", "pixel", "transition", "transaction", "combat_won"]

UNCERTAIN_ERROR_TOKENS: tuple[str, ...] = (
    "source-unconfirmed",
    "target-not-found",
    "effect-unverified",
    "inventory-inconsistent",
)

KNOWN_CAP = 10
HUNCH_CAP = 3
LESSON_CAP = 2
TTL_MISSES = 3
CONFIRM_DECAY = 0.15


def _norm(text: str) -> str:
    return " ".join((text or "").lower().split())


def _clip(text: str, n: int = 80) -> str:
    blob = " ".join((text or "").split())
    if len(blob) <= n:
        return blob
    return blob[: n - 1] + "…"


def fact_key(*, screen: str, heading: str = "", label: str = "") -> str:
    parts = [f"screen={_norm(screen) or 'other'}"]
    if heading:
        parts.append(f"heading={_norm(heading)}")
    if label:
        parts.append(f"label={_norm(label)}")
    return "|".join(parts)


@dataclass
class GroundedFact:
    key: str
    value: str
    source: SourceKind = "vlm_note"
    confidence: float = 0.5
    scope: ScopeKind = "run"
    screen: str = ""
    created_step: int = 0
    last_confirmed_step: int = 0
    miss_count: int = 0
    provenance: str = ""
    evidence: EvidenceKind = "observation"

    def line(self) -> str:
        return _clip(self.value, 72)


@dataclass
class GroundedStore:
    facts: dict[str, GroundedFact] = field(default_factory=dict)
    lessons: list[str] = field(default_factory=list)

    def clear_episode(self) -> None:
        drop = [key for key, row in self.facts.items() if row.scope == "episode"]
        for key in drop:
            del self.facts[key]

    def add_vlm_note(
        self,
        note: str,
        *,
        screen: str | None = None,
        step_id: int = 0,
        scope: ScopeKind = "episode",
    ) -> GroundedFact | None:
        blob = " ".join((note or "").split())
        if not blob:
            return None
        key = fact_key(screen=screen or "other", label=blob[:48])
        row = GroundedFact(
            key=key,
            value=blob,
            source="vlm_note",
            confidence=0.45,
            scope=scope,
            screen=_norm(screen or "other") or "other",
            created_step=step_id,
            last_confirmed_step=step_id,
        )
        self.facts[key] = row
        return row

    def promote_outcome(
        self,
        *,
        screen: str,
        value: str,
        step_id: int = 0,
        scope: ScopeKind = "run",
        heading: str = "",
        label: str = "",
        provenance: str = "",
        evidence: EvidenceKind = "transition",
        confidence: float = 1.0,
    ) -> GroundedFact | None:
        blob = " ".join((value or "").split())
        if not blob:
            return None
        key = fact_key(screen=screen, heading=heading, label=label or blob[:48])
        existing = self.facts.get(key)
        if existing is not None and existing.source == "vlm_note":
            existing.source = "outcome"
            existing.confidence = confidence
            existing.value = blob
            existing.last_confirmed_step = step_id
            existing.miss_count = 0
            existing.provenance = provenance or existing.provenance
            existing.evidence = evidence
            return existing
        if existing is not None and existing.source == "outcome" and existing.value == blob:
            existing.last_confirmed_step = step_id
            existing.confidence = max(existing.confidence, confidence)
            if provenance:
                existing.provenance = provenance
            existing.evidence = evidence
            return existing
        row = GroundedFact(
            key=key,
            value=blob,
            source="outcome",
            confidence=confidence,
            scope=scope,
            screen=_norm(screen) or "other",
            created_step=step_id,
            last_confirmed_step=step_id,
            provenance=provenance,
            evidence=evidence,
        )
        self.facts[key] = row
        return row

    def set_lesson(self, text: str) -> None:
        blob = _clip(text, 96)
        if not blob:
            return
        if self.lessons and self.lessons[-1] == blob:
            return
        self.lessons = (self.lessons + [blob])[-LESSON_CAP:]

    def promote_reward(
        self,
        *,
        screen: str,
        value: str,
        step_id: int = 0,
        scope: ScopeKind = "run",
        heading: str = "",
        label: str = "",
        provenance: str = "",
        evidence: EvidenceKind = "transaction",
    ) -> GroundedFact | None:
        """Promote a positive turn_reward delta to a verified outcome fact."""
        return self.promote_outcome(
            screen=screen,
            value=value,
            step_id=step_id,
            scope=scope,
            heading=heading,
            label=label,
            provenance=provenance,
            evidence=evidence,
        )

    def decay_stale_facts(
        self,
        *,
        screen: str | None,
        hud: dict[str, Any] | None = None,
    ) -> None:
        """Once per step: age facts off-screen and decay HUD contradictions."""
        screen_key = _norm(screen or "other") or "other"
        hud = hud or {}
        for row in self.facts.values():
            if self._contradicts_hud(row, hud):
                row.confidence = max(0.1, row.confidence - CONFIRM_DECAY)
                continue
            if row.screen != screen_key:
                row.miss_count += 1
                if row.miss_count >= TTL_MISSES and row.source == "outcome":
                    row.confidence = max(0.2, row.confidence - CONFIRM_DECAY)
            else:
                row.miss_count = 0

    def rank_for_prompt(
        self,
        *,
        screen: str | None,
        hud: dict[str, Any] | None = None,
        cap: int = KNOWN_CAP,
    ) -> tuple[list[GroundedFact], list[GroundedFact]]:
        screen_key = _norm(screen or "other") or "other"
        hud = hud or {}
        known: list[tuple[tuple[int, int, float], GroundedFact]] = []
        hunch: list[tuple[tuple[int, int, float], GroundedFact]] = []
        for row in self.facts.values():
            if self._contradicts_hud(row, hud):
                continue
            if row.screen != screen_key:
                continue
            rank = (
                0 if row.source == "outcome" else 1,
                -row.last_confirmed_step,
                -row.confidence,
            )
            if row.source == "outcome":
                known.append((rank, row))
            else:
                hunch.append((rank, row))
        known.sort(key=lambda item: item[0])
        hunch.sort(key=lambda item: item[0])
        return (
            [row for _, row in known[:cap]],
            [row for _, row in hunch[:HUNCH_CAP]],
        )

    def snapshot(self) -> dict[str, Any]:
        return {
            "facts": [asdict(row) for row in self.facts.values()],
            "lessons": list(self.lessons),
        }

    def load_snapshot(self, payload: dict[str, Any] | None) -> None:
        if not isinstance(payload, dict):
            return
        facts: dict[str, GroundedFact] = {}
        for row in payload.get("facts") or []:
            if not isinstance(row, dict) or not row.get("key"):
                continue
            try:
                facts[str(row["key"])] = GroundedFact(
                    key=str(row["key"]),
                    value=str(row.get("value") or ""),
                    source=str(row.get("source") or "vlm_note"),  # type: ignore[arg-type]
                    confidence=float(row.get("confidence") or 0.5),
                    scope=str(row.get("scope") or "run"),  # type: ignore[arg-type]
                    screen=str(row.get("screen") or ""),
                    created_step=int(row.get("created_step") or 0),
                    last_confirmed_step=int(row.get("last_confirmed_step") or 0),
                    miss_count=int(row.get("miss_count") or 0),
                    provenance=str(row.get("provenance") or ""),
                    evidence=str(row.get("evidence") or "observation"),  # type: ignore[arg-type]
                )
            except (TypeError, ValueError):
                continue
        self.facts = facts
        lessons = payload.get("lessons")
        self.lessons = [str(item) for item in lessons] if isinstance(lessons, list) else []

    def _contradicts_hud(self, row: GroundedFact, hud: dict[str, Any]) -> bool:
        blob = _norm(row.value)
        if not blob or not hud:
            return False
        for field_name in ("energy", "block", "hp", "floor"):
            val = hud.get(field_name)
            if val is None:
                continue
            match = re.search(rf"\b{field_name}\s*[=:]?\s*(\d+(?:\.\d+)?)", blob)
            if match is None:
                continue
            try:
                claimed = float(match.group(1))
            except ValueError:
                continue
            if abs(claimed - float(val)) > 0.5:
                return True
        return False


def step_error_blob(step: StepLog) -> str:
    parts = [str(step.result.error or "")]
    tx = step.result.card_transaction
    if tx is not None and tx.error:
        parts.append(str(tx.error))
    return " ".join(parts).lower()


def uncertain_error_category(step: StepLog) -> str | None:
    blob = step_error_blob(step)
    for token in UNCERTAIN_ERROR_TOKENS:
        if token in blob:
            return token
    return None


def has_empty_intent(step: StepLog) -> bool:
    schema = str(getattr(step, "decision_schema", "") or "")
    if schema == "v2":
        intent = getattr(step, "intent", {}) or {}
        if not isinstance(intent, dict):
            return True
        return not str(intent.get("kind") or "").strip()
    if step.fixture == "sts2":
        chord = step.proposed or step.action
        return not (
            chord.mark is not None
            or (chord.click_x is not None and chord.click_y is not None)
            or chord.name
        )
    return False


def card_transaction_unverified(step: StepLog) -> bool:
    tx = step.result.card_transaction
    if tx is None:
        return False
    if tx.outcome in {"success", "auto_resolved"}:
        return not (tx.effect_verified or tx.source_verified)
    return tx.outcome is not None


def unresolved_stable_identity(step: StepLog) -> bool:
    metrics = step.metrics or {}
    if int(metrics.get("unresolved_count") or 0) > 0:
        return True
    snapshot = step.turn_snapshot or {}
    for target in snapshot.get("targets") or []:
        if not isinstance(target, dict):
            continue
        conf = target.get("identity_confidence")
        if isinstance(conf, (int, float)) and conf < 0.75:
            return True
    tx = step.result.card_transaction
    if tx is None:
        return False
    return tx.outcome in {
        "source_unconfirmed",
        "target_not_found",
        "effect_unverified",
        "stale",
        "target_rebind_failed",
    }


def export_reject_reason(step: StepLog) -> str | None:
    category = uncertain_error_category(step)
    if category is not None:
        return category
    if has_empty_intent(step):
        return "empty-intent"
    if card_transaction_unverified(step):
        return "unverified-card-transaction"
    if unresolved_stable_identity(step):
        return "unresolved-identity"
    return None


def export_uncertain_reason(step: StepLog) -> str | None:
    """Structured execution uncertainty only (not empty-intent hygiene)."""
    category = uncertain_error_category(step)
    if category is not None:
        return category
    if card_transaction_unverified(step):
        return "unverified-card-transaction"
    if unresolved_stable_identity(step):
        return "unresolved-identity"
    return None


def eligible_as_positive(step: StepLog) -> bool:
    if export_reject_reason(step) is not None:
        return False
    return bool(step.result.executed_as_proposed)


def eligible_as_chosen(step: StepLog, *, reward: float) -> bool:
    if not eligible_as_positive(step):
        return False
    return reward > 0


def eligible_as_rejected(step: StepLog, *, chosen: StepLog, reward: float) -> bool:
    if export_reject_reason(step) is not None:
        return False
    if not step.result.executed_as_proposed and export_reject_reason(chosen) is not None:
        return False
    if reward > 0:
        return False
    return True


def play_relevant_outcome(
    *,
    outcome: str,
    prev_screen: str,
    next_screen: str,
    prev_metrics: dict[str, Any],
    metrics: dict[str, Any],
    label: str,
) -> bool:
    """Promote journal clicks that changed play state, not pure aim relabeling."""
    if outcome not in {"moved", "cleared", "tooltip"}:
        return False
    if prev_screen and next_screen and _norm(prev_screen) != _norm(next_screen):
        return True
    for key in ("energy", "block", "hp", "floor", "screen", "choice"):
        if prev_metrics.get(key) != metrics.get(key):
            return True
    return False
