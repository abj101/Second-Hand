import json, re
from pathlib import Path
import pytest
from scripts import eval_act

CASES = Path(__file__).resolve().parents[2] / "scripts" / "om_cases.json"

def test_action_option_maps_each_action_shape():
    ents = [{"name": "stairs"}, {"name": "Mom"}]
    assert eval_act.action_option({"go_to": "exit: door mat"}) == ("go_to", "exit: door mat")
    assert eval_act.action_option({"buttons": ["UP", "A"]}) == ("buttons", "UP A")
    assert eval_act.action_option({"thing": 1, "verb": "approach"}, ents) == ("approach", "Mom")
    assert eval_act.action_option({"thing": 9, "verb": "approach"}, ents, target="stairs") == ("approach", "stairs")
    assert eval_act.action_option({"verb": "advance"}) == ("advance", "")
    assert eval_act.action_option(None) == ("none", "")

def test_judging_matches_tool_and_label():
    case = {"right": [{"tool": "approach|go_to", "label_re": "door mat|rug"}]}
    assert eval_act.is_right(case, "go_to", "exit door mat: never used")
    assert eval_act.is_right(case, "approach", "approach rug")
    assert not eval_act.is_right(case, "buttons", "UP A -> door mat")  # the tool must match too
    assert not eval_act.is_right(case, "approach", "approach stairs")

def test_cases_file_is_well_formed():
    cases = json.loads(CASES.read_text(encoding="utf-8"))
    assert len(cases) == 8 and len({(c["run"], c["step"]) for c in cases}) == 8
    for c in cases:
        assert c["run"] in {"m1_gp5", "m1_gp2"} and c["right"] and {"map", "x", "y"} <= set(c["truth"])
        for e in c["right"] + c["wrong"]: re.compile(e.get("tool", ".*")); re.compile(e["label_re"])

def test_replay_never_reaches_the_network_and_stops_after_three_perceiver_misses(tmp_path):
    rep = eval_act.Replay(None, tmp_path, counter={"requests": 0})
    with pytest.raises(eval_act.OmAbort): rep.real._post({})  # no live case step: no request
    for n in range(3): assert rep.ask("s", f"unseen perception {n}", [], None, role="perceiver").data is None
    with pytest.raises(eval_act.OmAbort): rep.ask("s", "unseen perception 3", [], None, role="perceiver")
    assert rep.misses == 4 and rep.counter["requests"] == 0

def _synthetic(step=103, floor_map=38, label_re="stairs"):
    return {"run": "m1_gp5", "step": step, "truth": {"map": floor_map, "x": 0, "y": 0}, "situation": "synthetic",
            "right": [{"tool": "approach|go_to", "label_re": label_re}], "wrong": []}

def _opt(n, tool, label, action="a", notes=()):
    return {"n": n, "tool": tool, "label": label, "action": action, "notes": list(notes)}

FREE = _opt(9, "buttons", "free buttons", action=None)
DEAD_END = "everything unexplored from here is through this exit"
STAIRS_OK = _opt(1, "go_to", "exit stairs → 1F (used 2×, last step 99) (1 unexplored exit, 10 unexplored spots beyond)", notes=(DEAD_END,))
EXPLORE_0 = _opt(3, "explore", "walk to an unexplored part of this place (0 spots lead anywhere unseen)", action={"go_to": "unexplored"})
DOORWAY = _opt(2, "approach", "possible exit doorway (unverified, never crossed)")

def _case_2f(options=None, exits=None, prompt="x" * 400):
    if options is None: options = [STAIRS_OK, DOORWAY, _opt(3, "approach", "approach Mom"), FREE]
    if exits is None: exits = [{"dest": "1F", "tile": (3, 4)}, {"dest": "x", "tile": (5, 5)}]
    return {"case": _synthetic(), "options": options, "prompt": prompt, "exits": exits}

def _case_1f(options=None, exits=None):
    if options is None: options = [_opt(1, "approach", "possible exit door mat (unverified, never crossed)"), _opt(2, "approach", "approach Mom"), FREE]
    if exits is None: exits = [{"dest": "outside", "tile": (5, 3)}]
    return {"case": _synthetic(step=82, floor_map=37, label_re="door mat|rug"), "options": options, "prompt": "x" * 400, "exits": exits}

