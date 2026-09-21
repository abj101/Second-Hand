"""Quarantined experiment controller: admit, trial, rollback, promote."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from loop.adaptive.cli import cmd_collect
from loop.adaptive.experiment import (
    ExperimentController,
    ExperimentJournal,
    admit_plan,
    execute_trial,
    observation_fingerprint,
    preflight,
)
from loop.adaptive.orchestrate import SchemaDiff, filter_model_diffs
from loop.adaptive.perceive import SceneAssembler
from loop.adaptive.schema import EvidenceRecord, empty_schema
from loop.adaptive.session import Session
from loop.adaptive.world import CONTROL_NODE_ID, NeutralWorld
from loop.os.window import CaptureProvenance, WindowTarget


def _rgb(world: NeutralWorld):
    pixels = world.observe()[0]
    return pixels.convert("RGB") if pixels.mode != "RGB" else pixels


def _observe_world(world: NeutralWorld, assembler: SceneAssembler, session: Session):
    recs, graph = assembler.update(_rgb(world), ocr=lambda _im: world.ocr_tokens())
    snap = session.observe(graph)
    return recs, graph, snap


def _control_and_field(graph):
    control = None
    field_id = None
    ids: list[str] = []
    for node in graph.nodes:
        if node.attributes.get("numeric") is not None and node.attributes.get("label"):
            field_id = str(node.attributes["label"])
        if (
            node.node_id.startswith("track_")
            and node.attributes.get("label")
            and "numeric" not in node.attributes
        ):
            control = node
            ids = list(node.evidence_ids)
    assert control is not None and field_id
    return control, field_id, ids


def test_filter_model_diffs_rejects_ids_and_coordinates():
    valid = {"ev-a"}
    bad = (
        SchemaDiff("field", "x", ("missing",), {"field_id": "x"}),
        SchemaDiff("field", "y", ("ev-a",), {"field_id": "y", "bbox": [0, 0, 1, 1]}),
        SchemaDiff("field", "z", ("ev-a",), {"field_id": "z", "label": "z"}),
    )
    kept = filter_model_diffs(bad, valid)
    assert [d.suggested_id for d in kept] == ["z"]


def test_admit_rejects_fabricated_ids_and_bad_primitive():
    rec = EvidenceRecord("ev-1", "ocr", {"text": "go", "bbox": [0.1, 0.1, 0.2, 0.2]})
    schema = empty_schema()
    parsed = {
        "abstain": False,
        "candidate": {
            "primitive": "click",
            "candidate_ids": ["nope"],
            "predicted_effects": [{"field_id": "score", "op": "increased"}],
            "rollback": {"primitive": "click", "candidate_ids": ["nope"]},
        },
    }
    assert (
        admit_plan(
            schema_parsed={"diffs": []},
            experiment_parsed=parsed,
            evidence=(rec,),
            schema=schema,
            scenes=(),
            target_identity={},
        )
        is None
    )
    parsed["candidate"]["candidate_ids"] = ["ev-1"]
    parsed["candidate"]["primitive"] = "drag"
    assert (
        admit_plan(
            schema_parsed={"diffs": []},
            experiment_parsed=parsed,
            evidence=(rec,),
            schema=schema,
            scenes=(),
            target_identity={},
        )
        is None
    )


def test_preflight_rejects_untrusted_and_ambiguous():
    world = NeutralWorld()
    graph = world.reset(0)
    try:
        preflight(
            graph=graph,
            selector={"label": "missing"},
            provenance={"integrity": "trusted"},
            identity=None,
            target=None,
            live=False,
        )
        raised = False
    except RuntimeError as exc:
        raised = "selector" in str(exc)
    assert raised
    try:
        preflight(
            graph=graph,
            selector={"label": str(graph.nodes[0].attributes.get("label") or "x")},
            provenance={"integrity": "uncertain"},
            identity={"hwnd": 1},
            target=WindowTarget(1, "x", 0, 0, 10, 10),
            live=True,
        )
        trusted = True
    except RuntimeError as exc:
        trusted = "untrusted" not in str(exc)
    assert trusted is False


def test_two_trials_promote_and_journal_reload(tmp_path: Path):
    session = Session(tmp_path)
    world = NeutralWorld()
    world.reset(0)
    assembler = SceneAssembler()
    graphs = []
    recs = None
    for _ in range(4):
        recs, graph, _snap = _observe_world(world, assembler, session)
        graphs.append(graph)
    _control, field_id, ids = _control_and_field(graphs[-1])
    schema_parsed = {
        "diffs": [
            {
                "kind": "field",
                "suggested_id": field_id,
                "candidate_ids": ids[:1],
                "payload": {"field_id": field_id, "value_type": "number", "label": field_id},
            }
        ]
    }
    exp_parsed = {
        "abstain": False,
        "candidate": {
            "primitive": "click",
            "candidate_ids": ids[:1],
            "predicted_effects": [{"field_id": field_id, "op": "increased"}],
            "rollback": {"primitive": "click", "candidate_ids": ids[:1]},
        },
    }
    plan = admit_plan(
        schema_parsed=schema_parsed,
        experiment_parsed=exp_parsed,
        evidence=recs,
        schema=session.schema,
        scenes=graphs[-3:],
        target_identity={"selector": "synth"},
    )
    assert plan is not None
    ctrl = ExperimentController(session, min_frames=4)
    ctrl.accept_plan(plan)

    def click(node_id: str) -> None:
        box = next(n.bbox for n in graph.nodes if n.node_id == node_id)
        world.click_at((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)

    def rollback(_node_id: str) -> None:
        world.undo_click(CONTROL_NODE_ID)

    def after():
        _recs2, graph2, snap2 = _observe_world(world, assembler, session)
        graphs.append(graph2)
        return graph2, snap2

    _recs, graph, snap = _observe_world(world, assembler, session)
    t1 = execute_trial(
        plan=plan,
        index=1,
        graph=graph,
        before=snap,
        click=click,
        observe_after=after,
        rollback=rollback,
        provenance={"integrity": "trusted"},
        live=False,
        target=None,
        identity=None,
    )
    assert t1.rollback_ok
    _recs, graph, snap = _observe_world(world, assembler, session)
    t2 = execute_trial(
        plan=plan,
        index=2,
        graph=graph,
        before=snap,
        click=click,
        observe_after=after,
        rollback=rollback,
        provenance={"integrity": "trusted"},
        live=False,
        target=None,
        identity=None,
    )
    assert t2.rollback_ok
    ctrl.record_trial(t1)
    ctrl.record_trial(t2)
    assert ctrl.promotion_ready()
    schema = ctrl.promote()
    assert schema.actions
    assert all("track_" not in a.action_id for a in schema.actions)
    assert schema.actions[0].constraints.get("risk") == "low"
    assert schema.actions[0].expected_effects
    journal = ExperimentJournal(tmp_path / "experiment.jsonl")
    assert journal.last_plan() is not None
    loaded = ExperimentController(session, min_frames=4)
    assert loaded.plan is not None
    assert loaded.plan.plan_id == plan.plan_id
    assert len(loaded.trials) == 2


def _fake_scouts():
    def schema_scout(bundle: dict) -> dict:
        diffs = []
        for node in bundle["scene"]["nodes"]:
            attrs = node.get("attributes") or {}
            if "numeric" in attrs and attrs.get("label"):
                ids = node.get("evidence_ids") or bundle["evidence_ids"][:1]
                diffs.append(
                    {
                        "kind": "field",
                        "suggested_id": attrs["label"],
                        "candidate_ids": ids[:1],
                        "payload": {
                            "field_id": attrs["label"],
                            "value_type": "number",
                            "label": attrs["label"],
                        },
                    }
                )
        return {"diffs": diffs}

    def experiment_scout(bundle: dict) -> dict:
        field_id = None
        control_ids = None
        for node in bundle["scene"]["nodes"]:
            attrs = node.get("attributes") or {}
            if attrs.get("numeric") is not None and attrs.get("label"):
                field_id = attrs["label"]
            if (
                node["node_id"].startswith("track_")
                and attrs.get("label")
                and "numeric" not in attrs
            ):
                control_ids = node.get("evidence_ids") or []
        if not field_id or not control_ids:
            return {"abstain": True, "candidate": None}
        return {
            "abstain": False,
            "candidate": {
                "primitive": "click",
                "candidate_ids": control_ids[:1],
                "predicted_effects": [{"field_id": field_id, "op": "increased"}],
                "rollback": {"primitive": "click", "candidate_ids": control_ids[:1]},
            },
        }

    return schema_scout, experiment_scout


def test_collect_experiment_without_arm_emits_no_input(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("loop.adaptive.cli._load_scouts", lambda _dir: _fake_scouts())
    mouse = MagicMock()
    monkeypatch.setattr("loop.os.mouse.Mouse.execute", mouse)
    rc = cmd_collect(["--workspace", str(tmp_path), "--synthetic", "--experiment", "--steps", "4"])
    assert rc == 0
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["mode"] == "experiment"
    assert summary["admitted"] is True
    assert summary["armed"] is False
    mouse.assert_not_called()
    session = Session(tmp_path)
    assert session.schema.actions == ()


def test_collect_experiment_arm_promotes_one_action(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("loop.adaptive.cli._load_scouts", lambda _dir: _fake_scouts())
    rc = cmd_collect(
        [
            "--workspace",
            str(tmp_path),
            "--synthetic",
            "--experiment",
            "--arm",
            "--steps",
            "4",
        ]
    )
    assert rc == 0, (tmp_path / "summary.json").read_text(encoding="utf-8")
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["promoted"] is True
    assert summary["verified"] == 1
    session = Session(tmp_path)
    assert len(session.schema.actions) == 1
    assert session.recovery_state() is None


def test_live_experiment_without_arm_does_not_click(monkeypatch, tmp_path: Path):
    world = NeutralWorld()
    world.reset(0)
    dummy = WindowTarget(hwnd=1, title="dummy", left=0, top=0, width=320, height=240)
    monkeypatch.setattr("loop.os.window.list_windows", lambda _title: [dummy])
    monkeypatch.setattr(
        "loop.os.window.target_identity",
        lambda target, selector="": {
            "selector": selector,
            "exe": "dummy.exe",
            "window_class": "Dummy",
            "hwnd": int(target.hwnd),
            "pid": 1,
            "title": target.title,
            "client_size": [target.width, target.height],
        },
    )
    monkeypatch.setattr(
        "loop.os.window.grab_client_capture",
        lambda _target: (
            world.observe()[0].convert("RGB"),
            CaptureProvenance(
                source="test",
                client_size=(320, 240),
                content_box=(0.0, 0.0, 1.0, 1.0),
                integrity="trusted",
                reason="test",
            ),
        ),
    )
    monkeypatch.setattr("loop.adaptive.cli._live_ocr_tokens", lambda _im: world.ocr_tokens())
    monkeypatch.setattr("loop.adaptive.cli._load_scouts", lambda _dir: _fake_scouts())
    mouse = MagicMock()
    monkeypatch.setattr("loop.os.mouse.Mouse.execute", mouse)
    rc = cmd_collect(
        ["--workspace", str(tmp_path), "--window", "dummy", "--experiment", "--steps", "4"]
    )
    assert rc == 0
    mouse.assert_not_called()


def test_recovery_lock_blocks_until_baseline(tmp_path: Path):
    session = Session(tmp_path)
    session.recovery_lock({"baseline": "deadbeef"})
    world = NeutralWorld()
    world.reset(0)
    assembler = SceneAssembler()
    _recs, graph, snap = _observe_world(world, assembler, session)
    fp = observation_fingerprint(snap, graph)
    assert fp != "deadbeef"
    assert session.recovery_state() is not None
