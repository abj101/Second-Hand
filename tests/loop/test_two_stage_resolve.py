from __future__ import annotations

from types import SimpleNamespace

from loop.log.schema import ActionLog
from loop.os.aim import AimGuide, AimMark, action_from_labels
from loop.os.chords import is_empty_chord
from PIL import Image, ImageDraw


def _guide(*labels: tuple[int, str]) -> AimGuide:
    marks = tuple(
        AimMark(
            id=mid,
            x=0.1 * mid,
            y=0.8,
            x0=0.0,
            y0=0.7,
            x1=0.2,
            y1=0.9,
            label=label,
        )
        for mid, label in labels
    )
    return AimGuide.from_marks(Image.new("RGB", (100, 100)), marks)


def _raw_probe(image: Image.Image) -> SimpleNamespace:
    return SimpleNamespace(
        pre_frame=image,
        fresh_frame=image,
        frames=(image, image),
        deltas=(0.1, 0.0),
        elapsed_ms=120.0,
        raw_frame=image,
    )


def test_unresolved_choice_stays_empty() -> None:
    guide = _guide((1, "Back"), (2, "Strike"))
    action = action_from_labels(guide, choice="Got it")
    assert action is None
    steered = ActionLog(duration_ms=400)
    assert is_empty_chord(steered)


def test_avoid_back_on_active_tutorial() -> None:
    from loop.agent.loop import CollectAgent
    from loop.memory.journal import FactJournal

    journal = FactJournal(path=None, fact_cap=4)
    loop = CollectAgent.__new__(CollectAgent)
    loop.journal = journal
    loop._blocked_marks = set()
    loop._aim = _guide((1, "Back"), (2, "End Turn"))
    loop._host_stats = {"tutorial": {"active": True, "title": "Combat Basics", "step": 1, "total": 3}}
    loop._obs_metrics = {"screen": "tutorial"}
    avoid = loop._avoid_for_turn(host_screen="tutorial", tutorial_info=loop._host_stats["tutorial"])
    assert "mk_1" in avoid


def test_apply_perceive_scene_calls_dino_fuse(monkeypatch) -> None:
    from types import SimpleNamespace

    from loop.agent.loop import CollectAgent, CollectConfig
    from loop.agent.perceive import SceneDescription
    from loop.memory.lexicon import MarkLexicon
    from loop.os.aim import AimGuide, AimMark

    image = Image.new("RGB", (64, 64))
    loop = CollectAgent.__new__(CollectAgent)
    loop.config = CollectConfig(dino=True, arm="no_skill")
    loop.journal = SimpleNamespace(lexicon=MarkLexicon())
    loop._host_stats = {}
    loop._obs_metrics = {"screen": "combat"}
    loop._last_obs_image = image
    loop._aim = AimGuide.from_marks(
        image,
        [AimMark(id=1, x=0.5, y=0.5, x0=0.4, y0=0.4, x1=0.6, y1=0.6, label="Strike")],
    )
    loop._frame_model = None
    loop._last_ocr_words = []
    loop._detector = None
    loop._dino_loaded = True

    fused: list[list[dict]] = []
    monkeypatch.setattr(loop, "_dino_fuse_after_perceive", lambda c: fused.append(c))
    monkeypatch.setattr(loop, "_ground_aim_from_scene", lambda _d: None)
    monkeypatch.setattr(loop, "_build_frame_model", lambda **_: None)

    desc = SceneDescription(
        screen="combat",
        controls=[{"label": "Strike", "role": "card", "confidence": "high"}],
    )
    controls = loop._apply_perceive_scene(desc)
    assert fused
    assert controls[0]["label"] == "Strike"


def test_rescan_scene_applies_perceive_pipeline(monkeypatch) -> None:
    from loop.agent.loop import CollectAgent, CollectConfig
    from loop.agent.perception_cache import PerceptionCache
    from loop.agent.perceive import SceneDescription
    from loop.model.gateway import ModelCall

    image = Image.new("RGB", (64, 64))
    loop = CollectAgent.__new__(CollectAgent)
    loop.config = CollectConfig(arm="no_skill", architecture="two_stage")
    loop._last_obs_image = image
    loop._obs_metrics = {}
    loop._host_stats = {}
    loop._frame_model = None
    loop._aim = None
    loop._perception_cache = PerceptionCache()
    loop._perceive_gw = lambda: object()
    loop.fixture = SimpleNamespace(obs_longest_side=64)
    desc = SceneDescription(screen="tutorial", controls=[{"label": "Got it", "role": "button"}])
    applied: list[SceneDescription] = []

    monkeypatch.setattr(
        "loop.agent.loop.perceive_scene",
        lambda *a, **k: (
            desc,
            ModelCall(text="{}", parsed={}, latency_ms=3.0, model="fake"),
        ),
    )
    monkeypatch.setattr(
        loop,
        "_apply_perceive_scene",
        lambda d, **kw: applied.append(d) or [],
    )

    ms = loop._rescan_scene(focus="Got it")
    assert len(applied) == 1
    assert applied[0] is desc
    assert ms == 3.0


def test_refresh_obs_after_action_preserves_fresh_cards_and_regrounds_enemy(monkeypatch, tmp_path) -> None:
    from loop.agent.loop import CollectAgent, CollectConfig
    from loop.agent.perceive import SceneDescription
    from loop.os.aim import AimGuide, AimMark

    image = Image.new("RGB", (64, 64))
    loop = CollectAgent.__new__(CollectAgent)
    loop.config = CollectConfig(architecture="two_stage", arm="no_skill")
    loop._last_click = (0.5, 0.85)
    loop._scene_desc = SceneDescription(
        screen="combat",
        controls=[
            {"label": "Strike", "role": "card", "confidence": "high"},
            {"label": "Jaw Worm", "role": "enemy", "confidence": "high"},
        ],
        hand_cards=[{"label": "Strike", "role": "card", "confidence": "high"}],
        enemies=[{"label": "Jaw Worm", "role": "enemy", "confidence": "high"}],
    )
    base_marks = [
        AimMark(id=1, x=0.5, y=0.86, x0=0.4, y0=0.8, x1=0.6, y1=0.92, label="Strike"),
        AimMark(id=2, x=0.75, y=0.63, x0=0.68, y0=0.56, x1=0.82, y1=0.70, label=""),
    ]
    loop._aim = AimGuide.from_marks(image, base_marks)
    loop._host_stats = {"screen": "combat"}
    loop._prev_host_stats = {}
    loop._frame_model = None
    loop._step_id = 2
    loop.session_id = "test-run"

    class _Fixture:
        def observe_image(self, after):  # type: ignore[no-untyped-def]
            return after

        def scorer(self, view):  # type: ignore[no-untyped-def]
            return {"screen": "combat"}

    loop.fixture = _Fixture()

    from loop.memory.screen_objects import ScreenObjectStore

    loop._screen_objects = ScreenObjectStore(tmp_path / "objects.db")

    monkeypatch.setattr(loop, "_perception_reuse", lambda *a, **k: False)
    monkeypatch.setattr(
        loop,
        "_build_turn_marks",
        lambda *a, **k: ([], list(base_marks), [], None),
    )
    monkeypatch.setattr(loop, "_build_frame_model", lambda: None)
    monkeypatch.setattr(loop, "_two_stage", lambda: True)

    loop._refresh_obs_after_action(image, prev_metrics={"screen": "combat"})

    labels = {m.label for m in loop._aim.marks if (m.label or "").strip()}
    assert "Strike" in labels
    assert "Jaw Worm" in labels
    loop._screen_objects.close()


def test_gate_targeting_blocks_invalid_hand_card(tmp_path) -> None:
    from loop.agent.loop import CollectAgent
    from loop.log.schema import ActionLog
    from loop.memory.screen_objects import ScreenObjectStore
    from loop.mcp.orchestrator import ToolOrchestrator
    from loop.mcp.registry import ToolRegistry
    from loop.os.aim import AimMark

    loop = CollectAgent.__new__(CollectAgent)
    loop.config = type("C", (), {"burst_ms": 400})()
    loop._selected = None
    loop._aim = _guide((1, "Strike"), (2, "Jaw Worm"))
    loop._obs_metrics = {"screen": "combat", "energy": 3}
    store = ScreenObjectStore(tmp_path / "objects.db")
    hand = AimMark(id=1, x=0.5, y=0.86, x0=0.4, y0=0.8, x1=0.6, y1=0.92, label="Strike")
    store.save_frame(run_id="r1", step_id=1, marks=[hand], screen="combat", source="test")
    loop._screen_objects = store
    loop._tool_orchestrator = ToolOrchestrator(ToolRegistry(), max_rounds=1, budget_s=1.0)

    action = ActionLog(mark=1, click_x=0.5, click_y=0.86, duration_ms=400)
    action.name = action.derive_name()
    loop._step_illegal_blocked = False
    loop._pending_exec_error = None
    out = loop._gate_targeting(action, choice="Strike", tag="s01")
    assert loop._pending_exec_error is not None
    assert "invalid-target" in loop._pending_exec_error
    assert loop._step_illegal_blocked
    # A rejected card chord is cleared at the execution boundary.
    assert out.mark is None
    store.close()


