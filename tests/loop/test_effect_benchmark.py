from __future__ import annotations

from pathlib import Path

from loop.eval.effect_benchmark import (
    classify_step_effect,
    run_effect_benchmark,
    same_object_identity,
    score_effect_sequence,
    score_effect_step,
    unchanged_evidence,
)


def test_classify_step_effect_matches_production_semantics() -> None:
    assert classify_step_effect({"dispatch": "ok", "executed": ["click"], "pixel_changed": False}) == "unverified"
    assert classify_step_effect({"dispatch": "ok", "executed": ["click"], "pixel_changed": True}) == "verified"
    assert classify_step_effect({"dispatch": "ok", "executed": ["hover"], "pixel_changed": False, "hover": True}) == "inconclusive"
    assert (
        classify_step_effect(
            {
                "dispatch": "ok",
                "executed": ["hover"],
                "pixel_changed": True,
                "hover": True,
                "global_tiny_change": True,
            }
        )
        == "inconclusive"
    )
    assert classify_step_effect({"dispatch": "target-not-found", "executed": []}) == "none"
    # Production: hover jitter without a screen change is inconclusive even if pixels moved.
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
    assert (
        classify_step_effect(
            {
                "dispatch": "ok",
                "executed": ["hover"],
                "pixel_changed": True,
                "hover": True,
                "screen_changed": True,
            }
        )
        == "verified"
    )


def test_same_object_identity_uses_object_id_not_mark_numbers() -> None:
    left = {"object_id": "legend_elite_icon", "bbox": [0.1, 0.1, 0.2, 0.2], "mark_id": 3}
    right = {"object_id": "legend_elite_icon", "bbox": [0.5, 0.5, 0.6, 0.6], "mark_id": 9}
    different = {"object_id": "map_node_center", "bbox": [0.38, 0.34, 0.46, 0.42], "mark_id": 3}
    assert same_object_identity(left, right)
    assert not same_object_identity(left, different)


def test_same_object_identity_falls_back_to_bbox_overlap() -> None:
    left = {"bbox": [0.86, 0.14, 0.94, 0.22]}
    right = {"bbox": [0.87, 0.15, 0.93, 0.21]}
    far = {"bbox": [0.38, 0.34, 0.46, 0.42]}
    assert same_object_identity(left, right)
    assert not same_object_identity(left, far)


def test_unchanged_evidence_requires_screen_and_fingerprint() -> None:
    left = {"screen": "map", "evidence_fingerprint": "fp-a"}
    right = {"screen": "map", "evidence_fingerprint": "fp-a"}
    screen_changed = {"screen": "combat", "evidence_fingerprint": "fp-a"}
    evidence_changed = {"screen": "map", "evidence_fingerprint": "fp-b"}
    assert unchanged_evidence(left, right)
    assert not unchanged_evidence(left, screen_changed)
    assert not unchanged_evidence(left, evidence_changed)


def test_cycle_repeat_on_same_target_double_noop() -> None:
    steps = [
        {
            "sequence_id": "map_legend_noop_cycle",
            "screen": "map",
            "object_id": "legend_elite_icon",
            "bbox": [0.86, 0.14, 0.94, 0.22],
            "dispatch": "ok",
            "executed": ["click"],
            "pixel_changed": False,
            "evidence_fingerprint": "fp",
        },
        {
            "sequence_id": "map_legend_noop_cycle",
            "screen": "map",
            "object_id": "legend_elite_icon",
            "bbox": [0.86, 0.14, 0.94, 0.22],
            "dispatch": "ok",
            "executed": ["click"],
            "pixel_changed": False,
            "evidence_fingerprint": "fp",
        },
    ]
    report = score_effect_sequence(steps)
    assert report["cycle_repeat"] is True
    assert report["cycle_at"] == 1
    assert report["unresolved_recommended"] is False
    assert [row["effect"] for row in report["steps"]] == ["unverified", "unverified"]


def test_distinct_retry_is_not_cycle_but_recommends_unresolved() -> None:
    steps = [
        {
            "sequence_id": "map_noop_then_distinct",
            "screen": "map",
            "object_id": "legend_elite_icon",
            "bbox": [0.86, 0.14, 0.94, 0.22],
            "dispatch": "ok",
            "executed": ["click"],
            "pixel_changed": False,
            "evidence_fingerprint": "fp",
        },
        {
            "sequence_id": "map_noop_then_distinct",
            "screen": "map",
            "object_id": "map_node_center",
            "bbox": [0.38, 0.34, 0.46, 0.42],
            "dispatch": "ok",
            "executed": ["click"],
            "pixel_changed": False,
            "evidence_fingerprint": "fp",
        },
    ]
    report = score_effect_sequence(steps)
    assert report["cycle_repeat"] is False
    assert report["unresolved_recommended"] is True
    assert report["unresolved_at"] == 1


def test_hover_pixel_jitter_without_global_tiny_flag_is_inconclusive() -> None:
    """Match production: hover + tiny pixel motion, no screen change → inconclusive."""
    assert (
        classify_step_effect(
            {
                "dispatch": "ok",
                "executed": ["hover"],
                "pixel_changed": True,
                "hover": True,
                "screen_changed": False,
            }
        )
        == "inconclusive"
    )


def test_hover_sequence_stays_inconclusive() -> None:
    steps = [
        {
            "dispatch": "ok",
            "executed": ["hover"],
            "pixel_changed": False,
            "hover": True,
        },
        {
            "dispatch": "ok",
            "executed": ["hover"],
            "pixel_changed": True,
            "hover": True,
            "global_tiny_change": True,
        },
    ]
    report = score_effect_sequence(steps)
    assert all(row["effect"] == "inconclusive" for row in report["steps"])


def test_verified_pixel_change() -> None:
    scored = score_effect_step(
        {
            "dispatch": "ok",
            "executed": ["click"],
            "pixel_changed": True,
            "hover": False,
        }
    )
    assert scored["effect"] == "verified"
    assert scored["code"] is None


def test_effect_replay_fixture_covers_phase2_gate_cases() -> None:
    root = Path(__file__).resolve().parents[2]
    labels = root / "loop/fixtures/sts2_screens/effect_replay.jsonl"
    report = run_effect_benchmark(labels)
    assert report["transfer_gate"] == "pending"
    assert report["sequence_count"] == 4
    assert report["step_count"] == 8
    by_id = {seq["sequence_id"]: seq for seq in report["sequences"]}
    assert by_id["map_legend_noop_cycle"]["cycle_repeat"] is True
    assert by_id["map_noop_then_distinct"]["cycle_repeat"] is False
    assert by_id["map_noop_then_distinct"]["unresolved_recommended"] is True
    assert all(row["effect"] == "inconclusive" for row in by_id["hover_tooltip_only"]["steps"])
    assert by_id["verified_map_transition"]["steps"][0]["effect"] == "verified"
