from __future__ import annotations

from loop.agent.entities import EntityTracker, TrackedEntity
from loop.agent.loop import CollectAgent
from loop.agent.perceive import (
    SceneDescription,
    candidates_unidentified_only,
    combat_materially_changed,
    enemy_signature,
    scene_from_ocr,
    should_run_llm1_perceive,
)
from loop.os.frame_model import ActionSurface, FrameContext, FrameModel, FrameObject


def test_scene_change_runs_llm1() -> None:
    run, reason = should_run_llm1_perceive(screen="map", prev_screen="combat")
    assert run
    assert reason == "scene-change"

    run, reason = should_run_llm1_perceive(
        screen="title",
        prev_screen="map",
        host_stats={"screen": "title"},
        prev_host_stats={"screen": "map"},
    )
    assert run
    assert reason == "scene-change"


def test_first_observation_runs_llm1() -> None:
    run, reason = should_run_llm1_perceive(screen="map")
    assert run
    assert reason == "scene-change"


def test_stable_non_combat_skips_llm1() -> None:
    for screen in ("map", "title", "tutorial", "select", "pause"):
        run, reason = should_run_llm1_perceive(
            screen=screen,
            prev_screen=screen,
            host_stats={"screen": screen},
            prev_host_stats={"screen": screen},
            candidate_labels=["Continue", "Embark"],
        )
        assert not run
        assert reason == f"ocr-only:{screen}"


def test_unverified_effect_runs_llm1() -> None:
    run, reason = should_run_llm1_perceive(
        screen="map",
        prev_screen="map",
        host_stats={"screen": "map"},
        prev_host_stats={"screen": "map"},
        unverified_effect=True,
    )
    assert run
    assert reason == "unverified-effect"


def test_contradictory_runs_llm1() -> None:
    run, reason = should_run_llm1_perceive(
        screen="map",
        prev_screen="map",
        host_stats={"screen": "map"},
        prev_host_stats={"screen": "map"},
        contradictory=True,
    )
    assert run
    assert reason == "contradictory"


def test_unidentified_only_candidates_runs_llm1() -> None:
    run, reason = should_run_llm1_perceive(
        screen="map",
        prev_screen="map",
        host_stats={"screen": "map"},
        prev_host_stats={"screen": "map"},
        candidate_labels=["node_0", "icon", "unknown_character_1"],
    )
    assert run
    assert reason == "unidentified-only"


def test_candidates_none_does_not_trigger_unidentified_only() -> None:
    run, reason = should_run_llm1_perceive(
        screen="map",
        prev_screen="map",
        host_stats={"screen": "map"},
        prev_host_stats={"screen": "map"},
        candidate_labels=None,
    )
    assert not run
    assert reason == "ocr-only:map"


def test_candidates_unidentified_only_helper() -> None:
    assert not candidates_unidentified_only(None)
    assert candidates_unidentified_only(["node_0", "icon", ""])
    assert not candidates_unidentified_only(["Continue", "node_0"])


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


def test_record_dispatch_effect_keeps_unverified_noop() -> None:
    agent = object.__new__(CollectAgent)
    agent._last_intent = {"kind": "click_control", "control_id": "icon"}
    record = agent._record_dispatch_effect(
        error=None,
        executed=["click"],
        pixel_changed=False,
        hover=False,
    )
    assert record["dispatch"] == "ok"
    assert record["effect"] == "unverified"
    assert record["code"] == "effect-unverified"
    assert agent._last_unverified_effect is True
    assert agent._last_execution_failure is not None
    assert agent._last_execution_failure["effect"] == "unverified"


def test_record_dispatch_effect_clears_on_verified_move() -> None:
    agent = object.__new__(CollectAgent)
    agent._last_intent = {"kind": "click_control"}
    agent._last_unverified_effect = True
    agent._last_execution_failure = {"code": "effect-unverified"}
    record = agent._record_dispatch_effect(
        error=None,
        executed=["click"],
        pixel_changed=True,
        hover=False,
    )
    assert record["dispatch"] == "ok"
    assert record["effect"] == "verified"
    assert record["code"] is None
    assert agent._last_unverified_effect is False
    assert agent._last_execution_failure is None


