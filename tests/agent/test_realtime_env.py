from __future__ import annotations

import threading
import time
from concurrent.futures import Future
from types import SimpleNamespace

import pytest
from PIL import Image

import agent.env_pyboy as env_module


class FakePyBoy:
    def __init__(self, *args, **kwargs):
        self.owner = threading.get_ident()
        self.frame_count = 0
        self.held = set()
        self.ticks = []
        self.operations = []
        self.memory = {0x1234: 42, 0x1235: 84}
        self.screen = SimpleNamespace(image=Image.new("RGB", (160, 144), (1, 2, 3)))

    def _op(self, name, *args):
        self.operations.append((threading.get_ident(), name, args))

    def set_emulation_speed(self, speed): self._op("speed", speed)
    def button_press(self, button): self._op("press", button); self.held.add(button)
    def button_release(self, button): self._op("release", button); self.held.discard(button)
    def tick(self, count=1, render=True):
        self._op("tick", count, render)
        for _ in range(count):
            self.frame_count += 1
            self.ticks.append((self.frame_count, frozenset(self.held)))
            self.screen.image = Image.new("RGB", (160, 144), (self.frame_count % 255, 2, 3))
        return True
    def save_state(self, file): self._op("save"); file.write(str(self.frame_count).encode())
    def load_state(self, file): self._op("load"); self.frame_count = int(file.read())
    def stop(self, save=False): self._op("stop", save)


class NoFilter:
    def close(self): pass


@pytest.fixture
def fake_live(monkeypatch):
    instances = []

    def factory(*args, **kwargs):
        instance = FakePyBoy(*args, **kwargs)
        instances.append(instance)
        return instance

    monkeypatch.setattr(env_module, "PyBoy", factory)
    monkeypatch.setattr(env_module, "_install_live_event_filter", lambda: NoFilter())
    host = env_module.LivePyBoyHost("fake.gb")
    return host, instances[0]


def test_owner_pumps_frames_while_worker_callback_is_blocked_and_keeps_action_images(fake_live):
    host, pyboy = fake_live
    entered, release, done = threading.Event(), threading.Event(), threading.Event()
    callbacks = []
    failures = []

    def worker():
        try:
            def callback(image):
                callbacks.append((threading.get_ident(), image.info.copy()))
                if len(callbacks) == 1:
                    entered.set()
                    assert release.wait(2)
            host.env.press("A", 2, callback)
        except BaseException as exc:
            failures.append(exc)
        finally:
            done.set()

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    def publish(frame):
        if frame.frame_seq == 30:
            assert entered.wait(1)
            host.stop()
    host.run(publish)
    assert len(pyboy.ticks) == 30
    assert pyboy.ticks[:10] == [(i, frozenset({"a"}) if i <= 2 else frozenset()) for i in range(1, 11)]
    assert not pyboy.held
    release.set()
    assert done.wait(1)
    thread.join(1)
    assert not failures
    assert len(callbacks) == 10
    assert [row[1]["frame_seq"] for row in callbacks] == list(range(1, 11))
    assert all(row[1]["generation"] == host._generation for row in callbacks)
    assert all(row[0] != host._owner_thread for row in callbacks)
    assert {tid for tid, *_ in pyboy.operations} == {host._owner_thread}


def test_callback_error_does_not_leave_input_held_or_stop_owner(fake_live):
    host, pyboy = fake_live
    failures = []
    done = threading.Event()

    def worker():
        try:
            host.env.press("B", 3, lambda image: (_ for _ in ()).throw(ValueError("callback failed")))
        except BaseException as exc:
            failures.append(exc)
        finally: done.set()

    threading.Thread(target=worker, daemon=True).start()
    host.run(lambda frame: host.stop() if frame.frame_seq == 14 else None)
    assert done.wait(1)
    assert isinstance(failures[0], ValueError)
    assert not pyboy.held
    assert pyboy.ticks[0:3] == [(i, frozenset({"b"})) for i in range(1, 4)]
    assert pyboy.ticks[3:11] == [(i, frozenset()) for i in range(4, 12)]


def test_live_snapshot_history_ram_and_saved_state_are_owned_and_atomic(fake_live):
    host, _ = fake_live
    source = host.env.snapshot()
    original = source.image.tobytes()
    source.image.putpixel((0, 0), (255, 0, 0))
    assert host.env.snapshot().image.tobytes() == original
    result = {}
    ready = threading.Event()

    def worker():
        while host.env.snapshot().frame_seq < 2:
            time.sleep(0.001)
        result["ram"] = host.env.read_ram((0x1234, 0x1235))
        result["state"] = host.env.save_snapshot()
        ready.set()

    threading.Thread(target=worker, daemon=True).start()
    host.run(lambda frame: host.stop() if ready.is_set() and frame.frame_seq >= 4 else None)
    ram, state = result["ram"], result["state"]
    assert ram.values == {0x1234: 42, 0x1235: 84}
    assert ram.frame.generation == state.frame.generation
    assert ram.frame.frame_seq < state.frame.frame_seq
    assert state.state == str(state.frame.frame_seq).encode()
    assert all(tid == host._owner_thread for tid, *_ in host._pyboy.operations)


def test_history_reports_overflow_and_copies_frames(fake_live):
    host, _ = fake_live
    first = host.env.snapshot()
    result = {}
    def publish(frame):
        if frame.frame_seq == 125:
            result["batch"] = host.env.frames_after(first)
            original = result["batch"].frames[0].image.tobytes()
            result["batch"].frames[0].image.putpixel((0, 0), (0, 0, 0))
            result["second"] = host.env.frames_after(first)
            assert result["second"].frames[0].image.tobytes() == original
            host.stop()
    host.run(publish)
    batch = result["batch"]
    assert len(batch.frames) == 120
    assert batch.dropped
    assert batch.latest.frame_seq == 125


