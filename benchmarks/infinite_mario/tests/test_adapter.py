"""Adapter unit tests (no Chromium by default)."""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path

import io

import pytest
from PIL import Image

from benchmarks.infinite_mario.adapter import (
    CATALOG_REVISION,
    _result_from_obs,
    MarioEnvironment,
)
from loop.adaptive.environment import StepResult

REPO_ROOT = Path(__file__).resolve().parents[3]
ADAPTER = REPO_ROOT / "benchmarks" / "infinite_mario" / "adapter.py"
MANIFEST = REPO_ROOT / "benchmarks" / "infinite_mario" / "source-manifest.json"
PIN = "2d7a0280841e3244a299c9c640efc8cefc46d476"


def _jpeg_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (16, 12), (40, 80, 120)).save(buf, format="JPEG")
    return buf.getvalue()


def _fake_obs(*, score: float = 10.0, steps: int = 1, done: bool = False) -> dict:
    return {
        "score": score,
        "steps": steps,
        "done": done,
        "truncated": False,
        "frame": _jpeg_bytes(),
        "info": {"x": 32.0, "y": 10.0, "onGround": True, "dead": False, "won": False, "ticks": 9},
        "errors": [],
    }


def test_result_from_obs_reset_zeros_reward():
    obs = _fake_obs(score=5.0, steps=0)
    r = _result_from_obs(
        obs,
        episode_id="ep1",
        episode_generation=1,
        catalog_revision=CATALOG_REVISION,
        env_revision="infinite-mario-v1",
        prev_score=0.0,
        dispatch_ts=1.0,
        on_reset=True,
    )
    assert r.score_delta == 0.0
    assert r.reward == 0.0
    assert r.privileged_info["x"] == 32.0
    assert isinstance(r, StepResult)


def test_result_from_obs_uses_supplied_capture_ts():
    obs = _fake_obs(score=5.0, steps=0)
    r = _result_from_obs(
        obs,
        episode_id="ep1",
        episode_generation=1,
        catalog_revision=CATALOG_REVISION,
        env_revision="infinite-mario-v1",
        prev_score=0.0,
        dispatch_ts=1.0,
        on_reset=True,
        capture_ts=9.25,
    )
    assert r.capture_ts == 9.25
    assert r.dispatch_ts == 1.0


def test_result_from_obs_step_score_delta():
    obs = _fake_obs(score=12.0, steps=2)
    r = _result_from_obs(
        obs,
        episode_id="ep1",
        episode_generation=1,
        catalog_revision=CATALOG_REVISION,
        env_revision="infinite-mario-v1",
        prev_score=10.0,
        dispatch_ts=1.0,
        on_reset=False,
    )
    assert r.score_delta == 2.0
    assert r.reward == 2.0


def test_action_catalog_seven_names():
    env = MarioEnvironment(Path("/nonexistent"), headless=True)
    cat = env.actions()
    assert cat.revision == "mario-7"
    names = [c.action_id for c in cat.candidates]
    assert names == [
        "noop",
        "left",
        "right",
        "jump",
        "right jump",
        "right run",
        "right run jump",
    ]
    assert all(not c.suppress_no_change for c in cat.candidates)


def test_adapter_ast_no_session_or_runtime():
    tree = ast.parse(ADAPTER.read_text(encoding="utf-8"), filename=str(ADAPTER))
    banned = {"loop.adaptive.session", "loop.adaptive.runtime"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name not in banned
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert node.module not in banned
    src = ADAPTER.read_text(encoding="utf-8")
    assert "Session.act" not in src


def test_source_manifest_revision_pin():
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert data["playjev"]["revision"] == PIN


def test_seed_manifests_disjoint():
    root = REPO_ROOT / "benchmarks" / "infinite_mario" / "seed-manifests"
    sets = {}
    for name in ("train", "val", "test"):
        sets[name] = set(json.loads((root / f"{name}.json").read_text())["seeds"])
    assert sets["train"].isdisjoint(sets["val"])
    assert sets["train"].isdisjoint(sets["test"])
    assert sets["val"].isdisjoint(sets["test"])


@pytest.mark.benchmark
def test_live_mario_smoke():
    if os.environ.get("AVATAR_RUN_MARIO") != "1":
        pytest.skip("set AVATAR_RUN_MARIO=1 for live Playwright smoke")
    pytest.importorskip("playwright")
    cache = REPO_ROOT / ".cache" / "benchmarks" / "playjev" / PIN
    if not cache.is_dir():
        pytest.skip("PlayJev cache missing; run fetch-playjev")

    async def _run() -> None:
        env = MarioEnvironment(cache)
        try:
            await env.start()
            r = await env.reset(1, "live", 1)
            assert r.frame_digest
            r2 = await env.step("noop")
            assert r2.step >= r.step
        finally:
            await env.close()

    import asyncio

    asyncio.run(_run())
