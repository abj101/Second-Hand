from __future__ import annotations

from typing import Any

from loop.log.schema import ActionLog
from loop.os.aim import AimGuide, AimMark, fill_from_aim, in_combat_sprite_band, label_kind

HEURISTIC_POLICY_ID = "sts2-heuristic-v1"
HAND_Y = 0.70


def _compact(label: str) -> str:
    return " ".join((label or "").lower().replace("_", " ").split())


def _is_end_turn(label: str) -> bool:
    return "end turn" in _compact(label)


def _is_menu_progress(mark: AimMark) -> bool:
    return label_kind(mark.label) == "progress"


def _used_ids(recent: list[str]) -> set[int]:
    used: set[int] = set()
    for token in recent:
        for part in str(token).replace(">", " ").split():
            if part.startswith("mk_"):
                try:
                    used.add(int(part.split("_", 1)[1]))
                except (TypeError, ValueError, IndexError):
                    continue
    return used


def _click(mark: AimMark, *, duration_ms: int, target: AimMark | None = None) -> ActionLog:
    action = ActionLog(
        mark=mark.id,
        click_x=mark.x,
        click_y=mark.y,
        duration_ms=duration_ms,
    )
    if target is not None:
        action.target = target.id
        action.click_x2 = target.x
        action.click_y2 = target.y
    action.name = action.derive_name()
    return action


def _best(marks: list[AimMark], used: set[int]) -> AimMark | None:
    if not marks:
        return None
    unused = [m for m in marks if m.id not in used]
    pool = unused or marks
    pool.sort(key=lambda m: (-m.score, -m.y, m.x))
    return pool[0]


def heuristic_action(
    guide: AimGuide | None,
    *,
    metrics: dict[str, Any] | None,
    selected: str | None,
    recent: list[str],
    duration_ms: int,
) -> ActionLog | None:
    """Menu progress clicks; combat block/kill two-click. Proposer, not a rewrite."""
    if guide is None or not guide.marks:
        return None
    metrics = metrics or {}
    screen = str(metrics.get("screen") or "").strip().lower()
    used = _used_ids(recent)
    if screen == "combat":
        energy = metrics.get("energy")
        end_marks = [m for m in guide.marks if _is_end_turn(m.label)]
        if isinstance(energy, (int, float)) and float(energy) <= 0 and end_marks:
            pick = _best(end_marks, used)
            return _click(pick, duration_ms=duration_ms) if pick is not None else None
        enemies = [
            m
            for m in guide.marks
            if in_combat_sprite_band(m.y, m.x) and label_kind(m.label) != "danger" and not _is_end_turn(m.label)
        ]
        if selected:
            enemy = _best(enemies, used)
            if enemy is not None:
                return _click(enemy, duration_ms=duration_ms)
        cards = [
            m
            for m in guide.marks
            if m.y >= HAND_Y
            and label_kind(m.label) != "danger"
            and not _is_end_turn(m.label)
            and not _is_menu_progress(m)
        ]
        card = _best(cards, used)
        enemy = _best(enemies, used)
        if card is not None and enemy is not None:
            return _click(card, duration_ms=duration_ms, target=enemy)
        if card is not None:
            return _click(card, duration_ms=duration_ms)
        if end_marks:
            pick = _best(end_marks, used)
            return _click(pick, duration_ms=duration_ms) if pick is not None else None
    return fill_from_aim(guide, recent=recent, duration_ms=duration_ms)
