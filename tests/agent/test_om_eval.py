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
    reply = rep.ask("", "", role="act")
    assert reply.data["action"]["i"] == 2

def test_om_talk_reports_conversation_called_entity_and_interact_options(monkeypatch, capsys, tmp_path):
    nb = type("Notebook", (), {"log": [{"step": 2, "text": 'MOM at tiled room: "PROF. OAK, next door, is looking for you."'}]})()
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

class StubModel:
    def __init__(self, replies, posts_per_ask=1):
        self.replies, self.posts_per_ask = list(replies), posts_per_ask
        self.prompts, self.asks, self.posts = [], 0, 0
    def _post(self, payload): self.posts += 1
    def ask(self, system, user, images=(), schema=None, *, role=""):
        self.asks += 1; self.prompts.append((system, user, schema))
        for _ in range(self.posts_per_ask): self._post({})
        return eval_act.Reply("stub", self.replies.pop(0), 0, 0, 0)

def _fake_plan_checkpoint(tmp_path):
    nb = eval_act.Notebook(tmp_path / "memory")
    nb.apply_plan(eval_act.plan_mod.Plan(
        quests=[
            {"key": "oak", "text": "Find Oak", "why": ["oak_line"], "done_when": "speak with Oak", "status": "open"},
            {"key": "lab", "text": "Find the lab", "why": ["lab_line"], "done_when": "reach the lab", "status": "open"},
        ], beliefs=[], facts=[
            {"key": "oak_line", "text": "Prof. Oak next door is looking for you.", "source": "heard"},
            {"key": "lab_line", "text": "Gary is out at Grandpa's lab.", "source": "heard"},
        ], note="", why="fixture"), 49)
    nb.note(49, 'MOM at tiled room: "PROF. OAK, next door, is looking for you."')
    per = {"text": ["dialogue text"]}
    world = type("World", (), {"view": lambda self, goal, place: "memory view", "place_label": lambda self, place: "outside"})()
    loop = type("Loop", (), {"prev": per, "notebook": nb, "world": world, "place": "p1",
                              "_goal_text": lambda self: "Find Oak",
                              "_evidence": lambda self: "heard: Oak and Grandpa's lab\nsteps since progress: 20" + ("\nlast stall changed nothing; revise a belief or reorder" if self.stall_unchanged else ""),
                              "stall_unchanged": 0})()
    env = type("Env", (), {"closed": False, "inputs": lambda self: eval_act._INPUTS,
                             "close": lambda self: setattr(self, "closed", True)})()
    return loop, env

def test_om_plan_dry_prints_migrated_state_prompt_and_token_count_without_model_or_ledger(monkeypatch, tmp_path, capsys):
    loop, env = _fake_plan_checkpoint(tmp_path)
    monkeypatch.setattr(eval_act, "_plan_checkpoint", lambda run, step: (loop, env))
    monkeypatch.setattr(eval_act, "_plan_recent", lambda run, step: ["approach person -> same"])
    monkeypatch.setattr(eval_act, "Ollama", lambda *args, **kwargs: pytest.fail("dry mode created a model"))
    monkeypatch.setattr(eval_act.run_agent, "_ledger", lambda *args: pytest.fail("dry mode touched ledger"))
    assert eval_act.om_plan("fixture", 100, 3, live=False) == 0
    output = capsys.readouterr().out
    assert '"key": "oak"' in output and '"key": "lab"' in output
    assert "Trigger: stuck: no_progress" in output
    assert "New evidence: heard: Oak and Grandpa's lab" in output
    assert "Map and memory: memory view" in output and "dialogue text" in output
    assert "Recent actions: approach person -> same" in output and "tokens~" in output
    assert "SYSTEM + USER, len/4" in output and "requests=0" in output and env.closed

def test_om_plan_cli_defaults_tries_for_exact_dry_syntax(monkeypatch, tmp_path, capsys):
    loop, env = _fake_plan_checkpoint(tmp_path)
    monkeypatch.setattr(eval_act, "_plan_checkpoint", lambda run, step: (loop, env))
    monkeypatch.setattr(eval_act, "_plan_recent", lambda run, step: [])
    monkeypatch.setattr(eval_act, "Ollama", lambda *args, **kwargs: pytest.fail("dry mode created a model"))
    monkeypatch.setattr(eval_act.run_agent, "_ledger", lambda *args: pytest.fail("dry mode touched ledger"))
    assert eval_act.main(["--om", "plan", "--run", "fixture", "--from-step", "100"]) == 0
    assert "requests=0" in capsys.readouterr().out
    assert env.closed

