from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

SkillStatus = Literal["active", "deprecated"]


class Skill(BaseModel):
    id: str
    recognize: str
    avoid_or_do: dict[str, Any] = Field(default_factory=dict)
    end: str = ""
    status: SkillStatus = "active"
    version: int = 1
    n_trials: int = 0
    n_success: int = 0
    n_fail: int = 0
    contribution: float = 0.0
    parent_id: str | None = None
    source_signature: str | None = None

    def recompute(self) -> None:
        if self.n_trials <= 0:
            self.contribution = 0.0
            return
        self.contribution = (self.n_success - self.n_fail) / self.n_trials


class EvidenceRow(BaseModel):
    episode_id: str
    skill_id: str | None = None
    skill_on: bool = False
    success: bool = False
    signature: str = ""
    run_dir: str = ""
    arm: str = "ratchet"


class SkillBank:
    """Typed skill JSON. Writes only from curate. Never deletes deprecated rows."""

    def __init__(
        self,
        path: Path,
        *,
        n_min: int = 20,
        tau: float = 0.10,
        cap: int = 8,
    ) -> None:
        self.path = path
        self.n_min = n_min
        self.tau = tau
        self.cap = cap
        self.evidence_path = path.with_name("skills_evidence.jsonl")
        self.skills: dict[str, Skill] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        data = json.loads(self.path.read_text(encoding="utf-8"))
        rows = data.get("skills") if isinstance(data, dict) else data
        if isinstance(rows, list):
            for row in rows:
                skill = Skill.model_validate(row)
                self.skills[skill.id] = skill

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "n_min": self.n_min,
            "tau": self.tau,
            "cap": self.cap,
            "skills": [s.model_dump() for s in self.skills.values()],
        }
        self.path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def active(self) -> list[Skill]:
        return [s for s in self.skills.values() if s.status == "active"]

    def choose_active(self, rng: random.Random | None = None) -> Skill | None:
        """Uniform among ACTIVE skills so weaker skills still get n_trials."""
        pool = self.active()
        if not pool:
            return None
        pick = rng.choice if rng is not None else random.choice
        return pick(pool)

    def seen_episode_ids(self) -> set[str]:
        seen: set[str] = set()
        if not self.evidence_path.exists():
            return seen
        for line in self.evidence_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            eid = row.get("episode_id")
            if eid:
                seen.add(str(eid))
        return seen

    def append_evidence(self, row: EvidenceRow) -> None:
        self.evidence_path.parent.mkdir(parents=True, exist_ok=True)
        with self.evidence_path.open("a", encoding="utf-8") as fh:
            fh.write(row.model_dump_json() + "\n")

    def record_trial(self, skill_id: str, *, success: bool) -> Skill | None:
        skill = self.skills.get(skill_id)
        if skill is None:
            return None
        skill.n_trials += 1
        if success:
            skill.n_success += 1
        else:
            skill.n_fail += 1
        skill.recompute()
        return skill

    def would_retire(self, skill: Skill) -> bool:
        return skill.status == "active" and skill.n_trials >= self.n_min and skill.contribution <= -self.tau

    def retire_due(self) -> list[Skill]:
        retired: list[Skill] = []
        for skill in list(self.skills.values()):
            if self.would_retire(skill):
                skill.status = "deprecated"
                retired.append(skill)
        return retired

    def add_skill(self, skill: Skill) -> Skill | None:
        """Insert an ACTIVE skill, respecting cap C. Never deletes."""
        if len(self.active()) >= self.cap:
            victim = self._evict_candidate()
            if victim is None:
                return None
            victim.status = "deprecated"
        self.skills[skill.id] = skill
        return skill

    def _evict_candidate(self) -> Skill | None:
        eligible = [s for s in self.active() if s.n_trials >= self.n_min]
        if not eligible:
            return None
        eligible.sort(key=lambda s: (s.contribution, s.n_trials, s.id))
        return eligible[0]

    def signatures_owned(self) -> set[str]:
        return {s.source_signature for s in self.skills.values() if s.source_signature}


def contribution(n_success: int, n_fail: int, n_trials: int) -> float:
    if n_trials <= 0:
        return 0.0
    return (n_success - n_fail) / n_trials
