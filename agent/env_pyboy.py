from __future__ import annotations

import io
import os
import queue
import threading
import time
import uuid
from collections import deque
from concurrent.futures import Future
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PIL import Image
from pyboy import PyBoy

_INPUTS = ("UP", "DOWN", "LEFT", "RIGHT", "A", "B", "START", "SELECT")


def find_rom() -> str:
    if rom := os.getenv("POKERED_ROM"): return rom
    roms = sorted(Path("Rom").glob("*.gb"))
    if not roms:
        raise FileNotFoundError("Set POKERED_ROM or place a .gb ROM in Rom/")
    return str(roms[0])


@dataclass(frozen=True)
class FrameSnapshot:
    generation: str
    frame_seq: int
    captured_monotonic: float
    image: Image.Image


@dataclass(frozen=True)
class FrameBatch:
    frames: tuple[FrameSnapshot, ...]
    latest: FrameSnapshot
    dropped: bool


@dataclass(frozen=True)
class RamSnapshot:
    frame: FrameSnapshot
    values: dict[int, int]


@dataclass(frozen=True)
class SavedState:
    frame: FrameSnapshot
    state: bytes


class LiveEnvClosed(RuntimeError):
    """The live emulator has stopped."""


class LiveEnvFailed(RuntimeError):
    """The live emulator owner failed."""


class LiveEnvBackpressure(RuntimeError):
    """The bounded live command queue is full."""


class FrameGenerationChanged(RuntimeError):
    """A frame wait crossed a state-load/session boundary."""


class PyBoyEnv:
    """Deterministic headless environment for tests, evaluations and replay."""

    name: str
    pointer = False
    frame_sink: Callable[[Image.Image], None] | None = None  # sees every emulated frame (live viewer); never feeds back into the game

    def __init__(self, rom: str | None = None, *, state: str | None = None, name: str = "pokemon_red") -> None:
        self.name = name
        self.pyboy = PyBoy(rom or find_rom(), window="null")
        self.pyboy.set_emulation_speed(0)
        self._frame_seq, self._listeners = 0, []
        if state:
            self.load(state)

    def screenshot(self) -> Image.Image:
        return self.pyboy.screen.image.convert("RGB")
    def inputs(self) -> tuple[str, ...]:
        return _INPUTS
    def listen(self, fn): self._listeners.append(fn)
    def press(self, key: str, frames: int = 8, on_frame: Callable[[Image.Image], None] | None = None) -> None:
        button = key.lower()
        if key not in _INPUTS:
            raise ValueError(f"Unsupported pad input: {key}")
        self.pyboy.button_press(button)
        try:
            for _ in range(frames):
                self._tick(on_frame)
        finally:
            self.pyboy.button_release(button)
        for _ in range(8):
            self._tick(on_frame)

    def _tick(self, on_frame=None) -> None:
        self.pyboy.tick(1); self._frame_seq += 1
        if on_frame or self.frame_sink or self._listeners:
            image = self.screenshot()
            for listener in self._listeners: listener(image, self._frame_seq)
            if on_frame: on_frame(image)
            if self.frame_sink: self.frame_sink(image)

    def click(self, at: list[float]) -> None:
        raise NotImplementedError("PyBoy has no pointer input")
    def tick(self, frames: int = 1) -> None:
        if not self.frame_sink and not self._listeners: return self.pyboy.tick(frames)
        for _ in range(frames): self._tick()
    def save(self, path: str) -> None:
        with open(path, "wb") as file:
            self.pyboy.save_state(file)

    def load(self, path: str) -> None:
        with open(path, "rb") as file:
            self.pyboy.load_state(file)
        for listener in self._listeners: listener(None, self._frame_seq)
    def ram(self, addr: int) -> int:
        return self.pyboy.memory[addr]
    def close(self) -> None:
        self.pyboy.stop(save=False)


@dataclass
class _Command:
    kind: str
    value: object
    future: Future


