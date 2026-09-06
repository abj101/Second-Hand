from __future__ import annotations

from loop.agent.entities import EntityTracker, TrackedEntity
from loop.agent.loop import CollectAgent
from loop.agent.perceive import (
    SceneDescription,
    combat_materially_changed,
    enemy_signature,
    scene_from_ocr,
    should_run_llm1_perceive,
)
from loop.os.frame_model import FrameModel, FrameObject


def test_non_combat_never_runs_llm1() -> None:
    for screen in ("map", "title", "tutorial", "select", "pause"):
        run, reason = should_run_llm1_perceive(screen=screen, prev_screen="combat")
        assert not run
        assert reason.startswith("ocr-only:")


def test_entering_combat_runs_llm1() -> None:
    run, reason = should_run_llm1_perceive(
        screen="combat",
        prev_screen="map",
        host_stats={"screen": "combat", "enemies": [{"hp": 44, "hp_max": 44}]},
    )
    assert run
    assert reason == "entering-combat"


def test_stable_combat_skips_llm1_with_cache() -> None:
    cached = SceneDescription(
        screen="combat",
        combat={"active": True},
        enemies=[{"label": "Jaw Worm", "role": "enemy", "confidence": "high"}],
        controls=[
            {"label": "Strike", "role": "card", "confidence": "high"},
            {"label": "Jaw Worm", "role": "enemy", "confidence": "high"},
        ],
    )
    host = {
        "screen": "combat",
        "enemies": [{"hp": 44, "hp_max": 44, "label": "Jaw Worm"}],
    }
    run, reason = should_run_llm1_perceive(
        screen="combat",
        prev_screen="combat",
        host_stats=host,
        prev_host_stats=host,
        cached_scene=cached,
    )
    assert not run
    assert reason == "combat-stable"


def test_enemy_hp_change_runs_llm1() -> None:
    cached = SceneDescription(
        screen="combat",
        enemies=[{"label": "Jaw Worm", "role": "enemy", "confidence": "high"}],
    )
    prev_host = {"screen": "combat", "enemies": [{"hp": 44, "hp_max": 44}]}
    host = {"screen": "combat", "enemies": [{"hp": 30, "hp_max": 44}]}
    assert combat_materially_changed(prev_host, host)
    run, reason = should_run_llm1_perceive(
        screen="combat",
        prev_screen="combat",
        host_stats=host,
        prev_host_stats=prev_host,
        cached_scene=cached,
    )
    assert run
    assert reason == "combat-material-change"


def test_unidentified_enemy_skips_llm1_on_named_cache() -> None:
    cached = SceneDescription(
        screen="combat",
        enemies=[
            {
                "label": "unidentified_enemy",
                "role": "enemy",
                "confidence": "low",
                "inspectable": "true",
            }
        ],
    )
    host = {"screen": "combat", "enemies": [{"hp": 44, "hp_max": 44}]}
    run, reason = should_run_llm1_perceive(
        screen="combat",
        prev_screen="combat",
        host_stats=host,
        prev_host_stats=host,
        cached_scene=cached,
    )
    assert not run
    assert reason == "combat-stable"


def test_tracker_inspectable_skips_llm1_on_named_cache() -> None:
    tracker = EntityTracker(
        entities=[
            TrackedEntity(
                entity_id="unknown_character_1",
                label="unknown_character_1",
                role="unknown_character",
                cx=0.7,
                cy=0.45,
                inspectable=True,
                identified=False,
            )
        ]
    )
    cached = SceneDescription(
        screen="combat",
        enemies=[{"label": "Jaw Worm", "role": "enemy", "confidence": "high"}],
    )
    host = {"screen": "combat", "enemies": [{"hp": 44, "hp_max": 44, "label": "Jaw Worm"}]}
    run, reason = should_run_llm1_perceive(
        screen="combat",
        prev_screen="combat",
        host_stats=host,
        prev_host_stats=host,
        entity_tracker=tracker,
        cached_scene=cached,
    )
    assert not run
    assert reason == "combat-stable"


