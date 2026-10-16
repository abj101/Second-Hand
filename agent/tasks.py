"""Small no-progress monitor for the act loop."""

# ponytail: set from one run (m1_bedroom_live: the largest K that still fires inside S64-73); a knob, not re-tuned per run
STALL = 8


class Stuck:
    def __init__(self) -> None: self.progress_n, self.seen_places, self.seen_edges, self.u = 0, set(), set(), 0
    def state(self) -> dict: return {"progress_n": self.progress_n, "seen_places": sorted(self.seen_places), "seen_edges": sorted(self.seen_edges), "u": self.u}
    def restore(self, state: dict) -> None:
        self.progress_n, self.seen_places = int(state.get("progress_n", 0)), set(state.get("seen_places", []))
        self.seen_edges, self.u = set(state.get("seen_edges", [])), int(state.get("u", 0))
    def update(self, row: dict) -> str | None:
        if row.get("screen") == "world":
            place, nxt = row.get("place"), row.get("next_place")
            edge = f"{place}>{nxt}" if place and nxt and row.get("verified_edge") else None
            progress = bool(place and place not in self.seen_places) or bool(edge and edge not in self.seen_edges) or row.get("verdict") == "progress"
            if place: self.seen_places.add(place)
            if edge: self.seen_edges.add(edge)
            if progress: self.progress()
            else: self.progress_n += 1
        elif row.get("fresh_text") is False: self.progress_n += 1
        if self.progress_n >= STALL * 2**self.u: self.progress_n = 0; return "no_progress"
        return None
    def progress(self) -> None: self.progress_n, self.u = 0, 0
    def replanned(self, changed: bool) -> None: self.u = 0 if changed else min(self.u + 1, 3)
