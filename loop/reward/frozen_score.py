from __future__ import annotations

from typing import Any

from loop.log.schema import ActionLog
from loop.os.aim import AimMark, in_enemy_band, label_kind, parse_listing
from loop.os.chords import is_empty_chord
from loop.reward.sts import danger_turn_action

HAND_Y = 0.70
SCORE_PROGRESS = 0.35
SCORE_PAIR = 0.60
SCORE_END_EMPTY = 0.40
SCORE_END_ENERGY = 0.10
SCORE_HAND = 0.15
SCORE_DANGER = -0.50


def _compact(label: str) -> str:
    return " ".join((label or "").lower().replace("_", " ").split())


def _is_end_turn(label: str) -> bool:
    return "end turn" in _compact(label)


def _marks(metrics: dict[str, Any] | None) -> dict[int, AimMark]:
    listing = str((metrics or {}).get("aim_marks") or "")
    return {m.id: m for m in parse_listing(listing)}


def _label_for(action: ActionLog, marks: dict[int, AimMark]) -> str:
    if action.mark is not None and action.mark in marks:
        return marks[int(action.mark)].label
    return str((action.name or "")).strip()


def _y(action: ActionLog, marks: dict[int, AimMark], *, second: bool = False) -> float | None:
    if second:
        if action.target is not None and action.target in marks:
            return float(marks[int(action.target)].y)
        if action.click_y2 is not None:
            return float(action.click_y2)
        return None
    if action.mark is not None and action.mark in marks:
        return float(marks[int(action.mark)].y)
    if action.click_y is not None:
        return float(action.click_y)
    return None


def frozen_score(metrics: dict[str, Any] | None, action: ActionLog) -> float:
    """Score a candidate chord on a frozen frame. Does not execute."""
    metrics = metrics or {}
    if is_empty_chord(action):
        return 0.0
    if danger_turn_action(action, metrics):
        return SCORE_DANGER
    marks = _marks(metrics)
    label = _label_for(action, marks)
    kind = label_kind(label)
    if kind == "danger":
        return SCORE_DANGER
    screen = str(metrics.get("screen") or "").strip().lower()
    two_click = action.target is not None or (
        action.click_x2 is not None and action.click_y2 is not None
    )
    y1 = _y(action, marks)
    y2 = _y(action, marks, second=True)
    if screen == "combat":
        energy = metrics.get("energy")
        if _is_end_turn(label):
            if isinstance(energy, (int, float)) and float(energy) <= 0:
                return SCORE_END_EMPTY
            return SCORE_END_ENERGY
        if two_click:
            hand = y1 is not None and y1 >= HAND_Y
            enemy = y2 is not None and in_enemy_band(y2)
            if hand and enemy:
                return SCORE_PAIR
            return SCORE_HAND
        if y1 is not None and y1 >= HAND_Y:
            if isinstance(energy, (int, float)) and float(energy) > 0:
                return SCORE_HAND
            return 0.0
        if kind == "progress":
            return SCORE_PROGRESS
        return 0.0
    if kind == "progress":
        return SCORE_PROGRESS
    compact = _compact(label)
    if compact in {"logo", "spire"} or "logo" in compact:
        return 0.0
    return 0.0


def sandbox_frozen_score() -> tuple[bool, str]:
    """Path A verifier on frozen frames (no HUD deltas). Keep inequalities aligned with
    ``sandbox_turn_reward`` in loop.reward.sts — not score-equal, but these must hold:
    Resume > Escape, card+target > card-only, Continue > logo."""
    combat = {
        "screen": "combat",
        "energy": 3,
        "aim_marks": "3@400,850:Strike 7@550,300:Cultist 8@820,880:End_Turn",
    }
    empty_energy = dict(combat, energy=0)
    resume = frozen_score(
        {"screen": "pause", "aim_marks": "2@500,550:Resume 6@500,800:Give_Up"},
        ActionLog(mark=2, click_x=0.50, click_y=0.55),
    )
    escape = frozen_score({"screen": "combat"}, ActionLog(name="escape"))
    if resume <= escape:
        return False, f"Resume {resume} not greater than Escape {escape}"
    card = frozen_score(combat, ActionLog(mark=3, click_x=0.40, click_y=0.85))
    pair = frozen_score(
        combat,
        ActionLog(mark=3, click_x=0.40, click_y=0.85, target=7, click_x2=0.55, click_y2=0.30),
    )
    if pair <= card:
        return False, f"card+target {pair} not greater than card-only {card}"
    cont = frozen_score(
        {"screen": "title", "aim_marks": "1@500,700:Continue 9@500,180:logo"},
        ActionLog(mark=1, click_x=0.50, click_y=0.70),
    )
    logo = frozen_score(
        {"screen": "title", "aim_marks": "1@500,700:Continue 9@500,180:logo"},
        ActionLog(mark=9, click_x=0.50, click_y=0.18),
    )
    if cont <= logo:
        return False, f"Continue {cont} not greater than logo {logo}"
    end_ok = frozen_score(empty_energy, ActionLog(mark=8, click_x=0.82, click_y=0.88))
    end_waste = frozen_score(combat, ActionLog(mark=8, click_x=0.82, click_y=0.88))
    if end_ok <= end_waste:
        return False, f"End Turn at 0 energy {end_ok} not greater than with energy {end_waste}"
    return True, "ok"
