from __future__ import annotations

from io import BytesIO

from PIL import Image

from avatar.constructor.from_traces import skills_from_traces
from avatar.constructor.skills import Skill, SkillLibrary
from avatar.harness.primitives import KeyPrimitive
from avatar.harness.tables import clip_plan, compile_action, names, resolve
from avatar.harness.capture import encode_image
from avatar.memory.episodic import EpisodicMemory, TaskRecord
from avatar.schema import ActionStep


def test_swipe_table_has_four_directions() -> None:
    assert names("swipe") == ["swipe_up", "swipe_down", "swipe_left", "swipe_right", "restart"]
    prims = resolve("swipe", "swipe_left").primitives
    assert len(prims) == 1
    assert isinstance(prims[0], KeyPrimitive)
    assert prims[0].code == "ArrowLeft"


def test_clip_plan_caps_at_five() -> None:
    steps = [ActionStep(name=f"swipe_left") for _ in range(9)]
    assert len(clip_plan(steps, 5)) == 5


def test_encode_downscale_and_quality() -> None:
    img = Image.new("RGB", (1600, 900), color=(20, 40, 80))
    native = encode_image(img, longest_side="native", jpeg_quality=85)
    small = encode_image(img, longest_side=512, jpeg_quality=70)
    assert native.width == 1600
    assert max(small.width, small.height) <= 512
    assert small.nbytes < native.nbytes
    roundtrip = Image.open(BytesIO(small.data))
    assert roundtrip.size == (small.width, small.height)


def test_compose_hud_and_board_keeps_board_width() -> None:
    from avatar.fixtures.g2048 import compose_hud_and_board

    hud = Image.new("RGB", (800, 120), color=(187, 173, 160))
    board = Image.new("RGB", (1000, 1000), color=(119, 110, 101))
    out = compose_hud_and_board(hud, board)
    assert out.size == (1000, 150 + 1000)
    assert out.getpixel((0, 0)) == (187, 173, 160)
    assert out.getpixel((0, 160)) == (119, 110, 101)


def test_apply_crop_normalized_rect() -> None:
    from avatar.harness.profile import apply_crop

    full = Image.new("RGB", (1000, 1000), color=(10, 20, 30))
    cropped = apply_crop(full, (0.2, 0.2, 0.5, 0.5))
    assert cropped.size == (500, 500)
    assert apply_crop(full, None).size == (1000, 1000)


def test_crop_does_not_change_mouse_client_mapping() -> None:
    """Vision crop is independent of OS click coords (full client 0–1)."""
    from avatar.harness.profile import apply_crop

    client_w, client_h = 1000, 1000
    full = Image.new("RGB", (client_w, client_h))
    cropped = apply_crop(full, (0.2, 0.2, 0.5, 0.5))
    nx, ny = 0.22, 0.82  # StS click_card_1
    client_xy = (int(nx * client_w), int(ny * client_h))
    crop_xy = (int(nx * cropped.width), int(ny * cropped.height))
    assert client_xy == (220, 820)
    assert crop_xy != client_xy


def test_mean_absdiff_identical_and_tile_patch() -> None:
    from avatar.harness.profile import mean_absdiff, roi_changed, roi_thumb, use_cached_jpeg

    a = Image.new("L", (64, 64), 40)
    b = a.copy()
    assert mean_absdiff(a, b) == 0.0
    assert not roi_changed(a, b, 0.02)

    patched = a.copy()
    for x in range(16):
        for y in range(16):
            patched.putpixel((x, y), 255)
    assert mean_absdiff(a, patched) > 0.02
    assert roi_changed(a, patched, 0.02)
    # Downsampled 256 board with a tile-sized (1/4) patch still trips the default threshold.
    board = Image.new("RGB", (256, 256), color=(40, 40, 40))
    tile = board.copy()
    for x in range(64):
        for y in range(64):
            tile.putpixel((x, y), (255, 255, 255))
    assert roi_changed(roi_thumb(board), roi_thumb(tile), 0.02)

    assert use_cached_jpeg(skip_unchanged=True, changed=False, has_last=True) is True
    assert use_cached_jpeg(skip_unchanged=True, changed=False, has_last=True, fresh=True) is False
    assert use_cached_jpeg(skip_unchanged=False, changed=False, has_last=True) is False
    assert use_cached_jpeg(skip_unchanged=True, changed=True, has_last=True) is False


