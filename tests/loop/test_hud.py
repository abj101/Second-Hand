from __future__ import annotations

import time
from pathlib import Path

from PIL import Image

from loop.fixtures.elden import load_hud_spec
from loop.fixtures.elden_hud.synth import HUD_DIR, load_labels, paint_stamina, write_labeled_set
from loop.fixtures.fake import FAIL_CROP, HEALTH_CROP, paint_fail, paint_health
from loop.reward.hud import hud_visible, is_black_frame, score_hud, wait_for_hud


def _write_crop(path: Path, image: Image.Image) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def test_health_and_fail_on_synthetic(tmp_path: Path) -> None:
    hud = load_hud_spec()
    base = Image.new("RGB", (200, 100), (12, 12, 16))
    healthy = paint_health(base, 1.0)
    low = paint_health(base, 0.25)
    dead = paint_fail(paint_health(base, 0.0))
    hud_dir = Path(__file__).resolve().parents[2] / "loop" / "fixtures" / "elden_hud"
    _write_crop(hud_dir / "healthy.png", healthy)
    _write_crop(hud_dir / "fail.png", dead)

    h = score_hud(healthy, hud)
    l = score_hud(low, hud)
    d = score_hud(dead, hud)
    assert h["health_frac"] is not None and h["health_frac"] > 0.8
    assert l["health_frac"] is not None and l["health_frac"] < h["health_frac"]
    assert d["fail_screen"] is True
    assert HEALTH_CROP[2] > 0 and FAIL_CROP[2] > 0


def test_labels_jsonl_health_within_005() -> None:
    write_labeled_set(HUD_DIR)
    hud = load_hud_spec()
    labels = load_labels()
    assert len(labels) >= 20
    kinds = {row.get("kind") for row in labels}
    assert {"half-bar", "quarter", "near-death", "fail-overlay", "cutscene"} <= kinds
    for label in labels:
        image = Image.open(HUD_DIR / label["file"]).convert("RGB")
        scored = score_hud(image, hud)
        h_l, h_s = label.get("health_frac"), scored.get("health_frac")
        if h_l is None:
            assert h_s is None
        else:
            assert h_s is not None
            assert abs(float(h_s) - float(h_l)) < 0.05
        s_l, s_s = label.get("stamina_frac"), scored.get("stamina_frac")
        if s_l is None:
            assert s_s is None
        else:
            assert s_s is not None
            assert abs(float(s_s) - float(s_l)) < 0.05
        assert bool(scored.get("fail_screen")) is bool(label.get("fail_screen"))
        if label.get("runes"):
            assert scored.get("runes") == label["runes"]


def test_black_frame_gate() -> None:
    black = Image.new("RGB", (64, 64), (0, 0, 0))
    assert is_black_frame(black)
    lit = Image.new("RGB", (64, 64), (40, 40, 40))
    assert not is_black_frame(lit)


def test_hud_visible_hidden_until_bars() -> None:
    hud = load_hud_spec()
    dark = Image.new("RGB", (200, 100), (12, 12, 16))
    assert hud_visible(score_hud(dark, hud)) is False
    bars = paint_stamina(paint_health(dark.copy(), 0.9), 0.8)
    assert hud_visible(score_hud(bars, hud)) is True
    assert hud_visible({"health_frac": 0.0, "stamina_frac": None, "fail_screen": False}) is False
    assert hud_visible({"health_frac": 0.0, "stamina_frac": 0.0, "fail_screen": False}) is True
    assert hud_visible({"fail_screen": True}) is True


def test_wait_for_hud_retries_until_bars() -> None:
    hud = load_hud_spec()
    dark = Image.new("RGB", (200, 100), (12, 12, 16))
    bars = paint_stamina(paint_health(dark.copy(), 0.9), 0.8)
    frames = [dark, dark, bars]

    def grab() -> Image.Image:
        return frames.pop(0) if frames else bars

    result = wait_for_hud(grab, lambda image: score_hud(image, hud), timeout_s=2.0, poll_s=0.01)
    assert result.visible
    assert result.black is False
    assert result.attempts == 3


def test_wait_for_hud_black_stops_immediately() -> None:
    black = Image.new("RGB", (64, 64), (0, 0, 0))
    calls = {"n": 0}

    def grab() -> Image.Image:
        calls["n"] += 1
        return black

    result = wait_for_hud(
        grab,
        lambda image: score_hud(image, load_hud_spec()),
        timeout_s=5.0,
        poll_s=0.01,
    )
    assert result.black
    assert result.visible is False
    assert result.attempts == 1
    assert calls["n"] == 1


def test_hud_poller_does_not_block() -> None:
    from loop.reward.hud import HudPoller

    hud = load_hud_spec()
    dark = Image.new("RGB", (200, 100), (12, 12, 16))
    bars = paint_stamina(paint_health(dark.copy(), 0.9), 0.8)
    frames = [dark, bars]

    def grab() -> Image.Image:
        return frames[0] if frames else bars

    poller = HudPoller(grab, lambda image: score_hud(image, hud), interval_s=0.05)
    poller.start()
    try:
        deadline = time.time() + 2.0
        while time.time() < deadline and not poller.visible:
            if frames:
                frames.pop(0)
            time.sleep(0.05)
        assert poller.visible
        metrics, visible = poller.snapshot()
        assert visible
        assert metrics.get("health_frac") is not None
    finally:
        poller.stop()
