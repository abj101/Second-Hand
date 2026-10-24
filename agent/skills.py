"""Game-neutral, closed-loop input skills."""
from dataclasses import dataclass, field, replace; from typing import Literal
from functools import wraps
import re
import numpy as np
from PIL import Image, ImageDraw
from agent.env import Env
from agent.locate import refine
from agent.model import Ollama
from agent.observer import _blank, _residual, changed, cut, for_model, scroll, wait_settled
INTERACT_MIN_CHANGED_TILE_FRACTION = 0.25  # ponytail: quarter-tile minimum change, calibrated on m1_building_live_150 S146-149 vs S123/S126; raise if sprite noise exceeds it
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
    moves: list[dict] = field(default_factory=list)
    self_at: tuple[float, float] | None = None
    failure: dict | None = None  # why a blocked skill stopped: {"stage": "route"|"crossing", "reason", "direction"?}
    settled: bool | None = None  # False when any settle in the skill timed out unsettled; None = unknown (legacy)
    arrival_frame: Image.Image | None = None
@dataclass
class Ctx:
    env: Env
    model: Ollama
    tile_px: int | None = None
    move_frames: int = 16
    max_inputs: int = 30
    settles: list = field(default_factory=list)  # every settle's flag, shared by reference through replace(ctx)
    actor_boxes: object = None
    self_spot: tuple[float, float] | None = None
def _settled(ctx, **kw):
    settle = wait_settled(ctx.env, **kw); ctx.settles.append(settle.settled); return settle
def _tracked(fn):  # a skill's settled flag: False if any settle inside it (its own, nested skills', travel legs') timed out
    @wraps(fn)
    def run(ctx, *args, **kwargs):
        mark = len(ctx.settles); result = fn(ctx, *args, **kwargs)
        return replace(result, settled=all(ctx.settles[mark:]))
    return run
def _result(status, reason, inputs, calls, before, after, total=(0, 0), blank=False, moves=None, self_at=None, failure=None, arrival_frame=None):
    moves = moves or []
    return SkillResult(status, reason, inputs, calls, before, after, tuple(total), blank, moves, self_at or next((m.get("self_at") for m in reversed(moves) if isinstance(m, dict) and m.get("self_at") is not None), None), failure, arrival_frame=arrival_frame)
def _crossing(exit_target): return {"stage": "crossing", "reason": "exit not crossed"} if exit_target is not None else None
def _step(ctx, direction, before):
    frames = []
    ctx.env.press(direction, ctx.move_frames, on_frame=frames.append)
    end = _settled(ctx)
    if all(f.tobytes() == before.tobytes() for f in [*frames, end.frame]):
        end.no_response = True; ctx._last_residual_box, ctx._last_residual_box_coverage = None, 0
        return end, None, end.blank_seen, None
    shifts = [scroll(a, b) for a, b in zip([before, *frames], frames)]
    move = scroll(before, end.frame, max_shift=max(4, ctx.tile_px or 16)) if any(s is None for s in shifts) else tuple(sum(s[i] for s in shifts) for i in (0, 1))
    if move == (0, 0):
        reacted = False
        if ctx.self_spot is not None and ctx.tile_px:
            t = ctx.tile_px; x, y = (round(v * n) for v, n in zip(ctx.self_spot, before.size)); r, outer = round(.75 * t), round(1.5 * t)
            # ponytail: sprites larger than 1.5 tiles touch the ring and lose walls; measure sprite extent from motion if needed.
            mask = np.logical_or.reduce([np.any(np.asarray(f) != np.asarray(before), axis=2) for f in [*frames, end.frame]])
            # ponytail: ring ends 1.5 tiles from the motion centroid; widen it if nearby effects escape it.
            ring = np.zeros_like(mask); ring[max(0, y-outer):y+outer+1, max(0, x-outer):x+outer+1] = True; ring[max(0, y-r):y+r+1, max(0, x-r):x+r+1] = False
            for x0, y0, x1, y1 in ctx.actor_boxes() if callable(ctx.actor_boxes) else (): ring[max(0, y0):max(0, y1), max(0, x0):max(0, x1)] = False
            # ponytail: a missed wall costs one re-bump; widen the ring rule if NPC-adjacent misses matter.
            reacted = mask[max(0, y-r):y+r+1, max(0, x-r):x+r+1].any() and not (mask & ring).any()
        if not reacted:
            end.no_response = True; ctx._last_residual_box, ctx._last_residual_box_coverage = None, 0
            return end, None, end.blank_seen, None
    if any(s is None for s in shifts):
        ctx._last_residual_box, ctx._last_residual_box_coverage = None, 0
        return end, move, end.blank_seen or any(_blank(f) for f in frames), None
    cumulative, at = [(0, 0)], [0, 0]
    for shift in shifts:
        at[:] = [at[i] + shift[i] for i in (0, 1)]; cumulative.append(tuple(at))
    box, _tile, coverage, accepted = _residual([before, *frames], cumulative) if shifts else (None, 0, 0, False)
    ctx._last_residual_box_coverage = coverage
    if box:
        dx, dy = (round(v / 2) for v in cumulative[-1]); ctx._last_residual_box = (box[0] + dx, box[1] + dy, box[2] + dx, box[3] + dy)
    else: ctx._last_residual_box = None
    self_at = ((box[0] + box[2] + cumulative[-1][0]) / (2 * before.width),
               (box[1] + box[3] + cumulative[-1][1]) / (2 * before.height)) if accepted else None
    return end, move if shifts else None, end.blank_seen or any(_blank(f) for f in frames), self_at
