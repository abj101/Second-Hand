from __future__ import annotations

from loop.eval.effect_benchmark import classify_step_effect


def test_classify_step_effect_matches_production_semantics() -> None:
    assert classify_step_effect({"dispatch": "ok", "executed": ["click"], "pixel_changed": False}) == "unverified"
    assert classify_step_effect({"dispatch": "ok", "executed": ["click"], "pixel_changed": True}) == "verified"
    assert classify_step_effect({"dispatch": "target-not-found", "executed": []}) == "none"
    assert (
        classify_step_effect(
            {
                "dispatch": "ok",
                "executed": ["hover"],
                "pixel_changed": True,
                "hover": True,
            }
        )
        == "inconclusive"
    )
