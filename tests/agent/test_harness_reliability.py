"""HF5 integrated regressions: the production Loop, World and skills on GridEnv, answered by StubModel.

Perception and choices are scripted; menu parser cases use StubModel replies. Every test runs with the network
closed: any model POST or socket connect fails it at teardown."""
import json
import socket
from collections import deque
from types import SimpleNamespace

import httpx
import pytest

from agent import loop as loop_module
from agent.loop import Loop
from agent.memory import Memory
from agent.model import Ollama
from tests.agent.fakes import GridEnv, StubModel
from tests.agent.test_skills import Busy, short_settle

STEPS = {"UP": (0, -1), "DOWN": (0, 1), "LEFT": (-1, 0), "RIGHT": (1, 0)}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """Any attempt to reach the network is recorded and the test fails at teardown."""
    hits = []
    def refuse(*args, **kwargs):
        hits.append(args); raise RuntimeError("network attempted in an offline harness test")
    monkeypatch.setattr(Ollama, "_post", refuse); monkeypatch.setattr(httpx, "post", refuse)
    monkeypatch.setattr(httpx.Client, "send", refuse); monkeypatch.setattr(socket.socket, "connect", refuse)
    yield
    assert hits == []


def reply(data): return SimpleNamespace(data=data, text="")


def act_reply(action, expect="screen_changes", why="test"):
    return reply({"place": "", "notebook": [], "action": action, "text": None, "expect": expect, "why": why})


def go_to(name): return act_reply({"go_to": name}, why=f"go to {name}")


def no_action(): return act_reply(None, expect="no_change", why="look")


def pick(match, shown=None, text=None):
    """The chooser picks the first option matching `match` (none when nothing matches) and records the options it was shown;
    the real act parser and the real loop run on that choice."""
    from agent.act import act as real
    def act(model, per, focus, options, buttons, controls):
        if shown is not None: shown.append(options)
        n = next((o["n"] for o in options if match(o)), None)
        replies = [reply({"i": n, "why": "pick"})] if n is not None else [reply({}), reply({})]
        if text is not None: replies.append(reply({"text": text}))
        return real(StubModel(replies), per, focus, options, buttons, controls)
    return act


class Track(GridEnv):
    """GridEnv that records every press with the player's tile before the press. Its geometry is the gated one (player sprite on its
    tile, top 64), where the perceived player centre matches the drawn tile; the default geometry is half a tile off for this."""
    def __init__(self, **kwargs):
        super().__init__(**kwargs); self.presses = []; self._top = 64
    def press(self, key, frames=8, on_frame=None):
        self.presses.append((key, self.player)); super().press(key, frames, on_frame)


def at(env, tile):  # on-screen centre of a world tile, as GridEnv.perception reports it
    px, py = env.player
    return [(80 + (tile[0] - px) * 16 + 8) / 160, (env._top + (tile[1] - py) * 16 + 8) / 144]


def grid_perception(monkeypatch, env, things=()):
    """Perception is the env's own (player, npc, door) plus named things at tiles: perceive is not what these tests exercise."""
    def perceive(*_):
        per = env.perception()
        per = {**per, "entities": [*per["entities"], *[{"name": name, "kind": kind, "at": at(env, tile)} for name, kind, tile in things]]}
        return per, reply(per)
    monkeypatch.setattr(loop_module, "perceive", perceive)


def bfs(env, start, goal, avoid=()):
    """Shortest walk length on the fixture grid: walls from env.walls, plus the given blocked tiles."""
    queue, seen = deque([(start, 0)]), {start}
    while queue:
        at_, n = queue.popleft()
        if at_ == goal: return n
        for dx, dy in STEPS.values():
            nxt = (at_[0] + dx, at_[1] + dy)
            if nxt in seen or nxt in env.walls or nxt in avoid or not (0 <= nxt[0] < 16 and 0 <= nxt[1] < 16): continue
            seen.add(nxt); queue.append((nxt, n + 1))
    return None