@_tracked
def _pointer(ctx, at, target):
    first = _settled(ctx)
    if ctx.max_inputs < 1: return _result("blocked", "input limit", 0, 0, first.frame, first.frame, blank=first.blank_seen)
    point, _ = refine(ctx.model, first.frame, target, at)
    if point is None:
        return _result("failed", "target not visible", 0, 1, first.frame, first.frame, blank=first.blank_seen)
    ctx.env.click(point); last = _settled(ctx)
    return _result("done", "", 1, 1, first.frame, last.frame, blank=first.blank_seen or last.blank_seen)
def on_player(target_at, player_at, size, tile_px):  # the one screen test for "target is the player": approach refuses it, World.options does not offer it
    return player_at is not None and target_at is not None and max(abs(target_at[i] - player_at[i]) * size[i] for i in (0, 1)) <= (tile_px or 1) / 2
@_tracked
def approach(ctx: Ctx, target_at: list[float], player_at: list[float] | None, *, exit_target=None, route=None) -> SkillResult:
    first = _settled(ctx)
    if ctx.env.pointer: return _pointer(ctx, target_at, "")
    if player_at is None: return _result("blocked", "player position unknown", 0, 0, first.frame, first.frame, blank=first.blank_seen)
    initial, before, blank = first.frame, first.frame, first.blank_seen
    calls = 0  # exit tests walk to the tile-snapped perceived point; a miss shows as no scene cut, so no refine call
    w, h = before.size; delta = [(target_at[0] - player_at[0]) * w, (target_at[1] - player_at[1]) * h]
    if route is None and on_player(target_at, player_at, before.size, ctx.tile_px): return _result("blocked", "target is the player", 0, calls, initial, before, blank=blank)
    if ctx.tile_px: delta[:] = [round(v / ctx.tile_px) * ctx.tile_px for v in delta]
    total, blocked, inputs, last_dir, pushed, axis_dirs, moves = [0, 0], set(), 0, None, False, {}, []
    result = lambda *args, **kw: _result(*args, moves=moves, **kw)
    def step(direction, old):
        end, move, saw_blank, self_at = _step(ctx, direction, old); moves.append({"dir": direction, "shift": move, "self_at": self_at, **({"no_response": True} if getattr(end, "no_response", False) else {})}); return end, move, saw_blank
    if route is not None:
        dirs = route.get("dirs", [])[:max(0, ctx.max_inputs)]
        for direction in dirs:
            end, move, saw_blank = step(direction, before); inputs += 1
            if getattr(end, "no_response", False): return result("blocked", "no response", inputs, calls, initial, end.frame, total, blank or saw_blank, failure={"stage": "route", "reason": "no response", "direction": direction})
            if cut(before, end.frame, saw_blank): return result("done", "scene cut", inputs, calls, initial, end.frame, total, blank or saw_blank)
            if move == (0, 0): return result("blocked", "route blocked", inputs, calls, initial, end.frame, total, blank or saw_blank, failure={"stage": "route", "reason": "route blocked", "direction": direction})
            if move is not None: total[:] = [total[i] + move[i] for i in (0, 1)]
            blank |= saw_blank; before = end.frame; last_dir = direction
    while route is None and inputs < max(0, ctx.max_inputs):
        if max(map(abs, delta)) <= .5:
            if last_dir and not pushed and inputs < ctx.max_inputs:
                old = before
                end, move, saw_blank = step(last_dir, old)
                inputs += 1
                if getattr(end, "no_response", False): return result("blocked", "no response", inputs, calls, initial, end.frame, total, blank or saw_blank, failure={"stage": "route", "reason": "no response", "direction": last_dir})
                if move is None and not saw_blank: return result("blocked" if exit_target is not None else "failed", "exit not crossed" if exit_target is not None else "scroll unsure", inputs, calls, initial, end.frame, total, blank or saw_blank, failure=_crossing(exit_target))
                if move is not None: total[:] = [total[i] + move[i] for i in (0, 1)]
                blank |= saw_blank
                if cut(old, end.frame, saw_blank): return result("done", "scene cut", inputs, calls, initial, end.frame, total, blank)
                before, pushed = end.frame, True
            if exit_target is None: return result("done", "", inputs, calls, initial, before, total, blank)
            break
        tile = ctx.tile_px or 1
        d = [v / tile for v in delta]
        axes = [a for a in (0, 1) if a not in blocked]
        if not axes:
            near = ctx.tile_px is not None and sum(round(abs(v)) for v in d) <= 1
            if not near or exit_target is None: return result("done" if near else "blocked", "adjacent" if near else "exit not crossed" if exit_target is not None else "both axes blocked", inputs, calls, initial, before, total, blank, failure=_crossing(exit_target))
            break
        axis = max(axes, key=lambda a: abs(d[a]))
        direction = axis_dirs.get(axis, ("RIGHT" if axis == 0 else "DOWN")) if abs(delta[axis]) < .5 else (("RIGHT" if delta[0] > 0 else "LEFT") if axis == 0 else ("DOWN" if delta[1] > 0 else "UP"))
        if blocked: direction = axis_dirs.get(axis, direction)
        old = before; end, move, saw_blank = step(direction, old); inputs += 1
        if getattr(end, "no_response", False): return result("blocked", "no response", inputs, calls, initial, end.frame, total, blank or saw_blank, failure={"stage": "route", "reason": "no response", "direction": direction})
        if cut(old, end.frame, saw_blank): return result("done", "scene cut", inputs, calls, initial, end.frame, total, blank or saw_blank)
        if move is None: return result("blocked" if exit_target is not None else "failed", "exit not crossed" if exit_target is not None else "scroll unsure", inputs, calls, initial, end.frame, total, blank or saw_blank, failure=_crossing(exit_target))
        if sizes := [abs(v) for v in move if v]: ctx.tile_px = min(sizes + ([ctx.tile_px] if ctx.tile_px else []))
        if ctx.tile_px: delta[:] = [round(v / ctx.tile_px) * ctx.tile_px for v in delta]
        total[:] = [total[i] + move[i] for i in (0, 1)]; blank |= saw_blank
        if move == (0, 0):
            blocked.add(axis)
            near = ctx.tile_px is not None and sum(round(abs(v / ctx.tile_px)) for v in delta) <= 1
            if near and exit_target is None: return result("done", "adjacent", inputs, calls, initial, end.frame, total, blank)
        else:
            delta[0] += move[0]; delta[1] += move[1]
            if ctx.tile_px: delta[:] = [round(v / ctx.tile_px) * ctx.tile_px for v in delta]
            blocked.clear()
            if move[axis]: axis_dirs[axis] = direction
        before, last_dir = end.frame, direction
    if route is not None and exit_target is not None and inputs < ctx.max_inputs and route.get("push"):
        old = before; end, move, saw_blank = step(route["push"], old); inputs += 1
        if getattr(end, "no_response", False): return result("blocked", "no response", inputs, calls, initial, end.frame, total, blank or saw_blank, failure={"stage": "route", "reason": "no response", "direction": route["push"]})
        if cut(old, end.frame, saw_blank): return result("done", "scene cut", inputs, calls, initial, end.frame, total, blank or saw_blank)
        if move is not None: total[:] = [total[j] + move[j] for j in (0, 1)]
        blank |= saw_blank; before = end.frame
    return result("done" if route is not None and exit_target is None and len(route.get("dirs", [])) <= inputs else "blocked", "" if route is not None and exit_target is None and len(route.get("dirs", [])) <= inputs else "exit not crossed" if exit_target is not None else "input limit", inputs, calls, initial, before, total, blank, failure=_crossing(exit_target))
