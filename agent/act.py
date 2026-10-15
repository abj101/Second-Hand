"""One structured model call per step: read the screen and notebook, name the place, edit the notebook, pick an action."""

import re
from dataclasses import dataclass

from agent.observer import for_model
from agent.predict import EFFECTS

SYSTEM = ("You play a video game you have never seen, from screenshots only. Your goal is to finish the game. "
  "A planner keeps your quests; work on the top quest. Other open quests are listed; if an option would finish one of them now, you may take it. Read all on-screen text. "
  "The log lists what already happened, people's words included: do not repeat finished conversations or actions unless the focus needs them. "
  "Treat your notes as hypotheses: what you see now beats what you wrote. "
  "Flags list actions that already failed here: after one failure try something different and never repeat one that failed twice. "
  "Return only JSON.")
REPLY = ('Reply JSON: {"place": short name for where you are, from what you see (never a screen type like world, dialogue or menu; reuse a known place name only for the same place), '
  '"action": {"thing": n, "verb": "approach"|"interact"|"select"|"click"}, {"verb": "advance"|"cancel"|"type"}, {"go_to": a thing on screen or remembered, a known place, or "unexplored" for the nearest unexplored part of this place}, or {"buttons": up to 8 of %s}; '
  '"text": text to type for verb type, else null, "expect": one of %s, "why": one sentence}. '
  'On a dialogue or menu screen, use advance, select or cancel; a ready-made choice (such as suggested names) beats typing. '
  'On a text field with an on-screen keyboard, use {"verb": "type"} with the text in "text". Never pick letter buttons one by one.')
OPTION_NOTE = (" The option notes are what already happened here; prefer options that have not failed. "
  "The free buttons option and the options that list names are answered with {\"buttons\": [...]} or {\"go_to\": \"<name>\"}.")
REPLY_OPTIONS = REPLY.replace('"action": {"thing": n,', '"action": {"i": n} to pick one option by number (preferred); the free shapes only if no option fits: {"thing": n,', 1)
SCHEMA = {"type": "object", "properties": {"place": {"type": "string"},
  "action": {"type": "object"}, "text": {"type": ["string", "null"]}, "expect": {"type": "string"}, "why": {"type": "string"}},
  "required": ["place", "action", "expect", "why"]}

@dataclass
class Act:
    place: str; ops: list; action: dict | None; text: str | None; expect: str; why: str; option: int | None = None

def _pick(raw, options) -> dict | None:  # {"i": n} -> option n, when it has an action (the free buttons row and the name-list rows have no action)
    n = raw.get("i")
    return options[n - 1] if options and type(n) is int and 1 <= n <= len(options) and options[n - 1]["action"] is not None else None

def _action(raw, things: list, buttons: list[str]) -> dict | None:
    if not isinstance(raw, dict): return None
    n, verb = raw.get("thing"), raw.get("verb")
    if type(n) is int and 0 <= n < len(things) and verb in ("approach", "interact", "select", "click"):
        return {"thing": n, "verb": verb}
    if "thing" not in raw and verb in ("advance", "cancel", "type"): return {"verb": verb}
    if isinstance(raw.get("type"), str) and raw["type"].strip(): return {"verb": "type"}  # {"type": "RED"}: the model's own shape
    if isinstance(raw.get("go_to"), str) and raw["go_to"].strip(): return {"go_to": raw["go_to"].strip()}
    picks = raw.get("buttons")
    if isinstance(picks, list) and picks[:8]:
        known = {b.casefold(): b for b in buttons}
        names = [known.get(str(b).strip().casefold()) for b in picks[:8]]
        if all(names): return {"buttons": names}
    return None

def _option_text(o: dict) -> str:  # the tool word once: the label alone when its first words already hold the tool, spaced or raw
    head, w = o["label"].casefold().replace(":", " "), o["tool"].replace("_", " ").casefold()
    return o["label"] if any(f" {t} " in f" {' '.join(head.split()[:3])} " for t in (w, o["tool"].casefold())) else f"{o['tool']} {o['label']}"

DECIDE_SYSTEM = ("You choose the next action in a video game. You do not see the screen: the facts below come from the game's map memory "
  "and a screen reader, and they are true. The beliefs and planner note below are hypotheses; what you see now takes priority. A planner keeps your quests; work on the top quest. Other open quests are listed; if an option would finish one of them now, you may take it. Pick exactly one of the numbered options. "
  "An option whose notes say it failed or had no effect should not be picked again while another option could still work. "
  "Return only JSON.")

def _ask_valid(model, system, user, schema, check):
    """One call; if check(data) returns (None, reason), one retry naming the reason. Cloud models do not enforce schemas, so this is the guarantee."""
    for retry in (False, True):
        data = model.ask(system, user + (f"\nYour last reply was invalid: {reason}. Reply again." if retry else ""), [], schema, role="act").data
        value, reason = check(data if isinstance(data, dict) else {})
        if value is not None: return value, data
    return None, data

