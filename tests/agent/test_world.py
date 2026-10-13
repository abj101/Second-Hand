from agent.memory import Memory
from agent.world import World


def place(mem, id, name, first=1):
    mem.upsert("places", {"id": id, "name": name, "screen": "room", "state": "place", "first": first, "last": first, "edges": []})


def entity(mem, id, name, place, tile, seen=2):
    mem.upsert("entities", {"id": id, "name": name, "kind": "object", "place": place, "tile": tile, "seen": seen})


def test_merge_shifts_unions_redirects_and_collapses(tmp_path):
    mem = Memory(tmp_path); place(mem, "src", "room"); place(mem, "dst", "Hall"); place(mem, "from", "Start")
    entity(mem, "s1", "Oak", "src", [1, 1]); entity(mem, "s2", "Oak", "src", [1, 2], 3)
    entity(mem, "s3", "Pine", "src", [2, 1]); entity(mem, "d1", "Oak", "dst", [5, 6], 4)
    entity(mem, "d2", "Pine", "dst", [6, 6])
    mem.upsert("tiles", {"id": "src@1,1", "place": "src", "x": 1, "y": 1, "floor": True, "blocked": ["UP"], "pushed": [], "exit": None, "step": 1})
    mem.upsert("tiles", {"id": "dst@5,6", "place": "dst", "x": 5, "y": 6, "floor": False, "blocked": [], "pushed": ["LEFT"], "exit": {"dir": "UP", "to": "from", "arrive": [0, 0]}, "step": 1})
    mem.upsert("tiles", {"id": "from@0,0", "place": "from", "x": 0, "y": 0, "floor": True, "blocked": [], "pushed": [], "exit": {"dir": "DOWN", "to": "src", "arrive": [1, 1]}, "step": 1})
    mem.upsert("places", {**mem.get("places", "from"), "edges": [{"verb": "enter", "via": "door", "to": "src"}]})
    mem.upsert("relations", {"id": "r1", "s": "src", "p": "leads_to", "o": "from", "state": "live"})
    world = World(mem, 16); world.current_place, world.pos = "src", (3, 4)
    assert world.merge("src", "dst", 8)
    assert mem.get("places", "src")["offset"] == [4, 5] and world.pos == (7, 9)
    tile = mem.get("tiles", "dst@5,6")
    assert tile["floor"] is True and tile["blocked"] == ["UP"] and tile["pushed"] == ["LEFT"]
    assert mem.get("tiles", "from@0,0")["exit"]["to"] == "dst" and mem.get("tiles", "from@0,0")["exit"]["arrive"] == [5, 6]
    assert mem.get("places", "from")["edges"][0]["to"] == "dst" and mem.get("relations", "r1")["s"] == "dst"
    assert mem.get("entities", "d1")["seen"] == 9 and mem.get("entities", "s2")["place"] == "src"
    replay = Memory(tmp_path)
    assert replay.get("places", "src")["state"] == "merged" and replay.get("places", "src")["into"] == "dst"
    assert replay.resolve("Oak", "dst", (5, 6)) == "d1"
    assert replay.observe({"entities": [{"name": "Oak", "kind": "object"}]}, "dst", 9, {0: (5, 6)}) == {0: "d1"}


def test_name_refusal_alias_flag_and_new_arrival(tmp_path):
    mem = Memory(tmp_path); place(mem, "a", "Room", 1); place(mem, "b", "Room B", 1)
    entity(mem, "a1", "Oak", "a", [0, 0]); entity(mem, "a2", "Oak#2", "a", [1, 0])
    entity(mem, "b1", "Oak", "b", [4, 4]); entity(mem, "b2", "Oak#2", "b", [5, 4])
    world = World(mem, 16)
    assert world.name("a", "room b", 3) == "a"
    assert mem.get("places", "a")["aliases"] == ["room b"] and world.merge_pending == "room b"
    place(mem, "new", "screen", 5); place(mem, "old", "Known", 1)
    assert world.name("new", "known", 5) == "old" and mem.get("places", "new")["into"] == "old"


