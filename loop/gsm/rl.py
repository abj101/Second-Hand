from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from avatar.gsm.rl.dqn import DoubleDQN, LinearQ
from avatar.schema import GsmSpec
from loop.config import DEFAULT_MODEL
from loop.eval.outcome import cv_backed_prompt
from loop.gsm.explore import EXPLORE_ACTIONS, explore_spec
from loop.log.schema import StepLog
from loop.os.chords import chord_name, is_look_only, is_loco_walk

EXPLORE_RL_ACTIONS = list(EXPLORE_ACTIONS)
EXPLORE_OBS_DIM = 8


def explore_reward(
    *,
    view_changed: bool,
    prompt_cleared: bool,
    stall: bool,
    params: dict[str, Any] | None = None,
) -> float:
    """Numeric reward for an explore GSM. VLM does not rewrite this Python."""
    cfg = {
        "view_changed": 1.0,
        "prompt_cleared": 2.0,
        "stall_penalty": -0.5,
        "step_penalty": -0.01,
        **(params or {}),
    }
    reward = float(cfg["step_penalty"])
    if view_changed:
        reward += float(cfg["view_changed"])
    if prompt_cleared:
        reward += float(cfg["prompt_cleared"])
    if stall:
        reward += float(cfg["stall_penalty"])
    return float(reward)


def sandbox_explore_reward(params: dict[str, Any] | None = None) -> tuple[bool, str]:
    """view_changed must beat stall. Sequential VRAM: unload Ollama before DQN train."""
    r_view = explore_reward(view_changed=True, prompt_cleared=False, stall=False, params=params)
    r_stall = explore_reward(view_changed=False, prompt_cleared=False, stall=True, params=params)
    if r_view <= r_stall:
        return False, f"view reward {r_view} not greater than stall {r_stall}"
    r_prompt = explore_reward(view_changed=False, prompt_cleared=True, stall=False, params=params)
    if r_prompt <= r_view:
        return False, f"prompt-cleared {r_prompt} should beat look-only {r_view}"
    return True, "ok"


def explore_rl_spec() -> GsmSpec:
    spec = explore_spec(kind="rl")
    spec.action_space = list(EXPLORE_RL_ACTIONS)
    return spec


def encode_explore_obs(step: StepLog) -> list[float]:
    """Compact stall / look / prompt vector. No pixels — sandbox on logged pairs."""
    result = step.result
    prompt_ok = bool(result.prompt_cleared and cv_backed_prompt(step))
    return [
        1.0 if result.stall else 0.0,
        1.0 if result.view_changed else 0.0,
        1.0 if prompt_ok else 0.0,
        1.0 if result.pixel_changed else 0.0,
        1.0 if is_look_only(step.action) else 0.0,
        1.0 if is_loco_walk(step.action) else 0.0,
        1.0 if result.invoked_gsm else 0.0,
        1.0 if step.prompt_on else 0.0,
    ]


def explore_action_index(step: StepLog) -> int | None:
    name = chord_name(step.action) or (step.action.name or "")
    try:
        return EXPLORE_RL_ACTIONS.index(name)
    except ValueError:
        return None


def transitions_from_steps(
    steps: list[StepLog],
) -> list[tuple[list[float], int, float, list[float], bool]]:
    """(s, a, r, s', done) for consecutive explore chords in the same episode."""
    by_ep: dict[str, list[StepLog]] = {}
    for step in steps:
        by_ep.setdefault(step.episode_id, []).append(step)
    out: list[tuple[list[float], int, float, list[float], bool]] = []
    for rows in by_ep.values():
        ordered = sorted(rows, key=lambda row: row.step_id)
        for i, step in enumerate(ordered):
            action_i = explore_action_index(step)
            if action_i is None:
                continue
            nxt = ordered[i + 1] if i + 1 < len(ordered) else None
            prompt_ok = bool(step.result.prompt_cleared and cv_backed_prompt(step))
            reward = explore_reward(
                view_changed=step.result.view_changed,
                prompt_cleared=prompt_ok,
                stall=step.result.stall,
            )
            nxt_obs = encode_explore_obs(nxt) if nxt is not None else encode_explore_obs(step)
            done = nxt is None or step.terminal in {"fail", "win"} or prompt_ok
            out.append((encode_explore_obs(step), action_i, reward, nxt_obs, done))
    return out


def dump_q(net: LinearQ) -> dict[str, Any]:
    return {
        "n_obs": net.n_obs,
        "n_act": net.n_act,
        "hidden": net.hidden,
        "w1": net.w1,
        "b1": net.b1,
        "w2": net.w2,
        "b2": net.b2,
    }


def load_q(payload: dict[str, Any]) -> LinearQ:
    net = LinearQ(int(payload["n_obs"]), int(payload["n_act"]), int(payload.get("hidden") or 16))
    net.w1 = payload["w1"]
    net.b1 = payload["b1"]
    net.w2 = payload["w2"]
    net.b2 = payload["b2"]
    return net


def train_explore_dqn(
    steps: list[StepLog],
    *,
    dest: Path | None = None,
    train_steps: int = 64,
    seed: int = 0,
) -> dict[str, Any]:
    """Sandbox reward, then DoubleDQN on logged explore pairs. No live pad."""
    import random

    random.seed(seed)
    ok, reason = sandbox_explore_reward()
    trans = transitions_from_steps(steps)
    report: dict[str, Any] = {
        "ok": ok,
        "reason": reason,
        "n_transitions": len(trans),
        "sandbox_ok": ok,
    }
    if not ok:
        return report
    if len(trans) < 4:
        report["ok"] = False
        report["reason"] = f"need ≥4 explore transitions, got {len(trans)}"
        return report
    agent = DoubleDQN(
        n_obs=EXPLORE_OBS_DIM,
        n_act=len(EXPLORE_RL_ACTIONS),
        epsilon=0.2,
        hidden=16,
    )
    for obs, action, reward, nxt, done in trans:
        agent.remember(obs, action, reward, nxt, done)
    losses: list[float] = []
    for i in range(max(1, train_steps)):
        losses.append(agent.train_step())
        if i % 8 == 7:
            agent.sync_target()
    agent.sync_target()
    payload = {
        "actions": list(EXPLORE_RL_ACTIONS),
        "n_obs": EXPLORE_OBS_DIM,
        "online": dump_q(agent.online),
        "n_transitions": len(trans),
        "train_steps": train_steps,
        "loss_last": losses[-1] if losses else None,
    }
    if dest is not None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(payload), encoding="utf-8")
        report["policy"] = str(dest)
    report["ok"] = True
    report["reason"] = "ok"
    report["loss_last"] = payload["loss_last"]
    report["agent"] = agent
    return report


def train_explore_from_run(
    run_dir: Path,
    *,
    dest: Path | None = None,
    train_steps: int = 64,
    planner_model: str = DEFAULT_MODEL,
) -> dict[str, Any]:
    from loop.eval.export import load_steps
    from loop.eval.sft import unload_planner

    unload_planner(planner_model)
    steps = load_steps(run_dir)
    return train_explore_dqn(
        steps,
        dest=dest or (run_dir / "explore_dqn.json"),
        train_steps=train_steps,
    )