def test_encode_small_blob_shift_changes_bytes() -> None:
    dark = Image.new("RGB", (256, 256), (12, 12, 18))
    a = dark.copy()
    b = dark.copy()
    for x in range(8, 40):
        for y in range(8, 40):
            a.putpixel((x, y), (0, 220, 220))
    for x in range(8, 40):
        for y in range(80, 112):
            b.putpixel((x, y), (0, 220, 220))
    fa = encode_image(a, longest_side=256, jpeg_quality=85)
    fb = encode_image(b, longest_side=256, jpeg_quality=85)
    assert fa.data != fb.data
    from avatar.harness.profile import roi_changed, roi_thumb

    assert roi_changed(roi_thumb(a), roi_thumb(b), 0.004)


def test_poll_until_settled_timeout_without_change() -> None:
    from avatar.harness.profile import poll_until_settled

    still = Image.new("L", (64, 64), 12)

    class Clock:
        def __init__(self) -> None:
            self.t = 0.0

        def __call__(self) -> float:
            return self.t

        def sleep(self, dt: float) -> None:
            self.t += dt

    clock = Clock()
    result = poll_until_settled(
        lambda: still,
        threshold=0.02,
        timeout_ms=40,
        poll_ms=10,
        stable_ms=20,
        clock=clock,
        sleep=clock.sleep,
    )
    assert result.changed is False
    assert result.elapsed_ms >= 40
    assert result.polls >= 1


def test_poll_until_settled_change_then_stable() -> None:
    from avatar.harness.profile import poll_until_settled

    dark = Image.new("L", (64, 64), 0)
    bright = Image.new("L", (64, 64), 255)
    frames = [dark, bright, bright, bright, bright]

    class Clock:
        def __init__(self) -> None:
            self.t = 0.0

        def __call__(self) -> float:
            return self.t

        def sleep(self, dt: float) -> None:
            self.t += dt

    clock = Clock()
    result = poll_until_settled(
        lambda: frames.pop(0) if frames else bright,
        threshold=0.02,
        timeout_ms=80,
        poll_ms=10,
        stable_ms=20,
        clock=clock,
        sleep=clock.sleep,
    )
    assert result.changed is True
    assert result.elapsed_ms < 80


def test_skip_streak_hits_force_every() -> None:
    from avatar.harness.profile import next_skip_streak

    skip, streak = next_skip_streak(True, 3, 8)
    assert skip is False and streak == 0
    streak = 0
    skips = 0
    for _ in range(7):
        skip, streak = next_skip_streak(False, streak, 8)
        assert skip is True
        skips += 1
    skip, streak = next_skip_streak(False, streak, 8)
    assert skip is False and streak == 0
    assert skips == 7
    skip, streak = next_skip_streak(False, 0, 0)
    assert skip is False and streak == 0


def test_persist_layout_scout_writes_image_and_plan(tmp_path) -> None:
    from avatar.harness.layout import LayoutPlan
    from avatar.harness.profile import CaptureProfile
    from avatar.harness.scout import persist_layout_scout

    image = Image.new("RGB", (800, 600), color=(40, 80, 120))
    profile = CaptureProfile(scout_longest_side=512, jpeg_quality=80)
    plan = LayoutPlan(
        crop=(0.1, 0.1, 0.8, 0.8),
        longest_side=512,
        jpeg_quality=80,
        screen_kind="gameplay",
        source="cv",
    )
    meta = persist_layout_scout(tmp_path / "scout", image, plan, profile, name="layout")
    assert (tmp_path / "scout" / "layout.jpg").exists()
    assert (tmp_path / "scout" / "layout.json").exists()
    assert meta["source"] == "cv"
    assert meta["original_width"] == 800
    assert meta["crop"] == (0.1, 0.1, 0.8, 0.8)


def test_profile_from_config_2048_and_tetris() -> None:
    from avatar.config import load_config
    from avatar.harness.profile import profile_from_config

    cfg = load_config()
    g2048 = profile_from_config(cfg, "2048")
    assert g2048.current.longest_side == 512
    assert g2048.current.crop is not None
    assert g2048.layout_mode == "cv"
    assert g2048.layout_sides == (256, 384, 512, 768)
    assert g2048.yaml_gameplay is not None
    g2048.select("ui")
    assert g2048.current.crop is None
    assert g2048.current.longest_side == 768

    tetris = profile_from_config(cfg, "tetris")
    assert tetris.presets["gameplay"].longest_side == 768
    assert tetris.presets["gameplay"].crop is not None
    platinum = profile_from_config(cfg, "platinum")
    assert platinum.current.crop is None


def test_see_user_does_not_leak_canned_state() -> None:
    from avatar.prompts.library import see_user

    text = see_user("2048", "start")
    assert "start" not in text.lower()
    assert "canned" not in text.lower()


