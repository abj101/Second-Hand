from __future__ import annotations

from loop.fixtures.elden import EldenFixture
from loop.fixtures.fake import FakeFixture
from loop.fixtures.sts2 import Sts2Fixture
from loop.os.executor import OsController
from loop.os.gamepad import gamepad_available
from loop.os.window import find_window

FIXTURE_IDS = ("fake", "elden", "sts2")


def make_fixture(name: str, *, attach: bool = True) -> FakeFixture | EldenFixture | Sts2Fixture:
    if name == "fake":
        return FakeFixture()
    if name == "elden":
        fx = EldenFixture()
        if attach:
            table = "xbox360" if gamepad_available() else "keyboard"
            fx.action_table = table
            fx.device = "pad" if table == "xbox360" else "keyboard"
            if find_window(fx.window_title) is None:
                raise RuntimeError(
                    f"No window matching {fx.window_title!r}. Launch Elden Ring (borderless, offline) first."
                )
            fx.controller = OsController(
                fx.window_title,
                enable_gamepad=table == "xbox360",
            )
        return fx
    if name == "sts2":
        fx = Sts2Fixture()
        if attach:
            if find_window(fx.window_title) is None:
                raise RuntimeError(
                    f"No window matching {fx.window_title!r}. Launch Slay the Spire 2 "
                    "(borderless windowed) and leave it on a playable screen."
                )
            fx.controller = OsController(fx.window_title, enable_gamepad=False)
        return fx
    raise KeyError(f"Unknown fixture {name!r}")
