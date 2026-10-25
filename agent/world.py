"""Spatial state and bookkeeping for the act path."""
import hashlib, json, re
from collections import Counter
from dataclasses import replace
from itertools import pairwise
from PIL import ImageDraw
from agent.memory import ACTOR_KINDS, STATIC_GATE, TRANSIENT, _actor, _base, _distance, _is_exit as _memory_is_exit, _nameable, normalize_kind, place_aliases, place_name
from agent.observer import changed, cut
from agent.space import DIRS, exit_to, fold, frontier, mark_exit, outward, push_dirs, push_failed, route, tile_of
from agent.skills import SkillResult, approach, on_player, press
from agent.track import Tracker

TRAVEL_MAX_INPUTS, BUMPS = 120, 3
MISS_TO_STALE, ACTOR_STALE_STEPS = 2, 20
LANDMARKS_PER_PLACE, MAP_W, MAP_H, VIEW_TOKENS = 8, 21, 15, 500

TYPING = {"keyboard", "input_field"}  # a text field on a modal screen: type
FURNITURE = {"player", "dialogue_box", "text", "logo", "cursor"}  # screen chrome: never a choice on a modal screen

def modal(per):
    return per.get("screen") != "world" or any(normalize_kind(e.get("kind")) in {"dialogue_box", "menu", "keyboard", "input_field"} for e in per.get("entities", []))

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
def exit_ref(x):
    tiles = sorted(tuple(d["tile"]) for d in x["doors"])
    tile = tiles[0] if tiles else x.get("tile")
    if tile: return f"exit:{tile[0]},{tile[1]}"
    if x.get("ident"): return f"thing:{x['ident']}"
    return x.get("name")

def _yx(pos, name):  # sort key: row, column, name; no position sorts last
    return (pos[1], pos[0], str(name or "")) if isinstance(pos, (list, tuple)) and len(pos) == 2 else (10**6, 10**6, str(name or ""))

def _away(pos, tile):
    dx, dy = tile[0] - pos[0], tile[1] - pos[1]
    parts = ([f"{abs(dx)} {'left' if dx < 0 else 'right'}"] if dx else []) + ([f"{abs(dy)} {'up' if dy < 0 else 'down'}"] if dy else [])
    return ", ".join(parts) or "here"

def _is_exit(e):
    return any(normalize_kind(k) == "exit" for k in (e.get("kind"), *e.get("kinds", []))) or any(
        str(n).casefold().startswith("exit:") for n in (e.get("name"), *e.get("aliases", [])))

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

def unexplored(mem, pid):
    row = mem.get("seen", pid) or {}
    seen, cells = set(row.get("cells", [])), set(row.get("open", [])) & set(row.get("cells", []))
    seen_cells = {tuple(map(int, cell.split(","))) for cell in cells}
    tiles = [r for r in mem.rows("tiles") if r.get("place") == pid]
    known = {(r["x"], r["y"]) for r in tiles} | {
        (r["x"] + DIRS[d][0], r["y"] + DIRS[d][1]) for r in tiles for d in r.get("blocked", []) if d in DIRS}
    edge = {c for c in seen_cells if c not in known and any(f"{c[0]+dx},{c[1]+dy}" not in seen for dx, dy in DIRS.values())}  # ponytail: edge cells may be walls; each costs at most one bump before `known` excludes it.
    return {tuple(map(int, cell.split(","))) for cell in open_cells(mem, pid)} | {
        (tile[0] + DIRS[d][0], tile[1] + DIRS[d][1]) for tile, d in unseen_edges(mem, pid)} | edge