def test_card_transaction_dispatches_fresh_target_only_after_lift(monkeypatch) -> None:
    from loop.agent.loop import CollectAgent
    from loop.fixtures.sts2 import Sts2Fixture

    image = Image.new("RGB", (64, 64))
    old = AimGuide.from_marks(
        image,
        [
            AimMark(3, 0.40, 0.85, 0.30, 0.80, 0.50, 0.90, "Strike", object_id="card_1", role="card"),
            AimMark(7, 0.55, 0.63, 0.48, 0.56, 0.62, 0.70, "Cultist", object_id="enemy_1", role="enemy"),
        ],
    )
    fresh = AimGuide.from_marks(
        image,
        [
            # Captured-run shape: the selected card moves to the far-left lane,
            # so old/new boxes do not overlap even though identity is stable.
            AimMark(13, 0.085, 0.78, 0.04, 0.73, 0.13, 0.84, "Strike", object_id="card_1", role="card"),
            AimMark(19, 0.56, 0.64, 0.49, 0.57, 0.63, 0.71, "Cultist", object_id="enemy_1", role="enemy"),
        ],
    )

    class Controller:
        def __init__(self) -> None:
            self.prims: list[object] = []

        def execute(self, prims) -> None:  # type: ignore[no-untyped-def]
            self.prims.extend(prims)

        def release_all(self) -> None:
            return None

    controller = Controller()
    fixture = Sts2Fixture(controller=controller)  # type: ignore[arg-type]
    fixture.settle_ms = 0
    loop = CollectAgent.__new__(CollectAgent)
    loop.fixture = fixture
    loop._aim = old
    loop._obs_metrics = {"screen": "combat"}
    loop._host_stats = {"screen": "combat"}
    loop._last_click = None
    loop._perception_cache = type("Cache", (), {"invalidate_enemies": lambda self: None})()
    loop._force_perceive = False
    monkeypatch.setattr(loop, "_confirm_after_action", lambda **_: image)
    monkeypatch.setattr(loop, "_sample_card_post_click", lambda *_a, **_k: _raw_probe(image))
    monkeypatch.setattr(loop, "_classify_card_probe", lambda *_a, **_k: ("selected", ["raw lift"]))
    monkeypatch.setattr(loop, "_rebind_target", lambda *_a, **_k: fresh.by_id(19))

    result = loop._execute_card_transaction(  # noqa: SLF001
        ActionLog(mark=3, click_x=0.40, click_y=0.85, target=7, click_x2=0.55, click_y2=0.63),
        prev_metrics={"screen": "combat"},
        tag="t1",
    )

    assert len(controller.prims) == 2
    assert [row.phase for row in result.primitives] == ["card", "target"]
    assert result.transaction.selection_state == "confirmed"
    assert result.transaction.target_rebound is True
    assert result.transaction.target_dispatched is True
    assert result.action.target == 19
    assert result.executed == ["mk_3", "mk_19"]


def _textured_target_image(*, x: int, y: int, size: int = 20) -> Image.Image:
    """Build a deterministic sprite-like patch with enough local texture to match."""
    image = Image.new("RGB", (100, 100), (18, 22, 28))
    draw = ImageDraw.Draw(image)
    draw.rectangle((x, y, x + size - 1, y + size - 1), fill=(156, 74, 38))
    draw.rectangle((x + 3, y + 3, x + size - 4, y + size - 4), fill=(38, 112, 164))
    draw.line((x + 1, y + size - 3, x + size - 3, y + 2), fill=(240, 220, 96), width=2)
    draw.line((x + 4, y + 4, x + size - 4, y + size - 4), fill=(224, 238, 244), width=1)
    return image


def test_pixel_target_rebind_never_guesses_shifted_target() -> None:
    """A moved target needs a fresh typed surface, not template/coordinate matching."""
    from loop.agent.loop import CollectAgent

    before_image = _textured_target_image(x=40, y=30)
    fresh_image = _textured_target_image(x=44, y=26)
    before = AimMark(
        id=7, x=.50, y=.40, x0=.40, y0=.30, x1=.60, y1=.50,
        label="unidentified_enemy", object_id="enemy-1", role="enemy",
    )
    agent = object.__new__(CollectAgent)
    agent._transaction_pre_image = before_image
    agent._aim = AimGuide.from_marks(fresh_image, [])

    rebound = agent._rebind_target(before)

    assert rebound is None


def test_pixel_target_rebind_rejects_low_information_template() -> None:
    """A flat old box must not bind to an arbitrary equally flat fresh region."""
    from loop.agent.loop import CollectAgent

    before = AimMark(
        id=7, x=.50, y=.40, x0=.40, y0=.30, x1=.60, y1=.50,
        label="unidentified_enemy", object_id="enemy-1", role="enemy",
    )
    agent = object.__new__(CollectAgent)
    agent._transaction_pre_image = Image.new("RGB", (100, 100), (64, 64, 64))
    agent._aim = AimGuide.from_marks(Image.new("RGB", (100, 100), (64, 64, 64)), [])

    assert agent._rebind_target(before) is None


def test_pixel_target_rebind_rejects_ambiguous_equal_matches() -> None:
    """Two equally good local matches are unsafe; the executor must not guess."""
    from loop.agent.loop import CollectAgent

    before_image = _textured_target_image(x=40, y=30, size=12)
    fresh_image = Image.new("RGB", (100, 100), (18, 22, 28))
    patch = before_image.crop((40, 30, 52, 42))
    fresh_image.paste(patch, (32, 30))
    fresh_image.paste(patch, (48, 30))
    before = AimMark(
        id=7, x=.46, y=.36, x0=.40, y0=.30, x1=.52, y1=.42,
        label="unidentified_enemy", object_id="enemy-1", role="enemy",
    )
    agent = object.__new__(CollectAgent)
    agent._transaction_pre_image = before_image
    agent._aim = AimGuide.from_marks(fresh_image, [])

    assert agent._rebind_target(before) is None


def test_raw_probe_classifies_persistent_hand_motion_as_selected() -> None:
    from loop.agent.loop import CollectAgent

    before = Image.new("RGB", (100, 100), (20, 20, 20))
    ImageDraw.Draw(before).rectangle((30, 75, 50, 98), fill=(210, 60, 30))
    fresh = Image.new("RGB", (100, 100), (20, 20, 20))
    ImageDraw.Draw(fresh).rectangle((15, 72, 29, 98), fill=(210, 60, 30))
    card = AimMark(3, .40, .865, .30, .75, .50, .98, "Strike", role="card")
    probe = SimpleNamespace(pre_frame=before, fresh_frame=fresh, frames=(fresh, fresh))

    state, evidence = CollectAgent.__new__(CollectAgent)._classify_card_probe(card, None, probe)

    assert state == "selected"
    assert any("source movement" in row for row in evidence)


def test_raw_probe_requires_persistent_effect_for_auto_resolution() -> None:
    from loop.agent.loop import CollectAgent

    before = Image.new("RGB", (100, 100), (20, 20, 20))
    ImageDraw.Draw(before).rectangle((30, 75, 50, 98), fill=(210, 60, 30))
    fresh = Image.new("RGB", (100, 100), (20, 20, 20))
    draw = ImageDraw.Draw(fresh)
    draw.rectangle((15, 72, 29, 98), fill=(210, 60, 30))
    draw.rectangle((4, 72, 13, 93), fill=(245, 245, 245))
    card = AimMark(3, .40, .865, .30, .75, .50, .98, "Defend", role="card")
    probe = SimpleNamespace(pre_frame=before, fresh_frame=fresh, frames=(fresh, fresh))

    state, evidence = CollectAgent.__new__(CollectAgent)._classify_card_probe(card, None, probe)

    assert state == "auto_resolved"
    assert "energy_or_hud_changed" in evidence


