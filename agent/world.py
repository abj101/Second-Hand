"""Spatial state and bookkeeping for the act path."""
import hashlib, json
from dataclasses import replace
from itertools import pairwise
from statistics import median
from agent.memory import _base, _distance, normalize_kind
from agent.observer import cut
from agent.space import DIRS, exit_to, fold, mark_exit, route, tile_of
from agent.skills import SkillResult, approach

TRAVEL_MAX_INPUTS = 120

# ponytail: kinds stand in until W4 classifies anchors from motion.
ACTOR_KINDS = {"person", "npc", "character", "enemy", "monster", "animal"}

class World:
    def __init__(self, mem, tile_px):
        self.mem, self.tile_px = mem, tile_px
        self.pos, self.drift, self.drift_ref = (0, 0), None, None
        self.entity_tiles, self.entity_ids, self.anchors, self.actor_tiles = {}, {}, [], frozenset()
        self.entities, self.pa, self._prepared = [], None, None
        self.current_place, self.merge_pending, self.new_arrival = None, None, False
        self._travelled = None
    def _anchor(self, per, size):
        h = self.anchors
        p = next((e.get("at") for e in per.get("entities", []) if e.get("kind", "").casefold() == "player"), None)
        mid = lambda: h[0] if len(h) < 3 else [sorted(x[i] for x in h)[len(h) // 2] for i in (0, 1)]
        if p and self.tile_px and per.get("screen") == "world" and (not h or max(abs(p[i] - mid()[i]) * size[i] for i in (0, 1)) <= 1.5 * self.tile_px): h.append(list(p)); del h[:-9]
        return mid() if h else p
    def before(self, per, place, prior, size, step):
        self.current_place = place
        if prior and place != prior: self.pos, self.drift, self.drift_ref = (0, 0), None, None
        player = next((e for e in per.get("entities", []) if e.get("kind", "").casefold() == "player"), None)
        pa = self._anchor(per, size) if player else None
        self.pa = pa
        proj = lambda at: tile_of(at, pa, self.pos, size, self.tile_px) if pa and self.tile_px and isinstance(at, list) and len(at) == 2 else None
        tiles = {i: t for i, e in enumerate(per.get("entities", [])) if e.get("kind", "").casefold() != "player" and (t := proj(e.get("at")))}
        self.actor_tiles = frozenset(tiles[i] for i, e in enumerate(per.get("entities", [])) if i in tiles and normalize_kind(e.get("kind")) in ACTOR_KINDS)
        ids = self.mem.observe(per, place, step, tiles)
        self.entity_ids, self.entity_tiles = ids, {ids[i]: p for i, p in tiles.items() if i in ids}
        self.entities = [{"id": ids.get(i), "name": e.get("name"), "kind": e.get("kind"), "at": e.get("at")}
                         for i, e in enumerate(per.get("entities", []))]
        return proj, tiles, ids
    def prepare(self, skill, place, scene_cut, step):
        if skill is self._travelled: self._prepared = (skill, place, step, None, self.pos); return {"cut_dir": None, "exit_key": None}
        moves = getattr(skill, "moves", []); move_rows = moves[:-1] if scene_cut and moves else moves
        if self.tile_px: self.pos = fold(self.mem, place, self.pos, move_rows, self.tile_px, step, actors=self.actor_tiles)
        cut_dir = moves[-1][0] if scene_cut and moves else None
        self._prepared = (skill, place, step, cut_dir, self.pos)
        return {"cut_dir": cut_dir, "exit_key": (*self.pos, cut_dir) if cut_dir and self.tile_px else None}
    def after(self, skill, place, next_place, scene_cut, step):  # call after prepare() for the same step
        if skill is self._travelled: self.current_place = next_place; return {"known_exit": None, "scroll": list(skill.scroll) if skill.scroll is not None else None}
        cut_dir = self._prepared[3]
        known_exit = exit_to(self.mem, place, self.pos, cut_dir) if scene_cut and cut_dir else None
        if scene_cut and next_place != place:
            self.pos = tuple(known_exit["arrive"]) if known_exit else (0, 0)
            self.drift = self.drift_ref = None
            self.entity_tiles = self.entity_ids = {}
        return {"known_exit": known_exit,
                "scroll": list(skill.scroll) if skill.scroll is not None else None}
    def mark_exit(self, place, direction, to, arrive, step):
        mark_exit(self.mem, place, self._prepared[4], direction, to, arrive, step)
    def observe_after(self, per, frame, reused, place, size, step):
        player = next((e for e in per.get("entities", []) if e.get("kind", "").casefold() == "player"), None)
        aa = self._anchor(per, size) if player else None
        tiles = {i: tile_of(e["at"], aa, self.pos, size, self.tile_px) for i, e in enumerate(per.get("entities", []))
                 if aa and e.get("kind", "").casefold() != "player" and isinstance(e.get("at"), list) and self.tile_px}
        drift_fix = None
        if not reused and place and self.tile_px:
            old = {(e.get("name", "").casefold(), e.get("kind")): self.entity_ids[i] for i, e in enumerate(self.entities)
                   if i in self.entity_ids and sum((x.get("name", "").casefold(), x.get("kind")) == (e.get("name", "").casefold(), e.get("kind")) for x in self.entities) == 1}
            new = {(e.get("name", "").casefold(), e.get("kind")): i for i, e in enumerate(per.get("entities", [])) if i in tiles and sum((x.get("name", "").casefold(), x.get("kind")) == (e.get("name", "").casefold(), e.get("kind")) for x in per.get("entities", [])) == 1}
            pairs = {old[k]: new[k] for k in old.keys() & new.keys() if old[k] in self.entity_tiles}; anchor = self.drift_ref or {i: self.entity_tiles[i] for i in pairs}
            delta = [(tuple(anchor[i][j] - tiles[n][j] for j in (0, 1)), i) for i, n in pairs.items() if i in anchor]
            if delta:
                dx, dy = (sorted(v[0][j] for v in delta)[len(delta) // 2] for j in (0, 1)); drift = (dx, dy) if (dx, dy) != (0, 0) else None
                if drift and drift == self.drift: self.pos = (self.pos[0] + dx, self.pos[1] + dy); drift_fix = (dx, dy); tiles = {i: (p[0] + dx, p[1] + dy) for i, p in tiles.items()}; self.drift = self.drift_ref = None
                else: self.drift, self.drift_ref = drift, anchor if drift else None
        self.entity_ids = self.mem.observe(per, place, step, tiles)
        self.entity_tiles = {self.entity_ids[i]: p for i, p in tiles.items() if i in self.entity_ids}; self._travelled = None
        return drift_fix

    def state(self):
        return {"pos": self.pos, "drift": self.drift, "drift_ref": self.drift_ref, "entity_tiles": self.entity_tiles,
                "entity_ids": self.entity_ids, "anchors": self.anchors}

    def restore(self, state):
        self.pos, self.drift = tuple(state.get("pos", (0, 0))), tuple(state["drift"]) if state.get("drift") else None
        self.drift_ref, self.entity_tiles = state.get("drift_ref"), state.get("entity_tiles", {})
        self.entity_ids, self.anchors = {int(i): v for i, v in state.get("entity_ids", {}).items()}, state.get("anchors", [])
    def _paths(self, start):
        live = {p["id"] for p in self.mem.rows("places") if p.get("state") != "merged"}; links = {}
        for r in self.mem.rows("tiles"):
            edge = r.get("exit") or {}
            if r.get("place") in live and edge.get("to") in live: links.setdefault(r["place"], []).append((r, edge))
        paths, queue = {start: []}, [start]
        for p in queue:
            for row, edge in links.get(p, ()):
                if edge["to"] not in paths: paths[edge["to"]] = [*paths[p], (p, row, edge)]; queue.append(edge["to"])
        return paths
    def resolve(self, name, per, place):
        name = str(name or "").strip().removeprefix("exit:").strip()
        if not name: return None
        live = {p["id"] for p in self.mem.rows("places") if p.get("state") != "merged"}; dist = {p: len(path) for p, path in self._paths(place).items()}
        screen = [(i, e) for i, e in enumerate(per.get("entities", [])) if e.get("name") and _distance(e["name"], name) <= 1]
        if screen: i, e = min(screen, key=lambda pair: (_distance(pair[1]["name"], name), -(self.mem.get("entities", self.entity_ids.get(pair[0], "")) or {}).get("seen", 0))); ident = self.entity_ids.get(i); return {"kind": "screen", "place": place, "tile": self.entity_tiles.get(ident), "id": ident, "stale": False}
        rows = [e for e in self.mem.rows("entities") if e.get("place") in live and e.get("tile") and e.get("status") != "gone" and _distance(e.get("name", ""), name) <= 1]
        if rows: e = min(rows, key=lambda e: (e["place"] != place, dist.get(e["place"], 10**9), -e.get("seen", 0))); return {"kind": "memory", "place": e["place"], "tile": tuple(e["tile"]), "id": e["id"], "stale": e.get("status") == "stale"}
        rows = [p for p in self.mem.rows("places") if p.get("id") in live and any(_distance(v, name) <= 1 for v in [p.get("name", ""), *p.get("aliases", [])])]
        if not rows: return None
        p = min(rows, key=lambda p: (p["id"] != place, dist.get(p["id"], 10**9), -p.get("visits", 0))); incoming = [r for r in self.mem.rows("tiles") if r.get("place") in live and (r.get("exit") or {}).get("to") == p["id"]]
        tile = (incoming[0].get("exit") or {}).get("arrive") if incoming else next(((r["x"], r["y"]) for r in self.mem.rows("tiles") if r.get("place") == p["id"] and r.get("floor") is True), None)
        return {"kind": "place", "place": p["id"], "tile": tuple(tile) if tile else None, "id": p["id"], "stale": False}
    def plan(self, target):
        if not target or not target.get("tile") or not self.current_place or not self.tile_px: return None
        path = self._paths(self.current_place).get(target["place"])
        if path is None: return None
        legs, pos = [], self.pos
        p = self.current_place
        for p, row, edge in path:
            tile = row["x"], row["y"]; dirs = route(self.mem, p, pos, tile)
            if dirs is None: return None
            legs.append({"place": p, "dirs": dirs, "push": edge["dir"], "expect": edge["to"], "exit_tile": tile, "arrive": tuple(edge["arrive"])}); pos, p = tuple(edge["arrive"]), edge["to"]
        tile = tuple(target["tile"]); facing = None
        if target["kind"] == "memory":
            rows = {(r["x"], r["y"]): r for r in self.mem.rows("tiles") if r.get("place") == p}; near = []
            for d, (dx, dy) in DIRS.items():
                t = tile[0]-dx, tile[1]-dy
                if rows.get(t, {}).get("floor") is False: continue
                if (dirs := route(self.mem, p, pos, t)) is not None: near.append((t, d, dirs))
            if not near: return None
            tile, facing, dirs = min(near, key=lambda q: (abs(q[0][0]-pos[0])+abs(q[0][1]-pos[1]), q[1]))
        else: dirs = route(self.mem, p, pos, tile)
        if dirs is None: return None
        legs.append({"place": p, "dirs": dirs + ([facing] if facing else []), "push": None, "expect": None, "target": target}); return legs
    def travel(self, ctx, legs, step, trace=None):
        results, inputs, place = [], 0, self.current_place; self._travel_cut = False
        for n, leg in enumerate(legs, 1):
            if inputs >= TRAVEL_MAX_INPUTS: break
            if leg["place"] != place: return self._travel_result(results, "blocked", f"leg {n} unexpected place", ctx)
            result = approach(replace(ctx, max_inputs=min(ctx.max_inputs, TRAVEL_MAX_INPUTS-inputs)), self.pa, self.pa,
                              exit_target="exit" if leg.get("expect") else None, route={"dirs": leg["dirs"], "push": leg.get("push")})
            results.append(result); inputs += result.inputs
            is_cut = result.reason == "scene cut" or cut(result.before, result.after, result.blank_seen); self._travel_cut |= is_cut; self.pos = fold(self.mem, place, self.pos, result.moves[:-1] if is_cut and result.moves else result.moves, self.tile_px, step, actors=self.actor_tiles)
            if leg.get("expect"):
                arrival = self._arrival(result.after, trace) if trace else None
                edge = exit_to(self.mem, place, tuple(leg["exit_tile"]), leg["push"])
                if not is_cut: return self._travel_result(results, "blocked", f"leg {n} exit mismatch", ctx)
                if arrival and (arrival[2] in ("dialogue", "menu") or (self.mem.get("places", arrival[0]) or {}).get("screen") in ("dialogue", "menu")): return self._travel_result(results, "blocked", f"leg {n} modal opened", ctx)
                wrong = arrival and arrival[0] != leg["expect"]
                mismatch = wrong or not edge or edge.get("to") != leg["expect"] or arrival and tuple(edge["arrive"]) != arrival[1]
                if mismatch:
                    if not arrival: return self._travel_result(results, "blocked", f"leg {n} exit mismatch", ctx)
                    mark_exit(self.mem, place, tuple(leg["exit_tile"]), leg["push"], *arrival[:2], step); self.pos, place = arrival[:2][1], arrival[0]; self._land(place)
                    return self._travel_result(results, "blocked", f"leg {n} {'arrival' if wrong or edge and edge.get('to') == leg['expect'] else 'exit'} mismatch", ctx)
                self.pos, place = (arrival[1] if arrival else tuple(edge["arrive"])), leg["expect"]
                self._land(place)
            elif result.status != "done" and not (result.reason == "route blocked" and self._facing_target(leg, result)):
                return self._travel_result(results, result.status, f"leg {n} {result.reason or result.status}", ctx)
            if result.reason == "scene cut" and not leg.get("expect"): return self._travel_result(results, "blocked", f"leg {n} unexpected scene cut", ctx)
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
        if not target or target.get("kind") != "memory" or [m[0] for m in result.moves] != leg["dirs"]: return False
        return (self.pos[0]+dx, self.pos[1]+dy) == tuple(target["tile"])
    def _travel_result(self, results, status, reason, ctx):
        self._travelled = SkillResult(status, f"travel: {reason}" if reason else "", sum(r.inputs for r in results), sum(r.calls for r in results), results[0].before if results else ctx.env.screenshot(), results[-1].after if results else ctx.env.screenshot(), tuple(map(sum, zip(*(r.scroll for r in results)))) if results else (0, 0), any(r.blank_seen for r in results), [m for r in results for m in r.moves]); return self._travelled
    def name(self, place, name, step):
        self.new_arrival, self.merge_pending = False, None
        name = str(name or "").strip()
        if not name or not (row := self.mem.get("places", place)): return place
        key = name.casefold()
        target = next((r for r in self.mem.rows("places") if r.get("state") != "merged" and r["id"] != place
                       and key in [str(v).casefold() for v in [r.get("name"), *r.get("aliases", [])]]), None)
        if not target: return self.mem.name_place(place, name, step)
        if row.get("first") == step: self.new_arrival = True; return self.mem.name_place(place, name, step)
        if self.merge(place, target["id"], step): return target["id"]
        aliases = row.get("aliases", [])
        if key not in {a.casefold() for a in aliases}: self.mem.upsert("places", {**row, "aliases": [*aliases, name]})
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
