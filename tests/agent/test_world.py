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
