import pytest
from PIL import ImageDraw

from agent.model import Reply
from agent.skills import Ctx, approach, interact, press_seq, run_affordance, select
from tests.agent.fakes import GridEnv, StubModel


def point(env, at):
    return [.5 + (at[0] - env.player[0]) * 16 / 160,
            .5 + (at[1] - env.player[1]) * 16 / 144]


def ctx(env, tile_px=16, max_inputs=30, replies=None):
    return Ctx(env, StubModel(replies or []), tile_px=tile_px, max_inputs=max_inputs, self_spot=(.5, .5))


class RecordingEnv(GridEnv):
    def __init__(self):
        super().__init__(); self.keys = []

    def press(self, key, frames=8, on_frame=None):
        self.keys.append(key); super().press(key, frames, on_frame)


class MenuEnv(GridEnv):
    items = ["one", "two", "OPTION"]

    def __init__(self):
        super().__init__(); self.cursor = 0; self.selected = None

    def press(self, key, frames=8, on_frame=None):
        if key == "DOWN": self.cursor += 1
        elif key == "UP": self.cursor -= 1
        elif key == "A": self.selected = self.items[self.cursor]


def test_approach_reaches_target_three_tiles_away():
    env = GridEnv(); env.door = (11, 8); env.npc = (-5, -5)
    result = approach(ctx(env), point(env, (11, 8)), [.5, .5])
    assert result.status == "done" and result.reason == "scene cut"
    assert result.inputs == 3 and env.scene == 1


def test_approach_routes_around_wall():
    env = GridEnv(); env.walls.add((9, 8)); env.door = (-5, -5)
    result = approach(ctx(env), point(env, (10, 8)), [.5, .5])
    assert result.status == "done" and env.player in {(10, 8), (10, 7), (10, 9)}
    assert result.inputs > 2


def test_approach_keeps_sidestep_past_target_column():
    env = GridEnv(); env.player = (4, 3); env.door = (-5, -5); env.npc = (-5, -5)
    env.walls.update({(3, 4), (4, 4), (3, 5), (4, 5), (3, 6), (4, 6), (4, 7)})
    result = approach(ctx(env, max_inputs=12), point(env, (3, 7)), [.5, .5])
    assert result.status == "done" and env.player == (3, 7)
    assert result.inputs <= 12


def test_approach_is_bounded_when_boxed_in():
    env = GridEnv(); env.walls.update({(9, 8), (7, 8), (8, 9), (8, 7)})
    result = approach(ctx(env, max_inputs=5), point(env, (11, 8)), [.5, .5])
    assert result.status == "blocked" and result.inputs <= 5


def test_approach_does_not_treat_all_uncertain_scroll_as_blocked(monkeypatch):
    import agent.skills as skills
    monkeypatch.setattr(skills, "scroll", lambda *args, **kwargs: None)
    env = GridEnv()
    result = approach(ctx(env), point(env, (10, 8)), [.5, .5])
    assert result.status == "failed" and result.reason == "scroll unsure"
    assert result.inputs == 1


def test_approach_detects_fade_when_scroll_is_uncertain(monkeypatch):
    import agent.skills as skills
    monkeypatch.setattr(skills, "scroll", lambda *args, **kwargs: None)
    env = GridEnv()
    env.player = (9, 8)
    result = approach(ctx(env), point(env, env.door), [.5, .5])
    assert result.status == "done" and result.reason == "scene cut"
    assert result.blank_seen and env.scene == 1


def test_approach_through_door_reports_cut_and_blank():
    env = GridEnv()
    result = approach(ctx(env), point(env, env.door), [.5, .5])
    assert result.status == "done" and result.reason == "scene cut"
    assert result.blank_seen and env.scene == 1


def test_interact_route_walks_beside_target_faces_and_presses_a(tmp_path):
    import json
    env = RecordingEnv()
    route = {"dirs": ["RIGHT", "DOWN", "LEFT"]}
    result = interact(ctx(env), point(env, (8, 7)), [.5, .5],
                      route=route)
    row = {"step": 1, "verb": "interact", "route_len": len(route["dirs"]), "keys": env.keys}
    with (tmp_path / "presses.jsonl").open("a", encoding="utf8") as f:
        f.write(json.dumps(row) + "\n")
    assert result.inputs == 4
    assert env.keys == route["dirs"] + ["A"]
    assert len(env.keys) <= 2 * row["route_len"] + 2


