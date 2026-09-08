from __future__ import annotations

import argparse
from pathlib import Path

from avatar.eval.logger import new_run_dir
from avatar.fixtures.g2048 import open_2048
from avatar.harness.primitives import KeyPrimitive
from avatar.harness.tables import resolve


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Record a short 2048 Playwright demo (webm)")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    run_dir = new_run_dir("demo", "2048")
    out = args.out or (run_dir / "avatar_phase1_demo.webm")

    from playwright.sync_api import sync_playwright
    from avatar.fixtures.server import serve_2048

    server, url = serve_2048()
    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True)
    context = browser.new_context(
        viewport={"width": 900, "height": 1100},
        record_video_dir=str(run_dir / "video"),
        record_video_size={"width": 900, "height": 1100},
    )
    page = context.new_page()
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_function("() => window.__AVATAR_GAME__")
    page.evaluate("() => window.__AVATAR_GAME__.loadAvatarFixture('mid')")
    page.locator(".game-container").click()
    for name in ("swipe_left", "swipe_up", "swipe_right", "swipe_down"):
        prim = resolve("swipe", name).primitives[0]
        assert isinstance(prim, KeyPrimitive)
        page.keyboard.press(prim.code)
        page.wait_for_timeout(400)
    page.wait_for_timeout(800)
    context.close()
    browser.close()
    pw.stop()
    server.shutdown()

    videos = list((run_dir / "video").glob("*.webm"))
    if videos:
        target = Path(out)
        target.parent.mkdir(parents=True, exist_ok=True)
        videos[0].replace(target)
        print(f"Demo video: {target}")
    else:
        print(f"Playwright recorded under {run_dir / 'video'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