@_tracked
def interact(ctx: Ctx, target_at: list[float], player_at: list[float] | None, *, route=None) -> SkillResult:
    if ctx.env.pointer: return _pointer(ctx, target_at, "")
    start = _settled(ctx)
    if route is None and ctx.tile_px: return _result("blocked", "no route beside target", 0, 0, start.frame, start.frame, failure={"stage": "route", "reason": "no route beside target"})
    path = {**route, "dirs": route.get("dirs", [])[:-1]} if route is not None else None
    moved = approach(ctx, target_at, player_at, route=path)
    if moved.status != "done" or moved.reason == "scene cut" or moved.inputs >= ctx.max_inputs: return moved
    inputs, total, face_blank = moved.inputs, list(moved.scroll), False
    before_a = moved.after
    if route is not None:
        face = route["dirs"][-1] if route.get("dirs") else None
        if face and face in ctx.env.inputs() and inputs < ctx.max_inputs:
            end, move, saw_blank, self_at = _step(ctx, face, moved.after); inputs += 1; moved.moves.append({"dir": face, "shift": move, "self_at": self_at})
            before_a = end.frame
            face_blank = saw_blank
            if move is not None: total[:] = [total[i] + move[i] for i in (0, 1)]
            if cut(moved.after, end.frame, saw_blank):
                return _result("done", "scene cut", inputs, moved.calls, start.frame, end.frame, total, start.blank_seen or moved.blank_seen or saw_blank, moved.moves)
    elif player_at is not None:
        dx = (target_at[0] - player_at[0]) * start.frame.width + total[0]
        dy = (target_at[1] - player_at[1]) * start.frame.height + total[1]
        face = ("RIGHT" if dx > 0 else "LEFT") if abs(dx) >= abs(dy) and dx else ("DOWN" if dy > 0 else "UP")
        if face in ctx.env.inputs() and inputs < ctx.max_inputs:
            end, move, saw_blank, self_at = _step(ctx, face, moved.after); inputs += 1; moved.moves.append({"dir": face, "shift": move, "self_at": self_at})
            before_a = end.frame
            face_blank = saw_blank
            if move is not None: total[:] = [total[i] + move[i] for i in (0, 1)]
            if cut(moved.after, end.frame, saw_blank):
                return _result("done", "scene cut", inputs, moved.calls, start.frame, end.frame, total, start.blank_seen or moved.blank_seen or saw_blank, moved.moves)
    mask = tuple(ctx.actor_boxes()) if callable(ctx.actor_boxes) else ()
    if inputs < ctx.max_inputs: ctx.env.press("A", ctx.move_frames); inputs += 1
    end = _settled(ctx)
    if callable(ctx.actor_boxes): mask += tuple(ctx.actor_boxes())
    changed_a = changed(before_a, end.frame, mask=mask)
    included = Image.new("1", before_a.size, 1)
    draw = ImageDraw.Draw(included)
    for x0, y0, x1, y1 in mask:
        x0, y0, x1, y1 = max(0, x0), max(0, y0), min(before_a.width, x1), min(before_a.height, y1)
        if x0 < x1 and y0 < y1: draw.rectangle((x0, y0, x1 - 1, y1 - 1), fill=0)
    changed_pixels = round(changed_a * included.histogram()[1])
    changed_a = changed_pixels >= (ctx.tile_px or 8) ** 2 * INTERACT_MIN_CHANGED_TILE_FRACTION
    return _result("done" if changed_a else "failed",
                   "" if changed_a else "nothing changed", inputs, moved.calls,
                   start.frame, end.frame, total, start.blank_seen or moved.blank_seen or face_blank or end.blank_seen, moved.moves)