def test_interact_without_route_is_refused_without_inputs():
    env = RecordingEnv()
    result = interact(ctx(env), point(env, (10, 8)), [.5, .5], route=None)
    assert result.status == "blocked" and result.reason == "no route beside target"
    assert result.inputs == 0 and result.failure == {"stage": "route", "reason": "no route beside target"}
    assert env.keys == []


def test_approach_pushes_once_from_door_mat():
    env = GridEnv(); env.player = (8, 8); env.door = (10, 8); env.npc = (-5, -5)
    result = approach(ctx(env), point(env, (9, 8)), [.5, .5])
    assert result.status == "done" and result.reason == "scene cut"
    assert result.inputs == 2 and env.scene == 1


def test_exit_walks_rough_point_and_pushes_without_model_call():
    env = GridEnv(); env.door = (10, 8); env.npc = (-5, -5)
    result = run_affordance(ctx(env),
                            {"verb": "approach", "target": "exit down", "at": point(env, (9, 8))},
                            {"entities": [{"kind": "player", "at": [.5, .5]}]})
    assert result.status == "done" and result.reason == "scene cut"
    assert result.calls == 0 and env.scene == 1


def test_exit_mode_pushes_non_exit_named_target():
    env = GridEnv(); env.door = (10, 8); env.npc = (-5, -5)
    model = StubModel([])
    result = run_affordance(Ctx(env, model),
        {"verb": "approach", "target": "floor patch", "at": point(env, (9, 8))},
        {"entities": [{"kind": "player", "at": [.5, .5]}]}, exit=True)
    assert result.status == "done" and result.reason == "scene cut"
    assert result.inputs == 2 and result.calls == model.calls == 0 and env.scene == 1


def test_exit_without_scene_cut_is_blocked():
    env = GridEnv(); env.door = (-5, -5); env.npc = (-6, -6)
    result = run_affordance(ctx(env),
                            {"verb": "approach", "target": "exit down", "at": point(env, (10, 8))},
                            {"entities": [{"kind": "player", "at": [.5, .5]}]})
    assert result.status == "blocked" and result.reason == "exit not crossed"


def test_route_presses_directions_in_order_and_records_moves():
    env = RecordingEnv(); dirs = ["UP", "RIGHT"]
    result = run_affordance(ctx(env), {"verb": "approach", "at": point(env, (10, 6))},
                            {"entities": [{"kind": "player", "at": [.5, .5]}]}, route={"dirs": dirs})
    assert env.keys == dirs and [move["dir"] for move in result.moves] == dirs
    assert result.status == "done"


def test_route_confident_zero_shift_is_blocked():
    env = RecordingEnv(); env.walls.add((9, 8))
    result = run_affordance(ctx(env), {"verb": "approach", "at": point(env, (10, 8))},
                            {"entities": [{"kind": "player", "at": [.5, .5]}]}, route={"dirs": ["RIGHT"]})
    assert result.status == "blocked" and result.reason == "route blocked"


ROUTE_DIRS = [("RIGHT", 1, 0), ("LEFT", -1, 0), ("UP", 0, -1), ("DOWN", 0, 1)]


@pytest.mark.parametrize("key, dx, dy", ROUTE_DIRS)
def test_route_obstruction_mid_route_reports_route_failure(key, dx, dy):
    env = RecordingEnv(); env.door, env.npc = (-5, -5), (-6, -6); env.walls.add((8 + 3 * dx, 8 + 3 * dy))
    result = run_affordance(ctx(env), {"verb": "approach", "at": point(env, (10, 8))},
                            {"entities": [{"kind": "player", "at": [.5, .5]}]}, route={"dirs": [key] * 4})
    assert result.status == "blocked" and result.reason == "route blocked"
    assert result.inputs == 3 and env.keys == [key] * 3
    assert result.failure == {"stage": "route", "reason": "route blocked", "direction": key}


@pytest.mark.parametrize("key, dx, dy", ROUTE_DIRS)
def test_clean_route_has_no_failure(key, dx, dy):
    env = RecordingEnv(); env.door, env.npc = (-5, -5), (-6, -6)
    result = run_affordance(ctx(env), {"verb": "approach", "at": point(env, (10, 8))},
                            {"entities": [{"kind": "player", "at": [.5, .5]}]}, route={"dirs": [key] * 2})
    assert result.status == "done" and result.failure is None