def test_raw_probe_ignores_enemy_idle_motion_outside_hp_bar() -> None:
    from loop.agent.loop import CollectAgent

    before = Image.new("RGB", (100, 100), (20, 20, 20))
    before_draw = ImageDraw.Draw(before)
    before_draw.rectangle((30, 75, 50, 98), fill=(210, 60, 30))
    before_draw.rectangle((68, 35, 82, 58), fill=(40, 150, 90))
    fresh = Image.new("RGB", (100, 100), (20, 20, 20))
    fresh_draw = ImageDraw.Draw(fresh)
    fresh_draw.rectangle((15, 72, 29, 98), fill=(210, 60, 30))
    fresh_draw.rectangle((70, 34, 84, 57), fill=(40, 150, 90))
    card = AimMark(3, .40, .865, .30, .75, .50, .98, "Bash", role="card")
    target = AimMark(7, .75, .47, .68, .35, .82, .60, "enemy", role="enemy")
    probe = SimpleNamespace(pre_frame=before, fresh_frame=fresh, frames=(fresh, fresh))

    state, _ = CollectAgent.__new__(CollectAgent)._classify_card_probe(card, target, probe)

    assert state == "selected"


def test_card_transaction_refuses_target_without_visual_lift(monkeypatch) -> None:
    from loop.agent.loop import CollectAgent
    from loop.fixtures.sts2 import Sts2Fixture

    image = Image.new("RGB", (64, 64))
    old = AimGuide.from_marks(
        image,
        [
            AimMark(3, 0.40, 0.85, 0.30, 0.80, 0.50, 0.90, "Strike"),
            AimMark(7, 0.55, 0.63, 0.48, 0.56, 0.62, 0.70, "Cultist"),
        ],
    )

    class Controller:
        def __init__(self) -> None:
            self.prims: list[object] = []

        def execute(self, prims) -> None:  # type: ignore[no-untyped-def]
            self.prims.extend(prims)

        def release_all(self) -> None:
            return None

    controller = Controller()
    fixture = Sts2Fixture(controller=controller)  # type: ignore[arg-type]
    fixture.settle_ms = 0
    loop = CollectAgent.__new__(CollectAgent)
    loop.fixture = fixture
    loop._aim = old
    loop._obs_metrics = {"screen": "combat"}
    loop._host_stats = {"screen": "combat"}
    loop._last_click = None
    loop._perception_cache = type("Cache", (), {"invalidate_enemies": lambda self: None})()
    loop._force_perceive = False
    monkeypatch.setattr(loop, "_confirm_after_action", lambda **_: image)
    monkeypatch.setattr(loop, "_sample_card_post_click", lambda *_a, **_k: _raw_probe(image))
    monkeypatch.setattr(loop, "_classify_card_probe", lambda *_a, **_k: ("unconfirmed", []))

    result = loop._execute_card_transaction(  # noqa: SLF001
        ActionLog(mark=3, click_x=0.40, click_y=0.85, target=7, click_x2=0.55, click_y2=0.63),
        prev_metrics={"screen": "combat"},
        tag="t1",
    )

    # An unconfirmed source is observed, never hovered or clicked again: a
    # second source action could deselect a card that was actually selected.
    assert len(controller.prims) == 1
    assert [row.phase for row in result.primitives] == ["card"]
    assert result.transaction.selection_state == "unconfirmed"
    assert result.transaction.source_inspected is False
    assert result.transaction.target_dispatched is False
    assert result.error == "source-unconfirmed:Strike"


def test_defend_auto_resolution_ignores_unnecessary_target(monkeypatch) -> None:
    from loop.agent.loop import CollectAgent
    from loop.fixtures.sts2 import Sts2Fixture

    image = Image.new("RGB", (64, 64))
    old = AimGuide.from_marks(image, [
        AimMark(3, .40, .85, .30, .80, .50, .90, "Defend", role="card"),
        AimMark(7, .55, .30, .48, .22, .62, .38, "Cultist", role="enemy"),
    ])

    class Controller:
        def __init__(self) -> None:
            self.prims: list[object] = []

        def execute(self, prims) -> None:  # type: ignore[no-untyped-def]
            self.prims.extend(prims)

        def release_all(self) -> None:
            return None

    controller = Controller()
    fixture = Sts2Fixture(controller=controller)  # type: ignore[arg-type]
    fixture.settle_ms = 0
    loop = CollectAgent.__new__(CollectAgent)
    loop.fixture, loop._aim = fixture, old
    loop._obs_metrics = {"screen": "combat"}
    loop._host_stats = {"screen": "combat", "turn": "ours", "player": {"energy": 3, "block": 0}}
    loop._last_click = None
    loop._perception_cache = type("Cache", (), {"invalidate_enemies": lambda self: None})()
    loop._force_perceive = False
    monkeypatch.setattr(loop, "_confirm_after_action", lambda **_: image)
    monkeypatch.setattr(loop, "_sample_card_post_click", lambda *_a, **_k: _raw_probe(image))
    monkeypatch.setattr(loop, "_classify_card_probe", lambda *_a, **_k: ("auto_resolved", ["block changed"]))
    result = loop._execute_card_transaction(  # noqa: SLF001
        ActionLog(mark=3, click_x=.40, click_y=.85, target=7, click_x2=.55, click_y2=.30),
        prev_metrics={"screen": "combat"}, tag="t1",
    )

    assert len(controller.prims) == 1
    assert [row.phase for row in result.primitives] == ["card"]
    assert result.transaction.outcome == "auto_resolved"
    assert result.transaction.target_dispatched is False


def test_confirmed_selection_without_target_returns_target_required(monkeypatch) -> None:
    from loop.agent.loop import CollectAgent
    from loop.fixtures.sts2 import Sts2Fixture

    image = Image.new("RGB", (64, 64))
    old = AimGuide.from_marks(image, [
        AimMark(3, .40, .85, .30, .80, .50, .90, "Strike", role="card"),
        AimMark(7, .55, .30, .48, .22, .62, .38, "Cultist", role="enemy"),
    ])
    fresh = AimGuide.from_marks(image, [
        AimMark(13, .40, .76, .29, .69, .51, .83, "Strike", role="card"),
        AimMark(19, .56, .31, .49, .23, .63, .39, "Cultist", role="enemy"),
    ])

    class Controller:
        def __init__(self) -> None:
            self.prims: list[object] = []

        def execute(self, prims) -> None:  # type: ignore[no-untyped-def]
            self.prims.extend(prims)

        def release_all(self) -> None:
            return None

    controller = Controller()
    fixture = Sts2Fixture(controller=controller)  # type: ignore[arg-type]
    fixture.settle_ms = 0
    monkeypatch.setattr(fixture, "execute_card_drag", None)
    loop = CollectAgent.__new__(CollectAgent)
    loop.fixture, loop._aim = fixture, old
    loop._obs_metrics = {"screen": "combat"}
    loop._host_stats = {"screen": "combat", "turn": "ours", "player": {"energy": 3}}
    loop._last_click = None
    loop._perception_cache = type("Cache", (), {"invalidate_enemies": lambda self: None})()
    loop._force_perceive = False
    monkeypatch.setattr(loop, "_confirm_after_action", lambda **_: image)
    monkeypatch.setattr(loop, "_sample_card_post_click", lambda *_a, **_k: _raw_probe(image))
    monkeypatch.setattr(loop, "_classify_card_probe", lambda *_a, **_k: ("selected", ["raw lift"]))

    result = loop._execute_card_transaction(  # noqa: SLF001
        ActionLog(mark=3, click_x=.40, click_y=.85),
        prev_metrics={"screen": "combat"}, tag="t1",
    )

    assert len(controller.prims) == 1
    assert result.transaction.selection_state == "confirmed"
    assert result.transaction.outcome == "target_required"
    assert result.error == "target-required"
    assert loop._selected == "Strike"


def test_card_transaction_rejects_enemy_turn_as_auto_resolution(monkeypatch) -> None:
    from loop.agent.loop import CollectAgent
    from loop.fixtures.sts2 import Sts2Fixture

    image = Image.new("RGB", (64, 64))
    old = AimGuide.from_marks(
        image,
        [
            AimMark(3, 0.40, 0.85, 0.30, 0.80, 0.50, 0.90, "Strike"),
            AimMark(7, 0.55, 0.63, 0.48, 0.56, 0.62, 0.70, "Cultist"),
        ],
    )
    # The card is gone, and the game has moved to the enemy turn.  There is
    # no selected-card lift because the lone requested target was auto-picked.
    fresh = AimGuide.from_marks(
        image,
        [AimMark(19, 0.56, 0.31, 0.49, 0.23, 0.63, 0.39, "Cultist")],
    )

    class Controller:
        def __init__(self) -> None:
            self.prims: list[object] = []

        def execute(self, prims) -> None:  # type: ignore[no-untyped-def]
            self.prims.extend(prims)

        def release_all(self) -> None:
            return None

    fixture = Sts2Fixture(controller=Controller())  # type: ignore[arg-type]
    fixture.settle_ms = 0
    loop = CollectAgent.__new__(CollectAgent)
    loop.fixture = fixture
    loop._aim = old
    loop._obs_metrics = {"screen": "combat"}
    loop._host_stats = {"turn": "ours", "player": {"energy": 3}}
    loop._last_click = None
    loop._perception_cache = type("Cache", (), {"invalidate_enemies": lambda self: None})()
    loop._force_perceive = False
    monkeypatch.setattr(loop, "_confirm_after_action", lambda **_: image)
    monkeypatch.setattr(loop, "_sample_card_post_click", lambda *_a, **_k: _raw_probe(image))
    monkeypatch.setattr(loop, "_classify_card_probe", lambda *_a, **_k: ("unconfirmed", []))
    result = loop._execute_card_transaction(  # noqa: SLF001
        ActionLog(mark=3, click_x=0.40, click_y=0.85, target=7, click_x2=0.55, click_y2=0.63),
        prev_metrics={"screen": "combat"},
        tag="t1",
    )

    assert len(fixture.controller.prims) == 1  # type: ignore[attr-defined]
    assert result.transaction.outcome == "source_unconfirmed"
    assert result.transaction.effect_verified is False
    assert result.transaction.target_dispatched is False