def _status(rows): return {g: ok for g, ok, _ in rows}

def test_dry_gates_pass_on_synthetic_2F_and_1F_cases():
    assert all(ok for _, ok, _ in eval_act.dry_gates(_case_2f()))
    assert all(ok for _, ok, _ in eval_act.dry_gates(_case_1f()))

def test_right_present_fails_without_a_right_option():
    rows = eval_act.dry_gates(_case_2f(options=[DOORWAY, FREE]))
    assert _status(rows)["right present"] is False

def test_exit_truth_2F_fails_on_each_bad_stairs_label():
    bad = {
        "stairs unverified": [_opt(1, "approach", "possible exit stairs (unverified, never crossed)"), DOORWAY, FREE],
        "two verified exits": [STAIRS_OK, _opt(2, "go_to", "exit doorway → 1F (used 1×, last step 5)"), FREE],
        "verified exit is not the stairs": [_opt(1, "go_to", "exit doorway → 1F (used 1×) (1 unexplored exit, 2 unexplored spots beyond)"), FREE],
        "no beyond-summary": [_opt(1, "go_to", "exit stairs → 1F (used 2×, last step 99)"), FREE],
    }
    for name, options in bad.items():
        assert _status(eval_act.dry_gates(_case_2f(options=options)))["exit truth"] is False, name

def test_exit_truth_2F_needs_the_dead_end_note_and_a_zero_unseen_explore_label():
    ok = _case_2f(options=[STAIRS_OK, EXPLORE_0, DOORWAY, FREE])
    assert _status(eval_act.dry_gates(ok))["exit truth"] is True
    no_note = _opt(1, "go_to", STAIRS_OK["label"])
    rows = eval_act.dry_gates(_case_2f(options=[no_note, DOORWAY, FREE]))
    detail = next(d for g, ok_, d in rows if g == "exit truth")
    assert _status(rows)["exit truth"] is False and "the stairs are not named as the way to everything unexplored" in detail
    busy = _opt(3, "explore", "walk to an unexplored part of this place (26 spots lead somewhere unseen)", action={"go_to": "unexplored"})
    rows = eval_act.dry_gates(_case_2f(options=[STAIRS_OK, busy, DOORWAY, FREE]))
    detail = next(d for g, ok_, d in rows if g == "exit truth")
    assert _status(rows)["exit truth"] is False and "explore label reports unseen spots" in detail and "26 spots" in detail

def test_replay_maps_renamed_recorded_entity_by_its_cached_index():
    rep = object.__new__(eval_act.Replay)
    rep.step = 7; rep.by_option = True; rep.armed = False; rep.found = {}; rep.script = None; rep.live_step = None
    rep.rec = type("Rec", (), {"trace": {7: {"option": {"tool": "approach", "label": "approach person"},
                                                   "act": {"action": {"thing": 2, "verb": "approach"}}}},
                                    "act_ops": {}, "goal_ops": {}, "focus": {}})()
    rep.options_now = [{"n": 1, "tool": "approach", "label": "approach stranger", "action": {"thing": 4}},
                       {"n": 2, "tool": "approach", "label": "approach MOM", "action": {"thing": 2}}]
    schema = {"properties": {"i": {"enum": [1, 2]}}}
    reply = rep.ask("", "", schema=schema, role="act")
    assert reply.data["i"] == 2

def test_om_talk_reports_conversation_called_entity_and_interact_options(monkeypatch, capsys, tmp_path):
    nb = type("Notebook", (), {"data": {"text": [{"step": 2, "speaker": "MOM", "where": "tiled room", "text": "PROF. OAK, next door, is looking for you."}]}})()
    mem = type("Memory", (), {"rows": lambda self, section: [{"id": "e1", "name": "person", "called": "MOM", "place": "p1"}]})()
    loop = type("Loop", (), {"notebook": nb, "mem": mem})()
    class FakeReplay:
        def __init__(self, rec, out, *, cases, counter):
            self.loop, self.counter = loop, counter
            self.by_option = False
            self.found = {3: {"options": [{"n": 1, "tool": "interact", "label": "interact with MOM", "action": {"thing": 0}}]}}
            self.rows = {3: {"place": "p1"}}
        def run(self, last): assert last == 3
    monkeypatch.setattr(eval_act, "stage", lambda run: type("Rec", (), {"trace": {1: {}, 2: {}, 3: {}}, "copy": tmp_path})())
    monkeypatch.setattr(eval_act, "Replay", FakeReplay)
    assert eval_act.om_talk("m1_decide3") == 0
    output = capsys.readouterr().out
    assert "PROF. OAK, next door, is looking for you." in output
    assert "called names" in output and "interact with MOM" in output and "requests=0" in output

