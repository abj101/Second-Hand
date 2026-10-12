"""One structured model call per step: read the screen and notebook, name the place, edit the notebook, pick an action."""

from dataclasses import dataclass

from agent.observer import for_model
from agent.predict import EFFECTS

SYSTEM = ("You are playing a video game you have never seen, from screenshots only. Your goal is to finish the game. "
  "Work out what to do next the way a new player would: read every piece of on-screen text; when dialogue, signs, tutorials or menus tell you where to go or what to do, "
  "turn that into a goal in your notebook with the quote as evidence. Treat your notes as hypotheses: prefer what you see now over what you wrote earlier, and fix notes that turn out wrong. "
  "If you are stuck or an action keeps failing, try something different and write down what you learned. Return only JSON.")
REPLY = ('Reply JSON: {"place": short name for where you are (reuse a known place name if it is the same place), '
  '"notebook": list of edits {"op":"set"|"remove","section":"goals"|"hints"|"lessons","key":short id,"value":{...}}, '
  '"action": one of {"i": option number} or {"go_to": name of a thing on screen} or {"buttons": up to 8 of %s}, '
  '"text": text to type if the chosen option types text else null, "expect": one of %s, "why": one sentence}. '
  'Goal values: {"text","why","source":"saw|heard|assumed","evidence","status":"active|done|dropped"}. '
  'Hint values: {"text","source","checked"}. Lesson values: {"text"}. '
  'Only edit the notebook when something changed.')
SCHEMA = {"type": "object", "properties": {"place": {"type": "string"}, "notebook": {"type": "array", "items": {"type": "object"}},
  "action": {"type": "object"}, "text": {"type": ["string", "null"]}, "expect": {"type": "string"}, "why": {"type": "string"}},
  "required": ["place", "notebook", "action", "expect", "why"]}

@dataclass
class Act:
    place: str; ops: list; action: dict | None; text: str | None; expect: str; why: str

def _action(raw, affs: list, buttons: list[str]) -> dict | None:
    if not isinstance(raw, dict): return None
    if type(raw.get("i")) is int and 0 <= raw["i"] < len(affs): return {"i": raw["i"]}
    if isinstance(raw.get("go_to"), str) and raw["go_to"].strip(): return {"go_to": raw["go_to"].strip()}
    picks = raw.get("buttons")
    if isinstance(picks, list) and picks[:8]:
        known = {b.casefold(): b for b in buttons}
        names = [known.get(str(b).strip().casefold()) for b in picks[:8]]
        if all(names): return {"buttons": names}
    return None

def act(model, image, perception: dict, notebook: str, facts: str, flags: list[str], controls: str, prefs: dict, buttons: list[str], places: list[str] = ()) -> Act:
    affs, things = perception.get("affordances") or [], perception.get("entities") or []
    lines = [f"Notebook:\n{notebook}", f"Known facts:\n{facts or 'none'}", f"Flags: {'; '.join(flags) or 'none'}", f"Controls:\n{controls}",
             f"Preferences: {prefs}", f"Known places: {', '.join(places[:12]) or 'none'}", f"Screen: {perception.get('screen', '')}", f"Text on screen: {'; '.join(str(t) for t in perception.get('text') or [])}", "Things on screen:"]
    lines += [f"- {e.get('name', '')} ({e.get('kind', '')}) at {e.get('at', '')}" for e in things]
    lines += ["Options:"] + [f"{i}: {a.get('verb', '')} {a.get('target', '')} — {a.get('why', '')}" for i, a in enumerate(affs)]
    lines.append(REPLY % (", ".join(buttons), ", ".join(EFFECTS)))
    reply = model.ask(SYSTEM, "\n".join(lines), [for_model(image)] if image is not None else [], SCHEMA, role="act")
    data = reply.data if isinstance(reply.data, dict) else {}
    place, text, ops = data.get("place"), data.get("text"), data.get("notebook")
    return Act(place=place.strip()[:40] if isinstance(place, str) else "",
               ops=[o for o in ops if isinstance(o, dict)] if isinstance(ops, list) else [],
               action=_action(data.get("action"), affs, buttons),
               text=text if isinstance(text, str) else None,
               expect=data["expect"] if data.get("expect") in EFFECTS else "screen_changes",
               why=str(data.get("why") or ""))
