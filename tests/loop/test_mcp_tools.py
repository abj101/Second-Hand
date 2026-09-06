from __future__ import annotations

import json
from pathlib import Path

from loop.memory.journal import FactJournal
from loop.memory.notebook import Notebook
from loop.memory.screen_objects import ScreenObject, ScreenObjectStore
from loop.mcp.orchestrator import ToolOrchestrator
from loop.mcp.registry import ToolRegistry
from loop.mcp.tools.memory import register_reasoner_tools
from loop.mcp.tools.targeting import (
    bind_snapshot_target_ids,
    current_snapshot_identity,
    inspect_object,
    invalidate_hand_snapshot,
    object_role,
    plan_card_by_id,
    play_card,
    play_card_plan,
    resolve_planned_source,
    resolve_planned_target,
    screen_objects,
    targeting_validate,
    validate_stable_transaction,
)
from loop.model.gateway import ModelCall, VlmGateway
from loop.os.aim import AimMark


def _store_with_marks(tmp_path: Path) -> ScreenObjectStore:
    store = ScreenObjectStore(tmp_path / "obj.db")
    store.save_frame(
        run_id="r",
        step_id=1,
        marks=(
            AimMark(id=1, x=0.75, y=0.63, x0=0.68, y0=0.56, x1=0.82, y1=0.72, label="Slime", role="enemy"),
            AimMark(id=2, x=0.5, y=0.8, x0=0.4, y0=0.7, x1=0.6, y1=0.9, label="Strike", role="card"),
        ),
        screen="combat",
    )
    return store


def test_targeting_validate_card_and_enemy(tmp_path: Path) -> None:
    store = _store_with_marks(tmp_path)
    ok = targeting_validate(store, card_mark=2, target_mark=1)
    assert ok["ok"] is True
    bad = targeting_validate(store, card_mark=2, target_mark=2)
    assert bad["ok"] is False


def test_targeting_validate_map_node_not_hand_card(tmp_path: Path) -> None:
    store = ScreenObjectStore(tmp_path / "map_node.db")
    store.save_frame(
        run_id="r",
        step_id=1,
        marks=(
            AimMark(
                id=3,
                x=0.5,
                y=0.82,
                x0=0.4,
                y0=0.72,
                x1=0.6,
                y1=0.92,
                label="node_7",
            ),
        ),
        screen="map",
    )
    result = targeting_validate(store, card_mark=3, target_mark=None)
    assert result["ok"] is True
    assert "hand card" not in result["reason"]
    store.close()


def test_typed_inventory_rejects_icon_card_and_proposes_unknown_inspection(tmp_path: Path) -> None:
    store = ScreenObjectStore(tmp_path / "typed.db")
    store.save_frame(
        run_id="r", step_id=1, screen="combat",
        marks=(
            AimMark(id=1, x=0.50, y=0.84, x0=0.4, y0=0.75, x1=0.6, y1=0.95, label=":Icon"),
            AimMark(id=2, x=0.75, y=0.64, x0=0.68, y0=0.56, x1=0.82, y1=0.72, label="unknown_character_1", role="enemy"),
            AimMark(id=3, x=0.08, y=0.05, x0=0.01, y0=0.01, x1=0.15, y1=0.09, label=":Icon", role="hud"),
        ),
    )
    inventory = screen_objects(store)
    assert inventory["cards"] == []
    assert {row["mark"] for row in inventory["unknown"]} == {1}
    assert {row["mark"] for row in inventory["enemies"]} == {2}
    assert {row["target_mark"] for row in inventory["inspection_proposals"]} == {1}
    assert any(row["mark"] == 3 for row in inventory["hud"])
    assert [obj.mark_id for obj in store.enemies()] == [2]
    assert targeting_validate(store, card_mark=1, target_mark=2)["ok"] is False
    assert store.hand_cards() == []
    store.close()


def _combat_obj(label: str, x: float, y: float) -> ScreenObject:
    return ScreenObject.from_mark(
        AimMark(id=1, x=x, y=y, x0=x - 0.05, y0=y - 0.05, x1=x + 0.05, y1=y + 0.05, label=label),
        screen="combat",
    )


