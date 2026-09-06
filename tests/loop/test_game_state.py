from loop.os.frame_model import FrameModel, FrameObject
from loop.state.game_state import LegalAction, reconcile


def _combat_frame(*, selected=False):
    objects = [
        FrameObject(1, "Strike", "interactive", role="card", x=.30, y=.86, x0=.25, y0=.75, x1=.35, y1=.96),
        FrameObject(2, "Defend", "interactive", role="card", x=.42, y=.86, x0=.37, y0=.75, x1=.47, y1=.96),
        FrameObject(3, "Jaw Worm", "interactive", role="target", x=.55, y=.63, x0=.48, y0=.56, x1=.62, y1=.70),
        FrameObject(4, "End Turn", "interactive", role="button", x=.91, y=.70),
    ]
    return FrameModel(objects=objects, layout="hand_and_board", screen_hint="combat")


def _stats(**extra):
    result = {
        "screen": "combat", "activity": "combat", "turn": "ours", "ocr_confidence": "high",
        "player": {"hp": 80, "hp_max": 80, "block": 0, "energy": 3, "energy_max": 3},
        "enemies": [{"hp": 42, "hp_max": 42}],
    }
    result.update(extra)
    return result


def test_reconcile_builds_immutable_grounded_combat_state_and_legal_actions():
    state = reconcile(None, step=1, host_stats=_stats(), frame=_combat_frame())

    assert state.phase.value == "combat"
    assert state.underlying_activity.value == "combat"
    assert state.player.energy.value == 3
    assert state.player.energy.source == "host_stats"
    assert state.player.energy.last_confirmed_step == 1
    assert state.enemies[0].id == "enemy_1_1"
    assert state.enemies[0].targetable.value is True
    assert [card.id for card in state.hand] == ["card_0_1", "card_0_2"]
    assert {action.kind for action in state.legal_actions} == {"select_card", "end_turn"}
    try:
        state.step = 2
        assert False, "GameState must be immutable"
    except AttributeError:
        pass


def test_reconcile_preserves_stable_ids_and_only_turn_transition_resets_hand_epoch():
    first = reconcile(None, step=1, host_stats=_stats(), frame=_combat_frame())
    second = reconcile(first, step=2, host_stats=_stats(), frame=_combat_frame())
    assert second.enemies[0].id == first.enemies[0].id
    assert second.hand[0].id == first.hand[0].id
    theirs = reconcile(second, step=3, host_stats=_stats(turn="theirs"), frame=_combat_frame())
    next_turn = reconcile(theirs, step=4, host_stats=_stats(), frame=_combat_frame())
    assert next_turn.hand_epoch == first.hand_epoch + 1
    assert next_turn.hand[0].id == "card_1_1"


def test_unverified_claims_cannot_overwrite_sensor_state_and_unknowns_are_explicit():
    frame = FrameModel(objects=[FrameObject(9, "icon", "interactive", role="icon", x=.70, y=.40)], screen_hint="combat")
    state = reconcile(None, step=5, host_stats=_stats(player={"hp": 80, "energy": 3}), frame=frame, claims=["enemy is dead", "energy is 99"])
    assert state.player.energy.value == 3
    # OCR-only enemy evidence is retained, but cannot become a target without a bbox.
    assert state.enemies[0].hp.value == 42
    assert state.enemies[0].targetable.known is False
    assert state.unknown_objects[0].label.value == "icon"
    assert state.unknown_objects[0].bbox.known
    assert state.legal_actions[-1].kind == "inspect"


def test_generic_icons_in_hand_or_enemy_geometry_are_unknown_not_playable_or_targetable():
    frame = FrameModel(objects=[
        FrameObject(1, "icon", "interactive", role="card", x=.3, y=.85),
        FrameObject(2, ":Icon", "interactive", role="target", x=.5, y=.35),
    ], screen_hint="combat")
    state = reconcile(None, step=1, host_stats=_stats(), frame=frame)
    assert state.hand == ()
    assert not [enemy for enemy in state.enemies if enemy.targetable.value]
    assert len(state.unknown_objects) == 2
    assert {action.kind for action in state.legal_actions} == {"inspect"}


