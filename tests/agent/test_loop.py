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
    return reply({"place": "", "notebook": [], "action": None if i is None else {"thing": i, "verb": verb}, "text": None, "expect": expect, "why": "test"})
def fake_act(i=0, expect="screen_changes", verb="approach"):
    return lambda *a, **k: SimpleNamespace(place="", ops=[], action=None if i is None else {"thing": i, "verb": verb}, expect=expect, why="test", text=None)
def perception(*, two=False, press=False):
    affs = ([{"verb":"press","target":"A","at":[500,500]}] if press else
        [{"verb":"approach","target":"player","at":[500,500]}] * (2 if two else 1))
    return {"screen":"room","text":[],"entities":[{"kind":"player","name":"Hand","at":[500,500]}],"affordances":affs}


def test_numbered_thing_uses_exact_entity_id(tmp_path, monkeypatch):
    from agent import loop as module
    per = {"screen": "world", "text": [], "entities": [
        {"kind": "player", "name": "player", "at": [500, 500]},
        {"kind": "object", "name": "Lamp", "at": [700, 500]}]}
    act = reply({"place": "", "notebook": [], "action": {"thing": 1, "verb": "approach"},
                 "text": None, "expect": "screen_changes", "why": "lamp"})
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

    assert seen["aff"] == {"verb": "approach", "target": "Lamp", "at": [.7, .5]}
    assert row["choice"]["id"] == seen["ident"] == loop.world.entity_ids[1]


def test_bare_advance_presses_a(tmp_path):
    class Recording(GridEnv):
        def __init__(self): super().__init__(); self.keys = []
        def press(self, key, frames=8, on_frame=None): self.keys.append(key); super().press(key, frames, on_frame)
    env = Recording()
    data = {"screen": "dialogue", "text": ["Continue?"], "entities": []}
    advance = reply({"place": "", "notebook": [], "action": {"verb": "advance"},
                     "text": None, "expect": "screen_changes", "why": "continue"})
    Loop(env, StubModel([reply(data), advance, reply(data)]), Memory(tmp_path / "memory"), "grid").step()
    assert env.keys == ["A"]


@pytest.mark.parametrize("known", [False, True])
def test_verified_edge_only_counts_for_previously_unknown_place(tmp_path, monkeypatch, known):
    from agent import loop as loop_module

    monkeypatch.setattr(loop_module, "cut", lambda *args, **kwargs: True)
    monkeypatch.setattr(loop_module, "effects", lambda *args, **kwargs: {"effects": ["place_changes"], "basis": {"place_changes": "pixel"}, "to": "destination"})
    monkeypatch.setattr(loop_module, "verdict", lambda *args, **kwargs: "hit")
    replies = [reply(perception()), act_reply(0, "place_changes")]
    model = StubModel(replies); mem = Memory(tmp_path / "memory")
    mem.upsert("places", {"id": "source", "name": "Source", "edges": []})
    if known: mem.upsert("places", {"id": "destination", "name": "Destination", "edges": []})
    loop = Loop(GridEnv(), model, mem, "grid")
    matches = 0
    def match_place(*args, **kwargs):
        nonlocal matches
        matches += 1
        if matches % 2 == 0 and not known: mem.upsert("places", {"id": "destination", "name": "Destination", "edges": []})
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
    assert model.calls == 22 and rows[1]["perception_reused"] and rows[1]["calls"] == 1
    assert rows[0]["calls"] == 2 and not rows[0]["perception_reused"]
    assert sum(row["calls"] == 2 for row in rows) == 2  # initial perception plus one streak-triggered re-perception

def test_act_receives_controls_as_text(tmp_path, monkeypatch):
    from agent import loop as loop_module
    data = perception(two=True)
    class Captures(StubModel):
        def ask(self, *args, **kwargs):
            if kwargs.get("role") == "act": self.prompt = args[1]
            return super().ask(*args, **kwargs)
    monkeypatch.setattr(loop_module, "_load", lambda _game: {"controls": ["Move with arrows", "Confirm with A"]})
    model = Captures([reply(data), act_reply(0)])
    Loop(GridEnv(), model, Memory(tmp_path / "memory"), "grid").step()
    assert "Controls:\nMove with arrows\nConfirm with A" in model.prompt

def test_restore_and_parse_failure(tmp_path):
    model=StubModel([reply(perception(two=True)),act_reply(0)])
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

def test_cut_before_frame_suppresses_marker_and_same_raw_after_frame_reuses(tmp_path, monkeypatch):
    from PIL import Image
    from agent import loop as module
    env = GridEnv(); frame = Image.new("RGB", (160, 144), "black")
    frame.paste((90, 90, 90), (0, 0, 160, 72))  # not uniform: a one-colour frame counts as a blank transition (observer._blank)
    env.screenshot = lambda: frame.copy()
    loop = Loop(env, StubModel([]), Memory(tmp_path/"memory"), "grid")
    loop.world.self_spot, loop.world._self_from_motion = [.5, .5], True
    loop.prev, loop.prev_frame = {"screen": "world"}, Image.new("RGB", (160, 144), "white")
    per = {"screen":"world", "text":[], "entities":[{"name":"player", "kind":"player", "at":[.5,.5]}], "affordances":[]}
    calls, contexts, acts = [], [], []
    def perceive(_model, image, ctx):
        calls.append(image); contexts.append(ctx)
        return per, reply(per)
    def act(_model, image, _per, _notebook, _view, flags, *_args, **_kwargs):
        acts.append((image, flags))
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
    assert module.CAPTION in contexts[1] and "the sprite inside the red box is you" in acts[1][1]
    assert acts[1][0].getpixel((71, 62)) == (255, 0, 0)

@pytest.mark.parametrize("views", [False, True])
def test_stationary_two_fresh_frames_confirm_drift(tmp_path, monkeypatch, views):
    from agent import loop as module
    env = GridEnv(); env.phase = 0; original = env.screenshot
    def screenshot():
        frame = original(); frame.putpixel((0, 0), (env.phase, 0, 0)); return frame
    env.screenshot = screenshot; offset = [0.6]
    def perceive(*_):
        return {"screen":"world","text":[],"affordances":[{"verb":"approach","target":"lamp","at":[offset[0],.5]}],"entities":[{"name":"player","kind":"player","at":[.5,.5]}, {"name":"lamp","kind":"object","at":[offset[0],.5]}]}, reply({"screen":"world","text":[],"entities":[],"affordances":[]})
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
                "affordances": [{"verb": "approach", "target": "Chest", "at": [.6, .5]}],
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
        return {"screen":"world","text":[],"affordances":[{"verb":"approach","target":"exit: passage","at":[.5,.5+dy]}],
                "entities":[{"name":"player","kind":"player","at":[.5,.5]}, {"name":floor,"kind":"object","at":[.7,.5]},
                            {"name":"exit: passage","kind":"exit","at":[.5,.5+dy]}]}, reply({})
    monkeypatch.setattr(module, "perceive", perceive)
    monkeypatch.setattr(module, "act", fake_act(2, "place_changes"))
    loop=Loop(env,StubModel([]),Memory(tmp_path/"memory"),"grid"); ids=[]
    for _ in range(4): ids.append(loop.step()["next_place"])
    assert ids[0] == ids[2] and ids[1] == ids[3] and ids[0] != ids[1]

def test_perceiver_guides_whole_name_entry(tmp_path):
    class CapturesPrompt(StubModel):
        def ask(self, *args, **kwargs): self.prompt=args[1]; return super().ask(*args, **kwargs)
    model=CapturesPrompt([reply(perception())]); loop=Loop(GridEnv(),model,Memory(tmp_path/"memory"),"grid")
    loop._see(loop.env.screenshot(),None)
    assert "visible editable text field with its on-screen keyboard" not in model.prompt
    assert "Previous-place memory is reference only" not in model.prompt
    assert 'List every visible passage (door, stairs, door mat, opening) as an entity named "exit: <what it is>"' in model.prompt
    assert loop.prompt_version == "e5-perceiver-10"

def test_reperception_bypasses_cached_frame_with_player_hint(tmp_path, monkeypatch):
    from agent import loop as loop_module

    prompts = []
    data = perception()
    monkeypatch.setattr(loop_module, "perceive", lambda _model, _frame, prompt: (prompts.append(prompt) or data, reply(data)))
    loop = Loop(GridEnv(), StubModel([]), Memory(tmp_path / "memory"), "grid")
    frame = loop.env.screenshot()

    assert loop._see(frame, None)[2] is False
    assert loop._see(frame, None)[2] is True
    assert loop._see(frame, None, force=True, relook=True)[2] is False
    assert len(prompts) == 2
    assert prompts[-1].endswith("Your last labels may be wrong; look again at who the player is.")

def test_probe_act_uses_post_probe_frame_and_perception(tmp_path, monkeypatch):
    from agent import loop as loop_module

    env = GridEnv(); env.tile_px = 16
    monkeypatch.setattr(loop_module, "perceive", lambda _model, _frame, _prompt: (env.perception(), reply(env.perception())))
    def probe(_ctx):
        env.scene = 1
        return {"presses": 1, "locked": False, "scene_cut": False}
    def act_spy(_model, frame, per, *args, **kwargs):
        assert frame.tobytes() == env.screenshot().tobytes()
        assert per == env.perception()
        return fake_act(None)(*args, **kwargs)
    monkeypatch.setattr(loop_module, "act", act_spy)
    loop = Loop(env, StubModel([]), Memory(tmp_path / "memory"), "grid")
    monkeypatch.setattr(loop.world, "probe", probe)

    row = loop.step()

    assert row["probe"] == {"presses": 1, "locked": False}
    assert not row["perception_reused"]

