from __future__ import annotations

from dataclasses import replace

from PIL import Image

from loop.adaptive.cli import (
    _live_observe_factory,
    _live_ocr_for_assembler,
    _live_ocr_tokens,
    _prepare_live_target,
)
from loop.adaptive.contracts import ObservationQuality
from loop.adaptive.schema import ActionSurface, StateSnapshot, empty_schema
from loop.adaptive.snapshot import build_native_observation
from loop.os.window import CaptureProvenance, WindowTarget, select_client_capture
import loop.os.window as window


def _target(hwnd: int = 77) -> WindowTarget:
    return WindowTarget(hwnd, "target", 0, 0, 8, 6)


def test_mss_is_diagnostic_only_even_when_dimensions_match() -> None:
    mss = Image.new("RGB", (8, 6), "white")
    image, provenance = select_client_capture(None, mss, client_size=(8, 6))
    assert image is mss
    assert provenance.integrity == "uncertain"
    assert provenance.source == "uncertain"
    assert provenance.mss_box is not None


def test_prepare_live_target_fails_before_binding_when_focus_cannot_settle(monkeypatch) -> None:
    target = _target()
    monkeypatch.setattr("loop.adaptive.cli._resolve_live_target", lambda *_a: target)
    monkeypatch.setattr(
        "loop.os.window.focus_window_and_settle", lambda _target: False
    )
    try:
        _prepare_live_target("target")
    except RuntimeError as exc:
        assert "refusing observation" in str(exc)
    else:
        raise AssertionError("focus failure must stop before the first observation")


def test_focus_loss_stops_before_capture_or_ledger(monkeypatch) -> None:
    target = _target()
    captured = False

    class Session:
        ledger = type("Ledger", (), {"append_event": lambda *_a, **_k: None})()

    class Assembler:
        def update(self, *_a, **_k):
            raise AssertionError("perception must not run after focus loss")

    monkeypatch.setattr("loop.os.window.is_foreground", lambda _target: False)

    def grab(_target):
        nonlocal captured
        captured = True
        raise AssertionError("capture must not run after focus loss")

    monkeypatch.setattr("loop.os.window.grab_client_capture", grab)
    observe = _live_observe_factory(Session(), target, Assembler())
    try:
        observe()
    except RuntimeError as exc:
        assert "lost foreground" in str(exc)
    else:
        raise AssertionError("focus loss must fail closed")
    assert captured is False


def test_live_observe_rejects_fabricated_trusted_desktop_source(monkeypatch) -> None:
    target = _target()

    class Session:
        ledger = type("Ledger", (), {"append_event": lambda *_a, **_k: None})()

    class Assembler:
        def update(self, *_a, **_k):
            raise AssertionError("untrusted source must not reach perception")

    monkeypatch.setattr("loop.os.window.is_foreground", lambda _target: True)
    monkeypatch.setattr(
        "loop.os.window.target_identity",
        lambda target, selector="": {"hwnd": target.hwnd, "pid": 9},
    )
    monkeypatch.setattr(
        "loop.os.window.grab_client_capture",
        lambda _target: (
            Image.new("RGB", (8, 6), "white"),
            type(
                "P",
                (),
                {
                    "integrity": "trusted",
                    "source": "mss-client",
                    "reason": "desktop diagnostic",
                    "identity_before": None,
                    "identity_after": None,
                    "pinned_hwnd": None,
                    "foreground_before": None,
                    "foreground_after": None,
                },
            )(),
        ),
    )
    observe = _live_observe_factory(Session(), target, Assembler())
    try:
        observe()
    except RuntimeError as exc:
        assert "untrusted capture" in str(exc)
    else:
        raise AssertionError("desktop-coordinate source must not be trusted")