def test_om_plan_live_uses_independent_notebooks_and_caps_retry_posts(monkeypatch, tmp_path, capsys):
    loop, env = _fake_plan_checkpoint(tmp_path)
    replies = [
        {"facts": [], "beliefs": [], "quests": [
            {"key": "oak", "text": "Find Oak", "why": ["oak_line"], "done_when": "speak with Oak", "status": "open"},
            {"key": "lab", "text": "Find the lab", "why": ["lab_line"], "done_when": "reach the lab", "status": "open"},
        ], "note": "Both heard leads remain open.", "why": "The dialogue names both destinations."},
        {"facts": [], "beliefs": [], "quests": [
            {"key": "oak", "text": "Find Oak", "why": ["oak_line"], "done_when": "speak with Oak", "status": "open"},
        ], "note": "Keep looking for Oak.", "why": "I kept one lead."},
        {"facts": [], "beliefs": [], "quests": [], "note": "No leads.", "why": "I forgot the leads."},
    ]
    stub = StubModel(replies)
    ledger = []
    monkeypatch.setattr(eval_act, "_plan_checkpoint", lambda run, step: (loop, env))
    monkeypatch.setattr(eval_act, "_plan_recent", lambda run, step: [])
    monkeypatch.setattr(eval_act, "Ollama", lambda model, *, cache_dir: (pytest.fail("cache enabled") if cache_dir is not None else stub))
    monkeypatch.setattr(eval_act, "_budget", lambda: {"total": len(ledger)})
    monkeypatch.setattr(eval_act.run_agent, "_ledger", lambda *args: ledger.append(args))
    assert eval_act.om_plan("fixture", 100, 3, live=True) == 1  # only one raw reply keeps both heard quests
    assert len(ledger) == stub.posts == 3 and stub.asks == 3
    assert all("Find Oak" in prompt[1] for prompt in stub.prompts)
    output = capsys.readouterr().out
    assert 'successes=1/3; split={"revise_refute": 0, "reorder": 0}; requests=3' in output and "quests=[]" in output
    assert "omitted lab" in output and "reconcile flags=" in output and env.closed

def test_om_plan_caps_each_retry_post(monkeypatch, tmp_path, capsys):
    loop, env = _fake_plan_checkpoint(tmp_path)
    stub = StubModel([{"facts": [], "beliefs": [], "quests": [], "note": "", "why": ""}], posts_per_ask=2)
    ledger = []
    monkeypatch.setattr(eval_act, "_plan_checkpoint", lambda run, step: (loop, env))
    monkeypatch.setattr(eval_act, "_plan_recent", lambda run, step: [])
    monkeypatch.setattr(eval_act, "Ollama", lambda model, *, cache_dir: stub)
    monkeypatch.setattr(eval_act, "_budget", lambda: {"total": len(ledger)})
    monkeypatch.setattr(eval_act.run_agent, "_ledger", lambda *args: ledger.append(args))
    assert eval_act.om_plan("fixture", 100, 1, live=True) == 1
    assert len(ledger) == stub.posts == 1 and stub.asks == 1
    assert "try 1: failed: planner request cap" in capsys.readouterr().out and env.closed

def test_om_plan_stall_pivot_uses_unchanged_evidence_and_reports_belief_change(monkeypatch, tmp_path, capsys):
    loop, env = _fake_plan_checkpoint(tmp_path)
    stub = StubModel([{"facts": [], "quests": [], "beliefs": [
        {"key": "oak_location", "text": "Oak may be elsewhere.", "because": ["oak_line"], "confidence": "mid", "status": "revised", "reason": "The route attempts failed."},
    ], "note": "The location belief changed after the stalled route.", "why": "The evidence does not support the original location."}])
    monkeypatch.setattr(eval_act, "_plan_checkpoint", lambda run, step: (loop, env))
    monkeypatch.setattr(eval_act, "_plan_recent", lambda run, step: [])
    monkeypatch.setattr(eval_act, "Ollama", lambda model, *, cache_dir: stub)
    monkeypatch.setattr(eval_act, "_budget", lambda: {"total": 0})
    monkeypatch.setattr(eval_act.run_agent, "_ledger", lambda *args: None)
    assert eval_act.om_plan("fixture", 125, 1, live=True) == 0
    output = capsys.readouterr().out
    assert loop.stall_unchanged == 1
    assert "last stall changed nothing; revise a belief or reorder" in stub.prompts[0][1]
    assert "oak_location" in output and "route attempts failed" in output and "successes=1/1" in output

