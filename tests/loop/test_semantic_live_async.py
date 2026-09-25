"""Live async semantic wiring (fake/blocking provider only; no model, network or input)."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from PIL import Image

from loop.adaptive.cli import (
    _LiveSemanticBridge,
    _SEMANTIC_SHUTDOWN_TIMEOUT_S,
    _live_observe_factory,
    _optional_vlm,
    _semantic_worker_for,
)
from loop.adaptive.schema import SceneGraph, SceneNode
from loop.adaptive.semantic_confirmation import (
    CandidateConfirmationStatus,
    SemanticJobStatus,
    SemanticObservationResult,
    SemanticWorker,
)
from loop.adaptive.session import Session


def _image() -> Image.Image:
    return Image.new("RGB", (32, 24), color=(10, 20, 30))


def _graph(snapshot_id: str, *registry_ids: str, track_state: str = "new") -> SceneGraph:
    return SceneGraph(
        snapshot_id=snapshot_id,
        nodes=tuple(
            SceneNode(
                node_id=f"n_{rid}",
                bbox=(0.1, 0.1, 0.2, 0.2),
                evidence_ids=(),
                attributes={"registry_id": rid, "track_state": track_state},
            )
            for rid in registry_ids
        ),
    )


def _snap(revision: str = "rev-1"):
    return type("Snap", (), {"schema_revision_id": revision})()


def _identity(selector: str = "target") -> dict:
    return {"selector": selector, "exe": "game.exe", "window_class": "Game"}


def _prov(sequence: int, *, integrity: str = "trusted") -> dict:
    return {"integrity": integrity, "observation_sequence": sequence}


class _Assembler:
    def __init__(self, *, screen_replaced: bool = False) -> None:
        self.screen_replaced = screen_replaced


class _FakeConfirmer:
    """Deterministic confirmer stand-in; ``gate`` blocks the worker on demand."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.game_brief = None
        self.briefs: list[dict | None] = []
        self.cleared = 0
        self.invalidated: list[tuple[str, ...]] = []
        self.cycles = 0
        self.calls: list[dict] = []
        self.gate = threading.Event()
        self.gate.set()

    def set_game_brief(self, brief) -> None:
        with self._lock:
            self.game_brief = None if brief is None else dict(brief)
            self.briefs.append(self.game_brief)

    def clear_pending(self) -> None:
        with self._lock:
            self.cleared += 1

    def invalidate(self, candidate_ids) -> None:
        with self._lock:
            self.invalidated.append(tuple(candidate_ids))

    def start_new_confirmation_cycle(self) -> None:
        with self._lock:
            self.cycles += 1

    def observe(
        self,
        *,
        target_identity,
        screen_generation,
        observation_id,
        candidates,
        frame,
    ) -> SemanticObservationResult:
        with self._lock:
            self.calls.append(
                {
                    "target_identity": target_identity,
                    "screen_generation": screen_generation,
                    "observation_id": observation_id,
                }
            )
        self.gate.wait(timeout=5.0)
        return SemanticObservationResult(
            target_identity_key=str(target_identity),
            screen_generation=str(screen_generation),
            observation_id=observation_id,
            candidates=tuple(
                CandidateConfirmationStatus(candidate.candidate_id, "confirmed", "control", "ok")
                for candidate in candidates
            ),
            telemetry=(),
        )


def _wait_for(predicate, timeout: float = 3.0) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition not met")


def _wait_terminal(worker: SemanticWorker, job_id: int | None) -> None:
    assert job_id is not None
    _wait_for(lambda: worker.poll(job_id).status != SemanticJobStatus.PENDING)


def test_ready_result_applies_only_after_a_later_distinct_trusted_observation():
    confirmer = _FakeConfirmer()
    worker = SemanticWorker(confirmer)
    bridge = _LiveSemanticBridge(worker)
    try:
        bridge.begin_frame(_Assembler())
        bridge.step(
            graph=_graph("obs-1", "reg-a"),
            snap=_snap(),
            identity=_identity(),
            prov=_prov(1),
            image=_image(),
        )
        _wait_terminal(worker, bridge.pending_job_id)
        assert bridge.applied == 0

        # The same observation id (identical pixels) is not a distinct validation.
        graph_same = _graph("obs-1", "reg-a")
        bridge.step(
            graph=graph_same,
            snap=_snap(),
            identity=_identity(),
            prov=_prov(2),
            image=_image(),
        )
        time.sleep(0.05)
        assert bridge.applied == 0
        assert "semantic_status" not in graph_same.nodes[0].attributes

        # A later, distinct, trusted observation of the same target accepts it.
        graph_next = _graph("obs-2", "reg-a")
        bridge.step(
            graph=graph_next,
            snap=_snap(),
            identity=_identity(),
            prov=_prov(3),
            image=_image(),
        )
        _wait_for(lambda: bridge.applied == 1)
        assert graph_next.nodes[0].attributes["semantic_status"] == "confirmed"
        assert graph_next.nodes[0].attributes["semantic_class"] == "control"
    finally:
        worker.shutdown(wait=True)


