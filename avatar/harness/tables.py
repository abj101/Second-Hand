from __future__ import annotations

from typing import Any

from .primitives import (
    NamedAction,
    Primitive,
    key_tap,
    mouse_click,
    pad_button,
    pad_dpad,
)

# VLM picks names. Harness maps names to OS primitives.
# No "wait" / "observe" actions — GameSense forbids them as fake progress.

SWIPE_TABLE: dict[str, NamedAction] = {
    "swipe_up": NamedAction("swipe_up", (key_tap("ArrowUp"),)),
    "swipe_down": NamedAction("swipe_down", (key_tap("ArrowDown"),)),
    "swipe_left": NamedAction("swipe_left", (key_tap("ArrowLeft"),)),
    "swipe_right": NamedAction("swipe_right", (key_tap("ArrowRight"),)),
    "restart": NamedAction("restart", (key_tap("KeyR"),), notes="new game"),
}

TETRIS_TABLE: dict[str, NamedAction] = {
    "shift_left": NamedAction("shift_left", (key_tap("ArrowLeft"),)),
    "shift_right": NamedAction("shift_right", (key_tap("ArrowRight"),)),
    "rotate": NamedAction("rotate", (key_tap("ArrowUp"),)),
    "rotate_ccw": NamedAction("rotate_ccw", (key_tap("KeyA"),)),
    "soft_drop": NamedAction("soft_drop", (key_tap("ArrowDown"),)),
    "hard_drop": NamedAction("hard_drop", (key_tap("Space"),)),
    "hold": NamedAction("hold", (key_tap("KeyC"),)),
    "pause": NamedAction("pause", (key_tap("KeyP"),)),
    "resume": NamedAction("resume", (key_tap("KeyP"),)),
    "start": NamedAction("start", (key_tap("Enter"),)),
    "restart": NamedAction("restart", (key_tap("KeyR"),)),
}

# Virtual Xbox 360 pad. melonDS / DeSmuME accept an XInput controller.
NDS_TABLE: dict[str, NamedAction] = {
    "dpad_up": NamedAction("dpad_up", (pad_dpad("up"),)),
    "dpad_down": NamedAction("dpad_down", (pad_dpad("down"),)),
    "dpad_left": NamedAction("dpad_left", (pad_dpad("left"),)),
    "dpad_right": NamedAction("dpad_right", (pad_dpad("right"),)),
    "press_a": NamedAction("press_a", (pad_button("a"),), notes="Xbox A"),
    "press_b": NamedAction("press_b", (pad_button("b"),)),
    "press_x": NamedAction("press_x", (pad_button("x"),)),
    "press_y": NamedAction("press_y", (pad_button("y"),)),
    "press_l": NamedAction("press_l", (pad_button("lb"),)),
    "press_r": NamedAction("press_r", (pad_button("rb"),)),
    "press_start": NamedAction("press_start", (pad_button("start"),)),
    "press_select": NamedAction("press_select", (pad_button("back"),)),
    "tap_bottom_screen": NamedAction(
        "tap_bottom_screen",
        (mouse_click(0.50, 0.75),),
        notes="relative click in client crop",
    ),
}

STS_TABLE: dict[str, NamedAction] = {
    "click_card_1": NamedAction("click_card_1", (mouse_click(0.22, 0.82),)),
    "click_card_2": NamedAction("click_card_2", (mouse_click(0.36, 0.82),)),
    "click_card_3": NamedAction("click_card_3", (mouse_click(0.50, 0.82),)),
    "click_card_4": NamedAction("click_card_4", (mouse_click(0.64, 0.82),)),
    "click_card_5": NamedAction("click_card_5", (mouse_click(0.78, 0.82),)),
    "end_turn": NamedAction("end_turn", (key_tap("KeyE"), mouse_click(0.92, 0.55))),
    "confirm": NamedAction("confirm", (key_tap("Enter"),)),
    "proceed": NamedAction("proceed", (mouse_click(0.50, 0.55),)),
    "open_map": NamedAction("open_map", (key_tap("KeyM"),)),
    "open_deck": NamedAction("open_deck", (key_tap("KeyD"),)),
}

TABLES: dict[str, dict[str, NamedAction]] = {
    "swipe": SWIPE_TABLE,
    "tetris": TETRIS_TABLE,
    "nds": NDS_TABLE,
    "sts": STS_TABLE,
}

MAX_ACTIONS_PER_CYCLE = 5


def resolve(table_name: str, action_name: str) -> NamedAction:
    table = TABLES[table_name]
    if action_name not in table:
        raise KeyError(f"Unknown action {action_name!r} for table {table_name!r}")
    return table[action_name]


def compile_action(table_name: str, action_name: str) -> tuple[Primitive, ...]:
    return resolve(table_name, action_name).primitives


def names(table_name: str) -> list[str]:
    return list(TABLES[table_name].keys())


def prompt_listing(table_name: str) -> str:
    lines = []
    for bound in TABLES[table_name].values():
        extra = f" — {bound.notes}" if bound.notes else ""
        lines.append(f"- {bound.name}{extra}")
    return "\n".join(lines)


def clip_plan(actions: list[Any], cap: int = MAX_ACTIONS_PER_CYCLE) -> list[Any]:
    return actions[:cap]
