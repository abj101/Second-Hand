from __future__ import annotations

from .primitives import (
    NamedAction,
    key_tap,
    mouse_click,
    pad_button,
    pad_dpad,
    pad_stick,
    pad_trigger,
)

BURST_MS = 400

_STICK_DIRS: dict[str, tuple[float, float]] = {
    "n": (0.0, 1.0),
    "ne": (0.7, 0.7),
    "e": (1.0, 0.0),
    "se": (0.7, -0.7),
    "s": (0.0, -1.0),
    "sw": (-0.7, -0.7),
    "w": (-1.0, 0.0),
    "nw": (-0.7, 0.7),
}


def _stick_table(prefix: str, side: str) -> dict[str, NamedAction]:
    out: dict[str, NamedAction] = {}
    for name, (x, y) in _STICK_DIRS.items():
        key = f"{prefix}_{name}"
        out[key] = NamedAction(key, (pad_stick(side, x, y, duration_ms=BURST_MS),))  # type: ignore[arg-type]
    return out


XBOX360_TABLE: dict[str, NamedAction] = {
    **{
        btn: NamedAction(btn, (pad_button(btn),))  # type: ignore[arg-type]
        for btn in ("a", "b", "x", "y", "lb", "rb", "ls", "rs", "start", "back")
    },
    "lt": NamedAction("lt", (pad_trigger("left", 1.0, duration_ms=BURST_MS),)),
    "rt": NamedAction("rt", (pad_trigger("right", 1.0, duration_ms=BURST_MS),)),
    "dpad_up": NamedAction("dpad_up", (pad_dpad("up"),)),
    "dpad_down": NamedAction("dpad_down", (pad_dpad("down"),)),
    "dpad_left": NamedAction("dpad_left", (pad_dpad("left"),)),
    "dpad_right": NamedAction("dpad_right", (pad_dpad("right"),)),
    **_stick_table("stick_l", "left"),
    **_stick_table("stick_r", "right"),
}

KEYBOARD_TABLE: dict[str, NamedAction] = {
    code.lower(): NamedAction(code.lower(), (key_tap(code, duration_ms=BURST_MS if code.startswith("Key") else None),))
    for code in (
        "KeyW",
        "KeyA",
        "KeyS",
        "KeyD",
        "KeyE",
        "KeyF",
        "KeyR",
        "KeyC",
        "KeyQ",
        "Space",
        "Shift",
        "Tab",
        "Enter",
        "Escape",
        "ArrowUp",
        "ArrowDown",
        "ArrowLeft",
        "ArrowRight",
    )
}

LAB_TABLE: dict[str, NamedAction] = {
    "arrow_left": NamedAction("arrow_left", (key_tap("ArrowLeft"),)),
    "arrow_right": NamedAction("arrow_right", (key_tap("ArrowRight"),)),
    "arrow_up": NamedAction("arrow_up", (key_tap("ArrowUp"),)),
    "arrow_down": NamedAction("arrow_down", (key_tap("ArrowDown"),)),
    "click_center": NamedAction("click_center", (mouse_click(0.5, 0.5),)),
}

# Turn-based mouse/key slots. Names are layout cells, not game verbs.
STS_TABLE: dict[str, NamedAction] = {
    **{
        f"h{i}": NamedAction(f"h{i}", (mouse_click(0.14 + (i - 1) * 0.09, 0.86),))
        for i in range(1, 9)
    },
    **{
        f"m{i}": NamedAction(f"m{i}", (mouse_click(0.46 + (i - 1) * 0.12, 0.36),))
        for i in range(1, 5)
    },
    **{
        f"v{i}": NamedAction(
            f"v{i}",
            (mouse_click(0.34 + ((i - 1) % 3) * 0.16, 0.40 + ((i - 1) // 3) * 0.16),),
        )
        for i in range(1, 5)
    },
    "u1": NamedAction("u1", (mouse_click(0.08, 0.12),)),
    "u2": NamedAction("u2", (mouse_click(0.14, 0.12),)),
    "p": NamedAction("p", (mouse_click(0.18, 0.42),)),
    "z": NamedAction("z", (mouse_click(0.95, 0.72),)),
    "y": NamedAction("y", (mouse_click(0.93, 0.88),)),
    "g": NamedAction("g", (mouse_click(0.50, 0.52),)),
    **{f"d{i}": NamedAction(f"d{i}", (key_tap(f"Digit{i}"),)) for i in range(1, 10)},
    "d0": NamedAction("d0", (key_tap("Digit0"),)),
    "keye": NamedAction("keye", (key_tap("KeyE"),)),
    "enter": NamedAction("enter", (key_tap("Enter"),)),
    "escape": NamedAction("escape", (key_tap("Escape"),)),
    "space": NamedAction("space", (key_tap("Space"),)),
    "arrowleft": NamedAction("arrowleft", (key_tap("ArrowLeft"),)),
    "arrowright": NamedAction("arrowright", (key_tap("ArrowRight"),)),
    "arrowup": NamedAction("arrowup", (key_tap("ArrowUp"),)),
    "arrowdown": NamedAction("arrowdown", (key_tap("ArrowDown"),)),
}

TABLES: dict[str, dict[str, NamedAction]] = {
    "xbox360": XBOX360_TABLE,
    "keyboard": KEYBOARD_TABLE,
    "lab": LAB_TABLE,
    "sts": STS_TABLE,
}


def names(table_name: str) -> list[str]:
    return list(TABLES[table_name])


def resolve(table_name: str, action_name: str) -> NamedAction:
    table = TABLES[table_name]
    try:
        return table[action_name]
    except KeyError as exc:
        raise KeyError(f"Unknown action {action_name!r} in table {table_name!r}") from exc


def prompt_listing(table_name: str) -> str:
    return ", ".join(names(table_name))