def test_recent_keeps_no_effect_head_when_trimming(tmp_path, monkeypatch):
    from agent import loop as loop_module

    captured = {}
    def capture(_model, _frame, _per, _notes, _view, flags, *_a, **_k):
        captured["flags"] = flags
        return fake_act(None)()
    monkeypatch.setattr(loop_module, "act", capture)
    loop = Loop(GridEnv(), StubModel([reply(perception())]), Memory(tmp_path / "memory"), "grid")
    loop.stuck.n = 3
    loop.recent = [{"step": i, "choice": {"verb": "approach", "target": f"very long target {i}" * 4},
                    "skill": {"status": "blocked", "reason": "no progress " * 4},
                    "outcome": {"effects": ["no_change"]}} for i in range(10)]

    loop.step()

    recent = next(flag for flag in captured["flags"] if flag.startswith("recent:"))
    assert recent.startswith("recent: no effect x3;")
    assert len(recent) <= 200

def test_dialogue_opening_over_world_keeps_place(tmp_path, monkeypatch):
    from agent import loop as loop_module
    env = GridEnv()
    start = perception(); start["screen"] = "world"
    start["affordances"][0]["verb"] = "interact"
    destination = perception(); destination["screen"] = "dialogue"; destination["text"] = ["Hello!"]
    model = StubModel([reply(start), act_reply(0, verb="interact"), reply(destination)])
    loop = Loop(env, model, Memory(tmp_path/"memory"), "grid")
    loop.world.self_spot, loop.world._self_from_motion = [.5, .5], True
    def interact(ctx, aff, per, text=None, exit=False):
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
    start["affordances"][0]["verb"] = "interact"
    destination = perception(); destination["screen"] = "world"; destination["text"] = ["Hello!"]
    model = StubModel([reply(start), act_reply(0, verb="interact"), reply(destination)])
    loop = Loop(env, model, Memory(tmp_path/"memory"), "grid")
    loop.world.self_spot, loop.world._self_from_motion = [.5, .5], True
    def interact(ctx, aff, per, text=None, exit=False):
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
    model = StubModel([reply(world), act_reply(0), reply(world), reply(dialogue), act_reply(0)])
    loop = Loop(env, model, Memory(tmp_path/"memory"), "grid")
    loop.world.self_spot, loop.world._self_from_motion = [.5, .5], True
    monkeypatch.setattr(loop_module, "cut", lambda *args: True)
    moves = [0]
    def stay(ctx, aff, per, text=None, exit=False):
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
    model = StubModel([reply(world), act_reply(0), reply(dialogue)])
    loop = Loop(env, model, Memory(tmp_path/"memory"), "grid")
    loop.world.self_spot, loop.world._self_from_motion = [.5, .5], True
    def transition(ctx, aff, per, text=None, exit=False):
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
    model = StubModel([reply(world), act_reply(0), reply(dest)])
    loop = Loop(env, model, Memory(tmp_path/"memory"), "grid")
    loop.world.self_spot, loop.world._self_from_motion = [.5, .5], True
    def transition(ctx, aff, per, text=None, exit=False):
        before = env.screenshot(); env.scene = 1; after = env.screenshot()
        return SimpleNamespace(status="done", reason="scene cut", inputs=1, calls=0, moves=moves,
                               before=before, after=after, scroll=(0, 0), blank_seen=False)
    monkeypatch.setattr(loop_module, "run_affordance", transition)
    row = loop.step()
    assert row["scene_cut"] and (row["next_place"] != row["place"]) == (screen == "world")  # only a navigable screen is a new place
    assert ("place_changes" in row["outcome"]["effects"]) == expect
    if expect: assert row["outcome"]["to"] == row["next_place"]

def test_blocked_exit_overrides_pixel_prediction_and_cannot_hit(tmp_path, monkeypatch):
    from agent import loop as loop_module
    env = GridEnv()
    start = perception(); start["screen"] = "world"
    start["affordances"] = [{"verb": "approach", "target": "exit: door mat", "at": [0.3, 0.9]}]
    start["entities"].append({"kind": "exit", "name": "exit: door mat", "at": [0.3, 0.9]})
    model = StubModel([reply(start), act_reply(1), reply(start)])
    loop = Loop(env, model, Memory(tmp_path/"memory"), "grid")
    loop.world.self_spot, loop.world._self_from_motion = [.5, .5], True
    monkeypatch.setattr(loop_module, "predict", lambda *args: {"effect": "screen_changes", "detail": "movement"})
    monkeypatch.setattr(loop_module, "cut", lambda *args: False)
    def blocked(ctx, aff, per, text=None, exit=False, **kwargs):
        before = env.screenshot(); env.scene = 1; after = env.screenshot()
        return SimpleNamespace(status="blocked", reason="exit not crossed", inputs=8, calls=0,
                               before=before, after=after, scroll=(0, 0), blank_seen=False)
    monkeypatch.setattr(loop_module, "run_affordance", blocked)
    row = loop.step()
    assert row["pred"]["effect"] == "place_changes" and row["pred"]["detail"] is None
    assert "screen_changes" in row["outcome"]["effects"] and row["verdict"] == "miss"

def test_mislabelled_player_does_not_shift_tiles_or_duplicate_entities(tmp_path, monkeypatch):
    from agent import loop as module
    env, seen, shot = GridEnv(), [], GridEnv.screenshot; env.phase = 0
    env.screenshot = lambda: (f := shot(env), f.putpixel((0, 0), (env.phase, 0, 0)))[0]
    def perceive(*_):
        seen.append(1); player = [.5, .5] if len(seen) == 1 else [.5, .5 + 3 * 16 / 144]  # second view labels another sprite as the player
        data = {"screen":"world","text":[],"affordances":[{"verb":"approach","target":"lamp","at":[.7,.5]}],"entities":[{"name":"player","kind":"player","at":player}, {"name":"lamp","kind":"object","at":[.7,.5]}]}
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
    # The re-look and one repair both get a call; the repair stays malformed and is not repeated.
    assert row["options"] == 0 and not restored.state()["repair_pending"] and restored.state()["repair_used"]
    assert len(model.perceiver_prompts) == 3 and "previous JSON was empty or malformed" in model.perceiver_prompts[-1]

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
    loop.step(); assert loop.state()["repair_used"] and len(model.prompts)==3
    loop.step(); assert len(model.prompts)==3

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
    loop=Loop(env,FailsAfterChoice([reply(perception(press=True)),reply({"place":"","notebook":[],"action":{"verb":"advance"},"text":None,"expect":"place_changes","why":"test"})]),mem,"grid")
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
    assert resumed.notebook.log == []
    resumed._learn([], [], 47, "tiled room", "world")
    assert len(resumed.notebook.log) == 1
    entry = resumed.notebook.log[0]["text"]
    assert entry.startswith("MOM at tiled room:")
    assert "PROF. OAK, next door, is looking for you." in entry
    assert {"MOM: Right.", "All boys leave", "All boys leave home some day.", "home some day.",
            "It said so on TV.", "PROF. OAK, next", "door, is looking", "door, is looking for you."} <= {
                row["text"] for row in resumed.mem.rows("knowledge")}
    assert resumed.text_pending
    assert resumed.text_for_trigger == "Right. All boys leave home some day. It said so on TV. PROF. OAK, next door, is looking for you."

    resumed.text_pending = False
    for step, texts in fragments: resumed._learn(texts, [], step + 10, "tiled room", "dialogue")
    resumed._learn([], [], 57, "tiled room", "world")
    assert len(resumed.notebook.log) == 1
    assert not resumed.text_pending and not resumed.conversation["lines"]

    resumed._learn(["A sign says KEEP OUT."], [], 58, "tiled room", "world")
    assert len(resumed.notebook.log) == 2
    assert "A sign says KEEP OUT." in resumed.notebook.log[-1]["text"]
    resumed._learn(["A continuation ▼"], [], 59, "tiled room", "dialogue")
    resumed._learn(["A continuation is complete."], [], 60, "tiled room", "dialogue")
    resumed._learn([], [], 61, "tiled room", "world")
    assert resumed.notebook.log[-1]["text"].endswith('"A continuation is complete."')

