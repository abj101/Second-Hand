from __future__ import annotations

from collections import deque
from typing import Any, Callable

from PIL import Image, ImageDraw

class GridEnv:
    """Textured 16 px world; player centered, NPC left, door right."""
    name, pointer, tile_px, view_tiles = "grid", False, 16, (10, 9)
    inputs_available = ("UP", "DOWN", "LEFT", "RIGHT", "A", "B", "START", "SELECT")
    _steps = {"LEFT": (-1, 0), "RIGHT": (1, 0), "UP": (0, -1), "DOWN": (0, 1)}

    def __init__(self) -> None:
        self.player, self.npc, self.door = (8, 8), (7, 8), (10, 8)
        self.walls = {(x, y) for x in range(16) for y in range(16) if x in (0, 15) or y in (0, 15)}
        self.scene, self.text_box = 0, False
        self._travel = self._transition = 0
        self._moving = False
        self._direction = ""
    def inputs(self) -> tuple[str, ...]: return self.inputs_available

    def screenshot(self) -> Image.Image:
        if self._transition: return Image.new("RGB", (160, 144), "white")
        ground = ((178, 211, 137), (169, 204, 129)) if self.scene == 0 else ((133, 180, 211), (124, 171, 202))
        image = Image.new("RGB", (160, 144), ground[0])
        draw = ImageDraw.Draw(image)
        px, py = self.player
        dx = -self._travel if self._direction == "RIGHT" else self._travel if self._direction == "LEFT" else 0
        dy = -self._travel if self._direction == "DOWN" else self._travel if self._direction == "UP" else 0
        for ty in range(py - 5, py + 5):
            for tx in range(px - 5, px + 5):
                x, y = 80 + (tx - px) * 16 + dx, 72 + (ty - py) * 16 + dy
                tile = (tx, ty)
                if tile in self.walls:
                    color = (67, 73, 66)
                elif tile in (self.door, self.npc) and self.scene == 0:
                    color = (132, 78, 43) if tile == self.door else (88, 77, 171)
                else:
                    for sy in range(0, 16, 2):
                        for sx in range(0, 16, 2):
                            shade = hash((tx * 8 + sx // 2, ty * 8 + sy // 2)) % 5
                            color = tuple(max(0, c - shade * 5) for c in ground[0])
                            draw.rectangle((x + sx, y + sy, x + sx + 1, y + sy + 1), fill=color)
                    continue
                draw.rectangle((x, y, x + 15, y + 15), fill=color, outline=(color[0] - 8, color[1] - 8, color[2] - 8))
        draw.rectangle((76, 68, 83, 77), fill=(194, 56, 44))
        if self.text_box:
            draw.rectangle((4, 105, 155, 139), fill="white", outline="black", width=2)
            draw.text((12, 114), "Hello!", fill="black")
        return image

    def _frame(self) -> None:
        if self._transition:
            self._transition += 1
            if self._transition > 2:
                self.scene, self.player, self._transition = 1, (2, 8), 0
            return
        if not self._moving: return
        self._travel += 2
        if self._travel >= self.tile_px:
            x, y = self.player
            dx, dy = self._steps[self._direction]
            self.player, self._travel, self._moving = (x + dx, y + dy), 0, False
            if self.player == self.door and self.scene == 0: self._transition = 1

    def press(self, key: str, frames: int = 8, on_frame: Callable[[Image.Image], None] | None = None) -> None:
        if key not in self.inputs_available:
            raise ValueError(f"Unsupported pad input: {key}")
        if key == "A":
            if sum(abs(a - b) for a, b in zip(self.player, self.npc)) == 1: self.text_box = True
        elif key in self._steps:
            self._direction = key
            dx, dy = self._steps[key]
            dest = (self.player[0] + dx, self.player[1] + dy)
            self._moving = dest not in self.walls and not (self.scene == 0 and dest == self.npc)
        for _ in range(max(0, frames) + 8):
            self._frame()
            if on_frame: on_frame(self.screenshot())
        self._direction = ""
    def click(self, at: list[float]) -> None: raise NotImplementedError("GridEnv has no pointer input")

    def tick(self, frames: int = 1) -> None:
        for _ in range(frames): self._frame()

class StubModel:
    def __init__(self, replies: list[Any]) -> None: self._replies, self.calls = deque(replies), 0

    def ask(self, *args, **kwargs) -> Any: self.calls += 1; return self._replies.popleft()
