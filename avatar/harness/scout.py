"""Persist the full-window grab used for CV layout / crop detection."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PIL import Image

from avatar.harness.capture import encode_image
from avatar.harness.layout import LayoutPlan
from avatar.harness.profile import CaptureProfile


def persist_layout_scout(
    scout_dir: Path,
    image: Image.Image,
    plan: LayoutPlan,
    profile: CaptureProfile,
    *,
    name: str = "scout",
) -> dict[str, Any]:
    """Write the layout scout frame and plan metadata under *scout_dir*."""
    scout_dir.mkdir(parents=True, exist_ok=True)
    frame = encode_image(
        image,
        longest_side=profile.scout_longest_side,
        jpeg_quality=profile.jpeg_quality,
        fmt=profile.fmt,
    )
    stem = name.strip() or "scout"
    img_path = scout_dir / f"{stem}.jpg"
    img_path.write_bytes(frame.data)
    meta = {
        "image": str(img_path.name),
        "source": plan.source,
        "screen_kind": plan.screen_kind,
        "crop": plan.crop,
        "longest_side": plan.longest_side,
        "jpeg_quality": plan.jpeg_quality,
        "scout_longest_side": profile.scout_longest_side,
        "original_width": frame.original_width,
        "original_height": frame.original_height,
        "width": frame.width,
        "height": frame.height,
        "bytes": frame.nbytes,
    }
    plan_path = scout_dir / f"{stem}.json"
    plan_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta
