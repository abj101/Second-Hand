"""Matcher for fusion eval. No frames or weights."""

import hashlib
import json
import os
from types import SimpleNamespace

from PIL import Image

import benchmarks.visual_object_fusion as evaluator
from benchmarks.visual_object_fusion import (
    _frame_identity_problems,
    _oversized_offer_ids,
    localization_diagnostics,
    match_objects,
    offer_counters,
    qualification_verdict,
    temporal_sequence_metrics,
)
from loop.adaptive.schema import SceneGraph, SceneNode


def test_center_hit_and_iou_assignment():
    expected = [
        {"id": "end", "kind": "control", "bbox": [0.8, 0.8, 0.95, 0.9]},
        {"id": "card", "kind": "card", "bbox": [0.2, 0.7, 0.35, 0.95]},
        {"id": "player", "kind": "actor", "bbox": [0.1, 0.3, 0.3, 0.7]},
    ]
    predicted = [
        {"id": "trk_end", "bbox": [0.81, 0.81, 0.94, 0.89]},
        {"id": "trk_card", "bbox": [0.21, 0.71, 0.34, 0.94]},
        {"id": "extra", "bbox": [0.01, 0.01, 0.05, 0.05]},
    ]
    result = match_objects(expected, predicted)
    assert result["misses"] == ["player"]
    assert {row["gt"] for row in result["hits"]} == {"end", "card"}
    assert "extra" in result["extras"]


def test_diagnostics_separate_safe_point_from_full_box_overlap():
    expected = [
        {"id": "button", "kind": "control", "role": "ui", "bbox": [0, 0, 1, 1]},
        {"id": "player", "kind": "actor", "role": "world", "bbox": [0, 0, 0.4, 0.4]},
        {"id": "card", "kind": "card", "role": "ui", "bbox": [0.5, 0.5, 0.9, 0.9]},
    ]
    predicted = [
        {"id": "button", "role": "ui", "bbox": [0.45, 0.45, 0.55, 0.55]},
        {"id": "player", "role": "world", "bbox": [0.05, 0.05, 0.25, 0.25]},
        {"id": "card", "role": "ui", "bbox": [0.5, 0.5, 0.9, 0.9]},
    ]
    result = localization_diagnostics(expected, predicted)
    assert result == {
        "control": {"metric": "safe_point", "expected": 1, "matched": 1, "missed": 0},
        "actor": {"metric": "full_box", "expected": 1, "matched": 0, "missed": 1},
        "card": {"metric": "full_box", "expected": 1, "matched": 1, "missed": 0},
    }


def test_manual_qwen_profile_is_supplemental_not_policy_loaded(tmp_path, monkeypatch):
    monkeypatch.setattr(evaluator, "EVAL", tmp_path)
    (tmp_path / "qwen_resident_e_path_profile.json").write_text(json.dumps({
        "qwen_resident": True,
        "model": "Qwen/test",
        "status": "ok",
        "qwen_loader_note": "manual local CUDA load",
        "samples": [{"total_ms": 1200}],
        "stage_stats": {"ocr_ms": {"p50_ms": 800}},
    }), encoding="utf-8")
    profile = evaluator._qwen_resident_profile()
    assert profile["qwen_policy_loaded"] is False
    assert profile["method"] == "manual local CUDA load; separate from normal policy loader"
    assert profile["samples"] == 1


def test_manual_qwen_profile_does_not_satisfy_frozen_gate():
    frame = "f"
    report = {
        "arms": [{"arm": "fused", "status": "ok", "latency_p95_ms": 100,
                  "control_id_retention": 1.0, "frames": []}],
        "missing_surfaces": [],
        "qwen_resident": False,
        "qwen_resident_profile": {"qwen_policy_loaded": False, "status": "ok"},
    }
    verdict = qualification_verdict(report, {"frames": {frame: {"objects": []}}})
    assert "Qwen coresident test absent" in verdict["arm_failures"]["fused"]


