from agent.memory import Memory
from agent.tasks import Stuck, Tasks


def test_place_is_needs_two_settled_steps_and_accepts_place_name(tmp_path):
    mem = Memory(tmp_path)
    mem.upsert("places", {"id": "p_house", "name": "House"})
    tasks = Tasks(mem)
    task = tasks.add("visit house", [{"place_is": "House"}])
    tasks.current_place = "p_house"

    assert tasks.check(1) == []
    assert mem.get("tasks", task["id"])["status"] == "active"
    assert tasks.check(2)[0]["status"] == "done"


def test_unsettled_step_does_not_advance_done_streak(tmp_path):
    tasks = Tasks(Memory(tmp_path))
    task = tasks.add("see prompt", [{"text_seen": "Ready"}])
    tasks.perception = {"text": ["Ready"]}
    tasks.check(1)
    tasks.settled = False
    tasks.check(2)
    tasks.settled = True
    assert tasks.check(3) == []
    assert tasks.check(4)[0]["id"] == task["id"]


def test_confirmation_needs_consecutive_distinct_steps_and_survives_restore(tmp_path):
    mem = Memory(tmp_path)
    tasks = Tasks(mem)
    task = tasks.add("see prompt", [{"text_seen": "Ready"}], [{"steps_over": 200}])
    tasks.perception = {"text": ["Ready"]}
    tasks.check(1)
    tasks.check(1)
    resumed = Tasks(mem)
    resumed.perception = tasks.perception
    assert resumed.check(100) == []
    assert mem.get("tasks", task["id"])["status"] == "active"
    assert resumed.check(101)[0]["status"] == "done"


def test_steps_over_fails_task(tmp_path):
    tasks = Tasks(Memory(tmp_path))
    task = tasks.add("wait", [{"text_seen": "never"}], [{"steps_over": 2}])
    assert tasks.check(1) == []
    assert tasks.check(2) == []
    failed = tasks.check(3)[0]
    assert failed["id"] == task["id"]
    assert failed["status"] == "failed"


def test_default_fails_at_40_and_active_rearms_it(tmp_path):
    tasks = Tasks(Memory(tmp_path))
    first = tasks.active()
    assert first["goal"] == "Reach a place not visited yet"
    assert first["done_when"] == [{"new_place": True}]
    assert first["budget"] == 40
    for step in range(1, 40):
        assert tasks.check(step) == []
    assert tasks.check(40)[0]["status"] == "failed"
    replacement = tasks.active()
    assert replacement["id"] != first["id"]
    assert replacement["used"] == 0
    assert replacement["budget"] == 40
    assert replacement["src"] == "default"


def test_new_place_latches_across_two_step_confirmation_and_restart(tmp_path):
    mem = Memory(tmp_path)
    mem.upsert("places", {"id": "p_start", "edges": [{"to": "p_known", "ok": 1}]})
    tasker = Tasks(mem)
    task = tasker.add("explore", [{"new_place": True}])
    assert task["new_place_baseline"] == 1
    place = mem.get("places", "p_start")
    place["edges"].append({"to": "p_new", "ok": 1})
    mem.upsert("places", place)
    assert tasker.check(1) == []
    assert mem.get("tasks", task["id"])["done_streak"] == 1

    resumed = Tasks(mem)
    assert resumed.check(2)[0]["status"] == "done"


def test_reaching_a_known_place_does_not_satisfy_new_place(tmp_path):
    mem = Memory(tmp_path)
    mem.upsert("places", {"id": "p_start", "edges": [{"to": "p_known", "ok": 1}]})
    mem.upsert("places", {"id": "p_side", "edges": []})
    tasks = Tasks(mem)
    task = tasks.add("explore", [{"new_place": True}])
    mem.add_edge("p_side", "approach", "door", "p_known", True)
    assert tasks.check(1) == []
    assert tasks.check(2) == []
    assert mem.get("tasks", task["id"])["status"] == "active"


def test_model_goal_blocks_and_then_rearms_fallback(tmp_path):
    tasks = Tasks(Memory(tmp_path))
    fallback = tasks.active()
    goal = tasks.add("find Oak", [{"steps_over": 100}])
    assert tasks.active()["id"] == goal["id"]
    assert tasks.mem.get("tasks", fallback["id"])["status"] == "blocked"
    assert goal["fail_when"] == [{"steps_over": 40}]

    goal.update(status="done")
    tasks.mem.upsert("tasks", goal)
    rearmed = tasks.active()
    assert rearmed["id"] != fallback["id"]
    assert rearmed["status"] == "active"
    assert rearmed["used"] == 0


def test_model_deadline_counts_from_goal_creation(tmp_path):
    tasks = Tasks(Memory(tmp_path))
    tasks.step = 54
    goal = tasks.add("find Oak", [{"text_seen": "never"}])
    assert all(tasks.check(step) == [] for step in range(55, 95))
    assert tasks.check(95)[0]["id"] == goal["id"]


