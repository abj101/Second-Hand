from __future__ import annotations

from PIL import Image

from loop.os.profile import wait_until_quiet


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def sleep(self, dt: float) -> None:
        self.t += dt


def test_wait_until_quiet_already_still() -> None:
    still = Image.new("L", (64, 64), 12)
    clock = _Clock()
    result = wait_until_quiet(
        lambda: still,
        threshold=0.02,
        timeout_ms=400,
        poll_ms=50,
        quiet_ms=150,
        clock=clock,
        sleep=clock.sleep,
    )
    assert result.changed is False
    assert result.elapsed_ms >= 150
    assert result.elapsed_ms < 400
    assert result.polls >= 1


def test_wait_until_quiet_motion_then_still() -> None:
    dark = Image.new("L", (64, 64), 0)
    bright = Image.new("L", (64, 64), 255)
    frames = [dark, bright, bright, bright, bright, bright, bright]
    clock = _Clock()
    result = wait_until_quiet(
        lambda: frames.pop(0) if frames else bright,
        threshold=0.02,
        timeout_ms=400,
        poll_ms=50,
        quiet_ms=100,
        clock=clock,
        sleep=clock.sleep,
    )
    assert result.changed is True
    assert result.elapsed_ms < 400
    assert result.polls >= 2


def test_wait_until_quiet_timeout_keeps_moving() -> None:
    dark = Image.new("L", (64, 64), 0)
    bright = Image.new("L", (64, 64), 255)
    n = 0

    def grab() -> Image.Image:
        nonlocal n
        n += 1
        return dark if n % 2 else bright

    clock = _Clock()
    result = wait_until_quiet(
        grab,
        threshold=0.02,
        timeout_ms=120,
        poll_ms=40,
        quiet_ms=80,
        clock=clock,
        sleep=clock.sleep,
    )
    assert result.changed is True
    assert result.elapsed_ms >= 120
