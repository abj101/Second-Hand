from __future__ import annotations

from types import SimpleNamespace

from PIL import Image, ImageDraw

from loop.agent.discovery import difference_box
from loop.agent.loop import CollectAgent
from loop.agent.perception_cache import PerceptionCache
from loop.agent.perceive import SceneDescription
from loop.fixtures.sts2 import Sts2Fixture
from loop.log.schema import ActionLog
from loop.memory.screen_objects import ScreenObjectStore
from loop.os.aim import AimGuide, AimMark
from loop.os.profile import CaptureProfile, SettleResult
from loop.os.chords import is_empty_chord


def _mark(mid: int, label: str, x: float, y: float, *, role: str = "button") -> AimMark:
    return AimMark(
        id=mid, label=label, role=role, x=x, y=y,
        x0=x - .08, y0=y - .035, x1=x + .08, y1=y + .035,
        source="test", confidence=.99,
    )


def _reward_images() -> tuple[Image.Image, Image.Image]:
    before = Image.new("RGB", (240, 160), "#18202a")
    after = before.copy()
    old = ImageDraw.Draw(before)
    new = ImageDraw.Draw(after)
    # Gold vanishes and the card row compacts into its former place.  Skip is
    # intentionally identical and outside the STS2 stack ROI.
    old.rectangle((72, 60, 168, 70), fill="#f6c744")
    old.rectangle((72, 88, 168, 98), fill="#c8c8c8")
    old.rectangle((202, 140, 232, 151), fill="#8aa1b8")
    new.rectangle((72, 60, 168, 70), fill="#808080")
    new.rectangle((202, 140, 232, 151), fill="#8aa1b8")
    return before, after


def test_sts2_reward_reflow_is_limited_to_movable_stack() -> None:
    fixture = Sts2Fixture(controller=None)
    assert fixture._reward_reflow_roi((.30, .42, .70, .50)) == (.18, .42, .82, .78)
    assert fixture._reward_reflow_roi((.84, .84, .96, .92)) is None  # Skip
    assert fixture._reward_reflow_roi((.02, .02, .16, .14)) is None  # HUD


def test_reward_roi_detects_compaction_when_whole_frame_change_is_small() -> None:
    before, after = _reward_images()
    assert difference_box(before, after, union=True) is not None
    changed = difference_box(before, after, roi=(.18, .20, .82, .78), union=True)
    assert changed is not None
    assert .35 < changed[1] < .65 and .55 < changed[3] < .70
    assert difference_box(before, after, roi=(.84, .84, .98, .98), union=True) is None


def test_reward_confirmation_uses_cropped_thumb_and_compaction_settings(monkeypatch) -> None:
    image = Image.new("RGB", (200, 100), "black")
    fixture = SimpleNamespace(
        quiet_timeout_ms=8000, confirm_timeout_ms=3000, profile=CaptureProfile(),
        grab_image=lambda: image.copy(),
    )
    agent = CollectAgent.__new__(CollectAgent)
    agent.fixture = fixture
    seen: dict[str, object] = {}

    def fake_crop(frame, roi):
        seen["roi"] = roi
        return frame.crop((40, 20, 160, 80))

    def fake_quiet(grab, **kwargs):
        seen.update(kwargs)
        seen["thumb_size"] = grab().size
        return SettleResult(elapsed_ms=160, changed=True, polls=2)

    monkeypatch.setattr("loop.agent.loop.crop", fake_crop)
    monkeypatch.setattr("loop.agent.loop.wait_until_quiet", fake_quiet)
    agent._confirm_after_action(roi=(.2, .2, .8, .8))
    assert seen == {
        "roi": (.2, .2, .8, .8), "threshold": .012, "timeout_ms": 1100,
        "poll_ms": 80, "quiet_ms": 160, "should_stop": seen["should_stop"],
        "thumb_size": (64, 64),
    }


