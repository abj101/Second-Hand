from scripts.eval_identity import Tally

def info(i, actor=False): return {"id": i, "actor": actor, "hit": None}

def test_switches_split_by_sprite_kind():
    t = Tally()
    for s, ident in enumerate(["a", "b", "b"]): t.see((1, 2), (50, 60), info(ident))  # never moves: static, one switch
    for s, (x, ident) in enumerate([(10, "c"), (11, "c"), (12, "d")]): t.see((1, 3), (x, 60), info(ident, True))  # moves: character, one switch
    r = t.result()
    assert (r["static"]["switches"], r["character"]["switches"]) == (1, 1)
    assert r["static"]["causes"]["static object nearest"] == 1