def test_object_role_requires_fixture_owned_domain_type() -> None:
    for label in ("unidentified_enemy", "unknown_character_1"):
        obj = _combat_obj(label, 0.75, 0.645)
        assert object_role(obj, entity_targets_ok=True) == "unknown", label
        assert object_role(obj) == "unknown", label
        typed = ScreenObject.from_mark(
            AimMark(id=2, x=.13, y=.22, x0=.08, y0=.16, x1=.19, y1=.29, label=label, role="enemy"),
            screen="combat",
        )
        assert object_role(typed, entity_targets_ok=True) == "enemy", label


def test_object_role_hand_unknown_and_hud_unchanged() -> None:
    hand = _combat_obj("unknown_character_1", 0.40, 0.84)
    assert object_role(hand, entity_targets_ok=True) == "unknown"
    hud = ScreenObject.from_mark(AimMark(id=2, x=.08, y=.05, x0=.02, y0=.01, x1=.14, y1=.09, label=":Icon", role="hud"), screen="combat")
    assert object_role(hud, entity_targets_ok=True) == "hud"
    icon = _combat_obj(":icon", 0.75, 0.645)
    assert object_role(icon, entity_targets_ok=True) == "unknown"


def test_typed_targets_resolve_in_an_arbitrary_non_sts_layout(tmp_path: Path) -> None:
    """Generic target binding is independent of screen bands or game layout."""
    store = ScreenObjectStore(tmp_path / "arbitrary-layout.db")
    store.save_frame(
        run_id="r", step_id=1, screen="combat",
        marks=(
            AimMark(id=1, x=.11, y=.16, x0=.04, y0=.08, x1=.18, y1=.24, label="Tool", role="card"),
            AimMark(id=2, x=.91, y=.11, x0=.84, y0=.03, x1=.98, y1=.19, label="Target", role="enemy"),
        ),
    )
    plan = plan_card_by_id(store, card_id="card_1", target_id="enemy_1", entity_targets_ok=True)
    assert plan["ok"] is True
    assert plan["transaction"]["source"]["mark_id"] == 1
    assert plan["transaction"]["target_candidate"]["mark_id"] == 2
    store.close()


def test_play_card_accepts_player_self_target(tmp_path: Path) -> None:
    store = ScreenObjectStore(tmp_path / "self.db")
    store.save_frame(
        run_id="r",
        step_id=1,
        screen="combat",
        marks=(
            AimMark(
                id=1, x=0.24, y=0.58, x0=0.20, y0=0.47, x1=0.30, y1=0.69,
                label="player", role="player",
            ),
            AimMark(
                id=2, x=0.50, y=0.84, x0=0.40, y0=0.75, x1=0.60, y1=0.95,
                label="Inflame", role="card",
            ),
            AimMark(
                id=3, x=0.74, y=0.63, x0=0.68, y0=0.56, x1=0.80, y1=0.69,
                label="Nibbit", role="enemy",
            ),
        ),
    )
    inventory = screen_objects(store, entity_targets_ok=True)
    assert {row["label"] for row in inventory["players"]} == {"player"}
    assert {row["label"] for row in inventory["enemies"]} == {"Nibbit"}
    player = store.by_id(1)
    assert player is not None
    assert object_role(player, entity_targets_ok=True) == "player"
    frame = inventory["frame"]
    ok = targeting_validate(store, card_mark=2, target_mark=1, **frame)
    assert ok["ok"] is True
    plan = play_card_plan(store, card_mark=2, target_mark=1, **frame)
    assert plan["ok"] is True
    assert plan["transaction"]["target_mark"] == 1
    assert plan["transaction"]["expected_effects"]["player_block_or_status_changes"] is True
    store.close()


