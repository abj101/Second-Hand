"""Spatial state and bookkeeping for the act path."""
import hashlib, json, re
from dataclasses import replace
from itertools import pairwise
from statistics import median
from PIL import ImageDraw
from agent.memory import TRANSIENT, _base, _distance, _nameable, normalize_kind, place_aliases, place_name
from agent.observer import changed, cut
from agent.space import DIRS, exit_to, fold, frontier, mark_exit, route, tile_of
from agent.skills import SkillResult, approach, on_player, press
from agent.track import Tracker

TRAVEL_MAX_INPUTS = 120
MISS_TO_STALE, MISS_TO_GONE, ACTOR_STALE_STEPS = 2, 4, 20
LANDMARKS_PER_PLACE, MAP_W, MAP_H, VIEW_TOKENS = 8, 21, 15, 500
OPTION_CAP = 10  # max rows excluding the free-buttons row; ponytail: soft for exits and characters, which are never collapsed (only known places collapse)

ACTOR_KINDS = {"person", "npc", "character", "enemy", "monster", "animal"}
TYPING = {"keyboard", "input_field"}  # a text field on a modal screen: type
FURNITURE = {"player", "dialogue_box", "text", "logo", "cursor"}  # screen chrome: never a choice on a modal screen

def fail_key(place, verb, target, inputs=(), ref=None):
    """Intent key "place|family|subject", the one key Loop.fails, the option notes and the trace share. Presses: family buttons, direction keys
    collapse (else the first button). Other actions: family approach for walking (approach, go_to, explore), else the verb (interact, select,
    advance, cancel, type). Subject: ref, the entity, exit or place the action resolved to (never a label), else the target's name."""
    if verb == "buttons":
        t, keys = str(target).split(), set(inputs)
        if t and all(x.upper() in keys for x in t): u = list(dict.fromkeys(x.upper() for x in t)); t = [x for x in u if x in DIRS] or u[:1]
        return f"{place}|buttons|{' '.join(t).casefold() or verb}"
    family = "approach" if verb in ("approach", "go_to", "explore", "") else verb
    return f"{place}|{family}|{ref or str(target).casefold() or verb}"
def exit_ref(x):  # an exit record's reference: its entity when it has one, else its lowest door tile, never its label
    tiles = sorted(tuple(d["tile"]) for d in x["doors"])
    if x["ident"]: return f"thing:{x['ident']}"
    return f"exit:{x['dest']}@{','.join(map(str, tiles[0])) if tiles else (x['dir'] or x['name'])}"

def _tried(f):  # a failure row as a note: cycle marks, else repeated tries
    return f"part of a back-and-forth cycle (step {f.get('step', 0)})" if str(f.get("why", "")).endswith(": cycle") else f"tried here {f['n']}×, last: {f.get('why', '')}"

def _yx(pos, name):  # sort key: row, column, name; no position sorts last
    return (pos[1], pos[0], str(name or "")) if isinstance(pos, (list, tuple)) and len(pos) == 2 else (10**6, 10**6, str(name or ""))

def _is_exit(e): return normalize_kind(e.get("kind")) == "exit" or str(e.get("name", "")).casefold().startswith("exit:")

def _beyond_suffix(far):  # far: one exit's {"unused_exits","unexplored"} from World.beyond, or None; " (1 unexplored exit, 10 unexplored spots beyond)" or ""
    far = far or {}
    parts = [f"{n} {word}{'' if n == 1 else 's'}" for n, word in ((far.get("unused_exits", 0), "unexplored exit"), (far.get("unexplored", 0), "unexplored spot")) if n]
    return f" ({', '.join(parts)} beyond)" if parts else ""

def _cross_key(place, tile, other):  # one 'crossings' row per exit tile and the place on its other side
    return f"{place}@{tile[0]},{tile[1]}>{other}"

def unseen_edges(mem, pid):  # untried edges of pid whose target cell is not in its seen store: "unexplored"
    seen = set((mem.get("seen", pid) or {}).get("cells", []))
    return [(t, d) for t, d in frontier(mem, pid) if f"{t[0] + DIRS[d][0]},{t[1] + DIRS[d][1]}" not in seen]

def open_cells(mem, pid):  # cells just past a screen edge that showed something (not uniform void), not seen since: the place may go on there
    row = mem.get("seen", pid) or {}
    return set(row.get("open", [])) - set(row.get("cells", []))

def _open_edge(frame, t):
    """{side: [i, ...]}: the edge tiles (i-th along that side) that show something, not uniform void, so the place may continue past them.
    ponytail: <=2 grey levels in a tile = void (Game Boy black/white borders); a game with textured void needs a better test"""
    g = frame.convert("L"); w, h = g.size; cols, rows = w // t, h // t
    drawn = lambda x, y: len(set(g.crop((x * t, y * t, x * t + t, y * t + t)).getdata())) > 2
    return {"LEFT": [y for y in range(rows) if drawn(0, y)], "RIGHT": [y for y in range(rows) if drawn(cols - 1, y)],
            "UP": [x for x in range(cols) if drawn(x, 0)], "DOWN": [x for x in range(cols) if drawn(x, rows - 1)]}

