from __future__ import annotations

from PIL import Image

from loop.agent.prompts import system_for, turn_user_prompt
from loop.fixtures import make_fixture
from loop.fixtures.sts2 import Sts2Fixture
from loop.log.schema import Observation, StepLog, StepResult
from loop.os.tables import prompt_listing


def test_sts2_death_from_hud() -> None:
    from loop.reward.sts import extract_sts_hud, merge_sts_hud, score_sts

    dead = extract_sts_hud({"hp": 0, "hp_max": 80, "screen": "death", "choice": "continue"})
    assert dead["fail_screen"] is True
    assert dead["choice"] == "continue"
    merged = merge_sts_hud({"fail_screen": False, "ocr": "You Died at floor 3"}, dead)
    assert merged["fail_screen"] is True
    assert merged["health_frac"] == 0.0
    frame = Image.new("RGB", (64, 64), (20, 20, 20))
    scored = score_sts(frame)
    assert "hp" not in scored
    assert "gold" not in scored
    assert scored.get("fail_screen") is not True
    assert Sts2Fixture().terminal(scored, frame) == "alive"
    assert Sts2Fixture().terminal(merged, frame) == "fail"
    messy = extract_sts_hud({"screen": "You Died", "dead": "true", "choice": "Give Up"})
    assert messy["fail_screen"] is True
    assert messy["screen"] == "death"
    assert messy["choice"] == "Give Up"
    title = extract_sts_hud({"hp": 0, "hp_max": 0, "gold": 0, "floor": 0, "screen": "title", "dead": "true"})
    assert title.get("fail_screen") is not True
    assert Sts2Fixture().terminal(title, frame) == "alive"
    zero_hp = extract_sts_hud({"hp": 0, "hp_max": 80, "screen": "combat"})
    assert zero_hp["fail_screen"] is True
    vlm_dead = extract_sts_hud({"hp": 0, "hp_max": 80, "screen": "combat"})
    logged = dict(scored)
    choice = vlm_dead.get("choice")
    if isinstance(choice, str) and choice.strip():
        logged["choice"] = choice.strip()
    assert logged.get("fail_screen") is not True
    assert Sts2Fixture().terminal(logged, frame) == "alive"


def test_ocr_screen_markers() -> None:
    from loop.reward.sts import _screen_from_ocr

    assert _screen_from_ocr("click end turn to finish") == "combat"
    assert _screen_from_ocr("choose a relic") == "reward"
    assert _screen_from_ocr("act i the hollow") == "map"
    assert _screen_from_ocr("legend unknown merchant treasure elite") == "map"
    assert _screen_from_ocr("welcome to the merchant buy a card") == "shop"
    assert _screen_from_ocr("potion slot cards here are shuffled into your draw pile") == "tutorial"
    assert _screen_from_ocr("view upgrades you start each combat with all the cards in here end turn") == "tutorial"
    assert _screen_from_ocr("combat basics (2 of 3) end turn") == "tutorial"
    assert _screen_from_ocr("got it map tip") == "tutorial"
    assert _screen_from_ocr("paused resume give up") == "pause"
    assert _screen_from_ocr("resume settings give up save and quit") == "pause"
    assert _screen_from_ocr("no ui copy here") is None
    select = (
        "the ironclad 80/80 99 burning blood at the end of combat, heal 6 hp "
        "the last soldier of the ironclads. crushes foes with sword and flame against his will."
    )
    assert _screen_from_ocr(select) == "select"
    assert _screen_from_ocr(select + " end turn") == "combat"
    sts2_locked = (
        "locked 7/7 play a single run with ??? to unlock this character. "
        "unknown relic this character's relic is yet to be discovered."
    )
    assert _screen_from_ocr(sts2_locked) == "select"


def test_parse_turn_gate_and_stale_to_act() -> None:
    from loop.reward.sts import parse_turn_gate, stale_to_act

    assert parse_turn_gate("End Turn") == "ours"
    assert parse_turn_gate("END TURN 3") == "ours"
    assert parse_turn_gate("Enemy Turn") == "theirs"
    assert parse_turn_gate("enemy turn") == "theirs"
    assert parse_turn_gate("click end turn to finish") == "ours"
    assert parse_turn_gate("") == "unknown"
    assert parse_turn_gate("Continue") == "unknown"
    assert stale_to_act(obs_gate="ours", now_gate="theirs", mad=0.01) == "enemy-turn"
    assert stale_to_act(obs_gate="theirs", now_gate="ours", mad=0.01) == "turn-changed"
    assert stale_to_act(obs_gate="unknown", now_gate="ours", mad=0.12) == "motion"
    assert stale_to_act(obs_gate="ours", now_gate="ours", mad=0.12) == "motion"
    assert stale_to_act(obs_gate="ours", now_gate="ours", mad=0.02) is None
    assert stale_to_act(obs_gate="unknown", now_gate="unknown", mad=0.12) is None


def test_sts2_act_gate_blank_is_unknown() -> None:
    frame = Image.new("RGB", (8, 8), (20, 20, 20))
    assert Sts2Fixture().act_gate(frame) == "unknown"


def test_pause_give_up_is_not_death() -> None:
    from loop.reward.sts import _fail_from_ocr, extract_sts_hud

    assert _fail_from_ocr("paused resume give up save and quit") is False
    assert _fail_from_ocr("resume settings give up") is False
    assert _fail_from_ocr("you died") is True
    assert _fail_from_ocr("the enemy was slain") is True
    paused = extract_sts_hud({"screen": "pause", "choice": "Give Up"})
    assert paused.get("fail_screen") is not True
    assert paused["screen"] == "pause"