def test_unknown_first_routes_only_action_relevant_uncertainty_to_explore() -> None:
    """Informational uncertainty must not suppress a confirmed card action."""
    from loop.agent.loop import CollectAgent
    from loop.agent.perceive import SceneDescription

    agent = CollectAgent.__new__(CollectAgent)
    confirmed = SceneDescription(
        screen="combat",
        controls=[
            {"label": "Strike", "role": "card", "confidence": "high"},
            {"label": "Jaw Worm", "role": "enemy", "confidence": "high"},
        ],
        uncertain=["flavor text is partially obscured"],
    )
    assert agent._two_stage_reason_route(confirmed)[0] == "act"

    # Anonymous enemy without a safe bbox remains a blocker (unsafe geometry).
    unknown_target = SceneDescription(
        screen="combat",
        controls=[
            {"label": "Strike", "role": "card", "confidence": "high"},
            {
                "label": "unknown_enemy",
                "role": "enemy",
                "confidence": "low",
                "inspectable": "true",
            },
        ],
    )
    route, reason = agent._two_stage_reason_route(unknown_target)
    assert route == "explore"
    assert reason.startswith("epistemic-blocker:")


def test_anonymous_enemy_with_bbox_is_identify_before_act_blocker() -> None:
    """Unknown-first: inspectable anonymous hostile with bbox → Explorer."""
    from loop.agent.loop import CollectAgent, CollectConfig
    from loop.agent.perceive import SceneDescription

    scene = SceneDescription(
        screen="combat",
        controls=[{"label": "Strike", "role": "card", "confidence": "high"}],
        enemies=[
            {
                "label": "unidentified_enemy",
                "role": "enemy",
                "confidence": "high",
                "inspectable": "true",
                "bbox": [690, 580, 810, 710],
            }
        ],
        characters=[
            {
                "label": "player",
                "role": "character",
                "side": "player",
                "confidence": "high",
                "inspectable": "true",
                "bbox": [120, 500, 280, 780],
            }
        ],
    )
    agent = CollectAgent.__new__(CollectAgent)
    agent.config = CollectConfig(identify_before_act=True)
    assert agent._two_stage_reason_route(scene)[0] == "explore"
    facts = agent._entity_route_facts
    assert any(row["rule"] == "identify-before-act" for row in facts)
    assert not any(row.get("player") and row.get("blocker") for row in facts)

    fast = CollectAgent.__new__(CollectAgent)
    fast.config = CollectConfig(identify_before_act=False)
    assert fast._two_stage_epistemic_blockers(scene) == []
    assert fast._two_stage_reason_route(scene)[0] == "act"


def test_unknown_character_midboard_bbox_routes_to_explore() -> None:
    from loop.agent.loop import CollectAgent, CollectConfig
    from loop.agent.perceive import SceneDescription

    for label in ("unidentified_enemy", "unknown_character_1"):
        scene = SceneDescription(
            screen="combat",
            controls=[{"label": "Strike", "role": "card", "confidence": "high"}],
            enemies=[
                {
                    "label": label,
                    "role": "unknown_character",
                    "confidence": "low",
                    "inspectable": "true",
                    "bbox": [690, 580, 810, 710],
                }
            ],
            characters=[
                {
                    "label": "player",
                    "role": "character",
                    "side": "player",
                    "confidence": "high",
                    "bbox": [120, 500, 280, 780],
                }
            ],
        )
        agent = CollectAgent.__new__(CollectAgent)
        agent.config = CollectConfig(identify_before_act=True)
        assert agent._two_stage_reason_route(scene)[0] == "explore", label
        fast = CollectAgent.__new__(CollectAgent)
        fast.config = CollectConfig(identify_before_act=False)
        assert fast._two_stage_reason_route(scene)[0] == "act", label


def test_grounded_anonymous_enemy_still_blocks_until_identified() -> None:
    from loop.agent.loop import CollectAgent, CollectConfig
    from loop.agent.perceive import SceneDescription

    scene = SceneDescription(
        screen="combat",
        controls=[
            {"label": "Strike", "role": "card", "confidence": "high"},
            {
                "label": "unidentified_enemy",
                "role": "enemy",
                "confidence": "low",
                "inspectable": "true",
            },
        ],
        enemies=[
            {
                "label": "unidentified_enemy",
                "role": "unknown_character",
                "confidence": "low",
                "bbox": [690, 580, 810, 710],
            }
        ],
    )
    agent = CollectAgent.__new__(CollectAgent)
    agent.config = CollectConfig(identify_before_act=True)
    assert agent._two_stage_reason_route(scene)[0] == "explore"
    fast = CollectAgent.__new__(CollectAgent)
    fast.config = CollectConfig(identify_before_act=False)
    assert fast._two_stage_epistemic_blockers(scene) == []
    assert fast._two_stage_reason_route(scene)[0] == "act"


def test_player_character_never_blocks_or_enters_enemy_discovery() -> None:
    from loop.agent.entities import EntityTracker, is_enemy_discovery_row
    from loop.agent.loop import CollectAgent
    from loop.agent.perceive import SceneDescription

    player = {
        "label": "player",
        "role": "character",
        "bbox": [100, 500, 250, 800],
    }
    enemy = {
        "label": "unidentified_enemy",
        "role": "enemy",
        "bbox": [690, 580, 810, 710],
    }
    assert not is_enemy_discovery_row(player)
    assert is_enemy_discovery_row(enemy)

    tracker = EntityTracker()
    merged = tracker.update_rows([player, enemy])
    assert len(merged) == 1
    assert merged[0]["role"] in {"enemy", "unknown_character"}
    assert "player" not in {row["label"].lower() for row in merged}

    agent = CollectAgent.__new__(CollectAgent)
    scene = SceneDescription(
        screen="combat",
        characters=[player],
        enemies=[enemy],
        controls=[{"label": "Strike", "role": "card", "confidence": "high"}],
    )
    blockers = agent._two_stage_epistemic_blockers(scene)
    assert "player" not in {item.lower() for item in blockers}
    assert not any(row.get("player") and row.get("blocker") for row in agent._entity_route_facts)
    assert agent._two_stage_reason_route(scene)[0] == "explore"


def test_unknown_first_ignores_unanchored_icon_uncertainty() -> None:
    from loop.agent.loop import CollectAgent
    from loop.agent.perceive import SceneDescription

    agent = CollectAgent.__new__(CollectAgent)
    scene = SceneDescription(
        screen="combat",
        controls=[{"label": "Strike", "role": "card", "confidence": "high"}],
        unresolved=["mk_9 icon"],
        uncertain=["top HUD icon"],
    )
    assert agent._two_stage_epistemic_blockers(scene) == []
    assert agent._two_stage_reason_route(scene)[0] == "act"


