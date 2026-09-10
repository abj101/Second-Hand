"""Client/content geometry. Perception follows the live frame; coords stay in [0, 1]."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image

from loop.os.capture import EncodedFrame, LongestSide, encode_image

ContentBox = tuple[float, float, float, float]
MIN_CLIENT_PX = 8
TARGET_ASPECT = 16 / 9


@dataclass(frozen=True)
class ScreenGeometry:
    """Native client size plus the playable content rect inside it."""

    client_w: int
    client_h: int
    content: ContentBox
    content_w: int
    content_h: int

    @property
    def aspect(self) -> float:
        return _aspect(self.client_w, self.client_h)

    @property
    def content_aspect(self) -> float:
        return _aspect(self.content_w, self.content_h)

    @property
    def size(self) -> tuple[int, int]:
        return (self.client_w, self.client_h)

    def size_changed(self, other: ScreenGeometry | None, *, slack: int = 2) -> bool:
        if other is None:
            return True
        return (
            abs(self.client_w - other.client_w) > slack
            or abs(self.client_h - other.client_h) > slack
            or abs(self.content_w - other.content_w) > slack
            or abs(self.content_h - other.content_h) > slack
        )

    def line(self) -> str:
        x0, y0, x1, y1 = self.content
        return (
            f"client={self.client_w}x{self.client_h} aspect={self.aspect:.3f} "
            f"content={self.content_w}x{self.content_h} "
            f"box={x0:.2f},{y0:.2f},{x1:.2f},{y1:.2f}"
        )


def _aspect(width: float, height: float) -> float:
    return float(width) / max(float(height), 1e-6)


def letterbox_frac(image: Image.Image, *, threshold: float = 6.0) -> ContentBox:
    """Client-normalized (x0, y0, x1, y1) of the non-black game rect."""
    gray = np.asarray(image.convert("L"), dtype=np.float32)
    if gray.size == 0:
        return (0.0, 0.0, 1.0, 1.0)
    h, w = gray.shape
    rows = gray.mean(axis=1) > threshold
    cols = gray.mean(axis=0) > threshold
    ys = np.flatnonzero(rows)
    xs = np.flatnonzero(cols)
    if ys.size == 0 or xs.size == 0:
        return (0.0, 0.0, 1.0, 1.0)
    x0, x1 = float(xs[0]) / w, float(xs[-1] + 1) / w
    y0, y1 = float(ys[0]) / h, float(ys[-1] + 1) / h
    if (x1 - x0) < 0.5 or (y1 - y0) < 0.5:
        return (0.0, 0.0, 1.0, 1.0)
    return (x0, y0, x1, y1)


def fit_aspect_box(
    width: int,
    height: int,
    *,
    aspect: float = TARGET_ASPECT,
) -> ContentBox:
    """Largest centered `aspect` rectangle inside width×height, as client fractions."""
    if width <= 0 or height <= 0:
        return (0.0, 0.0, 1.0, 1.0)
    have = _aspect(width, height)
    if abs(have - aspect) / aspect < 1e-4:
        return (0.0, 0.0, 1.0, 1.0)
    if have > aspect:
        content_w = height * aspect
        pad = (width - content_w) / 2.0 / width
        return (pad, 0.0, 1.0 - pad, 1.0)
    content_h = width / aspect
    pad = (height - content_h) / 2.0 / height
    return (0.0, pad, 1.0, 1.0 - pad)


def box_is_full(box: ContentBox, *, slack: float = 0.01) -> bool:
    """True when *box* covers the client within *slack* on each edge."""
    return box[0] <= slack and box[1] <= slack and box[2] >= 1.0 - slack and box[3] >= 1.0 - slack


def boxes_close(left: ContentBox, right: ContentBox, *, slack: float = 0.02) -> bool:
    """True when two content boxes agree within *slack* per edge."""
    return all(abs(a - b) <= slack for a, b in zip(left, right, strict=True))


def is_top_left_scaled_capture(image: Image.Image, *, threshold: float = 6.0) -> bool:
    """True for PrintWindow-style corruption: game scaled into the top-left.

    Signature: non-black bounds anchored at the origin, substantial black on
    both the right and bottom, and similar x/y scale loss. Centered letterbox
    and full-client frames return False. Does not change ``letterbox_frac``.
    """
    box = letterbox_frac(image, threshold=threshold)
    x0, y0, x1, y1 = box
    if x0 > 0.02 or y0 > 0.02:
        return False
    if x1 > 0.85 or y1 > 0.85:
        return False
    width = x1 - x0
    height = y1 - y0
    if width < 0.5 or height < 0.5:
        return False
    if abs(width - height) > 0.08:
        return False
    return True


def content_box_for(image: Image.Image) -> ContentBox:
    """Letterbox if black bars exist; otherwise the full client is content."""
    letter = letterbox_frac(image)
    if not box_is_full(letter):
        return letter
    return (0.0, 0.0, 1.0, 1.0)


def crop_content(image: Image.Image, box: ContentBox | None = None) -> Image.Image:
    box = box if box is not None else content_box_for(image)
    if box == (0.0, 0.0, 1.0, 1.0):
        return image
    w, h = image.size
    x0, y0, x1, y1 = box
    left, top = int(x0 * w), int(y0 * h)
    right, bottom = int(x1 * w), int(y1 * h)
    if right <= left or bottom <= top:
        return image
    return image.crop((left, top, right, bottom))


def geometry_from_image(
    image: Image.Image,
    *,
    client: tuple[int, int] | None = None,
) -> ScreenGeometry:
    cw, ch = client if client is not None else image.size
    box = content_box_for(image)
    w, h = image.size
    return ScreenGeometry(
        client_w=max(0, int(cw)),
        client_h=max(0, int(ch)),
        content=box,
        content_w=max(1, int(round((box[2] - box[0]) * w))),
        content_h=max(1, int(round((box[3] - box[1]) * h))),
    )


def map_content_xy(x: float, y: float, box: ContentBox) -> tuple[float, float]:
    x0, y0, x1, y1 = box
    return (x0 + x * (x1 - x0), y0 + y * (y1 - y0))


def geometry_error(image: Image.Image) -> str | None:
    """None when the client has usable pixels. Aspect is not constrained."""
    w, h = image.size
    if w < MIN_CLIENT_PX or h < MIN_CLIENT_PX:
        return f"Client {w}x{h} is empty; restore the windowed game."
    return None


def format_geometry(image: Image.Image, box: ContentBox | None = None) -> str:
    geo = geometry_from_image(image)
    if box is not None:
        w, h = image.size
        content_w = max(1, int(round((box[2] - box[0]) * w)))
        content_h = max(1, int(round((box[3] - box[1]) * h)))
        geo = ScreenGeometry(
            client_w=geo.client_w,
            client_h=geo.client_h,
            content=box,
            content_w=content_w,
            content_h=content_h,
        )
    return geo.line()


def perceive_longest_side(image: Image.Image, cap: int | LongestSide = 1280) -> LongestSide:
    """Encode cap. `encode_image` keeps aspect; no upscale when native is smaller."""
    if cap == "native":
        return "native"
    native = max(1, max(image.size))
    limit = int(cap)
    if limit <= 0:
        return native
    return min(native, limit)


def encode_observe(
    image: Image.Image,
    *,
    cap: int | LongestSide = 1280,
    jpeg_quality: int = 90,
    fmt: str = "jpeg",
) -> EncodedFrame:
    return encode_image(
        image,
        longest_side=perceive_longest_side(image, cap),
        jpeg_quality=jpeg_quality,
        fmt=fmt,
    )


__all__ = [
    "ContentBox",
    "MIN_CLIENT_PX",
    "ScreenGeometry",
    "TARGET_ASPECT",
    "box_is_full",
    "boxes_close",
    "content_box_for",
    "crop_content",
    "encode_observe",
    "fit_aspect_box",
    "format_geometry",
    "geometry_error",
    "geometry_from_image",
    "is_top_left_scaled_capture",
    "letterbox_frac",
    "map_content_xy",
    "perceive_longest_side",
]
