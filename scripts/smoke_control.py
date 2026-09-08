"""Live OS input smoke: keyboard, mouse, optional virtual gamepad.

Requires a visible target window. Default: spawn the Tk control lab.

    python scripts/smoke_control.py
    python scripts/smoke_control.py --window Tetris
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from avatar.fixtures.control_lab import LAB_TITLE  # noqa: E402
from avatar.harness.executor import OsController  # noqa: E402
from avatar.harness.gamepad import GamepadUnavailable  # noqa: E402
from avatar.harness.primitives import GamepadPrimitive, KeyPrimitive, MousePrimitive  # noqa: E402
from avatar.harness.window import wait_for_window  # noqa: E402


def _countdown(seconds: int) -> None:
    for remaining in range(seconds, 0, -1):
        print(f"  sending input in {remaining}…", flush=True)
        time.sleep(1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Smoke keyboard/mouse/pad against a window")
    parser.add_argument("--window", default=None, help="title substring (default: spawn control lab)")
    parser.add_argument("--no-lab", action="store_true", help="do not spawn the Tk echo window")
    parser.add_argument("--countdown", type=int, default=3)
    parser.add_argument("--echo", type=Path, default=None)
    args = parser.parse_args(argv)

    lab_proc: subprocess.Popen | None = None
    echo_path = args.echo or (ROOT / "runs" / "control_lab_echo.json")
    title = args.window
    if title is None and not args.no_lab:
        title = LAB_TITLE
        echo_path.parent.mkdir(parents=True, exist_ok=True)
        lab_proc = subprocess.Popen(
            [sys.executable, "-m", "avatar.fixtures.control_lab", str(echo_path), "25"],
            cwd=str(ROOT),
        )
    if title is None:
        parser.error("pass --window TITLE or omit it to spawn the control lab")

    try:
        print(f"Waiting for window matching {title!r}…", flush=True)
        wait_for_window(title, timeout_s=20)
        _countdown(max(0, args.countdown))
        with OsController(title, enable_gamepad=True) as ctl:
            ctl.focus()
            print(f"Focused {ctl.target.title!r} client={ctl.target.width}x{ctl.target.height}", flush=True)
            print("Keyboard: ArrowLeft, ArrowRight, ArrowUp, ArrowDown", flush=True)
            for code in ("ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"):
                ctl.execute([KeyPrimitive(code=code, op="tap")])
                time.sleep(0.12)
            print("Mouse: click at client (0.5, 0.5)", flush=True)
            from avatar.harness.window import client_to_screen

            sx, sy = client_to_screen(ctl.target, 0.5, 0.5)
            print(f"  client bbox={ctl.target.bbox()} screen=({sx},{sy})", flush=True)
            ctl.execute([MousePrimitive(op="click", x=0.5, y=0.5)])
            import ctypes
            from ctypes import wintypes

            pt = wintypes.POINT()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
            print(f"  cursor now=({pt.x},{pt.y})", flush=True)
            time.sleep(0.2)
            try:
                print("Gamepad: A button + left stick nudge", flush=True)
                ctl.execute(
                    [
                        GamepadPrimitive(op="button_tap", button="a"),
                        GamepadPrimitive(op="stick", stick="left", x=0.5, y=0.0),
                    ]
                )
                time.sleep(0.2)
                if ctl.gamepad is not None:
                    ctl.gamepad.reset()
                print("Gamepad: ok", flush=True)
            except GamepadUnavailable as exc:
                print(f"Gamepad skipped: {exc}", flush=True)
        time.sleep(0.4)
        if echo_path.exists():
            payload = json.loads(echo_path.read_text(encoding="utf-8"))
            kinds = [row.get("kind") for row in payload]
            print(f"Echo events ({echo_path}): {kinds}", flush=True)
        print("Smoke complete.", flush=True)
        return 0
    finally:
        if lab_proc is not None:
            lab_proc.terminate()
            try:
                lab_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                lab_proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