def loop_for(env, tmp_path, replies, monkeypatch, *, root="memory"):
    actions = iter(replies)
    def act(*args):
        data = next(actions).data
        return SimpleNamespace(action=data["action"], text=None, why=data["why"], option=None)
    monkeypatch.setattr(loop_module, "act", act)
    loop = Loop(env, StubModel([]), Memory(tmp_path / root), "grid")
    loop.world.self_spot, loop.world._self_from_motion = at(env, env.player), True  # no self-locating probe presses: every press is the action's
    return loop


def seed_known_grid(loop, env):
    """The agent's map of the room is the fixture grid (floor where the env has floor, wall where it has wall), written for the place the loop observed."""
    for x in range(0, 16):
        for y in range(0, 16):
            loop.mem.upsert("tiles", {"id": f"{loop.place}@{x - 8},{y - 8}", "place": loop.place, "x": x - 8, "y": y - 8,
                                      "floor": (x, y) not in env.walls, "blocked": [], "pushed": [], "exit": None, "step": 1})


def presses_into(env, tiles):
    return [p for p in env.presses if (p[1][0] + STEPS[p[0]][0], p[1][1] + STEPS[p[0]][1]) in tiles]


# ---------------------------------------------------------------- case 1
def test_known_actor_between_player_and_goal_is_walked_around_within_a_shortest_path(tmp_path, monkeypatch):
    env = Track(); env.npc, env.door = (6, 8), (-5, -5)  # the npc stands on the straight way to the sign, two tiles west of the player
    sign, npc = (4, 8), (6, 8)
    grid_perception(monkeypatch, env, [("sign", "object", sign)])
    loop = loop_for(env, tmp_path, [go_to("sign")], monkeypatch)

    row = loop.step()

    assert row["skill"]["status"] == "done" and env.player == sign
    assert len(env.presses) <= bfs(env, (8, 8), sign, avoid={npc}) + 2  # within a shortest walk around the npc
    assert presses_into(env, {npc}) == []  # no collision with the actor
    walled = [r for r in loop.mem.rows("tiles") if r.get("place") == loop.place and (r["x"], r["y"]) == (npc[0] - 8, npc[1] - 8)]
    assert all(r["floor"] is not False and not r["blocked"] for r in walled)  # the actor's tile is never recorded as a wall


# ---------------------------------------------------------------- case 2
def test_actor_in_a_one_tile_corridor_is_a_route_failure_then_the_same_goal_succeeds_once_it_moves(tmp_path, monkeypatch):
    env = Track(); env.door = (-5, -5)
    for x in range(1, 15):
        if x != 6: env.walls.add((x, 7))  # the alcove at (6,7) is the only side opening
        env.walls.add((x, 9))
    env.npc = (6, 8); sign = (4, 8)
    grid_perception(monkeypatch, env, [("sign", "object", sign)])
    loop = loop_for(env, tmp_path, [no_action(), go_to("sign"), go_to("sign")], monkeypatch)
    loop.step()  # the first observation creates the place (nothing to do); the fixture grid is then the agent's map of it
    seed_known_grid(loop, env)

    first = loop.step()

    key = first["choice"]["intent"]
    known_blocked = env.walls | {(6, 8)}
    assert first["skill"]["status"] == "blocked" and first["skill"]["inputs"] == 0 and first["skill"]["reason"] == "no route avoids blocker"  # refused before any skill ran: zero input, route stage (loop.cause below)
    assert presses_into(env, known_blocked) == []  # no input into a known wall or the known actor
    assert loop.fails[key]["n"] == 1 and "failed at route" in loop.cause
    assert loop.mem.get("tiles", f"{loop.place}@-2,0")["floor"] is True  # the actor's tile is not a wall

    env.npc, before = (6, 7), len(env.presses)  # the actor steps into the alcove: the next observation shows the corridor open
    second = loop.step()

    assert second["skill"]["status"] == "done" and env.player == sign
    assert presses_into(env, env.walls) == []
    assert len(env.presses) - before <= bfs(env, (7, 8), sign, avoid={(6, 7)}) + 2  # within a shortest walk from where the first attempt left off
    assert key not in loop.fails  # the success clears this intent's own counter


