from __future__ import annotations

from typing import Any, Callable

from avatar.gsm.rl.dqn import encode_extracted, encode_pixels
from avatar.gsm.rl.reward import TETRIS_RL_ACTIONS, interpret_reward
from avatar.schema import GsmStateSource


class FakeGsmEnv:
    """In-memory env for tests. No window, no game API."""

    def __init__(
        self,
        *,
        state_source: GsmStateSource = "extracted",
        reward_params: dict[str, Any] | None = None,
        max_steps: int = 8,
    ) -> None:
        self.state_source = state_source
        self.reward_params = reward_params or {}
        self.max_steps = max_steps
        self.actions = list(TETRIS_RL_ACTIONS)
        self.n_obs = 16
        self.progress = 0.0
        self.steps = 0
        self.dead = False
        self.history: list[int] = []

    def reset(self) -> tuple[list[float], dict[str, Any]]:
        self.progress = 0.0
        self.steps = 0
        self.dead = False
        self.history = []
        return self._obs(), {"progress": 0.0, "dead": False}

    def step(self, action: int) -> tuple[list[float], float, bool, dict[str, Any]]:
        prev = {"progress": self.progress, "dead": self.dead}
        self.steps += 1
        if action == 3:
            self.progress += 1.0
        elif action == 0 and self.steps > 6:
            self.dead = True
        self.history.append(action)
        done = self.dead or self.steps >= self.max_steps or action == 3
        nxt = {"progress": self.progress, "dead": self.dead}
        reward = interpret_reward(prev, nxt, action, done, self.history[:-1], self.reward_params)
        return self._obs(), reward, done, nxt

    def _obs(self) -> list[float]:
        if self.state_source == "pixels":
            payload = bytes([int(self.progress) % 255] * 32)
            return encode_pixels(type("F", (), {"data": payload})(), dim=self.n_obs)
        if self.progress == 0:
            return encode_extracted([], dim=self.n_obs)
        vec = [self.progress, float(self.steps)]
        return vec + [0.0] * (self.n_obs - 2)


def occupancy_progress(boxes: list[Any]) -> tuple[float, bool]:
    """Screenshot occupancy: lower/flatter stack is better. Not a drop counter."""
    if not boxes:
        return 0.0, False
    n = float(len(boxes))
    height = sum(max(0.0, 1.0 - float(getattr(b, "y", 1.0))) for b in boxes) / n
    progress = -height - 0.02 * n
    piled_high = sum(1 for b in boxes if float(getattr(b, "y", 1.0)) < 0.12)
    dead = piled_high >= 3 and n >= 8
    return progress, dead


class TetrisGsmEnv:
    """Gymnasium-shaped wrapper: screenshot ROI → named actions → SendInput.

    Observation is pixels (JPEG) or extracted (detector vector). Reset uses
    the `restart` named action, never a teleport or game API.
    """

    def __init__(
        self,
        execute_named: Callable[..., None],
        grab: Callable[[], Any],
        *,
        table_name: str = "tetris",
        state_source: GsmStateSource = "extracted",
        reward_params: dict[str, Any] | None = None,
        detector: Any = None,
        max_steps: int = 32,
        labels: list[str] | None = None,
    ) -> None:
        self.execute_named = execute_named
        self.grab = grab
        self.table_name = table_name
        self.state_source = state_source
        self.reward_params = {"clean_action": -1, **(reward_params or {})}
        self.detector = detector
        self.max_steps = max_steps
        self.labels = list(labels or [])
        self.actions = list(TETRIS_RL_ACTIONS)
        self.n_obs = 16
        self.steps = 0
        self.history: list[int] = []
        self.progress = 0.0
        self._last_boxes: list[Any] = []

    def reset(self) -> tuple[list[float], dict[str, Any]]:
        self.execute_named(self.table_name, "restart")
        self.execute_named(self.table_name, "start")
        self.steps = 0
        self.history = []
        info = self._info()
        self.progress = float(info["progress"])
        return self._obs(), info

    def step(self, action: int) -> tuple[list[float], float, bool, dict[str, Any]]:
        prev = {"progress": self.progress, "dead": False}
        name = self.actions[action]
        self.execute_named(self.table_name, name)
        self.steps += 1
        self.history.append(action)
        nxt = self._info()
        self.progress = float(nxt["progress"])
        done = bool(nxt["dead"]) or self.steps >= self.max_steps
        reward = interpret_reward(prev, nxt, action, done, self.history[:-1], self.reward_params)
        return self._obs(), reward, done, nxt

    def _info(self) -> dict[str, Any]:
        boxes = self._boxes()
        self._last_boxes = boxes
        progress, dead = occupancy_progress(boxes)
        return {"progress": progress, "dead": dead}

    def _boxes(self) -> list[Any]:
        frame = None
        try:
            frame = self.grab()
        except Exception:  # noqa: BLE001
            frame = None
        if self.detector is None or frame is None:
            return []
        try:
            return list(self.detector.detect(frame, self.labels) or [])
        except Exception:  # noqa: BLE001
            return []

    def _obs(self) -> list[float]:
        frame = None
        try:
            frame = self.grab()
        except Exception:  # noqa: BLE001
            frame = None
        if self.state_source == "pixels":
            return encode_pixels(frame, dim=self.n_obs)
        boxes = self._last_boxes
        if self.detector is not None and frame is not None:
            try:
                boxes = list(self.detector.detect(frame, self.labels) or [])
                self._last_boxes = boxes
            except Exception:  # noqa: BLE001
                boxes = []
        return encode_extracted(boxes, dim=self.n_obs)
