"""Reversible NeutralWorld in a real OS window (capture + OS click)."""

from __future__ import annotations

import argparse
import tkinter as tk

from PIL import Image, ImageDraw, ImageFont, ImageTk

from loop.adaptive.world import NeutralWorld

WINDOW_TITLE = "AvatarNeutralWorld"
_SIZE = (640, 480)


def _font() -> ImageFont.ImageFont:
    for name in ("arial.ttf", "C:\\Windows\\Fonts\\arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, 28)
        except OSError:
            continue
    return ImageFont.load_default()


def render_world(world: NeutralWorld, size: tuple[int, int] = _SIZE) -> Image.Image:
    w, h = size
    img = Image.new("RGB", (w, h), (30, 30, 40))
    draw = ImageDraw.Draw(img)
    font = _font()
    for node in world._scene().nodes:
        x0, y0, x1, y1 = node.bbox
        px0, py0 = int(x0 * w), int(y0 * h)
        px1, py1 = int(x1 * w), int(y1 * h)
        fill = node.attributes.get("fill", (128, 128, 128))
        draw.rectangle([px0, py0, px1, py1], fill=fill)
        label = str(node.attributes.get("label", ""))
        if "numeric" in node.attributes:
            label = f"{label}:{node.attributes['numeric']}"
        draw.text((px0 + 8, py0 + 8), label, fill=(255, 255, 255), font=font)
    return img


class LiveNeutralWindow:
    def __init__(self, seed: int = 0) -> None:
        self.world = NeutralWorld()
        self.world.reset(seed)
        self.root = tk.Tk()
        self.root.title(WINDOW_TITLE)
        self.root.resizable(False, False)
        self._photo: ImageTk.PhotoImage | None = None
        self.label = tk.Label(self.root, borderwidth=0)
        self.label.pack()
        self.label.bind("<Button-1>", self._on_click)
        self._redraw()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(400, lambda: self.root.attributes("-topmost", False))

    def _redraw(self) -> None:
        img = render_world(self.world)
        self._photo = ImageTk.PhotoImage(img)
        self.label.configure(image=self._photo)
        self.root.update_idletasks()

    def _on_click(self, event) -> None:
        w = max(1, self.label.winfo_width())
        h = max(1, self.label.winfo_height())
        self.world.click_at(event.x / w, event.y / h)
        self._redraw()

    def run(self) -> None:
        self.root.mainloop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Live NeutralWorld window")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)
    LiveNeutralWindow(seed=args.seed).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
