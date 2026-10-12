"""Game-neutral action choice and task escalation."""

from dataclasses import dataclass
from collections import Counter

from agent.brief import GENERAL
from agent.memory import RECURRING
from agent.predict import EFFECTS

SYSTEM = "Choose a useful next action from the visible options. Return only JSON."
COND_KINDS = {"place_is", "entity_known", "text_seen", "relation", "steps_over", "new_place"}
_COND = {"type": "object", "anyOf": [{"type": "object", "properties": {k: {"type": "string"}}, "required": [k], "additionalProperties": False} for k in ("place_is", "entity_known", "text_seen")] + [{"type": "object", "properties": {"relation": {"type": "array", "items": {"type": "string"}, "minItems": 3, "maxItems": 3}}, "required": ["relation"], "additionalProperties": False}, {"type": "object", "properties": {"new_place": {"type": "boolean", "enum": [True]}}, "required": ["new_place"], "additionalProperties": False}, {"type": "object", "properties": {"steps_over": {"type": "integer"}}, "required": ["steps_over"], "additionalProperties": False}]}
_TASKS = {"type": "object", "properties": {"tasks": {"type": "array", "items": {"type": "object", "properties": {"goal": {"type": "string"}, "done_when": {"type": "array", "items": _COND}, "fail_when": {"type": "array", "items": _COND}, "budget": {"type": "integer"}}, "required": ["goal", "done_when"]}}, "pattern_names": {"type": "object", "additionalProperties": {"type": "string"}}, "notes": {"type": "string"}}, "required": ["tasks"]}


@dataclass
class Choice:
    i: int
    effect: str
    detail: str
    used: list[str]
    why: str
    called: bool
    text: str | None = None


def _context(perception: dict, view: str = "") -> str:
    return " ".join([view, *(str(t) for t in perception.get("text", [])), *(f"{e.get('name', '')} {e.get('kind', '')}" for e in perception.get("entities", []))]).casefold()
def _role(context: str, defaults: dict) -> str:
    return next((str(role) for role in defaults if role != "name" and str(role).casefold() in context), "name")
def _text(aff: dict, defaults: dict, perception: dict) -> str | None:
    if aff.get("verb") != "type":
        return None
    context = f"{_context(perception)} {aff.get('target', '')} {aff.get('why', '')}".casefold()
    return str(defaults.get(_role(context, defaults), defaults.get("name", "")))
def _custom_name(affs: list[dict], perception: dict, defaults: dict, view: str) -> int | None:
    context = _context(perception, view)
    if "name" not in context: return None
    expected = str(defaults.get(_role(context, defaults), defaults.get("name", ""))).casefold()
    if any(a.get("verb") == "select" and expected == str(a.get("target", "")).strip().casefold() for a in affs): return None
    markers = ("custom", "manual", "enter a name", "type a name")
    return next((i for i, a in enumerate(affs) if a.get("verb") == "select" and any(m in f"{a.get('target', '')} {a.get('why', '')}".casefold() for m in markers)), None)
def _key(aff: dict) -> tuple[str, str]:
    return str(aff.get("verb", "")).casefold(), str(aff.get("target", "")).casefold()
def _effect(aff: dict, effect: str) -> str:
    return "place_changes" if str(aff.get("target", "")).startswith("exit") else effect
def _fallback(affs: list[dict], recent: list[dict]) -> tuple[int | None, set[tuple[str, str]]]:
    tried = {_key(c) for r in recent if isinstance((c := r.get("choice")), dict)}
    return next((i for i, a in enumerate(affs) if _key(a) not in tried), None), tried
def _view_recent(view: str) -> list[dict]:
    verbs = {"approach", "interact", "select", "click", "drag", "advance", "cancel", "type", "press"}
    rows = []
    for line in view.splitlines():
        action = line.split(": ", 1)[-1].split(" → ", 1)[0].strip()
        verb, _, target = action.partition(" ")
        if line.startswith(("Recent: ", "Failed here: ")) and verb in verbs:
            rows.append({"choice": {"verb": verb, "target": target}})
    return rows
