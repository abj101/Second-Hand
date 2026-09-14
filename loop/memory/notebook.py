from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from loop.labels import bind_verb_ok
from loop.memory.graph import pick_focus
from loop.memory.grounded import GroundedStore, ephemeral_fact_token, fact_key, play_relevant_outcome
from loop.memory.journal import FactJournal, _bind_rank, _clip, _norm

MAX_LINES = 12
MISSION = "win a run"
_FOCUS_NOOP = 3
_GOAL_HINT_OK: dict[str, frozenset[str]] = {
    "combat": frozenset({"combat"}),
    "rest": frozenset({"rest", "map", "event"}),
    "shop": frozenset({"shop"}),
    "title": frozenset({"title"}),
    "select": frozenset({"select"}),
    "tutorial": frozenset({"tutorial"}),
    "leave": frozenset({"pause", "shop", "reward", "event", "other"}),
    "map": frozenset({"map", "event", "other"}),
    "hover": frozenset(),
}
_EXAMPLE_NOTES = frozenset(
    {
        "mk_1 is singleplayer on title",
        "mk_6 is end turn on combat",
    }
)
_MK_IS = re.compile(r"^mk_(\d+)\s+is\s+(.+?)(?:\s+on\s+\w+)?$", re.I)
_KNOWN_OK = frozenset({"moved", "cleared", "tooltip"})
_LOCKED_FLAVOR = frozenset({"play", "unlock", "single"})
_UNLOCK_GOAL = re.compile(r"\bunlock\b", re.I)
_DIALOG_LABELS = frozenset({"yes", "no", "got it"})
_OVERLAY_BIND = {
    "yes": "Yes",
    "no": "No",
    "got it": "Got it",
    "confirm": "Confirm",
    "continue": "Continue",
    "resume": "Resume",
    "singleplayer": "Singleplayer",
    "end turn": "End Turn",
    "close": "Close",
    "back": "Back",
    "map": "Map",
}
_OVERLAY_WHERE = {
    "yes": "dialog",
    "no": "dialog",
    "got it": "dialog",
    "singleplayer": "title",
    "confirm": "select",
    "end turn": "combat",
    "close": "overlay",
    "back": "menu",
    "map": "hud",
}
_SKIP_LISTING_LABELS = frozenset({"icon", "locked", "character", "button", "buttons", "menu"})
_HALLUCINATED_OVERLAY_GOAL = re.compile(
    r"\b(got it|dismiss overlay|dismiss popup|dismiss tutorial)\b",
    re.I,
)
_STALE_TUTORIAL_NOTE = re.compile(
    r"\b(tutorial\s+(phase\s+)?complete|tutorial\s+finished|tutorial\s+done|phase\s+\d+\s+done)\b",
    re.I,
)
_HUD_DUMP = re.compile(
    r"\b(?:hp|energy|block|floor|screen|gold)\s*[=:]?\s*\d",
    re.I,
)
_VERSIONISH = re.compile(r"\b(?:v\d|build|beta|patch|0\.\d+\.\d+)\b", re.I)
_MOSTLY_NUMERIC = re.compile(r"^[\d\s/:=\-+.,]+$")


def _reject_stale_tutorial_note(note: str, screen: str | None) -> bool:
    if (screen or "").strip().lower() != "tutorial":
        return False
    return bool(_STALE_TUTORIAL_NOTE.search(note or ""))


