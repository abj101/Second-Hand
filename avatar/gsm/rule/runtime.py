from __future__ import annotations

from typing import Any

from avatar.gsm.rule.detector import Detector, FakeDetector
from avatar.gsm.rule.policy import boxes_to_actions, boxes_to_swipes
from avatar.gsm.runtime import GsmRuntime, GsmStep
from avatar.schema import GsmSpec


class RuleGsmRuntime(GsmRuntime):
    """Detector boxes → named actions. No VLM per tick."""

    def __init__(self, spec: GsmSpec, *, detector: Detector | None = None) -> None:
        super().__init__(spec)
        self.detector = detector if detector is not None else FakeDetector()
        spec.detector_family = getattr(self.detector, "family", "") or spec.detector_family
        self.last_boxes: list[Any] = []
        self._queue: list[str] = []
        self._done = False
        self._last_key = ""
        self.banned: set[str] = set()
        self.rates: dict[str, float] = {}

    def reset(self) -> None:
        self.ticks = 0
        self.last_boxes = []
        self._queue = []
        self._done = False
        self._last_key = ""

    def step(self, frame: Any = None) -> GsmStep:
        self.ticks += 1
        if self._done:
            return GsmStep(actions=[], done=True, reason="placed")
        self.last_boxes = self.detector.detect(frame, list(self.spec.labels))
        banned = self.banned or None
        swipe = any(str(name).startswith("swipe_") for name in (self.spec.action_space or []))
        if swipe:
            queue = list(
                    boxes_to_swipes(
                        self.last_boxes,
                        action_space=list(self.spec.action_space),
                        banned=banned,
                        rates=self.rates or None,
                    )
            )
        else:
            queue = list(
                boxes_to_actions(
                    self.last_boxes,
                    labels=list(self.spec.labels) or None,
                    banned=banned,
                )
            )
        if not queue:
            self._done = True
            fallback = "swipe_down" if self.spec.fixture == "2048" else "hard_drop"
            return GsmStep(actions=[fallback], done=True, reason="no-piece")
        name = queue[0]
        from avatar.gsm.rule.policy import occupancy_fingerprint

        key = occupancy_fingerprint(self.last_boxes)
        if not swipe and key and key == self._last_key and name != "hard_drop":
            name = "hard_drop"
        self._last_key = key
        done = swipe or name == "hard_drop" or self.ticks >= 16
        if done:
            self._done = True
        return GsmStep(actions=[name], done=done, reason="rule" if done else "")