def choose(model, view: str, perception: dict, task: dict, preds: list[dict | None], defaults: dict, controls: str = "") -> Choice:
    affs = perception.get("affordances") or []
    eligible = [i for i, a in enumerate(affs) if a.get("verb") != "press"]
    if len(eligible) == 1:
        i = eligible[0]
        pred = preds[i] if i < len(preds) and isinstance(preds[i], dict) else {}
        effect = pred.get("effect") if pred.get("effect") in EFFECTS else "screen_changes"
        return Choice(i, _effect(affs[i], effect), str(pred.get("detail") or ""), [], "only available action", False,
                      _text(affs[i], defaults, perception))
    lines = ["General:", GENERAL, "Controls:", controls, "View:", view, f"Goal: {task.get('goal', '')}; progress: {task.get('used', 0)}/{task.get('budget', '')}; budget: {task.get('budget', '')}", "Affordances:"]
    lines.extend(f"{i}: {a}" for i, a in enumerate(affs))
    if any(p is not None for p in preds):
        lines.append(f"Code predictions: { {i: p for i, p in enumerate(preds) if p is not None} }")
    lines.append(f"Defaults: {defaults}")
    lines.append(f"Prefer untried affordances, using recent/failed view lines to avoid repeats. For default curiosity, favor visible traversal toward unfamiliar areas over repeatedly studying furniture or menus. Effect must be one of {EFFECTS}.")
    lines.append("For a requested name, use a matching preset; if none matches the required default, choose the custom name entry.")
    lines.append('Return JSON with i, effect, detail, used, why, and text. For type, text must come from defaults; otherwise text is null.')
    data = model.ask(SYSTEM, "\n".join(lines), role="chooser").data
    valid = isinstance(data, dict) and type(data.get("i")) is int and 0 <= data["i"] < len(affs) and data.get("effect") in EFFECTS
    recent = perception.get("recent", []) if isinstance(perception.get("recent", []), list) else []
    fallback, tried = _fallback(affs, [*recent, *_view_recent(view)])
    if not valid:
        i, effect, detail, used, why = fallback if fallback is not None else 0, "screen_changes", "", [], "invalid model choice; using first untried action"
    else:
        i, effect = data["i"], data["effect"]
        detail = str(data.get("detail") or "")
        used = [str(x) for x in data.get("used", []) if isinstance(x, (str, int))] if isinstance(data.get("used"), list) else []
        why = str(data.get("why") or "")
    custom = _custom_name(affs, perception, defaults, view)
    if custom is not None:
        i, effect, detail, why = custom, "screen_changes", "", "No preset matches the required name; use the custom entry."
    if task.get("src") == "default" and fallback is not None and _key(affs[i]) in tried:
        i, effect, detail, why = fallback, "screen_changes", "", "Default curiosity avoids repeating a tried action while an untried affordance remains."
    return Choice(i, _effect(affs[i], effect), detail, used, why, True, _text(affs[i], defaults, perception))
def code_choice(perception: dict, view_ids: list[str], recent: list[dict]) -> Choice:
    affs = perception.get("affordances") or []
    counts = Counter((str(c.get("verb", "")).casefold(), str(c.get("target", "")).casefold())
                     for r in recent if isinstance((c := r.get("choice")), dict))
    i = min(range(len(affs)), key=lambda n: (counts[(str(affs[n].get("verb", "")).casefold(),
             str(affs[n].get("target", "")).casefold())], n)) if affs else 0
    ident = view_ids[i] if i < len(view_ids) else ""
    return Choice(i, _effect(affs[i] if affs else {}, "screen_changes"), "", [ident] if ident else [], "least tried action", False)
def _conditions(rows) -> list[dict]:
    if not isinstance(rows, list):
        return []
    out = []
    for c in rows:
        if isinstance(c, str) and "(" in c and c.endswith(")"):
            kind, value = c[:-1].split("(", 1); c = {kind: int(value) if kind == "steps_over" and value.lstrip("-").isdigit() else value}
        if isinstance(c, dict) and len(c) == 1:
            kind, value = next(iter(c.items()))
            if kind in COND_KINDS and ((kind == "relation" and isinstance(value, (list, tuple)) and len(value) == 3) or (kind == "steps_over" and type(value) is int) or (kind == "new_place" and value is True) or (kind not in {"relation", "steps_over", "new_place"} and isinstance(value, str))): out.append({kind: value})
    return out

