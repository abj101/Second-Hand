from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass, field


@dataclass
class TaskRecord:
    goal: str
    verdict: str
    evidence: str
    actions: list[str] = field(default_factory=list)
    see: dict | None = None
    previous_task: str = ""
    historical_summary: str = ""
    action_design: str = ""


class EpisodicMemory:
    """GameSense last-task + last-10 narrative. In-session only."""

    def __init__(self, window: int = 10) -> None:
        self.window = window
        self._items: deque[TaskRecord] = deque(maxlen=window)
        self.banned: set[str] = set()
        self.lessons: str = ""

    def add(self, record: TaskRecord) -> None:
        self._items.append(record)

    @property
    def last(self) -> TaskRecord | None:
        return self._items[-1] if self._items else None

    def last_n(self) -> list[TaskRecord]:
        return list(self._items)

    def record_reflections(
        self,
        *,
        previous_task: str = "",
        historical_summary: str = "",
        action_design: str = "",
        see: dict | None = None,
    ) -> None:
        rec = self.last
        if rec is None:
            return
        rec.previous_task = previous_task
        rec.historical_summary = historical_summary
        rec.action_design = action_design
        if see is not None:
            rec.see = see

    def last_task_verdict(self) -> str:
        rec = self.last
        if rec is None:
            return "No prior task."
        return f"Last task '{rec.goal}' → {rec.verdict}. {rec.evidence}"

    def last_task_reflection(self) -> str:
        rec = self.last
        if rec is not None and rec.previous_task.strip():
            return rec.previous_task.strip()
        return self.last_task_verdict()

    def last_ten_summary(self) -> str:
        if not self._items:
            return "No episodic history yet."
        lines = []
        for i, rec in enumerate(self.last_n(), start=1):
            acts = ",".join(rec.actions) or "none"
            lines.append(
                f"{i}. {rec.goal} [{rec.verdict}] actions={acts} — {rec.evidence}"
            )
        return "\n".join(lines)

    def latest_historical_summary(self) -> str:
        for rec in reversed(self.last_n()):
            text = rec.historical_summary.strip()
            if text:
                return text
        return ""

    def last_ten_narrative(self) -> str:
        return self.latest_historical_summary() or self.last_ten_summary()

    def last_see_compact(self) -> str:
        rec = self.last
        if rec is None or not rec.see:
            return ""
        return json.dumps(rec.see, separators=(",", ":"), ensure_ascii=False)

    def prompt_block(self) -> str:
        parts: list[str] = []
        see_json = self.last_see_compact()
        if see_json:
            parts += ["Last See:", see_json, ""]
        parts += [
            "Last-task reflection:",
            self.last_task_reflection(),
            "",
            "Last-10 summary:",
            self.last_ten_narrative(),
        ]
        if self.lessons.strip():
            parts += ["", "Lessons:", self.lessons.strip()]
        if self.banned:
            parts += ["", "Banned actions: " + ", ".join(sorted(self.banned))]
        return "\n".join(parts)