def _decide(model, perception, notebook, facts, flags, controls, prefs, buttons, options) -> Act:
    """Text-only act: the reply is {"why", "i"} only; a row with no ready action (free buttons, a list of names, typing) gets one follow-up whose answer is an enum too."""
    ns = [o["n"] for o in options]
    lines = [f"Goals:\n{notebook}", f"Known facts:\n{facts or 'none'}", f"Flags: {'; '.join(flags) or 'none'}", f"Screen: {perception.get('screen', '')}",
             f"Text on screen: {'; '.join(str(t) for t in perception.get('text') or []) or 'none'}", "Options:",
             *[f"{o['n']}. {_option_text(o)}" + (f" — {'; '.join(o['notes'])}" if o["notes"] else "") for o in options],
             'Reply JSON: {"why": one sentence, "i": the option number}']
    schema = {"type": "object", "properties": {"why": {"type": "string", "maxLength": 200}, "i": {"type": "integer", "enum": ns}}, "required": ["why", "i"], "additionalProperties": False}
    def pick(d):
        n = d.get("i"); n = int(n) if isinstance(n, str) and n.strip().isdigit() else n
        return (n, None) if type(n) is int and n in ns else (None, f'"i" must be one of {ns}')
    n, data = _ask_valid(model, DECIDE_SYSTEM, "\n".join(lines), schema, pick)
    why = str(data.get("why") or "") if isinstance(data, dict) else ""
    o = next((o for o in options if o["n"] == n), None)
    action, text, chosen = (dict(o["action"]) if o and o["action"] else None), None, f"You picked option {n}: {_option_text(o)}." if o else ""
    if o and o["tool"] == "buttons":
        schema = {"type": "object", "properties": {"buttons": {"type": "array", "minItems": 1, "maxItems": 8, "items": {"type": "string", "enum": list(buttons)}}}, "required": ["buttons"], "additionalProperties": False}
        ok = lambda d: (d["buttons"], None) if isinstance(d.get("buttons"), list) and 0 < len(d["buttons"]) <= 8 and all(b in buttons for b in d["buttons"]) else (None, f'"buttons" must be 1-8 of {buttons}')
        picks, _ = _ask_valid(model, DECIDE_SYSTEM, "\n".join(lines[:5] + [f"Controls:\n{controls}", chosen, f'Reply JSON: {{"buttons": up to 8 of {", ".join(buttons)}, in order}}']), schema, ok)
        action = {"buttons": picks} if picks else None
    elif o and o["action"] is None and ": " in o["label"]:  # a row that lists names: pick one
        names = [re.sub(r" ×\d+$", "", s) for s in o["label"].split(": ", 1)[1].split(", ")]
        schema = {"type": "object", "properties": {"name": {"type": "string", "enum": names}}, "required": ["name"], "additionalProperties": False}
        name, _ = _ask_valid(model, DECIDE_SYSTEM, "\n".join(lines[:5] + [chosen, f'Reply JSON: {{"name": one of {", ".join(names)}}}']), schema,
                             lambda d: (d["name"], None) if d.get("name") in names else (None, f'"name" must be one of {names}'))
        action = {"go_to": name} if name else None
    elif action == {"verb": "type"}:
        schema = {"type": "object", "properties": {"text": {"type": "string", "minLength": 1, "maxLength": 40}}, "required": ["text"], "additionalProperties": False}
        text, _ = _ask_valid(model, DECIDE_SYSTEM, "\n".join(lines[:5] + [f"Preferences: {prefs}", chosen, 'Reply JSON: {"text": the text to type}']), schema,
                             lambda d: (d["text"].strip()[:40], None) if isinstance(d.get("text"), str) and d["text"].strip() else (None, '"text" must be a non-empty string'))
        action = action if text else None
    return Act(place="", ops=[], action=action, text=text, expect="screen_changes", why=why, option=n if o else None)

def act(model, image, perception: dict, notebook: str, facts: str, flags: list[str], controls: str, prefs: dict, buttons: list[str], places: list[str] = (), options: list[dict] | None = None, decide: bool = False) -> Act:
    if decide and options: return _decide(model, perception, notebook, facts, flags, controls, prefs, buttons, options)
    things = perception.get("entities") or []
    lines = [f"Notebook:\n{notebook}", f"Known facts:\n{facts or 'none'}", f"Flags: {'; '.join(flags) or 'none'}", f"Controls:\n{controls}",
             *([f"Preferences: {prefs}"] if prefs else []), f"Known places: {', '.join(places[:12]) or 'none'}", f"Screen: {perception.get('screen', '')}", f"Text on screen: {'; '.join(str(t) for t in perception.get('text') or [])}"]
    if options:
        lines.append("Options:")
        lines += [f"{o['n']}. {_option_text(o)}" + (f" — {'; '.join(o['notes'])}" if o["notes"] else "") for o in options]
    else:
        lines.append("Things on screen:")
        lines += [f"{i}: {e.get('name', '')} ({e.get('kind', '')}) at {e.get('at', '')}" + (" ← you" if e.get("kind", "").casefold() == "player" else "") for i, e in enumerate(things)]
    lines.append((REPLY_OPTIONS if options else REPLY) % (", ".join(buttons), ", ".join(EFFECTS)))
    reply = model.ask(SYSTEM + OPTION_NOTE if options else SYSTEM, "\n".join(lines), [for_model(image)] if image is not None else [], SCHEMA, role="act")
    data = reply.data if isinstance(reply.data, dict) else {}
    place, text, ops = data.get("place"), data.get("text"), data.get("notebook")
    raw = data.get("action") if isinstance(data.get("action"), dict) else {}
    if not (isinstance(text, str) and text.strip()):  # the text often comes inside the action: {"verb":"type","text":..} or {"type":..}
        text = next((v for v in (raw.get("text"), raw.get("type")) if isinstance(v, str) and v.strip() and v != "type"), None)
    pick = _pick(raw, options)
    why = next((v for v in (data.get("why"), raw.get("why")) if isinstance(v, str) and v.strip()), "")  # the model often nests why/expect in action
    expect = next((v for v in (data.get("expect"), raw.get("expect")) if v in EFFECTS), "screen_changes")
    return Act(place=place.strip()[:40] if isinstance(place, str) else "",
               ops=[o for o in ops if isinstance(o, dict)] if isinstance(ops, list) else [],
               action=dict(pick["action"]) if pick else _action(data.get("action"), things, buttons),
               text=text if isinstance(text, str) else None,
               expect=expect, why=why, option=pick["n"] if pick else None)
