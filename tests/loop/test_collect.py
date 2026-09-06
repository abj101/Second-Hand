from __future__ import annotations

from pathlib import Path

from loop.agent.loop import CollectAgent, CollectConfig
from loop.agent.prompts import lock_evidence_line, system_for, user_prompt
from loop.fixtures.fake import FakeFixture
from loop.log.schema import EpisodeLog, StepLog
from loop.log.store import EpisodeStore
from loop.memory.skills import Skill, SkillBank
from loop.memory.store import MemoryStore
from loop.os.tables import prompt_listing


def test_fake_collect_roundtrip(tmp_path: Path) -> None:
    fixture = FakeFixture(steps_until_fail=3)
    store = EpisodeStore(tmp_path)
    memory = MemoryStore(tmp_path / "memory.sqlite")
    agent = CollectAgent(
        fixture,
        store,
        memory=memory,
        config=CollectConfig(lives=2, max_steps=8, skip_models=True, arm="dump_all", memory=True),
    )
    summary = agent.run_session()
    assert summary.fixture == "fake"
    assert summary.arm == "dump_all"
    assert summary.episodes == 2
    assert summary.stop_requested is False
    assert summary.flush_completed is True
    assert summary.unique_device_names
    steps = store.read_steps()
    assert steps
    assert {row["fixture"] for row in steps} == {"fake"}
    assert any(row.get("obs_age_ms") is not None for row in steps)
    assert any(row.get("perceive_ms") is not None for row in steps)
    assert any(row.get("observation", {}).get("after_screenshot_ref") for row in steps)
    assert any(row.get("return_to_go") is not None for row in steps)
    assert memory.size() > 0
    fail_frame = fixture.grab_image()
    hits = memory.recall(fail_frame, k=3)
    assert hits
    memory.close()


def test_until_fail_stops_after_first_death(tmp_path: Path) -> None:
    fixture = FakeFixture(steps_until_fail=2)
    store = EpisodeStore(tmp_path)
    agent = CollectAgent(
        fixture,
        store,
        config=CollectConfig(
            lives=5,
            max_steps=8,
            skip_models=True,
            arm="no_skill",
            until_fail=True,
        ),
    )
    summary = agent.run_session()
    assert summary.episodes == 1
    episodes = [EpisodeLog.model_validate(row) for row in store.read_episodes()]
    assert episodes[0].terminal == "fail"


def test_ratchet_does_not_remember(tmp_path: Path) -> None:
    fixture = FakeFixture(steps_until_fail=2, min_survived_steps=2)
    store = EpisodeStore(tmp_path)
    memory = MemoryStore(tmp_path / "memory.sqlite")
    bank = SkillBank(tmp_path / "skills.json", n_min=5, tau=0.1, cap=8)
    bank.add_skill(
        Skill(
            id="keep_a",
            recognize="bars visible",
            avoid_or_do={"chord": {"tapped": ["a"], "duration_ms": 400}},
            end="still alive",
        )
    )
    agent = CollectAgent(
        fixture,
        store,
        memory=memory,
        skills=bank,
        config=CollectConfig(
            lives=12,
            max_steps=4,
            skip_models=True,
            arm="ratchet",
            skill_off_frac=0.5,
            rng_seed=0,
        ),
    )
    agent.run_session()
    assert agent.remember_calls == 0
    assert memory.size() == 0
    assert "remember" not in agent.last_user_prompt
    assert "Tools: recall" in agent.last_user_prompt
    assert "Active skill" in agent.last_user_prompt
    assert '"tool": "recall"' in system_for("ratchet")
    assert '"tool": "remember"' not in system_for("ratchet")
    episodes = [EpisodeLog.model_validate(row) for row in store.read_episodes()]
    flags = {ep.skill_on for ep in episodes}
    assert True in flags and False in flags
    on = [ep for ep in episodes if ep.skill_on]
    assert all(ep.active_skill_id == "keep_a" for ep in on)
    assert all(ep.active_skill_id == "keep_a" for ep in episodes)
    memory.close()


