"""Small JSONL-backed world model used by the agent."""
import json
import re
from pathlib import Path


RECURRING = ("menu_item", "move", "item", "option")
_BUDGETS = {"perceiver": 400, "chooser": 1200, "orchestrator": 3000}


def _key(value: str) -> str:
    return " ".join(str(value).casefold().split())


def _base(value: str) -> str:
    return re.sub(r"#\d+$", "", _key(value))


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
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
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

    def upsert(self, store: str, row: dict) -> dict:
        if not re.fullmatch(r"[\w.-]+", store) or not isinstance(row.get("id"), str):
            raise ValueError("store names and row ids must be simple strings")
        rows = self._data.setdefault(store, {})
        rows[row["id"]] = dict(row)
        with (self.root / f"{store}.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        return row

    def _id(self, store: str, prefix: str) -> str:
        used = self._data.get(store, {})
        base = re.sub(r"[^a-z0-9]+", "_", prefix.casefold()).strip("_") or "unnamed"
        candidate, n = f"{store[0]}_{base}", 2
        while candidate in used:
            candidate, n = f"{store[0]}_{base}_{n}", n + 1
        return candidate

    def match_place(self, perception: dict, step: int, *, prev: str | None,
                    cut: bool, via: str | None = None) -> str:
        if not cut and prev is not None:
            return prev
        if cut and prev:
            for edge in self.get("places", prev).get("edges", []) if self.get("places", prev) else ():
                if via and edge.get("via") == via and edge.get("to"):
                    return edge["to"]
        screen = str(perception.get("screen", "unknown"))
        landmarks = sorted({(str(e.get("name", "")).casefold(), str(e.get("kind", "")).casefold())
                            for e in perception.get("entities", []) if e.get("name")})
        best, score = None, 0.0
        current = set(landmarks)
        for place in self.rows("places"):
            known = set(map(tuple, place.get("landmarks", [])))
            union = current | known
            overlap = len(current & known) / len(union) if union else 0
            if place.get("screen") == screen and overlap >= .6 and overlap > score:
                best, score = place, overlap
        if best:
            best["visits"] = best.get("visits", 0) + 1
            best["last"] = step
            return self.upsert("places", best)["id"]
        pid = self._id("places", screen)
        self.upsert("places", {"id": pid, "name": screen, "screen": screen,
                               "state": "candidate", "visits": 1,
                               "landmarks": [list(x) for x in landmarks], "edges": [], "blocked": [],
                               "first": step, "last": step})
        return pid

    def observe(self, perception: dict, place: str, step: int) -> dict[int, str]:
        result, used = {}, set()
        for i, entity in enumerate(perception.get("entities", [])):
            name, kind = str(entity.get("name", "")).strip(), str(entity.get("kind", "unknown"))
            if not name:
                continue
            global_key = kind in RECURRING
            all_candidates = [r for r in self.rows("entities")
                          if (r.get("kind") == kind and _distance(r.get("name", ""), name) <= 1
                              if global_key else r.get("place") == place and _distance(r.get("name", ""), name) <= 1)]
            candidates = [r for r in all_candidates if r["id"] not in used]
            exact = [r for r in candidates if _base(r.get("name", "")) == _base(name)]
            pool = exact or candidates
            row = pool[0] if len(pool) == 1 else None
            if row is None:
                eid = self._id("entities", name)
                duplicate = bool(all_candidates) or any(
                    r.get("kind") == kind and _base(r.get("name", "")) == _base(name)
                    and (global_key or r.get("place") == place) for r in self.rows("entities"))
                suffix = max(2, sum(1 for r in self.rows("entities") if _base(r.get("name", "")) == _base(name)) + 1)
                label = f"{name}#{suffix}" if duplicate else name
                row = {"id": eid, "kind": kind, "name": label, "place": None if global_key else place,
                       "at": entity.get("at"), "attrs": {}, "first": step, "last": step,
                       "seen": 0, "src": "observed"}
            row.update(kind=kind, name=row.get("name", name), at=entity.get("at"), last=step,
                       seen=row.get("seen", 0) + 1)
            eid = self.upsert("entities", row)["id"]
            result[i], _ = eid, used.add(eid)
        return result

    def resolve(self, name: str, place: str) -> str | None:
        candidates = [r for r in self.rows("entities")
                      if (r.get("place") == place or r.get("kind") in RECURRING)
                      and _distance(r.get("name", ""), name) <= 1]
        exact = [r for r in candidates if _base(r.get("name", "")) == _base(name)]
        pool = exact or candidates
        if len(pool) == 1:
            return pool[0]["id"]
        if not pool:
            return self.upsert("entities", {"id": self._id("entities", name), "kind": "affordance",
                "name": name, "place": place, "at": None, "attrs": {}, "first": 0, "last": 0,
                "seen": 0, "src": "model"})["id"]
        return None

    def add_edge(self, src_place: str, verb: str, via: str, dst_place: str, ok: bool) -> None:
        place = self.get("places", src_place)
        if not place:
            return
        edges = place.setdefault("edges", [])
        edge = next((e for e in edges if e.get("verb") == verb and e.get("via") == via
                     and e.get("to") == dst_place), None)
        if edge is None:
            edge = {"verb": verb, "via": via, "to": dst_place, "n": 0, "ok": 0}
            edges.append(edge)
        edge["n"] += 1
        edge["ok"] += int(ok)
        self.upsert("places", place)

    def relate(self, s: str, p: str, o: str, *, src: str, step: int,
               hit: bool | None = None) -> dict:
        pixel = src in ("pixel", "observed")
        source = "observed" if pixel else src
        row = next((r for r in self.rows("relations")
                    if (r.get("s"), r.get("p"), r.get("o")) == (s, p, o)), None)
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

    def expire(self, step: int) -> None:
        for row in self.rows("relations"):
            if row.get("src") == "model" and row.get("state") != "expired" and step - row.get("step", step) >= 200:
                row["state"] = "expired"
                self.upsert("relations", row)

    def view(self, caller: str, ctx: dict) -> tuple[str, list[str]]:
        place, task = ctx.get("place"), ctx.get("task")
        on_screen, recent = set(ctx.get("on_screen", [])), ctx.get("recent", [])[-5:]
        lines, ids = [], []
        def add(line: str, item_id: str | None = None, reserve: int = 0) -> bool:
            text = "\n".join(lines + [line])
            if len(text) / 4 + reserve / 4 > _BUDGETS.get(caller, 400):
                return False
            lines.append(line)
            if item_id:
                ids.append(item_id)
            return True
        current = self.get("places", place) if place else None
        if current:
            add(f"Place: {current.get('name', place)} [{place}]", place)
            for edge in current.get("edges", []) if caller == "chooser" else []:
                for label, yes in (("Known exit", edge.get("ok")), ("Failed test", edge.get("n", 0) > edge.get("ok", 0))):
                    if yes: add(f"{label}: {edge.get('via')} → {edge.get('to')}")
        if caller == "perceiver":
            for row in sorted((r for r in self.rows("entities") if r.get("place") == place), key=lambda r: r["name"].casefold()):
                if not add(f"Known here: {row['name']} ({row['kind']})", row["id"]):
                    break
            return "\n".join(lines), ids
        if task:
            add(f"Task: {task}", str(task))
        history = []
        if caller in ("chooser", "orchestrator"):
            for row in recent:
                choice = row.get("choice") or {}
                history.append(f"Recent: {choice.get('verb', '')} {choice.get('target', '')[:60]} → {row.get('verdict') or 'pending'}")
            history += [f"Failed here: {(r.get('choice') or {}).get('verb', '')} {((r.get('choice') or {}).get('target', ''))[:60]}"
                        for r in recent if r.get("place") == place and r.get("verdict") in ("miss", "surprise")]
            history += [f"Surprise at step {r.get('step')}: {str(r.get('outcome', {}))[:100]}"
                        for r in recent if r.get("verdict") == "surprise"]
        text_reserve = sum(len(r.get("text", "")) + 12 for r in self.rows("knowledge") if r.get("src") == "game_text") if caller == "orchestrator" else 0
        reserve = sum(len(line) + 1 for line in history) + text_reserve
        entities = self.rows("entities")
        for row in sorted(entities, key=lambda r: (r["id"] not in on_screen, r.get("place") != place,
                                                    -r.get("seen", 0), r["id"])):
            if row["id"] not in on_screen and row.get("place") != place:
                continue
            add(f"{row['id']}: {row['name']} ({row['kind']})", row["id"], reserve)
        for line in history:
            add(line)
        rels = sorted((r for r in self.rows("relations") if r.get("state") != "expired"),
                      key=lambda r: (r.get("s") not in on_screen, -r.get("sup", 0), -r.get("step", 0)))
        refuted = 0
        for row in rels:
            if row.get("s") not in on_screen and row.get("o") not in on_screen and caller != "orchestrator":
                continue
            claim = f"{row['s']} {row['p']} {row['o']}"
            if row.get("src") == "model":
                claim += " (unverified)"
            if row.get("state") == "refuted":
                if refuted == 2:
                    continue
                claim = "Don't believe: " + claim
            if add(claim, row["id"], reserve):
                refuted += row.get("state") == "refuted"
        knowledge = self.rows("knowledge")
        if caller == "orchestrator": knowledge.sort(key=lambda r: r.get("src") != "game_text")
        if caller == "chooser":
            knowledge = [r for i, r in enumerate(knowledge) if r.get("text") and not any(o.get("src") == r.get("src") and str(o.get("text", "")).casefold().startswith(str(r["text"]).casefold()) and (len(str(o.get("text", ""))) > len(str(r["text"])) or j > i) for j, o in enumerate(knowledge))][-8:]
        for row in knowledge:
            claim = f"Knowledge: {row['text']}" + (" (unverified)" if row.get("src") == "model" else "")
            if not add(claim, row.get("id"), reserve - text_reserve):
                break
        return "\n".join(lines), ids


def conf(rel: dict) -> float:
    sup, con = rel.get("sup", 0), rel.get("con", 0)
    return (sup + 1) / (sup + con + 2)