def test_untrusted_or_late_validation_observation_is_rejected():
    confirmer = _FakeConfirmer()
    worker = SemanticWorker(confirmer)
    bridge = _LiveSemanticBridge(worker)
    try:
        bridge.begin_frame(_Assembler())
        bridge.step(
            graph=_graph("obs-1", "reg-a"),
            snap=_snap(),
            identity=_identity(),
            prov=_prov(1),
            image=_image(),
        )
        _wait_terminal(worker, bridge.pending_job_id)

        untrusted = _graph("obs-2", "reg-a")
        bridge.step(
            graph=untrusted,
            snap=_snap(),
            identity=_identity(),
            prov=_prov(2, integrity="degraded"),
            image=_image(),
        )
        assert bridge.applied == 0
        assert "semantic_status" not in untrusted.nodes[0].attributes
    finally:
        worker.shutdown(wait=True)


def test_blocked_provider_never_blocks_the_live_step():
    confirmer = _FakeConfirmer()
    confirmer.gate.clear()
    worker = SemanticWorker(confirmer)
    bridge = _LiveSemanticBridge(worker)
    try:
        bridge.begin_frame(_Assembler())
        bridge.step(
            graph=_graph("obs-1", "reg-a"),
            snap=_snap(),
            identity=_identity(),
            prov=_prov(1),
            image=_image(),
        )
        _wait_for(lambda: confirmer.calls)

        started = time.monotonic()
        blocked = _graph("obs-2", "reg-a")
        bridge.step(
            graph=blocked,
            snap=_snap(),
            identity=_identity(),
            prov=_prov(2),
            image=_image(),
        )
        assert time.monotonic() - started < 0.5
        assert bridge.applied == 0
        assert "semantic_status" not in blocked.nodes[0].attributes

        confirmer.gate.set()
        _wait_terminal(worker, bridge.pending_job_id)
        graph_next = _graph("obs-3", "reg-a")
        bridge.step(
            graph=graph_next,
            snap=_snap(),
            identity=_identity(),
            prov=_prov(3),
            image=_image(),
        )
        _wait_for(lambda: bridge.applied == 1)
    finally:
        confirmer.gate.set()
        worker.shutdown(wait=True)


def test_screen_generation_tracks_real_replacement_not_pixel_digest():
    confirmer = _FakeConfirmer()
    worker = SemanticWorker(confirmer)
    bridge = _LiveSemanticBridge(worker)
    try:
        assembler = _Assembler()
        assert bridge.begin_frame(assembler) == 0
        bridge.step(
            graph=_graph("obs-1", "reg-a"),
            snap=_snap(),
            identity=_identity(),
            prov=_prov(1),
            image=_image(),
        )
        _wait_for(lambda: confirmer.calls)
        assert confirmer.calls[-1]["screen_generation"] == "0"
        assert confirmer.calls[-1]["observation_id"] == "obs-1"

        assembler.screen_replaced = True
        assert bridge.begin_frame(assembler) == 1
        assert assembler.screen_replaced is False
        _wait_for(lambda: confirmer.cleared == 1)

        bridge.step(
            graph=_graph("obs-2", "reg-a"),
            snap=_snap(),
            identity=_identity(),
            prov=_prov(2),
            image=_image(),
        )
        _wait_for(lambda: len(confirmer.calls) >= 2)
        assert confirmer.calls[-1]["screen_generation"] == "1"
        assert confirmer.calls[-1]["observation_id"] == "obs-2"
    finally:
        worker.shutdown(wait=True)


def test_target_change_between_frames_drops_the_pending_result():
    confirmer = _FakeConfirmer()
    worker = SemanticWorker(confirmer)
    bridge = _LiveSemanticBridge(worker)
    try:
        bridge.begin_frame(_Assembler())
        bridge.step(
            graph=_graph("obs-1", "reg-a"),
            snap=_snap(),
            identity=_identity("target-a"),
            prov=_prov(1),
            image=_image(),
        )
        _wait_terminal(worker, bridge.pending_job_id)

        changed = _graph("obs-2", "reg-a")
        bridge.step(
            graph=changed,
            snap=_snap(),
            identity=_identity("target-b"),
            prov=_prov(2),
            image=_image(),
        )
        assert bridge.dropped_target_change == 1
        assert bridge.applied == 0
        assert "semantic_status" not in changed.nodes[0].attributes
    finally:
        worker.shutdown(wait=True)


