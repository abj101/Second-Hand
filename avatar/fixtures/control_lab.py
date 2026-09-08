"""Tk echo window for harness device tests. Not an observation channel for the VLM."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path


LAB_TITLE = "Avatar Control Lab"


def run_lab(echo_path: Path, *, hold_s: float = 30.0) -> None:
    import tkinter as tk

    echo_path.parent.mkdir(parents=True, exist_ok=True)
    events: list[dict] = []

    def flush() -> None:
        echo_path.write_text(json.dumps(events, indent=2), encoding="utf-8")

    def record(kind: str, **payload: object) -> None:
        events.append({"t": time.time(), "kind": kind, **payload})
        flush()

    root = tk.Tk()
    root.title(LAB_TITLE)
    root.geometry("480x360+80+80")
    root.configure(bg="#1a1a1a")
    label = tk.Label(
        root,
        text="Click here. Keys and clicks are echoed to JSON.",
        fg="#f0f0f0",
        bg="#1a1a1a",
        font=("Segoe UI", 12),
    )
    label.pack(expand=True, fill="both")

    def on_key(event: tk.Event) -> None:
        record("key", keysym=event.keysym, keycode=event.keycode, char=event.char)

    def on_click(event: tk.Event) -> None:
        w = max(root.winfo_width(), 1)
        h = max(root.winfo_height(), 1)
        record(
            "click",
            x=event.x,
            y=event.y,
            nx=round(event.x / w, 4),
            ny=round(event.y / h, 4),
            num=event.num,
        )

    root.bind_all("<Key>", on_key)
    root.bind_all("<Button>", on_click)
    root.after(200, root.focus_force)
    root.after(int(hold_s * 1000), root.destroy)
    record("ready", title=LAB_TITLE)
    root.mainloop()


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    echo = Path(args[0]) if args else Path("control_lab_echo.json")
    hold = float(args[1]) if len(args) > 1 else 30.0
    run_lab(echo, hold_s=hold)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