class _LivePyBoyEnv:
    name: str
    pointer = False

    def __init__(self, host: LivePyBoyHost) -> None:
        self._host = host
        self.name = host.name

    def inputs(self) -> tuple[str, ...]: return _INPUTS
    def listen(self, fn): self._host.listen(fn)
    def click(self, at: list[float]) -> None: raise NotImplementedError("PyBoy has no pointer input")

    def snapshot(self) -> FrameSnapshot:
        with self._host._condition:
            self._host._raise_if_unavailable()
            return _copy_frame(self._host._latest)

    def screenshot(self) -> Image.Image:
        return _copy_frame(self.snapshot()).image

    def wait_frame(self, after: FrameSnapshot, *, timeout: float) -> FrameSnapshot:
        deadline = time.monotonic() + timeout
        with self._host._condition:
            while True:
                current = self._host._latest
                if current.generation != after.generation:  # before the closed check: a load that happened is reported even if the host stopped since
                    raise FrameGenerationChanged(f"frame generation changed from {after.generation} to {current.generation}")
                self._host._raise_if_unavailable()
                if current.frame_seq > after.frame_seq:
                    return _copy_frame(current)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("no newer emulator frame before deadline")
                self._host._condition.wait(remaining)

    def frames_after(self, after: FrameSnapshot) -> FrameBatch:
        with self._host._condition:
            self._host._raise_if_unavailable()
            latest = self._host._latest
            if latest.generation != after.generation:
                return FrameBatch((), _copy_frame(latest), True)
            history = tuple(self._host._history)
            frames = tuple(frame for frame in history if frame.generation == after.generation and frame.frame_seq > after.frame_seq)
            dropped = bool(frames and frames[0].frame_seq != after.frame_seq + 1) or (not frames and latest.frame_seq > after.frame_seq)
            return FrameBatch(tuple(_copy_frame(frame) for frame in frames), _copy_frame(latest), dropped)

    def tick(self, frames: int = 1) -> None:
        if frames < 0: raise ValueError("frames must be non-negative")
        if frames == 0: return
        after = self.snapshot()
        self.wait_frame(after, timeout=max(1.0, frames / 20)) if frames == 1 else self._wait_count(after, frames)

    def _wait_count(self, after: FrameSnapshot, count: int) -> None:
        deadline = time.monotonic() + max(1.0, count / 20)
        with self._host._condition:
            while True:
                self._host._raise_if_unavailable()
                current = self._host._latest
                if current.generation != after.generation:
                    raise FrameGenerationChanged("frame generation changed while waiting")
                if current.frame_seq >= after.frame_seq + count: return
                remaining = deadline - time.monotonic()
                if remaining <= 0: raise TimeoutError("emulator did not advance enough frames")
                self._host._condition.wait(remaining)

    def press(self, key: str, frames: int = 8, on_frame: Callable[[Image.Image], None] | None = None) -> None:
        if key not in _INPUTS: raise ValueError(f"Unsupported pad input: {key}")
        if not 1 <= frames <= 600: raise ValueError("live press frames must be between 1 and 600")
        result = self._host._submit("press", (key.lower(), frames))
        for frame in result:
            if on_frame: on_frame(_copy_frame(frame).image)

    def read_ram(self, addresses: tuple[int, ...]) -> RamSnapshot:
        return self._host._submit("ram", tuple(addresses))

    def ram(self, addr: int) -> int:
        return self.read_ram((addr,)).values[addr]

    def save_snapshot(self) -> SavedState:
        return self._host._submit("save", None)

    def save(self, path: str) -> None:
        saved = self.save_snapshot()
        with open(path, "wb") as file: file.write(saved.state)

    def load(self, path: str) -> None:
        self._host._submit("load", Path(path).read_bytes())

    def close(self) -> None:
        self._host.stop()