def _direction(pos, target):
    dx, dy = target[0] - pos[0], target[1] - pos[1]
    if not dx and not dy: return None
    return ("RIGHT" if dx > 0 else "LEFT") if abs(dx) >= abs(dy) else ("DOWN" if dy > 0 else "UP")

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
        self.odometer = self.tracker.odometer; self.odometer.tile_px = tile_px; self._odo_px = (0, 0); self._odo_stats = None
        self._odo_screen = None
        self._held = None
        self.pos, self.drift, self.drift_ref = (0, 0), None, None
        self.entity_tiles, self.entity_ids, self.actor_tiles = {}, {}, frozenset()
        self.entities, self.pa, self._prepared = [], None, None; self._prepared_segments, self._prepared_forced, self._prepared_scene_cut = [], False, False
        self.legend = {}
        self._observed_per = None
        self.novel = 0
        self.current_place = None
        self.unsettled, self.no_response = False, 0
        self._travelled = None
        self._last_miss_hash = {}
        self.self_spot, self._self_contradictions, self._self_from_motion = None, 0, False
        self._hud, self._persist = {}, {}
    def _anchor(self, per, size):
        return next((e.get("at") for e in per.get("entities", [])
                     if e.get("kind", "").casefold() == "player"), None)
    def _self_index(self, per, size):
        entities = per.get("entities", [])
        valid = lambda e: isinstance(e.get("at"), list) and len(e["at"]) == 2
        if self._self_from_motion and self.self_spot is not None and self.tile_px:
            near = [(max(abs(e["at"][i] - self.self_spot[i]) * size[i] for i in (0, 1)), n)
                    for n, e in enumerate(entities) if valid(e)]
            return min(near)[1] if near and min(near)[0] <= self.tile_px / 2 else None
        return next((i for i, e in enumerate(entities) if e.get("kind", "").casefold() == "player"), None)
    def _observation(self, per, size):
        self_i = self._self_index(per, size)
        return {**per, "entities": [{"name": ""} if i == self_i else e for i, e in enumerate(per.get("entities", []))]}
    def _tiles(self, per, anchor, size, place):
        if per.get("screen") != "world" or (self.mem.get("places", place) or {}).get("screen") != "world" or place == self.mem.unplaced: return {}  # a title or menu screen before any map has no world tiles: its things keep text identity
        entities = per.get("entities", [])
        self_i = self._self_index(per, size)
        valid = lambda e: isinstance(e.get("at"), list) and len(e["at"]) == 2
        if not anchor or not self.tile_px: return {}
        return {i: tile_of(e["at"], anchor, self.pos, size, self.tile_px)
                for i, e in enumerate(entities) if i != self_i and valid(e)}
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
    def before(self, per, place, prior, size, step, frame=None):
        self.current_place, self.size = place, size
        self._odo_screen = per
        if per.get("screen") == "world": self.name(place, per.get("place"), step)
        if prior and place != prior:
            held = self._held
            if held and held["src"] == prior:
                located = self._locate(place, frame)
                delta = tuple(round(-sum(move[i + 1] for move in seg.get("moves", [])) / self.tile_px) for i in (0, 1)) if self.tile_px else (0, 0)
                arrival = tuple(located[i] - delta[i] for i in (0, 1)); self.pos = arrival; self._odo_px = (0, 0)
                self._held = None
                for segment in held["segments"]: self._odometer_fold({**segment, "_place": place}, place, step)
                if held["dir"]:
                    mark_exit(self.mem, held["src"], held["tile"], held["dir"], place, arrival, step)
                    self._cross(held["src"], held["tile"], place, arrival, step)
            else:
                if held:
                    self._held = None
                    if self._odo_stats is None: self.odometer_begin()
                    self._odo_stats["dropped"] += len(held["segments"])
                self.pos, self._odo_px = self._locate(place, frame), (0, 0)
            self.drift = self.drift_ref = None
            self._observed_per = None; self.tracker.clear()
        elif self._held and per.get("screen") == "world" and place == self._held["src"]:
            held = self._held; self._held = None
            for segment in held["segments"]: self._odometer_fold(segment, held["src"], step)
        player = next((e for e in per.get("entities", []) if e.get("kind", "").casefold() == "player"), None)
        pa = self.self_spot if self._self_from_motion else self._anchor(per, size) if player else None
        self.pa = pa
        self.tracker.phase = (round(pa[0] * size[0]) % self.tile_px, round(pa[1] * size[1]) % self.tile_px) if pa and self._self_from_motion and self.tile_px else None  # sprites rest on the player's grid
        px, py = (pa[0] * size[0], pa[1] * size[1]) if pa else (0, 0); self.tracker.ignore = [(max(0, int(px) - 10), max(0, int(py) - 10), int(px) + 10, int(py) + 10)] if pa else []  # the player animates in place: not a thing that moved
        proj = lambda at: tile_of(at, pa, self.pos, size, self.tile_px) if pa and self.tile_px and isinstance(at, list) and len(at) == 2 else None
        tiles = self._tiles(per, pa, size, place)
        observed = self._observation(per, size)
        ids = {} if per.get("screen") != "world" else self.entity_ids if per is self._observed_per else self.mem.observe(observed, place, step, tiles, self._hits(observed))
        self._observed_per = per; self._follow(per, ids, pa, size)
        self.actor_tiles = frozenset(tiles[i] for i, e in enumerate(per.get("entities", [])) if i in tiles and self._actor(ids.get(i), e))
        self.entity_ids = ids
        if per.get("screen") == "world": self.entity_tiles = {ids[i]: p for i, p in tiles.items() if i in ids}
        self.entities = [{"id": ids.get(i), "name": e.get("name"), "kind": e.get("kind"), "at": e.get("at")}
                         for i, e in enumerate(per.get("entities", []))]
        if per.get("screen") == "world": self._tiers(per, place, ids)
        return proj, tiles, ids
    def _hits(self, per):  # entity index -> id of the live pixel track under it (within a tile)
        if not self.tile_px or self.tracker.arr is None: return {}
        h, w = self.tracker.arr.shape[:2]
        tracks = {i: t for i, t in self.tracker.tracks.items() if t["status"] == "live"}
        pairs = []
        for i, e in enumerate(per.get("entities", [])):
            if isinstance(e.get("at"), list) and len(e["at"]) == 2:
                x, y = self.tracker._snap(e["at"][0] * w, e["at"][1] * h)
                pairs.extend((max(abs(t["x"] - x), abs(t["y"] - y)), i, ident)
                             for ident, t in tracks.items())
        hits, used = {}, set()
        for d, i, ident in sorted(pairs):
            if d <= self.tile_px and i not in hits and ident not in used:
                hits[i] = ident; used.add(ident)
        return hits
    def _follow(self, per, ids, anchor=None, size=None):  # seed tracks for what was just perceived; a track lost in view for a while (no text box over it) means the thing left: "gone"
        self.tracker.seed([(ids[i], e["at"], self._actor(ids[i], e)) for i, e in enumerate(per.get("entities", []))
                           if i in ids and self._actor(ids[i], e) and e.get("kind", "").casefold() != "player" and isinstance(e.get("at"), list) and len(e["at"]) == 2])
        if per.get("screen") != "world" or per.get("text") or any(e.get("kind") == "dialogue_box" for e in per.get("entities", [])): return
        gone = self.tracker.gone() - set(ids.values())
        for ident in gone: self.tracker.tracks.pop(ident)
        if anchor and size and (rect := self._view_rect(anchor, size)):
            x0, y0, x1, y1 = rect; present = set(ids.values()) | {ident for ident, track in self.tracker.tracks.items() if track["status"] == "live"}
            gone.update(row["id"] for row in self.mem.rows("entities") if row.get("place") == self.current_place and row.get("state") != "merged" and row.get("status") != "gone" and row.get("tile") and x0 <= row["tile"][0] <= x1 and y0 <= row["tile"][1] <= y1 and row["id"] not in present and self._actor(row["id"], row))
        for ident in gone:
            row = self.mem.get("entities", ident)
            if row and row.get("status") != "gone": self.mem.upsert("entities", {**row, "status": "gone"})
    def _promote(self, ids):
        for ident in ids:
            row = self.mem.get("entities", ident)
            if row and row.get("place") and (self.mem.get("places", row["place"]) or {}).get("state") == "merged": continue
            is_exit = bool(row and _is_exit(row))
            if is_exit and row.get("place") and self._refuted(row["place"], row.get("tile")) > 0:
                if row.get("tier") != "landmark": self.mem.upsert("entities", {**row, "tier": "landmark"})
            elif row and row.get("tier") != "interest": self.mem.upsert("entities", {**row, "tier": "interest"})
    def _actor(self, ident, entity):
        row = self.mem.get("entities", ident) if ident else None
        anchor = row.get("anchor") if row else None
        if anchor in ("actor", "world", "self", "screen"): return anchor == "actor"
        if row and row.get("kind"): return normalize_kind(row["kind"]) in ACTOR_KINDS
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
            is_exit = _is_exit(entity) or _is_exit(row)
            failed_exit = is_exit and self._refuted(place, row.get("tile")) > 0
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
    def _refuted(self, place, tile):
        if tile is None: return 0
        x, y = tile
        if any(r["id"].startswith(f"{place}@{x},{y}>") for r in self.mem.rows("crossings")): return 0
        blocked = 0
        for direction, (dx, dy) in DIRS.items():
            neighbour = self.mem.get("tiles", f"{place}@{x - dx},{y - dy}")
            if neighbour and neighbour.get("floor") is True and direction in neighbour.get("blocked", []): blocked += 1
        here = self.mem.get("tiles", f"{place}@{x},{y}")
        if here and here.get("floor") is True and not push_dirs(self.mem, place, tile): blocked += 1
        return blocked
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
        self.novel = 0
        if per.get("screen") != "world": return
        live = {r["id"] for r in self.mem.rows("places") if r.get("state") != "merged"}
        rect = self._view_rect(anchor, size) if place in live and place != self.mem.unplaced else None
        world_screen = per.get("screen") == "world"
        modal_screen = modal(per)
        if rect and world_screen:
            x0, y0, x1, y1 = rect
            seen = self.mem.get("seen", place) or {"id": place, "cells": [], "step": step}
            cells = set(seen.get("cells", [])); added = {f"{x},{y}" for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)} - cells
            self.novel = len(added)
            past = {"LEFT": lambda i: (x0 - 1, y0 + i), "RIGHT": lambda i: (x1 + 1, y0 + i), "UP": lambda i: (x0 + i, y0 - 1), "DOWN": lambda i: (x0 + i, y1 + 1)}
            edge = _open_edge(frame, self.tile_px) if frame is not None and not modal_screen else {}
            was = set(seen.get("open", [])); opened = {"%d,%d" % past[d](i) for d, idx in edge.items() for i in idx} - was
            if added or opened: self.mem.upsert("seen", {"id": place, "cells": sorted(cells | added), "open": sorted(was | opened), "step": step})
        distinct = False
        if rect and world_screen and not modal_screen and not reused:
            digest = hashlib.sha256(frame.tobytes()).hexdigest()
            distinct = self._last_miss_hash.get(place) != digest
            if distinct: self._last_miss_hash[place] = digest
        if rect:
            x0, y0, x1, y1 = rect
            mx0, my0, mx1, my1 = x0 + 1, y0 + 1, x1 - 1, y1 - 1
            if self.mem.looks is not None and frame is not None and world_screen and not modal_screen and self.tile_px:
                # ponytail: assumes the pixel grid starts at (0,0) when settled; V0 matched all 1,083 frames, but scrolling-phase games need tracker phase.
                gray, t = frame.convert("L"), self.tile_px
                for x in range(x0, x1 + 1):
                    for y in range(y0, y1 + 1):
                        if (x, y) == tuple(self.pos) or (x, y) in self.actor_tiles: continue
                        ident = f"{place}@{x},{y}"; look = hashlib.blake2b(gray.crop(((x - x0) * t, (y - y0) * t, (x - x0 + 1) * t, (y - y0 + 1) * t)).tobytes(), digest_size=8).hexdigest()
                        old = self.mem.get("looks", ident)
                        if not old or old.get("look") != look: self.mem.upsert("looks", {"id": ident, "place": place, "x": x, "y": y, "look": look, "step": step})
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
                status = "stale" if misses >= MISS_TO_STALE else status
                row = {**row, "misses": misses, "status": status}
            if row.get("status") != self.mem.get("entities", ident).get("status") or row.get("misses") != self.mem.get("entities", ident).get("misses"):
                self.mem.upsert("entities", row)
    def odometer_begin(self): self._odo_stats = {"frames": 0, "px": [0, 0], "uncertain": 0, "cuts": 0, "gap": False, "dropped": 0, "drained": False}
    def odometer_trace(self): return {k: v for k, v in (self._odo_stats or {}).items() if k != "drained"} if self._odo_stats and self._odo_stats["drained"] else None
    def _odometer_drain(self, through, place, step, bumps=(), next_per=None, next_place=None):
        segments = self.odometer.take(through=through)
        if self._odo_stats is None: self.odometer_begin()
        stats = self._odo_stats; stats["drained"] |= any(not seg.get("_synthetic") for seg in segments)
        screen, screen_place, cut = self._odo_screen or next_per, place, False
        for segment in segments:
            if cut: screen, screen_place = next_per or screen, next_place or place
            segment["_screen"], segment["_place"] = screen, screen_place
            stats["frames"] += segment["frames"]; stats["uncertain"] += segment["uncertain"]
            stats["cuts"] += segment["cut"] is not None; stats["gap"] |= segment["gap"]
            stats["px"] = [stats["px"][i] + sum(move[i + 1] for move in segment["moves"]) for i in (0, 1)]
            cut |= segment.get("cut") is not None or segment.get("gap", False)
        for move in bumps:
            raw = move
            if isinstance(move, (tuple, list)) and len(move) == 2: move = {"dir": move[0], "shift": move[1]}
            if not isinstance(move, dict): continue
            at = move.get("at") if move.get("at") is not None else through if through is not None else self.odometer.seq
            if isinstance(raw, dict): raw.setdefault("at", at)
            move = raw if isinstance(raw, dict) else dict(move, at=at)
            i = next((i for i, seg in enumerate(segments) if seg["end"] is not None and at is not None and at <= seg["end"]), len(segments) - 1)
            bump = move.get("shift") in ((0, 0), 0) or move.get("no_response") and move.get("shift") is None
            if not bump:
                if at is not None and i >= 0: segments[i].setdefault("_presses", []).append(move)
                continue
            if i < 0:
                segments.append({"start": None, "end": None, "moves": [], "cut": None, "gap": False, "arrival": None, "frames": 0, "uncertain": 0, "_synthetic": True, "_screen": self._odo_screen, "_place": place})
                i = 0
            segments[i].setdefault("_bumps", []).append(move)
        return segments
    def odometer_start(self, through, place, step, next_per=None, next_place=None):
        segments = self._odometer_drain(through, place, step, next_per=next_per, next_place=next_place)
        stop = next((i for i, seg in enumerate(segments) if seg.get("cut") or seg.get("gap")), len(segments))
        for segment in segments[:stop]: self._odometer_fold(segment, place, step)
        if stop < len(segments):
            if segments[stop].get("cut"): self._odometer_fold(segments[stop], place, step)
            self._odo_stats["dropped"] += len(segments) - stop - 1 if segments[stop].get("cut") else len(segments) - stop
        return any(segment.get("cut") for segment in segments)
    def _odometer_fold(self, segment, place, step):
        if self._held is not None:
            self._held["segments"].append(segment)
            return None
        screen, place = segment.get("_screen"), segment.get("_place", place)
        screen_name = screen.get("screen") if isinstance(screen, dict) else None
        world_type = screen_name == "world" or screen_name in ("dialogue", "menu") and (self.mem.get("places", place) or {}).get("screen") == "world"
        if not world_type or segment.get("gap"):
            if self._odo_stats is not None: self._odo_stats["dropped"] += 1
            if segment.get("cut") or segment.get("gap"): self._odo_px = (0, 0)
            return None
        if not self.tile_px or place == self.mem.unplaced:
            if self._odo_stats is not None: self._odo_stats["dropped"] += 1
            if segment.get("cut") or segment.get("gap"): self._odo_px = (0, 0)
            return None
        moves, px = [], list(self._odo_px); tile_delta, prev_delta, origin, last_dir = [0, 0], [0, 0], self.pos, None
        events = [(m.get("at"), 1, m) for m in segment.get("_bumps", [])]
        events.extend((m.get("at"), 1, m) for m in segment.get("_presses", []))
        events.extend((seq, 0, (dx, dy, held)) for seq, dx, dy, held in segment["moves"])
        for _seq, kind, move in sorted(events, key=lambda e: (e[0], e[1])):
            if kind == 1:
                move["_tile"] = (origin[0] + tile_delta[0], origin[1] + tile_delta[1])
                if move.get("shift") in ((0, 0), 0) and move.get("dir") in DIRS:
                    at = (origin[0] + tile_delta[0], origin[1] + tile_delta[1]); target = (at[0] + DIRS[move["dir"]][0], at[1] + DIRS[move["dir"]][1])
                    self._promote(i for i, tile in self.entity_tiles.items() if tile == target)
                    moves.append(move)
                elif move.get("no_response") and move.get("shift") is None and move.get("dir") in DIRS: moves.append(move)
                continue
            dx, dy, _held = move; px[0] += dx; px[1] += dy
            after = [round(-v / self.tile_px) for v in px]
            for axis, unit in enumerate(after[i] - prev_delta[i] for i in (0, 1)):
                if not unit: continue
                direction = ("RIGHT" if unit > 0 else "LEFT") if axis == 0 else ("DOWN" if unit > 0 else "UP")
                shift = [-self.tile_px if unit > 0 else self.tile_px, 0] if axis == 0 else [0, -self.tile_px if unit > 0 else self.tile_px]
                moves.extend({"dir": direction, "shift": tuple(shift)} for _ in range(abs(unit))); tile_delta[axis] += unit; last_dir = direction
            prev_delta = after
        if not segment.get("gap") and moves: self.pos = fold(self.mem, place, self.pos, moves, self.tile_px, step, actors=self.actor_tiles)
        self._odo_px = (0, 0) if segment.get("cut") or segment.get("gap") else tuple(px[i] + self.tile_px * prev_delta[i] for i in (0, 1))
        return last_dir
    @staticmethod
    def _join_odometer_segments(segments):
        joined = []
        for segment in segments:
            if joined and joined[-1].get("cut") is None and not joined[-1].get("gap") and joined[-1].get("end") is not None and segment.get("start") == joined[-1]["end"] + 1:
                prior = joined[-1]; prior["moves"].extend(segment["moves"]); prior["frames"] += segment["frames"]; prior["uncertain"] += segment["uncertain"]
                prior["end"], prior["cut"], prior["gap"] = segment["end"], segment["cut"], segment["gap"]
                if prior.get("arrival") is None: prior["arrival"] = segment.get("arrival")
            else: joined.append(dict(segment, moves=list(segment["moves"])))
        return joined
    def prepare(self, skill, place, scene_cut, step, through=None):
        repeated = bool(self._prepared and self._prepared[0] is skill and self._prepared[1:3] == (place, step))
        if not repeated:
            for move in getattr(skill, "moves", []):
                if isinstance(move, dict) and move.get("no_response"): self.no_response += 1
                elif move is not None and (move.get("shift") if isinstance(move, dict) else move[1]) is not None: self.no_response = 0
        after = getattr(skill, "after", None); after_frame = getattr(after, "frame", after)
        after_seq = (getattr(after, "frame_stamp", None) or {}).get("frame_seq") or getattr(after_frame, "info", {}).get("frame_seq")
        def late_cut_at(segments):
            for i, segment in enumerate(segments):
                if not segment.get("cut"): continue
                following = segments[i + 1] if i + 1 < len(segments) else None
                if after_seq is not None:
                    if following and following.get("start") is not None and following["start"] > after_seq: return i
                    if not following and segment.get("cut") == "blank" and segment.get("end") is not None and segment["end"] > after_seq: return i
                elif following and (arrival := following.get("arrival")) is not None and after_frame is not None and arrival.size == after_frame.size and arrival.tobytes() != after_frame.tobytes(): return i
            return None
        if skill is self._travelled:  # travel already folded pos up to the exit tile; an open cut still crosses from there
            moves = skill.moves; prior = getattr(self, "_travel_segments", []); self._travel_segments = []
            late = self._odometer_drain(through, place, step) if through is not None else []
            cut_at = late_cut_at(late)
            if cut_at is None and scene_cut and not self._travel_cut: cut_at = next((i for i, segment in enumerate(late) if segment.get("cut")), None)
            last_unit = None
            if not self._travel_open:
                for segment in late[:cut_at + 1 if cut_at is not None else len(late)]:
                    last_unit = self._odometer_fold(segment, place, step) or last_unit
                if cut_at is not None or scene_cut and not self._travel_cut: self._travel_open = self._travel_cut = True
                self._prepared_segments = late if cut_at is not None else []
            else: self._prepared_segments = [*prior, *late]
            press_dir = (moves[-1].get("dir") if isinstance(moves[-1], dict) else moves[-1][0]) if moves else None
            cut_dir = press_dir if self._travel_open and press_dir else last_unit
            self._prepared_forced = bool((self._prepared_segments or prior) and any(move[3] not in DIRS for move in (self._prepared_segments or prior)[0]["moves"]))
            self._prepared_scene_cut = scene_cut or self._travel_cut or cut_at is not None
            self._prepared = (skill, place, step, cut_dir, self.pos); return {"cut_dir": cut_dir, "exit_key": (*self.pos, cut_dir) if cut_dir and self.tile_px else None}
        through = through if through is not None else getattr(getattr(skill, "after", None), "info", {}).get("frame_seq", self.odometer.seq)
        prior = self._prepared_segments if repeated else []
        prior_cut = bool(repeated and self._prepared_scene_cut)
        moves = getattr(skill, "moves", []); segments = self._odometer_drain(through, place, step, () if repeated else moves)
        self._prepared_segments = [*prior, *segments]
        self._prepared_scene_cut = scene_cut or (self._prepared_scene_cut if repeated else False) or late_cut_at(self._prepared_segments) is not None
        first = segments[0] if segments else None; last_unit = self._odometer_fold(first, place, step) if first and not prior_cut else None
        evidence = prior[0] if prior else first
        self._prepared_forced = bool(evidence and any(move[3] not in DIRS for move in evidence["moves"]))
        for move in moves:
            if isinstance(move, dict) and move.get("dir") in DIRS: last_press = move.get("dir")
        cut_dir = (locals().get("last_press") or last_unit or (self._prepared[3] if repeated else None)) if self._prepared_scene_cut else None
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
            scene_cut = self._prepared_scene_cut
            result.update(presses=result["presses"] + skill.inputs, skill=skill, scene_cut=scene_cut)
            if not scene_cut: self.after(skill, place, place, False, step)
            move = skill.moves[-1] if skill.moves else {}
            spot, shift = move.get("self_at"), move.get("shift")
            if (spot is not None and isinstance(shift, tuple) and max(map(abs, shift)) >= self.tile_px):
                self.self_spot, self._self_contradictions, self._self_from_motion = list(spot), 0, True
            result["locked"] = self._self_from_motion
            if scene_cut or skill.status != "done" or result["locked"]: break
        return result

    def failed_push(self, skill, place, start, direction, step):
        moves = skill.moves
        if direction not in DIRS or not moves or not self.tile_px or place == self.mem.unplaced: return
        tile = self._travel_push_from if skill is self._travelled and self._travel_push_from is not None else moves[-1].get("_tile", tuple(start)) if isinstance(moves[-1], dict) else tuple(start)  # the odometer records the tile where the final press began
        shift = moves[-1].get("shift") if isinstance(moves[-1], dict) else moves[-1][1]
        if shift not in ((0, 0), 0): push_failed(self.mem, place, tile, direction, step)
    def after(self, skill, place, next_place, scene_cut, step):  # call after prepare() for the same step
        through = getattr(self, "_odo_through", self.odometer.seq); self._odo_through = None
        if skill is self._travelled and not self._travel_open:
            self.current_place = next_place
            for segment in self._odometer_drain(through, next_place, step): self._odometer_fold(segment, next_place, step)
            return {"known_exit": None}
        cut_dir = self._prepared[3]
        known_exit = exit_to(self.mem, place, self.pos, cut_dir) if scene_cut and cut_dir else None
        segments = self._join_odometer_segments([*getattr(self, "_prepared_segments", []), *self._odometer_drain(through, place, step)])
        self._prepared_segments = []
        after_cut = False
        for segment in segments:
            if after_cut: segment["_screen"], segment["_place"] = self._odo_screen, next_place
            after_cut |= segment.get("cut") is not None or segment.get("gap", False)
        if scene_cut and next_place != place:
            last = segments[-1] if len(segments) > 1 else None
            self._odo_stats["dropped"] += max(0, len(segments) - 2)
            arrival = last.get("arrival") if last else None
            view = self._seen_at(next_place); located = self._locate(next_place, arrival or getattr(skill, "after", None))
            delta = tuple(round(-sum(move[i + 1] for move in last["moves"]) / self.tile_px) for i in (0, 1)) if view and view[1] == "view" and last and self.tile_px else (0, 0)
            arrival_pos = tuple(located[i] - delta[i] for i in (0, 1)); self.pos = arrival_pos; self._prepared = (*self._prepared, arrival_pos)
            self._odo_px = (0, 0)
            if len(segments) > 1: self._odometer_fold(last, next_place, step)
            if cut_dir: self._cross(place, self._prepared[4], next_place, arrival_pos, step)  # a crossing takes a press in a direction
            self.drift = self.drift_ref = None
            self.entity_tiles = self.entity_ids = {}
        else:
            if self._held is None and scene_cut and next_place == place and (self._odo_screen or {}).get("screen") != "world":
                cut_at = next((i for i, segment in enumerate(segments) if segment.get("cut")), len(segments) - 1)
                last_dir = self._prepared[3]
                for segment in segments[1:cut_at + 1]: last_dir = self._odometer_fold(segment, place, step) or last_dir
                self._held = {"src": place, "tile": self.pos, "dir": last_dir, "segments": segments[cut_at + 1:]}
            else:
                for segment in segments[1:]: self._odometer_fold(segment, place, step)
        return {"known_exit": known_exit}
    def _cross(self, src, tile, dst, arrive, step):
        """Count a crossing on its departure tile only."""
        if tile: self._count(src, tuple(tile), dst, step)
    def _count(self, place, at, other, step):  # one crossing counted on one side of an exit
        key = _cross_key(place, at, other)
        row = self.mem.get("crossings", key) or {"id": key, "n": 0}
        if row.get("miss"): row = {**row, "miss": 0}
        self.mem.upsert("crossings", {**row, "n": row["n"] + 1, "last": step})
    def unseen_cross(self, src, dst, step):  # a place change first seen at the next observation: pos is still in src, so its exit tile is the one within 1
        if self._held is not None: return
        hits = [r for r in self.mem.rows("tiles") if r.get("place") == src and (r.get("exit") or {}).get("to") == dst and max(abs(r["x"] - self.pos[0]), abs(r["y"] - self.pos[1])) <= 1]
        if len(hits) == 1: self._cross(src, (hits[0]["x"], hits[0]["y"]), dst, hits[0]["exit"].get("arrive"), step)
        # ponytail: no exit tile within 1 of pos (or two): the crossing is not counted
    def _where(self, tile):  # ", 2 left, 1 up" from you; nothing when the tile or where you stand is unknown (an unplaced place): no invented distance
        return f", {_away(self.pos, tile)}" if tile and self.current_place != self.mem.unplaced else ""
    def _seen_at(self, place):
        hit = getattr(self.mem, "arrival", None)
        return (tuple(hit[1]), hit[2]) if hit and hit[0] == place else None
    def _locate(self, place, frame=None):
        """Where you stand on entering place: the tile the identifying match handed over, else a stored view of place, else unknown.
        ponytail: an unknown position (Memory.unplaced) stops tiles, folding and new views until a stored view of the place matches; a place
        with no view near any tile you can reach stays unplaced, add a re-anchor on a crossed exit if that happens"""
        arrival = self._seen_at(place); tile = arrival[0] if arrival else None
        if frame is not None and self.tile_px and (tile is None or arrival and arrival[1] == "inferred") and (hit := self.mem.seen_view(frame, None, None, self.tile_px, only=place)):
            view = tuple(hit[1])
            if arrival and arrival[1] == "inferred" and view != tile and (row := self.mem.get("tiles", getattr(self.mem, "arrival_exit", None))): self.mem.upsert("tiles", {**row, "exit": {**row["exit"], "arrive": list(view), "src": "observed"}})
            tile = view
        self.mem.unplaced = None if tile is not None else place
        return tile if tile is not None else self.pos
    def mark_exit(self, place, direction, to, arrive, step):
        mark_exit(self.mem, place, self._prepared[4], direction, to, self._prepared[5] if len(self._prepared) > 5 else arrive, step)
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
        if place == self.mem.unplaced and frame is not None and self.tile_px and (hit := self.mem.seen_view(frame, None, None, self.tile_px, only=place)):
            self.pos, self.mem.unplaced = tuple(hit[1]), None; self._odo_px = (0, 0)  # a stored view says where you are: positions count again
        player = next((e for e in per.get("entities", []) if e.get("kind", "").casefold() == "player"), None)
        aa = self.self_spot if self._self_from_motion else self._anchor(per, size) if player else None
        tiles = self._tiles(per, aa, size, place)
        drift_fix, ids = None, {} if per.get("screen") != "world" else self.entity_ids
        observed = self._observation(per, size)
        world_screen = False
        if not reused:
            modal_screen = modal(per)
            world_screen = per.get("screen") == "world" and not modal_screen
            if per.get("screen") == "world": ids = self.mem.observe(observed, place, step, tiles, self._hits(observed))
        if not reused and world_screen and place and self.tile_px:
            current = {ids[i]: t for i, t in tiles.items() if i in ids}
            walked = self.tracker.moved()
            untracked = {ids[i] for i, e in enumerate(per.get("entities", []))
                         if i in ids and self._actor(ids[i], e) and
                         (ids[i] not in self.tracker.tracks or self.tracker.tracks[ids[i]]["status"] != "live")}
            pairs = self.entity_tiles.keys() & current.keys() - walked - untracked
            anchor = self.drift_ref or self.entity_tiles
            delta = [(tuple(anchor[i][j] - current[i][j] for j in (0, 1)), i) for i in pairs if i in anchor]
            if delta:
                dx, dy = (sorted(v[0][j] for v in delta)[len(delta) // 2] for j in (0, 1)); drift = (dx, dy) if (dx, dy) != (0, 0) else None
                at = self.mem.seen_view(frame, None, None, self.tile_px, only=place) if drift and drift == self.drift and frame is not None else None
                if at and tuple(at[1]) != (self.pos[0] + dx, self.pos[1] + dy): self.drift = self.drift_ref = None  # stored views say where we are: entity drift was noise
                elif drift and drift == self.drift:
                    self.pos = (self.pos[0] + dx, self.pos[1] + dy); self._odo_px = (0, 0); drift_fix = (dx, dy)
                    tiles = {i: (p[0] + dx, p[1] + dy) for i, p in tiles.items()}
                    for ident, tile in current.items():
                        row = self.mem.get("entities", ident)
                        if row: self.mem.upsert("entities", {**row, "tile": [tile[0] + dx, tile[1] + dy]})
                    self.drift = self.drift_ref = None
                else: self.drift, self.drift_ref = drift, anchor if drift else None
        if not reused:
            self.entity_ids = ids
            self._observed_per = per; self._follow(per, self.entity_ids, aa, size)
        if not reused:
            stored = any(self.mem._home(v["place"])[0] == place and
                         tuple(v["pos"][i] + self.mem._home(v["place"])[1][i] for i in (0, 1)) == tuple(self.pos)
                         for v in self.mem.rows("views"))
            if world_screen and self.tile_px and not stored and frame is not None:
                hit = self.mem.seen_view(frame, "world", place, self.tile_px)
                if hit:
                    other, tile = hit[:2]; off = tuple(tile[i] - self.pos[i] for i in (0, 1)); prior = getattr(self, "_closure", None)
                    if prior and prior[:2] == (other, off) and prior[2] != self.pos:
                        row, known = self.mem.get("places", place) or {}, self.mem.get("places", other) or {}
                        if row.get("first", step) >= known.get("first", step):
                            src, dst, offset = place, other, off
                        else:
                            src, dst, offset = other, place, (-off[0], -off[1])
                        # ponytail: pixel-identical interiors (e.g. two copies of one building) can false-merge; add a topology check (no crossing path between them) if one does.
                        if self.merge(src, dst, step, offset):
                            if src == place:
                                tiles = {i: (t[0] + offset[0], t[1] + offset[1]) for i, t in tiles.items()}
                            place = self.current_place = dst
                            self.entity_ids = {i: self.canon(f"thing:{ident}").split(":", 1)[1] for i, ident in self.entity_ids.items()}
                            self._closure = None
                    else: self._closure = (other, off, tuple(self.pos))
                else: self._closure = None
            else: self._closure = None
        if per.get("screen") == "world":
            self._screen_things(per, frame, place, size, step)
            self._tiers(per, place, self.entity_ids)
        self._record_view(per, frame, reused, place, size, step, aa, self.entity_ids)
        if per.get("screen") == "world": self.entity_tiles = {self.entity_ids[i]: p for i, p in tiles.items() if i in self.entity_ids}
        self._travelled = None
        return drift_fix

    def state(self):
        return {"pos": self.pos, "drift": self.drift, "drift_ref": self.drift_ref, "entity_tiles": self.entity_tiles,
                "held": ({**self._held, "segments": [{k: v for k, v in seg.items() if k not in ("arrival", "frame")} for seg in self._held["segments"]]} if self._held else None),
                "entity_ids": self.entity_ids, "legend": self.legend,
                "last_miss_hash": self._last_miss_hash,
                "self_spot": self.self_spot, "self_contradictions": self._self_contradictions,
                "self_from_motion": self._self_from_motion,
                "hud_changed": {k: v["changed"] for k, v in self._hud.items() if v["changed"] is not None}}

    def restore(self, state):
        self.pos, self.drift = tuple(state.get("pos", (0, 0))), tuple(state["drift"]) if state.get("drift") else None
        self._odo_px, self._prepared_segments = (0, 0), []
        self._held = state.get("held")
        self.drift_ref, self.entity_tiles = state.get("drift_ref"), state.get("entity_tiles", {})
        self.entity_ids = {int(i): v for i, v in state.get("entity_ids", {}).items()}
        self.legend = dict(state.get("legend") or {})
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
                if rec["status"] != "refuted" and rec["doors"] and rec["to"] not in paths and rec["to"] not in avoid: paths[rec["to"]] = [*paths[p], (p, rec)]; queue.append(rec["to"])
        return paths
    def beyond(self, place, screen=()):
        """What lies beyond each verified exit of place: for every place reachable through it (never back through place), its unused
        (unverified) exits and unexplored edges (untried edges into unseen cells), counted per place then summed. dead_end: place has no unexplored edge or unverified exit and
        exactly one verified exit leads to anything unexplored (through: that exit's tile). Pure: writes nothing, asks no model."""
        live = {p["id"] for p in self.mem.rows("places") if p.get("state") != "merged"}
        def here(pid, exits): return sum(x["status"] == "unverified" for x in exits), (len(unexplored(self.mem, pid)) if self.tile_px else 0)
        own = self._exits(place, live, screen)
        own_unused, own_unexplored = here(place, own)
        exits = {}
        for x in own:
            if x["status"] != "verified" or not x["dest"] or x["dest"] == place or not x["tile"]: continue
            unused = unexp = 0; labels = []
            for pid in self._paths(x["dest"], avoid={place}):
                u, e = here(pid, self._exits(pid, live)); unused += u; unexp += e; labels.append(self.place_label(pid))
            exits[tuple(x["tile"])] = {"unused_exits": unused, "unexplored": unexp, "places": labels}
        beyond = [t for t, v in exits.items() if v["unused_exits"] + v["unexplored"] > 0]
        dead_end = not own_unused and not own_unexplored and len(beyond) == 1
        return {"exits": exits, "dead_end": dead_end, "through": beyond[0] if dead_end else None}
    def canon(self, ref):
        kind, _, ident = str(ref or "").partition(":")
        if kind == "place":
            return f"place:{self.canon_place(ident)}"
        if kind == "thing":
            seen = set()
            while (row := self.mem.get("entities", ident)) and row.get("state") == "merged" and row.get("into") and ident not in seen:
                seen.add(ident); ident = row["into"]
            return f"thing:{ident}"
        return ref

    def leads_to(self, ref):
        """Observed live places entered through this canonical thing."""
        canonical = self.canon(ref)
        if not str(canonical or "").startswith("thing:"): return []
        ident = canonical.split(":", 1)[1]
        thing = self.mem.get("entities", ident)
        if not thing: return []
        own = self.canon_place(thing.get("place"))
        places = set()
        for row in self.mem.rows("relations"):
            if row.get("p") != "leads_to" or row.get("src") != "observed" or row.get("state") != "live" or row.get("sup", 0) <= row.get("con", 0): continue
            if self.canon(f"thing:{row.get('s')}") != canonical: continue
            place = self.canon_place(row.get("o"))
            target = self.mem.get("places", place)
            if place != own and target and target.get("screen") == "world" and target.get("state") != "merged": places.add(place)
        return [f"place:{place}" for place in sorted(places)]

    def known(self, ref):
        canonical = self.canon(ref)
        kind, _, ident = str(canonical or "").partition(":")
        if kind == "place": return bool(self.mem.get("places", ident))
        if kind == "thing": return (self.mem.get("places", (self.mem.get("entities", ident) or {}).get("place")) or {}).get("screen") == "world"
        if kind == "exit" and self.current_place:
            try: tile = tuple(map(int, ident.split(",")))
            except ValueError: return False
            live = {p["id"] for p in self.mem.rows("places") if p.get("state") != "merged"}
            return any(x["tile"] == tile or any(d["tile"] == tile for d in x["doors"]) for x in self._exits(self.current_place, live))
        return False

    def ref(self, text):
        text = str(text or "").strip()
        if not text: return None
        if text.startswith(("thing:", "exit:", "place:")) and self.known(text):
            ref = self.canon(text)
            return ref if not ref.startswith("place:") or (self.mem.get("places", ref.split(":", 1)[1]) or {}).get("screen") == "world" else None
        places = [p for p in self.mem.rows("places") if p.get("state") != "merged" and p.get("screen") == "world"]
        if any(p["id"] == text for p in places): return f"place:{text}"
        if self.mem.get("places", text):
            ref = self.canon(f"place:{text}")
            return ref if (self.mem.get("places", ref.split(":", 1)[1]) or {}).get("screen") == "world" else None
        maps = {p["id"] for p in places if p.get("screen") == "world"}  # things on a title or menu screen before any map are no targets
        if (row := self.mem.get("entities", text)) and row.get("place") in maps: return self.canon(f"thing:{text}")
        key = text.casefold()
        labelled = [p for p in places if self.place_label(p["id"]).casefold() == key]
        if len(labelled) == 1: return f"place:{labelled[0]['id']}"
        if (match := re.search(r"\s+\[([^\[\]]+)\]$", text)) and self.canon_place(match[1]) in maps:
            return self.canon(f"place:{match[1]}")
        named = [p for p in places if key in {n.casefold() for n in [place_name(p), p.get("called"), *place_aliases(p)] if n}]
        if len(named) == 1: return f"place:{named[0]['id']}"
        things = self._named_things(text)
        if len(things) == 1: return f"thing:{things[0]['id']}"
        if (match := re.fullmatch(r"(?:(?:thing|landmark)\s+)?(\S)", text, re.IGNORECASE)):
            return self.legend.get(match[1])
        return None

    def _named_things(self, text):  # live things on a world map whose name or alias is text
        maps = {p["id"] for p in self.mem.rows("places") if p.get("state") != "merged" and p.get("screen") == "world"}
        key = str(text or "").strip().casefold()
        things = [e for e in self.mem.rows("entities") if e.get("state") != "merged" and e.get("place") in maps
                  and key in {str(n).casefold() for n in [e.get("name"), e.get("called"), *e.get("aliases", [])] if n}]
        here = [e for e in things if e["place"] == self.current_place]
        return here if len(things) > 1 and len(here) == 1 else things  # a name shared across places means the one here

    def ties(self, text):  # a name that several things share, which ref() cannot pick: each candidate's ref with its place's label
        things = self._named_things(text)
        return [(f"thing:{e['id']}", self.place_label(e["place"])) for e in things] if len(things) > 1 else []

    def exit_route(self, ref):  # (from, to) place refs of a thing named as an exit; None until its door's destination is known
        kind, _, ident = str(self.canon(ref) or "").partition(":")
        row = self.mem.get("entities", ident) if kind == "thing" else None
        if not row or not row.get("tile") or not row.get("place"): return None
        pid, tile = self.canon_place(row["place"]), tuple(row["tile"])
        live = {p["id"] for p in self.mem.rows("places") if p.get("state") != "merged"}
        exit = next((x for x in self._exits(pid, live) if x.get("ident") == ident or x["tile"] == tile or any(d["tile"] == tile for d in x["doors"])), None)
        return (f"place:{pid}", f"place:{exit['dest']}") if exit and exit.get("dest") else None

    def is_exit(self, ref):
        kind, _, ident = str(self.canon(ref) or "").partition(":")
        row = self.mem.get("entities", ident) if kind == "thing" else None
        return bool(row and _memory_is_exit(row.get("kind"), row.get("name")))

    def history(self, ref, place=None):
        canonical = self.canon(ref)
        kind, _, ident = str(canonical or "").partition(":")
        if kind == "place":
            row = self.mem.get("places", ident)
            return {k: row[k] for k in ("visits", "first", "last") if k in row} if row else {}
        if kind == "exit":
            place = place or self.current_place
            try: tile = tuple(map(int, ident.split(",")))
            except ValueError: return {}
            live = {p["id"] for p in self.mem.rows("places") if p.get("state") != "merged"}
            exit = next((x for x in self._exits(place, live) if x["tile"] == tile or any(d["tile"] == tile for d in x["doors"])), None) if place else None
            if not exit: return {}
            row = self.mem.get("tiles", f"{place}@{tile[0]},{tile[1]}") or {}
            return {"status": exit["status"], "uses": exit["uses"], "dest": exit["dest"], "blocked": row.get("blocked", [])}
        if kind != "thing" or not (row := self.mem.get("entities", ident)): return {}
        says = [r for r in self.mem.rows("relations") if r.get("s") == ident and r.get("p") == "says"]
        latest = max(says, key=lambda r: r.get("step", 0), default=None)
        text = latest.get("text", latest.get("o") or latest.get("why")) if latest else None
        result = {"called": row.get("called"), "talked": None, "checked": None,
                  "walked_into": sum(r.get("con", 0) for r in self.mem.rows("relations")
                                      if r.get("s") == ident and r.get("p") == "does_nothing"),
                  "status": row.get("status"), "last": row.get("last")}
        if latest:
            if _actor(row): result["talked"] = {"steps": sorted({s for r in says for s in r.get("ev", [])} or {latest.get("step")}), "text": text}
            else: result["checked"] = text
        return result

    def reachable(self, ref, per, place):  # the loop's go_to test, shared so a row is offered only if it executes: resolves, and is on screen or has travel legs
        found = self.resolve(ref, per, place)
        if not found or found["kind"] == "screen": return bool(found)
        held, self.actor_tiles = self.actor_tiles, frozenset()  # an actor in the way blocks only for now: the row stays, a failed try is recorded
        try: return self.plan(found) is not None
        finally: self.actor_tiles = held
    def resolve(self, name, per, place):
        ref = self.ref(name)
        if ref:
            kind, _, ident = ref.partition(":")
            if kind == "thing":
                if not str(name).strip().startswith("thing:"):
                    screen = [(i, e) for i, e in enumerate(per.get("entities", [])) if e.get("name") and
                              (_distance(e.get("name", ""), name) <= 1 or str(e.get("called", "")).casefold() == str(name).casefold())]
                    if screen:
                        i, e = min(screen, key=lambda pair: (_distance(pair[1].get("name", ""), name), pair[0]))
                        ident = self.entity_ids.get(i)
                        return {"kind": "screen", "place": place, "tile": self.entity_tiles.get(ident), "id": ident, "stale": False}
                row = self.mem.get("entities", ident)
                if row and row.get("place") and (self.mem.get("places", row["place"]) or {}).get("state") == "merged": row = None
                if row:
                    idx = next((i for i, entity_id in self.entity_ids.items() if entity_id == ident), None)
                    if idx is not None: return {"kind": "screen", "place": place, "tile": self.entity_tiles.get(ident), "id": ident, "stale": False}
                    if row.get("tile"): return {"kind": "memory", "place": row.get("place"), "tile": tuple(row["tile"]), "id": ident, "stale": row.get("status") == "stale"}
            elif kind == "exit":
                try: tile = tuple(map(int, ident.split(",")))
                except ValueError: tile = None
                live = {p["id"] for p in self.mem.rows("places") if p.get("state") != "merged"}
                exit = next((x for x in self._exits(place, live) if tile and (x["tile"] == tile or any(d["tile"] == tile for d in x["doors"]))), None)
                if exit: return {"kind": "exit", "place": place, "tile": tile, "id": exit.get("ident"), "stale": False, "exit": exit}
            elif kind == "place": name = ident
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
        rows = [e for e in self.mem.rows("entities") if e.get("place") in live and (self.mem.get("places", e["place"]) or {}).get("screen") == "world" and e.get("tile") and matches(e)]
        if rows: e = min(rows, key=lambda e: (str(e.get("called", "")).casefold() != name.casefold(), e["place"] != place, dist.get(e["place"], 10**9), -e.get("seen", 0))); return {"kind": "memory", "place": e["place"], "tile": tuple(e["tile"]), "id": e["id"], "stale": e.get("status") == "stale"}
        rows = [p for p in self.mem.rows("places") if p.get("id") in live and any(_distance(v, name) <= 1 for v in [p.get("name", ""), *p.get("aliases", [])])]
        if not rows: return None
        p = min(rows, key=lambda p: (p["id"] != place, dist.get(p["id"], 10**9), -p.get("visits", 0))); incoming = [r for r in self.mem.rows("tiles") if r.get("place") in live and (r.get("exit") or {}).get("to") == p["id"]]
        tile = (incoming[0].get("exit") or {}).get("arrive") if incoming else next(((r["x"], r["y"]) for r in self.mem.rows("tiles") if r.get("place") == p["id"] and r.get("floor") is True), None)
        return {"kind": "place", "place": p["id"], "tile": tuple(tile) if tile else None, "id": p["id"], "stale": False}

    def _shown(self, row, base):
        called = row.get("called")
        return f"{base}, maybe {called}" if called and (row.get("called_src") or {}).get("tier") == "text" else called or base

    def place_label(self, place):
        rows = [p for p in self.mem.rows("places") if p.get("state") != "merged"]
        row = self.mem.get("places", place)
        if not row: return place
        name = place_name(row)
        if not name:  # ponytail: unnamed reads "place <n>", n = the id's numeric suffix (minted ids: place, place_2, ...)
            n = place.rsplit("_", 1)[-1]
            return self._shown(row, f"place {n if n.isdigit() else 1}")
        duplicates = sum(place_name(p).casefold() == name.casefold() for p in rows) > 1
        return self._shown(row, f"{name} [{place}]" if duplicates else name)

    def here(self, place):
        unknown = ": the scene changed under the text; where you are is not known yet" if self.unsettled else ""
        if not place: return self.place_label(place) + unknown
        place = self.canon(place if str(place).startswith("place:") else f"place:{place}")
        label = self.label(place)
        entered = []
        seen = set()
        for thing in self.mem.rows("entities"):
            ref = self.canon(f"thing:{thing['id']}")
            if ref in seen or f"thing:{thing['id']}" != ref: continue
            seen.add(ref)
            if place in self.leads_to(ref): entered.append(self.label(ref))
        hint = (self.mem.get("places", place.split(":", 1)[1]) or {}).get("looks_like")
        return (f"{label}, entered through {', '.join(entered)}" if entered else label) + (f", partly looks like {self.place_label(hint[0])} ({hint[1]})" if hint else "") + unknown

    def label(self, ref, place=None):  # place: where an exit ref's tile lies (exit refs carry no place); default the current place
        ref = self.canon(ref)
        kind, _, ident = str(ref or "").partition(":")
        if kind == "place": return self.place_label(ident)
        if kind == "thing":
            row = self.mem.get("entities", ident) or {}; name, votes = row.get("name") or self.place_label(ident), row.get("recent_names", [])
            others = [n for n, _ in Counter(v for v in votes if v != name).most_common(2)] if votes.count(name) < 3 else []  # unconfident: show the other names it was called lately
            return self._shown(row, f"{name} (or: {', '.join(others)})" if others else name)
        if kind == "exit":
            try: tile = tuple(map(int, ident.split(",")))
            except ValueError: tile = None
            live = {p["id"] for p in self.mem.rows("places") if p.get("state") != "merged"}
            exit = next((x for x in self._exits(place or self.current_place, live) if tile and (x["tile"] == tile or any(d["tile"] == tile for d in x["doors"]))), None)
            if exit: return exit["head"] or exit.get("name") or self.place_label(exit.get("dest"))
        return self.place_label(ident or ref)

    def _exits(self, pid, live, screen=()):
        """Exit records of place pid: the one source for World.options and the view. A record is a tile exit to a live place, or a thing
        named as an exit (remembered, or on screen as (index, entity, id)). Records to the same destination with tiles within 1 are one exit, and so is
        an unverified record with no destination within 1 of a verified one: the best record (crossed or observed, then a tile exit) gives its name. The representative door (crossed or observed before a guess, then row order) gives tile, dir, arrive, src; doors lists every tile exit of the group. Status: verified by departure crossings or an observed exit, refuted by blocked pushes, else unverified.
        Uses count only crossings departing through the exit's tiles. Sorted by tile row and column."""
        things = {tuple(e["tile"]): e for e in self.mem.rows("entities") if e.get("place") == pid and e.get("tile") and e.get("name")
                  and e.get("status", "remembered") != "gone" and _is_exit(e)}
        loose = []
        for i, e, ident in screen:  # on screen now: perception's name and index, the tile the world knows for it
            tile = self._tile(ident)
            row = {**(self.mem.get("entities", ident) or {}), "id": ident, "name": e["name"], "idx": i, "tile": tile}
            if tile: things[tile] = row
            else: loose.append(row)
        tiles = self.mem.rows("tiles")
        recs = []
        for r in tiles:
            edge = r.get("exit") or {}
            if r.get("place") == pid and edge.get("to") in live and edge["to"] != pid:
                tile = (r["x"], r["y"])
                recs.append({"tile": tile, "dir": edge.get("dir") or "", "dest": edge["to"], "pushed": True, "observed": edge.get("src") == "observed", "row": things.get(tile) or {},
                             "arrive": edge.get("arrive"), "src": edge.get("src")})
        covered = {rec["tile"] for rec in recs}
        for row in [*(e for t, e in things.items() if t not in covered), *loose]:
            recs.append({"tile": tuple(row["tile"]) if row.get("tile") else None, "dir": "", "dest": None, "pushed": False, "observed": False, "row": row, "placement": row.get("placement")})
        for rec in recs:
            row, dest, tile = rec["row"], rec["dest"], rec["tile"]
            rec.update(name=row.get("name"), ident=row.get("id"), idx=row.get("idx"))
            c = (self.mem.get("crossings", _cross_key(pid, tile, dest)) or {}) if tile and dest else {}
            partner = (self.mem.get("crossings", _cross_key(dest, tuple(rec["arrive"]), pid)) or {}) if dest and rec.get("arrive") else {}
            rec.update(n=c.get("n", 0) + partner.get("n", 0), last=max((v for v in (c.get("last"), partner.get("last")) if v is not None), default=None), miss=c.get("miss", 0))
            rec["alone"] = bool(rec["n"] or rec["observed"])
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
            steps = [m["last"] for m in g if m["n"] and m["last"] is not None]
            uses = max(sum(m["n"] for m in g), 1 if any(m["observed"] for m in g) else 0)
            name = best["name"] or next((m["name"] for m in g if m["name"]), None); dir_ = door["dir"]
            head = (name[5:].strip() if name.casefold().startswith("exit:") else name) if name else self.place_label(dest) if dest else dir_
            refuted = not uses and any(self._refuted(pid, m["tile"]) for m in g) or bool(doors) and all(m["miss"] >= 2 for m in doors)
            status = "refuted" if refuted else "verified" if uses else "unverified"
            out.append({"tile": door["tile"], "dir": dir_, "dest": dest, "to": dest, "place": pid, "arrive": door.get("arrive"), "src": door.get("src"),
                        "doors": [{"tile": m["tile"], "dir": m["dir"], "to": dest, "arrive": m.get("arrive"), "src": m.get("src")} for m in doors],
                        "name": name, "head": head, "idx": next((m["idx"] for m in g if m["idx"] is not None), None),
                        "ident": next((m["ident"] for m in g if m["ident"]), None), "pushed": bool(doors), "uses": uses, "last": max(steps, default=None),
                        "status": status, "placement": best["row"].get("placement")})
        return sorted(out, key=lambda x: _yx(x["tile"], x["name"] or x["dir"]))

    def view(self, goal_text, place):
        live ={p["id"] for p in self.mem.rows("places") if p.get("state") != "merged" and p.get("screen") == "world"}  # navigable places only: the run-start place never lists
        populated = {r.get("place") for r in self.mem.rows("tiles")} | {e.get("place") for e in self.mem.rows("entities")}
        places = [p for p in self.mem.rows("places") if p.get("id") in live and (p.get("visits") or p["id"] in populated)]
        def exit_text(x, far=None):  # by _exits status: verified "head -> place used N×" + what lies beyond; refuted: its label; else a guess
            if x["status"] == "refuted":
                walks = [r for r in self.mem.rows("relations") if r.get("s") == x["ident"] and r.get("p") == "does_nothing" and r.get("state") == "refuted"]
                ev = [s for r in walks for s in (r.get("ev") or [])]
                count = max([self._refuted(place, x["tile"])] + [r.get("con", 0) for r in walks])
                return f"not an exit: {x['head']} (walked into {count}×" + (f", steps {min(ev)}–{max(ev)}" if ev else "") + ")"
            to = f" -> {self.place_label(x['dest'])}" if x["dest"] else ""
            if x["status"] == "verified": return f"{x['head']}{to} used {x['uses']}×{_beyond_suffix(far)}"
            possible_to = f" → {self.place_label(x['dest'])}" if x["dest"] else ""
            return f"{x['head']}{possible_to}: possible exit, unverified (never crossed)"
        goal = str(goal_text or "").casefold()
        promoted = [e["id"] for e in self.mem.rows("entities") if e.get("place") in live and e.get("name")
                    and _base(e["name"]) in goal]
        self._promote(promoted)

        label = self.place_label(place)
        tier = lambda e: e.get("tier", "interest" if normalize_kind(e.get("kind")) in ACTOR_KINDS else "transient" if normalize_kind(e.get("kind")) in TRANSIENT else "landmark")
        tiles = [r for r in self.mem.rows("tiles") if r.get("place") == place]
        seen = self.mem.get("seen", place) or {"cells": []}
        seen_cells = set(seen.get("cells", []))
        edges = frontier(self.mem, place)
        unseen_cells = unexplored(self.mem, place)
        walked = sum(r.get("floor") is True for r in tiles)
        lines = [f"Here: {label} ({self.pos[0]},{self.pos[1]})  map: {walked} walked, {len(seen_cells)} seen, {len(edges)} untried edges ({len(unseen_cells)} unseen cells)" + (f", {past} unseen cells past open screen edges" if (past := len(open_cells(self.mem, place))) else "")]
        by_direction = Counter(d for target in unseen_cells if (d := _direction(self.pos, target)))
        if directions := ", ".join(f"{d} {by_direction[d]}" for d in ("UP", "DOWN", "LEFT", "RIGHT") if by_direction[d]): lines[0] += f"; unseen by direction: {directions}"

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
        entities = [e for e in entities if not (_is_exit(e) and self._refuted(place, e.get("tile")) > 0)]
        grouped = {}
        for e in entities:
            grouped.setdefault(_base(e.get("name", e["id"])), []).append(e)
        current_groups = {}
        for e in current_entities:
            current_groups.setdefault(_base(e.get("name", e["id"])), []).append(e)
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
        marks, legend, legend_refs, legend_lines, shown = {}, [], {}, {}, set()
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
            named = next((member for member in e["_members"] if member.get("called")), e)
            name = rec["head"] if rec else self._shown(named, e["name"]) + (f" ×{count}" if count > 1 else "")
            nearest = f" (nearest {tuple(e['tile'])})" if count > 1 else ""
            legend_line = f"{'Thing' if tier(e) == 'interest' else 'Landmark'} {symbol} {name} {tuple(e['tile'])} {normalize_kind(e.get('kind'))} {last}{nearest}{self._notes(e['id'])}"
            legend.append(legend_line)
            legend_refs[symbol] = f"thing:{(rec or {}).get('ident') or e['id']}"
            legend_lines[symbol] = legend_line
        has_look_wall = False
        for y in range(y0, y1 + 1):
            row = []
            for x in range(x0, x1 + 1):
                tile = cells.get((x, y), {})
                glyph = "@" if (x, y) == tuple(self.pos) else "E" if tile.get("exit") else None
                if glyph is None:
                    for d, (dx, dy) in DIRS.items():
                        source = cells.get((x - dx, y - dy), {})
                        if d in source.get("blocked", []):
                            glyph = "#"; break
                if glyph is None:
                    look = self.mem.get("looks", f"{place}@{x},{y}"); store = getattr(self.mem, "looks", None); learned = store.says(look["look"]) if look and store else None
                    if tile.get("floor") is not True and learned == "wall": glyph = "x"; has_look_wall = True
                    else: glyph = "." if tile.get("floor") is True else "," if f"{x},{y}" in seen_cells else " "
                row.append(glyph)
            lines.append("".join(row))
        lines.append("Map: . walked, , seen, x looks like a wall, # blocked side, E exit already used, @ you; letters mark things and landmarks (kind exit = seen, not yet used)" if has_look_wall else "Map: . walked, , seen, # blocked side, E exit already used, @ you; letters mark things and landmarks (kind exit = seen, not yet used)")
        current_facts = []
        far = self.beyond(place)  # ponytail: no screen things here; view's live set is navigable places, so a through exit missing from the table prints nothing
        for e in exit_rows:  # beyond suffix on this place's exits only; other places' exits show status alone
            current_facts.append(f"E at ({e['tile'][0]},{e['tile'][1]}) {exit_text(e, far['exits'].get(e['tile']))}")
        through = [e["head"] for e in exit_rows if e["tile"] == far["through"]] if far["dead_end"] else []
        if through: current_facts.append(f"Everything unexplored from here is through: {through[0]}")
        here = {e["id"] for e in self.mem.rows("entities") if e.get("place") == place} | set(self.entity_ids.values())
        refuted = {}  # most recent first; a thing's first 'not working' note only
        for r in sorted((r for r in self.mem.rows("relations") if r.get("p") == "does_nothing" and r.get("state") == "refuted" and r.get("s") in here), key=lambda r: -r.get("step", 0)):
            note = [n for n in self._notes(r["s"]).split("; ") if n.startswith("not working")]
            if note: refuted.setdefault(r["s"], note[0])
        if refuted: current_facts.append("Don't believe: " + "; ".join(f"{(self.mem.get('entities', s) or {}).get('name', s)} ({n})" for s, n in list(refuted.items())[:2]))
        current_detail = []
        for base, group in sorted(current_groups.items()):
            if any(e["id"] in mapped for e in group): continue
            best = max(group, key=lambda e: (status_rank.get(e.get("status", "remembered"), 0), e.get("last", 0), e["id"]))
            tile_rows = [e for e in group if e.get("tile")]
            nearest = min(tile_rows, key=lambda e: (sum(abs(e["tile"][i] - self.pos[i]) for i in (0, 1)), e["id"])) if tile_rows else None
            tile = tuple(nearest["tile"]) if nearest else "?"
            status = best.get("status", "remembered")
            if len(group) > 1:
                current_detail.append(f"Thing: {self._shown(best, base)} ×{len(group)} {status} (nearest {tile})")
            else:
                last = f"last seen {tile} step {best.get('last', 0)}; may have moved" if status == "stale" else f"{status} step {best.get('last', 0)}"
                current_detail.append(f"Thing: {self._shown(best, best.get('name', best['id']))} {tile} {normalize_kind(best.get('kind'))} {last}{self._notes(best['id'])}")

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
            other_label = self.place_label(pid)
            same = {other_label.casefold(), str(place_name(other) or "").casefold()}
            redundant = all(str(a).casefold() in same for a in aliases)
            header = [f"Place: {other_label}" + (f" aliases: {', '.join(map(str, aliases))}" if aliases and not redundant else "")]
            header.extend(f"  Exit {exit_text(e)}" for e in self._exits(pid, live))
            place_headers[pid] = header
            other_edges = frontier(self.mem, pid)
            other_unseen = len(unexplored(self.mem, pid))
            groups = {}
            for e in self.mem.rows("entities"):
                if e.get("place") == pid and tier(e) == "interest" and eligible(e):
                    groups.setdefault(_base(e.get("name", e["id"])), []).append(e)
            rendered = []
            for base, group in sorted(groups.items()):
                best = max(group, key=lambda e: (status_rank.get(e.get("status", "remembered"), 0), e.get("last", 0), e["id"]))
                tile_rows = [e for e in group if e.get("tile")]
                nearest = min(tile_rows, key=lambda e: (sum(abs(e["tile"][i] - self.pos[i]) for i in (0, 1)), e["id"])) if tile_rows else None
                named = next((e for e in group if e.get("called")), best)
                if len(group) > 1:
                    rendered.append(f"  Thing: {self._shown(named, base)} ×{len(group)} {best.get('status', 'remembered')} (nearest {tuple(nearest['tile']) if nearest else '?'})")
                else:
                    status = best.get("status", "remembered")
                    fact = f"last seen {tuple(best['tile']) if best.get('tile') else '?'} step {best.get('last', 0)}; may have moved" if status == "stale" else status
                    rendered.append(f"  Thing: {self._shown(best, best.get('name', best['id']))} {fact}{self._notes(best['id'])}")
            place_things[pid] = [*rendered, f"  Edges: {len(other_edges)} untried ({other_unseen} into unseen)"]

        # ponytail: the local legend resolves every drawn letter; raise the cap only if real maps outgrow it.
        output = []
        grid_indexes = {}
        for i, line in enumerate(lines):
            if len("\n".join((*output, line))) <= VIEW_TOKENS * 4:
                if 1 <= i <= MAP_H: grid_indexes[i - 1] = len(output)
                output.append(line)
        def add_detail_bundle(label, detail):
            if not detail or len("\n".join((*output, label, detail[0]))) > VIEW_TOKENS * 4: return
            output.extend((label, detail[0]))
            for line in detail[1:]:
                if len("\n".join((*output, line))) <= VIEW_TOKENS * 4: output.append(line)
        add_detail_bundle(f"Things here: {label}", [*legend, *current_detail])
        for line in current_facts:
            if len("\n".join((*output, line))) <= VIEW_TOKENS * 4: output.append(line)
        included = set()
        for other in others:
            pid = other["id"]
            candidate = "\n".join((*output, *place_headers[pid]))
            if len(candidate) <= VIEW_TOKENS * 4:
                output.extend(place_headers[pid]); included.add(pid)
        for other in others:
            pid = other["id"]
            if pid in included: add_detail_bundle(f"Things: {self.place_label(pid)}", place_things[pid])

        flags = list(getattr(self, "view_flags", ()))
        if self._travelled and self._travelled.status != "done": flags.append(f"travel: {self._travelled.reason}")
        for ident, hud in self._hud.items():
            row = self.mem.get("entities", ident)
            if hud["changed"] is not None and row and row.get("place") == place: flags.append(f"HUD {row['name']} changed at step {hud['changed']}")
        for line in (f"Flag: {flag}" for flag in dict.fromkeys(flags)):
            if len("\n".join((*output, line))) <= VIEW_TOKENS * 4: output.append(line)
        self.legend = {symbol: ref for symbol, ref in legend_refs.items() if legend_lines[symbol] in output}
        for y, index in grid_indexes.items():
            output[index] = "".join(symbol if c not in "@E" and (symbol := marks.get((x0 + x, y0 + y))) in self.legend else c for x, c in enumerate(output[index]))
        return "\n".join(output)
    def _tile(self, ident):
        tile = (self.entity_tiles.get(ident) or (self.mem.get("entities", ident) or {}).get("tile")) if ident else None
        return tuple(tile) if tile else None
    def options(self, per, place, fails, step, inputs=(), targets=()):
        """What can be done now, from the situation only. Pure: writes nothing, asks no model, never removes or hides an option for its history.
        Modal choices stay in their existing order. Navigable rows are exits, actors, things, explore directions, places, then free buttons."""
        ents, modal, fails = per.get("entities", []), per.get("screen") != "world", fails or {}
        live = {p["id"] for p in self.mem.rows("places") if p.get("state") != "merged"}
        def thing_ref(ident): return f"thing:{ident}" if ident else None
        def option(tool, label, action, verb=None, target=None, ref=None, ident=None, door=None, note=None, push=None):
            ref = ref or thing_ref(ident)
            key = fail_key(place, verb, target, inputs, f"{ref}>{push}" if push else ref) if verb and target is not None else None
            hit = fails.get(key) if key else None
            history = self.history(ref, place) if ref else {}
            notes = []
            if note: notes.append(note)
            for dest in self.leads_to(ref) if ref else ():
                ident = dest.split(":", 1)[1]
                uses = sum(r.get("sup", 0) for r in self.mem.rows("relations")
                           if r.get("p") == "leads_to" and r.get("src") == "observed" and r.get("state") == "live"
                           and self.canon(f"thing:{r.get('s')}") == self.canon(ref)
                           and self.canon(f"place:{r.get('o')}") == dest)
                notes.append(f"leads to {self.place_label(ident)} {uses}×")
            if hit: notes.append(f"{hit.get('why', '')} {hit.get('n', 0)}× (last step {hit.get('step', 0)})")
            reach_place, reach_tile = ((self.mem.get("entities", ident) or {}).get("place") or place, self._tile(ident)) if ident else (place, None)
            if ref and ref.startswith("exit:"):
                try: reach_tile = tuple(map(int, ref.partition(":")[2].split(",")))
                except ValueError: pass
            elif ref and ref.startswith("place:") and (found := self.resolve(ref, per, place)):
                reach_place, reach_tile = found["place"], found.get("tile")
            if reach_tile and (reach := self.mem.get("reach", f"{reach_place}@{reach_tile[0]},{reach_tile[1]}")) and reach.get("n", 0):
                at = reach.get("at", [0, 0]); notes.append(f"not reached: stopped at ({at[0]},{at[1]}) {reach['n']}× (last step {reach.get('last', 0)})")
            if history.get("talked"):
                talked = history["talked"]
                spoken = str(talked.get("text") or "")[:60]
                notes.append(f"talked step {max(talked.get('steps') or [0])}: \"{spoken}\"")
            elif history.get("checked") is not None:
                ident = str(ref or "").partition(":")[2]
                said = max((r for r in self.mem.rows("relations") if r.get("s") == ident and r.get("p") == "says"), key=lambda r: r.get("step", 0), default={})
                notes.append(f"checked step {said.get('step', 0)}: {str(history['checked'] or 'nothing')[:60]}")
            state = "failed" if hit or history.get("status") == "refuted" else "worked" if history.get("talked") or history.get("checked") is not None or history.get("uses", 0) else "new"
            return {"tool": tool, "label": label, "action": action, "notes": notes, "intent_key": key,
                    "exit": door, "ref": ref, "state": state}
        def free(label):
            hits = sorted(((f.get("step", 0), k, f) for k, f in fails.items() if k.startswith(f"{place}|") and str(f.get("act", "")).startswith("buttons ")), key=lambda t: (-t[0], t[1]))
            row = option("buttons", label, None)
            tile = self.mem.get("tiles", f"{place}@{self.pos[0]},{self.pos[1]}") or {}
            blocked = ", ".join(d for d in DIRS if d in tile.get("blocked", [])) or "none"
            failed = ", ".join(f"{f['act'][len('buttons '):]} x{f['n']}" for _, _, f in hits[:4])
            row["notes"] = [f"blocked directions: {blocked}"] + ([f"failed here: {failed}"] if failed else [])
            if self.no_response: row["notes"].append(f"your last {self.no_response} presses changed nothing on screen")
            row["state"] = "new"
            return row
        free_label = "free buttons" + (f": {', '.join(inputs)}" if inputs else "")
        if modal:
            choices = [(i, e) for i, e in enumerate(ents) if e.get("name") and e.get("selectable") is True and normalize_kind(e.get("kind")) not in FURNITURE | TYPING]
            screen = self.mem.get("screens", self.mem.screen_id(per)) or {}
            rows = []
            for i, e in sorted(choices, key=lambda ie: _yx(ie[1].get("at"), ie[1]["name"])):
                row = option("select", f"select {e['name']}", {"thing": i, "verb": "select"}, "select", e["name"], thing_ref(self.entity_ids.get(i)))
                if (replies := screen.get("replies", {}).get(e["name"])): row["notes"].append(f'chosen step {replies[-1]["step"]}: "{replies[-1]["text"]}"')
                rows.append(row)
            rows += [option("advance", "advance", {"verb": "advance"}, "advance", "", "modal:advance"), option("cancel", "cancel", {"verb": "cancel"}, "cancel", "", "modal:cancel")]
            if any(normalize_kind(e.get("kind")) in TYPING for e in ents):
                rows.append(option("type", "type text into the field", {"verb": "type"}, "type", "", "modal:type"))
                rows[-1]["notes"].append("typing costs about 3 presses per letter")
            rows.append(free(free_label))
        else:
            on = set(self.entity_ids.get(i) for i in range(len(ents))) - {None}
            exits, actors, things, remembered, explores, places = [], [], [], [], [], []
            exit_things = [(i, e, self.entity_ids.get(i)) for i, e in enumerate(ents) if e.get("name") and _is_exit(e)]
            player_at = next((e.get("at") for e in ents if e.get("kind", "").lower() == "player"), None)
            beyond = self.beyond(place, exit_things)
            for x in self._exits(place, live, exit_things):
                dest = x["dest"]
                under = x["idx"] is not None and self.size is not None and on_player(ents[x["idx"]].get("at"), player_at, self.size, self.tile_px)  # approach's own refusal test: cross by the verified push, else nothing to offer
                if x["idx"] is not None and not under: action, tool, target = {"thing": x["idx"], "verb": "approach"}, "approach", x["name"]
                elif dest: action, tool, target = {"go_to": f"place:{dest}"}, "go_to", x["name"] or self.place_label(dest)  # the verified tile + push
                else: action, tool, target = {"go_to": exit_ref(x)}, "go_to", x["name"] or self.label(exit_ref(x), place)
                tiles = sorted(tuple(d["tile"]) for d in x["doors"])
                ref = exit_ref(x)
                history = self.history(ref, place)
                suffix = _beyond_suffix(beyond["exits"].get(x["tile"]))
                if history.get("status") == "verified": note = f"crossed {history['uses']}× → {self.place_label(history['dest'])}{suffix}"
                elif history.get("status") == "refuted": note = f"exit failed: blocked {self._refuted(place, x['tile'])}×"
                else: note = "exit, never crossed"
                if not x["dir"] and x["status"] != "refuted":
                    wall = x.get("placement"); here = (self.mem.get("tiles", f"{place}@{x['tile'][0]},{x['tile'][1]}") or {}) if x["tile"] else {}
                    untried = push_dirs(self.mem, place, x["tile"], wall) if x["tile"] else list(DIRS)
                    if not x["tile"] and wall: untried = list(dict.fromkeys([outward(self.mem, place, self.pos, wall), *untried]))
                    tried = [d for d in dict.fromkeys(here.get("pushed", []) + here.get("blocked", []) + here.get("held", [])) if d in DIRS]
                    label = f"go to {self.label(ref, place)}"; key = _yx(x["tile"], label)
                    on_tile = bool(x["tile"] and self.current_place != self.mem.unplaced and tuple(self.pos) == tuple(x["tile"]))
                    if x["tile"] and not on_tile:
                        action = {"thing": x["idx"], "verb": "approach", "onto": True} if x["idx"] is not None else {"go_to": ref, "onto": True}
                        row = option("approach", f"walk onto {self.label(ref, place)}{f' ({wall})' if wall else ''}{self._where(x['tile'])}", action, "approach", target, ref, x["ident"], note="exit, never crossed")
                        row["notes"].extend(f"pressed {d}: {'blocked' if d in here.get('blocked', []) else 'someone stood there' if d in here.get('held', []) else 'moved, no exit'}" for d in tried)
                        reach = self.mem.get("reach", f"{place}@{x['tile'][0]},{x['tile'][1]}")
                        row["state"] = "failed" if row["intent_key"] in fails or reach and reach.get("n", 0) > 0 or tried else "new"  # a tile whose pushes already failed is no untried door
                        exits.append((key, row)); continue
                    for direction in [*untried, *tried] if untried else [None]:
                        fact = "all four pushes tried" if direction is None else f"pressed {direction}: blocked" if direction in here.get("blocked", []) else f"pressed {direction}: someone stood there" if direction in here.get("held", []) else f"pressed {direction}: moved, no exit" if direction in here.get("pushed", []) else "untried"
                        text = f"{label}{f' ({wall})' if wall else ''}{self._where(x['tile'])}, then press {direction}" if direction else f"{label}, all four pushes tried"
                        if direction and x["tile"]:
                            dx, dy = DIRS[direction]; neighbour = self.mem.get("tiles", f"{place}@{x['tile'][0]+dx},{x['tile'][1]+dy}") or {}
                            facts = [f"toward the {wall}"] if wall and direction == outward(self.mem, place, x["tile"], wall) else []
                            facts.append(f"onto a known exit to {self.place_label(neighbour['exit'].get('to'))}" if neighbour.get("exit") else "back onto floor you walked" if neighbour.get("floor") is True else "onto ground not yet seen")
                            text += f": {', '.join(facts)}"
                        if x["tile"] and on_tile: text = f"press {direction} here: {', '.join(facts)}" if direction else "all four pushes tried here"
                        row = option(tool, text, {**action, "push": direction} if direction else action, "approach", target, ref, x["ident"], note="exit, never crossed", push=direction)
                        if direction in tried: row["notes"].insert(1, fact)
                        row["state"] = "failed" if direction is None or direction in tried or row["intent_key"] in fails else "new"
                        exits.append((key, row))
                    continue
                label = f"{'go through' if history.get('uses') else 'go to'} {self.label(ref, place)}{self._where(x['tile'])}"
                door = {"tile": list(tiles[0]), "to": dest} if tiles else None
                resolved = self.resolve(action["go_to"], per, place) if action.get("go_to") else None
                row = option(tool, label, action, "approach", target, ref, x["ident"], door, note)
                if action.get("go_to") and self.plan(resolved, exit=door) is None: row["notes"].append("no known route from here (remembered walls block every path)")
                if beyond["dead_end"] and x["tile"] == beyond["through"]: row["notes"].insert(0, "everything unexplored from here is through this exit")
                exits.append((_yx(x["tile"], label), row))
            plain_groups, listed = {}, set()
            candidates = [(i, e, self.entity_ids.get(i)) for i, e in enumerate(ents)]
            rect = self._view_rect(self.pa, self.size) if self.size else None
            if rect:
                x0, y0, x1, y1 = rect
                candidates.extend((-1, r, r["id"]) for r in self.mem.rows("entities")
                                  if r.get("place") == place and r.get("tile") and x0 <= r["tile"][0] <= x1 and y0 <= r["tile"][1] <= y1
                                  and r["id"] not in on and r["id"] not in listed and r.get("state") != "merged" and r.get("status") != "gone"
                                  and normalize_kind(r.get("kind")) not in TRANSIENT | ACTOR_KINDS and not _actor(r) and not _is_exit(r))
            for i, e, ident in candidates:
                kind, name = normalize_kind(e.get("kind")), e.get("name")
                if not name or kind in TRANSIENT or _is_exit(e) or (ident and ident in listed): continue  # two perceptions of one thing (one tile, one id) are one row
                if i >= 0 and self.size is not None and on_player(e.get("at"), player_at, self.size, self.tile_px): continue
                listed.add(ident)
                ref, where = thing_ref(ident), self._where(self._tile(ident))
                history = self.history(ref, place) if ref else {}
                target = name
                key = fail_key(place, "interact", target, inputs, ref)
                if kind not in ACTOR_KINDS and not history.get("talked") and not history.get("checked") and not history.get("walked_into") and key not in fails:
                    plain_groups.setdefault(name.casefold(), []).append((i, e, ident, ref, where))
                    continue
                label = f"talk to {self.label(ref)}{where}" if kind in ACTOR_KINDS else f"check {self.label(ref)}{where}"
                row = option("interact", label, {"thing": i if i >= 0 else ref, "verb": "interact"}, "interact", target, ref, ident)
                (actors if kind in ACTOR_KINDS else things).append((_yx(self._tile(ident) or e.get("at"), name), row))
            for group in plain_groups.values():
                group.sort(key=lambda item: (sum(abs(a - b) for a, b in zip(self._tile(item[2]) or self.pos, self.pos)), _yx(self._tile(item[2]), item[1].get("name"))))
                i, e, ident, ref, where = group[0]
                name = self.label(ref) if ref else e["name"]
                shown = f"{name} (nearest of {len(group)})" if len(group) > 1 else name
                label = f"check {shown}{where}"
                row = option("interact", label, {"thing": i if i >= 0 else ref, "verb": "interact"}, "interact", e["name"], ref, ident)
                things.append((_yx(self._tile(ident) or e.get("at"), name), row))
            actors = [row for _, row in sorted(actors, key=lambda t: t[0])]
            things = [row for _, row in sorted(things, key=lambda t: t[0])]
            far_groups, goal = {}, {self.canon(t) for t in targets}
            for r in self.mem.rows("entities"):
                if r.get("place") != place or not r.get("tile") or not r.get("name") or r["id"] in on or r["id"] in listed or r.get("status") == "gone" or normalize_kind(r.get("kind")) in TRANSIENT or _is_exit(r): continue
                ref = f"thing:{r['id']}"
                history = self.history(ref, place)
                if not history.get("talked") and history.get("checked") is None and not history.get("walked_into") and fail_key(place, "approach", ref, inputs, ref) not in fails:
                    if not _actor(r) and self.canon(ref) not in goal: continue  # user decision 10-19: an off-screen static thing with no history is no row; walking or explore shows it again
                    far_groups.setdefault(self.label(ref).casefold(), []).append(r)  # like on-screen plain things: one row per name, the nearest member
                    continue
                far_groups[ref] = [r]  # history or a failure: its own row, with its notes
            for group in far_groups.values():
                group.sort(key=lambda r: (sum(abs(a - b) for a, b in zip(r["tile"], self.pos)), _yx(r["tile"], r["id"])))
                r = next((m for m in group if self.reachable(f"thing:{m['id']}", per, place)), None)  # offered = executable: the loop runs exactly resolve + plan
                if r is None: continue
                tile, ref = tuple(r["tile"]), f"thing:{r['id']}"
                shown = f"{self.label(ref)} (nearest of {len(group)})" if len(group) > 1 else self.label(ref)
                label = f"go to {shown}, not on screen{self._where(tile)}" + (f", last seen {step - r.get('last', step)} steps ago" if self._actor(r["id"], r) else "")
                row = option("go_to", label, {"go_to": ref}, "approach", ref, ref, r["id"])
                remembered.append((_yx(tile, r["name"]), row))
            remembered = [row for _, row in sorted(remembered, key=lambda t: t[0])]
            for direction, route in sorted(self.explore_dirs(place).items(), key=lambda item: _yx(item[1]["to"], item[0])):
                label = f"explore {direction}, {route.get('steps', len(route['dirs']))} steps, {route.get('unseen', 0)} unexplored cells that way"
                ref = f"explore:{place}:{direction.casefold()}"
                explores.append(option("explore", label, {"go_to": "unexplored", "dir": direction}, "approach", "unexplored", ref))
            for p in self.mem.rows("places"):
                pid = p["id"]
                if pid == place or pid not in live or p.get("screen") != "world": continue
                label = self.place_label(pid)
                found = self.resolve(label, per, place)
                legs = self.plan(found) if found and found["kind"] == "place" and found["tile"] else None
                if not legs: continue
                first = legs[0]
                exit_tile = first.get("exit_tile")
                exit = next((x for x in self._exits(first["place"], live) if any(d["tile"] == exit_tile for d in x["doors"])), None) if exit_tile else None
                via = self.label(exit_ref(exit), first["place"]) if exit else first["push"]
                ref = f"place:{pid}"
                place_label_ = f"go to {label}, via {via}"
                places.append((_yx(found["tile"], label), option("go_to", place_label_, {"go_to": ref}, "approach", label, ref)))
            places = [row for _, row in sorted(places, key=lambda t: t[0])]
            nav = [row for _, row in sorted(exits, key=lambda t: t[0])] + actors + things + remembered + explores + places
            rows = sorted(nav, key=lambda r: ("new", "worked", "failed").index(r["state"])) + [free(free_label)]  # stable: state groups, order kept within
        return [{"n": n, **row} for n, row in enumerate(rows, 1)]
    def occupied(self, place):  # actors hold tiles of the current place only; another place's actors are stale
        return self.actor_tiles if place == self.current_place else frozenset()
    def explore_dirs(self, place):
        if not self.tile_px: return {}
        span = [max(1, self.size[i] // self.tile_px // 2) for i in (0, 1)] if self.size else [4, 4]
        candidates, routes = unexplored(self.mem, place), {}
        unseen = Counter(d for target in candidates if (d := _direction(self.pos, target)))
        for target in sorted(candidates):
            direction = _direction(self.pos, target)
            if not direction: continue
            dirs = route(self.mem, place, self.pos, target, occupied=self.occupied(place))
            if dirs is not None and (direction not in routes or (len(dirs), target) < (len(routes[direction][0]), routes[direction][1])):
                routes[direction] = (dirs, target)
        return {d: {"dirs": dirs + [d] * span[0 if d in ("LEFT", "RIGHT") else 1], "steps": len(dirs), "unseen": unseen[d], "to": target}
                for d, (dirs, target) in routes.items()}
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
    def plan(self, target, exit=None, push=None, onto=False):  # exit: the chosen exit ({"tile", "to"}) travels through its own door
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
        if target["kind"] == "exit":
            if onto:
                legs.append({"place": p, "dirs": dirs, "push": None, "expect": None, "target": target})
                return legs
            exit_record = target.get("exit") or {}
            push = push or next((door.get("dir") for door in exit_record.get("doors", []) if tuple(door["tile"]) == tile and door.get("dir")), None) or exit_record.get("dir") or next(iter(push_dirs(self.mem, p, tile, exit_record.get("placement"))), None)
            if push is None: return None
            if exit_record.get("to"): legs.append({"place": p, "dirs": dirs + [push], "push": None, "expect": exit_record["to"], "exit_tile": tile, "target": target})
            else: legs.append({"place": p, "dirs": dirs, "push": push, "expect": None, "cross": True, "exit_tile": tile, "target": target})
        else: legs.append({"place": p, "dirs": dirs + ([facing] if facing else []), "push": None, "expect": None, "target": target})
        return legs
    def travel(self, ctx, legs, step, trace=None):
        results, inputs, place, bumps = [], 0, self.current_place, 0; self._travel_cut = self._travel_open = False; self._travel_push = self._travel_push_from = None; self._travel_bumps = 0; self._travel_segments = []; self._travel_screen_leg = len(legs) == 1 and (legs[0].get("target") or {}).get("kind") == "screen"  # open: a cut travel could not place; the loop re-identifies
        if self._odo_screen is None: self._odo_screen = {"screen": (self.mem.get("places", place) or {}).get("screen")}
        for n, leg in enumerate(legs, 1):
            if inputs >= TRAVEL_MAX_INPUTS: break
            if leg["place"] != place: return self._travel_result(results, "blocked", f"leg {n} unexpected place", ctx)
            current = leg
            while True:
                result = approach(replace(ctx, max_inputs=min(ctx.max_inputs, TRAVEL_MAX_INPUTS-inputs)), self.pa, self.pa,
                                  exit_target="exit" if current.get("expect") or current.get("cross") else None, route={"dirs": current["dirs"], "push": current.get("push")})
                if result.failure: result = replace(result, failure={**result.failure, "place": place})
                self._travel_push = current.get("push") or (current["dirs"][-1] if current.get("expect") and current["dirs"] else None)
                results.append(result); inputs += result.inputs
                # the skill checks every press for a cut; first vs last frame of a long walk is a scroll, not a cut (m1_decide2 replay step 42)
                through = getattr(result.after, "info", {}).get("frame_seq", self.odometer.seq)
                self._travel_segments = self._odometer_drain(through, place, step, result.moves)
                is_cut = result.reason == "scene cut" or result.blank_seen or any(segment.get("cut") for segment in self._travel_segments); self._travel_cut |= is_cut
                for segment in self._travel_segments[:1] if is_cut else self._travel_segments: self._odometer_fold(segment, place, step)
                self._travel_push_from = tuple(result.moves[-1].get("_tile", self.pos)) if result.moves and isinstance(result.moves[-1], dict) else self.pos
                if result.reason == "no response" and not is_cut: return self._travel_result(results, "blocked", f"leg {n} no response", ctx)
                target = current.get("target") or {}; target_tile = target.get("tile"); directions = [m.get("dir") if isinstance(m, dict) else m[0] for m in result.moves]
                screen_facing = not current.get("push") and not current.get("cross") and target.get("kind") == "screen" and target_tile and current["dirs"] and directions == current["dirs"] and tuple(self.pos[i] + DIRS[current["dirs"][-1]][i] for i in (0, 1)) == tuple(target_tile)
                if result.reason != "route blocked" or is_cut or not result.failure or result.failure.get("stage") != "route" or self._facing_target(current, result) or screen_facing: break
                bumps += 1; self._travel_bumps = bumps
                if bumps >= BUMPS or inputs >= TRAVEL_MAX_INPUTS: break
                target = current.get("exit_tile") or (current.get("target") or {}).get("tile")
                if target is None: break
                target = tuple(target); target_kind = (current.get("target") or {}).get("kind")
                if target_kind == "memory" or not current.get("push") and target in self.occupied(place):
                    found = self.beside(place, self.pos, target)
                    dirs = found[2] + [found[1]] if found else None
                else: dirs = route(self.mem, place, self.pos, target, occupied=self.occupied(place))
                if dirs is None: break
                if current.get("expect") and not current.get("push") and current["dirs"]: dirs = dirs + [current["dirs"][-1]]
                current = {**current, "dirs": dirs}
            if leg.get("expect"):
                if not is_cut and result.failure and result.failure.get("stage") == "route": return self._travel_result(results, "blocked", f"leg {n} route blocked", ctx)  # the route never reached the exit: not an exit mismatch
                arrival = self._arrival(result.after, trace) if trace else None
                cut_dir = leg.get("push") or next((m.get("dir") if isinstance(m, dict) else m[0] for m in reversed(result.moves) if m), None)
                for segment in self._travel_segments[1:]: segment["_place"], segment["_screen"] = leg["expect"], {"screen": (self.mem.get("places", leg["expect"]) or {}).get("screen")}
                edge = exit_to(self.mem, place, tuple(leg["exit_tile"]), cut_dir)
                if not is_cut:
                    key = _cross_key(place, tuple(leg["exit_tile"]), leg["expect"]); row = self.mem.get("crossings", key) or {"id": key, "n": 0}
                    self.mem.upsert("crossings", {**row, "miss": row.get("miss", 0) + 1}); push_failed(self.mem, place, tuple(leg["exit_tile"]), self._travel_push, step)
                    return self._travel_result(results, "blocked", f"leg {n} exit mismatch", ctx)
                if arrival and (arrival[2] in ("dialogue", "menu") or (self.mem.get("places", arrival[0]) or {}).get("screen") in ("dialogue", "menu")): return self._travel_result(results, "blocked", f"leg {n} modal opened", ctx)
                wrong = arrival and arrival[0] != leg["expect"]
                mismatch = wrong or not edge or edge.get("to") != leg["expect"] or arrival and tuple(edge["arrive"]) != arrival[1]
                if mismatch:
                    if not arrival: self._travel_open = True; return self._travel_result(results, "blocked", f"leg {n} exit mismatch", ctx)
                    mark_exit(self.mem, place, tuple(leg["exit_tile"]), cut_dir, *arrival[:2], step); self._cross(place, tuple(leg["exit_tile"]), arrival[0], arrival[1], step)
                    self.pos, place = arrival[:2][1], arrival[0]; self._land(place)
                    if len(self._travel_segments) > 1: self._odometer_fold(self._travel_segments[-1], place, step); self._odo_stats["dropped"] += max(0, len(self._travel_segments) - 2)
                    self._travel_segments = []
                    return self._travel_result(results, "blocked", f"leg {n} {'arrival' if wrong or edge and edge.get('to') == leg['expect'] else 'exit'} mismatch", ctx)
                if not arrival and edge.get("src") == "inferred":
                    self.mem.match_place({"screen": "world"}, step, prev=place, cut=True, exit=(*leg["exit_tile"], edge.get("dir")))
                    arrive = self._locate(leg["expect"], result.after)
                else: arrive = arrival[1] if arrival else tuple(edge["arrive"])
                self._cross(place, tuple(leg["exit_tile"]), leg["expect"], arrive, step)
                self.pos, place = arrive, leg["expect"]
                self._land(place)
                if len(self._travel_segments) > 1: self._odometer_fold(self._travel_segments[-1], place, step); self._odo_stats["dropped"] += max(0, len(self._travel_segments) - 2)
                self._travel_segments = []
            elif result.status != "done" and not (result.reason == "route blocked" and (self._facing_target(current, result) or screen_facing)):
                return self._travel_result(results, result.status, f"leg {n} {result.reason or result.status}", ctx)
            if is_cut and not leg.get("expect"): self._travel_open = True; return self._travel_result(results, "done", f"leg {n} scene cut", ctx)  # a real cut, not a failure; the loop re-identifies the place
        self.current_place = place
        if inputs >= TRAVEL_MAX_INPUTS and len(results) < len(legs): return self._travel_result(results, "blocked", f"leg {len(results)+1} input limit", ctx)
        return self._travel_result(results, "done", "", ctx)
    def _arrival(self, image, trace):
        if not trace.path.exists(): return None
        digest = hashlib.sha256(image.tobytes()).hexdigest(); live = {p["id"] for p in self.mem.rows("places") if p.get("state") != "merged"}; rows = map(json.loads, trace.path.open(encoding="utf8"))
        matches = {(row["place"], tuple(prior["tile"]), row.get("screen")) for prior, row in pairwise(rows) if row.get("frame_hash") == digest and prior.get("place") in live and row.get("place") in live and prior.get("next_place") == row["place"] and prior.get("tile")}; return next(iter(matches)) if len(matches) == 1 else None
    def _land(self, place):
        self.current_place, self.drift, self.drift_ref = place, None, None; self._odo_px = (0, 0); self.entity_tiles, self.entity_ids, self.actor_tiles = {}, {}, frozenset()
    def _facing_target(self, leg, result):
        target = leg.get("target"); dx, dy = DIRS[leg["dirs"][-1]] if leg["dirs"] else (0, 0)
        if not target or target.get("kind") != "memory" or [m.get("dir") if isinstance(m, dict) else m[0] for m in result.moves] != leg["dirs"]: return False
        return (self.pos[0]+dx, self.pos[1]+dy) == tuple(target["tile"])
    def _travel_result(self, results, status, reason, ctx, failure=None):
        moves = [m for r in results for m in r.moves]
        # a done travel has no failure: an accepted final facing press is blocked in its skill but not a failure here
        failure = (failure or (results[-1].failure if results else None)) if status == "blocked" else None
        if status == "blocked" and getattr(self, "_travel_screen_leg", False) and reason.startswith("leg 1 "): reason = reason[6:]
        if failure:
            step_dir = DIRS.get(failure.get("direction")); ahead = (self.pos[0] + step_dir[0], self.pos[1] + step_dir[1]) if step_dir else None
            blockers = [i for i, t in self.entity_tiles.items() if ahead and tuple(t) == ahead]
            failure = {**failure, "tile": tuple(self.pos), **({"blocker_id": blockers[0]} if len(blockers) == 1 else {}), **({"bumps": BUMPS} if getattr(self, "_travel_bumps", 0) >= BUMPS else {})}
        screen_leg = getattr(self, "_travel_screen_leg", False); reason = "scene cut" if screen_leg and status == "done" and reason.endswith("scene cut") else f"travel: {reason}" if reason and not (status == "blocked" and screen_leg) else reason
        self._travelled = SkillResult(status, reason, sum(r.inputs for r in results), sum(r.calls for r in results), results[0].before if results else ctx.env.screenshot(), results[-1].after if results else ctx.env.screenshot(), tuple(map(sum, zip(*(r.scroll for r in results)))) if results else (0, 0), any(r.blank_seen for r in results), moves, next((m.get("self_at") for m in reversed(moves) if isinstance(m, dict) and m.get("self_at") is not None), None), failure, settled=all(r.settled is not False for r in results)); self._travelled.bumps = getattr(self, "_travel_bumps", 0); return self._travelled
    def name(self, place, name, step):
        row, name = self.mem.get("places", place), str(name or "").strip()
        if row and row.get("screen") == "world" and _nameable(name): self.mem.name_place(place, name, step)

    def merge(self, src, dst, step, offset):
        if src == dst: return True
        dx, dy = offset
        for row in self.mem.rows("crossings"):
            origin, other = row["id"].split(">", 1); place, tile = origin.split("@", 1)
            if place != src and other != src: continue
            x, y = map(int, tile.split(",")); key = _cross_key(dst if place == src else place, (x + dx, y + dy) if place == src else (x, y), dst if other == src else other)
            saved = self.mem.get("crossings", key) or {}
            self.mem.upsert("crossings", {**row, "id": key, "n": row.get("n", 0) + saved.get("n", 0), "last": max(row.get("last", 0), saved.get("last", 0))})
        entities = self.mem.rows("entities")
        left = [e for e in entities if e.get("place") == src and e.get("state") != "merged"]
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
        targets, redirects, used = [e for e in entities if e.get("place") == dst and e.get("state") != "merged"], {}, set()
        for e in left:
            tile = [e["tile"][0] + dx, e["tile"][1] + dy] if e.get("tile") else None
            duplicate = next((r for r in targets if r["id"] not in used and _actor(r) == _actor(e) and tile and r.get("tile")
                              and max(abs(tile[i] - r["tile"][i]) for i in (0, 1)) <= STATIC_GATE), None)
            if duplicate:
                saved = self.mem.get("entities", duplicate["id"])
                self.mem.upsert("entities", {**saved, "seen": saved.get("seen", 0) + e.get("seen", 0), "last": max(step, saved.get("last", step)),
                    "called": saved.get("called") or e.get("called"),
                    "aliases": list(dict.fromkeys([*saved.get("aliases", []), *e.get("aliases", []), *([e["name"]] if e.get("name") != saved.get("name") else [])])),
                    "kinds": list(dict.fromkeys([*saved.get("kinds", []), *e.get("kinds", []), *([e["kind"]] if e.get("kind") != saved.get("kind") else [])]))})
                self.mem.upsert("entities", {**e, "state": "merged", "into": duplicate["id"]}); used.add(duplicate["id"])
                redirects[e["id"]] = duplicate["id"]
            else:
                moved = {**e, "place": dst, **({"tile": tile} if tile else {}), "last": step}
                self.mem.upsert("entities", moved); targets.append(moved)
        for r in self.mem.rows("relations"):
            s, o = redirects.get(r.get("s"), dst if r.get("s") == src else r.get("s")), redirects.get(r.get("o"), dst if r.get("o") == src else r.get("o"))
            if (s, o) != (r.get("s"), r.get("o")): self.mem.upsert("relations", {**r, "s": s, "o": o})
        p = self.mem.get("places", src)
        self.mem.upsert("places", {**p, "state": "merged", "into": dst, "offset": [dx, dy]})
        if self.current_place == src: self.pos = (self.pos[0] + dx, self.pos[1] + dy); self._odo_px = (0, 0); self.current_place = dst
        return True