def test_sts2_fixture_is_turn_based() -> None:
    fx = make_fixture("sts2", attach=False)
    assert isinstance(fx, Sts2Fixture)
    assert fx.id == "sts2"
    assert fx.pace == "turn"
    assert fx.device == "mouse"
    assert fx.action_table == "sts"
    assert fx.settle_ms > 0
    assert fx.quiet_timeout_ms >= 8000
    frame = Image.new("RGB", (64, 64), (20, 20, 20))
    metrics = fx.scorer(frame)
    assert fx.terminal(metrics, frame) == "alive"
    assert "h1" in prompt_listing("sts")


def test_sts2_success_on_pixel_change() -> None:
    fx = Sts2Fixture()
    step = StepLog(
        session_id="s",
        episode_id="e",
        step_id=1,
        ts="t",
        fixture="sts2",
        observation=Observation(screenshot_ref="a"),
        result=StepResult(pixel_changed=True),
    )
    assert fx.success([step], "win") is True
    assert fx.success([step], "fail") is False
    assert fx.success([step], "timeout") is False
    assert fx.success([step], "alive") is False
    combat = StepLog(
        session_id="s",
        episode_id="e",
        step_id=2,
        ts="t",
        fixture="sts2",
        observation=Observation(screenshot_ref="a"),
        result=StepResult(pixel_changed=True),
        metrics={"screen": "combat", "floor": 1},
    )
    title = step.model_copy(update={"metrics": {"screen": "title", "floor": 0}})
    assert fx.success([title, combat], "timeout") is True


def test_turn_prompts_ask_for_named_action() -> None:
    text = system_for("no_skill", pace="turn")
    assert '"mark": 4' in text
    assert "gx" in text
    assert "stick_l" not in text
    assert '"goal"' in text
    assert '"note"' in text
    assert '"tool":' not in text
    assert "notebook" in text.lower()
    assert "host See" in text
    assert "do not re-emit hp, energy, or screen" in text
    assert "spend energy then End Turn" not in text
    assert '"target": 7' not in text
    assert "gx2" not in text
    assert "character select" not in text.lower()
    assert "Singleplayer" in text
    user = turn_user_prompt(actions="h1, m1, enter")
    assert "enter" in user
    assert "mark" in user
    assert "gx" not in user or "schema example" in user
    assert "stick_l" not in user
    assert 'Prefer {"thought": "...", "mark": 4}' not in user
    assert "schema example" in user
    assert "Memory tools are off" not in user
    ratchet = system_for("ratchet", pace="turn")
    assert '"tool": "recall"' in ratchet
    dump = system_for("dump_all", pace="turn")
    assert "Tools: recall, remember" in dump
    assert "hover" in dump
    assert "Win the run" in dump
    assert "Each step tests one control" not in dump
    assert "spend energy then End Turn" not in dump
    assert '"target": 7' not in dump
    user = turn_user_prompt(actions="enter")
    assert "JSON goal is this-step move" in user
    assert "Notebook Goal:" in user
    assert "See: screen=" in user
    combat = turn_user_prompt(
        actions="enter",
        screen="combat",
        hud={"screen": "combat", "energy": 3, "energy_max": 3, "hp": 80, "hp_max": 80},
        aim_listing="3@400,850:Strike 8@820,880:End_Turn",
    )
    assert "See: screen=combat" in combat
    assert "energy=3" in combat
    assert "hp=80" in combat
    assert "click a hand card" not in combat
    assert "End Turn only" not in combat
    selected = turn_user_prompt(
        actions="enter",
        screen="combat",
        hud={"screen": "combat", "energy": 3, "energy_max": 3, "hp": 80, "hp_max": 80},
        selected="Strike",
        aim_listing="3@400,850:Strike 8@820,880:End_Turn",
    )
    assert "SELECTED: Strike" in selected
    pause = turn_user_prompt(actions="enter", screen="pause")
    assert "Resume" in pause
    assert "Give Up" in pause
    title = turn_user_prompt(actions="enter", screen="title")
    assert "Singleplayer" in title or "Continue" in title
    mmap = turn_user_prompt(actions="enter", screen="map")
    assert "Legend" in mmap
    still_menu = turn_user_prompt(actions="enter", last_mad=0.005)
    assert "Almost still" in still_menu
    no_change = turn_user_prompt(actions="enter", last_pixels_changed=False)
    assert "did not change pixels" in no_change
    locked = turn_user_prompt(
        actions="enter",
        screen="select",
        last_mad=0.005,
        aim_listing="13@440,880:Locked 14@160,500:Play 6@960,724:Confirm",
    )
    assert "Locked portraits are not cards" in locked or "not playable" in locked.lower()
    dialog = turn_user_prompt(
        actions="enter",
        last_mad=0.005,
        aim_listing="5@321,862:No 6@540,858:Yes 15@476,634:icon",
    )
    assert "Tutorial:" in dialog
    assert ":Yes" in dialog
    assert "choice must copy" in dialog
    assert "mk_N is Label" in dialog
    assert "Control scheme is Marks" in dialog
    assert "off-scheme" in dialog
    assert "Keys (only if needed)" not in dialog
    inspect = turn_user_prompt(
        actions="enter",
        last_mad=0.005,
        aim_listing="1@80,500:icon 2@920,500:icon 3@500,160:Close",
    )
    assert "Magnified card" not in inspect
    assert "Side arrows only page" not in inspect
    assert "Unlabeled Marks" in inspect
    assert "hover once" in inspect
    backdrop = turn_user_prompt(
        actions="enter",
        last_mad=0.005,
        aim_listing="3@500,160:Close",
    )
    assert "Magnified card" in backdrop
    assert ":Close" in backdrop
    assert "darkened" in backdrop
    still_sel = turn_user_prompt(
        actions="enter",
        screen="combat",
        selected="Strike",
        last_pixels_changed=False,
        last_mad=0.005,
    )
    assert "SELECTED: Strike" in still_sel
    assert "creature" in still_sel.lower()
    retrieved = turn_user_prompt(
        actions="enter",
        arm="dump_all",
        allow_memory=True,
        recall_hits=["map heading=g400,g700 tried=pt_0.40_0.70 blocked"],
        journal_block="Journal:\n- map heading=g400,g700 tried=pt_0.40_0.70 blocked",
    )
    assert "Retrieved:" in retrieved
    assert "Tools: recall, remember" in retrieved
    assert "Journal:" in retrieved
    assert "pt_0.40_0.70" in retrieved
    no_skill = turn_user_prompt(
        actions="enter",
        arm="no_skill",
        notebook_block="Notebook:\nGoal: start a run",
    )
    assert "Notebook:" in no_skill
    assert "Memory tools are off this step." not in no_skill
    assert "recall" not in no_skill.lower()


