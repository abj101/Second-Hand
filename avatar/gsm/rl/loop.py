from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from avatar.eval.logger import new_run_dir
from avatar.gsm.rl.dqn import DoubleDQN
from avatar.gsm.rl.reward import DEFAULT_REWARD
from avatar.schema import GsmSpec


def train_analyze_optimize(
    spec: GsmSpec,
    *,
    env: Any | None = None,
    max_opt_iters: int = 3,
    episodes: int = 8,
    steps_per_ep: int = 8,
    run_dir: Path | None = None,
    rewrite_reward: Callable[[dict[str, Any], list[float]], dict[str, Any]] | None = None,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Train → dump curves → optional VLM rewrite of JSON reward → sandbox → train. Cap 3."""
    from avatar.gsm.sandbox import sandbox_reward

    if env is None:
        raise ValueError(
            "Live train_analyze_optimize requires TetrisGsmEnv. "
            "Pass FakeGsmEnv only from tests."
        )
    params = {**DEFAULT_REWARD, **(spec.reward_ref or {})}
    dest = run_dir or new_run_dir("gsm_train", spec.fixture or "tetris-rt")
    dest.mkdir(parents=True, exist_ok=True)
    curves: list[list[float]] = []
    agent = DoubleDQN(n_obs=env.n_obs, n_act=len(env.actions), epsilon=0.3)
    last_ok = True
    last_reason = "ok"
    opt_used = 0
    for opt in range(max(1, min(max_opt_iters, 3))):
        ok, reason = sandbox_reward(params)
        last_ok, last_reason = ok, reason
        if not ok:
            break
        env.reward_params = params
        returns: list[float] = []
        for ep_i in range(episodes):
            obs, _info = env.reset()
            ep_ret = 0.0
            done = False
            for _step in range(steps_per_ep):
                action = agent.act(obs)
                nxt, reward, done, _ = env.step(action)
                agent.remember(obs, action, reward, nxt, done)
                agent.train_step()
                ep_ret += reward
                obs = nxt
                if done:
                    break
            returns.append(ep_ret)
            agent.sync_target()
            if on_progress is not None:
                on_progress(
                    {
                        "opt": opt + 1,
                        "episode": ep_i + 1,
                        "episodes": episodes,
                        "return": ep_ret,
                        "done": done,
                    }
                )
        curves.append(returns)
        opt_used = opt + 1
        if rewrite_reward is None:
            break
        params = rewrite_reward(params, returns)
        spec.reward_ref = params
        spec.rewrites += 1
    curve_path = dest / "rl_curves.json"
    import json

    curve_path.write_text(
        json.dumps({"curves": curves, "opt_iters": opt_used, "sandbox": last_reason}, indent=2),
        encoding="utf-8",
    )
    spec.enabled = False
    return {
        "run_dir": str(dest),
        "curves": curves,
        "opt_iters": opt_used,
        "sandbox_ok": last_ok,
        "sandbox_reason": last_reason,
        "agent": agent,
        "spec": spec,
    }
