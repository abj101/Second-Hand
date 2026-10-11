from PIL import Image

from agent.predict import EFFECTS, effects, verdict
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