def test_retrieved_facts_uses_last_click_cell(tmp_path: Path) -> None:
    from loop.agent.loop import CollectAgent, CollectConfig
    from loop.log.store import EpisodeStore

    fx = Sts2Fixture(controller=None)
    agent = CollectAgent(
        fx,
        EpisodeStore(tmp_path),
        config=CollectConfig(skip_models=True, arm="no_skill"),
    )
    from loop.memory.journal import FactJournal
    from loop.memory.notebook import Notebook

    agent.journal = FactJournal(path=tmp_path / "journal.json")
    agent.notebook = Notebook(agent.journal)
    agent.journal.record_click(
        screen="map",
        x=0.40,
        y=0.70,
        device="pt_0.40_0.70",
        pixel_changed=False,
        step_id=1,
    )
    agent.journal.record_click(
        screen="map",
        x=0.40,
        y=0.70,
        device="pt_0.40_0.70",
        pixel_changed=False,
        step_id=2,
    )
    agent._last_click = (0.41, 0.69)
    hits = agent._retrieved_facts(screen="map")
    assert hits
    assert "blocked" in hits[0]
    assert "map" in hits[0]


def test_repeat_hover_promotes_to_click(tmp_path: Path) -> None:
    from loop.agent.loop import CollectAgent, CollectConfig
    from loop.log.schema import ActionLog
    from loop.log.store import EpisodeStore
    from loop.os.aim import AimGuide, AimMark

    agent = CollectAgent(
        Sts2Fixture(controller=None),
        EpisodeStore(tmp_path),
        config=CollectConfig(skip_models=True, arm="no_skill"),
    )
    first = agent._promote_repeat_hover(ActionLog(hover=True, mark=1, click_x=0.4, click_y=0.68))
    assert first.hover is True
    second = agent._promote_repeat_hover(ActionLog(hover=True, mark=1, click_x=0.4, click_y=0.68))
    assert second.hover is False
    assert second.derive_name() == "mk_1"

    agent._last_hover_key = None
    agent._aim = AimGuide(
        marks=(
            AimMark(id=1, x=0.4, y=0.68, x0=0.3, y0=0.66, x1=0.5, y1=0.70, label="Continue"),
            AimMark(id=6, x=0.4, y=0.87, x0=0.3, y0=0.85, x1=0.5, y1=0.89, label="Quit"),
        ),
        last_xy=None,
        image=Image.new("RGB", (8, 8), (0, 0, 0)),
    )
    labeled = agent._promote_repeat_hover(ActionLog(hover=True, mark=1, click_x=0.4, click_y=0.68))
    assert labeled.hover is False
    quit_once = agent._promote_repeat_hover(ActionLog(hover=True, mark=6, click_x=0.4, click_y=0.87))
    assert quit_once.hover is True
    quit_twice = agent._promote_repeat_hover(ActionLog(hover=True, mark=6, click_x=0.4, click_y=0.87))
    assert quit_twice.hover is True


def test_letterbox_maps_clicks_into_content() -> None:
    from loop.fixtures.sts2 import letterbox_frac, map_content_xy

    frame = Image.new("RGB", (200, 200), (0, 0, 0))
    frame.paste(Image.new("RGB", (200, 100), (40, 40, 40)), (0, 50))
    box = letterbox_frac(frame)
    assert box[1] == 0.25
    assert box[3] == 0.75
    _, y = map_content_xy(0.5, 1.0, box)
    assert abs(y - 0.75) < 1e-6
    _, y0 = map_content_xy(0.5, 0.0, box)
    assert abs(y0 - 0.25) < 1e-6
    cropped = Sts2Fixture(controller=None).observe_image(frame)
    assert cropped.size[1] == 100