def test_play_card_plan_has_both_marks_and_verifiable_effects(tmp_path: Path) -> None:
    store = _store_with_marks(tmp_path)
    frame = screen_objects(store)["frame"]
    plan = play_card_plan(store, card_mark=2, target_mark=1, **frame)
    assert plan["ok"] is True
    transaction = plan["transaction"]
    assert transaction["card_mark"] == 2
    assert transaction["target_mark"] == 1
    assert transaction["expected_effects"]["energy_spent"] is True
    assert transaction["expected_effects"]["enemy_hp_changes"] is True
    store.close()


def test_play_card_plan_requires_current_frame_nonce(tmp_path: Path) -> None:
    store = _store_with_marks(tmp_path)
    plan = play_card_plan(store, card_mark=2, target_mark=1)
    assert plan["ok"] is False
    assert "run_id" in plan["reason"]
    assert plan["transaction"] is None
    store.close()


def test_stable_id_plan_rebinds_after_mark_renumbering(tmp_path: Path) -> None:
    store = _store_with_marks(tmp_path)
    first = plan_card_by_id(store, card_id="card_1", target_id="enemy_1")
    assert first["ok"] is True
    assert first["transaction"]["source"]["mark_id"] == 2
    store.save_frame(
        run_id="r", step_id=2, screen="combat",
        marks=(
            AimMark(id=81, x=0.75, y=0.63, x0=0.68, y0=0.56, x1=0.82, y1=0.72, label="Slime", role="enemy"),
                AimMark(id=34, x=0.50, y=0.80, x0=0.40, y0=0.70, x1=0.60, y1=0.90, label="Strike", role="card"),
        ),
    )
    rebound = plan_card_by_id(store, card_id="card_1", target_id="enemy_1")
    assert rebound["ok"] is True
    assert rebound["transaction"]["source"]["mark_id"] == 34
    assert rebound["transaction"]["target_candidate"]["mark_id"] == 81
    store.close()


def test_stable_id_contract_rejects_a_stale_snapshot(tmp_path: Path) -> None:
    store = _store_with_marks(tmp_path)
    plan = plan_card_by_id(store, card_id="card_1", target_id="enemy_1")
    assert plan["ok"] is True
    store.save_frame(
        run_id="r", step_id=2, screen="combat",
        marks=(
            AimMark(id=1, x=0.75, y=0.63, x0=0.68, y0=0.56, x1=0.82, y1=0.72, label="Slime", role="enemy"),
            AimMark(id=2, x=0.50, y=0.80, x0=0.40, y0=0.70, x1=0.60, y1=0.90, label="Strike"),
        ),
    )
    assert validate_stable_transaction(store, plan["transaction"]) == {"ok": False, "reason": "stale-snapshot"}
    store.close()


def test_stable_id_plan_treats_target_as_optional_candidate(tmp_path: Path) -> None:
    store = _store_with_marks(tmp_path)
    strike_without_target = plan_card_by_id(store, card_id="card_1")
    assert strike_without_target["ok"] is True
    assert strike_without_target["transaction"]["target_candidate"] is None
    assert strike_without_target["transaction"]["expected_effects"]["target_required_after_source_click"] == "observe"

    store.save_frame(
        run_id="r", step_id=2, screen="combat",
        marks=(
            AimMark(id=9, x=0.75, y=0.63, x0=0.68, y0=0.56, x1=0.82, y1=0.72, label="Slime", role="enemy"),
                AimMark(id=7, x=0.50, y=0.80, x0=0.40, y0=0.70, x1=0.60, y1=0.90, label="Defend", role="card"),
        ),
    )
    defend_with_extra_target = plan_card_by_id(store, card_id="card_1", target_id="enemy_1")
    assert defend_with_extra_target["ok"] is True
    transaction = defend_with_extra_target["transaction"]
    assert transaction["target_candidate"]["object_id"] == "enemy_1"
    assert transaction["expected_effects"]["target_required_after_source_click"] == "observe"
    store.close()