def test_scout_user_asks_screen_kind_only() -> None:
    from avatar.prompts.library import scout_user

    text = scout_user("tetris")
    assert "tetris" in text.lower()
    assert "screen_kind" in text


def test_memory_last_ten() -> None:
    mem = EpisodicMemory(window=10)
    for i in range(12):
        mem.add(TaskRecord(goal=f"g{i}", verdict="success", evidence="ok", actions=["swipe_left"]))
    assert len(mem.last_n()) == 10
    assert "g11" in mem.last_ten_summary()
    assert "g0" not in mem.last_ten_summary()


def test_constructor_merge_not_spawn() -> None:
    lib = SkillLibrary(max_rewrites=3, soft_cap=8)
    a = Skill(name="merge_left", recognize="two equal tiles", do=["swipe_left"], end="after swipe")
    b = Skill(name="merge_left", recognize="two equal tiles", do=["swipe_left"], end="after swipe")
    assert lib.propose(a) == "spawned"
    assert lib.propose(b) == "merged"
    assert len(lib.skills) == 1
    assert lib.stats()["merged"] == 1


def test_constructor_rewrite_cap() -> None:
    lib = SkillLibrary(max_rewrites=3, soft_cap=8)
    lib.propose(Skill(name="drop", recognize="piece", do=["hard_drop"], end="lock"))
    for i in range(3):
        assert lib.rewrite("drop", Skill(name="drop", recognize="piece", do=["soft_drop"], end=str(i))) == "rewritten"
    assert lib.rewrite("drop", Skill(name="drop", recognize="piece", do=["hard_drop"], end="nope")) == "rejected"
    assert lib.get("drop").rewrites == 3


def test_skills_from_traces_merge(tmp_path) -> None:
    jsonl = tmp_path / "turns.jsonl"
    lines = []
    for _ in range(4):
        lines.append(
            '{"fixture":"2048","action":{"actions":[{"name":"swipe_left"},{"name":"swipe_up"}]}}'
        )
    for _ in range(4):
        lines.append(
            '{"fixture":"2048","action":{"actions":[{"name":"swipe_left"},{"name":"swipe_up"}]}}'
        )
    jsonl.write_text("\n".join(lines), encoding="utf-8")
    lib = skills_from_traces(jsonl, min_repeats=3)
    assert lib.stats()["spawned"] == 1
    assert "swipe_left" in next(iter(lib.skills.values())).do


def test_default_2048_skills_prompt_block() -> None:
    from avatar.constructor.skills import default_2048_skills

    lib = default_2048_skills()
    block = lib.prompt_block()
    assert "keep_left" in block
    assert "corner_down_left" in block
    assert "swipe_left" in block


def test_nds_and_sts_compile_to_os_primitives() -> None:
    from avatar.harness.primitives import GamepadPrimitive, MousePrimitive

    a = compile_action("nds", "press_a")[0]
    assert isinstance(a, GamepadPrimitive)
    assert a.button == "a"
    click = compile_action("sts", "click_card_1")[0]
    assert isinstance(click, MousePrimitive)
    assert click.x == 0.22


def test_keyboard_scan_codes_cover_tables() -> None:
    from avatar.harness.keyboard import scan_for

    for code in ("ArrowLeft", "KeyA", "Space", "Enter", "KeyE", "shift"):
        scan, _extended = scan_for(code)
        assert scan > 0


def test_gamepad_available_does_not_raise() -> None:
    from avatar.harness.gamepad import gamepad_available

    assert gamepad_available() in {True, False}


def test_live_os_input_control_lab() -> None:
    import json
    import os
    import subprocess
    import sys
    import time
    from pathlib import Path

    import pytest

    if os.environ.get("AVATAR_LIVE_INPUT") != "1":
        pytest.skip("set AVATAR_LIVE_INPUT=1 to send real OS input")

    from avatar.fixtures.control_lab import LAB_TITLE
    from avatar.harness.executor import OsController
    from avatar.harness.primitives import KeyPrimitive, MousePrimitive
    from avatar.harness.window import wait_for_window

    echo = Path("runs") / "test_control_lab_echo.json"
    echo.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        [sys.executable, "-m", "avatar.fixtures.control_lab", str(echo), "20"],
    )
    try:
        wait_for_window(LAB_TITLE, timeout_s=15)
        time.sleep(0.5)
        with OsController(LAB_TITLE, enable_gamepad=False) as ctl:
            ctl.focus()
            ctl.execute([KeyPrimitive(code="ArrowLeft", op="tap")])
            time.sleep(0.15)
            ctl.execute([MousePrimitive(op="click", x=0.5, y=0.5)])
            time.sleep(0.4)
        payload = json.loads(echo.read_text(encoding="utf-8"))
        kinds = {row.get("kind") for row in payload}
        assert "key" in kinds
        assert "click" in kinds
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_oracle_http_roundtrip() -> None:
    import httpx

    from avatar.fixtures.server import latest_oracle, serve_2048

    server, url = serve_2048()
    try:
        payload = {
            "fixture": "2048",
            "grid": [[2, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0], [0, 2, 0, 0]],
            "score": 0,
            "over": False,
        }
        posted = httpx.post(url + "__oracle", json=payload, timeout=5.0)
        assert posted.status_code == 204
        got = httpx.get(url + "__oracle", timeout=5.0).json()
        assert got["score"] == 0
        assert got["grid"][0][0] == 2
        assert latest_oracle()["grid"][3][1] == 2
    finally:
        server.shutdown()


