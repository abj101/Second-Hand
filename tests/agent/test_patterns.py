from agent.memory import Memory, normalize_kind
from agent.predict import predict


def _door(mem, id, kind="door"):
    mem.upsert("entities", {"id": id, "kind": kind, "name": id, "place": "home"})


def _place(mem, id):
    mem.upsert("places", {"id": id, "name": id, "state": "place"})


def _hit(mem, door, place="room"):
    return mem.relate(door, "leads_to", place, src="observed", step=1,
                      hit=True, verb="approach")


def test_patterns_need_distinct_instances_and_promote(tmp_path):
    mem = Memory(tmp_path)
    _place(mem, "room")
    for id, kind in (("d1", "doors"), ("d2", "Door"), ("d3", "doorway")):
        _door(mem, id, kind)

    _hit(mem, "d1")
    assert mem.rows("patterns") == []
    _hit(mem, "d1")
    assert mem.rows("patterns") == []
    _hit(mem, "d2")
    pattern, = mem.rows("patterns")
    assert (pattern["s"], pattern["p"], pattern["o"], pattern["verb"]) == (
        "door", "leads_to", "place", "approach")
    assert (pattern["sup"], pattern["state"], len(pattern["inst"])) == (2, "candidate", 2)
    _hit(mem, "d3")
    pattern, = mem.rows("patterns")
    assert (pattern["sup"], pattern["state"], len(pattern["inst"])) == (3, "promoted", 3)


def test_pattern_demotes_after_two_of_last_five_misses(tmp_path):
    mem = Memory(tmp_path)
    _place(mem, "room")
    for id in ("d1", "d2", "d3"):
        _door(mem, id)
        _hit(mem, id)
    pattern, = mem.rows("patterns")

    for i, door in enumerate(("d1", "d2")):
        mem.relate(door, "leads_to", "", src="observed", step=10 + i,
                   hit=False, verb="approach", pattern_id=pattern["id"])

    pattern, = mem.rows("patterns")
    assert (pattern["sup"], pattern["con"], pattern["state"]) == (3, 2, "demoted")


def test_unrelated_instance_outcomes_do_not_advance_pattern_use_history(tmp_path):
    mem = Memory(tmp_path)
    _place(mem, "room")
    for id in ("d1", "d2", "d3", "d4", "d5"):
        _door(mem, id)
    for id in ("d1", "d2", "d3"):
        _hit(mem, id)
    pattern, = mem.rows("patterns")

    mem.relate("d1", "leads_to", "", src="observed", step=10,
               hit=False, verb="approach", pattern_id=pattern["id"])
    for i, door in enumerate(("d2", "d3", "d4", "d5")):
        mem.relate(door, "leads_to", "room", src="observed", step=11 + i,
                   hit=False, verb="approach")

    pattern, = mem.rows("patterns")
    assert pattern["uses"] == [False]
    assert (pattern["con"], pattern["state"]) == (5, "candidate")


def test_model_and_inferred_relations_never_support_patterns(tmp_path):
    mem = Memory(tmp_path)
    _place(mem, "room")
    _door(mem, "d1")
    _door(mem, "d2")
    for door, src in (("d1", "model"), ("d2", "inferred")):
        mem.relate(door, "leads_to", "room", src=src, step=1,
                   hit=True, verb="approach")
    assert mem.rows("patterns") == []


def test_predict_uses_promoted_pattern_only_without_matching_instance(tmp_path):
    mem = Memory(tmp_path)
    _place(mem, "room")
    for id in ("d1", "d2", "d3", "d4"):
        _door(mem, id)
    for id in ("d1", "d2", "d3"):
        _hit(mem, id)
    pattern, = mem.rows("patterns")

    inferred = predict(mem, {"verb": "approach"}, "d4", "home")
    assert inferred == {"basis": "pattern", "id": pattern["id"],
                        "effect": "place_changes", "detail": None, "conf": .8}
    mem.relate("d4", "leads_to", "room", src="observed", step=2,
               hit=True, verb="inspect")
    assert predict(mem, {"verb": "approach"}, "d4", "home")["basis"] == "pattern"
    assert predict(mem, {"verb": "inspect"}, "d4", "home")["basis"] == "instance"
    mem.relate("d4", "leads_to", "room", src="observed", step=2,
               hit=True, verb="approach")
    assert predict(mem, {"verb": "approach"}, "d4", "home")["basis"] == "instance"


def test_kind_normalization():
    assert normalize_kind("doors") == normalize_kind("Door") == normalize_kind("doorways") == "door"
    assert normalize_kind("exit: door") == "exit"