def test_v2_end_turn_gate_uses_current_frame_identity_not_inventory_hash(tmp_path) -> None:
    from loop.agent.loop import CollectAgent, CollectConfig
    from loop.memory.screen_objects import ScreenObjectStore
    from loop.mcp.tools.targeting import current_snapshot_identity

    store = ScreenObjectStore(tmp_path / "objects.db")
    end_turn = AimMark(
        5, .89, .82, .84, .77, .96, .88, "End Turn",
        object_id="control:end_turn", role="button",
    )
    store.save_frame(run_id="run-1", step_id=4, marks=[end_turn], screen="combat", source="test")
    frame = current_snapshot_identity(store)
    agent = CollectAgent.__new__(CollectAgent)
    agent.config = CollectConfig(architecture="two_stage")
    agent.fixture = SimpleNamespace(pace="turn", id="sts2")
    agent._obs_metrics = {"screen": "combat"}
    agent._host_stats = {"screen": "combat"}
    agent._screen_objects = store
    agent._last_decision_schema = "v2"
    agent._last_snapshot_id = frame["snapshot_id"]
    agent._last_execution_plan = {
        "decision_schema": "v2",
        "snapshot_id": frame["snapshot_id"],
        "frame_snapshot_id": "deliberately-not-authoritative-for-controls",
        "frame_run_id": "run-1",
        "frame_step_id": 4,
        "action": "end_turn",
        "control_id": "control:end_turn",
        "point": {"x": .89, "y": .82},
    }
    agent._pending_exec_error = None
    action = ActionLog(
        mark=5, click_x=.89, click_y=.82,
        source_role="button", source_object_id="control:end_turn",
    )

    accepted = agent._final_combat_dispatch_gate(action, inspect_requested=False)

    assert accepted.mark == 5
    assert agent._pending_exec_error is None
    store.close()


def test_attaching_unidentified_control_does_not_erase_canonical_control_id(tmp_path) -> None:
    from loop.agent.loop import CollectAgent, CollectConfig
    from loop.memory.screen_objects import ScreenObjectStore

    store = ScreenObjectStore(tmp_path / "objects.db")
    store.save_frame(
        run_id="run-1", step_id=1,
        marks=[AimMark(5, .89, .82, .84, .77, .96, .88, "End Turn", role="button")],
        screen="combat", source="test",
    )
    agent = CollectAgent.__new__(CollectAgent)
    agent.fixture = SimpleNamespace(pace="turn", id="sts2")
    agent.config = CollectConfig(architecture="two_stage")
    agent._obs_metrics = {"screen": "combat"}
    agent._host_stats = {"screen": "combat"}
    agent._screen_objects = store
    action = ActionLog(
        mark=5, click_x=.89, click_y=.82,
        source_role="button", source_object_id="control:end_turn",
    )

    attached = agent._attach_action_identity(action)

    assert attached.source_object_id == "control:end_turn"
    store.close()


def test_v2_end_turn_never_enters_card_transaction_by_hand_geometry() -> None:
    from loop.agent.loop import CollectAgent

    agent = CollectAgent.__new__(CollectAgent)
    agent.fixture = SimpleNamespace(id="sts2")
    agent._last_decision_schema = "v2"
    agent._last_execution_plan = {"action": "end_turn"}
    action = ActionLog(
        mark=5, click_x=.89, click_y=.82,
        source_role="unknown", source_object_id="control:end_turn",
    )

    assert not agent._is_targeted_card_transaction(action)


def test_v2_never_allows_legacy_empty_chord_fill() -> None:
    from loop.agent.loop import CollectAgent

    agent = CollectAgent.__new__(CollectAgent)
    agent._last_decision_schema = "v2"
    assert not agent._allow_legacy_empty_fill()

    agent._last_decision_schema = "v1"
    assert agent._allow_legacy_empty_fill()


def test_immediate_enemy_hp_resolution_beats_card_lift_requirement() -> None:
    """A resolved card must not dispatch a stale target solely because it did not lift."""
    from loop.agent.loop import CollectAgent

    image = Image.new("RGB", (64, 64))
    card = AimMark(3, .4, .85, .3, .8, .5, .9, "Strike")
    agent = CollectAgent.__new__(CollectAgent)
    agent._aim = AimGuide.from_marks(image, [card])
    agent._prev_host_stats = {"turn": "ours", "enemies": [{"hp": 20, "hp_max": 20}]}
    agent._host_stats = {"turn": "ours", "enemies": [{"hp": 14, "hp_max": 20}]}
    assert agent._auto_target_effect_verified(card)


def test_immediate_direct_energy_resolution_beats_card_lift_requirement() -> None:
    from loop.agent.loop import CollectAgent

    image = Image.new("RGB", (64, 64))
    card = AimMark(3, .4, .85, .3, .8, .5, .9, "Strike")
    agent = CollectAgent.__new__(CollectAgent)
    agent._aim = AimGuide.from_marks(image, [card])
    agent._prev_host_stats = {"turn": "ours", "energy": 3}
    agent._host_stats = {"turn": "ours", "energy": 2}
    assert agent._auto_target_effect_verified(card)


def _v2_card_agent(monkeypatch, tmp_path, marks, *, energy=3, architecture="two_stage", hover_ok=True):
    from loop.agent.loop import CollectAgent, CollectConfig
    from loop.fixtures.sts2 import Sts2Fixture
    from loop.memory.screen_objects import ScreenObjectStore
    from loop.mcp.tools.targeting import plan_card_by_id

    image = Image.new("RGB", (64, 64))

    class Controller:
        def __init__(self) -> None:
            self.prims: list[object] = []

        def execute(self, prims) -> None:  # type: ignore[no-untyped-def]
            self.prims.extend(prims)

        def release_all(self) -> None:
            return None

    controller = Controller()
    fixture = Sts2Fixture(controller=controller)  # type: ignore[arg-type]
    fixture.settle_ms = 0
    monkeypatch.setattr(fixture, "grab_image", lambda: image)
    store = ScreenObjectStore(tmp_path / "objects.db")
    stamped = store.save_frame(run_id="r", step_id=1, marks=marks, screen="combat", source="test")
    loop = CollectAgent.__new__(CollectAgent)
    loop.config = CollectConfig(architecture=architecture, arm="no_skill")
    loop.fixture = fixture
    loop._aim = AimGuide.from_marks(image, stamped)
    loop._screen_objects = store
    loop._obs_metrics = {"screen": "combat", "energy": energy}
    loop._host_stats = {"screen": "combat", "energy": energy, "player": {"energy": energy}}
    loop._last_click = None
    loop._perception_cache = type("Cache", (), {"invalidate_enemies": lambda self: None})()
    loop._force_perceive = False
    loop._last_decision_schema = "v2"
    loop._planned_hand_signature = None
    loop._stale_source_plan = None
    loop._blocked_sources = {}
    loop._blocked_hand_signature = None
    loop.session_id = "r"
    loop._step_id = 1
    if hover_ok:
        monkeypatch.setattr(loop, "_source_roi_changed", lambda *_a, **_k: True)
    return loop, store, controller, image, plan_card_by_id


def _play_action(source, target=None) -> ActionLog:
    mark_id = int(getattr(source, "id", None) or getattr(source, "mark_id"))
    target_mark = None if target is None else int(
        getattr(target, "id", None) or getattr(target, "mark_id")
    )
    action = ActionLog(
        mark=mark_id,
        click_x=source.x,
        click_y=source.y,
        target=target_mark,
        click_x2=None if target is None else target.x,
        click_y2=None if target is None else target.y,
        source_object_id=source.object_id,
        source_role="card",
        target_object_id=None if target is None else target.object_id,
        duration_ms=400,
    )
    action.name = action.derive_name()
    return action


def test_card_plan_before_hand_reflow_is_rejected_before_click(monkeypatch, tmp_path) -> None:
    marks = (
        AimMark(id=3, x=0.40, y=0.85, x0=0.30, y0=0.80, x1=0.50, y1=0.90, label="Strike", role="card"),
        AimMark(id=7, x=0.55, y=0.63, x0=0.48, y0=0.56, x1=0.62, y1=0.70, label="Cultist", role="enemy"),
    )
    loop, store, controller, image, plan_card_by_id = _v2_card_agent(monkeypatch, tmp_path, marks)
    planned = plan_card_by_id(store, card_id="card_1", target_id="enemy_1", entity_targets_ok=True)
    assert planned["ok"] is True
    loop._last_execution_plan = planned["transaction"]
    loop._planned_hand_signature = loop._hand_inventory_signature()
    from loop.mcp.tools.targeting import invalidate_hand_snapshot

    invalidate_hand_snapshot(store, reason="hand-reflow")
    monkeypatch.setattr(loop, "_sample_card_post_click", lambda *_a, **_k: _raw_probe(image))
    source = store.by_object_id("card_1")
    target = store.by_object_id("enemy_1")
    assert source is not None and target is not None
    result = loop._execute_card_transaction(
        _play_action(source, target),
        prev_metrics={"screen": "combat"},
        tag="t1",
    )
    assert controller.prims == []
    assert result.transaction.outcome == "stale"
    assert result.error == "stale-snapshot"
    store.close()