def test_skill_result_failure_is_optional_for_positional_construction():
    from PIL import Image
    from agent.skills import SkillResult

    frame = Image.new("RGB", (8, 8))
    assert SkillResult("done", "", 0, 0, frame, frame, (0, 0), False).failure is None
    assert SkillResult("blocked", "x", 1, 0, frame, frame, (0, 0), False, [], None).failure is None


def test_exit_not_crossed_reports_crossing_failure():
    env = GridEnv(); env.door = (-5, -5); env.npc = (-6, -6)
    result = run_affordance(ctx(env), {"verb": "approach", "target": "exit down", "at": point(env, (10, 8))},
                            {"entities": [{"kind": "player", "at": [.5, .5]}]})
    assert result.status == "blocked" and result.reason == "exit not crossed"
    assert result.failure == {"stage": "crossing", "reason": "exit not crossed"}


def test_exit_route_pushes_once():
    env = RecordingEnv(); env.player = (9, 8); env.door = (10, 8); env.npc = (-5, -5)
    result = run_affordance(ctx(env), {"verb": "approach", "at": point(env, (10, 8))},
                            {"entities": [{"kind": "player", "at": [.5, .5]}]}, exit=True,
                            route={"dirs": [], "push": "RIGHT"})
    assert result.status == "done" and env.keys == ["RIGHT"]
    assert result.inputs == 1 and result.moves[0]["dir"] == "RIGHT"


def test_onto_approach_does_not_set_exit_target(monkeypatch):
    from agent import skills as module
    seen = {}
    frame = GridEnv().screenshot()
    monkeypatch.setattr(module, "approach", lambda ctx, at, player, **kw: seen.update(kw) or module.SkillResult("done", "", 0, 0, frame, frame, (0, 0), False))
    result = run_affordance(ctx(GridEnv()), {"verb": "approach", "target": "exit down", "at": point(GridEnv(), (10, 8))},
                            {"entities": [{"kind": "player", "at": [.5, .5]}]}, onto=True)
    assert seen == {"exit_target": None, "route": None} and result.status == "done"


def test_blocked_exit_route_push_is_one_recorded_press():
    env = RecordingEnv(); env.player = (9, 8); env.walls.add((10, 8)); env.door = (-5, -5); env.npc = (-6, -6)
    result = run_affordance(ctx(env), {"verb": "approach", "at": point(env, (10, 8))},
                            {"entities": [{"kind": "player", "at": [.5, .5]}]}, exit=True,
                            route={"dirs": ["UP", "DOWN"], "push": "RIGHT"})
    assert result.status == "blocked" and result.reason == "exit not crossed"
    assert result.inputs == 3 and env.keys == ["UP", "DOWN", "RIGHT"]
    assert result.moves[-1] == {"dir": "RIGHT", "shift": (0, 0), "self_at": None}


def test_interact_and_directional_press_preserve_move_records():
    env = GridEnv()
    result = interact(ctx(env), point(env, env.npc), [.5, .5], route={"dirs": ["LEFT"]})
    assert result.moves and all(move["dir"] in {"UP", "DOWN", "LEFT", "RIGHT"} and set(move) == {"dir", "shift", "self_at"} for move in result.moves)
    env = GridEnv(); env.player = (9, 8)
    result = run_affordance(ctx(env), {"verb": "press", "target": "RIGHT"}, {"entities": []})
    assert env.scene == 1 and result.moves[-1]["dir"] == "RIGHT"
    assert result.self_at == result.moves[-1]["self_at"]


def test_skill_result_carries_last_non_none_self_at(monkeypatch):
    import agent.skills as skills

    spots = iter(((.4, .5), (.6, .5), None))
    def step(ctx, direction, before):
        return type("End", (), {"frame": before, "blank_seen": False})(), (-16, 0), False, next(spots)
    monkeypatch.setattr(skills, "_step", step)
    result = approach(ctx(RecordingEnv()), [.8, .5], [.5, .5], route={"dirs": ["RIGHT", "LEFT", "UP"]})
    assert result.self_at == (.6, .5)
    assert [move["self_at"] for move in result.moves] == [(.4, .5), (.6, .5), None]


