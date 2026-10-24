from __future__ import annotations

from collections import deque
from types import SimpleNamespace
from typing import Any, Callable

from PIL import Image, ImageDraw

class GridEnv:
    """Textured 16 px world; player centered, NPC left, door right."""
    name, pointer, tile_px, view_tiles = "grid", False, 16, (10, 9)
    inputs_available = ("UP", "DOWN", "LEFT", "RIGHT", "A", "B", "START", "SELECT")
    _steps = {"LEFT": (-1, 0), "RIGHT": (1, 0), "UP": (0, -1), "DOWN": (0, 1)}

    def __init__(self, *, hud: bool = False, wander: bool = False, animated: bool = False) -> None:
        self.player, self.npc, self.door = (8, 8), (7, 8), (10, 8)
        self.walls = {(x, y) for x in range(16) for y in range(16) if x in (0, 15) or y in (0, 15)}
        self.scene, self.text_box = 0, False
        self._travel = self._transition = 0
        self._moving = False
        self._direction = ""
        self.hud, self.wander, self.animated = hud, wander, animated
        self.hud_value, self._beat, self._cycle_i = 0, 0, 0
        self._facing = None  # last pressed direction; drawn as a marker only when hud/wander/animated
        # ponytail: gated geometry puts tile rows at y = 64 + 16k (centres 72 + 16k) so they sit on observer.snap's grid; default geometry stays unchanged for goldens.
        self._top = 64 if (hud or wander or animated) else 72
        self._listeners, self._frame_seq = [], 0
    def listen(self, fn) -> None: self._listeners.append(fn)
    def _notify(self, image):
        self._frame_seq += 1
        for listener in self._listeners: listener(image, self._frame_seq)
    def inputs(self) -> tuple[str, ...]: return self.inputs_available
    def set_hud_value(self, n: int) -> None: self.hud_value = n
    def _wander_step(self) -> None:
        # ponytail: fixed DOWN/UP cycle (vertical only, so the NPC never shares a horizontal camera move); one step per press; no pathing
        for _ in range(4):
            dx, dy = self._steps[("DOWN", "UP")[self._cycle_i % 2]]; self._cycle_i += 1
            dest = (self.npc[0] + dx, self.npc[1] + dy)
            if dest not in self.walls and dest not in (self.player, self.door):
                self.npc = dest; return
    def perception(self) -> dict:
        px, py = self.player
        def centre(tile: tuple[int, int]) -> tuple[int, int]:  # same placement as screenshot()
            return 80 + (tile[0] - px) * 16 + 8, self._top + (tile[1] - py) * 16 + 8
        def at(tile: tuple[int, int]) -> list[float]:
            x, y = centre(tile); return [x / 160, y / 144]
        def visible(tile: tuple[int, int]) -> bool:
            x, y = centre(tile); return 0 <= x < 160 and 0 <= y < 144
        entities = [{"name": "player", "kind": "player", "at": [0.5, 0.5] if self._top == 72 else [88 / 160, 72 / 144]}]
        if visible(self.npc): entities.append({"name": "npc", "kind": "npc", "at": at(self.npc)})
        if self.scene == 0 and visible(self.door): entities.append({"name": "exit: door", "kind": "exit", "at": at(self.door)})
        if self.animated and visible((9, 7)): entities.append({"name": "fountain", "kind": "object", "at": at((9, 7))})
        if self.hud: entities.append({"name": "hp bar", "kind": "object", "at": [16 / 160, 16 / 144]})
        return {"screen": "world", "text": [], "entities": entities}

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
                x, y = 80 + (tx - px) * 16 + dx, self._top + (ty - py) * 16 + dy
                tile = (tx, ty)
                if tile in self.walls:
                    color = (67, 73, 66)
                elif tile in (self.door, self.npc) and self.scene == 0:
                    color = (132, 78, 43) if tile == self.door else (88, 77, 171)
                elif self.animated and tile == (9, 7):
                    color = (60, 120, 200) if self._beat % 2 == 0 else (90, 150, 230)
                else:
                    for sy in range(0, 16, 2):
                        for sx in range(0, 16, 2):
                            shade = hash((tx * 8 + sx // 2, ty * 8 + sy // 2)) % 5
                            color = tuple(max(0, c - shade * 5) for c in ground[0])
                            draw.rectangle((x + sx, y + sy, x + sx + 1, y + sy + 1), fill=color)
                    continue
                draw.rectangle((x, y, x + 15, y + 15), fill=color, outline=(color[0] - 8, color[1] - 8, color[2] - 8))
        if self._top == 72: draw.rectangle((76, 68, 83, 77), fill=(194, 56, 44))
        else: draw.rectangle((84, 67, 91, 76), fill=(194, 56, 44))  # sprite centred on its tile
        if self.text_box:
            draw.rectangle((4, 105, 155, 139), fill="white", outline="black", width=2)
            draw.text((12, 114), "Hello!", fill="black")
        if self.hud: draw.rectangle((8, 8, 23, 23), fill=(250, 40 + 20 * (self.hud_value % 10), 250))
        if (self.hud or self.wander or self.animated) and self._facing:
            mark = {"UP": (78, 69), "DOWN": (78, 75), "LEFT": (76, 72), "RIGHT": (82, 72)}[self._facing] if self._top == 72 else {"UP": (87, 67), "DOWN": (87, 75), "LEFT": (84, 71), "RIGHT": (90, 71)}[self._facing]
            draw.rectangle((mark[0], mark[1], mark[0] + 1, mark[1] + 1), fill=(255, 255, 0))
        return image

    def _frame(self) -> None:
        self._beat += 1
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
            self._direction = key; self._facing = key
            dx, dy = self._steps[key]
            dest = (self.player[0] + dx, self.player[1] + dy)
            self._moving = dest not in self.walls and not (self.scene == 0 and dest == self.npc)
        for _ in range(max(0, frames) + 8):
            self._frame()
            if on_frame:
                image = self.screenshot()
                if key in self._steps and not self._moving and dest != self.player and _ < 4:
                    x = 78 if self._top == 72 else 88
                    ImageDraw.Draw(image).rectangle((x, 72, x + 1, 73), fill=(245, 140, 120))
                self._notify(image)
                on_frame and on_frame(image)
            else: self._notify(self.screenshot())
        if self.wander: self._wander_step()
        self._direction = ""
    def click(self, at: list[float]) -> None: raise NotImplementedError("GridEnv has no pointer input")

    def tick(self, frames: int = 1) -> None:
        for _ in range(frames):
            self._frame()
            if self._listeners: self._notify(self.screenshot())

class StubModel:
    def __init__(self, replies: list[Any]) -> None: self._replies, self.calls = deque(replies), 0

    def ask(self, *args, **kwargs) -> Any:
        # planner calls get an empty plan unless the test queued one (a reply whose data has "focus")
        nxt = self._replies[0] if self._replies else None
        if kwargs.get("role") == "plan" and not (isinstance(getattr(nxt, "data", None), dict) and "focus" in nxt.data):
            return SimpleNamespace(data={"goals": [], "focus": "", "why": "stub"}, text="")
        self.calls += 1; return self._replies.popleft()
