from __future__ import annotations

from pathlib import Path

from PIL import Image

from avatar.gsm.rule.detector import DetectedBox, FakeDetector
from loop.agent.loop import CollectAgent, CollectConfig
from loop.agent.prompts import system_for, user_prompt
from loop.fixtures.fake import FakeFixture
from loop.log.schema import StepLog
from loop.log.store import EpisodeStore
from loop.os.ground import (
    box_range,
    boxes_are_near,
    boxes_payload,
    filter_grounded,
    format_grounded,
    grounded_with_loot,
    paint_boxes,
    visual_labels_for,
)
from loop.os.tables import prompt_listing


def test_visual_labels_elden_only() -> None:
    assert visual_labels_for("elden") == [
        "doorway",
        "stone arch",
        "golden trail",
        "glowing item",
    ]
    assert visual_labels_for("fake") == []
    assert visual_labels_for("") == []
    assert CollectConfig().dino is False
    assert CollectConfig().gui is True
    assert CollectConfig().gui_device == "cpu"


def test_filter_grounded_picks_one_walk_target() -> None:
    boxes = [
        DetectedBox(label="person", x=0.40, y=0.50, w=0.18, h=0.28, score=0.72),
        DetectedBox(label="fog wall", x=0.05, y=0.05, w=0.90, h=0.80, score=0.61),
        DetectedBox(label="ground", x=0.30, y=0.70, w=0.20, h=0.10, score=0.50),
        DetectedBox(label="doorway stone arch", x=0.52, y=0.28, w=0.16, h=0.24, score=0.38),
        DetectedBox(label="doorway", x=0.55, y=0.30, w=0.14, h=0.22, score=0.55),
        DetectedBox(label="golden glowing trail on the ground", x=0.48, y=0.62, w=0.12, h=0.08, score=0.42),
        DetectedBox(label="golden trail", x=0.47, y=0.60, w=0.10, h=0.08, score=0.51),
        DetectedBox(label="stone arch", x=0.20, y=0.25, w=0.18, h=0.20, score=0.48),
    ]
    kept = filter_grounded(boxes)
    assert [b.label for b in kept] == ["golden trail", "doorway", "stone arch"]
    assert kept[0].score == 0.51
    text = format_grounded(kept)
    assert text.startswith("Hint: loot glow")
    assert "stone arch" in text
    assert "stick_l_" in text
    assert "person" not in text
    assert "fog" not in text
    assert format_grounded([]) == ""


def test_filter_grounded_drops_locked_kind() -> None:
    door = DetectedBox(label="doorway", x=0.55, y=0.30, w=0.14, h=0.22, score=0.55)
    arch = DetectedBox(label="stone arch", x=0.20, y=0.25, w=0.18, h=0.20, score=0.48)
    kept = filter_grounded([door, arch], drop=["doorway"])
    assert [b.label for b in kept] == ["stone arch"]


def test_filter_grounded_keeps_close_centered_doorway() -> None:
    close = DetectedBox(label="doorway", x=0.20, y=0.10, w=0.60, h=0.75, score=0.52)
    off = DetectedBox(label="doorway", x=0.70, y=0.10, w=0.55, h=0.80, score=0.60)
    huge = DetectedBox(label="doorway", x=0.02, y=0.02, w=0.96, h=0.96, score=0.70)
    trail = DetectedBox(label="golden trail", x=0.42, y=0.68, w=0.12, h=0.08, score=0.80)
    kept = filter_grounded([close, off, huge, trail])
    assert [b.label for b in kept] == ["golden trail", "doorway"]
    assert kept[1].w == 0.60
    text = format_grounded(kept)
    assert "interact check" in text
    assert "At doorway" in text
    assert "golden trail" in text


