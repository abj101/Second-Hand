import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from agent.loop import Loop
from agent.memory import Memory
from agent.notebook import Notebook
from agent.space import mark_exit
from tests.agent.fakes import GridEnv, StubModel
from tests.agent.test_skills import Busy, short_settle
from tests.agent.test_options import INPUTS, room
from agent.model import ModelUnavailable

def reply(data): return type("Reply", (), {"data": data})()

def act_reply(i=0, expect="screen_changes", verb="approach"):
    return reply({"i": 2 if i is None else 1, "why": "test"})
def fake_act(i=0, expect="screen_changes", verb="approach"):
    return lambda *a, **k: SimpleNamespace(place="", ops=[], action=None if i is None else {"thing": i, "verb": verb}, expect=expect, why="test", text=None)
def perception():
    return {"screen":"room","text":[],"entities":[{"kind":"player","name":"Hand","at":[500,500]}]}


def test_numbered_thing_uses_exact_entity_id(tmp_path, monkeypatch):
    from agent import loop as module
    per = {"screen": "world", "text": [], "entities": [
        {"kind": "player", "name": "player", "at": [500, 500]},
        {"kind": "object", "name": "Lamp", "at": [700, 500]}]}
    act = reply({"i": 1, "why": "lamp"})
    loop = Loop(GridEnv(), StubModel([reply(per), act, reply(per)]), Memory(tmp_path / "memory"), "grid")
    loop.world.self_spot, loop.world._self_from_motion = [.5, .5], True
    seen = {}
    def run(ctx, aff, *_args, **_kwargs):
        seen.update(aff=aff, ident=loop.world.entity_ids[1])
        frame = ctx.env.screenshot()
        return SimpleNamespace(status="done", reason="", inputs=0, calls=0,
                               before=frame, after=frame, scroll=(0, 0), blank_seen=False, moves=[])
    monkeypatch.setattr(module, "run_affordance", run)

    row = loop.step()

    assert seen["aff"] == {"verb": "interact", "target": "Lamp", "at": [.7, .5]}
    assert row["choice"]["id"] == seen["ident"] == loop.world.entity_ids[1]


def test_thing_approach_to_wall_exit_routes_and_records_blocked_push(tmp_path, monkeypatch):
    from agent import loop as module
    class Recording(GridEnv):
        def __init__(self): super().__init__(); self.keys = []
        def press(self, key, frames=8, on_frame=None): self.keys.append(key); super().press(key, frames, on_frame)
    env = Recording(); env.walls.add((10, 8))
    per = {"screen": "world", "text": [], "entities": [
        {"name": "player", "kind": "player", "at": [.5, .5]},
        {"name": "exit: wall", "kind": "exit", "at": [.6, .5]}]}
    monkeypatch.setattr(module, "perceive", lambda *_: (per, reply({})))
    monkeypatch.setattr(module, "act", lambda *a, **k: SimpleNamespace(place="", ops=[], action={"thing": 1, "verb": "approach"}, expect="screen_changes", why="exit", text=None))
    loop = Loop(env, StubModel([]), Memory(tmp_path / "memory"), "grid")
    loop.world.self_spot, loop.world._self_from_motion = [.5, .5], True
    routed = {}
    run = module.run_affordance
    def capture(ctx, aff, *args, **kwargs):
        routed["route"] = kwargs.get("route")
        return run(ctx, aff, *args, **kwargs)
    monkeypatch.setattr(module, "run_affordance", capture)

    row = loop.step()

    assert len(env.keys) <= len(routed["route"]["dirs"]) + 1
    assert row["skill"]["status"] == "blocked"
    assert any(r.get("place") == loop.place and "RIGHT" in r.get("blocked", []) for r in loop.mem.rows("tiles"))


def test_bare_advance_presses_a(tmp_path):
    class Recording(GridEnv):
        def __init__(self): super().__init__(); self.keys = []
        def press(self, key, frames=8, on_frame=None): self.keys.append(key); super().press(key, frames, on_frame)
    env = Recording()
    data = {"screen": "dialogue", "text": ["Continue?"], "entities": []}
    advance = reply({"i": 1, "why": "continue"})
    Loop(env, StubModel([reply(data), advance, reply(data)]), Memory(tmp_path / "memory"), "grid").step()
    assert env.keys == ["A"]


@pytest.mark.parametrize("known", [False, True])
def test_first_departure_crossing_is_verified_for_known_or_new_destination(tmp_path, monkeypatch, known):
    from agent import loop as loop_module

    monkeypatch.setattr(loop_module, "cut", lambda *args, **kwargs: True)
    monkeypatch.setattr(loop_module, "effects", lambda *args, **kwargs: {"effects": ["place_changes"], "basis": {"place_changes": "pixel"}, "to": "destination"})
    monkeypatch.setattr(loop_module, "run_affordance", lambda ctx, *args, **kwargs: SimpleNamespace(
        status="done", reason="scene cut", inputs=1, calls=0, before=ctx.env.screenshot(), after=ctx.env.screenshot(),
        scroll=(0, 0), blank_seen=True, moves=[{"dir": "RIGHT", "shift": (16, 0)}]))
    replies = [reply(perception()), act_reply(0, "place_changes")]
    model = StubModel(replies); mem = Memory(tmp_path / "memory")
    mem.upsert("places", {"id": "source", "name": "Source"})
    if known: mem.upsert("places", {"id": "destination", "name": "Destination"})
    loop = Loop(GridEnv(), model, mem, "grid")
    matches = 0
    def match_place(*args, **kwargs):
        nonlocal matches
        matches += 1
        if matches % 2 == 0 and not known: mem.upsert("places", {"id": "destination", "name": "Destination"})
        return "source" if matches % 2 else "destination"
    loop.mem.match_place = match_place

    row = loop.step()

    assert row["verified_edge"]
    assert model.calls == 2

def test_twenty_steps_have_contract_keys_and_reuse(tmp_path):
    from collections import deque
    class Roles(StubModel):
        def __init__(self):
            super().__init__([]); self.replies = {"perceiver": deque([reply(perception()), reply(perception())]),
                                                   "act": deque(act_reply(None) for _ in range(20))}
        def ask(self, *args, **kwargs):
            if kwargs["role"] == "plan": return SimpleNamespace(data={"goals": [], "focus": "", "why": "stub"}, text="")
            self.calls += 1; return self.replies[kwargs["role"]].popleft()
    model=Roles(); loop=Loop(GridEnv(),model,Memory(tmp_path/"memory"),"grid")
    rows=[loop.step() for _ in range(20)]
    keys={"step","t","place","screen","task","frame","options","choice","pred","used","skill","outcome","verdict","calls","ms","shortcut","self_spot","self_locked"}
    assert all(keys <= row.keys() for row in rows)
    assert model.calls == 21 and rows[1]["perception_reused"] and rows[1]["calls"] == 1
    assert rows[0]["calls"] == 2 and not rows[0]["perception_reused"]
    assert sum(row["calls"] == 2 for row in rows) == 1  # initial perception plus action

def test_act_receives_controls_as_text(tmp_path, monkeypatch):
    from agent import loop as loop_module
    data = perception()
    class Captures(StubModel):
        def ask(self, *args, **kwargs):
            if kwargs.get("role") == "act": self.prompt = args[1]
            return super().ask(*args, **kwargs)
    monkeypatch.setattr(loop_module, "_load", lambda _game: {"controls": ["Move with arrows", "Confirm with A"]})
    model = Captures([reply(data), reply({"i": 3, "why": "controls"}), reply({"buttons": ["A"]}), reply(data)])
    Loop(GridEnv(), model, Memory(tmp_path / "memory"), "grid").step()
    assert "Controls:\nMove with arrows\nConfirm with A" in model.prompt

def test_restore_and_parse_failure(tmp_path):
    model=StubModel([reply(perception()),act_reply(0),reply(perception())])
    loop=Loop(GridEnv(),model,Memory(tmp_path/"memory"),"grid"); row=loop.step()
    assert row["choice"]["called"]
    restored=Loop(loop.env,model,loop.mem,"grid"); restored.restore(loop.state())
    assert restored.state()["step"] == loop.state()["step"] and restored.cached == loop.cached

def test_position_checkpoint_defaults_and_restores(tmp_path):
    loop = Loop(GridEnv(), StubModel([]), Memory(tmp_path/"memory"), "grid"); loop.pos = (4, -2)
    state = loop.state(); resumed = Loop(loop.env, loop.model, loop.mem, "grid"); resumed.restore(state)
    assert resumed.pos == (4, -2)
    resumed.restore({}); assert resumed.pos == (0, 0)

def test_marked_perception_uses_raw_hash_and_restores_mark_state(tmp_path, monkeypatch):
    from PIL import Image
    from agent import loop as module
    raw = Image.new("RGB", (160, 144), "black")
    model = StubModel([reply(perception())])
    loop = Loop(GridEnv(), model, Memory(tmp_path/"memory"), "grid")
    loop.world.self_spot, loop.world._self_from_motion = [.5, .5], True
    loop.env.screenshot = lambda: raw.copy()
    observed = []
    monkeypatch.setattr(module, "perceive", lambda _model, image, _ctx: (perception(), observed.append(image) or reply(perception())))

    per, digest, reused, _ = loop._see(raw, None, screen_was_world=True)
    image, marked = loop.seen_image, loop.seen_marked
    assert marked and not reused and digest == __import__("hashlib").sha256(raw.tobytes()).hexdigest()
    assert image.getpixel((71, 62)) == (255, 0, 0) and raw.getpixel((71, 62)) == (0, 0, 0)
    assert loop._see(raw, None, screen_was_world=True)[2] and len(observed) == 1
    saved = loop.state()
    assert saved["perception_marked"] is True
    resumed = Loop(loop.env, model, loop.mem, "grid"); resumed.restore(saved)
    assert resumed.cached_marked is True
    _, same_digest, reused, _ = loop._see(raw, None, screen_was_world=False)
    marked = loop.seen_marked
    assert same_digest == digest and not reused and not marked and len(observed) == 2


def test_live_observation_guard_rejects_blank_scene_and_keeps_generation_changes(tmp_path):
    import time
    from types import SimpleNamespace
    from PIL import Image
    import numpy as np

    original = Image.fromarray(np.random.default_rng(1).integers(0, 255, (144, 160, 3), dtype=np.uint8))
    stamp = {"generation": "g1", "frame_seq": 10, "captured_monotonic": time.monotonic()}
    loop = Loop.__new__(Loop)
    loop.env = SimpleNamespace(snapshot=lambda: SimpleNamespace(generation="g1", image=original.copy()))
    stale, _, age = loop._live_observation_stale(original, stamp)
    assert not stale and age >= 0
    loop.env.snapshot = lambda: SimpleNamespace(generation="g1", image=Image.new("RGB", original.size, "black"))
    assert loop._live_observation_stale(original, stamp)[0]
    loop.env.snapshot = lambda: SimpleNamespace(generation="g2", image=original.copy())
    assert loop._live_observation_stale(original, stamp)[0]
    menu = Image.new("RGB", original.size, "white"); menu.paste((0, 0, 0), (8, 8, 60, 30))  # NEW GAME menu: >95% one colour
    loop.env.snapshot = lambda: SimpleNamespace(generation="g1", image=menu.copy())
    assert not loop._live_observation_stale(menu, stamp)[0]


def test_live_resume_invalidation_clears_pending_but_keeps_durable_world(tmp_path):
    loop = Loop(GridEnv(), StubModel([]), Memory(tmp_path / "memory"), "grid")
    loop.env.snapshot = lambda: None
    loop.pos = (4, 5); loop.world.entity_ids = {1: "npc"}
    loop.pending = {"action_step": 3}; loop.cached = {"screen": "world"}; loop.cached_hash = "old"
    loop.world.self_spot, loop.world._self_from_motion = [.5, .5], True
    loop.world.tracker.tracks["npc"] = {"status": "live"}
    loop.world._hud["player"] = {"crop": object(), "changed": 2}
    loop.invalidate_live_observation()
    assert loop.pending is None and loop.cached is loop.cached_hash is None
    assert loop.world.tracker.tracks == {} and loop.world.self_spot is None and not loop.world._self_from_motion
    assert loop.pos == (4, 5) and loop.world.entity_ids == {1: "npc"}
    assert loop.world._hud["player"] == {"crop": None, "changed": 2}
    assert loop.live_invalidation_reason == "live_resume_cleared_pending"

def test_first_step_after_live_resume_has_no_prior_frame_and_keeps_its_place(tmp_path, monkeypatch):
    from agent import loop as module
    loop = Loop(GridEnv(), StubModel([]), Memory(tmp_path / "memory"), "grid")
    loop.mem.upsert("places", {"id": "room", "name": "Room", "screen": "world", "state": "place", "visits": 1})
    loop.world.current_place = loop.place = "room"; loop.prev_frame = None  # what invalidate_live_observation leaves after a resume
    data = {"screen": "world", "text": [], "entities": [{"kind": "player", "name": "player", "at": [.5, .5]}]}
    monkeypatch.setattr(module, "perceive", lambda *_: (data, reply(data)))
    monkeypatch.setattr(module, "act", lambda *_a, **_k: SimpleNamespace(place="", ops=[], action=None, expect="screen_changes", why="test", text=None))

    loop.step()  # m1_h1h4_live resume: cut(None, frame) raised before any trace row

    assert loop.place == "room"

def test_live_resume_compares_against_the_last_after_frame_on_disk(tmp_path):
    from PIL import Image
    loop = Loop(GridEnv(), StubModel([]), Memory(tmp_path / "run" / "memory"), "grid")
    loop.env.snapshot = lambda: None; loop.step_no = 90
    loop.invalidate_live_observation()
    assert loop.prev_frame is None and loop.cut_carry  # no record of what was last seen: re-identify at the next world screen
    (tmp_path / "run" / "frames").mkdir(parents=True); Image.new("RGB", (160, 144), "red").save(tmp_path / "run" / "frames" / "000090_after.png")
    loop.cut_carry = False
    loop.invalidate_live_observation()
    assert loop.prev_frame.size == (160, 144) and not loop.cut_carry

def test_a_cut_seen_on_a_dialogue_screen_reidentifies_at_the_next_world_screen(tmp_path, monkeypatch):
    from agent import loop as module
    loop = Loop(GridEnv(), StubModel([]), Memory(tmp_path / "memory"), "grid")
    loop.mem.upsert("places", {"id": "room", "name": "Room", "screen": "world", "state": "place", "visits": 1})
    loop.world.current_place = loop.place = "room"
    screens = iter(["dialogue"])  # m1_h1h4_live S91-S109: walked into another map during a scripted dialogue; then the world
    def see(*_):
        data = {"screen": next(screens, "world"), "text": [], "entities": [{"kind": "player", "name": "player", "at": [.5, .5]}]}
        return data, reply(data)
    monkeypatch.setattr(module, "perceive", see)
    monkeypatch.setattr(module, "act", lambda *_a, **_k: SimpleNamespace(place="", ops=[], action=None, expect="screen_changes", why="test", text=None))
    from PIL import Image
    first = Image.new("RGB", (160, 144), "black"); first.paste((90, 90, 90), (0, 0, 160, 72))  # not uniform: a one-colour frame is a blank transition
    second = first.copy(); second.putpixel((3, 3), (200, 0, 0))  # a new frame (fresh perception) that is no cut by itself: only the carried cut counts
    loop.env.screenshot = lambda: first.copy()
    loop.cut_carry = True  # the cut was seen on the dialogue screen
    loop.step()
    assert loop.place == "room" and loop.cut_carry  # a dialogue is a state of the place: the cut waits
    loop.env.screenshot = lambda: second.copy()
    loop.step()
    assert loop.place != "room" and not loop.cut_carry  # the first world screen is matched as a new place

