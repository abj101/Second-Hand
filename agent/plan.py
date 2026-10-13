"""Event-triggered planner: the model keeps its goal tree, worked backwards from its top goals to one focus it can act on now."""

from dataclasses import dataclass

SYSTEM = ("You plan for a player of a video game they have never seen. The top goal is to finish the game. "
  "You keep a goal tree. For each active goal, work backwards: ask 'what must be true just before this can happen?' and add that as a child goal, "
  "until you reach a goal that can be started from where the player is now. That leaf is the focus. "
  "Goals are outcomes in the game world: reach a place, find or talk to someone, get or use something, win something. "
  "Never make a goal out of pressing a button, moving in a direction or retrying an input; the player chooses inputs. "
  "When nothing tells you where to go, the goal is to explore: reach parts of the map you have not seen (the map counts untried edges into unseen cells), find exits, and go through exits you have not used. "
  "Use only what the screen, the map, the log or the known facts show; if a goal is a guess, mark its source assumed. Never invent places, items or routes. "
  "Do not re-create a goal the log says is done. Keep goals that still hold and change only what the trigger calls for. "
  "When a focus is blocked, choose a different way to reach its parent (another exit, another person, another object) and say why. "
  "When the trigger is a cycle or an action that failed twice, the current focus is not working: mark it blocked and pick a different approach, not a rewording of the same one. "
  "When the trigger is arriving somewhere new, re-check which goals this place serves before keeping the old focus. Return only JSON.")
REPLY = ('Reply JSON: {"goals": list of edits {"op":"set"|"remove","section":"goals","key":short id,'
  '"value":{"text","parent":key of the goal this serves or null,"done_when":"one observable sign it is done","source":"saw|heard|assumed","evidence","status":"todo|active|done|blocked|dropped"}}, '
  '"focus": key of the one leaf goal to work on now, "lesson": one short lesson learned or null, "why": one sentence}.')
SCHEMA = {"type": "object", "properties": {"goals": {"type": "array", "items": {"type": "object"}}, "focus": {"type": "string"},
  "lesson": {"type": ["string", "null"]}, "why": {"type": "string"}}, "required": ["goals", "focus", "why"]}

@dataclass
class Plan:
    ops: list; focus: str; lesson: str | None; why: str

def plan(model, trigger: str, notebook, view: str, screen_text: list, recent: list[str]) -> Plan:
    lines = [f"Trigger: {trigger}", f"Goal tree (focus: {notebook.focus or 'none'}):\n{notebook.render()}",
             f"Log (what already happened):\n{notebook.render_log(40)}", f"Map and memory:\n{view or 'none'}",
             f"Text on screen now: {'; '.join(str(t) for t in screen_text) or 'none'}",
             f"Recent actions: {'; '.join(recent) or 'none'}", REPLY]
    data = model.ask(SYSTEM, "\n".join(lines), [], SCHEMA, role="plan").data
    data = data if isinstance(data, dict) else {}
    ops = [dict(o, section="goals") for o in data.get("goals") or [] if isinstance(o, dict)] if isinstance(data.get("goals"), list) else []
    lesson = data.get("lesson")
    return Plan(ops=ops, focus=str(data.get("focus") or "").strip(), lesson=lesson.strip()[:160] if isinstance(lesson, str) and lesson.strip() else None,
                why=str(data.get("why") or ""))