def test_stable_id_plan_rejects_unknown_card_or_typed_target(tmp_path: Path) -> None:
    store = _store_with_marks(tmp_path)
    assert plan_card_by_id(store, card_id="card_missing")["reason"] == "source-not-found"
    assert plan_card_by_id(store, card_id="card_1", target_id="enemy_missing")["reason"] == "target-not-found"
    store.close()


def test_prompt_instance_id_unknown_character_resolves_to_enemy_mark(tmp_path: Path) -> None:
    store = ScreenObjectStore(tmp_path / "alias.db")
    store.save_frame(
        run_id="r",
        step_id=1,
        screen="combat",
        marks=(
            AimMark(id=1, x=0.50, y=0.80, x0=0.40, y0=0.70, x1=0.60, y1=0.90, label="Strike", role="card"),
            AimMark(id=2, x=0.75, y=0.63, x0=0.68, y0=0.56, x1=0.82, y1=0.72, label="unidentified_enemy", role="enemy"),
        ),
    )
    enemy = store.by_object_id("enemy_1")
    assert enemy is not None
    controls = [{
        "label": "unknown_character_1",
        "role": "unknown_character",
        "confidence": "high",
        "entity_id": "unknown_character_1",
        "bbox": [680, 560, 820, 720],
    }]
    bind_snapshot_target_ids(store, controls, entity_targets_ok=True)
    plan = plan_card_by_id(store, card_id="card_1", target_id="unknown_character_1", entity_targets_ok=True)
    assert plan["ok"] is True
    assert plan["transaction"]["target_candidate"]["mark_id"] == enemy.mark_id
    assert plan["transaction"]["target_candidate"]["object_id"] == "enemy_1"
    assert resolve_planned_target(store, "unknown_character_1", entity_targets_ok=True) == enemy
    store.close()


def test_prompt_instance_id_enemy_alias_resolves_when_geometry_matches(tmp_path: Path) -> None:
    store = ScreenObjectStore(tmp_path / "enemy_alias.db")
    store.save_frame(
        run_id="r",
        step_id=1,
        screen="combat",
        marks=(
            AimMark(id=1, x=0.50, y=0.80, x0=0.40, y0=0.70, x1=0.60, y1=0.90, label="Strike", role="card"),
            AimMark(id=2, x=0.75, y=0.63, x0=0.68, y0=0.56, x1=0.82, y1=0.72, label="unidentified_enemy", role="enemy"),
        ),
    )
    controls = [{
        "label": "unidentified_enemy",
        "role": "enemy",
        "confidence": "high",
        "entity_id": "enemy_1",
        "bbox": [680, 560, 820, 720],
    }]
    bind_snapshot_target_ids(store, controls, entity_targets_ok=True)
    plan = plan_card_by_id(store, card_id="card_1", target_id="enemy_1", entity_targets_ok=True)
    assert plan["ok"] is True
    store.close()


def test_captured_run_alias_binds_to_one_current_surface_object(tmp_path: Path) -> None:
    """Reduced 20260906T063400Z evidence: LLM alias → canonical enemy_1."""
    store = ScreenObjectStore(tmp_path / "captured-alias.db")
    store.save_frame(
        run_id="61138978e78f", step_id=1, screen="combat",
        marks=(
            AimMark(id=6, x=0.687, y=0.95, x0=0.617, y0=0.823, x1=0.757, y1=1.0, label="Bash", role="card"),
            AimMark(id=11, x=0.7505, y=0.6405, x0=0.6905, y0=0.5605, x1=0.8105, y1=0.7205, label="unknown_character_1", role="enemy"),
        ),
    )
    rows = [{"entity_id": "unknown_character_1", "role": "unknown_character", "bbox": [690, 560, 811, 721]}]
    bind_snapshot_target_ids(store, rows, entity_targets_ok=True)
    assert rows[0]["entity_id"] == "enemy_1"
    assert resolve_planned_target(store, "unknown_character_1", entity_targets_ok=True) == store.by_object_id("enemy_1")
    store.close()