def test_content_box_snaps_tall_client_to_16x9() -> None:
    from loop.fixtures.sts2 import (
        content_box_for,
        fit_aspect_box,
        geometry_error,
        map_content_xy,
    )

    wide = Image.new("RGB", (1280, 720), (40, 20, 20))
    assert content_box_for(wide) == (0.0, 0.0, 1.0, 1.0)
    assert geometry_error(wide) is None
    tall = Image.new("RGB", (1278, 1360), (40, 20, 20))
    box = content_box_for(tall)
    assert box[0] == 0.0 and box[2] == 1.0
    assert 0.20 < box[1] < 0.28
    assert 0.72 < box[3] < 0.80
    _, y = map_content_xy(0.5, 1.0, box)
    assert abs(y - box[3]) < 1e-6
    cropped = Sts2Fixture(controller=None).observe_image(tall)
    assert abs(cropped.size[0] / cropped.size[1] - 16 / 9) < 0.02
    assert geometry_error(tall) is not None
    assert "16:9" in geometry_error(tall)
    fx = Sts2Fixture(controller=None)
    try:
        fx.check_geometry(tall)
        raise AssertionError("expected RuntimeError")
    except RuntimeError as exc:
        assert "16:9" in str(exc)
    fx.check_geometry(wide)
    fitted = fit_aspect_box(1278, 1360)
    assert fitted == box


def test_point_click_remaps_through_letterbox() -> None:
    from loop.agent.loop import format_chord
    from loop.log.schema import ActionLog
    from loop.os.primitives import mouse_click

    fx = Sts2Fixture(controller=None)
    fx._content_box = (0.0, 0.25, 1.0, 0.75)
    remapped = fx._remap(mouse_click(0.5, 1.0))
    assert remapped.x is not None and abs(remapped.x - 0.5) < 1e-6
    assert remapped.y is not None and abs(remapped.y - 0.75) < 1e-6
    action = ActionLog(click_x=0.52, click_y=0.41, duration_ms=400)
    assert "xy=0.52,0.41" in format_chord(action)
    assert action.derive_name() == "pt_0.52_0.41"
    hovered = ActionLog(click_x=0.50, click_y=0.20, hover=True, mark=6)
    assert "hover" in format_chord(hovered)
    assert hovered.derive_name() == "hv_6"
    from loop.os.primitives import mouse_move

    moved = fx._remap(mouse_move(0.5, 1.0))
    assert moved.op == "move"
    assert moved.y is not None and abs(moved.y - 0.75) < 1e-6


def test_two_click_execute_with_settle(monkeypatch) -> None:
    from loop.log.schema import ActionLog

    sleeps: list[float] = []
    monkeypatch.setattr("loop.fixtures.sts2.time.sleep", lambda s: sleeps.append(s))

    class FakeController:
        def __init__(self) -> None:
            self.prims: list[object] = []

        def execute(self, prims) -> None:
            self.prims.extend(prims)

        def release_all(self) -> None:
            return None

    fx = Sts2Fixture(controller=FakeController())  # type: ignore[arg-type]
    fx._content_box = (0.0, 0.0, 1.0, 1.0)
    fx.settle_ms = 50
    action = ActionLog(
        mark=3,
        click_x=0.40,
        click_y=0.85,
        target=7,
        click_x2=0.55,
        click_y2=0.30,
    )
    sent = fx.execute_chord(action)
    assert len(fx.controller.prims) == 1  # type: ignore[attr-defined]
    assert action.derive_name() == "mk_3>mk_7"
    assert sent == [action.derive_name()]
    assert sleeps


def test_execute_chord_can_skip_post_input_settle(monkeypatch) -> None:
    from loop.log.schema import ActionLog

    sleeps: list[float] = []
    monkeypatch.setattr("loop.fixtures.sts2.time.sleep", lambda s: sleeps.append(s))

    class FakeController:
        def __init__(self) -> None:
            self.prims: list[object] = []

        def execute(self, prims) -> None:
            self.prims.extend(prims)

        def release_all(self) -> None:
            return None

    fx = Sts2Fixture(controller=FakeController())  # type: ignore[arg-type]
    fx._content_box = (0.0, 0.0, 1.0, 1.0)
    fx.settle_ms = 50

    sent = fx.execute_chord(
        ActionLog(mark=3, click_x=0.40, click_y=0.85),
        settle=False,
    )

    assert sent == ["mk_3"]
    assert len(fx.controller.prims) == 1  # type: ignore[attr-defined]
    assert sleeps == []


def test_execute_chord_settle_keyword_defaults_to_legacy_behavior(monkeypatch) -> None:
    from loop.log.schema import ActionLog

    sleeps: list[float] = []
    monkeypatch.setattr("loop.fixtures.sts2.time.sleep", lambda s: sleeps.append(s))

    class FakeController:
        def execute(self, prims) -> None:
            del prims

        def release_all(self) -> None:
            return None

    fx = Sts2Fixture(controller=FakeController())  # type: ignore[arg-type]
    fx._content_box = (0.0, 0.0, 1.0, 1.0)
    fx.settle_ms = 50

    fx.execute_chord(ActionLog(mark=3, click_x=0.40, click_y=0.85))

    assert sleeps == [0.05]


