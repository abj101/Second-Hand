"""Precise locating (spec §6): rough point -> zoom-refine -> snap to OCR text.

Precision only matters for the action actually executed, so refine runs per target, not per frame.
All points are [x, y] screen fractions in [0, 1].
"""

from __future__ import annotations


from PIL import Image, ImageDraw

from agent.model import Ollama, Reply

ZOOM_FRAC = 0.25    # crop side as a fraction of frame width
ZOOM_LONG_SIDE = 768

REFINE_SYSTEM = "You locate one thing in an enlarged part of a video game screenshot."
REFINE_PROMPT = ('Point to the centre of "{target}". Answer JSON {{"at": [x, y]}} on a 0-1000 grid of THIS image '
                 '([0, 0] top-left), or {{"at": null}} if it is not visible here.')
REFINE_SCHEMA = {"type": "object", "properties": {"at": {"type": ["array", "null"], "items": {"type": "number"}}},
                 "required": ["at"]}


def crop_box(size: tuple[int, int], at: list[float], frac: float = ZOOM_FRAC) -> tuple[int, int, int, int]:
    """Square-ish pixel crop centred on `at`, shifted to stay inside the frame."""
    w, h = size
    sw, sh = min(frac * w, w), min(frac * w, h)
    x0 = min(max(at[0] * w - sw / 2, 0), w - sw)
    y0 = min(max(at[1] * h - sh / 2, 0), h - sh)
    return round(x0), round(y0), round(x0 + sw), round(y0 + sh)


def zoom(image: Image.Image, at: list[float], frac: float = ZOOM_FRAC) -> tuple[Image.Image, tuple[int, int, int, int]]:
    box = crop_box(image.size, at, frac)
    crop = image.crop(box)
    k = ZOOM_LONG_SIDE / max(crop.size)
    return crop.resize((round(crop.width * k), round(crop.height * k)), Image.LANCZOS), box


def refine(model: Ollama, image: Image.Image, target: str, at: list[float]) -> tuple[list[float] | None, Reply]:
    """Re-point `target` inside a zoomed crop around `at`; None when the model can't see it there."""
    big, (x0, y0, x1, y1) = zoom(image, at)
    reply = model.ask(REFINE_SYSTEM, REFINE_PROMPT.format(target=target), [big], REFINE_SCHEMA)
    p = (reply.data or {}).get("at")
    if not isinstance(p, list) or len(p) != 2 or not all(isinstance(v, (int, float)) for v in p):
        return None, reply
    px = x0 + min(max(p[0], 0), 1000) / 1000 * (x1 - x0)
    py = y0 + min(max(p[1], 0), 1000) / 1000 * (y1 - y0)
    return [px / image.width, py / image.height], reply


def rulers(image: Image.Image, margin: int = 36) -> Image.Image:
    """Pad the frame with 0-1000 rulers on all four edges; the content itself is not covered."""
    w, h = image.size
    out = Image.new("RGB", (w + 2 * margin, h + 2 * margin), (255, 255, 255))
    out.paste(image.convert("RGB"), (margin, margin))
    draw = ImageDraw.Draw(out)
    for v in range(0, 1001, 50):
        x, y = margin + v / 1000 * (w - 1), margin + v / 1000 * (h - 1)
        major = v % 100 == 0
        tick = margin // 2 if major else margin // 4
        for (ax, ay, bx, by) in ((x, margin - tick, x, margin), (x, margin + h, x, margin + h + tick),
                                 (margin - tick, y, margin, y), (margin + w, y, margin + w + tick, y)):
            draw.line([ax, ay, bx, by], fill=(0, 0, 0), width=2 if major else 1)
        if major:
            draw.text((x - 8, 2), str(v), fill=(0, 0, 0))
            draw.text((x - 8, margin + h + tick), str(v), fill=(0, 0, 0))
            draw.text((2, y - 6), str(v), fill=(0, 0, 0))
            draw.text((margin + w + tick + 1, y - 6), str(v), fill=(0, 0, 0))
    return out