def test_cut_before_frame_suppresses_marker_and_same_raw_after_frame_reuses(tmp_path, monkeypatch):
    from PIL import Image
    from agent import loop as module
    env = GridEnv(); frame = Image.new("RGB", (160, 144), "black")
    frame.paste((90, 90, 90), (0, 0, 160, 72))  # not uniform: a one-colour frame counts as a blank transition (observer._blank)
    env.screenshot = lambda: frame.copy()
    loop = Loop(env, StubModel([]), Memory(tmp_path/"memory"), "grid")
    loop.world.self_spot, loop.world._self_from_motion = [.5, .5], True
    loop.prev, loop.prev_frame = {"screen": "world"}, Image.new("RGB", (160, 144), "white")
    per = {"screen":"world", "text":[], "entities":[{"name":"player", "kind":"player", "at":[.5,.5]}]}
    calls, contexts, acts = [], [], []
    def perceive(_model, image, ctx):
        calls.append(image); contexts.append(ctx)
        return per, reply(per)
    def act(_model, _per, _focus, _options, _buttons, _controls):
        acts.append(loop.seen_image)
        return SimpleNamespace(place="", ops=[], action=None, expect="screen_changes", why="test", text=None)
    monkeypatch.setattr(module, "perceive", perceive)
    monkeypatch.setattr(module, "act", act)
    cut_calls = [0]
    def cut_once(*_args, **_kwargs):
        cut_calls[0] += 1
        return cut_calls[0] == 1
    monkeypatch.setattr(module, "cut", cut_once)

    first = loop.step()
    second = loop.step()

    assert not first["marked"] and second["marked"]
    assert second["perception_reused"] and len(calls) == 2
    assert module.CAPTION in contexts[1]
    assert acts[1].getpixel((71, 62)) == (255, 0, 0)

@pytest.mark.parametrize("views", [False, True])
def test_stationary_two_fresh_frames_confirm_drift(tmp_path, monkeypatch, views):
    from agent import loop as module
    env = GridEnv(); env.phase = 0; original = env.screenshot
    def screenshot():
        frame = original(); frame.putpixel((0, 0), (env.phase, 0, 0)); return frame
    env.screenshot = screenshot; offset = [0.6]
    def perceive(*_):
        return {"screen":"world","text":[],"entities":[{"name":"player","kind":"player","at":[.5,.5]}, {"name":"lamp","kind":"object","at":[offset[0],.5]}]}, reply({"screen":"world","text":[],"entities":[]})
    monkeypatch.setattr(module, "perceive", perceive)
    monkeypatch.setattr(module, "act", fake_act(1))
    def still(*a,**k):
        before=env.screenshot(); env.phase+=1; after=env.screenshot()
        return SimpleNamespace(status="done",reason="",inputs=0,calls=0,before=before,after=after,scroll=(0,0),blank_seen=False,moves=[])
    monkeypatch.setattr(module, "run_affordance", still)
    loop = Loop(env, StubModel([]), Memory(tmp_path/"memory"), "grid")
    if not views: monkeypatch.setattr(loop.mem, "seen_view", lambda *a, **k: None)  # no stored view matches: entity drift decides
    loop.world.self_spot, loop.world._self_from_motion = [.5, .5], True
    loop.step(); offset[0] = .75; first = loop.step()
    assert loop.pos == (0, 0) and first["drift_fix"] is None and loop.world.drift == (-1, 0)
    second = loop.step()
    if views: assert loop.pos == (0, 0) and second["drift_fix"] is None  # screen never moved: stored view vetoes the entity drift
    else: assert loop.pos == (-1, 0) and second["drift_fix"] == [-1, 0]


def test_loop_fixes_swapped_player_before_approaching_target(tmp_path, monkeypatch):
    from agent import loop as module
    env = GridEnv()
    env.door, env.npc = (-5, -5), (-6, -6)
    perceptions = []
    def perceive(*_):
        data = {"screen": "world", "text": [],
                "entities": [{"name": "Guide", "kind": "player", "at": [.6875, .5]},
                             {"name": "Hero", "kind": "npc", "at": [.5, .5]},
                             {"name": "Chest", "kind": "object", "at": [.6, .5]}]}
        perceptions.append(data)
        return data, reply({})
    monkeypatch.setattr(module, "perceive", perceive)
    monkeypatch.setattr(module, "act", fake_act(2))
    loop = Loop(env, StubModel([]), Memory(tmp_path / "memory"), "grid")
    loop.world.self_spot, loop.world._self_from_motion = [.5, .5], True

    row = loop.step()

    assert row["choice"]["target"] == "Chest" and row["player_fix"] == {"from": [.6875, .5], "to": [.5, .5]}
    assert row["skill"]["status"] == "done" and env.player[0] > 8 and env.scene == 0
    assert len(perceptions) == 2 and all(per["entities"][0]["kind"] == "character" and per["entities"][1]["kind"] == "player" for per in perceptions)


def test_approaching_player_is_blocked_without_input():
    from agent.skills import Ctx, approach
    env = GridEnv(); result = approach(Ctx(env, StubModel([]), tile_px=16), [.5, .5], [.5, .5])
    assert result.status == "blocked" and result.reason == "target is the player" and result.inputs == 0
    assert env.player == (8, 8)


def test_grid_loop_reuses_two_spatial_exits_between_floors(tmp_path, monkeypatch):
    from agent import loop as module
    class TwoFloor(GridEnv):
        def __init__(self):
            super().__init__(); self.door = (-5, -5)
        def _frame(self):
            if self._transition:
                self._transition += 1
                if self._transition > 2:
                    self.scene, self.player, self._transition = 1-self.scene, (8,9), 0
                return
            super()._frame()
            if not self._moving and self.player == ((8,10) if self.scene == 0 else (8,8)): self._transition = 1
    env = TwoFloor(); floors = ("2F", "1F")
    def perceive(*_):
        floor = floors[env.scene]
        dy = (2 if env.scene == 0 else -1) * 16/144
        return {"screen":"world","text":[],"entities":[{"name":"player","kind":"player","at":[.5,.5]}, {"name":floor,"kind":"object","at":[.7,.5]},
                            {"name":"exit: passage","kind":"exit","at":[.5,.5+dy]}]}, reply({})
    monkeypatch.setattr(module, "perceive", perceive)
    monkeypatch.setattr(module, "act", fake_act(2, "place_changes"))
    loop=Loop(env,StubModel([]),Memory(tmp_path/"memory"),"grid"); ids=[]
    for _ in range(4): ids.append(loop.step()["next_place"])
    assert ids[0] == ids[2] and ids[1] == ids[3] and ids[0] != ids[1]
    assert loop.stuck.progress_n == 2  # crossings 3 and 4 repeat known edges: a bounce is not progress

def test_perceiver_guides_whole_name_entry(tmp_path):
    class CapturesPrompt(StubModel):
        def ask(self, *args, **kwargs): self.prompt=args[1]; return super().ask(*args, **kwargs)
    model=CapturesPrompt([reply(perception())]); loop=Loop(GridEnv(),model,Memory(tmp_path/"memory"),"grid")
    loop._see(loop.env.screenshot(),None)
    assert "visible editable text field with its on-screen keyboard" not in model.prompt
    assert "Previous-place memory is reference only" not in model.prompt
    assert 'List every visible passage (door, stairs, door mat, opening) as an entity named "exit: <what it is>"' in model.prompt
    assert loop.prompt_version == "e5-perceiver-10"

def test_probe_act_uses_post_probe_frame_and_perception(tmp_path, monkeypatch):
    from agent import loop as loop_module

    env = GridEnv(); env.tile_px = 16
    monkeypatch.setattr(loop_module, "perceive", lambda _model, _frame, _prompt: (env.perception(), reply(env.perception())))
    def probe(_ctx):
        env.scene = 1
        return {"presses": 1, "locked": False, "scene_cut": False}
    def act_spy(_model, per, *args, **kwargs):
        assert per == env.perception()
        return fake_act(None)(*args, **kwargs)
    monkeypatch.setattr(loop_module, "act", act_spy)
    loop = Loop(env, StubModel([]), Memory(tmp_path / "memory"), "grid")
    monkeypatch.setattr(loop.world, "probe", probe)

    row = loop.step()

    assert row["probe"] == {"presses": 1, "locked": False}
    assert not row["perception_reused"]


def test_dialogue_opening_over_world_keeps_place(tmp_path, monkeypatch):
    from agent import loop as loop_module
    env = GridEnv()
    start = perception(); start["screen"] = "world"
    destination = perception(); destination["screen"] = "dialogue"; destination["text"] = ["Hello!"]
    model = StubModel([reply(start), reply(destination)])
    monkeypatch.setattr(loop_module, "act", fake_act(0, verb="interact"))
    loop = Loop(env, model, Memory(tmp_path/"memory"), "grid")
    loop.world.self_spot, loop.world._self_from_motion = [.5, .5], True
    def interact(ctx, aff, per, text=None, exit=False, **kwargs):
        env.scene = 1
        return SimpleNamespace(status="done", reason="", inputs=4, calls=0,
                               before=env.screenshot(), after=env.screenshot(), scroll=(0, 0), blank_seen=False)
    monkeypatch.setattr(loop_module, "run_affordance", interact)
    monkeypatch.setattr(loop_module, "cut", lambda *args: True)
    row = loop.step()
    assert not row["scene_cut"] and row["next_place"] == row["place"]

def test_interact_scroll_with_new_text_is_not_a_cut(tmp_path, monkeypatch):
    from agent import loop as loop_module
    env = GridEnv()
    start = perception(); start["screen"] = "world"
    destination = perception(); destination["screen"] = "world"; destination["text"] = ["Hello!"]
    model = StubModel([reply(start), reply(destination)])
    monkeypatch.setattr(loop_module, "act", fake_act(0, verb="interact"))
    loop = Loop(env, model, Memory(tmp_path/"memory"), "grid")
    loop.world.self_spot, loop.world._self_from_motion = [.5, .5], True
    def interact(ctx, aff, per, text=None, exit=False, **kwargs):
        env.scene = 1
        return SimpleNamespace(status="done", reason="", inputs=12, calls=0,
                               before=GridEnv().screenshot(), after=env.screenshot(), scroll=(48, 0), blank_seen=False)
    monkeypatch.setattr(loop_module, "run_affordance", interact)
    monkeypatch.setattr(loop_module, "cut", lambda *args: True)
    row = loop.step()
    assert not row["scene_cut"] and row["next_place"] == row["place"]

def test_modal_overlay_at_step_start_keeps_place(tmp_path, monkeypatch):
    from agent import loop as loop_module
    env = GridEnv()
    world = perception(); world["screen"] = "world"
    dialogue = perception(); dialogue["screen"] = "dialogue"
    model = StubModel([reply(world), reply(world), reply(dialogue)])
    monkeypatch.setattr(loop_module, "act", fake_act(0))
    loop = Loop(env, model, Memory(tmp_path/"memory"), "grid")
    loop.world.self_spot, loop.world._self_from_motion = [.5, .5], True
    monkeypatch.setattr(loop_module, "cut", lambda *args: True)
    moves = [0]
    def stay(ctx, aff, per, text=None, exit=False, route=None):
        before = env.screenshot()
        if moves[0] == 0: env.scene = 1
        moves[0] += 1; frame = env.screenshot()
        return SimpleNamespace(status="done", reason="", inputs=1, calls=0,
                               before=before, after=frame, scroll=(0, 0), blank_seen=False)
    monkeypatch.setattr(loop_module, "run_affordance", stay)
    first = loop.step()
    env.scene = 0
    second = loop.step()
    assert second["place"] == first["next_place"]  # modal overlay keeps the post-cut place

def test_explicit_transition_into_dialogue_keeps_place(tmp_path, monkeypatch):  # was ..._creates_place: dialogue is a state, not a place
    from agent import loop as loop_module
    env = GridEnv()
    world = perception(); world["screen"] = "world"
    dialogue = perception(); dialogue["screen"] = "dialogue"
    model = StubModel([reply(world), reply(dialogue)])
    monkeypatch.setattr(loop_module, "act", fake_act(0))
    loop = Loop(env, model, Memory(tmp_path/"memory"), "grid")
    loop.world.self_spot, loop.world._self_from_motion = [.5, .5], True
    def transition(ctx, aff, per, text=None, exit=False, route=None):
        before = env.screenshot(); env.scene = 1; after = env.screenshot()
        return SimpleNamespace(status="done", reason="scene cut", inputs=1, calls=0,
                               before=before, after=after, scroll=(0, 0), blank_seen=False)
    monkeypatch.setattr(loop_module, "run_affordance", transition)
    row = loop.step()
    assert row["scene_cut"] and row["next_place"] == row["place"] and "place_changes" not in row["outcome"]["effects"]

@pytest.mark.parametrize(("screen", "moves", "expect"), [("world", [("DOWN", 1)], True), ("world", [], False), ("dialogue", [("DOWN", 1)], False)])
def test_directional_world_exit_into_new_place_is_place_changes(tmp_path, monkeypatch, screen, moves, expect):
    from agent import loop as loop_module
    env = GridEnv()
    world = perception(); world["screen"] = "world"
    dest = perception(); dest["screen"] = screen; dest["entities"] = [{"kind": "tree", "name": "Oak", "at": [500, 500]}]
    model = StubModel([reply(world), reply(dest)])
    monkeypatch.setattr(loop_module, "act", fake_act(0))
    loop = Loop(env, model, Memory(tmp_path/"memory"), "grid")
    loop.world.self_spot, loop.world._self_from_motion = [.5, .5], True
    def transition(ctx, aff, per, text=None, exit=False, route=None):
        before = env.screenshot(); env.scene = 1; after = env.screenshot()
        return SimpleNamespace(status="done", reason="scene cut", inputs=1, calls=0, moves=moves,
                               before=before, after=after, scroll=(0, 0), blank_seen=False)
    monkeypatch.setattr(loop_module, "run_affordance", transition)
    row = loop.step()
    assert row["scene_cut"] and (row["next_place"] != row["place"]) == (screen == "world")  # only a navigable screen is a new place
    assert ("place_changes" in row["outcome"]["effects"]) == expect
    if expect: assert row["outcome"]["to"] == row["next_place"]

def test_blocked_exit_without_novelty_reports_failed(tmp_path, monkeypatch):
    from agent import loop as loop_module
    env = GridEnv()
    start = perception(); start["screen"] = "world"
    start["entities"].append({"kind": "exit", "name": "exit: door mat", "at": [0.3, 0.9]})
    model = StubModel([reply(start), act_reply(1), reply(start)])
    loop = Loop(env, model, Memory(tmp_path/"memory"), "grid")
    loop.world.self_spot, loop.world._self_from_motion = [.5, .5], True
    monkeypatch.setattr(loop_module, "cut", lambda *args: False)
    def blocked(ctx, aff, per, text=None, exit=False, **kwargs):
        before = env.screenshot(); env.scene = 1; after = env.screenshot()
        return SimpleNamespace(status="blocked", reason="exit not crossed", inputs=8, calls=0,
                               before=before, after=after, scroll=(0, 0), blank_seen=False)
    monkeypatch.setattr(loop_module, "run_affordance", blocked)
    row = loop.step()
    assert row["pred"] is None and row["used"] == []
    assert "screen_changes" in row["outcome"]["effects"] and row["verdict"] == "failed"

def test_mislabelled_player_does_not_shift_tiles_or_duplicate_entities(tmp_path, monkeypatch):
    from agent import loop as module
    env, seen, shot = GridEnv(), [], GridEnv.screenshot; env.phase = 0
    env.screenshot = lambda: (f := shot(env), f.putpixel((0, 0), (env.phase, 0, 0)))[0]
    def perceive(*_):
        seen.append(1); player = [.5, .5] if len(seen) == 1 else [.5, .5 + 3 * 16 / 144]  # second view labels another sprite as the player
        data = {"screen":"world","text":[],"entities":[{"name":"player","kind":"player","at":player}, {"name":"lamp","kind":"object","at":[.7,.5]}]}
        return data, reply(data)
    monkeypatch.setattr(module, "perceive", perceive)
    def still(*a, **k):
        before = env.screenshot(); env.phase += 1
        return SimpleNamespace(status="done",reason="",inputs=0,calls=0,before=before,after=env.screenshot(),scroll=(0,0),blank_seen=False,moves=[])
    monkeypatch.setattr(module, "run_affordance", still); monkeypatch.setattr(module, "act", fake_act(0))
    loop = Loop(env, StubModel([]), Memory(tmp_path/"memory"), "grid")
    loop.world.self_spot, loop.world._self_from_motion = [.5, .5], True
    loop.step()
    lamps = [e for e in loop.mem.rows("entities") if e["name"].startswith("lamp")]
    assert len(seen) == 2 and len(lamps) == 1 and lamps[0]["tile"] == [2, 0]
    assert loop.trace.tail(1)[0]["self_locked"] is True
    resumed = Loop(env, StubModel([]), loop.mem, "grid"); resumed.restore(loop.state())
    assert resumed.world.self_spot == [.5, .5] and resumed.world._self_from_motion