def test_resolve_and_plan_skip_merged_history_and_plan_entity_facing(tmp_path):
    mem = Memory(tmp_path)
    for key in ("a", "b", "c", "old"): place(mem, key, key)
    mem.upsert("places", {**mem.get("places", "old"), "state": "merged", "into": "c"})
    for key, pos, dest in (("a", (1, 0), "b"), ("b", (1, 0), "c"), ("old", (0, 0), "c")): mem.upsert("tiles", {"id": f"{key}@{pos[0]},{pos[1]}", "place": key, "x": pos[0], "y": pos[1], "floor": True, "blocked": [], "pushed": [], "exit": {"dir": "RIGHT", "to": dest, "arrive": [0, 0]}, "step": 1})
    for pos in ((0, 0), (2, 0)): mem.upsert("tiles", {"id": f"c@{pos[0]},{pos[1]}", "place": "c", "x": pos[0], "y": pos[1], "floor": True, "blocked": [], "pushed": [], "exit": None, "step": 1})
    entity(mem, "fern", "Fern", "c", [1, 0]); entity(mem, "ghost", "Ghost", "old", [0, 0])
    mem.upsert("entities", {**mem.get("entities", "fern"), "status": "remembered"})
    world = World(mem, 16); world.current_place = "a"
    target = world.resolve("Fern", {"entities": []}, "a")
    assert target == {"kind": "memory", "place": "c", "tile": (1, 0), "id": "fern", "stale": False}
    assert world.resolve("Ghost", {"entities": []}, "a") is None
    legs = world.plan(target)
    assert [leg["place"] for leg in legs] == ["a", "b", "c"]
    assert legs[-1]["dirs"] == ["RIGHT"]
    world.current_place = "old"
    assert world.plan({**target, "place": "c"}) is None


def test_plan_routes_to_unknown_entity_neighbor_around_blocked_edges(tmp_path):
    from agent.space import DIRS
    from tests.agent.fakes import GridEnv, StubModel

    mem = Memory(tmp_path); place(mem, "room", "room"); entity(mem, "fern", "Fern", "room", [-1, 1])
    mem.upsert("tiles", {"id": "room@-1,0", "place": "room", "x": -1, "y": 0, "floor": False, "blocked": [], "pushed": [], "exit": None, "step": 1})
    mem.upsert("tiles", {"id": "room@0,0", "place": "room", "x": 0, "y": 0, "floor": True, "blocked": ["DOWN", "RIGHT"], "pushed": [], "exit": None, "step": 1})
    env, model = GridEnv(), StubModel([]); world = World(mem, 16); world.current_place = "room"
    target = world.resolve("Fern", {"entities": []}, "room")

    legs = world.plan(target)

    dirs = legs[-1]["dirs"]; at = (0, 0)
    for direction in dirs[:-1]:
        dx, dy = DIRS[direction]; at = (at[0] + dx, at[1] + dy)
        assert at != (-1, 0)
    assert at == (0, 1) and dirs[-1] == "LEFT" and dirs[0] not in ("DOWN", "RIGHT")
    assert mem.get("tiles", "room@0,0")["blocked"] == ["DOWN", "RIGHT"]
    assert len(mem.rows("tiles")) == 2 and model.calls == 0 and env.player == (8, 8)


def test_travel_leg_cap_and_blocked_leg_reason(tmp_path, monkeypatch):
    from agent.skills import Ctx, SkillResult; from tests.agent.fakes import GridEnv, StubModel; from agent import world as world_module

    mem = Memory(tmp_path); place(mem, "room", "room")
    env, model = GridEnv(), StubModel([]); world = World(mem, 16); world.current_place = "room"
    def consume(ctx, target, player, **kwargs): frame = ctx.env.screenshot(); return SkillResult("done", "", ctx.max_inputs, 0, frame, frame, (0, 0), False)
    monkeypatch.setattr(world_module, "approach", consume)
    legs = [{"place": "room", "dirs": ["UP"] * 30, "push": None} for _ in range(5)]
    result = world.travel(Ctx(env, model, 16), legs, 1)
    assert result.inputs == 120
    assert result.calls == model.calls == 0 and result.reason == "travel: leg 5 input limit"
    frame = env.screenshot(); monkeypatch.setattr(world_module, "approach", lambda ctx, *_a, **_k: SkillResult("blocked", "route blocked", 1, 0, frame, frame, (0, 0), False, [("UP", (0, 0))]))
    result = world.travel(Ctx(env, model, 16), [{"place": "room", "dirs": ["UP"], "push": None}], 2)
    assert result.reason == "travel: leg 1 route blocked"