def _in_crop(crop: tuple[float, float, float, float], nx: float, ny: float) -> bool:
    return crop[0] <= nx <= crop[0] + crop[2] and crop[1] <= ny <= crop[1] + crop[3]


def _synthetic_2048(*, board_x0: int = 280) -> Image.Image:
    """Cream page, gray board + score HUD, thin how-to lines at the bottom."""
    from PIL import ImageDraw

    img = Image.new("RGB", (1000, 1000), (250, 248, 239))
    draw = ImageDraw.Draw(img)
    draw.rectangle([board_x0, 320, board_x0 + 439, 759], fill=(187, 173, 160))
    draw.rectangle([board_x0 + 140, 200, board_x0 + 339, 279], fill=(187, 173, 160))
    for y in range(880, 940, 4):
        draw.line([(180, y), (820, y)], fill=(90, 80, 70), width=1)
    return img


def test_diagnose_layout_keeps_score_drops_howto() -> None:
    from avatar.harness.layout import choose_longest_side, crop_iou, diagnose_layout
    from avatar.harness.profile import CapturePreset

    fallback = CapturePreset(crop=(0.10, 0.02, 0.80, 0.90), longest_side=512, jpeg_quality=85)
    plan = diagnose_layout(_synthetic_2048(), fallback=fallback)
    assert plan.source == "cv"
    assert plan.crop is not None
    assert plan.longest_side in {256, 384, 512, 768}
    assert _in_crop(plan.crop, 0.50, 0.54)
    assert _in_crop(plan.crop, 0.47, 0.24)
    assert not _in_crop(plan.crop, 0.50, 0.91)
    assert choose_longest_side(400, (256, 384, 512), 512) == 512
    assert choose_longest_side(700, (256, 384, 512), 512) == 512
    assert choose_longest_side(200, (256, 384, 512), 512) == 256
    assert choose_longest_side(700, (256, 384, 512, 768), 768) == 768
    same = diagnose_layout(_synthetic_2048(), fallback=fallback)
    assert crop_iou(plan.crop, same.crop) >= 0.7
    shifted = diagnose_layout(_synthetic_2048(board_x0=80), fallback=fallback)
    assert crop_iou(plan.crop, shifted.crop) < 0.7


def _synthetic_2048_tall_header() -> Image.Image:
    """Tall app window: score HUD far above the board, compact footer below."""
    from PIL import ImageDraw

    img = Image.new("RGB", (1000, 1400), (250, 248, 239))
    draw = ImageDraw.Draw(img)
    draw.rectangle([280, 40, 500, 120], fill=(119, 110, 101))
    draw.rectangle([560, 40, 700, 110], fill=(187, 173, 160))
    draw.rectangle([680, 40, 820, 110], fill=(187, 173, 160))
    draw.rectangle([280, 420, 719, 859], fill=(187, 173, 160))
    draw.rectangle([350, 1100, 650, 1180], fill=(187, 173, 160))
    for y in range(1220, 1300, 4):
        draw.line([(180, y), (820, y)], fill=(90, 80, 70), width=1)
    return img


def test_diagnose_layout_tall_header_keeps_score_drops_footer() -> None:
    from avatar.harness.layout import diagnose_layout
    from avatar.harness.profile import CapturePreset

    fallback = CapturePreset(crop=(0.10, 0.02, 0.80, 0.72), longest_side=512, jpeg_quality=85)
    plan = diagnose_layout(_synthetic_2048_tall_header(), fallback=fallback)
    assert plan.source == "cv"
    assert plan.crop is not None
    assert _in_crop(plan.crop, 0.50, 0.46)
    assert _in_crop(plan.crop, 0.63, 0.05)
    assert _in_crop(plan.crop, 0.75, 0.05)
    assert not _in_crop(plan.crop, 0.50, 0.81)
    assert not _in_crop(plan.crop, 0.50, 0.90)