def test_replay_passes_callable_ocr_to_assembler(monkeypatch):
    calls = []
    selected_sizes = []
    from loop.adaptive import cli

    def fake_ocr(image, *, small=False):
        selected_sizes.append(small)
        return [("token", (0, 0, 1, 1))]

    monkeypatch.setattr(cli, "_live_ocr_tokens", fake_ocr)

    class Scene:
        nodes = []

    tracker = SimpleNamespace(status="ok", name="fused-small-test", require_visual_ownership=True)

    class Assembler:
        tracker_status = "ok"

        def __init__(self, tracker):
            self._tracker = tracker

        def update(self, image, *, ocr):
            assert callable(ocr)
            calls.append(ocr(image))
            return (), Scene()

    monkeypatch.setattr(evaluator, "SceneAssembler", Assembler)
    monkeypatch.setattr(evaluator, "open_tracker", lambda name: tracker)
    monkeypatch.setattr(evaluator, "_load_frame", lambda path: object())
    monkeypatch.setattr(evaluator, "_cuda_sync", lambda: None)
    monkeypatch.setattr(evaluator, "_preds", lambda scene: [])
    monkeypatch.setattr(evaluator, "registry_offers", lambda scene: [])
    monkeypatch.setattr(evaluator, "offer_decisions", lambda scene: [])
    monkeypatch.setattr(evaluator, "_vram_mib", lambda: None)

    result = evaluator.replay_arm("fused", {"frame": {"path": "frame.bin", "objects": []}})
    assert selected_sizes == [True]
    assert calls == [[("token", (0, 0, 1, 1))]]
    assert result["ocr_model"] == "RapidOCR Small"
    assert result["frames"][0]["ocr_calls"] == 1


def test_duplicate_and_role_mismatch_are_counted():
    expected = [{"id": "end", "role": "ui", "kind": "control", "bbox": [0.8, 0.8, 0.95, 0.9]}]
    predicted = [
        {"id": "ui_1", "role": "ui", "bbox": [0.81, 0.81, 0.94, 0.89]},
        {"id": "ui_2", "role": "ui", "bbox": [0.82, 0.82, 0.93, 0.88]},
        {"id": "world", "role": "world", "bbox": [0.81, 0.81, 0.94, 0.89]},
    ]
    result = match_objects(expected, predicted)
    assert len(result["hits"]) == 1
    assert result["duplicates"] == 1
    assert result["duplicate_ids"] == ["ui_2"]
    assert "world" in result["extras"]


def test_unavailable_arm_cannot_win_qualification():
    report = {
        "arms": [{"arm": "fused", "status": "unavailable", "frames": []}],
        "missing_surfaces": [],
        "qwen_resident": False,
    }
    verdict = qualification_verdict(report, {"frames": {}})
    assert verdict["E"] == "FAIL"
    assert verdict["F"] == "BLOCKED_BY_E"
    assert verdict["winner"] is None
    assert verdict["default_tracker"] == "none"


def test_detected_control_must_also_be_an_offer():
    frame = "f"
    report = {
        "arms": [{
            "arm": "fused",
            "status": "ok",
            "latency_p95_ms": 100,
            "control_id_retention": 1.0,
            "frames": [{
                "frame": frame,
                "matched": {"hits": [{"gt": "button", "pred": "track_1"}], "duplicates": 0},
                "offer_ids": [],
                "oversized_offers": 0,
                "false_offer_ids": [],
            }],
        }],
        "missing_surfaces": [],
        "qwen_resident": True,
    }
    annotations = {"frames": {frame: {"objects": [
        {"id": "button", "kind": "control", "role": "ui", "bbox": [0, 0, 1, 1], "offerable": True}
    ]}}}
    verdict = qualification_verdict(report, annotations)
    assert "f: detected controls withheld from offers" in verdict["arm_failures"]["fused"]


