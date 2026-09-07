from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from loop.agent.entities import EntityTracker
from loop.agent.loop import CollectAgent, CollectConfig
from loop.fixtures.sts2 import Sts2Fixture
from loop.log.schema import Observation, StepLog, StepResult
from loop.memory.screen_objects import ScreenObjectStore, _stable_object_id
from loop.mcp.tools.targeting import (
    bind_snapshot_target_ids,
    object_role,
    plan_card_by_id,
    resolve_planned_target,
)
from loop.os.aim import AimMark, apply_enemy_bboxes


def _hp_plate(**kwargs: object) -> AimMark:
    defaults = dict(
        id=10, x=0.75, y=0.35, x0=0.70, y0=0.32, x1=0.80, y1=0.38,
        label="unidentified_enemy", role="unknown",
    )
    defaults.update(kwargs)
    return AimMark(**defaults)  # type: ignore[arg-type]


def _sprite(**kwargs: object) -> AimMark:
    defaults = dict(
        id=11, x=0.76, y=0.54, x0=0.70, y0=0.48, x1=0.82, y1=0.62,
        label="unidentified_enemy", role="enemy",
    )
    defaults.update(kwargs)
    return AimMark(**defaults)  # type: ignore[arg-type]


def _card() -> AimMark:
    return AimMark(
        id=1, x=0.50, y=0.80, x0=0.40, y0=0.70, x1=0.60, y1=0.90,
        label="Strike", role="card",
    )


def test_hp_only_frame_has_no_legal_enemy_id(tmp_path: Path) -> None:
    store = ScreenObjectStore(tmp_path / "hp_only.db")
    store.save_frame(run_id="r", step_id=1, screen="combat", marks=(_card(), _hp_plate()))
    assert all(object_role(obj, entity_targets_ok=True) != "enemy" for obj in store.objects)
    assert plan_card_by_id(
        store, card_id="card_1", target_id="unknown_character_1", entity_targets_ok=True,
    )["reason"] == "target-not-found"
    store.close()


def test_sprite_plus_plate_assigns_enemy_id_to_sprite(tmp_path: Path) -> None:
    store = ScreenObjectStore(tmp_path / "split.db")
    store.save_frame(
        run_id="r", step_id=1, screen="combat",
        marks=(_card(), _hp_plate(), _sprite()),
    )
    plate = next(obj for obj in store.objects if obj.mark_id == 10)
    sprite = next(obj for obj in store.objects if obj.mark_id == 11)
    assert object_role(plate, entity_targets_ok=True) != "enemy"
    assert sprite.object_id == "enemy_1"
    assert object_role(plate, entity_targets_ok=True) == "unknown"
    assert object_role(sprite, entity_targets_ok=True) == "enemy"
    store.close()


def test_hp_bbox_does_not_bind_without_a_matching_current_object(tmp_path: Path) -> None:
    store = ScreenObjectStore(tmp_path / "column.db")
    store.save_frame(
        run_id="r", step_id=1, screen="combat",
        marks=(_card(), _hp_plate(), _sprite()),
    )
    rows = [{
        "label": "unknown_character_1",
        "role": "unknown_character",
        "entity_id": "unknown_character_1",
        "bbox": [700, 320, 800, 380],
    }]
    bind_snapshot_target_ids(store, rows, entity_targets_ok=True)
    assert rows[0]["entity_id"] == "unknown_character_1"
    store.close()


def test_hp_unidentified_unknown_role_stays_non_targetable(tmp_path: Path) -> None:
    store = ScreenObjectStore(tmp_path / "hp_unknown.db")
    store.save_frame(
        run_id="r", step_id=1, screen="combat",
        marks=(_card(), _hp_plate(role="unknown", object_id="object:11")),
    )
    plate = next(obj for obj in store.objects if abs(obj.y - 0.35) < 0.02)
    assert object_role(plate, entity_targets_ok=True) == "unknown"
    assert plate.role == "unknown"
    store.close()


def test_canonical_enemy_id_survives_save_frame(tmp_path: Path) -> None:
    store = ScreenObjectStore(tmp_path / "persist.db")
    store.save_frame(run_id="r", step_id=1, screen="combat", marks=(_card(), _sprite(id=11)))
    first = plan_card_by_id(store, card_id="card_1", target_id="enemy_1", entity_targets_ok=True)
    assert first["ok"] is True
    store.save_frame(
        run_id="r", step_id=2, screen="combat",
        marks=(_card(), _hp_plate(id=4), _sprite(id=8)),
    )
    again = plan_card_by_id(store, card_id="card_1", target_id="enemy_1", entity_targets_ok=True)
    assert again["ok"] is True
    assert again["transaction"]["target_candidate"]["object_id"] == "enemy_1"
    assert resolve_planned_target(store, "unknown_character_1", entity_targets_ok=True) is None
    store.close()