def test_press_seq_presses_in_order_and_stops_at_blocked_key():
    env = RecordingEnv()
    result = press_seq(ctx(env), ["UP", "RIGHT", "NOPE", "A"])
    assert env.keys == ["UP", "RIGHT"] and result.status == "blocked"
    assert result.reason == "unsupported input" and result.inputs == 2


def test_exit_blocked_by_walls_reports_not_crossed():
    env = GridEnv(); env.walls.update({(8, 7), (8, 9), (7, 8), (9, 8)})
    result = run_affordance(ctx(env),
                            {"verb": "approach", "target": "exit down", "at": point(env, (11, 11))},
                            {"entities": [{"kind": "player", "at": [.5, .5]}]})
    assert result.status == "blocked" and result.reason == "exit not crossed"


def test_approach_at_player_is_blocked_without_pressing():
    for tile_px in (16, None):
        env = GridEnv()
        result = approach(ctx(env, tile_px=tile_px), point(env, env.player), [.5, .5])
        assert result.status == "blocked" and result.reason == "target is the player" and result.inputs == 0


def test_interact_opens_text_box():
    env = GridEnv()
    result = interact(ctx(env), point(env, env.npc), [.5, .5], route={"dirs": ["LEFT"]})
    assert result.status == "done" and env.text_box


def test_interact_ignores_actor_motion_between_a_press_frames(monkeypatch):
    import agent.skills as skills
    from types import SimpleNamespace
    from PIL import Image

    before = Image.new("RGB", (16, 16)); before.paste((255, 0, 0), (4, 4, 8, 8))
    after = before.copy(); after.paste((0, 0, 0), (4, 4, 8, 8)); after.paste((255, 0, 0), (8, 8, 12, 12))
    frames = iter((before, before, after))
    monkeypatch.setattr(skills, "wait_settled", lambda _env, **_kw: SimpleNamespace(frame=next(frames), settled=True, blank_seen=False))
    boxes = iter((((4, 4, 8, 8),), ((8, 8, 12, 12),)))
    c = ctx(RecordingEnv(), tile_px=None); c.actor_boxes = lambda: next(boxes)
    monkeypatch.setattr(skills, "_step", lambda _ctx, _direction, old: (SimpleNamespace(frame=old, blank_seen=False), (0, 0), False, None))

    result = interact(c, [.6, .5], [.5, .5], route={"dirs": ["RIGHT"]})

    assert result.status == "failed" and result.reason == "nothing changed"


def test_interact_keeps_dialogue_text_change_after_actor_mask(monkeypatch):
    import agent.skills as skills
    from types import SimpleNamespace
    from PIL import Image

    before = Image.new("RGB", (16, 16)); after = before.copy()
    after.paste((255, 255, 255), (1, 10, 15, 15))
    frames = iter((before, before, after))
    monkeypatch.setattr(skills, "wait_settled", lambda _env, **_kw: SimpleNamespace(frame=next(frames), settled=True, blank_seen=False))
    boxes = iter((((4, 4, 8, 8),), ((8, 8, 12, 12),)))
    c = ctx(RecordingEnv(), tile_px=None); c.actor_boxes = lambda: next(boxes)
    monkeypatch.setattr(skills, "_step", lambda _ctx, _direction, old: (SimpleNamespace(frame=old, blank_seen=False), (0, 0), False, None))

    result = interact(c, [.6, .5], [.5, .5], route={"dirs": ["RIGHT"]})

    assert result.status == "done" and result.reason == ""


def test_interact_does_not_count_face_step_as_a_press_effect(monkeypatch):
    import agent.skills as skills
    from types import SimpleNamespace
    from PIL import Image

    initial = Image.new("RGB", (16, 16)); faced = initial.copy()
    faced.paste((255, 255, 0), (7, 7, 9, 9))
    frames = iter((initial, initial, faced))
    monkeypatch.setattr(skills, "wait_settled", lambda _env, **_kw: SimpleNamespace(frame=next(frames), settled=True, blank_seen=False))
    c = ctx(RecordingEnv(), tile_px=None)
    monkeypatch.setattr(skills, "_step", lambda _ctx, _direction, _old: (SimpleNamespace(frame=faced, blank_seen=False), (0, 0), False, None))

    result = interact(c, [.6, .5], [.5, .5], route={"dirs": ["RIGHT"]})

    assert result.status == "failed" and result.reason == "nothing changed"