def test_rejected_oversized_is_not_counted_as_an_offer():
    graph = SceneGraph(
        "s",
        (SceneNode("n1", (0.0, 0.0, 1.0, 1.0), (), {
            "registry_id": "hand", "label": "hand", "track_source": "gpa-gui",
        }),),
    )
    counters = offer_counters(graph)
    assert counters["oversized_offers"] == 0
    assert counters["n_offers"] == 0
    assert counters["source_rejections"] == {"oversized": 1}


def test_offered_oversized_box_is_flagged():
    offers = [
        SimpleNamespace(offer_id="big", node_id="n_big"),
        SimpleNamespace(offer_id="small", node_id="n_small"),
    ]
    boxes = {"n_big": (0.0, 0.0, 1.0, 0.9), "n_small": (0.0, 0.0, 0.2, 0.2)}
    assert _oversized_offer_ids(offers, boxes) == ["big"]


def test_unavailable_offer_diagnostic_is_none_not_zero():
    counters = offer_counters(None)
    assert counters["oversized_offers"] is None
    assert counters["source_rejections"] is None
    assert counters["n_offers"] is None


def test_ambiguous_assignment_is_deterministic_and_flagged():
    expected = [
        {"id": "a", "kind": "control", "role": "ui", "bbox": [0, 0, 1, 1]},
        {"id": "b", "kind": "control", "role": "ui", "bbox": [0, 0, 1, 1]},
    ]
    predicted = [{"id": "p1", "role": "ui", "bbox": [0.4, 0.4, 0.6, 0.6]}]
    result = match_objects(expected, predicted)
    assert result["ambiguous"] is True
    assert result["hits"] == [{"gt": "a", "pred": "p1", "score": 1.0}]
    assert result["misses"] == ["b"]


def test_matching_is_independent_of_prediction_order():
    expected = [
        {"id": "left", "kind": "control", "role": "ui", "bbox": [0.0, 0.0, 0.4, 0.4]},
        {"id": "right", "kind": "control", "role": "ui", "bbox": [0.6, 0.6, 1.0, 1.0]},
    ]
    p_left = {"id": "p_left", "role": "ui", "bbox": [0.1, 0.1, 0.3, 0.3]}
    p_right = {"id": "p_right", "role": "ui", "bbox": [0.7, 0.7, 0.9, 0.9]}
    mapping = lambda result: {hit["gt"]: hit["pred"] for hit in result["hits"]}  # noqa: E731
    forward = match_objects(expected, [p_left, p_right])
    reverse = match_objects(expected, [p_right, p_left])
    assert mapping(forward) == mapping(reverse) == {"left": "p_left", "right": "p_right"}
    assert forward["ambiguous"] is False


def _complete_annotation():
    return {"frames": {"f1": {"objects": [
        {"id": "btn", "kind": "control", "role": "ui", "bbox": [0, 0, 1, 1], "offerable": True},
        {"id": "actor", "kind": "actor", "role": "world", "bbox": [0, 0, 0.5, 0.5], "offerable": False},
    ]}}}


def _passing_row(frame="f1", **overrides):
    row = {
        "frame": frame,
        "matched": {
            "hits": [{"gt": "btn", "pred": "t1"}, {"gt": "actor", "pred": "t2"}],
            "duplicates": 0,
            "ambiguous": False,
            "ambiguous_pairs": [],
        },
        "offer_ids": ["t1"],
        "oversized_offers": 0,
        "false_offer_ids": [],
        "frame_identity_problems": [],
    }
    row.update(overrides)
    return row


def _gate(rows, **arm_overrides):
    arm = {
        "arm": "fused",
        "status": "ok",
        "latency_p95_ms": 100,
        "control_id_retention": 1.0,
        "role_metrics": {"ui": {"matched": 1}, "world": {"matched": 1}},
        "frames": rows,
    }
    arm.update(arm_overrides)
    return {"arms": [arm], "missing_surfaces": [], "qwen_resident": True}


