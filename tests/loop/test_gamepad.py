from __future__ import annotations

import pytest

from loop.os.gamepad import gamepad_available, get_gamepad, xinput_connected, xinput_state


def test_xinput_state_index_bounds() -> None:
    assert xinput_state(-1) is None
    assert xinput_state(4) is None
    connected = xinput_connected()
    assert isinstance(connected, list)
    assert all(isinstance(i, int) for i in connected)


def test_gamepad_available_reuses_one_pad() -> None:
    if not gamepad_available():
        pytest.skip("ViGEm/vgamepad not available")
    first = get_gamepad()
    second = get_gamepad()
    assert first is not None
    assert first is second