def test_legacy_default_checkpoint_gets_new_fallback(tmp_path):
    mem = Memory(tmp_path)
    old = mem.upsert("tasks", {"id": "t_old", "goal": "Explore and test the world model",
        "done_when": [], "fail_when": [], "status": "active", "budget": 150, "used": 54,
        "src": "default"})
    task = Tasks(mem).active()
    assert task["id"] != old["id"]
    assert task["goal"] == "Reach a place not visited yet"
    assert task["budget"] == 40
    assert mem.get("tasks", old["id"])["status"] == "blocked"


def test_add_rejects_already_true_done_conditions_from_game_text(tmp_path):
    mem = Memory(tmp_path)
    mem.know("PROF. OAK is looking for you", src="game_text", step=1)
    tasks = Tasks(mem)
    try:
        tasks.add("find Oak", [{"text_seen": "looking for you"}])
    except ValueError:
        pass
    else:
        raise AssertionError("already-true done condition was accepted")
    assert mem.rows("tasks") == []


def test_active_returns_deepest_task(tmp_path):
    tasks = Tasks(Memory(tmp_path))
    root = tasks.add("root", [{"steps_over": 500}])
    child = tasks.add("child", [{"steps_over": 500}], parent=root["id"])
    grandchild = tasks.add("grandchild", [{"steps_over": 500}], parent=child["id"])
    assert tasks.active()["id"] == grandchild["id"]


def test_known_entities_and_game_text_are_global_but_model_relations_are_not(tmp_path):
    mem = Memory(tmp_path)
    mem.upsert("entities", {"id": "e_npc", "name": "Guide", "place": "p_other"})
    mem.know("The gate is locked", src="game_text", step=1)
    mem.upsert("relations", {"id": "r_claim", "s": "e_gate", "p": "opens", "o": "p_room",
                              "src": "model", "state": "live", "sup": 0, "con": 0, "ev": []})
    tasks = Tasks(mem)
    task = tasks.add("check facts", [{"relation": ["e_gate", "opens", "p_room"]}])
    assert tasks.check(1) == []
    assert mem.get("tasks", task["id"])["done_streak"] == 0
    mem.relate("e_gate", "opens", "p_room", src="observed", step=2, hit=True)
    tasks.check(2)
    assert tasks.check(3)[0]["status"] == "done"


def test_stuck_counts_zero_affordance_and_eight_no_change_steps():
    stuck = Stuck()
    assert [stuck.update({"options": 0}) for _ in range(7)] == [None] * 7
    assert stuck.update({"options": 0}) == "no_change"
    assert stuck.n == 8


def test_stuck_fires_on_third_same_action_on_same_frame_and_roundtrips():
    stuck = Stuck()
    row = {"choice": {"verb": "approach", "target": "door"}, "frame_hash": "abc"}
    assert stuck.update(row) is None
    assert stuck.update(row) is None
    assert stuck.update(row) == "same_action"

    restored = Stuck()
    restored.restore(stuck.state())
    assert restored.action == stuck.action
    assert restored.hash == "abc"

    legacy = Stuck()
    legacy.restore({"n": 2, "action": ["approach", "door", None],
                   "hash": "abc", "same_action": 2})
    assert legacy.update(row) == "same_action"


def test_stuck_counts_nonconsecutive_action_frame_repeats_across_restore():
    stuck = Stuck()
    repeat = {"choice": {"verb": "interact", "target": "console"}, "frame_hash": "abc"}
    other = {"choice": {"verb": "wait"}, "frame_hash": "abc"}
    assert stuck.update(repeat) is None
    assert stuck.update(other) is None

    restored = Stuck()
    restored.restore(stuck.state())
    assert restored.update(repeat) is None
    assert restored.update(repeat) == "same_action"
    assert restored.update(repeat) is None


def test_stuck_escalates_after_25_world_steps_without_new_place_or_edge():
    stuck = Stuck()
    row = {"screen": "world", "place": "p_start", "next_place": "p_start"}
    assert all(stuck.update(row) is None for _ in range(23))
    assert stuck.update({**row, "screen": "menu"}) is None
    assert stuck.update(row) is None
    assert stuck.update(row) == "no_progress"


def test_stuck_new_place_resets_counter_and_restores_visited_places():
    stuck = Stuck()
    row = {"screen": "world", "place": "p_start", "next_place": "p_start"}
    assert all(stuck.update(row) is None for _ in range(23))

    resumed = Stuck()
    resumed.restore(stuck.state())
    assert resumed.progress_n == 23
    assert resumed.seen_places == {"p_start"}
    new_place = {**row, "place": "p_new", "next_place": "p_new"}
    assert resumed.update(new_place) is None
    assert resumed.progress_n == 0

    backtrack = {**row, "place": "p_new", "next_place": "p_start",
                 "outcome": {"effects": ["place_changes"], "basis": {"place_changes": "pixel"}}}
    assert resumed.update(backtrack) is None
    assert resumed.progress_n == 1
    assert resumed.seen_places == {"p_start", "p_new"}


def test_stuck_new_verified_edge_resets_no_progress_counter():
    stuck = Stuck()
    row = {"screen": "world", "place": "p_start", "next_place": "p_start"}
    assert all(stuck.update(row) is None for _ in range(24))
    assert stuck.update({**row, "verified_edge": True}) is None
    assert stuck.progress_n == 0
