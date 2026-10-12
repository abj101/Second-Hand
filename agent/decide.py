"""Game-neutral action choice and task escalation."""

from dataclasses import dataclass
from collections import Counter

from agent.predict import EFFECTS

SYSTEM = "Choose a useful next action from the visible options. Return only JSON."
COND_KINDS = {"place_is", "entity_known", "text_seen", "relation", "steps_over"}
_COND = {"type": "object", "anyOf": [{"type": "object", "properties": {k: {"type": "string"}}, "required": [k], "additionalProperties": False} for k in ("place_is", "entity_known", "text_seen")] + [{"type": "object", "properties": {"relation": {"type": "array", "items": {"type": "string"}, "minItems": 3, "maxItems": 3}}, "required": ["relation"], "additionalProperties": False}, {"type": "object", "properties": {"steps_over": {"type": "integer"}}, "required": ["steps_over"], "additionalProperties": False}]}
_TASKS = {"type": "object", "properties": {"tasks": {"type": "array", "items": {"type": "object", "properties": {"goal": {"type": "string"}, "done_when": {"type": "array", "items": _COND}, "fail_when": {"type": "array", "items": _COND}, "budget": {"type": "integer"}}, "required": ["goal", "done_when"]}}}, "required": ["tasks"]}


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
def choose(model, view: str, perception: dict, task: dict, preds: list[dict | None], defaults: dict) -> Choice:
    affs = perception.get("affordances") or []
    eligible = [i for i, a in enumerate(affs) if a.get("verb") != "press"]
    if len(eligible) == 1:
        i = eligible[0]
        pred = preds[i] if i < len(preds) and isinstance(preds[i], dict) else {}
        effect = pred.get("effect") if pred.get("effect") in EFFECTS else "screen_changes"
        return Choice(i, _effect(affs[i], effect), str(pred.get("detail") or ""), [], "only available action", False,
                      _text(affs[i], defaults, perception))
    lines = ["View:", view, f"Task: {task}", "Affordances:"]
    lines.extend(f"{i}: {a}" for i, a in enumerate(affs))
    lines.append(f"Code predictions: {preds}")
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
            if kind in COND_KINDS and ((kind == "relation" and isinstance(value, (list, tuple)) and len(value) == 3) or (kind == "steps_over" and type(value) is int) or (kind not in {"relation", "steps_over"} and isinstance(value, str))): out.append({kind: value})
    return out
def escalate(model, view: str, reason: str) -> list[dict]:
    prompt = (f"View:\n{view}\nReason: {reason}\n" 
              'Suggest up to 3 useful subtasks as JSON {"tasks":[{"goal":str,"done_when":[conditions],"fail_when":[conditions]}]}. '
              "Conditions may use only place_is, entity_known, text_seen, relation, or steps_over.")
    data = model.ask(SYSTEM, prompt, schema=_TASKS, role="escalate").data
    tasks = data.get("tasks", []) if isinstance(data, dict) else []
    out = []
    for task in tasks if isinstance(tasks, list) else []:
        if not isinstance(task, dict) or not str(task.get("goal") or "").strip():
            continue
        done = _conditions(task.get("done_when"))
        if not done:
            continue
        item = {"goal": str(task["goal"]).strip(), "done_when": done,
                "fail_when": _conditions(task.get("fail_when"))}
        if type(task.get("budget")) is int and task["budget"] > 0:
            item["budget"] = task["budget"]
        out.append(item)
        if len(out) == 3:
            break
    return out