def test_old_card_2_intent_rejected_after_preceding_card_leaves(monkeypatch, tmp_path) -> None:
    marks = (
        AimMark(id=1, x=0.30, y=0.85, x0=0.22, y0=0.78, x1=0.38, y1=0.92, label="Strike", role="card"),
        AimMark(id=2, x=0.50, y=0.85, x0=0.42, y0=0.78, x1=0.58, y1=0.92, label="Defend", role="card"),
        AimMark(id=7, x=0.55, y=0.63, x0=0.48, y0=0.56, x1=0.62, y1=0.70, label="Cultist", role="enemy"),
    )
    loop, store, controller, image, plan_card_by_id = _v2_card_agent(monkeypatch, tmp_path, marks)
    old = plan_card_by_id(store, card_id="card_2", entity_targets_ok=True)
    assert old["transaction"]["source"]["label"] == "Defend"
    loop._last_execution_plan = old["transaction"]
    loop._planned_hand_signature = loop._hand_inventory_signature()
    store.save_frame(
        run_id="r",
        step_id=2,
        screen="combat",
        marks=(
            AimMark(id=2, x=0.40, y=0.85, x0=0.32, y0=0.78, x1=0.48, y1=0.92, label="Defend", role="card"),
            AimMark(id=7, x=0.55, y=0.63, x0=0.48, y0=0.56, x1=0.62, y1=0.70, label="Cultist", role="enemy"),
        ),
    )
    result = loop._execute_card_transaction(
        ActionLog(mark=2, click_x=0.50, click_y=0.85, source_object_id="card_2", source_role="card"),
        prev_metrics={"screen": "combat"},
        tag="t1",
    )
    assert controller.prims == []
    assert result.error in {"stale-snapshot", "source-not-found"}
    rebuilt = plan_card_by_id(store, card_id="card_1")
    assert rebuilt["ok"] is True
    assert rebuilt["transaction"]["source"]["label"] == "Defend"
    store.close()


def test_duplicate_label_cards_are_not_silently_substituted(monkeypatch, tmp_path) -> None:
    marks = (
        AimMark(id=1, x=0.28, y=0.85, x0=0.20, y0=0.78, x1=0.36, y1=0.92, label="Strike", role="card"),
        AimMark(id=2, x=0.48, y=0.85, x0=0.40, y0=0.78, x1=0.56, y1=0.92, label="Strike", role="card"),
        AimMark(id=7, x=0.55, y=0.63, x0=0.48, y0=0.56, x1=0.62, y1=0.70, label="Cultist", role="enemy"),
    )
    loop, store, controller, _image, plan_card_by_id = _v2_card_agent(monkeypatch, tmp_path, marks)
    old = plan_card_by_id(store, card_id="card_2")
    loop._last_execution_plan = old["transaction"]
    loop._planned_hand_signature = loop._hand_inventory_signature()
    store.save_frame(
        run_id="r",
        step_id=2,
        screen="combat",
        marks=(
            AimMark(id=2, x=0.48, y=0.85, x0=0.40, y0=0.78, x1=0.56, y1=0.92, label="Strike", role="card"),
            AimMark(id=7, x=0.55, y=0.63, x0=0.48, y0=0.56, x1=0.62, y1=0.70, label="Cultist", role="enemy"),
        ),
    )
    result = loop._execute_card_transaction(
        ActionLog(mark=2, click_x=0.48, click_y=0.85, source_object_id="card_2", source_role="card"),
        prev_metrics={"screen": "combat"},
        tag="t1",
    )
    assert controller.prims == []
    leftover = store.by_object_id("card_1")
    assert leftover is not None and leftover.label == "Strike"
    assert result.error in {"stale-snapshot", "source-not-found"}
    store.close()


def test_source_unconfirmed_blocks_identical_immediate_retry(monkeypatch, tmp_path) -> None:
    marks = (
        AimMark(id=3, x=0.40, y=0.85, x0=0.30, y0=0.80, x1=0.50, y1=0.90, label="Strike", role="card"),
        AimMark(id=7, x=0.55, y=0.63, x0=0.48, y0=0.56, x1=0.62, y1=0.70, label="Cultist", role="enemy"),
    )
    loop, store, controller, image, plan_card_by_id = _v2_card_agent(monkeypatch, tmp_path, marks)
    planned = plan_card_by_id(store, card_id="card_1", target_id="enemy_1", entity_targets_ok=True)
    loop._last_execution_plan = planned["transaction"]
    loop._planned_hand_signature = loop._hand_inventory_signature()
    monkeypatch.setattr(loop, "_sample_card_post_click", lambda *_a, **_k: _raw_probe(image))
    monkeypatch.setattr(loop, "_classify_card_probe", lambda *_a, **_k: ("unconfirmed", []))
    source = store.by_object_id("card_1")
    target = store.by_object_id("enemy_1")
    action = _play_action(source, target)
    first = loop._execute_card_transaction(action, prev_metrics={"screen": "combat"}, tag="t1")
    assert len(controller.prims) == 2
    assert first.transaction.outcome == "source_unconfirmed"
    assert "source-snapshot-invalidated" in first.transaction.verification_evidence
    assert loop._force_perceive is True
    from loop.mcp.tools.targeting import invalidate_hand_snapshot, plan_card_by_id as plan_again

    invalidate_hand_snapshot(store, reason="source-unconfirmed")
    replayed = plan_again(store, card_id="card_1", target_id="enemy_1", entity_targets_ok=True)
    loop._last_execution_plan = replayed["transaction"]
    second = loop._execute_card_transaction(action, prev_metrics={"screen": "combat"}, tag="t1")
    assert len(controller.prims) == 2
    assert second.transaction.outcome == "stale"
    assert "blocked-same-coordinate-retry" in second.transaction.verification_evidence
    store.close()


def test_hover_no_lift_does_not_click(monkeypatch, tmp_path) -> None:
    marks = (
        AimMark(id=3, x=0.40, y=0.85, x0=0.30, y0=0.80, x1=0.50, y1=0.90, label="Strike", role="card"),
        AimMark(id=7, x=0.55, y=0.63, x0=0.48, y0=0.56, x1=0.62, y1=0.70, label="Cultist", role="enemy"),
    )
    loop, store, controller, image, plan_card_by_id = _v2_card_agent(
        monkeypatch, tmp_path, marks, hover_ok=False
    )
    planned = plan_card_by_id(store, card_id="card_1", target_id="enemy_1", entity_targets_ok=True)
    loop._last_execution_plan = planned["transaction"]
    loop._planned_hand_signature = loop._hand_inventory_signature()
    monkeypatch.setattr(loop, "_sample_card_post_click", lambda *_a, **_k: _raw_probe(image))
    source = store.by_object_id("card_1")
    target = store.by_object_id("enemy_1")
    result = loop._execute_card_transaction(
        _play_action(source, target),
        prev_metrics={"screen": "combat"},
        tag="t1",
    )
    assert len(controller.prims) == 1
    assert getattr(controller.prims[0], "op", None) == "move"
    assert result.transaction.outcome == "source_unconfirmed"
    assert str(result.error or "").startswith("source-unconfirmed:hover:")
    store.close()


def test_off_hand_source_mark_rejected_before_click(monkeypatch, tmp_path) -> None:
    marks = (
        AimMark(id=1, x=0.085, y=0.79, x0=0.04, y0=0.73, x1=0.13, y1=0.84, label="Strike", role="card"),
        AimMark(id=7, x=0.55, y=0.63, x0=0.48, y0=0.56, x1=0.62, y1=0.70, label="Cultist", role="enemy"),
    )
    loop, store, controller, _image, plan_card_by_id = _v2_card_agent(monkeypatch, tmp_path, marks)
    planned = plan_card_by_id(store, card_id="card_1", target_id="enemy_1", entity_targets_ok=True)
    loop._last_execution_plan = planned["transaction"]
    loop._planned_hand_signature = loop._hand_inventory_signature()
    source = store.by_object_id("card_1")
    target = store.by_object_id("enemy_1")
    result = loop._execute_card_transaction(
        _play_action(source, target),
        prev_metrics={"screen": "combat"},
        tag="t1",
    )
    assert controller.prims == []
    assert result.error == "source-not-found"
    assert any(item.startswith("off-hand-source-mark:") for item in result.transaction.verification_evidence)
    store.close()