def _task_results(data: dict, limit: int = 3, kinds: set[str] | None = None, timeout: int = 0) -> list[dict]:
    out = []
    for task in data.get("tasks", []) if isinstance(data, dict) and isinstance(data.get("tasks", []), list) else []:
        if not isinstance(task, dict) or not str(task.get("goal") or "").strip(): continue
        done, fail = _conditions(task.get("done_when")), _conditions(task.get("fail_when"))
        if kinds: done, fail = [c for c in done if next(iter(c)) in kinds], [c for c in fail if next(iter(c)) in kinds]
        if not done: continue
        if timeout and not any("steps_over" in c for c in fail): fail.append({"steps_over": timeout})
        item = {"goal": str(task["goal"]).strip(), "done_when": done, "fail_when": fail,
                **({"budget": task["budget"]} if type(task.get("budget")) is int and task["budget"] > 0 else {})}
        out.append(item)
        if len(out) == limit: break
    return out


def navigation_candidates(perception: dict, affordance_ids: list[str | None], entity_ids: dict[int, str], *, tile_frac: tuple[float, float] | None = None, player_at: list[float] | None = None) -> list[dict]:
    entities, affs = perception.get("entities") or [], perception.get("affordances") or []
    by_id, aff_ids = {entity_ids[i]: e for i, e in enumerate(entities) if entity_ids.get(i)}, {x for x in affordance_ids if x}
    rows = [(affordance_ids[i] if i < len(affordance_ids) else None, by_id.get(affordance_ids[i] if i < len(affordance_ids) else None, {}), a) for i, a in enumerate(affs) if a.get("verb") == "approach" and str(a.get("target", "")).strip().casefold() not in {"player", "the player", "player character"}]
    rows += [(entity_ids.get(i), e, {"verb": "approach", "target": e.get("name", ""), "at": e.get("at"), "why": "visible entity", **({"placement": e["placement"]} if e.get("placement") else {})}) for i, e in enumerate(entities) if e.get("kind", "").casefold() != "player" and entity_ids.get(i) not in aff_ids]
    candidates, seen = [], set()
    for ident, entity, aff in rows:
        at = aff.get("at") or entity.get("at")
        if not isinstance(at, list) or len(at) != 2 or entity.get("kind", "").casefold() == "player": continue
        key = ident or (str(entity.get("name", aff.get("target", "thing"))).casefold(), tuple(at))
        if key in seen: continue
        seen.add(key); dx = dy = 0.0
        if isinstance(player_at, list) and len(player_at) == 2:
            dx, dy = at[0] - player_at[0], at[1] - player_at[1]
            if tile_frac and tile_frac[0] and tile_frac[1]: dx, dy = dx / tile_frac[0], dy / tile_frac[1]
        pos = [f"{n} tiles {d}" for v, d in ((dx, "left" if dx < 0 else "right"), (dy, "up" if dy < 0 else "down")) if (n := round(abs(v)))]
        name, kind, placement = entity.get("name") or aff.get("target"), entity.get("kind"), aff.get("placement") or entity.get("placement")
        candidates.append({"id": ident, "name": str(name or kind or "thing"), "kind": str(kind or "object"), "at": at, **({"placement": placement} if placement else {}), "affordance": aff, "position": ", ".join(pos) or "at the player's position", "distance": abs(dx) + abs(dy)})
    return candidates


def resolve_target(mem, target: str, place: str, tile=None) -> str | None:
    name = str(target or "").strip()
    return mem.resolve(name.split(":", 1)[1].strip() if name.casefold().startswith("exit:") else name, place, tile) if name else None


def related_target_ids(mem, place: str) -> set[str]:
    return {(resolve_target(mem, e.get("name", ""), place) if e.get("place") == place or e.get("kind") in RECURRING else None) or r["s"]
            for r in mem.rows("relations") if r.get("p") == "leads_to" and r.get("s") for e in [mem.get("entities", r["s"]) or {}]}


