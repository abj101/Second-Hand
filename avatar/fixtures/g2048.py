from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image
from playwright.sync_api import Page, sync_playwright

from avatar.fixtures.server import serve_2048
from avatar.harness.capture import EncodedFrame, encode_image


FIXTURE_STATES = ("start", "mid", "gameover")
CAPTURE_SWEEP = (
    ("native", 85),
    ("native", 70),
    (768, 85),
    (768, 70),
    (512, 85),
    (512, 70),
)


HUD_FILL = (250, 248, 239)  # 2048 page background


def compose_hud_and_board(hud: Image.Image, board: Image.Image) -> Image.Image:
    """Stack heading (title + Score/Best) on the grid without shrinking the board crop."""
    hud = hud.convert("RGB")
    board = board.convert("RGB")
    width = max(hud.width, board.width)

    def _fit(image: Image.Image) -> Image.Image:
        if image.width == width:
            return image
        height = max(1, round(image.height * width / image.width))
        return image.resize((width, height), Image.Resampling.LANCZOS)

    hud = _fit(hud)
    board = _fit(board)
    out = Image.new("RGB", (width, hud.height + board.height), HUD_FILL)
    out.paste(hud, (0, 0))
    out.paste(board, (0, hud.height))
    return out


class Game2048Session:
    def __init__(self, page: Page) -> None:
        self.page = page

    def load_state(self, name: str) -> dict[str, Any]:
        self.page.wait_for_function("() => window.__AVATAR_GAME__")
        oracle = self.page.evaluate(
            """(name) => window.__AVATAR_GAME__.loadAvatarFixture(name)""",
            name,
        )
        self.page.wait_for_function(
            """() => document.querySelectorAll('.tile').length > 0"""
        )
        if name == "gameover":
            self.page.wait_for_selector(".game-message.game-over, .game-message.game-won", timeout=3000)
        self.page.wait_for_timeout(400)
        return oracle

    def oracle(self) -> dict[str, Any]:
        return self.page.evaluate("() => window.__AVATAR_ORACLE__") or {}

    def screenshot_board(self) -> Image.Image:
        hud = Image.open(BytesIO(self.page.locator(".heading").screenshot())).convert("RGB")
        board = Image.open(BytesIO(self.page.locator(".game-container").screenshot())).convert("RGB")
        return compose_hud_and_board(hud, board)

    def encode_board(self, longest_side: int | str, quality: int) -> EncodedFrame:
        return encode_image(
            self.screenshot_board(),
            longest_side=longest_side,  # type: ignore[arg-type]
            jpeg_quality=quality,
        )


def open_2048(headless: bool = True) -> tuple[Game2048Session, Any]:
    server, url = serve_2048()
    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=headless)
    page = browser.new_page(
        viewport={"width": 900, "height": 1100},
        device_scale_factor=2,
    )
    page.goto(url, wait_until="domcontentloaded")
    page.evaluate("() => localStorage.clear()")
    page.reload(wait_until="domcontentloaded")
    session = Game2048Session(page)

    def close() -> None:
        browser.close()
        pw.stop()
        server.shutdown()

    return session, close


def capture_sweep(session: Game2048Session, out_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for state in FIXTURE_STATES:
        oracle = session.load_state(state)
        for longest, quality in CAPTURE_SWEEP:
            frame = session.encode_board(longest, quality)
            name = f"{state}_ls{longest}_q{quality}.jpg"
            path = out_dir / name
            path.write_bytes(frame.data)
            rows.append(
                {
                    "state": state,
                    "longest_side": longest,
                    "jpeg_quality": quality,
                    "path": str(path),
                    "bytes": frame.nbytes,
                    "width": frame.width,
                    "height": frame.height,
                    "oracle": oracle,
                    "frame": frame,
                }
            )
    return rows
