"""Admission, qualification, and rollback gates for the experiment path."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from loop.adaptive.cli import (
    _qualification_frame_row,
    _run_experiment,
    cmd_collect,
    qwen_qualification_gate,
)
from loop.adaptive.perceive import SceneAssembler
from loop.adaptive.runtime import normalized_click_point
from loop.adaptive.schema import SceneGraph, SceneNode
from loop.adaptive.session import Session
from loop.adaptive.world import NeutralWorld
from loop.os.window import CaptureProvenance, WindowTarget
from tests.loop.test_adaptive_experiment import _fake_scouts


def test_qwen_not_requested_is_not_a_pass():
    gate = qwen_qualification_gate(
        {"backend": "deterministic", "load_status": "not_requested"},
        valid_inferences=0,
    )
    assert gate["pass"] is False


def test_qwen_without_inference_fails():
    gate = qwen_qualification_gate(
        {"backend": "qwen", "load_status": "ok"},
        valid_inferences=0,
    )
    assert gate == {"pass": False, "reason": "no_valid_inference"}


def test_qwen_valid_inference_passes():
    gate = qwen_qualification_gate(
        {"backend": "qwen", "load_status": "ok"},
        valid_inferences=1,
    )
    assert gate["pass"] is True


def test_failed_admission_sends_zero_input(monkeypatch, tmp_path: Path):
    clicks: list[tuple[float, float]] = []
    monkeypatch.setattr("loop.adaptive.cli._load_scouts", lambda _directory: None)

    def _spy(self, x, y):
        clicks.append((x, y))

    monkeypatch.setattr(NeutralWorld, "click_at", _spy)
    rc = cmd_collect(
        [
            "--workspace",
            str(tmp_path),
            "--synthetic",
            "--experiment",
            "--arm",
            "--steps",
            "30",
        ]
    )
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert rc == 0
    assert summary["admitted"] is False
    assert clicks == []


def test_configuration_reaches_experiment_and_unresolved_stay_ineligible(monkeypatch, tmp_path: Path):
    world = NeutralWorld()
    world.reset(0)
    dummy = WindowTarget(hwnd=1, title="dummy", left=0, top=0, width=320, height=240)
    monkeypatch.setattr("loop.os.window.list_windows", lambda _title: [dummy])
    monkeypatch.setattr("loop.os.window.focus_window_and_settle", lambda _target: True)
    monkeypatch.setattr("loop.os.window.is_foreground", lambda _target: True)
    monkeypatch.setattr(
        "loop.os.window.target_identity",
        lambda target, selector="": {
            "hwnd": int(target.hwnd),
            "pid": 1,
            "title": target.title,
        },
    )
    monkeypatch.setattr(
        "loop.os.window.grab_client_capture",
        lambda _target: (
            world.observe()[0].convert("RGB"),
            CaptureProvenance(
                source="hwnd-imagegrab",
                client_size=(320, 240),
                content_box=(0.0, 0.0, 1.0, 1.0),
                integrity="trusted",
                reason="test",
            ),
        ),
    )
    monkeypatch.setattr("loop.adaptive.cli._live_ocr_tokens", lambda _image: world.ocr_tokens())
    monkeypatch.setattr("loop.os.mouse.Mouse.execute", MagicMock())
    rc = cmd_collect(
        [
            "--workspace",
            str(tmp_path),
            "--window",
            "dummy",
            "--experiment",
            "--tracker",
            "none",
            "--semantic-confirmation",
            "required",
            "--steps",
            "4",
        ]
    )
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["effective_config"]["tracker"] == "none"
    assert summary["effective_config"]["semantic_confirmation"] == "required"
    assert rc in {0, 2}

    session = Session(tmp_path)
    graph = SceneGraph(
        snapshot_id="s",
        nodes=(
            SceneNode(
                "ocr_1",
                (0.1, 0.1, 0.3, 0.2),
                (),
                {"label": "Settings", "registry_id": "reg_0", "registry_owner": "ocr"},
            ),
        ),
        relations=(),
    )
    snap = session.observe(graph)
    row = _qualification_frame_row(
        graph, snap, semantic_required=True, ledger=session.ledger
    )
    assert row["eligible_offer_ids"] == []


def test_insufficient_stability_blocks_execution(monkeypatch, tmp_path: Path):
    clicks: list[tuple[float, float]] = []
    monkeypatch.setattr("loop.adaptive.cli._load_scouts", lambda _directory: _fake_scouts())

    def _spy(self, x, y):
        clicks.append((x, y))

    monkeypatch.setattr(NeutralWorld, "click_at", _spy)
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
    assert rc == 2
    assert clicks == []
    assert Session(tmp_path).schema.actions == ()


def test_rollback_failure_releases_inputs_and_locks_recovery(monkeypatch, tmp_path: Path):
    world = NeutralWorld()
    world.reset(0)
    assembler = SceneAssembler()
    session = Session(tmp_path)
    session.bind_target({"hwnd": 1, "pid": 1, "title": "dummy"})
    target = WindowTarget(hwnd=1, title="dummy", left=0, top=0, width=320, height=240)
    clicks: list[str] = []
    releases: list[str] = []
    holder: list = [None]

    class _Owner:
        def release_all(self, reason: str) -> None:
            releases.append(reason)

    monkeypatch.setattr("loop.adaptive.arm.create_owner", lambda _target: _Owner())
    monkeypatch.setattr("loop.adaptive.cli._load_scouts", lambda _directory: _fake_scouts())

    def observe():
        recs, graph = assembler.update(
            world.observe()[0].convert("RGB"),
            ocr=lambda _image: world.ocr_tokens(),
            ledger=session.ledger,
        )
        holder[0] = graph
        snap = session.observe(graph)
        observe.provenance = {"integrity": "trusted", "hwnd": 1, "pid": 1}
        return recs, graph, snap, holder

    def click(node_id: str) -> None:
        clicks.append(node_id)
        x, y = normalized_click_point(holder[0], str(node_id))
        world.click_at(x, y)

    def rollback(_node_id: str) -> None:
        return None

    rc, summary = _run_experiment(
        session,
        30,
        observe=observe,
        click=click,
        rollback=rollback,
        legal_runner=lambda _steps, _bindings: None,
        arm=True,
        live=False,
        target=target,
    )
    assert rc != 0
    assert clicks
    assert releases == ["experiment_rollback_failed"]
    assert summary["recovery_locked"] is True
    assert session.recovery_state() is not None
