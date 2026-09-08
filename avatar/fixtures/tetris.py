from __future__ import annotations

from io import BytesIO
from typing import Any

from PIL import Image
from playwright.sync_api import Page

from avatar.harness.capture import encode_image


class TetrisSession:
    def __init__(self, page: Page) -> None:
        self.page = page

    def wait_ready(self) -> None:
        self.page.wait_for_function("() => window.__AVATAR_ORACLE__")

    def oracle(self) -> dict[str, Any]:
        return self.page.evaluate("() => window.__AVATAR_ORACLE__") or {}

    def dispatch(self, action_type: str) -> dict[str, Any]:
        return self.page.evaluate(
            """(t) => { window.__AVATAR_DISPATCH__({ type: t }); return window.__AVATAR_ORACLE__; }""",
            action_type,
        )

    def start(self) -> dict[str, Any]:
        """Press start. With ?pause_and_decide=1 the game auto-pauses after spawn."""
        self.wait_ready()
        self.dispatch("start")
        return self.oracle()

    start_paused = start

    def screenshot_board(self) -> Image.Image:
        png = self.page.locator("#game-canvas").screenshot()
        return Image.open(BytesIO(png)).convert("RGB")

    def encode_board(self, longest_side: int | str = 768, quality: int = 85):
        return encode_image(
            self.screenshot_board(),
            longest_side=longest_side,  # type: ignore[arg-type]
            jpeg_quality=quality,
        )
