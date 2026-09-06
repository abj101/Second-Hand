from __future__ import annotations

from loop.agent.perceive import SceneDescription, _compact, perceive_scene, validate_perceive_schema
from loop.agent.prompts import (
    allowed_controls_for_turn,
    allowed_sets_from_controls,
    choice_in_allowed_controls,
    format_host_hud_block,
    inspectable_targets_from_controls,
    paginated_tutorial_active,
    reason_controls_for_turn,
    reason_turn_user_prompt,
    strip_mark_leaks,
    validate_choice,
)
from loop.model.gateway import ModelCall, VlmGateway
from loop.os.aim import AimMark, action_from_labels
from loop.os.capture import EncodedFrame


def _frame() -> EncodedFrame:
    return EncodedFrame(
        data=b"abc",
        mime="image/jpeg",
        width=100,
        height=100,
        jpeg_quality=90,
        longest_side=100,
        original_width=100,
        original_height=100,
    )


class _FakeGateway(VlmGateway):
    def __init__(self, parsed: dict | None) -> None:
        super().__init__()
        self._parsed = parsed

    def complete_json(self, **kwargs):  # type: ignore[no-untyped-def]
        del kwargs
        return ModelCall(
            text='{"scene":"combat"}',
            parsed=self._parsed,
            latency_ms=1.0,
            model="fake",
        )


def test_scene_description_fallback() -> None:
    desc = SceneDescription.from_parsed(None, hud={"screen": "combat"})
    assert desc.fallback
    assert desc.screen == "combat"


def test_perceive_scene_fallback_uses_ocr() -> None:
    frame = _frame()
    gw = _FakeGateway(None)
    desc, call = perceive_scene(gw, frame, hud={"screen": "map", "ocr": "Continue Singleplayer"})
    assert desc.fallback
    assert "Continue" in desc.scene
    assert call.latency_ms == 1.0


def test_perceive_scene_expands_token_budget_after_truncation() -> None:
    valid = {
        "screen": "combat",
        "controls": [],
        "visible_text": [],
        "legal_actions": [],
            "unresolved": [],
            "hand_cards": [],
            "characters": [],
        "enemies": [],
    }

    class _TruncatedGateway:
        def __init__(self) -> None:
            self.budgets: list[int | None] = []

        def complete_json(self, **kwargs):  # type: ignore[no-untyped-def]
            self.budgets.append(kwargs["max_tokens"])
            if len(self.budgets) == 1:
                return ModelCall('{"screen":"combat"', None, 1.0, "fake")
            return ModelCall("{}", valid, 1.0, "fake")

    gateway = _TruncatedGateway()
    desc, _ = perceive_scene(gateway, _frame(), max_tokens=1536)  # type: ignore[arg-type]

    assert desc.schema_valid
    assert gateway.budgets == [1536, 3072]


def test_scene_description_controls() -> None:
    parsed = {
        "screen": "title",
        "controls": [{"label": "Continue", "role": "menu"}, {"label": "Singleplayer", "role": "menu"}],
        "dialog": {"prompt": "Enable tutorials?", "options": ["Yes", "No"]},
        "tutorial": {"active": False},
    }
    desc = SceneDescription.from_parsed(parsed)
    assert "Continue" in desc.control_labels()
    assert desc.dialog_options() == ["Yes", "No"]
    assert "Singleplayer" in desc.as_text()
    assert "screen=title" in desc.as_text()


def test_scene_description_v2_tutorial() -> None:
    parsed = {
        "screen": "tutorial",
        "tutorial": {"active": True, "title": "Combat Basics", "step": 1, "total": 3},
        "controls": [{"label": "Strike", "role": "card"}, {"label": "End Turn", "role": "button"}],
        "visible_text": ["Defeat enemies by playing cards from your hand!"],
    }
    desc = SceneDescription.from_parsed(parsed)
    assert desc.tutorial_text == "Combat Basics (1 of 3)"
    assert "Strike" in desc.control_labels()
    assert "46/46" not in desc.control_labels()


