from __future__ import annotations

from loop.os.tables import KEYBOARD_TABLE, XBOX360_TABLE, names, prompt_listing, resolve
from loop.safety.panic import should_panic


def test_xbox_table_is_unlabeled() -> None:
    verbs = {"dodge", "flask", "light_attack", "swipe_left", "jump"}
    assert verbs.isdisjoint(XBOX360_TABLE)
    assert "a" in XBOX360_TABLE
    assert "stick_l_n" in XBOX360_TABLE
    assert "rb" in XBOX360_TABLE
    listing = prompt_listing("xbox360")
    assert "dodge" not in listing
    assert resolve("xbox360", "b").name == "b"


def test_chord_name_and_banned_tokens() -> None:
    from loop.log.schema import ActionLog
    from loop.os.chords import chord_name, fill_empty_chord, is_empty_chord, skill_text_leaks

    action = ActionLog(stick_l="ne", held=["b"], duration_ms=400)
    assert chord_name(action) == "stick_l_ne+b"
    assert skill_text_leaks("dodge then flask") == ["dodge", "flask"]
    assert skill_text_leaks({"pitfall": "hold b"}) == []
    assert is_empty_chord(ActionLog(duration_ms=400))
    assert is_empty_chord(ActionLog(name="end", duration_ms=400))
    assert not is_empty_chord(ActionLog(stick_l="n", duration_ms=400))
    filled = fill_empty_chord(ActionLog(duration_ms=0), "xbox360", recent=["stick_l_n"], burst_ms=400)
    assert filled.stick_r == "n"
    assert filled.stick_l is None
    assert filled.name == "stick_r_n"
    patched = fill_empty_chord(
        ActionLog(duration_ms=0),
        "xbox360",
        recent=["stick_l_n"],
        burst_ms=400,
        patches=[(0.9, 0.4)],
    )
    assert patched.stick_r == "n"
    assert patched.stick_l is None
    tapped = fill_empty_chord(
        ActionLog(duration_ms=0),
        "xbox360",
        recent=["stick_l_n"],
        burst_ms=400,
        prompt_on=True,
        glyph="a",
    )
    assert tapped.tapped == ["a"]
    assert tapped.stick_l is None
    assert tapped.name == "a"
    look_not_tap = fill_empty_chord(
        ActionLog(duration_ms=0),
        "xbox360",
        recent=["stick_l_n"],
        burst_ms=400,
        prompt_on=False,
        glyph="a",
    )
    assert look_not_tap.tapped == []
    assert look_not_tap.stick_r == "n"


def test_bind_prompt_devices_adds_named_glyph() -> None:
    from loop.log.schema import ActionLog
    from loop.os.chords import bind_prompt_devices, extract_prompt_devices

    assert "x" in extract_prompt_devices("glyph resembles 'x' for pillage")
    assert "n" not in extract_prompt_devices("compass shows N and E")
    assert "b" not in extract_prompt_devices("The prompt box is present with no glyph yet")
    assert "a" in extract_prompt_devices("The prompt box 'O: Pillage' names the 'o' button")
    assert "b" not in extract_prompt_devices("The prompt box 'O: Pillage' names the 'o' button")
    action = ActionLog(stick_l="n", duration_ms=400)
    bound, missing = bind_prompt_devices(
        "The glyph uses a button that resembles 'x'",
        action,
        "xbox360",
    )
    assert missing == ["x", "no-stick"]
    assert "x" in bound.tapped
    assert bound.stick_l is None