# ---------------------------------------------------------------- case 3
def test_duplicate_exit_records_pick_the_observed_door_and_the_failure_note_follows_rename_and_resume(tmp_path, monkeypatch):
    env = Track(); env.door = (-5, -5)  # the observed door leads nowhere: every crossing attempt fails
    grid_perception(monkeypatch, env)
    root = tmp_path / "memory"
    loop = loop_for(env, tmp_path, [no_action()], monkeypatch)
    loop.step()  # the room is seen once with nothing to do
    loop.mem.upsert("places", {"id": "hall", "name": "Hall", "screen": "world", "state": "place", "first": 1, "last": 1})
    loop.mem.upsert("tiles", {"id": f"{loop.place}@2,0", "place": loop.place, "x": 2, "y": 0, "floor": True, "blocked": [], "pushed": [], "step": 1,
                              "exit": {"dir": "RIGHT", "to": "hall", "arrive": [0, 0], "src": "observed"}})  # the observed door
    loop.mem.upsert("tiles", {"id": f"{loop.place}@2,1", "place": loop.place, "x": 2, "y": 1, "floor": True, "blocked": [], "pushed": [], "step": 1,
                              "exit": {"dir": "RIGHT", "to": "hall", "arrive": [0, 0], "src": "inferred"}})  # its inferred duplicate, one row below
    monkeypatch.setattr(loop_module, "act", pick(lambda o: o.get("exit") == {"tile": [2, 0], "to": "hall"}))  # the model picks the exit row

    first = loop.step()

    key = first["choice"]["intent"]
    assert ("RIGHT", (10, 8)) in env.presses and all(p[1] != (10, 9) for p in env.presses)  # crossed from the observed door only
    assert first["verdict"] == "changed" and key not in loop.fails

    retry = loop.step()  # repeat the same blocked crossing after its first view exposed new rows
    assert retry["choice"]["intent"] == key and retry["verdict"] == "failed" and loop.fails[key]["n"] == 1

    loop.mem.upsert("places", {**loop.mem.get("places", "hall"), "name": "Hallway"})  # the destination is renamed
    second = loop.step()

    assert second["choice"]["intent"] == key and loop.fails[key]["n"] == 2

    state = json.loads(json.dumps(loop.state()))
    resumed = Loop(env, StubModel([]), Memory(root), "grid"); resumed.restore(state)
    third = resumed.step()

    assert third["choice"]["intent"] == key and resumed.fails[key]["n"] == 3 and resumed.place == loop.place
    noted = next(o for o in resumed.world.options(env.perception(), resumed.place, resumed.fails, 9, inputs=list(env.inputs())) if o.get("intent_key") == key)
    assert noted["label"].startswith("go through RIGHT,") and noted["state"] == "failed"
    assert "exit mismatch 3×" in "; ".join(noted["notes"])


# ---------------------------------------------------------------- case 4
def dialogue_scene(): return {"screen": "dialogue", "text": ["Watch the television?"], "entities": [
    {"name": "television", "kind": "object", "at": [.2, .3]}, {"name": "player", "kind": "player", "at": [.5, .5]}, {"name": "dialogue box", "kind": "dialogue_box", "at": [.5, .85]}]}
def menu_scene(): return {"screen": "menu", "text": ["FIGHT BAG"], "entities": [
    {"name": "FIGHT", "kind": "menu_item", "at": [.3, .4], "selectable": True}, {"name": "BAG", "kind": "menu_item", "at": [.3, .6], "selectable": True}]}
def world_scene(): return {"screen": "world", "text": [], "entities": [{"name": "player", "kind": "player", "at": [.5, .5]}]}