def test_missing_required_frame_row_fails_gate():
    verdict = qualification_verdict(_gate([]), _complete_annotation())
    assert "f1: missing required frame row" in verdict["arm_failures"]["fused"]


def test_missing_required_role_diagnostic_fails_gate():
    verdict = qualification_verdict(
        _gate([_passing_row()], role_metrics={"ui": {"matched": 1}}), _complete_annotation()
    )
    assert "role diagnostic missing: world" in verdict["arm_failures"]["fused"]


def test_frame_identity_problem_fails_gate():
    row = _passing_row(frame_identity_problems=["digest mismatch"])
    verdict = qualification_verdict(_gate([row]), _complete_annotation())
    assert "f1: frame identity digest mismatch" in verdict["arm_failures"]["fused"]


def test_unavailable_oversized_diagnostic_fails_gate_closed():
    row = _passing_row(oversized_offers=None)
    verdict = qualification_verdict(_gate([row]), _complete_annotation())
    assert "f1: oversized-offer diagnostic unavailable" in verdict["arm_failures"]["fused"]


def test_strict_identity_requires_digest_and_size(tmp_path):
    spec = {"path": str(tmp_path / "f.bin")}
    problems = _frame_identity_problems(spec, frame_size=None, strict=True)
    assert "digest missing" in problems
    assert "frame size missing" in problems


def test_strict_identity_detects_digest_and_size_mismatch(tmp_path):
    path = tmp_path / "f.bin"
    path.write_bytes(b"abc")
    bad = {"path": str(path), "sha256": hashlib.sha256(b"other").hexdigest()}
    problems = _frame_identity_problems(bad, frame_size=[2, 2], strict=True)
    assert "digest mismatch" in problems
    assert "size mismatch: 3 != 12 bytes" in problems
    good = {"path": str(path), "sha256": hashlib.sha256(b"abc").hexdigest()}
    assert _frame_identity_problems(good, frame_size=[1, 1], strict=True) == []


def _seq_frame(objects):
    return {"objects": objects}


def _seq_object(obj_id, physical_id, offerable=False):
    return {
        "id": obj_id,
        "physical_id": physical_id,
        "kind": "card" if not offerable else "control",
        "role": "ui",
        "bbox": [0, 0, 0.1, 0.1],
        "offerable": offerable,
    }


def _obs_row(frame, *hits):
    return {
        "frame": frame,
        "matched": {"hits": [{"gt": gt, "pred": pred} for gt, pred in hits], "duplicates": 0},
    }


def _sequence(seq_id, frames, start, end):
    return {
        "sequence_id": seq_id,
        "boundary": {"kind": "episode", "start_frame": start, "end_frame": end},
        "frames": list(frames),
    }


def test_temporal_switch_is_reported_as_incorrect_association():
    annotations = {
        "frames": {
            "f1": _seq_frame([_seq_object("a", "P")]),
            "f2": _seq_frame([_seq_object("a2", "P")]),
        },
        "sequences": [_sequence("s1", ["f1", "f2"], "f1", "f2")],
    }
    rows = [_obs_row("f1", ("a", "t1")), _obs_row("f2", ("a2", "t2"))]
    result = temporal_sequence_metrics(annotations, rows)
    assert result["available"] is True
    totals = result["totals"]
    assert totals["expected_visible_opportunities"] == 1
    assert totals["correct_associations"] == 0
    assert totals["incorrect_associations"] == 1
    assert totals["switches"] == 1
    assert totals["misses"] == 0
    assert totals["fragmentation"] == 1