def test_pixel_changed_is_this_step(tmp_path: Path) -> None:
    fixture = FakeFixture(steps_until_fail=2, good_action="a", bad_action="b")
    store = EpisodeStore(tmp_path)
    agent = CollectAgent(
        fixture,
        store,
        config=CollectConfig(lives=1, max_steps=2, skip_models=True, arm="no_skill"),
    )
    agent.run_session()
    steps = [StepLog.model_validate(row) for row in store.read_steps()]
    assert steps
    assert all(row.observation.after_screenshot_ref for row in steps)
    assert all(row.result.pixel_mad is not None for row in steps)


def test_survey_looks_before_round_robin(tmp_path: Path) -> None:
    fixture = FakeFixture(steps_until_fail=None, min_survived_steps=1)
    store = EpisodeStore(tmp_path)
    agent = CollectAgent(
        fixture,
        store,
        config=CollectConfig(
            lives=1,
            max_steps=4,
            skip_models=True,
            arm="no_skill",
            survey=True,
            timeout_s=30,
        ),
    )
    agent.run_session()
    steps = [StepLog.model_validate(row) for row in store.read_steps()]
    assert len(steps) == 4
    assert all(row.result.invoked_gsm == "survey" for row in steps)
    assert [row.action.stick_r for row in steps] == ["e", "e", "w", "w"]


def test_no_skill_prompt_has_no_tools() -> None:
    text = user_prompt(actions=prompt_listing("xbox360"), arm="no_skill")
    assert "recall" not in text
    assert "remember" not in text
    assert "Memory tools are off" in text


def test_user_prompt_includes_recent_chords() -> None:
    text = user_prompt(
        actions=prompt_listing("xbox360"),
        arm="no_skill",
        recent_chords=["b", "b", "stick_l_n"],
    )
    assert "Recent device names: b, b, stick_l_n" in text


def test_schema_examples_do_not_prime_button_b() -> None:
    for arm in ("no_skill", "dump_all", "ratchet"):
        text = system_for(arm)
        assert '"tapped": ["b"]' not in text
        assert '"held": ["b"]' not in text
        assert "non-empty" in text
        assert "player" in text or "playing" in text


def test_user_prompt_requires_nonempty_and_pixel_feedback() -> None:
    text = user_prompt(
        actions=prompt_listing("xbox360"),
        arm="ratchet",
        last_mad=0.004,
        last_had_prompt=False,
        bright_patches="0.22,0.41",
    )
    assert "Empty JSON is invalid" in text
    assert "motion=0.004" in text
    assert "blocked" in text.lower() or "stick_l_s" in text
    assert "Pick a different device name" not in text
    assert "Bright patches" in text
    assert "Explore anyway" not in text
    assert "Still play" in text
    prompt_text = user_prompt(
        actions=prompt_listing("xbox360"),
        arm="ratchet",
        last_mad=0.0,
        last_had_prompt=True,
        prompt_box=True,
        prompt_glyph="a",
    )
    assert "just used" in prompt_text.lower() or "do not mash" in prompt_text.lower()
    assert "prompt bar yes" not in prompt_text.lower()
    absent = user_prompt(
        actions=prompt_listing("xbox360"),
        arm="ratchet",
        last_mad=0.0,
        prompt_box=False,
        burst_caption="burst 4 frames, mad 0.02→0.08→0.03, after_prompt=no; last result: stick_l_n → blocked/no-change mad=0.002 prompt_now=no",
        second_image="Single image: full frame plus labeled PROMPT inset banner (zoomed bottom-center bar).",
    )
    assert "Almost still / blocked" in absent
    assert "Still read the PROMPT inset" not in absent
    assert "burst 4 frames" in absent
    assert "PROMPT inset" in absent
    assert "Retry that glyph" not in absent
    assert "Do not tap." not in absent