def test_interact_requires_quarter_tile_of_unmasked_pixels(monkeypatch):
    import agent.skills as skills
    from types import SimpleNamespace
    from PIL import Image

    before = Image.new("RGB", (32, 32)); after = before.copy()
    after.paste((255, 0, 0), (28, 0, 32, 3))
    frames = iter((before, before, after))
    monkeypatch.setattr(skills, "wait_settled", lambda _env, **_kw: SimpleNamespace(frame=next(frames), settled=True, blank_seen=False))
    boxes = ((-20, -20, 28, 28), (16, 16, 40, 40))
    c = ctx(RecordingEnv(), tile_px=8); c.actor_boxes = lambda: boxes
    monkeypatch.setattr(skills, "_step", lambda _ctx, _direction, old: (SimpleNamespace(frame=old, blank_seen=False), (0, 0), False, None))

    result = interact(c, [.6, .5], [.5, .5], route={"dirs": ["RIGHT"]})

    assert result.status == "failed" and result.reason == "nothing changed"


def test_interact_uses_included_pixel_count_for_overlapping_clipped_masks(monkeypatch):
    import agent.skills as skills
    from types import SimpleNamespace
    from PIL import Image

    before = Image.new("RGB", (40, 40)); after = before.copy()
    after.paste((255, 0, 0), (24, 0, 40, 16))
    frames = iter((before, before, after))
    monkeypatch.setattr(skills, "wait_settled", lambda _env, **_kw: SimpleNamespace(frame=next(frames), settled=True, blank_seen=False))
    boxes = ((-20, -20, 24, 24), (16, 16, 50, 50))
    c = ctx(RecordingEnv(), tile_px=16); c.actor_boxes = lambda: boxes
    monkeypatch.setattr(skills, "_step", lambda _ctx, _direction, old: (SimpleNamespace(frame=old, blank_seen=False), (0, 0), False, None))

    result = interact(c, [.6, .5], [.5, .5], route={"dirs": ["RIGHT"]})

    assert result.status == "done" and result.reason == ""


def test_select_uses_one_model_call_then_moves_and_confirms():
    env = MenuEnv()
    model = StubModel([Reply("", {"items": env.items, "cursor": 0,
                               "layout": "vertical"}, 0, None, None)])
    result = select(Ctx(env, model, max_inputs=5), "option")
    assert result.status == "done" and result.inputs == 3
    assert env.cursor == 2 and env.selected == "OPTION"
    assert result.calls == model.calls == 1
def test_step_remeasures_entire_move_when_a_frame_shift_is_uncertain(monkeypatch):
    import agent.skills as skills

    calls = []

    def measured(a, b, **kwargs):
        calls.append(kwargs)
        return (1, 0) if len(calls) == 1 else None if not kwargs else (8, 0)

    monkeypatch.setattr(skills, "scroll", measured)
    env = GridEnv()
    _, move, _, self_at = skills._step(ctx(env), "RIGHT", env.screenshot())

    assert move == (8, 0)
    assert self_at is None
    assert calls[-1] == {"max_shift": 16}


def test_step_keeps_position_unknown_when_whole_move_is_uncertain(monkeypatch):
    import agent.skills as skills

    monkeypatch.setattr(skills, "scroll", lambda *args, **kwargs: None)
    env = GridEnv()
    _, move, _, self_at = skills._step(ctx(env), "RIGHT", env.screenshot())

    assert move is None
    assert self_at is None


def test_type_text_accepts_cell_rows_without_done_key_and_never_erases_on_empty_text():
    from types import SimpleNamespace as NS
    from agent.skills import Ctx, type_text
    from tests.agent.fakes import GridEnv
    env = GridEnv(); pressed = []; env.press = lambda key, frames=8, on_frame=None: pressed.append(key)
    replies = [NS(data={"layout": [["A", "B"], ["C", "Ed"]], "cursor": [0, 0], "done_key": None, "presets": []}), NS(data={"name": "CAB"})]
    class Model:
        calls = 0
        def ask(self, *a, **k): return replies.pop(0)
    assert type_text(Ctx(env, Model(), tile_px=16), "").reason == "no text given to type" and pressed == []
    result = type_text(Ctx(env, Model(), tile_px=16), "CAB")
    assert result.status == "done" and pressed[-1] == "A" and pressed.count("A") == 4  # C, A, B, then Ed found by name