def test_alias_resolution_rejects_ambiguous_arbitrary_layout(tmp_path: Path) -> None:
    store = ScreenObjectStore(tmp_path / "ambiguous.db")
    store.save_frame(
        run_id="any", step_id=4, screen="combat",
        marks=(
            AimMark(id=1, x=0.12, y=0.17, x0=0.08, y0=0.12, x1=0.16, y1=0.22, label="Card", role="card"),
            AimMark(id=2, x=0.31, y=0.33, x0=0.27, y0=0.28, x1=0.35, y1=0.38, label="A", role="enemy"),
            AimMark(id=3, x=0.49, y=0.33, x0=0.45, y0=0.28, x1=0.53, y1=0.38, label="B", role="enemy"),
        ),
    )
    rows = [{"entity_id": "unknown_character_1", "role": "unknown_character", "bbox": [360, 280, 440, 380]}]
    bind_snapshot_target_ids(store, rows, entity_targets_ok=True)
    assert store.object_for_instance_id("unknown_character_1") is None
    store.close()


def test_stale_prompt_instance_id_fails_after_inventory_rebuild(tmp_path: Path) -> None:
    store = ScreenObjectStore(tmp_path / "stale_alias.db")
    store.save_frame(
        run_id="r",
        step_id=1,
        screen="combat",
        marks=(
            AimMark(id=1, x=0.50, y=0.80, x0=0.40, y0=0.70, x1=0.60, y1=0.90, label="Strike", role="card"),
            AimMark(id=2, x=0.75, y=0.63, x0=0.68, y0=0.56, x1=0.82, y1=0.72, label="unidentified_enemy", role="enemy"),
        ),
    )
    controls = [{
        "label": "unknown_character_1",
        "role": "unknown_character",
        "confidence": "high",
        "entity_id": "unknown_character_1",
        "bbox": [680, 560, 820, 720],
    }]
    bind_snapshot_target_ids(store, controls, entity_targets_ok=True)
    assert plan_card_by_id(store, card_id="card_1", target_id="unknown_character_1", entity_targets_ok=True)["ok"]
    store.save_frame(
        run_id="r",
        step_id=2,
        screen="combat",
        marks=(
            AimMark(id=9, x=0.50, y=0.80, x0=0.40, y0=0.70, x1=0.60, y1=0.90, label="Strike", role="card"),
            AimMark(id=8, x=0.75, y=0.63, x0=0.68, y0=0.56, x1=0.82, y1=0.72, label="unidentified_enemy", role="enemy"),
        ),
    )
    stale = plan_card_by_id(store, card_id="card_1", target_id="unknown_character_1", entity_targets_ok=True)
    assert stale["ok"] is False
    assert stale["reason"] == "target-not-found"
    live = plan_card_by_id(store, card_id="card_1", target_id="enemy_1", entity_targets_ok=True)
    assert live["ok"] is True
    assert live["transaction"]["target_candidate"]["object_id"] == "enemy_1"
    store.close()


def test_invalidate_hand_snapshot_changes_identity_on_same_objects(tmp_path: Path) -> None:
    store = _store_with_marks(tmp_path)
    first = current_snapshot_identity(store)
    bumped = invalidate_hand_snapshot(store, reason="energy-change")
    second = current_snapshot_identity(store)
    assert first["snapshot_id"] != second["snapshot_id"]
    assert second["hand_generation"] == int(first["hand_generation"]) + 1
    assert bumped["invalidation_reason"] == "energy-change"
    store.close()


def test_resolve_and_validate_reject_after_hand_invalidation(tmp_path: Path) -> None:
    store = _store_with_marks(tmp_path)
    plan = plan_card_by_id(store, card_id="card_1")
    assert plan["ok"] is True
    invalidate_hand_snapshot(store, reason="hand-reflow")
    snapshot = plan["transaction"]["snapshot"]
    resolved = resolve_planned_source(store, card_id="card_1", snapshot=snapshot)
    assert resolved["ok"] is False
    assert resolved["reason"] == "stale-snapshot"
    assert validate_stable_transaction(store, plan["transaction"]) == {
        "ok": False,
        "reason": "stale-snapshot",
    }
    store.close()


