from agent.model import Reply
from agent.skills import Ctx, approach, interact, run_affordance, select
from tests.agent.fakes import GridEnv, StubModel


def point(env, at):
    return [.5 + (at[0] - env.player[0]) * 16 / 160,
            .5 + (at[1] - env.player[1]) * 16 / 144]


def ctx(env, tile_px=16, max_inputs=30, replies=None):
    return Ctx(env, StubModel(replies or []), tile_px=tile_px, max_inputs=max_inputs)


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


def test_exit_blocked_by_walls_reports_not_crossed():
    env = GridEnv(); env.walls.update({(8, 7), (8, 9), (7, 8), (9, 8)})
    result = run_affordance(ctx(env),
                            {"verb": "approach", "target": "exit down", "at": point(env, (11, 11))},
                            {"entities": [{"kind": "player", "at": [.5, .5]}]})
    assert result.status == "blocked" and result.reason == "exit not crossed"


def test_approach_at_target_fails_without_pressing():
    for tile_px in (16, None):
        env = GridEnv()
        result = approach(ctx(env, tile_px=tile_px), point(env, env.player), [.5, .5])
        assert result.status == "failed" and result.reason == "already there" and result.inputs == 0


def test_interact_opens_text_box():
    env = GridEnv()
    result = interact(ctx(env), point(env, env.npc), [.5, .5])
    assert result.status == "done" and env.text_box


def test_select_uses_one_model_call_then_moves_and_confirms():
    env = MenuEnv()
    model = StubModel([Reply("", {"items": env.items, "cursor": 0,
                               "layout": "vertical"}, 0, None, None)])
    result = select(Ctx(env, model, max_inputs=5), "option")
    assert result.status == "done" and result.inputs == 3
    assert env.cursor == 2 and env.selected == "OPTION"
    assert result.calls == model.calls == 1
