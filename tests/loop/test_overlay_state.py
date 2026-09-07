from __future__ import annotations

from loop.memory.overlay_state import OverlayState


def test_overlay_state_host_wins_screen() -> None:
    state = OverlayState()
    assert state.resolved_screen(host_screen="combat", perceive_screen="tutorial") == "combat"


def test_overlay_suppresses_stale_tutorial_ocr() -> None:
    state = OverlayState()
    state.record_dismissal(title="Energy Tutorial", step_id=14)
    assert state.should_suppress_ocr(text="Spend energy to play cards", current_step=15)
    assert not state.should_suppress_ocr(text="Spend energy to play cards", current_step=20)


def test_screen_disagreement_logged() -> None:
    state = OverlayState()
    state.log_screen_disagreement(host_screen="combat", perceive_screen="tutorial", step_id=11)
    assert state.screen_disagreements[0]["host"] == "combat"


def test_tutorial_page_prefers_current_frame() -> None:
    state = OverlayState()
    host = {"active": True, "title": "Combat Basics", "step": 3, "total": 3}
    llm1 = {"active": True, "title": "Combat Basics", "step": 2, "total": 3}
    resolved = state.resolve_tutorial(
        host_tutorial=host,
        llm1_tutorial=llm1,
        frame_hash="aaa",
        layout="paginated_overlay",
        dialog_open=True,
        activity="combat",
    )
    assert resolved.get("step") == 2
    closed = state.resolve_tutorial(
        host_tutorial=host,
        llm1_tutorial=None,
        frame_hash="bbb",
        layout="hand_and_board",
        dialog_open=False,
        activity="combat",
    )
    assert not closed.get("active")