def test_filter_grounded_trail_outranks_distant_doorway() -> None:
    door = DetectedBox(label="doorway", x=0.55, y=0.30, w=0.14, h=0.22, score=0.55)
    trail = DetectedBox(label="golden trail", x=0.47, y=0.60, w=0.10, h=0.08, score=0.51)
    kept = filter_grounded([door, trail])
    assert [b.label for b in kept] == ["golden trail", "doorway"]
    text = format_grounded(kept)
    assert text.startswith("Hint: loot glow")
    assert "pickup on a corpse" in text
    assert "Also: doorway" in text
    assert "interact check" not in text


def test_filter_grounded_keeps_oversized_trail() -> None:
    huge = DetectedBox(label="golden trail", x=0.10, y=0.25, w=0.70, h=0.65, score=0.55)
    door = DetectedBox(label="doorway", x=0.55, y=0.30, w=0.14, h=0.22, score=0.55)
    kept = filter_grounded([huge, door])
    assert [b.label for b in kept] == ["golden trail", "doorway"]
    assert kept[0].w == 0.70


def test_gold_glows_and_chroma_backup() -> None:
    from loop.os.saliency import gold_glows

    img = Image.new("RGB", (64, 36), (20, 20, 30))
    for x in range(28, 36):
        for y in range(20, 26):
            img.putpixel((x, y), (200, 160, 40))
    pts = gold_glows(img)
    assert pts
    chroma = grounded_with_loot(img, [])
    assert "loot glow (chroma)" in chroma
    assert "pickup on a corpse" in chroma
    door = DetectedBox(label="doorway", x=0.55, y=0.30, w=0.14, h=0.22, score=0.55)
    mixed = grounded_with_loot(img, [door])
    assert "Hint: doorway" in mixed
    assert "loot glow (chroma)" in mixed
    trail = DetectedBox(label="golden trail", x=0.47, y=0.60, w=0.10, h=0.08, score=0.51)
    dino = grounded_with_loot(img, [trail])
    assert "chroma" not in dino
    assert "loot glow (golden trail)" in dino
    assert grounded_with_loot(None, []) == ""


def test_user_prompt_loot_glow_is_a_walk() -> None:
    grounded = format_grounded(
        [DetectedBox(label="golden trail", x=0.47, y=0.60, w=0.10, h=0.08, score=0.51)]
    )
    text = user_prompt(
        actions=prompt_listing("xbox360"),
        arm="no_skill",
        grounded=grounded,
    )
    assert "Loot glow is a pickup" in text
    assert "Do not ignore it" in text
    locked = user_prompt(
        actions=prompt_listing("xbox360"),
        arm="no_skill",
        grounded=grounded,
        lock_note="CV: glyph 'a' already failed.",
    )
    assert "Loot glow is still a pickup" in locked


def test_box_range_is_2d_proxy_not_true_depth() -> None:
    far = DetectedBox(label="doorway", x=0.55, y=0.30, w=0.14, h=0.22, score=0.55)
    mid = DetectedBox(label="doorway", x=0.40, y=0.30, w=0.25, h=0.28, score=0.55)
    near = DetectedBox(label="doorway", x=0.20, y=0.10, w=0.60, h=0.75, score=0.52)
    trail_feet = DetectedBox(label="golden trail", x=0.42, y=0.68, w=0.12, h=0.08, score=0.80)
    assert box_range(far) == "far"
    assert box_range(mid) == "mid"
    assert box_range(near) == "near"
    assert box_range(trail_feet) == "near"
    assert boxes_are_near([far]) is False
    assert boxes_are_near([near]) is True
    text = format_grounded([far])
    assert " far " in f" {text} "
    assert "~1600ms" in text
    assert "near, interact check" in format_grounded([near])


def test_user_prompt_prompt_blocks_walk_through_loot() -> None:
    grounded = format_grounded(
        [DetectedBox(label="golden trail", x=0.47, y=0.60, w=0.10, h=0.08, score=0.51)]
    )
    text = user_prompt(
        actions=prompt_listing("xbox360"),
        arm="no_skill",
        grounded=grounded,
        prompt_text="Pick up",
        prompt_box=True,
    )
    assert "Do not walk through it" in text
    assert "Walk stick_l toward it" not in text
    prior = system_for("no_skill").lower()
    assert "2d screenshot" in prior
    assert "near/mid/far" in prior


