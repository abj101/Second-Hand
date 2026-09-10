from __future__ import annotations

from types import SimpleNamespace

from PIL import Image

from loop.agent.loop import CollectAgent
from loop.fixtures.base import FixtureBase
from loop.fixtures.sts2 import Sts2Fixture
from loop.log.schema import ActionLog
from loop.os.pointer_hygiene import (
    IN_CLIENT_FALLBACK,
    PointerHygieneState,
    hover_occlusion_from_scene,
    neutral_park_xy,
    should_park_after_pointer,
)
from loop.os.primitives import MousePrimitive


def test_neutral_park_xy_prefers_letterbox_margins() -> None:
    assert neutral_park_xy((0.12, 0.0, 1.0, 1.0))[0] < 0.12
    assert abs(neutral_park_xy((0.12, 0.0, 1.0, 1.0))[1] - 0.50) < 1e-9
    right = neutral_park_xy((0.0, 0.0, 0.88, 1.0))
    assert right[0] > 0.88
    top = neutral_park_xy((0.0, 0.10, 1.0, 1.0))
    assert top[1] < 0.10
    bottom = neutral_park_xy((0.0, 0.0, 1.0, 0.80))
    assert bottom[1] > 0.80
    assert neutral_park_xy((0.0, 0.0, 1.0, 1.0)) == IN_CLIENT_FALLBACK
    assert neutral_park_xy(None) == IN_CLIENT_FALLBACK


def test_should_park_after_pointer_exemptions() -> None:
    assert should_park_after_pointer(hover=False, has_click=True)
    assert not should_park_after_pointer(hover=True, has_click=True)
    assert not should_park_after_pointer(hover=False, has_click=True, mid_transaction=True)
    assert not should_park_after_pointer(hover=False, has_click=True, drag=True)
    assert not should_park_after_pointer(hover=False, has_click=True, combat_selection_held=True)
    assert should_park_after_pointer(
        hover=False, has_click=True, combat_selection_held=True, park_after=True,
    )
    assert not should_park_after_pointer(hover=False, has_click=True, park_after=False)
    assert not should_park_after_pointer(hover=False, has_click=False)


def test_hover_occlusion_from_tooltip_glossary() -> None:
    scene = SimpleNamespace(
        overlays_active=[],
        visible_text=[
            "Choose a Card",
            "Vulnerable",
            "Vulnerable creatures take 50% more damage from Attacks.",
            "Exhaust",
            "Removed until the end of combat.",
        ],
    )
    assert hover_occlusion_from_scene(scene, last_click=(0.50, 0.59))
    clean = SimpleNamespace(overlays_active=[], visible_text=["Choose a Card", "Skip"])
    assert not hover_occlusion_from_scene(clean, last_click=(0.50, 0.59))


class _FakeController:
    def __init__(self) -> None:
        self.prims: list[object] = []

    def execute(self, prims) -> None:
        self.prims.extend(prims)

    def release_all(self) -> None:
        return None


def _agent_with_mouse(monkeypatch, *, screen: str = "reward") -> CollectAgent:
    monkeypatch.setattr("loop.agent.loop.time.sleep", lambda _s: None)
    fx = Sts2Fixture(controller=_FakeController())  # type: ignore[arg-type]
    fx._content_box = (0.0, 0.0, 1.0, 1.0)
    fx.settle_ms = 0
    agent = CollectAgent.__new__(CollectAgent)
    agent.fixture = fx
    agent._pointer_hygiene = PointerHygieneState()
    agent._host_stats = {"screen": screen, "activity": screen, "combat_active": screen == "combat"}
    agent._obs_metrics = dict(agent._host_stats)
    agent._last_execution_plan = {}
    agent._scene_desc = None
    agent._last_click = None
    agent._last_obs_image = Image.new("RGB", (32, 32), (10, 10, 10))
    return agent


def test_collect_agent_parks_after_reward_click(monkeypatch) -> None:
    agent = _agent_with_mouse(monkeypatch, screen="reward")
    action = ActionLog(click_x=0.50, click_y=0.59, source_role="button")
    assert agent._maybe_park_after_action(action)
    moves = [p for p in agent.fixture.controller.prims if isinstance(p, MousePrimitive) and p.op == "move"]
    assert moves
    assert (moves[-1].x, moves[-1].y) == IN_CLIENT_FALLBACK
    assert agent._hygiene().parked
    assert agent._hygiene().last_trigger == "post-action"


def test_collect_agent_skips_park_on_intentional_hover(monkeypatch) -> None:
    agent = _agent_with_mouse(monkeypatch, screen="reward")
    action = ActionLog(click_x=0.50, click_y=0.59, hover=True, source_role="button")
    assert not agent._maybe_park_after_action(action)
    assert agent.fixture.controller.prims == []
    assert agent._hygiene().hover_intentional
    assert not agent._hygiene().parked


def test_collect_agent_skips_park_on_combat_card_until_commit(monkeypatch) -> None:
    agent = _agent_with_mouse(monkeypatch, screen="combat")
    action = ActionLog(click_x=0.40, click_y=0.85, source_role="card")
    assert not agent._maybe_park_after_action(action)
    assert agent._hygiene().selection_held
    assert agent._maybe_park_after_action(action, park_after=True)
    assert agent._hygiene().parked
    assert not agent._hygiene().selection_held


def test_collect_agent_skips_park_on_drag(monkeypatch) -> None:
    agent = _agent_with_mouse(monkeypatch, screen="combat")
    action = ActionLog(click_x=0.40, click_y=0.85, source_role="card")
    assert not agent._maybe_park_after_action(action, drag=True)
    assert agent.fixture.controller.prims == []


def test_occlusion_recapture_is_one_shot(monkeypatch) -> None:
    agent = _agent_with_mouse(monkeypatch, screen="reward")
    agent._hygiene().parked = False
    agent._hygiene().last_xy = (0.50, 0.59)
    agent._hygiene().last_action_xy = (0.50, 0.59)
    calls = {"n": 0}

    def fake_rescan(*, focus: str = "") -> float:
        calls["n"] += 1
        agent._hygiene().parked = True
        return 1.0

    monkeypatch.setattr(agent, "_rescan_scene", fake_rescan)
    scene = SimpleNamespace(
        overlays_active=[],
        visible_text=["Vulnerable creatures take 50% more damage from Attacks."],
    )
    assert agent._maybe_recapture_occluded_scene(scene)
    assert calls["n"] == 1
    assert agent._hygiene().recapture_used == 1
    assert agent._hygiene().last_trigger == "occlusion-recapture"
    assert not agent._maybe_recapture_occluded_scene(scene)
    assert calls["n"] == 1


def test_pre_observe_parks_leftover_pointer(monkeypatch) -> None:
    agent = _agent_with_mouse(monkeypatch, screen="reward")
    agent._hygiene().parked = False
    agent._hygiene().last_action_xy = (0.50, 0.59)
    dirty = Image.new("RGB", (32, 32), (20, 20, 20))
    clean = Image.new("RGB", (32, 32), (40, 40, 40))
    agent.fixture.grab_image = lambda: clean  # type: ignore[method-assign]
    agent.fixture.observe_image = lambda image: image  # type: ignore[method-assign]
    agent.fixture.commit_observation = lambda _image: None  # type: ignore[attr-defined]
    out = agent._ensure_clean_observation(dirty, trigger="pre-observe")
    assert out is clean
    assert agent._hygiene().parked
    assert agent._hygiene().last_trigger == "pre-observe"


def test_fixture_can_override_park_geometry(monkeypatch) -> None:
    agent = _agent_with_mouse(monkeypatch, screen="map")
    agent.fixture.neutral_park_xy = lambda: (0.02, 0.50)  # type: ignore[method-assign]
    assert agent._neutral_park_xy() == (0.02, 0.50)
    action = ActionLog(click_x=0.40, click_y=0.40, source_role="button")
    assert agent._maybe_park_after_action(action)
    moves = [p for p in agent.fixture.controller.prims if isinstance(p, MousePrimitive) and p.op == "move"]
    assert (moves[-1].x, moves[-1].y) == (0.02, 0.50)


def test_fixture_base_default_park_uses_content_box() -> None:
    fx = FixtureBase.__new__(FixtureBase)
    fx._click_box = (0.15, 0.0, 1.0, 1.0)
    xy = FixtureBase.neutral_park_xy(fx)
    assert xy[0] < 0.15