def test_malformed_parse_gets_one_repair_and_resumes(tmp_path, monkeypatch):
    from agent import loop as module
    monkeypatch.setattr(module, "act", fake_act(None))
    class Captures(StubModel):
        def ask(self,*a,**k):
            if k.get("role") == "perceiver": self.perceiver_prompts = getattr(self,"perceiver_prompts",[]) + [a[1]]
            return super().ask(*a,**k)
    model=Captures([reply({"wrong_root":True}),reply(None),reply({"wrong_root":True})]); loop=Loop(GridEnv(),model,Memory(tmp_path/"memory"),"grid")
    for _ in range(8): loop.step()
    assert loop.state()["repair_pending"] and loop.state()["repair_used"]
    restored=Loop(loop.env,model,loop.mem,"grid"); restored.restore(loop.state()); row=restored.step()
    # One repair gets a call; the repair stays malformed and is not repeated.
    assert row["options"] == 0 and not restored.state()["repair_pending"] and restored.state()["repair_used"]
    assert len(model.perceiver_prompts) == 2 and "previous JSON was empty or malformed" in model.perceiver_prompts[-1]

def test_string_text_is_valid_perception(tmp_path):
    data=perception(); data["text"]="A visible dialogue line"
    loop=Loop(GridEnv(),StubModel([reply(data)]),Memory(tmp_path/"memory"),"grid")
    parsed,_,_,_=loop._see(loop.env.screenshot(),None)
    assert parsed["text"] == ["A visible dialogue line"] and not loop.parse_failed

def test_failed_parse_repair_is_not_repeated(tmp_path):
    class AlwaysMalformed(StubModel):
        def __init__(self): super().__init__([]); self.prompts=[]
        def ask(self,*a,**k):
            self.calls+=1
            if k.get("role")=="perceiver": self.prompts.append(a[1]); return reply({"wrong_root":True})
            return reply(None)
    model=AlwaysMalformed(); loop=Loop(GridEnv(),model,Memory(tmp_path/"memory"),"grid")
    for _ in range(8): loop.step()
    loop.step(); assert loop.state()["repair_used"] and len(model.prompts)==2
    loop.step(); assert len(model.prompts)==2

def test_old_checkpoint_invalidates_cached_perception(tmp_path):
    model=StubModel([reply(perception()),reply(perception())]); env=GridEnv(); loop=Loop(env,model,Memory(tmp_path/"memory"),"grid")
    frame=env.screenshot(); loop._see(frame,None); old=loop.state(); old.pop("perception_version")
    restored=Loop(env,model,loop.mem,"grid"); restored.restore(old)
    assert restored.cached is None
    assert not restored._see(frame,None)[2] and model.calls == 2

def test_resume_preserves_cut_if_post_perception_fails(tmp_path):
    class CutEnv(GridEnv):
        def press(self,key,frames=8,on_frame=None): self.scene=1
    class FailsAfterChoice(StubModel):
        def ask(self,*a,**k):
            if self.calls==2: self.calls+=1; raise ModelUnavailable("offline")
            return super().ask(*a,**k)
    env=CutEnv(); mem=Memory(tmp_path/"memory")
    loop=Loop(env,FailsAfterChoice([reply(perception()),reply({"i": 1, "why": "test"})]),mem,"grid")
    with pytest.raises(ModelUnavailable): loop.step()
    assert loop.state()["pending_cut"]
    town=perception(); town["screen"]="town"
    resumed=Loop(env,StubModel([reply(town), act_reply(None)]),mem,"grid"); resumed.restore(loop.state())
    assert mem.get("places",resumed.step()["place"])["state"] == "candidate"

def test_dialogue_fragments_flush_as_one_log_entry_and_survive_restore(tmp_path):
    env = GridEnv(); memory = Memory(tmp_path / "memory")
    loop = Loop(env, StubModel([]), memory, "grid")
    fragments = [
        (42, ["MOM: Right.", "All boys leave"]),
        (43, ["All boys leave home some day."]),
        (44, ["home some day.", "It said so on TV."]),
        (45, ["PROF. OAK, next", "door, is looking"]),
        (46, ["door, is looking for you."]),
    ]
    step, texts = fragments[0]
    loop._learn(texts, [], step, "tiled room", "dialogue")
    checkpoint = loop.state()
    resumed = Loop(env, loop.model, memory, "grid"); resumed.restore(checkpoint)
    assert resumed.state()["planner"]["conversation"] == checkpoint["planner"]["conversation"]

    for step, texts in fragments[1:]: resumed._learn(texts, [], step, "tiled room", "dialogue")
    assert resumed.notebook.data["text"] == []
    resumed._learn([], [], 47, "tiled room", "world")
    assert len(resumed.notebook.data["text"]) == 1
    entry = resumed.notebook.data["text"][0]
    assert (entry["speaker"], entry["where"]) == ("MOM", "tiled room")
    assert "PROF. OAK, next door, is looking for you." in entry["text"]
    assert {"MOM: Right.", "All boys leave", "All boys leave home some day.", "home some day.",
            "It said so on TV.", "PROF. OAK, next", "door, is looking", "door, is looking for you."} <= {
                row["text"] for row in resumed.mem.rows("knowledge")}
    assert [e["kind"] for e in resumed.events] == ["T1"]
    assert resumed.text_for_trigger == "Right. All boys leave home some day. It said so on TV. PROF. OAK, next door, is looking for you."

    resumed.events = []
    for step, texts in fragments: resumed._learn(texts, [], step + 10, "tiled room", "dialogue")
    resumed._learn([], [], 57, "tiled room", "world")
    assert len(resumed.notebook.data["text"]) == 1
    assert not resumed.events and not resumed.conversation["lines"]

    resumed._learn(["A sign says KEEP OUT."], [], 58, "tiled room", "world")
    assert len(resumed.notebook.data["text"]) == 2
    assert resumed.notebook.data["text"][-1]["text"] == "A sign says KEEP OUT."
    assert [e["kind"] for e in resumed.events] == ["T1"]
    resumed._learn(["A continuation ▼"], [], 59, "tiled room", "dialogue")
    resumed._learn(["A continuation is complete."], [], 60, "tiled room", "dialogue")
    resumed._learn([], [], 61, "tiled room", "world")
    assert resumed.notebook.data["text"][-1]["text"] == "A continuation is complete."
    assert [e["kind"] for e in resumed.events] == ["T1", "T1"]

def test_tracked_approach_scroll_does_not_split_but_real_cuts_do(tmp_path, monkeypatch):
    from agent import loop as loop_module
    from agent.observer import cut as observer_cut

    def run_case(root, verb="approach", reason="", blank=False):
        env = GridEnv(); before = env.screenshot()
        start = perception(); start["screen"] = "world"
        destination = perception(); destination["screen"] = "world"
        destination["entities"] += [{"kind":"object", "name":"new landmark", "at":[.2,.2]}, {"kind":"exit", "name":"exit: door", "at":[500,500]}]
        if verb == "interact": destination["text"] = ["Visible dialogue"]
        model = StubModel([reply(start), reply(destination)])
        monkeypatch.setattr(loop_module, "act", fake_act(0, verb=verb))
        mem = Memory(root/"memory"); loop = Loop(env, model, mem, "grid")
        loop.world.self_spot, loop.world._self_from_motion = [.5, .5], True  # no self-locating probe presses

        def move(ctx, aff, per, text=None, exit=False, route=None):
            env.scene = 1; after = env.screenshot()
            assert observer_cut(before, after)
            return SimpleNamespace(status="done", reason=reason, inputs=7, calls=0,
                                   before=before, after=after, scroll=(112,0), blank_seen=blank)

        monkeypatch.setattr(loop_module, "run_affordance", move)
        return loop, mem, loop.step()

    loop, mem, row = run_case(tmp_path/"tracked")
    assert row["scene_cut"] is False and row["next_place"] == row["place"]
    assert "place_changes" not in row["outcome"]["effects"]
    assert "edges" not in mem.get("places", row["place"])

    for name, verb, reason, blank in (("skill", "approach", "scene cut", False),
                                      ("blank", "approach", "", True)):
        loop, mem, row = run_case(tmp_path/name, verb, reason, blank)
        assert row["scene_cut"] is True and row["next_place"] != row["place"]
        assert mem.get("places", row["next_place"])["state"] == "candidate"
        assert "edges" not in mem.get("places", row["place"])
        assert not [r for r in mem.rows("relations") if r["p"] == "leads_to" and r["src"] == "inferred"]
    loop, mem, row = run_case(tmp_path/"interact", "interact")
    assert row["scene_cut"] is False and row["next_place"] == row["place"]


@pytest.mark.parametrize(("status", "failure", "expected_con"), [
    ("done", None, 1), ("failed", None, 0), ("blocked", {"stage": "route", "reason": "route blocked"}, 0),
])
def test_interact_without_place_change_contradicts_leads_to_only_when_complete(tmp_path, monkeypatch, status, failure, expected_con):
    from agent import loop as module
    per = {"screen": "world", "text": [], "entities": [
        {"kind": "player", "name": "player", "at": [.5, .5]},
        {"kind": "object", "name": "Building", "at": [.7, .5]}]}
    env = GridEnv(); mem = Memory(tmp_path / "memory")
    loop = Loop(env, StubModel([]), mem, "grid")
    monkeypatch.setattr(module, "perceive", lambda *_: (per, reply(per)))
    monkeypatch.setattr(module, "act", fake_act(1, verb="interact"))
    def interact(ctx, aff, *_args, **_kwargs):
        ident = loop.world.entity_ids[1]
        mem.relate(ident, "leads_to", "inside", src="observed", step=0, hit=True, verb="approach")
        frame = ctx.env.screenshot()
        return SimpleNamespace(status=status, reason="input limit" if status == "failed" else "", inputs=1, calls=0, before=frame, after=frame,
                               scroll=(0, 0), blank_seen=False, moves=[], failure=failure)
    monkeypatch.setattr(module, "run_affordance", interact)
    mem.upsert("places", {"id": "inside", "name": "Inside", "screen": "world", "state": "place"})
    loop.world.self_spot, loop.world._self_from_motion = [.5, .5], True

    row = loop.step()

    relations = [r for r in mem.rows("relations") if r.get("p") == "leads_to"]
    relation = relations[0]
    assert row["skill"]["status"] == status and row["next_place"] == row["place"]
    assert len(relations) == 1 and relation["sup"] == 1 and relation["con"] == expected_con and relation["state"] == "live"
    assert relation["verb"] == "approach"

def test_runner_press_exception_and_truncation(tmp_path, monkeypatch):
    from scripts import run_agent
    class Broken(GridEnv):
        def press(self,*a,**k): raise RuntimeError("focus lost")
        def save(self,path): Path(path).write_text("state")
        def close(self): pass
    model=StubModel([reply(perception()),reply({"i": 1, "why": "test"})]); model._post=lambda p: None
    monkeypatch.setattr(run_agent,"PyBoyEnv",lambda *a,**k: Broken())
    monkeypatch.setattr(run_agent,"find_rom",lambda:"rom")
    monkeypatch.setattr(run_agent,"Ollama",lambda *a,**kw:model)
    monkeypatch.setattr(run_agent,"_ledger",lambda *a:None)
    monkeypatch.setattr(run_agent,"read",lambda env:{"map":1})
    run=tmp_path/"run"
    assert run_agent.main(["--headless","--steps","1","--max-calls","5","--run",str(run)]) == 0
    assert (run/"checkpoint/000000/harness.json").exists()
    path=tmp_path/"rows.jsonl"; path.write_text('{"step":1}\n{"step":2}\n')
    run_agent._truncate(path,1); assert len(path.read_text().splitlines()) == 1

def test_runner_call_cap_and_unavailable_checkpoint(tmp_path, monkeypatch):
    from scripts import run_agent
    class Saved(GridEnv):
        def save(self,path): Path(path).write_text("state")
        def close(self): pass
    monkeypatch.setattr(run_agent,"PyBoyEnv",lambda *a,**k:Saved()); monkeypatch.setattr(run_agent,"find_rom",lambda:"rom")
    monkeypatch.setattr(run_agent,"_ledger",lambda *a:None); monkeypatch.setattr(run_agent,"read",lambda env:{})
    class Posts(StubModel):
        def ask(self,*a,**k): self._post({}); return super().ask(*a,**k)
    monkeypatch.setattr(run_agent,"Ollama",lambda *a,**k:model)
    model=Posts([reply(perception()),reply({"i": 1, "why": "test"})]); model._post=lambda p:None
    assert run_agent.main(["--headless","--steps","1","--max-calls","1","--run",str(tmp_path/"cap")]) == 3
    assert (tmp_path/"cap/checkpoint/000000/harness.json").exists()
    class Offline(StubModel):
        def ask(self,*a,**k): raise ModelUnavailable("offline")
    model=Offline([]); model._post=lambda p:None; monkeypatch.setattr(run_agent,"Ollama",lambda *a,**k:model)
    assert run_agent.main(["--headless","--steps","1","--max-calls","5","--run",str(tmp_path/"offline")]) == 4
    assert (tmp_path/"offline/checkpoint/000000/harness.json").exists()

def test_budget_ledger_is_independent_and_survives_truncation(tmp_path, monkeypatch):
    from scripts import run_agent
    monkeypatch.chdir(tmp_path); path=Path("runs/e3_budget.json"); path.parent.mkdir()
    path.write_text(json.dumps({"total":501,"m1":501,"smoke":0,"stage_a":1}))
    run_agent._ledger(Path("runs/m1"),"a",180)
    data=json.loads(path.read_text()); assert (data["total"],data["m1"],data["stage_a"]) == (502,502,2)
    rows=Path("runs/m1/calls.jsonl"); rows.parent.mkdir(exist_ok=True); rows.write_text('{"step":1}\n{"step":2}\n')
    run_agent._truncate(rows,1); assert json.loads(path.read_text())["total"] == 502
    path.write_text(json.dumps({"total":600,"m1":600,"smoke":0,"stage_a":180}))
    with pytest.raises(run_agent.CallCap): run_agent._ledger(Path("runs/m1"),"a",180)

def test_stage_b_cap_override_keeps_phase_and_m1_limits(tmp_path, monkeypatch):
    from scripts import run_agent
    monkeypatch.chdir(tmp_path); path=Path("runs/e3_budget.json"); path.parent.mkdir()
    original={"total":500,"m1":500,"smoke":0,"stage_b":180}
    path.write_text(json.dumps(original))
    with pytest.raises(run_agent.CallCap): run_agent._ledger(Path("runs/m1"),"b",180)
    assert json.loads(path.read_text()) == original
    run_agent._ledger(Path("runs/m1"),"b",300)
    data=json.loads(path.read_text())
    assert (data["total"],data["m1"],data["stage_b"]) == (501,501,181)
    rows=Path("runs/m1/calls.jsonl"); rows.parent.mkdir(exist_ok=True); rows.write_text('{"step":1}\n')
    run_agent._truncate(rows,0); assert json.loads(path.read_text()) == data
    for _ in range(119): run_agent._ledger(Path("runs/m1"),"b",300)
    data=json.loads(path.read_text()); assert data["stage_b"] == 300
    with pytest.raises(run_agent.CallCap): run_agent._ledger(Path("runs/m1"),"b",300)
    assert json.loads(path.read_text()) == data
    phase_exhausted={**data,"total":run_agent.TOTAL_CAP,"stage_b":181}
    path.write_text(json.dumps(phase_exhausted))
    with pytest.raises(run_agent.CallCap): run_agent._ledger(Path("runs/m1"),"b",300)
    assert json.loads(path.read_text()) == phase_exhausted
    m1_exhausted={**data,"m1":run_agent.M1_CAP,"stage_b":181}
    path.write_text(json.dumps(m1_exhausted))
    with pytest.raises(run_agent.CallCap): run_agent._ledger(Path("runs/m1"),"b",300)
    assert json.loads(path.read_text()) == m1_exhausted

def test_stage_b_override_reaches_invocation_guard(tmp_path, monkeypatch):
    from scripts import run_agent
    class Saved(GridEnv):
        def save(self,path): Path(path).write_text("state")
        def close(self): pass
    class Model(StubModel):
        def __init__(self): super().__init__([])
        def _post(self,payload): self.calls+=1
    class ManyRequests:
        def __init__(self,env,model,mem,game): self.env,self.model,self.mem=env,model,mem; self.step_no=0
        def step(self):
            for _ in range(181): self.model._post({})
            self.step_no+=1
            return {"step":self.step_no,"place":"","task":"","choice":{},"verdict":""}
        def state(self): return {"step":self.step_no}
    monkeypatch.setattr(run_agent,"PyBoyEnv",lambda *a,**k:Saved()); monkeypatch.setattr(run_agent,"find_rom",lambda:"rom")
    monkeypatch.setattr(run_agent,"Loop",ManyRequests); monkeypatch.setattr(run_agent,"read",lambda env:{})
    monkeypatch.setattr(run_agent,"_ledger",lambda *a:None); monkeypatch.setattr(run_agent,"Ollama",lambda *a,**k:Model())
    common=["--steps","1","--max-calls","300","--stage","b"]
    assert run_agent.main(["--headless",*common,"--run",str(tmp_path/"default")]) == 3
    assert run_agent.main(["--headless",*common,"--stage-b-cap","300","--run",str(tmp_path/"override")]) == 0

