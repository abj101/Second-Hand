from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from PIL import Image

from loop.log.schema import ActionLog, StepLog, Terminal
from loop.os.executor import OsController
from loop.os.profile import CaptureProfile

DeviceKind = Literal["pad", "keyboard", "mouse"]
Pace = Literal["realtime", "turn"]

# Activities where entity/enemy targeting is legal. Fixtures may override.
DEFAULT_TARGETABLE_ACTIVITIES: frozenset[str] = frozenset({"combat"})


class Fixture(Protocol):
    id: str
    window_title: str
    device: DeviceKind
    action_table: str
    pace: Pace

    def start_snapshot(self) -> Path | None: ...
    def scorer(self, image: Image.Image) -> dict[str, Any]: ...
    def terminal(self, metrics: dict[str, Any], image: Image.Image) -> Terminal: ...
    def after_fail(self, *, softlock: bool = False, eval_mode: bool = False) -> Literal["in_game", "snapshot"]: ...
    def restore_snapshot(self) -> bool: ...
    def grab_image(self) -> Image.Image: ...
    def execute(self, name: str, *, duration_ms: int | None = None) -> list[str]: ...
    def execute_chord(self, action: ActionLog, *, settle: bool = True) -> list[str]: ...
    def enrich_frame_marks(self, marks: list[Any], context: Any, host_stats: dict[str, Any] | None = None) -> list[Any]: ...
    def success(self, steps: list[StepLog], terminal: Terminal) -> bool: ...
    def close(self) -> None: ...


def alive_success(
    steps: list[StepLog],
    terminal: Terminal,
    *,
    min_survived_steps: int = 8,
) -> bool:
    """Fake / parametric lives: survive K with a visible bar. Not chapel."""
    rune_start = _first_number(steps, "runes")
    rune_end = _last_number(steps, "runes")
    if rune_start is not None and rune_end is not None and rune_end - rune_start > 0:
        return True
    if terminal == "win":
        return True
    if terminal == "fail":
        return False
    if len(steps) < min_survived_steps:
        return False
    health = _last_number(steps, "health_frac")
    if health is None or health <= 0.02:
        return False
    return True


def chapel_success(
    steps: list[StepLog],
    terminal: Terminal,
    *,
    min_survived_steps: int = 8,
) -> bool:
    """Chapel: rune, prompt-cleared, or look coverage. Wall-timeout is fail."""
    del min_survived_steps
    from loop.eval.outcome import verified_chapel_success

    return verified_chapel_success(steps, terminal)


def _first_number(steps: list[StepLog], key: str) -> float | None:
    for step in steps:
        value = step.metrics.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _last_number(steps: list[StepLog], key: str) -> float | None:
    for step in reversed(steps):
        value = step.metrics.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return None


@dataclass
class FixtureBase:
    id: str
    window_title: str
    device: DeviceKind
    action_table: str
    controller: OsController | None = None
    profile: CaptureProfile = field(default_factory=CaptureProfile)
    executed: list[str] = field(default_factory=list)
    min_survived_steps: int = 8
    pace: Pace = "realtime"
    settle_ms: int = 0
    quiet_timeout_ms: int = 0
    obs_longest_side: int = 768
    # Play modes that allow entity/enemy targeting (override per fixture).
    targetable_activities: frozenset[str] = field(
        default_factory=lambda: frozenset(DEFAULT_TARGETABLE_ACTIVITIES)
    )

    def start_snapshot(self) -> Path | None:
        return None

    def scorer(self, image: Image.Image) -> dict[str, Any]:
        return {}

    def resolve_activity(
        self,
        *,
        metrics: dict[str, Any] | None = None,
        host_stats: dict[str, Any] | None = None,
        screen: str | None = None,
    ) -> str:
        """Underlying play mode; may differ from UI ``screen`` when overlays are up."""
        from loop.os.activity import activity_from_state

        metrics = metrics or {}
        host_stats = host_stats or {}
        screen_key = screen
        if screen_key is None:
            screen_key = str(
                host_stats.get("screen") or metrics.get("screen") or ""
            ).strip() or None
        return activity_from_state(
            screen=screen_key,
            metrics=metrics,
            host_stats=host_stats,
        )

    def allows_entity_targets(
        self,
        *,
        metrics: dict[str, Any] | None = None,
        host_stats: dict[str, Any] | None = None,
        screen: str | None = None,
    ) -> bool:
        """True when enemy/entity targeting is legal for the current activity."""
        activity = self.resolve_activity(
            metrics=metrics,
            host_stats=host_stats,
            screen=screen,
        )
        return activity in self.targetable_activities

    def terminal(self, metrics: dict[str, Any], image: Image.Image) -> Terminal:
        if metrics.get("fail_screen"):
            return "fail"
        if metrics.get("win_screen"):
            return "win"
        return "alive"

    def after_fail(self, *, softlock: bool = False, eval_mode: bool = False) -> Literal["in_game", "snapshot"]:
        if eval_mode or softlock:
            if self.restore_snapshot():
                return "snapshot"
        return "in_game"

    def restore_snapshot(self) -> bool:
        return False

    def grab_image(self) -> Image.Image:
        if self.controller is None:
            raise RuntimeError(f"Fixture {self.id} has no controller")
        return self.controller.capture.grab_image(full=False)

    def observe_image(self, image: Image.Image) -> Image.Image:
        """Frame sent to the VLM. Default is the raw grab."""
        return image

    def act_gate(self, image: Image.Image) -> Literal["ours", "theirs", "unknown"]:
        """Whether the player may legally act on this still. Unknown = quiet is enough."""
        del image
        return "unknown"

    def enrich_frame_marks(
        self,
        marks: list[Any],
        context: Any,
        host_stats: dict[str, Any] | None = None,
    ) -> list[Any]:
        """Fixture-owned semantic enrichment; generic fixtures preserve marks."""
        del context, host_stats
        return list(marks)

    def execute(self, name: str, *, duration_ms: int | None = None) -> list[str]:
        if self.controller is None:
            raise RuntimeError(f"Fixture {self.id} has no controller")
        self.controller.execute_named(self.action_table, name, duration_ms=duration_ms)
        self.executed.append(name)
        return [name]

    def execute_chord(self, action: ActionLog, *, settle: bool = True) -> list[str]:
        del settle
        if self.controller is None:
            raise RuntimeError(f"Fixture {self.id} has no controller")
        name = self.controller.execute_chord(self.action_table, action)
        self.executed.append(name)
        return [name]

    def success(self, steps: list[StepLog], terminal: Terminal) -> bool:
        return chapel_success(steps, terminal, min_survived_steps=self.min_survived_steps)

    def close(self) -> None:
        if self.controller is not None:
            self.controller.close()
            self.controller = None