def test_diagnose_layout_yaml_y0_floor_drops_header() -> None:
    from avatar.harness.layout import diagnose_layout
    from avatar.harness.profile import CapturePreset

    fallback = CapturePreset(crop=(0.12, 0.20, 0.76, 0.58), longest_side=512, jpeg_quality=85)
    plan = diagnose_layout(_synthetic_2048_tall_header(), fallback=fallback)
    assert plan.crop is not None
    assert plan.crop[1] >= 0.20
    assert not _in_crop(plan.crop, 0.63, 0.05)


def test_diagnose_layout_degenerate_falls_back_to_yaml() -> None:
    from avatar.harness.layout import diagnose_layout
    from avatar.harness.profile import CapturePreset

    fallback = CapturePreset(crop=(0.10, 0.02, 0.80, 0.72), longest_side=512, jpeg_quality=85)
    blank = Image.new("RGB", (800, 800), (250, 248, 239))
    plan = diagnose_layout(blank, fallback=fallback)
    assert plan.source == "yaml"
    assert plan.crop == fallback.crop
    assert plan.longest_side in {256, 384, 512, 768}


def _synthetic_tetris() -> Image.Image:
    """Dark playfield with next/hold HUD on the right and a thin footer."""
    from PIL import ImageDraw

    img = Image.new("RGB", (1000, 1000), (12, 16, 24))
    draw = ImageDraw.Draw(img)
    draw.rectangle([200, 80, 520, 880], fill=(34, 48, 64))
    draw.rectangle([560, 120, 740, 280], fill=(34, 48, 64))
    draw.rectangle([560, 320, 740, 460], fill=(34, 48, 64))
    for y in range(940, 980, 4):
        draw.line([(80, y), (920, y)], fill=(200, 200, 210), width=1)
    return img


def test_diagnose_layout_tetris_includes_side_hud() -> None:
    from avatar.harness.layout import diagnose_layout
    from avatar.harness.profile import CapturePreset

    fallback = CapturePreset(crop=(0.14, 0.02, 0.72, 0.94), longest_side=768, jpeg_quality=85)
    plan = diagnose_layout(_synthetic_tetris(), fallback=fallback)
    assert plan.source == "cv"
    assert plan.crop is not None
    assert _in_crop(plan.crop, 0.36, 0.48)
    assert _in_crop(plan.crop, 0.65, 0.20)
    assert _in_crop(plan.crop, 0.65, 0.39)
    assert not _in_crop(plan.crop, 0.50, 0.96)
    assert plan.longest_side in {256, 384, 512, 768}


def test_diagnose_layout_busy_frame_falls_back() -> None:
    from avatar.harness.layout import diagnose_layout
    from avatar.harness.profile import CapturePreset

    fallback = CapturePreset(crop=None, longest_side=768, jpeg_quality=85)
    busy = Image.new("RGB", (640, 480))
    pix = busy.load()
    for y in range(480):
        for x in range(640):
            pix[x, y] = ((x * 13 + y * 7) % 256, (x * 5) % 256, (y * 11) % 256)
    plan = diagnose_layout(busy, fallback=fallback)
    assert plan.source == "yaml"
    assert plan.crop is None


def test_filter_banned_strips_names() -> None:
    from avatar.experiments.common import parse_act
    from avatar.harness.cycle import filter_banned

    plan = parse_act({"actions": [{"name": "swipe_left"}, {"name": "swipe_up"}]})
    out = filter_banned(plan, {"swipe_left"})
    assert [step.name for step in out.actions] == ["swipe_up"]
    skill_plan = parse_act({"invoke_skill": "drop", "actions": []})
    assert filter_banned(skill_plan, {"drop"}).invoke_skill is None
    gsm_plan = parse_act({"invoke_gsm": "place_piece", "actions": []})
    assert filter_banned(gsm_plan, {"place_piece"}).invoke_gsm is None
    empty = filter_banned(plan, {"swipe_left", "swipe_up"})
    assert empty.actions == []
    assert "banned" in empty.reasoning.lower()


def test_invoke_skill_named_action_alias() -> None:
    from avatar.experiments.common import parse_act
    from avatar.constructor.skills import default_2048_skills
    from avatar.harness.tables import resolve

    plan = parse_act({"invoke_skill": "restart", "actions": []})
    skills = default_2048_skills()
    assert skills.get("restart") is None
    resolve("swipe", plan.invoke_skill or "")