def test_from_step_zero_rebuilds_boot_and_preserves_ledger(tmp_path, monkeypatch):
    from scripts import run_agent
    monkeypatch.chdir(tmp_path); run=tmp_path/"runs/m1"; (run/"checkpoint/000025/memory").mkdir(parents=True)
    (run/"checkpoint/000025/harness.json").write_text('{"step":25}')
    (run/"memory").mkdir(); (run/"memory/places.jsonl").write_text('{"id":"old"}\n')
    for name in ("trace.jsonl","chooser.jsonl","calls.jsonl","truth.jsonl"): (run/name).write_text('{"step":25}\n')
    budget=tmp_path/"runs/e3_budget.json"; budget.parent.mkdir(exist_ok=True); budget.write_text('{"total":33,"m1":33,"smoke":0}')
    class Saved(GridEnv):
        def save(self,path): Path(path).write_text("state")
        def close(self): pass
    monkeypatch.setattr(run_agent,"PyBoyEnv",lambda *a,**k:Saved()); monkeypatch.setattr(run_agent,"find_rom",lambda:"rom")
    monkeypatch.setattr(run_agent,"read",lambda env:{"map":1})
    model=StubModel([reply(perception()),act_reply(None)]); model._post=lambda p:None; monkeypatch.setattr(run_agent,"Ollama",lambda *a,**k:model)
    assert run_agent.main(["--headless","--resume","--from-step","0","--steps","1","--max-calls","5","--run",str(run)]) == 0
    assert (run/"checkpoint/000000/harness.json").exists() and not (run/"checkpoint/000025").exists()
    assert not list((run/"checkpoint/000000/memory").glob("*.jsonl"))
    assert [json.loads(x)["step"] for x in (run/"trace.jsonl").read_text().splitlines()] == [1]
    assert json.loads(budget.read_text())["total"] == 33

def test_resume_prunes_checkpoints_after_selected_step(tmp_path, monkeypatch):
    from scripts import run_agent
    monkeypatch.chdir(tmp_path); run=tmp_path/"runs/m1"
    for n in (10,25):
        cp=run/f"checkpoint/{n:06d}"; (cp/"memory").mkdir(parents=True); (cp/"harness.json").write_text(json.dumps({"step":n,"place":None,"stuck":{}})); (cp/"pyboy.state").write_text("state")
    (run/"trace.jsonl").parent.mkdir(parents=True,exist_ok=True); (run/"trace.jsonl").write_text("".join(json.dumps({"step":n})+"\n" for n in range(1,26)))
    class Saved(GridEnv):
        def save(self,path): Path(path).write_text("state")
        def close(self): pass
    monkeypatch.setattr(run_agent,"PyBoyEnv",lambda *a,**k:Saved()); monkeypatch.setattr(run_agent,"find_rom",lambda:"rom"); monkeypatch.setattr(run_agent,"read",lambda env:{})
    model=StubModel([reply(perception()),act_reply(None)]); model._post=lambda p:None; monkeypatch.setattr(run_agent,"Ollama",lambda *a,**k:model); monkeypatch.setattr(run_agent,"_ledger",lambda *a:None)
    assert run_agent.main(["--headless","--resume","--from-step","10","--steps","1","--max-calls","5","--run",str(run)]) == 0
    assert not (run/"checkpoint/000025").exists() and (run/"checkpoint/000011").exists()
    assert [json.loads(x)["step"] for x in (run/"trace.jsonl").read_text().splitlines()] == list(range(1,12))

def test_keyboard_interrupt_writes_checkpoint(tmp_path, monkeypatch):
    from scripts import run_agent
    class Saved(GridEnv):
        def save(self,path): Path(path).write_text("state")
        def close(self): pass
    class Interrupted(StubModel):
        def ask(self,*a,**k): raise KeyboardInterrupt
    monkeypatch.setattr(run_agent,"PyBoyEnv",lambda *a,**k:Saved()); monkeypatch.setattr(run_agent,"find_rom",lambda:"rom")
    monkeypatch.setattr(run_agent,"read",lambda env:{}); model=Interrupted([]); model._post=lambda p:None
    monkeypatch.setattr(run_agent,"Ollama",lambda *a,**k:model); monkeypatch.setattr(run_agent,"_ledger",lambda *a:None)
    run=tmp_path/"interrupted"
    assert run_agent.main(["--headless","--steps","1","--max-calls","5","--run",str(run)]) == 0
    assert (run/"checkpoint/000000/harness.json").exists()

def test_probe_stage_stops_at_thirty_invocations(tmp_path, monkeypatch):
    from scripts import run_agent
    class Saved(GridEnv):
        def save(self, path): Path(path).write_text("state")
        def close(self): pass
    class Model(StubModel):
        def __init__(self): super().__init__([])
        def _post(self, payload): self.calls += 1
    class ManyRequests:
        def __init__(self, env, model, mem, game): self.env, self.model, self.mem = env, model, mem; self.step_no = 0
        def step(self):
            for _ in range(31): self.model._post({})
            self.step_no += 1
            return {"step": self.step_no, "place": "p", "task": "t", "choice": {}, "verdict": ""}
        def state(self): return {"step": self.step_no}
    ledger = []
    monkeypatch.setattr(run_agent, "PyBoyEnv", lambda *a, **k: Saved()); monkeypatch.setattr(run_agent, "find_rom", lambda: "rom")
    monkeypatch.setattr(run_agent, "Loop", ManyRequests); monkeypatch.setattr(run_agent, "read", lambda env: {"map": 1})
    monkeypatch.setattr(run_agent, "_ledger", lambda *a: ledger.append(a)); monkeypatch.setattr(run_agent, "Ollama", lambda *a, **k: Model())
    assert run_agent.main(["--headless", "--steps", "1", "--max-calls", "100", "--stage", "p", "--run", str(tmp_path/"m1")]) == 3
    assert len(ledger) == 30

def test_probe_stage_stops_after_reaching_town(tmp_path, monkeypatch):
    from scripts import run_agent
    class Saved(GridEnv):
        def save(self, path): Path(path).write_text("state")
        def close(self): pass
    class OneStep:
        def __init__(self, env, model, mem, game): self.env, self.mem, self.step_no = env, mem, 0
        def step(self): self.step_no += 1; return {"step": self.step_no, "place": "p", "task": "t", "choice": {}, "verdict": ""}
        def state(self): return {"step": self.step_no}
    steps = []
    class CountStep(OneStep):
        def step(self): steps.append(1); return super().step()
    monkeypatch.setattr(run_agent, "PyBoyEnv", lambda *a, **k: Saved()); monkeypatch.setattr(run_agent, "find_rom", lambda: "rom")
    monkeypatch.setattr(run_agent, "Loop", CountStep); monkeypatch.setattr(run_agent, "read", lambda env: {"map": 0})
    monkeypatch.setattr(run_agent, "_ledger", lambda *a: pytest.fail("Town stop should precede any model call"))
    class Model(StubModel):
        def _post(self, payload): self.calls += 1
    monkeypatch.setattr(run_agent, "Ollama", lambda *a, **k: Model([]))
    run = tmp_path/"m1"
    assert run_agent.main(["--headless", "--steps", "15", "--max-calls", "30", "--stage", "p", "--run", str(run)]) == 0
    assert steps == [1] and json.loads((run/"checkpoint/000001/harness.json").read_text())["step"] == 1

def test_probe_stage_ledger_persists_thirty_request_cap(tmp_path, monkeypatch):
    from scripts import run_agent
    monkeypatch.chdir(tmp_path)
    for _ in range(30): run_agent._ledger(Path("runs/m1"), "p", 30)
    data = json.loads(Path("runs/e3_budget.json").read_text())
    assert (data["total"], data["m1"], data["stage_p"]) == (30, 30, 30)
    with pytest.raises(run_agent.CallCap): run_agent._ledger(Path("runs/m1"), "p", 30)
    assert json.loads(Path("runs/e3_budget.json").read_text()) == data

def test_failure_text_revisit_and_stall_planner_triggers(tmp_path):
    loop = Loop(GridEnv(), StubModel([]), Memory(tmp_path / "memory"), "grid")
    loop.notebook.data["quests"] = [{"key": "q", "text": "Find the path", "status": "open"}]
    loop.flags = ["cause: approach door failed"]
    loop.cause = "approach door failed"
    assert loop._plan_trigger({"screen": "world"}) is None

    loop._event("T1", "new game text: A sign gives a clue.", 1)
    assert loop._plan_trigger({"screen": "world"}).startswith("new game text:")
    loop.events = []

    loop.mem.upsert("places", {"id": "visited", "name": "Visited", "screen": "world", "state": "place", "visits": 3})
    loop._moved("source", "visited", "door", 4)
    assert not loop.events
    assert loop._plan_trigger({"screen": "world"}) is None

    loop.last_plan, loop.step_no, loop.flags = 3, 4, ["no_progress"]
    loop._event("T4", "stuck: no_progress", 4)
    loop.recent = [{"step": 4, "place": "source", "choice": {"target": "north exit"}, "verdict": "miss"}]
    assert loop._plan_trigger({"screen": "world"}) is None  # three-step floor
    loop.step_no = 5
    assert loop._plan_trigger({"screen": "world"}) == "stuck: no_progress"
    assert "north exit" in loop._evidence()


def test_place_target_closes_after_observation_without_planner_call(tmp_path, monkeypatch):
    from agent import loop as module
    short_settle(monkeypatch); scripted_world(monkeypatch, acts=[chosen(None)])
    loop = Loop(Busy(), StubModel([]), Memory(tmp_path / "memory"), "grid")
    loop.mem.upsert("places", {"id": "lab", "name": "Lab", "screen": "world", "state": "place", "visits": 1})
    loop.mem.match_place = lambda *args, **kwargs: "lab"
    loop.notebook.data["quests"] = [{"key": "lab", "text": "Reach the lab", "target": "place:lab", "opened": 1, "status": "open"}]
    monkeypatch.setattr(module, "plan", lambda *a, **k: pytest.fail("a target closure must not call the planner"))

    row = loop.step()

    assert loop.notebook.data["quests"][0]["status"] == "done"
    assert [event["kind"] for event in loop.events] == ["T3"]
    assert row["verdict"] == "progress" and loop.stuck.progress_n == 0


def test_text_from_target_thing_closes_talk_quest(tmp_path, monkeypatch):
    from agent import loop as module
    per = {"screen": "world", "text": [], "entities": [
        {"kind": "player", "name": "player", "at": [.5, .5]},
        {"kind": "person", "name": "Guide", "at": [.6, .5]},
    ]}
    short_settle(monkeypatch); scripted_world(monkeypatch, per=per, acts=[chosen({"thing": 1, "verb": "interact"})])
    monkeypatch.setattr(module, "effects", lambda *a, **k: {"effects": ["text_appears"], "basis": {"text_appears": "model"}, "to": None})
    frame = Busy().screenshot()
    monkeypatch.setattr(module, "run_affordance", lambda *a, **k: SimpleNamespace(
        status="done", reason="", inputs=0, calls=0, before=frame, after=frame,
        scroll=(0, 0), blank_seen=False, moves=[], failure=None))
    loop = Loop(Busy(), StubModel([]), Memory(tmp_path / "memory"), "grid")
    loop.notebook.data["quests"] = [{"key": "guide", "text": "Talk to the guide", "target": "thing:e_guide", "opened": 1, "status": "open"}]
    monkeypatch.setattr(module, "plan", lambda *a, **k: pytest.fail("a target closure must not call the planner"))

    loop.step()

    assert loop.notebook.data["quests"][0]["status"] == "open"  # touching it is not hearing it
    assert any(r.get("s") == "e_guide" and r.get("p") == "says" and r.get("step") == 1 for r in loop.mem.rows("relations"))
    loop.conversation = {"place": None, "lines": ["Guide: Welcome."], "who": "thing:e_guide"}
    loop._flush_conversation(2)
    assert loop.notebook.data["quests"][0]["status"] == "done" and loop.notebook.observe(3, None) == ["guide"]


def test_act_ops_never_change_notebook(tmp_path, monkeypatch):
    from agent import loop as module
    loop = Loop(GridEnv(), StubModel([reply(perception()), reply(perception())]), Memory(tmp_path / "memory"), "grid")
    loop.notebook.data["quests"] = [{"key": "keep", "text": "Stay focused", "status": "open"}]
    before = json.loads(json.dumps(loop.notebook.data))
    monkeypatch.setattr(module, "act", lambda *a, **k: SimpleNamespace(
        place="", ops=[{"op": "quest", "key": "injected"}], action=None, option=None,
        expect="screen_changes", why="test", text=None))

    loop.step()

    assert {k: v for k, v in loop.notebook.data.items() if k != "here"} == {k: v for k, v in before.items() if k != "here"}  # here: observe's last place, not an act write


def test_plan_ops_apply_and_plan_row_carries_results(tmp_path, monkeypatch):
    from agent import loop as module
    short_settle(monkeypatch); scripted_world(monkeypatch, acts=[chosen(None)])
    loop = Loop(Busy(), StubModel([]), Memory(tmp_path / "memory"), "grid")
    loop.notebook.heard(0, None, "room", "A guide is waiting.")
    loop._plan_trigger = lambda per: "stuck: no_progress"
    plan = SimpleNamespace(ops=[{"op": "add", "key": "guide", "text": "Meet the guide", "why": ["t1"]}], why="new lead")
    monkeypatch.setattr(module, "plan", lambda *a, **k: plan)

    row = loop.step()

    assert loop.notebook.data["quests"][0]["key"] == "guide"
    assert row["plan"]["ops"] == [{"op": "add", "key": "guide", "result": "ok"}]
    assert row["plan"]["why"] == "new lead" and "quests_changed" not in row["plan"]


def test_zero_ok_stuck_plan_reply_increments_backoff(tmp_path, monkeypatch):
    from agent import loop as module
    short_settle(monkeypatch); scripted_world(monkeypatch, acts=[chosen(None)])
    loop = Loop(Busy(), StubModel([]), Memory(tmp_path / "memory"), "grid")
    loop.mem.upsert("places", {"id": "room", "name": "Room", "screen": "world", "state": "place", "visits": 1})
    loop.mem.match_place = lambda *args, **kwargs: "room"
    loop.place = "room"; loop.stuck.seen_places.add("room")
    loop.prev_frame = loop.env.screenshot()
    loop._plan_trigger = lambda per: "stuck: no_progress"
    plans = iter([SimpleNamespace(ops=[{"op": "done", "key": "missing", "reason": "finished"}], why="nothing applied"),
                  SimpleNamespace(ops=[], why="nothing applied")])
    monkeypatch.setattr(module, "plan", lambda *a, **k: next(plans))

    row = loop.step()

    assert loop.stuck.u == 1
    assert row["plan"]["ops"] == [{"op": "done", "key": "missing", "result": "error: key is not open"}]


def test_plan_returns_operations_and_why(tmp_path):
    from agent.plan import plan
    notebook = Notebook(tmp_path)
    operation = {"op": "add", "key": "help", "text": "Help the person", "why": ["t1"]}
    parsed = plan(StubModel([reply({"focus": "", "ops": [operation], "why": "new information"})]),
                  "text", notebook, "", [], [], evidence="short packet")
    assert parsed.ops == [operation] and parsed.why == "new information"
    missing = plan(StubModel([reply({"focus": "", "why": "nothing new"})]), "cycle", notebook, "", [], [], evidence="")
    assert missing.ops is None and missing.why == "nothing new"


def test_focused_off_screen_static_quest_target_stays_in_menu(tmp_path, monkeypatch):
    from agent import loop as module

    loop = Loop(GridEnv(), StubModel([]), Memory(tmp_path / "memory"), "grid")
    loop.mem.upsert("places", {"id": "room", "name": "Room", "screen": "world", "state": "place", "visits": 1})
    loop.mem.upsert("entities", {"id": "crate1", "name": "crate", "kind": "furniture", "place": "room", "tile": [2, 2], "status": "remembered", "last": 1, "seen": 1})
    loop.mem.match_place = lambda *args, **kwargs: "room"
    loop.world.current_place = loop.place = "room"
    loop.world.self_spot, loop.world._self_from_motion = [.5, .5], True
    loop.notebook.data["quests"] = [{"key": "crate", "text": "Inspect the crate", "why": [], "target": "thing:crate1", "status": "open"}]
    data = {"screen": "world", "text": [], "entities": [{"kind": "player", "name": "player", "at": [.5, .5]}]}
    monkeypatch.setattr(module, "perceive", lambda *_: (data, reply(data)))
    monkeypatch.setattr(module, "cut", lambda *_a, **_k: False)
    menus = []
    monkeypatch.setattr(module, "act", lambda _model, _per, _focus, options, *_a, **_k: menus.append(options) or SimpleNamespace(
        place="", ops=[], action=None, expect="screen_changes", why="test", text=None))

    loop.step()

    assert any(row.get("ref") == "thing:crate1" for row in menus[0])


