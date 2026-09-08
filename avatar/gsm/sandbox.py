from __future__ import annotations

from typing import Any

from avatar.gsm.rl.reward import interpret_reward
from avatar.schema import GsmSpec


def sandbox_reward(params: dict[str, Any] | None = None) -> tuple[bool, str]:
    """Synthetic (s, s', a, done) checks. Broken always-dodge rewards fail."""
    cfg = dict(params or {})
    progress_s = {"progress": 0.0, "dead": False}
    progress_sp = {"progress": 1.0, "dead": False}
    dodge_s = {"progress": 0.0, "dead": False}
    dodge_sp = {"progress": 0.0, "dead": False}
    r_progress = interpret_reward(progress_s, progress_sp, 3, False, [], cfg)
    r_dodge = interpret_reward(dodge_s, dodge_sp, 0, False, [0, 0, 0, 0], cfg)
    if cfg.get("dodge_bonus", 0) >= 10 and cfg.get("progress_scale", 1.0) <= 0:
        return False, "always-dodge: progress ignored, dodge-spam rewarded"
    if r_progress <= r_dodge:
        return False, f"progress reward {r_progress} not greater than dodge {r_dodge}"
    death_sp = {"progress": 0.0, "dead": True}
    r_death = interpret_reward(progress_s, death_sp, 0, True, [], cfg)
    if r_death >= 0:
        return False, f"death should be penalized, got {r_death}"
    return True, "ok"


def sandbox_rule(spec: GsmSpec, frames: list[Any] | None = None) -> tuple[bool, str]:
    """Labeled fake frames: policy must emit a known table action then done."""
    from avatar.gsm.rule.detector import FakeDetector
    from avatar.gsm.rule.runtime import RuleGsmRuntime

    _ = frames
    runtime = RuleGsmRuntime(spec, detector=FakeDetector())
    seen: list[str] = []
    for _ in range(16):
        step = runtime.step(None)
        seen.extend(step.actions)
        if step.done:
            break
    else:
        return False, "rule GSM never returned done"
    allowed = set(spec.action_space) | {
        "shift_left",
        "shift_right",
        "rotate",
        "hard_drop",
        "soft_drop",
        "hold",
        "swipe_up",
        "swipe_down",
        "swipe_left",
        "swipe_right",
    }
    if not seen:
        return False, "rule GSM emitted no actions"
    bad = [n for n in seen if n not in allowed]
    if bad:
        return False, f"unknown actions {bad}"
    return True, "ok"


def sandbox_spec(spec: GsmSpec) -> tuple[bool, str]:
    if spec.kind == "rl":
        return sandbox_reward(spec.reward_ref)
    return sandbox_rule(spec)