def test_reason_prompt_has_allowed_controls() -> None:
    controls = [
        {"label": "Strike", "role": "card", "confidence": "high"},
        {"label": "End Turn", "role": "button", "confidence": "high"},
    ]
    prompt = reason_turn_user_prompt(
        state={"screen": "tutorial", "turn": "ours"},
        hud_stats={"hp": "80/80", "energy": "3/3"},
        controls=controls,
        visible_text=["Defeat enemies by playing cards from your hand!"],
    )
    assert "Allowed controls (only legal 'choice' values" in prompt
    assert "End Turn" in prompt
    assert "Strike" in prompt
    assert "State: screen=tutorial turn=ours" in prompt
    assert "Stats:" in prompt
    assert "Visible text (context only, not clickable):" in prompt
    assert "Defeat enemies" in prompt
    assert "Scene transcript:" not in prompt


def test_reason_prompt_has_no_marks() -> None:
    controls = [
        {"label": "Yes", "role": "button", "confidence": "high"},
        {"label": "No", "role": "button", "confidence": "high"},
        {"label": "Strike", "role": "card", "confidence": "high"},
    ]
    prompt = reason_turn_user_prompt(
        state={
            "screen": "tutorial",
            "turn": "ours",
            "tutorial_title": "Combat Basics",
            "tutorial_step": 1,
            "tutorial_total": 3,
        },
        hud_stats={"hp": "80/80", "energy": "3/3"},
        controls=controls,
        journal="Journal:\n- ui|tutorial|mk_2|Strike → moved",
        last_outcome="choice='Back' → blocked",
    )
    assert "Marks:" not in prompt
    assert "mk_1" not in prompt
    assert "Yes" in prompt
    assert "Allowed controls (only legal 'choice' values" in prompt
    assert "tutorial=Combat Basics (1 of 3)" in prompt
    assert "Journal (strategy notes only" in prompt
    assert "Last outcome:" in prompt


def test_reason_prompt_tutorial_title_without_step() -> None:
    """Title-only tutorial metadata must not KeyError when step/total missing."""
    from loop.agent.prompts import reason_state_from_host

    state = reason_state_from_host(
        screen="tutorial",
        turn="ours",
        tutorial_info={"active": True, "title": "Combat Basics"},
    )
    prompt = reason_turn_user_prompt(
        state=state,
        hud_stats={"hp": "80/80"},
        controls=[{"label": "Strike", "role": "card", "confidence": "high"}],
    )
    assert "tutorial=Combat Basics" in prompt
    assert " of " not in prompt.split("tutorial=")[1].split("\n")[0]


def test_perceive_promotes_got_it_from_ocr() -> None:
    parsed = {
        "screen": "tutorial",
        "tutorial": {"active": True, "title": "Combat Basics", "step": 1, "total": 3},
        "controls": [{"label": "Strike", "role": "card"}],
        "visible_text": ["Defeat enemies by playing cards from your hand!"],
    }
    hud = {"ocr": "Combat Basics (1 of 3) Got it Strike End Turn 80/80"}
    desc = SceneDescription.from_parsed(parsed, hud=hud, clickable_labels=["Got it", "Strike"])
    labels = [c["label"] for c in desc.controls]
    assert "Got it" in labels
    assert desc.screen == "tutorial"


def test_perceive_does_not_promote_no_from_unknown() -> None:
    parsed = {
        "screen": "map",
        "controls": [],
        "visible_text": ["Legend"],
    }
    hud = {"ocr": "Act I Legend Unknown Merchant Enemy", "screen": "map"}
    desc = SceneDescription.from_parsed(parsed, hud=hud)
    labels = [c["label"] for c in desc.controls]
    assert "No" not in labels


def test_perceive_drops_ungrounded_confirm_on_tutorial() -> None:
    parsed = {
        "screen": "tutorial",
        "tutorial": {"active": True, "title": "Combat Basics", "step": 1, "total": 3},
        "controls": [
            {"label": "Confirm", "role": "button"},
            {"label": "Strike", "role": "card"},
        ],
        "visible_text": ["Defeat enemies by playing cards from your hand!"],
    }
    hud = {"ocr": "Combat Basics (1 of 3) Strike Defend End Turn", "screen": "tutorial"}
    desc = SceneDescription.from_parsed(
        parsed,
        hud=hud,
        host_stats={"screen": "tutorial", "tutorial": {"active": True, "title": "Combat Basics", "step": 1, "total": 3}},
        clickable_labels=["Back"],
    )
    labels = [c["label"] for c in desc.controls]
    assert "Confirm" not in labels
    assert "Strike" in labels