def test_live_observe_waits_for_black_transition_before_perception(monkeypatch) -> None:
    target = _target()
    events = []
    updates = []
    captures = iter((
        (Image.new("RGB", (8, 6), "black"), CaptureProvenance(
            "uncertain", (8, 6), (0, 0, 1, 1), "uncertain", "hwnd-black;mss-black"
        )),
        (Image.new("RGB", (8, 6), "white"), CaptureProvenance(
            "hwnd-wgc", (8, 6), (0, 0, 1, 1), "trusted", "ok"
        )),
    ))

    class Session:
        ledger = type("Ledger", (), {"append_event": lambda _self, *a: events.append(a)})()

        def observe(self, graph):
            return graph

    class Assembler:
        def update(self, image, **_kwargs):
            updates.append(image.getpixel((0, 0)))
            return (), type("Graph", (), {"snapshot_id": "fresh", "nodes": ()})()

    monkeypatch.setattr("loop.os.window.is_foreground", lambda _target: True)
    monkeypatch.setattr("loop.os.window.target_identity", lambda _target: {"hwnd": 77, "pid": 9})
    monkeypatch.setattr("loop.os.window.grab_client_capture", lambda _target: next(captures))
    monkeypatch.setattr("loop.adaptive.cli.time.sleep", lambda _seconds: None)
    observe = _live_observe_factory(Session(), target, Assembler())
    observe.on_transition = lambda: events.append(("input_released", {}))
    observe()
    assert updates == [(255, 255, 255)]
    assert observe.transition_wait_s >= 0
    assert sum(kind == "input_released" for kind, _payload in events) == 1
    assert any(kind == "capture_transition" for kind, _payload in events)


def test_live_observe_stops_after_black_transition_timeout(monkeypatch) -> None:
    target = _target()
    monkeypatch.setattr("loop.os.window.is_foreground", lambda _target: True)
    monkeypatch.setattr("loop.os.window.target_identity", lambda _target: {"hwnd": 77, "pid": 9})
    monkeypatch.setattr("loop.os.window.grab_client_capture", lambda _target: (
        Image.new("RGB", (8, 6), "black"), CaptureProvenance(
            "uncertain", (8, 6), (0, 0, 1, 1), "uncertain", "hwnd-black;mss-black"
        )
    ))
    monkeypatch.setattr("loop.adaptive.cli._BLACK_TRANSITION_TIMEOUT_S", 0)
    observe = _live_observe_factory(object(), target, object())
    try:
        observe()
    except RuntimeError as exc:
        assert "black transition exceeded" in str(exc)
    else:
        raise AssertionError("a persistent black capture must time out")


def test_black_transition_discards_pending_choice_and_releases_input() -> None:
    from loop.adaptive.controller import NativeShadowLoop

    reasons = []
    loop = object.__new__(NativeShadowLoop)
    loop._pending = object()
    loop._owner = type("Owner", (), {"release_all": lambda _self, reason: reasons.append(reason)})()
    loop.interrupt_for_capture_transition()
    assert loop._pending is None
    assert reasons == ["black_transition"]


def test_live_ocr_omits_low_confidence(monkeypatch) -> None:
    box = [[0, 0], [10, 0], [10, 10], [0, 10]]

    class _Out:
        txts = ("Continue", "Slay te")
        boxes = (box, box)
        scores = (1.0, 0.6)

    class _Engine:
        def __call__(self, _image, use_cls=False):
            return _Out()

    monkeypatch.setattr("loop.adaptive.cli._rapid_engine", lambda: _Engine())
    tokens = _live_ocr_tokens(Image.new("RGB", (20, 20), "white"))
    assert [text for text, _bbox in tokens] == ["Continue"]


def test_visual_ownership_tracker_selects_small_ocr_only(monkeypatch) -> None:
    calls = []

    def ocr(_image, *, small=False):
        calls.append(small)
        return []

    monkeypatch.setattr("loop.adaptive.cli._live_ocr_tokens", ocr)
    fused = type("Assembler", (), {
        "_tracker": type("Tracker", (), {"require_visual_ownership": True})()
    })()
    legacy = type("Assembler", (), {"_tracker": None})()
    image = Image.new("RGB", (20, 20), "white")

    _live_ocr_for_assembler(fused)(image)
    _live_ocr_for_assembler(legacy)(image)

    assert calls == [True, False]


