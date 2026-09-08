from __future__ import annotations

import json
from typing import Any, Callable

from PIL import Image

from loop.os.sts_stats import extract_sts_stats


def register_hud_tools(
    registry: Any,
    *,
    image_getter: Callable[[], Image.Image | None],
) -> None:
    def _read(**_: Any) -> str:
        image = image_getter() if callable(image_getter) else None
        if image is None:
            return json.dumps({"error": "no frame"})
        return json.dumps(extract_sts_stats(image))

    registry.register(
        "hud_read",
        description=(
            "Authoritative STS2 HUD stats from OCR: screen, turn, player HP/energy/block, "
            "enemy HP, tutorial step. Call before combat/tutorial decisions."
        ),
        parameters={},
        handler=_read,
    )