def test_perceive_filters_map_legend_controls() -> None:
    parsed = {
        "screen": "map",
        "controls": [
            {"label": "Unknown", "role": "button"},
            {"label": "Merchant", "role": "button"},
        ],
        "visible_text": ["Legend"],
    }
    hud = {"ocr": "Act I Legend Unknown Merchant", "screen": "map"}
    desc = SceneDescription.from_parsed(parsed, hud=hud, clickable_labels=[])
    labels = [c["label"] for c in desc.controls]
    assert "Unknown" not in labels
    assert "Merchant" not in labels


def test_perceive_filters_map_share_chrome() -> None:
    parsed = {
        "screen": "map",
        "controls": [{"label": "Share", "role": "button", "confidence": "high"}],
        "visible_text": ["Legend"],
    }
    hud = {"ocr": "Legend Unknown Merchant Share", "screen": "map"}
    desc = SceneDescription.from_parsed(parsed, hud=hud, clickable_labels=[])
    labels = [c["label"] for c in desc.controls]
    assert "Share" not in labels


def test_detector_candidates_from_marks_includes_nodes() -> None:
    from loop.agent.prompts import detector_candidates_from_marks
    from loop.os.aim import AimMark

    marks = [
        AimMark(id=1, x=0.3, y=0.5, x0=0.28, y0=0.48, x1=0.32, y1=0.52, label="node_0"),
        AimMark(id=2, x=0.4, y=0.5, x0=0.38, y0=0.48, x1=0.42, y1=0.52, label="icon"),
    ]
    rows = detector_candidates_from_marks(marks)
    labels = [row["label"] for row in rows]
    assert "node_0" in labels
    assert "icon" not in labels


def test_ocr_candidates_from_hud_filters_map_share() -> None:
    from loop.agent.prompts import _ocr_candidates_from_hud

    hud = {
        "ocr": "80/80 Legend Unknown Merchant Share v0.111.0 CS4BEJ50Z7X8",
        "screen": "map",
    }
    candidates = _ocr_candidates_from_hud(hud=hud, host_stats={"screen": "map"})
    joined = " ".join(candidates).lower()
    assert "share" not in joined
    assert "unknown" not in joined
    assert "80/80" not in joined


def test_scene_perceive_user_anchor_and_verified() -> None:
    from loop.agent.prompts import scene_perceive_user

    prompt = scene_perceive_user(
        hud={"hp": 80, "energy": 3},
        host_stats={
            "screen": "tutorial",
            "turn": "ours",
            "tutorial": {"active": True, "title": "Combat Basics", "step": 1, "total": 3},
        },
        clickable_labels=["Back", "Strike"],
    )
    assert "Verify and structure this exact frame" in prompt
    assert "Host anchor" in prompt
    assert "'screen': 'tutorial'" in prompt
    assert "Detector element candidates" in prompt
    assert "Strike" in prompt
    assert "Back" in prompt


def test_scene_perceive_system_includes_pass3_rules_and_few_shots() -> None:
    from loop.agent.prompts import (
        PERCEPTION_SCHEMA_VERSION,
        SCENE_PERCEIVE_FEW_SHOTS,
        scene_perceive_system,
    )

    system = scene_perceive_system()
    assert "MODALS AND DIMMED BACKGROUNDS" in system
    assert "ICON-ONLY CONTROLS" in system
    assert "FEW-SHOT EXAMPLES" in system
    assert '"step":2,"total":3' in system.replace(" ", "") or '"step": 2, "total": 3' in system
    assert "Paginated tutorial overlay" in system
    assert f"Schema version: {PERCEPTION_SCHEMA_VERSION}" in system
    assert all(not validate_perceive_schema(example["expected_output"]) for example in SCENE_PERCEIVE_FEW_SHOTS)


def test_observer_prompt_has_no_action_instruction() -> None:
    from loop.agent.prompts import SCENE_PERCEIVE_SYSTEM

    assert "Do not recommend, rank, select, click, target" in SCENE_PERCEIVE_SYSTEM