def test_targetless_card_drag_is_one_unsettled_primitive() -> None:
    from loop.log.schema import ActionLog
    from loop.os.primitives import MousePrimitive

    class FakeController:
        def __init__(self) -> None:
            self.prims: list[object] = []

        def execute(self, prims) -> None:
            self.prims.extend(prims)

        def release_all(self) -> None:
            return None

    fx = Sts2Fixture(controller=FakeController())  # type: ignore[arg-type]
    fx._content_box = (0.0, 0.0, 1.0, 1.0)
    sent = fx.execute_card_drag(ActionLog(mark=3, click_x=.40, click_y=.85))

    assert sent == ["mk_3"]
    assert len(fx.controller.prims) == 1  # type: ignore[attr-defined]
    primitive = fx.controller.prims[0]  # type: ignore[attr-defined]
    assert isinstance(primitive, MousePrimitive)
    assert primitive.op == "drag"
    assert primitive.x2 == .40
    assert primitive.y2 == .55


def test_menu_confirm_uses_longer_hold() -> None:
    from loop.log.schema import ActionLog
    from loop.os.mouse import MENU_HOLD_S, MENU_HOVER_S
    from loop.os.primitives import MousePrimitive

    class FakeController:
        def __init__(self) -> None:
            self.prims: list[object] = []

        def execute(self, prims) -> None:
            self.prims.extend(prims)

        def release_all(self) -> None:
            return None

    fx = Sts2Fixture(controller=FakeController())  # type: ignore[arg-type]
    fx._content_box = (0.0, 0.0, 1.0, 1.0)
    fx.settle_ms = 0
    fx.pending_aim_label = "Confirm"
    fx.execute_chord(ActionLog(mark=1, click_x=0.94, click_y=0.84))
    menu = fx.controller.prims[0]  # type: ignore[attr-defined]
    assert isinstance(menu, MousePrimitive)
    assert menu.hover_s == MENU_HOVER_S
    assert menu.hold_s == MENU_HOLD_S
    fx.pending_aim_label = "Strike"
    fx.execute_chord(ActionLog(mark=3, click_x=0.40, click_y=0.85))
    combat = fx.controller.prims[1]  # type: ignore[attr-defined]
    assert isinstance(combat, MousePrimitive)
    assert combat.hover_s is None
    assert combat.hold_s is None


def test_selected_card_and_aim_label(tmp_path) -> None:
    from loop.agent.loop import CollectAgent, CollectConfig
    from loop.log.schema import ActionLog
    from loop.log.store import EpisodeStore
    from loop.os.aim import AimGuide, AimMark

    agent = CollectAgent(
        Sts2Fixture(controller=None),
        EpisodeStore(tmp_path),
        config=CollectConfig(skip_models=True, arm="no_skill"),
    )
    agent._aim = AimGuide(
        marks=(
            AimMark(id=3, x=0.4, y=0.85, x0=0.3, y0=0.82, x1=0.5, y1=0.90, label="Strike"),
            AimMark(id=7, x=0.55, y=0.30, x0=0.5, y0=0.25, x1=0.6, y1=0.35, label="Cultist", role="enemy"),
        ),
        last_xy=None,
        image=Image.new("RGB", (8, 8), (0, 0, 0)),
    )
    assert agent._aim_label(ActionLog(mark=3)) == "Strike"
    agent._update_selected(
        ActionLog(mark=3, click_x=0.4, click_y=0.85),
        metrics={"screen": "combat", "choice": "", "energy": 3},
        prev_metrics={"screen": "combat", "energy": 3},
        pixel_mad=0.12,
        pixel_changed=True,
    )
    assert agent._selected is None  # hand position alone is not selection evidence
    agent._update_selected(
        ActionLog(mark=3, click_x=0.4, click_y=0.85),
        metrics={"screen": "combat", "choice": "", "energy": 3},
        prev_metrics={"screen": "combat", "energy": 3},
        pixel_mad=0.008,
        pixel_changed=False,
    )
    assert agent._selected is None
    agent._update_selected(
        ActionLog(mark=3, click_x=0.4, click_y=0.85, target=7, click_x2=0.55, click_y2=0.30),
        metrics={"screen": "combat", "energy": 3},
        prev_metrics={"screen": "combat", "energy": 3},
        pixel_mad=0.04,
        pixel_changed=True,
    )
    assert agent._selected is None
    agent._update_selected(
        ActionLog(mark=3, click_x=0.4, click_y=0.85),
        metrics={"screen": "combat", "choice": "", "energy": 3},
        prev_metrics={"screen": "combat", "energy": 3},
        pixel_mad=0.12,
        pixel_changed=True,
        selection_state="confirmed",
    )
    assert agent._selected == "Strike"
    agent._update_selected(
        ActionLog(mark=7, click_x=0.55, click_y=0.30),
        metrics={"screen": "combat", "energy": 3},
        prev_metrics={"screen": "combat", "energy": 3},
        pixel_mad=0.04,
        pixel_changed=True,
    )
    assert agent._selected is None
    agent._update_selected(
        ActionLog(mark=3, click_x=0.4, click_y=0.85, target=7, click_x2=0.55, click_y2=0.30),
        metrics={"screen": "combat", "energy": 2, "block": 5},
        prev_metrics={"screen": "combat", "energy": 3, "block": 0},
        pixel_mad=0.04,
        pixel_changed=True,
    )
    assert agent._selected is None
    agent._update_selected(
        ActionLog(mark=4, click_x=0.53, click_y=0.79),
        metrics={"screen": "select", "choice": "Ironclad"},
        prev_metrics={"screen": "select"},
        pixel_mad=0.006,
        pixel_changed=False,
    )
    assert agent._selected is None
    agent._selected = "Strike"
    agent._sync_selected_for_screen("map")
    assert agent._selected is None