def test_temporal_misses_cover_all_expected_adjacent_visible_pairs():
    annotations = {
        "frames": {
            "f1": _seq_frame([_seq_object("a", "P")]),
            "f2": _seq_frame([_seq_object("a2", "P")]),
            "f3": _seq_frame([_seq_object("a3", "P")]),
        },
        "sequences": [_sequence("s1", ["f1", "f2", "f3"], "f1", "f3")],
    }
    rows = [_obs_row("f1", ("a", "t1")), _obs_row("f2", ("a2", "t1")), _obs_row("f3")]
    result = temporal_sequence_metrics(annotations, rows)
    totals = result["totals"]
    assert totals["expected_visible_opportunities"] == 2
    assert totals["misses"] == 1
    assert totals["correct_associations"] == 1
    assert totals["incorrect_associations"] == 0


def test_temporal_disappearance_and_return_with_id_change():
    annotations = {
        "frames": {
            "f1": _seq_frame([_seq_object("a", "P")]),
            "f2": _seq_frame([_seq_object("a2", "P")]),
            "f3": _seq_frame([_seq_object("a3", "P")]),
        },
        "sequences": [_sequence("s1", ["f1", "f2", "f3"], "f1", "f3")],
    }
    rows = [_obs_row("f1", ("a", "t1")), _obs_row("f2"), _obs_row("f3", ("a3", "t2"))]
    result = temporal_sequence_metrics(annotations, rows)
    totals = result["totals"]
    assert totals["returns"] == 1
    assert totals["returns_with_id_change"] == 1
    assert totals["misses"] == 2
    assert totals["correct_associations"] == 0


def test_temporal_reset_collision_across_sequence_boundary():
    annotations = {
        "frames": {
            "f1": _seq_frame([_seq_object("a", "P1")]),
            "f2": _seq_frame([_seq_object("a2", "P1")]),
            "f3": _seq_frame([_seq_object("b", "P2")]),
            "f4": _seq_frame([_seq_object("b2", "P2")]),
        },
        "sequences": [
            _sequence("s1", ["f1", "f2"], "f1", "f2"),
            _sequence("s2", ["f3", "f4"], "f3", "f4"),
        ],
    }
    rows = [
        _obs_row("f1", ("a", "t1")),
        _obs_row("f2", ("a2", "t1")),
        _obs_row("f3", ("b", "t1")),
        _obs_row("f4", ("b2", "t1")),
    ]
    result = temporal_sequence_metrics(annotations, rows)
    totals = result["totals"]
    assert totals["reset_boundaries"] == 1
    assert totals["reset_collisions"] == 1
    assert totals["collisions"] == 0
    assert totals["correct_associations"] == 2


def test_temporal_collision_and_fragmentation_are_separate():
    annotations = {
        "frames": {"f1": _seq_frame([_seq_object("a", "P1"), _seq_object("b", "P2")])},
        "sequences": [_sequence("s1", ["f1", "f2"], "f1", "f2")],
    }
    annotations["frames"]["f2"] = _seq_frame([_seq_object("a2", "P1"), _seq_object("b2", "P2")])
    rows = [_obs_row("f1", ("a", "t1"), ("b", "t1")), _obs_row("f2", ("a2", "t2"), ("b2", "t2"))]
    result = temporal_sequence_metrics(annotations, rows)
    totals = result["totals"]
    assert totals["collisions"] == 2
    assert totals["fragmentation"] == 2
    assert totals["switches"] == 2
    assert totals["incorrect_associations"] == 2


def test_temporal_fails_closed_without_reviewed_sequences():
    annotations = {"frames": {"f1": _seq_frame([_seq_object("a", "P")])}}
    result = temporal_sequence_metrics(annotations, [_obs_row("f1", ("a", "t1"))])
    assert result["available"] is False
    assert "annotations.sequences missing" in result["reason"]
    assert result["sequences"] == {}
    assert all(value is None for value in result["totals"].values())


def test_temporal_fails_closed_without_reviewed_physical_ids():
    annotations = {
        "frames": {
            "f1": _seq_frame([_seq_object("a", None)]),
            "f2": _seq_frame([_seq_object("a2", None)]),
        },
        "sequences": [_sequence("s1", ["f1", "f2"], "f1", "f2")],
    }
    rows = [_obs_row("f1", ("a", "t1")), _obs_row("f2", ("a2", "t1"))]
    result = temporal_sequence_metrics(annotations, rows)
    assert result["available"] is False
    assert "missing reviewed physical_id" in result["reason"]


