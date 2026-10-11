"""Game-neutral, closed-loop input skills."""
from dataclasses import dataclass, replace; from typing import Literal
import re
from PIL import Image
from agent.env import Env
from agent.locate import refine
from agent.model import Ollama
from agent.observer import _blank, changed, cut, for_model, scroll, wait_settled
@dataclass
class SkillResult:
    status: Literal["done", "failed", "blocked"]
    reason: str
    inputs: int
    calls: int
    before: Image.Image
    after: Image.Image
    scroll: tuple[int, int]
    blank_seen: bool
@dataclass
class Ctx:
    env: Env
    model: Ollama
    tile_px: int | None = None
    move_frames: int = 16
    max_inputs: int = 30
def _settled(ctx): return wait_settled(ctx.env)
def _result(status, reason, inputs, calls, before, after, total=(0, 0), blank=False):
    return SkillResult(status, reason, inputs, calls, before, after, tuple(total), blank)
def _step(ctx, direction, before):
    frames = []
    ctx.env.press(direction, ctx.move_frames, on_frame=frames.append)
    end = _settled(ctx); shifts = [s for s in (scroll(a, b) for a, b in zip([before, *frames], frames)) if s is not None]
    if not shifts and (s := scroll(before, end.frame, max_shift=max(4, ctx.tile_px or 16))) is not None: shifts = [s]
    move = tuple(sum(s[i] for s in shifts) for i in (0, 1)); return end, move if shifts else None, end.blank_seen or any(_blank(f) for f in frames)
def _pointer(ctx, at, target):
    first = _settled(ctx)
    if ctx.max_inputs < 1: return _result("blocked", "input limit", 0, 0, first.frame, first.frame, blank=first.blank_seen)
    point, _ = refine(ctx.model, first.frame, target, at)
    if point is None:
        return _result("failed", "target not visible", 0, 1, first.frame, first.frame, blank=first.blank_seen)
    ctx.env.click(point); last = _settled(ctx)
    return _result("done", "", 1, 1, first.frame, last.frame, blank=first.blank_seen or last.blank_seen)
def approach(ctx: Ctx, target_at: list[float], player_at: list[float] | None) -> SkillResult:
    first = _settled(ctx)
    if ctx.env.pointer: return _pointer(ctx, target_at, "")
    if player_at is None: return _result("blocked", "player position unknown", 0, 0, first.frame, first.frame, blank=first.blank_seen)
    initial, before, blank = first.frame, first.frame, first.blank_seen
    w, h = before.size; delta = [(target_at[0] - player_at[0]) * w, (target_at[1] - player_at[1]) * h]
    if ctx.tile_px: delta[:] = [round(v / ctx.tile_px) * ctx.tile_px for v in delta]
    total, blocked, inputs, last_dir, pushed, axis_dirs = [0, 0], set(), 0, None, False, {}
    while inputs < max(0, ctx.max_inputs):
        if max(map(abs, delta)) <= .5:
            if last_dir and not pushed and inputs < ctx.max_inputs:
                old = before
                end, move, saw_blank = _step(ctx, last_dir, old)
                inputs += 1
                if move is None and not saw_blank: return _result("failed", "scroll unsure", inputs, 0, initial, end.frame, total, blank or saw_blank)
                if move is not None: total[:] = [total[i] + move[i] for i in (0, 1)]
                blank |= saw_blank
                if cut(old, end.frame, saw_blank): return _result("done", "scene cut", inputs, 0, initial, end.frame, total, blank)
                before, pushed = end.frame, True
            return _result("done", "", inputs, 0, initial, before, total, blank)
        tile = ctx.tile_px or 1
        d = [v / tile for v in delta]
        axes = [a for a in (0, 1) if a not in blocked]
        if not axes:
            near = ctx.tile_px is not None and sum(round(abs(v)) for v in d) <= 1
            return _result("done" if near else "blocked", "adjacent" if near else "both axes blocked", inputs, 0, initial, before, total, blank)
        axis = max(axes, key=lambda a: abs(d[a]))
        direction = axis_dirs.get(axis, ("RIGHT" if axis == 0 else "DOWN")) if abs(delta[axis]) < .5 else (("RIGHT" if delta[0] > 0 else "LEFT") if axis == 0 else ("DOWN" if delta[1] > 0 else "UP"))
        if blocked: direction = axis_dirs.get(axis, direction)
        old = before; end, move, saw_blank = _step(ctx, direction, old); inputs += 1
        if cut(old, end.frame, saw_blank): return _result("done", "scene cut", inputs, 0, initial, end.frame, total, blank or saw_blank)
        if move is None: return _result("failed", "scroll unsure", inputs, 0, initial, end.frame, total, blank or saw_blank)
        if sizes := [abs(v) for v in move if v]: ctx.tile_px = min(sizes + ([ctx.tile_px] if ctx.tile_px else []))
        if ctx.tile_px: delta[:] = [round(v / ctx.tile_px) * ctx.tile_px for v in delta]
        total[:] = [total[i] + move[i] for i in (0, 1)]; blank |= saw_blank
        if move == (0, 0):
            blocked.add(axis)
            near = ctx.tile_px is not None and sum(round(abs(v / ctx.tile_px)) for v in delta) <= 1
            if near: return _result("done", "adjacent", inputs, 0, initial, end.frame, total, blank)
        else:
            delta[0] += move[0]; delta[1] += move[1]
            if ctx.tile_px: delta[:] = [round(v / ctx.tile_px) * ctx.tile_px for v in delta]
            blocked.clear()
            if move[axis]: axis_dirs[axis] = direction
        before, last_dir = end.frame, direction
    return _result("blocked", "input limit", inputs, 0, initial, before, total, blank)