def test_filter_grounded_demote_is_hint_rank_only() -> None:
    door = DetectedBox(label="doorway", x=0.55, y=0.30, w=0.14, h=0.22, score=0.55)
    trail = DetectedBox(label="golden trail", x=0.47, y=0.60, w=0.10, h=0.08, score=0.51)
    from loop.os.ground import lock_demote_labels

    kept = filter_grounded([door, trail], demote=["doorway"])
    assert [b.label for b in kept] == ["golden trail", "doorway"]
    text = format_grounded(kept, demote=["doorway"])
    assert text.startswith("Hint: loot glow")
    assert "golden trail" in text
    assert "stick_l_" in text
    assert "not stick_l into it" in text
    assert "interact check" not in text
    assert lock_demote_labels([door, trail]) == {"doorway"}


def test_paint_boxes_marks_copy() -> None:
    image = Image.new("RGB", (320, 180), (12, 12, 12))
    box = DetectedBox(label="doorway", x=0.50, y=0.35, w=0.20, h=0.30, score=0.91)
    painted = paint_boxes(image, [box])
    assert painted.size == image.size
    assert painted.tobytes() != image.tobytes()
    assert image.getpixel((0, 0)) == (12, 12, 12)
    text = format_grounded([box])
    assert text.startswith("Hint: doorway")
    assert "stick_l_" in text
    assert "0.60,0.50" in text
    payload = boxes_payload([box])
    assert payload[0]["label"] == "doorway"
    assert payload[0]["score"] == 0.91


def test_user_prompt_grounded_is_not_a_glyph() -> None:
    grounded = format_grounded(
        [DetectedBox(label="doorway", x=0.52, y=0.36, w=0.20, h=0.24, score=0.55)]
    )
    text = user_prompt(
        actions=prompt_listing("xbox360"),
        arm="no_skill",
        prompt_box=False,
        bright_patches="0.22,0.41→stick_l_n(ahead)",
        grounded=grounded,
        last_mad=0.004,
    )
    assert "Hint:" in text
    assert grounded in text
    assert "Grounded objects" not in text
    assert "Bright patches" not in text
    assert "not a required walk" in text.lower()
    assert "Almost still / blocked" in text
    assert "Painted box is a hint" in text
    assert "Still read the PROMPT inset" not in text
    assert "Do not tap." not in text
    prior = system_for("no_skill").lower()
    assert "hint" in prior
    assert "bright patches" in prior
    assert '"tapped": ["b"]' not in system_for("no_skill")
    tap = user_prompt(
        actions=prompt_listing("xbox360"),
        arm="no_skill",
        prompt_box=True,
        grounded=grounded,
    )
    assert "Hint:" in tap
    assert "prompt bar yes" not in tap.lower()


def test_prompt_looks_locked_and_stuck() -> None:
    from loop.os.saliency import prompt_looks_locked, skip_interact_interrupt, stuck_prompt

    assert prompt_looks_locked("Opens only to those carrying a finger") is True
    assert prompt_looks_locked("A key is required") is True
    assert prompt_looks_locked("Open") is False
    assert prompt_looks_locked("Read message") is False
    assert prompt_looks_locked("") is False
    assert (
        stuck_prompt(prompt_on=True, glyph="a", last_interrupt_glyph="a")
        is True
    )
    assert stuck_prompt(prompt_on=True, glyph="a", last_interrupt_glyph=None) is False
    assert (
        skip_interact_interrupt(
            prompt_text="",
            prompt_on=True,
            glyph="a",
            last_interrupt_glyph="a",
        )
        is True
    )
    assert (
        skip_interact_interrupt(
            prompt_text="Open",
            prompt_on=True,
            glyph="a",
            last_interrupt_glyph=None,
        )
        is False
    )