def test_travel_corrects_wrong_arrival_from_exact_trace_frame(tmp_path, monkeypatch):
    import hashlib; from PIL import Image; from agent.skills import Ctx, SkillResult; from agent.trace import Trace; from agent import world as world_module; from tests.agent.fakes import GridEnv, StubModel

    mem = Memory(tmp_path); place(mem, "source", "Source"); place(mem, "expected", "Expected"); place(mem, "actual", "Actual")
    row = {"id": "source@1,0", "place": "source", "x": 1, "y": 0, "floor": True, "blocked": [], "pushed": [], "exit": {"dir": "RIGHT", "to": "expected", "arrive": [0, 0]}}
    mem.upsert("tiles", row); frame = Image.new("RGB", (8, 8), "red"); digest = hashlib.sha256(frame.tobytes()).hexdigest()
    trace = Trace(tmp_path / "trace.jsonl")
    trace.write({"place": "source", "next_place": "actual", "tile": [3, 4]})
    trace.write({"place": "actual", "frame_hash": digest, "next_place": "expected", "tile": [99, 99]})
    env, model = GridEnv(), StubModel([]); world = World(mem, 16); world.current_place = "source"
    monkeypatch.setattr(world_module, "approach", lambda ctx, *_a, **_k: SkillResult("done", "scene cut", 1, 0, frame, frame, (0, 0), False, [("RIGHT", (0, 0))]))
    result = world.travel(Ctx(env, model, 16), [{"place": "source", "dirs": ["RIGHT"], "push": "RIGHT", "expect": "expected", "exit_tile": (1, 0)}], 3, trace)
    assert model.calls == 0 and result.reason == "travel: leg 1 arrival mismatch"
    assert world.current_place == "actual" and world.pos == (3, 4)
    assert {k: mem.get("tiles", row["id"])["exit"][k] for k in ("to", "arrive")} == {"to": "actual", "arrive": [3, 4]}
    modal = Trace(tmp_path / "modal.jsonl"); modal.write({"place": "source", "next_place": "actual", "tile": [3, 4]}); modal.write({"place": "actual", "screen": "dialogue", "frame_hash": digest})
    world2 = World(mem, 16); world2.current_place = "source"
    assert world2.travel(Ctx(env, model, 16), [{"place": "source", "dirs": ["RIGHT"], "push": "RIGHT", "expect": "expected", "exit_tile": (1, 0)}], 4, modal).reason == "travel: leg 1 modal opened"


def test_loop_travel_fetch_leg_traverses_three_rooms_and_faces_remembered_target(tmp_path, monkeypatch):
    from PIL import ImageDraw
    from types import SimpleNamespace
    from agent.loop import Loop
    from agent import loop as loop_module, world as world_module
    from tests.agent.fakes import GridEnv, StubModel

    class ThreeRooms(GridEnv):
        def __init__(self): super().__init__(); self.room, self.facing, self.npc = 0, None, (9, 8)
        def _frame(self):
            if self._transition:
                self._transition += 1
                if self._transition > 2: self.room += 1; self.scene = self.room; self.player = (8, 8); self._transition = 0
            elif self._moving:
                self._travel += 2
                if self._travel >= self.tile_px:
                    x, y = self.player; dx, dy = self._steps[self._direction]; self.player, self._travel, self._moving = (x+dx, y+dy), 0, False
                    if self.player == self.door and self.room < 2: self._transition = 1
        def press(self, key, frames=8, on_frame=None):
            if key in self._steps:
                self._direction = self.facing = key; dx, dy = self._steps[key]; dest = (self.player[0]+dx, self.player[1]+dy)
                self._moving = dest not in self.walls and not (self.room == 2 and dest == self.npc)
            for _ in range(frames+8):
                self._frame()
                if on_frame: on_frame(self.screenshot())
            self._direction = ""
        def screenshot(self):
            image = super().screenshot()
            if self.room == 2: ImageDraw.Draw(image).rectangle((88, 64, 103, 79), fill=(88, 77, 171))
            return image

    mem = Memory(tmp_path / "memory"); [mem.upsert("places", {"id": p, "name": p, "screen": "world", "state": "place", "edges": []}) for p in ("a", "b", "c")]
    for p, q in (("a", "b"), ("b", "c")): mem.upsert("tiles", {"id": f"{p}@1,0", "place": p, "x": 1, "y": 0, "floor": True, "blocked": [], "pushed": [], "exit": {"dir": "RIGHT", "to": q, "arrive": [0, 0]}})
    for x in (0, 2): mem.upsert("tiles", {"id": f"c@{x},0", "place": "c", "x": x, "y": 0, "floor": True, "blocked": [], "pushed": [], "exit": None})
    entity(mem, "fern", "Fern", "c", [1, 0]); mem.upsert("entities", {**mem.get("entities", "fern"), "status": "remembered"})
    env = ThreeRooms(); player = {"name": "Player", "kind": "player", "at": [.5, .5]}; fern = {"name": "Fern", "kind": "npc", "at": [.6, .5]}
    initial = {"screen": "world", "text": [], "entities": [player], "affordances": []}; final = {**initial, "entities": [player, fern]}; model = StubModel([SimpleNamespace(data=initial), SimpleNamespace(data=final)])
    loop = Loop(env, model, mem, "grid", act_mode=True); loop.place, loop.prev_frame = "a", env.screenshot(); acts, calls = [], []
    monkeypatch.setattr(loop_module, "act", lambda *_a, **_k: (acts.append(model.calls) or SimpleNamespace(place="a", ops=[], action={"go_to": "Fern"}, expect="screen_changes", why="test", text=None)))
    approach = world_module.approach
    def watch(ctx, *args, **kwargs):
        before = ctx.model.calls; result = approach(ctx, *args, **kwargs); calls.append((before, ctx.model.calls)); return result
    monkeypatch.setattr(world_module, "approach", watch)
    row = loop.step()
    assert acts == [1] and model.calls == 2 and calls == [(1, 1)] * 3
    assert row["next_place"] == "c" and env.room == 2 and env.player == (8, 8) and env.facing == "RIGHT" and abs(env.player[0]-env.npc[0])+abs(env.player[1]-env.npc[1]) == 1
    assert row["act"]["action"]["go_to"] == "Fern" and row["skill"]["reason"] == ""