def test_tracked_approach_scroll_does_not_split_but_real_cuts_do(tmp_path, monkeypatch):
    from agent import loop as loop_module
    from agent.observer import cut as observer_cut

    def run_case(root, verb="approach", reason="", blank=False):
        env = GridEnv(); before = env.screenshot()
        start = perception(two=True); start["screen"] = "world"
        for aff in start["affordances"]: aff["verb"] = verb
        destination = perception(); destination["screen"] = "world"
        destination["entities"] += [{"kind":"object", "name":"new landmark", "at":[.2,.2]}, {"kind":"exit", "name":"exit: door", "at":[500,500]}]
        if verb == "interact": destination["text"] = ["Visible dialogue"]
        model = StubModel([reply(start), act_reply(0, "place_changes", verb), reply(destination)])
        mem = Memory(root/"memory"); loop = Loop(env, model, mem, "grid")
        loop.world.self_spot, loop.world._self_from_motion = [.5, .5], True  # no self-locating probe presses

        def move(ctx, aff, per, text=None, exit=False):
            env.scene = 1; after = env.screenshot()
            assert observer_cut(before, after)
            return SimpleNamespace(status="done", reason=reason, inputs=7, calls=0,
                                   before=before, after=after, scroll=(112,0), blank_seen=blank)

        monkeypatch.setattr(loop_module, "run_affordance", move)
        return loop, mem, loop.step()

    loop, mem, row = run_case(tmp_path/"tracked")
    assert row["scene_cut"] is False and row["next_place"] == row["place"]
    assert "place_changes" not in row["outcome"]["effects"]
    assert not mem.get("places", row["place"])["edges"]

    for name, verb, reason, blank in (("skill", "approach", "scene cut", False),
                                      ("blank", "approach", "", True)):
        loop, mem, row = run_case(tmp_path/name, verb, reason, blank)
        assert row["scene_cut"] is True and row["next_place"] != row["place"]
        assert mem.get("places", row["next_place"])["state"] == "candidate"
        assert len(mem.get("places", row["place"])["edges"]) == 1
        assert not [r for r in mem.rows("relations") if r["p"] == "leads_to" and r["src"] == "inferred"]
    loop, mem, row = run_case(tmp_path/"interact", "interact")
    assert row["scene_cut"] is False and row["next_place"] == row["place"]

def test_runner_press_exception_and_truncation(tmp_path, monkeypatch):
    from scripts import run_agent
    class Broken(GridEnv):
        def press(self,*a,**k): raise RuntimeError("focus lost")
        def save(self,path): Path(path).write_text("state")
        def close(self): pass
    model=StubModel([reply(perception(press=True)),reply({"place":"","notebook":[],"action":{"verb":"advance"},"text":None,"expect":"screen_changes","why":"test"})]); model._post=lambda p: None
    monkeypatch.setattr(run_agent,"PyBoyEnv",lambda *a,**k: Broken())
    monkeypatch.setattr(run_agent,"find_rom",lambda:"rom")
    monkeypatch.setattr(run_agent,"Ollama",lambda *a,**kw:model)
    monkeypatch.setattr(run_agent,"_ledger",lambda *a:None)
    monkeypatch.setattr(run_agent,"read",lambda env:{"map":1})
    run=tmp_path/"run"
    assert run_agent.main(["--steps","1","--max-calls","5","--run",str(run)]) == 0
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
    model=Posts([reply(perception(press=True)),reply({"place":"","notebook":[],"action":{"verb":"advance"},"text":None,"expect":"screen_changes","why":"test"})]); model._post=lambda p:None
    assert run_agent.main(["--steps","1","--max-calls","1","--run",str(tmp_path/"cap")]) == 3
    assert (tmp_path/"cap/checkpoint/000000/harness.json").exists()
    class Offline(StubModel):
        def ask(self,*a,**k): raise ModelUnavailable("offline")
    model=Offline([]); model._post=lambda p:None; monkeypatch.setattr(run_agent,"Ollama",lambda *a,**k:model)
    assert run_agent.main(["--steps","1","--max-calls","5","--run",str(tmp_path/"offline")]) == 4
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
    phase_exhausted={**data,"total":2925,"stage_b":181}
    path.write_text(json.dumps(phase_exhausted))
    with pytest.raises(run_agent.CallCap): run_agent._ledger(Path("runs/m1"),"b",300)
    assert json.loads(path.read_text()) == phase_exhausted
    m1_exhausted={**data,"m1":2875,"stage_b":181}
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
    assert run_agent.main([*common,"--run",str(tmp_path/"default")]) == 3
    assert run_agent.main([*common,"--stage-b-cap","300","--run",str(tmp_path/"override")]) == 0

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
    assert run_agent.main(["--resume","--from-step","0","--steps","1","--max-calls","5","--run",str(run)]) == 0
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
    assert run_agent.main(["--resume","--from-step","10","--steps","1","--max-calls","5","--run",str(run)]) == 0
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
    assert run_agent.main(["--steps","1","--max-calls","5","--run",str(run)]) == 0
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
    assert run_agent.main(["--steps", "1", "--max-calls", "100", "--stage", "p", "--run", str(tmp_path/"m1")]) == 3
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
    assert run_agent.main(["--steps", "15", "--max-calls", "30", "--stage", "p", "--run", str(run)]) == 0
    assert steps == [1] and json.loads((run/"checkpoint/000001/harness.json").read_text())["step"] == 1

def test_probe_stage_ledger_persists_thirty_request_cap(tmp_path, monkeypatch):
    from scripts import run_agent
    monkeypatch.chdir(tmp_path)
    for _ in range(30): run_agent._ledger(Path("runs/m1"), "p", 30)
    data = json.loads(Path("runs/e3_budget.json").read_text())
    assert (data["total"], data["m1"], data["stage_p"]) == (30, 30, 30)
    with pytest.raises(run_agent.CallCap): run_agent._ledger(Path("runs/m1"), "p", 30)
    assert json.loads(Path("runs/e3_budget.json").read_text()) == data

def test_planner_owns_goals_and_act_closes_the_focus(tmp_path):
    class Roles(StubModel):
        def __init__(self, replies): super().__init__(replies); self.roles = []
        def ask(self, *args, **kwargs): self.roles.append(kwargs.get("role")); return super().ask(*args, **kwargs)
    plan_reply = reply({"goals": [{"op": "set", "key": "top", "value": {"text": "finish", "parent": None, "status": "active"}},
                                  {"op": "set", "key": "g1", "value": {"text": "leave", "parent": "top", "done_when": "new place", "source": "assumed"}}],
                        "facts": [{"key": "find_person", "text": "A person next door is looking for you.", "from": "MOM", "source": "heard"}],
                        "focus": "g1", "lesson": None, "why": "start"})
    act_reply = reply({"place": "Hall", "notebook": [{"op": "set", "section": "goals", "key": "sneaky", "value": {"text": "x"}},
                                                    {"op": "focus", "status": "done", "evidence": "door crossed"}],
                       "action": {"thing": 0, "verb": "approach"}, "text": None, "expect": "screen_changes", "why": "try"})
    world = perception(); world["screen"] = "world"  # a place is named only on a navigable screen
    model = Roles([reply(world), plan_reply, act_reply, reply(world), reply(world)])
    loop = Loop(GridEnv(), model, Memory(tmp_path / "memory"), "grid")
    loop.world.self_spot, loop.world._self_from_motion = [.5, .5], True  # no self-locating probe presses

    row = loop.step()

    saved = json.loads((tmp_path / "memory" / "notebook.json").read_text())
    assert row["plan"]["focus"] == "g1" and row["act"]["ops"] == 1 and row["act"]["why"] == "try"
    assert "sneaky" not in saved["goals"] and saved["goals"]["g1"]["status"] == "done"  # act can't add goals, only close the focus
    assert any("goal g1 done: door crossed" in e["text"] for e in saved["log"])
    assert loop.focus_closed and loop._plan_trigger({}) == loop.focus_closed  # closing the focus replans next step
    assert loop.mem.get("places", row["place"])["name"] == "Hall"
    assert saved["hints"]["find_person"] == {"text": "A person next door is looking for you.", "from": "MOM", "source": "heard", "step": 1}
    assert "facts (what the game told or showed):" in loop.notebook.render()
    assert "facts (what the game told or showed):" in loop.notebook.render_focus()
    assert model.roles[:3] == ["perceiver", "plan", "act"] and "chooser" not in model.roles


def test_plan_without_facts_still_parses(tmp_path):
    from agent.plan import plan
    notebook = Notebook(tmp_path)
    parsed = plan(StubModel([reply({"goals": [], "focus": "", "why": "new information",
                                    "facts": [{"key": "ok", "text": "A person needs help.", "from": "MOM", "source": "heard"},
                                              {"key": "empty", "text": " "}, {"key": "x" * 41, "text": "too long"}, "junk"]})]),
                  "text", notebook, "", [], [])
    assert parsed.facts == [{"key": "ok", "text": "A person needs help.", "from": "MOM", "source": "heard"}]
    missing = plan(StubModel([reply({"goals": [], "focus": "", "why": "nothing new"})]), "cycle", notebook, "", [], [])
    assert missing.facts == []


def test_planner_prioritizes_game_text_and_checks_it_before_throttle(tmp_path):
    from agent.plan import SYSTEM
    loop = Loop(GridEnv(), StubModel([]), Memory(tmp_path / "memory"), "grid")
    loop.notebook.data["goals"]["g"] = {"text": "explore", "status": "active"}
    loop.notebook.focus = "g"
    loop.step_no = loop.last_plan = 10
    loop.text_pending = True
    loop.text_for_trigger = "Someone is looking for you. " + "x" * 220

    assert "What the game tells you comes first." in SYSTEM
    assert "Explore only as a child of a goal" in SYSTEM
    assert "it may be a goal before it is on the map (source heard)" in SYSTEM
    assert "first record what it tells you as facts" in SYSTEM
    assert "Facts are what the game told or showed; trust them over guesses." in SYSTEM
    assert loop._plan_trigger({"screen": "dialogue"}) is None
    assert loop._plan_trigger({"screen": "world"}) == "new game text: " + loop.text_for_trigger[:200]


def test_actor_speaker_name_attaches_after_new_text(tmp_path, monkeypatch):
    from agent import loop as module
    from types import SimpleNamespace

    before = {"screen": "world", "text": [], "entities": [
        {"kind": "player", "name": "player", "at": [.5, .5]},
        {"kind": "npc", "name": "person", "at": [.6, .5]}], "affordances": []}
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