def test_om_plan_stall_pivot_accepts_reordering_retained_quests_citing_belief(monkeypatch, tmp_path, capsys):
    loop, env = _fake_plan_checkpoint(tmp_path)
    stub = StubModel([{"facts": [], "beliefs": [
        {"key": "route_blocked", "text": "The usual route may be blocked.", "because": ["oak_line"], "confidence": "mid", "status": "held"},
    ], "quests": [
        {"key": "lab", "text": "Find the lab", "why": ["route_blocked"], "done_when": "reach the lab", "status": "open"},
        {"key": "oak", "text": "Find Oak", "why": ["route_blocked"], "done_when": "speak with Oak", "status": "open"},
    ], "note": "The blocked route changes which lead comes first.", "why": "The route belief supports this order."}])
    monkeypatch.setattr(eval_act, "_plan_checkpoint", lambda run, step: (loop, env))
    monkeypatch.setattr(eval_act, "_plan_recent", lambda run, step: [])
    monkeypatch.setattr(eval_act, "Ollama", lambda model, *, cache_dir: stub)
    monkeypatch.setattr(eval_act, "_budget", lambda: {"total": 0})
    monkeypatch.setattr(eval_act.run_agent, "_ledger", lambda *args: None)
    assert eval_act.om_plan("pivot-reorder", 125, 1, live=True) == 0
    assert "successes=1/1" in capsys.readouterr().out and env.closed

def test_om_plan_stall_pivot_rejects_renames_and_new_keys_as_reordering(monkeypatch, tmp_path, capsys):
    loop, env = _fake_plan_checkpoint(tmp_path)
    monkeypatch.setattr(eval_act, "_plan_checkpoint", lambda run, step: (loop, env))
    monkeypatch.setattr(eval_act, "_plan_recent", lambda run, step: [])
    monkeypatch.setattr(eval_act, "_budget", lambda: {"total": 0})
    monkeypatch.setattr(eval_act.run_agent, "_ledger", lambda *args: None)
    replies = [
        {"facts": [], "beliefs": [{"key": "route_blocked", "text": "Blocked.", "because": ["oak_line"], "confidence": "mid", "status": "held"}],
         "quests": [{"key": "new_lab", "text": "Find the lab", "why": ["route_blocked"], "status": "open"},
                    {"key": "new_oak", "text": "Find Oak", "why": ["route_blocked"], "status": "open"}], "note": "Renamed leads.", "why": "The labels changed."},
        {"facts": [], "beliefs": [{"key": "route_blocked", "text": "Blocked.", "because": ["oak_line"], "confidence": "mid", "status": "held"}],
         "quests": [{"key": "oak", "text": "Find Oak", "why": ["route_blocked"], "status": "open"},
                    {"key": "new", "text": "Explore another route", "why": ["route_blocked"], "status": "open"},
                    {"key": "lab", "text": "Find the lab", "why": ["route_blocked"], "status": "open"}], "note": "Inserted a new lead without changing existing order.", "why": "The list grew."},
    ]
    for i, reply in enumerate(replies):
        stub = StubModel([reply])
        monkeypatch.setattr(eval_act, "Ollama", lambda model, *, cache_dir, stub=stub: stub)
        assert eval_act.om_plan(f"pivot-negative-{i}", 125, 1, live=True) == 1
    assert "successes=0/1" in capsys.readouterr().out and env.closed

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


def test_om_act_size_measures_the_decide_prompt_with_a_capturing_stub_and_no_requests(monkeypatch, tmp_path, capsys):
    nb = eval_act.Notebook(tmp_path / "memory")
    option = {"n": 1, "tool": "approach", "label": "approach door", "action": {"thing": 1}, "notes": []}
    world = type("World", (), {"options": lambda self, per, place, fails, step, inputs: [option],
                               "view": lambda self, goal, place, compact=False: "memory view", "here_flag": lambda self, fails: "here"})()
    loop = type("Loop", (), {"prev": {"screen": "world", "text": ["hello"]}, "notebook": nb, "world": world, "place": "p1", "fails": {},
                              "step_no": 100, "game": "pokemon_red", "_goal_text": lambda self: "goal"})()
    env = type("Env", (), {"closed": False, "inputs": lambda self: eval_act._INPUTS, "close": lambda self: setattr(self, "closed", True)})()
    monkeypatch.setattr(eval_act, "_plan_checkpoint", lambda run, step: (loop, env))
    monkeypatch.setattr(eval_act, "Ollama", lambda *args, **kwargs: pytest.fail("act-size created a model"))
    monkeypatch.setattr(eval_act.run_agent, "_ledger", lambda *args: pytest.fail("act-size touched the ledger"))
    assert eval_act.main(["--om", "act-size", "--run", "fixture", "--from-step", "100"]) == 0
    output = capsys.readouterr().out
    assert "decide prompt chars=" in output and "tokens~" in output and "(len/4)" in output and "requests=0" in output and env.closed
    loop.prev = None
    assert eval_act.om_act_size("fixture", 100) == 2
    assert "no cached perception" in capsys.readouterr().out
