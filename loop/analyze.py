"""Bernoulli contrast: p̂_on − p̂_off per scheduled skill.

Ratchet chooses a skill first, then rolls skill_off_frac. Off episodes still
record that skill_id so the control is matched, not a global p0 dump.
Older logs with skill_id only on the on-arm fall back to unmatched p_off.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from loop.config import project_root
from loop.curate.curator import skill_bank_path
from loop.log.schema import EpisodeLog
from loop.log.store import EpisodeStore
from loop.memory.skills import EvidenceRow, SkillBank


def _rate(flags: list[bool]) -> dict[str, float | int | None]:
    n = len(flags)
    k = int(sum(flags))
    p = (k / n) if n else None
    return {"n": n, "k": k, "p": p}


def _se(p_on: float | None, n_on: int, p_off: float | None, n_off: int) -> float | None:
    if p_on is None or p_off is None or n_on <= 0 or n_off <= 0:
        return None
    return (p_on * (1.0 - p_on) / n_on + p_off * (1.0 - p_off) / n_off) ** 0.5


def load_evidence(
    fixture: str,
    *,
    root: Path | None = None,
    run_dir: Path | None = None,
) -> list[EvidenceRow]:
    base = root or project_root()
    by_id: dict[str, EvidenceRow] = {}
    bank = SkillBank(skill_bank_path(fixture, base))
    if bank.evidence_path.exists():
        for line in bank.evidence_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = EvidenceRow.model_validate(json.loads(line))
            by_id[row.episode_id] = row
    run_dirs: list[Path] = []
    if run_dir is not None:
        run_dirs.append(run_dir)
    else:
        runs = base / "runs"
        if runs.exists():
            run_dirs.extend(
                sorted(
                    (p for p in runs.iterdir() if p.is_dir() and p.name.endswith(f"_{fixture}_collect")),
                    key=lambda p: p.name,
                )
            )
    for dest in run_dirs:
        store = EpisodeStore(dest)
        for raw in store.read_episodes():
            ep = EpisodeLog.model_validate(raw)
            if ep.outcome == "inconclusive":
                continue
            by_id[ep.episode_id] = EvidenceRow(
                episode_id=ep.episode_id,
                skill_id=ep.active_skill_id,
                skill_on=ep.skill_on,
                success=ep.success,
                signature="",
                run_dir=str(dest),
                arm=ep.arm,
            )
    return list(by_id.values())


def contrast(rows: list[EvidenceRow]) -> dict[str, Any]:
    ratchet = [r for r in rows if r.arm == "ratchet"]
    global_off = [r for r in ratchet if not r.skill_on]
    by_skill: dict[str, list[EvidenceRow]] = defaultdict(list)
    for row in ratchet:
        if row.skill_id:
            by_skill[row.skill_id].append(row)
    skills: list[dict[str, Any]] = []
    for skill_id, group in sorted(by_skill.items()):
        on = [r.success for r in group if r.skill_on]
        off_matched = [r.success for r in group if not r.skill_on]
        matched = bool(off_matched)
        off = off_matched if matched else [r.success for r in global_off]
        on_stats = _rate(on)
        off_stats = _rate(off)
        p_on = on_stats["p"]
        p_off = off_stats["p"]
        delta = None
        if isinstance(p_on, float) and isinstance(p_off, float):
            delta = p_on - p_off
        skills.append(
            {
                "skill_id": skill_id,
                "matched": matched,
                "n_on": on_stats["n"],
                "n_off": off_stats["n"],
                "p_on": p_on,
                "p_off": p_off,
                "delta": delta,
                "se": _se(
                    p_on if isinstance(p_on, float) else None,
                    int(on_stats["n"] or 0),
                    p_off if isinstance(p_off, float) else None,
                    int(off_stats["n"] or 0),
                ),
                "underpowered": int(on_stats["n"] or 0) < 20 or int(off_stats["n"] or 0) < 20,
            }
        )
    p0 = _rate([r.success for r in global_off])
    return {
        "n_episodes": len(ratchet),
        "n_on": sum(1 for r in ratchet if r.skill_on),
        "n_off": len(global_off),
        "p_off_global": p0["p"],
        "skills": skills,
    }


def analyze_fixture(
    fixture: str,
    *,
    root: Path | None = None,
    run_dir: Path | None = None,
) -> dict[str, Any]:
    rows = load_evidence(fixture, root=root, run_dir=run_dir)
    report = contrast(rows)
    report["fixture"] = fixture
    if run_dir is not None:
        report["run_dir"] = str(run_dir)
    return report
