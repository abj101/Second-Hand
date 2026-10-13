import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from agent.loop import Loop
from agent.memory import Memory
from tests.agent.fakes import GridEnv, StubModel
from agent.model import ModelUnavailable

def reply(data): return type("Reply", (), {"data": data})()

def act_reply(i=0, expect="screen_changes"):
    return reply({"place": "", "notebook": [], "action": None if i is None else {"i": i}, "text": None, "expect": expect, "why": "test"})
def fake_act(i=0, expect="screen_changes"):
    return lambda *a, **k: SimpleNamespace(place="", ops=[], action=None if i is None else {"i": i}, expect=expect, why="test", text=None)
def perception(*, two=False, press=False):
    affs = ([{"verb":"press","target":"A","at":[500,500]}] if press else
        [{"verb":"approach","target":"player","at":[500,500]}] * (2 if two else 1))
    return {"screen":"room","text":[],"entities":[{"kind":"player","name":"Hand","at":[500,500]}],"affordances":affs}


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
        def ask(self, *args, **kwargs): self.calls += 1; return self.replies[kwargs["role"]].popleft()
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

def test_stationary_two_fresh_frames_confirm_drift(tmp_path, monkeypatch):
    from agent import loop as module
    env = GridEnv(); env.phase = 0; original = env.screenshot
    def screenshot():
        frame = original(); frame.putpixel((0, 0), (env.phase, 0, 0)); return frame
    env.screenshot = screenshot; offset = [0.6]
    def perceive(*_):
        return {"screen":"world","text":[],"affordances":[{"verb":"approach","target":"lamp","at":[offset[0],.5]}],"entities":[{"name":"player","kind":"player","at":[.5,.5]}, {"name":"lamp","kind":"object","at":[offset[0],.5]}]}, reply({"screen":"world","text":[],"entities":[],"affordances":[]})
    monkeypatch.setattr(module, "perceive", perceive)
    monkeypatch.setattr(module, "act", fake_act(0))
    def still(*a,**k):
        before=env.screenshot(); env.phase+=1; after=env.screenshot()
        return SimpleNamespace(status="done",reason="",inputs=0,calls=0,before=before,after=after,scroll=(0,0),blank_seen=False,moves=[])
    monkeypatch.setattr(module, "run_affordance", still)
    loop = Loop(env, StubModel([]), Memory(tmp_path/"memory"), "grid")
    loop.world.self_spot, loop.world._self_from_motion = [.5, .5], True
    loop.step(); offset[0] = .75; first = loop.step()
    assert loop.pos == (0, 0) and first["drift_fix"] is None and loop.world.drift == (-1, 0)
    second = loop.step()
    assert loop.pos == (-1, 0) and second["drift_fix"] == [-1, 0]


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
    monkeypatch.setattr(module, "act", fake_act(0))
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
                "entities":[{"name":"player","kind":"player","at":[.5,.5]}, {"name":floor,"kind":"object","at":[.7,.5]}]}, reply({})
    monkeypatch.setattr(module, "perceive", perceive)
    monkeypatch.setattr(module, "act", fake_act(0, "place_changes"))
    loop=Loop(env,StubModel([]),Memory(tmp_path/"memory"),"grid"); ids=[]
    for _ in range(4): ids.append(loop.step()["next_place"])
    assert ids[0] == ids[2] and ids[1] == ids[3] and ids[0] != ids[1]

def test_perceiver_guides_whole_name_entry(tmp_path):
    class CapturesPrompt(StubModel):
        def ask(self, *args, **kwargs): self.prompt=args[1]; return super().ask(*args, **kwargs)
    model=CapturesPrompt([reply(perception())]); loop=Loop(GridEnv(),model,Memory(tmp_path/"memory"),"grid")
    loop._see(loop.env.screenshot(),None)
    assert "visible editable text field with its on-screen keyboard" in model.prompt
    assert "dialogue questions alone are advance" in model.prompt and "preset name menus are select" in model.prompt
    assert "A visible modal dialogue or selection menu blocks world movement" in model.prompt
    assert "Previous-place memory is reference only" in model.prompt
    assert 'Name every visible passage "exit: <what it is>"' in model.prompt
    assert loop.prompt_version == "e5-perceiver-8"

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
    model = StubModel([reply(start), act_reply(0), reply(destination)])
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
    model = StubModel([reply(start), act_reply(0), reply(destination)])
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

