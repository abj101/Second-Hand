from __future__ import annotations

from typing import Any

from avatar.gsm.rl.dqn import DoubleDQN, encode_extracted, encode_pixels
from avatar.gsm.rl.reward import TETRIS_RL_ACTIONS
from avatar.gsm.runtime import GsmRuntime, GsmStep
from avatar.schema import GsmSpec


class RlGsmRuntime(GsmRuntime):
    """Deployed RL GSM: policy net only, no VLM per tick."""

    def __init__(self, spec: GsmSpec, *, policy: DoubleDQN | None = None, detector: Any = None) -> None:
        super().__init__(spec)
        names = list(spec.action_space) or list(TETRIS_RL_ACTIONS)
        self.names = names
        self.policy = policy
        self.detector = detector
        self.last_boxes: list[Any] = []
        self.banned: set[str] = set()
        self._done = False

    def reset(self) -> None:
        self.ticks = 0
        self._done = False
        self.last_boxes = []

    def step(self, frame: Any = None) -> GsmStep:
        self.ticks += 1
        if self._done:
            return GsmStep(actions=[], done=True, reason="placed")
        if self.policy is None:
            self._done = True
            return GsmStep(actions=["hard_drop"], done=True, reason="no-policy")
        if self.spec.state_source == "pixels":
            obs = encode_pixels(frame, dim=self.policy.n_obs)
        else:
            boxes: list[Any] = []
            if self.detector is not None and frame is not None:
                try:
                    boxes = list(self.detector.detect(frame, list(self.spec.labels) or []))
                except Exception:  # noqa: BLE001 — empty obs is honest if detect fails
                    boxes = []
            self.last_boxes = boxes
            obs = encode_extracted(boxes, dim=self.policy.n_obs)
        names = [n for n in self.names if n not in self.banned] or list(self.names)
        name = self.policy.act_name(obs, names, greedy=True)
        self._done = name == "hard_drop" or self.ticks >= 16
        return GsmStep(actions=[name], done=self._done, reason="rl")