def interact(ctx: Ctx, target_at: list[float], player_at: list[float] | None) -> SkillResult:
    if ctx.env.pointer: return _pointer(ctx, target_at, "")
    start = _settled(ctx)
    moved = approach(ctx, target_at, player_at)
    if moved.status != "done" or moved.reason == "scene cut" or moved.inputs >= ctx.max_inputs: return moved
    inputs, total, face_blank = moved.inputs, list(moved.scroll), False
    if player_at is not None:
        dx = (target_at[0] - player_at[0]) * start.frame.width + total[0]
        dy = (target_at[1] - player_at[1]) * start.frame.height + total[1]
        face = ("RIGHT" if dx > 0 else "LEFT") if abs(dx) >= abs(dy) and dx else ("DOWN" if dy > 0 else "UP")
        if face in ctx.env.inputs() and inputs < ctx.max_inputs:
            end, move, saw_blank = _step(ctx, face, moved.after); inputs += 1
            face_blank = saw_blank
            if move is not None: total[:] = [total[i] + move[i] for i in (0, 1)]
            if cut(moved.after, end.frame, saw_blank):
                return _result("done", "scene cut", inputs, moved.calls, start.frame, end.frame, total, start.blank_seen or moved.blank_seen or saw_blank)
    if inputs < ctx.max_inputs: ctx.env.press("A", ctx.move_frames); inputs += 1
    end = _settled(ctx)
    return _result("done" if changed(start.frame, end.frame) else "failed",
                   "" if changed(start.frame, end.frame) else "nothing changed", inputs, moved.calls,
                   start.frame, end.frame, total, start.blank_seen or moved.blank_seen or face_blank or end.blank_seen)
