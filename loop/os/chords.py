from __future__ import annotations

import json
import math
import re
from typing import Any, Iterable

from loop.log.schema import ActionLog
from loop.os.primitives import GamepadPrimitive, Primitive
from loop.os.tables import _STICK_DIRS, names, resolve

BANNED_TOKENS = ("dodge", "roll", "flask", "attack", "sprint")

_BUTTONS = frozenset({"a", "b", "x", "y", "lb", "rb", "ls", "rs", "start", "back", "lt", "rt"})
_DPAD = frozenset({"dpad_up", "dpad_down", "dpad_left", "dpad_right"})
_STICKS = frozenset(_STICK_DIRS)


def chord_name(action: ActionLog) -> str | None:
    derived = action.derive_name()
    if derived:
        return derived
    return action.name


def contains_banned(text: str, extra: Iterable[str] = ()) -> list[str]:
    hay = text.lower()
    found: list[str] = []
    for token in (*BANNED_TOKENS, *extra):
        if token.lower() in hay:
            found.append(token)
    return found


def skill_text_leaks(payload: Any) -> list[str]:
    if payload is None:
        return []
    if isinstance(payload, str):
        return contains_banned(payload)
    if isinstance(payload, dict):
        hits: list[str] = []
        for value in payload.values():
            hits.extend(skill_text_leaks(value))
        return sorted(set(hits))
    if isinstance(payload, (list, tuple)):
        hits: list[str] = []
        for value in payload:
            hits.extend(skill_text_leaks(value))
        return sorted(set(hits))
    return contains_banned(str(payload))


def flatten_parsed(parsed: dict[str, Any]) -> dict[str, Any]:
    """Un-nest {thought: {thought, stick_l, ...}} so a wrapped JSON still has a chord."""
    data = dict(parsed)
    thought = data.get("thought")
    if isinstance(thought, str):
        blob = thought.strip()
        if blob.startswith("{") and blob.endswith("}"):
            try:
                inner = json.loads(blob)
            except (json.JSONDecodeError, TypeError, ValueError):
                inner = None
            if isinstance(inner, dict):
                thought = inner
    if isinstance(thought, dict):
        nested = flatten_parsed(thought)
        thought_s = nested.pop("thought", None)
        for key, value in nested.items():
            if key not in data or data[key] in (None, "", [], {}):
                data[key] = value
        if isinstance(thought_s, str):
            data["thought"] = thought_s
        elif isinstance(data.get("thought"), dict):
            data["thought"] = str(thought_s or "")
    return data


def parse_action(parsed: dict[str, Any], table_name: str) -> ActionLog:
    parsed = flatten_parsed(parsed)
    duration = parsed.get("duration_ms")
    try:
        duration_ms = int(duration) if duration is not None else None
    except (TypeError, ValueError):
        duration_ms = None
    table = set(names(table_name))
    has_sticks = any(n.startswith("stick_") for n in table)
    stick_l = _stick_dir(parsed.get("stick_l")) if has_sticks else None
    stick_r = _stick_dir(parsed.get("stick_r")) if has_sticks else None
    held = _device_names(parsed.get("held"), table_name)
    tapped = _device_names(parsed.get("tapped"), table_name)
    name = parsed.get("action") or parsed.get("name")
    name_s = str(name).strip().lower() if name else None
    if name_s and name_s not in table:
        name_s = None
    if name_s and name_s in table:
        stick_l = None
        stick_r = None
        held = []
        tapped = []
    click_x = click_y = None
    click_x2 = click_y2 = None
    mark = None
    target = None
    if not has_sticks:
        mark = _as_mark(parsed.get("mark"))
        target = _as_mark(parsed.get("target"))
        if target is not None and target == mark:
            target = None
        pair = _parse_click_xy(parsed)
        if pair is not None:
            click_x, click_y = pair
        pair2 = _parse_click_xy2(parsed)
        if pair2 is not None:
            click_x2, click_y2 = pair2
        if mark is not None or pair is not None:
            name_s = None
            held = []
            tapped = []
    hover = _as_bool(parsed.get("hover")) or str(parsed.get("op") or "").strip().lower() == "hover"
    if hover:
        target = None
        click_x2 = click_y2 = None
    action = ActionLog(
        name=name_s,
        duration_ms=duration_ms,
        tool=str(parsed.get("tool") or "") or None,
        stick_l=stick_l,
        stick_r=stick_r,
        held=held,
        tapped=tapped,
        click_x=click_x,
        click_y=click_y,
        mark=mark,
        hover=hover,
        click_x2=click_x2,
        click_y2=click_y2,
        target=target,
    )
    derived = chord_name(action)
    if derived:
        action.name = derived
    return action