def test_l1_injected_plan_unchanged() -> None:
    from PIL import Image

    from avatar.experiments.common import parse_act
    from avatar.harness.capture import encode_image
    from avatar.harness.cycle import AgentCycle

    class _Dummy:
        pass

    cycle = AgentCycle(table_name="swipe", controller=_Dummy(), fixture="2048")  # type: ignore[arg-type]
    frame = encode_image(Image.new("RGB", (8, 8)), longest_side=8)
    injected = parse_act({"actions": [{"name": "swipe_left"}]})
    plan, call = cycle.plan(frame, "task", injected=injected)
    assert [step.name for step in plan.actions] == ["swipe_left"]
    assert call is None


def test_l2_injected_plan_strips_banned(tmp_path) -> None:
    from PIL import Image

    from avatar.experiments.common import parse_act
    from avatar.harness.capture import encode_image
    from avatar.harness.cycle import AgentCycle
    from avatar.memory import AgentMemory

    class _Dummy:
        pass

    mem = AgentMemory("2048", root=tmp_path)
    mem.episodic.banned.add("swipe_left")
    cycle = AgentCycle(
        table_name="swipe",
        controller=_Dummy(),  # type: ignore[arg-type]
        fixture="2048",
        memory=mem,
        harness_level="L2",
        brain=object(),
    )
    frame = encode_image(Image.new("RGB", (8, 8)), longest_side=8)
    injected = parse_act({"actions": [{"name": "swipe_left"}, {"name": "swipe_up"}]})
    plan, call = cycle.plan(frame, "task", injected=injected)
    assert [step.name for step in plan.actions] == ["swipe_up"]
    assert call is None


def test_brain_prompt_is_text_only() -> None:
    from avatar.prompts.library import brain_system, brain_user

    system = brain_system("swipe", memory_block="Banned actions: swipe_left")
    assert "cannot see pixels" in system.lower()
    assert "swipe_left" in system
    user = brain_user("2048", "merge tiles", {"board": [[2, 2], [None, None]], "score": 0})
    assert "See JSON" in user
    assert "wait" not in user.lower()


class _HarnessGateway:
    def __init__(self) -> None:
        self.calls = 0
        self.systems: list[str] = []
        self.model = "fake"
        self.provider = "test"

    def complete_json(self, **kwargs) -> object:
        self.calls += 1
        system = str(kwargs.get("system") or "")
        self.systems.append(system)
        from avatar.models import ModelCall

        if "Named action table" in system:
            parsed: dict = {"actions": [{"name": "swipe_left"}], "reasoning": "plan"}
        elif "presence" in system or "screen_kind" in system:
            parsed = {"screen_kind": "gameplay", "status": "ok", "presence": "none"}
        elif "verdict" in system:
            parsed = {"verdict": "partial", "evidence": "pixels"}
        elif "narrative" in system:
            parsed = {"narrative": "last ten"}
        elif "proposed_labels" in system or "sequencing" in system:
            parsed = {"timing": "ok", "sequencing": "ok"}
        else:
            parsed = {"screen_kind": "gameplay"}
        return ModelCall(
            text="{}",
            parsed=parsed,
            latency_ms=1.0,
            cost_usd=0.0,
            model="fake",
            provider="test",
        )


class _HarnessController:
    def __init__(self) -> None:
        self.executed: list[str] = []
        self.capture = _HarnessCapture()

    def execute_named(self, table_name: str, action_name: str, **kwargs) -> None:
        self.executed.append(action_name)

    def execute_plan(self, plan, table_name: str, **kwargs) -> object:
        for step in plan.actions:
            self.execute_named(table_name, step.name)
        from avatar.harness.executor import CycleResult

        return CycleResult(plan, list(self.executed), None, plan.model_dump())


class _HarnessCapture:
    skip_streak = 0
    last_meta = type("M", (), {"changed": True})()
    layout_ready = True
    profile = type(
        "P",
        (),
        {"scout_every": 0, "scout_longest_side": 384, "jpeg_quality": 85, "fmt": "jpeg", "force_every": 8},
    )()

    def refresh(self) -> None:
        return None

    def diagnose_and_apply(self, scout_dir=None) -> None:
        return None

    def grab(self, *, full: bool = False, fresh: bool = False) -> tuple:
        _ = full, fresh
        frame = encode_image(Image.new("RGB", (8, 8)), longest_side=8)
        return frame, False

    def grab_image(self, *, full: bool = False):
        _ = full
        return Image.new("RGB", (8, 8))

    def refresh_layout(self, image=None) -> bool:
        _ = image
        return False

    def select_preset(self, screen_kind: str) -> None:
        _ = screen_kind

    def wait_settled(self) -> object:
        return type("S", (), {"elapsed_ms": 0.0, "changed": True})()