def test_update_last_interrupt_glyph_keeps_look_on_same_bar() -> None:
    from loop.os.saliency import update_last_interrupt_glyph

    after_tap = update_last_interrupt_glyph(
        None, interrupt=True, glyph="a", prompt_on=True, moved=False
    )
    assert after_tap == "a"
    looked = update_last_interrupt_glyph(
        "a", interrupt=False, glyph="a", prompt_on=True, moved=True
    )
    assert looked == "a"
    flicker = update_last_interrupt_glyph(
        "a", interrupt=False, glyph=None, prompt_on=False, moved=False
    )
    assert flicker == "a"
    left = update_last_interrupt_glyph(
        "a", interrupt=False, glyph=None, prompt_on=False, moved=True
    )
    assert left is None
    other = update_last_interrupt_glyph(
        "a", interrupt=False, glyph="b", prompt_on=True, moved=False
    )
    assert other is None


def test_collect_dino_overlay_keeps_raw_obs(tmp_path: Path) -> None:
    fixture = FakeFixture(steps_until_fail=2)
    store = EpisodeStore(tmp_path)
    detector = FakeDetector(
        boxes=[
            DetectedBox(label="person", x=0.4, y=0.5, w=0.2, h=0.3, score=0.8),
            DetectedBox(label="doorway", x=0.4, y=0.35, w=0.25, h=0.3, score=0.8),
            DetectedBox(label="fog wall", x=0.1, y=0.1, w=0.8, h=0.7, score=0.6),
        ]
    )
    agent = CollectAgent(
        fixture,
        store,
        detector=detector,
        config=CollectConfig(
            lives=1,
            max_steps=2,
            skip_models=True,
            arm="no_skill",
            dino=True,
            dino_labels=["doorway", "person", "fog wall"],
        ),
    )
    agent.run_session()
    steps = [StepLog.model_validate(row) for row in store.read_steps()]
    assert steps
    assert any(row.dino for row in steps)
    assert any(row.observation.dino_screenshot_ref for row in steps)
    assert any(row.observation.vlm_screenshot_ref for row in steps)
    row = next(s for s in steps if s.observation.dino_screenshot_ref)
    obs = tmp_path / row.observation.screenshot_ref
    dino = tmp_path / row.observation.dino_screenshot_ref
    vlm = tmp_path / (row.observation.vlm_screenshot_ref or "")
    assert obs.exists()
    assert dino.exists()
    assert vlm.exists()
    assert row.prompt_on is not None
    obs_img = Image.open(obs).convert("RGB")
    dino_img = Image.open(dino).convert("RGB")
    assert obs_img.tobytes() != dino_img.tobytes()
    assert "Hint:" in agent.last_user_prompt
    assert "doorway" in agent.last_user_prompt
    assert "person" not in agent.last_user_prompt
    assert "Bright patches" not in agent.last_user_prompt
    assert "Grounded objects" not in agent.last_user_prompt
    assert [box["label"] for box in row.dino] == ["doorway"]


def test_skip_models_does_not_load_dino(tmp_path: Path) -> None:
    fixture = FakeFixture(steps_until_fail=2)
    store = EpisodeStore(tmp_path)
    agent = CollectAgent(
        fixture,
        store,
        config=CollectConfig(lives=1, max_steps=2, skip_models=True, arm="no_skill"),
    )
    agent.run_session()
    steps = [StepLog.model_validate(row) for row in store.read_steps()]
    assert steps
    assert all(not row.observation.dino_screenshot_ref for row in steps)
    assert all(not row.dino for row in steps)
    assert "Hint:" not in agent.last_user_prompt
    assert "Grounded objects" not in agent.last_user_prompt


class _BoomGateway:
    def complete_json(self, **kwargs):  # noqa: ANN003
        raise AssertionError("VLM should not be called on interact interrupt")