def lesson_is_sane(text: str) -> bool:
    blob = " ".join((text or "").split())
    if not blob or len(blob) < 8:
        return False
    if len(blob) > 120:
        return False
    if _MOSTLY_NUMERIC.match(blob):
        return False
    if _HUD_DUMP.search(blob):
        return False
    if _VERSIONISH.search(blob):
        return False
    alpha = sum(ch.isalpha() for ch in blob)
    if alpha < max(8, len(blob) // 4):
        return False
    return True


def note_is_raw_hud_dump(note: str) -> bool:
    return not lesson_is_sane(note) and bool(_HUD_DUMP.search(note or ""))


def default_goal(screen: str | None = None) -> str:
    del screen
    return MISSION


def overlay_hint(aim_listing: str | None) -> str | None:
    return overlay_default_goal(aim_listing)


def note_copies_thought(note: str, thought: str) -> bool:
    left = _norm(note)
    right = _norm(thought)
    return bool(left) and left == right


def note_is_schema_example(note: str) -> bool:
    return _norm(note) in _EXAMPLE_NOTES


def note_is_hover_fluff(note: str) -> bool:
    blob = _norm(note)
    return "tooltip" in blob or blob.endswith(" icon")


def note_disagrees_aim(note: str, aim_listing: str | None) -> bool:
    listing = (aim_listing or "").strip()
    if not listing:
        return False
    match = _MK_IS.match(" ".join((note or "").split()))
    if match is None:
        return False
    mark_id = match.group(1)
    claimed = _label_tokens(match.group(2))
    actual = _label_tokens(_aim_label_for(listing, mark_id))
    if not claimed or not actual:
        return False
    if actual <= _SKIP_LISTING_LABELS and claimed != actual:
        return True
    return not claimed <= actual


def _label_tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z]+", _norm(text)))


def _aim_label_for(listing: str, mark_id: str) -> str:
    for part in listing.split():
        if ":" not in part:
            continue
        head, label = part.split(":", 1)
        if head.split("@", 1)[0] == mark_id:
            return _norm(label.replace("_", " "))
    return ""


def listing_labels(aim_listing: str | None) -> set[str]:
    labels: set[str] = set()
    for part in (aim_listing or "").split():
        if ":" not in part:
            continue
        labels.add(part.split(":", 1)[1].replace("_", " ").lower())
    return labels


def listing_has_dialog(aim_listing: str | None) -> bool:
    return bool(listing_labels(aim_listing) & _DIALOG_LABELS)


def listing_has_close(aim_listing: str | None) -> bool:
    return "close" in listing_labels(aim_listing)


def listing_backdrop_close(aim_listing: str | None) -> bool:
    """Click-the-dim-backdrop only when :Close is the sole mark — not beside arrows."""
    parts = [part for part in (aim_listing or "").split() if part]
    return len(parts) == 1 and "close" in listing_labels(aim_listing)


def listing_has_named(aim_listing: str | None) -> bool:
    return bool(listing_labels(aim_listing) - _SKIP_LISTING_LABELS)


def listing_has_locked(aim_listing: str | None) -> bool:
    return ":locked" in (aim_listing or "").lower()


def overlay_screen(screen: str | None, aim_listing: str | None) -> str:
    """Scope Avoid/Known to tutorial when Marks prints Yes/No/Got it and HUD says other."""
    key = (screen or "").strip().lower()
    labels = listing_labels(aim_listing)
    if labels & _DIALOG_LABELS and key in {"", "other"}:
        return "tutorial"
    if "end turn" in labels and key in {"", "other"}:
        return "combat"
    return key


def overlay_default_goal(aim_listing: str | None) -> str | None:
    labels = listing_labels(aim_listing)
    if labels & {"yes", "no"}:
        return "click a printed :Yes or :No"
    if "got it" in labels:
        return "click :Got it"
    if listing_backdrop_close(aim_listing):
        return "click :Close on the darkened backdrop"
    return None