@pytest.mark.parametrize("pointer", [True, False])
def test_select_on_dialogue_with_only_unflagged_furniture_is_refused_without_a_model_call_or_press(pointer):
    env = MenuEnv(); env.pointer = pointer; presses = []
    real_press = env.press
    env.press = lambda key, frames=8, on_frame=None: presses.append(key) or real_press(key, frames, on_frame)
    model = StubModel([])
    perception = {"screen": "dialogue", "entities": [{"name": "television", "kind": "furniture", "at": [.2, .3]}, {"name": "dialogue_box", "kind": "dialogue_box", "at": [.5, .9]}]}
    result = run_affordance(Ctx(env, model), {"verb": "select", "target": "television"}, perception)
    assert result.status == "failed" and result.reason == "not a selectable choice on this screen"
    assert result.failure == {"stage": "interaction", "reason": "not a selectable choice on this screen"}
    assert model.calls == 0 and presses == [] and (result.inputs, result.calls) == (0, 0)


@pytest.mark.parametrize("pointer", [True, False])
def test_select_on_flagged_menu_item_reads_menu_once_and_presses_bounded(pointer):
    env = MenuEnv(); env.pointer = pointer
    model = StubModel([Reply("", {"items": env.items, "cursor": 0, "layout": "vertical"}, 0, None, None)])
    perception = {"screen": "menu", "entities": [{"name": "OPTION", "kind": "menu_item", "at": [.3, .6], "selectable": True}]}
    result = run_affordance(Ctx(env, model, max_inputs=5), {"verb": "select", "target": "option"}, perception)
    assert result.status == "done" and result.inputs == 3
    assert env.cursor == 2 and env.selected == "OPTION"
    assert result.calls == model.calls == 1