def test_old_card_2_intent_is_stale_after_preceding_card_leaves(tmp_path: Path) -> None:
    store = ScreenObjectStore(tmp_path / "reflow.db")
    store.save_frame(
        run_id="r",
        step_id=1,
        screen="combat",
        marks=(
            AimMark(id=1, x=0.30, y=0.84, x0=0.22, y0=0.76, x1=0.38, y1=0.92, label="Strike", role="card"),
            AimMark(id=2, x=0.50, y=0.84, x0=0.42, y0=0.76, x1=0.58, y1=0.92, label="Defend", role="card"),
            AimMark(id=8, x=0.75, y=0.63, x0=0.68, y0=0.56, x1=0.82, y1=0.72, label="Slime", role="enemy"),
        ),
    )
    old = plan_card_by_id(store, card_id="card_2", entity_targets_ok=True)
    assert old["ok"] is True
    assert old["transaction"]["source"]["label"] == "Defend"
    store.save_frame(
        run_id="r",
        step_id=2,
        screen="combat",
        marks=(
            AimMark(id=2, x=0.40, y=0.84, x0=0.32, y0=0.76, x1=0.48, y1=0.92, label="Defend", role="card"),
            AimMark(id=8, x=0.75, y=0.63, x0=0.68, y0=0.56, x1=0.82, y1=0.72, label="Slime", role="enemy"),
        ),
    )
    assert validate_stable_transaction(store, old["transaction"])["reason"] == "stale-snapshot"
    assert plan_card_by_id(store, card_id="card_2")["reason"] == "source-not-found"
    rebuilt = plan_card_by_id(store, card_id="card_1")
    assert rebuilt["ok"] is True
    assert rebuilt["transaction"]["source"]["label"] == "Defend"
    assert rebuilt["transaction"]["snapshot"]["snapshot_id"] != old["transaction"]["snapshot"]["snapshot_id"]
    store.close()


def test_duplicate_strikes_are_not_substituted_after_reflow(tmp_path: Path) -> None:
    store = ScreenObjectStore(tmp_path / "dup.db")
    store.save_frame(
        run_id="r",
        step_id=1,
        screen="combat",
        marks=(
            AimMark(id=1, x=0.28, y=0.84, x0=0.20, y0=0.76, x1=0.36, y1=0.92, label="Strike", role="card"),
            AimMark(id=2, x=0.48, y=0.84, x0=0.40, y0=0.76, x1=0.56, y1=0.92, label="Strike", role="card"),
            AimMark(id=8, x=0.75, y=0.63, x0=0.68, y0=0.56, x1=0.82, y1=0.72, label="Slime", role="enemy"),
        ),
    )
    old = plan_card_by_id(store, card_id="card_2")
    assert old["ok"] is True
    store.save_frame(
        run_id="r",
        step_id=2,
        screen="combat",
        marks=(
            AimMark(id=2, x=0.48, y=0.84, x0=0.40, y0=0.76, x1=0.56, y1=0.92, label="Strike", role="card"),
            AimMark(id=8, x=0.75, y=0.63, x0=0.68, y0=0.56, x1=0.82, y1=0.72, label="Slime", role="enemy"),
        ),
    )
    leftover = store.by_object_id("card_1")
    assert leftover is not None and leftover.label == "Strike"
    resolved = resolve_planned_source(
        store, card_id="card_2", snapshot=old["transaction"]["snapshot"]
    )
    assert resolved["ok"] is False
    assert resolved["reason"] in {"stale-snapshot", "source-not-found"}
    assert validate_stable_transaction(store, old["transaction"])["ok"] is False
    store.close()


def test_terminal_play_card_and_inspect_require_current_typed_frame(tmp_path: Path) -> None:
    store = _store_with_marks(tmp_path)
    frame = screen_objects(store)["frame"]
    command = play_card(store, card_mark=2, target_mark=1, **frame)
    assert command["ok"] is True
    assert command["terminal"] is True
    assert command["transaction"]["card_mark"] == 2
    assert play_card(store, card_mark=2, target_mark=1, run_id="stale", step_id=1)["ok"] is False
    assert inspect_object(store, mark_id=1, **frame)["ok"] is True
    assert inspect_object(store, mark_id=99, **frame)["ok"] is False
    store.close()


