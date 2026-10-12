"""Spatial state and bookkeeping for the act path."""
from agent.space import exit_to, fold, mark_exit, tile_of

class World:
    def __init__(self, mem, tile_px):
        self.mem, self.tile_px = mem, tile_px
        self.pos, self.drift, self.drift_ref = (0, 0), None, None
        self.entity_tiles, self.entity_ids, self.anchors = {}, {}, []
        self.entities, self.pa, self._prepared = [], None, None
    def _anchor(self, per, size):
        h = self.anchors
        p = next((e.get("at") for e in per.get("entities", []) if e.get("kind", "").casefold() == "player"), None)
        mid = lambda: h[0] if len(h) < 3 else [sorted(x[i] for x in h)[len(h) // 2] for i in (0, 1)]
        if p and self.tile_px and per.get("screen") == "world" and (not h or max(abs(p[i] - mid()[i]) * size[i] for i in (0, 1)) <= 1.5 * self.tile_px): h.append(list(p)); del h[:-9]
        return mid() if h else p
    def before(self, per, place, prior, size, step):
        if prior and place != prior: self.pos, self.drift, self.drift_ref = (0, 0), None, None
        player = next((e for e in per.get("entities", []) if e.get("kind", "").casefold() == "player"), None)
        pa = self._anchor(per, size) if player else None
        self.pa = pa
        proj = lambda at: tile_of(at, pa, self.pos, size, self.tile_px) if pa and self.tile_px and isinstance(at, list) and len(at) == 2 else None
        tiles = {i: t for i, e in enumerate(per.get("entities", [])) if e.get("kind", "").casefold() != "player" and (t := proj(e.get("at")))}
        ids = self.mem.observe(per, place, step, tiles)
        self.entity_ids, self.entity_tiles = ids, {ids[i]: p for i, p in tiles.items() if i in ids}
        self.entities = [{"id": ids.get(i), "name": e.get("name"), "kind": e.get("kind"), "at": e.get("at")}
                         for i, e in enumerate(per.get("entities", []))]
        return proj, tiles, ids
    def prepare(self, skill, place, scene_cut, step):
        moves = getattr(skill, "moves", []); move_rows = moves[:-1] if scene_cut and moves else moves
        if self.tile_px: self.pos = fold(self.mem, place, self.pos, move_rows, self.tile_px, step)
        cut_dir = moves[-1][0] if scene_cut and moves else None
        self._prepared = (skill, place, step, cut_dir, self.pos)
        return {"cut_dir": cut_dir, "exit_key": (*self.pos, cut_dir) if cut_dir and self.tile_px else None}
    def after(self, skill, place, next_place, scene_cut, step):  # call after prepare() for the same step
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
        self.entity_tiles = {self.entity_ids[i]: p for i, p in tiles.items() if i in self.entity_ids}
        return drift_fix

    def state(self):
        return {"pos": self.pos, "drift": self.drift, "drift_ref": self.drift_ref, "entity_tiles": self.entity_tiles,
                "entity_ids": self.entity_ids, "anchors": self.anchors}

    def restore(self, state):
        self.pos, self.drift = tuple(state.get("pos", (0, 0))), tuple(state["drift"]) if state.get("drift") else None
        self.drift_ref, self.entity_tiles = state.get("drift_ref"), state.get("entity_tiles", {})
        self.entity_ids, self.anchors = {int(i): v for i, v in state.get("entity_ids", {}).items()}, state.get("anchors", [])
