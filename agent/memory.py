"""Small JSONL-backed world model used by the agent."""
import base64
import json
import re
from pathlib import Path

import numpy as np
from PIL import Image


TRANSIENT = frozenset({"player", "exit", "cursor", "dialogue_box", "ui_element", "icon", "text", "menu_item", "input_field", "keyboard", "logo"})  # not landmarks
RECURRING = ("menu_item", "move", "item", "option")
ACTOR_KINDS = {"person", "npc", "character", "enemy", "monster", "animal"}
STATIC_GATE = 1
CLAIM_TIERS = {"text": 0, "said": 1}


class Looks:
    def __init__(self, path: Path, run: str):
        self.path, self.run, self.rows, self.counts = Path(path), run, {}, {}
        if self.path.exists():
            with self.path.open(encoding="utf-8") as stream:
                for line in stream:
                    try:
                        row = json.loads(line)
                        if isinstance(row, dict) and isinstance(row.get("id"), str) and row["id"] not in self.rows:
                            self.rows[row["id"]] = row; self._count(row)
                    except (json.JSONDecodeError, UnicodeError): continue
    def add(self, look, label, place, cell, step):
        ident = f"{look}|{self.run}|{place}@{cell[0]},{cell[1]}|{label}"
        if ident in self.rows: return self.rows[ident]
        row = {"id": ident, "look": look, "label": label, "run": self.run, "place": place, "cell": list(cell), "step": step}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as stream: stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        self.rows[ident] = row
        self._count(row)
        return row
    def _count(self, row):
        counts = self.counts.setdefault(row["look"], set()); counts.add(row["label"])
    def says(self, look):
        labels = self.counts.get(look, ())
        return next(iter(labels)) if len(labels) == 1 else "mixed" if labels else None


def normalize_kind(kind: str) -> str:
    value = "_".join(str(kind or "").casefold().split(":", 1)[0].split())  # "dialogue box" == "dialogue_box"
    value = value[:-1] if value.endswith("s") else value
    return {"doorway": "door", "ui": "ui_element"}.get(value, value)


def _is_exit(kind, name) -> bool:  # category is part of identity: a passage is never the same thing as a wall or a sign on its tile
    return normalize_kind(kind) == "exit" or str(name or "").casefold().startswith("exit:")


def _actor(row: dict) -> bool:
    return row.get("anchor") == "actor" or normalize_kind(row.get("kind")) in ACTOR_KINDS


def _key(value: str) -> str:
    return " ".join(str(value).casefold().split())


def _base(value: str) -> str:
    return re.sub(r"(#\d+)+$", "", _key(value)).removeprefix("exit:").strip()  # "exit: path south" == "path south"


# The perceiver's screen enum (agent/perceive.py): a place is never named or aliased with one of these.
SCREEN_NAMES = frozenset({"world", "dialogue", "menu", "keyboard", "battle", "title", "other"})
def _nameable(name: str) -> bool:
    name = str(name or "").strip()
    return bool(name) and name.casefold() not in SCREEN_NAMES


def place_name(row: dict | None) -> str:
    """A place's model-given name; a screen type stored as a name by older runs does not count."""
    row = row or {}
    name = str(row.get("name") or "").strip()
    return name if _nameable(name) else ""


def place_aliases(row: dict | None) -> list[str]:
    """Stored aliases that still count as names when matching (older rows may hold a screen type or a reference)."""
    return [a for a in (row or {}).get("aliases", []) if _nameable(str(a).strip())]


# ponytail: calibrated on Red's house 1F/2F (same floor >=.958 within 4 tiles, other floor <=.789);
# pixel-identical interiors in different towns (Poke Centers) will match: add a graph-consistency check then.
VIEW_MATCH, VIEW_REACH = .9, 6