class _Clock:
    """Deterministic monotonic clock; each read advances like a real clock."""

    def __init__(self, start: float = 1000.0, step: float = 1e-6) -> None:
        self.t = start
        self.step = step

    def __call__(self) -> float:
        value = self.t
        self.t += self.step
        return value

    def advance(self, seconds: float) -> None:
        self.t += seconds


class _FakeTime:
    def __init__(self, clock: _Clock) -> None:
        self.monotonic = clock


def _patch_sync_capture(monkeypatch, clock: _Clock, grab, *, hwnd: int = 77):
    target = _target(hwnd)
    identity = {"hwnd": hwnd, "pid": 123}
    monkeypatch.setattr(window, "time", _FakeTime(clock))
    monkeypatch.setattr(window, "_sync_client", lambda value: value)
    monkeypatch.setattr(window, "target_identity", lambda value: dict(identity))
    monkeypatch.setattr(window.user32, "GetForegroundWindow", lambda: hwnd)
    monkeypatch.setattr(window, "grab_hwnd_wgc", lambda value: None)
    monkeypatch.setattr(window, "grab_hwnd_imagegrab", grab)
    return target


def test_identical_captures_get_distinct_acquisition_times(monkeypatch) -> None:
    clock = _Clock()
    image = Image.new("RGB", (8, 6), (180, 40, 20))
    target = _patch_sync_capture(monkeypatch, clock, lambda value: image)

    _, first = window.grab_client_capture(target)
    _, second = window.grab_client_capture(target)

    assert first.acquisition_age_known is True
    assert second.acquisition_age_known is True
    assert first.acquisition_origin == window.ACQ_ORIGIN_PRE_GRAB_SYNC
    assert first.acquisition_ts_monotonic is not None
    assert second.acquisition_ts_monotonic is not None
    assert first.acquisition_ts_monotonic != second.acquisition_ts_monotonic


def test_slow_synchronous_grab_bounds_age_conservatively(monkeypatch) -> None:
    clock = _Clock()
    image = Image.new("RGB", (8, 6), (180, 40, 20))

    def slow_grab(_value):
        clock.advance(1.2)
        return image

    target = _patch_sync_capture(monkeypatch, clock, slow_grab)
    _, provenance = window.grab_client_capture(target)

    # The stamp is taken before the grab, so the true age can only be smaller
    # than the measured gap: the reported bound never understates staleness.
    assert provenance.acquisition_age_known is True
    assert clock.t - provenance.acquisition_ts_monotonic >= 1.2


def _verified_snapshot() -> StateSnapshot:
    rev = empty_schema().revision.revision_id
    surface = ActionSurface(snapshot_id="s1", schema_revision_id=rev, action_ids=())
    return StateSnapshot(
        snapshot_id="s1",
        schema_revision_id=rev,
        mode_id=None,
        fields={},
        entities={},
        surface=surface,
    )


def test_observation_age_and_sequence_contract_fails_closed() -> None:
    observation = build_native_observation(
        _verified_snapshot(),
        episode_id="ep",
        episode_generation=1,
        capture_ts_monotonic=10.0,
        publication_ts_monotonic=10.2,
        capture_integrity="trusted",
        capture_provenance=("hwnd-wgc",),
        hwnd=77,
        pid=9,
        target_identity={"hwnd": "77"},
        quality=ObservationQuality.trusted,
    )
    # A producer that supplies no sequence/age bound leaves them unavailable;
    # unknown age is not silently eligible for input.
    assert observation.observation_sequence is None
    assert observation.capture_age_known is False
    assert observation.acquisition_is_actuation_eligible() is False

    bound = replace(
        observation,
        capture_age_known=True,
        acquisition_origin="pre-grab-sync",
        observation_sequence=1,
    )
    assert bound.acquisition_is_actuation_eligible() is True
