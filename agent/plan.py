"""Build planner prompts and return their proposed notebook operations."""

from dataclasses import dataclass

SYSTEM = (
    "You are the strategist in an unfamiliar game; quests are outcomes in the game world, never buttons, directions, or coordinates, and another model chooses the moves. "
    "Reply with operations that change your notebook, or with no operations when nothing should change; "
    "each operation is applied in order or answered with an error; you see the results next time. "
    "A quest's why cites the t-keys the quest comes from; if no game text supports it, give one sentence. "
    "Give a quest a target when it means being in a place, going through an exit, or dealing with something the map names (someone to talk to or a thing to check); target unexplored when the outcome is finding somewhere new. A place or exit target closes by itself when you arrive; unexplored closes when you enter a place you have not been before; any other target stays open until you close it with done. "
    "Close a quest with done or drop and a reason; the first open quest is worked on now, and top changes it; "
    "when game text names something or somewhere you can see on the map, you may name it with op name, citing the text; such a name stays a guess (shown 'maybe') until the game itself names it when you talk to it. Return only JSON."
)
REPLY = (
    'Reply JSON: {"ops":[{"op":"add|done|drop|reopen|top|target|name","key":"id",'
    '"text":"text","why":"t-keys or one sentence when no game text supports the quest","target":"place-id, thing-id or unexplored",'
    '"reason":"reason"}],"why":"why this plan"}. '
    "Use only fields needed by each operation; include at most 8 operations. Top, target, done and drop take an open quest key (add creates one); done and drop need a reason sentence; name's key is a map label."
)
SCHEMA = {
    "type": "object",
    "properties": {
        "ops": {
            "type": "array",
            "maxItems": 8,
            "items": {
                "type": "object",
                "properties": {
                    "op": {"type": "string", "enum": ["add", "done", "drop", "reopen", "top", "target", "name"]},
                    "key": {"type": "string", "maxLength": 40},
                    "text": {"type": "string"},
                    "why": {"oneOf": [
                        {"type": "string"},
                        {"type": "array", "items": {"type": "string"}},
                    ]},
                    "target": {"type": "string"},
                    "reason": {"type": "string"},
                },
            },
        },
        "why": {"type": "string", "maxLength": 200},
    },
    "required": ["ops", "why"],
}


@dataclass
class Plan:
    ops: list | None
    why: str


def plan(model, trigger: str, notebook, view: str, screen_text: list, recent: list[str], evidence: str | None = None) -> Plan:
    prompt = "\n".join((
        f"Trigger: {trigger}", notebook.render(), f"Evidence since last plan: {evidence or 'none'}",
        f"Map and memory: {view or 'none'}",
        f"Text on screen now: {'; '.join(str(text) for text in screen_text) or 'none'}",
        f"Recent actions: {'; '.join(recent) or 'none'}", REPLY,
    ))
    data = model.ask(SYSTEM, prompt, [], SCHEMA, role="plan").data
    data = data if isinstance(data, dict) else {}
    ops = data.get("ops")
    why = data.get("why")
    return Plan(ops if isinstance(ops, list) else None, why[:200] if isinstance(why, str) else "")