class _StillFixture(FakeFixture):
    def __init__(self, image: Image.Image, **kwargs) -> None:
        super().__init__(**kwargs)
        self._still = image.convert("RGB")

    def grab_image(self) -> Image.Image:
        return self._still.copy()

    def scorer(self, image: Image.Image) -> dict:
        del image
        return {"health_frac": 1.0, "stamina_frac": 1.0, "fail_screen": False}

    def terminal(self, metrics, image) -> str:
        del metrics, image
        return "alive"


def _prompt_fixture(name: str) -> Image.Image:
    path = Path(__file__).resolve().parent / "fixtures" / name
    assert path.is_file(), f"missing {path}"
    return Image.open(path).convert("RGB")


def test_detect_interact_cv_on_open_door() -> None:
    from loop.os.ground import detect_interact

    frame = _prompt_fixture("prompt_open_door.jpg")
    hint = detect_interact(frame, detector=None)
    assert hint.on is True
    assert hint.glyph == "a"
    assert hint.source == "cv"


def test_detect_interact_cv_on_read_message() -> None:
    from loop.os.ground import detect_interact

    frame = _prompt_fixture("prompt_read_message.jpg")
    hint = detect_interact(frame, detector=None)
    assert hint.on is True
    assert hint.source == "cv"


def test_detect_interact_dino_or_when_cv_off() -> None:
    from loop.os.ground import detect_interact

    dark = Image.new("RGB", (320, 180), (12, 12, 12))
    detector = FakeDetector(
        boxes=[DetectedBox(label="A button", x=0.35, y=0.40, w=0.20, h=0.20, score=0.55)]
    )
    hint = detect_interact(dark, detector=detector, cv_on=False, cv_glyph=None)
    assert hint.on is True
    assert hint.glyph == "a"
    assert hint.source == "dino"
    assert hint.n_kept == 1


def test_detect_interact_dino_unlabeled_has_no_glyph() -> None:
    from loop.os.ground import detect_interact, interrupt_ready

    dark = Image.new("RGB", (320, 180), (12, 12, 12))
    detector = FakeDetector(
        boxes=[DetectedBox(label="interact prompt", x=0.30, y=0.40, w=0.40, h=0.15, score=0.70)]
    )
    hint = detect_interact(dark, detector=detector, cv_on=False, cv_glyph=None)
    assert hint.on is True
    assert hint.glyph is None
    assert hint.source == "dino"
    assert interrupt_ready(hint, dino_streak=2) is False


def test_interrupt_ready_cv_needs_copy() -> None:
    from loop.os.ground import InteractHint, interrupt_ready, prompt_confirmed

    cv = InteractHint(on=True, glyph="x", source="cv")
    assert prompt_confirmed(cv, "") is False
    assert interrupt_ready(cv, prompt_text="") is False
    assert interrupt_ready(cv, prompt_text="Open") is True
    both = InteractHint(on=True, glyph="a", source="cv+dino", n_kept=1)
    assert prompt_confirmed(both, "") is True
    assert interrupt_ready(both, prompt_text="") is True
    dino = InteractHint(on=True, glyph="a", source="dino", n_kept=1)
    assert interrupt_ready(dino, dino_streak=1) is False
    assert interrupt_ready(dino, dino_streak=2) is True


def test_detect_interact_cv_on_no_default_glyph() -> None:
    from loop.os.ground import detect_interact

    dark = Image.new("RGB", (320, 180), (12, 12, 12))
    hint = detect_interact(dark, detector=None, cv_on=True, cv_glyph=None)
    assert hint.on is True
    assert hint.glyph is None
    assert hint.source == "cv"


def test_filter_prompt_boxes_drops_clutter() -> None:
    from loop.os.ground import filter_prompt_boxes

    kept = filter_prompt_boxes(
        [
            DetectedBox(label="person", x=0.4, y=0.4, w=0.2, h=0.2, score=0.9),
            DetectedBox(label="interact prompt", x=0.3, y=0.4, w=0.4, h=0.15, score=0.20),
            DetectedBox(label="A button", x=0.35, y=0.42, w=0.12, h=0.10, score=0.51),
        ]
    )
    assert [b.label for b in kept] == ["A button"]