def test_anonymous_enemy_role_is_targetable_without_fabricating_name():
    frame = FrameModel(objects=[
        FrameObject(7, "unknown_enemy", "interactive", role="target", x=.5, y=.63, x0=.43, y0=.56, x1=.57, y1=.70),
    ], screen_hint="combat")
    state = reconcile(None, step=1, host_stats=_stats(), frame=frame)
    enemy = state.enemies[0]
    assert enemy.label.known is False
    assert enemy.targetable.value is True
    assert enemy.targetable.source == "host+frame"
    assert enemy.hp.value == 42


def test_anonymous_enemy_is_a_legal_selected_card_target():
    frame = FrameModel(objects=[
        FrameObject(1, "Strike", "interactive", role="card", x=.3, y=.85),
        FrameObject(7, "unidentified_enemy", "interactive", role="target", x=.5, y=.63, x0=.43, y0=.56, x1=.57, y1=.70),
    ], screen_hint="combat")
    first = reconcile(None, step=1, host_stats=_stats(), frame=frame)
    selected = reconcile(first, step=2, host_stats=_stats(), frame=frame, verified_selected_card_id="card_0_1")
    assert any(action.kind == "play_card" and action.target_id == "enemy_1_1" for action in selected.legal_actions)


def test_tutorial_is_overlay_over_combat_and_blocks_card_actions_until_dismissed():
    stats = _stats(screen="tutorial", tutorial={"active": True, "title": "Combat Basics", "step": 1})
    state = reconcile(None, step=1, host_stats=stats, frame=_combat_frame())
    assert state.phase.value == "tutorial"
    assert state.underlying_activity.value == "combat"
    assert state.overlays[0].value == "Combat Basics"
    assert [action.kind for action in state.legal_actions] == ["dismiss_overlay"]


def test_action_outcome_requires_observed_sensor_effect():
    initial = reconcile(None, step=1, host_stats=_stats(), frame=_combat_frame())
    action = LegalAction("select_card", card_id=initial.hand[0].id)
    no_effect = reconcile(initial, step=2, host_stats=_stats(), frame=_combat_frame(), executed_action=action, expected_effects=["energy_decreased"])
    assert no_effect.last_action.verified is False
    spent = reconcile(initial, step=2, host_stats=_stats(player={"hp": 80, "hp_max": 80, "block": 0, "energy": 2, "energy_max": 3}), frame=_combat_frame(), executed_action=action, expected_effects=["energy_decreased"])
    assert spent.last_action.verified is True
    assert spent.last_action.observed_effects == ("energy_decreased",)


def test_dispatched_surface_target_keeps_play_card_effect_expectations_across_id_namespaces():
    from loop.agent.loop import CollectAgent

    agent = CollectAgent.__new__(CollectAgent)
    agent._game_state = reconcile(None, step=1, host_stats=_stats(), frame=_combat_frame())

    action, expected = agent._state_action_from_labels("Strike", "enemy_1")

    assert action is not None
    assert action.kind == "play_card"
    assert action.target_id == "enemy_1"
    assert expected == ("energy_spent", "card_leaves_hand", "enemy_hp_changes")


def test_hp_ocr_and_sprite_same_column_are_one_sprite_enemy():
    frame = FrameModel(objects=[
        FrameObject(1, "Strike", "interactive", role="card", x=.3, y=.85),
        FrameObject(7, "unidentified_enemy", "interactive", role="target", x=.75, y=.35, x0=.70, y0=.32, x1=.80, y1=.38),
        FrameObject(10, "unknown_character_1", "interactive", role="unknown_character", x=.76, y=.64, x0=.70, y0=.56, x1=.82, y1=.72),
    ], screen_hint="combat")
    state = reconcile(None, step=1, host_stats=_stats(), frame=frame)
    located = [enemy for enemy in state.enemies if enemy.bbox.known]
    assert len(located) == 1
    box = located[0].bbox.value
    assert box is not None
    cy = (box[1] + box[3]) / 2
    assert cy >= 0.50
    assert located[0].label.known is False