def test_record_dispatch_effect_hover_stays_inconclusive() -> None:
    agent = object.__new__(CollectAgent)
    agent._last_intent = {"kind": "inspect"}
    record = agent._record_dispatch_effect(
        error=None,
        executed=["hover"],
        pixel_changed=False,
        hover=True,
    )
    assert record["effect"] == "inconclusive"
    assert agent._last_unverified_effect is False
    assert agent._last_execution_failure is None


def test_record_dispatch_effect_card_unverified_keeps_dispatch_ok() -> None:
    from loop.log.schema import CardTransaction

    agent = object.__new__(CollectAgent)
    agent._last_intent = {"kind": "play_card"}
    txn = CardTransaction(source_dispatched=True, target_dispatched=True, outcome="effect_unverified")
    record = agent._record_dispatch_effect(
        error=None,
        executed=["click", "click"],
        pixel_changed=False,
        hover=False,
        card_transaction=txn,
    )
    assert record["dispatch"] == "ok"
    assert record["effect"] == "unverified"
    assert record["code"] == "effect-unverified"
    assert record["transaction_outcome"] == "effect_unverified"
    assert agent._last_unverified_effect is True


def test_hover_pixel_jitter_without_screen_change_stays_inconclusive() -> None:
    agent = object.__new__(CollectAgent)
    agent._last_intent = {"kind": "inspect"}
    record = agent._record_dispatch_effect(
        error=None,
        executed=["hover"],
        pixel_changed=True,
        hover=True,
        screen_changed=False,
        global_tiny_change=True,
    )
    assert record["effect"] == "inconclusive"
    assert agent._last_unverified_effect is False


def test_same_object_unverified_twice_flags_cycle_not_unresolved() -> None:
    agent = object.__new__(CollectAgent)
    agent._last_intent = {}
    kwargs = dict(
        error=None,
        executed=["click"],
        pixel_changed=False,
        hover=False,
        screen="map",
        evidence_fingerprint="map-legend-panel-v1",
        bbox=[0.86, 0.14, 0.94, 0.22],
    )
    first = agent._record_dispatch_effect(object_id="legend_elite_icon", **kwargs)
    second = agent._record_dispatch_effect(object_id="legend_elite_icon", **kwargs)
    assert first["effect"] == "unverified"
    assert second["cycle_repeat"] is True
    assert second["unresolved"] is False
    assert agent._last_unverified_effect is True


def test_distinct_unverified_retry_marks_perception_unresolved() -> None:
    agent = object.__new__(CollectAgent)
    agent._last_intent = {}
    shared = dict(
        error=None,
        executed=["click"],
        pixel_changed=False,
        hover=False,
        screen="map",
        evidence_fingerprint="map-unchanged-v1",
    )
    agent._record_dispatch_effect(
        object_id="legend_elite_icon",
        bbox=[0.86, 0.14, 0.94, 0.22],
        **shared,
    )
    second = agent._record_dispatch_effect(
        object_id="map_node_center",
        bbox=[0.38, 0.34, 0.46, 0.42],
        **shared,
    )
    assert second["unresolved"] is True
    assert second["cycle_repeat"] is False
    assert second["code"] == "perception-unresolved"
    assert agent._last_unverified_effect is False
    assert agent._pending_exec_error == "perception-unresolved"


def test_empty_snapshot_still_bounds_same_object_retry() -> None:
    agent = object.__new__(CollectAgent)
    agent._last_intent = {}
    agent._last_snapshot_id = ""
    agent._last_surface_membership = ()
    agent._perception_cache = None
    agent._last_obs_image = None
    kwargs = dict(
        error=None,
        executed=["click"],
        pixel_changed=False,
        hover=False,
        screen="map",
        bbox=[0.86, 0.14, 0.94, 0.22],
    )
    first = agent._record_dispatch_effect(object_id="legend_elite_icon", **kwargs)
    second = agent._record_dispatch_effect(object_id="legend_elite_icon", **kwargs)
    assert first["effect"] == "unverified"
    assert second["cycle_repeat"] is True
    assert agent._effect_attempts[-1]["evidence_fingerprint"] == "map|obs"