class Busy(GridEnv):
    """A screen that keeps changing after a press: plan lists, per press, the ticks the screen keeps changing for (0 = still); busy=N starts it changing.
    A changing screen never gives two equal frames in a row, so a settle times out there."""
    def __init__(self, plan=(), *, busy: float = 0) -> None:
        super().__init__(); self.plan, self.busy, self.serial, self.ticks, self.keys = iter(plan), busy, 0, 0, []
    def press(self, key, frames=8, on_frame=None):
        self.keys.append(key); super().press(key, frames, on_frame); self.busy = next(self.plan, 0)
    def tick(self, frames=1):
        for _ in range(frames):
            super().tick(); self.ticks += 1
            if self.busy > 0: self.busy -= 1; self.serial += 1
    def screenshot(self):
        image = super().screenshot()
        if self.serial: ImageDraw.Draw(image).rectangle((0, 0, 3, 3), fill=(self.serial % 251, self.serial // 251 % 251, 1))
        return image


def short_settle(monkeypatch):
    """A settle needs two still frames and gives up after six ticks, so a test can force either outcome cheaply."""
    from agent import observer
    monkeypatch.setitem(observer.wait_settled.__kwdefaults__, "quiet", 2)
    monkeypatch.setitem(observer.wait_settled.__kwdefaults__, "max_frames", 6)


def test_settled_flag_ands_every_settle_inside_a_skill_sequence(monkeypatch):
    from agent.skills import Ctx, press, press_seq, wait_until
    short_settle(monkeypatch)
    for plan, expect in (([0, 0], True), ([0, 20], False), ([20, 0], False)):
        assert press_seq(Ctx(Busy(plan), StubModel([]), 16), ["RIGHT", "RIGHT"]).settled is expect, plan
    assert press(Ctx(Busy([20]), StubModel([]), 16), "RIGHT").settled is False
    assert wait_until(Ctx(Busy(busy=20), StubModel([]), 16)).settled is False
    assert wait_until(Ctx(Busy(), StubModel([]), 16)).settled is True


def test_travel_settled_ands_its_legs_and_folds_each_move_once(tmp_path, monkeypatch):
    from agent.memory import Memory
    from agent.skills import Ctx
    from agent.world import World
    short_settle(monkeypatch)

    def travel(name, plan):
        mem = Memory(tmp_path / name)
        mem.upsert("places", {"id": "room", "name": "room", "screen": "world", "state": "place", "first": 1, "last": 1, "edges": [], "visits": 1})
        env, world = Busy(plan), World(mem, 16)
        world.current_place, world.pa = "room", [.5, .5]
        legs = [{"place": "room", "dirs": ["RIGHT"], "push": None}, {"place": "room", "dirs": ["RIGHT"], "push": None}]
        return world.travel(Ctx(env, StubModel([]), 16), legs, 1), world, env

    still, still_world, _ = travel("still", [0, 0])
    first_late, first_world, _ = travel("first_late", [20, 0])
    second_late, second_world, _ = travel("second_late", [0, 20])
    assert still.settled is True and first_late.settled is False and second_late.settled is False
    assert still.inputs == first_late.inputs == second_late.inputs == 2
    assert still.reason == first_late.reason == second_late.reason
    assert still_world.pos == first_world.pos == second_world.pos and len(still.moves) == len(second_late.moves) == 2


def test_travel_across_a_scene_cut_counts_its_crossing_once_and_reports_unsettled(tmp_path, monkeypatch):
    from agent.memory import Memory
    from agent.skills import Ctx
    from agent.world import World
    short_settle(monkeypatch)

    def travel(name, plan):
        mem = Memory(tmp_path / name)
        for id_, label in (('source', 'Source'), ('expected', 'Expected')):
            mem.upsert('places', {'id': id_, 'name': label, 'screen': 'world', 'state': 'place', 'first': 1, 'last': 1, 'edges': [], 'visits': 1})
        mem.upsert('tiles', {'id': 'source@2,0', 'place': 'source', 'x': 2, 'y': 0, 'floor': True, 'blocked': [], 'pushed': [], 'step': 1,
                             'exit': {'dir': 'RIGHT', 'to': 'expected', 'arrive': [0, 0]}})
        env = Busy(plan); env.door = (10, 8)  # the second step is onto the door: the screen cuts
        world = World(mem, 16); world.current_place, world.pa = 'source', [.5, .5]
        legs = [{'place': 'source', 'dirs': ['RIGHT', 'RIGHT'], 'push': 'RIGHT', 'expect': 'expected', 'exit_tile': (2, 0)}]
        return world.travel(Ctx(env, StubModel([]), 16), legs, 1), world, mem

    still, still_world, still_mem = travel('still', [0, 0])
    late, late_world, late_mem = travel('late', [0, 20])
    crossings = lambda mem: sorted((c['id'], c['n']) for c in mem.rows('crossings'))
    assert still.settled is True and late.settled is False
    assert still.reason == late.reason and still.inputs == late.inputs == 2
    assert still_world.current_place == late_world.current_place == 'expected' and still_world.pos == late_world.pos
    assert crossings(still_mem) == crossings(late_mem) and crossings(late_mem) and all(n == 1 for _, n in crossings(late_mem))


def test_non_direction_press_folds_forced_motion(tmp_path, monkeypatch):
    from agent.observer import Settle
    from agent.skills import press
    from agent.memory import Memory
    from agent.space import fold
    env = GridEnv(); frame = env.screenshot()
    settles = iter([Settle(frame, True, 0, False), Settle(frame, True, 3, False, scroll=(-48, 0), last_dir="RIGHT")])
    monkeypatch.setattr("agent.skills._settled", lambda *_: next(settles))
    result = press(ctx(env), "A")
    assert result.moves == [{"dir": "RIGHT", "shift": (-48, 0), "forced": True}]
    assert fold(Memory(tmp_path), "room", (0, 0), result.moves, 16, 1) == (3, 0)


def test_press_sequence_keeps_following_moves_after_cut(monkeypatch):
    from agent.skills import SkillResult
    from agent import skills
    env = GridEnv(); frame = env.screenshot()
    results = iter([SkillResult("done", "", 1, 0, frame, frame, (0, 0), True, [{"dir": "DOWN", "shift": (0, -16), "forced": True}], arrival_frame=frame), SkillResult("done", "", 1, 0, frame, frame, (0, 0), False, [{"dir": "UP", "shift": (0, 16)}])])
    monkeypatch.setattr(skills, "press", lambda *_: next(results))
    result = press_seq(ctx(env), ["A", "UP"])
    assert not result.moves[0].get("after_cut") and result.moves[1]["after_cut"]


@pytest.mark.parametrize("animated", [False, True])
def test_no_response_is_not_an_animated_bump(tmp_path, monkeypatch, animated):
    from agent import skills
    from agent.memory import Memory
    from agent.space import fold
    class Bump(GridEnv):
        def press(self, key, frames=8, on_frame=None):
            if animated and on_frame:
                frame = self.screenshot(); frame.putpixel((0, 0), (255, 0, 0)); on_frame(frame)
    env = Bump(); monkeypatch.setattr(skills, "scroll", lambda *a, **k: (0, 0))
    result = skills.press(ctx(env), "RIGHT"); move = result.moves[0]
    assert move["shift"] is None and move.get("no_response") is True
    mem = Memory(tmp_path); assert fold(mem, "room", (0, 0), result.moves, 16, 1) == (0, 0)
    assert (mem.get("tiles", "room@0,0") or {}).get("blocked", []) == []


@pytest.mark.parametrize("pixels,actors,bump", [([(72, 65)], (), True), ([(145, 130)], (), False), ([(72, 49)], (), False), ([(72, 65), (88, 65)], ((80, 64, 96, 80),), True)])
def test_wall_needs_player_tile_reaction(monkeypatch, pixels, actors, bump):
    from PIL import Image
    from agent import skills
    from agent.observer import Settle
    before = Image.new("RGB", (160, 144), "white"); animated = before.copy()
    for pixel in pixels: animated.putpixel(pixel, (0, 0, 0))
    class Env:
        def press(self, direction, frames, on_frame): on_frame(animated)
    c = Ctx(Env(), StubModel([]), tile_px=16, self_spot=(.45, .451), actor_boxes=lambda: actors)
    monkeypatch.setattr(skills, "scroll", lambda *a, **kw: (0, 0))
    monkeypatch.setattr(skills, "_settled", lambda *_: Settle(before, True, 0, False))
    end, move, _, _ = skills._step(c, "RIGHT", before)
    assert move == ((0, 0) if bump else None) and bool(getattr(end, "no_response", False)) == (not bump)


@pytest.mark.parametrize("routed", [True, False])
def test_walk_stops_at_first_no_response(tmp_path, monkeypatch, routed):
    from types import SimpleNamespace
    from agent import skills
    from agent.memory import Memory
    from agent.space import fold
    env = RecordingEnv(); frame = env.screenshot(); keys = []
    def step(c, direction, before):
        keys.append(direction); frozen = len(keys) == 2
        return SimpleNamespace(frame=frame, blank_seen=False, no_response=frozen), None if frozen else (-16, 0), False, None
    monkeypatch.setattr(skills, "_step", step)
    result = approach(ctx(env), [.9, .5], [.5, .5], route={"dirs": ["RIGHT"] * 4} if routed else None)
    assert keys == ["RIGHT"] * 2 and result.inputs == 2 and result.status == "blocked" and result.reason == "no response"
    assert result.failure == {"stage": "route", "reason": "no response", "direction": "RIGHT"}
    mem = Memory(tmp_path); assert fold(mem, "room", (0, 0), result.moves, 16, 1) == (1, 0)
    assert all(not row["blocked"] for row in mem.rows("tiles"))


def test_travel_does_not_reroute_no_response(tmp_path, monkeypatch):
    from agent.memory import Memory
    from agent.world import World
    from agent import skills
    from types import SimpleNamespace
    env = RecordingEnv(); frame = env.screenshot(); keys = []
    def step(c, direction, before):
        keys.append(direction); return SimpleNamespace(frame=frame, blank_seen=False, no_response=True), None, False, None
    monkeypatch.setattr(skills, "_step", step)
    monkeypatch.setattr("agent.world.route", lambda *a, **kw: pytest.fail("no-response travel rerouted"))
    world = World(Memory(tmp_path), 16); world.current_place = "room"; world.pa = [.5, .5]
    result = world.travel(ctx(env), [{"place": "room", "dirs": ["RIGHT"] * 3, "target": {"tile": [3, 0]}}], 1)
    assert keys == ["RIGHT"] and result.status == "blocked" and result.failure["reason"] == "no response"
