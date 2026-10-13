from __future__ import annotations

import os
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


class PyBoyEnv:
    name: str
    pointer = False

    def __init__(self, rom: str | None = None, *, state: str | None = None, name: str = "pokemon_red") -> None:
        self.name = name
        self.pyboy = PyBoy(rom or find_rom(), window="null")
        self.pyboy.set_emulation_speed(0)
        if state:
            self.load(state)

    def screenshot(self) -> Image.Image:
        return self.pyboy.screen.image.convert("RGB")
    def inputs(self) -> tuple[str, ...]:
        return _INPUTS
    def press(self, key: str, frames: int = 8, on_frame: Callable[[Image.Image], None] | None = None) -> None:
        button = key.lower()
        if key not in _INPUTS:
            raise ValueError(f"Unsupported pad input: {key}")
        self.pyboy.button_press(button)
        try:
            for _ in range(frames):
                self.pyboy.tick(1)
                if on_frame:
                    on_frame(self.screenshot())
        finally:
            self.pyboy.button_release(button)
        for _ in range(8):
            self.pyboy.tick(1)
            if on_frame:
                on_frame(self.screenshot())

    def click(self, at: list[float]) -> None:
        raise NotImplementedError("PyBoy has no pointer input")
    def tick(self, frames: int = 1) -> None:
        self.pyboy.tick(frames)
    def save(self, path: str) -> None:
        with open(path, "wb") as file:
            self.pyboy.save_state(file)

    def load(self, path: str) -> None:
        with open(path, "rb") as file:
            self.pyboy.load_state(file)
    def ram(self, addr: int) -> int:
        return self.pyboy.memory[addr]
    def close(self) -> None:
        self.pyboy.stop(save=False)