def test_single_vlm_see_and_plan_use_eyes() -> None:
    from avatar.harness.cycle import AgentCycle

    gateway = _HarnessGateway()
    cycle = AgentCycle(
        table_name="swipe",
        controller=_HarnessController(),  # type: ignore[arg-type]
        fixture="2048",
        eyes=gateway,
        brain=None,
        settle=False,
    )
    frame = encode_image(Image.new("RGB", (8, 8)), longest_side=8)
    see, see_call = cycle.see(frame)
    assert see is not None
    assert see_call is not None
    assert gateway.calls >= 1
    plan, plan_call = cycle.plan(frame, "swipe left")
    assert plan_call is not None
    assert any("Named action table" in system for system in gateway.systems)
    assert [step.name for step in plan.actions] == ["swipe_left"]

    live = _HarnessGateway()
    live_cycle = AgentCycle(
        table_name="swipe",
        controller=_HarnessController(),  # type: ignore[arg-type]
        fixture="2048",
        eyes=live,
        brain=None,
        settle=False,
    )
    live_cycle.turn("swipe left", model="dry-run")
    assert live.calls >= 1
    assert any("Named action table" in system for system in live.systems)


def test_see_coerces_2048_ui_to_gameplay() -> None:
    from avatar.harness.cycle import AgentCycle
    from avatar.models import ModelCall

    class _UiEyes:
        model = "fake"
        provider = "test"

        def complete_json(self, **kwargs) -> object:
            _ = kwargs
            return ModelCall(
                text="{}",
                parsed={"screen_kind": "ui", "board": [[2, None], [None, None]], "status": "none"},
                latency_ms=1.0,
                cost_usd=0.0,
                model="fake",
                provider="test",
            )

    cycle = AgentCycle(
        table_name="swipe",
        controller=_HarnessController(),  # type: ignore[arg-type]
        fixture="2048",
        eyes=_UiEyes(),
        brain=None,
        settle=False,
    )
    frame = encode_image(Image.new("RGB", (8, 8)), longest_side=8)
    see, _ = cycle.see(frame)
    assert see is not None
    assert see.screen_kind == "gameplay"


def test_see_clears_false_2048_gameover() -> None:
    from avatar.harness.cycle import AgentCycle
    from avatar.models import ModelCall

    class _OverEyes:
        model = "fake"
        provider = "test"

        def complete_json(self, **kwargs) -> object:
            _ = kwargs
            return ModelCall(
                text="{}",
                parsed={
                    "screen_kind": "ui",
                    "board": [[None, None, None, None], [None, None, None, None], [None, None, None, None], [2, 2, None, None]],
                    "status": "gameover",
                    "score": 0,
                },
                latency_ms=1.0,
                cost_usd=0.0,
                model="fake",
                provider="test",
            )

    cycle = AgentCycle(
        table_name="swipe",
        controller=_HarnessController(),  # type: ignore[arg-type]
        fixture="2048",
        eyes=_OverEyes(),
        brain=None,
        settle=False,
    )
    frame = encode_image(Image.new("RGB", (8, 8)), longest_side=8)
    see, _ = cycle.see(frame)
    assert see is not None
    assert see.screen_kind == "gameplay"
    assert see.status == "playing"


def test_phase2_reflection_models_parse() -> None:
    from avatar.schema import (
        ActionDesignReflection,
        GsmLabelRewrite,
        HistoricalSummary,
        PreviousTaskReflection,
    )

    prev = PreviousTaskReflection.model_validate(
        {"verdict": "success", "evidence": "tile merged"}
    )
    assert prev.verdict == "success"
    assert prev.optimizations == ""
    hist = HistoricalSummary.model_validate({"narrative": "kept the left wall"})
    assert "left" in hist.narrative
    design = ActionDesignReflection.model_validate(
        {"timing": "short", "proposed_labels": ["T tetromino"]}
    )
    assert design.proposed_labels == ["T tetromino"]
    rewrite = GsmLabelRewrite.model_validate({"labels": ["T tetromino", "I tetromino"]})
    assert rewrite.labels[0] == "T tetromino"


