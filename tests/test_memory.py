from __future__ import annotations

from pathlib import Path

from avatar.memory.store import AgentMemory, CHAR_BUDGET
from avatar.schema import TurnOutcomeRecord


def _record(
    *,
    verdict: str,
    action: str,
    state_key: str = "board-a",
    evidence: str = "unchanged",
    goal: str = "swipe",
) -> TurnOutcomeRecord:
    return TurnOutcomeRecord(
        verdict=verdict,  # type: ignore[arg-type]
        pixel_changed=verdict != "noop",
        evidence=evidence,
        actions=[action],
        goal=goal,
        state_key=state_key,
    )


def test_noop_ban_after_two_same_state(tmp_path: Path) -> None:
    mem = AgentMemory("2048", root=tmp_path)
    mem.observe(_record(verdict="noop", action="swipe_left"))
    assert "swipe_left" not in mem.banned
    out = mem.observe(_record(verdict="noop", action="swipe_left"))
    assert "swipe_left" in mem.banned
    assert "swipe_left" in out.banned
    block = mem.prompt_block()
    assert "Banned actions" in block
    assert "swipe_left" in block


def test_procedural_prompt_rate_is_percent(tmp_path: Path) -> None:
    mem = AgentMemory("2048", root=tmp_path)
    mem.observe(_record(verdict="success", action="swipe_left"))
    mem.observe(_record(verdict="noop", action="swipe_left"))
    block = mem.prompt_block()
    assert "rate is a percent" in block
    assert "success= is a count" in block
    assert "success=1" in block
    assert "rate=50% (1/2)" in block


def test_see_flicker_does_not_clear_bans(tmp_path: Path) -> None:
    mem = AgentMemory("2048", root=tmp_path)
    mem.observe(_record(verdict="noop", action="swipe_down", state_key="empty"))
    mem.observe(_record(verdict="noop", action="swipe_down", state_key="empty"))
    assert "swipe_down" in mem.banned
    mem.observe(
        _record(verdict="noop", action="swipe_left", state_key="three-tiles", evidence="see flickered")
    )
    assert "swipe_down" in mem.banned


def test_ban_clears_when_state_changes(tmp_path: Path) -> None:
    mem = AgentMemory("2048", root=tmp_path)
    mem.observe(_record(verdict="noop", action="swipe_left", state_key="a"))
    mem.observe(_record(verdict="noop", action="swipe_left", state_key="a"))
    assert "swipe_left" in mem.banned
    mem.observe(_record(verdict="success", action="swipe_up", state_key="b", evidence="moved"))
    assert "swipe_left" not in mem.banned


def test_success_clears_bans_same_state(tmp_path: Path) -> None:
    mem = AgentMemory("2048", root=tmp_path)
    mem.observe(_record(verdict="noop", action="swipe_left", state_key="frozen"))
    mem.observe(_record(verdict="noop", action="swipe_left", state_key="frozen"))
    assert "swipe_left" in mem.banned
    mem.observe(_record(verdict="success", action="swipe_up", state_key="frozen", evidence="moved"))
    assert "swipe_left" not in mem.banned


def test_procedural_json_roundtrip(tmp_path: Path) -> None:
    mem = AgentMemory("2048", root=tmp_path)
    mem.observe(_record(verdict="noop", action="swipe_left", evidence="illegal"))
    mem.observe(_record(verdict="success", action="swipe_up", evidence="merged"))
    path = tmp_path / "2048" / "procedural.json"
    assert path.exists()
    again = AgentMemory("2048", root=tmp_path)
    stat = again.procedural.stats["swipe_left"]
    assert stat.n_noop == 1
    assert stat.n_success == 0
    assert "illegal" in stat.last_note
    assert again.procedural.stats["swipe_up"].n_success == 1
    assert "swipe_left" in again.prompt_block()


def test_procedural_prompt_rate_is_percent(tmp_path: Path) -> None:
    mem = AgentMemory("2048", root=tmp_path)
    mem.observe(_record(verdict="success", action="swipe_left"))
    mem.observe(_record(verdict="noop", action="swipe_left"))
    block = mem.prompt_block()
    assert "rate is a percent" in block
    assert "success= is a count" in block
    assert "success=1" in block
    assert "rate=50% (1/2)" in block


def test_prompt_block_budget(tmp_path: Path) -> None:
    mem = AgentMemory("2048", root=tmp_path)
    for i in range(10):
        mem.observe(
            _record(
                verdict="failure",
                action="swipe_down",
                evidence="x" * 400,
                goal=f"goal-{i}-" + ("y" * 80),
                state_key=f"s{i}",
            )
        )
    assert len(mem.prompt_block()) <= CHAR_BUDGET