def _map_action_surface(
    *, run_id: str, step_id: int, node_x: float = 0.42, extra: bool = False, mark_offset: int = 0,
):
    objects = [
        FrameObject(
            id=1 + mark_offset,
            x=0.90,
            y=0.18,
            x0=0.86,
            y0=0.14,
            x1=0.94,
            y1=0.22,
            label="Legend",
            role="button",
            kind="interactive",
        ),
        FrameObject(
            id=2 + mark_offset,
            x=node_x,
            y=0.38,
            x0=node_x - 0.04,
            y0=0.34,
            x1=node_x + 0.04,
            y1=0.42,
            label="Node",
            role="button",
            kind="interactive",
        ),
    ]
    if extra:
        objects.append(
            FrameObject(
                id=3 + mark_offset,
                x=0.62,
                y=0.55,
                x0=0.58,
                y0=0.51,
                x1=0.66,
                y1=0.59,
                label="Rest",
                role="button",
                kind="interactive",
            )
        )
    return ActionSurface(
        run_id=run_id,
        step_id=step_id,
        snapshot_id=f"snapshot-{run_id}-{step_id}",
        context=FrameContext(screen="map", activity="map"),
        objects=tuple(objects),
    )


def test_effect_fingerprint_ignores_snapshot_run_step_and_mark_ids() -> None:
    agent = object.__new__(CollectAgent)
    agent._action_surface = _map_action_surface(run_id="run-a", step_id=1)
    agent._last_snapshot_id = "snapshot-a"
    first_snapshot = agent._action_surface.snapshot_id
    first = agent._evidence_fingerprint(screen="map")

    agent._action_surface = _map_action_surface(run_id="run-b", step_id=2, mark_offset=10)
    agent._last_snapshot_id = "snapshot-b"
    second = agent._evidence_fingerprint(screen="map")

    assert agent._action_surface.snapshot_id != first_snapshot
    assert first == second
    assert "snapshot" not in first
    assert first.startswith("surf:map|")


def test_effect_fingerprint_resets_noop_sequence_when_surface_changes() -> None:
    agent = object.__new__(CollectAgent)
    agent._last_intent = {}
    agent._action_surface = _map_action_surface(run_id="run", step_id=1)
    shared = dict(error=None, executed=["click"], pixel_changed=False, hover=False, screen="map")
    first = agent._record_dispatch_effect(object_id="legend", bbox=[0.86, 0.14, 0.94, 0.22], **shared)

    agent._action_surface = _map_action_surface(run_id="run", step_id=2, node_x=0.50)
    second = agent._record_dispatch_effect(object_id="node", bbox=[0.46, 0.34, 0.54, 0.42], **shared)

    assert first["effect"] == "unverified"
    assert second["unresolved"] is False
    assert len(agent._effect_attempts) == 1

    agent._action_surface = _map_action_surface(run_id="run", step_id=3, node_x=0.50, extra=True)
    third = agent._record_dispatch_effect(object_id="rest", bbox=[0.58, 0.51, 0.66, 0.59], **shared)

    assert third["unresolved"] is False
    assert len(agent._effect_attempts) == 1


def test_distinct_noops_on_fresh_snapshot_of_same_surface_force_rescan() -> None:
    agent = object.__new__(CollectAgent)
    agent._last_intent = {}
    agent._action_surface = _map_action_surface(run_id="run-a", step_id=1)
    agent._last_snapshot_id = "snapshot-a"
    shared = dict(error=None, executed=["click"], pixel_changed=False, hover=False, screen="map")
    agent._record_dispatch_effect(object_id="legend", bbox=[0.86, 0.14, 0.94, 0.22], **shared)

    agent._action_surface = _map_action_surface(run_id="run-b", step_id=2, mark_offset=10)
    agent._last_snapshot_id = "snapshot-b"
    second = agent._record_dispatch_effect(object_id="node", bbox=[0.38, 0.34, 0.46, 0.42], **shared)

    assert second["unresolved"] is True
    assert agent._pending_exec_error == "perception-unresolved"



