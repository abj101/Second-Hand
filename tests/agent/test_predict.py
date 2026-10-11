from PIL import Image

from agent.memory import Memory
from agent.observer import cut
from agent.predict import EFFECTS, PRED, effects, predict, verdict
from agent.skills import SkillResult


def result(before=None, after=None, total=(0, 0), blank=False):
    before = before or Image.new("RGB", (32, 32), "black")
    after = after or before.copy()
    return SkillResult("done", "", 1, 0, before, after, total, blank)


def test_effect_basis_uses_pixels_or_model_lists():
    old = {"text": [], "entities": []}
    new = {"text": ["Hello"], "entities": [{"kind": "npc", "name": "Guide", "at": [.5, .5]}]}
    before = Image.new("RGB", (32, 32), "black")
    after = before.copy(); after.putpixel((2, 2), (255, 0, 0))
    out = effects(old, new, result(before, after, (4, 0), True), "town", "house")
    assert out["effects"] == ["place_changes", "text_appears", "screen_changes",
                              "entity_appears", "view_scrolled"]
    assert out["basis"] == {"place_changes": "pixel", "text_appears": "model",
                             "screen_changes": "pixel", "entity_appears": "model",
                             "view_scrolled": "pixel"}
    assert out["to"] == "house" and EFFECTS[-1] == "no_change"


def test_verdict_hit_partial_surprise_miss_and_unobservable():
    pred = {"effect": "place_changes", "detail": "house"}
    assert verdict(pred, {"effects": ["place_changes"], "to": "house"}) == "hit"
    assert verdict(pred, {"effects": ["place_changes"], "to": "cave"}) == "partial"
    assert verdict(pred, {"effects": ["text_appears"]}) == "surprise"
    assert verdict(pred, {"effects": ["no_change"]}) == "miss"
    assert verdict(pred, {}) == "unobservable"


def test_predict_ignores_model_claims_and_picks_highest_confidence(tmp_path):
    mem = Memory(tmp_path)
    mem.upsert("relations", {"id": "r_model", "s": "e_door", "p": "leads_to", "o": "p_fake",
                              "src": "model", "sup": 100, "con": 0, "state": "live"})
    mem.upsert("relations", {"id": "r_low", "s": "e_door", "p": "opens", "o": "menu",
                              "src": "observed", "sup": 1, "con": 0, "state": "live"})
    mem.upsert("relations", {"id": "r_high", "s": "e_door", "p": "leads_to", "o": "p_house",
                              "src": "observed", "sup": 5, "con": 0, "state": "live"})
    mem.upsert("relations", {"id": "r_refuted", "s": "e_door", "p": "says", "o": "hello",
                              "src": "observed", "sup": 10, "con": 11, "state": "refuted"})

    pred = predict(mem, {"verb": "approach", "target": "door"}, "e_door", "p_town")
    assert pred == {"basis": "instance", "id": "r_high", "effect": "place_changes",
                    "detail": "p_house", "conf": 6 / 7}
    assert PRED == {"place_changes": "leads_to", "text_appears": "says", "screen_changes": "opens",
                    "entity_appears": "reveals", "entity_disappears": "removes",
                    "view_scrolled": "moves_view", "no_change": "does_nothing"}


def test_cut_into_candidate_is_screen_change():
    before = Image.new("RGB", (32, 32), "black")
    after = Image.new("RGB", (32, 32), "white")
    out = effects({}, {}, result(before, after, blank=True), "town", "menu",
                  place_after_candidate=True, scene_cut=True)
    assert out["effects"] == ["screen_changes"]
    assert out["basis"] == {"screen_changes": "pixel"}
    assert out["to"] is None


def test_registered_scroll_can_suppress_a_scene_cut_place_change():
    before = Image.new("RGB", (32, 32), "black")
    after = Image.new("RGB", (32, 32), "white")
    res = result(before, after, (4, 0))
    assert cut(res.before, res.after, res.blank_seen)

    out = effects({}, {}, res, "town", "town", scene_cut=False)

    assert out["effects"] == ["screen_changes", "view_scrolled"]
    assert out["basis"] == {"screen_changes": "pixel", "view_scrolled": "pixel"}
    assert out["to"] is None
