from __future__ import annotations

from pathlib import Path

from loop.curate.critic import cluster_failures, failure_signature
from loop.curate.curator import Curator
from loop.fixtures.base import chapel_success
from loop.fixtures.fake import FakeFixture
from loop.log.schema import ActionLog, EpisodeLog, Observation, StepLog
from loop.log.store import EpisodeStore
from loop.memory.skills import Skill, SkillBank, contribution
from loop.os.chords import skill_text_leaks


def _step(eid: str, name: str, *, health: float = 0.2, fail: bool = True) -> StepLog:
    return StepLog(
        session_id="s",
        episode_id=eid,
        step_id=1,
        ts="2026-08-24T00:00:00+00:00",
        fixture="fake",
        observation=Observation(screenshot_ref="x.jpg", after_screenshot_ref="y.jpg"),
        action=ActionLog(name=name, duration_ms=400),
        metrics={"health_frac": health, "fail_screen": fail},
        terminal="fail" if fail else "timeout",
        arm="ratchet",
        skill_on=False,
        success=False,
    )


def _episode(eid: str, *, terminal: str = "fail", success: bool = False) -> EpisodeLog:
    return EpisodeLog(
        session_id="s",
        episode_id=eid,
        fixture="fake",
        steps=1,
        terminal=terminal,  # type: ignore[arg-type]
        audit_flags=["no_pixel_change"],
        arm="ratchet",
        success=success,
    )


def test_good_skill_contribution_positive() -> None:
    skill = Skill(id="good", recognize="x", avoid_or_do={"chord": {"tapped": ["a"]}})
    skill.n_trials = 8
    skill.n_success = 6
    skill.n_fail = 2
    skill.recompute()
    assert skill.contribution > 0
    assert contribution(6, 2, 8) == skill.contribution


def test_bad_skill_retires_after_n_min(tmp_path: Path) -> None:
    bank = SkillBank(tmp_path / "skills.json", n_min=5, tau=0.10, cap=8)
    bad = Skill(id="bad", recognize="y", avoid_or_do={"chord": {"tapped": ["b"]}})
    bank.add_skill(bad)
    for _ in range(5):
        bank.record_trial("bad", success=False)
    assert bank.would_retire(bank.skills["bad"])
    retired = bank.retire_due()
    assert [s.id for s in retired] == ["bad"]
    assert bank.skills["bad"].status == "deprecated"
    bank.save()
    again = SkillBank(tmp_path / "skills.json", n_min=5, tau=0.10, cap=8)
    assert again.skills["bad"].status == "deprecated"


def test_cap_refuses_when_none_eligible(tmp_path: Path) -> None:
    bank = SkillBank(tmp_path / "skills.json", n_min=5, tau=0.10, cap=2)
    bank.add_skill(Skill(id="s1", recognize="a", avoid_or_do={"chord": {}}))
    bank.add_skill(Skill(id="s2", recognize="b", avoid_or_do={"chord": {}}))
    assert bank.add_skill(Skill(id="s3", recognize="c", avoid_or_do={"chord": {}})) is None
    assert len(bank.active()) == 2


