"""Strategic game planning from remembered facts, beliefs, quests, and notes."""

import json
from dataclasses import dataclass

SYSTEM = (
    "You are the strategist for a player in an unfamiliar game. "
    "Quests describe outcomes in the game world, and Act chooses how to achieve them. "
    "Never name buttons, directions, coordinates, or a specific door in a quest. "
    "Every quest must cite why, using a fact or belief key. "
    "Beliefs are hypotheses, not facts. "
    "When new evidence arrives, say which beliefs it supports or contradicts, then revise or refute them with a reason. "
    "Keep refuted ideas ruled out. "
    "What the game said is a fact and outranks beliefs. "
    "A place you were told about may be a quest before it is mapped. "
    "Give every quest that names a place, a person, or words to hear a check: reach <place name>, speak <speaker>, or text <words>. "
    "On a stall, change something by revising a belief or reordering quests, and say why. "
    "Return the full ordered quest list with no more than five open quests; work on the first open quest now. "
    "Write a note of at most 400 characters explaining what changed in your understanding, why this order makes sense, and what you expect next. "
    "Return only JSON."
)
REPLY = (
    'Reply JSON: {"quests": list of {"key": short id up to 40 characters, "text": outcome, "why": fact or belief key(s), '
    '"done_when": observable sign, "status":"open"|"done"|"dropped"|"parked", "evidence":reason, '
    '"check":{"kind":"reach"|"speak"|"text","arg":string} optional}, '
    '"beliefs": list of {"key":short id, "text":hypothesis, "because":[fact or belief keys], "confidence":"low"|"mid"|"high", '
    '"status":"held"|"revised"|"refuted", "reason":why it changed}, '
    '"facts": list of {"key":short id up to 40 characters, "text":what was heard or seen, "from":speaker, "source":"heard"|"saw", "place":where}, '
    '"note": a note up to 400 characters, "why": one sentence explaining the plan}. '
)
_RECORD = {"type": "object", "propertyNames": {"type": "string", "maxLength": 40},
           "properties": {"key": {"type": "string", "maxLength": 40}}}
SCHEMA = {
    "type": "object",
    "properties": {
        "quests": {"type": "array", "items": {**_RECORD, "properties": {**_RECORD["properties"],
            "text": {"type": "string"}, "why": {"oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]},
            "done_when": {"type": "string"}, "status": {"enum": ["open", "done", "dropped", "parked"]},
            "evidence": {"type": "string"}, "check": {"type": "object", "properties": {
                "kind": {"enum": ["reach", "speak", "text"]}, "arg": {"type": "string"}}, "required": ["kind", "arg"]}}}},
        "beliefs": {"type": "array", "items": {**_RECORD, "properties": {**_RECORD["properties"],
            "text": {"type": "string"}, "because": {"type": "array", "items": {"type": "string"}},
            "confidence": {"enum": ["low", "mid", "high"]}, "status": {"enum": ["held", "revised", "refuted"]},
            "reason": {"type": "string"}}}},
        "facts": {"type": "array", "items": {**_RECORD, "properties": {**_RECORD["properties"],
            "text": {"type": "string"}, "from": {"type": "string"}, "source": {"enum": ["heard", "saw"]},
            "place": {"type": "string"}}}},
        "note": {"type": "string", "maxLength": 400},
        "why": {"type": "string"},
    },
    "required": ["quests", "beliefs", "facts", "note", "why"],
}
CHAPTER_SYSTEM = (
    "Compact the recent planning history into a short chapter memory. "
    "Summarize what changed, keep closed quests and ruled-out ideas useful for future decisions, and list unresolved leads. "
    "Do not rewrite open quests or held beliefs; those are preserved separately. Return only JSON."
)
CHAPTER_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "maxLength": 600},
        "ruled_out": {"type": "array", "items": {"type": "string"}},
        "open_leads": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "ruled_out", "open_leads"],
}


@dataclass
class Plan:
    quests: list[dict] | None
    beliefs: list[dict]
    facts: list[dict]
    note: str
    why: str