def _as_coord(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(n) or n < 0:
        return None
    return n


def _parse_click_xy(parsed: dict[str, Any]) -> tuple[float, float] | None:
    """Fractions in [0, 1], or a 0–1000 computer-use grid."""
    raw_x = parsed.get("click_x", parsed.get("gx", parsed.get("x")))
    raw_y = parsed.get("click_y", parsed.get("gy", parsed.get("y")))
    click = parsed.get("click")
    if (raw_x is None or raw_y is None) and isinstance(click, (list, tuple)) and len(click) >= 2:
        raw_x, raw_y = click[0], click[1]
    x = _as_coord(raw_x)
    y = _as_coord(raw_y)
    if x is None or y is None:
        return None
    if x > 1.0 or y > 1.0:
        x, y = x / 1000.0, y / 1000.0
    x = max(0.0, min(1.0, x))
    y = max(0.0, min(1.0, y))
    return x, y


def _parse_click_xy2(parsed: dict[str, Any]) -> tuple[float, float] | None:
    """Second click: gx2/gy2, click_x2/click_y2, or x2/y2 on a 0–1000 grid."""
    raw_x = parsed.get("click_x2", parsed.get("gx2", parsed.get("x2")))
    raw_y = parsed.get("click_y2", parsed.get("gy2", parsed.get("y2")))
    x = _as_coord(raw_x)
    y = _as_coord(raw_y)
    if x is None or y is None:
        return None
    if x > 1.0 or y > 1.0:
        x, y = x / 1000.0, y / 1000.0
    x = max(0.0, min(1.0, x))
    y = max(0.0, min(1.0, y))
    return x, y


def has_click(action: ActionLog) -> bool:
    return action.click_x is not None and action.click_y is not None


def _as_mark(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        return None
    if 1 <= n <= 64:
        return n
    return None


def _as_bool(value: Any) -> bool:
    if value is True:
        return True
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value == 1
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "hover"}
    return False


def _stick_dir(value: Any) -> str | None:
    if value is None:
        return None
    token = str(value).lower().removeprefix("stick_l_").removeprefix("stick_r_")
    return token if token in _STICKS else None


def _device_names(value: Any, table_name: str) -> list[str]:
    if value is None:
        return []
    raw = value if isinstance(value, (list, tuple)) else [value]
    allowed = set(names(table_name)) | _BUTTONS | _DPAD
    out: list[str] = []
    for item in raw:
        token = str(item).lower()
        if token in allowed and token not in out:
            out.append(token)
    return out


_PROMPT_DEVICES = _BUTTONS | _DPAD
_SINGLE_PROMPT = frozenset({"a", "b", "x", "y"})
_SWITCH_ACTION = re.compile(r"switch\s+action|cycle\s+(?:the\s+)?action|next\s+action", re.I)
_DEVICE_LIST = re.compile(
    r"if the box names|names?\s+a,\s*b,\s*x,\s*y(?:\s*,\s*lb,\s*rb)?|a,\s*b,\s*x,\s*y(?:\s*,\s*lb,\s*rb)?",
    re.I,
)
_OCR_CONFIRM_A = re.compile(
    r"['\"][@o]['\"]|(?:glyph|button|prompt|names(?:\s+the)?)\s+['\"]?[@o]['\"]?|[@o]\s*:",
    re.I,
)
_GO_INTENT = re.compile(r"\b(walk|go|going|toward|towards|approach|move|head)\b", re.I)
_LOOK_INTENT = re.compile(r"\b(look|looking|camera|glance|peek)\b", re.I)
_FACE_FAIL = re.compile(
    r"\b(failed|already tried|\btried\b|did not|didn't|mash(?:ing|ed)?|still closed|"
    r"not opening|no (?:result|success|change)|blocked|pressed|pressing|tapped|"
    r"tapping|previous actions|cleared the prompt)\b",
    re.I,
)


def face_bind_blocked(thought: str, *, yield_lock: bool = False) -> bool:
    """True when thought is look/explore or a failed face, not a live tap."""
    if yield_lock:
        return True
    blob = thought or ""
    return bool(_LOOK_INTENT.search(blob) or _FACE_FAIL.search(blob))


def extract_prompt_devices(text: str) -> list[str]:
    """Device names mentioned as prompts. Excludes stick dirs so compass N/E is not a stick."""
    hay = _DEVICE_LIST.sub(" ", text or "")
    failed = bool(_FACE_FAIL.search(hay))
    if _OCR_CONFIRM_A.search(hay) and not re.search(
        r"['\"]a['\"]|(?:press|tap|glyph|button)\s+a\b", hay, flags=re.I
    ):
        hay = hay + " glyph 'a'"
    found: list[str] = []
    for token in sorted(_PROMPT_DEVICES, key=len, reverse=True):
        if token in _SINGLE_PROMPT:
            if failed:
                continue
            if not re.search(
                rf"(?:press|hold|tap|button|glyph|name|resembles|prompt|shows)\s+['\"]?{token}['\"]?(?![a-z0-9_])"
                rf"|['\"]{token}['\"]",
                hay,
                flags=re.I,
            ):
                continue
        elif not re.search(
            rf"(?:press|hold|tap|button|glyph)\s+['\"]?{re.escape(token)}['\"]?(?![a-z0-9_])"
            rf"|['\"]{re.escape(token)}['\"]",
            hay,
            flags=re.I,
        ):
            continue
        found.append(token)
    return found


def bind_prompt_devices(
    thought: str,
    action: ActionLog,
    table_name: str,
    *,
    visible: bool = True,
    cv_glyph: str | None = None,
    yield_lock: bool = False,
) -> tuple[ActionLog, list[str]]:
    """Put on-screen prompt glyphs on this chord.

    Thought-named faces bind only while the bar is visible. A dark bar leaves
    the proposed chord unchanged so past-tense "pressing 'a'" cannot steal a walk.
    *cv_glyph* only applies when *visible* is true. Switch-action dpad only when
    the bar is visible. Lock-yield, look-intent, and failed-face narrative leave
    the chord unchanged.
    """
    if not visible:
        return action, []
    if face_bind_blocked(thought, yield_lock=yield_lock):
        return action, []
    allowed = set(names(table_name)) | _PROMPT_DEVICES
    mentioned = [tok for tok in extract_prompt_devices(thought) if tok in allowed]
    faces = [tok for tok in mentioned if tok in _SINGLE_PROMPT]
    rest = [tok for tok in mentioned if tok in _DPAD]
    glyph = (cv_glyph or "").strip().lower()
    if _SWITCH_ACTION.search(thought or ""):
        dpad = next(
            (name for name in ("dpad_up", "dpad_right", "dpad_down", "dpad_left") if name in allowed),
            None,
        )
        if dpad and dpad not in rest:
            rest.append(dpad)
    if not faces and glyph in _SINGLE_PROMPT and glyph in allowed:
        faces = [glyph]
    mentioned = ([faces[0]] if faces else []) + rest
    if not mentioned:
        return action, []
    present = {*(action.held or []), *(action.tapped or [])}
    if action.name:
        present.update(action.name.split("+"))
    missing = [tok for tok in mentioned if tok not in present]
    tapped = list(action.tapped)
    for tok in missing:
        if tok not in tapped:
            tapped.append(tok)
    face_tapped = [tok for tok in tapped if tok in _SINGLE_PROMPT]
    if len(face_tapped) > 1:
        keep = face_tapped[0]
        tapped = [tok for tok in tapped if tok not in _SINGLE_PROMPT or tok == keep]
    extra = [tok for tok in tapped if tok not in _SINGLE_PROMPT and tok not in _DPAD]
    if extra:
        tapped = [tok for tok in tapped if tok not in extra]
        missing = [tok for tok in missing if tok not in extra]
    keep_sticks = bool(_GO_INTENT.search(thought or "") and (action.stick_l or action.stick_r))
    flags = list(missing)
    if (action.stick_l or action.stick_r) and not keep_sticks:
        flags.append("no-stick")
    if not flags and tapped == list(action.tapped):
        return action, []
    update: dict[str, Any] = {"tapped": tapped, "held": []}
    if not keep_sticks:
        update["stick_l"] = None
        update["stick_r"] = None
    bound = action.model_copy(update=update)
    bound.name = chord_name(bound)
    return bound, flags


def interrupt_chord(glyph: str | None, *, burst_ms: int) -> ActionLog:
    """One face tap. Default a. No sticks."""
    face = (glyph or "a").strip().lower()
    if face not in _SINGLE_PROMPT:
        face = "a"
    action = ActionLog(tapped=[face], duration_ms=burst_ms)
    action.name = chord_name(action)
    return action


def apply_chord_duration(action: ActionLog, *, burst_ms: int, stick_ms: int) -> ActionLog:
    """Honor VLM duration for stick_l walks (clamped); taps / look / mixed stay burst_ms.

    *stick_ms* is the max walk hold. *burst_ms* is the floor for walks and the fixed
    length for camera/taps so look does not spin ~360°.
    """
    loco_only = bool(action.stick_l) and not action.stick_r and not action.held and not action.tapped
    if loco_only:
        lo = max(100, min(burst_ms, stick_ms))
        hi = max(lo, stick_ms)
        raw = action.duration_ms if action.duration_ms is not None else burst_ms
        try:
            asked = int(raw)
        except (TypeError, ValueError):
            asked = burst_ms
        duration = max(lo, min(hi, asked))
    else:
        duration = burst_ms
    bound = action.model_copy(update={"duration_ms": duration})
    bound.name = chord_name(bound) or bound.name
    return bound


def is_loco_walk(action: ActionLog) -> bool:
    """True when this chord is stick_l locomotion with nothing else."""
    if not action.stick_l or action.stick_r or action.held or action.tapped:
        return False
    name = (action.name or "").lower()
    if name and "+" in name:
        return False
    return True


def is_chord(action: ActionLog) -> bool:
    return bool(
        action.stick_l
        or action.stick_r
        or action.held
        or action.tapped
        or has_click(action)
        or action.mark is not None
    )


def is_empty_chord(action: ActionLog) -> bool:
    if action.stick_l or action.stick_r or action.held or action.tapped or has_click(action):
        return False
    if action.mark is not None or action.target is not None:
        return False
    if action.click_x2 is not None and action.click_y2 is not None:
        return False
    name = (action.name or "").strip().lower()
    if not name or name in {"idle", "end", "none", "null"}:
        return True
    return False


def fill_empty_chord(
    action: ActionLog,
    table_name: str,
    *,
    recent: list[str],
    burst_ms: int,
    patches: list[tuple[float, float]] | None = None,
    prompt_on: bool = False,
    glyph: str | None = None,
) -> ActionLog:
    """Empty JSON is invalid. Fill with unused look or back up — never a CV walk.

    *prompt_on* must be interrupt-ready (CV bar or labeled DINO face after debounce),
    not a raw DINO inset hit. Otherwise empty JSON light-attacks.
    """
    del patches  # DINO/bright patches are prompt hints, not motors
    duration = action.duration_ms if action.duration_ms and action.duration_ms > 0 else burst_ms
    if prompt_on:
        return interrupt_chord(glyph, burst_ms=burst_ms)
    used = {token.lower() for token in recent}
    table = names(table_name)
    look_names = [n for n in table if n.startswith("stick_r_")]
    stick_names = [n for n in table if n.startswith("stick_l_")]
    for name in look_names:
        if name not in used:
            filled = ActionLog(stick_r=_stick_dir(name.removeprefix("stick_r_")), duration_ms=duration)
            filled.name = chord_name(filled)
            return filled
    if stick_names and "stick_l_s" in table and "stick_l_s" not in used:
        filled = ActionLog(stick_l="s", duration_ms=duration)
        filled.name = chord_name(filled)
        return filled
    for name in stick_names:
        if name not in used:
            filled = ActionLog(stick_l=_stick_dir(name.removeprefix("stick_l_")), duration_ms=duration)
            filled.name = chord_name(filled)
            return filled
    for name in table:
        if name not in used:
            return ActionLog(name=name, duration_ms=duration)
    fallback = look_names[0] if look_names else (stick_names[0] if stick_names else (table[0] if table else None))
    if fallback and fallback.startswith("stick_r_"):
        filled = ActionLog(stick_r=_stick_dir(fallback.removeprefix("stick_r_")), duration_ms=duration)
        filled.name = chord_name(filled)
        return filled
    if fallback and fallback.startswith("stick_l_"):
        filled = ActionLog(stick_l=_stick_dir(fallback.removeprefix("stick_l_")), duration_ms=duration)
        filled.name = chord_name(filled)
        return filled
    return ActionLog(name=fallback, duration_ms=duration)


_FACE_ONLY = frozenset({"a", "b", "x", "y"})


def _pure_look_name(name: str) -> str | None:
    token = (name or "").lower()
    if not token.startswith("stick_r_"):
        return None
    if "+" in token or "stick_l" in token:
        return None
    return token


def _pure_walk_name(name: str) -> str | None:
    token = (name or "").lower()
    if not token.startswith("stick_l_"):
        return None
    if "+" in token:
        return None
    if "stick_r" in token:
        return None
    return token


def walk_stall_count(recent: list[str]) -> tuple[int, str | None]:
    """Trailing same stick_l-only walk. Face taps (Unsheathe) do not reset stall."""
    filtered = [name for name in recent if (name or "").lower() not in _FACE_ONLY]
    if not filtered:
        return 0, None
    last = _pure_walk_name(filtered[-1])
    if last is None:
        return 0, None
    n = 0
    for name in reversed(filtered):
        if _pure_walk_name(name) == last:
            n += 1
        else:
            break
    return n, last


ESCAPE_CYCLE: tuple[tuple[str | None, str | None], ...] = (
    (None, "e"),
    (None, "w"),
    ("s", None),
    ("e", None),
    (None, "nw"),
    ("w", None),
)
_ESCAPE_CYCLE = ESCAPE_CYCLE


def escape_blocked_chord(
    action: ActionLog,
    *,
    recent: list[str],
    last_mad: float | None,
    prompt_on: bool,
    stall_need: int = 3,
    mad_eps: float = 0.015,
) -> tuple[ActionLog, str | None]:
    """Owned by the explore GSM. Do not call from the VLM rewrite path."""
    if prompt_on or action.tapped or action.held:
        return action, None
    if last_mad is None or last_mad >= mad_eps:
        return action, None
    stall, walked = walk_stall_count(recent)
    if stall < stall_need or walked is None:
        return action, None
    stick_l, stick_r = ESCAPE_CYCLE[(stall - stall_need) % len(ESCAPE_CYCLE)]
    bound = ActionLog(stick_l=stick_l, stick_r=stick_r, duration_ms=action.duration_ms)
    bound.name = chord_name(bound)
    why = f"blocked after {stall}x {walked} mad={last_mad:.3f} → {bound.name}"
    return bound, why


def steer_explore_chord(
    action: ActionLog,
    *,
    patches: list[tuple[float, float]] | None,
    thought: str,
    recent: list[str],
    prompt_on: bool,
    last_mad: float | None = None,
) -> tuple[ActionLog, str | None]:
    """No-op. Look→walk and patch-walk are not a write path; stall escape is the GSM."""
    del patches, thought, recent, prompt_on, last_mad
    return action, None


def is_look_only(action: ActionLog) -> bool:
    if not action.stick_r or action.stick_l or action.held or action.tapped:
        return False
    name = (action.name or "").lower()
    if name and "+" in name and "stick_l" in name:
        return False
    return True


def same_chord(left: ActionLog, right: ActionLog) -> bool:
    return (
        left.stick_l == right.stick_l
        and left.stick_r == right.stick_r
        and list(left.held or []) == list(right.held or [])
        and list(left.tapped or []) == list(right.tapped or [])
        and left.click_x == right.click_x
        and left.click_y == right.click_y
        and left.mark == right.mark
        and left.target == right.target
        and left.click_x2 == right.click_x2
        and left.click_y2 == right.click_y2
        and bool(left.hover) == bool(right.hover)
    )


def action_from_skill(skill: Any, table_name: str, *, burst_ms: int) -> ActionLog | None:
    payload = getattr(skill, "avoid_or_do", None) or {}
    if not isinstance(payload, dict):
        return None
    chord = payload.get("chord")
    if not isinstance(chord, dict):
        return None
    action = parse_action(chord, table_name)
    if is_empty_chord(action):
        return None
    return apply_chord_duration(action, burst_ms=burst_ms, stick_ms=burst_ms)


def skill_invoke_gsm(skill: Any) -> str | None:
    payload = getattr(skill, "avoid_or_do", None) or {}
    if not isinstance(payload, dict):
        return None
    name = str(payload.get("invoke_gsm") or "").strip().lower()
    return name or None


def chord_primitives(action: ActionLog, table_name: str, *, duration_ms: int) -> list[Primitive]:
    """Compile a chord to sequential primitives (fallback when pad has no hold-all)."""
    prims: list[Primitive] = []
    if action.stick_l:
        prims.extend(resolve(table_name, f"stick_l_{action.stick_l}").primitives)
    if action.stick_r:
        prims.extend(resolve(table_name, f"stick_r_{action.stick_r}").primitives)
    for name in action.held:
        if name in names(table_name):
            prims.extend(resolve(table_name, name).primitives)
    for name in action.tapped:
        if name in names(table_name):
            prims.extend(resolve(table_name, name).primitives)
    if not prims and action.name and action.name in names(table_name):
        prims.extend(resolve(table_name, action.name).primitives)
    patched: list[Primitive] = []
    for prim in prims:
        if isinstance(prim, GamepadPrimitive) and prim.duration_ms:
            patched.append(
                GamepadPrimitive(
                    op=prim.op,
                    button=prim.button,
                    dpad=prim.dpad,
                    stick=prim.stick,
                    x=prim.x,
                    y=prim.y,
                    trigger=prim.trigger,
                    value=prim.value,
                    duration_ms=duration_ms,
                )
            )
        else:
            patched.append(prim)
    return patched