def _thumb(frame) -> np.ndarray:
    w, h = frame.size
    return (np.asarray(frame.convert("L").resize((w // 2, h // 2), Image.Resampling.NEAREST)) // 64).astype(np.uint8)


def _view_score(a: np.ndarray, b: np.ndarray, sx: int, sy: int) -> float:
    """Share of equal pixels where b is a shifted by (sx, sy); off-map black in both is ignored."""
    h, w = a.shape
    aa = a[max(sy, 0):h + min(sy, 0), max(sx, 0):w + min(sx, 0)]
    bb = b[max(-sy, 0):h + min(-sy, 0), max(-sx, 0):w + min(-sx, 0)]
    lit = (aa > 0) | (bb > 0)
    return float((aa == bb)[lit].mean()) if lit.sum() >= a.size / 4 else 0.0


def _distance(a: str, b: str) -> int:
    a, b = _base(a), _base(b)
    if abs(len(a) - len(b)) > 1:
        return 2
    row = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        nxt = [i]
        for j, cb in enumerate(b, 1):
            nxt.append(min(nxt[-1] + 1, row[j] + 1, row[j - 1] + (ca != cb)))
        if min(nxt) > 1:
            return 2
        row = nxt
    return row[-1]


class Memory:
    arrival = None  # (place, tile you arrived on), set by whichever match_place path identified the place; (new place, (0, 0)) on a mint
    unplaced = None  # a known place entered with no arrival evidence: where you are in it is unknown until a stored view matches
    excursion = None  # the place a dialogue/menu/battle screen was last seen in, until a navigable screen follows

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.looks = None
        self._data: dict[str, dict[str, dict]] = {}
        for path in self.root.glob("*.jsonl"):
            store = path.stem
            rows = self._data.setdefault(store, {})
            with path.open(encoding="utf-8") as stream:
                for line in stream:
                    try:
                        row = json.loads(line)
                        if isinstance(row, dict) and isinstance(row.get("id"), str):
                            rows[row["id"]] = row
                    except (json.JSONDecodeError, UnicodeError):
                        continue

    def get(self, store: str, id: str) -> dict | None:
        return self._data.get(store, {}).get(id)

    def rows(self, store: str) -> list[dict]:
        return list(self._data.get(store, {}).values())

    def learn_look(self, place, cell, label, step):
        if self.looks is None: return
        row = self.get("looks", f"{place}@{cell[0]},{cell[1]}")
        if row: self.looks.add(row["look"], label, place, cell, step)

    def upsert(self, store: str, row: dict) -> dict:
        if not re.fullmatch(r"[\w.-]+", store) or not isinstance(row.get("id"), str):
            raise ValueError("store names and row ids must be simple strings")
        rows = self._data.setdefault(store, {})
        rows[row["id"]] = dict(row)
        with (self.root / f"{store}.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        return row

    def mark_unreached(self, place, tile, at, step):
        x, y = map(int, tile); ident = f"{place}@{x},{y}"; row = self.get("reach", ident) or {"id": ident, "place": place, "tile": [x, y], "n": 0, "step": step}
        return self.upsert("reach", {**row, "n": row.get("n", 0) + 1, "step": step, "last": step, "at": list(at)})

    def mark_reached(self, place, tile, step):
        x, y = map(int, tile); ident = f"{place}@{x},{y}"; row = self.get("reach", ident)
        if row and row.get("n", 0) and step > row.get("last", 0): return self.upsert("reach", {**row, "n": 0, "step": step, "reached": step})
        return row

    @staticmethod
    def screen_id(per):
        items = [str(e["name"]) for e in per.get("entities", []) if e.get("name") and e.get("selectable") is True]
        return f"{per.get('screen')}|{'|'.join(sorted(items))}" if per.get("screen") != "world" and items else None
    def observe_screen(self, per, step):
        ident = self.screen_id(per)
        if not ident: return None
        items = [str(e["name"]) for e in per.get("entities", []) if e.get("name") and e.get("selectable") is True]
        row = self.get("screens", ident) or {"id": ident, "screen": per["screen"], "items": items, "seen": 0, "first": step, "replies": {}}
        return self.upsert("screens", {**row, "items": items, "seen": row["seen"] + 1, "last": step})
    def screen_reply(self, ident, item, step, text):
        row = self.get("screens", ident)
        if not row or item not in row["items"]: return
        replies = dict(row.get("replies", {})); replies[item] = [*replies.get(item, []), {"step": step, "text": str(text)[:80]}][-3:]
        self.upsert("screens", {**row, "replies": replies})

    def call(self, entity_id: str, name: str, step: int, tier: str = "said", key: str | None = None, store: str = "entities") -> dict | None:
        """A name from the game, ranked: "text" (the planner read it in game text) < "said" (the game named the thing when the agent interacted with it).
        A claim of the same or a higher tier replaces the old one; a lower tier never does. A `called` with no `called_src` counts as said."""
        row, name = self.get(store, entity_id), str(name or "").strip()
        if not row or not name or (row.get("called") and CLAIM_TIERS[tier] < CLAIM_TIERS[(row.get("called_src") or {}).get("tier", "said")]):
            return row
        return self.upsert(store, {**row, "called": name, "called_src": {"tier": tier, "step": step, "key": key}})

    def _id(self, store: str, prefix: str) -> str:
        used = self._data.get(store, {})
        base = re.sub(r"[^a-z0-9]+", "_", prefix.casefold()).strip("_") or "unnamed"
        candidate, n = f"{store[0]}_{base}", 2
        while candidate in used:
            candidate, n = f"{store[0]}_{base}_{n}", n + 1
        return candidate

    def match_place(self, perception: dict, step: int, *, prev: str | None,
                    cut: bool, via: str | None = None,
                    exit: tuple[int, int, str] | None = None, via_id: str | None = None,
                    frame=None, tile_px: int | None = None) -> str:
        self.arrival_exit = None; self.arrival = None
        screen = str(perception.get("screen", "unknown"))
        if screen != "world":  # dialogue, menu, title, battle: a state of the current place, never a place
            if prev is not None:
                self.excursion = prev
                return prev
            # ponytail: before the first navigable screen one unnamed run-start place stands in; it is never matched or named
            return self.upsert("places", {"id": self._id("places", "place"), "state": "candidate", "visits": 1, "blocked": [], "first": step, "last": step})["id"]
        back = prev is not None and self.excursion == prev  # back from a dialogue/menu/battle to the place it left: that place may match again
        self.excursion = None
        if not cut and (self.get("places", prev) or {}).get("screen") == "world":
            return prev  # the run-start place is never promoted: the first map is minted below, so pre-map screens stay out of it
        exclude = None if back else prev
        if cut and frame is not None and (hit := self.seen_view(frame, screen, exclude, tile_px)):
            self.arrival_exit = None; self.arrival = (*hit[:2], "view")  # place, tile, source
            row = self.get("places", hit[0])
            return self.upsert("places", {**row, "visits": row.get("visits", 0) + 1, "last": step})["id"]
        if cut and prev and exit:  # the exact exit, else every known exit within one tile (position drifts) if they agree on where they lead
            near = [(max(abs(r["x"] - exit[0]), abs(r["y"] - exit[1])), r["exit"].get("dir") != exit[2], r["exit"]["to"], tuple(r["exit"].get("arrive") or ()), i, r["exit"].get("src"), r["id"]) for i, r in enumerate(self.rows("tiles"))
                    if r.get("place") == prev and (r.get("exit") or {}).get("to") not in (None, prev) and max(abs(r["x"] - exit[0]), abs(r["y"] - exit[1])) <= 1]
            if near and (min(near)[:2] == (0, False) or len({n[2] for n in near}) == 1):
                best = min(near)
                if best[3]: self.arrival = (best[2], best[3], best[5]); self.arrival_exit = best[6]  # ponytail: checkpoints between match_place and _locate may drop this id harmlessly; only correction is skipped
                return best[2]
        if cut and via_id:
            known = next((r["o"] for r in self.rows("relations") if r.get("s") == via_id and r.get("p") == "leads_to" and r.get("state") == "live" and r.get("o") != prev and self.get("places", r.get("o"))), None)
            if known: return known
        pid = self._id("places", "place")
        self.upsert("places", {"id": pid, "screen": "world",
                               "state": "candidate", "visits": 1,
                               "blocked": [],
                               "first": step, "last": step})
        if cut and frame is not None and (hit := self.seen_view(frame, screen, prev, tile_px, at_least=.5)):
            self.upsert("places", {**self.get("places", pid), "looks_like": [hit[0], round(hit[2], 2), step]})
        self.arrival_exit = None; self.arrival = (pid, (0, 0), "new")  # a new place's frame starts where you stand
        return pid

    def remember_view(self, place: str, pos, frame) -> None:
        """One small greyscale view per (place, tile) so a later arrival can be recognised by pixels."""
        vid = f"{place}@{pos[0]},{pos[1]}"
        if frame is None or place == self.unplaced or self.get("views", vid): return  # an unknown position would file the view under a wrong tile
        q = _thumb(frame)
        self.upsert("views", {"id": vid, "place": place, "pos": list(pos), "shape": list(q.shape),
                              "q": base64.b64encode(q.tobytes()).decode()})

    def seen_view(self, frame, screen: str, exclude: str | None, tile_px: int | None, only: str | None = None, at_least: float = VIEW_MATCH):
        """(place, tile, score) of the known view this frame matches, allowing whole-tile camera scroll; `only` = look up where you are in that place."""
        q, best, step = _thumb(frame), None, (tile_px or 0) // 2
        reach = sorted(range(-VIEW_REACH, VIEW_REACH + 1), key=abs) if step else (0,)
        for v in self.rows("views"):
            place, (ox, oy), row = self._home(v["place"])
            # views are only stored on world screens, so no screen-type check: a place first seen under a dialogue box keeps type "dialogue" (m1_gp5: bedroom re-minted on return)
            if not row or list(q.shape) != v["shape"] or (place != only if only else place == exclude): continue
            stored = np.frombuffer(base64.b64decode(v["q"]), np.uint8).reshape(v["shape"])
            for dy in reach:
                for dx in reach:
                    score = _view_score(stored, q, dx * step, dy * step)
                    if score >= at_least and (best is None or score > best[0]):
                        best = (score, place, (v["pos"][0] + ox + dx, v["pos"][1] + oy + dy))
        return (*best[1:], best[0]) if best else None

    def _home(self, place: str):
        """Live place a (possibly merged) place now lives in, its tile offset there, and that place's row."""
        ox, oy = 0, 0
        while (row := self.get("places", place)) and row.get("state") == "merged":
            ox, oy, place = ox + row["offset"][0], oy + row["offset"][1], row["into"]
        return place, (ox, oy), row

    def views_agree(self, src: str, dst: str, offset, tile_px: int | None):
        """None: no stored views of src and dst within scroll reach of each other at this offset; else whether any pair matches."""
        step, scores = (tile_px or 0) // 2, []
        if not step: return None
        load = lambda v: np.frombuffer(base64.b64decode(v["q"]), np.uint8).reshape(v["shape"])
        home = lambda v: self._home(v["place"])
        theirs = [(v, h[1]) for v in self.rows("views") if (h := home(v))[0] == dst]
        for mine, (sx, sy) in ((v, h[1]) for v in self.rows("views") if (h := home(v))[0] == src):
            at = (mine["pos"][0] + sx + offset[0], mine["pos"][1] + sy + offset[1])
            for v, (tx, ty) in theirs:
                dx, dy = at[0] - v["pos"][0] - tx, at[1] - v["pos"][1] - ty
                if max(abs(dx), abs(dy)) <= VIEW_REACH and v["shape"] == mine["shape"]:
                    scores.append(_view_score(load(v), load(mine), dx * step, dy * step))
        return max(scores) >= VIEW_MATCH if scores else None

    def name_place(self, place: str, name: str, step: int) -> None:
        name, row = str(name or "").strip(), self.get("places", place)
        if not row or not _nameable(name): return
        key = name.casefold()
        aliases = row.get("aliases", [])
        if key not in {str(a).casefold() for a in aliases}: aliases = [*aliases, name]
        self.upsert("places", {**row, "name": place_name(row) or name, "aliases": aliases})  # the first name sticks; later names are aliases

    def observe(self, perception: dict, place: str, step: int,
                tiles: dict[int, tuple[int, int]] | None = None, tracks: dict[int, str] | None = None) -> dict[int, str]:
        """tracks: entity index -> id of the pixel track under it (agent/track.py). That record is the same thing whatever the model now calls it."""
        rows = self.rows("entities")
        entities, result, used = [], {}, set()
        for i, entity in enumerate(perception.get("entities", [])):
            name, kind = str(entity.get("name", "")).strip(), str(entity.get("kind", "unknown"))
            if kind.casefold() == "player": kind = "unknown"
            if not name:
                continue
            nk, at = normalize_kind(kind), entity.get("at")
            if nk == "exit" and at and any(c <= .02 or c >= .98 for c in at[:2]):
                continue
            tile = tiles.get(i) if tiles and (perception.get("screen") == "world" or kind not in RECURRING) else None  # menu lines keep name identity; a world thing keeps its position whatever it is called
            entities.append((i, entity, name, kind, nk, tile))

        # Tile-less and recurring entities keep their name-based identity.
        for i, entity, name, kind, nk, tile in entities:
            if tile is not None:
                continue
            pool = [r for r in rows if (r["id"] not in used or _base(r.get("name", "")) == _base(name)) and r.get("state") != "merged"  # no position: the same text twice is one thing
                    and (normalize_kind(r.get("kind")) == nk if kind in RECURRING else r.get("place") == place)
                    and _distance(r.get("name", ""), name) <= 1]
            exact = [r for r in pool if _base(r.get("name", "")) == _base(name)]
            row = max(exact, key=lambda r: r.get("seen", 0), default=None)
            if row is None and len(pool) == 1:
                row = pool[0]
            if row:
                result[i], _ = row["id"], used.add(row["id"])

        # Position is the identity evidence for every positioned observation.
        lead = {}  # a tile holds one static thing: static perceptions projected onto one tile in one frame follow the first one
        for i, entity, name, kind, nk, tile in entities:
            if tile is not None and not (nk in ACTOR_KINDS or nk == "player"): lead.setdefault((tuple(tile), _is_exit(kind, name)), i)
        pairs = []
        for i, entity, name, kind, nk, tile in entities:
            if tile is None or i in result:
                continue
            is_actor = nk in ACTOR_KINDS or nk == "player"
            exit_ = _is_exit(kind, name)
            if not is_actor and lead[(tuple(tile), exit_)] != i:
                continue
            hit = self.get("entities", (tracks or {}).get(i))
            if hit and hit.get("place") == place and hit.get("state") != "merged" and _is_exit(hit.get("kind"), hit.get("name")) == exit_:  # a track never turns a passage into its walker
                pairs.append(((-1,), i, hit))
            for row in rows:
                if row.get("place") != place or row.get("state") == "merged" or not row.get("tile") or _is_exit(row.get("kind"), row.get("name")) != exit_ or _actor(row) != is_actor:
                    continue
                d = max(abs(tile[0] - row["tile"][0]), abs(tile[1] - row["tile"][1]))
                gate = min(4, 1 + max(0, step - row.get("last", step)) // 4) if (
                    row.get("anchor") == "actor" or normalize_kind(row.get("kind")) in ACTOR_KINDS
                ) else STATIC_GATE
                if d <= gate:
                    pairs.append(((d, (_actor(row) != is_actor), _distance(row.get("name", ""), name), -row.get("seen", 0)), i, row))
        for cost, i, row in sorted(pairs, key=lambda pair: pair[0]):
            same_tile_static = len(cost) > 1 and cost[0] == 0 and not cost[1] and not _actor(row)  # a tile holds one static thing: a second perception on it is the same thing
            if i not in result and (row["id"] not in used or same_tile_static):
                result[i], _ = row["id"], used.add(row["id"])

        for i, entity, name, kind, nk, tile in entities:
            row = self.get("entities", result[i]) if i in result else None
            group = (tuple(tile), _is_exit(kind, name)) if tile is not None else None
            if row is None and group and lead.get(group, i) != i:  # follows the first static perception of its category on its tile, whatever that one matched or minted
                row = self.get("entities", result.get(lead[group]))
            if row is None and tile is not None and not (nk in ACTOR_KINDS or nk == "player"):  # a tile holds one static thing: a second perception on it in one frame is projection noise, not a new thing
                row = next((r for r in self.rows("entities") if r.get("place") == place and r.get("state") != "merged" and not _actor(r)
                            and r.get("tile") and tuple(r["tile"]) == tuple(tile) and _is_exit(r.get("kind"), r.get("name")) == _is_exit(kind, name)), None)
            if row is None:
                recurring = kind in RECURRING and tile is None
                eid = self._id("entities", name)
                is_actor = nk in ACTOR_KINDS or nk == "player"
                near = []
                for other in self.rows("entities"):
                    if other.get("place") != place or other.get("state") == "merged" or _actor(other) != is_actor:
                        continue
                    tile_d = max(abs(tile[0] - other["tile"][0]), abs(tile[1] - other["tile"][1])) if tile and other.get("tile") else None
                    near.append({"id": other["id"], "name": other.get("name"), "tile": other.get("tile"),
                                 "last": other.get("last"), "used": other["id"] in used,
                                 "name_d": _distance(other.get("name", ""), name), "tile_d": tile_d})
                near.sort(key=lambda r: r["tile_d"] if r["tile_d"] is not None else float("inf"))
                row = {"id": eid, "kind": kind, "name": name, "place": None if recurring else place,
                       "at": entity.get("at"), "attrs": {}, "aliases": [], "kinds": [kind],
                       "first": step, "last": step, "seen": 0, "src": "observed"}
                with (self.root / "mints.jsonl").open("a", encoding="utf-8") as f:
                    f.write(json.dumps({"step": step, "id": eid, "name": name, "kind": kind, "place": place,
                                        "at": entity.get("at"), "tile": tile, "track": (tracks or {}).get(i),
                                        "track_place": (self.get("entities", (tracks or {}).get(i)) or {}).get("place"),
                                        "near": near}) + "\n")
            elif name != row.get("name") and name not in row.get("aliases", []):
                row["aliases"] = [*row.get("aliases", []), name]
            if str(row.get("kind", "")).casefold() == "player": row["kind"] = "unknown"
            if kind not in row.setdefault("kinds", [row.get("kind", kind)]):
                row["kinds"] = [*row["kinds"], kind]
            recent_names = [*row.get("recent_names", []), name][-6:]
            row["recent_names"] = recent_names
            shown_name = max(recent_names, key=recent_names.count)
            if recent_names.count(shown_name) >= 3 and shown_name != row.get("name"):
                if row.get("name") and row["name"] not in row.get("aliases", []): row["aliases"] = [*row.get("aliases", []), row["name"]]
                row["name"] = shown_name
            row_actor = _actor(row)
            recent_kinds = ["unknown" if str(k).casefold() == "player" else k for k in row.get("recent_kinds", [])]
            recent_kinds = [*recent_kinds, kind][-6:]
            row["recent_kinds"] = recent_kinds
            votes = [k for k in recent_kinds if _actor({"kind": k}) == row_actor]
            if votes:
                shown_kind = max(votes, key=votes.count)
                if votes.count(shown_kind) >= 3: row["kind"] = shown_kind
            row.update(at=entity.get("at"), last=step, seen=row.get("seen", 0) + 1, status="seen", misses=0)
            if _is_exit(kind, name) and entity.get("placement") in ("top wall", "bottom wall", "left wall", "right wall"): row["placement"] = entity["placement"]
            if tile is not None:
                if _actor(row): row["tile"] = list(tile)
                elif not row.get("tile") or tuple(row["tile"]) == tuple(tile): row["tile"] = list(tile); row.pop("pending_tile", None)
                elif row.get("pending_tile") == list(tile):
                    if not any(r["id"] != row["id"] and r.get("place") == place and r.get("state") != "merged" and not _actor(r)
                               and r.get("tile") and tuple(r["tile"]) == tuple(tile) and _is_exit(r.get("kind"), r.get("name")) == _is_exit(row.get("kind"), row.get("name"))
                               for r in self.rows("entities")): row["tile"] = list(tile)
                    row.pop("pending_tile", None)
                else: row["pending_tile"] = list(tile)
            result[i] = self.upsert("entities", row)["id"]
            used.add(result[i])
        return result

    def relate(self, s: str, p: str, o: str, *, src: str, step: int,
               hit: bool | None = None, verb: str | None = None) -> dict:
        pixel = src in ("pixel", "observed")
        source = "observed" if pixel else src
        row = next((r for r in self.rows("relations")
                    if (r.get("s"), r.get("p"), r.get("o")) == (s, p, o)
                    and (not verb or r.get("verb", verb) == str(verb).casefold())), None)
        if row is None:
            row = {"id": self._id("relations", "relation"), "s": s, "p": p, "o": o,
                   "src": source, "sup": 0, "con": 0, "ev": [], "state": "live", "why": "", "step": step}
        if pixel and hit is not None:
            row["sup" if hit else "con"] += 1
            row.setdefault("ev", []).append(step)
        if source == "model" and row.get("src") != "observed":
            row["src"] = "model"
        elif pixel:
            row["src"] = "observed"
        if verb:
            row["verb"] = str(verb).casefold()
        row["step"] = step
        row["state"] = "refuted" if row["con"] >= 2 and row["con"] > row["sup"] else "live"
        if row["state"] == "refuted":
            row["why"] = "contradicted by observed outcomes"
        if p in ("moves_view", "view_scrolled") and pixel:
            entity = self.get("entities", s)
            candidate = entity.get("place") if entity else None
            if not candidate and o in self._data.get("places", {}):
                candidate = o
            place = self.get("places", candidate) if candidate else None
            if place and place.get("state") == "candidate":
                place["state"] = "place"
                self.upsert("places", place)
        return self.upsert("relations", row)

    def know(self, text: str, *, src: str, step: int) -> str:
        key = _key(text)
        row = next((r for r in self.rows("knowledge") if _key(r.get("text", "")) == key), None)
        if row is None:
            row = {"id": self._id("knowledge", "knowledge"), "text": text, "src": src, "step": step}
        else:
            row.update(text=text, src=src, step=step)
        return self.upsert("knowledge", row)["id"]

def conf(rel: dict) -> float:
    sup, con = rel.get("sup", 0), rel.get("con", 0)
    return (sup + 1) / (sup + con + 2)