def test_frozen_three_frame_annotations_mark_temporal_unavailable():
    ann = json.loads((evaluator.EVAL / "annotations.json").read_text(encoding="utf-8"))
    result = temporal_sequence_metrics(ann, [])
    assert result["available"] is False
    assert "annotations.sequences missing" in result["reason"]
    assert all(value is None for value in result["totals"].values())


def test_temporal_unavailable_fails_gate_closed():
    verdict = qualification_verdict(_gate([_passing_row()]), _complete_annotation())
    assert "temporal sequence metrics unavailable" in verdict["arm_failures"]["fused"]


class _FakeAssembler:
    def __init__(self, tracker):
        self.tracker = tracker

    def update(self, image, *, ocr=None):
        if ocr is not None:
            ocr(image)
        return (), SimpleNamespace(nodes=())


def _raw_frame(tmp_path, name, size=(4, 4)):
    data = bytes(size[0] * size[1] * 3)
    path = tmp_path / name
    path.write_bytes(data)
    return {"path": str(path), "sha256": hashlib.sha256(data).hexdigest()}


def _fake_tracker(arm="fused-gpa"):
    return SimpleNamespace(status="ok", name=arm, require_visual_ownership=False)


class _OcrSpy:
    """Counts real invocations; the report must match this, never exceed it."""

    def __init__(self):
        self.calls = []

    def __call__(self, image):
        self.calls.append(image.size)
        return [("End Turn", (0, 0, 1, 1))]


def _run_fake_harness(monkeypatch, tmp_path, *, frames, out_dir, **overrides):
    monkeypatch.setattr(evaluator, "SceneAssembler", _FakeAssembler)
    spy = _OcrSpy()
    options = {
        "tracker_factory": lambda arm: _fake_tracker(arm),
        "ocr_factory": lambda assembler: spy,
        "qwen_loader": lambda: (SimpleNamespace(score=lambda *a, **k: None), {"loader": "fake"}),
        "qwen_probe": lambda policy, **kw: {"offer_count": 3, "choice": "act_attack"},
        "frame_loader": lambda path: Image.new("RGB", (4, 4)),
    }
    options.update(overrides)
    report = evaluator.run_timing_harness(
        frames=frames, out_dir=out_dir, frame_size=[4, 4], command=["pytest"], **options
    )
    return report, spy


def test_timing_harness_drives_ocr_only_through_the_assembler(monkeypatch, tmp_path):
    frames = {"f1": _raw_frame(tmp_path, "f1.bin"), "f2": _raw_frame(tmp_path, "f2.bin")}
    report, spy = _run_fake_harness(
        monkeypatch, tmp_path, frames=frames, out_dir=tmp_path / "run-ocr", repeats=4, warmup=1
    )
    counters = report["counters"]
    # No separate full-frame copy: the spy and the reported counters describe the
    # same assembler-driven invocations.
    assert len(spy.calls) == counters["ocr_invocations"]
    assert counters["ocr_invocations"] == counters["update_invocations"]
    assert counters["precomputed_ocr"] is False
    cold = report["cold"]["updates"]
    assert len(cold) == 1
    assert cold[0]["ocr_full_frame_invocations"] == 1
    assert cold[0]["ocr_crop_invocations"] == 0
    assert counters["ocr_full_frame_invocations"] == counters["update_invocations"]
    assert "no separate full-frame OCR copy" in report["warm"]["note"]
    assert report["gate"]["harness_accounting"] == "blocked"
    assert any(
        "injected components present" in reason
        for reason in report["gate"]["accounting_reasons"]
    )