def test_selected_card_resolves_target_from_post_selection_inventory(monkeypatch, tmp_path) -> None:
    marks = (
        AimMark(id=3, x=0.40, y=0.85, x0=0.30, y0=0.80, x1=0.50, y1=0.90, label="Strike", role="card"),
        AimMark(id=7, x=0.55, y=0.63, x0=0.48, y0=0.56, x1=0.62, y1=0.70, label="Cultist", role="enemy"),
    )
    loop, store, controller, image, plan_card_by_id = _v2_card_agent(monkeypatch, tmp_path, marks)
    planned = plan_card_by_id(store, card_id="card_1", target_id="enemy_1", entity_targets_ok=True)
    loop._last_execution_plan = planned["transaction"]
    loop._planned_hand_signature = loop._hand_inventory_signature()
    monkeypatch.setattr(loop, "_sample_card_post_click", lambda *_a, **_k: _raw_probe(image))
    monkeypatch.setattr(loop, "_classify_card_probe", lambda *_a, **_k: ("selected", ["raw lift"]))

    def refresh(_after, prev_metrics=None):  # type: ignore[no-untyped-def]
        del prev_metrics
        stamped = store.save_frame(
            run_id="r",
            step_id=1,
            screen="combat",
            marks=(
                AimMark(id=13, x=0.08, y=0.78, x0=0.04, y0=0.73, x1=0.13, y1=0.84, label="Strike", object_id="card_1", role="card"),
                AimMark(id=21, x=0.70, y=0.63, x0=0.64, y0=0.56, x1=0.76, y1=0.70, label="Cultist", object_id="enemy_1", role="enemy"),
            ),
        )
        loop._aim = AimGuide.from_marks(image, stamped)
        return {"screen": "combat"}

    monkeypatch.setattr(loop, "_refresh_obs_after_action", refresh)
    reacquire_calls: list[str] = []
    monkeypatch.setattr(
        loop,
        "_reacquire_current_target_surface",
        lambda *, target_id: reacquire_calls.append(target_id) is None,
    )
    monkeypatch.setattr(
        loop,
        "_rebind_target",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("stale pre-click rebind")),
    )
    source = store.by_object_id("card_1")
    target = store.by_object_id("enemy_1")
    result = loop._execute_card_transaction(
        _play_action(source, target),
        prev_metrics={"screen": "combat"},
        tag="t1",
    )
    assert len(controller.prims) == 3
    assert [row.phase for row in result.primitives] == ["card", "card", "target"]
    assert result.primitives[2].x == 0.70
    assert result.action.target == 21
    assert reacquire_calls == ["enemy_1"]
    assert "post-selection-target:enemy_1" in result.transaction.verification_evidence
    store.close()


def test_target_reacquisition_publishes_fresh_surface_before_dispatch(monkeypatch, tmp_path) -> None:
    from loop.os.frame_model import build_action_surface

    marks = (
        AimMark(id=3, x=0.40, y=0.85, x0=0.30, y0=0.80, x1=0.50, y1=0.90, label="Strike", role="card"),
        AimMark(id=7, x=0.55, y=0.63, x0=0.48, y0=0.56, x1=0.62, y1=0.70, label="Cultist", role="enemy"),
    )
    loop, store, _controller, image, _plan_card_by_id = _v2_card_agent(monkeypatch, tmp_path, marks)
    loop._last_obs_image = image
    observed_focus: list[str] = []

    def fresh_rescan(*, focus: str) -> float:
        observed_focus.append(focus)
        fresh_marks = (
            AimMark(id=31, x=0.11, y=0.79, x0=0.06, y0=0.72, x1=0.16, y1=0.86, label="Strike", role="card"),
            AimMark(id=47, x=0.22, y=0.19, x0=0.16, y0=0.13, x1=0.28, y1=0.25, label="new target", role="enemy"),
        )
        surface = build_action_surface(fresh_marks, run_id="r", step_id=1, screen="combat", activity="combat")
        stamped = store.save_frame(run_id="r", step_id=1, marks=fresh_marks, screen="combat", context=surface.context, surface=surface)
        loop._aim = AimGuide.from_marks(image, stamped)
        loop._action_surface = surface
        loop._scene_desc = SimpleNamespace(
            enemies=[{"entity_id": "unknown_character_1", "role": "unknown_character", "bbox": [160, 130, 280, 250]}],
            characters=[], controls=[],
        )
        return 1.0

    monkeypatch.setattr(loop, "_rescan_scene", fresh_rescan)
    assert loop._reacquire_current_target_surface(target_id="enemy_1") is True
    assert observed_focus == ["reacquire current target enemy_1"]
    fresh = store.by_object_id("enemy_1")
    assert fresh is not None and fresh.mark_id == 47
    store.close()


def test_missing_stable_target_never_clicks_stale_target(monkeypatch, tmp_path) -> None:
    marks = (
        AimMark(id=3, x=0.40, y=0.85, x0=0.30, y0=0.80, x1=0.50, y1=0.90, label="Strike", role="card"),
        AimMark(id=7, x=0.55, y=0.63, x0=0.48, y0=0.56, x1=0.62, y1=0.70, label="Cultist", role="enemy"),
    )
    loop, store, controller, image, plan_card_by_id = _v2_card_agent(monkeypatch, tmp_path, marks)
    planned = plan_card_by_id(store, card_id="card_1", target_id="enemy_1", entity_targets_ok=True)
    loop._last_execution_plan = planned["transaction"]
    loop._planned_hand_signature = loop._hand_inventory_signature()
    monkeypatch.setattr(loop, "_sample_card_post_click", lambda *_a, **_k: _raw_probe(image))
    monkeypatch.setattr(loop, "_classify_card_probe", lambda *_a, **_k: ("selected", ["raw lift"]))

    def refresh(_after, prev_metrics=None):  # type: ignore[no-untyped-def]
        del prev_metrics
        stamped = store.save_frame(
            run_id="r",
            step_id=1,
            screen="combat",
            marks=(
                AimMark(id=13, x=0.08, y=0.78, x0=0.04, y0=0.73, x1=0.13, y1=0.84, label="Strike", object_id="card_1", role="card"),
            ),
        )
        loop._aim = AimGuide.from_marks(image, stamped)
        return {"screen": "combat"}

    monkeypatch.setattr(loop, "_refresh_obs_after_action", refresh)
    stale = AimMark(id=99, x=0.55, y=0.63, x0=0.48, y0=0.56, x1=0.62, y1=0.70, label="Cultist", object_id="enemy_1", role="enemy")
    monkeypatch.setattr(loop, "_rebind_target", lambda *_a, **_k: stale)
    source = store.by_object_id("card_1")
    target = store.by_object_id("enemy_1")
    result = loop._execute_card_transaction(
        _play_action(source, target),
        prev_metrics={"screen": "combat"},
        tag="t1",
    )
    assert [row.phase for row in result.primitives] == ["card", "card"]
    assert result.transaction.outcome == "target_not_found"
    assert result.transaction.target_dispatched is False
    assert "missing-stable-target" in result.transaction.verification_evidence
    store.close()


def test_hp_only_post_selection_fails_closed(monkeypatch, tmp_path) -> None:
    marks = (
        AimMark(id=3, x=0.40, y=0.85, x0=0.30, y0=0.80, x1=0.50, y1=0.90, label="Strike", role="card"),
        AimMark(id=7, x=0.75, y=0.64, x0=0.70, y0=0.58, x1=0.80, y1=0.70, label="unidentified_enemy", role="enemy"),
    )
    loop, store, controller, image, plan_card_by_id = _v2_card_agent(monkeypatch, tmp_path, marks)
    planned = plan_card_by_id(store, card_id="card_1", target_id="enemy_1", entity_targets_ok=True)
    loop._last_execution_plan = planned["transaction"]
    loop._planned_hand_signature = loop._hand_inventory_signature()
    monkeypatch.setattr(loop, "_sample_card_post_click", lambda *_a, **_k: _raw_probe(image))
    monkeypatch.setattr(loop, "_classify_card_probe", lambda *_a, **_k: ("selected", ["raw lift"]))

    def refresh(_after, prev_metrics=None):  # type: ignore[no-untyped-def]
        del prev_metrics
        stamped = store.save_frame(
            run_id="r",
            step_id=1,
            screen="combat",
            marks=(
                AimMark(id=13, x=0.08, y=0.78, x0=0.04, y0=0.73, x1=0.13, y1=0.84, label="Strike", object_id="card_1", role="card"),
                AimMark(id=11, x=0.75, y=0.35, x0=0.70, y0=0.32, x1=0.80, y1=0.38, label="unidentified_enemy", role="unknown"),
            ),
        )
        loop._aim = AimGuide.from_marks(image, stamped)
        return {"screen": "combat"}

    monkeypatch.setattr(loop, "_refresh_obs_after_action", refresh)
    monkeypatch.setattr(loop, "_rebind_target", lambda *_a, **_k: None)
    source = store.by_object_id("card_1")
    target = store.by_object_id("enemy_1")
    result = loop._execute_card_transaction(
        _play_action(source, target),
        prev_metrics={"screen": "combat"},
        tag="t1",
    )
    assert [row.phase for row in result.primitives] == ["card", "card"]
    assert result.transaction.outcome == "target_not_found"
    assert result.transaction.target_dispatched is False
    store.close()