@_tracked
def select(ctx: Ctx, item: str) -> SkillResult:
    start = _settled(ctx)
    schema = {"type": "object", "properties": {"items": {"type": "array", "items": {"type": "string"}},
              "cursor": {"type": "integer"}, "layout": {"type": "string"}, "cursor_cell": {"type": "array", "items": {"type": "integer"}}, "item_cell": {"type": "array", "items": {"type": "integer"}}}, "required": ["items", "cursor", "layout"]}
    reply = ctx.model.ask("Read the visible menu and answer only with JSON.", f'Return one JSON object with these keys: "items" (the visible menu labels in order as strings), "cursor" (the highlighted item index, zero-based integer), and "layout" ("vertical", "horizontal" or "grid"); for a grid also "cursor_cell" and "item_cell", the [row, column] of the highlighted item and of the target. Match the target label case-insensitively: {item}', [for_model(start.frame)], schema, role="skill")
    data = reply.data or {}; items, cursor = data.get("items"), data.get("cursor")
    cells = [data.get("cursor_cell"), data.get("item_cell")]
    if data.get("layout") == "grid" and all(isinstance(c, list) and len(c) == 2 and all(type(v) is int for v in c) for c in cells):  # a keyboard: rows and columns, as type_text moves
        (r0, c0), (r1, c1) = cells
        keys = ["DOWN" if r1 > r0 else "UP"] * abs(r1 - r0) + ["RIGHT" if c1 > c0 else "LEFT"] * abs(c1 - c0)
    else:
        if not isinstance(items, list) or not items or not isinstance(cursor, int) or not 0 <= cursor < len(items): return _result("failed", "menu or item not identified", 0, 1, start.frame, start.frame, blank=start.blank_seen)
        diff = next((i - cursor for i, name in enumerate(items) if str(name).casefold() == item.casefold()), None)
        if diff is None: return _result("failed", "menu or item not identified", 0, 1, start.frame, start.frame, blank=start.blank_seen)
        keys = [("RIGHT" if diff > 0 else "LEFT") if data.get("layout") == "horizontal" else ("DOWN" if diff > 0 else "UP")] * abs(diff)
    n = len(keys) + 1
    if n > ctx.max_inputs: return _result("blocked", "input limit", 0, 1, start.frame, start.frame, blank=start.blank_seen)
    for key in keys: ctx.env.press(key, ctx.move_frames)
    ctx.env.press("A", ctx.move_frames); end = _settled(ctx)
    return _result("done", "", n, 1, start.frame, end.frame, blank=start.blank_seen or end.blank_seen)
