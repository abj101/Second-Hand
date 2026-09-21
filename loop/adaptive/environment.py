"""Generic environment protocol and in-memory fixture for benchmarks."""

from __future__ import annotations

import time
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Protocol

from PIL import Image

from loop.adaptive.candidates import ActionCatalog, CandidateAction


@dataclass(frozen=True)
class StepResult:
    observation: Any
    frame_digest: str
    episode_id: str
    episode_generation: int
    step: int
    tick: int
    virtual_game_time: float
    score: float
    score_delta: float
    reward: float
    terminated: bool
    truncated: bool
    termination_reason: str | None
    environment_revision: str
    action_catalog_revision: str
    errors: tuple[str, ...]
    privileged_info: dict
    capture_ts: float
    dispatch_ts: float


def _image_digest(img: Image.Image) -> str:
    return sha256(img.tobytes()).hexdigest()


def _seed_image(seed: int) -> Image.Image:
    color = (seed % 256, (seed * 7) % 256, (seed * 13) % 256)
    return Image.new("RGB", (32, 32), color)


class Environment(Protocol):
    async def reset(
        self, seed: int, episode_id: str, episode_generation: int
    ) -> StepResult: ...

    def actions(self) -> ActionCatalog: ...

    async def step(self, action_id: str) -> StepResult: ...

    async def close(self) -> None: ...

    def fingerprint(self) -> dict: ...

    def release_all(self) -> None: ...


class MemoryEnvironment:
    _CATALOG_REV = "memory-catalog-v1"
    _ENV_REV = "memory-v1"

    def __init__(
        self,
        action_ids: tuple[str, ...] = ("wait", "advance", "retreat"),
        *,
        max_steps: int = 8,
        privileged_secret: object = "SECRET",
    ) -> None:
        self._action_ids = action_ids
        self._max_steps = max_steps
        self._privileged_secret = privileged_secret
        self.released = False
        self._seed = 0
        self._episode_id = ""
        self._episode_generation = 0
        self._step = 0
        self._tick = 0
        self._virtual_game_time = 0.0
        self._score = 0.0
        self._terminated = False
        self._truncated = False
        self._termination_reason: str | None = None
        self._last_image: Image.Image | None = None

    def _catalog(self) -> ActionCatalog:
        return ActionCatalog(
            revision=self._CATALOG_REV,
            candidates=tuple(
                CandidateAction(
                    action_id=aid,
                    command=aid,
                    description=f"memory action {aid}",
                )
                for aid in self._action_ids
            ),
        )

    def _make_result(self, *, score_delta: float) -> StepResult:
        now = time.monotonic()
        img = self._last_image
        digest = _image_digest(img) if img is not None else ""
        reward = score_delta
        return StepResult(
            observation=img,
            frame_digest=digest,
            episode_id=self._episode_id,
            episode_generation=self._episode_generation,
            step=self._step,
            tick=self._tick,
            virtual_game_time=self._virtual_game_time,
            score=self._score,
            score_delta=score_delta,
            reward=reward,
            terminated=self._terminated,
            truncated=self._truncated,
            termination_reason=self._termination_reason,
            environment_revision=self._ENV_REV,
            action_catalog_revision=self._CATALOG_REV,
            errors=(),
            privileged_info={"secret": self._privileged_secret, "x": int(self._score)},
            capture_ts=now,
            dispatch_ts=now,
        )

    async def reset(
        self, seed: int, episode_id: str, episode_generation: int
    ) -> StepResult:
        self.released = False
        self._seed = seed
        self._episode_id = episode_id
        self._episode_generation = episode_generation
        self._step = 0
        self._tick = 0
        self._virtual_game_time = 0.0
        self._score = 0.0
        self._terminated = False
        self._truncated = False
        self._termination_reason = None
        self._last_image = _seed_image(seed)
        return self._make_result(score_delta=0.0)

    def actions(self) -> ActionCatalog:
        return self._catalog()

    async def step(self, action_id: str) -> StepResult:
        if self._terminated or self._truncated:
            raise RuntimeError("episode already finished")
        ids = set(self._action_ids)
        if action_id not in ids:
            raise ValueError(f"unknown action_id: {action_id!r}")
        old_score = self._score
        self._step += 1
        self._tick += 3
        self._virtual_game_time += 0.1
        if action_id == "advance":
            self._score += 1.0
        elif action_id == "retreat":
            pass
        # wait: unchanged
        self._last_image = _seed_image(self._seed + self._step)
        score_delta = self._score - old_score
        if self._step >= self._max_steps:
            self._truncated = True
            self._termination_reason = "horizon"
        return self._make_result(score_delta=score_delta)

    async def close(self) -> None:
        return None

    def fingerprint(self) -> dict:
        return {"kind": "memory", "revision": "memory-v1"}

    def release_all(self) -> None:
        self.released = True