def test_inconclusive_legacy_timeout_does_not_penalize_or_cluster(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    # Historical `success=true` must not override durable timeout evidence.
    (run / "episodes.jsonl").write_text(
        '{"session_id":"s","episode_id":"old-timeout","fixture":"fake",'
        '"terminal":"timeout","success":true,"arm":"ratchet",'
        '"skill_on":true,"active_skill_id":"skill"}\n',
        encoding="utf-8",
    )
    bank = SkillBank(tmp_path / "skills.json")
    bank.add_skill(Skill(id="skill", recognize="x", avoid_or_do={"chord": {}}))
    report = Curator("fake", run_dir=run, bank=bank, skip_models=True, root=tmp_path).run()
    assert report.recorded == 0
    assert bank.skills["skill"].n_trials == 0
    timeout = EpisodeLog.model_validate({
        "session_id": "s", "episode_id": "old-timeout", "fixture": "fake",
        "terminal": "timeout", "success": True,
    })
    assert timeout.outcome == "inconclusive"
    assert cluster_failures([timeout], {}) == {}


def test_birth_blocked_until_three_signatures(tmp_path: Path) -> None:
    run = tmp_path / "run"
    store = EpisodeStore(run)
    for i in range(2):
        eid = f"e{i}"
        store.write_step(_step(eid, "b"))
        store.write_episode(_episode(eid))
    bank = SkillBank(tmp_path / "skills.json", n_min=5, tau=0.10, cap=8)
    curator = Curator("fake", run_dir=run, bank=bank, skip_models=True, root=tmp_path)
    report = curator.run(preview=False)
    assert report.born == []
    eid = "e2"
    store.write_step(_step(eid, "b"))
    store.write_episode(_episode(eid))
    report = curator.run(preview=False)
    assert len(report.born) == 1
    assert bank.active()


def test_preview_does_not_write(tmp_path: Path) -> None:
    run = tmp_path / "run"
    store = EpisodeStore(run)
    for i in range(3):
        eid = f"p{i}"
        store.write_step(_step(eid, "b"))
        store.write_episode(_episode(eid))
    bank_path = tmp_path / "skills.json"
    bank = SkillBank(bank_path, n_min=5, tau=0.10, cap=8)
    bank.save()
    before = bank_path.read_text(encoding="utf-8")
    curator = Curator("fake", run_dir=run, bank=bank, skip_models=True, root=tmp_path)
    report = curator.run(preview=True)
    assert report.preview
    assert report.born
    assert bank_path.read_text(encoding="utf-8") == before
    assert not bank.evidence_path.exists()
    assert bank.active() == []


def test_chapel_success_not_standstill() -> None:
    alive = [
        StepLog(
            session_id="s",
            episode_id="e",
            step_id=1,
            ts="2026-08-24T00:00:00+00:00",
            fixture="fake",
            observation=Observation(screenshot_ref="x.jpg"),
            metrics={"health_frac": 1.0, "fail_screen": False},
            terminal="timeout",
        )
    ]
    assert chapel_success(alive, "timeout", min_survived_steps=8) is False
    long = []
    for i in range(8):
        long.append(
            StepLog(
                session_id="s",
                episode_id="e",
                step_id=i + 1,
                ts="2026-08-24T00:00:00+00:00",
                fixture="fake",
                observation=Observation(screenshot_ref="x.jpg"),
                metrics={"health_frac": 0.8, "fail_screen": False},
                terminal="timeout",
            )
        )
    assert chapel_success(long, "timeout", min_survived_steps=8) is False
    assert chapel_success(long, "fail", min_survived_steps=8) is False
    long[-1].metrics["runes"] = 2
    long[-2].metrics["runes"] = 2
    long[-3].metrics["runes"] = 0
    long[0].metrics["runes"] = 0
    assert chapel_success(long, "fail", min_survived_steps=8) is False
    assert chapel_success(long, "timeout", min_survived_steps=8) is True


def test_chapel_success_uses_last_visible_health() -> None:
    long = []
    for i in range(8):
        long.append(
            StepLog(
                session_id="s",
                episode_id="e",
                step_id=i + 1,
                ts="2026-08-24T00:00:00+00:00",
                fixture="fake",
                observation=Observation(screenshot_ref="x.jpg"),
                metrics={"health_frac": 0.8, "fail_screen": False},
                terminal="timeout",
            )
        )
    long[-1].metrics = {"health_frac": None, "fail_screen": False}
    assert chapel_success(long, "timeout", min_survived_steps=8) is False


def test_parametric_fake_good_vs_bad() -> None:
    fx = FakeFixture(good_action="a", bad_action="b", min_survived_steps=2)
    fx.execute("a")
    fx.execute("a")
    img = fx.grab_image()
    metrics = fx.scorer(img)
    steps = [
        StepLog(
            session_id="s",
            episode_id="e",
            step_id=1,
            ts="2026-08-24T00:00:00+00:00",
            fixture="fake",
            observation=Observation(screenshot_ref="x.jpg"),
            metrics=metrics,
            terminal="timeout",
        ),
        StepLog(
            session_id="s",
            episode_id="e",
            step_id=2,
            ts="2026-08-24T00:00:00+00:00",
            fixture="fake",
            observation=Observation(screenshot_ref="x.jpg"),
            metrics=metrics,
            terminal="timeout",
        ),
    ]
    assert fx.success(steps, "timeout") is True
    bad = FakeFixture(good_action="a", bad_action="b", min_survived_steps=2)
    bad.execute("b")
    bad.execute("b")
    fail_img = bad.grab_image()
    fail_metrics = bad.scorer(fail_img)
    fail_steps = [
        StepLog(
            session_id="s",
            episode_id="e",
            step_id=1,
            ts="2026-08-24T00:00:00+00:00",
            fixture="fake",
            observation=Observation(screenshot_ref="x.jpg"),
            metrics=fail_metrics,
            terminal="fail",
        )
    ]
    assert bad.terminal(fail_metrics, fail_img) == "fail"
    assert bad.success(fail_steps, "fail") is False


def test_synth_strips_banned_verbs() -> None:
    from loop.curate.synth import skill_from_parsed

    skill = skill_from_parsed(
        {
            "id": "x",
            "recognize": "when you should dodge",
            "avoid_or_do": {"chord": {"tapped": ["b"]}, "pitfall": "flask"},
            "end": "after attack",
        },
        "flags|b|fail",
    )
    assert skill is not None
    assert skill_text_leaks(skill.model_dump()) == []


def test_failure_signature_clusters() -> None:
    a = _episode("a")
    b = _episode("b")
    steps = {"a": [_step("a", "b")], "b": [_step("b", "b")]}
    assert failure_signature(a, steps["a"]) == failure_signature(b, steps["b"])
    clusters = cluster_failures([a, b], steps)
    assert len(clusters) == 1
    assert len(next(iter(clusters.values()))) == 2


def test_curate_refuses_look_skill_without_view_changed(tmp_path: Path) -> None:
    from loop.curate.critic import commit_allowed
    from loop.curate.synth import deterministic_skill
    from loop.log.schema import StepResult

    sig = "stall=0.8|look=0.0|prompt=0|timeout"
    skill = deterministic_skill(sig)
    assert skill.avoid_or_do["chord"]["stick_r"] == "e"
    stuck = _step("e", "stick_r_e", fail=False)
    stuck.result = StepResult(view_changed=False, stall=True)
    stuck.action.stick_r = "e"
    stuck.action.stick_l = None
    assert commit_allowed(skill.avoid_or_do["chord"], [stuck]) is False
    stuck.result.view_changed = True
    assert commit_allowed(skill.avoid_or_do["chord"], [stuck]) is True
    walk = _step("w", "stick_l_n", fail=False)
    walk.result = StepResult(view_changed=True, stall=False)
    walk.action.stick_l = "n"
    walk.action.stick_r = None
    assert commit_allowed(skill.avoid_or_do["chord"], [walk]) is False


def test_curate_refuses_tap_skill_without_cv_prompt() -> None:
    from loop.curate.critic import commit_allowed
    from loop.log.schema import ActionLog, StepResult

    chord = {"tapped": ["a"], "duration_ms": 400}
    dino = _step("e", "a", fail=False)
    dino.prompt_on = True
    dino.prompt_source = "dino"
    dino.action = ActionLog(tapped=["a"], duration_ms=400)
    dino.result = StepResult(prompt_cleared=True)
    assert commit_allowed(chord, [dino]) is False
    cv = _step("e", "a", fail=False)
    cv.prompt_on = True
    cv.prompt_source = "cv"
    cv.action = ActionLog(tapped=["a"], duration_ms=400)
    cv.result = StepResult(prompt_cleared=True)
    assert commit_allowed(chord, [cv]) is True
    quiet = _step("e", "stick_r_e", fail=False)
    quiet.prompt_on = False
    quiet.prompt_source = ""
    assert commit_allowed(chord, [quiet]) is True


def test_stall_synth_is_look_not_north() -> None:
    from loop.curate.synth import deterministic_skill

    skill = deterministic_skill("stall=0.8|look=0.0|prompt=0|timeout")
    chord = skill.avoid_or_do["chord"]
    assert chord.get("stick_r") == "e"
    assert chord.get("stick_l") in (None, "")
