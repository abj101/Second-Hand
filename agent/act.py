"""One structured model call per step: read the screen and notebook, name the place, edit the notebook, pick an action."""

from dataclasses import dataclass

from agent.observer import for_model
from agent.predict import EFFECTS

SYSTEM = ("You are playing a video game you have never seen, from screenshots only. Your goal is to finish the game. "
  "A planner keeps your goals; you work on the focus goal at the end of the goal path. Read every piece of on-screen text. "
  "The log lists what already happened, including what people said: do not repeat finished conversations or actions unless the focus needs it. "
  "When the focus's done_when sign is visible, mark it done with the evidence; when you have tried it several ways and it cannot work from here, mark it blocked and say why. "
  "Treat your notes as hypotheses: prefer what you see now over what you wrote earlier. "
  "Flags list actions that already failed here: after one failure try something different, and never repeat an action that failed twice here. "
  "If you are stuck or an action keeps failing, try something different and write down what you learned. Return only JSON.")
REPLY = ('Reply JSON: {"place": short descriptive name for where you are, from what you see (e.g. "bedroom", "kitchen", "town"; never a screen type like world, dialogue or menu; reuse a known place name only if it is the same place), '
  '"notebook": list of edits: {"op":"focus","status":"done"|"blocked","evidence":"what you saw"}, {"op":"tried","text":"what you just tried for the focus"}, or {"op":"set"|"remove","section":"hints"|"lessons","key":short id,"value":{...}}, '
  '"action": {"thing": n, "verb": "approach"|"interact"|"select"|"click"}, {"verb": "advance"|"cancel"|"type"}, {"go_to": name of a thing on screen, remembered thing or known place, or "unexplored" to walk to the nearest part of this place you have not explored}, or {"buttons": up to 8 of %s}; '
  '"text": text to type when the verb is type, else null, "expect": one of %s, "why": one sentence}. '
  'On a dialogue or menu screen, use advance, select or cancel. If a menu offers ready-made choices (such as suggested names), picking one is fine and quicker than typing. '
  'On a text field with an on-screen keyboard, use {"verb": "type"} with the text in "text". Never pick letter buttons one by one. '
  'Hint values: {"text","source","checked"}. Lesson values: {"text"}. '
  'Only edit the notebook when something changed.')
OPTION_NOTE = " The option notes are what already happened here; prefer options that have not failed."
REPLY_OPTIONS = REPLY.replace('"action": {"thing": n,', '"action": {"i": n} to pick one option by number (preferred); use the free shapes only if no option fits (the buttons option and any \'info\' line are answered with the free shapes, e.g. {"buttons": [...]}): {"thing": n,', 1)
SCHEMA = {"type": "object", "properties": {"place": {"type": "string"}, "notebook": {"type": "array", "items": {"type": "object"}},
  "action": {"type": "object"}, "text": {"type": ["string", "null"]}, "expect": {"type": "string"}, "why": {"type": "string"}},
  "required": ["place", "notebook", "action", "expect", "why"]}

@dataclass
class Act:
    place: str; ops: list; action: dict | None; text: str | None; expect: str; why: str; option: int | None = None

def _pick(raw, options) -> dict | None:  # {"i": n} -> option n, when it has an action (the buttons and info rows do not)
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

def act(model, image, perception: dict, notebook: str, facts: str, flags: list[str], controls: str, prefs: dict, buttons: list[str], places: list[str] = (), options: list[dict] | None = None) -> Act:
    things = perception.get("entities") or []
    lines = [f"Notebook:\n{notebook}", f"Known facts:\n{facts or 'none'}", f"Flags: {'; '.join(flags) or 'none'}", f"Controls:\n{controls}",
             *([f"Preferences: {prefs}"] if prefs else []), f"Known places: {', '.join(places[:12]) or 'none'}", f"Screen: {perception.get('screen', '')}", f"Text on screen: {'; '.join(str(t) for t in perception.get('text') or [])}"]
    if options:
        lines.append("Options:")
        lines += [f"{o['n']}. {o['tool']} {o['label']}" + (f" — {'; '.join(o['notes'])}" if o["notes"] else "") for o in options]
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
    return Act(place=place.strip()[:40] if isinstance(place, str) else "",
               ops=[o for o in ops if isinstance(o, dict)] if isinstance(ops, list) else [],
               action=dict(pick["action"]) if pick else _action(data.get("action"), things, buttons),
               text=text if isinstance(text, str) else None,
               expect=data["expect"] if data.get("expect") in EFFECTS else "screen_changes",
               why=str(data.get("why") or ""), option=pick["n"] if pick else None)
