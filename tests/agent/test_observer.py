import numpy as np
from PIL import Image

from agent.observer import changed, cut, for_model, snap, scroll, wait_settled


class StubEnv:
    def __init__(self, frames, loop_from=0):
        self.frames, self.index, self.loop_from = frames, 0, loop_from

    def screenshot(self):
        return self.frames[self.index % len(self.frames)]

    def tick(self, frames=1):
        self.index += frames
        if self.index >= len(self.frames):
            self.index = self.loop_from + (self.index - self.loop_from) % (len(self.frames) - self.loop_from)


def texture():
    rng = np.random.default_rng(4)
    tile = rng.integers(0, 80, (16, 16, 3), dtype=np.uint8)
    image = np.tile(tile, (4, 4, 1))
    image[8:13, 7:11] = (255, 40, 20)
    image[35:39, 44:50] = (20, 240, 60)
    image[52:56, 18:23] = (30, 50, 255)
    return Image.fromarray(image)


def shifted(image, dy, dx):
    return Image.fromarray(np.roll(np.asarray(image), (dy, dx), axis=(0, 1)))


def test_scroll_finds_horizontal_and_vertical_frame_shifts():
    image = texture()
    assert scroll(image, shifted(image, 0, 2)) == (2, 0)
    assert scroll(image, shifted(image, -2, 0)) == (0, -2)


def test_scroll_and_cut_handle_uniform_and_consistent_motion():
    flat = Image.new("RGB", (32, 32))
    image = texture()
    moved = shifted(image, 0, -2)
    assert scroll(flat, flat) is None
    assert not cut(image, moved)
    assert cut(image, moved, blank_seen=True)


def test_summed_scroll_tracks_a_two_pixel_move():
    image, total = texture(), 0
    for step in range(8):
        before = shifted(image, 0, 2 * step)
        after = shifted(image, 0, 2 * (step + 1))
        total += scroll(before, after)[0]
    assert total == 16


def test_wait_settled_accepts_repeating_animation_and_records_blank():
    image = texture()
    blank = Image.new("RGB", image.size)
    frames = [blank, image, shifted(image, 1, 0), shifted(image, 2, 0)]
    result = wait_settled(StubEnv(frames, loop_from=1), quiet=3, max_frames=20)
    assert result.settled and result.blank_seen
    assert not np.all(np.asarray(result.frame) == 0)


def test_wait_settled_does_not_settle_while_text_keeps_gaining_pixels():
    image = texture()
    frames = [np.asarray(image).copy() for _ in range(6)]
    for index, frame in enumerate(frames):
        frame[0, :index + 1] = (255, 255, 255)
    result = wait_settled(StubEnv([Image.fromarray(frame) for frame in frames]), quiet=3, max_frames=5)
    assert not result.settled and result.waited == 5


def test_changed_model_scaling_and_tile_snap():
    image = texture()
    assert changed(image, image.copy()) == 0
    assert for_model(image, 80).size == (80, 80)
    assert for_model(Image.new("RGB", (2, 640))).size == (2, 640)
    assert snap([0.13, 0.13], (64, 64), 16) == [0.125, 0.125]
