from PIL import Image

from agent.model import Reply
from agent.skills import Ctx, approach, press, run_affordance, type_text
from tests.agent.fakes import StubModel


class KeyboardEnv:
    pointer = False
    rows = ["ABCDEFGHI", "JKLMNOPQR", "STUVWXYZ "]

    def __init__(self, name="", menu=False):
        self.events, self.cursor, self.name = [], [0, 0], name
        self.lower, self.committed, self.menu = False, False, menu
        self.case_toggle, self.done_key = [2, 7], [2, 6]

    def screenshot(self): return Image.new("RGB", (20, 20), "#777")
    def tick(self, frames=1): pass
    def inputs(self): return ("UP", "DOWN", "LEFT", "RIGHT", "A", "B")

    def press(self, key, frames=8, on_frame=None):
        self.events.append(key)
        if self.menu:
            if key == "DOWN": self.cursor[0] += 1
            elif key == "UP": self.cursor[0] -= 1
            elif key == "A": self.name = ("RED", "BLUE")[self.cursor[0]]; self.committed = True
        elif key in ("UP", "DOWN", "LEFT", "RIGHT"):
            self.cursor[0] += (key == "DOWN") - (key == "UP")
            self.cursor[1] += (key == "RIGHT") - (key == "LEFT")
        elif key == "B": self.name = self.name[:-1]
        elif key == "A":
            if self.cursor == self.case_toggle: self.lower = not self.lower
            elif self.cursor == self.done_key: self.committed = True
            else: self.name += self.rows[self.cursor[0]][self.cursor[1]].lower() if self.lower else self.rows[self.cursor[0]][self.cursor[1]]


class NameModel(StubModel):
    def __init__(self, replies, env): super().__init__(replies); self.env = env
    def ask(self, *args, **kwargs):
        if "currently shown" in args[0]:
            self.calls += 1
            if not self.env.menu: assert not self.env.committed
            return Reply("", {"name": self.env.name}, 0, None, None)
        return super().ask(*args, **kwargs)


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
