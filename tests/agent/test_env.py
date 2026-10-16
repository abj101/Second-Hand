from tests.agent.fakes import GridEnv
from agent.observer import scroll


def test_grid_env_moves_one_tile_and_blocks_at_walls():
    env = GridEnv()
    start = env.player
    before, frames = env.screenshot(), []
    env.press("RIGHT", frames=8, on_frame=frames.append)
    assert env.player == (start[0] + 1, start[1])
    assert any(frame.tobytes() != before.tobytes() for frame in frames)
    shifts = [scroll(a, b) for a, b in zip([before] + frames, frames)]
    assert all(shift is not None for shift in shifts[:8])
    assert sum(shift[0] for shift in shifts[:8] if shift is not None) == -16
    env.player = (14, 8)
    env.press("RIGHT", frames=16)
    assert env.player == (14, 8)


def test_on_frame_runs_for_held_and_post_release_frames():
    env = GridEnv()
    frames = []
    env.press("UP", frames=3, on_frame=frames.append)
    assert len(frames) == 11
    assert len({id(image) for image in frames}) == len(frames)


def test_grid_env_door_and_npc_interactions():
    env = GridEnv()
    env.player = (9, 8)
    frames = []
    env.press("RIGHT", on_frame=frames.append)
    assert env.scene == 1
    assert any(frame.getpixel((40, 40)) == (255, 255, 255) for frame in frames)
    env = GridEnv()
    env.player = (8, 8)
    env.press("A")
    assert env.text_box


def test_pyboy_smoke_round_trip(tmp_path):
    from agent.env_pyboy import PyBoyEnv, find_rom

    try:
        rom = find_rom()
    except FileNotFoundError:
        import pytest

        pytest.skip("No Pokémon Red ROM found")
    env = PyBoyEnv(rom)
    try:
        first = env.screenshot()
        assert first.size == (160, 144) and first.mode == "RGB"
        env.press("A", frames=2)
        second = env.screenshot()
        assert first is not second
        state = tmp_path / "state.state"
        env.save(str(state))
        saved = env.screenshot()
        env.press("RIGHT", frames=3)
        env.load(str(state))
        assert env.screenshot().tobytes() == saved.tobytes()
        class ButtonSpy:
            def __init__(self, pyboy):
                self.pyboy, self.held = pyboy, set()

            def __getattr__(self, name):
                return getattr(self.pyboy, name)

            def button_press(self, button):
                self.held.add(button)
                self.pyboy.button_press(button)

            def button_release(self, button):
                self.pyboy.button_release(button)
                self.held.discard(button)

        seen = []; env.frame_sink = seen.append
        env.tick(5); env.press("B", frames=2)
        assert len(seen) == 5 + 2 + 8
        env.frame_sink = None
        env.pyboy = ButtonSpy(env.pyboy)
        env.press("B", frames=1)
        assert not env.pyboy.held
    finally:
        env.close()
