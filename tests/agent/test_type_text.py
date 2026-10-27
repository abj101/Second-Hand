from PIL import Image

from agent.model import Reply
from agent.skills import Ctx, approach, press, run_affordance, select, type_text
from tests.agent.fakes import KeyboardEnv, NameModel, StubModel


def layout(**changes):
    data = {"rows": KeyboardEnv.rows, "cursor": [0, 0], "case_toggle": [2, 7],
            "done_key": [2, 6], "presets": []}
    data.update(changes)
    return Reply("", data, 0, None, None)


def test_hand_uses_grid_arrows_and_case_toggle():
    env = KeyboardEnv(); model = NameModel([layout()], env)
    result = run_affordance(Ctx(env, model), {"verb": "type"}, {"entities": []}, "Hand")
    assert result.status == "done" and result.calls == 2 and env.committed and env.name == "Hand"
    assert env.events == ["RIGHT"] * 7 + ["A", "DOWN", "DOWN", "A"] + ["LEFT"] * 7 + ["UP", "UP", "A"] + ["RIGHT"] * 4 + ["DOWN", "A", "LEFT", "UP", "A"] + ["RIGHT"] * 3 + ["DOWN", "DOWN", "A"]


def test_cached_layout_alias_and_invalid_toggle_fall_back_to_uppercase():
    env = KeyboardEnv(); env.rows = ["ABCDEFGHI", "JKLMNOPQR", "STUVWXYZ", "x():;[]PKN", "-?!♀♂/.,ED"]
    env.case_toggle, env.done_key = [5, 0], [4, 8]
    reply = {"layout": env.rows, "cursor": [0, 0], "case_toggle": [5, 0], "done_key": [4, 8], "presets": [], "text_to_enter": "Hand"}
    model = NameModel([Reply("", reply, 0, None, None)], env)
    result = run_affordance(Ctx(env, model), {"verb": "type"}, {"entities": []}, "Hand")
    assert result.status == "done" and result.reason == "uppercase fallback" and result.calls == 2
    assert env.name == "HAND" and env.committed


def test_matching_preset_uses_select_without_grid_or_done():
    env = KeyboardEnv(menu=True)
    menu = Reply("", {"items": ["RED", "BLUE"], "cursor": 0, "layout": "vertical"}, 0, None, None)
    model = NameModel([Reply("", {"presets": ["Blue"]}, 0, None, None), menu], env)
    result = type_text(Ctx(env, model), "Blue")
    assert result.status == "done" and result.calls == model.calls == 2
    assert env.name == "BLUE" and env.events == ["DOWN", "A"]


def test_missing_case_toggle_uses_uppercase_fallback():
    env = KeyboardEnv(); model = NameModel([layout(case_toggle=None)], env)
    result = type_text(Ctx(env, model, max_inputs=60), "a")
    assert result.status == "done" and result.reason == "uppercase fallback" and env.name == "A"


def test_wrong_readback_erases_name_before_exactly_one_retry():
    env = KeyboardEnv(name="X"); model = NameModel([layout(), layout(cursor=[0, 0])], env)
    result = type_text(Ctx(env, model, max_inputs=60), "A")
    assert result.status == "done" and result.calls == model.calls == 4
    assert env.name == "A" and env.committed and env.events.count("B") == 2


def test_press_normalizes_whole_tokens_and_blocks_ambiguous_labels():
    env = KeyboardEnv(); ctx = Ctx(env, StubModel([]))
    assert press(ctx, "A").status == "done"
    assert run_affordance(ctx, {"verb": "press", "target": "A button"}, {"entities": []}).status == "done"
    assert run_affordance(ctx, {"verb": "press", "target": "Up arrow"}, {"entities": []}).status == "done"
    before = env.events[:]
    assert run_affordance(ctx, {"verb": "press", "target": "A or B"}, {"entities": []}).status == "blocked"
    assert run_affordance(ctx, {"verb": "press", "target": "Cancel"}, {"entities": []}).status == "blocked"
    assert before == env.events == ["A", "A", "UP"]


def test_approach_keeps_sidestepping_past_a_two_tile_obstacle(monkeypatch):
    from types import SimpleNamespace
    import agent.skills as skills

    class Grid:
        pointer = False
        def __init__(self, wall=(), unknown=False): self.player, self.wall, self.unknown = (0, 0), set(wall), unknown
        def screenshot(self): return Image.new("RGB", (160, 144), "#777")
        def tick(self, frames=1): pass

    frame = Image.new("RGB", (160, 144), "#777")
    monkeypatch.setattr(skills, "wait_settled", lambda env: SimpleNamespace(frame=frame, settled=True, blank_seen=False))
    def step(ctx, direction, before):
        if ctx.env.unknown: return SimpleNamespace(frame=frame, blank_seen=False), None, False, None
        dx, dy = {"LEFT": (-1, 0), "RIGHT": (1, 0), "UP": (0, -1), "DOWN": (0, 1)}[direction]
        dest = (ctx.env.player[0] + dx, ctx.env.player[1] + dy)
        if dest in ctx.env.wall: return SimpleNamespace(frame=frame, blank_seen=False), (0, 0), False, None
        ctx.env.player = dest
        return SimpleNamespace(frame=frame, blank_seen=False), (-16 * dx, -16 * dy), False, None
    monkeypatch.setattr(skills, "_step", step)

    env = Grid({(1, y) for y in range(-3, 2)})
    result = approach(Ctx(env, StubModel([]), tile_px=16, max_inputs=12), [.7, .5], [.5, .5])
    assert result.status == "done" and result.inputs <= 12 and env.player[0] >= 2

    clear = Grid()
    result = approach(Ctx(clear, StubModel([]), tile_px=16, max_inputs=5), [.7, .5], [.5, .5])
    assert result.status == "done" and result.inputs <= 5 and clear.player[0] >= 2

    unknown = Grid(unknown=True)
    result = approach(Ctx(unknown, StubModel([]), tile_px=16, max_inputs=5), [.7, .5], [.5, .5])
    assert result.status == "failed" and result.reason == "scroll unsure" and result.inputs == 1


def test_select_on_a_grid_moves_by_row_and_column():
    env = KeyboardEnv(); env.done_key = [2, 6]
    model = StubModel([Reply("", {"items": ["A"], "cursor": 0, "layout": "grid", "cursor_cell": [0, 0], "item_cell": [2, 6]}, 0, None, None)])
    result = select(Ctx(env, model), "Y")
    assert result.status == "done" and result.inputs == 9 and env.committed
    assert env.events == ["DOWN", "DOWN"] + ["RIGHT"] * 6 + ["A"]