def goal_screen_hint(goal: str) -> str | None:
    """Named-screen intent in a goal string, or None if the goal is portable."""
    blob = _norm(goal)
    if not blob:
        return None
    if any(
        token in blob
        for token in ("tooltip", "hover", "card details", "view card")
    ):
        return "hover"
    if blob in {"explore", "move forward"}:
        return "map"
    if any(token in blob for token in ("got it", "yes or no")) or ":yes" in blob or ":no" in blob:
        return "tutorial"
    if ":close" in blob or "darkened backdrop" in blob:
        return "tutorial"
    if any(
        token in blob
        for token in (
            "end turn",
            "spend energy",
            "play a card",
            "play card",
            "select card",
            "strike",
            "defend",
            "enemy",
            "intent",
        )
    ):
        return "combat"
    if any(token in blob for token in ("rest", "campfire", "smith")):
        return "rest"
    if any(token in blob for token in ("merchant", "shop", "buy", "purge")):
        return "shop"
    if any(token in blob for token in ("singleplayer", "start a run", "save profile")):
        return "title"
    if any(
        token in blob
        for token in ("portrait", "embark", "ironclad", "character", "unlocked")
    ):
        return "select"
    if any(
        token in blob
        for token in ("return to map", "go back to map", "leave combat")
    ):
        return "leave"
    return None


def goal_fits_screen(
    goal: str,
    screen: str | None,
    aim_listing: str | None = None,
) -> bool:
    overlay = overlay_default_goal(aim_listing)
    if overlay:
        if goal_screen_hint(goal) == "hover":
            return False
        return _norm(goal) == _norm(overlay) or (
            not _reject_overlay_goal(goal, aim_listing)
            and goal_screen_hint(goal) in {None, "tutorial"}
        )
    hint = goal_screen_hint(goal)
    if hint == "hover":
        return False
    if hint is None:
        return True
    key = (screen or "").strip().lower()
    if not key:
        return True
    allowed = _GOAL_HINT_OK.get(hint)
    if allowed is None:
        return True
    return key in allowed


def resolve_goal(
    stored: str,
    *,
    screen: str | None,
    aim_listing: str | None = None,
) -> str:
    del stored, screen, aim_listing
    return MISSION


def overlay_bind_note(
    aim_listing: str | None,
    mark_id: int | None,
    label: str = "",
) -> str | None:
    """Host note `mk_N is Yes on dialog` from the executed badge, not VLM paraphrase."""
    if mark_id is None:
        return None
    printed = _aim_label_for(aim_listing or "", str(int(mark_id))) if aim_listing else ""
    compact = printed or _norm(label)
    pretty = _OVERLAY_BIND.get(compact)
    if pretty is None:
        return None
    where = _OVERLAY_WHERE.get(compact, "dialog" if listing_has_dialog(aim_listing) else "menu")
    note = f"mk_{int(mark_id)} is {pretty} on {where}"
    if aim_listing and note_disagrees_aim(note, aim_listing):
        return None
    return note


def _reject_overlay_goal(goal: str, aim_listing: str | None) -> bool:
    labels = listing_labels(aim_listing)
    if "got it" in labels or not (labels & {"yes", "no"}):
        return False
    return bool(_HALLUCINATED_OVERLAY_GOAL.search(goal or ""))


def _reject_overlay_hallucination(note: str, aim_listing: str | None) -> bool:
    labels = listing_labels(aim_listing)
    printed = labels & _DIALOG_LABELS
    if not printed or "got it" in printed:
        return False
    return "got it" in _norm(note)


def _reject_unlock_goal(goal: str, *, screen: str | None, aim_listing: str | None) -> bool:
    if not _UNLOCK_GOAL.search(goal or ""):
        return False
    key = (screen or "").strip().lower()
    return key == "select" or listing_has_locked(aim_listing)


def _reject_locked_flavor_note(note: str, aim_listing: str | None) -> bool:
    if not listing_has_locked(aim_listing):
        return False
    match = _MK_IS.match(" ".join((note or "").split()))
    if match is None:
        return False
    claimed = _norm(match.group(2))
    words = set(re.findall(r"[a-z]+", claimed))
    return bool(words & _LOCKED_FLAVOR)