def test_reason_phase_prompts_are_separate_and_turn_alias_is_action_phase() -> None:
    from loop.agent.prompts import REASON_ACT_SYSTEM, REASON_EXPLORE_SYSTEM, REASON_TURN_SYSTEM

    assert REASON_TURN_SYSTEM == REASON_ACT_SYSTEM
    assert REASON_EXPLORE_SYSTEM != REASON_ACT_SYSTEM
    assert "Do not select a game control" in REASON_EXPLORE_SYSTEM
    assert '"tool":"inspect_object"' in REASON_EXPLORE_SYSTEM
    assert '"tool":"request_recheck"' in REASON_EXPLORE_SYSTEM
    assert "memory_notebook" not in REASON_EXPLORE_SYSTEM
    assert "Do not inspect, hover, call request_recheck" in REASON_ACT_SYSTEM
    assert '"tool":"request_recheck"' not in REASON_ACT_SYSTEM
    assert '"action":"inspect"' not in REASON_ACT_SYSTEM


def test_two_stage_prompt_id_includes_both_reason_phases() -> None:
    import hashlib

    from loop.agent.prompts import (
        NOTEBOOK_SCHEMA_VERSION,
        REASON_ACT_SYSTEM,
        REASON_EXPLORE_SYSTEM,
        prompt_id,
        scene_perceive_system,
    )

    text = scene_perceive_system() + REASON_EXPLORE_SYSTEM + REASON_ACT_SYSTEM
    text += "\n# notebook_schema=" + NOTEBOOK_SCHEMA_VERSION
    expected = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    assert prompt_id(arm="no_skill", pace="turn", architecture="two_stage") == expected


def test_perceive_strips_dimmed_controls_on_paginated_tutorial() -> None:
    parsed = {
        "screen": "combat",
        "tutorial": {"active": True, "title": "Combat Basics", "step": 2, "total": 3},
        "controls": [
            {"label": "Strike", "role": "card", "confidence": "high"},
            {"label": "End Turn", "role": "button", "confidence": "high"},
            {"label": "Prev", "role": "button", "confidence": "high"},
            {"label": "Next", "role": "button", "confidence": "high"},
        ],
        "visible_text": ["Cards require Energy to play."],
    }
    desc = SceneDescription.from_parsed(parsed)
    labels = [c["label"] for c in desc.controls]
    assert desc.screen == "tutorial"
    assert labels == ["Prev", "Next"]
    assert "Strike" not in labels
    assert "End Turn" not in labels


def test_format_host_hud_block_tutorial() -> None:
    block = format_host_hud_block(
        hud={"hp": 80, "energy": 3},
        host_stats={
            "screen": "tutorial",
            "turn": "ours",
            "player": {"hp": 80, "hp_max": 80, "energy": 3, "energy_max": 3},
            "tutorial": {"active": True, "title": "Combat Basics", "step": 1, "total": 3},
            "enemies": [{"hp": 46, "hp_max": 46}],
        },
    )
    assert "screen=tutorial" in block
    assert "energy=3" in block
    assert "Combat Basics" in block
    assert "enemies=" in block


def test_strip_mark_leaks() -> None:
    assert "mk_4" not in strip_mark_leaks("Avoid mk_4 — use Yes instead")


def test_paginated_tutorial_active() -> None:
    assert paginated_tutorial_active({"active": True, "step": 2, "total": 3})
    assert not paginated_tutorial_active({"active": True, "step": 3, "total": 3})
    assert not paginated_tutorial_active({"active": False, "step": 1, "total": 3})


def test_allowed_controls_for_turn_paginated_uses_detector() -> None:
    allowed, source = allowed_controls_for_turn(
        inventory=[
            {"label": "End Turn 1", "role": "button", "confidence": "high"},
            {"label": "Strike", "role": "card", "confidence": "high"},
            {"label": "Jaw Worm", "role": "enemy", "confidence": "high"},
        ],
        detector_rows=[
            {"label": "Arrow", "role": "nav_prev", "x": 0.1, "y": 0.5, "x0": 0.04, "y0": 0.44, "x1": 0.12, "y1": 0.56},
            {"label": "Next", "role": "nav_next", "x": 0.9, "y": 0.5, "x0": 0.88, "y0": 0.44, "x1": 0.96, "y1": 0.56},
            {"label": "Back", "role": "nav_prev", "x": 0.12, "y": 0.5, "x0": 0.08, "y0": 0.44, "x1": 0.16, "y1": 0.56},
        ],
        tutorial_info={"active": True, "title": "Combat Basics", "step": 2, "total": 3},
    )
    assert source == "detector"
    assert [row["label"] for row in allowed] == ["Arrow", "Back", "Next"]
    assert all(row.get("role") in {"nav_prev", "nav_next", "button"} for row in allowed)


