from __future__ import annotations

import random
from pathlib import Path

from loop.analyze import analyze_fixture, contrast, load_evidence
from loop.memory.skills import EvidenceRow, Skill, SkillBank


def test_choose_active_is_uniform(tmp_path: Path) -> None:
    bank = SkillBank(tmp_path / "skills.json")
    bank.add_skill(Skill(id="a", recognize="a", avoid_or_do={"chord": {}}))
    bank.add_skill(Skill(id="b", recognize="b", avoid_or_do={"chord": {}}))
    rng = random.Random(0)
    seen = {bank.choose_active(rng).id for _ in range(40)}  # type: ignore[union-attr]
    assert seen == {"a", "b"}


def test_matched_contrast_delta(tmp_path: Path) -> None:
    bank = SkillBank(tmp_path / "data" / "loop" / "fake" / "skills.json")
    for i in range(8):
        bank.append_evidence(
            EvidenceRow(
                episode_id=f"on{i}",
                skill_id="keep_a",
                skill_on=True,
                success=True,
                arm="ratchet",
            )
        )
        bank.append_evidence(
            EvidenceRow(
                episode_id=f"off{i}",
                skill_id="keep_a",
                skill_on=False,
                success=False,
                arm="ratchet",
            )
        )
    report = analyze_fixture("fake", root=tmp_path)
    assert report["n_episodes"] == 16
    row = report["skills"][0]
    assert row["skill_id"] == "keep_a"
    assert row["matched"] is True
    assert row["p_on"] == 1.0
    assert row["p_off"] == 0.0
    assert row["delta"] == 1.0
    assert row["underpowered"] is True


def test_unmatched_off_falls_back_to_global_p0() -> None:
    rows = [
        EvidenceRow(episode_id="on1", skill_id="s1", skill_on=True, success=True, arm="ratchet"),
        EvidenceRow(episode_id="on2", skill_id="s1", skill_on=True, success=False, arm="ratchet"),
        EvidenceRow(episode_id="off1", skill_id=None, skill_on=False, success=False, arm="ratchet"),
        EvidenceRow(episode_id="off2", skill_id=None, skill_on=False, success=False, arm="ratchet"),
    ]
    report = contrast(rows)
    row = report["skills"][0]
    assert row["matched"] is False
    assert row["p_on"] == 0.5
    assert row["p_off"] == 0.0
    assert row["delta"] == 0.5


def test_legacy_successful_timeout_is_excluded_from_analysis_evidence(tmp_path: Path) -> None:
    run = tmp_path / "run"
    # Write an immutable historical shape directly: it predates ``outcome``
    # and incorrectly calls the timeout a success.
    (run).mkdir()
    (run / "episodes.jsonl").write_text(
        '{"session_id":"s","episode_id":"old-timeout","fixture":"fake",'
        '"terminal":"timeout","success":true,"arm":"ratchet"}\n',
        encoding="utf-8",
    )
    assert load_evidence("fake", root=tmp_path, run_dir=run) == []


def test_elden_restore_copies_start_sl2(tmp_path: Path, monkeypatch) -> None:
    from loop.fixtures.elden import EldenFixture

    src = tmp_path / "start.sl2"
    dest = tmp_path / "ER0000.sl2"
    src.write_bytes(b"SNAP")
    dest.write_bytes(b"OLD")
    monkeypatch.setenv("AVATAR_ELDEN_SAVE", str(dest))
    fx = EldenFixture()
    fx.start_snapshot = lambda: src  # type: ignore[method-assign]
    assert fx.restore_snapshot() is True
    assert dest.read_bytes() == b"SNAP"
    assert dest.with_suffix(dest.suffix + ".bak").read_bytes() == b"OLD"
    assert fx.after_fail(eval_mode=True) == "snapshot"
