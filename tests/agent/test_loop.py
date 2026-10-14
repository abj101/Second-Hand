import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from agent.loop import Loop
from agent.memory import Memory
from agent.space import mark_exit
from tests.agent.fakes import GridEnv, StubModel
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
    phase_exhausted={**data,"total":2675,"stage_b":181}
    path.write_text(json.dumps(phase_exhausted))
    with pytest.raises(run_agent.CallCap): run_agent._ledger(Path("runs/m1"),"b",300)
    assert json.loads(path.read_text()) == phase_exhausted
    m1_exhausted={**data,"m1":2625,"stage_b":181}
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
    assert model.roles[:3] == ["perceiver", "plan", "act"] and "chooser" not in model.roles


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
    assert loop.fails["town|door"] == {"n": 2, "why": "leads back to house: cycle", "step": 14}


def test_second_failure_of_same_action_flags_act_and_replans(tmp_path, monkeypatch):
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
    loop.step(); assert loop.urgent is None
    loop.step(); assert loop.urgent == "the same action failed twice here: approach door (wall)"
    row = loop.step()
    assert not any(f.startswith("failed here before") for f in seen_flags[-1])  # its content is in the option notes now
    assert [o["notes"][0] for o in seen_options[-1] if o["label"] == "possible exit door (unverified, never crossed)"] == ["tried here 2×, last: wall"]
    assert row["plan"]["trigger"] == "the same action failed twice here: approach door (wall)"


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
    assert key("p", "go_to", "Stairs") == key("p", "approach", "stairs") != key("q", "approach", "stairs") and key("p", "advance", "") == "p|advance"
    for mem_id in ("a", "b"): loop.mem.upsert("places", {"id": mem_id, "name": mem_id, "screen": "world", "state": "place", "visits": 2})
    for step, (s, d) in enumerate((("a", "b"), ("b", "a"), ("a", "b"), ("b", "a")), 1): loop._moved(s, d, "UP UP A", step)
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
