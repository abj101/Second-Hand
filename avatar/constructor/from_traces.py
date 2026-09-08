from __future__ import annotations

from collections import Counter
from pathlib import Path

from avatar.constructor.skills import Skill, SkillLibrary
from avatar.eval.logger import iter_jsonl


def skills_from_traces(
    jsonl_path: Path,
    library: SkillLibrary | None = None,
    *,
    min_repeats: int = 3,
) -> SkillLibrary:
    """Offline constructor. Counts repeated action sequences; merge-not-spawn."""
    lib = library or SkillLibrary()
    counts: Counter[tuple[str, ...]] = Counter()
    examples: dict[tuple[str, ...], dict] = {}
    for row in iter_jsonl(jsonl_path):
        action = row.get("action") or {}
        names = tuple(
            step.get("name")
            for step in (action.get("actions") or [])
            if isinstance(step, dict) and step.get("name")
        )
        if not names:
            if action.get("invoke_skill"):
                names = (f"invoke:{action['invoke_skill']}",)
            else:
                continue
        counts[names] += 1
        examples[names] = row
    for seq, n in counts.most_common():
        if n < min_repeats:
            continue
        sample = examples[seq]
        candidate = Skill(
            name="_".join(seq)[:48] or "pattern",
            recognize=f"Repeated {n}x in traces; fixture={sample.get('fixture')}",
            do=[s for s in seq if not str(s).startswith("invoke:")],
            end="Return to planner after the sequence.",
            traces=[str(jsonl_path)],
            enabled=False,
        )
        lib.propose(candidate)
    return lib
