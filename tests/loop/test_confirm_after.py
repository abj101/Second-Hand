from __future__ import annotations

from PIL import Image

from loop.agent.loop import CollectAgent, CollectConfig
from loop.fixtures.fake import FakeFixture
from loop.log.store import EpisodeStore
from loop.os.profile import SettleResult


def test_confirm_after_action_waits_then_grabs(tmp_path, monkeypatch) -> None:
    fixture = FakeFixture(steps_until_fail=99)
    store = EpisodeStore(tmp_path)
    agent = CollectAgent(
        fixture,
        store,
        config=CollectConfig(max_steps=1, skip_models=True),
    )
    calls: list[str] = []

    def fake_quiet(*_args, **_kwargs) -> SettleResult:
        calls.append("quiet")
        return SettleResult(elapsed_ms=120.0, changed=True, polls=3)

    original_grab = fixture.grab_image

    def counting_grab():
        calls.append("grab")
        return original_grab()

    fixture.grab_image = counting_grab  # type: ignore[method-assign]
    monkeypatch.setattr("loop.agent.loop.wait_until_quiet", fake_quiet)

    frame = agent._confirm_after_action(tag="s1")  # noqa: SLF001

    assert isinstance(frame, Image.Image)
    assert calls == ["quiet", "grab"]