def test_allowed_controls_for_turn_combat_uses_inventory() -> None:
    allowed, source = allowed_controls_for_turn(
        inventory=[
            {"label": "Strike", "role": "card", "confidence": "high"},
            {"label": "End Turn", "role": "button", "confidence": "high"},
            {"label": "Jaw Worm", "role": "enemy", "confidence": "high"},
        ],
        detector_rows=[{"label": "Arrow", "role": "nav_prev", "x": 0.1, "y": 0.5}],
        tutorial_info={"active": True, "title": "Combat Basics", "step": 3, "total": 3},
    )
    assert source == "inventory"
    assert [row["label"] for row in allowed] == ["End Turn"]


def test_allowed_controls_for_turn_rejects_non_control_roles() -> None:
    allowed, _source = allowed_controls_for_turn(
        inventory=[
            {"label": "Next", "role": "other", "confidence": "high"},
            {"label": "Confirm", "role": "card", "confidence": "high"},
            {"label": "End Turn", "role": "button", "confidence": "high"},
        ],
    )
    assert [row["label"] for row in allowed] == ["End Turn"]


def test_reason_prompt_detector_allowed_on_paginated_tutorial() -> None:
    controls = reason_controls_for_turn(
        controls=[{"label": "End Turn 1", "role": "button", "confidence": "high"}],
        detector_rows=[
            {"label": "Arrow", "role": "nav_prev", "x": 0.1, "y": 0.5, "x0": 0.04, "y0": 0.44, "x1": 0.12, "y1": 0.56},
            {"label": "Next", "role": "nav_next", "x": 0.9, "y": 0.5, "x0": 0.88, "y0": 0.44, "x1": 0.96, "y1": 0.56},
            {"label": "Back", "role": "nav_prev", "x": 0.12, "y": 0.5, "x0": 0.08, "y0": 0.44, "x1": 0.16, "y1": 0.56},
        ],
        tutorial_info={"active": True, "step": 2, "total": 3},
    )
    prompt = reason_turn_user_prompt(
        state={"screen": "tutorial", "turn": "ours"},
        hud_stats={},
        controls=controls,
    )
    assert "Arrow" in prompt
    assert "Allowed controls (only legal 'choice' values" in prompt


def test_reason_prompt_splits_targets() -> None:
    controls = [
        {"label": "Strike", "role": "card", "confidence": "high"},
        {"label": "Jaw Worm", "role": "enemy", "confidence": "high"},
        {"label": "Slime", "role": "enemy", "confidence": "low"},
    ]
    prompt = reason_turn_user_prompt(
        state={"screen": "combat", "turn": "ours"},
        hud_stats={"hp": "80/80"},
        controls=controls,
    )
    assert "Allowed targets (only legal 'target' values" in prompt
    assert "Jaw Worm" in prompt
    assert "Unconfirmed targets" in prompt
    assert "Slime" in prompt


def test_reason_prompt_preserves_duplicate_card_instances() -> None:
    controls = reason_controls_for_turn(
        controls=[
            {"id": "card_1", "label": "Strike", "role": "card", "confidence": "high"},
            {"id": "card_2", "label": "Strike", "role": "card", "confidence": "high"},
        ]
    )
    prompt = reason_turn_user_prompt(
        state={"screen": "combat", "turn": "ours"}, hud_stats={}, controls=controls
    )
    assert "card_1=Strike" in prompt
    assert "card_2=Strike" in prompt


def test_validate_choice_rejects_bad_target() -> None:
    retry_state: dict[str, int] = {"invalid_choice_count": 0}
    allowed_controls = {"Strike"}
    allowed_targets = {"Jaw Worm"}
    result = validate_choice(
        {"choice": "Strike", "target": "Slime"},
        allowed_controls,
        allowed_targets,
        retry_state,
    )
    assert result["type"] == "force_rescan"
    assert "Slime" in result["focus"]


def test_validate_choice_accepts_good_play() -> None:
    retry_state: dict[str, int] = {"invalid_choice_count": 0}
    result = validate_choice(
        {"choice": "Strike", "target": "Jaw Worm"},
        {"Strike"},
        {"Jaw Worm"},
        retry_state,
    )
    assert result == {"type": "click", "choice": "Strike", "target": "Jaw Worm"}