def test_user_prompt_includes_prompt_text() -> None:
    text = user_prompt(
        actions=prompt_listing("xbox360"),
        arm="no_skill",
        prompt_text="Opens only to those carrying a finger",
    )
    assert "PROMPT TEXT" in text
    assert "carrying a finger" in text
    assert "do not mash" in text.lower()
    locked = user_prompt(
        actions=prompt_listing("xbox360"),
        arm="no_skill",
        prompt_locked=True,
    )
    assert "condition" in locked.lower()
    assert "PROMPT TEXT" not in locked
    grounded_lock = user_prompt(
        actions=prompt_listing("xbox360"),
        arm="no_skill",
        prompt_locked=True,
        grounded="At doorway — interact check.",
    )
    assert "condition, not a tap" in grounded_lock.lower() or "not a tap" in grounded_lock.lower()
    assert "do not long-walk" not in grounded_lock.lower()
    persist = user_prompt(
        actions=prompt_listing("xbox360"),
        arm="no_skill",
        lock_note=lock_evidence_line(glyph="a", kinds=["doorway"]),
        grounded="Hint: doorway 0.52,0.51 score=0.50 (condition; look stick_r or walk away, not stick_l into it)",
    )
    assert "already failed to clear" in persist
    assert "jump, not Open" in persist
    assert "do not long-walk" not in persist.lower()


def test_working_block_is_not_dump_all_tools() -> None:
    text = user_prompt(
        actions=prompt_listing("xbox360"),
        arm="ratchet",
        working_block="Working memory:\n- stick_l_n no-change glyphs=x",
    )
    assert "Working memory:" in text
    assert "Tools: recall" in text
    assert "remember" not in text.lower()
    assert '"tool": "recall"' in system_for("ratchet")
    assert '"tool": "remember"' not in system_for("ratchet")


def test_skip_models_collect_does_not_synth(tmp_path: Path) -> None:
    fixture = FakeFixture(steps_until_fail=2)
    store = EpisodeStore(tmp_path)
    bank_path = tmp_path / "skills.json"
    bank = SkillBank(bank_path, n_min=5, tau=0.1, cap=8)
    agent = CollectAgent(
        fixture,
        store,
        skills=bank,
        config=CollectConfig(lives=3, max_steps=3, skip_models=True, arm="ratchet"),
    )
    agent.run_session()
    assert not bank_path.exists()
    assert bank.active() == []


def test_collect_honors_stop_flag(tmp_path: Path) -> None:
    from loop.live import read, request_stop

    request_stop()
    fixture = FakeFixture(steps_until_fail=3)
    store = EpisodeStore(tmp_path)
    agent = CollectAgent(
        fixture,
        store,
        config=CollectConfig(lives=5, max_steps=4, skip_models=True, arm="no_skill"),
    )
    summary = agent.run_session()
    assert summary.episodes == 0
    assert summary.stop_requested is True
    assert summary.flush_completed is True
    assert read()["phase"] == "stopped"
    summary_path = store.run_dir / "summary.json"
    assert summary_path.exists()
    grounded_path = store.run_dir / "memory_grounded.json"
    assert grounded_path.exists()
    import json

    json.loads(grounded_path.read_text(encoding="utf-8"))
    loaded = json.loads(summary_path.read_text(encoding="utf-8"))
    assert loaded["stop_requested"] is True
    assert loaded["flush_completed"] is True


def test_collect_publishes_live_status(tmp_path: Path) -> None:
    from loop.live import read

    fixture = FakeFixture(steps_until_fail=2)
    store = EpisodeStore(tmp_path)
    agent = CollectAgent(
        fixture,
        store,
        config=CollectConfig(lives=1, max_steps=4, skip_models=True, arm="no_skill"),
    )
    agent.run_session()
    status = read()
    assert status["job"] == "collect"
    assert status["phase"] == "done"
    assert status["fixture"] == "fake"


