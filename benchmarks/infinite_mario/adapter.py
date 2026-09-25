"""Infinite Mario environment adapter (benchmark-only)."""

from __future__ import annotations

import io
import time
from hashlib import sha256
from pathlib import Path
from typing import Any

from PIL import Image

from loop.adaptive.candidates import ActionCatalog, CandidateAction
from loop.adaptive.environment import StepResult

CATALOG_REVISION = "mario-7"
ENV_REVISION = "infinite-mario-v1"
GAME_ID = "mario"


def _frame_digest(frame_bytes: bytes) -> str:
    return sha256(frame_bytes).hexdigest()


def _bytes_to_image(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data)).convert("RGB")


def _termination_reason(obs: dict, info: dict) -> str | None:
    if not obs.get("done"):
        return None
    if obs.get("truncated"):
        return "truncated"
    if info.get("won"):
        return "win"
    if info.get("dead"):
        return "death"
    return obs.get("reason") or "done"


def _result_from_obs(
    obs: dict,
    *,
    episode_id: str,
    episode_generation: int,
    catalog_revision: str,
    env_revision: str,
    prev_score: float,
    dispatch_ts: float,
    on_reset: bool,
    capture_ts: float | None = None,
) -> StepResult:
    info = dict(obs.get("info") or {})
    frame_bytes = obs.get("frame") or b""
    if isinstance(frame_bytes, str):
        frame_bytes = b""
    img = _bytes_to_image(frame_bytes) if frame_bytes else Image.new("RGB", (448, 336), (0, 0, 0))
    digest = _frame_digest(frame_bytes) if frame_bytes else ""
    step_n = int(obs.get("steps") or 0)
    tick = int(info.get("ticks") or 0)
    virtual_game_time = tick * (1.0 / 30.0) if tick else step_n * 0.1
    score = float(obs.get("score") or 0.0)
    if on_reset:
        score_delta = 0.0
        reward = 0.0
    else:
        score_delta = score - prev_score
        reward = score_delta
    terminated = bool(obs.get("done"))
    truncated = bool(obs.get("truncated"))
    errors = tuple(str(e) for e in (obs.get("errors") or []))
    if capture_ts is None:
        capture_ts = time.monotonic()
    return StepResult(
        observation=img,
        frame_digest=digest,
        episode_id=episode_id,
        episode_generation=episode_generation,
        step=step_n,
        tick=tick,
        virtual_game_time=virtual_game_time,
        score=score,
        score_delta=score_delta,
        reward=reward,
        terminated=terminated,
        truncated=truncated,
        termination_reason=_termination_reason(obs, info),
        environment_revision=env_revision,
        action_catalog_revision=catalog_revision,
        errors=errors,
        privileged_info=info,
        capture_ts=capture_ts,
        dispatch_ts=dispatch_ts,
    )


