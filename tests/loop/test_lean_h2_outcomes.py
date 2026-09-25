"""H2 bounded post-action observation and outcome receipts (fake clock, no OS input).

The window is driven by a shimmed ``loop.adaptive.session.time`` so the tests never
sleep in real time and every after-capture is explicit about being later than
execution.
"""

from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import loop.adaptive.session as session_mod
from loop.adaptive.arm import _bind_lease_context
from loop.adaptive.contracts import (
    EFFECT_STATUS_NOT_EXECUTED,
    EFFECT_STATUS_NO_EFFECT,
    EFFECT_STATUS_UNOBSERVABLE,
    EFFECT_STATUS_VERIFIED,
    EFFECT_STATUS_WRONG_EFFECT,
    ActuationLease,
    ExecutionReceipt,
    PostActionObservationPolicy,
)
from loop.adaptive.schema import (
    ActionInvocation,
    ActionSpec,
    EffectPredicate,
    EntityTypeSpec,
    FieldSpec,
    ParamSpec,
    PrimitiveStep,
    SceneGraph,
    empty_schema,
)
from loop.adaptive.session import Session
from loop.adaptive.world import CONTROL_NODE_ID, SCALAR_FIELD_ID, NeutralWorld

WINDOW = PostActionObservationPolicy(
    max_window_s=0.5, cadence_s=0.1, max_frames=6, required_negative_frames=2
)


class _Clock:
    """Monotonic shim: reading never advances, sleeping does."""

    def __init__(self, start: float = 500.0) -> None:
        self.now = start

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += max(float(seconds), 0.001)


def _schema(*, effect: str = "increased"):
    return replace(
        empty_schema(),
        fields=(FieldSpec(SCALAR_FIELD_ID, "number"),),
        entity_types=(EntityTypeSpec("type_unit", ()),),
        actions=(
            ActionSpec(
                action_id="act_click",
                parameters=(ParamSpec("target", "entity"),),
                preconditions=(),
                primitive_plan=(PrimitiveStep("sleep", ()),),
                expected_effects=(EffectPredicate(SCALAR_FIELD_ID, effect),),
            ),
        ),
    )


class _Frames:
    """Fake after-capture source: one scene + one provenance dict per frame."""

    def __init__(self, clock: _Clock, frames: list[dict], scene_fn=None) -> None:
        self.clock = clock
        self.frames = frames
        self.scene_fn = scene_fn
        self.index = 0
        self.current: dict | None = None
        self.order: list[str] = []

    def scene_source(self):
        frame = self.frames[min(self.index, len(self.frames) - 1)]
        self.current = frame
        self.clock.now += 0.05
        self.order.append("capture")
        if self.scene_fn is not None:
            return self.scene_fn()
        return frame["scene"]

    def evidence_source(self) -> dict:
        frame = self.current or {}
        evidence = dict(frame.get("evidence") or {})
        evidence.setdefault("integrity", "trusted")
        evidence.setdefault("hwnd", 1)
        evidence.setdefault("pid", 1)
        evidence.setdefault("source", "test-after")
        evidence.setdefault("capture_ts_monotonic", self.clock.now)
        evidence.setdefault("observation_sequence", self.index + 1)
        self.index += 1
        return evidence


def _setup(tmp_path: Path, *, effect: str = "increased"):
    session = Session(tmp_path)
    session.set_schema(_schema(effect=effect))
    session.bind_target(
        {"selector": "t", "exe": "e.exe", "window_class": "C", "hwnd": 1, "pid": 1}
    )
    world = NeutralWorld()
    before = session.observe(world.reset(0))
    inv = ActionInvocation(
        "act_click",
        {"target": CONTROL_NODE_ID},
        before.snapshot_id,
        before.schema_revision_id,
    )
    lease = ActuationLease(
        episode_generation=0,
        snapshot_id=before.snapshot_id,
        schema_revision_id=before.schema_revision_id,
        catalog_revision="cat",
        directive_revision=0,
        policy_revision="pol",
        hwnd=1,
        pid=1,
        target_identity={"selector": "t", "exe": "e.exe", "window_class": "C"},
        expires_at_monotonic=time.monotonic() + 60.0,
        selected_action_id=inv.action_id,
        hard_mask_digest="mask",
    )
    _bind_lease_context(session, lease)
    owner = MagicMock()
    owner.check_target.return_value = (True, "ok")
    owner.drain_primitive_results.return_value = ()
    owner.release_all.return_value = "released"
    return session, world, before, inv, lease, owner