def test_named_nibbit_sprite_plans(tmp_path: Path) -> None:
    store = ScreenObjectStore(tmp_path / "nibbit.db")
    store.save_frame(
        run_id="r", step_id=1, screen="combat",
        marks=(
            _card(),
            AimMark(
                id=6, x=0.76, y=0.60, x0=0.70, y0=0.54, x1=0.82, y1=0.68,
                label="Nibbit", role="enemy", object_id="object:6",
            ),
        ),
    )
    enemy = next(obj for obj in store.objects if obj.label == "Nibbit")
    plan = plan_card_by_id(
        store, card_id="card_1", target_id=enemy.object_id, entity_targets_ok=True,
    )
    assert plan["ok"] is True
    assert plan["transaction"]["target_candidate"]["point"]["y"] >= 0.50
    store.close()


def test_snapshot_omits_prompt_ids_and_hp_plate(tmp_path: Path) -> None:
    store = ScreenObjectStore(tmp_path / "snap.db")
    store.save_frame(
        run_id="r", step_id=1, screen="combat",
        marks=(_card(), _hp_plate(), _sprite()),
    )
    agent = CollectAgent.__new__(CollectAgent)
    agent.config = CollectConfig(architecture="two_stage", arm="no_skill")
    agent.fixture = Sts2Fixture(
        controller=type("C", (), {"execute": lambda *_a, **_k: [], "release_all": lambda: None})(),
    )
    agent._screen_objects = store
    agent._action_surface = None
    agent._entity_tracker = EntityTracker()
    agent._reason_controls = [{
        "label": "unknown_character_1",
        "role": "unknown_character",
        "entity_id": "unknown_character_1",
        "bbox": [700, 320, 800, 380],
    }]
    agent._game_state = None
    agent._last_execution_failure = None
    agent._v2_control_objects = {}
    agent._host_stats = {"screen": "combat", "energy": 3}
    agent._obs_metrics = {"screen": "combat"}
    scene = SimpleNamespace(
        screen="combat", enemies=[], characters=[], controls=[], hand_cards=[],
    )
    snapshot = agent._build_turn_snapshot(scene)
    ids = [row.target_id for row in snapshot.targets]
    assert "unknown_character_1" not in ids
    assert ids == ["enemy_1"]
    store.close()


def test_apply_enemy_bboxes_does_not_promote_prompt_id_to_canonical_id() -> None:
    plate = _hp_plate(id=3, role="unknown")
    sprite = _sprite(id=4, role="", object_id="")
    out = apply_enemy_bboxes(
        [plate, sprite],
        [{"entity_id": "unknown_character_1", "label": "unknown_character_1", "bbox": [700, 320, 800, 380]}],
    )
    by_id = {mark.id: mark for mark in out}
    assert by_id[3].object_id != "unknown_character_1"
    assert by_id[4].object_id != "unknown_character_1"


def test_sts2_success_uses_terminal_evidence_not_target_error_counts() -> None:
    fx = Sts2Fixture()
    steps = [
        StepLog(
            session_id="s",
            episode_id="e",
            step_id=i,
            ts="t",
            fixture="sts2",
            observation=Observation(screenshot_ref="a"),
            result=StepResult(error="target-not-found:unknown_character_1"),
            metrics={"screen": "combat", "energy": 3, "hp": 80, "floor": 1},
        )
        for i in range(1, 5)
    ]
    assert fx.success(steps, "timeout") is True
    progress = [
        StepLog(
            session_id="s",
            episode_id="e",
            step_id=1,
            ts="t",
            fixture="sts2",
            observation=Observation(screenshot_ref="a"),
            result=StepResult(pixel_changed=True),
            metrics={"screen": "title", "floor": 0},
        ),
        StepLog(
            session_id="s",
            episode_id="e",
            step_id=2,
            ts="t",
            fixture="sts2",
            observation=Observation(screenshot_ref="a"),
            result=StepResult(pixel_changed=True),
            metrics={"screen": "combat", "floor": 1, "energy": 3},
        ),
    ]
    assert fx.success(progress, "timeout") is True


def test_stable_object_id_rejects_object_prefixed_ids() -> None:
    def mark(*, object_id: str) -> AimMark:
        return AimMark(
            id=10, x=0.75, y=0.55, x0=0.68, y0=0.48, x1=0.82, y1=0.66,
            label="Nibbit", role="enemy", object_id=object_id,
        )

    assert _stable_object_id(mark(object_id="object:10")) == ""
    assert _stable_object_id(mark(object_id="enemy_1")) == "enemy_1"
    assert _stable_object_id(mark(object_id="mark_3")) == ""
