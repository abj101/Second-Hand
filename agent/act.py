"""Choose one action from the current options."""

from dataclasses import dataclass


SYSTEM = (
    "You choose the next action in an unfamiliar video game. "
    "Use the goal, current screen, and numbered options to choose what to do. "
    "Each option includes an available action and relevant history; choose one option; to go through an exit on a wall, step onto it and press toward that wall. "
    "Return only JSON with a brief why and the option number i."
)


@dataclass
class Act:
    action: dict | None
    text: str | None
    why: str
    option: int | None
    replan: bool = False


def _ask_valid(model, system, user, schema, check):
    """Retry once with the validation reason, then return None if still invalid."""
    reason = None
    for retry in (False, True):
        data = model.ask(system, user + (f"\nYour last reply was invalid: {reason}. Reply again." if retry else ""), [], schema, role="act").data
        value, reason = check(data if isinstance(data, dict) else {})
        if value is not None:
            return value, data
    return None, data


HEADERS = {"new": "Not tried here:", "worked": "Worked here:", "failed": "Failed here:"}


def _option_lines(per, options):
    """Option rows. World menus get a header at each state change; the free-buttons row follows without one. Modal menus keep their order and no headers."""
    lines, last = [], None
    for o in options:
        state = o.get("state") if per.get("screen") == "world" and o.get("tool") != "buttons" else None
        if state and state != last: lines.append(HEADERS[state])
        last = state or last
        lines.append(f"{o['n']}. {o['label']}" + (f" — {'; '.join(o['notes'])}" if o.get("notes") else ""))
    return lines


def act(model, per, focus, options, buttons, controls) -> Act:
    lines = [
        focus,
        f"Screen: {per.get('screen', '')}",
        f"Text on screen: {'; '.join(str(t) for t in per.get('text') or [])}",
        *_option_lines(per, options),
        'Reply JSON: {"why": one sentence, "i": the option number, "replan": true only if new text changes what to do}',
    ]
    numbers = [o["n"] for o in options]
    schema = {"type": "object", "properties": {
        "why": {"type": "string", "maxLength": 200},
        "i": {"type": "integer", "enum": numbers},
        "replan": {"type": "boolean"},
    }, "required": ["why", "i"], "additionalProperties": False}

    def pick(data):
        if "replan" in data and not isinstance(data["replan"], bool): return None, '"replan" must be boolean'
        n = data.get("i")
        n = int(n) if isinstance(n, str) and n.strip().isdigit() else n
        return (n, None) if type(n) is int and n in numbers else (None, f'"i" must be one of {numbers}')

    n, data = _ask_valid(model, SYSTEM, "\n".join(lines), schema, pick)
    why = str(data.get("why") or "") if isinstance(data, dict) else ""
    option = next((o for o in options if o["n"] == n), None)
    action, text = (dict(option["action"]) if option and option["action"] else None), None

    if option and option["tool"] == "buttons":
        schema = {"type": "object", "properties": {"buttons": {
            "type": "array", "minItems": 1, "maxItems": 8,
            "items": {"type": "string", "enum": list(buttons)},
        }}, "required": ["buttons"], "additionalProperties": False}

        def valid_buttons(data):
            picks = data.get("buttons")
            valid = isinstance(picks, list) and 0 < len(picks) <= 8 and all(b in buttons for b in picks)
            return (picks, None) if valid else (None, f'"buttons" must be 1-8 of {buttons}')

        prompt = "\n".join(lines[:3] + [f"Controls:\n{controls}", f"You picked option {n}: {option['label']}.",
                                         'Reply JSON: {"buttons": up to 8 of ' + ", ".join(buttons) + ", in order}"])
        picks, _ = _ask_valid(model, SYSTEM, prompt, schema, valid_buttons)
        action = {"buttons": picks} if picks else None
    elif action == {"verb": "type"}:
        schema = {"type": "object", "properties": {"text": {
            "type": "string", "minLength": 1, "maxLength": 40,
        }}, "required": ["text"], "additionalProperties": False}
        prompt = "\n".join(lines[:3] + [f"You picked option {n}: {option['label']}.",
                                         'Reply JSON: {"text": the text to type}'])
        typed, _ = _ask_valid(model, SYSTEM, prompt, schema,
                              lambda d: (d["text"].strip()[:40], None) if isinstance(d.get("text"), str) and d["text"].strip() else (None, '"text" must be a non-empty string'))
        text = typed
        action = action if text else None

    return Act(action=action, text=text, why=why, option=n if option else None, replan=data.get("replan", False) if isinstance(data, dict) else False)