def _forced(end):
    def axes(shift, last=None, **extra):
        moves = [{"dir": ("RIGHT" if sx < 0 else "LEFT") if sx else ("DOWN" if sy < 0 else "UP"), "shift": (sx, sy), "forced": True, **extra}
                 for sx, sy in ((shift[0], 0), (0, shift[1])) if sx or sy]
        return sorted(moves, key=lambda m: last is not None and (m["dir"] in ("UP", "DOWN")) == (last in ("UP", "DOWN")))
    source = axes(end.scroll, end.last_dir)
    if source and end.blank_seen: source[-1]["last_dir"] = end.last_dir
    return source + axes(end.after_scroll, after_cut=True)

@_tracked
def press(ctx: Ctx, key: str) -> SkillResult:
    start = _settled(ctx); keys = ctx.env.inputs(); words = set(re.findall(r"[a-z0-9]+", key.casefold())); matches = [k for k in keys if k.casefold() in words]
    key = key if key in keys else matches[0] if len(matches) == 1 else None
    if key is None or ctx.max_inputs < 1: return _result("blocked", "unsupported input" if key is None else "input limit", 0, 0, start.frame, start.frame, blank=start.blank_seen)
    end, move, blank, self_at = _step(ctx, key, start.frame) if key in ("UP", "DOWN", "LEFT", "RIGHT") else (None, None, False, None)
    if end is None: ctx.env.press(key, ctx.move_frames); end = _settled(ctx)
    return _result("done", "", 1, 0, start.frame, end.frame, blank=start.blank_seen or end.blank_seen or blank, moves=[{"dir": key, "shift": move, "self_at": self_at, **({"no_response": True} if getattr(end, "no_response", False) else {})}] if key in ("UP", "DOWN", "LEFT", "RIGHT") else _forced(end), self_at=self_at, arrival_frame=end.arrival_frame)