def _local_agent(before: Image.Image) -> CollectAgent:
    agent = CollectAgent.__new__(CollectAgent)
    agent.fixture = Sts2Fixture(controller=None)
    agent.journal = SimpleNamespace(lexicon=None)
    agent._aim = AimGuide.from_marks(before, [
        _mark(1, "16 Gold", .50, .41), _mark(2, "Add a card to your deck", .50, .58),
        _mark(3, "Skip", .89, .91),
    ])
    agent._last_ocr_words = []
    agent._last_click = None
    agent._host_stats = {"screen": "reward", "activity": "reward"}
    agent._obs_metrics = dict(agent._host_stats)
    agent._step_id = 1
    agent._entity_tracker = SimpleNamespace(entities=[])
    agent._perception_cache = PerceptionCache()
    agent._cached_perceive_scene = None
    agent._cached_perceive_scene_step = None
    agent._force_perceive = False
    agent._last_execution_plan = {"label": "16 Gold"}
    agent._scene_desc = SceneDescription(screen="reward", controls=[
        {"label": "16 Gold", "role": "button"},
        {"label": "Add a card to your deck", "role": "button"},
        {"label": "Skip", "role": "button"},
    ])
    return agent


def test_local_reward_refresh_republishes_compacted_controls_without_full_detector(monkeypatch) -> None:
    before, after = _reward_images()
    agent = _local_agent(before)
    calls = {"gui": 0, "full": 0, "vlm": 0}

    monkeypatch.setattr("loop.agent.loop.ocr_click_marks", lambda _image: [])
    monkeypatch.setattr("loop.agent.loop.detect_marks", lambda _image: [_mark(7, "icon", .50, .15, role="")])
    monkeypatch.setattr("loop.agent.loop.gui_click_marks", lambda *_a, **_k: calls.__setitem__("gui", calls["gui"] + 1))
    monkeypatch.setattr("loop.agent.loop.build_marks", lambda *_a, **_k: [_mark(7, "Add a card to your deck", .50, .15)])

    def finalize(_scene, **_kwargs):
        labels = [mark.label for mark in agent._aim.marks if mark.label != "16 Gold"]
        return SimpleNamespace(interactive=lambda: [SimpleNamespace(label=label) for label in labels])

    agent._finalize_action_surface = finalize
    assert agent._refresh_sts2_reward_reflow(
        before, after, action=ActionLog(mark=1), reflow_roi=(.18, .20, .82, .78),
    )
    labels = [(mark.label, round(mark.y, 2)) for mark in agent._aim.marks]
    assert ("16 Gold", .41) not in labels
    assert ("Add a card to your deck", .35) in labels
    assert ("Skip", .91) in labels
    assert calls == {"gui": 0, "full": 0, "vlm": 0}


def test_local_reward_refresh_keeps_surface_on_noop(monkeypatch) -> None:
    before, _after = _reward_images()
    agent = _local_agent(before)
    monkeypatch.setattr("loop.agent.loop.difference_box", lambda *_a, **_k: None)
    assert agent._refresh_sts2_reward_reflow(
        before, before.copy(), action=ActionLog(mark=1), reflow_roi=(.18, .20, .82, .78),
    )


def test_local_reward_scan_pads_narrow_change_against_full_stack_not_clicked_suffix(monkeypatch) -> None:
    """OCR needs nearby Loot context, but local scans must still exclude Skip/HUD."""
    before, after = _reward_images()
    agent = _local_agent(before)
    seen: dict[str, tuple[float, float, float, float]] = {}
    # The clicked Gold suffix begins at y=.42. Its tiny paint change must
    # include .08 of context above that line, bounded by the full stack.
    monkeypatch.setattr("loop.agent.loop.difference_box", lambda *_args, **_kwargs: (.45, .46, .55, .48))
    monkeypatch.setattr("loop.agent.loop.crop", lambda image, box: seen.setdefault("box", box) and image)
    monkeypatch.setattr("loop.agent.loop.ocr_click_marks", lambda _image: [])
    monkeypatch.setattr("loop.agent.loop.detect_marks", lambda _image: [])
    monkeypatch.setattr("loop.agent.loop.build_marks", lambda *_args, **_kwargs: [])
    agent._finalize_action_surface = lambda *_args, **_kwargs: SimpleNamespace(
        interactive=lambda: [SimpleNamespace(label="Skip")]
    )

    assert not agent._refresh_sts2_reward_reflow(
        before, after, action=ActionLog(mark=1), reflow_roi=(.18, .42, .82, .78),
    )

    assert all(
        abs(actual - expected) < 1e-9
        for actual, expected in zip(seen["box"], (.37, .38, .63, .56))
    )
    # Fixture-wide stack stays below the HUD and above the fixed Skip row.
    assert .20 <= seen["box"][1] < .42
    assert seen["box"][3] <= .78 < .91


