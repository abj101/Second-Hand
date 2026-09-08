from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Literal

from PIL import Image

LongestSide = int | Literal["native"]


@dataclass(frozen=True)
class EncodedFrame:
    data: bytes
    mime: str
    width: int
    height: int
    jpeg_quality: int | None
    longest_side: LongestSide
    original_width: int
    original_height: int

    @property
    def nbytes(self) -> int:
        return len(self.data)


def _scale(image: Image.Image, longest_side: LongestSide) -> Image.Image:
    if longest_side == "native":
        return image
    w, h = image.size
    longest = max(w, h)
    if longest <= longest_side:
        return image
    scale = longest_side / longest
    size = (max(1, int(w * scale)), max(1, int(h * scale)))
    return image.resize(size, Image.Resampling.LANCZOS)


def encode_image(
    image: Image.Image,
    *,
    longest_side: LongestSide = "native",
    jpeg_quality: int = 85,
    fmt: str = "jpeg",
) -> EncodedFrame:
    """Crop is the caller's job. This only downscales and compresses."""
    rgb = image.convert("RGB")
    original = rgb.size
    scaled = _scale(rgb, longest_side)
    buf = io.BytesIO()
    fmt_l = fmt.lower()
    if fmt_l in {"jpg", "jpeg"}:
        scaled.save(buf, format="JPEG", quality=jpeg_quality, optimize=True)
        mime = "image/jpeg"
        quality = jpeg_quality
    elif fmt_l == "webp":
        scaled.save(buf, format="WEBP", quality=jpeg_quality, method=4)
        mime = "image/webp"
        quality = jpeg_quality
    else:
        scaled.save(buf, format="PNG")
        mime = "image/png"
        quality = None
    return EncodedFrame(
        data=buf.getvalue(),
        mime=mime,
        width=scaled.size[0],
        height=scaled.size[1],
        jpeg_quality=quality,
        longest_side=longest_side,
        original_width=original[0],
        original_height=original[1],
    )


class FrameEncoder:
    def __init__(
        self,
        longest_side: LongestSide = "native",
        jpeg_quality: int = 85,
        fmt: str = "jpeg",
    ) -> None:
        self.longest_side = longest_side
        self.jpeg_quality = jpeg_quality
        self.fmt = fmt

    def encode(self, image: Image.Image) -> EncodedFrame:
        return encode_image(
            image,
            longest_side=self.longest_side,
            jpeg_quality=self.jpeg_quality,
            fmt=self.fmt,
        )