def test_allowed_sets_from_controls() -> None:
    controls = [
        {"label": "Strike", "role": "card", "confidence": "high"},
        {"label": "Jaw Worm", "role": "enemy", "confidence": "high"},
        {"label": "Slime", "role": "enemy", "confidence": "low"},
    ]
    allowed_controls, allowed_targets = allowed_sets_from_controls(controls)
    assert allowed_controls == set()
    assert allowed_targets == {"Jaw Worm"}


def test_choice_in_allowed_controls() -> None:
    allowed = ["End Turn 1", "Strike"]
    allowed_targets = ["Jaw Worm"]
    assert choice_in_allowed_controls("Strike", allowed)
    assert choice_in_allowed_controls("Strike", allowed, target="Jaw Worm", allowed_targets=allowed_targets)
    assert not choice_in_allowed_controls("Got it", allowed)
    assert not choice_in_allowed_controls("Next", allowed)
    assert not choice_in_allowed_controls(
        "Strike", allowed, target="Slime", allowed_targets=allowed_targets
    )
    assert choice_in_allowed_controls("", allowed)
    assert choice_in_allowed_controls("icon", [":icon"])
    assert choice_in_allowed_controls(":icon", ["icon"])


def test_validate_choice_accepts_colon_icon_alias() -> None:
    retry_state: dict[str, int] = {"invalid_choice_count": 0}
    result = validate_choice(
        {"choice": "icon"},
        {":icon"},
        set(),
        retry_state,
    )
    assert result == {"type": "click", "choice": "icon", "target": None}


def test_validate_choice_requires_target_for_attack_card() -> None:
    retry_state: dict[str, int] = {"invalid_choice_count": 0}
    result = validate_choice(
        {"choice": "Strike", "target": None},
        {"Strike"},
        {"Jaw Worm"},
        retry_state,
        attack_cards={"Strike"},
    )
    assert result["type"] == "force_rescan"
    assert "requires target" in result["reason"]


def test_validate_choice_selected_picks_enemy() -> None:
    retry_state: dict[str, int] = {"invalid_choice_count": 0}
    result = validate_choice(
        {"choice": "Jaw Worm"},
        {"Strike"},
        {"Jaw Worm"},
        retry_state,
        selected="Strike",
    )
    assert result == {"type": "click", "choice": "Jaw Worm", "target": "Jaw Worm"}


def test_validate_choice_accepts_inspect_action() -> None:
    retry_state: dict[str, int] = {"invalid_choice_count": 0}
    controls = [
        {"label": "Strike", "role": "card", "confidence": "high"},
        {
            "label": "unknown_character_1",
            "role": "unknown_character",
            "confidence": "low",
            "inspectable": "true",
            "bbox": [680, 560, 820, 720],
        },
    ]
    inspectable = inspectable_targets_from_controls(controls)
    assert "unknown_character_1" in inspectable
    result = validate_choice(
        {"action": "inspect", "target": "unknown_character_1"},
        {"Strike"},
        set(),
        retry_state,
        inspectable_targets=inspectable,
    )
    assert result == {"type": "inspect", "target": "unknown_character_1"}


def test_validate_choice_rejects_unknown_inspect_target() -> None:
    retry_state: dict[str, int] = {"invalid_choice_count": 0}
    result = validate_choice(
        {"action": "inspect", "target": "unknown_character_9"},
        {"Strike"},
        set(),
        retry_state,
        inspectable_targets={"unknown_character_1"},
    )
    assert result["type"] == "force_rescan"
    assert "unknown_character_9" in result["focus"]

def test_action_from_labels() -> None:
    marks = (
        AimMark(id=1, x=0.1, y=0.8, x0=0.0, y0=0.7, x1=0.2, y1=0.9, label="Yes"),
        AimMark(id=2, x=0.5, y=0.3, x0=0.4, y0=0.2, x1=0.6, y1=0.4, label="Slime"),
        AimMark(id=3, x=0.5, y=0.85, x0=0.4, y0=0.75, x1=0.6, y1=0.95, label="Strike"),
    )
    from loop.os.aim import AimGuide
    from PIL import Image

    guide = AimGuide.from_marks(Image.new("RGB", (100, 100)), marks)
    action = action_from_labels(guide, choice="Yes")
    assert action is not None
    assert action.mark == 1
    two = action_from_labels(guide, choice="Strike", target_label="Slime")
    assert two is not None
    assert two.target == 2