def test_maybe_rewrite_labels_caps_at_three(tmp_path: Path) -> None:
    from avatar.gsm.optimize import maybe_rewrite_labels
    from avatar.gsm.registry import GsmRegistry
    from avatar.schema import GsmSpec

    registry = GsmRegistry("tetris", max_rewrites=3)
    registry.persist_path = tmp_path / "registry.json"
    spec = GsmSpec(
        name="place_piece",
        kind="rule",
        fixture="tetris",
        labels=["T tetromino"],
        action_space=["hard_drop"],
        enabled=True,
    )
    registry.specs["place_piece"] = spec
    n = {"i": 0}

    def complete_json(**kwargs):
        _ = kwargs
        n["i"] += 1
        return {"labels": [["I tetromino"], ["O tetromino"], ["S tetromino"]][n["i"] % 3]}

    for _ in range(3):
        got = maybe_rewrite_labels(
            registry, spec, last_boxes=[], reason="no-piece", complete_json=complete_json
        )
        assert got == spec.rewrites
    assert spec.rewrites == 3
    assert n["i"] == 3
    got = maybe_rewrite_labels(
        registry, spec, last_boxes=[], reason="no-piece", complete_json=complete_json
    )
    assert got == 3
    assert spec.rewrites == 3
    assert n["i"] == 3


def test_maybe_rewrite_labels_noop_saves(tmp_path: Path) -> None:
    from avatar.gsm.optimize import maybe_rewrite_labels
    from avatar.gsm.registry import GsmRegistry
    from avatar.gsm.rule.detector import DetectedBox
    from avatar.schema import GsmSpec

    registry = GsmRegistry("2048", max_rewrites=3)
    dest = tmp_path / "registry.json"
    registry.persist_path = dest
    spec = GsmSpec(
        name="choose_swipe",
        kind="rule",
        labels=["tile"],
        action_space=["swipe_down"],
        enabled=True,
    )
    registry.specs["choose_swipe"] = spec
    box = DetectedBox(label="tile", x=0.1, y=0.1, w=0.2, h=0.2)
    got = maybe_rewrite_labels(
        registry,
        spec,
        last_boxes=[box],
        reason="noop after ['swipe_down']",
        complete_json=lambda **kwargs: {"labels": ["tile"]},
    )
    assert got == 1
    assert spec.labels == ["tile"]
    assert dest.exists()


def test_maybe_rewrite_labels_rejects_miss(tmp_path: Path) -> None:
    from avatar.gsm.optimize import maybe_rewrite_labels
    from avatar.gsm.registry import GsmRegistry
    from avatar.schema import GsmSpec

    registry = GsmRegistry("2048", max_rewrites=3)
    dest = tmp_path / "registry.json"
    registry.persist_path = dest
    spec = GsmSpec(
        name="choose_swipe",
        kind="rule",
        labels=["tile"],
        action_space=["swipe_down"],
        enabled=True,
    )
    registry.specs["choose_swipe"] = spec
    got = maybe_rewrite_labels(
        registry,
        spec,
        last_boxes=[],
        reason="no-piece",
        complete_json=lambda **kwargs: {"labels": ["miss", "miss"]},
    )
    assert got == 0
    assert spec.labels == ["tile"]
    assert not dest.exists()


def test_maybe_rewrite_labels_skips_pixel_blob(tmp_path: Path) -> None:
    from avatar.gsm.optimize import maybe_rewrite_labels
    from avatar.gsm.registry import GsmRegistry
    from avatar.schema import GsmSpec

    registry = GsmRegistry("2048", max_rewrites=3)
    spec = GsmSpec(
        name="choose_swipe",
        kind="rule",
        labels=["tile"],
        detector_family="pixel-blob",
        action_space=["swipe_down"],
        enabled=True,
    )
    registry.specs["choose_swipe"] = spec
    got = maybe_rewrite_labels(
        registry,
        spec,
        last_boxes=[],
        reason="noop after ['swipe_down']",
        complete_json=lambda **kwargs: {"labels": ["tile", "2048"]},
    )
    assert got == 0
    assert spec.labels == ["tile"]


def test_maybe_rewrite_labels_skips_full_2048_occupancy(tmp_path: Path) -> None:
    from avatar.gsm.optimize import maybe_rewrite_labels
    from avatar.gsm.registry import GsmRegistry
    from avatar.gsm.rule.detector import DetectedBox
    from avatar.schema import GsmSpec

    registry = GsmRegistry("2048", max_rewrites=3)
    spec = GsmSpec(
        name="choose_swipe",
        kind="rule",
        fixture="2048",
        labels=["tile"],
        action_space=["swipe_down"],
        enabled=True,
    )
    registry.specs["choose_swipe"] = spec
    boxes = [
        DetectedBox(label="tile", x=(i % 4) / 4, y=(i // 4) / 4, w=0.25, h=0.25)
        for i in range(9)
    ]
    n = {"i": 0}

    def complete_json(**kwargs):
        n["i"] += 1
        return {"labels": ["tile-2"]}

    got = maybe_rewrite_labels(
        registry, spec, last_boxes=boxes, reason="", complete_json=complete_json
    )
    assert got == 0
    assert n["i"] == 0
    assert spec.labels == ["tile"]



