"""Event-triggered planner: the model keeps its goal tree, worked backwards from its top goals to one focus it can act on now."""

from dataclasses import dataclass, field

SYSTEM = ("You plan for a player of a video game they have never seen. The top goal is to finish the game. "
  "You keep a goal tree. For each active goal, work backwards: ask 'what must be true just before this can happen?' and add that as a child goal, "
  "until you reach a goal that can be started from where the player is now. That leaf is the focus. "
  "Goals are outcomes in the game world: reach a place, find or talk to someone, get or use something, win something. "
  "A goal names an outcome an option can achieve (reach a place, use an exit, talk to someone), never a thing listed under \"Don't believe\". "
  "Never make a goal out of pressing a button, moving in a direction or retrying an input; the player chooses inputs. "
  "What the game tells you comes first. When text names a person, place or task (someone is looking for you, go somewhere, get something), add it as a goal with source heard, above exploring, and keep it until its done_when is seen or it is shown false. Explore only as a child of a goal, to find what that goal needs; with no such goal, explore to find one. An exit marked unverified or possible is only a guess; for a goal that needs somewhere new, go through an exit whose beyond-summary shows something unexplored. "
  "Use only what the screen, the map, the log or the known facts show; if a goal is a guess, mark its source assumed. Never invent places, items or routes; a place you were told about is not invented: it may be a goal before it is on the map (source heard), and its done_when says how you will recognise it. "
  "Do not re-create a goal the log says is done. Keep goals that still hold. When the trigger is new game text, first record what it tells you as facts, then check whether it gives a new or more urgent goal. Facts are what the game told or showed; trust them over guesses. "
  "When a focus is blocked, choose a different way to reach its parent (another exit, another person, another object) and say why. "
  "When the trigger is a cycle or an action that failed twice, the current focus is not working: mark it blocked and pick a different approach, not a rewording of the same one. "
  "When the trigger is an arrival, first compare every active goal's done_when with this place and mark the goals it satisfies done; then re-check which goals this place serves before keeping the old focus. Return only JSON.")
REPLY = ('Reply JSON: {"goals": list of edits {"op":"set"|"remove","section":"goals","key":short id,'
  '"value":{"text","parent":key of the goal this serves or null,"done_when":"one observable sign it is done","source":"saw|heard|assumed","evidence","status":"todo|active|done|blocked|dropped"}}, '
  '"facts": list of new facts {"key":short id (max 40 chars),"text":one sentence of what you were told or shown (who, where, what to do),"from":speaker or null,"source":"heard|saw"} (or []), '
  '"focus": key of the one leaf goal to work on now, "lesson": one short lesson learned or null, "why": one sentence}.')
SCHEMA = {"type": "object", "properties": {"goals": {"type": "array", "items": {"type": "object"}}, "focus": {"type": "string"},
  "facts": {"type": "array", "items": {"type": "object"}},
  "lesson": {"type": ["string", "null"]}, "why": {"type": "string"}}, "required": ["goals", "focus", "why"]}

@dataclass
class Plan:
    ops: list; focus: str; lesson: str | None; why: str; facts: list = field(default_factory=list)

def plan(model, trigger: str, notebook, view: str, screen_text: list, recent: list[str], failure: str | None = None) -> Plan:
    lines = [f"Trigger: {trigger}", f"Goal tree (focus: {notebook.focus or 'none'}):\n{notebook.render()}",
             f"Log (what already happened):\n{notebook.render_log(40)}", f"Map and memory:\n{view or 'none'}",
             f"Text on screen now: {'; '.join(str(t) for t in screen_text) or 'none'}",
             f"Recent actions: {'; '.join(recent) or 'none'}", *([f"Last failure: {failure}"] if failure else []), REPLY]
    data = model.ask(SYSTEM, "\n".join(lines), [], SCHEMA, role="plan").data
    data = data if isinstance(data, dict) else {}
    ops = [dict(o, section="goals") for o in data.get("goals") or [] if isinstance(o, dict)] if isinstance(data.get("goals"), list) else []
    facts = []
    for fact in data.get("facts", []) if isinstance(data.get("facts"), list) else []:
        if not isinstance(fact, dict): continue
        key, text = fact.get("key"), fact.get("text")
        if not isinstance(key, str) or not key.strip() or len(key.strip()) > 40 or not isinstance(text, str) or not text.strip(): continue
        item = {"key": key.strip(), "text": text.strip()}
        if isinstance(fact.get("from"), str) and fact["from"].strip(): item["from"] = fact["from"].strip()
        if fact.get("source") in ("heard", "saw"): item["source"] = fact["source"]
        facts.append(item)
    lesson = data.get("lesson")
    return Plan(ops=ops, focus=str(data.get("focus") or "").strip(), lesson=lesson.strip()[:160] if isinstance(lesson, str) and lesson.strip() else None,
                why=str(data.get("why") or ""), facts=facts)