def test_timing_harness_warmup_is_two_extra_iterations_after_the_cold_frame(monkeypatch, tmp_path):
    frames = {"f1": _raw_frame(tmp_path, "f1.bin"), "f2": _raw_frame(tmp_path, "f2.bin")}
    report, _ = _run_fake_harness(
        monkeypatch, tmp_path, frames=frames, out_dir=tmp_path / "run-warmup", repeats=5, warmup=2
    )
    counters = report["counters"]
    assert counters["cold_frames"] == 1
    assert counters["warmup_iterations"] == 2
    assert counters["warm_samples"] == 5
    assert counters["update_invocations"] == 1 + 2 + 5
    assert len(report["cold"]["updates"]) == 1
    assert len(report["warmup"]["updates"]) == 2
    assert len(report["warm"]["samples"]) == 5
    assert report["warm"]["warmup_iterations"] == 2
    assert report["warm"]["update"]["samples"] == 5
    assert report["warm"]["update"]["max_ms"] is not None
    assert report["cold"]["tracker_load_ms"] is not None


def test_timing_harness_calls_qwen_while_resident_on_every_warm_sample(monkeypatch, tmp_path):
    frames = {"f1": _raw_frame(tmp_path, "f1.bin"), "f2": _raw_frame(tmp_path, "f2.bin")}
    report, _ = _run_fake_harness(
        monkeypatch, tmp_path, frames=frames, out_dir=tmp_path / "run-qwen", repeats=6, warmup=2
    )
    qwen = report["qwen"]
    assert qwen["status"] == "loaded"
    assert qwen["resident_before_warmup"] is True
    assert qwen["synthetic_offers"] is True
    assert "not scene-choice accuracy" in qwen["claim"]
    assert report["counters"]["qwen_decision_calls"] == 6
    assert qwen["decision_calls"] == 6
    assert qwen["offers_per_decision"] == [3] * 6
    assert qwen["decision_ms"]["samples"] == 6
    assert all("qwen_offers" in sample for sample in report["warm"]["samples"])
    assert all("qwen_offers" not in item for item in report["warmup"]["updates"])
    assert all("qwen_offers" not in item for item in report["cold"]["updates"])


def test_timing_harness_keeps_lean_ui_qualification_separate_and_blocked(monkeypatch, tmp_path):
    frames = {"f1": _raw_frame(tmp_path, "f1.bin"), "f2": _raw_frame(tmp_path, "f2.bin")}
    report, _ = _run_fake_harness(
        monkeypatch, tmp_path, frames=frames, out_dir=tmp_path / "run-gate", repeats=5, warmup=1
    )
    gate = report["gate"]
    assert gate["harness_accounting"] == "blocked"
    assert gate["lean_ui_qualification"] == "blocked"
    assert any("100" in reason for reason in gate["lean_ui_blockers"])
    assert any("held-out" in reason for reason in gate["lean_ui_blockers"])
    assert evaluator.REQUIRED_WARM_SAMPLES == 100


def test_timing_harness_forces_offline_env_before_tracker_and_qwen_load(monkeypatch, tmp_path):
    keys = ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_HUB_DISABLE_TELEMETRY")
    expected = {key: "1" for key in keys}
    frames = {"f1": _raw_frame(tmp_path, "f1.bin"), "f2": _raw_frame(tmp_path, "f2.bin")}
    seen: dict = {}

    def tracker_factory(arm):
        seen["tracker"] = {key: os.environ.get(key) for key in keys}
        return _fake_tracker(arm)

    def qwen_loader():
        seen["qwen"] = {key: os.environ.get(key) for key in keys}
        return SimpleNamespace(score=lambda *a, **k: None), {"loader": "fake"}

    report, _ = _run_fake_harness(
        monkeypatch,
        tmp_path,
        frames=frames,
        out_dir=tmp_path / "run-offline",
        tracker_factory=tracker_factory,
        qwen_loader=qwen_loader,
    )
    assert seen["tracker"] == expected  # offline env set before tracker construction
    assert seen["qwen"] == expected  # and still set before the Qwen loader runs
    assert report["identity"]["offline_env"] == expected