def test_evidence_counts_distinct_world_frames_and_restores_seen_entities(tmp_path):
    from PIL import Image

    mem = Memory(tmp_path); place(mem, "room", "room")
    world = World(mem, 16); player = {"name": "Player", "kind": "player", "at": [.5, .5]}
    item = {"name": "Coin", "kind": "object", "at": [.6, .5]}
    world.before({"screen": "world", "entities": [player, item], "text": [], "affordances": [{"target": "Coin"}]}, "room", None, (160, 144), 1)
    ident = next(r["id"] for r in mem.rows("entities") if r["name"] == "Coin")
    for step, color in ((2, "red"), (3, "blue")):
        world.observe_after({"screen": "world", "entities": [player]}, Image.new("RGB", (160, 144), color), False, "room", (160, 144), step)
        assert mem.get("entities", ident)["misses"] == step - 1
        assert mem.get("entities", ident)["status"] == ("stale" if step == 3 else "remembered")
    frame = Image.new("RGB", (160, 144), "green")
    world.observe_after({"screen": "world", "entities": [player]}, frame, True, "room", (160, 144), 4)
    assert mem.get("entities", ident)["misses"] == 2
    modal = {"name": "Text box", "kind": "dialogue_box", "at": [.5, .9]}
    world.observe_after({"screen": "world", "entities": [player, modal]}, Image.new("RGB", (160, 144), "yellow"), False, "room", (160, 144), 5)
    assert mem.get("entities", ident)["misses"] == 2
    world.observe_after({"screen": "world", "entities": [player]}, Image.new("RGB", (160, 144), "purple"), False, "room", (160, 144), 6)
    assert mem.get("entities", ident)["misses"] == 3 and mem.get("entities", ident)["status"] == "stale"
    world.observe_after({"screen": "world", "entities": [player, item]}, Image.new("RGB", (160, 144), "black"), False, "room", (160, 144), 7)
    assert mem.get("entities", ident)["status"] == "seen" and mem.get("entities", ident)["misses"] == 0
    for step, color in zip(range(8, 12), ("white", "orange", "cyan", "gray")):
        world.observe_after({"screen": "world", "entities": [player]}, Image.new("RGB", (160, 144), color), False, "room", (160, 144), step)
    assert mem.get("entities", ident)["misses"] == 4 and mem.get("entities", ident)["status"] == "gone"


def test_seen_cells_union_the_full_view_rectangles(tmp_path):
    from PIL import Image
    from types import SimpleNamespace
    from tests.agent.fakes import GridEnv

    mem = Memory(tmp_path); place(mem, "room", "room"); world = World(mem, 16)
    env = GridEnv()
    player = {"name": "Player", "kind": "player", "at": [.5, .5]}
    world.before({"screen": "world", "entities": [player], "text": [], "affordances": []}, "room", None, (160, 144), 1)
    world.observe_after({"screen": "world", "entities": [player]}, env.screenshot(), False, "room", (160, 144), 1)
    env.press("RIGHT", frames=16)
    world.prepare(SimpleNamespace(moves=[("RIGHT", (-16, 0))]), "room", False, 2)
    frame = env.screenshot()
    world.observe_after({"screen": "world", "entities": [player]}, frame, False, "room", (160, 144), 2)
    cells = set(mem.get("seen", "room")["cells"])
    assert len(cells) == 99 and {"-4,-4", "5,4", "-3,-4", "6,4"} <= cells
    world.observe_after({"screen": "world", "entities": [player]}, frame, False, "room", (160, 144), 3)
    assert len((tmp_path / "seen.jsonl").read_text(encoding="utf-8").splitlines()) == 2