def test_perception_gate_signals_pass_unverified_and_placeholders() -> None:
    from loop.os.aim import AimGuide, AimMark
    from PIL import Image

    agent = object.__new__(CollectAgent)
    agent._last_unverified_effect = True
    agent._host_stats = {"screen": "map"}
    agent._obs_metrics = {"screen": "map"}
    agent._cached_perceive_scene = None
    agent._overlay_state = None
    agent._aim = AimGuide.from_marks(
        Image.new("RGB", (8, 8)),
        [
            AimMark(id=1, x=0.2, y=0.8, x0=0.16, y0=0.76, x1=0.24, y1=0.84, label="icon"),
            AimMark(id=2, x=0.42, y=0.38, x0=0.38, y0=0.34, x1=0.46, y1=0.42, label=""),
        ],
    )
    signals = agent._perception_gate_signals()
    assert signals["unverified_effect"] is True
    assert signals["contradictory"] is False
    assert signals["candidate_labels"] == ["icon", ""]
    run, reason = should_run_llm1_perceive(
        screen="map",
        prev_screen="map",
        host_stats=agent._host_stats,
        prev_host_stats=agent._host_stats,
        **signals,
    )
    assert run
    assert reason == "unverified-effect"


def test_perception_contradictory_cached_screen_mismatch() -> None:
    agent = object.__new__(CollectAgent)
    agent._host_stats = {"screen": "map"}
    agent._obs_metrics = {"screen": "map"}
    agent._cached_perceive_scene = SceneDescription(screen="combat")
    agent._overlay_state = None
    assert agent._perception_contradictory() is True


def test_rebuild_observation_marks_drops_stale_labels(monkeypatch) -> None:
    from loop.os.aim import AimGuide, AimMark
    from PIL import Image

    stale = AimMark(id=1, x=0.1, y=0.1, x0=0.05, y0=0.05, x1=0.15, y1=0.15, label="stale_old")
    fresh = AimMark(id=2, x=0.42, y=0.38, x0=0.38, y0=0.34, x1=0.46, y1=0.42, label="")
    image = Image.new("RGB", (16, 16))
    agent = object.__new__(CollectAgent)
    agent._aim = AimGuide.from_marks(image, [stale])
    agent._host_stats = {"screen": "map"}
    agent._obs_metrics = {"screen": "map"}
    agent._scene_desc = None
    agent.journal = type("Journal", (), {"lexicon": None})()
    agent._last_ocr_words = []
    monkeypatch.setattr("loop.agent.loop.ocr_click_marks", lambda *_a, **_k: [])
    monkeypatch.setattr("loop.agent.loop.gui_click_marks", lambda *_a, **_k: [])
    monkeypatch.setattr("loop.agent.loop.build_marks", lambda *_a, **_k: (fresh,))
    monkeypatch.setattr(CollectAgent, "_number_aim_marks", lambda self, marks, **_k: list(marks))
    out = agent._rebuild_observation_marks(image)
    assert len(out) == 1
    assert abs(out[0].x - 0.42) < 0.01
    assert agent._gate_candidate_labels() == [""]
    assert "stale_old" not in (agent._gate_candidate_labels() or [])


def test_inventory_inconsistent_forces_llm1_rescan() -> None:
    from unittest.mock import Mock

    from loop.agent.decision import TurnSnapshot
    from loop.agent.perception_cache import PerceptionCache
    from loop.os.aim import AimMark
    from loop.os.frame_model import build_action_surface

    marks = [
        AimMark(id=1, x=0.5, y=0.5, x0=0.4, y0=0.4, x1=0.6, y1=0.6, label="icon", role="icon"),
    ]
    agent = CollectAgent.__new__(CollectAgent)
    agent._action_surface = build_action_surface(
        marks, run_id="r", step_id=1, screen="combat", activity="combat",
    )
    agent.gateway = Mock()
    agent._last_execution_failure = None
    agent._last_reason_state_key = ""
    agent._unchanged_failure_count = 0
    agent._empty_action_surface_key = ""
    agent._empty_action_surface_count = 0
    agent._decision_softlock = False
    agent._session_flags = []
    agent._pending_exec_error = None
    agent._force_perceive = False
    agent._perception_cache = PerceptionCache()
    agent._pending_inventory_rescan = False
    agent._last_frame_mad = 0.0
    agent._obs_metrics = {}
    agent._host_stats = {"screen": "combat"}
    agent._aim = None
    agent._last_obs_image = None
    snapshot = TurnSnapshot(snapshot_id="t", screen="combat", turn="ours", hp=80, block=None, energy=3)
    agent._run_v2_act(snapshot)
    assert agent._pending_exec_error == "inventory-inconsistent"
    assert agent._force_perceive is True
    assert agent._pending_inventory_rescan is True
    run, _reason = should_run_llm1_perceive(screen="combat", force=True)
    assert run
