from __future__ import annotations

from typing import Any

from loop.log.schema import StepLog, Terminal


def score_delta(prev: dict[str, Any], curr: dict[str, Any], terminal: Terminal) -> float:
    if terminal == "win":
        return 1.0
    if terminal == "fail":
        return -1.0
    if terminal == "timeout":
        return 0.0
    delta = 0.0
    prev_h = prev.get("health_frac")
    curr_h = curr.get("health_frac")
    if isinstance(prev_h, (int, float)) and isinstance(curr_h, (int, float)):
        delta += float(curr_h) - float(prev_h)
    prev_s = prev.get("stamina_frac")
    curr_s = curr.get("stamina_frac")
    if isinstance(prev_s, (int, float)) and isinstance(curr_s, (int, float)):
        delta += 0.1 * (float(curr_s) - float(prev_s))
    prev_r = prev.get("runes")
    curr_r = curr.get("runes")
    if isinstance(prev_r, (int, float)) and isinstance(curr_r, (int, float)):
        delta += 0.05 * (float(curr_r) - float(prev_r))
    prev_g = prev.get("gold")
    curr_g = curr.get("gold")
    if isinstance(prev_g, (int, float)) and isinstance(curr_g, (int, float)):
        delta += 0.001 * (float(curr_g) - float(prev_g))
    return delta


def explore_progress(
    *,
    view_changed: bool = False,
    new_cell: bool = False,
    new_prompt: bool = False,
) -> float:
    """Shaping between nothing and chapel success. Not a win by itself."""
    delta = 0.0
    if view_changed:
        delta += 0.02
    if new_cell:
        delta += 0.01
    if new_prompt:
        delta += 0.03
    return delta


def audit_step(
    *,
    metrics: dict[str, Any],
    pixel_changed: bool,
    failed_while_thinking: bool,
    action_name: str | None,
    menu_streak: int,
) -> list[str]:
    flags: list[str] = []
    health = metrics.get("health_frac")
    failed = metrics.get("fail_screen")
    if failed is False and isinstance(health, (int, float)) and health <= 0.02:
        flags.append("fail_screen_disagrees_vitals")
    if failed_while_thinking:
        flags.append("failed_while_thinking")
    if not pixel_changed:
        flags.append("no_pixel_change")
    if action_name in {"start", "back", "escape"}:
        flags.append("menu_button")
    if menu_streak >= 4:
        flags.append("long_menu_stretch")
    return flags


def hud_identity(metrics: dict[str, Any] | None) -> tuple[str, Any, Any]:
    metrics = metrics or {}
    screen = str(metrics.get("screen") or "").strip().lower()
    return (screen, metrics.get("hp"), metrics.get("floor"))


def hud_identity_stuck(steps: list[StepLog], *, window: int = 24) -> bool:
    """True when a populated screen/hp/floor freeze for `window` steps.

    Missing HUD is not a fail — tutorials and overlays often hide bars.
    """
    if window < 1 or len(steps) < window:
        return False
    tail = steps[-window:]
    ids = [hud_identity(row.metrics) for row in tail]
    if len(set(ids)) != 1:
        return False
    screen, hp, floor = ids[0]
    if screen in {"tutorial", "title"}:
        return False
    populated = bool(screen) or hp is not None or floor is not None
    return populated


def rejected_actionless_streak(steps: list[StepLog]) -> int:
    """Count the rejected, undispatched, pixel-static tail."""
    count = 0
    for row in reversed(steps):
        result = row.result
        if (
            result.execution_disposition != "rejected_pre_dispatch"
            or result.executed
            or result.pixel_changed
        ):
            break
        count += 1
    return count


def audit_episode(*, steps: list[StepLog], stuck_steps: int = 8) -> list[str]:
    flags: list[str] = []
    if len(steps) >= stuck_steps and all(
        not row.result.pixel_changed for row in steps[-stuck_steps:]
    ):
        flags.append("stuck_no_pixel_change")
    rejected_streak = rejected_actionless_streak(steps)
    if rejected_streak >= 3:
        flags.append(f"stuck_rejected_actionless:{rejected_streak}")
    if hud_identity_stuck(steps, window=max(stuck_steps, 24)):
        flags.append("hud_identity_stuck")
    if any(row.failed_while_thinking for row in steps):
        flags.append("failed_while_thinking")
    return flags