def test_condense_hook_after_five(tmp_path: Path) -> None:
    mem = AgentMemory("2048", root=tmp_path)
    calls = {"n": 0}

    def fake_complete_json(**_kwargs: object) -> dict[str, str]:
        calls["n"] += 1
        return {"lessons": "- do not repeat swipe_left when the board is unchanged"}

    for i in range(5):
        mem.observe(_record(verdict="noop", action="swipe_left", state_key="same"))
    assert mem.maybe_condense(fake_complete_json) is not None
    assert calls["n"] == 1
    assert "do not repeat" in mem.lessons
    assert mem.maybe_condense(fake_complete_json) is None
    assert calls["n"] == 1


def test_repeat_fail_streak_bans_action_list(tmp_path: Path) -> None:
    mem = AgentMemory("2048", root=tmp_path)
    for _ in range(3):
        mem.observe(_record(verdict="failure", action="swipe_right", state_key="stuck"))
    assert "swipe_right" in mem.banned


def test_compute_outcome_noop_and_board_change() -> None:
    from avatar.eval.outcome import compute_outcome
    from avatar.schema import SeeResponse

    before = SeeResponse(screen_kind="gameplay", board=[[2, None], [None, 2]], score=0)
    after_same = SeeResponse(screen_kind="gameplay", board=[[2, None], [None, 2]], score=0)
    after_merge = SeeResponse(screen_kind="gameplay", board=[[4, None], [None, None]], score=4)

    noop = compute_outcome(
        pixel_changed=False,
        actions=["swipe_left"],
        see_before=before,
        see_after=after_same,
        goal="swipe",
    )
    assert noop.verdict == "noop"
    assert noop.pixel_changed is False

    win = compute_outcome(
        pixel_changed=True,
        actions=["swipe_up"],
        see_before=before,
        see_after=after_merge,
        goal="swipe",
    )
    assert win.verdict == "success"
    assert win.board_changed is True
    assert win.score_delta == 4

    empty = compute_outcome(pixel_changed=True, actions=[], goal="swipe")
    assert empty.verdict == "failure"

    partial = compute_outcome(
        pixel_changed=True,
        actions=["swipe_down"],
        see_before=before,
        see_after=after_same,
        goal="swipe",
    )
    assert partial.verdict == "partial"

    gsm_pixels = compute_outcome(
        pixel_changed=True,
        actions=["swipe_down"],
        see_before=before,
        see_after=after_same,
        compare_see=False,
        goal="swipe",
    )
    assert gsm_pixels.verdict == "success"
    assert gsm_pixels.board_changed is None

    frozen = compute_outcome(
        pixel_changed=True,
        actions=["swipe_down"],
        occupancy_changed=False,
        goal="swipe",
    )
    assert frozen.verdict == "noop"
    moved = compute_outcome(
        pixel_changed=False,
        actions=["swipe_left"],
        occupancy_changed=True,
        goal="swipe",
    )
    assert moved.verdict == "success"


def test_procedural_retrieve_by_name(tmp_path: Path) -> None:
    mem = AgentMemory("2048", root=tmp_path)
    mem.observe(_record(verdict="success", action="swipe_left", evidence="moved"))
    mem.procedural.record_code(
        "swipe_left",
        key="Left",
        duration_ms=80,
        action_design="pack tiles to the left wall",
    )
    block = mem.retrieve_procedural(["swipe_left"])
    assert "pack tiles to the left wall" in block
    assert "80" in block
    assert mem.retrieve_procedural(["missing"]) == ""


def test_episodic_prompt_includes_last_task_reflection(tmp_path: Path) -> None:
    mem = AgentMemory("2048", root=tmp_path)
    mem.observe(_record(verdict="success", action="swipe_up", evidence="merged"))
    mem.record_reflections(
        previous_task="swipe_up merged two 2s into a 4",
        historical_summary="Packed toward the corner after one merge.",
        action_design="prefer up when the top row has space",
        see={"screen_kind": "gameplay", "score": 4},
    )
    block = mem.episodic.prompt_block()
    assert "swipe_up merged two 2s into a 4" in block
    assert "Packed toward the corner after one merge." in block
    assert '"screen_kind":"gameplay"' in block


def test_procedural_code_json_roundtrip(tmp_path: Path) -> None:
    mem = AgentMemory("2048", root=tmp_path)
    mem.procedural.record_code(
        "hard_drop",
        key="Space",
        duration_ms=40,
        action_design="drop immediately when the column is clear",
    )
    again = AgentMemory("2048", root=tmp_path)
    stat = again.procedural.stats["hard_drop"]
    assert stat.key == "Space"
    assert stat.duration_ms == 40
    assert stat.action_design == "drop immediately when the column is clear"
    assert stat.n_success == 0
    assert "drop immediately when the column is clear" in again.retrieve_procedural(
        ["hard_drop"]
    )