def test_planned_hp_plate_coords_are_not_clicked(monkeypatch, tmp_path) -> None:
    marks = (
        AimMark(id=3, x=0.40, y=0.85, x0=0.30, y0=0.80, x1=0.50, y1=0.90, label="Strike", role="card"),
        AimMark(id=7, x=0.75, y=0.64, x0=0.70, y0=0.58, x1=0.80, y1=0.70, label="unidentified_enemy", role="enemy"),
    )
    loop, store, controller, image, plan_card_by_id = _v2_card_agent(monkeypatch, tmp_path, marks)
    planned = plan_card_by_id(store, card_id="card_1", target_id="enemy_1", entity_targets_ok=True)
    candidate = dict(planned["transaction"]["target_candidate"])
    candidate["point"] = {"x": 0.75, "y": 0.35}
    candidate["bounds"] = {"x0": 0.70, "y0": 0.32, "x1": 0.80, "y1": 0.38}
    planned["transaction"]["target_candidate"] = candidate
    loop._last_execution_plan = planned["transaction"]
    loop._planned_hand_signature = loop._hand_inventory_signature()
    monkeypatch.setattr(loop, "_sample_card_post_click", lambda *_a, **_k: _raw_probe(image))
    monkeypatch.setattr(loop, "_classify_card_probe", lambda *_a, **_k: ("selected", ["raw lift"]))

    def refresh(_after, prev_metrics=None):  # type: ignore[no-untyped-def]
        del prev_metrics
        stamped = store.save_frame(
            run_id="r",
            step_id=1,
            screen="combat",
            marks=(
                AimMark(id=13, x=0.08, y=0.78, x0=0.04, y0=0.73, x1=0.13, y1=0.84, label="Strike", object_id="card_1", role="card"),
            ),
        )
        loop._aim = AimGuide.from_marks(image, stamped)
        return {"screen": "combat"}

    monkeypatch.setattr(loop, "_refresh_obs_after_action", refresh)
    monkeypatch.setattr(loop, "_rebind_target", lambda *_a, **_k: None)
    source = store.by_object_id("card_1")
    hp = AimMark(id=7, x=0.75, y=0.35, x0=0.70, y0=0.32, x1=0.80, y1=0.38, label="unidentified_enemy", object_id="enemy_1", role="enemy")
    result = loop._execute_card_transaction(
        _play_action(source, hp),
        prev_metrics={"screen": "combat"},
        tag="t1",
    )
    assert [row.phase for row in result.primitives] == ["card", "card"]
    assert result.transaction.outcome == "target_not_found"
    store.close()


def test_stamp_reason_controls_binds_unknown_character_to_store_enemy(monkeypatch, tmp_path) -> None:
    from loop.agent.loop import CollectAgent, CollectConfig
    from loop.fixtures.sts2 import Sts2Fixture
    from loop.memory.screen_objects import ScreenObjectStore

    marks = (
        AimMark(id=1, x=0.50, y=0.80, x0=0.40, y0=0.70, x1=0.60, y1=0.90, label="Strike", role="card"),
        AimMark(id=2, x=0.75, y=0.63, x0=0.68, y0=0.56, x1=0.82, y1=0.72, label="unidentified_enemy", role="enemy"),
    )
    store = ScreenObjectStore(tmp_path / "stamp.db")
    store.save_frame(run_id="r", step_id=1, marks=marks, screen="combat")
    loop = CollectAgent.__new__(CollectAgent)
    loop.config = CollectConfig(architecture="two_stage", arm="no_skill")
    loop.fixture = Sts2Fixture(controller=type("C", (), {"execute": lambda *_a, **_k: [], "release_all": lambda: None})())  # type: ignore[arg-type]
    loop._screen_objects = store
    loop._obs_metrics = {"screen": "combat"}
    loop._host_stats = {"screen": "combat"}
    loop.session_id = "r"
    loop._step_id = 1
    controls = [{
        "label": "unknown_character_1",
        "role": "unknown_character",
        "confidence": "high",
        "entity_id": "unknown_character_1",
        "bbox": [680, 560, 820, 720],
    }]
    stamped = loop._stamp_reason_control_ids(controls)
    enemy_rows = [row for row in stamped if str(row.get("role") or "") == "enemy"]
    assert enemy_rows
    assert enemy_rows[0]["entity_id"] == "enemy_1"
    from loop.mcp.tools.targeting import plan_card_by_id

    plan = plan_card_by_id(store, card_id="card_1", target_id="unknown_character_1", entity_targets_ok=True)
    assert plan["ok"] is True
    assert plan["transaction"]["target_candidate"]["object_id"] == "enemy_1"
    store.close()


def test_rebound_matches_frozen_bounds_when_id_reminted(monkeypatch, tmp_path) -> None:
    marks = (
        AimMark(id=3, x=0.40, y=0.85, x0=0.30, y0=0.80, x1=0.50, y1=0.90, label="Strike", role="card"),
        AimMark(id=7, x=0.55, y=0.62, x0=0.48, y0=0.55, x1=0.62, y1=0.69, label="Slime", role="enemy"),
        AimMark(id=8, x=0.72, y=0.62, x0=0.65, y0=0.55, x1=0.79, y1=0.69, label="Slime", role="enemy"),
        AimMark(id=9, x=0.90, y=0.64, x0=0.84, y0=0.57, x1=0.96, y1=0.71, label="Slime", role="enemy"),
    )
    loop, store, controller, image, plan_card_by_id = _v2_card_agent(monkeypatch, tmp_path, marks)
    planned = plan_card_by_id(store, card_id="card_1", target_id="enemy_3", entity_targets_ok=True)
    assert planned["ok"] is True
    loop._last_execution_plan = planned["transaction"]
    loop._planned_hand_signature = loop._hand_inventory_signature()
    loop._last_obs_image = image
    monkeypatch.setattr(loop, "_sample_card_post_click", lambda *_a, **_k: _raw_probe(image))
    monkeypatch.setattr(loop, "_classify_card_probe", lambda *_a, **_k: ("selected", ["raw lift"]))

    def refresh(_after, prev_metrics=None):  # type: ignore[no-untyped-def]
        del prev_metrics
        stamped = store.save_frame(
            run_id="r",
            step_id=1,
            screen="combat",
            marks=(
                AimMark(id=13, x=0.40, y=0.85, x0=0.30, y0=0.80, x1=0.50, y1=0.90, label="Strike", object_id="card_1", role="card"),
                AimMark(id=21, x=0.55, y=0.62, x0=0.48, y0=0.55, x1=0.62, y1=0.69, label="Slime", object_id="enemy_1", role="enemy"),
                AimMark(id=22, x=0.90, y=0.64, x0=0.84, y0=0.57, x1=0.96, y1=0.71, label="End Turn", object_id="", role="unknown"),
            ),
        )
        loop._aim = AimGuide.from_marks(image, stamped)
        return {"screen": "combat"}

    monkeypatch.setattr(loop, "_refresh_obs_after_action", refresh)
    monkeypatch.setattr(loop, "_reacquire_current_target_surface", lambda *, target_id: True)
    monkeypatch.setattr(
        loop,
        "_rebind_target",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("stale pre-click rebind")),
    )
    source = store.by_object_id("card_1")
    target = store.by_object_id("enemy_3")
    result = loop._execute_card_transaction(
        _play_action(source, target),
        prev_metrics={"screen": "combat"},
        tag="t1",
    )
    assert result.transaction.outcome != "target_not_found"
    assert result.transaction.target_dispatched is True
    assert [row.phase for row in result.primitives][-1] == "target"
    assert abs(float(result.primitives[-1].x) - 0.90) < 0.03
    store.close()


def test_aim_label_prefers_execution_plan_source(monkeypatch, tmp_path) -> None:
    marks = (
        AimMark(id=3, x=0.40, y=0.85, x0=0.30, y0=0.80, x1=0.50, y1=0.90, label="Strike", role="card"),
        AimMark(id=7, x=0.55, y=0.63, x0=0.48, y0=0.56, x1=0.62, y1=0.70, label="Cultist", role="enemy"),
    )
    loop, store, _controller, image, plan_card_by_id = _v2_card_agent(monkeypatch, tmp_path, marks)
    planned = plan_card_by_id(store, card_id="card_1", target_id="enemy_1", entity_targets_ok=True)
    loop._last_execution_plan = planned["transaction"]
    loop._aim = AimGuide.from_marks(
        image,
        (
            AimMark(id=3, x=0.40, y=0.85, x0=0.30, y0=0.80, x1=0.50, y1=0.90, label="Back", role="button"),
            AimMark(id=7, x=0.55, y=0.63, x0=0.48, y0=0.56, x1=0.62, y1=0.70, label="Cultist", role="enemy"),
        ),
    )
    source = store.by_object_id("card_1")
    assert loop._aim_label(_play_action(source)) == "Strike"
    store.close()