def test_dialogue_over_room_objects_offers_no_select_and_advance_progresses_the_text(tmp_path, monkeypatch):
    shown, env = [], Track()
    monkeypatch.setattr(loop_module, "act", pick(lambda o: o["tool"] == "advance", shown))
    loop = Loop(env, StubModel([reply(dialogue_scene()), reply(world_scene())]), Memory(tmp_path / "memory"), "grid")  # then the screen after the press

    row = loop.step()

    assert [o["tool"] for o in shown[0]][:2] == ["advance", "cancel"] and not [o for o in shown[0] if o["tool"] == "select"]
    assert "select television" not in [o["label"] for o in shown[0]]  # furniture is never a choice on a modal screen
    assert [p[0] for p in env.presses] == ["A"] and row["execution"] == "done"  # one press: the text moves on
    assert row["choice"]["verb"] == "advance" and row["verdict"] == "progress"


def test_flagged_menu_choice_is_selected_by_its_own_name_and_the_menu_is_read_once(tmp_path, monkeypatch):
    env, shown = Track(), []
    menu_read = reply({"items": ["FIGHT", "BAG"], "cursor": 0, "layout": "vertical"})
    loop = Loop(env, StubModel([reply(menu_scene()), menu_read, reply(world_scene())]), Memory(tmp_path / "memory"), "grid")
    monkeypatch.setattr(loop_module, "act", pick(lambda o: o["label"] == "select BAG", shown))

    row = loop.step()

    assert [o["label"] for o in shown[0]][:2] == ["select FIGHT", "select BAG"]
    assert [p[0] for p in env.presses] == ["DOWN", "A"] and row["skill"]["status"] == "done" and row["choice"]["verb"] == "select"


# ---------------------------------------------------------------- case 4, typing
def test_typing_field_on_a_name_screen_is_offered_and_typing_runs_to_the_name(tmp_path, monkeypatch):
    from PIL import Image, ImageDraw
    from tests.agent.test_type_text import KeyboardEnv, NameModel
    from agent.model import Reply

    class Textured(KeyboardEnv):  # a near-uniform frame reads as blank to the settle check: stripes and the cursor cell give it texture
        def screenshot(self):
            image = Image.new("RGB", (160, 144), "#777"); draw = ImageDraw.Draw(image)
            for x in range(0, 160, 2): draw.line((x, 0, x, 143), fill="#666")
            draw.rectangle((8 * self.cursor[1], 8 * self.cursor[0], 8 * self.cursor[1] + 7, 8 * self.cursor[0] + 7), fill="#222")
            return image
    keyboard = {"screen": "menu", "text": ["NAME?"], "entities": [{"name": "keyboard", "kind": "keyboard", "at": [.5, .5]}]}
    env = Textured(menu=True); shown = []
    model = NameModel([reply(keyboard), Reply("", {"presets": ["Blue"]}, 0, None, None), Reply("", {"items": ["RED", "BLUE"], "cursor": 0, "layout": "vertical"}, 0, None, None),
                       reply(keyboard)], env)  # the last: the screen after the typing
    monkeypatch.setattr(loop_module, "act", pick(lambda o: o["tool"] == "type", shown, text="Blue"))
    loop = Loop(env, model, Memory(tmp_path / "memory"), "grid")

    row = loop.step()

    assert [o["label"] for o in shown[0] if o["tool"] == "type"] == ["type text into the field"]
    assert env.name == "BLUE" and env.committed and row["skill"]["status"] == "done" and row["choice"]["verb"] == "type"


# ---------------------------------------------------------------- case 5
class TrackBusy(Busy):
    """Busy with the gated geometry (see Track); keys records each press."""
    def __init__(self, plan): super().__init__(plan=plan); self._top = 64


def crossing_run(tmp_path, name, monkeypatch, *, checkpoint):
    """Two presses cross the door while the scene keeps changing for 20 ticks after the second press.
    With checkpoint, save and restore after the timed-out action row."""
    short_settle(monkeypatch)
    env = TrackBusy(plan=[0, 20]); grid_perception(monkeypatch, env)
    loop = loop_for(env, tmp_path, [act_reply({"buttons": ["RIGHT", "RIGHT"]})], monkeypatch, root=f"{name}/memory")
    rows = [loop.step()]
    if checkpoint:
        state = json.loads(json.dumps(loop.state()))
        loop = Loop(env, StubModel([]), Memory(tmp_path / name / "memory"), "grid"); loop.restore(state)
    return loop, env, rows