def test_bind_skips_device_list_and_ocr_o_is_a() -> None:
    from loop.log.schema import ActionLog
    from loop.os.chords import bind_prompt_devices, extract_prompt_devices, parse_action, steer_explore_chord

    listed = (
        "The CV line says if the box names a, b, x, y, lb, rb, dpad_*, use that. "
        "I see 'a' and Switch action."
    )
    found = extract_prompt_devices(listed)
    assert "lb" not in found and "rb" not in found
    bound, flags = bind_prompt_devices(listed, ActionLog(stick_l="n", duration_ms=400), "xbox360")
    assert bound.tapped == ["a", "dpad_up"] or bound.tapped == ["dpad_up", "a"] or (
        "a" in bound.tapped and "dpad_up" in bound.tapped and "rb" not in bound.tapped
    )
    assert bound.stick_l is None
    ocr, ocr_flags = bind_prompt_devices(
        "The prompt box 'O: Pillage remains' names the 'o' button.",
        ActionLog(stick_l="ne", duration_ms=400),
        "xbox360",
    )
    assert ocr.tapped == ["a"]
    assert "b" not in ocr.tapped
    nested = parse_action(
        {"thought": {"thought": "walk to the light", "stick_l": "n", "tapped": []}},
        "xbox360",
    )
    assert nested.stick_l == "n"
    steered, why = steer_explore_chord(
        ActionLog(stick_r="nw", duration_ms=400),
        patches=[(0.6, 0.5)],
        thought="I will walk toward the bright patch.",
        recent=["stick_r_nw"],
        prompt_on=False,
    )
    assert steered.stick_r == "nw"
    assert steered.stick_l is None
    assert why is None
    escaped, esc_why = steer_explore_chord(
        ActionLog(stick_l="n", duration_ms=400),
        patches=[(0.5, 0.4)],
        thought="keep walking north",
        recent=["stick_l_n", "stick_l_n", "stick_l_n"],
        prompt_on=False,
        last_mad=0.002,
    )
    assert esc_why is None
    assert escaped.stick_l == "n"
    assert escaped.stick_r is None
    assert escaped.name in {None, "stick_l_n"}


def test_bind_skips_when_prompt_absent() -> None:
    from loop.log.schema import ActionLog
    from loop.os.chords import bind_prompt_devices

    action = ActionLog(stick_l="e", duration_ms=1200)
    bound, flags = bind_prompt_devices(
        "The bottom-center box is empty",
        action,
        "xbox360",
        visible=False,
    )
    assert flags == []
    assert bound.stick_l == "e"
    assert bound.tapped == []
    named, named_flags = bind_prompt_devices(
        "The bottom-center box shows 'y'",
        ActionLog(stick_l="e", duration_ms=1200),
        "xbox360",
        visible=False,
    )
    assert named.tapped == []
    assert named.stick_l == "e"
    assert named_flags == []
    past = (
        "The previous actions involved moving north and pressing 'a' which triggered a prompt. "
        "I've now cleared the prompt. I will now attempt to move forward (north)."
    )
    history, history_flags = bind_prompt_devices(
        past,
        ActionLog(stick_l="n", duration_ms=400),
        "xbox360",
        visible=False,
    )
    assert history_flags == []
    assert history.stick_l == "n"
    assert history.tapped == []
    still_visible, _ = bind_prompt_devices(
        past,
        ActionLog(stick_l="n", duration_ms=400),
        "xbox360",
        visible=True,
    )
    assert still_visible.stick_l == "n"
    assert still_visible.tapped == []
    live, live_flags = bind_prompt_devices(
        "The prompt shows 'a'. I will press 'a' to open.",
        ActionLog(stick_l="n", duration_ms=400),
        "xbox360",
        visible=True,
    )
    assert "a" in live.tapped


def test_bind_skips_look_intent_and_failed_face() -> None:
    from loop.log.schema import ActionLog
    from loop.os.chords import bind_prompt_devices, extract_prompt_devices

    look = ActionLog(stick_r="e", duration_ms=400)
    bound, flags = bind_prompt_devices(
        "Pressing 'a' failed. I will turn the camera to look for other paths.",
        look,
        "xbox360",
        visible=False,
    )
    assert flags == []
    assert bound.stick_r == "e"
    assert bound.tapped == []
    lock, lock_flags = bind_prompt_devices(
        "I will look around for the finger.",
        ActionLog(stick_r="w", duration_ms=400),
        "xbox360",
        visible=False,
        yield_lock=True,
    )
    assert lock_flags == []
    assert lock.stick_r == "w"
    assert lock.tapped == []
    assert "a" not in extract_prompt_devices(
        "I have already tried pressing 'a' multiple times and it did not open."
    )