def test_planner_prioritizes_game_text_and_checks_it_before_throttle(tmp_path):
    loop = Loop(GridEnv(), StubModel([]), Memory(tmp_path / "memory"), "grid")
    loop.notebook.data["quests"] = [{"key": "g", "text": "explore", "status": "open"}]
    loop.step_no = loop.last_plan = 10
    loop.text_for_trigger = "Someone is looking for you. " + "x" * 220
    loop._event("T1", "new game text: " + loop.text_for_trigger[:200], 10)

    assert loop._plan_trigger({"screen": "dialogue"}) is None
    loop.step_no = 12
    assert loop._plan_trigger({"screen": "world"}) == "new game text: " + loop.text_for_trigger[:200]


def test_dialogue_text_replans_on_a_naming_menu(tmp_path):
    loop = Loop(GridEnv(), StubModel([]), Memory(tmp_path / "memory"), "grid")
    loop.notebook.data["quests"] = [{"key": "q1", "text": "Choose a name", "status": "open"}]
    loop.last_plan, loop.step_no = 19, 24
    loop._event("T1", "Erm, what is his name again?", 24)
    for screen in ("world", "menu", "keyboard"):
        assert "what is his name again" in loop._plan_trigger({"screen": screen})
    for screen in ("dialogue", "battle"):
        assert loop._plan_trigger({"screen": screen}) is None


@pytest.mark.parametrize("case", ["closes_without_adding", "rejected_target", "invalid_twice", "empty_ops", "malformed"])
def test_plan_followup_before_act_is_bounded_and_reads_errors(tmp_path, monkeypatch, case):
    from agent import loop as module
    from agent.act import act
    short_settle(monkeypatch)
    scripted_world(monkeypatch)
    monkeypatch.setattr(module, "act", act)
    first = {
        "closes_without_adding": [{"op": "done", "key": "q1", "reason": "Professor Oak has acknowledged the name RED and the introduction sequence has progressed."}],
        "rejected_target": [{"op": "add", "key": "q3", "text": "Find Professor Oak (t4: 'PROF. OAK, next door, is looking for you.')", "why": ["t4"], "target": "PROF. OAK"}],
        "invalid_twice": [{"op": "add", "key": "q3", "text": "Leave the office and begin the adventure (t3: 'Your very own POKÉMON legend is about to unfold!')", "target": "B"}],
        "empty_ops": [],
        "malformed": None,
    }[case]
    second = first if case == "invalid_twice" else [{"op": "add", "key": "q2", "text": "Choose the rival's name", "why": ["t1"]}]
    plans = iter([first, second]); sent = []
    class Capture(StubModel):
        def ask(self, system, user, images=(), schema=None, *, role=""):
            sent.append((role, user))
            if role == "plan":
                ops = next(plans)
                return reply({"goals": [], "why": "x"} if case == "malformed" else {"ops": ops, "why": "follow the game text"})
            return reply({"i": 0, "why": "no action"})
    loop = Loop(Busy(), Capture([]), Memory(tmp_path / "memory"), "grid")
    for text in ("What is his name again?", "Introduction", "Begin your adventure", "PROF. OAK, next door, is looking for you."):
        loop.notebook.heard(0, None, "room", text)
    if case == "closes_without_adding":
        loop.notebook.apply_ops([{"op": "add", "key": "q1", "text": "Choose your name", "why": ["t1"]}], 0)
    loop._event("T1", "new game text", 1)

    row = loop.step()

    plan_prompts = [user for role, user in sent if role == "plan"]
    assert len(plan_prompts) == (1 if case in ("empty_ops", "malformed") else 2)
    if case == "malformed":
        assert row["plan"]["ops"] == [{"op": None, "key": None, "result": "error: ops must be a list"}]
    if case in ("closes_without_adding", "rejected_target"):
        assert loop.notebook.focus == "q2"
        assert all(user.startswith("Goal:") and not user.startswith("Goal: (none)") for role, user in sent if role == "act")
        assert row["plan"]["focus"] == "q2"
    if case == "rejected_target":
        assert row["plan"]["ops"][0]["result"].startswith("error: target")
        assert row["plan"]["ops"][0]["result"] in plan_prompts[1]
    if case == "invalid_twice":
        assert loop.notebook.focus is None
        assert len(row["plan"]["ops"]) == 2
        loop.step()
        assert len([user for role, user in sent if role == "plan"]) == 2


def test_actor_speaker_name_attaches_after_new_text(tmp_path, monkeypatch):
    from agent import loop as module
    from types import SimpleNamespace

    before = {"screen": "world", "text": [], "entities": [
        {"kind": "player", "name": "player", "at": [.5, .5]},
        {"kind": "npc", "name": "person", "at": [.6, .5]}]}
    after = {**before, "text": ["MOM: Hello there."]}
    perceptions, calls = [before, after], [0]
    def perceive(*_):
        data = perceptions[min(calls[0], 1)]; calls[0] += 1
        return data, reply(data)
    monkeypatch.setattr(module, "perceive", perceive)
    monkeypatch.setattr(module, "act", lambda *a, **k: SimpleNamespace(
        place="", ops=[], action={"thing": 1, "verb": "interact"}, expect="text_appears", why="talk", text=None))
    def interact(ctx, *_a, **_kw):
        frame = ctx.env.screenshot()
        return SimpleNamespace(status="done", reason="", inputs=1, calls=0, before=frame, after=frame,
                               scroll=(0, 0), blank_seen=False, moves=[])
    monkeypatch.setattr(module, "run_affordance", interact)
    monkeypatch.setattr(module, "effects", lambda *_a, **_kw: {"effects": ["text_appears"], "basis": {"text_appears": "pixel"}})

    loop = Loop(GridEnv(), StubModel([]), Memory(tmp_path / "memory"), "grid")
    loop.world.self_spot, loop.world._self_from_motion = [.5, .5], True
    loop.step()

    actor = next(row for row in loop.mem.rows("entities") if row.get("name") == "person")
    assert actor["called"] == "MOM"








def test_act_buttons_are_pressed_in_order(tmp_path):
    class Recording(GridEnv):
        def __init__(self): super().__init__(); self.keys = []
        def press(self, key, frames=8, on_frame=None): self.keys.append(key); super().press(key, frames, on_frame)
    env = Recording()
    picks = [reply({"i": 3, "why": "walk"}), reply({"buttons": ["UP", "A"]})]
    loop = Loop(env, StubModel([reply(perception()), *picks, reply(perception()), reply(perception())]), Memory(tmp_path / "memory"), "grid")

    row = loop.step()

    assert env.keys == ["UP", "A"] and row["choice"]["verb"] == "buttons"


def test_act_invalid_action_waits_and_is_unobservable(tmp_path):
    invalid = reply({"i": 999, "why": "?"})
    loop = Loop(GridEnv(), StubModel([reply(perception()), invalid, invalid, reply(perception())]), Memory(tmp_path / "memory"), "grid")

    row = loop.step()

    assert row["choice"] is None and row["act"]["action"] is None and row["verdict"] == "unobservable"


def test_option_pick_drives_grid_through_its_exit(tmp_path, monkeypatch):
    from agent import loop as module
    env = GridEnv()
    def perceive(*_):
        per = env.perception(); return per, reply(per)
    monkeypatch.setattr(module, "perceive", perceive)
    pick = reply({"i": 1, "why": "test"})
    loop = Loop(env, StubModel([pick]), Memory(tmp_path / "memory"), "grid")
    loop.world.self_spot, loop.world._self_from_motion = [.5, .5], True

    row = loop.step()

    assert row["option"]["n"] == 1 and row["option"]["state"] == "new"
    assert row["option"]["label"].startswith("go to ") and row["option"]["notes"] == ["exit, never crossed"]
    assert row["act"]["action"]["thing"] == 2 and row["choice"]["verb"] == "approach" and row["choice"]["target"] == "exit: door"
    assert env.scene == 1

def test_picking_unknown_exit_on_player_tile_sends_outward_key(tmp_path, monkeypatch):
    from agent import loop as module
    class Recording(GridEnv):
        def __init__(self):
            super().__init__(); self.keys = []
        def press(self, key, frames=8, on_frame=None):
            self.keys.append(key); super().press(key, frames, on_frame)

    env = Recording()
    def perceive(*_):
        per = env.perception()
        player = next(e for e in per["entities"] if e.get("kind") == "player")
        exit = next(e for e in per["entities"] if e.get("kind") == "exit")
        exit["at"] = list(player["at"])
        return per, reply(per)
    monkeypatch.setattr(module, "perceive", perceive)
    loop = Loop(env, StubModel([reply({"i": 1, "why": "test"})]), Memory(tmp_path / "memory"), "grid")
    loop.world.self_spot, loop.world._self_from_motion = [.5, .5], True
    monkeypatch.setattr(loop.world, "occupied", lambda _place: frozenset())
    monkeypatch.setattr(loop.world, "resolve", lambda _ref, _per, place: {
        "kind": "exit", "place": place, "tile": tuple(loop.world.pos), "id": "door", "stale": False,
        "exit": {"tile": tuple(loop.world.pos), "dir": "", "to": None, "doors": []}})

    row = loop.step()

    assert row["act"]["action"]["go_to"].startswith("exit:")
    assert row["skill"]["inputs"] >= 1 and env.keys
    assert row["skill"]["status"] == "blocked" and row["skill"]["failure"]["reason"] == "exit not crossed"

def test_go_to_unexplored_walks_to_nearest_frontier(tmp_path):
    from agent.space import fold
    loop = Loop(GridEnv(), StubModel([]), Memory(tmp_path / "memory"), "grid")
    fold(loop.mem, "p", (0, 0), [{"dir": d, "shift": 0} for d in ("UP", "DOWN", "LEFT")], 16, 1)  # walls on three sides, RIGHT untried
    loop.pos = (0, 0)
    routes = loop.world.explore_dirs("p")
    assert loop._unexplored("p") == routes["RIGHT"]
    assert loop._unexplored("p", "RIGHT") == routes["RIGHT"]
    assert len(routes["RIGHT"]["dirs"]) >= 4
    from agent.space import mark_exit
    mark_exit(loop.mem, "q", (0, 0), "DOWN", "r", (0, 0), 2)  # arriving in r: r@(0,0) gets an inferred UP exit back to q
    fold(loop.mem, "r", (0, 0), [{"dir": d, "shift": 0} for d in ("LEFT", "RIGHT", "DOWN")], 16, 3)
    assert loop._unexplored("r") == loop.world.explore_dirs("r").get("UP")
    loop.tile_px = loop.world.tile_px = None
    assert loop._unexplored("p") is None


def test_new_place_event_replans_after_floor(tmp_path):
    loop = Loop(GridEnv(), StubModel([]), Memory(tmp_path / "memory"), "grid")
    loop.notebook.data["quests"] = [{"key": "q", "text": "explore", "status": "open"}]
    for pid in ("house", "town"): loop.mem.upsert("places", {"id": pid, "name": pid, "screen": "world", "state": "place", "visits": 1})
    loop.last_plan = loop.step_no = 10  # a plan just ran: ordinary triggers are throttled
    loop._moved("house", "town", "door", 11)
    assert [e["text"] for e in loop.events] == ["arrived somewhere new: town"] and loop._plan_trigger({}) is None
    loop.step_no = 12
    assert loop._plan_trigger({}) == "arrived somewhere new: town"


def test_repeated_failure_flags_act_and_replans_once_on_new_evidence(tmp_path, monkeypatch):
    from agent import loop as module
    env = GridEnv()
    def perceive(*_):
        return {"screen": "world", "text": [], "entities": [{"name": "player", "kind": "player", "at": [.5, .5]}, {"name": "door", "kind": "exit", "at": [.6, .5]}]}, reply({})
    monkeypatch.setattr(module, "perceive", perceive)
    seen_options = []
    def act(*a, **k):
        seen_options.append(a[3])
        row = next(o for o in a[3] if o["tool"] == "approach")
        return SimpleNamespace(action=dict(row["action"]), option=row["n"], why="t", text=None)
    monkeypatch.setattr(module, "act", act)
    def blocked(ctx, aff, per, **k):
        frame = env.screenshot(); return SimpleNamespace(status="blocked", reason="wall", inputs=1, calls=0, before=frame, after=frame, scroll=(0, 0), blank_seen=False, moves=[])
    monkeypatch.setattr(module, "run_affordance", blocked)
    loop = Loop(env, StubModel([]), Memory(tmp_path / "memory"), "grid")
    loop.world.self_spot, loop.world._self_from_motion = [.5, .5], True
    loop.notebook.data["quests"] = [{"key": "q", "text": "explore", "status": "open"}]
    loop.step(); assert loop.cause
    row = loop.step()  # ordinary failures remain visible to act and evidence, not triggers
    assert row["plan"] is None
    row = loop.step(); assert row["plan"] is None
    failed = [o for o in seen_options[-1] if o["tool"] == "approach"]
    assert failed and all(o["state"] == "failed" and "2×" in "; ".join(o["notes"]) for o in failed)


def test_act_known_places_are_navigable_named_places_only(tmp_path, monkeypatch):
    from agent import loop as loop_module
    seen = {}
    def recording_act(model, per, focus, options, inputs, controls):
        seen.update(focus=focus, options=options)
        return SimpleNamespace(action=None, why="", text=None)
    monkeypatch.setattr(loop_module, "act", recording_act)
    mem = Memory(tmp_path / "memory")
    mem.upsert("places", {"id": "p_hall", "name": "Hall", "screen": "world", "state": "place", "visits": 1, "last": 2})
    mem.upsert("places", {"id": "p_menu", "screen": "menu", "state": "candidate", "last": 3})
    world = perception(); world["screen"] = "world"
    loop = Loop(GridEnv(), StubModel([reply(world), reply(world)]), mem, "grid")  # before and after perception
    loop.world.self_spot, loop.world._self_from_motion = [.5, .5], True
    loop.mem.match_place = lambda *args, **kwargs: "p_hall"
    loop.step()
    assert "Here: Hall" in seen["focus"] and all(o.get("ref") != "place:p_menu" for o in seen["options"])


def test_fail_key_merges_rewordings(tmp_path):
    loop = Loop(GridEnv(), StubModel([]), Memory(tmp_path / "memory"), "grid")
    key = loop._fail_key
    assert len({key("p", "buttons", t) for t in ("UP A", "UP", "up UP up A")}) == 1 and key("p", "buttons", "A") != key("p", "buttons", "B")
    assert key("p", "go_to", "Stairs") == key("p", "approach", "stairs") != key("q", "approach", "stairs") and key("p", "advance", "") == "p|advance|advance"
    assert key("p", "interact", "npc") != key("p", "approach", "npc")  # an interaction and an approach are different intents


def test_three_presses_into_a_wall_thing_refute_it_but_keep_it(tmp_path, monkeypatch):
    from agent import loop as module
    env = GridEnv(); env.walls.add((8, 7))  # the fake exit is drawn on a wall one tile north
    monkeypatch.setattr(module, "perceive", lambda *_: ({"screen": "world", "text": [], "entities": [
        {"name": "player", "kind": "player", "at": [.5, .5]}, {"name": "exit: doorway", "kind": "exit", "at": [.5, .444]}]}, reply({})))
    monkeypatch.setattr(module, "act", lambda *a, **k: SimpleNamespace(place="", ops=[], action={"buttons": ["UP"]}, expect="screen_changes", why="t", text=None))
    loop = Loop(env, StubModel([]), Memory(tmp_path / "memory"), "grid")
    loop.world.self_spot, loop.world._self_from_motion = [.5, .5], True
    rows = [loop.step() for _ in range(3)]
    exit_id = next(r["id"] for r in loop.mem.rows("entities") if r["name"] == "exit: doorway")
    dn = [r for r in loop.mem.rows("relations") if r["s"] == exit_id and r["p"] == "does_nothing" and r["o"] == ""]
    assert [r["outcome"]["effects"] for r in rows] == [["no_change"]] * 3
    assert len(dn) == 1 and dn[0]["state"] == "refuted" and dn[0]["con"] == 3 and dn[0]["ev"] == [1, 2, 3]
    assert "not working: walked into 3×, steps 1–3" in loop.world.view("", loop.place)
    assert any(r["id"] == exit_id for r in loop.mem.rows("entities"))  # refuted, never deleted


