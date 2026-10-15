"""Perceiver: one screenshot -> visible text, place and entities (spec §6).

Models answer on a 0-1000 grid (Gemma and Qwen both ground natively on it); the output here is
normalized to [0, 1] screen fractions. Nothing returned is a fact until it is verified (spec §2).
"""

from __future__ import annotations

from PIL import Image

from agent.locate import rulers
from agent.model import Ollama, Reply

PLACEMENTS = ("top wall", "bottom wall", "left wall", "right wall", "room edge", "screen edge", "middle")

SYSTEM = (
    "You look at a single screenshot of a video game you may have never seen. "
    "Describe exactly what is on screen. Do not suggest actions."
)

PLACE_LINE = '\n- "place": a short name for where the player is, specific enough to tell it from similar places (such as "upstairs bedroom" or "town square", not just "house"); null when no map is shown.'
PROMPT = """Return JSON with:
- "screen": one of world, dialogue, menu, keyboard, battle, title, other.""" + PLACE_LINE + """
- "text": every piece of readable text on screen, verbatim.
- "entities": each distinct thing on screen (characters, objects, buttons, icons, cards, menu items), as
  {"kind": short category, "name": its name or visible label, "at": [x, y], "placement": optional position in the room}.

For placement, use only top wall, bottom wall, left wall, right wall, room edge, screen edge, or middle, based on what is visible.

Coordinates are the centre of the thing on a 0-1000 grid: [0, 0] is top-left, [1000, 1000] is bottom-right.
Report only what is visible.
If the player's own character is visible, list it as an entity with kind "player".
List each distinct thing separately, including small icons.
List every visible passage (door, stairs, door mat, opening) as an entity named "exit: <what it is>", placed on the tile to step onto. Set "selectable": true only for an item the player can choose right now in a menu, list, or yes/no prompt; omit it for scenery, characters, objects behind a dialogue box, text, and cursors."""

_POINT = {"type": "array", "items": {"type": "number"}}
SCHEMA = {
    "type": "object",
    "properties": {
        "screen": {"type": "string", "enum": ["world", "dialogue", "menu", "keyboard", "battle", "title", "other"]},
        "place": {"type": ["string", "null"]},
        "text": {"type": "array", "items": {"type": "string"}},
        "entities": {"type": "array", "items": {"type": "object", "properties": {
            "kind": {"type": "string"}, "name": {"type": "string"}, "at": _POINT, "placement": {"type": "string", "enum": list(PLACEMENTS)}, "selectable": {"type": "boolean"}},
            "required": ["kind", "name", "at"]}},
    },
    "required": ["screen", "text", "entities"],
}


RULER_NOTE = ("\n\nThe screenshot is framed by rulers marking its 0-1000 grid. "
              "Read coordinates off the rulers; the white margin is not part of the screen.")


def perceive(model: Ollama, image: Image.Image, context: str = "", *, with_rulers: bool = False) -> tuple[dict, Reply]:
    prompt = PROMPT + (RULER_NOTE if with_rulers else "") + (f"\n\n{context}" if context else "")
    reply = model.ask(SYSTEM, prompt, [rulers(image) if with_rulers else image], SCHEMA, role="perceiver")
    return normalize(reply.data), reply


def _point(value) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    try:
        x, y = (float(v) for v in value)
    except (TypeError, ValueError):
        return None
    return [min(max(x / 1000, 0.0), 1.0), min(max(y / 1000, 0.0), 1.0)]


def normalize(data: dict | None) -> dict:
    """Model JSON -> screen fractions; drops rows without a usable point. Never raises on bad output."""
    data = data or {}
    text = data.get("text") or []
    if isinstance(text, str): text = text.splitlines()  # Gemma sometimes ignores the array schema
    screen = data.get("screen")
    out = {"screen": screen if screen in ("world", "dialogue", "menu", "keyboard", "battle", "title", "other") else "other", "text": [str(t) for t in text if t],
           "entities": [], **({"place": data["place"].strip()[:40]} if isinstance(data.get("place"), str) and data["place"].strip() else {})}
    for e in data.get("entities") or []:
        if isinstance(e, dict) and (at := _point(e.get("at"))):
            out["entities"].append({"kind": str(e.get("kind") or ""), "name": str(e.get("name") or ""), "at": at,
                                    **({"placement": e["placement"]} if e.get("placement") in PLACEMENTS else {}),
                                    **({"selectable": True} if e.get("selectable") is True else {})})
    return out