@_tracked
def press_seq(ctx: Ctx, keys: list[str]) -> SkillResult:
    if not keys: frame = ctx.env.screenshot(); return _result("failed", "no keys", 0, 0, frame, frame)
    results = []
    for key in keys[:8]:
        results.append(press(ctx, key))
        if results[-1].status != "done": break
    first, last = results[0], results[-1]
    moves, crossed = [], False
    for r in results:
        moves.extend({**m, "after_cut": True} if crossed and isinstance(m, dict) else m for m in r.moves); crossed |= r.blank_seen
    return _result(last.status, last.reason, sum(r.inputs for r in results), sum(r.calls for r in results), first.before, last.after, blank=any(r.blank_seen for r in results), moves=moves, arrival_frame=next((r.arrival_frame for r in results if r.arrival_frame is not None), None))
def advance(ctx: Ctx) -> SkillResult: return press(ctx, "A")
def cancel(ctx: Ctx) -> SkillResult: return press(ctx, "B")
@_tracked
def wait_until(ctx: Ctx, max_frames: int = 240) -> SkillResult:
    start = _settled(ctx); end = _settled(ctx, max_frames=max_frames)
    return _result("done" if end.settled else "failed", "" if end.settled else "not settled", 0, 0, start.frame, end.frame, blank=start.blank_seen or end.blank_seen, moves=_forced(end), arrival_frame=end.arrival_frame)
