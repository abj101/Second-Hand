from __future__ import annotations

from loop.eval.outcome import cv_backed_prompt
from loop.log.schema import EpisodeLog, StepLog
from loop.os.chords import is_look_only


def _bucket(frac: float) -> str:
    quantized = round(max(0.0, min(1.0, frac)) * 5) / 5
    return f"{quantized:.1f}"


def failure_signature(episode: EpisodeLog, steps: list[StepLog]) -> str:
    n = max(len(steps), 1)
    stall_frac = sum(1 for row in steps if row.result.stall) / n
    look_frac = sum(
        1 for row in steps if is_look_only(row.action) and row.result.view_changed
    ) / n
    prompt_cleared = any(row.result.prompt_cleared for row in steps)
    return (
        f"stall={_bucket(stall_frac)}|look={_bucket(look_frac)}"
        f"|prompt={int(prompt_cleared)}|{episode.terminal}"
    )


def cluster_failures(
    episodes: list[EpisodeLog],
    steps_by_episode: dict[str, list[StepLog]],
) -> dict[str, list[EpisodeLog]]:
    clusters: dict[str, list[EpisodeLog]] = {}
    for episode in episodes:
        if episode.outcome != "loss":
            continue
        steps = steps_by_episode.get(episode.episode_id) or []
        sig = failure_signature(episode, steps)
        clusters.setdefault(sig, []).append(episode)
    return clusters


def commit_allowed(skill_chord: dict, steps: list[StepLog]) -> bool:
    """Look skills need matching view_changed. Tap skills need a CV-backed clear."""
    stick_r = skill_chord.get("stick_r")
    stick_l = skill_chord.get("stick_l")
    tapped = skill_chord.get("tapped") or skill_chord.get("held") or []
    look_only = bool(stick_r) and not stick_l and not tapped
    if look_only:
        want = str(stick_r or "").lower()
        return any(
            is_look_only(row.action)
            and row.result.view_changed
            and (row.action.stick_r or "").lower() == want
            for row in steps
        )
    if tapped:
        if any(row.prompt_on for row in steps):
            return any(row.result.prompt_cleared and cv_backed_prompt(row) for row in steps)
        return True
    return True