def test_local_reward_phrase_does_not_bind_matching_ocr_outside_affected_roi() -> None:
    """Local reward finalization may only consume OCR emitted by its crop."""
    agent = CollectAgent.__new__(CollectAgent)
    image = Image.new("RGB", (128, 96))
    agent._aim = AimGuide.from_marks(image, [_mark(1, "icon", .50, .40, role="")])
    agent._host_stats = agent._obs_metrics = {"screen": "reward"}
    agent._reward_phrase_roi = (.18, .20, .82, .50)
    # This complete phrase is valid current OCR, but belongs to a fixed area
    # outside the transient loot crop and must not ground the local row.
    agent._last_ocr_words = [
        _mark(2, "Add", .34, .72), _mark(3, "a", .42, .72),
        _mark(4, "card", .50, .72), _mark(5, "to", .58, .72),
        _mark(6, "your", .66, .72), _mark(7, "deck", .74, .72),
    ]

    reconciled = agent._reconcile_scene_marks(SceneDescription(
        screen="reward", controls=[{"label": "Add a card to your deck", "role": "button"}],
    ))

    assert [(mark.label, mark.role) for mark in reconciled] == [("icon", "")]


def test_successful_local_reward_refresh_replaces_cached_gold_evidence(monkeypatch) -> None:
    """A later low-MAD cache decision must start from the compacted reward frame."""
    before, after = _reward_images()
    agent = _local_agent(before)
    agent._host_stats = agent._obs_metrics = {"screen": "reward", "activity": "reward"}
    agent._step_id = 17
    agent._entity_tracker = SimpleNamespace(entities=[])
    agent._perception_cache = PerceptionCache()
    agent._perception_cache.update(
        view=before, screen="reward", ocr_words=[_mark(10, "16 Gold", .50, .41)],
        marks=list(agent._aim.marks), host_stats=agent._host_stats,
    )
    agent._cached_perceive_scene = agent._scene_desc
    agent._cached_perceive_scene_step = 16
    agent._force_perceive = True

    monkeypatch.setattr("loop.agent.loop.ocr_click_marks", lambda _image: [
        _mark(20, "Add", .34, .15), _mark(21, "a", .42, .15),
        _mark(22, "card", .50, .15), _mark(23, "to", .58, .15),
        _mark(24, "your", .66, .15), _mark(25, "deck", .74, .15),
    ])
    monkeypatch.setattr("loop.agent.loop.detect_marks", lambda _image: [_mark(30, "icon", .50, .15, role="")])
    monkeypatch.setattr("loop.agent.loop.build_marks", lambda *_args, **_kwargs: [
        _mark(30, "Add a card to your deck", .50, .15),
    ])
    agent._finalize_action_surface = lambda _scene, **_kwargs: SimpleNamespace(
        interactive=lambda: [SimpleNamespace(label=mark.label) for mark in agent._aim.marks]
    )

    assert agent._refresh_sts2_reward_reflow(
        before, after, action=ActionLog(mark=1), reflow_roi=(.18, .20, .82, .78),
    )
    reuse = agent._perception_cache.evaluate(
        view=after, screen="reward", host_stats=agent._host_stats,
        prev_host_stats=agent._host_stats,
    )

    assert reuse.reuse_ocr and reuse.reason == "frame-identical"
    assert all(word.label != "16 Gold" for word in agent._perception_cache.last_ocr)
    assert [row["label"] for row in agent._cached_perceive_scene.controls] == [
        "Add a card to your deck", "Skip",
    ]
    assert agent._cached_perceive_scene_step == 17
    assert not agent._force_perceive