def test_scene_from_ocr_map_uses_clickable_labels() -> None:
    desc = scene_from_ocr(
        hud={"screen": "map", "ocr": "Continue Singleplayer"},
        host_stats={"screen": "map"},
        clickable_labels=["Continue", "Singleplayer"],
    )
    assert desc.screen == "map"
    assert desc.fallback
    assert desc.schema_valid
    assert "Continue" in desc.control_labels()


def test_scene_from_ocr_combat_reuses_cached_enemies() -> None:
    cached = SceneDescription(
        screen="combat",
        combat={"active": True},
        enemies=[{"label": "Jaw Worm", "role": "enemy", "confidence": "high"}],
        controls=[
            {"label": "Jaw Worm", "role": "enemy", "confidence": "high"},
            {"label": "Strike", "role": "card", "confidence": "high"},
        ],
    )
    frame = FrameModel(
        objects=[
            FrameObject(id=1, label="Defend", kind="interactive", role="card"),
            FrameObject(id=2, label="End Turn", kind="interactive", role="button"),
        ],
        layout="hand_and_board",
        screen_hint="combat",
    )
    desc = scene_from_ocr(
        hud={"screen": "combat", "ocr": "End Turn Defend"},
        host_stats={
            "screen": "combat",
            "enemies": [{"hp": 44, "hp_max": 44, "label": "Jaw Worm"}],
        },
        frame_model=frame,
        cached=cached,
    )
    labels = desc.control_labels()
    assert "Jaw Worm" in labels
    assert "Defend" in labels
    assert "End Turn" in labels
    assert "Strike" in labels
    assert desc.hand_cards
    assert desc.hand_cards[0]["label"] == "Strike"


def test_enemy_signature_detects_roster_change() -> None:
    prev = {"enemies": [{"hp": 44, "hp_max": 44}]}
    same = {"enemies": [{"hp": 44, "hp_max": 44}]}
    fewer = {"enemies": []}
    assert enemy_signature(prev) == enemy_signature(same)
    assert enemy_signature(prev) != enemy_signature(fewer)


def test_record_perception_telemetry_scene_cache_reuse() -> None:
    agent = object.__new__(CollectAgent)
    agent._cached_perceive_scene = SceneDescription(screen="combat")
    agent._cached_perceive_scene_step = 4
    agent._reset_turn_telemetry()
    agent._record_perception_telemetry(
        ran_llm1=False,
        gate_reason="combat-stable",
        forced=False,
    )
    assert agent._turn_llm1_called is False
    assert agent._turn_perception_source == "scene_cache"
    assert agent._turn_llm1_reused_from_step == 4


def test_record_perception_telemetry_fresh_and_forced_llm1() -> None:
    agent = object.__new__(CollectAgent)
    agent._cached_perceive_scene = None
    agent._cached_perceive_scene_step = None
    agent._reset_turn_telemetry()
    agent._record_perception_telemetry(
        ran_llm1=True,
        gate_reason="entering-combat",
        forced=False,
    )
    assert agent._turn_llm1_called is True
    assert agent._turn_perception_source == "llm1"
    assert agent._turn_llm1_reused_from_step is None

    agent._record_perception_telemetry(
        ran_llm1=True,
        gate_reason="combat-material-change",
        forced=True,
    )
    assert agent._turn_perception_source == "forced_rescan"
    assert agent._turn_llm1_reused_from_step is None


def test_record_perception_telemetry_ocr_only_vs_scene_cache() -> None:
    agent = object.__new__(CollectAgent)
    agent._cached_perceive_scene = None
    agent._cached_perceive_scene_step = None
    agent._reset_turn_telemetry()
    agent._record_perception_telemetry(
        ran_llm1=False,
        gate_reason="ocr-only:map",
        forced=False,
    )
    assert agent._turn_perception_source == "ocr_cache"
    assert agent._turn_llm1_reused_from_step is None