def avoid_devices(journal: FactJournal, screen: str | None = None) -> frozenset[str]:
    """Devices on the Avoid line: blocked bindings with n>=2, screen-scoped."""
    hits: set[str] = set()
    ranked = sorted(journal.bindings.values(), key=lambda row: (-row.n, row.device or ""))
    for row in ranked:
        if row.outcome != "blocked" or row.n < 2:
            continue
        if not _kind_ok(row.kind, screen):
            continue
        token = row.device or row.glyph
        if token:
            hits.add(token)
    if hits:
        return frozenset(hits)
    blocked = [
        fact
        for fact in journal.facts.values()
        if fact.kind != "note"
        and fact.outcome == "blocked"
        and (fact.device or fact.glyph)
        and _kind_ok(fact.kind, screen)
    ]
    blocked.sort(key=lambda fact: -fact.step_id)
    for fact in blocked:
        token = fact.device or fact.glyph or ""
        if token:
            hits.add(token)
        if len(hits) >= 2:
            break
    return frozenset(hits)


def _kind_ok(row_kind: str, screen: str | None) -> bool:
    want = (screen or "").strip().lower()
    if not want:
        return True
    got = (row_kind or "").strip().lower()
    if not got:
        return False
    return got == want


def _pivot_line(blocked_hint: str) -> str:
    text = " ".join((blocked_hint or "").split())
    if not text:
        return ""
    if text.upper().startswith("BLOCKED:"):
        text = text[len("BLOCKED:") :].strip()
        text = text.replace("pick a different gx,gy", "different mark or gx,gy")
        return f"PIVOT: last {text}"
    if text.upper().startswith("PIVOT:"):
        return text
    return f"PIVOT: {text}"


def _last_line(journal: FactJournal) -> str:
    facts = [fact for fact in journal.facts.values() if fact.kind != "note"]
    if not facts:
        return ""
    fact = max(facts, key=lambda row: (row.step_id, row.key))
    device = fact.device or fact.glyph or "?"
    label = _clip(fact.prompt, 32) if fact.prompt else ""
    if label:
        return f'{device} "{label}" → {fact.outcome}'
    return f"{device} → {fact.outcome}"


def _avoid_line(journal: FactJournal, screen: str | None = None) -> str:
    hits: list[str] = []
    seen: set[str] = set()
    ranked = sorted(journal.bindings.values(), key=lambda row: (-row.n, row.device))
    for row in ranked:
        if row.outcome != "blocked" or row.n < 2:
            continue
        if not _kind_ok(row.kind, screen):
            continue
        token = row.device or row.glyph
        if not token or token in seen:
            continue
        seen.add(token)
        hits.append(f"{token}×{row.n}")
        if len(hits) >= 3:
            break
    if hits:
        return ", ".join(hits)
    blocked = [
        fact
        for fact in journal.facts.values()
        if fact.kind != "note"
        and fact.outcome == "blocked"
        and (fact.device or fact.glyph)
        and _kind_ok(fact.kind, screen)
    ]
    blocked.sort(key=lambda fact: -fact.step_id)
    for fact in blocked:
        token = fact.device or fact.glyph or ""
        if not token or token in seen:
            continue
        seen.add(token)
        hits.append(token)
        if len(hits) >= 2:
            break
    return ", ".join(hits)


def _known_line(journal: FactJournal, screen: str | None = None) -> str:
    rows = [
        row
        for row in journal.bindings.values()
        if row.outcome in _KNOWN_OK
        and _kind_ok(row.kind, screen)
        and bind_verb_ok(row.verb)
    ]
    rows = sorted(rows, key=_bind_rank)[:2]
    bits: list[str] = []
    for row in rows:
        verb = _clip(row.verb, 24) if row.verb else ""
        glyph = row.glyph or row.device
        if verb:
            bits.append(f'{glyph} "{verb}" → {row.outcome}')
        else:
            bits.append(f"{glyph} → {row.outcome}")
    return "; ".join(bits)


