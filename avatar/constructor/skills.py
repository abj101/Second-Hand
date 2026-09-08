from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

Outcome = Literal["merged", "spawned", "rejected", "rewritten"]


@dataclass
class Skill:
    name: str
    recognize: str
    do: list[str]
    end: str
    traces: list[str] = field(default_factory=list)
    rewrites: int = 0
    enabled: bool = False
    invoke_count: int = 0

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict) -> "Skill":
        return cls(
            name=data["name"],
            recognize=data["recognize"],
            do=list(data.get("do") or []),
            end=data.get("end") or "",
            traces=list(data.get("traces") or []),
            rewrites=int(data.get("rewrites") or 0),
            enabled=bool(data.get("enabled")),
            invoke_count=int(data.get("invoke_count") or 0),
        )


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


class SkillLibrary:
    """Bounded constructor library. Merge-not-spawn. Cap 3 rewrites. Human enable."""

    def __init__(self, *, max_rewrites: int = 3, soft_cap: int = 8) -> None:
        self.max_rewrites = max_rewrites
        self.soft_cap = soft_cap
        self.skills: dict[str, Skill] = {}
        self.log: list[dict] = []

    def get(self, name: str) -> Skill | None:
        return self.skills.get(name) or self.skills.get(_norm(name).replace(" ", "_"))

    def record_invoke(self, name: str) -> None:
        skill = self.get(name)
        if skill:
            skill.invoke_count += 1

    def propose(self, candidate: Skill) -> Outcome:
        match = self._best_match(candidate)
        if match is not None:
            match.do = candidate.do or match.do
            match.end = candidate.end or match.end
            match.recognize = candidate.recognize or match.recognize
            match.traces.extend(candidate.traces)
            self.log.append({"outcome": "merged", "into": match.name, "from": candidate.name})
            return "merged"
        if len(self.skills) >= self.soft_cap:
            self.log.append({"outcome": "rejected", "reason": "soft_cap", "name": candidate.name})
            return "rejected"
        key = candidate.name
        self.skills[key] = candidate
        self.log.append({"outcome": "spawned", "name": key})
        return "spawned"

    def rewrite(self, name: str, candidate: Skill) -> Outcome:
        skill = self.get(name)
        if skill is None:
            return self.propose(candidate)
        if skill.rewrites >= self.max_rewrites:
            self.log.append({"outcome": "rejected", "reason": "rewrite_cap", "name": name})
            return "rejected"
        skill.recognize = candidate.recognize or skill.recognize
        skill.do = candidate.do or skill.do
        skill.end = candidate.end or skill.end
        skill.rewrites += 1
        skill.traces.extend(candidate.traces)
        self.log.append({"outcome": "rewritten", "name": name, "rewrites": skill.rewrites})
        return "rewritten"

    def prompt_block(self) -> str:
        enabled = [s for s in self.skills.values() if s.enabled]
        if not enabled:
            return ""
        lines = []
        for skill in enabled:
            do = ", ".join(skill.do) or "(empty)"
            lines.append(f"- {skill.name}: {skill.recognize} → {do}")
        return "\n".join(lines)

    def enable(self, name: str, enabled: bool = True) -> None:
        skill = self.get(name)
        if skill is None:
            raise KeyError(name)
        skill.enabled = enabled

    def stats(self) -> dict:
        spawned = sum(1 for e in self.log if e["outcome"] == "spawned")
        merged = sum(1 for e in self.log if e["outcome"] == "merged")
        rewritten = sum(1 for e in self.log if e["outcome"] == "rewritten")
        rejected = sum(1 for e in self.log if e["outcome"] == "rejected")
        invokes = sum(s.invoke_count for s in self.skills.values())
        spawned_plus = max(spawned + merged, 1)
        return {
            "skill_count": len(self.skills),
            "spawned": spawned,
            "merged": merged,
            "rewritten": rewritten,
            "rejected": rejected,
            "invokes": invokes,
            "reuse_rate": merged / spawned_plus,
            "duplicate_spawn_ratio": spawned / spawned_plus,
        }

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "skills": [s.to_json() for s in self.skills.values()],
            "log": self.log,
            "max_rewrites": self.max_rewrites,
            "soft_cap": self.soft_cap,
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "SkillLibrary":
        data = json.loads(path.read_text(encoding="utf-8"))
        lib = cls(
            max_rewrites=int(data.get("max_rewrites") or 3),
            soft_cap=int(data.get("soft_cap") or 8),
        )
        for item in data.get("skills") or []:
            skill = Skill.from_json(item)
            lib.skills[skill.name] = skill
        lib.log = list(data.get("log") or [])
        return lib

    def _best_match(self, candidate: Skill) -> Skill | None:
        cname = _norm(candidate.name)
        crec = _norm(candidate.recognize)
        for skill in self.skills.values():
            if _norm(skill.name) == cname:
                return skill
            if crec and _norm(skill.recognize) == crec:
                return skill
            if crec and crec in _norm(skill.recognize):
                return skill
        return None


def default_2048_skills() -> SkillLibrary:
    """Enabled corner-pack skills the L2 planner can invoke by name."""
    lib = SkillLibrary()
    for skill in (
        Skill(
            name="keep_left",
            recognize="tiles can slide left; keep the high tile on the left wall",
            do=["swipe_left"],
            end="after one left swipe",
            enabled=True,
        ),
        Skill(
            name="corner_down_left",
            recognize="pack toward the bottom-left corner",
            do=["swipe_down", "swipe_left"],
            end="after down then left",
            enabled=True,
        ),
    ):
        lib.propose(skill)
        lib.enable(skill.name, True)
    return lib