class LivePyBoyHost:
    """Main-thread PyBoy owner. Only its facade is passed to agent workers."""

    _HISTORY = 120
    _COMMANDS = 8

    def __init__(self, rom: str | None = None, *, state: str | None = None, name: str = "pokemon_red") -> None:
        self.name = name
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError("LivePyBoyHost must be constructed on the main thread")
        self._owner_thread = threading.get_ident()
        self._pyboy = PyBoy(rom or find_rom(), window="SDL2", no_input=False, sound_volume=0)  # muted; emulation unchanged
        try:
            self._pyboy.set_emulation_speed(1)
            if state:
                with open(state, "rb") as file: self._pyboy.load_state(file)
            self._generation = str(uuid.uuid4())
            self._frame_seq = 0
            image = self._capture_image()
            self._latest = FrameSnapshot(self._generation, 0, time.monotonic(), image)
            self._history: deque[FrameSnapshot] = deque(maxlen=self._HISTORY)
            self._condition = threading.Condition()
            self._commands: queue.Queue[_Command] = queue.Queue(self._COMMANDS)
            self._pending: set[Future] = set()
            self._stop = threading.Event()
            self._closed = False
            self._failure: BaseException | None = None
            self._active: tuple[_Command, str, int, list[FrameSnapshot], int] | None = None
            self._held: set[str] = set()
            self._listeners: list[Callable[[Image.Image | None, int], None]] = []
            self.env = _LivePyBoyEnv(self)
            self._event_filter = _install_live_event_filter()
        except BaseException:
            self._pyboy.stop(save=False)
            raise

    def run(self, publish: Callable[[FrameSnapshot], None]) -> None:
        if threading.get_ident() != self._owner_thread:
            raise RuntimeError("LivePyBoyHost.run must execute on its construction thread")
        try:
            while not self._stop.is_set():
                self._drain_commands()
                if self._stop.is_set(): break
                running = self._pyboy.tick(1, render=True)
                if not running:
                    self.stop()
                    break
                frame = self._record_frame()
                if self._active is not None:
                    cmd, button, held_left, images, released_left = self._active
                    images.append(frame)
                    if held_left:
                        held_left -= 1
                        if held_left == 0:
                            self._pyboy.button_release(button); self._held.discard(button)
                            released_left = 8
                        self._active = (cmd, button, held_left, images, released_left)
                    else:
                        released_left -= 1
                        self._active = (cmd, button, 0, images, released_left) if released_left else None
                        if not released_left: self._complete(cmd, tuple(images))
                publish(frame)
        except BaseException as exc:
            if not isinstance(exc, LiveEnvClosed): self._failure = exc
            raise
        finally:
            self._release_all()
            self._fail_pending()
            try:
                self._pyboy.stop(save=False)
            finally:
                self._event_filter.close()
                with self._condition:
                    self._closed = True
                    self._condition.notify_all()

    def stop(self) -> None:
        self._stop.set()
        with self._condition: self._condition.notify_all()

    def listen(self, fn) -> None:
        self._raise_if_unavailable(); self._listeners.append(fn)

    def _capture_image(self) -> Image.Image:
        return self._pyboy.screen.image.convert("RGB").copy()

    def _record_frame(self) -> FrameSnapshot:
        self._frame_seq += 1
        now = time.monotonic()
        image = self._capture_image()
        image.info.update(generation=self._generation, frame_seq=self._frame_seq, captured_monotonic=now)
        frame = FrameSnapshot(self._generation, self._frame_seq, now, image)
        for listener in self._listeners: listener(image, self._frame_seq)
        with self._condition:
            self._latest = frame
            if self._active is None: self._history.append(frame)
            self._condition.notify_all()
        return frame

    def _submit(self, kind: str, value: object):
        self._raise_if_unavailable()
        future = Future()
        cmd = _Command(kind, value, future)
        with self._condition:
            self._raise_if_unavailable()
            self._pending.add(future)
        try:
            self._commands.put_nowait(cmd)
        except queue.Full as exc:
            with self._condition: self._pending.discard(future)
            raise LiveEnvBackpressure("live command queue is full") from exc
        return future.result()

    def _drain_commands(self) -> None:
        if self._active is not None: return
        for _ in range(self._COMMANDS):
            try: cmd = self._commands.get_nowait()
            except queue.Empty: return
            try:
                if cmd.kind == "press":
                    key, frames = cmd.value
                    self._pyboy.button_press(key); self._held.add(key)
                    self._active = (cmd, key, frames, [], 0)
                    return
                if cmd.kind == "ram":
                    values = {address: self._pyboy.memory[address] for address in cmd.value}
                    self._complete(cmd, RamSnapshot(_copy_frame(self._latest), values))
                elif cmd.kind == "save":
                    buffer = io.BytesIO(); self._pyboy.save_state(buffer)
                    self._complete(cmd, SavedState(_copy_frame(self._latest), buffer.getvalue()))
                elif cmd.kind == "load":
                    self._release_all(); self._pyboy.load_state(io.BytesIO(cmd.value))
                    self._generation = str(uuid.uuid4()); self._frame_seq = 0
                    image = self._capture_image(); now = time.monotonic()
                    image.info.update(generation=self._generation, frame_seq=0, captured_monotonic=now)
                    loaded = FrameSnapshot(self._generation, 0, now, image)
                    with self._condition:
                        self._latest = loaded; self._history.clear(); self._condition.notify_all()
                    for listener in self._listeners: listener(None, 0)
                    self._complete(cmd, None)
                else: raise ValueError(f"unknown live command: {cmd.kind}")
            except BaseException as exc:
                self._complete(cmd, error=exc)

    def _complete(self, cmd: _Command, value=None, error: BaseException | None = None) -> None:
        with self._condition: self._pending.discard(cmd.future)
        if error: cmd.future.set_exception(error)
        else: cmd.future.set_result(value)

    def _fail_pending(self) -> None:
        reason = LiveEnvFailed(str(self._failure)) if self._failure else LiveEnvClosed("live PyBoy host stopped")
        with self._condition:
            pending = tuple(self._pending); self._pending.clear()
            self._condition.notify_all()
        for future in pending:
            if not future.done(): future.set_exception(reason)

    def _raise_if_unavailable(self) -> None:
        if self._failure: raise LiveEnvFailed(str(self._failure)) from self._failure
        if self._closed or self._stop.is_set(): raise LiveEnvClosed("live PyBoy host is closed")

    def _release_all(self) -> None:
        active, self._active = self._active, None
        for button in tuple(self._held):
            try: self._pyboy.button_release(button)
            except Exception: pass
        self._held.clear()
        if active and not active[0].future.done():
            error = LiveEnvFailed(str(self._failure)) if self._failure else LiveEnvClosed("live action interrupted")
            self._complete(active[0], error=error)