def test_arrival_format_has_no_walk_vector() -> None:
    from loop.os.ground import is_arrival

    close = DetectedBox(label="doorway", x=0.20, y=0.10, w=0.60, h=0.75, score=0.52)
    far = DetectedBox(label="doorway", x=0.55, y=0.30, w=0.14, h=0.22, score=0.55)
    trail = DetectedBox(label="golden trail", x=0.42, y=0.68, w=0.12, h=0.08, score=0.80)
    assert is_arrival([close]) is True
    assert "stick_l_" not in format_grounded([close])
    assert is_arrival([far]) is False
    assert "stick_l_" in format_grounded([far])
    assert is_arrival([trail, close]) is True
    mixed = format_grounded([trail, close])
    assert "At doorway" in mixed
    assert "loot glow" in mixed
    assert "golden trail" in mixed
    locked = format_grounded([trail, close], locked=True)
    assert "condition" in locked
    assert "interact check" not in locked


def test_arrival_walk_clamps_to_burst() -> None:
    from loop.log.schema import ActionLog
    from loop.os.chords import apply_chord_duration

    long = apply_chord_duration(
        ActionLog(stick_l="n", duration_ms=1500), burst_ms=400, stick_ms=400
    )
    assert long.duration_ms == 400
    uncapped = apply_chord_duration(
        ActionLog(stick_l="n", duration_ms=1500), burst_ms=400, stick_ms=2000
    )
    assert uncapped.duration_ms == 1500