def test_tiers_keep_landmarks_promote_interest_blockers_and_cap_is_not_deletion(tmp_path):
    from types import SimpleNamespace

    mem = Memory(tmp_path); place(mem, "room", "room")
    mem.upsert("tiles", {"id": "room@-2,0", "place": "room", "x": -2, "y": 0, "floor": True, "blocked": [], "pushed": [], "exit": None})
    p = {"name": "Player", "kind": "player", "at": [.5, .5]}
    entities = [p, {"name": "Oak", "kind": "object", "at": [.6, .5]},
                {"name": "Guide", "kind": "npc", "at": [.4, .5]},
                {"name": "exit: path south", "kind": "affordance", "at": [.3, .5]},
                {"name": "Chest", "kind": "object", "at": [.55, .6]},
                {"name": "Sign", "kind": "object", "at": [.45, .6]}]
    world = World(mem, 16)
    _, _, ids = world.before({"screen": "world", "entities": entities, "text": ["Sign"], "affordances": [{"verb": "interact", "target": "Chest"}]}, "room", None, (160, 144), 1)
    oak, guide, exit_id, chest, sign = (ids[i] for i in (1, 2, 3, 4, 5))
    assert mem.get("entities", oak)["tier"] == "landmark"
    assert mem.get("entities", guide)["tier"] == "interest"
    assert mem.get("entities", chest)["tier"] == mem.get("entities", sign)["tier"] == "interest"
    mem.upsert("places", {**mem.get("places", "room"), "edges": [{"via": "exit: path south", "ok": 0}]})
    mem.upsert("relations", {"id": "failed_exit", "s": exit_id, "p": "leads_to", "o": "other", "src": "observed", "sup": 1, "con": 2, "state": "refuted"})
    world._tiers({"entities": [entities[3]], "text": [], "affordances": [{"verb": "approach", "target": "exit: path south"}]}, "room", {0: exit_id})
    assert mem.get("entities", exit_id)["tier"] == "landmark"
    world.prepare(SimpleNamespace(moves=[("RIGHT", -16), ("RIGHT", (0, 0))]), "room", False, 2)
    assert mem.get("entities", oak)["tier"] == "interest"
    for i in range(10):
        mem.upsert("entities", {"id": f"landmark{i}", "name": f"Landmark {i}", "kind": "object", "place": "room", "tier": "landmark", "seen": i})
    assert len([r for r in mem.rows("entities") if r.get("tier") == "landmark"]) > 8


def test_actor_observation_radius_and_status_reset(tmp_path):
    mem = Memory(tmp_path)
    ident = mem.observe({"entities": [{"name": "Guide", "kind": "npc"}]}, "room", 1, {0: (0, 0)})[0]
    mem.upsert("entities", {**mem.get("entities", ident), "status": "stale", "misses": 3})
    assert mem.observe({"entities": [{"name": "Guide", "kind": "person"}]}, "room", 13, {0: (4, 0)}) == {0: ident}
    assert mem.get("entities", ident)["status"] == "seen" and mem.get("entities", ident)["misses"] == 0


def test_resolve_place_labels_precede_entity_names_and_gone_targets_remain_executable(tmp_path):
    mem = Memory(tmp_path); place(mem, "p_world", "world"); place(mem, "p_other", "WORLD"); place(mem, "p_2f", "2F")
    entity(mem, "world-thing", "world", "p_2f", [1, 0])
    entity(mem, "gone-chest", "Chest", "p_2f", [2, 0])
    mem.upsert("entities", {**mem.get("entities", "gone-chest"), "status": "gone", "tier": "landmark"})
    world = World(mem, 16); world.current_place = "p_2f"
    screen = {"entities": [{"name": "world", "kind": "object"}]}

    assert world.place_label("p_world") == "world [p_world]"
    assert world.resolve("world [p_world]", screen, "p_2f")["id"] == "p_world"
    assert world.resolve("p_world", screen, "p_2f")["id"] == "p_world"
    assert world.resolve("Chest", {"entities": []}, "p_2f") == {
        "kind": "memory", "place": "p_2f", "tile": (2, 0), "id": "gone-chest", "stale": False}
    assert world.resolve("world", screen, "p_2f")["kind"] == "screen"