def test_steps_flush_during_episode(tmp_path: Path) -> None:
    fixture = FakeFixture(steps_until_fail=4)
    store = EpisodeStore(tmp_path)
    seen: list[int] = []
    orig = fixture.execute_chord

    def wrapped(action):
        seen.append(len(store.read_steps()))
        return orig(action)

    fixture.execute_chord = wrapped  # type: ignore[method-assign]
    agent = CollectAgent(
        fixture,
        store,
        config=CollectConfig(lives=1, max_steps=4, skip_models=True, arm="no_skill"),
    )
    agent.run_session()
    assert seen
    assert seen[0] == 0
    if len(seen) > 1:
        assert seen[1] >= 1
    steps = store.read_steps()
    assert steps
    assert any(row.get("return_to_go") is not None for row in steps)


def test_ratchet_samples_active_skills(tmp_path: Path) -> None:
    fixture = FakeFixture(steps_until_fail=2, min_survived_steps=2)
    store = EpisodeStore(tmp_path)
    bank = SkillBank(tmp_path / "skills.json", n_min=5, tau=0.1, cap=8)
    for sid in ("keep_a", "keep_b"):
        bank.add_skill(
            Skill(
                id=sid,
                recognize="bars visible",
                avoid_or_do={"chord": {"tapped": ["a"], "duration_ms": 400}},
            )
        )
    agent = CollectAgent(
        fixture,
        store,
        skills=bank,
        config=CollectConfig(
            lives=24,
            max_steps=2,
            skip_models=True,
            arm="ratchet",
            skill_off_frac=0.5,
            rng_seed=1,
        ),
    )
    agent.run_session()
    scheduled = {row["active_skill_id"] for row in store.read_episodes()}
    assert scheduled == {"keep_a", "keep_b"}


def test_skill_on_executes_verified_chord(tmp_path: Path) -> None:
    fixture = FakeFixture(steps_until_fail=3, min_survived_steps=2)
    store = EpisodeStore(tmp_path)
    bank = SkillBank(tmp_path / "skills.json", n_min=5, tau=0.1, cap=8)
    bank.add_skill(
        Skill(
            id="keep_a",
            recognize="bars visible",
            avoid_or_do={"chord": {"tapped": ["a"], "duration_ms": 400}},
        )
    )
    agent = CollectAgent(
        fixture,
        store,
        skills=bank,
        config=CollectConfig(
            lives=1,
            max_steps=2,
            skip_models=True,
            arm="ratchet",
            skill_off_frac=0.0,
            rng_seed=0,
        ),
    )
    agent.run_session()
    steps = [StepLog.model_validate(row) for row in store.read_steps()]
    assert steps
    assert all("skill-execute" in (row.thought or "") for row in steps)
    assert all("a" in (row.action.tapped or []) for row in steps)
    assert all(row.result.executed_as_proposed for row in steps)


def test_fake_softlock_uses_snapshot_reset() -> None:
    fixture = FakeFixture()
    fixture.health = 0.2
    assert fixture.after_fail() == "in_game"
    assert fixture.health == 1.0
    fixture.health = 0.3
    assert fixture.after_fail(softlock=True) == "snapshot"
    fixture.health = 0.3
    assert fixture.after_fail(eval_mode=True) == "snapshot"


class _BoomGateway:
    def complete_json(self, **kwargs):  # noqa: ANN003
        raise AssertionError("VLM should not be called on interact interrupt")


class _WalkGateway:
    def __init__(self) -> None:
        self.calls = 0

    def complete_json(self, **kwargs):  # noqa: ANN003
        from loop.model.gateway import ModelCall

        self.calls += 1
        return ModelCall(
            text="{}",
            parsed={"thought": "walk north", "stick_l": "n", "duration_ms": 400},
            latency_ms=1.0,
            model="dummy",
        )