def test_act_prompt_summarizes_recent_repeated_blocked_action(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from agent import loop as module

    class Captures(StubModel):
        def __init__(self, replies): super().__init__(replies); self.prompts = []
        def ask(self, *args, **kwargs):
            if kwargs.get("role") == "act": self.prompts.append(args[1])
            return super().ask(*args, **kwargs)

    data = {"screen": "world", "text": [], "entities": [
                {"kind": "player", "name": "player", "at": [.5, .5]},
                {"kind": "npc", "name": "npc", "at": [.6, .5]}],
            "affordances": [{"verb": "approach", "target": "npc", "at": [.6, .5]}]}
    monkeypatch.setattr(module, "perceive", lambda *args: (data, reply(data)))
    def blocked(ctx, *args, **kwargs):
        frame = ctx.env.screenshot()
        return SimpleNamespace(status="blocked", reason="both axes blocked", inputs=1, calls=0,
                               before=frame, after=frame, scroll=(0, 0), blank_seen=False, moves=[])
    monkeypatch.setattr(module, "run_affordance", blocked)
    response = {"place": "Room", "notebook": [], "action": {"thing": 1, "verb": "approach"}, "text": None,
                "expect": "screen_changes", "why": "try"}
    model = Captures([reply(response) for _ in range(4)])
    loop = Loop(GridEnv(), model, Memory(tmp_path / "memory"), "grid")

    rows = [loop.step() for _ in range(4)]

    assert "x3" in model.prompts[3] and "both axes blocked" in model.prompts[3]
    assert "screen unchanged x3" in model.prompts[3]
    flags = rows[3]["act"]["flags"]
    assert len([flag for flag in flags if flag.startswith("recent:")]) == 1
    assert flags[-1] in model.prompts[3]
    assert all(row["skill"]["reason"] == "both axes blocked" for row in rows[:3])
    model._replies.append(reply(response))
    resumed = Loop(loop.env, model, loop.mem, "grid")
    resumed_row = resumed.step()
    assert "x4" in model.prompts[4] and resumed_row["act"]["flags"][-1] in model.prompts[4]


def test_act_recent_groups_by_action_and_caps_mixed_outcomes(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from agent import loop as module

    class Captures(StubModel):
        def __init__(self, replies): super().__init__(replies); self.prompts = []
        def ask(self, *args, **kwargs):
            if kwargs.get("role") == "act": self.prompts.append(args[1])
            return super().ask(*args, **kwargs)

    data = {"screen": "world", "text": [], "entities": [{"kind": "player", "name": "player", "at": [.5, .5]}],
            "affordances": [{"verb": "approach", "target": "npc", "at": [.6, .5]}]}
    monkeypatch.setattr(module, "perceive", lambda *args: (data, reply(data)))
    def blocked(ctx, *args, **kwargs):
        frame = ctx.env.screenshot()
        return SimpleNamespace(status="blocked", reason="both axes blocked", inputs=1, calls=0,
                               before=frame, after=frame, scroll=(0, 0), blank_seen=False, moves=[])
    monkeypatch.setattr(module, "run_affordance", blocked)
    response = {"place": "Room", "notebook": [], "action": {"thing": 1, "verb": "approach"}, "text": None,
                "expect": "screen_changes", "why": "try"}
    model = Captures([reply(response)])
    loop = Loop(GridEnv(), model, Memory(tmp_path / "memory"), "grid")
    loop.recent = [{"step": 77 + i, "choice": {"verb": "approach", "target": "npc", "id": "detector-id-123"},
                    "skill": {"status": "blocked" if i != 1 else "done", "reason": "both axes blocked" if i != 1 else "adjacent"},
                    "outcome": {"effects": ["no_change"] if i in (0, 4) else ["screen_changes"]}}
                   for i in range(5)]

    row = loop.step()

    flag = next(flag for flag in row["act"]["flags"] if flag.startswith("recent:"))
    assert "approach npc x5 since step 77; last blocked (both axes blocked)" in flag
    assert "screen unchanged x2" in flag and "detector-id-123" not in flag
    assert len(flag) <= 200 and flag in model.prompts[0]




def test_act_recent_says_no_effect_when_every_run_left_the_screen_unchanged(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from agent import loop as module

    class Captures(StubModel):
        def __init__(self, replies): super().__init__(replies); self.prompts = []
        def ask(self, *args, **kwargs):
            if kwargs.get("role") == "act": self.prompts.append(args[1])
            return super().ask(*args, **kwargs)

    data = {"screen": "world", "text": [], "entities": [{"kind": "player", "name": "player", "at": [.5, .5]}],
            "affordances": [{"verb": "approach", "target": "npc", "at": [.6, .5]}]}
    monkeypatch.setattr(module, "perceive", lambda *args: (data, reply(data)))
    def blocked(ctx, *args, **kwargs):
        frame = ctx.env.screenshot()
        return SimpleNamespace(status="blocked", reason="both axes blocked", inputs=1, calls=0,
                               before=frame, after=frame, scroll=(0, 0), blank_seen=False, moves=[])
    monkeypatch.setattr(module, "run_affordance", blocked)
    response = {"place": "Room", "notebook": [], "action": {"thing": 1, "verb": "approach"}, "text": None,
                "expect": "screen_changes", "why": "try"}
    model = Captures([reply(response)])
    loop = Loop(GridEnv(), model, Memory(tmp_path / "memory"), "grid")
    loop.recent = [{"step": 77 + i, "choice": {"verb": "approach", "target": "npc", "id": "detector-id-123"},
                    "skill": {"status": "done", "reason": ""},
                    "outcome": {"effects": ["no_change"]}}
                   for i in range(5)]

    row = loop.step()

    flag = next(flag for flag in row["act"]["flags"] if flag.startswith("recent:"))
    assert "approach npc -> no effect x5 since step 77" in flag and "-> done" not in flag
def test_act_buttons_are_pressed_in_order(tmp_path):
    class Recording(GridEnv):
        def __init__(self): super().__init__(); self.keys = []
        def press(self, key, frames=8, on_frame=None): self.keys.append(key); super().press(key, frames, on_frame)
    env = Recording()
    act_reply = reply({"place": "Hall", "notebook": [], "action": {"buttons": ["up", "a"]}, "text": None, "expect": "screen_changes", "why": "walk"})
    loop = Loop(env, StubModel([reply(perception()), act_reply, reply(perception()), reply(perception())]), Memory(tmp_path / "memory"), "grid")

    row = loop.step()

    assert env.keys == ["UP", "A"] and row["choice"]["verb"] == "buttons"


def test_act_invalid_action_waits_and_is_unobservable(tmp_path):
    act_reply = reply({"place": "Hall", "notebook": [], "action": {"thing": 9, "verb": "approach"}, "text": None, "expect": "screen_changes", "why": "?"})
    loop = Loop(GridEnv(), StubModel([reply(perception()), act_reply, reply(perception()), reply(perception())]), Memory(tmp_path / "memory"), "grid")

    row = loop.step()

    assert row["choice"] is None and row["act"]["action"] is None and row["verdict"] == "unobservable"


def test_option_pick_drives_grid_through_its_exit(tmp_path, monkeypatch):
    from agent import loop as module
    env = GridEnv()
    def perceive(*_):
        per = env.perception(); return per, reply(per)
    monkeypatch.setattr(module, "perceive", perceive)
    pick = reply({"place": "", "notebook": [], "action": {"i": 1}, "text": None, "expect": "place_changes", "why": "the door"})
    loop = Loop(env, StubModel([pick]), Memory(tmp_path / "memory"), "grid")
    loop.world.self_spot, loop.world._self_from_motion = [.5, .5], True

    row = loop.step()

    assert row["option"] == {"n": 1, "tool": "approach", "label": "possible exit door (unverified, never crossed)", "notes": []}
    assert row["act"]["action"] == {"thing": 2, "verb": "approach"} and row["choice"]["verb"] == "approach" and row["choice"]["target"] == "exit: door"
    assert env.scene == 1

def test_go_to_unexplored_walks_to_nearest_frontier(tmp_path):
    from agent.space import fold
    loop = Loop(GridEnv(), StubModel([]), Memory(tmp_path / "memory"), "grid")
    fold(loop.mem, "p", (0, 0), [{"dir": d, "shift": 0} for d in ("UP", "DOWN", "LEFT")], 16, 1)  # walls on three sides, RIGHT untried
    loop.pos = (0, 0)
    assert loop._unexplored("p") == {"dirs": ["RIGHT"]}
    from agent.space import mark_exit
    mark_exit(loop.mem, "q", (0, 0), "DOWN", "r", (0, 0), 2)  # arriving in r: r@(0,0) gets an inferred UP exit back to q
    fold(loop.mem, "r", (0, 0), [{"dir": d, "shift": 0} for d in ("LEFT", "RIGHT", "DOWN")], 16, 3)
    assert loop._unexplored("r") is None  # the only open edge is the way back out: not "unexplored"
    loop.tile_px = None
    assert loop._unexplored("p") is None


def test_new_place_and_back_and_forth_cycle_trigger_replans_without_throttle(tmp_path):
    loop = Loop(GridEnv(), StubModel([]), Memory(tmp_path / "memory"), "grid")
    for pid in ("house", "town"): loop.mem.upsert("places", {"id": pid, "name": pid, "screen": "world", "state": "place", "visits": 1})
    loop.last_plan = loop.step_no = 10  # a plan just ran: ordinary triggers are throttled
    loop._moved("house", "town", "door", 11)
    assert loop.urgent == "arrived somewhere new: town" and loop._plan_trigger({}) == loop.urgent
    loop.urgent = None
    for step, (a, b) in enumerate((("town", "house"), ("house", "town"), ("town", "house")), 12): loop._moved(a, b, "door", step)
    assert loop.urgent.startswith("cycle: went back and forth between town and house 4 times")
    assert loop._plan_trigger({}) == loop.urgent
    assert loop.fails["town|approach|door"] == {"n": 2, "why": "leads back to house: cycle", "step": 14}


def test_repeated_failure_flags_act_and_replans_once_on_new_evidence(tmp_path, monkeypatch):
    from agent import loop as module
    env = GridEnv()
    def perceive(*_):
        return {"screen": "world", "text": [], "entities": [{"name": "player", "kind": "player", "at": [.5, .5]}, {"name": "door", "kind": "exit", "at": [.6, .5]}]}, reply({})
    monkeypatch.setattr(module, "perceive", perceive)
    seen_flags, seen_options = [], []
    def act(*a, **k): seen_flags.append(a[5]); seen_options.append(k["options"]); return SimpleNamespace(place="", ops=[], action={"thing": 1, "verb": "approach"}, expect="place_changes", why="t", text=None)
    monkeypatch.setattr(module, "act", act)
    def blocked(ctx, aff, per, **k):
        frame = env.screenshot(); return SimpleNamespace(status="blocked", reason="wall", inputs=1, calls=0, before=frame, after=frame, scroll=(0, 0), blank_seen=False, moves=[])
    monkeypatch.setattr(module, "run_affordance", blocked)
    loop = Loop(env, StubModel([]), Memory(tmp_path / "memory"), "grid")
    loop.world.self_spot, loop.world._self_from_motion = [.5, .5], True
    loop.step(); assert loop.urgent.startswith("new failure: approach door [")  # the first failure is new evidence
    row = loop.step()  # it replans on the next step; the same failure again is not new evidence
    assert row["plan"]["trigger"].startswith("new failure: approach door [") and loop.urgent is None
    row = loop.step(); assert row["plan"] is None and loop.urgent is None
    assert not any(f.startswith("failed here before") for f in seen_flags[-1])  # its content is in the option notes now
    assert [o["notes"][0] for o in seen_options[-1] if o["label"] == "possible exit door (unverified, never crossed)"] == ["tried here 2×, last: wall"]


def test_act_known_places_are_navigable_named_places_only(tmp_path, monkeypatch):
    from agent import loop as loop_module
    seen = {}
    def recording_act(model, image, per, focus, view, flags, controls, defaults, inputs, places=None, options=None):
        seen["places"] = places
        return SimpleNamespace(place="", ops=[], action=None, expect="", why="", text=None)
    monkeypatch.setattr(loop_module, "act", recording_act)
    mem = Memory(tmp_path / "memory")
    mem.upsert("places", {"id": "p_hall", "name": "Hall", "screen": "world", "state": "place", "last": 2, "edges": []})
    mem.upsert("places", {"id": "p_menu", "screen": "menu", "state": "candidate", "last": 3, "edges": []})
    world = perception(); world["screen"] = "world"
    loop = Loop(GridEnv(), StubModel([reply(world), reply(world)]), mem, "grid")  # before and after perception
    loop.world.self_spot, loop.world._self_from_motion = [.5, .5], True
    loop.step()
    assert seen["places"] == ["Hall"]


def test_fail_key_merges_rewordings_and_cycle_marks(tmp_path):
    loop = Loop(GridEnv(), StubModel([]), Memory(tmp_path / "memory"), "grid")
    key = loop._fail_key
    assert len({key("p", "buttons", t) for t in ("UP A", "UP", "up UP up A")}) == 1 and key("p", "buttons", "A") != key("p", "buttons", "B")
    assert key("p", "go_to", "Stairs") == key("p", "approach", "stairs") != key("q", "approach", "stairs") and key("p", "advance", "") == "p|advance|advance"
    assert key("p", "interact", "npc") != key("p", "approach", "npc")  # an interaction and an approach are different intents
    for mem_id in ("a", "b"): loop.mem.upsert("places", {"id": mem_id, "name": mem_id, "screen": "world", "state": "place", "visits": 2})
    for step, (s, d) in enumerate((("a", "b"), ("b", "a"), ("a", "b"), ("b", "a")), 1): loop._moved(s, d, "UP UP A", step, key(s, "buttons", "UP UP A"))  # each move's key is its own source place's
    assert loop.fails[key("a", "buttons", "UP")]["why"] == "leads back to b: cycle"


def test_slow_cycle_fires_and_progress_resets_history(tmp_path):
    loop = Loop(GridEnv(), StubModel([]), Memory(tmp_path / "memory"), "grid")
    for pid in ("a", "b"): loop.mem.upsert("places", {"id": pid, "name": pid, "screen": "world", "state": "place", "visits": 2})
    for step, (s, d) in zip((1, 3, 21, 33), (("a", "b"), ("b", "a"), ("a", "b"), ("b", "a"))): loop._moved(s, d, "door", step)
    assert loop.urgent.startswith("cycle: went back and forth between b and a 4 times in 32 steps") and loop.moves == []
    loop.urgent = None; loop._moved("a", "b", "door", 40); loop._moved("b", "a", "door", 41); assert loop.urgent is None  # a full alternation is needed again


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
def test_place_change_seen_only_by_next_observation_counts_its_exit_on_both_sides(tmp_path, monkeypatch, pos):
    loop, mem, per = _seen_next_step(tmp_path, monkeypatch, pos)
    loop.step()
    assert mem.get("crossings", "room@2,0>hall")["n"] == 1 and mem.get("crossings", "hall@0,0>room")["n"] == 1
    assert any(o["label"].startswith("exit RIGHT → Hall (used 1×") for o in loop.world.options(per, "room", {}, 3, inputs=INPUTS))
    assert any(o["label"].startswith("exit LEFT → Room (used 1×") for o in loop.world.options(per, "hall", {}, 3, inputs=INPUTS))


def test_place_change_with_no_exit_tile_counts_nothing_and_does_not_crash(tmp_path, monkeypatch):
    loop, mem, _ = _seen_next_step(tmp_path, monkeypatch, exit_tile=False)
    assert loop.step()["next_place"] == "hall" and mem.rows("crossings") == []


def test_crossing_already_counted_by_the_scene_cut_is_not_counted_again(tmp_path, monkeypatch):
    loop, mem, _ = _seen_next_step(tmp_path, monkeypatch)
    loop.world._cross("room", (2, 0), "hall", (0, 0), 1)  # what World.after counts for a scene cut after a press
    loop.place, loop.world.current_place, loop.world.pos = "hall", "hall", (0, 0)  # the end of that step
    loop.step()
    assert mem.get("crossings", "room@2,0>hall")["n"] == 1 and mem.get("crossings", "hall@0,0>room")["n"] == 1


def test_any_arrival_replans_after_the_floor_and_new_places_skip_it(tmp_path):
    loop = Loop(GridEnv(), StubModel([]), Memory(tmp_path / "memory"), "grid")
    for pid, visits in (("a", 3), ("b", 3), ("c", 1)): loop.mem.upsert("places", {"id": pid, "name": pid.upper(), "screen": "world", "visits": visits})
    loop.notebook.data["goals"]["g"] = {"text": "leave", "status": "active"}; loop.notebook.focus = "g"
    loop.step_no, loop.last_plan = 10, 10

    loop._moved("a", "b", "door", 11)
    assert loop.arrived == "arrived at B (been here before)" and loop.urgent is None
    assert loop._plan_trigger({}) is None  # inside the 3-step floor
    loop.step_no = 12
    assert loop._plan_trigger({}) == loop.arrived
    loop._moved("b", "c", "door", 13)
    assert loop.urgent == "arrived somewhere new: C"


def _room_with_open_east_edge(mem, place):  # 3x3 open room, walls around it, (1,0) untried to the RIGHT into unseen (2,0)
    for x in range(-2, 3):
        for y in range(-2, 3):
            if (x, y) != (2, 0): mem.upsert("tiles", {"id": f"{place}@{x},{y}", "place": place, "x": x, "y": y, "floor": -1 <= x <= 1 and -1 <= y <= 1, "blocked": [], "pushed": [], "exit": None, "step": 1})


def test_go_to_unexplored_routes_around_an_actor_on_the_way(tmp_path):
    from agent.space import DIRS
    loop = Loop(GridEnv(), StubModel([]), Memory(tmp_path / "memory"), "grid")
    _room_with_open_east_edge(loop.mem, "p"); loop.pos = (-1, 0); loop.world.current_place = "p"; loop.world.actor_tiles = frozenset({(0, 0)})

    dirs = loop._unexplored("p")["dirs"]

    at, visited = (-1, 0), [(-1, 0)]
    for d in dirs: at = (at[0] + DIRS[d][0], at[1] + DIRS[d][1]); visited.append(at)
    assert dirs[-1] == "RIGHT" and visited[-1] == (2, 0) and (0, 0) not in visited


def test_go_to_unexplored_skips_a_frontier_whose_step_cell_is_held(tmp_path):
    loop = Loop(GridEnv(), StubModel([]), Memory(tmp_path / "memory"), "grid")
    _room_with_open_east_edge(loop.mem, "p"); loop.pos = (-1, 0); loop.world.current_place = "p"; loop.world.actor_tiles = frozenset({(2, 0)})

    assert loop._unexplored("p") is None


def test_go_to_on_screen_npc_routes_beside_it_facing_it_around_another_actor(tmp_path, monkeypatch):
    from agent import loop as module
    per = {"screen": "world", "text": [], "entities": [
        {"name": "player", "kind": "player", "at": [.5, .5]},
        {"name": "npc", "kind": "npc", "at": [.25, .5]},  # two tiles left of the player
        {"name": "guard", "kind": "npc", "at": [.35, .5]}]}  # the tile between them
    monkeypatch.setattr(module, "perceive", lambda *_: (per, reply({})))
    monkeypatch.setattr(module, "act", lambda *a, **k: SimpleNamespace(place="", ops=[], action={"go_to": "npc"}, expect="screen_changes", why="talk", text=None))
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
    def fake(*_args, **kwargs):
        kind, value = next(steps); seen.append(kwargs.get("decide", False))
        o = next(o for o in kwargs["options"] if o["label"] == value) if kind == "option" else None
        return SimpleNamespace(place="", ops=[], action=dict(o["action"]) if o else value, option=o["n"] if o else None, expect="screen_changes", why="t", text=None)
    monkeypatch.setattr(module, "act", fake)
    return seen


@pytest.mark.parametrize("decide", [False, True])
def test_equivalent_resolved_intents_share_one_counter_and_interaction_is_its_own(tmp_path, monkeypatch, decide):
    env = GridEnv(); npc_scene(monkeypatch, env)
    seen = scripted_act(monkeypatch, [("option", "approach npc"), ("action", {"thing": 1, "verb": "approach"}), ("action", {"go_to": "npc"}), ("option", "interact with npc")])
    loop = Loop(env, StubModel([]), Memory(tmp_path / "memory"), "grid", decide=decide)
    loop.world.self_spot, loop.world._self_from_motion = [.5, .5], True
    for _ in range(4): loop.step()
    ident = loop.world.entity_ids[1]
    assert seen == [decide] * 4
    assert {k: v["n"] for k, v in loop.fails.items()} == {f"{loop.place}|approach|thing:{ident}": 3, f"{loop.place}|interact|thing:{ident}": 1}


def test_options_without_intent_key_still_count_failures(tmp_path, monkeypatch):
    env = GridEnv(); npc_scene(monkeypatch, env); scripted_act(monkeypatch, [("option", "approach npc")])
    loop = Loop(env, StubModel([]), Memory(tmp_path / "memory"), "grid")
    loop.world.self_spot, loop.world._self_from_motion = [.5, .5], True
    real = loop.world.options
    loop.world.options = lambda *a, **k: [{name: v for name, v in o.items() if name != "intent_key"} for o in real(*a, **k)]  # rows as an older checkpoint made them
    row = loop.step()
    assert row["choice"]["verb"] == "approach" and sum(v["n"] for v in loop.fails.values()) == 1


def test_counters_and_identity_round_trip_and_legacy_names_follow_the_rules(tmp_path, monkeypatch):
    env = GridEnv(); npc_scene(monkeypatch, env); scripted_act(monkeypatch, [("option", "approach npc"), ("option", "interact with npc")])
    loop = Loop(env, StubModel([]), Memory(tmp_path / "memory"), "grid")
    loop.world.self_spot, loop.world._self_from_motion = [.5, .5], True
    loop.step(); loop.step()
    place, ident = loop.place, loop.world.entity_ids[1]
    state = json.loads(json.dumps(loop.state()))
    fresh = Loop(GridEnv(), StubModel([]), Memory(tmp_path / "memory"), "grid"); fresh.restore(state)
    assert fresh.fails == loop.fails and set(fresh.fails) == {f"{place}|approach|thing:{ident}", f"{place}|interact|thing:{ident}"}
    # an alias merged into this place; a legacy name with one intent (a lamp); a legacy name with two (an actor: approach or interact)
    loop.mem.upsert("places", {"id": "old", "name": "Old", "screen": "world", "state": "merged", "into": place, "edges": []})
    loop.mem.upsert("entities", {"id": "lamp1", "name": "lamp", "kind": "object", "place": place, "tile": [1, 1], "status": "remembered", "last": 1, "seen": 1})
    state["planner"]["fails"] = {**state["planner"]["fails"],
                                 f"old|approach|thing:{ident}": {"n": 2, "why": "wall", "step": 3, "act": "approach npc"},
                                 f"{place}|approach|thing:{ident}": {"n": 3, "why": "wall", "step": 4, "act": "approach npc"},
                                 f"{place}|lamp": {"n": 1, "why": "wall", "step": 5, "act": "approach lamp"},
                                 f"{place}|npc": {"n": 2, "why": "wall", "step": 6, "act": "approach npc"},
                                 f"{place}|up a": {"n": 4, "why": "no change", "step": 7, "act": "buttons UP A"}}
    fresh = Loop(GridEnv(), StubModel([]), Memory(tmp_path / "memory"), "grid"); fresh.restore(json.loads(json.dumps(state)))
    assert fresh.fails[f"{place}|approach|thing:{ident}"]["n"] == 3  # the alias folds in: the larger count, not 5
    assert fresh.fails[f"{place}|approach|thing:lamp1"] == {"n": 1, "why": "wall", "step": 5, "act": "approach lamp"}  # one intent: migrated
    assert fresh.fails[f"{place}|npc"]["n"] == 2 and f"{place}|interact|thing:{ident}" in fresh.fails  # two intents: history, not transferred
    assert fresh.fails[f"{place}|buttons|up a"] == {"n": 4, "why": "no change", "step": 7, "act": "buttons UP A"}  # a legacy press key: same subject
    assert not any(k.startswith("old|") for k in fresh.fails)


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
    env = GridEnv(); crate = (0, -1); seen = {"routes": [], "flags": [], "options": [], "acts": 0}
    monkeypatch.setattr(module, "perceive", lambda *_: ({"screen": "world", "text": [], "entities": [
        {"name": "player", "kind": "player", "at": [.5, .5]}, {"name": "sign", "kind": "object", "at": [.5, 1 / 6]}, {"name": "crate", "kind": "object", "at": [.5, .5 - 1 / 9]}]}, reply({})))
    def act(model, image, per, focus, view, flags, *a, **k):
        seen["flags"].append(flags); seen["options"].append([o["label"] for o in k["options"]])
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
    loop.notebook.data["goals"]["g"] = {"text": "reach the sign", "status": "active"}; loop.notebook.focus = "g"
    return loop, model, seen


def enclose(loop):
    """Known walls around the player on every side but the crate's: the only way to the sign is through the crate."""
    place = loop.place
    for x, y in [(-1, 0), (1, 0), (0, 1), (-1, 1), (1, 1), (-1, -1), (1, -1)]:
        loop.mem.upsert("tiles", {"id": f"{place}@{x},{y}", "place": place, "x": x, "y": y, "floor": False, "blocked": [], "pushed": [], "exit": None, "step": 1})


def test_route_failure_reaches_planner_prompt_with_intent_stage_blocker_and_no_crossing(tmp_path, monkeypatch):
    loop, model, _ = crate_scene(monkeypatch, tmp_path)
    loop.step(); cause = loop.cause  # the first failure is new evidence: the planner runs on the next step
    loop.step()
    prompt = model.plans[0]
    assert "Last failure: approach sign [" + loop.place + "|approach|thing:" in prompt
    assert "failed at route" in prompt and "blocked at (0,-1) by thing:" in prompt and "no crossing attempted" in prompt
    assert cause.endswith("no crossing attempted") and "Last failure: " + cause in prompt


def test_crossing_failure_says_crossing_attempted(tmp_path, monkeypatch):
    from agent.loop import failure_cause
    assert failure_cause("p|approach|exit:door", "approach exit: door", "crossing", "exit not crossed") == "approach exit: door [p|approach|exit:door] failed at crossing; crossing attempted"
    assert failure_cause("p|approach|thing:e7", "approach sign", "route", "route blocked", (0, -1), "e7") == "approach sign [p|approach|thing:e7] failed at route; blocked at (0,-1) by thing:e7; no crossing attempted"


def test_chooser_flags_show_the_same_cause_as_the_planner(tmp_path, monkeypatch):
    loop, model, seen = crate_scene(monkeypatch, tmp_path)
    loop.step(); cause = loop.cause
    loop.step()
    assert f"cause: {cause}" in seen["flags"][1] and "no crossing attempted" in cause
    assert "Last failure: " + cause in model.plans[0]


def test_repeated_identical_failure_plans_once_and_new_evidence_plans_once_more(tmp_path, monkeypatch):
    loop, model, seen = crate_scene(monkeypatch, tmp_path, goals=("sign", "sign", "sign", "sign", "crate", "crate"))
    loop.step(); assert len(model.plans) == 0; enclose(loop)  # no detour from here: the same failure repeats
    loop.step(); assert len(model.plans) == 1  # new evidence: the first failure
    for _ in range(2): loop.step()  # the same failure again: no planner call
    assert len(model.plans) == 1 and loop.urgent is None
    loop.step(); assert len(model.plans) == 1  # a new intent failing is new evidence, replanned on the step after
    loop.step(); assert len(model.plans) == 2 and "approach crate" in model.plans[1]


def test_replan_trigger_state_round_trips_and_older_checkpoints_restore(tmp_path, monkeypatch):
    loop, model, seen = crate_scene(monkeypatch, tmp_path)
    loop.step(); enclose(loop)
    state = json.loads(json.dumps(loop.state()))
    assert state["planner"]["reported"] == loop.reported and state["planner"]["cause"] == loop.cause and loop.urgent
    resumed, resumed_model, _ = crate_scene(monkeypatch, tmp_path)
    resumed.restore(state)
    assert (resumed.reported, resumed.cause, resumed.urgent) == (loop.reported, loop.cause, loop.urgent)  # the pending replan is kept
    resumed.step(); assert len(resumed_model.plans) == 1
    resumed.step(); assert len(resumed_model.plans) == 1  # and not re-fired by the same failure
    old = json.loads(json.dumps(loop.state()))
    old["planner"].pop("cause"); old["planner"].pop("reported")
    fresh, _, _ = crate_scene(monkeypatch, tmp_path)
    fresh.restore(old)
    assert fresh.cause is None and fresh.reported is None and fresh.urgent == loop.urgent


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
    assert row["skill"] == {"status": "blocked", "reason": "no route avoids blocker", "inputs": 0, "calls": 0}
    assert again["skill"]["inputs"] == 0 and loop.world.pos == (0, 0)
    assert seen["options"][1] == seen["options"][2] and "approach sign" in seen["options"][1]  # the chooser's rows: same labels, same order
    assert loop.notebook.data["goals"] == {"g": {"text": "reach the sign", "status": "active"}}  # the harness adds no goal
    assert next(iter(loop.fails.values()))["n"] == 3 and next(iter(loop.fails.values()))["avoid"] == [0, -1]


class Keys(GridEnv):
    def __init__(self): super().__init__(); self.keys = []
    def press(self, key, frames=8, on_frame=None): self.keys.append(key); super().press(key, frames, on_frame)

class Roles(StubModel):
    def __init__(self, replies): super().__init__(replies); self.roles = []
    def ask(self, *args, **kwargs):
        if kwargs.get("role") != "plan": self.roles.append(kwargs.get("role"))
        return super().ask(*args, **kwargs)

def pick_reply(i): return reply({"place": "", "notebook": [], "action": {"i": i}, "text": None, "expect": "screen_changes", "why": "test"})
def free_reply(action): return reply({"place": "", "notebook": [], "action": action, "text": None, "expect": "screen_changes", "why": "test"})
def dialogue_scene(): return {"screen": "dialogue", "text": ["Watch the television?"], "affordances": [], "entities": [
    {"name": "television", "kind": "object", "at": [.2, .3]}, {"name": "player", "kind": "player", "at": [.5, .5]}, {"name": "dialogue box", "kind": "dialogue_box", "at": [.5, .85]}]}
def menu_scene(): return {"screen": "menu", "text": ["FIGHT BAG"], "affordances": [], "entities": [
    {"name": "FIGHT", "kind": "menu_item", "at": [.3, .4], "selectable": True}, {"name": "BAG", "kind": "menu_item", "at": [.3, .6], "selectable": True}]}
def world_scene(): return {"screen": "world", "text": [], "affordances": [], "entities": [{"name": "player", "kind": "player", "at": [.5, .5]}]}

def options_seen(monkeypatch):
    from agent import loop as module
    seen, real = [], module.act
    def record(*args, **kwargs): seen.append(kwargs["options"]); return real(*args, **kwargs)
    monkeypatch.setattr(module, "act", record)
    return seen


def test_stale_select_of_unflagged_furniture_fails_without_menu_read_or_input(tmp_path, monkeypatch):
    seen = options_seen(monkeypatch)
    # spare replies: a menu read and an after-screen, answered only if the stale select runs (so baseline fails on its assertions, not a crash)
    spare = [reply({"items": ["television"], "cursor": 0, "layout": "vertical"}), reply(world_scene())]
    env, model = Keys(), Roles([reply(dialogue_scene()), free_reply({"thing": 0, "verb": "select"}), *spare])
    loop = Loop(env, model, Memory(tmp_path / "memory"), "grid")
    row = loop.step()
    assert env.keys == [] and "skill" not in model.roles  # no menu read, no press
    assert row["skill"] == {"status": "failed", "reason": "not a selectable choice on this screen", "inputs": 0, "calls": 0}
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


def test_pre_action_that_never_settles_sends_nothing_and_writes_one_pending_row(tmp_path, monkeypatch):
    short_settle(monkeypatch)
    env = Busy(busy=10 ** 9); seen = scripted_world(monkeypatch, acts=[chosen({"buttons": ["RIGHT"]})])
    loop = Loop(env, StubModel([]), Memory(tmp_path / "memory"), "grid")

    row = loop.step()

    assert row["observation_status"] == "pending" and row["execution"] is None and row["choice"] is None and row["verdict"] == "unobservable"
    assert seen == {"perceive": 0, "act": 0} and loop.model.calls == 0 and env.keys == [] and env.ticks == 6  # one bounded wait, no input
    assert loop.fails == {} and loop.place is None and loop.world.pos == (0, 0) and loop.pending is None and loop.step_no == 1
    assert loop.mem.rows("relations") == [] and [e for p in loop.mem.rows("places") for e in p.get("edges", [])] == []
    assert [r["observation_status"] for r in trace_rows(tmp_path)] == ["pending"]


def test_inputs_then_unsettled_after_frame_resolve_once_on_the_next_settled_frame(tmp_path, monkeypatch):
    short_settle(monkeypatch)
    env = Busy(plan=[20]); seen = scripted_world(monkeypatch, acts=[chosen({"buttons": ["RIGHT"]})])
    loop = Loop(env, StubModel([]), Memory(tmp_path / "memory"), "grid")
    loop.world.self_spot, loop.world._self_from_motion = [.5, .5], True  # no probe press before the action

    first = loop.step()

    assert first["observation_status"] == "pending" and first["execution"] == "done" and first["skill"]["inputs"] == 1 and first["action_step"] is None
    assert env.keys == ["RIGHT"] and loop.pending["action_step"] == first["step"]
    later = []
    while loop.pending is not None and len(later) < 20: later.append(loop.step())
    resolved = later[-1]
    assert all(r["observation_status"] == "pending" and r["execution"] is None for r in later[:-1])
    assert resolved["observation_status"] == "settled" and resolved["action_step"] == first["step"] and resolved["execution"] == "done"
    assert resolved["choice"]["intent"] == first["choice"]["intent"] and resolved["choice"]["verb"] == "buttons"
    assert {"screen_changes", "view_scrolled"} & set(resolved["outcome"]["effects"])  # the screen change is attributed to the action
    assert env.keys == ["RIGHT"] and seen["act"] == 1 and loop.fails == {} and loop.pending is None


def test_forever_animated_screen_gives_one_bounded_pending_row_per_step(tmp_path, monkeypatch):
    short_settle(monkeypatch)
    env = Busy(busy=10 ** 9); seen = scripted_world(monkeypatch)
    loop = Loop(env, StubModel([]), Memory(tmp_path / "memory"), "grid")

    rows = [loop.step() for _ in range(5)]

    assert [r["observation_status"] for r in rows] == ["pending"] * 5 and [r["step"] for r in rows] == [1, 2, 3, 4, 5]
    assert env.ticks == 5 * 6 and seen == {"perceive": 0, "act": 0} and env.keys == []
    assert len(trace_rows(tmp_path)) == 5


def test_checkpoint_while_an_action_is_pending_resumes_without_input_and_counts_its_failure_once(tmp_path, monkeypatch):
    short_settle(monkeypatch)
    env = Busy(plan=[20]); seen = scripted_world(monkeypatch, acts=[chosen({"buttons": ["RIGHT", "NOPE"]})])
    root = tmp_path / "memory"
    loop = Loop(env, StubModel([]), Memory(root), "grid")
    loop.world.self_spot, loop.world._self_from_motion = [.5, .5], True  # no probe press before the action
    first = loop.step()
    assert first["observation_status"] == "pending" and first["execution"] == "blocked" and loop.fails == {}

    state = json.loads(json.dumps(loop.state()))
    assert state["pending"]["action_step"] == first["step"]
    resumed = Loop(env, StubModel([]), Memory(root), "grid"); resumed.restore(state)
    later = []
    while resumed.pending is not None and len(later) < 20: later.append(resumed.step())

    resolved = later[-1]
    assert env.keys == ["RIGHT"] and seen["act"] == 1  # no input replayed, no second action
    assert resolved["observation_status"] == "settled" and resolved["execution"] == "blocked" and resolved["action_step"] == first["step"]
    assert resolved["choice"]["intent"] == first["choice"]["intent"]
    assert resumed.fails[first["choice"]["intent"]]["n"] == 1 and sum(v["n"] for v in resumed.fails.values()) == 1

    legacy = {k: v for k, v in state.items() if k != "pending"}  # a checkpoint from before this field
    old = Loop(env, StubModel([]), Memory(root), "grid"); old.restore(legacy)
    assert old.pending is None


def test_zero_input_failed_select_is_a_hit_on_its_prediction_but_a_failed_execution(tmp_path, monkeypatch):
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

    assert row["observation_status"] == "settled" and row["verdict"] == "hit" and row["execution"] == "failed" and row["skill"]["status"] == "failed"
    assert row["pred"]["effect"] == "no_change" and sum(v["n"] for v in loop.fails.values()) == 1 and seen["act"] == 1


def test_unsettled_zero_input_failure_sets_no_no_change_refutes_nothing_and_counts_nothing(tmp_path, monkeypatch):
    from agent import loop as module
    short_settle(monkeypatch)
    per = {"screen": "world", "text": [], "entities": [{"name": "player", "kind": "player", "at": [.5, .5]}, {"name": "gate", "kind": "object", "at": [.6, .5]}]}
    scripted_world(monkeypatch, per=per, acts=[chosen({"thing": 1, "verb": "select"}, expect="no_change")])
    env = Busy()
    def failed_while_animating(ctx, aff, per, **kwargs):  # the screen starts moving after the choice, with no input sent
        frame = ctx.env.screenshot(); ctx.env.busy = 10 ** 9
        return SimpleNamespace(status="failed", reason="menu or item not identified", inputs=0, calls=1, before=frame, after=frame, scroll=(0, 0), blank_seen=False, moves=[], failure=None)
    monkeypatch.setattr(module, "run_affordance", failed_while_animating)
    loop = Loop(env, StubModel([]), Memory(tmp_path / "memory"), "grid")

    row = loop.step()

    assert row["observation_status"] == "pending" and row["verdict"] == "unobservable" and row["execution"] == "failed"
    assert loop.mem.rows("relations") == [] and [e for p in loop.mem.rows("places") for e in p.get("edges", [])] == []
    assert loop.fails == {} and loop.pending is None


def _edges(mem):
    return sorted((p["id"], e.get("verb"), e.get("via"), e.get("to"), bool(e.get("ok"))) for p in mem.rows("places") for e in p.get("edges", []))


def _cut_run(tmp_path, name, plan, monkeypatch):
    """Two presses: the first walks, the second steps onto the door and the screen cuts to the next scene (blank frames while it fades)."""
    env = Busy(plan=plan); seen = scripted_world(monkeypatch, acts=[chosen({"buttons": ["RIGHT", "RIGHT"]})])
    loop = Loop(env, StubModel([]), Memory(tmp_path / name / "memory"), "grid")
    loop.world.self_spot, loop.world._self_from_motion = [.5, .5], True  # no probe press before the action
    rows = [loop.step()]
    while loop.pending is not None and len(rows) < 30: rows.append(loop.step())
    return loop, env, rows, seen


def test_scene_cut_across_a_deferred_press_keeps_source_and_before_frame_and_folds_once(tmp_path, monkeypatch):
    short_settle(monkeypatch)
    still, still_env, still_rows, _ = _cut_run(tmp_path, "still", [0, 0], monkeypatch)
    late, late_env, late_rows, late_seen = _cut_run(tmp_path, "late", [0, 20], monkeypatch)
    pending = late_rows[0]
    resolved = late_rows[-1]

    assert len(still_rows) == 1 and still_rows[0]["scene_cut"] is True and still_rows[0]["next_place"] != still_rows[0]["place"]
    assert pending["observation_status"] == "pending" and len(late_rows) >= 2
    assert resolved["observation_status"] == "settled" and resolved["action_step"] == pending["step"] and late_seen["act"] == 1
    assert resolved["place"] == pending["place"] == still_rows[0]["place"]  # the source place, not the place the cut landed on
    assert resolved["frame"] == pending["frame"]  # the action's own before-frame
    assert resolved["scene_cut"] is True and resolved["next_place"] == still_rows[0]["next_place"]
    assert still_env.keys == late_env.keys == ["RIGHT", "RIGHT"]
    assert late.world.pos == still.world.pos and late.place == still.place  # no second fold, no double crossing
    assert _edges(late.mem) == _edges(still.mem) and sorted((c["id"], c["n"]) for c in late.mem.rows("crossings")) == sorted((c["id"], c["n"]) for c in still.mem.rows("crossings"))


def test_pending_action_whose_before_frame_is_gone_resolves_degraded_with_no_evidence(tmp_path, monkeypatch):
    short_settle(monkeypatch)
    env = Busy(plan=[20]); scripted_world(monkeypatch, acts=[chosen({"buttons": ["RIGHT", "NOPE"]})])
    loop = Loop(env, StubModel([]), Memory(tmp_path / "memory"), "grid")
    loop.world.self_spot, loop.world._self_from_motion = [.5, .5], True  # no probe press before the action
    first = loop.step()
    (tmp_path / first["frame"]).unlink()  # the checkpoint's before-frame reference no longer resolves

    later = []
    while loop.pending is not None and len(later) < 20: later.append(loop.step())

    resolved = later[-1]
    assert resolved["degraded"] is True and resolved["observation_status"] == "settled" and resolved["execution"] == "blocked"
    assert resolved["action_step"] == first["step"] and resolved["verdict"] == "unobservable" and resolved["outcome"] is None
    assert loop.fails[first["choice"]["intent"]]["n"] == 1 and sum(v["n"] for v in loop.fails.values()) == 1
    assert loop.mem.rows("relations") == [] and [e for p in loop.mem.rows("places") for e in p.get("edges", [])] == []
    assert env.keys == ["RIGHT"] and loop.pending is None

class Counting(StubModel):
    """StubModel with Ollama's call counter: seq is the 1-based id of the latest ask, cache hits included."""
    def __init__(self, replies=()): super().__init__(list(replies)); self.seq = 0
    def ask(self, *args, **kwargs): self.seq += 1; return super().ask(*args, **kwargs)


class Asks:
    """Model double for scripted perception and act: every ask is counted and answered with an empty plan."""
    def __init__(self): self.seq = self.calls = 0
    def ask(self, *args, **kwargs): self.seq += 1; return SimpleNamespace(data={"goals": [], "focus": "", "why": "stub"}, text="")


class Saving(Busy):
    def save(self, path): Path(path).write_text("state", encoding="utf-8")


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


def test_steps_without_model_calls_list_none(tmp_path, monkeypatch):
    short_settle(monkeypatch)
    no_model = Loop(GridEnv(), StubModel([reply(perception()), act_reply(None)]), Memory(tmp_path / "plain" / "memory"), "grid")
    assert no_model.step()["call_ids"] == []
    model = Counting([])
    unsettled = Loop(Busy(busy=10 ** 9), model, Memory(tmp_path / "pending" / "memory"), "grid")
    row = unsettled.step()
    assert row["observation_status"] == "pending" and row["call_ids"] == [] and model.seq == 0


def test_resolved_pending_row_carries_its_own_step_calls_and_the_original_action_step(tmp_path, monkeypatch):
    from agent import loop as module
    short_settle(monkeypatch)
    per = {"screen": "world", "text": [], "entities": [{"name": "player", "kind": "player", "at": [.5, .5]}]}
    steps = iter([chosen({"buttons": ["RIGHT"]})])
    def perceive(model, *_):
        model.ask("system", "user", role="perceive"); return per, reply(per)
    def act(model, *_args, **_kwargs):
        model.ask("system", "user", role="act"); return next(steps)
    monkeypatch.setattr(module, "perceive", perceive); monkeypatch.setattr(module, "act", act)
    model = Asks()
    loop = Loop(Busy(plan=[20]), model, Memory(tmp_path / "memory"), "grid")
    loop.world.self_spot, loop.world._self_from_motion = [.5, .5], True  # no probe press before the action

    first = loop.step(); first_end = model.seq
    later = []
    while loop.pending is not None and len(later) < 20: later.append(loop.step())

    resolved = later[-1]
    assert first["observation_status"] == "pending" and first["call_ids"] == list(range(1, first_end + 1))
    assert all(r["call_ids"] == [] for r in later[:-1])
    assert resolved["observation_status"] == "settled" and resolved["action_step"] == first["step"]
    assert resolved["call_ids"] == list(range(first_end + 1, model.seq + 1)) and resolved["call_ids"]
    assert not set(resolved["call_ids"]) & set(first["call_ids"])


def test_pending_checkpoint_through_the_runner_writer_resumes_with_one_press(tmp_path, monkeypatch):
    from scripts import run_agent
    short_settle(monkeypatch)
    run = tmp_path / "run"; env = Saving(plan=[20])
    scripted_world(monkeypatch, acts=[chosen({"buttons": ["RIGHT"]})])
    loop = Loop(env, StubModel([]), Memory(run / "memory"), "grid")
    loop.world.self_spot, loop.world._self_from_motion = [.5, .5], True  # no probe press before the action
    first = loop.step()
    assert first["observation_status"] == "pending" and loop.pending is not None and env.keys == ["RIGHT"]

    harness = json.loads((run_agent._checkpoint(env, loop, run) / "harness.json").read_text(encoding="utf-8"))
    assert harness["pending"]["action_step"] == first["step"] and harness["provenance"]["incomplete"] is False
    resumed = Loop(env, StubModel([]), Memory(run / "memory"), "grid"); resumed.restore(harness)
    presses_per_step, later = [], []
    while resumed.pending is not None and len(later) < 20:
        before = len(env.keys); later.append(resumed.step()); presses_per_step.append(len(env.keys) - before)

    resolved = later[-1]
    assert all(n == 0 for n in presses_per_step)  # nothing is pressed before the resolution row, nor in it
    assert resolved["observation_status"] == "settled" and resolved["action_step"] == first["step"] and env.keys == ["RIGHT"]