def test_explicit_transition_into_dialogue_creates_place(tmp_path, monkeypatch):
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
    assert row["scene_cut"] and row["next_place"] != row["place"]

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
    assert row["scene_cut"] and row["next_place"] != row["place"]
    assert ("place_changes" in row["outcome"]["effects"]) == expect
    if expect: assert row["outcome"]["to"] == row["next_place"]

def test_blocked_exit_overrides_pixel_prediction_and_cannot_hit(tmp_path, monkeypatch):
    from agent import loop as loop_module
    env = GridEnv()
    start = perception(); start["screen"] = "world"
    start["affordances"] = [{"verb": "approach", "target": "exit: door mat", "at": [0.3, 0.9]}]
    model = StubModel([reply(start), act_reply(0), reply(start)])
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
    loop=Loop(env,FailsAfterChoice([reply(perception(press=True)),act_reply(0,"place_changes")]),mem,"grid")
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
        start = perception(two=True)
        for aff in start["affordances"]: aff["verb"] = verb
        destination = perception(); destination["screen"] = "other room"
        destination["entities"] += [{"kind":"object", "name":"new landmark", "at":[.2,.2]}, {"kind":"exit", "name":"exit: door", "at":[500,500]}]
        if verb == "interact": destination["text"] = ["Visible dialogue"]
        model = StubModel([reply(start), act_reply(0, "place_changes"), reply(destination)])
        mem = Memory(root/"memory"); loop = Loop(env, model, mem, "grid")

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
    model=StubModel([reply(perception(press=True)),act_reply(0)]); model._post=lambda p: None
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
    model=Posts([reply(perception(press=True)),act_reply(0)]); model._post=lambda p:None
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
    phase_exhausted={**data,"total":1100,"stage_b":181}
    path.write_text(json.dumps(phase_exhausted))
    with pytest.raises(run_agent.CallCap): run_agent._ledger(Path("runs/m1"),"b",300)
    assert json.loads(path.read_text()) == phase_exhausted
    m1_exhausted={**data,"m1":1050,"stage_b":181}
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

def test_act_names_place_edits_notebook_and_skips_planner(tmp_path):
    class Roles(StubModel):
        def __init__(self, replies): super().__init__(replies); self.roles = []
        def ask(self, *args, **kwargs): self.roles.append(kwargs.get("role")); return super().ask(*args, **kwargs)
    act_reply = reply({"place": "Hall", "notebook": [{"op": "set", "section": "goals", "key": "g1", "value": {"text": "leave", "source": "assumed"}}],
                       "action": {"i": 0}, "text": None, "expect": "screen_changes", "why": "try"})
    model = Roles([reply(perception()), act_reply, reply(perception()), reply(perception())])
    loop = Loop(GridEnv(), model, Memory(tmp_path / "memory"), "grid")

    row = loop.step()

    assert row["act"]["ops"] == 1 and row["act"]["why"] == "try"
    assert json.loads((tmp_path / "memory" / "notebook.json").read_text())["goals"]["g1"]["text"] == "leave"
    assert loop.mem.get("places", row["place"])["name"] == "Hall"
    assert model.roles[:2] == ["perceiver", "act"] and "plan" not in model.roles and "chooser" not in model.roles


def test_act_prompt_summarizes_recent_repeated_blocked_action(tmp_path, monkeypatch):
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
    response = {"place": "Room", "notebook": [], "action": {"i": 0}, "text": None,
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
    response = {"place": "Room", "notebook": [], "action": {"i": 0}, "text": None,
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
    act_reply = reply({"place": "Hall", "notebook": [], "action": {"i": 9}, "text": None, "expect": "screen_changes", "why": "?"})
    loop = Loop(GridEnv(), StubModel([reply(perception()), act_reply, reply(perception()), reply(perception())]), Memory(tmp_path / "memory"), "grid")

    row = loop.step()

    assert row["choice"] is None and row["act"]["action"] is None and row["verdict"] == "unobservable"