def test_open_door_interrupt_skips_vlm(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("loop.agent.loop.read_prompt_text", lambda _img: "Open")
    frame = _prompt_fixture("prompt_open_door.jpg")
    fixture = _StillFixture(frame, steps_until_fail=None, min_survived_steps=1)
    store = EpisodeStore(tmp_path)
    agent = CollectAgent(
        fixture,
        store,
        gateway=_BoomGateway(),  # type: ignore[arg-type]
        config=CollectConfig(
            lives=1,
            max_steps=1,
            skip_models=False,
            arm="no_skill",
            dino=False,
            timeout_s=30,
        ),
    )
    agent.run_session()
    steps = [StepLog.model_validate(row) for row in store.read_steps()]
    assert steps
    row = steps[0]
    assert row.prompt_on is True
    assert row.prompt_glyph == "a"
    assert row.action.tapped == ["a"]
    assert row.action.stick_l is None
    assert "interact-interrupt" in row.thought
    assert "waiting on VLM" not in row.thought


def test_read_message_interrupt_fires(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("loop.agent.loop.read_prompt_text", lambda _img: "Read message")
    frame = _prompt_fixture("prompt_read_message.jpg")
    fixture = _StillFixture(frame, steps_until_fail=None, min_survived_steps=1)
    store = EpisodeStore(tmp_path)
    agent = CollectAgent(
        fixture,
        store,
        gateway=_BoomGateway(),  # type: ignore[arg-type]
        config=CollectConfig(
            lives=1,
            max_steps=1,
            skip_models=False,
            arm="no_skill",
            dino=False,
            timeout_s=30,
        ),
    )
    agent.run_session()
    steps = [StepLog.model_validate(row) for row in store.read_steps()]
    assert steps
    row = steps[0]
    assert row.prompt_on is True
    assert row.action.tapped == ["a"]
    assert "interact-interrupt" in row.thought


from loop.model.gateway import ModelCall


class _CountGateway:
    def __init__(self) -> None:
        self.calls = 0

    def complete_json(self, **kwargs):  # noqa: ANN003
        self.calls += 1
        return ModelCall(
            text="{}",
            parsed={"thought": "walk north", "stick_l": "n", "duration_ms": 400},
            latency_ms=1.0,
            model="dummy",
        )


def test_lock_text_skips_interrupt(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "loop.agent.loop.read_prompt_text",
        lambda _img: "Opens only to those carrying a finger",
    )
    frame = _prompt_fixture("prompt_open_door.jpg")
    fixture = _StillFixture(frame, steps_until_fail=None, min_survived_steps=1)
    store = EpisodeStore(tmp_path)
    gateway = _CountGateway()
    agent = CollectAgent(
        fixture,
        store,
        gateway=gateway,  # type: ignore[arg-type]
        config=CollectConfig(
            lives=1,
            max_steps=1,
            skip_models=False,
            arm="no_skill",
            dino=False,
            timeout_s=30,
        ),
    )
    agent.run_session()
    steps = [StepLog.model_validate(row) for row in store.read_steps()]
    assert gateway.calls == 1
    assert steps
    assert "interact-interrupt" not in (steps[0].thought or "")
    assert "PROMPT TEXT" in agent.last_user_prompt
    assert "carrying a finger" in agent.last_user_prompt


def test_stuck_bar_yields_vlm_after_interrupt(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("loop.agent.loop.read_prompt_text", lambda _img: "Open")
    frame = _prompt_fixture("prompt_open_door.jpg")
    fixture = _StillFixture(frame, steps_until_fail=None, min_survived_steps=1)
    store = EpisodeStore(tmp_path)
    gateway = _CountGateway()
    agent = CollectAgent(
        fixture,
        store,
        gateway=gateway,  # type: ignore[arg-type]
        config=CollectConfig(
            lives=1,
            max_steps=2,
            skip_models=False,
            arm="no_skill",
            dino=False,
            timeout_s=30,
        ),
    )
    agent.run_session()
    steps = [StepLog.model_validate(row) for row in store.read_steps()]
    assert len(steps) == 2
    assert "interact-interrupt" in (steps[0].thought or "")
    assert steps[0].action.tapped == ["a"]
    assert gateway.calls == 1
    assert "interact-interrupt" not in (steps[1].thought or "")
    assert "same face" in agent.last_user_prompt.lower()


def test_empty_ocr_cv_does_not_walk_through_prompt(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("loop.agent.loop.read_prompt_text", lambda _img: "")
    frame = _prompt_fixture("prompt_open_door.jpg")
    fixture = _StillFixture(frame, steps_until_fail=None, min_survived_steps=1)
    store = EpisodeStore(tmp_path)
    gateway = _CountGateway()
    agent = CollectAgent(
        fixture,
        store,
        gateway=gateway,  # type: ignore[arg-type]
        config=CollectConfig(
            lives=1,
            max_steps=1,
            skip_models=False,
            arm="no_skill",
            dino=False,
            timeout_s=30,
        ),
    )
    agent.run_session()
    steps = [StepLog.model_validate(row) for row in store.read_steps()]
    assert gateway.calls == 1
    assert steps
    assert "interact-interrupt" not in (steps[0].thought or "")
    assert steps[0].action.tapped == []
    assert steps[0].result.invoked_gsm != "approach"
    assert "do not walk" in agent.last_user_prompt.lower()


def test_last_interrupt_glyph_persists_across_episodes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("loop.agent.loop.read_prompt_text", lambda _img: "Open")
    frame = _prompt_fixture("prompt_open_door.jpg")
    fixture = _StillFixture(frame, steps_until_fail=None, min_survived_steps=1)
    store = EpisodeStore(tmp_path)
    gateway = _CountGateway()
    agent = CollectAgent(
        fixture,
        store,
        gateway=gateway,  # type: ignore[arg-type]
        config=CollectConfig(
            lives=2,
            max_steps=1,
            skip_models=False,
            arm="no_skill",
            dino=False,
            timeout_s=30,
        ),
    )
    agent.run_session()
    steps = [StepLog.model_validate(row) for row in store.read_steps()]
    assert len(steps) == 2
    assert "interact-interrupt" in (steps[0].thought or "")
    assert "interact-interrupt" not in (steps[1].thought or "")
    assert gateway.calls == 1
    assert agent._last_interrupt_glyph == "a"


def test_dino_or_interrupt_when_cv_off(tmp_path: Path) -> None:
    dark = Image.new("RGB", (320, 180), (12, 12, 12))
    fixture = _StillFixture(dark, steps_until_fail=None, min_survived_steps=1)
    store = EpisodeStore(tmp_path)
    detector = FakeDetector(
        boxes=[DetectedBox(label="A button", x=0.35, y=0.40, w=0.20, h=0.20, score=0.62)]
    )
    gateway = _CountGateway()
    agent = CollectAgent(
        fixture,
        store,
        detector=detector,
        gateway=gateway,  # type: ignore[arg-type]
        config=CollectConfig(
            lives=1,
            max_steps=2,
            skip_models=False,
            arm="no_skill",
            dino=True,
            dino_labels=["doorway"],
            timeout_s=30,
        ),
    )
    agent.run_session()
    steps = [StepLog.model_validate(row) for row in store.read_steps()]
    assert len(steps) == 2
    assert gateway.calls == 1
    assert "interact-interrupt" not in (steps[0].thought or "")
    assert "interact-interrupt" in (steps[1].thought or "")
    assert agent._interrupt_dino == 1


def test_dino_generic_prompt_does_not_skip_vlm(tmp_path: Path) -> None:
    from loop.os.ground import detect_interact, interrupt_ready

    dark = Image.new("RGB", (320, 180), (12, 12, 12))
    detector = FakeDetector(
        boxes=[DetectedBox(label="interact prompt", x=0.30, y=0.40, w=0.40, h=0.15, score=0.70)]
    )
    hint = detect_interact(dark, detector=detector, cv_on=False, cv_glyph=None)
    assert hint.on is True
    assert hint.glyph is None
    assert hint.source == "dino"
    assert interrupt_ready(hint, dino_streak=5) is False
    fixture = _StillFixture(dark, steps_until_fail=None, min_survived_steps=1)
    store = EpisodeStore(tmp_path)
    gateway = _CountGateway()
    agent = CollectAgent(
        fixture,
        store,
        detector=detector,
        gateway=gateway,  # type: ignore[arg-type]
        config=CollectConfig(
            lives=1,
            max_steps=1,
            skip_models=False,
            arm="no_skill",
            dino=True,
            dino_labels=["doorway"],
            timeout_s=30,
        ),
    )
    agent.run_session()
    steps = [StepLog.model_validate(row) for row in store.read_steps()]
    assert gateway.calls == 1
    assert steps
    assert "interact-interrupt" not in (steps[0].thought or "")


class _EmptyGateway:
    def __init__(self) -> None:
        self.calls = 0

    def complete_json(self, **kwargs):  # noqa: ANN003
        self.calls += 1
        return ModelCall(
            text="{}",
            parsed={"thought": "not sure"},
            latency_ms=1.0,
            model="dummy",
        )


def test_empty_json_dino_only_does_not_tap_a(tmp_path: Path) -> None:
    dark = Image.new("RGB", (320, 180), (12, 12, 12))
    fixture = _StillFixture(dark, steps_until_fail=None, min_survived_steps=1)
    store = EpisodeStore(tmp_path)
    detector = FakeDetector(
        boxes=[DetectedBox(label="A button", x=0.35, y=0.40, w=0.20, h=0.20, score=0.62)]
    )
    gateway = _EmptyGateway()
    agent = CollectAgent(
        fixture,
        store,
        detector=detector,
        gateway=gateway,  # type: ignore[arg-type]
        config=CollectConfig(
            lives=1,
            max_steps=1,
            skip_models=False,
            arm="no_skill",
            dino=True,
            dino_labels=["doorway"],
            timeout_s=30,
        ),
    )
    agent.run_session()
    steps = [StepLog.model_validate(row) for row in store.read_steps()]
    assert gateway.calls == 1
    row = steps[0]
    assert row.prompt_on is True
    assert row.prompt_source == "dino"
    assert "interact-interrupt" not in (row.thought or "")
    assert row.action.tapped == []