def test_bind_switch_action_uses_dpad_and_one_face() -> None:
    from loop.log.schema import ActionLog
    from loop.os.chords import apply_chord_duration, bind_prompt_devices

    action = ActionLog(stick_l="n", tapped=["a", "b", "x"], duration_ms=400)
    bound, flags = bind_prompt_devices(
        "The prompt shows 'a' :Open and Switch action. I will press a, b, and x.",
        action,
        "xbox360",
    )
    assert "dpad_up" in bound.tapped
    assert bound.tapped.count("a") + bound.tapped.count("b") + bound.tapped.count("x") <= 1
    assert bound.stick_l is None
    assert "no-stick" in flags
    locos = apply_chord_duration(ActionLog(stick_l="nw", duration_ms=400), burst_ms=400, stick_ms=2000)
    assert locos.duration_ms == 400
    long = apply_chord_duration(ActionLog(stick_l="n", duration_ms=1500), burst_ms=400, stick_ms=2000)
    assert long.duration_ms == 1500
    capped = apply_chord_duration(ActionLog(stick_l="n", duration_ms=9000), burst_ms=400, stick_ms=2000)
    assert capped.duration_ms == 2000
    look = apply_chord_duration(ActionLog(stick_r="e", duration_ms=1500), burst_ms=400, stick_ms=2000)
    assert look.duration_ms == 400
    tap = apply_chord_duration(ActionLog(tapped=["a"], duration_ms=1500), burst_ms=400, stick_ms=2000)
    assert tap.duration_ms == 400


def test_bind_uses_cv_glyph_when_thought_vague() -> None:
    from loop.log.schema import ActionLog
    from loop.os.chords import bind_prompt_devices, is_loco_walk

    action = ActionLog(stick_l="n", duration_ms=400)
    bound, flags = bind_prompt_devices(
        "I see a prompt bar and will interact",
        action,
        "xbox360",
        visible=True,
        cv_glyph="a",
    )
    assert bound.tapped == ["a"]
    assert bound.stick_l is None
    assert "a" in flags or "no-stick" in flags
    thought_wins, _ = bind_prompt_devices(
        "The glyph resembles 'x'",
        ActionLog(stick_l="n", duration_ms=400),
        "xbox360",
        visible=True,
        cv_glyph="a",
    )
    assert thought_wins.tapped == ["x"]
    assert is_loco_walk(ActionLog(stick_l="n", duration_ms=1200))
    assert not is_loco_walk(ActionLog(stick_l="n", tapped=["a"], duration_ms=400))


def test_keyboard_table_is_unlabeled() -> None:
    assert "keyw" in KEYBOARD_TABLE
    assert "dodge" not in KEYBOARD_TABLE
    assert names("keyboard")


def test_sts_table_is_unlabeled_layout_slots() -> None:
    from loop.os.tables import STS_TABLE

    verbs = {"end_turn", "click_card_1", "play_card", "attack"}
    assert verbs.isdisjoint(STS_TABLE)
    assert "h1" in STS_TABLE
    assert "m1" in STS_TABLE
    assert "keye" in STS_TABLE
    assert "d1" in STS_TABLE
    listing = prompt_listing("sts")
    assert "end_turn" not in listing
    assert resolve("sts", "h1").name == "h1"