def _copy_frame(frame: FrameSnapshot) -> FrameSnapshot:
    image = frame.image.copy()
    image.info.update(generation=frame.generation, frame_seq=frame.frame_seq, captured_monotonic=frame.captured_monotonic)
    return FrameSnapshot(frame.generation, frame.frame_seq, frame.captured_monotonic, image)


class _EventFilter:
    """Reversibly suppress native game/controller inputs while preserving window events."""

    def __init__(self, sdl, callback, previous_filter, previous_userdata) -> None:
        self.sdl, self.callback = sdl, callback
        self.previous_filter, self.previous_userdata = previous_filter, previous_userdata
        self.closed = False

    def close(self) -> None:
        if not self.closed:
            self.sdl.SDL_SetEventFilter(self.previous_filter, self.previous_userdata)
            self.closed = True


def _install_live_event_filter() -> _EventFilter:
    import ctypes
    import sdl2

    previous_filter = sdl2.SDL_EventFilter()
    previous_userdata = ctypes.c_void_p()
    has_previous = sdl2.SDL_GetEventFilter(ctypes.byref(previous_filter), ctypes.byref(previous_userdata))
    if has_previous:
        previous = previous_filter
    else:
        previous = ctypes.cast(ctypes.c_void_p(), sdl2.SDL_EventFilter)
    allow_keys = {sdl2.SDLK_ESCAPE, sdl2.SDLK_F11}
    filtered = {sdl2.SDL_KEYDOWN, sdl2.SDL_KEYUP, sdl2.SDL_CONTROLLERBUTTONDOWN, sdl2.SDL_CONTROLLERBUTTONUP,
                sdl2.SDL_CONTROLLERAXISMOTION, sdl2.SDL_JOYBUTTONDOWN, sdl2.SDL_JOYBUTTONUP, sdl2.SDL_JOYAXISMOTION}

    @sdl2.SDL_EventFilter
    def callback(userdata, event):
        if event.contents.type in (sdl2.SDL_KEYDOWN, sdl2.SDL_KEYUP):
            return 1 if event.contents.key.keysym.sym in allow_keys else 0
        return 0 if event.contents.type in filtered else 1

    sdl2.SDL_SetEventFilter(callback, None)
    return _EventFilter(sdl2, callback, previous, previous_userdata if has_previous else None)