def test_targeting_nonce_rejects_a_stale_frame(tmp_path: Path) -> None:
    store = _store_with_marks(tmp_path)
    current = screen_objects(store)["frame"]
    assert targeting_validate(store, card_mark=2, target_mark=1, **current)["ok"] is True
    assert targeting_validate(store, card_mark=2, target_mark=1, run_id="old", step_id=current["step_id"])["ok"] is False
    store.close()


def test_turn_tools_include_targeting(tmp_path: Path) -> None:
    from loop.memory.screen_objects import ScreenObjectStore
    from loop.mcp.tools.targeting import register_targeting_tools

    journal = FactJournal(path=tmp_path / "j.json", fact_cap=8)
    notebook = Notebook(journal)
    reg = ToolRegistry()
    register_reasoner_tools(reg, journal=journal, notebook=notebook, screen="combat")
    store = ScreenObjectStore(tmp_path / "objects.db")
    register_targeting_tools(reg, store, selected_getter=lambda: None)
    names = set(reg.names())
    assert "targeting_options" in names
    assert "targeting_validate" in names
    assert {"screen_objects", "play_card_plan", "play_card", "inspect_object", "plan_card_by_id", "play_card_by_id"}.issubset(names)
    assert reg.get("plan_card_by_id").parameters == {"card_id": "string", "target_id": "optional string"}  # type: ignore[union-attr]
    store.close()


def test_reasoner_tools_are_memory_only(tmp_path: Path) -> None:
    journal = FactJournal(path=tmp_path / "j.json", fact_cap=8)
    notebook = Notebook(journal)
    reg = ToolRegistry()
    register_reasoner_tools(reg, journal=journal, notebook=notebook, screen="combat")
    names = set(reg.names())
    assert names == {"memory_notebook", "memory_recall", "memory_remember"}
    assert "screen_perceive" not in names
    assert "hud_read" not in names
    assert "screen_list_objects" not in names
    assert "targeting_options" not in names


def test_memory_recall_tool(tmp_path: Path) -> None:
    journal = FactJournal(path=tmp_path / "j.json", fact_cap=8)
    notebook = Notebook(journal)
    reg = ToolRegistry()
    register_reasoner_tools(reg, journal=journal, notebook=notebook, screen="combat")
    out = reg.call("memory_recall", {"query": "door"})
    assert isinstance(out, str)


def test_orchestrator_tool_then_action() -> None:
    calls: list[dict] = [{"tool": "memory_notebook"}, {"choice": "Yes", "thought": "dismiss"}]

    class _Gw(VlmGateway):
        def __init__(self) -> None:
            super().__init__()
            self.i = 0

        def complete_text(self, **kwargs):  # type: ignore[no-untyped-def]
            del kwargs
            parsed = calls[min(self.i, len(calls) - 1)]
            self.i += 1
            return ModelCall(
                text=json.dumps(parsed),
                parsed=parsed,
                latency_ms=1.0,
                model="fake",
            )

    reg = ToolRegistry()
    reg.register(
        "memory_notebook",
        description="nb",
        parameters={},
        handler=lambda **_: "Goal: win",
    )
    orch = ToolOrchestrator(reg, max_rounds=3, budget_s=5.0)
    result = orch.run(_Gw(), system="sys", user="user", table_name="sts2")
    assert result.parsed.get("choice") == "Yes"
    assert result.rounds >= 2
    assert result.tool_events[0]["tool"] == "memory_notebook"


