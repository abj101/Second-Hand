import numpy as np
from types import SimpleNamespace
from PIL import Image

from agent.observer import _residual, changed, cut, for_model, snap, scroll, wait_settled


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


class LiveStub:
    def __init__(self, frames):
        self.frames, self.index = frames, 0
    def _snapshot(self, index):
        return SimpleNamespace(generation="g", frame_seq=index, captured_monotonic=1.0, image=self.frames[index])
    def snapshot(self): return self._snapshot(self.index)
    def wait_frame(self, after, *, timeout):
        if self.index + 1 >= len(self.frames): raise TimeoutError
        self.index += 1
        return self._snapshot(self.index)


def test_live_wait_settled_counts_distinct_frames_and_returns_stamp():
    image = texture()
    result = wait_settled(LiveStub([image.copy() for _ in range(5)]), quiet=3, max_frames=4)
    assert result.settled and result.waited == 3
    assert result.frame_stamp == {"generation": "g", "frame_seq": 3, "captured_monotonic": 1.0}


def test_live_wait_settled_is_bounded_on_animation():
    frames = [Image.new("RGB", (32, 32), (i * 40, 20, 30)) for i in range(5)]
    result = wait_settled(LiveStub(frames), quiet=3, max_frames=3)
    assert not result.settled and result.waited == 3
    assert result.frame_stamp["frame_seq"] == 3


def test_live_wait_settled_restarts_after_generation_change():
    class FrameGenerationChanged(Exception): pass
    image, blank = texture(), Image.new("RGB", (64, 64))
    class Live:
        def __init__(self): self.index = 0
        def snapshot(self):
            generation, frame, seq = ("old", image, 8) if self.index == 0 else ("new", blank if self.index == 1 else image, self.index - 1)
            return SimpleNamespace(generation=generation, frame_seq=seq, captured_monotonic=2.0, image=frame)
        def wait_frame(self, after, *, timeout):
            if after.generation == "old":
                self.index = 1
                raise FrameGenerationChanged
            self.index += 1
            return self.snapshot()

    result = wait_settled(Live(), quiet=2, max_frames=8)
    assert result.settled and result.frame_stamp["generation"] == "new"
    assert result.frame_stamp["frame_seq"] == 3


class CyclingEnv:
    def __init__(self):
        self.index = 0
        rng = np.random.default_rng(4)
        self.frames = [Image.fromarray(rng.integers(0, 256, (32, 32, 3), dtype=np.uint8)) for _ in range(8)]

    def image(self):
        return self.frames[self.index // 21 % 8]


class SteppedCyclingEnv(CyclingEnv):
    def screenshot(self): return self.image()
    def tick(self): self.index += 1


class LiveCyclingEnv(CyclingEnv):
    def snapshot(self):
        return SimpleNamespace(generation="g", frame_seq=self.index, captured_monotonic=1.0, image=self.image())

    def wait_frame(self, after, *, timeout):
        self.index += 1
        return self.snapshot()


def test_wait_settled_keeps_long_cycle_memory_on_both_paths():
    for env_type in (SteppedCyclingEnv, LiveCyclingEnv):
        assert wait_settled(env_type(), max_frames=300).settled
        assert not wait_settled(env_type(), max_frames=300, memory=24).settled


def test_changed_model_scaling_and_tile_snap():
    image = texture()
    assert changed(image, image.copy()) == 0
    assert for_model(image, 80).size == (80, 80)
    assert for_model(Image.new("RGB", (2, 640))).size == (2, 640)
    assert snap([0.13, 0.13], (64, 64), 16) == [0.125, 0.125]


def test_changed_excludes_masked_pixel_boxes():
    a = Image.new("RGB", (10, 10)); b = a.copy()
    b.paste((255, 255, 255), (2, 2, 4, 4))

    assert changed(a, b) == .04
    assert changed(a, b, mask=((1, 1, 5, 5),)) == 0


def test_residual_finds_compact_screen_anchored_motion():
    tile = np.random.default_rng(4).integers(0, 160, (16, 16, 3), dtype=np.uint8)
    image = np.tile(tile, (9, 10, 1))[:144, :160]
    first = image.copy(); first[68:76, 80:88] = (255, 20, 20)
    moved = np.roll(image, 16, axis=1); moved[68:76, 80:88] = (255, 20, 20)
    box, _tile, _coverage, accepted = _residual([Image.fromarray(first), Image.fromarray(moved)], [(0, 0), (16, 0)])
    assert accepted
    spot = ((box[0] + box[2] + 16) / 320, (box[1] + box[3]) / 288)  # box centre plus the camera shift
    assert abs(spot[0] - .5) <= 8 / 160 and abs(spot[1] - 72 / 144) <= 8 / 144


def test_mostly_white_menu_is_not_blank():
    from PIL import Image
    from agent.observer import _blank
    menu = Image.new("RGB", (160, 144), "white"); menu.paste((0, 0, 0), (0, 0, 64, 18))  # ~95% white, like the NEW GAME menu
    assert not _blank(menu) and _blank(Image.new("RGB", (160, 144), "white"))


def test_cut_when_the_scene_changes_under_an_unchanged_dialogue_box():
    import numpy as np
    rng = np.random.default_rng(3)
    box = rng.integers(0, 255, (56, 160, 3), dtype=np.uint8)  # the same text box in both frames pins the best shift to (0, 0)
    a = Image.fromarray(np.vstack([rng.integers(0, 255, (88, 160, 3), dtype=np.uint8), box]))
    b = Image.fromarray(np.vstack([rng.integers(0, 255, (88, 160, 3), dtype=np.uint8), box]))
    assert scroll(a, b) == (0, 0) and changed(a, b) > .5 and cut(a, b)  # m1_h1h4_live S90->S91: another map behind the professor's dialogue
    assert not cut(a, a)


def test_settle_segments_static_band_motion_at_blank(monkeypatch):
    from agent import observer
    image = np.random.default_rng(9).integers(0, 255, (96, 96, 3), dtype=np.uint8)
    frames = []
    for dx in (0, -16, -32, -48):
        a = np.roll(image, dx, axis=1); a[-24:] = 0; frames.append(Image.fromarray(a))
    blank = Image.new("RGB", (96, 96))
    frames += [blank, frames[0], frames[1], frames[1]]
    shifts = iter([(-16, 0)] * 4)
    monkeypatch.setattr(observer, "scroll", lambda a, b: next(shifts))
    result = wait_settled(StubEnv(frames, loop_from=7), quiet=2)
    assert result.scroll == (-48, 0) and result.last_dir == "RIGHT"
    assert result.after_scroll == (-16, 0) and result.arrival_frame.tobytes() == frames[0].tobytes()
    still = wait_settled(StubEnv([frames[0]]), quiet=1)
    assert still.scroll == still.after_scroll == (0, 0)
