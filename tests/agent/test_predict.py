import json
from pathlib import Path

from PIL import Image

from agent.observer import cut
from agent.predict import EFFECTS, effects, result as classify
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
    assert out["effects"] == ["place_changes", "text_appears", "screen_changes", "view_scrolled"]
    assert out["basis"] == {"place_changes": "pixel", "text_appears": "model",
                             "screen_changes": "pixel", "view_scrolled": "pixel"}
    assert out["to"] == "house" and EFFECTS[-1] == "no_change"


def test_result_prioritizes_novelty_then_failure_then_no_change():
    assert classify("blocked", ["no_change"], True) == "changed"
    assert classify("failed", ["view_scrolled"], True) == "changed"
    assert classify("failed", ["screen_changes"], False) == "failed"
    assert classify("done", ["no_change"], False) == "no_change"
    assert classify("done", ["screen_changes"], False) == "changed"


def test_m1_surprises_score_novelty_without_entity_churn():
    path = Path(__file__).parent / "data" / "r4_m1_rows.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf8").splitlines()]
    surprises = [r for r in rows if r["verdict"] == "surprise"]
    assert len(surprises) == 23
    outcomes = {}
    for row in surprises:
        effects = [e for e in row["outcome"]["effects"] if not e.startswith("entity_")]
        novel = tuple(row["scroll"]) != (0, 0) or row["next_place"] != row["place"]
        outcomes[row["step"]] = classify(row["skill"]["status"], effects, novel)
    unmoved = [r for r in surprises if r["moved"] is False]
    assert len(unmoved) == 13
    assert all(outcomes[r["step"]] == "failed" for r in unmoved)
    assert outcomes[100] == "changed"
    step_105 = next(r for r in surprises if r["step"] == 105)
    assert step_105["moved"] is True and step_105["skill"]["status"] == "done"
    assert tuple(step_105["scroll"]) != (0, 0) and outcomes[105] == "progress"
    assert set(outcomes.values()) <= {"failed", "progress", "changed", "no_change"}


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