def known_routes(mem) -> list[str]:
    out = []
    for r in mem.rows("relations"):
        if r.get("p") != "leads_to" or not r.get("s"): continue
        name, place = (mem.get("entities", r["s"]) or {}).get("name", r["s"]), mem.get("places", r.get("o") or "")
        if place and r.get("state") == "live": out.append(f"{name} leads to {place.get('name', r['o'])} (already visited)")
        elif r.get("con", 0) > r.get("sup", 0): out.append(f"{name}: tested, no new place")
    return out


def next_test(judge, goal: str, known: list[str], candidates: list[dict], tested: set[str]) -> dict | None:
    pending = [c for c in candidates if not c.get("id") or c["id"] not in tested]
    if not pending: return None
    edge = {"top wall", "bottom wall", "left wall", "right wall", "room edge", "screen edge"}; walls = [c for c in pending if c.get("placement") in edge]; pending = walls or pending; pending.sort(key=lambda c: c.get("distance", 0)); options = pending[:10]
    names = [c["name"] for c in options]; labels = {c["name"] if names.count(c["name"]) == 1 else f"{c['name']} ({i + 1})": c for i, c in enumerate(options)}
    things = {n: {"kind": c.get("kind", "object"), "position": c.get("position", "unknown position"), "placement": c.get("placement") or "unknown placement"} for n, c in labels.items()}
    question = {"target": {"type": "choice", "instructions": f"Which thing should the player walk to next to {goal}?", "criteria": {n: f"walk to the {n} ({c['position']}; {c['placement']})" for n, c in things.items()}}}
    try: return labels.get(judge.rank({"goal": goal, "known": known, "things": things}, question).get("answers", {}).get("target", {}).get("choice"), options[0]) if judge else options[0]
    except (AttributeError, TypeError): return options[0]

class PlanResult(list):
    def __init__(self, tasks, pattern_names=None, notes=""):
        super().__init__(tasks); self.pattern_names = pattern_names or {}; self.notes = notes


def plan(model, view: str, reason: str, last_goal: str = "", last_end: str = "", screen_text: str | list[str] = "", pattern_names: dict | None = None) -> list[dict]:
    text = "\n".join(screen_text) if isinstance(screen_text, list) else str(screen_text or "")
    known = {str(k): str(v) for k, v in (pattern_names or {}).items() if isinstance(k, str) and isinstance(v, str)}
    data = model.ask("Set a useful, verifiable next goal from the memory and current screen. Return only JSON.",
                     f"Memory:\n{view}\nTrigger: {reason}\nLast goal: {last_goal}\nEnded: {last_end}\n"
                     f"Screen text: {text}\nExisting pattern ids and names: {known}\nReturn one main goal and an optional child first step toward it; "
                     "use new_place, text_seen, entity_known, relation, steps_over; prefer new_place for travel. "
                     'Answer as {"tasks":[{"goal":str,"done_when":[{"new_place":true}],"fail_when":[{"steps_over":40}]}],"pattern_names":{"existing_id":"short name"},"notes":"brief useful observation"}. '
                     "Only name ids listed above; omit unknown ids and never promote patterns.",
                     schema=_TASKS, role="plan").data
    names = data.get("pattern_names", {}) if isinstance(data, dict) else {}
    names = {k: v for k, v in names.items() if isinstance(k, str) and isinstance(v, str)} if isinstance(names, dict) else {}
    return PlanResult(_task_results(data, 2, {"new_place", "text_seen", "entity_known", "relation", "steps_over"}, 40), names,
                      data.get("notes", "") if isinstance(data, dict) and isinstance(data.get("notes", ""), str) else "")

def escalate(model, view: str, reason: str) -> list[dict]:
    prompt = (f"View:\n{view}\nReason: {reason}\n" 
              'Suggest up to 3 useful subtasks as JSON {"tasks":[{"goal":str,"done_when":[conditions],"fail_when":[conditions]}]}. '
              "Conditions may use only place_is, entity_known, text_seen, relation, or steps_over.")
    data = model.ask(SYSTEM, prompt, schema=_TASKS, role="escalate").data
    return _task_results(data)
