"""One structured model call per step: read the screen and notebook, name the place, edit the notebook, pick an action."""

from dataclasses import dataclass

from agent.observer import for_model
from agent.predict import EFFECTS

SYSTEM = ("You are playing a video game you have never seen, from screenshots only. Your goal is to finish the game. "
  "A planner keeps your goals; you work on the focus goal at the end of the goal path. Read every piece of on-screen text. "
  "The log lists what already happened, including what people said: do not repeat finished conversations or actions unless the focus needs it. "
  "When the focus's done_when sign is visible, mark it done with the evidence; when you have tried it several ways and it cannot work from here, mark it blocked and say why. "
  "Treat your notes as hypotheses: prefer what you see now over what you wrote earlier. "
  "If you are stuck or an action keeps failing, try something different and write down what you learned. Return only JSON.")
REPLY = ('Reply JSON: {"place": short name for where you are (reuse a known place name if it is the same place), '
  '"notebook": list of edits: {"op":"focus","status":"done"|"blocked","evidence":"what you saw"}, {"op":"tried","text":"what you just tried for the focus"}, or {"op":"set"|"remove","section":"hints"|"lessons","key":short id,"value":{...}}, '
  '"action": {"thing": n, "verb": "approach"|"interact"|"select"|"click"}, {"verb": "advance"|"cancel"|"type"}, {"go_to": name of a thing on screen, remembered thing or known place}, or {"buttons": up to 8 of %s}; '
  '"text": text to type when the verb is type, else null, "expect": one of %s, "why": one sentence}. '
  'On a dialogue or menu screen, use advance, select or cancel. On a text field with an on-screen keyboard, use type with the text. Never pick letter buttons one by one. '
  'Hint values: {"text","source","checked"}. Lesson values: {"text"}. '
  'Only edit the notebook when something changed.')
SCHEMA = {"type": "object", "properties": {"place": {"type": "string"}, "notebook": {"type": "array", "items": {"type": "object"}},
  "action": {"type": "object"}, "text": {"type": ["string", "null"]}, "expect": {"type": "string"}, "why": {"type": "string"}},
  "required": ["place", "notebook", "action", "expect", "why"]}

@dataclass
class Act:
    place: str; ops: list; action: dict | None; text: str | None; expect: str; why: str

def _action(raw, things: list, buttons: list[str]) -> dict | None:
    if not isinstance(raw, dict): return None
    n, verb = raw.get("thing"), raw.get("verb")
    if type(n) is int and 0 <= n < len(things) and verb in ("approach", "interact", "select", "click"):
        return {"thing": n, "verb": verb}
    if "thing" not in raw and verb in ("advance", "cancel", "type"): return {"verb": verb}
    if isinstance(raw.get("go_to"), str) and raw["go_to"].strip(): return {"go_to": raw["go_to"].strip()}
    picks = raw.get("buttons")
    if isinstance(picks, list) and picks[:8]:
        known = {b.casefold(): b for b in buttons}
        names = [known.get(str(b).strip().casefold()) for b in picks[:8]]
        if all(names): return {"buttons": names}
    return None

def act(model, image, perception: dict, notebook: str, facts: str, flags: list[str], controls: str, prefs: dict, buttons: list[str], places: list[str] = ()) -> Act:
    things = perception.get("entities") or []
    lines = [f"Notebook:\n{notebook}", f"Known facts:\n{facts or 'none'}", f"Flags: {'; '.join(flags) or 'none'}", f"Controls:\n{controls}",
             f"Preferences: {prefs}", f"Known places: {', '.join(places[:12]) or 'none'}", f"Screen: {perception.get('screen', '')}", f"Text on screen: {'; '.join(str(t) for t in perception.get('text') or [])}", "Things on screen:"]
    lines += [f"{i}: {e.get('name', '')} ({e.get('kind', '')}) at {e.get('at', '')}" + (" ← you" if e.get("kind", "").casefold() == "player" else "") for i, e in enumerate(things)]
    lines.append(REPLY % (", ".join(buttons), ", ".join(EFFECTS)))
    reply = model.ask(SYSTEM, "\n".join(lines), [for_model(image)] if image is not None else [], SCHEMA, role="act")
    data = reply.data if isinstance(reply.data, dict) else {}
    place, text, ops = data.get("place"), data.get("text"), data.get("notebook")
    return Act(place=place.strip()[:40] if isinstance(place, str) else "",
               ops=[o for o in ops if isinstance(o, dict)] if isinstance(ops, list) else [],
               action=_action(data.get("action"), things, buttons),
               text=text if isinstance(text, str) else None,
               expect=data["expect"] if data.get("expect") in EFFECTS else "screen_changes",
               why=str(data.get("why") or ""))