def test_non_combat_enemy_action_guard(tmp_path) -> None:
    from loop.agent.loop import CollectAgent, CollectConfig
    from loop.log.schema import ActionLog
    from loop.log.store import EpisodeStore
    from loop.os.aim import AimGuide, AimMark

    agent = CollectAgent(
        Sts2Fixture(controller=None),
        EpisodeStore(tmp_path),
        config=CollectConfig(skip_models=True, arm="no_skill"),
    )
    agent._selected = "Strike"
    agent._obs_metrics = {"screen": "map"}
    agent._host_stats = {"screen": "map"}
    action = ActionLog(mark=7, click_x=0.55, click_y=0.30)
    assert agent._guard_non_combat_enemy_action(action, screen="map")
    assert agent._selected is None
    assert agent._force_perceive is True


def test_non_combat_upper_menu_click_is_not_an_enemy_target(tmp_path) -> None:
    from loop.agent.loop import CollectAgent, CollectConfig
    from loop.log.schema import ActionLog
    from loop.log.store import EpisodeStore

    agent = CollectAgent(
        Sts2Fixture(controller=None),
        EpisodeStore(tmp_path),
        config=CollectConfig(skip_models=True, arm="no_skill"),
    )
    agent._obs_metrics = {"screen": "select"}
    agent._host_stats = {"screen": "select"}

    action = ActionLog(mark=2, click_x=0.90, click_y=0.37)
    assert not agent._guard_non_combat_enemy_action(action, screen="select")


def test_sandbox_turn_reward_inequalities() -> None:
    from loop.reward.sts import sandbox_turn_reward

    ok, reason = sandbox_turn_reward()
    assert ok, reason


def test_character_select_ocr_does_not_mint_combat_hud(monkeypatch) -> None:
    from loop.reward import sts

    blob = (
        "The Ironclad 80/80 99 Burning Blood. At the end of combat, heal 6 HP. "
        "The last soldier of the Ironclads. Crushes foes with sword and flame against his will."
    )
    monkeypatch.setattr(sts, "_ocr_full", lambda _image: blob)
    scored = sts.score_sts(Image.new("RGB", (64, 64), (20, 20, 20)))
    assert scored.get("screen") == "select"
    assert "hp" not in scored
    assert "gold" not in scored
    assert "energy" not in scored
    monkeypatch.setattr(sts, "_ocr_full", lambda _image: "End Turn 80/80 energy 3/3")
    combat = sts.score_sts(Image.new("RGB", (64, 64), (20, 20, 20)))
    assert combat.get("screen") == "combat"
    assert combat.get("hp") == 80
    assert combat.get("hp_max") == 80


def test_sts2_locked_panel_is_select_not_combat_hud(monkeypatch) -> None:
    from loop.reward import sts

    blob = (
        "Locked 7/7 Play a single run with ??? to unlock this character. "
        "Unknown Relic. This character's relic is yet to be discovered."
    )
    monkeypatch.setattr(sts, "_ocr_full", lambda _image: blob)
    scored = sts.score_sts(Image.new("RGB", (64, 64), (20, 20, 20)))
    assert scored.get("screen") == "select"
    assert "hp" not in scored
    assert "gold" not in scored


def test_vlm_energy_without_ocr_does_not_mint_combat() -> None:
    from loop.log.schema import ActionLog, StepResult
    from loop.reward.sts import extract_sts_hud, score_sts, turn_reward

    frame = Image.new("RGB", (64, 64), (20, 20, 20))
    ocr = score_sts(frame)
    assert "energy" not in ocr
    vlm = extract_sts_hud(
        {"energy": 2, "energy_max": 3, "hp": 80, "hp_max": 80, "screen": "combat", "choice": "Strike"}
    )
    assert vlm.get("energy") == 2
    logged = dict(ocr)
    choice = vlm.get("choice")
    if isinstance(choice, str) and choice.strip():
        logged["choice"] = choice.strip()
    assert "energy" not in logged
    assert logged.get("screen") != "combat" or "energy" not in logged
    prev = {"screen": "combat", "energy": 3, "block": 0, "hp": 80}
    action = ActionLog(
        mark=3, click_x=0.40, click_y=0.85, target=7, click_x2=0.55, click_y2=0.30
    )
    result = StepResult(pixel_changed=True, executed_as_proposed=True)
    filled = dict(logged)
    filled["energy"] = 2
    filled["screen"] = "combat"
    assert turn_reward(prev, filled, action, result) > 0
    assert turn_reward(prev, logged, action, result) <= 0


def test_sandbox_frozen_score() -> None:
    from loop.reward.frozen_score import sandbox_frozen_score

    ok, reason = sandbox_frozen_score()
    assert ok, reason


def test_energy_ocr_pair() -> None:
    from loop.reward.sts import _energy_pair

    assert _energy_pair("energy 2/3", hp_pair=(80, 80)) == (2, 3)
    assert _energy_pair("3/10 80/80", hp_pair=(80, 80)) == (3, 10)
    assert _energy_pair("energy 0/3", hp_pair=None) == (0, 3)
    assert _energy_pair("80/80", hp_pair=(80, 80)) is None