def test_world_view_is_bounded_deterministic_promotes_goal_and_caps_landmark_map(tmp_path):
    mem = Memory(tmp_path); place(mem, "here", "Atrium"); place(mem, "other", "Kitchen"); place(mem, "merged", "Kitchen old")
    mem.upsert("places", {**mem.get("places", "merged"), "state": "merged", "into": "other"})
    mem.upsert("tiles", {"id": "here@0,0", "place": "here", "x": 0, "y": 0, "floor": True, "blocked": ["RIGHT"], "pushed": [], "exit": None, "step": 1})
    mem.upsert("tiles", {"id": "here@0,-1", "place": "here", "x": 0, "y": -1, "floor": True, "blocked": [], "pushed": [], "exit": {"dir": "UP", "to": "other", "arrive": [2, 3]}, "step": 1})
    mem.upsert("tiles", {"id": "other@2,3", "place": "other", "x": 2, "y": 3, "floor": True, "blocked": [], "pushed": [], "exit": None, "step": 1})
    mem.upsert("seen", {"id": "here", "cells": ["3,2", "4,2"], "step": 5})
    entity(mem, "goal", "Chest", "here", [1, 0], 2)
    mem.upsert("entities", {**mem.get("entities", "goal"), "tier": "landmark", "status": "stale", "last": 4})
    entity(mem, "gone", "Vanished", "here", [2, 0]); mem.upsert("entities", {**mem.get("entities", "gone"), "tier": "interest", "status": "gone"})
    entity(mem, "merged-ghost", "Merged ghost", "merged", [1, 1]); mem.upsert("entities", {**mem.get("entities", "merged-ghost"), "tier": "interest"})
    for i in range(10):
        entity(mem, f"landmark{i}", f"Landmark {i}", "here", [i - 4, 2], i)
        mem.upsert("entities", {**mem.get("entities", f"landmark{i}"), "tier": "landmark", "last": i})
    entity(mem, "kitchen-maple", "Maple", "other", [2, 3]); mem.upsert("entities", {**mem.get("entities", "kitchen-maple"), "tier": "interest", "status": "remembered"})
    world = World(mem, 16); world.current_place, world.pos = "here", (0, 0)

    view = world.view("Find Chest near Maple", "here")

    assert view == world.view("Find Chest near Maple", "here") and len(view) <= 2000
    assert view.startswith("Here: Atrium (0,0)  map: 2 walked, 2 seen,")
    assert "Map: . walked, , seen, # blocked side, E exit, @ you" in view
    assert "E UP (0,-1) -> Kitchen" in view and "Place: Kitchen" in view
    assert "Thing A Chest (1, 0) object last seen (1, 0) step 4; may have moved" in view
    assert "Vanished" not in view and "Merged ghost" not in view
    assert mem.get("entities", "goal")["tier"] == "interest"
    assert all(f"Landmark {i}" in view for i in range(2, 10))
    assert all(f"Landmark {i}" not in view for i in range(2))
    assert view.index("Place: Kitchen") < view.index("Edges:")


def test_world_view_place_ranking_prefers_goal_overlap_and_keeps_duplicate_labels_unique(tmp_path):
    mem = Memory(tmp_path); place(mem, "here", "world"); place(mem, "p_world", "world"); place(mem, "goal", "Forest")
    place(mem, "recent", "Cave", first=9)
    for src, dst, ident in (("here", "goal", "a"), ("here", "recent", "b")):
        mem.upsert("tiles", {"id": f"{src}@{ident}", "place": src, "x": 0 if ident == "a" else 1, "y": 0,
                              "floor": True, "blocked": [], "pushed": [], "exit": {"dir": "RIGHT", "to": dst, "arrive": [0, 0]}, "step": 1})
    world = World(mem, 16); world.current_place = "here"
    labels = [world.place_label(p["id"]) for p in mem.rows("places") if p.get("state") != "merged"]

    view = world.view("reach forest", "here")

    assert len(labels) == len(set(labels)) and "world [here]" in labels and "world [p_world]" in labels
    assert view.index("Place: Forest") < view.index("Place: Cave")


def test_world_view_groups_entities_and_hides_unseen_model_stubs(tmp_path):
    from tests.agent.fakes import GridEnv, StubModel

    mem = Memory(tmp_path); place(mem, "here", "Atrium")
    for i in range(17):
        entity(mem, f"shelf-{i}", "shelf#11#16" if i == 16 else f"shelf#{i + 1}", "here", [i % 5, 0], i + 1)
        row = mem.get("entities", f"shelf-{i}")
        mem.upsert("entities", {**row, "tier": "interest", "status": "stale" if i == 0 else "remembered"})
    mem.upsert("entities", {"id": "stub", "name": "path east", "kind": "affordance", "place": "here",
                            "src": "model", "tile": None, "seen": 0, "tier": "interest"})
    env, model = GridEnv(), StubModel([])
    world = World(mem, 16); world.current_place, world.pos = "here", (0, 0)
    before = mem.rows("entities")

    view = world.view("", "here")

    shelf_lines = [line for line in view.splitlines() if "shelf" in line.casefold()]
    assert shelf_lines == ["Thing A shelf ×17 (0, 0) object remembered step 0 (nearest (0, 0))"]
    assert "path east" not in view and "stub" not in view
    assert mem.rows("entities") == before and model.calls == 0 and env.player == (8, 8)