def test_fill_empty_named_table_does_not_invent_sticks() -> None:
    from loop.log.schema import ActionLog
    from loop.os.chords import fill_empty_chord, parse_action

    filled = fill_empty_chord(ActionLog(duration_ms=0), "sts", recent=[], burst_ms=400)
    assert filled.stick_l is None
    assert filled.stick_r is None
    assert filled.name in names("sts")
    parsed = parse_action({"thought": "try a slot", "action": "h3"}, "sts")
    assert parsed.name == "h3"
    assert parsed.stick_l is None
    ignored = parse_action({"action": "h1", "stick_l": "n"}, "sts")
    assert ignored.name == "h1"
    assert ignored.stick_l is None


def test_parse_point_click_on_mouse_table() -> None:
    from loop.log.schema import ActionLog
    from loop.os.chords import has_click, is_empty_chord, parse_action

    frac = parse_action({"thought": "loot", "x": 0.52, "y": 0.41}, "sts")
    assert frac.click_x == 0.52
    assert frac.click_y == 0.41
    assert frac.derive_name() == "pt_0.52_0.41"
    assert frac.name == "pt_0.52_0.41"
    assert not is_empty_chord(frac)
    assert has_click(frac)

    grid = parse_action({"x": 520, "y": 410}, "sts")
    assert abs(grid.click_x - 0.52) < 1e-9
    assert abs(grid.click_y - 0.41) < 1e-9

    aliases = parse_action({"click_x": 0.2, "click_y": 0.8}, "sts")
    assert aliases.click_x == 0.2
    assert aliases.click_y == 0.8

    pair = parse_action({"click": [0.25, 0.75]}, "sts")
    assert pair.click_x == 0.25
    assert pair.click_y == 0.75

    both = parse_action({"action": "h3", "x": 0.5, "y": 0.4}, "sts")
    assert both.click_x == 0.5
    assert both.name == "pt_0.50_0.40"

    nested = parse_action({"thought": {"thought": "center", "x": 0.3, "y": 0.7}}, "sts")
    assert nested.click_x == 0.3
    assert nested.click_y == 0.7

    wrapped = parse_action({"thought": '{"thought": "dismiss", "action": "enter"}'}, "sts")
    assert wrapped.name == "enter"
    assert wrapped.click_x is None

    pad = parse_action({"stick_l": "n", "x": 0.5, "y": 0.4}, "xbox360")
    assert pad.click_x is None
    assert pad.stick_l == "n"

    assert not is_empty_chord(ActionLog(click_x=0.5, click_y=0.5))
    named = parse_action({"action": "enter"}, "sts")
    assert named.name == "enter"
    assert named.click_x is None

    grid = parse_action({"gx": 410, "gy": 750}, "sts")
    assert abs(grid.click_x - 0.41) < 1e-9
    assert abs(grid.click_y - 0.75) < 1e-9

    marked = parse_action({"thought": "badge", "mark": 4}, "sts")
    assert marked.mark == 4
    assert not is_empty_chord(marked)
    assert marked.derive_name() == "mk_4"

    hovered = parse_action({"thought": "read tooltip", "hover": True, "mark": 6}, "sts")
    assert hovered.hover is True
    assert hovered.mark == 6
    assert hovered.derive_name() == "hv_6"
    grid_hover = parse_action({"hover": "true", "gx": 500, "gy": 180}, "sts")
    assert grid_hover.hover is True
    assert grid_hover.derive_name() == "hv_0.50_0.18"

    pair = parse_action({"mark": 3, "target": 7}, "sts")
    assert pair.mark == 3
    assert pair.target == 7
    assert pair.derive_name() == "mk_3>mk_7"
    grid2 = parse_action({"gx": 400, "gy": 850, "gx2": 550, "gy2": 300}, "sts")
    assert abs(grid2.click_x - 0.40) < 1e-9
    assert abs(grid2.click_x2 - 0.55) < 1e-9
    assert grid2.derive_name() == "pt_0.40_0.85>pt_0.55_0.30"


def test_panic_on_cursor_title() -> None:
    assert should_panic("Cursor Agents — Avatar")
    assert not should_panic("ELDEN RING")
    # Cursor being foreground is not itself the abort: we steal focus first.
    # Panic only if the game steal still lands on Cursor.