class MarioEnvironment:
    def __init__(self, cache: Path, *, headless: bool = True) -> None:
        self._cache = Path(cache)
        self._headless = headless
        self._pw = None
        self._browser = None
        self._context = None
        self._server = None
        self._page: Any = None
        self._spec: dict | None = None
        self._step_frames: int = 6
        self._action_by_id: dict[str, int] = {}
        self._action_meta: list[dict] = []
        self._episode_id = ""
        self._episode_generation = 0
        self._prev_score = 0.0
        self._started = False
        self._need_release = False

    async def start(self) -> None:
        if self._started:
            return
        from benchmarks.test_games import by_name

        upstream = by_name("Infinite Mario").repo
        raise FileNotFoundError(
            f"no bundled Mario page server. Upstream: {upstream}"
        )

    def actions(self) -> ActionCatalog:
        candidates: list[CandidateAction] = []
        self._action_by_id.clear()
        for i, meta in enumerate(self._action_meta):
            name = str(meta.get("name") or meta.get("index"))
            self._action_by_id[name] = int(meta.get("index", i))
            candidates.append(
                CandidateAction(
                    action_id=name,
                    command=str(self._action_by_id[name]),
                    description=str(meta.get("description") or name),
                    suppress_no_change=False,
                )
            )
        if not candidates:
            names = (
                "noop",
                "left",
                "right",
                "jump",
                "right jump",
                "right run",
                "right run jump",
            )
            for i, name in enumerate(names):
                self._action_by_id[name] = i
                candidates.append(
                    CandidateAction(
                        action_id=name,
                        command=str(i),
                        description=name,
                        suppress_no_change=False,
                    )
                )
        return ActionCatalog(revision=CATALOG_REVISION, candidates=tuple(candidates))

    async def reset(
        self, seed: int, episode_id: str, episode_generation: int
    ) -> StepResult:
        if not self._started:
            await self.start()
        assert self._page is not None
        await self._apply_release()
        dispatch_ts = time.monotonic()
        obs = await self._page.reset(seed)
        capture_ts = time.monotonic()
        self._action_meta = list(await self._page.page.evaluate("pj.actions"))
        self._episode_id = episode_id
        self._episode_generation = episode_generation
        self._prev_score = float(obs.get("score") or 0.0)
        return _result_from_obs(
            obs,
            episode_id=episode_id,
            episode_generation=episode_generation,
            catalog_revision=CATALOG_REVISION,
            env_revision=ENV_REVISION,
            prev_score=self._prev_score,
            dispatch_ts=dispatch_ts,
            on_reset=True,
            capture_ts=capture_ts,
        )

    async def step(self, action_id: str) -> StepResult:
        if self._page is None:
            raise RuntimeError("environment not started")
        if action_id not in self._action_by_id:
            catalog = self.actions()
            ids = {c.action_id for c in catalog.candidates}
            if action_id not in ids:
                raise ValueError(f"unknown action_id: {action_id!r}")
            self._action_by_id[action_id] = int(
                next(c.command for c in catalog.candidates if c.action_id == action_id)
            )
        index = self._action_by_id[action_id]
        await self._apply_release()
        dispatch_ts = time.monotonic()
        old = self._prev_score
        obs = await self._page.step(index, self._step_frames)
        capture_ts = time.monotonic()
        self._prev_score = float(obs.get("score") or 0.0)
        return _result_from_obs(
            obs,
            episode_id=self._episode_id,
            episode_generation=self._episode_generation,
            catalog_revision=CATALOG_REVISION,
            env_revision=ENV_REVISION,
            prev_score=old,
            dispatch_ts=dispatch_ts,
            on_reset=False,
            capture_ts=capture_ts,
        )

    def release_all(self) -> None:
        self._need_release = True

    async def _apply_release(self) -> None:
        if not self._need_release:
            return
        self._need_release = False
        page = getattr(self._page, "page", None)
        if page is None:
            return
        await page.evaluate("pj.releaseAll()")

    def fingerprint(self) -> dict:
        fp: dict[str, Any] = {
            "upstream_repo": "https://github.com/robertkleffner/mariohtml5",
            "environment_revision": ENV_REVISION,
            "action_catalog_revision": CATALOG_REVISION,
            "viewport": (self._spec or {}).get("viewport"),
            "step_frames": self._step_frames,
            "jpeg_long_side": 448,
            "game_id": GAME_ID,
        }
        try:
            import playwright

            fp["playwright_version"] = getattr(playwright, "__version__", None)
        except ImportError:
            fp["playwright_version"] = None
        if self._browser is not None:
            fp["chromium_revision"] = getattr(self._browser, "version", None)
        hook = self._cache / "games" / "mario" / "pj_hook.js"
        if hook.is_file():
            fp["patch_digest"] = sha256(hook.read_bytes()).hexdigest()
        upstream = self._cache / "games" / "mario" / "UPSTREAM"
        if upstream.is_file():
            fp["upstream_from_UPSTREAM_file"] = upstream.read_text(encoding="utf-8").strip()
        record = self._cache / "FETCH_RECORD.json"
        if record.is_file():
            fp["fetch_record"] = record.name
        return fp

    async def close(self) -> None:
        if self._page is not None:
            await self._page.close()
            self._page = None
        if self._context is not None:
            await self._context.close()
            self._context = None
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._pw is not None:
            await self._pw.stop()
            self._pw = None
        if self._server is not None:
            self._server.close()
            self._server = None
        self._started = False