def test_world_view_lists_all_place_exits_before_any_other_place_things(tmp_path):
    mem = Memory(tmp_path); place(mem, "here", "Atrium")
    for name in ("North", "South", "West"):
        place(mem, name.lower(), name)
        mem.upsert("tiles", {"id": f"{name.lower()}@0,0", "place": name.lower(), "x": 0, "y": 0,
                              "floor": True, "blocked": [], "pushed": [],
                              "exit": {"dir": "DOWN", "to": "here", "arrive": [0, 0]}, "step": 1})
        entity(mem, f"thing-{name}", f"Thing {name}", name.lower(), [1, 0])
        mem.upsert("entities", {**mem.get("entities", f"thing-{name}"), "tier": "interest"})
    for i in range(60):
        mem.upsert("entities", {"id": f"clutter-{i}", "name": f"Clutter {i}", "kind": "object",
                                "place": "here", "tile": None, "seen": 1, "tier": "interest"})
    world = World(mem, 16); world.current_place = "here"

    view = world.view("", "here")

    headings = [view.index(f"Place: {name}") for name in ("North", "South", "West")]
    exit_rows = [line for line in view.splitlines() if line == "  Exit DOWN -> Atrium"]
    exits = [view.index(line) for line in exit_rows]
    first_thing = min(i for i in (view.find("Thing "), view.find("Thing:"), view.find("Landmark ")) if i >= 0)
    assert len(set(headings)) == 3 and len(exits) == 3 and max(headings + exits) < first_thing and len(view) <= 2000


def test_world_view_labels_other_place_detail_bundle(tmp_path):
    mem = Memory(tmp_path); place(mem, "here", "Atrium"); place(mem, "hall", "Hall")
    for source, target, direction in (("here", "hall", "RIGHT"), ("hall", "here", "LEFT")):
        mem.upsert("tiles", {"id": f"{source}@0,0", "place": source, "x": 0, "y": 0, "floor": True,
                              "blocked": [], "pushed": [], "exit": {"dir": direction, "to": target, "arrive": [0, 0]}, "step": 1})
    entity(mem, "chest", "Chest", "hall", [1, 0])
    mem.upsert("entities", {**mem.get("entities", "chest"), "tier": "interest"})
    world = World(mem, 16); world.current_place = "here"

    view = world.view("", "here")

    lines = view.splitlines()
    assert lines.index("Place: Hall") < lines.index("  Exit LEFT -> Atrium") < lines.index("Things: Hall")
    assert lines[lines.index("Things: Hall") + 1] == "  Thing: Chest remembered"