def test_real_exit_used_once_is_not_refuted(tmp_path, monkeypatch):
    from agent import loop as module
    env = GridEnv(); env.door = (8, 7)  # a real exit one tile north: walking onto it changes the scene
    monkeypatch.setattr(module, "perceive", lambda *_: ({"screen": "world", "text": [], "entities": [
        {"name": "player", "kind": "player", "at": [.5, .5]}, {"name": "exit: door", "kind": "exit", "at": [.5, .444]}]}, reply({})))
    monkeypatch.setattr(module, "act", lambda *a, **k: SimpleNamespace(place="", ops=[], action={"buttons": ["UP"]}, expect="place_changes", why="t", text=None))
    loop = Loop(env, StubModel([]), Memory(tmp_path / "memory"), "grid")
    loop.world.self_spot, loop.world._self_from_motion = [.5, .5], True
    row = loop.step()
    assert "place_changes" in row["outcome"]["effects"]
    assert not [r for r in loop.mem.rows("relations") if r["p"] == "does_nothing" and r["state"] == "refuted"]
    assert "not working" not in loop.world.view("", loop.place)


def test_same_text_from_the_same_thing_is_noted_as_said_before(tmp_path, monkeypatch):
    from agent import loop as module
    env, state = GridEnv(), {"text": []}
    monkeypatch.setattr(module, "perceive", lambda *_: ({"screen": "world", "text": list(state["text"]), "entities": [
        {"name": "player", "kind": "player", "at": [.5, .5]}, {"name": "sign", "kind": "object", "at": [.6, .5]}]}, reply({})))
    monkeypatch.setattr(module, "act", lambda *a, **k: SimpleNamespace(place="", ops=[], action={"thing": 1, "verb": "interact"}, expect="text_appears", why="t", text=None))
    def read(ctx, aff, per, text=None, exit=False, **kwargs):  # the sign's box opens with the same line each time
        before = env.screenshot(); env.text_box = True; state["text"] = ["Hi"]; after = env.screenshot()
        return SimpleNamespace(status="done", reason="", inputs=1, calls=0, before=before, after=after, scroll=(0, 0), blank_seen=False, moves=[])
    monkeypatch.setattr(module, "run_affordance", read)
    loop = Loop(env, StubModel([]), Memory(tmp_path / "memory"), "grid")
    loop.world.self_spot, loop.world._self_from_motion = [.5, .5], True
    loop.step()
    env.text_box, state["text"] = False, []
    loop.step()
    says = [r for r in loop.mem.rows("relations") if r["p"] == "says" and r["o"] == "Hi"]
    assert len(says) == 1 and says[0]["why"] == "says the same as before (step 1)"


def test_successful_use_clears_a_refuted_thing_in_place(tmp_path, monkeypatch):
    from agent import loop as module
    env = GridEnv(); env.walls.add((8, 7))  # the fake exit: three presses into a wall, then one real use
    monkeypatch.setattr(module, "perceive", lambda *_: ({"screen": "world", "text": [], "entities": [
        {"name": "player", "kind": "player", "at": [.5, .5]}, {"name": "exit: doorway", "kind": "exit", "at": [.5, .444]}]}, reply({})))
    actions = iter([{"buttons": ["UP"]}] * 3 + [{"thing": 1, "verb": "approach"}])
    monkeypatch.setattr(module, "act", lambda *a, **k: SimpleNamespace(place="", ops=[], action=next(actions), expect="screen_changes", why="t", text=None))
    def use(ctx, aff, per, text=None, exit=False, **kwargs):  # the thing works this time: the screen changes
        before = env.screenshot(); env.text_box = True; after = env.screenshot()
        return SimpleNamespace(status="done", reason="", inputs=1, calls=0, before=before, after=after, scroll=(0, 0), blank_seen=False, moves=[])
    monkeypatch.setattr(module, "run_affordance", use)
    loop = Loop(env, StubModel([]), Memory(tmp_path / "memory"), "grid")
    loop.world.self_spot, loop.world._self_from_motion = [.5, .5], True
    for _ in range(3): loop.step()
    exit_id = next(r["id"] for r in loop.mem.rows("entities") if r["name"] == "exit: doorway")
    assert "not working: walked into 3×, steps 1–3" in loop.world.view("", loop.place)
    row = loop.step()
    assert row["choice"]["verb"] == "approach" and row["outcome"]["effects"] != ["no_change"]
    dn = [r for r in loop.mem.rows("relations") if r["s"] == exit_id and r["p"] == "does_nothing" and r["o"] == ""]
    assert len(dn) == 1 and dn[0]["state"] == "live" and dn[0]["con"] == 0 and dn[0]["ev"] == []  # reset in place, not deleted
    assert "not working" not in loop.world.view("", loop.place) and any(r["id"] == exit_id for r in loop.mem.rows("entities"))


def _seen_next_step(tmp_path, monkeypatch, pos=(2, 0), exit_tile=True):
    """Loop in room at pos; its next observation is hall, with no scene cut and no press counted by World.after."""
    from agent import loop as module
    mem = Memory(tmp_path / "memory"); room(mem)
    if exit_tile: mark_exit(mem, "room", (2, 0), "RIGHT", "hall", (0, 0), 1)
    per = {"screen": "world", "text": [], "entities": [{"name": "player", "kind": "player", "at": [.5, .5]}]}
    monkeypatch.setattr(module, "perceive", lambda *_: (per, reply({})))
    monkeypatch.setattr(module, "act", fake_act(None))
    env = GridEnv(); loop = Loop(env, StubModel([]), mem, "grid")
    loop.prev_frame = env.screenshot()  # the previous step's frame: same as now, so no cut
    loop.place, loop.world.current_place, loop.world.pos = "room", "room", pos
    loop.world._self_from_motion, loop.world.self_spot = True, [.5, .5]  # no probe press
    loop.mem.match_place = lambda *args, **kwargs: "hall"
    return loop, mem, per


@pytest.mark.parametrize("pos", [(2, 0), (1, 1)])
def test_place_change_seen_only_by_next_observation_counts_only_its_departure(tmp_path, monkeypatch, pos):
    loop, mem, per = _seen_next_step(tmp_path, monkeypatch, pos)
    row = loop.step()
    assert row["verified_edge"]
    assert mem.get("crossings", "room@2,0>hall")["n"] == 1 and mem.get("crossings", "hall@0,0>room") is None
    assert any(o["label"].startswith("go through RIGHT") and any("crossed 1×" in note for note in o["notes"]) for o in loop.world.options(per, "room", {}, 3, inputs=INPUTS))
    assert any(o["label"].startswith("go to LEFT") and "exit, never crossed" in o["notes"] for o in loop.world.options(per, "hall", {}, 3, inputs=INPUTS))  # hall was entered with no arrival evidence: no distance until a view places you


def test_place_change_with_no_exit_tile_counts_nothing_and_does_not_crash(tmp_path, monkeypatch):
    loop, mem, _ = _seen_next_step(tmp_path, monkeypatch, exit_tile=False)
    assert loop.step()["next_place"] == "hall" and mem.rows("crossings") == []


def test_crossing_already_counted_by_the_scene_cut_is_not_counted_again(tmp_path, monkeypatch):
    loop, mem, _ = _seen_next_step(tmp_path, monkeypatch)
    loop.world._cross("room", (2, 0), "hall", (0, 0), 1)  # what World.after counts for a scene cut after a press
    loop.place, loop.world.current_place, loop.world.pos = "hall", "hall", (0, 0)  # the end of that step
    row = loop.step()
    assert not row["verified_edge"]
    assert mem.get("crossings", "room@2,0>hall")["n"] == 1 and mem.get("crossings", "hall@0,0>room") is None


def test_any_arrival_replans_after_the_floor_and_new_places_skip_it(tmp_path):
    loop = Loop(GridEnv(), StubModel([]), Memory(tmp_path / "memory"), "grid")
    for pid, visits in (("a", 3), ("b", 3), ("c", 1)): loop.mem.upsert("places", {"id": pid, "name": pid.upper(), "screen": "world", "visits": visits})
    loop.notebook.data["quests"] = [{"key": "g", "text": "leave", "status": "open"}]
    loop.step_no, loop.last_plan = 10, 10

    loop._moved("a", "b", "door", 11)
    assert not loop.events
    assert loop._plan_trigger({}) is None  # inside the 3-step floor
    loop.step_no = 12
    assert loop._plan_trigger({}) is None
    loop.last_plan = 11
    loop._moved("b", "c", "door", 13)
    assert [e["text"] for e in loop.events] == ["arrived somewhere new: C"]
    assert loop._plan_trigger({}) is None  # still inside the floor: it waits
    loop.step_no = 13
    assert loop._plan_trigger({}) == "arrived somewhere new: C"


def _room_with_open_east_edge(mem, place):  # 3x3 open room, walls around it, (1,0) untried to the RIGHT into unseen (2,0)
    for x in range(-2, 3):
        for y in range(-2, 3):
            if (x, y) != (2, 0): mem.upsert("tiles", {"id": f"{place}@{x},{y}", "place": place, "x": x, "y": y, "floor": -1 <= x <= 1 and -1 <= y <= 1, "blocked": [], "pushed": [], "exit": None, "step": 1})


def test_go_to_unexplored_routes_around_an_actor_on_the_way(tmp_path):
    from agent.space import DIRS
    loop = Loop(GridEnv(), StubModel([]), Memory(tmp_path / "memory"), "grid")
    _room_with_open_east_edge(loop.mem, "p"); loop.pos = (-1, 0); loop.world.current_place = "p"; loop.world.actor_tiles = frozenset({(0, 0)})

    explore = loop._unexplored("p", "RIGHT")
    dirs = explore["dirs"]

    at, visited = (-1, 0), [(-1, 0)]
    for d in dirs: at = (at[0] + DIRS[d][0], at[1] + DIRS[d][1]); visited.append(at)
    assert explore["to"] == (2, 0) and dirs[-1] == "RIGHT" and (0, 0) not in visited


def test_go_to_unexplored_skips_a_frontier_whose_step_cell_is_held(tmp_path):
    loop = Loop(GridEnv(), StubModel([]), Memory(tmp_path / "memory"), "grid")
    _room_with_open_east_edge(loop.mem, "p"); loop.pos = (-1, 0); loop.world.current_place = "p"; loop.world.actor_tiles = frozenset({(2, 0)})

    assert loop._unexplored("p", "RIGHT") is None


def test_go_to_unexplored_direction_walks_route_and_marks_new_view(tmp_path, monkeypatch):
    from agent import loop as module
    class Recording(GridEnv):
        def __init__(self): super().__init__(); self.keys = []
        def press(self, key, frames=8, on_frame=None): self.keys.append(key); super().press(key, frames, on_frame)
    env = Recording(); env.door = (100, 100); per = env.perception()
    monkeypatch.setattr(module, "perceive", lambda *_: (per, reply({})))
    monkeypatch.setattr(module, "act", lambda *a, **k: SimpleNamespace(place="", ops=[], action={"go_to": "unexplored", "dir": "RIGHT"}, expect="screen_changes", why="explore", text=None))
    loop = Loop(env, StubModel([]), Memory(tmp_path / "memory"), "grid")
    loop.world._self_from_motion = True
    route = {"dirs": ["RIGHT"] * 4, "to": (0, 0)}
    loop.world.explore_dirs = lambda place: {"RIGHT": route}

    loop.step()

    assert env.keys == route["dirs"]
    assert loop.world.novel >= env.view_tiles[1]


@pytest.mark.parametrize("action", [{"go_to": "npc"}, {"thing": 1, "verb": "approach"}, {"thing": 1, "verb": "interact"}])
def test_on_screen_npc_routes_beside_it_facing_it_around_another_actor(tmp_path, monkeypatch, action):
    from agent import loop as module
    per = {"screen": "world", "text": [], "entities": [
        {"name": "player", "kind": "player", "at": [.5, .5]},
        {"name": "npc", "kind": "npc", "at": [.25, .5]},  # two tiles left of the player
        {"name": "guard", "kind": "npc", "at": [.35, .5]}]}  # the tile between them
    monkeypatch.setattr(module, "perceive", lambda *_: (per, reply({})))
    monkeypatch.setattr(module, "act", lambda *a, **k: SimpleNamespace(place="", ops=[], action=action, expect="screen_changes", why="talk", text=None))
    loop = Loop(GridEnv(), StubModel([]), Memory(tmp_path / "memory"), "grid")
    loop.world.self_spot, loop.world._self_from_motion = [.5, .5], True
    seen = {}
    def run(ctx, aff, *_args, **kwargs):
        seen.update(route=kwargs.get("route"))
        frame = ctx.env.screenshot()
        return SimpleNamespace(status="done", reason="", inputs=0, calls=0,
                               before=frame, after=frame, scroll=(0, 0), blank_seen=False, moves=[])
    monkeypatch.setattr(module, "run_affordance", run)

    loop.step()

    assert seen["route"]["dirs"] == ["UP", "LEFT", "LEFT", "DOWN"]


def test_interact_without_beside_route_is_refused_without_inputs(tmp_path, monkeypatch):
    from agent import loop as module
    per = {"screen": "world", "text": [], "entities": [
        {"name": "player", "kind": "player", "at": [.5, .5]},
        {"name": "block", "kind": "object", "at": [.6, .5]}]}
    monkeypatch.setattr(module, "perceive", lambda *_: (per, reply({})))
    monkeypatch.setattr(module, "act", lambda *a, **k: SimpleNamespace(place="", ops=[], action={"thing": 1, "verb": "interact"}, expect="screen_changes", why="check", text=None))
    monkeypatch.setattr(module.World, "beside", lambda *_: None)
    env = GridEnv(); keys = []; env.press = lambda key, *a, **k: keys.append(key)
    loop = Loop(env, StubModel([]), Memory(tmp_path / "memory"), "grid")
    loop.world.self_spot, loop.world._self_from_motion = [.5, .5], True

    row = loop.step()

    assert row["skill"]["failure"] == {"stage": "route", "reason": "no route beside target", "direction": None}
    assert row["skill"]["inputs"] == 0 and keys == []


def fail_affordance(monkeypatch, env, *, status="blocked", reason="wall", failure=None):
    """Every affordance returns this result with an unchanged screen."""
    from agent import loop as module
    def run(ctx, aff, per, **kwargs):
        frame = env.screenshot()
        return SimpleNamespace(status=status, reason=reason, inputs=1, calls=0, before=frame, after=frame, scroll=(0, 0), blank_seen=False, moves=[], failure=failure)
    monkeypatch.setattr(module, "run_affordance", run)


def npc_scene(monkeypatch, env, **result):
    """A room with an npc one tile east; every affordance fails the way fail_affordance says."""
    from agent import loop as module
    per = {"screen": "world", "text": [], "entities": [{"name": "player", "kind": "player", "at": [.5, .5]}, {"name": "npc", "kind": "npc", "at": [.6, .5]}]}
    monkeypatch.setattr(module, "perceive", lambda *_: (per, reply({})))
    fail_affordance(monkeypatch, env, **result)


def scripted_act(monkeypatch, script):
    """One answer per step, shaped like act's: an option pick carries that option's action and number, a free action carries itself."""
    from agent import loop as module
    steps, seen = iter(script), []
    def fake(*args, **kwargs):
        kind, value = next(steps); seen.append(bool(kwargs))
        o = next(o for o in args[3] if o["label"].startswith(value + ",")) if kind == "option" else None
        return SimpleNamespace(place="", ops=[], action=dict(o["action"]) if o else value, option=o["n"] if o else None, expect="screen_changes", why="t", text=None)
    monkeypatch.setattr(module, "act", fake)
    return seen


@pytest.mark.parametrize("decide", [False, True])
def test_equivalent_resolved_intents_share_one_counter_and_interaction_is_its_own(tmp_path, monkeypatch, decide):
    env = GridEnv(); npc_scene(monkeypatch, env)
    seen = scripted_act(monkeypatch, [("action", {"thing": 1, "verb": "approach"}), ("action", {"thing": 1, "verb": "approach"}), ("action", {"go_to": "npc"}), ("option", "talk to npc")])
    loop = Loop(env, StubModel([]), Memory(tmp_path / "memory"), "grid", decide=decide)
    loop.world.self_spot, loop.world._self_from_motion = [.5, .5], True
    for _ in range(4): loop.step()
    ident = loop.world.entity_ids[1]
    assert seen == [False] * 4
    assert {k: v["n"] for k, v in loop.fails.items()} == {f"{loop.place}|approach|thing:{ident}": 3, f"{loop.place}|interact|thing:{ident}": 1}