def test_ambiguous_or_oversized_local_refresh_requests_one_full_rebuild(monkeypatch) -> None:
    before, after = _reward_images()
    agent = _local_agent(before)
    # An oversized envelope is deliberately not trusted as a local reflow.
    monkeypatch.setattr("loop.agent.loop.difference_box", lambda *_a, **_k: (.18, .20, .82, .78))
    assert not agent._refresh_sts2_reward_reflow(
        before, after, action=ActionLog(mark=1), reflow_roi=(.18, .20, .82, .78),
    )


def test_failed_local_refresh_uses_full_mark_path_once(monkeypatch) -> None:
    before, after = _reward_images()
    agent = CollectAgent.__new__(CollectAgent)
    agent.fixture = SimpleNamespace(
        observe_image=lambda image: image,
        scorer=lambda _image: {"screen": "reward"},
    )
    agent._two_stage = lambda: False
    agent._host_stats = {"screen": "reward"}
    agent._obs_metrics = {"screen": "reward"}
    agent._last_click = None
    agent._planned_hand_signature = None
    agent.journal = SimpleNamespace(lexicon=None)
    agent._layout_adapter = lambda: None
    agent._refresh_sts2_reward_reflow = lambda *_a, **_k: False
    agent._perception_reuse = lambda *_a, **_k: None
    agent._track_combat_index = lambda: None
    agent._maybe_apply_combat_exit_transition = lambda metrics: metrics
    calls = {"full": 0}

    def full_marks(*_args, **_kwargs):
        calls["full"] += 1
        return [], [], [], None

    monkeypatch.setattr(agent, "_build_turn_marks", full_marks)
    monkeypatch.setattr("loop.agent.loop.build_aim", lambda *_a, **_k: None)
    agent._refresh_obs_after_action(
        after, prev_metrics={"screen": "reward"}, execution_frame=before,
        action=ActionLog(mark=1), reflow_roi=(.18, .20, .82, .78),
    )
    assert calls == {"full": 1}


def test_reward_fast_path_reads_host_once_without_scorer_or_full_marks(monkeypatch) -> None:
    """A successful local publish must remain entirely out of global perception."""
    before, after = _reward_images()
    calls = {"host": 0, "local": 0, "scorer": 0, "full": 0, "track": 0}
    agent = CollectAgent.__new__(CollectAgent)
    agent.fixture = SimpleNamespace(
        id="sts2",
        observe_image=lambda image: image,
        scorer=lambda _image: calls.__setitem__("scorer", calls["scorer"] + 1),
    )
    agent._two_stage = lambda: True
    agent.session_id = "test"
    agent._step_id = 1
    agent._host_stats = {"screen": "reward"}
    agent._obs_metrics = {"screen": "reward"}
    agent._last_ocr_words = []
    agent._aim = None
    agent._last_click = None
    agent._read_host_stats = lambda _image: (
        calls.__setitem__("host", calls["host"] + 1) or {"screen": "reward", "activity": "reward"}
    )

    def local(*_args, **_kwargs):
        calls["local"] += 1
        return True

    agent._refresh_sts2_reward_reflow = local
    agent._build_turn_marks = lambda *_args, **_kwargs: (
        calls.__setitem__("full", calls["full"] + 1) or (_ for _ in ()).throw(AssertionError("full marks"))
    )
    agent._track_combat_index = lambda: calls.__setitem__("track", calls["track"] + 1)

    agent._refresh_obs_after_action(
        after, prev_metrics={"screen": "reward"}, execution_frame=before,
        action=ActionLog(mark=1), reflow_roi=(.18, .20, .82, .78),
    )

    assert calls == {"host": 1, "local": 1, "scorer": 0, "full": 0, "track": 1}


