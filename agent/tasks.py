"""Task conditions and the small no-progress monitor."""

DEFAULT_GOAL = "Explore and test the world model"


class Tasks:
    def __init__(self, mem) -> None:
        self.mem = mem
        # Set by the loop before check(step).
        self.current_place: str | None = None
        self.perception: dict = {}
        self.settled = True

    def add(self, goal: str, done_when: list, fail_when=(), *, parent: str | None = None,
            budget: int = 100, src: str = "orchestrator") -> dict:
        return self.mem.upsert("tasks", {"id": self.mem._id("tasks", goal), "goal": str(goal),
            "done_when": list(done_when), "fail_when": list(fail_when), "parent": parent,
            "status": "active", "budget": int(budget), "used": 0, "attempts": 0,
            "last_failure": None, "src": src, "last_check": None, "done_streak": 0})

    def active(self) -> dict:
        rows = self.mem.rows("tasks")
        live = [r for r in rows if r.get("status") == "active"]
        if not live:
            return self.add(DEFAULT_GOAL, [], budget=150, src="default")
        by_id = {r["id"]: r for r in rows}

        def depth(row):
            seen, parent, n = set(), row.get("parent"), 0
            while parent in by_id and parent not in seen:
                seen.add(parent); n += 1; parent = by_id[parent].get("parent")
            return n

        return max(enumerate(live), key=lambda pair: (depth(pair[1]), pair[0]))[1]

    def _condition(self, cond: dict, step: int) -> bool:
        if not isinstance(cond, dict) or len(cond) != 1:
            return False
        kind, value = next(iter(cond.items()))
        if kind == "place_is" and isinstance(value, str):
            place = self.current_place
            row = self.mem.get("places", place) if place else None
            return place == value or bool(row and row.get("name", "").casefold() == value.casefold())
        if kind == "entity_known" and isinstance(value, str):
            names = [e.get("name", "") for e in self.perception.get("entities", []) if isinstance(e, dict)]
            return any(str(n).casefold() == value.casefold() for n in names) or any(
                str(r.get("name", "")).casefold() == value.casefold() for r in self.mem.rows("entities"))
        if kind == "text_seen" and isinstance(value, str):
            texts = self.perception.get("text", [])
            if isinstance(texts, str): texts = [texts]
            return (any(value.casefold() in str(t).casefold() for t in texts)
                    or any(r.get("src") == "game_text" and value.casefold() in
                           str(r.get("text", "")).casefold() for r in self.mem.rows("knowledge")))
        if kind == "relation" and isinstance(value, (list, tuple)) and len(value) == 3:
            return any(tuple(r.get(k) for k in ("s", "p", "o")) == tuple(value)
                       and r.get("src") == "observed" and r.get("state") == "live"
                       for r in self.mem.rows("relations"))
        return kind == "steps_over" and type(value) in (int, float) and step > value

    def check(self, step: int) -> list[dict]:
        changed = []
        for task in self.mem.rows("tasks"):
            if task.get("status") != "active":
                continue
            old = task["status"]
            last = task.get("last_check")
            if last is None or step > last:
                task["used"] = int(task.get("used", 0)) + 1
                consecutive = last == step - 1
                done = bool(task.get("done_when")) and all(
                    self._condition(c, step) for c in task["done_when"])
                task["done_streak"] = (task.get("done_streak", 0) + 1 if consecutive else 1) \
                    if self.settled and done else 0
                task["last_check"] = step
            failed = any(self._condition(c, step) for c in task.get("fail_when", []))
            if failed or task["used"] >= task["budget"]:
                task.update(status="failed", last_failure="fail_when condition met" if failed else "budget exhausted")
            elif self.settled and task.get("done_streak", 0) >= 2:
                task["status"] = "done"
            self.mem.upsert("tasks", task)
            if task["status"] != old: changed.append(task)
        return changed

    def wake(self, row: dict, step: int) -> str | None:
        return None  # E4


class Stuck:
    def __init__(self) -> None:
        self.n, self.action, self.hash, self.same_action = 0, None, None, 0
        self.repetitions, self.progress_n, self.seen_places = [], 0, set()

    def state(self) -> dict:
        return {"n": self.n, "action": self.action, "hash": self.hash,
                "same_action": self.same_action, "repetitions": self.repetitions,
                "progress_n": self.progress_n, "seen_places": sorted(self.seen_places)}

    def restore(self, state: dict) -> None:
        self.n, action, self.hash = int(state.get("n", 0)), state.get("action"), state.get("hash")
        self.action = tuple(action) if isinstance(action, list) else action
        self.same_action, self.progress_n, self.seen_places = int(state.get("same_action", 0)), int(state.get("progress_n", 0)), set(state.get("seen_places", []))
        self.repetitions = [[tuple(a), frame, int(count)] for a, frame, count
                            in state.get("repetitions", [])][-32:]
        if not self.repetitions and self.action and self.hash and self.same_action:
            self.repetitions = [[self.action, self.hash, self.same_action]]

    def update(self, row: dict) -> str | None:
        if row.get("task_budget_exhausted"):
            return "budget"
        outcome = row.get("outcome") or {}
        no_change = "no_change" in outcome.get("effects", []) or row.get("options") == 0
        self.n = self.n + 1 if no_change else 0
        if row.get("screen") == "world":
            place, nxt = row.get("place"), row.get("next_place")
            current_new = bool(place and self.seen_places and place not in self.seen_places)
            if place: self.seen_places.add(place)
            place_changed = "place_changes" in outcome.get("effects", []) and outcome.get("basis", {}).get("place_changes") == "pixel"
            progress = current_new or (nxt and nxt not in self.seen_places and place_changed) or row.get("verified_edge")
            if nxt: self.seen_places.add(nxt)
            self.progress_n = 0 if progress else self.progress_n + 1
        choice = row.get("choice") or {}
        action = (choice.get("verb"), choice.get("target"), choice.get("id")) if choice else None
        frame = row.get("frame_hash") or (row.get("frame") if not no_change else None)
        match = next((item for item in self.repetitions
                      if item[0] == action and item[1] == frame), None)
        if action and frame is not None:
            if match:
                match[2] += 1
            else:
                match = [action, frame, 1]; self.repetitions.append(match)
            self.repetitions = self.repetitions[-32:]
        self.same_action = match[2] if match else 0
        self.action = action
        if frame is not None: self.hash = frame
        if self.same_action >= 3:
            self.repetitions.remove(match); self.same_action = 0
            return "same_action"
        if self.progress_n >= 25: self.progress_n = 0; return "no_progress"
        return "no_change" if self.n >= 8 else None