def test_loop_act_uses_world_facts_active_goals_and_labels_but_gated_view_keeps_describe(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from agent.loop import Loop
    from agent import loop as loop_module
    from tests.agent.fakes import GridEnv, StubModel

    mem = Memory(tmp_path / "act"); place(mem, "home", "Home"); place(mem, "p_world", "world"); place(mem, "p_world2", "world")
    mem.upsert("tiles", {"id": "home@0,0", "place": "home", "x": 0, "y": 0, "floor": True, "blocked": [], "pushed": [], "exit": {"dir": "RIGHT", "to": "p_world", "arrive": [1, 0]}, "step": 1})
    entity(mem, "chest", "Chest", "home", [1, 0]); mem.upsert("entities", {**mem.get("entities", "chest"), "tier": "landmark"})
    entity(mem, "oak", "Oak", "home", [2, 0]); mem.upsert("entities", {**mem.get("entities", "oak"), "tier": "landmark"})
    per = {"screen": "world", "text": [], "entities": [{"name": "Player", "kind": "player", "at": [.5, .5]},
                                                                  {"name": "Chest", "kind": "object", "at": [.6, .5]}], "affordances": []}
    env = GridEnv(); model = StubModel([SimpleNamespace(data=per)]); loop = Loop(env, model, mem, "grid", act_mode=True)
    loop.place, loop.prev_frame = "home", env.screenshot()
    loop.notebook.data["goals"] = {"active": {"text": "Find Chest", "status": "active"},
                                   "finished": {"text": "Find Oak", "status": "done"}}
    loop.notebook.data["hints"] = {"hint": {"text": "Oak", "status": "active"}}
    seen = {}
    monkeypatch.setattr(loop_module, "act", lambda *args, **kwargs: (seen.update(view=args[4], places=kwargs["places"]) or
                      SimpleNamespace(place="Home", ops=[], action=None, expect="none", why="test", text=None)))

    loop.step()

    assert "Here: Home" in seen["view"] and "Thing" in seen["view"] and "Chest" in seen["view"]
    assert "world [p_world]" in seen["places"] and "world [p_world2]" in seen["places"]
    assert mem.get("entities", "chest")["tier"] == "interest" and mem.get("entities", "oak")["tier"] == "landmark"

    gated_mem = Memory(tmp_path / "gated"); place(gated_mem, "home", "Home")
    gated_env = GridEnv(); gated = Loop(gated_env, StubModel([SimpleNamespace(data=per), SimpleNamespace(data=per)]), gated_mem, "grid", act_mode=False)
    gated.place, gated.prev_frame = "home", gated_env.screenshot()
    described = []
    monkeypatch.setattr(loop_module, "describe", lambda *_args: (described.append(True) or "GATED DESCRIBE"))
    monkeypatch.setattr(gated.world, "view", lambda *_args: (_ for _ in ()).throw(AssertionError("act world view on gated branch")))

    gated.step()

    assert described


def test_evidence_actor_stales_after_twenty_steps(tmp_path):
    from PIL import Image

    mem = Memory(tmp_path); place(mem, "room", "room"); world = World(mem, 16)
    player = {"name": "Player", "kind": "player", "at": [.5, .5]}
    npc = {"name": "Guide", "kind": "npc", "at": [.4, .5]}
    _, _, ids = world.before({"screen": "world", "entities": [player, npc], "text": [], "affordances": []}, "room", None, (160, 144), 1)
    ident = ids[1]
    place(mem, "other", "other")
    mem.upsert("entities", {"id": "remote", "name": "Remote", "kind": "object", "place": "other", "status": "seen", "misses": 0, "last": 1})
    mem.upsert("entities", {"id": "remote_npc", "name": "Remote NPC", "kind": "npc", "place": "other", "status": "remembered", "misses": 0, "last": 1})
    place(mem, "merged", "old"); mem.upsert("places", {**mem.get("places", "merged"), "state": "merged"})
    mem.upsert("entities", {"id": "merged_npc", "name": "Merged NPC", "kind": "npc", "place": "merged", "status": "remembered", "misses": 0, "last": 1})
    world.observe_after({"screen": "world", "entities": [player]}, Image.new("RGB", (160, 144), "red"), False, "room", (160, 144), 21)
    assert mem.get("entities", ident)["status"] == "stale" and mem.get("entities", ident)["misses"] == 0
    assert mem.get("entities", "remote")["status"] == "remembered"
    assert mem.get("entities", "remote_npc")["status"] == "stale"
    assert mem.get("entities", "merged_npc")["status"] == "remembered"


def test_miss_hash_survives_world_checkpoint(tmp_path):
    from PIL import Image

    mem = Memory(tmp_path); place(mem, "room", "room"); world = World(mem, 16)
    player = {"name": "Player", "kind": "player", "at": [.5, .5]}
    item = {"name": "Coin", "kind": "object", "at": [.55, .5]}
    ids = world.before({"screen": "world", "entities": [player, item], "text": [], "affordances": []}, "room", None, (160, 144), 1)[2]
    ident, frame = ids[1], Image.new("RGB", (160, 144), "red")
    world.observe_after({"screen": "world", "entities": [player]}, frame, False, "room", (160, 144), 2)
    restored = World(mem, 16); restored.restore(world.state())
    restored.observe_after({"screen": "world", "entities": [player]}, frame, False, "room", (160, 144), 3)
    assert mem.get("entities", ident)["misses"] == 1


def test_evidence_actor_is_remembered_then_stale_after_twenty_steps(tmp_path):
    from PIL import Image

    mem = Memory(tmp_path); place(mem, "room", "room"); world = World(mem, 16)
    player = {"name": "Player", "kind": "player", "at": [.5, .5]}
    npc = {"name": "Guide", "kind": "npc", "at": [.4, .5]}
    ident = world.before({"screen": "world", "entities": [player, npc], "text": [], "affordances": []}, "room", None, (160, 144), 1)[2][1]
    world.observe_after({"screen": "world", "entities": [player]}, Image.new("RGB", (160, 144), "red"), False, "room", (160, 144), 2)
    assert mem.get("entities", ident)["status"] == "remembered"
    world.observe_after({"screen": "world", "entities": [player]}, Image.new("RGB", (160, 144), "blue"), False, "room", (160, 144), 20)
    assert mem.get("entities", ident)["status"] == "remembered"
    world.observe_after({"screen": "world", "entities": [player]}, Image.new("RGB", (160, 144), "green"), False, "room", (160, 144), 21)
    assert mem.get("entities", ident)["status"] == "stale"


def test_tiers_preserve_transient_interest_and_promote_offscreen_text_names(tmp_path):
    mem = Memory(tmp_path); place(mem, "room", "room")
    world = World(mem, 16)
    ui_id = mem.observe({"entities": [{"name": "Start", "kind": "menu_item"}]}, "room", 1)[0]
    mem.upsert("entities", {**mem.get("entities", ui_id), "tier": "interest"})
    note_id = mem.upsert("entities", {"id": "map", "name": "Old Map", "kind": "object", "place": "room", "tier": "landmark", "status": "remembered", "misses": 0})["id"]
    world.before({"screen": "menu", "entities": [{"name": "Start", "kind": "menu_item"}], "text": [], "affordances": []}, "room", None, (160, 144), 2)
    world._tiers({"entities": [], "text": ["The Old Map points north"], "affordances": []}, "room", {})
    assert mem.get("entities", ui_id)["tier"] == "interest"
    assert mem.get("entities", note_id)["tier"] == "interest"
