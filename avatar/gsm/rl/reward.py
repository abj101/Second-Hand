from __future__ import annotations

from typing import Any

DEFAULT_REWARD: dict[str, Any] = {
    "progress_scale": 1.0,
    "death_penalty": -1.0,
    "step_penalty": -0.01,
    "clean_action_bonus": 0.05,
    "overuse_penalty": 0.1,
    "overuse_window": 8,
    "dodge_bonus": 0.0,
    "tier_thresholds": [1.0, 4.0, 10.0],
    "tier_bonuses": [0.5, 1.0, 2.0],
    "clean_action": 3,
}

TETRIS_RL_ACTIONS = ["shift_left", "shift_right", "rotate", "hard_drop"]


def interpret_reward(
    prev: dict[str, Any],
    nxt: dict[str, Any],
    action_index: int,
    done: bool,
    action_history: list[int],
    params: dict[str, Any] | None = None,
) -> float:
    """VLM fills numbers, not code. Never exec model Python."""
    cfg = {**DEFAULT_REWARD, **(params or {})}
    progress = float(nxt.get("progress") or 0) - float(prev.get("progress") or 0)
    reward = float(cfg["progress_scale"]) * progress + float(cfg["step_penalty"])
    if int(cfg.get("clean_action", 3)) == action_index:
        reward += float(cfg["clean_action_bonus"])
    window = int(cfg.get("overuse_window") or 8)
    recent = action_history[-window:]
    if recent.count(action_index) >= max(3, window // 2):
        reward -= float(cfg["overuse_penalty"])
    dodge = float(cfg.get("dodge_bonus") or 0)
    if dodge and action_index == 0:
        reward += dodge
    if done:
        if nxt.get("dead"):
            reward += float(cfg["death_penalty"])
        progress_end = float(nxt.get("progress") or 0)
        thresholds = list(cfg.get("tier_thresholds") or [])
        bonuses = list(cfg.get("tier_bonuses") or [])
        for thresh, bonus in zip(thresholds, bonuses):
            if progress_end >= float(thresh):
                reward += float(bonus)
    return float(reward)