def test_validate_combat_perceive_requires_enemies() -> None:
    from loop.agent.perceive import validate_combat_perceive

    desc = SceneDescription(
        screen="combat",
        combat={"active": True},
        controls=[{"label": "Strike", "role": "card", "confidence": "high"}],
    )
    errors = validate_combat_perceive(desc, {"enemies": [{"hp": 44, "hp_max": 44}]})
    assert errors
    assert any("enemy" in e.lower() for e in errors)


def test_scene_description_typed_slots_merge() -> None:
    parsed = {
        "screen": "combat",
        "controls": [],
        "visible_text": [],
        "legal_actions": [],
        "unresolved": [],
        "combat": {"active": True},
        "hud": [{"label": "End Turn", "confidence": "high"}],
        "hand_cards": [{"label": "Strike", "confidence": "high"}],
        "enemies": [{"label": "Jaw Worm", "confidence": "high", "role": "enemy", "inspectable": False}],
        "characters": [],
        "overlays_active": [],
    }
    desc = SceneDescription.from_parsed(
        parsed,
        host_stats={"screen": "combat", "markers": ["Jaw Worm", "Strike", "End Turn"]},
    )
    assert any(c["label"] == "Jaw Worm" for c in desc.controls)
    assert desc.enemies[0]["label"] == "Jaw Worm"
    assert desc.hand_cards[0]["label"] == "Strike"


def test_unknown_sprite_preserved_with_null_name() -> None:
    parsed = {
        "screen": "combat",
        "controls": [],
        "visible_text": [],
        "legal_actions": [],
        "unresolved": [],
        "combat": {"active": True},
        "hud": [],
        "hand_cards": [],
        "characters": [
            {
                "label": None,
                "name": None,
                "name_source": "unresolved",
                "role": "unknown_character",
                "confidence": "low",
                "inspectable": True,
                "bbox": [680, 580, 820, 760],
            }
        ],
        "enemies": [
            {
                "name": None,
                "label": None,
                "name_source": "unresolved",
                "role": "unknown_character",
                "confidence": "low",
                "inspectable": True,
                "bbox": [680, 580, 820, 760],
            }
        ],
        "overlays_active": [],
    }
    assert not validate_perceive_schema(parsed)
    desc = SceneDescription.from_parsed(parsed)
    assert desc.characters[0]["role"] == "unknown_character"
    assert desc.characters[0]["label"] == "unknown_character"
    assert desc.characters[0]["bbox"] == "680,580,820,760"
    assert desc.enemies[0]["label"] == "unknown_character"


def test_perceive_scene_accepts_unknown_sprite_json() -> None:
    parsed = {
        "screen": "combat",
        "controls": [],
        "visible_text": [],
        "legal_actions": [],
        "unresolved": [],
        "combat": {"active": True},
        "hud": [],
        "hand_cards": [],
        "characters": [
            {
                "label": None,
                "name": None,
                "name_source": "unresolved",
                "role": "unknown_character",
                "confidence": "low",
                "inspectable": True,
                "bbox": [680, 580, 820, 760],
            }
        ],
        "enemies": [
            {
                "label": None,
                "name": None,
                "name_source": "unresolved",
                "role": "unknown_character",
                "confidence": "low",
                "inspectable": True,
                "bbox": [680, 580, 820, 760],
            }
        ],
        "overlays_active": [],
    }
    frame = _frame()
    gw = _FakeGateway(parsed)
    desc, call = perceive_scene(gw, frame, hud={"screen": "combat"})
    assert desc.schema_valid
    assert not desc.fallback
    assert any(c.get("role") == "unknown_character" for c in desc.characters)
    assert any(c.get("bbox") for c in desc.characters)
    assert call.parsed == parsed


def test_compact_strips_non_alphanumerics_like_aim() -> None:
    from loop.os.aim import _compact_label

    assert _compact(":icon") == _compact("icon")
    assert _compact(":icon") == _compact_label(":icon")
    assert _compact(":icon") == _compact_label("icon")