def test_options_without_intent_key_still_count_failures(tmp_path, monkeypatch):
    env = GridEnv(); npc_scene(monkeypatch, env); scripted_act(monkeypatch, [("option", "talk to npc")])
    loop = Loop(env, StubModel([]), Memory(tmp_path / "memory"), "grid")
    loop.world.self_spot, loop.world._self_from_motion = [.5, .5], True
    real = loop.world.options
    loop.world.options = lambda *a, **k: [{name: v for name, v in o.items() if name != "intent_key"} for o in real(*a, **k)]  # rows as an older checkpoint made them
    row = loop.step()
    assert row["choice"]["verb"] == "interact" and sum(v["n"] for v in loop.fails.values()) == 1


def test_counters_and_identity_round_trip(tmp_path, monkeypatch):
    env = GridEnv(); npc_scene(monkeypatch, env); scripted_act(monkeypatch, [("action", {"thing": 1, "verb": "approach"}), ("option", "talk to npc")])
    loop = Loop(env, StubModel([]), Memory(tmp_path / "memory"), "grid")
    loop.world.self_spot, loop.world._self_from_motion = [.5, .5], True
    loop.step(); loop.step()
    place, ident = loop.place, loop.world.entity_ids[1]
    state = json.loads(json.dumps(loop.state()))
    fresh = Loop(GridEnv(), StubModel([]), Memory(tmp_path / "memory"), "grid"); fresh.restore(state)
    assert fresh.fails == loop.fails and set(fresh.fails) == {f"{place}|approach|thing:{ident}", f"{place}|interact|thing:{ident}"}


def test_route_stage_failure_is_no_evidence_about_the_exit(tmp_path, monkeypatch):
    from agent import loop as module
    env = GridEnv(); env.walls.add((8, 7))  # the fake exit is drawn on a wall one tile north
    monkeypatch.setattr(module, "perceive", lambda *_: ({"screen": "world", "text": [], "entities": [
        {"name": "player", "kind": "player", "at": [.5, .5]}, {"name": "exit: doorway", "kind": "exit", "at": [.5, .444]}]}, reply({})))
    monkeypatch.setattr(module, "act", lambda *a, **k: SimpleNamespace(place="", ops=[], action={"thing": 1, "verb": "approach"}, expect="place_changes", why="t", text=None))
    fail_affordance(monkeypatch, env, reason="route blocked", failure={"stage": "route", "reason": "route blocked", "direction": "UP"})
    loop = Loop(env, StubModel([]), Memory(tmp_path / "memory"), "grid")
    loop.world.self_spot, loop.world._self_from_motion = [.5, .5], True
    loop.step()
    exit_id = next(r["id"] for r in loop.mem.rows("entities") if r["name"] == "exit: doorway")
    assert not [r for r in loop.mem.rows("relations") if r["s"] == exit_id and r["p"] == "does_nothing"]
    assert "not working" not in loop.world.view("", loop.place)
    assert loop.fails == {f"{loop.place}|approach|thing:{exit_id}": {"n": 1, "why": "route blocked", "step": 1, "act": "approach exit: doorway"}}
    fail_affordance(monkeypatch, env, status="blocked", reason="exit not crossed", failure={"stage": "crossing", "reason": "exit not crossed"})
    for _ in range(3): loop.step()  # a reached crossing that does nothing is evidence
    dn = [r for r in loop.mem.rows("relations") if r["s"] == exit_id and r["p"] == "does_nothing" and r["o"] == ""]
    assert len(dn) == 1 and dn[0]["state"] == "refuted" and dn[0]["con"] == 3
    loop.mem.upsert("relations", {"id": "sign-dn", "s": "sign", "p": "does_nothing", "o": "", "src": "observed", "sup": 0, "con": 3, "ev": [1, 2, 3], "state": "refuted", "verb": "approach", "step": 3})
    loop.fails["unrelated|approach|x"] = {"n": 2, "why": "wall", "step": 2, "act": "approach x"}
    def use(ctx, aff, per, **kwargs):  # the crossing works this time: the screen changes
        before = env.screenshot(); env.text_box = True; after = env.screenshot()
        return SimpleNamespace(status="done", reason="", inputs=1, calls=0, before=before, after=after, scroll=(0, 0), blank_seen=False, moves=[], failure=None)
    monkeypatch.setattr(module, "run_affordance", use)
    row = loop.step()
    assert row["outcome"]["effects"] != ["no_change"]
    assert next(r for r in loop.mem.rows("relations") if r["id"] == dn[0]["id"])["state"] == "live"
    assert next(r for r in loop.mem.rows("relations") if r["id"] == "sign-dn")["state"] == "refuted"  # another thing's failure stays
    assert f"{loop.place}|approach|thing:{exit_id}" not in loop.fails and loop.fails["unrelated|approach|x"]["n"] == 2  # only this exit's counter clears


class PlanRecorder(StubModel):
    """StubModel that keeps the planner's prompts, so a test can read what the planner was told."""
    def __init__(self, replies=()): super().__init__(list(replies)); self.plans = []
    def ask(self, *args, **kwargs):
        if kwargs.get("role") == "plan": self.plans.append(args[1])
        return super().ask(*args, **kwargs)


def crate_scene(monkeypatch, tmp_path, goals=("sign",)):
    """A sign three tiles north with a crate one tile north of the player, on the straight way to it. The fake walk stops at the crate."""
    from agent import loop as module
    from agent.space import DIRS
    env = GridEnv(); crate = (0, -1); seen = {"routes": [], "flags": [], "options": [], "rows": [], "acts": 0}
    monkeypatch.setattr(module, "perceive", lambda *_: ({"screen": "world", "text": [], "entities": [
        {"name": "player", "kind": "player", "at": [.5, .5]}, {"name": "sign", "kind": "object", "at": [.5, 1 / 6]}, {"name": "crate", "kind": "object", "at": [.5, .5 - 1 / 9]}]}, reply({})))
    def act(model, per, focus, options, inputs, controls):
        seen["flags"].append(focus); seen["options"].append([o["label"] for o in options]); seen["rows"].append(options)
        goal = goals[min(seen["acts"], len(goals) - 1)]; seen["acts"] += 1
        return SimpleNamespace(place="", ops=[], action={"go_to": goal}, expect="place_changes", why="t", text=None)
    def run(ctx, aff, per, *, route=None, **kwargs):
        frame = ctx.env.screenshot(); seen["routes"].append(route); at = (0, 0)
        for direction in route["dirs"]:
            step = (at[0] + DIRS[direction][0], at[1] + DIRS[direction][1])
            if step == crate:
                return SimpleNamespace(status="blocked", reason="route blocked", inputs=1, calls=0, before=frame, after=frame, scroll=(0, 0), blank_seen=False, moves=[],
                                       failure={"stage": "route", "reason": "route blocked", "direction": direction})
            at = step
        return SimpleNamespace(status="done", reason="", inputs=len(route["dirs"]), calls=0, before=frame, after=frame, scroll=(0, 0), blank_seen=False, moves=[], failure=None)
    monkeypatch.setattr(module, "act", act); monkeypatch.setattr(module, "run_affordance", run)
    model = PlanRecorder()
    loop = Loop(env, model, Memory(tmp_path / "memory"), "grid")
    loop.world.self_spot, loop.world._self_from_motion = [.5, .5], True
    loop.notebook.data["quests"] = [{"key": "g", "text": "reach the sign", "status": "open", "approach": "Use the clear route."}]
    return loop, model, seen


def enclose(loop):
    """Known walls around the player on every side but the crate's: the only way to the sign is through the crate."""
    place = loop.place
    for x, y in [(-1, 0), (1, 0), (0, 1), (-1, 1), (1, 1), (-1, -1), (1, -1)]:
        loop.mem.upsert("tiles", {"id": f"{place}@{x},{y}", "place": place, "x": x, "y": y, "floor": False, "blocked": [], "pushed": [], "exit": None, "step": 1})


def test_route_failure_reaches_planner_prompt_with_intent_stage_blocker_and_no_crossing(tmp_path, monkeypatch):
    loop, model, _ = crate_scene(monkeypatch, tmp_path)
    loop.step(); cause = loop.cause
    packet = loop._evidence()
    assert "tried by place:" in packet and '"sign":{"failed":1}' in packet
    assert "failed attempts:" in packet and "route blocked" in packet
    assert cause.endswith("no crossing attempted") and not model.plans


def test_crossing_failure_says_crossing_attempted(tmp_path, monkeypatch):
    from agent.loop import failure_cause
    assert failure_cause("p|approach|exit:door", "approach exit: door", "crossing", "exit not crossed") == "approach exit: door [p|approach|exit:door] failed at crossing; crossing attempted"
    assert failure_cause("p|approach|thing:e7", "approach sign", "route", "route blocked", (0, -1), "e7") == "approach sign [p|approach|thing:e7] failed at route; blocked at (0,-1) by thing:e7; no crossing attempted"


def test_chooser_rows_show_failure_evidence(tmp_path, monkeypatch):
    from agent import loop as module
    loop, model, seen = crate_scene(monkeypatch, tmp_path)
    fail_affordance(monkeypatch, loop.env, reason="route blocked",
                    failure={"stage": "route", "reason": "route blocked", "direction": "UP"})
    recorded = module.act
    def choose(*args):
        action = recorded(*args)
        row = next(o for o in args[3] if o["label"].startswith("check sign,"))
        action.action, action.option = dict(row["action"]), row["n"]
        return action
    monkeypatch.setattr(module, "act", choose)
    loop.step(); cause = loop.cause
    loop.step()
    assert any(o["state"] == "failed" and "route blocked" in "; ".join(o["notes"]) for o in seen["rows"][1])
    assert not any(line.startswith("cause:") for line in seen["flags"][1].splitlines()) and "no crossing attempted" in cause
    assert "failed attempts:" in loop._evidence() and not model.plans


def test_repeated_identical_failure_plans_once_and_new_evidence_plans_once_more(tmp_path, monkeypatch):
    loop, model, seen = crate_scene(monkeypatch, tmp_path, goals=("sign", "sign", "sign", "sign", "crate", "crate"))
    loop.step(); assert len(model.plans) == 0; enclose(loop)  # no detour from here: the same failure repeats
    loop.step(); assert len(model.plans) == 0  # a failure alone never replans
    for _ in range(2): loop.step()  # the same failure again: no planner call
    assert len(model.plans) == 0
    assert "failed attempts:" in loop._evidence() and loop.fails


def test_replan_trigger_cause_round_trips(tmp_path, monkeypatch):
    loop, model, seen = crate_scene(monkeypatch, tmp_path)
    loop.step(); enclose(loop)
    state = json.loads(json.dumps(loop.state()))
    assert state["planner"]["cause"] == loop.cause
    resumed, resumed_model, _ = crate_scene(monkeypatch, tmp_path)
    resumed.restore(state)
    assert resumed.cause == loop.cause
    resumed.step(); assert len(resumed_model.plans) == 0
    resumed.step(); assert len(resumed_model.plans) == 0


def test_repeated_destination_goes_around_a_known_blocker_when_a_detour_exists(tmp_path, monkeypatch):
    loop, model, seen = crate_scene(monkeypatch, tmp_path)
    loop.step(); assert next(iter(loop.fails.values()))["avoid"] == [0, -1]
    row = loop.step()
    assert row["skill"]["status"] == "done" and row["skill"]["inputs"] == len(seen["routes"][1]["dirs"])  # the revised route is walked, none into the crate
    assert seen["routes"][1]["dirs"] != seen["routes"][0]["dirs"] and seen["routes"][1]["dirs"][0] != "UP"


def test_repeated_destination_without_a_detour_is_a_zero_input_failure_and_options_are_untouched(tmp_path, monkeypatch):
    loop, model, seen = crate_scene(monkeypatch, tmp_path)
    loop.step(); enclose(loop)
    row = loop.step(); again = loop.step()
    assert len(seen["routes"]) == 1  # no skill ran after the first: nothing was pressed
    assert {k: row["skill"][k] for k in ("status", "reason", "inputs", "calls")} == {"status": "blocked", "reason": "no route avoids blocker", "inputs": 0, "calls": 0}
    assert again["skill"]["inputs"] == 0 and loop.world.pos == (0, 0)
    assert seen["options"][1] == seen["options"][2] and any(label.startswith("check sign,") for label in seen["options"][1])
    assert loop.notebook.data["quests"][0]["key"] == "g" and loop.notebook.data["quests"][0]["status"] == "open"
    assert next(iter(loop.fails.values()))["n"] == 3 and next(iter(loop.fails.values()))["avoid"] == [0, -1]


def test_evidence_omits_removed_planner_reconciliation_fields(tmp_path):
    loop = Loop(GridEnv(), StubModel([]), Memory(tmp_path / "memory"), "grid")
    loop.new_places = [(1, "p")]
    loop.recent = [{"step": 1, "place": "p", "choice": {"verb": "approach", "target": "door"}, "verdict": "blocked"}]
    loop.fails = {"p|approach|door": {"n": 2, "step": 1, "why": "blocked", "act": "approach door"}}
    loop.stuck.progress_n = 20

    packet = loop._evidence()

    assert len(packet) <= 600
    assert "new places: p" in packet and "tried by place:" in packet
    assert "steps since last progress: 20 (stall at 20)" in packet
    assert all(text not in packet for text in ("reconcile flags", "closed checks", "new text since last plan", "last stall changed nothing"))


def test_evidence_counts_all_post_plan_attempts_and_only_new_failures(tmp_path):
    loop = Loop(GridEnv(), StubModel([]), Memory(tmp_path / "memory"), "grid")
    loop.last_plan = 5
    loop.fail_baseline = {"p|approach|door": 4}
    loop.fails = {
        "p|approach|door": {"n": 5, "step": 12, "why": "blocked", "act": "approach door"},
    }
    loop.planner_evidence = [
        {"step": step, "place": "p", "target": target, "verdict": verdict}
        for step, target, verdict in ((6, "door", "blocked"), (7, "gate", "miss"), (8, "door", "blocked"),
                                      (9, "gate", "miss"), (10, "door", "blocked"), (11, "gate", "miss"),
                                      (12, "door", "blocked"), (13, "gate", "miss"))
    ]
    loop.moves = [[6, "p", "hall", "north door", None]]

    packet = loop._evidence()

    assert '"door":{"blocked":4}' in packet and '"gate":{"miss":4}' in packet
    assert "approach door -> blocked x1" in packet
    assert '"north door":1' in packet


class Keys(GridEnv):
    def __init__(self): super().__init__(); self.keys = []
    def press(self, key, frames=8, on_frame=None): self.keys.append(key); super().press(key, frames, on_frame)

class Roles(StubModel):
    def __init__(self, replies): super().__init__(replies); self.roles = []
    def ask(self, *args, **kwargs):
        if kwargs.get("role") != "plan": self.roles.append(kwargs.get("role"))
        return super().ask(*args, **kwargs)

def pick_reply(i): return reply({"i": i, "why": "test"})
def free_reply(action): return reply({"place": "", "notebook": [], "action": action, "text": None, "expect": "screen_changes", "why": "test"})
def dialogue_scene(): return {"screen": "dialogue", "text": ["Watch the television?"], "entities": [
    {"name": "television", "kind": "object", "at": [.2, .3]}, {"name": "player", "kind": "player", "at": [.5, .5]}, {"name": "dialogue box", "kind": "dialogue_box", "at": [.5, .85]}]}
def menu_scene(): return {"screen": "menu", "text": ["FIGHT BAG"], "entities": [
    {"name": "FIGHT", "kind": "menu_item", "at": [.3, .4], "selectable": True}, {"name": "BAG", "kind": "menu_item", "at": [.3, .6], "selectable": True}]}
def world_scene(): return {"screen": "world", "text": [], "entities": [{"name": "player", "kind": "player", "at": [.5, .5]}]}

def options_seen(monkeypatch):
    from agent import loop as module
    seen, real = [], module.act
    def record(*args, **kwargs): seen.append(args[3]); return real(*args, **kwargs)
    monkeypatch.setattr(module, "act", record)
    return seen


