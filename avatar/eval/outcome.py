from __future__ import annotations

import json

from avatar.schema import SeeResponse, TurnOutcomeRecord


def see_state_key(see: SeeResponse | None) -> str:
    if see is None:
        return ""
    return json.dumps(
        {
            "board": see.board,
            "score": see.score,
            "status": see.status,
            "kind": see.screen_kind,
        },
        sort_keys=True,
        default=str,
    )


def occupancy_state_key(boxes: list[object] | None) -> str:
    if not boxes:
        return ""
    from avatar.gsm.rule.policy import occupancy_fingerprint

    return occupancy_fingerprint(boxes) or ""


def compact_see(see: SeeResponse | None) -> dict[str, object] | None:
    if see is None:
        return None
    return {
        "screen_kind": see.screen_kind,
        "board": see.board,
        "score": see.score,
        "status": see.status,
    }


def compute_outcome(
    *,
    pixel_changed: bool,
    actions: list[str],
    goal: str = "",
    see_before: SeeResponse | None = None,
    see_after: SeeResponse | None = None,
    compare_see: bool = True,
    occupancy_changed: bool | None = None,
) -> TurnOutcomeRecord:
    """Code-level before/after verdict. Occupancy delta beats pixel flicker."""
    board_changed: bool | None = None
    score_delta: int | None = None
    if compare_see and see_before is not None and see_after is not None:
        if see_before.board is not None and see_after.board is not None:
            board_changed = see_before.board != see_after.board
        if see_before.score is not None and see_after.score is not None:
            score_delta = see_after.score - see_before.score
    if occupancy_changed is True:
        board_changed = True
    elif occupancy_changed is False:
        board_changed = False

    if not actions:
        verdict: str = "failure"
        evidence = "No named actions executed."
    elif occupancy_changed is False:
        verdict = "noop"
        evidence = "Occupancy unchanged after action."
    elif not pixel_changed and occupancy_changed is not True:
        verdict = "noop"
        evidence = "ROI unchanged after action (likely illegal or no-op)."
    elif board_changed is True or (score_delta is not None and score_delta > 0):
        verdict = "success"
        evidence = "Board or score changed."
    elif board_changed is False:
        verdict = "partial"
        evidence = "Pixels moved but parsed board unchanged."
    else:
        verdict = "success"
        evidence = "Screen changed after action."

    return TurnOutcomeRecord(
        verdict=verdict,  # type: ignore[arg-type]
        pixel_changed=pixel_changed,
        board_changed=board_changed,
        score_delta=score_delta,
        actions=list(actions),
        goal=goal,
        evidence=evidence,
        state_key=see_state_key(see_after or see_before),
    )