def test_timed_out_crossing_checkpoint_matches_without_replaying_inputs(tmp_path, monkeypatch):
    straight, straight_env, _ = crossing_run(tmp_path, "straight", monkeypatch, checkpoint=False)
    resumed, env, rows = crossing_run(tmp_path, "resumed", monkeypatch, checkpoint=True)

    assert len(rows) == 1 and rows[0]["observation_status"] == "timed_out" and rows[0]["execution"] == "done"
    assert env.keys == ["RIGHT", "RIGHT"] == straight_env.keys  # one input sequence, never replayed after the checkpoint
    assert resumed.fails == {} and straight.fails == {}  # no premature failure
    assert rows[0]["verdict"] != "unobservable"
    counted = sorted((c["id"], c["n"]) for c in resumed.mem.rows("crossings"))
    assert counted == sorted((c["id"], c["n"]) for c in straight.mem.rows("crossings")) and [n for _, n in counted] == [1]  # one departure crossing
    assert resumed.world.pos == straight.world.pos and resumed.place == straight.place and env.player == straight_env.player


# ---------------------------------------------------------------- case 6
PLAN = {"goals": [{"op": "set", "key": "g1", "value": {"text": "leave the room", "parent": None, "status": "active", "done_when": "new place", "source": "assumed"}}],
        "facts": [{"key": "find_person", "text": "A person next door is looking for you.", "from": "MOM", "source": "heard"}],
        "focus": "g1", "lesson": None, "why": "start"}


def test_door_crossing_keeps_place_identity_heard_facts_and_the_top_quest_through_a_checkpoint(tmp_path, monkeypatch):
    env = Track()  # the door at (10,8) opens into scene 1
    grid_perception(monkeypatch, env)
    root = tmp_path / "memory"
    plan = {"ops": [{"op": "add", "key": "g1", "text": "Find the person next door", "why": ["t1"]},
                    {"op": "top", "key": "g1"}], "why": "heard a lead"}
    class LocalPlanModel(StubModel):
        def ask(self, *args, **kwargs):
            if kwargs.get("role") == "plan":
                self.calls += 1
                return reply(plan)
            return super().ask(*args, **kwargs)
    loop = loop_for(env, tmp_path, [], monkeypatch)
    loop.model = LocalPlanModel([])
    assert loop.notebook.heard(0, "MOM", "room", "A person next door is looking for you.") == "t1"
    monkeypatch.setattr(loop_module, "act", pick(lambda o: o["tool"] == "approach"))

    first = loop.step()
    monkeypatch.setattr(loop_module, "act", pick(lambda o: False))  # no further action: the next observation is only read
    second = loop.step()

    assert env.scene == 1 and first["place"] != second["place"] and [c["n"] for c in loop.mem.rows("crossings")] == [1]  # one departure crossing
    assert loop.notebook.focus == "g1" and loop.notebook.data["quests"][0]["key"] == "g1" and loop.notebook.data["quests"][0]["status"] == "open"
    saved = json.loads((root / "notebook.json").read_text(encoding="utf8"))
    assert saved["text"][0]["key"] == "t1" and saved["text"][0]["speaker"] == "MOM"

    state = json.loads(json.dumps(loop.state()))
    resumed = Loop(env, StubModel([]), Memory(root), "grid"); resumed.restore(state)
    again = resumed.step()

    assert again["place"] == second["place"] and resumed.place == loop.place  # the same room keeps its identity after the restore
    assert resumed.notebook.focus == "g1" and resumed.notebook.data["quests"][0]["key"] == "g1" and resumed.notebook.data["quests"][0]["status"] == "open"
    assert resumed.notebook.data["text"][0]["text"].startswith("A person next door")
    assert "Goal: Find the person next door" in resumed.notebook.render_focus(resumed.world.label)