def test_stop_wakes_waiters_and_generation_change_is_explicit(fake_live):
    host, _ = fake_live
    initial = host.env.snapshot()
    result = []
    started = threading.Event()

    def waiter():
        started.set()
        try: host.env.wait_frame(initial, timeout=5)
        except BaseException as exc: result.append(exc)

    thread = threading.Thread(target=waiter, daemon=True)
    thread.start()
    host.run(lambda frame: host.stop() if frame.frame_seq == 1 else None)
    thread.join(1)
    assert result and isinstance(result[0], (env_module.LiveEnvClosed,))

def test_state_load_changes_generation_and_wakes_frame_waiter(fake_live):
    host, _ = fake_live
    initial = host.env.snapshot()
    changed = []
    thread = threading.Thread(target=lambda: _wait_generation(host.env, initial, changed), daemon=True)
    thread.start()
    loader = threading.Thread(target=lambda: host.env._host._submit("load", b"0"), daemon=True)
    loader.start()
    deadline = time.monotonic() + 1
    while host._commands.empty() and time.monotonic() < deadline:
        time.sleep(0.001)
    assert not host._commands.empty()
    time.sleep(0.01)
    host.run(lambda frame: host.stop() if frame.frame_seq == 1 else None)
    thread.join(1); loader.join(1)
    assert any(isinstance(exc, env_module.FrameGenerationChanged) for exc in changed)


def test_owner_failure_wakes_waiters_with_the_underlying_error(fake_live):
    host, pyboy = fake_live
    waiter_error = []
    waiting = threading.Event()

    def waiter():
        waiting.set()
        try: host.env.wait_frame(host.env.snapshot(), timeout=5)
        except BaseException as exc: waiter_error.append(exc)

    thread = threading.Thread(target=waiter, daemon=True)
    thread.start()
    assert waiting.wait(1)
    time.sleep(0.01)
    def fail_tick(*args, **kwargs): raise RuntimeError("fake tick failed")
    pyboy.tick = fail_tick
    with pytest.raises(RuntimeError, match="fake tick failed"):
        host.run(lambda frame: None)
    thread.join(1)
    assert waiter_error and isinstance(waiter_error[0], env_module.LiveEnvFailed)
    assert "fake tick failed" in str(waiter_error[0])


def test_command_queue_backpressure_is_immediate_and_explicit(fake_live):
    host, _ = fake_live
    for i in range(host._COMMANDS):
        host._commands.put_nowait(env_module._Command("unused", i, Future()))
    with pytest.raises(env_module.LiveEnvBackpressure):
        host.env.read_ram((0x1234,))
    host.stop()
    host.run(lambda frame: None)


def test_window_close_stops_cleanly_and_releases_queued_input(fake_live):
    host, pyboy = fake_live
    errors = []
    queued = threading.Event()

    def worker():
        queued.set()
        try: host.env.press("A", 1)
        except BaseException as exc: errors.append(exc)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    assert queued.wait(1)
    deadline = time.monotonic() + 1
    while host._commands.empty() and time.monotonic() < deadline:
        time.sleep(0.001)
    assert not host._commands.empty()
    def closed(*args, **kwargs): pyboy._op("tick-close"); return False
    pyboy.tick = closed
    host.run(lambda frame: None)
    thread.join(1)
    assert not pyboy.held
    assert errors and isinstance(errors[0], env_module.LiveEnvClosed)
    with pytest.raises(env_module.LiveEnvClosed): host.env.snapshot()


def test_sdl_filter_suppresses_native_controls_but_keeps_quit_and_fullscreen():
    import ctypes
    import sdl2

    event_filter = env_module._install_live_event_filter()
    try:
        callback = event_filter.callback
        event = sdl2.SDL_Event()
        event.type = sdl2.SDL_KEYDOWN
        event.key.keysym.sym = sdl2.SDLK_p
        assert callback(None, ctypes.byref(event)) == 0
        event.key.keysym.sym = sdl2.SDLK_F11
        assert callback(None, ctypes.byref(event)) == 1
        event.key.keysym.sym = sdl2.SDLK_ESCAPE
        assert callback(None, ctypes.byref(event)) == 1
        event.type = sdl2.SDL_CONTROLLERBUTTONDOWN
        assert callback(None, ctypes.byref(event)) == 0
        event.type = sdl2.SDL_QUIT
        assert callback(None, ctypes.byref(event)) == 1
        event.type = sdl2.SDL_WINDOWEVENT
        assert callback(None, ctypes.byref(event)) == 1
    finally:
        event_filter.close()


def test_live_pyboy_real_rom_uses_main_thread_and_stops_cleanly(monkeypatch):
    try:
        rom = env_module.find_rom()
    except FileNotFoundError:
        pytest.skip("No Pokémon Red ROM found")
    host = env_module.LivePyBoyHost(rom)
    owner = threading.get_ident()
    assert host.env.snapshot().image.size == (160, 144)
    host.run(lambda frame: host.stop() if frame.frame_seq == 3 else None)
    assert host._pyboy.frame_count >= 3
    assert host._closed
    assert host._owner_thread == owner


def _wait_generation(env, initial, result):
    try: env.wait_frame(initial, timeout=5)
    except BaseException as exc: result.append(exc)
