from collections import deque; from agent.observer import snap
DIRS = {"UP": (0, -1), "DOWN": (0, 1), "LEFT": (-1, 0), "RIGHT": (1, 0)}
_OPPOSITE = {"UP": "DOWN", "DOWN": "UP", "LEFT": "RIGHT", "RIGHT": "LEFT"}
def _id(place, pos): return f"{place}@{pos[0]},{pos[1]}"
def _get(mem, place, pos): return mem.get("tiles", _id(place, pos))
def _new(place, pos, step): return {"id": _id(place, pos), "place": place, "x": pos[0], "y": pos[1], "floor": None, "blocked": [], "pushed": [], "exit": None, "step": step}
def _save(mem, row, before): return mem.upsert("tiles", row) if {k: v for k, v in row.items() if k != "step"} != {k: v for k, v in before.items() if k != "step"} else None
def tile_of(at, player_at, pos, size, tile_px): a, p = snap(at, size, tile_px), snap(player_at, size, tile_px); return tuple(pos[i] + round((a[i] - p[i]) * size[i] / tile_px) for i in (0, 1))
def fold(mem, place, pos, moves, tile_px, step, actors: frozenset[tuple[int, int]] = frozenset()):
    here = pos
    for move in moves:
        if move is None: continue
        direction, shift = (move.get("dir"), move.get("shift")) if isinstance(move, dict) else move
        if direction not in DIRS or shift is None: continue
        if (shift == (0, 0) or shift == 0) and (here[0] + DIRS[direction][0], here[1] + DIRS[direction][1]) in actors: continue
        row = _get(mem, place, here) or _new(place, here, step); before = dict(row, blocked=list(row["blocked"]), pushed=list(row["pushed"]))
        if row["floor"] is not True: row["floor"] = True; row["step"] = step
        if shift == (0, 0) or shift == 0:
            if direction not in row["blocked"]: row["blocked"].append(direction); row["step"] = step
            _save(mem, row, before); continue
        if direction in row["blocked"]: row["blocked"].remove(direction)
        sx, sy = DIRS[direction]; shift = (shift, 0) if isinstance(shift, int) and sx else (0, shift) if isinstance(shift, int) else shift
        dx, dy = (round(-v / tile_px) for v in shift); count = max(abs(dx), abs(dy)); sx = (dx > 0) - (dx < 0); sy = (dy > 0) - (dy < 0)
        for _ in range(count):
            here = (here[0] + sx, here[1] + sy); floor = _get(mem, place, here) or _new(place, here, step)
            old = dict(floor, blocked=list(floor["blocked"]), pushed=list(floor["pushed"]))
            if floor["floor"] is not True: floor["floor"] = True; floor["step"] = step
            _save(mem, floor, old)
        _save(mem, row, before)
    return here
def mark_exit(mem, place, tile, direction, to, arrive, step):
    for src, at, way, dst, dest, source in ((place, tile, direction, to, arrive, "observed"), (to, arrive, _OPPOSITE[direction], place, tile, "inferred")):
        row = _get(mem, src, at) or _new(src, at, step)
        if source == "inferred" and (row.get("exit") or {}).get("src") == "observed": continue  # a guessed way back never replaces a crossed exit
        old = dict(row, blocked=list(row["blocked"]), pushed=list(row["pushed"]))
        row.update(exit={"dir": way, "to": dst, "arrive": list(dest), "src": source}, floor=True, step=step); _save(mem, row, old)
def exit_to(mem, place, tile, direction): row = _get(mem, place, tile); edge = row.get("exit") if row else None; return edge if edge and edge.get("dir") == direction else None
def route(mem, place, start, goal, strict=True, *, occupied=frozenset()):  # strict: an inferred exit (the guessed way back) is avoided too, unless no route avoids it; occupied: tiles held by actors
    if goal in occupied: return None
    rows = [r for r in mem.rows("tiles") if r.get("place") == place]; xs = [start[0], goal[0], *(r["x"] for r in rows)]; ys = [start[1], goal[1], *(r["y"] for r in rows)]
    bounds = (min(xs) - 1, max(xs) + 1, min(ys) - 1, max(ys) + 1); queue = deque([(start, [])]); seen = {start}
    while queue:
        at, path = queue.popleft()
        if at == goal: return path
        row = _get(mem, place, at) or {}
        for direction, (dx, dy) in DIRS.items():
            nxt = (at[0] + dx, at[1] + dy)
            cell = _get(mem, place, nxt)
            edge = row.get("exit") or {}
            if direction in row.get("blocked", []) or (strict or edge.get("src") != "inferred") and direction == edge.get("dir") or nxt in seen or nxt in occupied or cell and cell.get("floor") is False and nxt != goal or not (bounds[0] <= nxt[0] <= bounds[1] and bounds[2] <= nxt[1] <= bounds[3]): continue
            seen.add(nxt); queue.append((nxt, path + [direction]))
    return route(mem, place, start, goal, False, occupied=occupied) if strict else None
def outward(mem, place, tile, placement=None):
    if placement in ("top wall", "bottom wall", "left wall", "right wall"): return {"top wall": "UP", "bottom wall": "DOWN", "left wall": "LEFT", "right wall": "RIGHT"}[placement]
    floors = [r for r in mem.rows("tiles") if r.get("place") == place and r.get("floor") is True]
    if not floors: return "UP"
    cx = sum(r["x"] for r in floors) / len(floors); cy = sum(r["y"] for r in floors) / len(floors); dx, dy = tile[0] - cx, tile[1] - cy
    return ("RIGHT" if dx > 0 else "LEFT") if abs(dx) > abs(dy) else ("DOWN" if dy > 0 else "UP")
def frontier(mem, place):
    result = []
    for row in mem.rows("tiles"):
        if row.get("place") != place or row.get("floor") is not True: continue
        for direction, (dx, dy) in DIRS.items():
            edge = row.get("exit") or {}
            if direction not in row.get("blocked", []) and direction not in row.get("pushed", []) and (edge.get("src") == "inferred" or direction != edge.get("dir")) and not _get(mem, place, (row["x"] + dx, row["y"] + dy)): result.append(((row["x"], row["y"]), direction))
    return result
