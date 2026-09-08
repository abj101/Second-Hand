from __future__ import annotations

from avatar.schema import GsmSpec
from loop.log.schema import ActionLog
from loop.os.chords import ESCAPE_CYCLE, chord_name

EXPLORE_ACTIONS = ["stick_r_e", "stick_r_w", "stick_l_s", "stick_l_n"]


def explore_spec(*, kind: str = "rule") -> GsmSpec:
    """Frozen Elden explore GSM. Kinds stay rule|rl; no spawn at collect time."""
    return GsmSpec(
        name="explore",
        kind=kind if kind in {"rule", "rl"} else "rule",  # type: ignore[arg-type]
        fixture="elden",
        recognize="stall or unknown heading; look then walk",
        end="prompt visible, fail screen, or tick cap",
        enabled=True,
        state_source="pixels",
        action_space=list(EXPLORE_ACTIONS),
        detector_family="none",
        reward_ref={
            "view_changed": 1.0,
            "prompt_cleared": 2.0,
            "stall_penalty": -0.5,
            "step_penalty": -0.01,
        },
    )


class ExploreGsmRuntime:
    """Pad-chord explore motor. No VLM per tick. Escape cycle is owned here."""

    MAX_TICKS = 8

    def __init__(self, spec: GsmSpec | None = None) -> None:
        self.spec = spec or explore_spec()
        self.ticks = 0
        self._escape_i = 0
        self._walks = 0

    def reset(self) -> None:
        self.ticks = 0
        self._escape_i = 0
        self._walks = 0

    def next_action(
        self,
        *,
        stall: bool,
        prompt_on: bool,
        fail: bool = False,
        lock: bool = False,
        burst_ms: int = 400,
    ) -> tuple[ActionLog, bool]:
        if fail:
            action = ActionLog(duration_ms=burst_ms)
            action.name = "idle"
            return action, True
        if prompt_on and not lock:
            action = ActionLog(duration_ms=burst_ms)
            action.name = "idle"
            return action, True
        if lock:
            stall = True
        self.ticks += 1
        done = self.ticks >= self.MAX_TICKS
        if stall:
            stick_l, stick_r = ESCAPE_CYCLE[self._escape_i % len(ESCAPE_CYCLE)]
            self._escape_i += 1
            action = ActionLog(stick_l=stick_l, stick_r=stick_r, duration_ms=burst_ms)
        elif self._walks % 3 == 2:
            self._walks += 1
            action = ActionLog(stick_r="e", duration_ms=burst_ms)
        else:
            self._walks += 1
            action = ActionLog(stick_l="n", duration_ms=burst_ms)
        action.name = chord_name(action)
        return action, done


class SurveyGsmRuntime:
    """Mandated 4-point camera sweep. No VLM."""

    LOOKS = ("e", "e", "w", "w")

    def __init__(self) -> None:
        self.ticks = 0

    def reset(self) -> None:
        self.ticks = 0

    def next_action(self, *, burst_ms: int = 400) -> tuple[ActionLog, bool]:
        look = self.LOOKS[self.ticks]
        self.ticks += 1
        action = ActionLog(stick_r=look, duration_ms=burst_ms)
        action.name = chord_name(action)
        done = self.ticks >= len(self.LOOKS)
        return action, done


def sandbox_explore(frames: list[object] | None = None) -> tuple[bool, str]:
    """Stall must yaw, not walk north. VLM look chords are not rewritten here."""
    del frames
    runtime = ExploreGsmRuntime()
    first, _ = runtime.next_action(stall=True, prompt_on=False)
    if first.stick_r != "e" or first.stick_l is not None:
        return False, f"stall should yaw stick_r_e, got {first.name}"
    second, _ = runtime.next_action(stall=True, prompt_on=False)
    if second.stick_r != "w" or second.stick_l is not None:
        return False, f"second stall tick should yaw west, got {second.name}"
    idle, done = runtime.next_action(stall=True, prompt_on=True)
    if not done or (idle.stick_l or idle.stick_r or idle.tapped):
        return False, "prompt should yield idle"
    locked, lock_done = ExploreGsmRuntime().next_action(stall=False, prompt_on=True, lock=True)
    if lock_done or locked.stick_r != "e" or locked.stick_l is not None:
        return False, f"lock prompt should yaw, got {locked.name}"
    look = ActionLog(stick_r="nw", duration_ms=400)
    look.name = chord_name(look)
    if look.stick_l is not None or look.stick_r != "nw":
        return False, "look chord must stay look-only outside this runtime"
    return True, "ok"
