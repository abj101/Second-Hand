"""Spatial state and bookkeeping for the act path."""
import hashlib, json
from dataclasses import replace
from itertools import pairwise
from statistics import median
from agent.memory import TRANSIENT, _base, _distance, normalize_kind
from agent.observer import cut
from agent.space import DIRS, exit_to, fold, frontier, mark_exit, route, tile_of
from agent.skills import SkillResult, approach

TRAVEL_MAX_INPUTS = 120
MISS_TO_STALE, MISS_TO_GONE, ACTOR_STALE_STEPS = 2, 4, 20
LANDMARKS_PER_PLACE, MAP_W, MAP_H, VIEW_TOKENS = 8, 21, 15, 500

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
        self._last_miss_hash = {}
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
        self._tiers(per, place, ids)
        return proj, tiles, ids
    def _promote(self, ids):
        for ident in ids:
            row = self.mem.get("entities", ident)
            if row and row.get("place") and (self.mem.get("places", row["place"]) or {}).get("state") == "merged": continue
            is_exit = bool(row and (normalize_kind(row.get("kind")) == "exit" or str(row.get("name", "")).casefold().startswith("exit:")))
            if is_exit and row.get("place") and self._failed_exit(row, row["place"]):
                if row.get("tier") != "landmark": self.mem.upsert("entities", {**row, "tier": "landmark"})
            elif row and row.get("tier") != "interest": self.mem.upsert("entities", {**row, "tier": "interest"})
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
        targets = [str(a.get("target", "")).casefold() for a in per.get("affordances", [])]
        promote = set()
        for i, entity in enumerate(per.get("entities", [])):
            ident = ids.get(i); row = self.mem.get("entities", ident) if ident else None
            if not row or row.get("place") not in (place, None): continue
            kind, name = normalize_kind(entity.get("kind")), str(entity.get("name", "")).casefold()
            is_exit = kind == "exit" or str(entity.get("name", "")).casefold().startswith("exit:")
            failed_exit = is_exit and self._failed_exit(row, place)
            matched = any(name and (name in target or _distance(name, target) <= 1) for target in targets)
            named = bool(name and name in text)
            tier = "interest" if row.get("tier") == "interest" else "transient" if kind in TRANSIENT else row.get("tier") or "landmark"
            if failed_exit: tier = "landmark"
            elif kind in ACTOR_KINDS or is_exit or matched or named: tier = "interest"
            if tier != row.get("tier"): self.mem.upsert("entities", {**row, "tier": tier})
            if matched: promote.add(ident)
        promote.update(r["id"] for r in self.mem.rows("entities")
                       if r.get("place") == place and r.get("name") and str(r["name"]).casefold() in text)
        promote.update(r.get("s") for r in self.mem.rows("relations")
                       if r.get("src") == "observed" and r.get("sup", 0) and r.get("s"))
        self._promote(i for i in promote if self.mem.get("entities", i) and self.mem.get("entities", i).get("place") == place)
    def _failed_exit(self, entity, place):
        if not entity.get("tile"): return False
        x, y = entity["tile"]; tile = self.mem.get("tiles", f"{place}@{x},{y}")
        if not tile or tile.get("floor") is not True or tile.get("exit"): return False
        relations = any(r.get("s") == entity["id"] and r.get("p") == "leads_to" and r.get("state") == "refuted" for r in self.mem.rows("relations"))
        edges = self.mem.get("places", place).get("edges", [])
        return relations or any(e.get("ok", 0) == 0 and _distance(e.get("via", ""), entity.get("name", "")) <= 1 for e in edges)
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
            if added: self.mem.upsert("seen", {"id": place, "cells": sorted(cells | added), "step": step})
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
            kind, tier = normalize_kind(row.get("kind")), row.get("tier", "landmark")
            is_matched = row.get("place") == place and ident in matched
            if is_matched: continue
            if status == "seen": row = {**row, "status": "remembered"}; status = "remembered"
            if kind in ACTOR_KINDS:
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
        if skill is self._travelled: self._prepared = (skill, place, step, None, self.pos); return {"cut_dir": None, "exit_key": None}
        moves = getattr(skill, "moves", []); move_rows = moves[:-1] if scene_cut and moves else moves
        at = self.pos
        for direction, shift in move_rows:
            if direction not in DIRS: continue
            if shift in ((0, 0), 0):
                target = at[0] + DIRS[direction][0], at[1] + DIRS[direction][1]
                self._promote(i for i, tile in self.entity_tiles.items() if tile == target)
            elif self.tile_px and isinstance(shift, (tuple, int)):
                dx, dy = ((round(-shift / self.tile_px), 0) if direction in ("LEFT", "RIGHT") else (0, round(-shift / self.tile_px))) if isinstance(shift, int) else (round(-v / self.tile_px) for v in shift)
                n = max(abs(dx), abs(dy))
                if n: at = (at[0] + ((dx > 0) - (dx < 0)) * n, at[1] + ((dy > 0) - (dy < 0)) * n)
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
        self._tiers(per, place, self.entity_ids)
        self._record_view(per, frame, reused, place, size, step, aa, self.entity_ids)
        self.entity_tiles = {self.entity_ids[i]: p for i, p in tiles.items() if i in self.entity_ids}; self._travelled = None
        return drift_fix

    def state(self):
        return {"pos": self.pos, "drift": self.drift, "drift_ref": self.drift_ref, "entity_tiles": self.entity_tiles,
                "entity_ids": self.entity_ids, "anchors": self.anchors,
                "last_miss_hash": self._last_miss_hash}

    def restore(self, state):
        self.pos, self.drift = tuple(state.get("pos", (0, 0))), tuple(state["drift"]) if state.get("drift") else None
        self.drift_ref, self.entity_tiles = state.get("drift_ref"), state.get("entity_tiles", {})
        self.entity_ids, self.anchors = {int(i): v for i, v in state.get("entity_ids", {}).items()}, state.get("anchors", [])
        self._last_miss_hash = state.get("last_miss_hash", {})
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
        requested = str(name or "").strip()
        if not requested: return None
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
        screen = [(i, e) for i, e in enumerate(per.get("entities", [])) if e.get("name") and _distance(e["name"], name) <= 1]
        if screen: i, e = min(screen, key=lambda pair: (_distance(pair[1]["name"], name), -(self.mem.get("entities", self.entity_ids.get(pair[0], "")) or {}).get("seen", 0))); ident = self.entity_ids.get(i); return {"kind": "screen", "place": place, "tile": self.entity_tiles.get(ident), "id": ident, "stale": False}
        rows = [e for e in self.mem.rows("entities") if e.get("place") in live and e.get("tile") and _distance(e.get("name", ""), name) <= 1]
        if rows: e = min(rows, key=lambda e: (e["place"] != place, dist.get(e["place"], 10**9), -e.get("seen", 0))); return {"kind": "memory", "place": e["place"], "tile": tuple(e["tile"]), "id": e["id"], "stale": e.get("status") == "stale"}
        rows = [p for p in self.mem.rows("places") if p.get("id") in live and any(_distance(v, name) <= 1 for v in [p.get("name", ""), *p.get("aliases", [])])]
        if not rows: return None
        p = min(rows, key=lambda p: (p["id"] != place, dist.get(p["id"], 10**9), -p.get("visits", 0))); incoming = [r for r in self.mem.rows("tiles") if r.get("place") in live and (r.get("exit") or {}).get("to") == p["id"]]
        tile = (incoming[0].get("exit") or {}).get("arrive") if incoming else next(((r["x"], r["y"]) for r in self.mem.rows("tiles") if r.get("place") == p["id"] and r.get("floor") is True), None)
        return {"kind": "place", "place": p["id"], "tile": tuple(tile) if tile else None, "id": p["id"], "stale": False}

    def place_label(self, place):
        rows = [p for p in self.mem.rows("places") if p.get("state") != "merged"]
        row = self.mem.get("places", place)
        if not row: return place
        name = str(row.get("name") or place)
        duplicates = sum(str(p.get("name") or p["id"]).casefold() == name.casefold() for p in rows) > 1
        return f"{name} [{place}]" if duplicates else name

    def view(self, goal_text, place):
        live = {p["id"] for p in self.mem.rows("places") if p.get("state") != "merged"}
        places = [p for p in self.mem.rows("places") if p.get("id") in live]
        goal = str(goal_text or "").casefold()
        def base_name(name):
            base = _base(name)
            while (stripped := _base(base)) != base: base = stripped
            return base
        promoted = [e["id"] for e in self.mem.rows("entities") if e.get("place") in live and e.get("name")
                    and _base(e["name"]) in goal]
        self._promote(promoted)

        current = self.mem.get("places", place) or {"id": place, "name": place}
        label = self.place_label(place)
        tier = lambda e: e.get("tier", "interest" if normalize_kind(e.get("kind")) in ACTOR_KINDS else "transient" if normalize_kind(e.get("kind")) in TRANSIENT else "landmark")
        tiles = [r for r in self.mem.rows("tiles") if r.get("place") == place]
        seen = self.mem.get("seen", place) or {"cells": []}
        seen_cells = set(seen.get("cells", []))
        edges = frontier(self.mem, place)
        unseen = sum(f"{x + DIRS[d][0]},{y + DIRS[d][1]}" not in seen_cells for (x, y), d in edges)
        walked = sum(r.get("floor") is True for r in tiles)
        lines = [f"Here: {label} ({self.pos[0]},{self.pos[1]})  map: {walked} walked, {len(seen_cells)} seen, {len(edges)} untried edges ({unseen} into unseen)"]

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
        symbols = "ABCDFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
        marks, legend = {}, []
        mapped = set()
        for i, e in enumerate(e for e in entities if x0 <= e["tile"][0] <= x1 and y0 <= e["tile"][1] <= y1 and tuple(e["tile"]) not in marks):
            if i == len(symbols): break
            symbol = symbols[i]; marks[tuple(e["tile"])] = symbol
            mapped.update(member["id"] for member in e["_members"])
            status = e.get("status", "remembered")
            last = f"last seen {tuple(e['tile'])} step {e.get('last', 0)}; may have moved" if status == "stale" else f"{status} step {e.get('last', 0)}"
            count = len(e["_members"])
            name = f"{e['name']} ×{count}" if count > 1 else e.get("name", e["id"])
            nearest = f" (nearest {tuple(e['tile'])})" if count > 1 else ""
            legend.append(f"{'Thing' if tier(e) == 'interest' else 'Landmark'} {symbol} {name} {tuple(e['tile'])} {normalize_kind(e.get('kind'))} {last}{nearest}")
        for y in range(y0, y1 + 1):
            row = []
            for x in range(x0, x1 + 1):
                tile = cells.get((x, y), {})
                glyph = "@" if (x, y) == tuple(self.pos) else "E" if tile.get("exit") else marks.get((x, y))
                if glyph is None:
                    for d, (dx, dy) in DIRS.items():
                        source = cells.get((x - dx, y - dy), {})
                        if d in source.get("blocked", []):
                            glyph = "#"; break
                if glyph is None: glyph = "." if tile.get("floor") is True else "," if f"{x},{y}" in seen_cells else " "
                row.append(glyph)
            lines.append("".join(row))
        lines.append("Map: . walked, , seen, # blocked side, E exit, @ you; letters mark things and landmarks")
        exits = sorted((r for r in tiles if r.get("exit")), key=lambda r: (r["y"], r["x"], r["exit"].get("dir", "")))
        lines.extend(f"E {r['exit'].get('dir')} ({r['x']},{r['y']}) -> {self.place_label(r['exit'].get('to'))}"
                     for r in exits if r.get("place") in live and r["exit"].get("to") in live)
        current_detail = []
        for base, group in sorted(current_groups.items()):
            if any(e["id"] in mapped for e in group): continue
            best = max(group, key=lambda e: (status_rank.get(e.get("status", "remembered"), 0), e.get("last", 0), e["id"]))
            tile_rows = [e for e in group if e.get("tile")]
            nearest = min(tile_rows, key=lambda e: (sum(abs(e["tile"][i] - self.pos[i]) for i in (0, 1)), e["id"])) if tile_rows else None
            tile = tuple(nearest["tile"]) if nearest else "?"
            status = best.get("status", "remembered")
            if len(group) > 1:
                current_detail.append(f"Thing: {base} ×{len(group)} {status} (nearest {tile})")
            else:
                last = f"last seen {tile} step {best.get('last', 0)}; may have moved" if status == "stale" else f"{status} step {best.get('last', 0)}"
                current_detail.append(f"Thing: {best.get('name', best['id'])} {tile} {normalize_kind(best.get('kind'))} {last}")

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
            header = [f"Place: {self.place_label(pid)}" + (f" aliases: {', '.join(map(str, aliases))}" if aliases else "")]
            links = sorted((r for r in self.mem.rows("tiles") if r.get("place") == pid and (r.get("exit") or {}).get("to") in live),
                           key=lambda r: (r["y"], r["x"], r["exit"].get("dir", "")))
            header.extend(f"  Exit {r['exit'].get('dir')} -> {self.place_label(r['exit']['to'])}" for r in links)
            place_headers[pid] = header
            other_edges = frontier(self.mem, pid)
            other_seen = set((self.mem.get("seen", pid) or {}).get("cells", []))
            other_unseen = sum(f"{x + DIRS[d][0]},{y + DIRS[d][1]}" not in other_seen for (x, y), d in other_edges)
            groups = {}
            for e in self.mem.rows("entities"):
                if e.get("place") == pid and tier(e) == "interest" and eligible(e):
                    groups.setdefault(base_name(e.get("name", e["id"])), []).append(e)
            rendered = []
            for base, group in sorted(groups.items()):
                best = max(group, key=lambda e: (status_rank.get(e.get("status", "remembered"), 0), e.get("last", 0), e["id"]))
                tile_rows = [e for e in group if e.get("tile")]
                nearest = min(tile_rows, key=lambda e: (sum(abs(e["tile"][i] - self.pos[i]) for i in (0, 1)), e["id"])) if tile_rows else None
                if len(group) > 1:
                    rendered.append(f"  Thing: {base} ×{len(group)} {best.get('status', 'remembered')} (nearest {tuple(nearest['tile']) if nearest else '?'})")
                else:
                    status = best.get("status", "remembered")
                    fact = f"last seen {tuple(best['tile']) if best.get('tile') else '?'} step {best.get('last', 0)}; may have moved" if status == "stale" else status
                    rendered.append(f"  Thing: {best.get('name', best['id'])} {fact}")
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
        add_detail_bundle(f"Things here: {label}", [*legend, *current_detail])
        for other in others:
            pid = other["id"]
            if pid in included: add_detail_bundle(f"Things: {self.place_label(pid)}", place_things[pid])

        flags = list(getattr(self, "view_flags", ()))
        if self.merge_pending: flags.append(f"merge_pending: {self.merge_pending}")
        if self._travelled and self._travelled.status != "done": flags.append(f"travel: {self._travelled.reason}")
        for line in (f"Flag: {flag}" for flag in dict.fromkeys(flags)):
            if len("\n".join((*output, line))) <= VIEW_TOKENS * 4: output.append(line)
        return "\n".join(output)
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
