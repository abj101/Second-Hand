from pathlib import Path

from loop.agent.loop import CollectAgent
from loop.analyze import load_evidence
from loop.curate.curator import Curator
from loop.eval.export import _determinate_success_rate
from loop.fixtures.fake import FakeFixture
from loop.log.schema import CardTransaction, EpisodeLog, StepResult, execution_disposition
from loop.log.store import EpisodeStore
from loop.memory.skills import Skill, SkillBank


def test_execution_dispositions_keep_source_only_distinct() -> None:
    assert StepResult().execution_disposition == "unknown"
    assert execution_disposition(executed=[], transaction=None, error=None) == "not_attempted"
    assert execution_disposition(executed=[], transaction=None, error="blocked") == "rejected_pre_dispatch"
    assert execution_disposition(
        executed=["click"],
        transaction=CardTransaction(source_dispatched=True, original_target_mark=3),
        error=None,
    ) == "partially_dispatched"
    assert execution_disposition(
        executed=["click", "click"],
        transaction=CardTransaction(source_dispatched=True, target_dispatched=True),
        error=None,
    ) == "dispatched"


def test_legacy_episode_uses_terminal_not_success_flag() -> None:
    legacy_timeout = EpisodeLog.model_validate(
        {"session_id": "s", "episode_id": "old", "fixture": "fake", "terminal": "timeout", "success": True}
    )
    assert legacy_timeout.outcome == "inconclusive"
    assert legacy_timeout.success is False
    assert legacy_timeout.outcome_evidence == ["terminal:timeout"]
    assert EpisodeLog(session_id="s", episode_id="win", fixture="fake", terminal="win").success is True
    assert EpisodeLog(session_id="s", episode_id="loss", fixture="fake", terminal="fail").outcome == "loss"


def test_summary_rates_exclude_inconclusive(tmp_path: Path) -> None:
    agent = CollectAgent(FakeFixture(), EpisodeStore(tmp_path))
    agent._episode_returns = [1.0, -1.0, 0.0]
    agent._episode_outcomes = ["win", "loss", "inconclusive"]
    summary = agent._build_session_summary()
    assert (summary.wins, summary.losses, summary.inconclusive_episodes) == (1, 1, 1)
    assert summary.success_rate == 0.5
    assert _determinate_success_rate([
        EpisodeLog(session_id="s", episode_id="t", fixture="fake", terminal="timeout")
    ]) is None


def test_inconclusive_episodes_do_not_enter_skill_evidence_or_trials(tmp_path: Path) -> None:
    run = tmp_path / "run"
    store = EpisodeStore(run)
    store.write_episode(EpisodeLog(
        session_id="s", episode_id="timeout", fixture="fake", terminal="timeout",
        arm="ratchet", skill_on=True, active_skill_id="skill",
    ))
    bank = SkillBank(tmp_path / "skills.json")
    bank.add_skill(Skill(id="skill", recognize="x", avoid_or_do={"chord": {}}))
    report = Curator("fake", run_dir=run, bank=bank, skip_models=True, root=tmp_path).run()
    assert report.recorded == 0
    assert bank.skills["skill"].n_trials == 0
    assert load_evidence("fake", root=tmp_path, run_dir=run) == []