def test_reward_screen_transition_bypasses_local_refresh_and_uses_one_full_rebuild() -> None:
    """A post-click transition is never reconstructed from the old reward surface."""
    before, after = _reward_images()
    calls = {"local": 0, "scorer": 0, "full": 0, "save": 0}
    agent = CollectAgent.__new__(CollectAgent)
    agent.fixture = SimpleNamespace(
        id="sts2",
        observe_image=lambda image: image,
        scorer=lambda _image: (calls.__setitem__("scorer", calls["scorer"] + 1) or {"screen": "map"}),
    )
    agent._two_stage = lambda: True
    agent.session_id = "test"
    agent._step_id = 1
    agent._host_stats = {"screen": "reward"}
    agent._obs_metrics = {"screen": "reward"}
    agent._last_click = None
    agent._planned_hand_signature = None
    agent._read_host_stats = lambda _image: {"screen": "map", "activity": "map"}
    agent._refresh_sts2_reward_reflow = lambda *_args, **_kwargs: calls.__setitem__("local", calls["local"] + 1) or True
    agent._perception_reuse = lambda *_args, **_kwargs: None
    agent._build_turn_marks = lambda *_args, **_kwargs: (
        calls.__setitem__("full", calls["full"] + 1) or ([], [], [], 0.0)
    )
    agent._screen_objects = SimpleNamespace(save_frame=lambda **_kwargs: calls.__setitem__("save", calls["save"] + 1) or [])
    agent._build_frame_model = lambda: None
    agent._combat_screen_now = lambda: False
    agent._track_combat_index = lambda: None
    agent._maybe_apply_combat_exit_transition = lambda metrics: metrics

    agent._refresh_obs_after_action(
        after, prev_metrics={"screen": "reward"}, execution_frame=before,
        action=ActionLog(mark=1), reflow_roi=(.18, .20, .82, .78),
    )

    assert calls == {"local": 0, "scorer": 1, "full": 1, "save": 1}


def test_reward_dispatch_rejects_stale_gold_and_accepts_current_add(tmp_path) -> None:
    store = ScreenObjectStore(tmp_path / "objects.db")
    try:
        old = store.save_frame(run_id="r", step_id=1, screen="reward", marks=[_mark(1, "16 Gold", .50, .41)])
        gold = store.by_id(1)
        current = store.save_frame(run_id="r", step_id=2, screen="reward", marks=[
            _mark(2, "Add a card to your deck", .50, .41), _mark(3, "Skip", .89, .91),
        ])
        add = store.by_id(2)
        assert gold is not None and add is not None
        agent = CollectAgent.__new__(CollectAgent)
        agent._screen_objects = store
        agent._obs_metrics = agent._host_stats = {"screen": "reward"}
        agent._aim = AimGuide.from_marks(Image.new("RGB", (80, 80)), current)
        agent._last_decision_schema = "v2"
        agent._last_snapshot_id = "new"
        agent._pending_exec_error = None
        agent._last_legal_reason = None
        agent._force_perceive = False
        agent.config = SimpleNamespace(burst_ms=10)
        agent._two_stage = lambda: True

        agent._last_execution_plan = {"action": "click_control", "control_id": gold.object_id,
            "object_id": gold.object_id, "mark_id": 1, "role": "button", "point": {"x": gold.x, "y": gold.y}, "snapshot_id": "old"}
        stale = agent._final_combat_dispatch_gate(ActionLog(mark=1, click_x=gold.x, click_y=gold.y,
            source_role="button", source_object_id=gold.object_id, duration_ms=10), inspect_requested=False)
        assert is_empty_chord(stale) and agent._pending_exec_error == "stale-control-binding"

        agent._last_execution_plan = {"action": "click_control", "control_id": add.object_id,
            "object_id": add.object_id, "mark_id": 2, "role": "button", "point": {"x": add.x, "y": add.y}, "snapshot_id": "new"}
        fresh = ActionLog(mark=2, click_x=add.x, click_y=add.y, source_role="button", source_object_id=add.object_id, duration_ms=10)
        assert agent._final_combat_dispatch_gate(fresh, inspect_requested=False) == fresh
    finally:
        store.close()