@_tracked
def type_text(ctx: Ctx, text: str) -> SkillResult:
    limit = min(60, max(0, ctx.max_inputs)); start = _settled(ctx); frame = start.frame; inputs = calls = 0; reason = ""; blank = start.blank_seen
    if not str(text or "").strip(): return _result("failed", "no text given to type", 0, 0, start.frame, frame, blank=blank)  # an empty target would erase what is already entered
    layout, readback = ({"type": "object", "properties": {"rows": {"type": "array", "items": {"type": "string"}}, "cursor": {"type": "array", "items": {"type": "integer"}}, "case_toggle": {"anyOf": [{"type": "array", "items": {"type": "integer"}}, {"type": "null"}]}, "done_key": {"type": "array", "items": {"type": "integer"}}, "presets": {"type": "array", "items": {"type": "string"}}}, "required": ["presets"]}, {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]})
    for attempt in range(2):
        reply = ctx.model.ask("Read the visible on-screen name keyboard and return only its layout as JSON.", 'Return rows as strings with one character per cell, cursor/case_toggle/done_key as [row,column] (case_toggle may be null), and presets as visible preset names. Text to enter: ' + text, [for_model(frame)], layout, role="skill")
        calls += 1; data = reply.data or {}; rows, cursor, done = data.get("rows") or data.get("layout"), data.get("cursor"), data.get("done_key")
        # rows come back as strings ("A B C") or as lists of cells (["A","B","PK"]); keep cells either way
        rows = [list(r.replace(" ", "")) if isinstance(r, str) else [str(c) for c in r] if isinstance(r, list) else r for r in rows] if isinstance(rows, list) else rows
        preset = next((p for p in data.get("presets", []) if isinstance(p, str) and p.casefold() == text.casefold()), None) if isinstance(data.get("presets"), list) else None
        if preset is not None:
            selected = select(ctx, preset); inputs += selected.inputs; calls += selected.calls; frame = selected.after
            return _result(selected.status, selected.reason, inputs, calls, start.frame, frame, blank=blank or selected.blank_seen)
        valid = lambda p: isinstance(p, list) and len(p) == 2 and all(type(v) is int for v in p) and 0 <= p[0] < len(rows) and 0 <= p[1] < len(rows[p[0]])
        if not isinstance(rows, list) or not rows or any(not isinstance(r, list) or not r for r in rows) or not valid(cursor):
            return _result("failed", "keyboard not identified", inputs, calls, start.frame, frame, blank=blank)
        if not valid(done): done = next(([ri, ci] for ri, row in enumerate(rows) for ci, cell in enumerate(row) if cell.casefold() in ("ed", "end", "ok", "done")), None)
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
            if done is None: return _result("done", "typed; no done key found, confirm it", inputs, calls, start.frame, frame, blank=blank)
            if not go(done) or not press("A"): return _result("blocked", "input limit", inputs, calls, start.frame, frame, blank=blank)
            settled = _settled(ctx); frame = settled.frame; blank |= settled.blank_seen
            return _result("done", reason, inputs, calls, start.frame, frame, blank=blank)
        if attempt or not isinstance(name, str): return _result("failed", "name read-back mismatch", inputs, calls, start.frame, frame, blank=blank)
        erase = next((key for key in ("B", "BACKSPACE", "DELETE") if key in ctx.env.inputs()), None)
        if erase is None: return _result("failed", "cannot clear name for retry", inputs, calls, start.frame, frame, blank=blank)
        if not all(press(erase) for _ in name): return _result("blocked", "input limit", inputs, calls, start.frame, frame, blank=blank)
        frame = _settled(ctx).frame
@_tracked
def run_affordance(ctx: Ctx, aff: dict, perception: dict, text: str | None = None, *, exit: bool = False, route: dict | None = None, onto: bool = False) -> SkillResult:
    verb, target, at = aff.get("verb"), aff.get("target", ""), aff.get("at")
    player = next((e.get("at") for e in perception.get("entities", []) if e.get("kind", "").lower() == "player"), None)
    if verb == "approach": return _pointer(ctx, at, target) if ctx.env.pointer else approach(ctx, at, player, exit_target=None if onto else (target or "visible target") if exit else target if target.startswith("exit") else None, route=route)
    if verb == "interact": return _pointer(ctx, at, target) if ctx.env.pointer else interact(ctx, at, player, route=route)
    if verb == "select":
        if not any(str(e.get("name", "")).casefold() == target.casefold() and e.get("selectable") is True for e in perception.get("entities", [])):
            frame = ctx.env.screenshot(); return _result("failed", "not a selectable choice on this screen", 0, 0, frame, frame, failure={"stage": "interaction", "reason": "not a selectable choice on this screen"})
        return select(ctx, target)
    if verb == "advance": return advance(ctx)
    if verb == "cancel": return cancel(ctx)
    if verb == "press": return press(ctx, target)
    if verb == "wait_until": return wait_until(ctx)
    if verb == "type": return type_text(replace(ctx, max_inputs=60), text or target)
    if verb == "click":
        if ctx.env.pointer: return _pointer(ctx, at, target)
        frame = ctx.env.screenshot(); return _result("blocked", "no pointer", 0, 0, frame, frame)
    frame = ctx.env.screenshot(); return _result("failed", f"unknown affordance: {verb}", 0, 0, frame, frame)