def test_timing_harness_refuses_historical_directory():
    for forbidden in (evaluator.EVAL, evaluator.EVAL / "timing"):
        try:
            evaluator.run_timing_harness(frames={"a": {"path": "x"}}, out_dir=forbidden)
        except ValueError as exc:
            assert "historical" in str(exc)
        else:
            raise AssertionError(f"harness must refuse {forbidden}")


def test_timing_harness_never_overwrites_an_existing_run(monkeypatch, tmp_path):
    frames = {"f1": _raw_frame(tmp_path, "f1.bin"), "f2": _raw_frame(tmp_path, "f2.bin")}
    out_dir = tmp_path / "run-once"
    _run_fake_harness(monkeypatch, tmp_path, frames=frames, out_dir=out_dir)
    try:
        _run_fake_harness(monkeypatch, tmp_path, frames=frames, out_dir=out_dir)
    except FileExistsError:
        pass
    else:
        raise AssertionError("harness must not overwrite an existing run directory")


def test_timing_harness_fails_closed_on_frame_identity_mismatch(monkeypatch, tmp_path):
    frames = {"f1": _raw_frame(tmp_path, "f1.bin"), "f2": _raw_frame(tmp_path, "f2.bin")}
    frames["f2"] = {**frames["f2"], "sha256": "0" * 64}
    report, spy = _run_fake_harness(
        monkeypatch, tmp_path, frames=frames, out_dir=tmp_path / "run-bad"
    )
    assert report["gate"]["harness_accounting"] == "blocked"
    assert any("digest mismatch" in r for r in report["gate"]["accounting_reasons"])
    assert report["counters"]["warm_samples"] == 0
    assert report["counters"]["update_invocations"] == 0
    assert report["counters"]["ocr_invocations"] == 0
    assert spy.calls == []


def test_timing_harness_blocks_when_qwen_route_cannot_be_proved(monkeypatch, tmp_path):
    frames = {"f1": _raw_frame(tmp_path, "f1.bin"), "f2": _raw_frame(tmp_path, "f2.bin")}

    def failing_loader():
        raise RuntimeError("HF_HUB_OFFLINE has no staged snapshot")

    report, _ = _run_fake_harness(
        monkeypatch,
        tmp_path,
        frames=frames,
        out_dir=tmp_path / "run-noqwen",
        qwen_loader=failing_loader,
    )
    assert report["qwen"]["status"] == "blocked"
    assert report["gate"]["harness_accounting"] == "blocked"
    assert any(
        "normal Qwen policy route not proved" in r for r in report["gate"]["accounting_reasons"]
    )


def test_timing_harness_requires_changed_frames(monkeypatch, tmp_path):
    frames = {"f1": _raw_frame(tmp_path, "f1.bin")}
    report, _ = _run_fake_harness(
        monkeypatch, tmp_path, frames=frames, out_dir=tmp_path / "run-one"
    )
    assert report["gate"]["harness_accounting"] == "blocked"
    assert any("changed-frame" in r for r in report["gate"]["accounting_reasons"])


def test_timing_harness_records_identity_versions_and_command(monkeypatch, tmp_path):
    frames = {"f1": _raw_frame(tmp_path, "f1.bin"), "f2": _raw_frame(tmp_path, "f2.bin")}
    report, _ = _run_fake_harness(
        monkeypatch, tmp_path, frames=frames, out_dir=tmp_path / "run-id"
    )
    identity = report["identity"]
    assert set(identity["frames"]) == {"f1", "f2"}
    assert identity["frames"]["f1"]["sha256"] == frames["f1"]["sha256"]
    assert identity["command"] == ["pytest"]
    assert "versions" in identity and "python" in identity["versions"]
    assert "revision" in identity
    assert (tmp_path / "run-id" / "timing_report.json").is_file()