def select(ctx: Ctx, item: str) -> SkillResult:
    start = _settled(ctx)
    schema = {"type": "object", "properties": {"items": {"type": "array", "items": {"type": "string"}},
              "cursor": {"type": "integer"}, "layout": {"type": "string"}}, "required": ["items", "cursor", "layout"]}
    reply = ctx.model.ask("Read the visible menu and answer only with JSON.", f'Return one JSON object with exactly these keys: "items" (the visible menu labels in order as strings), "cursor" (the highlighted item index, zero-based integer), and "layout" ("vertical" or "horizontal"). Match the target label case-insensitively: {item}', [for_model(start.frame)], schema, role="skill")
    data = reply.data or {}; items, cursor = data.get("items"), data.get("cursor")
    if not isinstance(items, list) or not items or not isinstance(cursor, int) or not 0 <= cursor < len(items): return _result("failed", "menu or item not identified", 0, 1, start.frame, start.frame, blank=start.blank_seen)
    diff = next((i - cursor for i, name in enumerate(items) if str(name).casefold() == item.casefold()), None)
    if diff is None: return _result("failed", "menu or item not identified", 0, 1, start.frame, start.frame, blank=start.blank_seen)
    key = ("RIGHT" if diff > 0 else "LEFT") if data.get("layout") == "horizontal" else ("DOWN" if diff > 0 else "UP")
    n = abs(diff) + 1
    if n > ctx.max_inputs: return _result("blocked", "input limit", 0, 1, start.frame, start.frame, blank=start.blank_seen)
    for _ in range(abs(diff)): ctx.env.press(key, ctx.move_frames)
    ctx.env.press("A", ctx.move_frames); end = _settled(ctx)
    return _result("done", "", n, 1, start.frame, end.frame, blank=start.blank_seen or end.blank_seen)
def press(ctx: Ctx, key: str) -> SkillResult:
    start = _settled(ctx); keys = ctx.env.inputs(); words = set(re.findall(r"[a-z0-9]+", key.casefold())); matches = [k for k in keys if k.casefold() in words]
    key = key if key in keys else matches[0] if len(matches) == 1 else None
    if key is None or ctx.max_inputs < 1: return _result("blocked", "unsupported input" if key is None else "input limit", 0, 0, start.frame, start.frame, blank=start.blank_seen)
    ctx.env.press(key, ctx.move_frames); end = _settled(ctx)
    return _result("done", "", 1, 0, start.frame, end.frame, blank=start.blank_seen or end.blank_seen)
def advance(ctx: Ctx) -> SkillResult: return press(ctx, "A")
def cancel(ctx: Ctx) -> SkillResult: return press(ctx, "B")
def wait_until(ctx: Ctx, max_frames: int = 240) -> SkillResult:
    start = _settled(ctx); end = wait_settled(ctx.env, max_frames=max_frames)
    return _result("done" if end.settled else "failed", "" if end.settled else "not settled", 0, 0, start.frame, end.frame, blank=start.blank_seen or end.blank_seen)