def _act(
    monkeypatch,
    session,
    world,
    before,
    inv,
    lease,
    owner,
    frames: _Frames,
    *,
    policy: PostActionObservationPolicy = WINDOW,
    run=None,
) -> ExecutionReceipt:
    clock = frames.clock
    monkeypatch.setattr(session_mod, "time", clock)
    receipt = session.act(
        inv,
        run if run is not None else (lambda _s, _b: None),
        frames.scene_source,
        before=before,
        lease=lease,
        owner=owner,
        get_scene=lambda: world.observe()[1],
        observation_policy=policy,
        after_evidence=frames.evidence_source,
    )
    assert isinstance(receipt, ExecutionReceipt)
    return receipt


def test_delayed_effect_verifies_within_window(monkeypatch, tmp_path: Path):
    clock = _Clock()
    session, world, before, inv, lease, owner = _setup(tmp_path)

    def scene_at_capture():
        # The action landed but the animation settles only after two captures.
        if frames.index >= 2:
            world.click(CONTROL_NODE_ID)
        return world.observe()[1]

    frames = _Frames(clock, [{}, {}, {}], scene_fn=scene_at_capture)
    receipt = _act(monkeypatch, session, world, before, inv, lease, owner, frames)

    assert receipt.accepted is True
    assert receipt.expected_effect.status == EFFECT_STATUS_VERIFIED
    assert receipt.expected_effect.verified is True
    assert receipt.expected_effect.reason == "verified"
    assert len(receipt.expected_effect.evidence) == 3
    verdicts = [record["verdict"] for record in receipt.expected_effect.evidence]
    assert verdicts == [EFFECT_STATUS_NO_EFFECT, EFFECT_STATUS_NO_EFFECT, EFFECT_STATUS_VERIFIED]
    assert all(record["trusted"] for record in receipt.expected_effect.evidence)
    stamps = [record["capture_ts_monotonic"] for record in receipt.expected_effect.evidence]
    assert stamps == sorted(stamps) and len(set(stamps)) == 3


def test_no_effect_only_after_adequate_negative_evidence(monkeypatch, tmp_path: Path):
    clock = _Clock()
    session, world, before, inv, lease, owner = _setup(tmp_path)
    scene = world.observe()[1]
    frames = _Frames(clock, [{"scene": scene} for _ in range(6)])
    receipt = _act(monkeypatch, session, world, before, inv, lease, owner, frames)

    assert receipt.expected_effect.status == EFFECT_STATUS_NO_EFFECT
    assert receipt.expected_effect.verified is False
    evidence = receipt.expected_effect.evidence
    negative = [r for r in evidence if r["verdict"] == EFFECT_STATUS_NO_EFFECT]
    assert len(negative) >= WINDOW.required_negative_frames
    # The settling window ran to its bound rather than judging one immediate frame.
    assert len(evidence) > 1


def test_insufficient_trusted_evidence_is_unobservable(monkeypatch, tmp_path: Path):
    clock = _Clock()
    session, world, before, inv, lease, owner = _setup(tmp_path)
    scene = world.observe()[1]
    frames = _Frames(
        clock,
        [
            {"scene": scene, "evidence": {"integrity": "degraded"}},
            {"scene": scene, "evidence": {"hwnd": 777}},
            {"scene": scene, "evidence": {"integrity": "untrusted"}},
        ],
    )
    receipt = _act(monkeypatch, session, world, before, inv, lease, owner, frames)

    assert receipt.expected_effect.status == EFFECT_STATUS_UNOBSERVABLE
    assert receipt.expected_effect.verified is False
    assert receipt.expected_effect.reason == "insufficient trusted after-capture evidence"
    assert not any(r["trusted"] for r in receipt.expected_effect.evidence)
    reasons = {r["trust_reason"] for r in receipt.expected_effect.evidence}
    assert "after-capture target changed" in reasons
    assert any(r.startswith("after-capture integrity") for r in reasons)


def test_missing_perception_is_unobservable_not_wrong_effect(monkeypatch, tmp_path: Path):
    clock = _Clock()
    session, world, before, inv, lease, owner = _setup(tmp_path)
    blank = SceneGraph(snapshot_id="snap-black", nodes=())
    frames = _Frames(clock, [{"scene": blank}, {"scene": blank}])
    receipt = _act(monkeypatch, session, world, before, inv, lease, owner, frames)

    assert receipt.expected_effect.status == EFFECT_STATUS_UNOBSERVABLE
    assert receipt.expected_effect.status != EFFECT_STATUS_WRONG_EFFECT
    assert receipt.expected_effect.verified is False