def test_worker_controls_route_brief_and_returned_invalidation():
    confirmer = _FakeConfirmer()
    worker = _semantic_worker_for(confirmer, None)
    assert worker is not None
    try:
        worker.set_game_brief({"objective": "win"})
        _wait_for(
            lambda: confirmer.briefs and confirmer.briefs[-1] == {"objective": "win"}
        )
        bridge = _LiveSemanticBridge(worker)
        bridge.invalidate_returned(_graph("obs-1", "reg-a", track_state="returned"))
        _wait_for(lambda: confirmer.invalidated == [("reg-a",)])
    finally:
        worker.shutdown(wait=True)


def test_live_observe_factory_leaves_the_cloud_vlm_off(monkeypatch):
    from loop.os.window import CaptureProvenance, WindowTarget

    target = WindowTarget(77, "target", 0, 0, 8, 6)
    seen: dict = {}

    class SessionStub:
        ledger = type("Ledger", (), {"append_event": lambda *_a, **_k: None})()

        def observe(self, graph):
            return graph

    class Assembler:
        def update(self, image, **kwargs):
            seen.update(kwargs)
            return (), type("Graph", (), {"snapshot_id": "fresh", "nodes": ()})()

    def _grab(_target):
        now = time.monotonic()
        return Image.new("RGB", (8, 6), "white"), CaptureProvenance(
            source="hwnd-imagegrab",
            client_size=(8, 6),
            content_box=(0.0, 0.0, 1.0, 1.0),
            integrity="trusted",
            reason="ok",
            acquisition_ts_monotonic=now,
            acquisition_origin="pre-grab-sync",
            acquisition_age_known=True,
            grab_requested_ts_monotonic=now,
        )

    monkeypatch.setattr("loop.os.window.is_foreground", lambda _target: True)
    monkeypatch.setattr(
        "loop.os.window.target_identity", lambda _target: {"hwnd": 77, "pid": 9}
    )
    monkeypatch.setattr("loop.os.window.grab_client_capture", _grab)
    monkeypatch.setattr(
        "loop.adaptive.cli._optional_vlm",
        lambda: pytest.fail("live observe must not build the cloud VLM"),
    )
    observe = _live_observe_factory(SessionStub(), target, Assembler())
    observe()

    assert observe.vlm_model is None
    assert "vlm" not in seen
    assert _optional_vlm is not None  # the direct perceive(vlm=...) hook is retained


def _explode(*_args, **_kwargs):
    raise RuntimeError("capture failed")


def test_perception_collect_shuts_the_worker_down_on_error(monkeypatch, tmp_path: Path):
    import loop.adaptive.cli as cli

    shutdowns: list[tuple[bool, float]] = []

    class SpyWorker:
        def shutdown(self, *, wait: bool = False, timeout_s: float = 2.0) -> bool:
            shutdowns.append((wait, timeout_s))
            return True

        def set_game_brief(self, *_args, **_kwargs) -> bool:
            return True

        def clear_pending(self, *_args, **_kwargs) -> bool:
            return True

        def invalidate(self, *_args, **_kwargs) -> bool:
            return True

        def submit(self, **_kwargs):
            raise AssertionError("no frame should be submitted")

        def poll(self, *_args, **_kwargs):
            raise AssertionError("no frame should be polled")

        def validate_result(self, *_args, **_kwargs):
            raise AssertionError("no result should be validated")

    monkeypatch.setattr(
        cli,
        "_prepare_live_target",
        lambda *_a, **_k: type("T", (), {"hwnd": 7, "title": "t"})(),
    )
    monkeypatch.setattr(cli, "_bind_live_session", lambda *_a, **_k: None)
    monkeypatch.setattr(cli, "_select_bound_game_brief", lambda *_a, **_k: None)
    monkeypatch.setattr(cli, "_record_game_brief", lambda *_a, **_k: None)
    monkeypatch.setattr(cli, "_semantic_worker_for", lambda *_a, **_k: SpyWorker())
    monkeypatch.setattr(cli, "_live_observe_factory", lambda *_a, **_k: _explode)

    session = Session(Path(tmp_path))
    with pytest.raises(RuntimeError, match="capture failed"):
        cli._perception_collect(
            session,
            "unused",
            3,
            semantic_mode="required",
            semantic_confirmer=object(),
        )
    assert shutdowns == [(True, _SEMANTIC_SHUTDOWN_TIMEOUT_S)]
