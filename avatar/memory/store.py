from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Any, Callable

from avatar.memory.condense import maybe_condense
from avatar.memory.episodic import EpisodicMemory, TaskRecord
from avatar.memory.procedural import ProceduralMemory
from avatar.schema import TurnOutcomeRecord

CHAR_BUDGET = 3200  # ~800 tokens
NOOP_BAN_THRESHOLD = 2
REPEAT_STREAK = 3


class AgentMemory:
    """L2 facade: working bans + episodic last-10 + persisted procedural stats."""

    def __init__(
        self,
        fixture: str,
        *,
        root: Path | None = None,
        window: int = 10,
        condense_every: int = 5,
    ) -> None:
        self.fixture = fixture
        self.episodic = EpisodicMemory(window=window)
        self.procedural = ProceduralMemory(fixture, root=root)
        self.condense_every = condense_every
        self._turns = 0
        self._last_state_key = ""
        self._noop_counts: dict[tuple[str, str], int] = {}
        self._recent: deque[tuple[tuple[str, ...], str]] = deque(maxlen=REPEAT_STREAK)
        self._lessons_stale = True

    @property
    def banned(self) -> set[str]:
        return self.episodic.banned

    @property
    def lessons(self) -> str:
        return self.episodic.lessons

    def observe(self, record: TurnOutcomeRecord) -> TurnOutcomeRecord:
        incoming_key = record.state_key or ""
        world_moved = record.verdict == "success" or bool(record.pixel_changed)
        if world_moved:
            self.episodic.banned.clear()
            self._noop_counts.clear()
            if incoming_key:
                self._last_state_key = incoming_key
        elif incoming_key and not self._last_state_key:
            self._last_state_key = incoming_key
        # See-only flicker must not reset bans or noop counts.
        state_key = self._last_state_key or incoming_key

        actions = [name for name in record.actions if name]
        self.episodic.add(
            TaskRecord(
                goal=record.goal,
                verdict=record.verdict,
                evidence=record.evidence,
                actions=list(actions),
            )
        )
        for name in actions or ["(none)"]:
            self.procedural.record(name, record.verdict, record.evidence)

        if record.verdict == "noop":
            for name in actions:
                key = (name, state_key)
                self._noop_counts[key] = self._noop_counts.get(key, 0) + 1
                if self._noop_counts[key] >= NOOP_BAN_THRESHOLD:
                    self.episodic.banned.add(name)

        act_tuple = tuple(actions)
        self._recent.append((act_tuple, record.verdict))
        if len(self._recent) == REPEAT_STREAK:
            lists = [item[0] for item in self._recent]
            verdicts = [item[1] for item in self._recent]
            if (
                lists[0]
                and lists[0] == lists[1] == lists[2]
                and all(verdict in {"noop", "failure", "fail"} for verdict in verdicts)
            ):
                self.episodic.banned.update(lists[0])

        self._turns += 1
        self._lessons_stale = True
        record.banned = sorted(self.episodic.banned)
        return record

    def record_reflections(
        self,
        *,
        previous_task: str = "",
        historical_summary: str = "",
        action_design: str = "",
        see: dict | None = None,
    ) -> None:
        self.episodic.record_reflections(
            previous_task=previous_task,
            historical_summary=historical_summary,
            action_design=action_design,
            see=see,
        )

    def retrieve_procedural(self, names: list[str]) -> str:
        return self.procedural.retrieve(names)

    def prompt_block(self) -> str:
        parts = [self.episodic.prompt_block(), "", self.procedural.prompt_block()]
        text = "\n".join(parts).strip()
        if len(text) <= CHAR_BUDGET:
            return text
        rec = self.episodic.last
        head: list[str] = []
        see_json = self.episodic.last_see_compact()
        if see_json:
            head += ["Last See:", see_json, ""]
        head += ["Last-task reflection:", self.episodic.last_task_reflection()]
        historical = self.episodic.latest_historical_summary()
        if historical:
            head += ["", "Last-10 summary:", historical]
        if self.episodic.lessons.strip():
            head += ["", "Lessons:", self.episodic.lessons.strip()]
        if self.episodic.banned:
            head += ["", "Banned actions: " + ", ".join(sorted(self.episodic.banned))]
        names = list(rec.actions) if rec is not None else []
        retrieved = self.procedural.retrieve(names) if names else ""
        head += ["", retrieved or self.procedural.prompt_block()]
        compact = "\n".join(head).strip()
        if len(compact) <= CHAR_BUDGET:
            return compact
        return compact[: CHAR_BUDGET - 1] + "…"

    def maybe_condense(self, complete_json: Callable[..., Any] | None = None) -> str | None:
        empty = not self.episodic.lessons.strip()
        due = self._turns > 0 and self._turns % self.condense_every == 0 and self._lessons_stale
        over_budget = len(self.prompt_block()) > CHAR_BUDGET
        if not (empty or due or over_budget):
            return None
        result = maybe_condense(
            self.episodic,
            complete_json,
            min_items=5,
            stale=empty or self._lessons_stale,
        )
        if result:
            self._lessons_stale = False
        return result