def test_unclassified_ocr_does_not_mint_hp(monkeypatch) -> None:
    from loop.reward import sts

    monkeypatch.setattr(sts, "_ocr_full", lambda _image: "62/80 @99 2:21:05")
    scored = sts.score_sts(Image.new("RGB", (64, 64), (20, 20, 20)))
    assert scored.get("screen") is None
    assert "hp" not in scored
    assert "energy" not in scored
    assert "gold" not in scored


def test_map_legend_ocr_does_not_mint_combat_hp(monkeypatch) -> None:
    from loop.reward import sts

    monkeypatch.setattr(
        sts,
        "_ocr_full",
        lambda _image: "62/80 99 Legend Unknown Merchant Treasure Rest Enemy Elite",
    )
    scored = sts.score_sts(Image.new("RGB", (64, 64), (20, 20, 20)))
    assert scored.get("screen") == "map"
    assert "hp" not in scored
    assert "energy" not in scored


def test_combat_exit_evidence_confirms_baseline_loot_frame() -> None:
    from loop.reward.sts import combat_exit_evidence

    previous = {
        "screen": "combat",
        "activity": "combat",
        "combat_active": True,
        "player": {"energy": 1, "hp": 80, "hp_max": 80},
        "enemies": [{"hp": 0, "hp_max": 48, "label": "Nibbit"}],
    }
    current = {
        "screen": "loot",
        "ocr": "Loot 52 Gold Take Gold Skip",
        "player": None,
        "enemies": [],
        "black": False,
    }
    evidence = combat_exit_evidence(previous, current)
    assert evidence.confirmed is True
    assert evidence.score >= 3
    assert evidence.loot_heading is True


def test_combat_exit_evidence_requires_reward_specific_signal() -> None:
    from loop.reward.sts import combat_exit_evidence

    previous = {
        "screen": "combat",
        "enemies": [{"hp": 0, "hp_max": 48}],
        "player": {"energy": 0},
    }
    current = {
        "screen": "combat",
        "ocr": "Victory animation",
        "enemies": [],
        "player": None,
        "black": False,
    }
    evidence = combat_exit_evidence(previous, current)
    assert evidence.confirmed is False


def test_combat_exit_evidence_rejects_black_frame() -> None:
    from loop.reward.sts import combat_exit_evidence

    evidence = combat_exit_evidence(
        {"screen": "combat", "enemies": [{"hp": 0, "hp_max": 10}]},
        {"screen": "loot", "ocr": "Loot", "black": True},
    )
    assert evidence.confirmed is False
    assert evidence.black_frame is True


def test_combat_exit_evidence_reward_is_not_terminal_win() -> None:
    from loop.fixtures.sts2 import Sts2Fixture, normalize_host_stats

    frame = Image.new("RGB", (64, 64), (20, 20, 20))
    stats = normalize_host_stats(
        {"screen": "loot", "ocr": "Loot 40 Gold Take Gold Skip", "enemies": [], "player": None}
    )
    metrics = {"screen": stats["screen"], "ocr": stats["ocr"]}
    assert metrics["screen"] == "reward"
    assert Sts2Fixture().terminal(metrics, frame) == "alive"
    assert metrics.get("win_screen") is not True


def test_combat_exit_transition_clears_stale_combat_state(tmp_path) -> None:
    from loop.agent.loop import CollectAgent, CollectConfig
    from loop.fixtures.sts2 import Sts2Fixture
    from loop.log.store import EpisodeStore
    from loop.os.aim import AimMark, AimGuide
    from loop.reward.sts import combat_exit_evidence

    agent = CollectAgent(
        Sts2Fixture(controller=None),
        EpisodeStore(tmp_path),
        config=CollectConfig(skip_models=True, arm="no_skill"),
    )
    agent._current_episode_id = "ep1"
    agent._combat_index = 1
    agent._prev_host_stats = {
        "screen": "combat",
        "activity": "combat",
        "combat_active": True,
        "player": {"energy": 1, "hp": 80, "hp_max": 80},
        "enemies": [{"hp": 0, "hp_max": 48, "label": "Nibbit"}],
    }
    agent._host_stats = {
        "screen": "loot",
        "ocr": "Loot 52 Gold Take Gold Skip",
        "player": None,
        "enemies": [],
        "black": False,
    }
    agent._obs_metrics = {"screen": "combat", "energy": 1}
    agent._selected = "Strike"
    nibbit = AimMark(
        9, 0.74, 0.63, 0.68, 0.56, 0.80, 0.69, "Nibbit", object_id="enemy_9", role="enemy"
    )
    strike = AimMark(
        3, 0.40, 0.85, 0.34, 0.80, 0.46, 0.90, "Strike", object_id="card_3", role="card"
    )
    image = Image.new("RGB", (64, 64), (30, 30, 30))
    agent._aim = AimGuide.from_marks(image, [nibbit, strike])
    agent._last_execution_plan = {"action": "play_card", "source": {"object_id": "card_3"}}
    agent._control_suppression.record_no_change("fp", "next@combat")
    assert combat_exit_evidence(agent._prev_host_stats, agent._host_stats).confirmed

    metrics = agent._maybe_apply_combat_exit_transition(agent._obs_metrics)
    assert metrics["screen"] == "reward"
    assert metrics["combat_won"] is True
    assert metrics["combat_won_transition_id"] == "ep1:1"
    assert agent._selected is None
    assert not agent._entity_tracker.entities
    assert agent._last_execution_plan == {}
    assert agent._control_suppression.attempts == {}
    assert all(mark.role != "enemy" for mark in agent._aim.marks) if agent._aim else True

    agent._step_id = 2
    again = agent._maybe_apply_combat_exit_transition(metrics)
    assert again.get("combat_won") is not True
    assert len(agent._combat_won_keys) == 1