class World:
    _travel_open = False
    size = None
    def __init__(self, mem, tile_px):
        self.mem, self.tile_px = mem, tile_px
        self.tracker = Tracker(tile_px or 16)  # pixel tracks keep one id per moving thing; Loop wraps the env so it sees every frame
        self.pos, self.drift, self.drift_ref = (0, 0), None, None
        self.entity_tiles, self.entity_ids, self.anchors, self.actor_tiles = {}, {}, [], frozenset()
        self.entities, self.pa, self._prepared = [], None, None
        self._observed_per = None
        self.current_place, self.merge_pending, self.new_arrival = None, None, False
        self._travelled = None
        self._cut_arrival = None
        self._lone = None  # (step, dst, src): this step's crossing counted only its leaving side; mark_exit counts the arrival side
        self._last_miss_hash = {}
        self.self_spot, self._self_contradictions, self._self_from_motion = None, 0, False
        self._hud, self._persist = {}, {}
    def _anchor(self, per, size):
        return next((e.get("at") for e in per.get("entities", [])
                     if e.get("kind", "").casefold() == "player"), None)
    def fix_player(self, per, size):
        if per.get("screen") != "world" or not self.tile_px or not self._self_from_motion or self.self_spot is None: return None
        entities = per.get("entities", [])
        players = [e for e in entities if e.get("kind", "").casefold() == "player"]
        old = list(players[0]["at"]) if players and isinstance(players[0].get("at"), list) else None
        original = [(e, e.get("kind"), e.get("name")) for e in entities]
        median = self.self_spot
        dist = lambda at: max(abs(at[i] - median[i]) * size[i] for i in (0, 1))
        valid_at = lambda e: isinstance(e.get("at"), list) and len(e["at"]) == 2
        player = next((e for e in players if valid_at(e) and dist(e["at"]) <= 1.5 * self.tile_px), None)
        if player:
            fixed = list(player["at"])
        else:
            candidates = [(dist(e["at"]), e) for e in entities if valid_at(e)]
            near = min(candidates, key=lambda item: item[0]) if candidates else None
            if near and near[0] <= self.tile_px / 2:
                player = near[1]
                fixed = list(player["at"])
            else:
                player = {"kind": "player", "name": "player", "at": list(median)}
                entities.append(player)
                fixed = list(median)
        for e in players:
            if e is not player:
                e["kind"] = "character"
                if e.get("name") == "player": e["name"] = "character"
        player["kind"], player["name"] = "player", "player"
        changed = not players or any(e.get("kind") != kind or e.get("name") != name for e, kind, name in original)
        return {"from": old, "to": fixed} if changed else None
    def mark(self, frame, screen_was_world):
        if not self._self_from_motion or self.self_spot is None or not screen_was_world: return frame, False
        image = frame.copy()
        x, y = self.self_spot[0] * frame.width, self.self_spot[1] * frame.height
        # ponytail: fixed red outline and offsets from the 2026-10-13 test; pick a contrasting colour per frame if a colour game hides it.
        ImageDraw.Draw(image).rectangle([x - 9, y - 10, x + 9, y + 9], outline="red", width=1)
        return image, True
    def before(self, per, place, prior, size, step):
        self.current_place, self.size = place, size
        if prior and place != prior:
            self.pos, self.drift, self.drift_ref = self._seen_at(place) or (0, 0), None, None
            self._observed_per = None; self.tracker.clear()
        player = next((e for e in per.get("entities", []) if e.get("kind", "").casefold() == "player"), None)
        pa = self.self_spot if self._self_from_motion else self._anchor(per, size) if player else None
        self.pa = pa
        self.tracker.phase = (round(pa[0] * size[0]) % self.tile_px, round(pa[1] * size[1]) % self.tile_px) if pa and self._self_from_motion and self.tile_px else None  # sprites rest on the player's grid
        px, py = (pa[0] * size[0], pa[1] * size[1]) if pa else (0, 0); self.tracker.ignore = [(max(0, int(px) - 10), max(0, int(py) - 10), int(px) + 10, int(py) + 10)] if pa else []  # the player animates in place: not a thing that moved
        proj = lambda at: tile_of(at, pa, self.pos, size, self.tile_px) if pa and self.tile_px and isinstance(at, list) and len(at) == 2 else None
        tiles = {i: t for i, e in enumerate(per.get("entities", [])) if e.get("kind", "").casefold() != "player" and (t := proj(e.get("at")))}
        ids = self.entity_ids if per is self._observed_per else self.mem.observe(per, place, step, tiles, self._hits(per))
        self._observed_per = per; self._follow(per, ids)
        self.actor_tiles = frozenset(tiles[i] for i, e in enumerate(per.get("entities", [])) if i in tiles and self._actor(ids.get(i), e))
        self.entity_ids, self.entity_tiles = ids, {ids[i]: p for i, p in tiles.items() if i in ids}
        self.entities = [{"id": ids.get(i), "name": e.get("name"), "kind": e.get("kind"), "at": e.get("at")}
                         for i, e in enumerate(per.get("entities", []))]
        self._tiers(per, place, ids)
        return proj, tiles, ids
    def _hits(self, per):  # entity index -> id of the live pixel track under it (within a tile)
        hits = {}
        for i, e in enumerate(per.get("entities", [])):
            if normalize_kind(e.get("kind")) in ACTOR_KINDS and isinstance(e.get("at"), list) and len(e["at"]) == 2 and (t := self.tracker.near(e["at"], hits.values())): hits[i] = t  # only moving things have pixel tracks; furniture matches by name and tile
        return hits
    def _follow(self, per, ids):  # seed tracks for what was just perceived; a track lost in view for a while (no text box over it) means the thing left: "gone"
        self.tracker.seed([(ids[i], e["at"], self._actor(ids[i], e)) for i, e in enumerate(per.get("entities", []))
                           if i in ids and self._actor(ids[i], e) and e.get("kind", "").casefold() != "player" and isinstance(e.get("at"), list) and len(e["at"]) == 2])
        if per.get("screen") != "world" or per.get("text") or any(e.get("kind") == "dialogue_box" for e in per.get("entities", [])): return
        for ident in self.tracker.gone() - set(ids.values()):
            self.tracker.tracks.pop(ident)
            row = self.mem.get("entities", ident)
            if row and row.get("status") != "gone": self.mem.upsert("entities", {**row, "status": "gone"})
    def _promote(self, ids):
        for ident in ids:
            row = self.mem.get("entities", ident)
            if row and row.get("place") and (self.mem.get("places", row["place"]) or {}).get("state") == "merged": continue
            is_exit = bool(row and (normalize_kind(row.get("kind")) == "exit" or str(row.get("name", "")).casefold().startswith("exit:")))
            if is_exit and row.get("place") and self._failed_exit(row, row["place"]):
                if row.get("tier") != "landmark": self.mem.upsert("entities", {**row, "tier": "landmark"})
            elif row and row.get("tier") != "interest": self.mem.upsert("entities", {**row, "tier": "interest"})
    def _actor(self, ident, entity):
        anchor = (self.mem.get("entities", ident) or {}).get("anchor") if ident else None
        if anchor == "actor": return True
        if anchor in ("world", "self", "screen"): return False
        return normalize_kind(entity.get("kind")) in ACTOR_KINDS
    def _tiers(self, per, place, ids):
        record = self.mem.get("places", place)
        if not record or record.get("state") == "merged": return
        for row in self.mem.rows("entities"):
            if row.get("place") == place and ("tier" not in row or "status" not in row or "misses" not in row):
                kind = normalize_kind(row.get("kind"))
                tier = "interest" if kind in ACTOR_KINDS else "transient" if kind in TRANSIENT else "landmark"
                self.mem.upsert("entities", {**row, "tier": row.get("tier", tier),
                                               "status": row.get("status", "remembered"), "misses": row.get("misses", 0)})
        text = " ".join(map(str, per.get("text", []))).casefold()
        promote = set()
        for i, entity in enumerate(per.get("entities", [])):
            ident = ids.get(i); row = self.mem.get("entities", ident) if ident else None
            if not row or row.get("place") not in (place, None): continue
            kind, name = normalize_kind(entity.get("kind")), str(entity.get("name", "")).casefold()
            is_exit = kind == "exit" or str(entity.get("name", "")).casefold().startswith("exit:")
            failed_exit = is_exit and self._failed_exit(row, place)
            named = bool(name and name in text)
            tier = "interest" if row.get("tier") == "interest" else "transient" if kind in TRANSIENT else row.get("tier") or "landmark"
            if failed_exit: tier = "landmark"
            elif self._actor(ident, entity) or is_exit or named: tier = "interest"
            if tier != row.get("tier"): self.mem.upsert("entities", {**row, "tier": tier})
        promote.update(r["id"] for r in self.mem.rows("entities")
                       if r.get("place") == place and r.get("name") and str(r["name"]).casefold() in text)
        promote.update(r.get("s") for r in self.mem.rows("relations")
                       if r.get("src") == "observed" and r.get("sup", 0) and r.get("s"))
        self._promote(i for i in promote if self.mem.get("entities", i) and self.mem.get("entities", i).get("place") == place)
    def _failed_exit(self, entity, place):
        if not entity.get("tile"): return False
        x, y = entity["tile"]; tile = self.mem.get("tiles", f"{place}@{x},{y}")
        if not tile or tile.get("floor") is not True or tile.get("exit"): return False
        relations = any(r.get("s") == entity["id"] and r.get("p") in ("leads_to", "does_nothing") and r.get("state") == "refuted" for r in self.mem.rows("relations"))
        edges = self.mem.get("places", place).get("edges", [])
        return relations or any(e.get("ok", 0) == 0 and _distance(e.get("via", ""), entity.get("name", "")) <= 1 for e in edges)
    def _notes(self, ident):
        """Evidence shown after a remembered thing: refuted 'does_nothing' (walked into it) and repeated text."""
        out = []
        for r in self.mem.rows("relations"):
            if r.get("s") != ident: continue
            if r.get("p") == "does_nothing" and r.get("state") == "refuted" and r.get("ev"): out.append(f"not working: walked into {r.get('con', 0)}×, steps {min(r['ev'])}–{max(r['ev'])}")
            elif r.get("p") == "says" and r.get("why"): out.append(r["why"])
        return "".join(f"; {n}" for n in out)
    def _view_rect(self, anchor, size):
        if not anchor or not self.tile_px: return None
        width, height = size[0] // self.tile_px, size[1] // self.tile_px
        if not width or not height: return None
        ax, ay = (round(anchor[i] * size[i] / self.tile_px - .5) for i in (0, 1))
        x0, y0 = self.pos[0] - ax, self.pos[1] - ay
        return (x0, y0, x0 + width - 1, y0 + height - 1)
    def _record_view(self, per, frame, reused, place, size, step, anchor, ids):
        live = {r["id"] for r in self.mem.rows("places") if r.get("state") != "merged"}
        rect = self._view_rect(anchor, size) if place in live else None
        world_screen = per.get("screen") == "world"
        modal = any(normalize_kind(e.get("kind")) in {"dialogue_box", "menu", "keyboard", "input_field"}
                    for e in per.get("entities", []))
        if rect and world_screen:
            x0, y0, x1, y1 = rect
            seen = self.mem.get("seen", place) or {"id": place, "cells": [], "step": step}
            cells = set(seen.get("cells", [])); added = {f"{x},{y}" for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)} - cells
            past = {"LEFT": lambda i: (x0 - 1, y0 + i), "RIGHT": lambda i: (x1 + 1, y0 + i), "UP": lambda i: (x0 + i, y0 - 1), "DOWN": lambda i: (x0 + i, y1 + 1)}
            edge = _open_edge(frame, self.tile_px) if frame is not None and not modal else {}
            was = set(seen.get("open", [])); opened = {"%d,%d" % past[d](i) for d, idx in edge.items() for i in idx} - was
            if added or opened: self.mem.upsert("seen", {"id": place, "cells": sorted(cells | added), "open": sorted(was | opened), "step": step})
        distinct = False
        if rect and world_screen and not modal and not reused:
            digest = hashlib.sha256(frame.tobytes()).hexdigest()
            distinct = self._last_miss_hash.get(place) != digest
            if distinct: self._last_miss_hash[place] = digest
        if rect:
            x0, y0, x1, y1 = rect
            mx0, my0, mx1, my1 = x0 + 1, y0 + 1, x1 - 1, y1 - 1
        matched = set(ids.values()) if place in live else set()
        for row in self.mem.rows("entities"):
            if row.get("place") not in live: continue
            ident, old_status = row["id"], row.get("status", "remembered")
            status = old_status
            tier = row.get("tier", "landmark")
            is_matched = row.get("place") == place and ident in matched
            if is_matched or status == "gone": continue
            if status == "seen": row = {**row, "status": "remembered"}; status = "remembered"
            if self._actor(ident, row):
                if step - row.get("last", step) >= ACTOR_STALE_STEPS and status != "stale":
                    self.mem.upsert("entities", {**row, "status": "stale"})
                elif old_status != status: self.mem.upsert("entities", row)
                continue
            if (row.get("place") == place and distinct and tier in ("interest", "landmark") and row.get("tile") and
                    mx0 <= row["tile"][0] <= mx1 and my0 <= row["tile"][1] <= my1):
                misses = row.get("misses", 0) + 1
                status = "gone" if misses >= MISS_TO_GONE else "stale" if misses >= MISS_TO_STALE else status
                row = {**row, "misses": misses, "status": status}
            if row.get("status") != self.mem.get("entities", ident).get("status") or row.get("misses") != self.mem.get("entities", ident).get("misses"):
                self.mem.upsert("entities", row)
    def prepare(self, skill, place, scene_cut, step):
        if skill is self._travelled:  # travel already folded pos up to the exit tile; an open cut still crosses from there
            moves = skill.moves; cut_dir = (moves[-1].get("dir") if isinstance(moves[-1], dict) else moves[-1][0]) if self._travel_open and moves else None
            self._prepared = (skill, place, step, cut_dir, self.pos); return {"cut_dir": cut_dir, "exit_key": (*self.pos, cut_dir) if cut_dir and self.tile_px else None}
        moves = getattr(skill, "moves", []); move_rows = moves[:-1] if scene_cut and moves else moves
        at = self.pos
        for move in move_rows:
            direction, shift = (move.get("dir"), move.get("shift")) if isinstance(move, dict) else move
            if direction not in DIRS: continue
            if shift in ((0, 0), 0):
                target = at[0] + DIRS[direction][0], at[1] + DIRS[direction][1]
                self._promote(i for i, tile in self.entity_tiles.items() if tile == target)
            elif self.tile_px and isinstance(shift, (tuple, int)):
                dx, dy = ((round(-shift / self.tile_px), 0) if direction in ("LEFT", "RIGHT") else (0, round(-shift / self.tile_px))) if isinstance(shift, int) else (round(-v / self.tile_px) for v in shift)
                n = max(abs(dx), abs(dy))
                if n: at = (at[0] + ((dx > 0) - (dx < 0)) * n, at[1] + ((dy > 0) - (dy < 0)) * n)
        if self.tile_px: self.pos = fold(self.mem, place, self.pos, move_rows, self.tile_px, step, actors=self.actor_tiles)
        cut_dir = (moves[-1].get("dir") if isinstance(moves[-1], dict) else moves[-1][0]) if scene_cut and moves else None
        self._prepared = (skill, place, step, cut_dir, self.pos)
        return {"cut_dir": cut_dir, "exit_key": (*self.pos, cut_dir) if cut_dir and self.tile_px else None}

    def probe(self, ctx):
        place = self.current_place
        result = {"presses": 0, "locked": self._self_from_motion}
        if (not place or self.tile_px is None or self._self_from_motion): return result
        step = getattr(ctx, "step", 0)
        tried = set()
        for _ in range(4):
            tile = self.mem.get("tiles", f"{place}@{self.pos[0]},{self.pos[1]}") or {}
            direction = next((d for d in DIRS if d not in tile.get("blocked", []) and d not in tried), None)
            if direction is None: break
            tried.add(direction)
            skill = press(ctx, direction)
            scene_cut = skill.blank_seen or cut(skill.before, skill.after)
            self.prepare(skill, place, scene_cut, step)
            result.update(presses=result["presses"] + skill.inputs, skill=skill, scene_cut=scene_cut)
            if not scene_cut: self.after(skill, place, place, False, step)
            move = skill.moves[-1] if skill.moves else {}
            spot, shift = move.get("self_at"), move.get("shift")
            if (spot is not None and isinstance(shift, tuple) and max(map(abs, shift)) >= self.tile_px):
                self.self_spot, self._self_contradictions, self._self_from_motion = list(spot), 0, True
            result["locked"] = self._self_from_motion
            if scene_cut or skill.status != "done" or result["locked"]: break
        return result

    def here_flag(self, fails=None):
        tile = self.mem.get("tiles", f"{self.current_place}@{self.pos[0]},{self.pos[1]}") or {}
        blocked = set(tile.get("blocked", []))
        open_dirs = {d for d, (dx, dy) in DIRS.items()
                     if (neighbor := self.mem.get("tiles", f"{self.current_place}@{self.pos[0] + dx},{self.pos[1] + dy}"))
                     and neighbor.get("floor") is True}
        opened = open_dirs - blocked
        failed = sorted(((k, f) for k, f in (fails or {}).items()
                         if k.startswith(f"{self.current_place}|") and str(f.get("act", "")).startswith("buttons ")),
                        key=lambda kf: kf[1].get("step", 0), reverse=True)
        failed_keys = {k for k, _ in failed}
        untried = {d for d in set(DIRS) - blocked - opened if fail_key(self.current_place, "buttons", d) not in failed_keys}
        parts = []
        for label, dirs in (("blocked", blocked), ("open", opened), ("untried", untried)):
            parts.append(f"{label} {', '.join(d for d in DIRS if d in dirs) or 'none'}")
        if failed:
            parts.append("failed here: " + ", ".join(f"{f['act'][len('buttons '):]} x{f['n']}" for _, f in failed[:4]))
        parts.append("self locked" if self._self_from_motion else "self unlocked")
        return "here: " + "; ".join(parts)
    def after(self, skill, place, next_place, scene_cut, step):  # call after prepare() for the same step
        if skill is self._travelled and not self._travel_open:
            self._cut_arrival = None; self.current_place = next_place
            return {"known_exit": None, "scroll": list(skill.scroll) if skill.scroll is not None else None}
        cut_dir = self._prepared[3]
        known_exit = exit_to(self.mem, place, self.pos, cut_dir) if scene_cut and cut_dir else None
        self._cut_arrival = (step, next_place, place) if scene_cut and next_place != place else None
        if scene_cut and next_place != place:
            arrive = self._seen_at(next_place) or (tuple(known_exit["arrive"]) if known_exit else None)
            if cut_dir: self._cross(place, self._prepared[4], next_place, arrive, step)  # a crossing takes a press in a direction
            self.pos = arrive or (0, 0)
            self.drift = self.drift_ref = None
            self.entity_tiles = self.entity_ids = {}
        return {"known_exit": known_exit,
                "scroll": list(skill.scroll) if skill.scroll is not None else None}
    def _cross(self, src, tile, dst, arrive, step):
        """Count one crossing under the exit it went through, on both sides: (src, tile, dst) and (dst, arrive, src). A side whose tile is
        unknown is found as the other end of the known one (_partner); if none, only the known side counts.
        ponytail: a place change seen only by the next observation counts in unseen_cross, and only when its exit tile is within 1 of pos."""
        tile, arrive = (tuple(tile) if tile else None), (tuple(arrive) if arrive else None)
        if tile and not arrive: arrive = self._partner(dst, src, tile)
        if arrive and not tile: tile = self._partner(src, dst, arrive)
        if tile and not arrive: self._lone = (step, dst, src)  # the exit row for the arrival side is written by mark_exit, after this
        for place, at, other in ((src, tile, dst), (dst, arrive, src)):
            if at: self._count(place, at, other, step)
    def _count(self, place, at, other, step):  # one crossing counted on one side of an exit
        key = _cross_key(place, at, other)
        row = self.mem.get("crossings", key) or {"id": key, "n": 0}
        self.mem.upsert("crossings", {**row, "n": row["n"] + 1, "last": step})
    def _partner(self, place, to, at):  # the tile of place whose exit leads to `to` and arrives on `at`: the other end of one exit, or None
        hits = [(r["x"], r["y"]) for r in self.mem.rows("tiles") if r.get("place") == place and (r.get("exit") or {}).get("to") == to
                and tuple((r.get("exit") or {}).get("arrive") or ()) == at]
        return hits[0] if len(hits) == 1 else None
    def unseen_cross(self, src, dst, step):  # a place change first seen at the next observation: pos is still in src, so its exit tile is the one within 1
        hits = [r for r in self.mem.rows("tiles") if r.get("place") == src and (r.get("exit") or {}).get("to") == dst and max(abs(r["x"] - self.pos[0]), abs(r["y"] - self.pos[1])) <= 1]
        if len(hits) == 1: self._cross(src, (hits[0]["x"], hits[0]["y"]), dst, hits[0]["exit"].get("arrive"), step)
        # ponytail: no exit tile within 1 of pos (or two): the crossing is not counted
    def _seen_at(self, place):
        hit = getattr(self.mem, "arrival", None)
        return tuple(hit[1]) if hit and hit[0] == place else None
    def mark_exit(self, place, direction, to, arrive, step):
        mark_exit(self.mem, place, self._prepared[4], direction, to, arrive, step)
        if self._lone == (step, to, place):  # the arrival side of this step's crossing: now that its exit row exists, count it
            self._count(to, tuple(arrive), place, step); self._lone = None
    def _box(self, image, centre):
        t = self.tile_px; w, h = image.size
        x0, y0 = (min(max(round(centre[i] - t / 2), 0), max(n - t, 0)) for i, n in ((0, w), (1, h)))
        return (x0, y0, x0 + t, y0 + t)
    def _screen_things(self, per, frame, place, size, step):
        for i, e in enumerate(per.get("entities", [])):
            ident = self.entity_ids.get(i); row = self.mem.get("entities", ident) if ident else None
            kind = normalize_kind(e.get("kind"))
            if not row or kind == "player": continue
            if kind in TRANSIENT:
                # ponytail: transient kinds are screen-anchored unless motion already decided them world/actor/self.
                anchor = row.get("anchor") if row.get("anchor") in ("world", "actor", "self") else "screen"
                if (row.get("anchor"), row.get("transient")) != (anchor, True): self.mem.upsert("entities", {**row, "anchor": anchor, "transient": True})
            elif isinstance(e.get("at"), list) and len(e["at"]) == 2:
                key = (str(e.get("name", "")).casefold(), kind, tuple(round(v, 2) for v in e["at"]))
                places = self._persist.setdefault(key, set()); places.add(place)
                if len(places) >= 2 and row.get("anchor") not in ("self", "screen"): self.mem.upsert("entities", {**row, "anchor": "screen"})
            if not self.tile_px or per.get("screen") != "world" or kind in TRANSIENT or not isinstance(e.get("at"), list) or len(e["at"]) != 2: continue
            row = self.mem.get("entities", ident)
            if row.get("anchor") != "screen": continue
            box = self._box(frame, [e["at"][k] * size[k] for k in (0, 1)])
            if row.get("region") != list(box): self.mem.upsert("entities", {**row, "region": list(box)})
            hud = self._hud.setdefault(ident, {"crop": None, "changed": None}); crop = frame.crop(box)
            if hud["crop"] is not None and changed(hud["crop"], crop) > .05: hud["changed"] = step
            hud["crop"] = crop
    def observe_after(self, per, frame, reused, place, size, step, before=None, skill=None):
        for move in getattr(skill, "moves", []):
            if not isinstance(move, dict): continue
            shift, spot = move.get("shift"), move.get("self_at")
            if not self.tile_px or not isinstance(shift, tuple) or max(map(abs, shift)) < self.tile_px: continue
            if spot is None:
                self._self_contradictions = 0
            elif self.self_spot is None:
                self.self_spot, self._self_contradictions, self._self_from_motion = list(spot), 0, True
            elif not self._self_from_motion:
                self.self_spot, self._self_contradictions, self._self_from_motion = list(spot), 0, True
            elif max(abs(spot[i] - self.self_spot[i]) * size[i] for i in (0, 1)) > self.tile_px:
                self._self_contradictions += 1
                if self._self_contradictions >= 2: self.self_spot, self._self_contradictions, self._self_from_motion = None, 0, False
            else:
                self._self_contradictions = 0
        player = next((e for e in per.get("entities", []) if e.get("kind", "").casefold() == "player"), None)
        aa = (self.self_spot if self._self_from_motion else self._anchor(per, size)) if player else None
        tiles = {i: tile_of(e["at"], aa, self.pos, size, self.tile_px) for i, e in enumerate(per.get("entities", []))
                 if aa and e.get("kind", "").casefold() != "player" and isinstance(e.get("at"), list) and self.tile_px}
        drift_fix = None
        if not reused and place and self.tile_px:
            old = {(e.get("name", "").casefold(), e.get("kind")): self.entity_ids[i] for i, e in enumerate(self.entities)
                   if i in self.entity_ids and sum((x.get("name", "").casefold(), x.get("kind")) == (e.get("name", "").casefold(), e.get("kind")) for x in self.entities) == 1}
            new = {(e.get("name", "").casefold(), e.get("kind")): i for i, e in enumerate(per.get("entities", [])) if i in tiles and sum((x.get("name", "").casefold(), x.get("kind")) == (e.get("name", "").casefold(), e.get("kind")) for x in per.get("entities", [])) == 1}
            walked = self.tracker.moved()  # things that moved on their own are not fixed references
            pairs = {old[k]: new[k] for k in old.keys() & new.keys() if old[k] in self.entity_tiles and old[k] not in walked}; anchor = self.drift_ref or {i: self.entity_tiles[i] for i in pairs}
            delta = [(tuple(anchor[i][j] - tiles[n][j] for j in (0, 1)), i) for i, n in pairs.items() if i in anchor]
            if delta:
                dx, dy = (sorted(v[0][j] for v in delta)[len(delta) // 2] for j in (0, 1)); drift = (dx, dy) if (dx, dy) != (0, 0) else None
                at = self.mem.seen_view(frame, None, None, self.tile_px, only=place) if drift and drift == self.drift and frame is not None else None
                if at and tuple(at[1]) != (self.pos[0] + dx, self.pos[1] + dy): self.drift = self.drift_ref = None  # stored views say where we are: entity drift was noise
                elif drift and drift == self.drift: self.pos = (self.pos[0] + dx, self.pos[1] + dy); drift_fix = (dx, dy); tiles = {i: (p[0] + dx, p[1] + dy) for i, p in tiles.items()}; self.drift = self.drift_ref = None
                else: self.drift, self.drift_ref = drift, anchor if drift else None
        if not reused:
            self.entity_ids = self.mem.observe(per, place, step, tiles, self._hits(per))
            self._observed_per = per; self._follow(per, self.entity_ids)
        self._screen_things(per, frame, place, size, step)
        self._tiers(per, place, self.entity_ids)
        self._record_view(per, frame, reused, place, size, step, aa, self.entity_ids)
        self.entity_tiles = {self.entity_ids[i]: p for i, p in tiles.items() if i in self.entity_ids}; self._travelled = None
        return drift_fix

    def state(self):
        return {"pos": self.pos, "drift": self.drift, "drift_ref": self.drift_ref, "entity_tiles": self.entity_tiles,
                "entity_ids": self.entity_ids, "anchors": self.anchors,
                "last_miss_hash": self._last_miss_hash,
                "self_spot": self.self_spot, "self_contradictions": self._self_contradictions,
                "self_from_motion": self._self_from_motion,
                "hud_changed": {k: v["changed"] for k, v in self._hud.items() if v["changed"] is not None}}

    def restore(self, state):
        self.pos, self.drift = tuple(state.get("pos", (0, 0))), tuple(state["drift"]) if state.get("drift") else None
        self.drift_ref, self.entity_tiles = state.get("drift_ref"), state.get("entity_tiles", {})
        self.entity_ids, self.anchors = {int(i): v for i, v in state.get("entity_ids", {}).items()}, state.get("anchors", [])
        self._last_miss_hash = state.get("last_miss_hash", {})
        self.self_spot = state.get("self_spot")
        self._self_contradictions = int(state.get("self_contradictions", 0))
        self._self_from_motion = bool(state.get("self_from_motion", False))
        self._hud = {k: {"crop": None, "changed": v} for k, v in state.get("hud_changed", {}).items()}
    def _paths(self, start, avoid=()):  # one path per destination, through _exits' representative door of each exit: the same door the options show
        live = {p["id"] for p in self.mem.rows("places") if p.get("state") != "merged"}
        paths, queue = {start: []}, [start]
        for p in queue:
            for rec in (self._exits(p, live) if p in live else ()):
                if rec["doors"] and rec["to"] not in paths and rec["to"] not in avoid: paths[rec["to"]] = [*paths[p], (p, rec)]; queue.append(rec["to"])
        return paths
    def beyond(self, place, screen=()):
        """What lies beyond each verified exit of place: for every place reachable through it (never back through place), its unused
        (unverified) exits and unexplored edges (untried edges into unseen cells), counted per place then summed. dead_end: place has no unexplored edge or unverified exit and
        exactly one verified exit leads to anything unexplored (through: that exit's tile). Pure: writes nothing, asks no model."""
        live = {p["id"] for p in self.mem.rows("places") if p.get("state") != "merged"}
        def here(pid, exits): return sum(x["status"] == "unverified" for x in exits), (len(unseen_edges(self.mem, pid)) + len(open_cells(self.mem, pid)) if self.tile_px else 0)
        own = self._exits(place, live, screen)
        own_unused, own_unexplored = here(place, own)
        exits = {}
        for x in own:
            if x["status"] != "verified" or not x["dest"] or x["dest"] == place or not x["tile"]: continue
            unused = unexplored = 0; labels = []
            for pid in self._paths(x["dest"], avoid={place}):
                u, e = here(pid, self._exits(pid, live)); unused += u; unexplored += e; labels.append(self.place_label(pid))
            exits[tuple(x["tile"])] = {"unused_exits": unused, "unexplored": unexplored, "places": labels}
        beyond = [t for t, v in exits.items() if v["unused_exits"] + v["unexplored"] > 0]
        dead_end = not own_unused and not own_unexplored and len(beyond) == 1
        return {"exits": exits, "dead_end": dead_end, "through": beyond[0] if dead_end else None}
    def resolve(self, name, per, place):
        requested = re.sub(r"\s*×\d+$", "", str(name or "").strip())  # a grouped label ("vent ×3") resolves like its name
        if not requested: return None
        def matches(entity):
            return _distance(entity.get("name", ""), name) <= 1 or str(entity.get("called", "")).casefold() == name.casefold()
        def match_distance(entity):
            return min(_distance(entity.get("name", ""), name), _distance(entity.get("called", ""), name))
        places = [p for p in self.mem.rows("places") if p.get("state") != "merged"]
        live = {p["id"] for p in places}
        exact = next((p for p in places if requested == p["id"] or requested.casefold() == self.place_label(p["id"]).casefold()), None)
        if exact:
            incoming = [r for r in self.mem.rows("tiles") if r.get("place") in live and (r.get("exit") or {}).get("to") == exact["id"]]
            tile = (incoming[0].get("exit") or {}).get("arrive") if incoming else next(
                ((r["x"], r["y"]) for r in self.mem.rows("tiles") if r.get("place") == exact["id"] and r.get("floor") is True), None)
            return {"kind": "place", "place": exact["id"], "tile": tuple(tile) if tile else None, "id": exact["id"], "stale": False}
        name = requested.removeprefix("exit:").strip()
        dist = {p: len(path) for p, path in self._paths(place).items()}
        screen = [(i, e) for i, e in enumerate(per.get("entities", [])) if e.get("name") and matches({**e, "called": (self.mem.get("entities", self.entity_ids.get(i, "")) or {}).get("called", "")})]
        if screen: i, e = min(screen, key=lambda pair: (match_distance({**pair[1], "called": (self.mem.get("entities", self.entity_ids.get(pair[0], "")) or {}).get("called", "")}), -(self.mem.get("entities", self.entity_ids.get(pair[0], "")) or {}).get("seen", 0))); ident = self.entity_ids.get(i); return {"kind": "screen", "place": place, "tile": self.entity_tiles.get(ident), "id": ident, "stale": False}
        rows = [e for e in self.mem.rows("entities") if e.get("place") in live and e.get("tile") and matches(e)]
        if rows: e = min(rows, key=lambda e: (str(e.get("called", "")).casefold() != name.casefold(), e["place"] != place, dist.get(e["place"], 10**9), -e.get("seen", 0))); return {"kind": "memory", "place": e["place"], "tile": tuple(e["tile"]), "id": e["id"], "stale": e.get("status") == "stale"}
        rows = [p for p in self.mem.rows("places") if p.get("id") in live and any(_distance(v, name) <= 1 for v in [p.get("name", ""), *p.get("aliases", [])])]
        if not rows: return None
        p = min(rows, key=lambda p: (p["id"] != place, dist.get(p["id"], 10**9), -p.get("visits", 0))); incoming = [r for r in self.mem.rows("tiles") if r.get("place") in live and (r.get("exit") or {}).get("to") == p["id"]]
        tile = (incoming[0].get("exit") or {}).get("arrive") if incoming else next(((r["x"], r["y"]) for r in self.mem.rows("tiles") if r.get("place") == p["id"] and r.get("floor") is True), None)
        return {"kind": "place", "place": p["id"], "tile": tuple(tile) if tile else None, "id": p["id"], "stale": False}

    def place_label(self, place):
        rows = [p for p in self.mem.rows("places") if p.get("state") != "merged"]
        row = self.mem.get("places", place)
        if not row: return place
        name = place_name(row)
        if not name:  # ponytail: unnamed reads "place <n>", n = the id's numeric suffix (minted ids: place, place_2, ...)
            n = place.rsplit("_", 1)[-1]
            return f"place {n if n.isdigit() else 1}"
        duplicates = sum(place_name(p).casefold() == name.casefold() for p in rows) > 1
        return f"{name} [{place}]" if duplicates else name

    def _exits(self, pid, live, screen=()):
        """Exit records of place pid: the one source for World.options and the view. A record is a tile exit to a live place, or a thing
        named as an exit (remembered, or on screen as (index, entity, id)). Records to the same destination with tiles within 1 are one exit, and so is
        an unverified record with no destination within 1 of a verified one: the best record (crossed or observed, then a tile exit) gives its name. The representative door (crossed or observed before a guess, then row order) gives tile, dir, arrive, src; doors lists every tile exit of the group. Status: verified when crossed in either direction
        (counted crossings, a confirmed leads_to, or an observed exit arriving on its tile), refuted by the _failed_exit rule, else unverified.
        uses/last count every crossing through the exit's tiles, both directions. Sorted by tile row and column."""
        things = {tuple(e["tile"]): e for e in self.mem.rows("entities") if e.get("place") == pid and e.get("tile") and e.get("name")
                  and e.get("status", "remembered") != "gone" and _is_exit(e)}
        loose = []
        for i, e, ident in screen:  # on screen now: perception's name and index, the tile the world knows for it
            tile = self._tile(ident)
            row = {**(self.mem.get("entities", ident) or {}), "id": ident, "name": e["name"], "idx": i, "tile": tile}
            if tile: things[tile] = row
            else: loose.append(row)
        relations, tiles = self.mem.rows("relations"), self.mem.rows("tiles")
        def leads(ident): return sorted((x for x in relations if ident and x.get("s") == ident and x.get("p") == "leads_to" and x.get("o") in live),
                                        key=lambda x: (x.get("state") != "live", -x.get("sup", 0)))
        arrivals = {(r.get("place"), tuple(r["exit"]["arrive"])) for r in tiles if (r.get("exit") or {}).get("to") == pid and r["exit"].get("src") == "observed" and r["exit"].get("arrive")}
        recs = []
        for r in tiles:
            edge = r.get("exit") or {}
            if r.get("place") == pid and edge.get("to") in live and edge["to"] != pid:
                tile = (r["x"], r["y"])
                recs.append({"tile": tile, "dir": edge.get("dir") or "", "dest": edge["to"], "pushed": True, "observed": edge.get("src") == "observed", "row": things.get(tile) or {},
                             "arrive": edge.get("arrive"), "src": edge.get("src")})
        covered = {rec["tile"] for rec in recs}
        for row in [*(e for t, e in things.items() if t not in covered), *loose]:
            known = leads(row.get("id"))
            recs.append({"tile": tuple(row["tile"]) if row.get("tile") else None, "dir": "", "dest": known[0]["o"] if known else None, "pushed": False, "observed": False, "row": row})
        edges = (self.mem.get("places", pid) or {}).get("edges", [])
        for rec in recs:
            row, dest, tile = rec["row"], rec["dest"], rec["tile"]
            rec.update(name=row.get("name"), ident=row.get("id"), idx=row.get("idx"))
            c = (self.mem.get("crossings", _cross_key(pid, tile, dest)) or {}) if tile and dest else {}
            rec.update(n=c.get("n", 0), last=c.get("last"), sup=[x for x in leads(rec["ident"]) if x["o"] == dest] if dest else [],
                       arrived=bool(tile and dest and (dest, tile) in arrivals))
            rec["alone"] = bool(rec["n"] or rec["sup"] or rec["arrived"] or rec["observed"])
        groups = []  # ponytail: a twin is found by destination and Chebyshev distance 1 against the group's best record only
        for rec in sorted(recs, key=lambda r: (not r["alone"], not r["observed"], not r["pushed"], _yx(r["tile"], r["name"] or r["dir"]))):
            twin = next((g for g in groups if (rec["dest"] and g[0]["dest"] == rec["dest"] or g[0]["alone"] and not rec["alone"] and not rec["dest"]) and rec["tile"] and g[0]["tile"]
                         and max(abs(g[0]["tile"][0] - rec["tile"][0]), abs(g[0]["tile"][1] - rec["tile"][1])) <= 1), None)  # an unverified thing with no destination next to a verified exit is that exit; a known other destination stays its own exit
            if twin is None: groups.append([rec])
            else: twin.append(rec)
        out = []
        for g in groups:
            best, dest = g[0], g[0]["dest"]
            doors = [m for m in g if m["pushed"]]  # tile exits of this exit, in preference order: crossed or observed before a guess, then row order
            door = doors[0] if doors else best  # the representative door: what display, _paths and plan all use
            sup = [x for m in g for x in m["sup"]]
            steps = [m["last"] for m in g if m["n"] and m["last"] is not None] + [x["step"] for x in sup if x.get("step") is not None]
            uses = max(sum(m["n"] for m in g), sum(x.get("sup", 0) for x in sup), 1 if any(m["observed"] or m["arrived"] for m in g) else 0)
            name = best["name"] or next((m["name"] for m in g if m["name"]), None); dir_ = door["dir"]
            head = (name[5:].strip() if name.casefold().startswith("exit:") else name) if name else dir_
            refuted = not uses and any(self._failed_exit(m["row"], pid) for m in g if m["row"].get("tile"))
            if uses: label = f"exit {head} → {self.place_label(dest)} (used {uses}×" + (f", last step {max(steps)}" if steps else "") + ")"
            elif refuted:
                idents = {m["ident"] for m in g if m["ident"]}
                walks = [r for r in relations if r.get("s") in idents and r.get("p") == "does_nothing" and r.get("state") == "refuted"]
                ev = [s for r in walks for s in (r.get("ev") or [])]
                count = max([r.get("con", 0) for r in walks] + [sum(e.get("n", 0) for e in edges if e.get("ok", 0) == 0 and _distance(e.get("via", ""), name or "") <= 1)])
                label = f"not an exit: {head} (walked into {count}×" + (f", steps {min(ev)}–{max(ev)}" if ev else "") + ")"
            else: label = f"possible exit {head} (unverified, never crossed)"
            status = "verified" if uses else "refuted" if refuted else "unverified"
            out.append({"tile": door["tile"], "dir": dir_, "dest": dest, "to": dest, "place": pid, "arrive": door.get("arrive"), "src": door.get("src"),
                        "doors": [{"tile": m["tile"], "dir": m["dir"], "to": dest, "arrive": m.get("arrive"), "src": m.get("src")} for m in doors],
                        "name": name, "head": head, "idx": next((m["idx"] for m in g if m["idx"] is not None), None),
                        "ident": next((m["ident"] for m in g if m["ident"]), None), "pushed": bool(doors), "uses": uses, "last": max(steps, default=None),
                        "status": status, "label": label})
        return sorted(out, key=lambda x: _yx(x["tile"], x["name"] or x["dir"]))

    def view(self, goal_text, place, compact=False):  # compact: the ACT prompt's facts, without what its options already say
        live ={p["id"] for p in self.mem.rows("places") if p.get("state") != "merged" and p.get("screen") == "world"}  # navigable places only: the run-start place never lists
        populated = {r.get("place") for r in self.mem.rows("tiles")} | {e.get("place") for e in self.mem.rows("entities")}
        places = [p for p in self.mem.rows("places") if p.get("id") in live and (p.get("edges") or p.get("visits") or p["id"] in populated)]
        def exit_text(x, far=None):  # by _exits status: verified "head -> place used N×" + what lies beyond; refuted: its label; else a guess
            if x["status"] == "refuted": return x["label"]
            to = f" -> {self.place_label(x['dest'])}" if x["dest"] else ""
            if x["status"] == "verified": return f"{x['head']}{to} used {x['uses']}×{_beyond_suffix(far)}"
            return f"{x['head']}{to}: possible exit, unverified (never crossed)"
        goal = str(goal_text or "").casefold()
        base_name = _base
        promoted = [e["id"] for e in self.mem.rows("entities") if e.get("place") in live and e.get("name")
                    and _base(e["name"]) in goal]
        self._promote(promoted)

        label = self.place_label(place)
        tier = lambda e: e.get("tier", "interest" if normalize_kind(e.get("kind")) in ACTOR_KINDS else "transient" if normalize_kind(e.get("kind")) in TRANSIENT else "landmark")
        tiles = [r for r in self.mem.rows("tiles") if r.get("place") == place]
        seen = self.mem.get("seen", place) or {"cells": []}
        seen_cells = set(seen.get("cells", []))
        edges = frontier(self.mem, place)
        unseen, past = len(unseen_edges(self.mem, place)), len(open_cells(self.mem, place))
        walked = sum(r.get("floor") is True for r in tiles)
        lines = [f"Here: {label} ({self.pos[0]},{self.pos[1]})  map: {walked} walked, {len(seen_cells)} seen, {len(edges)} untried edges ({unseen} into unseen)" + (f", {past} unseen cells past open screen edges" if past else "")]

        x0, y0 = self.pos[0] - MAP_W // 2, self.pos[1] - MAP_H // 2
        x1, y1 = x0 + MAP_W - 1, y0 + MAP_H - 1
        cells = {(r["x"], r["y"]): r for r in tiles}
        eligible = lambda e: e.get("status", "remembered") != "gone" and not (
            e.get("src") == "model" and not e.get("tile") and not e.get("seen", 0))
        current_entities = [e for e in self.mem.rows("entities") if e.get("place") == place
                            and tier(e) == "interest" and eligible(e)]
        entities = [e for e in self.mem.rows("entities") if e.get("place") == place and e.get("tile")
                    and eligible(e) and tier(e) in ("interest", "landmark")]
        landmarks = sorted((e for e in entities if tier(e) == "landmark"), key=lambda e: (-e.get("seen", 0), e["id"]))[:LANDMARKS_PER_PLACE]
        entities = [e for e in entities if tier(e) == "interest"] + landmarks
        entities = [e for e in entities if not ((normalize_kind(e.get("kind")) == "exit" or str(e.get("name", "")).casefold().startswith("exit:")) and self._failed_exit(e, place))]
        grouped = {}
        for e in entities:
            grouped.setdefault(base_name(e.get("name", e["id"])), []).append(e)
        current_groups = {}
        for e in current_entities:
            current_groups.setdefault(base_name(e.get("name", e["id"])), []).append(e)
        status_rank = {"seen": 3, "remembered": 2, "stale": 1}
        entities = []
        for base, group in sorted(grouped.items()):
            members = group + [e for e in current_groups.get(base, ()) if e["id"] not in {r["id"] for r in group}]
            nearest = min(group, key=lambda e: (sum(abs(e["tile"][i] - self.pos[i]) for i in (0, 1)), e["id"]))
            best = max(members, key=lambda e: (status_rank.get(e.get("status", "remembered"), 0), e.get("last", 0), e["id"]))
            entities.append({**nearest, "name": base if len(members) > 1 else nearest.get("name", nearest["id"]),
                             "status": best.get("status", "remembered"), "last": best.get("last", 0),
                             "_members": members})
        entities.sort(key=lambda e: (tier(e) != "interest", e["id"]))
        exit_rows = self._exits(place, live)
        def owner(e):  # the _exits record an exit-kind thing belongs to: same ident, else its tile within Chebyshev 1
            if not _is_exit(e): return None
            return next((x for x in exit_rows if x["ident"] == e["id"] or x["tile"] and e.get("tile")
                         and max(abs(x["tile"][0] - e["tile"][0]), abs(x["tile"][1] - e["tile"][1])) <= 1), None)
        symbols = "ABCDFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
        marks, legend, shown = {}, [], set()
        mapped = set()
        for i, e in enumerate(e for e in entities if x0 <= e["tile"][0] <= x1 and y0 <= e["tile"][1] <= y1 and tuple(e["tile"]) not in marks):
            if i == len(symbols): break
            mapped.update(member["id"] for member in e["_members"])
            rec = owner(e)
            if rec and id(rec) in shown: continue  # one exit, one legend line: its other things are not listed again
            if rec: shown.add(id(rec))
            symbol = symbols[i]; marks[tuple(e["tile"])] = symbol
            status = e.get("status", "remembered")
            last = f"last seen {tuple(e['tile'])} step {e.get('last', 0)}; may have moved" if status == "stale" else f"{status} step {e.get('last', 0)}"
            count = len(e["_members"])
            called = next((member.get("called") for member in e["_members"] if member.get("called")), None)
            name = rec["head"] if rec else f"{called or e['name']} ×{count}" if count > 1 else called or e.get("name", e["id"])
            nearest = f" (nearest {tuple(e['tile'])})" if count > 1 else ""
            legend.append(f"{'Thing' if tier(e) == 'interest' else 'Landmark'} {symbol} {name} {tuple(e['tile'])} {normalize_kind(e.get('kind'))} {last}{nearest}{self._notes(e['id'])}")
        for y in range(y0, y1 + 1):
            row = []
            for x in range(x0, x1 + 1):
                tile = cells.get((x, y), {})
                glyph = "@" if (x, y) == tuple(self.pos) else "E" if tile.get("exit") else None if compact else marks.get((x, y))
                if glyph is None:
                    for d, (dx, dy) in DIRS.items():
                        source = cells.get((x - dx, y - dy), {})
                        if d in source.get("blocked", []):
                            glyph = "#"; break
                if glyph is None: glyph = "." if tile.get("floor") is True else "," if f"{x},{y}" in seen_cells else " "
                row.append(glyph)
            lines.append("".join(row).rstrip() if compact else "".join(row))
        lines.append("Map: . walked, , seen, # blocked side, E exit already used, @ you" + ("" if compact else "; letters mark things and landmarks (kind exit = seen, not yet used)"))
        far = self.beyond(place)  # ponytail: no screen things here; view's live set is navigable places, so a through exit missing from the table prints nothing
        if compact:  # the act prompt needs where this place's exits are from you, not what the options say
            status_word = {"verified": "used", "unverified": "possible, never crossed", "refuted": "not an exit"}
            def away(tile):  # "2 left, 1 up"; screen y grows down, "on it" when standing there
                dx, dy = tile[0] - self.pos[0], tile[1] - self.pos[1]
                parts = ([f"{abs(dx)} {'left' if dx < 0 else 'right'}"] if dx else []) + ([f"{abs(dy)} {'up' if dy < 0 else 'down'}"] if dy else [])
                return ", ".join(parts) or "on it"
            for e in exit_rows:
                if e["tile"]: lines.append(f"E at ({e['tile'][0]},{e['tile'][1]}) {e['head']}: {status_word[e['status']]}, {away(e['tile'])}")
        if not compact:  # the options already name this place's exits and the dead end
            for e in exit_rows:  # ponytail: beyond suffix on this place's exits only; other places' exits show status alone (each beyond() walks the map)
                lines.append(f"E at ({e['tile'][0]},{e['tile'][1]}) {exit_text(e, far['exits'].get(e['tile']))}")
            through = [e["head"] for e in exit_rows if e["tile"] == far["through"]] if far["dead_end"] else []
            if through: lines.append(f"Everything unexplored from here is through: {through[0]}")
        here = {e["id"] for e in self.mem.rows("entities") if e.get("place") == place} | set(self.entity_ids.values())
        refuted = {}  # most recent first; a thing's first 'not working' note only
        for r in sorted((r for r in self.mem.rows("relations") if r.get("p") == "does_nothing" and r.get("state") == "refuted" and r.get("s") in here), key=lambda r: -r.get("step", 0)):
            note = [n for n in self._notes(r["s"]).split("; ") if n.startswith("not working")]
            if note: refuted.setdefault(r["s"], note[0])
        if refuted and not compact: lines.append("Don't believe: " + "; ".join(f"{(self.mem.get('entities', s) or {}).get('name', s)} ({n})" for s, n in list(refuted.items())[:2]))
        current_detail = []
        for base, group in sorted(current_groups.items()):
            if any(e["id"] in mapped for e in group): continue
            best = max(group, key=lambda e: (status_rank.get(e.get("status", "remembered"), 0), e.get("last", 0), e["id"]))
            tile_rows = [e for e in group if e.get("tile")]
            nearest = min(tile_rows, key=lambda e: (sum(abs(e["tile"][i] - self.pos[i]) for i in (0, 1)), e["id"])) if tile_rows else None
            tile = tuple(nearest["tile"]) if nearest else "?"
            status = best.get("status", "remembered")
            if len(group) > 1:
                current_detail.append(f"Thing: {best.get('called') or base} ×{len(group)} {status} (nearest {tile})")
            else:
                last = f"last seen {tile} step {best.get('last', 0)}; may have moved" if status == "stale" else f"{status} step {best.get('last', 0)}"
                current_detail.append(f"Thing: {best.get('called') or best.get('name', best['id'])} {tile} {normalize_kind(best.get('kind'))} {last}{self._notes(best['id'])}")

        paths = self._paths(place)
        words = {w.strip(".,!?;:()[]{}\"'") for w in goal.split() if w.strip(".,!?;:()[]{}\"'")}
        others = sorted((p for p in places if p["id"] != place), key=lambda p: (
            len(paths.get(p["id"], ())) if p["id"] in paths else 10**9,
            -len(words & {w.strip(".,!?;:()[]{}\"'") for w in " ".join(map(str, [p.get("name", ""), *p.get("aliases", []), *(e.get("name", "") for e in self.mem.rows("entities") if e.get("place") == p["id"] and tier(e) == "interest")])).casefold().split()}),
            -p.get("last", p.get("visits", 0)), p["id"]))
        place_headers, place_things = {}, {}
        for other in others:
            pid = other["id"]
            aliases = other.get("aliases", [])
            label = self.place_label(pid)
            same = {label.casefold(), str(place_name(other) or "").casefold()}
            redundant = compact and all(str(a).casefold() in same for a in aliases)  # compact: aliases that only repeat the name say nothing
            header = [f"Place: {label}" + (f" aliases: {', '.join(map(str, aliases))}" if aliases and not redundant else "")]
            header.extend(f"  Exit {exit_text(e)}" for e in self._exits(pid, live))
            place_headers[pid] = header
            other_edges = frontier(self.mem, pid)
            other_unseen = len(unseen_edges(self.mem, pid)) + len(open_cells(self.mem, pid))
            groups = {}
            for e in self.mem.rows("entities"):
                if e.get("place") == pid and tier(e) == "interest" and eligible(e):
                    groups.setdefault(base_name(e.get("name", e["id"])), []).append(e)
            rendered = []
            for base, group in sorted(groups.items()):
                best = max(group, key=lambda e: (status_rank.get(e.get("status", "remembered"), 0), e.get("last", 0), e["id"]))
                tile_rows = [e for e in group if e.get("tile")]
                nearest = min(tile_rows, key=lambda e: (sum(abs(e["tile"][i] - self.pos[i]) for i in (0, 1)), e["id"])) if tile_rows else None
                called = next((e.get("called") for e in group if e.get("called")), None)
                if len(group) > 1:
                    rendered.append(f"  Thing: {called or base} ×{len(group)} {best.get('status', 'remembered')} (nearest {tuple(nearest['tile']) if nearest else '?'})")
                else:
                    status = best.get("status", "remembered")
                    fact = f"last seen {tuple(best['tile']) if best.get('tile') else '?'} step {best.get('last', 0)}; may have moved" if status == "stale" else status
                    rendered.append(f"  Thing: {best.get('called') or best.get('name', best['id'])} {fact}{self._notes(best['id'])}")
            place_things[pid] = [*rendered, f"  Edges: {len(other_edges)} untried ({other_unseen} into unseen)"]

        # ponytail: place summaries fit before entity detail; raise VIEW_TOKENS only if real maps outgrow it.
        output = []
        for line in lines:
            if len("\n".join((*output, line))) <= VIEW_TOKENS * 4: output.append(line)
        included = set()
        for other in others:
            pid = other["id"]
            candidate = "\n".join((*output, *place_headers[pid]))
            if len(candidate) <= VIEW_TOKENS * 4:
                output.extend(place_headers[pid]); included.add(pid)
        def add_detail_bundle(label, detail):
            if not detail or len("\n".join((*output, label, detail[0]))) > VIEW_TOKENS * 4: return
            output.extend((label, detail[0]))
            for line in detail[1:]:
                if len("\n".join((*output, line))) <= VIEW_TOKENS * 4: output.append(line)
        if not compact:  # the options already name the things; the detail bundles repeat them
            add_detail_bundle(f"Things here: {label}", [*legend, *current_detail])
            for other in others:
                pid = other["id"]
                if pid in included: add_detail_bundle(f"Things: {self.place_label(pid)}", place_things[pid])

        flags = list(getattr(self, "view_flags", ()))
        if self.merge_pending: flags.append(f"merge_pending: {self.merge_pending}")
        if self._travelled and self._travelled.status != "done": flags.append(f"travel: {self._travelled.reason}")
        for ident, hud in self._hud.items():
            row = self.mem.get("entities", ident)
            if hud["changed"] is not None and row and row.get("place") == place: flags.append(f"HUD {row['name']} changed at step {hud['changed']}")
        for line in (f"Flag: {flag}" for flag in dict.fromkeys(flags)) if not compact else ():  # compact: the act prompt's Flags: line carries them
            if len("\n".join((*output, line))) <= VIEW_TOKENS * 4: output.append(line)
        return "\n".join(output)
    def _tile(self, ident):
        tile = (self.entity_tiles.get(ident) or (self.mem.get("entities", ident) or {}).get("tile")) if ident else None
        return tuple(tile) if tile else None
    def options(self, per, place, fails, step, inputs=()):
        """What can be done now, from the situation only. Pure: writes nothing, asks no model, never removes or hides an option for its history.
        Modal screen: its choices, advance/cancel, type if a text field is shown, free buttons.
        Navigable screen, in this fixed order: exits (one row per tile, with what lies beyond); characters and things with history
        (says, does_nothing, a failure), on screen then remembered; explore; other known places (one row each, or one row for all
        when the rows so far exceed OPTION_CAP); one row naming every other thing here (look at or go to, by name); free buttons last.
        History only decides which row a thing is listed in, never whether it is offered."""
        ents, modal, fails = per.get("entities", []), per.get("screen") != "world", fails or {}
        live = {p["id"] for p in self.mem.rows("places") if p.get("state") != "merged"}
        def thing_ref(ident): return f"thing:{ident}" if ident else None
        def option(group, tool, label, action, ident=None, verb="approach", target=None, goto=None, ref=None, door=None):
            notes = []
            key = fail_key(place, verb, target, inputs, ref) if target is not None else None  # the loop's own intent key
            hit = fails.get(key) if key else None
            if hit: notes.append(_tried(hit))
            for n in (self._notes(ident) if ident else "").split("; "):
                if n: notes.append(f"refuted: {n}" if n.startswith("not working") else n.replace("says the same as before", "talked; same text as before"))
            return (group, {"tool": tool, "label": label, "action": action, "notes": notes, "intent_key": key, "exit": door}, goto)
        def free(label):  # free input has no single target; its one note names this place's failed button combos, newest first, at most 4
            hits = sorted(((f.get("step", 0), k, f) for k, f in fails.items() if k.startswith(f"{place}|") and str(f.get("act", "")).startswith("buttons ")), key=lambda t: (-t[0], t[1]))
            _, row, _ = option("free", "buttons", label, None)
            failed = ", ".join(f"{f['act'][len('buttons '):]} x{f['n']}" for _, _, f in hits[:4])
            return "free", {**row, "notes": [f"failed here: {failed}"] if failed else []}, None
        free_label = "free buttons" + (f": {', '.join(inputs)}" if inputs else "")
        if modal:
            choices = [(i, e) for i, e in enumerate(ents) if e.get("name") and e.get("selectable") is True and normalize_kind(e.get("kind")) not in FURNITURE | TYPING]
            rows = [option("choice", "select", f"select {e['name']}", {"thing": i, "verb": "select"}, self.entity_ids.get(i), "select", e["name"], ref=thing_ref(self.entity_ids.get(i)))
                    for i, e in sorted(choices, key=lambda ie: _yx(ie[1].get("at"), ie[1]["name"]))]
            rows += [option("choice", "advance", "advance", {"verb": "advance"}, None, "advance", ""), option("choice", "cancel", "cancel", {"verb": "cancel"}, None, "cancel", "")]
            if any(normalize_kind(e.get("kind")) in TYPING for e in ents): rows.append(option("choice", "type", "type text into the field", {"verb": "type"}, None, "type", ""))
            rows.append(free(free_label))
        else:
            on = set(self.entity_ids.get(i) for i in range(len(ents))) - {None}
            rows, dests = [], set()
            exit_things = [(i, e, self.entity_ids.get(i)) for i, e in enumerate(ents) if e.get("name") and _is_exit(e)]
            beyond = self.beyond(place, exit_things)
            for x in self._exits(place, live, exit_things):
                dest = x["dest"]
                if dest: dests.add(dest)
                under = x["idx"] is not None and self.size is not None and on_player(ents[x["idx"]].get("at"), next((e.get("at") for e in ents if e.get("kind", "").lower() == "player"), None), self.size, self.tile_px)  # approach's own refusal test: cross by the verified push, else nothing to offer
                if under and not (dest and x["dir"]): continue
                if x["idx"] is not None and not under: action, tool, target = {"thing": x["idx"], "verb": "approach"}, "approach", x["name"]
                elif dest: action, tool, target = {"go_to": self.place_label(dest)}, "go_to", x["name"] or self.place_label(dest)  # the verified tile + push
                elif x["name"]: action, tool, target = {"go_to": x["name"]}, "go_to", x["name"]
                else: action, tool, target = {"go_to": self.place_label(dest)}, "go_to", self.place_label(dest)
                tiles = sorted(tuple(d["tile"]) for d in x["doors"])
                rows.append(option("exit", tool, x["label"] + _beyond_suffix(beyond["exits"].get(x["tile"])), action, x["ident"], "approach", target,
                                   ref=exit_ref(x), door={"tile": list(tiles[0]), "to": dest} if tiles else None))
                if beyond["dead_end"] and x["tile"] == beyond["through"]: rows[-1][1]["notes"].insert(0, "everything unexplored from here is through this exit")
            hist = {r.get("s") for r in self.mem.rows("relations") if r.get("p") in ("says", "does_nothing") and r.get("s")}  # talked to or walked into, any state
            things, quiet = [], []  # quiet: (sort key, name) of things with no history and no actor kind; they share one row
            for i, e in enumerate(ents):
                kind, name, ident = normalize_kind(e.get("kind")), e.get("name"), self.entity_ids.get(i)
                if not name or kind in TRANSIENT or _is_exit(e): continue
                shown = (self.mem.get("entities", ident) or {}).get("called") or name
                key = _yx(self._tile(ident) or e.get("at"), name)
                row = option("thing", "approach", f"approach {shown}", {"thing": i, "verb": "approach"}, ident, "approach", name, ref=thing_ref(ident))
                if kind not in ACTOR_KINDS and not row[1]["notes"] and ident not in hist: quiet.append((key, shown)); continue
                things.append((key, row))
                if kind in ACTOR_KINDS: things.append((key, option("thing", "interact", f"interact with {shown}", {"thing": i, "verb": "interact"}, ident, "interact", name, ref=thing_ref(ident))))
            rows += [row for _, row in sorted(things, key=lambda kv: kv[0])]
            remembered = []
            for r in self.mem.rows("entities"):
                if r.get("place") != place or not r.get("tile") or not r.get("name") or r["id"] in on or normalize_kind(r.get("kind")) in TRANSIENT or _is_exit(r): continue
                tile, name = tuple(r["tile"]), r["name"]
                shown = r.get("called") or name
                row = option("remembered", "go_to", f"go to {shown} ({r.get('status', 'remembered')}, seen {step - r.get('last', step)} steps ago)", {"go_to": name}, r["id"], "approach", name, goto=name, ref=thing_ref(r["id"]))
                if normalize_kind(r.get("kind")) not in ACTOR_KINDS and not row[1]["notes"] and r["id"] not in hist:
                    if r.get("status") != "gone" and r.get("anchor") not in ("screen", "self"): quiet.append((_yx(tile, shown), shown))  # gone things and HUD/self things are not in this place's row
                    continue
                remembered.append((_yx(tile, name), row))
            rows += [row for _, row in sorted(remembered, key=lambda kv: kv[0])]
            if self.tile_px:
                untried = [(t, d) for t, d in frontier(self.mem, place) if ((self.mem.get("tiles", f"{place}@{t[0]},{t[1]}") or {}).get("exit") or {}).get("dir") != d]
                n = len(set(untried) & set(unseen_edges(self.mem, place))) + len(open_cells(self.mem, place))
                spots = "0 spots lead anywhere unseen" if not n else "1 spot leads somewhere unseen" if n == 1 else f"{n} spots lead somewhere unseen"
                if untried: rows.append(option("explore", "explore", f"walk to an unexplored part of this place ({spots})", {"go_to": "unexplored"}, None, "approach", "unexplored"))
            places = []
            for p in self.mem.rows("places"):
                pid = p["id"]
                if pid == place or pid not in live or p.get("screen") != "world" or pid in dests: continue
                label = self.place_label(pid); found = self.resolve(label, per, place)
                if found and found["kind"] == "place" and found["tile"] and self.plan(found):
                    places.append((_yx(found["tile"], label), option("place", "go_to", f"go to {label}", {"go_to": label}, None, "approach", label, goto=label, ref=f"place:{pid}")))
            places = [row for _, row in sorted(places, key=lambda kv: kv[0])]
            if places and len(rows) + len(places) + bool(quiet) > OPTION_CAP:  # ponytail: all known places share one row; exits and characters are never collapsed, so the cap is soft for them
                places = [("place", {"tool": "go_to", "label": "go to another known place: " + ", ".join(row[2] for row in places), "action": None, "notes": []}, None)]
            if quiet:
                groups = {}  # base name, case-insensitive ("vent 1", "vent#2" -> "vent"): [first sort key, base, distinct names]
                for key, name in sorted(quiet, key=lambda kv: kv[0]):
                    base = re.sub(r"(\s*#\d+|\s+\d+)$", "", name) or name
                    g = groups.setdefault(base.casefold(), [key, base, []])
                    if name not in g[2]: g[2].append(name)
                names = [g[2][0] if len(g[2]) == 1 else f"{g[1]} ×{len(g[2])}" for g in sorted(groups.values(), key=lambda g: (g[0], g[1]))]
                quiet =[("thing", {"tool": "go_to", "label": "look at or go to a thing here: " + ", ".join(names), "action": None, "notes": []}, None)]
            rows += places + quiet + [free(free_label)]
        return [{"n": n, **row[1]} for n, row in enumerate(rows, 1)]
    def occupied(self, place):  # actors hold tiles of the current place only; another place's actors are stale
        return self.actor_tiles if place == self.current_place else frozenset()
    def beside(self, place, pos, tile):  # nearest reachable free tile beside tile and the direction that faces it: (stand, facing, dirs)
        rows = {(r["x"], r["y"]): r for r in self.mem.rows("tiles") if r.get("place") == place}; occupied = self.occupied(place); near = []
        for d, (dx, dy) in DIRS.items():
            t = tile[0]-dx, tile[1]-dy
            if rows.get(t, {}).get("floor") is False: continue
            if (dirs := route(self.mem, place, pos, t, occupied=occupied)) is not None: near.append((t, d, dirs))
        return min(near, key=lambda q: (abs(q[0][0]-pos[0])+abs(q[0][1]-pos[1]), q[1])) if near else None
    def _through(self, exit, place):  # path to place whose first leg is the chosen exit's own door ({"tile", "to"}); None if no such exit or no way on
        live = {p["id"] for p in self.mem.rows("places") if p.get("state") != "merged"}
        cur, tile = self.current_place, tuple(exit["tile"])
        rec = next((r for r in self._exits(cur, live) if any(d["tile"] == tile for d in r["doors"])), None)
        rest = self._paths(rec["to"]).get(place) if rec else None
        return None if rest is None else [(cur, rec), *rest]
    def canon_place(self, pid):  # a merged place's live place: its counters and identity follow it
        seen = set()
        while (row := self.mem.get("places", pid)) and row.get("state") == "merged" and row.get("into") and pid not in seen: seen.add(pid); pid = row["into"]
        return pid
    def named_intents(self, place, name):  # intent keys a name-keyed counter from an older checkpoint could mean here: one per entity, exit or place it names
        keys, live, same = set(), {p["id"] for p in self.mem.rows("places") if p.get("state") != "merged"}, lambda v: str(v or "").casefold() == name.casefold()
        for r in self.mem.rows("entities"):
            if r.get("place") == place and same(r.get("name")) and r.get("status") != "gone" and not _is_exit(r):
                keys.add(fail_key(place, "approach", None, ref=f"thing:{r['id']}"))
                if normalize_kind(r.get("kind")) in ACTOR_KINDS: keys.add(fail_key(place, "interact", None, ref=f"thing:{r['id']}"))
        for x in self._exits(place, live):
            if same(x["name"]): keys.add(fail_key(place, "approach", None, ref=exit_ref(x)))
        for p in self.mem.rows("places"):
            if p["id"] != place and p["id"] in live and same(self.place_label(p["id"])): keys.add(fail_key(place, "approach", None, ref=f"place:{p['id']}"))
        return keys
    def plan(self, target, exit=None):  # exit: the chosen exit ({"tile", "to"}) travels through its own door
        if not target or not target.get("tile") or not self.current_place or not self.tile_px: return None
        path = self._through(exit, target["place"]) if exit and exit.get("tile") else self._paths(self.current_place).get(target["place"])
        if path is None: return None
        legs, pos = [], self.pos
        p = self.current_place
        for p, rec in path:
            for door in rec["doors"]:  # the representative door first; another door of the same exit if its route is infeasible
                tile = door["tile"]; dirs = route(self.mem, p, pos, tile, occupied=self.occupied(p))
                if dirs is not None: break
            else: return None
            legs.append({"place": p, "dirs": dirs, "push": door["dir"], "expect": door["to"], "exit_tile": tile, "arrive": tuple(door["arrive"])}); pos, p = tuple(door["arrive"]), door["to"]
        tile = tuple(target["tile"]); facing = None
        if target["kind"] == "memory":
            found = self.beside(p, pos, tile)
            if found is None: return None
            tile, facing, dirs = found
        else: dirs = route(self.mem, p, pos, tile, occupied=self.occupied(p))
        if dirs is None: return None
        legs.append({"place": p, "dirs": dirs + ([facing] if facing else []), "push": None, "expect": None, "target": target}); return legs
    def travel(self, ctx, legs, step, trace=None):
        results, inputs, place = [], 0, self.current_place; self._travel_cut = self._travel_open = False  # open: a cut travel could not place; the loop re-identifies
        for n, leg in enumerate(legs, 1):
            if inputs >= TRAVEL_MAX_INPUTS: break
            if leg["place"] != place: return self._travel_result(results, "blocked", f"leg {n} unexpected place", ctx)
            result = approach(replace(ctx, max_inputs=min(ctx.max_inputs, TRAVEL_MAX_INPUTS-inputs)), self.pa, self.pa,
                              exit_target="exit" if leg.get("expect") else None, route={"dirs": leg["dirs"], "push": leg.get("push")})
            if result.failure: result = replace(result, failure={**result.failure, "place": place})
            results.append(result); inputs += result.inputs
            # the skill checks every press for a cut; first vs last frame of a long walk is a scroll, not a cut (m1_decide2 replay step 42)
            is_cut = result.reason == "scene cut" or result.blank_seen; self._travel_cut |= is_cut; self.pos = fold(self.mem, place, self.pos, result.moves[:-1] if is_cut and result.moves else result.moves, self.tile_px, step, actors=self.actor_tiles)
            if leg.get("expect"):
                if not is_cut and result.failure and result.failure.get("stage") == "route": return self._travel_result(results, "blocked", f"leg {n} route blocked", ctx)  # the route never reached the exit: not an exit mismatch
                arrival = self._arrival(result.after, trace) if trace else None
                edge = exit_to(self.mem, place, tuple(leg["exit_tile"]), leg["push"])
                if not is_cut: return self._travel_result(results, "blocked", f"leg {n} exit mismatch", ctx)
                if arrival and (arrival[2] in ("dialogue", "menu") or (self.mem.get("places", arrival[0]) or {}).get("screen") in ("dialogue", "menu")): return self._travel_result(results, "blocked", f"leg {n} modal opened", ctx)
                wrong = arrival and arrival[0] != leg["expect"]
                mismatch = wrong or not edge or edge.get("to") != leg["expect"] or arrival and tuple(edge["arrive"]) != arrival[1]
                if mismatch:
                    if not arrival: self._travel_open = True; return self._travel_result(results, "blocked", f"leg {n} exit mismatch", ctx)
                    mark_exit(self.mem, place, tuple(leg["exit_tile"]), leg["push"], *arrival[:2], step); self._cross(place, tuple(leg["exit_tile"]), arrival[0], arrival[1], step)
                    self.pos, place = arrival[:2][1], arrival[0]; self._land(place)
                    return self._travel_result(results, "blocked", f"leg {n} {'arrival' if wrong or edge and edge.get('to') == leg['expect'] else 'exit'} mismatch", ctx)
                arrive = arrival[1] if arrival else tuple(edge["arrive"])
                self._cross(place, tuple(leg["exit_tile"]), leg["expect"], arrive, step)
                self.pos, place = arrive, leg["expect"]
                self._land(place)
            elif result.status != "done" and not (result.reason == "route blocked" and self._facing_target(leg, result)):
                return self._travel_result(results, result.status, f"leg {n} {result.reason or result.status}", ctx)
            if result.reason == "scene cut" and not leg.get("expect"): self._travel_open = True; return self._travel_result(results, "done", f"leg {n} scene cut", ctx)  # a real cut, not a failure; the loop re-identifies the place
        self.current_place = place
        if inputs >= TRAVEL_MAX_INPUTS and len(results) < len(legs): return self._travel_result(results, "blocked", f"leg {len(results)+1} input limit", ctx)
        return self._travel_result(results, "done", "", ctx)
    def _arrival(self, image, trace):
        if not trace.path.exists(): return None
        digest = hashlib.sha256(image.tobytes()).hexdigest(); live = {p["id"] for p in self.mem.rows("places") if p.get("state") != "merged"}; rows = map(json.loads, trace.path.open(encoding="utf8"))
        matches = {(row["place"], tuple(prior["tile"]), row.get("screen")) for prior, row in pairwise(rows) if row.get("frame_hash") == digest and prior.get("place") in live and row.get("place") in live and prior.get("next_place") == row["place"] and prior.get("tile")}; return next(iter(matches)) if len(matches) == 1 else None
    def _land(self, place):
        self.current_place, self.drift, self.drift_ref = place, None, None; self.entity_tiles, self.entity_ids, self.actor_tiles = {}, {}, frozenset()
    def _facing_target(self, leg, result):
        target = leg.get("target"); dx, dy = DIRS[leg["dirs"][-1]] if leg["dirs"] else (0, 0)
        if not target or target.get("kind") != "memory" or [m.get("dir") if isinstance(m, dict) else m[0] for m in result.moves] != leg["dirs"]: return False
        return (self.pos[0]+dx, self.pos[1]+dy) == tuple(target["tile"])
    def _travel_result(self, results, status, reason, ctx, failure=None):
        moves = [m for r in results for m in r.moves]
        # a done travel has no failure: an accepted final facing press is blocked in its skill but not a failure here
        failure = (failure or next((r.failure for r in results if r.failure), None)) if status == "blocked" else None
        if failure:
            step_dir = DIRS.get(failure.get("direction")); ahead = (self.pos[0] + step_dir[0], self.pos[1] + step_dir[1]) if step_dir else None
            blockers = [i for i, t in self.entity_tiles.items() if ahead and tuple(t) == ahead]
            failure = {**failure, "tile": tuple(self.pos), **({"blocker_id": blockers[0]} if len(blockers) == 1 else {})}
        self._travelled = SkillResult(status, f"travel: {reason}" if reason else "", sum(r.inputs for r in results), sum(r.calls for r in results), results[0].before if results else ctx.env.screenshot(), results[-1].after if results else ctx.env.screenshot(), tuple(map(sum, zip(*(r.scroll for r in results)))) if results else (0, 0), any(r.blank_seen for r in results), moves, next((m.get("self_at") for m in reversed(moves) if isinstance(m, dict) and m.get("self_at") is not None), None), failure, settled=all(r.settled is not False for r in results)); return self._travelled
    def name(self, place, name, step, frame=None):
        self.new_arrival, self.merge_pending = False, None
        cut_arrival, self._cut_arrival = self._cut_arrival, None
        if cut_arrival and (cut_arrival[1] != place or cut_arrival[0] + 1 != step): cut_arrival = None
        name = str(name or "").strip()
        if not name or not (row := self.mem.get("places", place)) or row.get("screen") != "world": return place  # only navigable places are named
        key = name.casefold()
        live = [r for r in self.mem.rows("places") if r.get("state") != "merged" and r.get("screen") == "world"]
        # "name [p_x]" is our own disambiguated label: it points at that place and is never a name or alias
        ref = next((r for r in live if self.place_label(r["id"]).casefold() == key and place_name(r).casefold() != key), None)
        if ref and ref["id"] == place: return place
        if not ref and not _nameable(name): return place  # a screen type or an unknown "name [p_x]" is no name or alias
        target = ref or next((r for r in live if r["id"] != place
                              and key in [str(v).casefold() for v in [place_name(r), *place_aliases(r)]]), None)
        if not target: return self.mem.name_place(place, name, step)
        if not cut_arrival or target["id"] != cut_arrival[2]:
            looks = [v for v in self.mem.rows("views") if self.mem._home(v["place"])[0] == target["id"]]
            if frame is not None and looks and self.mem.seen_view(frame, None, None, self.tile_px, only=target["id"]) is None:
                self.merge_pending = f"{name} (but this does not look like the known {self.place_label(target['id'])}; give it its own name)"; return place
            if row.get("first") == step: self.new_arrival = True; return self.mem.name_place(place, name, step)
            if self.merge(place, target["id"], step): return target["id"]
        aliases = row.get("aliases", [])
        if not ref and key not in {a.casefold() for a in aliases}: self.mem.upsert("places", {**row, "aliases": [*aliases, name]})
        self.merge_pending = name
        return place

    def merge(self, src, dst, step):
        if src == dst: return True
        entities = self.mem.rows("entities")
        left = [e for e in entities if e.get("place") == src]
        tiled = [e for e in left if e.get("tile")]
        right = [e for e in entities if e.get("place") == dst and e.get("tile")]
        pairs, used, bases = [], set(), set()
        for e in tiled:
            base = _base(e.get("name", ""))
            if base in bases: continue
            matches = [(r, max(abs(e["tile"][i] - r["tile"][i]) for i in (0, 1))) for r in right
                       if r["id"] not in used and _base(r.get("name", "")) == base]
            if matches:
                r, _ = min(matches, key=lambda p: p[1]); used.add(r["id"])
                bases.add(base)
                pairs.append(tuple(r["tile"][i] - e["tile"][i] for i in (0, 1)))
        if len(pairs) < 2: return False
        dx, dy = (round(median(p[i] for p in pairs)) for i in (0, 1))
        if sum(max(abs(p[0] - dx), abs(p[1] - dy)) <= 1 for p in pairs) < 2: return False
        if self.mem.views_agree(src, dst, (dx, dy), self.tile_px) is False: return False  # pixels outrank names
        for t in self.mem.rows("tiles"):
            if t.get("place") == src:
                x, y = t["x"] + dx, t["y"] + dy; tid = f"{dst}@{x},{y}"; other = self.mem.get("tiles", tid)
                if other:
                    t = {**other, "floor": True if True in (t.get("floor"), other.get("floor")) else False if t.get("floor") is other.get("floor") is False else None,
                         "blocked": list(dict.fromkeys(other.get("blocked", []) + t.get("blocked", []))),
                         "pushed": list(dict.fromkeys(other.get("pushed", []) + t.get("pushed", []))), "exit": other.get("exit") or t.get("exit"), "step": step}
                else: t = {**t, "id": tid, "place": dst, "x": x, "y": y, "step": step}
                self.mem.upsert("tiles", t)  # src rows stay as history: readers skip rows of merged places
        for t in self.mem.rows("tiles"):
            ex = t.get("exit")
            if t.get("place") != src and ex and ex.get("to") == src:
                self.mem.upsert("tiles", {**t, "exit": {**ex, "to": dst, "arrive": [ex["arrive"][0] + dx, ex["arrive"][1] + dy]}})
        targets, redirects = [e for e in entities if e.get("place") == dst], {}
        for e in left:
            tile = [e["tile"][0] + dx, e["tile"][1] + dy] if e.get("tile") else None
            duplicate = next((r for r in targets if _base(r.get("name", "")) == _base(e.get("name", "")) and
                              tile and r.get("tile") and max(abs(tile[i] - r["tile"][i]) for i in (0, 1)) <= 1), None)
            if duplicate:
                saved = self.mem.get("entities", duplicate["id"])
                self.mem.upsert("entities", {**saved, "seen": saved.get("seen", 0) + e.get("seen", 0), "last": step})
                redirects[e["id"]] = duplicate["id"]
            else:
                moved = {**e, "place": dst, **({"tile": tile} if tile else {}), "last": step}
                self.mem.upsert("entities", moved); targets.append(moved)
        for p in self.mem.rows("places"):
            edges = [{**e, "to": dst} if e.get("to") == src else e for e in p.get("edges", [])]
            if edges != p.get("edges", []): self.mem.upsert("places", {**p, "edges": edges})
        for r in self.mem.rows("relations"):
            s, o = redirects.get(r.get("s"), dst if r.get("s") == src else r.get("s")), redirects.get(r.get("o"), dst if r.get("o") == src else r.get("o"))
            if (s, o) != (r.get("s"), r.get("o")): self.mem.upsert("relations", {**r, "s": s, "o": o})
        p = self.mem.get("places", src)
        self.mem.upsert("places", {**p, "state": "merged", "into": dst, "offset": [dx, dy]})
        if self.current_place == src: self.pos = (self.pos[0] + dx, self.pos[1] + dy); self.current_place = dst
        return True
