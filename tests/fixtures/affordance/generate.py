"""Frozen frame-pair fixtures for affordance detector regression tests.

Synthetic PNG pairs exercise the generic highlight/dim detector without STS2 rules.
Run: pytest tests/loop/test_affordance_fixtures.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

FIXTURE_DIR = Path(__file__).resolve().parent


def _write_pair(name: str, *, pre_fill: tuple[int, int, int], post_fill: tuple[int, int, int]) -> None:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    size = (320, 240)
    rect = (80, 60, 240, 180)
    for tag, fill in (("pre", pre_fill), ("post", post_fill)):
        img = Image.new("RGB", size, (35, 32, 28))
        draw = ImageDraw.Draw(img)
        draw.rectangle(rect, fill=fill)
        img.save(FIXTURE_DIR / f"{name}_{tag}.png")


def build_all() -> None:
    _write_pair("highlight_target", pre_fill=(55, 55, 55), post_fill=(190, 200, 210))
    _write_pair("dim_hand_card", pre_fill=(170, 165, 160), post_fill=(70, 68, 65))
    _write_pair("unchanged_ui", pre_fill=(100, 100, 100), post_fill=(102, 101, 99))


if __name__ == "__main__":
    build_all()