def type_text(ctx: Ctx, text: str) -> SkillResult:
    limit = min(60, max(0, ctx.max_inputs)); start = _settled(ctx); frame = start.frame; inputs = calls = 0; reason = ""; blank = start.blank_seen
    layout, readback = ({"type": "object", "properties": {"rows": {"type": "array", "items": {"type": "string"}}, "cursor": {"type": "array", "items": {"type": "integer"}}, "case_toggle": {"anyOf": [{"type": "array", "items": {"type": "integer"}}, {"type": "null"}]}, "done_key": {"type": "array", "items": {"type": "integer"}}, "presets": {"type": "array", "items": {"type": "string"}}}, "required": ["presets"]}, {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]})
    for attempt in range(2):
        reply = ctx.model.ask("Read the visible on-screen name keyboard and return only its layout as JSON.", 'Return rows as strings with one character per cell, cursor/case_toggle/done_key as [row,column] (case_toggle may be null), and presets as visible preset names. Text to enter: ' + text, [for_model(frame)], layout, role="skill")
        calls += 1; data = reply.data or {}; rows, cursor, done = data.get("rows") or data.get("layout"), data.get("cursor"), data.get("done_key")
        preset = next((p for p in data.get("presets", []) if isinstance(p, str) and p.casefold() == text.casefold()), None) if isinstance(data.get("presets"), list) else None
        if preset is not None:
            selected = select(ctx, preset); inputs += selected.inputs; calls += selected.calls; frame = selected.after
            return _result(selected.status, selected.reason, inputs, calls, start.frame, frame, blank=blank or selected.blank_seen)
        valid = lambda p: isinstance(p, list) and len(p) == 2 and all(type(v) is int for v in p) and 0 <= p[0] < len(rows) and 0 <= p[1] < len(rows[p[0]])
        if not isinstance(rows, list) or not rows or any(not isinstance(r, str) or not r for r in rows) or not valid(cursor) or not valid(done):
            return _result("failed", "keyboard not identified", inputs, calls, start.frame, frame, blank=blank)
        cur = list(cursor)
        def press(key):
            nonlocal inputs
            if inputs >= limit: return False
            ctx.env.press(key, ctx.move_frames); inputs += 1; return True
        def go(dest):
            nonlocal cur
            if not valid(dest): return False
            for key, count in (("RIGHT" if dest[1] > cur[1] else "LEFT", abs(dest[1]-cur[1])), ("DOWN" if dest[0] > cur[0] else "UP", abs(dest[0]-cur[0]))):
                for _ in range(count):
                    if not press(key): return False
            cur = list(dest); return True
        toggle = data.get("case_toggle"); lower = False
        if not valid(toggle): reason = "uppercase fallback"
        for char in text:
            want_lower = char.islower() and not reason
            if want_lower != lower and valid(toggle):
                if not go(toggle) or not press("A"): return _result("blocked", "input limit", inputs, calls, start.frame, frame, blank=blank)
                lower = want_lower
            target = char.lower() if want_lower else char.upper()
            dest = next(([ri, ci] for ri, row in enumerate(rows) for ci, cell in enumerate(row) if cell.upper() == target.upper()), None)
            if dest is None or not go(dest) or not press("A"): return _result("failed" if dest is None else "blocked", "character missing" if dest is None else "input limit", inputs, calls, start.frame, frame, blank=blank)
        settled = _settled(ctx); frame = settled.frame; blank |= settled.blank_seen
        check = ctx.model.ask("Read only the name currently shown in the name field.", 'Return JSON with the current name as "name".', [for_model(frame)], readback, role="skill")
        calls += 1; name = (check.data or {}).get("name")
        if isinstance(name, str) and name.casefold() == text.casefold():
            if not go(done) or not press("A"): return _result("blocked", "input limit", inputs, calls, start.frame, frame, blank=blank)
            settled = _settled(ctx); frame = settled.frame; blank |= settled.blank_seen
            return _result("done", reason, inputs, calls, start.frame, frame, blank=blank)
        if attempt or not isinstance(name, str): return _result("failed", "name read-back mismatch", inputs, calls, start.frame, frame, blank=blank)
        erase = next((key for key in ("B", "BACKSPACE", "DELETE") if key in ctx.env.inputs()), None)
        if erase is None: return _result("failed", "cannot clear name for retry", inputs, calls, start.frame, frame, blank=blank)
        if not all(press(erase) for _ in name): return _result("blocked", "input limit", inputs, calls, start.frame, frame, blank=blank)
        frame = _settled(ctx).frame
def run_affordance(ctx: Ctx, aff: dict, perception: dict, text: str | None = None) -> SkillResult:
    verb, target, at = aff.get("verb"), aff.get("target", ""), aff.get("at")
    player = next((e.get("at") for e in perception.get("entities", []) if e.get("kind", "").lower() == "player"), None)
    if verb == "approach": return _pointer(ctx, at, target) if ctx.env.pointer else approach(ctx, at, player)
    if verb == "interact": return _pointer(ctx, at, target) if ctx.env.pointer else interact(ctx, at, player)
    if verb == "select": return select(ctx, target)
    if verb == "advance": return advance(ctx)
    if verb == "cancel": return cancel(ctx)
    if verb == "press": return press(ctx, target)
    if verb == "wait_until": return wait_until(ctx)
    if verb == "type": return type_text(replace(ctx, max_inputs=60), text or target)
    if verb == "click":
        if ctx.env.pointer: return _pointer(ctx, at, target)
        frame = ctx.env.screenshot(); return _result("blocked", "no pointer", 0, 0, frame, frame)
    frame = ctx.env.screenshot(); return _result("failed", f"unknown affordance: {verb}", 0, 0, frame, frame)