def _records(value):
    return [dict(item) for item in value if isinstance(item, dict)
            and all(isinstance(key, str) and len(key) <= 40 for key in item)
            and ("key" not in item or isinstance(item["key"], str) and len(item["key"]) <= 40)] if isinstance(value, list) else []


def _state(notebook):
    data = getattr(notebook, "data", {})
    return data if isinstance(data, dict) else {}


def _dump(value):
    return json.dumps(value, ensure_ascii=False, default=str)


def _chapter_end(state):
    chapters = state.get("chapters", [])
    ends = [chapter["to"] for chapter in chapters if isinstance(chapter, dict) and type(chapter.get("to")) is int] if isinstance(chapters, list) else []
    return max(ends, default=None)


def _chapter_log(notebook, limit):
    log = getattr(notebook, "log", [])
    if not isinstance(log, list):
        return []
    end = _chapter_end(_state(notebook))
    if end is not None:
        log = [event for event in log if isinstance(event, dict) and event.get("step", 0) > end]
    return log[-limit:]


def _closed_quests(state):
    end = _chapter_end(state)
    quests = state.get("quests", [])
    return [q for q in quests if isinstance(q, dict) and q.get("status") in ("done", "dropped")
            and (end is None or type(q.get("closed")) is int and q["closed"] > end)] if isinstance(quests, list) else []


def plan(model, trigger: str, notebook, view: str, screen_text: list, recent: list[str], evidence: str | None = None) -> Plan:
    state = _state(notebook)
    lines = [
        f"Trigger: {trigger}", f"New evidence: {evidence or 'none'}", f"Persisted notebook state:\n{notebook.render()}",
        f"Current chapter log span: {_dump(_chapter_log(notebook, 40))}", f"Map and memory: {view or 'none'}",
        f"Text on screen now: {'; '.join(str(t) for t in screen_text) or 'none'}",
        f"Recent actions: {'; '.join(recent) or 'none'}", REPLY,
    ]
    data = model.ask(SYSTEM, "\n".join(lines), [], SCHEMA, role="plan").data
    data = data if isinstance(data, dict) else {}
    quests = _records(data.get("quests")) if isinstance(data.get("quests"), list) else None
    for quest in quests or []:
        check = quest.get("check")
        if not (isinstance(check, dict) and check.get("kind") in ("reach", "speak", "text") and isinstance(check.get("arg"), str)):
            quest.pop("check", None)
    existing = state.get("beliefs", {})
    existing = existing if isinstance(existing, dict) else {}
    beliefs = []
    for belief in _records(data.get("beliefs")):
        confidence = belief.get("confidence")
        if confidence not in ("low", "mid", "high"):
            if "confidence" in belief and belief.get("status") not in ("revised", "refuted"):
                continue
            belief.pop("confidence", None)
            if belief.get("key") not in existing:
                belief["confidence"] = "low"
        beliefs.append(belief)
    note = data.get("note")
    return Plan(quests, beliefs, _records(data.get("facts")), note[:400] if isinstance(note, str) else "",
                data.get("why") if isinstance(data.get("why"), str) else "")


def chapter(model, notebook) -> dict | None:
    state = _state(notebook)
    quests = state.get("quests", [])
    prompt = "\n".join((
        f"Current notes: {_dump(state.get('notes', []))}",
        f"Current chapter log span: {_dump(_chapter_log(notebook, 200))}",
        f"Closed quests: {_dump(_closed_quests(state))}",
        f"Held beliefs and open quests (context only): {_dump({'beliefs': state.get('beliefs', {}), 'quests': [q for q in quests if isinstance(q, dict) and q.get('status') == 'open'] if isinstance(quests, list) else []})}",
        'Reply JSON: {"summary": up to 600 characters, "ruled_out": list of strings, "open_leads": list of strings}.',
    ))
    data = model.ask(CHAPTER_SYSTEM, prompt, [], CHAPTER_SCHEMA, role="chapter").data
    if not isinstance(data, dict) or not isinstance(data.get("summary"), str):
        return None
    if not all(isinstance(data.get(key), list) and all(isinstance(item, str) for item in data[key]) for key in ("ruled_out", "open_leads")):
        return None
    return {"summary": data["summary"][:600], "ruled_out": data["ruled_out"], "open_leads": data["open_leads"]}