def test_contradictory_evidence_is_wrong_effect(monkeypatch, tmp_path: Path):
    clock = _Clock()
    session, world, before, inv, lease, owner = _setup(tmp_path, effect="decreased")
    world.click(CONTROL_NODE_ID)  # the value rises although a drop was expected
    frames = _Frames(clock, [{"scene": world.observe()[1]}])
    receipt = _act(monkeypatch, session, world, before, inv, lease, owner, frames)

    assert receipt.expected_effect.status == EFFECT_STATUS_WRONG_EFFECT
    assert receipt.expected_effect.verified is False
    assert receipt.expected_effect.reason == "expected effect contradicted"


def test_after_capture_earlier_than_execution_is_insufficient(monkeypatch, tmp_path: Path):
    clock = _Clock()
    session, world, before, inv, lease, owner = _setup(tmp_path)
    scene = world.observe()[1]
    frames = _Frames(clock, [{"scene": scene, "evidence": {"capture_ts_monotonic": clock.now - 5.0}}])
    receipt = _act(monkeypatch, session, world, before, inv, lease, owner, frames)

    assert receipt.expected_effect.status == EFFECT_STATUS_UNOBSERVABLE
    assert receipt.expected_effect.evidence[0]["trust_reason"] == "after-capture not later than execution"


def test_repeated_acquisition_is_not_independent_confirmation(monkeypatch, tmp_path: Path):
    clock = _Clock()
    session, world, before, inv, lease, owner = _setup(tmp_path)
    scene = world.observe()[1]
    cached = {
        "scene": scene,
        "evidence": {"capture_ts_monotonic": clock.now + 0.05, "observation_sequence": 1},
    }
    frames = _Frames(clock, [cached, dict(cached), dict(cached)])
    receipt = _act(monkeypatch, session, world, before, inv, lease, owner, frames)

    # One independent capture is not enough negative evidence for the declared window.
    assert receipt.expected_effect.status == EFFECT_STATUS_UNOBSERVABLE
    reasons = [r["trust_reason"] for r in receipt.expected_effect.evidence]
    assert "repeated observation sequence" in reasons
    assert reasons.count("ok") == 1


def test_release_happens_before_any_settling_capture(monkeypatch, tmp_path: Path):
    clock = _Clock()
    session, world, before, inv, lease, owner = _setup(tmp_path)
    scene = world.observe()[1]
    frames = _Frames(clock, [{"scene": scene} for _ in range(4)])
    order: list[str] = []

    def release_all(reason: str) -> str:
        order.append("release")
        return "released"

    owner.release_all.side_effect = release_all
    frames.order = order  # capture events append into the same list

    receipt = _act(monkeypatch, session, world, before, inv, lease, owner, frames)

    assert "release" in order and "capture" in order
    assert order.index("release") < order.index("capture")
    assert receipt.release_status == "released"


def test_rejected_lease_is_not_executed_and_not_no_effect(monkeypatch, tmp_path: Path):
    clock = _Clock()
    session, world, before, inv, _lease, owner = _setup(tmp_path)
    scene = world.observe()[1]
    frames = _Frames(clock, [{"scene": scene}])
    monkeypatch.setattr(session_mod, "time", clock)
    runner = MagicMock()
    receipt = session.act(
        inv,
        runner,
        frames.scene_source,
        before=before,
        owner=owner,
        observation_policy=WINDOW,
        after_evidence=frames.evidence_source,
    )
    assert isinstance(receipt, ExecutionReceipt)
    assert receipt.accepted is False
    assert receipt.reason == "lease required"
    assert receipt.expected_effect.status == EFFECT_STATUS_NOT_EXECUTED
    assert receipt.expected_effect.status != EFFECT_STATUS_NO_EFFECT
    runner.assert_not_called()
    owner.run_plan.assert_not_called()


def test_status_contract_rejects_inconsistent_outcome():
    from loop.adaptive.contracts import ExpectedEffectOutcome

    with pytest.raises(ValueError, match="verified is true only"):
        ExpectedEffectOutcome(True, "noop", status=EFFECT_STATUS_NO_EFFECT)
    with pytest.raises(ValueError, match="unknown effect status"):
        ExpectedEffectOutcome(False, "noop", status="probably_fine")