class Notebook:
    """Host scratchpad: standing mission, ephemeral focus, sensor last/known/avoid."""

    def __init__(
        self,
        journal: FactJournal,
        *,
        memory_scope: str = "run",
    ) -> None:
        self.journal = journal
        self.grounded = GroundedStore()
        self.memory_scope = memory_scope if memory_scope in {"run", "episode"} else "run"
        self.focus = ""
        self._focus_noops = 0
        self._pivot_focus = False
        self._ensure_mission()

    def reset_episode(self) -> dict[str, Any]:
        """Clear episode-scoped fields. Journal bindings are run-scoped within a
        collect run; when memory_scope=='episode', also reset journal notes/bindings.
        Run-scoped grounded Known/Lessons survive."""
        carried = {
            "goal": self.journal.goal,
            "grounded_run": sum(1 for row in self.grounded.facts.values() if row.scope == "run"),
            "grounded_episode": sum(1 for row in self.grounded.facts.values() if row.scope == "episode"),
            "lessons": len(self.grounded.lessons),
        }
        self.focus = ""
        self._focus_noops = 0
        self._pivot_focus = False
        self.grounded.clear_episode()
        if self.memory_scope == "episode":
            self.journal.reset_episode_scope()
        return carried

    def decay_grounded(
        self,
        *,
        screen: str | None = None,
        energy: float | int | None = None,
        aim_listing: str | None = None,
    ) -> None:
        """Age grounded facts once per step (call from loop before prompt render)."""
        screen_key = overlay_screen(screen, aim_listing) or "other"
        hud = {"energy": energy} if energy is not None else None
        self.grounded.decay_stale_facts(screen=screen_key, hud=hud)

    def _ensure_mission(self) -> None:
        if self.journal.goal == MISSION:
            return
        self.journal.goal = MISSION
        if self.journal.path is not None:
            try:
                self.journal.save()
            except OSError:
                return

    def set_goal(self, goal: str) -> None:
        blob = " ".join((goal or "").split())
        if _norm(blob) != _norm(MISSION):
            self._ensure_mission()
            return
        self.journal.goal = MISSION
        if self.journal.path is not None:
            try:
                self.journal.save()
            except OSError:
                return

    def ingest(
        self,
        parsed: dict[str, Any],
        *,
        thought: str = "",
        step_id: int = 0,
        screen: str | None = None,
        aim_listing: str | None = None,
    ) -> str | None:
        """Save notes from planner JSON. JSON goal does not replace the mission."""
        self._ensure_mission()
        note = str(parsed.get("note") or "").strip()
        if not note or note_copies_thought(note, thought):
            return None
        if (
            note_is_schema_example(note)
            or note_is_hover_fluff(note)
            or note_disagrees_aim(note, aim_listing)
            or _reject_locked_flavor_note(note, aim_listing)
            or _reject_overlay_hallucination(note, aim_listing)
            or _reject_stale_tutorial_note(note, screen)
            or note_is_raw_hud_dump(note)
        ):
            return None
        scope = "episode" if self.memory_scope == "episode" else "run"
        self.grounded.add_vlm_note(note, screen=screen, step_id=step_id, scope=scope)
        saved = self.journal.remember_note(note, step_id=step_id)
        return note if saved is not None else None

    def promote_click(
        self,
        *,
        screen: str,
        label: str,
        device: str,
        outcome: str,
        prev_screen: str,
        next_screen: str,
        prev_metrics: dict[str, Any],
        metrics: dict[str, Any],
        step_id: int = 0,
        object_id: str = "",
    ) -> str | None:
        if ephemeral_fact_token(label) or ephemeral_fact_token(object_id):
            return None
        if not play_relevant_outcome(
            outcome=outcome,
            prev_screen=prev_screen,
            next_screen=next_screen,
            prev_metrics=prev_metrics,
            metrics=metrics,
            label=label,
        ):
            return None
        value = f"{device} {_clip(label, 32)} → {outcome}" if label else f"{device} → {outcome}"
        scope = "run" if self.memory_scope == "run" else "episode"
        provenance = "|".join(part for part in (prev_screen, object_id or label) if part)
        row = self.grounded.promote_outcome(
            screen=screen,
            value=value,
            step_id=step_id,
            scope=scope,
            label=label,
            provenance=provenance or "verified-click",
            evidence="transition",
        )
        return row.line() if row is not None else None

    def promote_turn_reward(
        self,
        *,
        screen: str,
        label: str,
        device: str,
        delta: float,
        prev_metrics: dict[str, Any],
        metrics: dict[str, Any],
        step_id: int = 0,
    ) -> str | None:
        combat_won = bool(metrics.get("combat_won"))
        if delta <= 0 and not combat_won:
            return None
        prev_screen = str(prev_metrics.get("screen") or "")
        next_screen = str(metrics.get("screen") or "")
        if combat_won:
            transition_id = str(metrics.get("combat_won_transition_id") or f"{step_id}")
            key_label = f"combat_won:{transition_id}"
            value = (
                f"{device} {_clip(label, 32)} → combat→{next_screen or 'reward'}"
                if label
                else f"{device} → combat→{next_screen or 'reward'}"
            )
            scope = "run" if self.memory_scope == "run" else "episode"
            stable_key = fact_key(screen=screen, label=key_label)
            if stable_key in self.grounded.facts:
                return None
            row = self.grounded.promote_outcome(
                screen=screen,
                value=value,
                step_id=step_id,
                scope=scope,
                label=key_label,
                provenance="combat_won",
                evidence="combat_won",
                confidence=1.0,
            )
            return row.line() if row is not None else None
        verified = play_relevant_outcome(
            outcome="moved",
            prev_screen=prev_screen,
            next_screen=next_screen,
            prev_metrics=prev_metrics,
            metrics=metrics,
            label=label,
        )
        if not verified:
            return None
        bits: list[str] = []
        if prev_screen and next_screen and prev_screen != next_screen:
            bits.append(f"{prev_screen}→{next_screen}")
        choice = str(metrics.get("choice") or "").strip()
        if choice:
            bits.append(choice)
        outcome = " → ".join(bits) if bits else "progress"
        value = f"{device} {_clip(label, 32)} → {outcome}" if label else f"{device} → {outcome}"
        scope = "run" if self.memory_scope == "run" else "episode"
        row = self.grounded.promote_reward(
            screen=screen,
            value=value,
            step_id=step_id,
            scope=scope,
            label=label,
            provenance="turn-reward",
            evidence="transaction",
        )
        return row.line() if row is not None else None

    def write_lesson(self, text: str) -> None:
        if not lesson_is_sane(text):
            return
        self.grounded.set_lesson(text)

    def observe_step(
        self,
        *,
        screen: str | None,
        prev_screen: str | None,
        reward: float = 0.0,
        pixel_changed: bool = False,
        new_fact: bool = False,
        listing: str | None = None,
        hovered: set[int] | frozenset[int] | None = None,
    ) -> None:
        """Complete Focus on progress; PIVOT after repeated no-ops."""
        del hovered, listing
        prev = (prev_screen or "").strip().lower()
        curr = (screen or "").strip().lower()
        if self.focus and (
            (prev and curr and prev != curr) or reward > 0 or new_fact
        ):
            self.focus = ""
            self._focus_noops = 0
            self._pivot_focus = False
            return
        if self.focus and not pixel_changed:
            self._focus_noops += 1
            if self._focus_noops >= _FOCUS_NOOP:
                self._pivot_focus = True
                self.focus = ""
                self._focus_noops = 0
            return
        if pixel_changed:
            self._focus_noops = 0

    def bind_overlay(
        self,
        *,
        mark_id: int | None,
        label: str = "",
        listing: str | None = None,
        step_id: int = 0,
        screen: str | None = None,
    ) -> str | None:
        """Sensor-write the executed badge name so Known/notes keep Yes/No, not Got it."""
        note = overlay_bind_note(listing, mark_id, label)
        if not note:
            return None
        compact = _norm(note)
        for fact in self.journal.facts.values():
            if fact.kind == "note" and _norm(fact.prompt) == compact:
                return None
        saved = self.journal.remember_note(note, step_id=step_id)
        if saved is None:
            return None
        screen_key = overlay_screen(screen, listing) or "other"
        scope = "run" if self.memory_scope == "run" else "episode"
        self.grounded.promote_outcome(
            screen=screen_key,
            value=note,
            step_id=step_id,
            scope=scope,
            label=label,
        )
        return note

    def prompt_block(
        self,
        *,
        screen: str | None = None,
        blocked_hint: str | None = None,
        limit: int = MAX_LINES,
        aim_listing: str | None = None,
        hovered: set[int] | frozenset[int] | None = None,
        energy: float | int | None = None,
        selected: str | None = None,
    ) -> str:
        cap = max(2, min(limit, MAX_LINES))
        self._ensure_mission()
        screen_key = overlay_screen(screen, aim_listing) or "other"
        if not self.focus:
            self.focus = pick_focus(
                self.journal.graph,
                screen=screen_key,
                aim_listing=aim_listing,
                hovered=hovered,
                energy=energy,
                selected=selected,
            )
        lines = ["Notebook:", f"Goal: {MISSION}"]
        if self.focus and len(lines) < cap:
            lines.append(f"Focus: {self.focus}")
        hint = overlay_hint(aim_listing)
        if hint and len(lines) < cap:
            lines.append(f"Hint: {hint}")
        if self._pivot_focus:
            pivot = "PIVOT: same move no-op, try another play action"
            self._pivot_focus = False
        else:
            pivot = _pivot_line(blocked_hint or "")
        if pivot and len(lines) < cap:
            lines.append(pivot)
        knowledge = self.journal.graph.slice(screen_key, aim_listing, cap=6)
        for row in knowledge:
            if len(lines) >= cap:
                break
            lines.append(row)
        last = _last_line(self.journal)
        if last and len(lines) < cap:
            lines.append(f"Last: {last}")
        avoid = _avoid_line(self.journal, screen)
        if avoid and len(lines) < cap:
            lines.append(f"Avoid: {avoid}")
        known = _known_line(self.journal, screen)
        grounded_known, grounded_hunch = self.grounded.rank_for_prompt(
            screen=screen_key,
            hud={"energy": energy} if energy is not None else None,
        )
        if grounded_known and len(lines) < cap:
            bits = "; ".join(row.line() for row in grounded_known[:6])
            lines.append(f"Known: {bits}")
        elif known and len(lines) < cap:
            lines.append(f"Known: {known}")
        if grounded_hunch and len(lines) < cap:
            bits = "; ".join(row.line() for row in grounded_hunch)
            lines.append(f"Hunch: {bits}")
        if self.grounded.lessons and len(lines) < cap:
            lines.append(f"Lessons: {self.grounded.lessons[-1]}")
        return "\n".join(lines[:cap])

    def memory_snapshot(
        self,
        *,
        screen: str | None = None,
        aim_listing: str | None = None,
        energy: float | int | None = None,
        hud: dict[str, Any] | None = None,
    ) -> str:
        """Stable text for DPO state matching (same screen scope as prompt_block)."""
        screen_key = overlay_screen(screen, aim_listing) or "other"
        if hud is None and energy is not None:
            hud = {"energy": energy}
        parts: list[str] = []
        known, hunch = self.grounded.rank_for_prompt(screen=screen_key, hud=hud, cap=12)
        if known:
            parts.append("known=" + "|".join(sorted(row.key for row in known)))
        if hunch:
            parts.append("hunch=" + "|".join(sorted(row.key for row in hunch)))
        if self.grounded.lessons:
            parts.append("lesson=" + self.grounded.lessons[-1])
        return ";".join(parts)

    def save_grounded(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.grounded.snapshot(), indent=2), encoding="utf-8")

    def load_grounded(self, path: Path) -> None:
        if not path.exists():
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return
        if isinstance(payload, dict):
            self.grounded.load_snapshot(payload)
