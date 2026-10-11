"""Perceiver: one screenshot -> visible text, entities and affordances (spec §6).

Models answer on a 0-1000 grid (Gemma and Qwen both ground natively on it); the output here is
normalized to [0, 1] screen fractions. Nothing returned is a fact until it is verified (spec §2).
"""

from __future__ import annotations

from PIL import Image

from agent.locate import rulers
from agent.model import Ollama, Reply

VERBS = ("approach", "interact", "select", "click", "drag", "advance", "cancel", "type", "press")

SYSTEM = (
    "You look at a single screenshot of a video game you may have never seen. "
    "Report what is on screen and every action the player could take right now."
)

PROMPT = """Return JSON with:
- "screen": a short name for this kind of screen, in your own words.
- "text": every piece of readable text on screen, verbatim.
- "entities": each distinct thing on screen (characters, objects, buttons, icons, cards, menu items), as
  {"kind": short category, "name": its name or visible label, "at": [x, y]}.
- "affordances": every action the player could take right now, as
  {"verb": one of %s, "target": entity name or input, "at": [x, y], "to": [x, y] only for drag, "why": expected result}.

Coordinates are the centre of the thing on a 0-1000 grid: [0, 0] is top-left, [1000, 1000] is bottom-right.
List each clickable thing separately (each card, each button, each menu item, each map node), including small icons.
Report only what is visible.""" % ", ".join(VERBS)

_POINT = {"type": "array", "items": {"type": "number"}}
SCHEMA = {
    "type": "object",
    "properties": {
        "screen": {"type": "string"},
        "text": {"type": "array", "items": {"type": "string"}},
        "entities": {"type": "array", "items": {"type": "object", "properties": {
            "kind": {"type": "string"}, "name": {"type": "string"}, "at": _POINT},
            "required": ["kind", "name", "at"]}},
        "affordances": {"type": "array", "items": {"type": "object", "properties": {
            "verb": {"type": "string"}, "target": {"type": "string"}, "at": _POINT, "to": _POINT,
            "why": {"type": "string"}}, "required": ["verb", "target", "at"]}},
    },
    "required": ["screen", "text", "entities", "affordances"],
}


RULER_NOTE = ("\n\nThe screenshot is framed by rulers marking its 0-1000 grid. "
              "Read coordinates off the rulers; the white margin is not part of the screen.")


def perceive(model: Ollama, image: Image.Image, context: str = "", *, with_rulers: bool = False) -> tuple[dict, Reply]:
    prompt = PROMPT + (RULER_NOTE if with_rulers else "") + (f"\n\n{context}" if context else "")
    reply = model.ask(SYSTEM, prompt, [rulers(image) if with_rulers else image], SCHEMA)
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
    out = {"screen": str(data.get("screen") or ""), "text": [str(t) for t in data.get("text") or [] if t],
           "entities": [], "affordances": []}
    for e in data.get("entities") or []:
        if isinstance(e, dict) and (at := _point(e.get("at"))):
            out["entities"].append({"kind": str(e.get("kind") or ""), "name": str(e.get("name") or ""), "at": at})
    for a in data.get("affordances") or []:
        if isinstance(a, dict) and (at := _point(a.get("at"))):
            row = {"verb": str(a.get("verb") or "").lower(), "target": str(a.get("target") or ""), "at": at,
                   "why": str(a.get("why") or "")}
            if to := _point(a.get("to")):
                row["to"] = to
            out["affordances"].append(row)
    return out