def test_combat_exit_repeated_same_step_preserves_reward_surface(tmp_path) -> None:
    """The second pre-ACT check must not erase Loot rebuilt after the first."""
    from loop.agent.loop import CollectAgent, CollectConfig
    from loop.fixtures.sts2 import Sts2Fixture
    from loop.log.store import EpisodeStore

    agent = CollectAgent(
        Sts2Fixture(controller=None),
        EpisodeStore(tmp_path),
        config=CollectConfig(skip_models=True, arm="no_skill"),
    )
    agent._current_episode_id = "ep1"
    agent._combat_index = 1
    agent._step_id = 8
    agent._prev_host_stats = {
        "screen": "combat", "combat_active": True,
        "enemies": [{"hp": 0, "hp_max": 44}],
    }
    agent._host_stats = {
        "screen": "reward", "ocr": "Loot! 16 Gold Add a card to your deck. Skip",
        "enemies": [], "player": None, "black": False,
    }
    first = agent._maybe_apply_combat_exit_transition({"activity": "other"})
    assert first["combat_won"] is True
    reward_surface = object()
    agent._action_surface = reward_surface

    same_step = agent._maybe_apply_combat_exit_transition(first)
    assert same_step["combat_won"] is True
    assert same_step["combat_won_transition_id"] == "ep1:1"
    assert agent._action_surface is reward_surface

    agent._step_id = 9
    stable = agent._maybe_apply_combat_exit_transition({"activity": "other"})
    assert stable["screen"] == "reward"
    assert stable.get("combat_won") is not True
    assert agent._action_surface is reward_surface


def test_combat_exit_uses_current_scorer_and_perception_evidence(tmp_path) -> None:
    """Loot must not wait for the host reader to classify the reward frame."""
    from loop.agent.loop import CollectAgent, CollectConfig
    from loop.agent.perceive import SceneDescription
    from loop.fixtures.sts2 import Sts2Fixture
    from loop.log.store import EpisodeStore

    agent = CollectAgent(
        Sts2Fixture(controller=None),
        EpisodeStore(tmp_path),
        config=CollectConfig(skip_models=True, arm="no_skill"),
    )
    agent._current_episode_id = "ep1"
    agent._combat_index = 1
    agent._step_id = 10
    agent._prev_host_stats = {
        "screen": "combat", "combat_active": True,
        "enemies": [{"hp": 0, "hp_max": 44}],
    }
    # This matches the live regression: compact host OCR has no screen, but
    # current-frame scorer OCR and validated LLM1 both identify Loot.
    agent._host_stats = {"screen": None, "enemies": [], "player": None, "black": False}
    scene = SceneDescription(
        screen="reward",
        controls=[{"label": "Add a card", "role": "button"}, {"label": "Skip", "role": "button"}],
    )
    metrics = agent._maybe_apply_combat_exit_transition(
        {"ocr": "Loot! 17 Gold Add a card to your deck. Skip", "activity": "other"},
        scene_desc=scene,
    )
    assert metrics["screen"] == "reward"
    assert metrics["combat_won"] is True
    assert metrics["combat_won_transition_id"] == "ep1:1"

    agent._step_id = 11
    stable = agent._maybe_apply_combat_exit_transition(
        {"ocr": "Loot! 17 Gold Add a card to your deck. Skip", "activity": "other"},
        scene_desc=scene,
    )
    assert stable["screen"] == "reward"
    assert stable.get("combat_won") is not True


def test_combat_animation_then_loot_emits_once(tmp_path) -> None:
    from loop.agent.loop import CollectAgent, CollectConfig
    from loop.fixtures.sts2 import Sts2Fixture
    from loop.log.store import EpisodeStore

    agent = CollectAgent(
        Sts2Fixture(controller=None),
        EpisodeStore(tmp_path),
        config=CollectConfig(skip_models=True, arm="no_skill"),
    )
    agent._current_episode_id = "ep1"
    agent._combat_index = 1
    combat = {
        "screen": "combat",
        "activity": "combat",
        "combat_active": True,
        "player": {"energy": 0, "hp": 80, "hp_max": 80},
        "enemies": [{"hp": 0, "hp_max": 40, "label": "Jaw Worm"}],
    }
    animation = {
        "screen": "combat",
        "ocr": "Enemy defeated",
        "enemies": [],
        "player": None,
        "black": False,
    }
    loot = {
        "screen": "loot",
        "ocr": "Loot 25 Gold Take Gold Skip",
        "enemies": [],
        "player": None,
        "black": False,
    }
    agent._prev_host_stats = combat
    agent._host_stats = animation
    agent._obs_metrics = {"screen": "combat"}
    agent._selected = "Strike"
    unchanged = agent._maybe_apply_combat_exit_transition(agent._obs_metrics)
    assert unchanged.get("combat_won") is not True
    assert agent._selected == "Strike"

    agent._prev_host_stats = animation
    agent._host_stats = loot
    metrics = agent._maybe_apply_combat_exit_transition(agent._obs_metrics)
    assert metrics["combat_won"] is True
    assert metrics["screen"] == "reward"
    assert agent._selected is None

    agent._step_id = 2
    stable = agent._maybe_apply_combat_exit_transition(metrics)
    assert stable.get("combat_won") is not True