def test_stale_select_of_unflagged_furniture_fails_without_menu_read_or_input(tmp_path, monkeypatch):
    from agent import loop as module
    seen = []
    def stale_action(*args):
        seen.append(args[3])
        return chosen({"thing": 0, "verb": "select"})
    monkeypatch.setattr(module, "act", stale_action)
    # spare replies: a menu read and an after-screen, answered only if the stale select runs (so baseline fails on its assertions, not a crash)
    spare = [reply({"items": ["television"], "cursor": 0, "layout": "vertical"}), reply(world_scene())]
    env, model = Keys(), Roles([reply(dialogue_scene()), *spare])
    loop = Loop(env, model, Memory(tmp_path / "memory"), "grid")
    row = loop.step()
    assert env.keys == [] and "skill" not in model.roles  # no menu read, no press
    assert {k: row["skill"][k] for k in ("status", "reason", "inputs", "calls")} == {"status": "failed", "reason": "not a selectable choice on this screen", "inputs": 0, "calls": 0}
    assert row["verdict"] != "hit"
    intent = row["choice"]["intent"]
    assert loop.fails[intent]["n"] == 1 and loop.fails[intent]["act"] == "select television"
    assert loop.cause == f"select television [{intent}] failed at interaction: not a selectable choice on this screen"
    assert not any(o["tool"] == "select" for o in seen[0])  # the chooser was never offered a select on unflagged furniture
    assert [o["label"] for o in seen[0] if o["tool"] == "advance"] == ["advance"]
    assert [r for r in loop.mem.rows("relations") if r.get("s") == row["choice"]["id"]] == []  # nothing was pressed: no observed evidence about the television


def test_advance_option_on_dialogue_progresses_without_auto_advance(tmp_path, monkeypatch):
    seen = options_seen(monkeypatch)
    env, model = Keys(), Roles([reply(dialogue_scene()), pick_reply(1), reply(world_scene())])
    loop = Loop(env, model, Memory(tmp_path / "memory"), "grid")
    row = loop.step()
    assert [(o["tool"], o["label"]) for o in seen[0]][:2] == [("advance", "advance"), ("cancel", "cancel")]  # option 1 is advance
    assert row["option"]["tool"] == "advance" and row["choice"]["verb"] == "advance"
    assert env.keys == ["A"] and row["skill"]["status"] == "done" and "skill" not in model.roles
    assert loop.prev["screen"] == "world"  # the fake dialogue progressed


def test_flagged_menu_choice_picked_by_number_reads_menu_once_and_selects(tmp_path, monkeypatch):
    seen = options_seen(monkeypatch)
    menu_read = reply({"items": ["FIGHT", "BAG"], "cursor": 0, "layout": "vertical"})
    env, model = Keys(), Roles([reply(menu_scene()), pick_reply(2), menu_read, reply(world_scene())])
    loop = Loop(env, model, Memory(tmp_path / "memory"), "grid")
    row = loop.step()
    assert [o["label"] for o in seen[0]][:2] == ["select FIGHT", "select BAG"]  # option 2 is BAG
    assert row["option"]["tool"] == "select" and row["choice"]["verb"] == "select"
    assert model.roles.count("skill") == 1 and env.keys == ["DOWN", "A"]  # one menu read, then move one step down and confirm
    assert row["skill"]["status"] == "done" and row["skill"]["calls"] == 1


def chosen(action, expect="screen_changes", why="test"):
    return SimpleNamespace(place="", ops=[], action=action, expect=expect, why=why, text=None)


def scripted_world(monkeypatch, *, per=None, acts=()):
    """Perception and the chooser are scripted; the settle timing is what these tests exercise."""
    from agent import loop as module
    per = per or {"screen": "world", "text": [], "entities": [{"name": "player", "kind": "player", "at": [.5, .5]}]}
    seen, steps = {"perceive": 0, "act": 0}, iter(acts)
    def perceive(*_):
        seen["perceive"] += 1; return per, reply(per)
    def act(*_args, **_kwargs):
        seen["act"] += 1; return next(steps)
    monkeypatch.setattr(module, "perceive", perceive); monkeypatch.setattr(module, "act", act)
    return seen


def trace_rows(root):
    return [json.loads(line) for line in (root / "trace.jsonl").read_text(encoding="utf8").splitlines()]

def test_unsettled_after_frame_writes_one_timed_out_row_with_normal_tail(tmp_path, monkeypatch):
    short_settle(monkeypatch)
    env = Busy(plan=[10 ** 9]); seen = scripted_world(monkeypatch, acts=[chosen({"buttons": ["RIGHT"]})])
    loop = Loop(env, StubModel([]), Memory(tmp_path / "memory"), "grid")

    loop.world.self_spot, loop.world._self_from_motion = [.5, .5], True  # no probe press before the action

    row = loop.step()

    assert row["observation_status"] == "timed_out" and row["execution"] == "done" and row["choice"]["verb"] == "buttons"
    assert row["outcome"] is not None and row["action_step"] is None and loop.step_no == 1
    assert seen["act"] == 1 and env.keys == ["RIGHT"] and loop.pending is None
    assert [r["observation_status"] for r in trace_rows(tmp_path)] == ["timed_out"]

def test_zero_input_failed_select_reports_failed_execution(tmp_path, monkeypatch):
    from agent import loop as module
    short_settle(monkeypatch)
    per = {"screen": "world", "text": [], "entities": [{"name": "player", "kind": "player", "at": [.5, .5]}, {"name": "gate", "kind": "object", "at": [.6, .5]}]}
    seen = scripted_world(monkeypatch, per=per, acts=[chosen({"thing": 1, "verb": "select"}, expect="no_change")])
    def failed(ctx, aff, per, **kwargs):
        frame = ctx.env.screenshot()
        return SimpleNamespace(status="failed", reason="menu or item not identified", inputs=0, calls=1, before=frame, after=frame, scroll=(0, 0), blank_seen=False, moves=[], failure=None)
    monkeypatch.setattr(module, "run_affordance", failed)
    loop = Loop(Busy(), StubModel([]), Memory(tmp_path / "memory"), "grid")

    row = loop.step()

    assert row["observation_status"] == "settled" and row["verdict"] == "failed" and row["execution"] == "failed" and row["skill"]["status"] == "failed"
    assert row["pred"] is None and row["used"] == [] and sum(v["n"] for v in loop.fails.values()) == 1 and seen["act"] == 1

class Counting(StubModel):
    """StubModel with Ollama's call counter: seq is the 1-based id of the latest ask, cache hits included."""
    def __init__(self, replies=()): super().__init__(list(replies)); self.seq = 0
    def ask(self, *args, **kwargs): self.seq += 1; return super().ask(*args, **kwargs)

class Asks:
    """Model double for scripted perception and act: every ask is counted and answered with an empty plan."""
    def __init__(self): self.seq = self.calls = 0
    def ask(self, *args, **kwargs): self.seq += 1; return SimpleNamespace(data={"goals": [], "focus": "", "why": "stub"}, text="")


def test_checkpoint_modes_round_trip_and_a_legacy_state_is_marked_incomplete(tmp_path):
    env = GridEnv(); mem = Memory(tmp_path / "memory")
    decided = Loop(env, StubModel([]), mem, "grid", decide=True)
    state = json.loads(json.dumps(decided.state()))
    assert state["modes"] == {"decide": True, "act_text": False}
    resumed = Loop(env, StubModel([]), mem, "grid", decide=True); resumed.restore(state)
    assert resumed.provenance_incomplete is False and resumed.state()["modes"] == state["modes"]

    plain = json.loads(json.dumps(Loop(env, StubModel([]), mem, "grid").state()))
    legacy = {k: v for k, v in plain.items() if k != "modes"}  # a checkpoint written before modes were recorded
    old = Loop(GridEnv(), StubModel([reply(perception()), act_reply(None)]), Memory(tmp_path / "old" / "memory"), "grid"); old.restore(legacy)
    assert old.provenance_incomplete is True and "modes" not in old.state()
    row = old.step()
    assert row["provenance_incomplete"] is True and row["invocation"] is None

    fresh = Loop(GridEnv(), StubModel([reply(perception()), act_reply(None)]), Memory(tmp_path / "fresh" / "memory"), "grid"); fresh.restore(plain)
    assert fresh.provenance_incomplete is False and "provenance_incomplete" not in fresh.step()

def test_restore_rejects_constructor_modes_that_conflict_with_the_checkpoint_before_any_press(tmp_path):
    class Recording(GridEnv):
        def __init__(self): super().__init__(); self.presses = []
        def press(self, key, *args, **kwargs): self.presses.append(key)
    saved = json.loads(json.dumps(Loop(GridEnv(), StubModel([]), Memory(tmp_path / "saved" / "memory"), "grid", decide=True).state()))
    env = Recording(); loop = Loop(env, StubModel([]), Memory(tmp_path / "memory"), "grid", decide=False)
    before = json.dumps(loop.state(), sort_keys=True, default=str)

    with pytest.raises(ValueError, match="conflicts with checkpoint modes"): loop.restore(saved)

    assert env.presses == [] and json.dumps(loop.state(), sort_keys=True, default=str) == before and loop.provenance_incomplete is False

def test_trace_rows_list_the_model_calls_of_their_own_step(tmp_path):
    model = Counting([reply(perception()), act_reply(None), reply(perception()), act_reply(None)])
    loop = Loop(GridEnv(), model, Memory(tmp_path / "memory"), "grid")

    first = loop.step(); first_end = model.seq
    second = loop.step()

    assert first_end >= 2 and first["call_ids"] == list(range(1, first_end + 1))
    assert second["call_ids"] == list(range(first_end + 1, model.seq + 1))
    assert first["invocation"] is None and second["invocation"] is None

def test_no_model_calls_list_none(tmp_path, monkeypatch):
    short_settle(monkeypatch)
    no_model = Loop(GridEnv(), StubModel([reply(perception()), act_reply(None)]), Memory(tmp_path / "plain" / "memory"), "grid")
    assert no_model.step()["call_ids"] == []

def test_trace_skill_carries_failure_and_settled(tmp_path, monkeypatch):
    # m1_hf_checkpoint_1: the trace skill dict dropped failure/settled, so HF1/HF4 stage data never reached the trace
    loop, model, seen = crate_scene(monkeypatch, tmp_path)
    row = loop.step()
    assert row["skill"]["failure"]["stage"] == "route" and "settled" in row["skill"]


# --- QR.1: one persisted event queue, honest progress ---
def _loop(tmp_path, model=None): return Loop(GridEnv(), model or StubModel([]), Memory(tmp_path / "memory"), "grid")


def test_no_open_quest_fires_once_until_a_new_event(tmp_path):
    loop, plans = _loop(tmp_path), []
    for step in range(1, 13):
        loop.step_no = step - 1
        if step == 8: loop._event("T1", "new game text: hi", step)  # new game text re-arms T5
        if (trigger := loop._plan_trigger({"screen": "world"})): plans.append((step, trigger)); loop.events, loop.last_plan = [], step
    assert plans == [(8, "new game text: hi; no open quest")]  # nothing heard before step 8: no plan could cite anything
    state = json.loads(json.dumps(loop.state())); resumed = _loop(tmp_path / "r"); resumed.restore(state)
    assert resumed.t5_armed is False


def test_events_wait_out_the_floor_and_are_never_lost(tmp_path):
    loop = _loop(tmp_path)
    loop.notebook.data["quests"] = [{"key": "q", "text": "explore", "status": "open"}]
    loop.last_plan = loop.step_no = 10
    for kind, text in (("T2", "arrived somewhere new: A"), ("T1", "new game text: hi"), ("T3", "quest x closed")): loop._event(kind, text, 10)
    assert loop._plan_trigger({"screen": "world"}) is None and len(loop.events) == 3  # inside the floor: nothing consumed, nothing dropped
    loop.step_no = 12
    assert loop._plan_trigger({"screen": "world"}) == "quest x closed; new game text: hi; arrived somewhere new: A"  # T3, T1, T2
    loop._event("T5", "no open quest", 12); loop._event("T5", "no open quest", 12)
    assert [e["kind"] for e in loop.events].count("T5") == 1


def test_revisit_does_not_drop_pending_new_place_event(tmp_path):
    loop = _loop(tmp_path)
    for pid, visits in (("a", 3), ("b", 1), ("c", 3)): loop.mem.upsert("places", {"id": pid, "name": pid.upper(), "screen": "world", "visits": visits})
    loop._moved("a", "b", "door", 5)
    loop._moved("b", "c", "door", 6)  # a revisit afterwards
    assert [e["text"] for e in loop.events] == ["arrived somewhere new: B"]


def test_events_round_trip_through_checkpoint(tmp_path):
    loop = _loop(tmp_path)
    loop._event("T3", "quest q closed", 4); loop._event("T4", "stuck: no_progress", 5)
    state = json.loads(json.dumps(loop.state()))
    resumed = _loop(tmp_path / "r"); resumed.restore(state)
    assert resumed.events == loop.events


def test_text_does_not_reset_stall_counter(tmp_path):
    loop = _loop(tmp_path)
    loop.stuck.progress_n = 7
    loop._learn(["A sign says KEEP OUT."], [], 3, "p", "world")
    loop._learn(["MOM: Hello there."], [], 4, "p", "dialogue"); loop._learn([], [], 5, "p", "world")
    assert loop.stuck.progress_n == 7 and [e["kind"] for e in loop.events] == ["T1", "T1"]


def test_menu_text_is_not_a_game_text_trigger(tmp_path):
    loop = _loop(tmp_path)
    loop._learn(["FIGHT BAG POKEMON"], [], 3, "p", "menu")
    assert loop.events == [] and loop.conversation["lines"] == [] and any("FIGHT" in r["text"] for r in loop.mem.rows("knowledge"))


def test_title_text_is_not_a_game_text_trigger(tmp_path):
    loop = _loop(tmp_path)
    assert loop._learn(["GAME FREAK Red Version"], [], 3, "p", "title") is True
    assert loop.events == [] and loop.conversation["lines"] == [] and loop.text_for_trigger == ""
    assert any("GAME FREAK" in r["text"] for r in loop.mem.rows("knowledge"))


def test_dialogue_ends_when_its_screen_kind_changes(tmp_path):
    loop = _loop(tmp_path)
    loop._learn(["OAK: Hello there!"], [], 5, "p", "dialogue"); loop._learn(["Welcome to the world of POKEMON!"], [], 6, "p", "dialogue")
    assert loop.events == []  # still one conversation
    loop._learn(["NEW NAME"], [], 7, "p", "menu")  # the intro moves on to a naming menu, long before any world screen
    assert [e["kind"] for e in loop.events] == ["T1"] and "Welcome to the world" in loop.text_for_trigger
    assert ["OAK"] == [t["speaker"] for t in loop.notebook.data["text"]] and "Welcome to the world" in loop.notebook.data["text"][0]["text"]  # a citable t-line


def _idle_loop(tmp_path, monkeypatch, action):
    from agent import loop as module
    per = {"screen": "world", "text": [], "entities": [{"kind": "player", "name": "player", "at": [.5, .5]}, {"kind": "object", "name": "Lamp", "at": [.7, .5]}]}
    loop = Loop(GridEnv(), StubModel([reply(per), reply(per)]), Memory(tmp_path / "memory"), "grid")
    loop.world.self_spot, loop.world._self_from_motion = [.5, .5], True
    picks = []
    def act(model, per, focus, options, *args, **kwargs):
        o = next(o for o in options if action(o)); picks.append(o)
        return SimpleNamespace(action=dict(o["action"]) if action(o) is True else action(o), option=o["n"], why="test", text=None, place="", ops=[], expect="screen_changes")
    def run(ctx, aff, *_args, **_kwargs):
        frame = ctx.env.screenshot()
        return SimpleNamespace(status="done", reason="", inputs=0, calls=0, before=frame, after=frame, scroll=(0, 0), blank_seen=False, moves=[])
    monkeypatch.setattr(module, "act", act); monkeypatch.setattr(module, "run_affordance", run)
    return loop, picks


def test_a_picked_option_that_sends_no_input_lands_in_fails(tmp_path, monkeypatch):
    loop, picks = _idle_loop(tmp_path, monkeypatch, lambda o: "thing" in (o.get("action") or {}) or None)
    row = loop.step()
    assert row["skill"]["inputs"] == 0 and row["verdict"] == "failed"
    assert loop.fails[row["choice"]["intent"]]["why"] == "did nothing"


def test_an_option_whose_action_never_resolves_lands_in_fails_under_its_own_key(tmp_path, monkeypatch):
    loop, picks = _idle_loop(tmp_path, monkeypatch, lambda o: {"go_to": "thing:e_gone"})  # S144: a remembered row whose go_to resolves to nothing
    row = loop.step()
    assert row["choice"] is None and row["skill"]["inputs"] == 0 and row["verdict"] == "failed"
    assert loop.fails[picks[0]["intent_key"]]["why"] == "did nothing"  # the menu notes it, so the next prompt differs