def test_exit_truth_1F_fails_when_the_rug_is_verified():
    options = [_opt(1, "approach", "exit door mat → outside (used 3×, last step 40)"), FREE]
    assert _status(eval_act.dry_gates(_case_1f(options=options)))["exit truth"] is False

def test_exit_truth_fails_on_two_exit_records_with_the_same_destination_and_tile():
    exits = [{"dest": "1F", "tile": (3, 4)}, {"dest": "1F", "tile": (3, 4)}]
    assert _status(eval_act.dry_gates(_case_2f(exits=exits)))["exit truth"] is False

def test_size_counts_options_without_the_free_row_and_the_prompt_tokens():
    ten = [_opt(n, "approach", f"approach thing {n}") for n in range(1, 11)]
    assert _status(eval_act.dry_gates(_case_2f(options=ten + [FREE])))["size"] is True
    eleven = [_opt(n, "approach", f"approach thing {n}") for n in range(1, 12)]
    assert _status(eval_act.dry_gates(_case_2f(options=eleven + [FREE])))["size"] is False
    assert _status(eval_act.dry_gates(_case_2f(prompt="x" * 6801)))["size"] is False  # 1,700.25 tokens

def test_live_is_refused_when_a_dry_gate_fails(monkeypatch, capsys):
    rows = [("right present", False, "step 82: no right option is listed"), ("exit truth", True, ""), ("size", True, "")] * 8
    monkeypatch.setattr(eval_act, "dry_report", lambda cases, counter, fresh=False: (rows, 0))
    monkeypatch.setattr(eval_act, "Replay", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no replay may be built")))
    monkeypatch.setattr(eval_act, "live_cases", lambda *a: (_ for _ in ()).throw(AssertionError("live must not run")))
    assert eval_act.om_cases(True) == 1
    assert "DRY GATES FAILED: live measure not run" in capsys.readouterr().out

def test_live_runs_after_all_dry_gates_pass(monkeypatch):
    rows = [(g, True, "") for g in ("right present", "exit truth", "size")] * 8
    calls = []
    monkeypatch.setattr(eval_act, "dry_report", lambda cases, counter, fresh=False: (rows, 0))
    monkeypatch.setattr(eval_act, "live_cases", lambda cases, counter, before, old_right: calls.append((len(cases), old_right)) or 0)
    assert eval_act.om_cases(True) == 0
    assert calls == [(8, 0)]

def test_dry_run_never_calls_live(monkeypatch):
    rows = [(g, True, "") for g in ("right present", "exit truth", "size")] * 8
    monkeypatch.setattr(eval_act, "dry_report", lambda cases, counter, fresh=False: (rows, 0))
    monkeypatch.setattr(eval_act, "live_cases", lambda *a: (_ for _ in ()).throw(AssertionError("dry must not call live")))
    assert eval_act.om_cases(False) == 0

def test_classify_outcomes():
    b, tiles = {"map": 38, "x": 6, "y": 1}, {(7, 1)}
    at = lambda m, x, y: {"map": m, "x": x, "y": y}
    assert eval_act.classify(b, at(37, 7, 1), 37, tiles) == "crossed_right"
    assert eval_act.classify(b, at(40, 0, 0), 37, tiles) == "crossed_other"
    assert eval_act.classify(at(38, 3, 1), at(38, 4, 1), 37, tiles) == "closer"
    assert eval_act.classify(b, at(38, 5, 1), 37, tiles) == "other"
    assert eval_act.classify(b, at(38, 6, 1), 37, tiles) == "no_move"


def test_om_act_size_measures_the_act_prompt_with_a_capturing_stub_and_no_requests(monkeypatch, tmp_path, capsys):
    nb = eval_act.Notebook(tmp_path / "memory")
    option = {"n": 1, "tool": "approach", "label": "approach door", "action": {"thing": 1}, "notes": []}
    world = type("World", (), {"options": lambda self, per, place, fails, step, inputs: [option],
                                   "label": lambda self, ref: str(ref), "place_label": lambda self, place: "Room",
                                   "here": lambda self, place: self.place_label(place)})()
    loop = type("Loop", (), {"prev": {"screen": "world", "text": ["hello"]}, "notebook": nb, "world": world, "place": "p1", "fails": {},
                              "step_no": 100, "game": "pokemon_red", "_goal_text": lambda self: "goal"})()
    env = type("Env", (), {"closed": False, "inputs": lambda self: eval_act._INPUTS, "close": lambda self: setattr(self, "closed", True)})()
    monkeypatch.setattr(eval_act, "_plan_checkpoint", lambda run, step: (loop, env))
    monkeypatch.setattr(eval_act, "Ollama", lambda *args, **kwargs: pytest.fail("act-size created a model"))
    monkeypatch.setattr(eval_act.run_agent, "_ledger", lambda *args: pytest.fail("act-size touched the ledger"))
    assert eval_act.main(["--om", "act-size", "--run", "fixture", "--from-step", "100"]) == 0
    output = capsys.readouterr().out
    assert "act prompt chars=" in output and "tokens~" in output and "(len/4)" in output and "requests=0" in output and env.closed
    loop.prev = None
    assert eval_act.om_act_size("fixture", 100) == 2
    assert "no cached perception" in capsys.readouterr().out

def test_points_stub_returns_step_role_responses_in_order_then_stops_perceiver():
    stub = eval_act.PointsStub([
        {"step": 4, "role": "perceiver", "response": '{"screen":"menu"}'},
        {"step": 4, "role": "perceiver", "response": '{"screen":"world"}'},
    ], {}, 5, {})
    stub.loop = type("Loop", (), {"step_no": 3})()
    assert stub.ask("", "", role="perceiver").data["screen"] == "menu"
    assert stub.ask("", "", role="perceiver").data["screen"] == "world"
    assert stub.ask("", "", role="perceiver").data["screen"] == "world"
    assert stub.ask("", "", role="perceiver").data["screen"] == "world"
    with pytest.raises(eval_act.PointsInconclusive, match="perception fallback > 2"):
        stub.ask("", "", role="perceiver")
    assert isinstance(stub.calls, int) and stub.calls == 5

def test_points_divergence_reports_first_mismatching_step():
    actual = {4: {"map": 1, "x": 2, "y": 3}, 5: {"map": 1, "x": 4, "y": 3}, 6: {"map": 1, "x": 5, "y": 3}}
    expected = {4: actual[4], 5: {"map": 1, "x": 3, "y": 3}, 6: actual[6]}
    checked = []
    first = next((s for s in range(4, 7) if (checked.append(s) or eval_act.points_divergence(s, actual[s], expected[s]) is not None)), None)
    assert first == 5 and checked == [4, 5]

def test_points_checks_cover_right_wrong_section_notes_goal_and_plan():
    case = {
        "right": [{"tool": "select", "label_re": "RED"}],
        "wrong": [{"tool": "select", "label_re": "YES"}],
        "section": {"label_re": "select RED", "state": "new"},
        "note_re": {"label_re": "talk to OAK", "re": "same reply"},
        "goal_re": "Goal: Pick RED",
        "plan_re": "BALL",
    }
    options = [
        {"tool": "select", "label": "select RED", "state": "new", "notes": []},
        {"tool": "select", "label": "select YES", "state": "new", "notes": []},
        {"tool": "approach", "label": "talk to OAK", "state": "failed", "notes": ["same reply"]},
    ]
    checks = eval_act.points_checks(case, options, "Goal: Pick RED\nBecause:", "Planner should choose BALL")
    assert checks and all(ok for _, ok, _ in checks)
    assert not eval_act.points_live_right(case, "select", "select YES")
    assert eval_act.points_live_right(case, "select", "select RED")
    assert eval_act.points_live_right({"right": [], "wrong": [{"tool": "select", "label_re": "YES"}]}, "select", "select RED")
    not_applicable = eval_act.points_checks({"plan_re": "BALL"}, options, "", None)
    assert not_applicable == [("plan_re", True, "BALL: NOT APPLICABLE")]

def test_points_text_checks_match_focus_line_and_planner_view():
    case = {"text_absent": ["speaks to you"], "text_re": [r"Things here:.*(ball|sphere|item)"]}
    prompt = "Trigger: new text\nMap and memory: Things here: shop\nThing A ball\nText on screen now: none"

    checks = eval_act.points_checks(case, [], "Goal: Choose an item\nBecause:", prompt)

    assert checks == [
        ("text_absent", True, "speaks to you: not found"),
        ("text_re", True, r"Things here:.*(ball|sphere|item): matched"),
    ]
    assert not eval_act.points_checks(case, [], "Goal: speaks to you", prompt)[0][1]
    missing_view = eval_act.points_checks(case, [], "", None)
    assert missing_view == [
        ("text_absent", False, "speaks to you: planner view not captured"),
        ("text_re", False, r"Things here:.*(ball|sphere|item): planner view not captured"),
    ]

def test_points_stub_captures_target_plan_prompt_even_when_recording_is_exhausted():
    found = {}
    stub = eval_act.PointsStub([], {}, 5, found)
    stub.loop = type("Loop", (), {"step_no": 4})()
    assert stub.ask("system", "planner text", role="plan").data == {"ops": [], "why": ""}
    assert found["plan_prompt"]["user"] == "planner text" and stub.plan_extra == 1 and stub.calls == 1

@pytest.mark.parametrize("no_cache", [False, True])
def test_points_case_ignores_saved_modes_and_captures_target_without_rom(monkeypatch, tmp_path, no_cache):
    monkeypatch.chdir(tmp_path)
    case = {"run": "fixture", "step": 1, "checkpoint": 0, "situation": "fixture", "right": [{"tool": "select", "label_re": "RED"}], "wrong": []}
    cp = tmp_path / "runs" / "fixture" / "checkpoint" / "000000"
    (cp / "memory").mkdir(parents=True)
    (cp / "pyboy.state").write_bytes(b"state")
    harness = {"step": 0, "modes": {"decide": True, "act_text": False}}
    (cp / "harness.json").write_text(json.dumps(harness), encoding="utf-8")
    run = cp.parents[1]
    (run / "trace.jsonl").write_text(json.dumps({"step": 1, "act": {"action": {"thing": 0, "verb": "select"}}}) + "\n", encoding="utf-8")
    (run / "truth.jsonl").write_text(json.dumps({"step": 1, "map": 7, "x": 2, "y": 3}) + "\n", encoding="utf-8")
    (run / "calls.jsonl").write_text("", encoding="utf-8")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(eval_act, "_hf5_path", lambda name: scratch / name)
    monkeypatch.setattr(eval_act, "find_rom", lambda: "fake-rom")
    monkeypatch.setattr(eval_act, "truth_read", lambda env: {"map": 7, "x": 2, "y": 3})
    monkeypatch.setattr(eval_act, "_budget", lambda: {})
    from agent.model import Ollama, Reply
    import httpx
    cache = tmp_path / "cache"
    cached = Ollama(eval_act.OM_MODEL, cache_dir=cache)
    posted, charged = [], []
    def post(self, payload):
        posted.append(payload)
        return httpx.Response(200, json={"message": {"content": '{"i":1}'}})
    monkeypatch.setattr(Ollama, "_post", post)
    monkeypatch.setattr(eval_act.run_agent, "_ledger", lambda *args: charged.append(args))
    monkeypatch.setattr(eval_act, "MODEL_CACHE", cache)
    monkeypatch.setattr(eval_act, "Memory", lambda path: object())
    envs = []
    class Env:
        tile_px = 16
        def close(self): self.closed = True
    class FakeLoop:
        def __init__(self, env, model, mem, game):
            self.env, self.model, self.step_no = env, model, 0
            self.options = [{"n": 1, "tool": "select", "label": "select RED", "state": "new", "notes": [], "action": {"thing": 0, "verb": "select"}}]
        def restore(self, state): self.step_no = state["step"]
        def step(self): eval_act.loop_mod.act(self.model, {"screen": "menu", "text": [], "entities": []}, "Goal: Select RED\nHere: menu", self.options, [], "")
    monkeypatch.setattr(eval_act, "PyBoyEnv", lambda *a, **k: envs.append(Env()) or envs[-1])
    monkeypatch.setattr(eval_act, "Loop", FakeLoop)
    from agent.act import act
    class Capture:
        def ask(self, system, user, images=(), schema=None, **kwargs):
            cached._store(cache / f"{cached._key(system, user, images, schema)}.json", Reply('{"i":2}', {"i": 2}, 0, None, None))
            return Reply('{"i":1}', {"i": 1}, 0, None, None)
    act(Capture(), {"screen": "menu", "text": [], "entities": []}, "Goal: Select RED\nHere: menu",
        [{"n": 1, "tool": "select", "label": "select RED", "state": "new", "notes": [], "action": {"thing": 0, "verb": "select"}}], [], "")
    result = eval_act._points_case(case, live=no_cache, no_cache=no_cache)
    assert len(posted) == len(charged) == int(no_cache)
    if no_cache: assert result["live_right"]
    assert result["status"] == "PASS" and result["options"][0]["label"] == "select RED"
    assert result["act_prompt"]["user"] and envs[0].closed


def test_points_perception_fallback_uses_last_prior_row_not_future():
    stub = eval_act.PointsStub([
        {"step": 10, "role": "perceiver", "response": {"screen": "menu"}},
        {"step": 12, "role": "perceiver", "response": {"screen": "world"}},
    ], {}, 20, {})
    stub.loop = type("Loop", (), {"step_no": 10})()
    assert stub.ask("", "", role="perceiver").data == {"screen": "menu"}
    assert stub.perception_fallback == 1
    stub.ask("", "", role="perceiver")
    with pytest.raises(eval_act.PointsInconclusive, match="perception fallback > 2"):
        stub.ask("", "", role="perceiver")
    with pytest.raises(eval_act.PointsInconclusive, match="no recorded skill"):
        stub.ask("", "", role="skill")


def test_absent_points_check_fails_on_match_and_passes_without_match():
    case = {"absent": [{"tool": "approach", "label_re": "press here"}]}
    assert eval_act.points_checks(case, [_opt(1, "approach", "press here")]) == [("absent", False, "approach / press here: 1 match(es)")]
    assert eval_act.points_checks(case, [_opt(1, "approach", "walk to door")])[0][1]


def test_eval_memory_on_a_tiny_run(tmp_path):
    from scripts import eval_memory
    run = tmp_path / "run"; memory = run / "memory"; memory.mkdir(parents=True)
    (memory / "places.jsonl").write_text('{"id":"p","screen":"world"}\n', encoding="utf-8")
    (memory / "entities.jsonl").write_text(
        '{"id":"e","place":"p","name":"old","kind":"menu_item","last":1}\n'
        '{"id":"e","place":"p","name":"new","kind":"menu_item","last":2}\n'
        '{"id":"f","place":"p","name":"wall","kind":"wall","first":2}\n', encoding="utf-8")
    rows = [
        {"step": 1, "screen": "menu", "choice": {"intent": "i"}, "skill": {"failure": {"stage": "route"}}},
        {"step": 2, "screen": "battle", "choice": {"intent": "i"}, "skill": {"failure": {"stage": "route"}}},
        {"step": 3, "screen": "world", "choice": {"intent": "i"}, "skill": {"status": "done"}},
        {"step": 4, "screen": "world", "skill": {"failure": {"stage": "crossing"}}},
    ]
    (run / "trace.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    assert eval_memory.analyze(run) == {"world": 2, "modal": 1, "minted": 1, "strict": 1, "loose": 1,
                                         "route": 2, "crossing": 1, "steps": 4, "unreached": 1, "reached": 1}
