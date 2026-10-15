"""Small no-progress monitor for the act loop."""


class Stuck:
    def __init__(self) -> None:
        self.n, self.action, self.hash, self.same_action = 0, None, None, 0
        self.repetitions, self.progress_n, self.seen_places, self.seen_edges = [], 0, set(), set()

    def state(self) -> dict:
        return {"n": self.n, "action": self.action, "hash": self.hash,
                "same_action": self.same_action, "repetitions": self.repetitions,
                "progress_n": self.progress_n, "seen_places": sorted(self.seen_places),
                "seen_edges": sorted(self.seen_edges)}

    def restore(self, state: dict) -> None:
        self.n, action, self.hash = int(state.get("n", 0)), state.get("action"), state.get("hash")
        self.action = tuple(action) if isinstance(action, list) else action
        self.same_action, self.progress_n, self.seen_places = int(state.get("same_action", 0)), int(state.get("progress_n", 0)), set(state.get("seen_places", []))
        self.seen_edges = set(state.get("seen_edges", []))
        self.repetitions = [[tuple(a), frame, int(count)] for a, frame, count
                            in state.get("repetitions", [])][-32:]
        if not self.repetitions and self.action and self.hash and self.same_action:
            self.repetitions = [[self.action, self.hash, self.same_action]]

    def update(self, row: dict) -> str | None:
        outcome = row.get("outcome") or {}
        no_change = "no_change" in outcome.get("effects", []) or row.get("options") == 0
        self.n = self.n + 1 if no_change else 0
        if row.get("screen") == "world":
            place, nxt = row.get("place"), row.get("next_place")
            current_new = bool(place and place not in self.seen_places)
            if place: self.seen_places.add(place)
            edge = f"{place}>{nxt}" if place and nxt else None
            new_edge = bool(row.get("verified_edge") and edge and edge not in self.seen_edges)
            if new_edge: self.seen_edges.add(edge)
            if current_new or new_edge: self.progress()
            else: self.progress_n += 1
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
        if self.progress_n >= 20: self.progress_n = 0; return "no_progress"
        return "no_change" if self.n >= 8 else None

    def progress(self) -> None:
        self.progress_n = 0
