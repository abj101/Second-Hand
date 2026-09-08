from __future__ import annotations

from loop.os.mouse import screen_to_absolute
from loop.os.window import client_frac_to_px


def test_screen_to_absolute_uses_65536() -> None:
    assert screen_to_absolute(0, 0, (0, 0, 1920, 1080)) == (0, 0)
    abs_x, abs_y = screen_to_absolute(960, 540, (0, 0, 1920, 1080))
    assert abs_x == round(960 * 65536 / 1920)
    assert abs_y == round(540 * 65536 / 1080)
    last_x, _ = screen_to_absolute(1919, 0, (0, 0, 1920, 1080))
    assert last_x == min(65535, round(1919 * 65536 / 1920))
    offset_x, offset_y = screen_to_absolute(100, 200, (-1920, 0, 3840, 1080))
    assert offset_x == round((100 - (-1920)) * 65536 / 3840)
    assert offset_y == round(200 * 65536 / 1080)


def test_client_frac_centers_on_pixel() -> None:
    assert client_frac_to_px(0.0, 0.0, 1920, 1080) == (0, 0)
    assert client_frac_to_px(1.0, 1.0, 1920, 1080) == (1919, 1079)
    assert client_frac_to_px(0.5, 0.5, 1920, 1080) == (960, 540)
    assert client_frac_to_px(-1.0, 2.0, 100, 50) == (0, 49)


def test_menu_click_timing_is_opt_in() -> None:
    from loop.os.aim import is_menu_label
    from loop.os.mouse import CLICK_S, HOVER_S, MENU_HOLD_S, MENU_HOVER_S
    from loop.os.primitives import mouse_click

    assert is_menu_label("Confirm")
    assert is_menu_label("Singleplayer")
    assert not is_menu_label("Strike")
    combat = mouse_click(0.40, 0.85)
    assert combat.hover_s is None
    assert combat.hold_s is None
    menu = mouse_click(0.94, 0.84, hover_s=MENU_HOVER_S, hold_s=MENU_HOLD_S)
    assert menu.hover_s == MENU_HOVER_S
    assert menu.hold_s == MENU_HOLD_S
    assert MENU_HOVER_S > HOVER_S
    assert MENU_HOLD_S > CLICK_S


def test_sts2_hover_releases_any_prior_button_state() -> None:
    from loop.fixtures.sts2 import Sts2Fixture
    from loop.log.schema import ActionLog

    class Controller:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def release_all(self) -> None:
            self.calls.append("release")

        def execute(self, primitives) -> None:
            assert len(primitives) == 1
            assert primitives[0].op == "move"
            self.calls.append("move")

    controller = Controller()
    fixture = Sts2Fixture(controller=controller)  # type: ignore[arg-type]
    fixture.settle_ms = 0
    fixture.execute_chord(
        ActionLog(mark=2, click_x=.75, click_y=.60, hover=True, duration_ms=1)
    )
    assert controller.calls == ["release", "move"]
