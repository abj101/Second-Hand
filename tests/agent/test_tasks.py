from agent.tasks import Stuck


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