class _StillFixture(FakeFixture):
    def __init__(self, image, **kwargs) -> None:
        super().__init__(**kwargs)
        from PIL import Image

        self._still = image.convert("RGB") if not isinstance(image, Image.Image) else image.convert("RGB")

    def grab_image(self):
        return self._still.copy()

    def scorer(self, image):
        del image
        return {"health_frac": 1.0, "stamina_frac": 1.0, "fail_screen": False}

    def terminal(self, metrics, image) -> str:
        del metrics, image
        return "alive"


def test_interrupt_skips_vlm_on_open_door(tmp_path: Path, monkeypatch) -> None:
    from PIL import Image

    monkeypatch.setattr("loop.agent.loop.read_prompt_text", lambda _img: "Open")
    frame = Image.open(Path(__file__).resolve().parent / "fixtures" / "prompt_open_door.jpg").convert("RGB")
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
    summary = agent.run_session()
    steps = [StepLog.model_validate(row) for row in store.read_steps()]
    assert steps
    row = steps[0]
    assert row.prompt_on is True
    assert row.prompt_source == "cv"
    assert row.action.tapped == ["a"]
    assert "interact-interrupt" in row.thought
    assert summary.interrupt_cv == 1
    assert summary.interrupt_dino == 0


def test_dino_generic_prompt_calls_vlm(tmp_path: Path) -> None:
    from PIL import Image

    from avatar.gsm.rule.detector import DetectedBox, FakeDetector

    dark = Image.new("RGB", (320, 180), (12, 12, 12))
    fixture = _StillFixture(dark, steps_until_fail=None, min_survived_steps=1)
    store = EpisodeStore(tmp_path)
    gateway = _WalkGateway()
    agent = CollectAgent(
        fixture,
        store,
        detector=FakeDetector(
            boxes=[DetectedBox(label="interact prompt", x=0.30, y=0.40, w=0.40, h=0.15, score=0.70)]
        ),
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
    assert gateway.calls == 1
    steps = [StepLog.model_validate(row) for row in store.read_steps()]
    assert "interact-interrupt" not in (steps[0].thought or "")


def test_explore_gsm_continues_without_vlm(tmp_path: Path) -> None:
    from PIL import Image

    dark = Image.new("RGB", (320, 180), (12, 12, 12))
    fixture = _StillFixture(dark, steps_until_fail=None, min_survived_steps=1)
    store = EpisodeStore(tmp_path)
    gateway = _WalkGateway()
    agent = CollectAgent(
        fixture,
        store,
        gateway=gateway,  # type: ignore[arg-type]
        config=CollectConfig(
            lives=1,
            max_steps=4,
            skip_models=False,
            arm="no_skill",
            dino=False,
            timeout_s=30,
        ),
    )
    agent.run_session()
    steps = [StepLog.model_validate(row) for row in store.read_steps()]
    assert len(steps) == 4
    assert gateway.calls == 2
    assert steps[0].action.stick_l == "n"
    assert steps[1].action.stick_l == "n"
    assert steps[2].result.invoked_gsm == "explore"
    assert steps[2].action.stick_r == "e"
    assert steps[2].action.stick_l is None
    assert "continue" in (steps[3].thought or "")
    assert steps[3].result.invoked_gsm == "explore"


def test_user_prompt_approach_note_walks_closer() -> None:
    from loop.agent.prompts import approach_evidence_line

    text = user_prompt(
        actions=prompt_listing("xbox360"),
        arm="no_skill",
        approach_note=approach_evidence_line(),
        last_mad=0.01,
        prompt_box=False,
        last_had_prompt=False,
        bright_patches="0.48,0.62",
    )
    assert "no readable verb" in text
    assert "Short step toward" in text
    assert "just used" not in text.lower()
    assert "condition, not a tap" not in text.lower()
    assert "If the target is far, walk closer" in system_for("no_skill")
    assert "2D screenshot" in system_for("no_skill")


def test_user_prompt_journal_and_recall() -> None:
    text = user_prompt(
        actions=prompt_listing("xbox360"),
        arm="ratchet",
        journal_block="Journal:\n- doorway heading=n prompt=lock glyph=a lock",
        recall_hits=["glyph=a → a → lock ×1"],
    )
    assert "Journal:" in text
    assert "heading=n" in text
    assert "Retrieved:" in text
    assert "Tools: recall" in text
    quiet = user_prompt(actions=prompt_listing("xbox360"), arm="no_skill")
    assert "Memory tools are off" in quiet
    assert "Tools: recall" not in quiet


def test_explore_owns_pad_yields_on_lock() -> None:
    from loop.agent.loop import explore_owns_pad

    assert explore_owns_pad(interrupt=False, yield_lock=False, stall=True, gsm_busy=False) is True
    assert explore_owns_pad(interrupt=False, yield_lock=True, stall=True, gsm_busy=True) is False
    assert explore_owns_pad(interrupt=True, yield_lock=False, stall=True, gsm_busy=True) is False
    assert explore_owns_pad(interrupt=False, yield_lock=False, stall=False, gsm_busy=True) is True


def test_stop_after_action_persists_completed_row_once(tmp_path: Path) -> None:
    from loop.live import request_stop

    fixture = FakeFixture(steps_until_fail=10)
    store = EpisodeStore(tmp_path)
    stop_after = 2
    orig = fixture.execute_chord

    def wrapped(action):
        result = orig(action)
        if len(fixture.executed) >= stop_after:
            request_stop(tmp_path)
        return result

    fixture.execute_chord = wrapped  # type: ignore[method-assign]
    agent = CollectAgent(
        fixture,
        store,
        config=CollectConfig(lives=1, max_steps=8, skip_models=True, arm="no_skill"),
    )
    summary = agent.run_session()
    steps = store.read_steps()
    assert len(steps) == stop_after
    assert len({(row["episode_id"], row["step_id"]) for row in steps}) == stop_after
    assert summary.stop_requested is True
    assert summary.flush_completed is True


def test_finalize_session_is_idempotent_and_db_reopens(tmp_path: Path) -> None:
    import json
    import sqlite3

    from loop.memory.journal import FactJournal

    fixture = FakeFixture(steps_until_fail=3)
    store = EpisodeStore(tmp_path)
    journal_path = tmp_path / "journal.json"
    agent = CollectAgent(
        fixture,
        store,
        config=CollectConfig(lives=1, max_steps=2, skip_models=True, arm="no_skill"),
    )
    agent.journal = FactJournal(path=journal_path)
    agent.journal.record_click(
        screen="map",
        x=0.40,
        y=0.70,
        device="pt_0.40_0.70",
        pixel_changed=True,
        step_id=1,
        label="Node",
    )
    first = agent.run_session()
    second = agent._finalize_session()
    assert first.model_dump() == second.model_dump()
    assert first.flush_completed is True
    db_path = store.run_dir / "objects.db"
    assert db_path.exists()
    conn = sqlite3.connect(str(db_path))
    conn.execute("SELECT 1")
    conn.close()
    json.loads(journal_path.read_text(encoding="utf-8"))
    json.loads((store.run_dir / "memory_grounded.json").read_text(encoding="utf-8"))


def test_stop_before_decision_writes_no_step_rows(tmp_path: Path) -> None:
    from loop.live import request_stop

    request_stop(tmp_path)
    fixture = FakeFixture(steps_until_fail=5)
    store = EpisodeStore(tmp_path)
    agent = CollectAgent(
        fixture,
        store,
        config=CollectConfig(lives=1, max_steps=4, skip_models=True, arm="no_skill"),
    )
    summary = agent.run_session()
    assert store.read_steps() == []
    assert summary.stop_requested is True
    assert summary.flush_completed is True