def test_orchestrator_stops_after_terminal_play_command() -> None:
    class _Gw(VlmGateway):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        def complete_text(self, **kwargs):  # type: ignore[no-untyped-def]
            del kwargs
            self.calls += 1
            parsed = {"tool": "play_card", "card_mark": 2, "target_mark": 1, "run_id": "r", "step_id": 1}
            return ModelCall(text=json.dumps(parsed), parsed=parsed, latency_ms=1.0, model="fake")

    reg = ToolRegistry()
    reg.register(
        "play_card", description="terminal", parameters={},
        handler=lambda **_: json.dumps({"ok": True, "terminal": True, "transaction": {"action": "play_card"}}),
    )
    gw = _Gw()
    result = ToolOrchestrator(reg, max_rounds=3, budget_s=5.0).run(gw, system="sys", user="user", table_name="sts2")
    assert gw.calls == 1
    assert result.parsed == {"tool": "play_card", "terminal": True}


def test_orchestrator_retries_empty_response() -> None:
    calls: list[dict] = [{}, {"choice": "Yes", "thought": "dismiss"}]

    class _Gw(VlmGateway):
        def __init__(self) -> None:
            super().__init__()
            self.i = 0

        def complete_text(self, **kwargs):  # type: ignore[no-untyped-def]
            del kwargs
            parsed = calls[min(self.i, len(calls) - 1)]
            self.i += 1
            return ModelCall(
                text=json.dumps(parsed),
                parsed=parsed,
                latency_ms=1.0,
                model="fake",
            )

    reg = ToolRegistry()
    orch = ToolOrchestrator(reg, max_rounds=3, budget_s=5.0)
    result = orch.run(_Gw(), system="sys", user="user", table_name="sts2")
    assert result.parsed.get("choice") == "Yes"
    assert result.rounds >= 2


def test_orchestrator_rejects_unknown_vision_tool() -> None:
    calls: list[dict] = [{"tool": "screen_perceive", "focus": "Got it"}, {"choice": "Got it"}]

    class _Gw(VlmGateway):
        def __init__(self) -> None:
            super().__init__()
            self.i = 0

        def complete_text(self, **kwargs):  # type: ignore[no-untyped-def]
            del kwargs
            parsed = calls[min(self.i, len(calls) - 1)]
            self.i += 1
            return ModelCall(
                text=json.dumps(parsed),
                parsed=parsed,
                latency_ms=1.0,
                model="fake",
            )

    reg = ToolRegistry()
    orch = ToolOrchestrator(reg, max_rounds=3, budget_s=5.0)
    result = orch.run(_Gw(), system="sys", user="user", table_name="sts2")
    assert "Unknown tool" in " ".join(result.tool_trace)


def test_orchestrator_registry_events_carry_mcp_kind() -> None:
    class _Gw(VlmGateway):
        def complete_text(self, **kwargs):  # type: ignore[no-untyped-def]
            del kwargs
            parsed = {"tool": "memory_notebook"}
            return ModelCall(
                text=json.dumps(parsed),
                parsed=parsed,
                latency_ms=1.0,
                model="fake",
            )

    reg = ToolRegistry()
    reg.register("memory_notebook", description="nb", parameters={}, handler=lambda **_: "ok")
    result = ToolOrchestrator(reg, max_rounds=2, budget_s=5.0).run(
        _Gw(), system="sys", user="user", table_name="sts2"
    )
    assert result.tool_events[0]["kind"] == "mcp_registry"
    assert result.tool_events[0]["tool"] == "memory_notebook"


def test_explicit_mcp_tools_exclude_collector_audits() -> None:
    from loop.log.store import registry_tool_names

    tool_events = [
        {"kind": "mcp_registry", "tool": "memory_notebook"},
        {"kind": "collector_audit", "tool": "targeting_validate", "status": "rejected"},
    ]
    explicit = registry_tool_names(tool_events)
    assert explicit == ["memory_notebook"]
    assert len(tool_events) > len(explicit)


def test_direct_action_has_empty_explicit_mcp_list() -> None:
    from loop.log.store import registry_tool_names

    audit_only = [
        {"kind": "collector_audit", "tool": "targeting_validate", "status": "rejected"},
    ]
    assert registry_tool_names(audit_only) == []
