"""Live, read-only diagnostics side panel for adaptive collect sessions."""

from __future__ import annotations

import argparse
import json
import os
import textwrap
import time
import tkinter as tk
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any


BACKGROUND = "#080808"
PANEL = "#0d0d0d"
FOREGROUND = "#f2f2f2"
MUTED = "#858585"
DIM = "#303030"
SUBTLE = "#171717"
WIDTH = 472
MIN_HEIGHT = 680


class JsonlTail:
    """Read appended JSONL records without repeatedly loading the whole file."""

    def __init__(self, path: Path, *, keep: int = 160) -> None:
        self.path = path
        self.position = 0
        self.rows: deque[dict[str, Any]] = deque(maxlen=keep)
        self.error = ""

    def poll(self) -> tuple[dict[str, Any], ...]:
        try:
            size = self.path.stat().st_size
        except FileNotFoundError:
            return tuple(self.rows)
        if size < self.position:
            self.position = 0
            self.rows.clear()
        try:
            with self.path.open("r", encoding="utf-8") as source:
                source.seek(self.position)
                for line in source:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(item, dict):
                        self.rows.append(item)
                self.position = source.tell()
            self.error = ""
        except (OSError, UnicodeError) as exc:
            self.error = str(exc)
        return tuple(self.rows)


@dataclass(frozen=True)
class ChoiceView:
    action_id: str
    label: str
    score: float
    selected: bool


@dataclass(frozen=True)
class DiagnosticState:
    frame_index: int
    decision_source: str
    selected_action_id: str | None
    selected_label: str
    dispatch_accepted: bool
    dispatch_reason: str
    latency_policy_s: float
    capture_integrity: str
    focused: bool
    score_kind: str
    choices: tuple[ChoiceView, ...]
    ocr_items: tuple[dict[str, Any], ...]
    directive_revision: int
    directive_intent: str
    orchestrator_events: tuple[dict[str, Any], ...]


def _latest_with(rows: tuple[dict[str, Any], ...], key: str) -> dict[str, Any]:
    return next((row for row in reversed(rows) if row.get(key)), {})


def build_diagnostic_state(rows: tuple[dict[str, Any], ...]) -> DiagnosticState | None:
    if not rows:
        return None
    latest = rows[-1]
    decision = _latest_with(rows, "selected_action_id") or latest
    selected = decision.get("selected_action_id")
    labels = {
        str(key): str(value)
        for key, value in (latest.get("candidate_labels") or {}).items()
    }
    labels.update(
        {
            str(key): str(value)
            for key, value in (decision.get("candidate_labels") or {}).items()
        }
    )
    allowed = tuple(str(item) for item in (latest.get("mask_allowed_ids") or ()))
    if selected and str(selected) not in allowed:
        allowed = (*allowed, str(selected))
    scores = {
        str(key): max(0.0, min(1.0, float(value)))
        for key, value in (decision.get("candidate_scores") or {}).items()
    }
    score_kind = "model probability" if scores else "selected action"
    choices = tuple(
        ChoiceView(
            action_id=action_id,
            label=labels.get(action_id, action_id.replace("_", " ")),
            score=scores.get(action_id, 1.0 if action_id == selected else 0.0),
            selected=action_id == selected,
        )
        for action_id in sorted(
            allowed,
            key=lambda item: (
                item != selected,
                -scores.get(item, 0.0),
                item,
            ),
        )
    )
    ocr_row = _latest_with(rows, "ocr_items") or latest
    event_log: list[dict[str, Any]] = []
    last_revision = 0
    for row in rows[-24:]:
        revision = int(row.get("directive_revision") or 0)
        if revision and revision != last_revision:
            event_log.append({"kind": "directive_published", "revision": revision})
            last_revision = revision
        event_log.extend(
            event
            for event in (row.get("orchestrator_events") or ())
            if isinstance(event, dict)
        )
    events = tuple(event_log[-8:])
    selected_label = labels.get(str(selected), str(selected or "waiting for decision"))
    return DiagnosticState(
        frame_index=int(latest.get("frame_index") or 0),
        decision_source=str(decision.get("decision_source") or "observe"),
        selected_action_id=str(selected) if selected else None,
        selected_label=selected_label,
        dispatch_accepted=bool(decision.get("dispatch_accepted")),
        dispatch_reason=str(decision.get("dispatch_reason") or "pending"),
        latency_policy_s=float(decision.get("latency_policy_s") or 0.0),
        capture_integrity=str(latest.get("capture_integrity") or "unknown"),
        focused=bool(latest.get("focused")),
        score_kind=score_kind,
        choices=choices,
        ocr_items=tuple(ocr_row.get("ocr_items") or ()),
        directive_revision=int(latest.get("directive_revision") or 0),
        directive_intent=str(latest.get("directive_intent") or "awaiting directive"),
        orchestrator_events=events,
    )


def _short(value: str, limit: int = 48) -> str:
    value = " ".join(value.split())
    return value if len(value) <= limit else value[: limit - 1] + "…"


class DiagnosticsWindow:
    def __init__(self, workspace: Path, *, refresh_ms: int = 250) -> None:
        self.workspace = workspace
        self.refresh_ms = max(100, refresh_ms)
        self.tail = JsonlTail(workspace / "shadow_react.jsonl")
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("Avatar Diagnostics")
        self.root.configure(bg=BACKGROUND)
        self.root.minsize(WIDTH, MIN_HEIGHT)
        self.root.geometry(f"{WIDTH}x840")
        self.root.attributes("-topmost", True)
        self.root.bind("<Escape>", lambda _event: self.root.destroy())
        self.root.bind("<Configure>", lambda _event: self._redraw())
        self.canvas = tk.Canvas(
            self.root,
            bg=BACKGROUND,
            highlightthickness=0,
            bd=0,
        )
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.scroll_y = 0
        self.content_height = 0
        self.last_file_mtime = 0.0
        self.state: DiagnosticState | None = None
        self.root.update_idletasks()
        self._place_at_right_edge()
        self._show_without_activating()
        self._poll()

    def _place_at_right_edge(self) -> None:
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        height = max(MIN_HEIGHT, min(900, screen_h - 72))
        x = max(0, screen_w - WIDTH - 20)
        self.root.geometry(f"{WIDTH}x{height}+{x}+24")

    def _show_without_activating(self) -> None:
        if os.name != "nt":
            self.root.deiconify()
            return
        try:
            import ctypes

            hwnd = int(self.root.winfo_id())
            user32 = ctypes.windll.user32
            exstyle = user32.GetWindowLongW(hwnd, -20)
            user32.SetWindowLongW(hwnd, -20, exstyle | 0x08000000 | 0x00000080)
            self.root.deiconify()
            user32.ShowWindow(hwnd, 4)
            user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)
        except (AttributeError, OSError, ValueError):
            self.root.deiconify()

    def _on_wheel(self, event: tk.Event) -> None:
        max_scroll = max(0, self.content_height - self.canvas.winfo_height())
        self.scroll_y = max(0, min(max_scroll, self.scroll_y - int(event.delta / 3)))
        self._redraw()

    def _poll(self) -> None:
        rows = self.tail.poll()
        self.state = build_diagnostic_state(rows)
        try:
            self.last_file_mtime = self.tail.path.stat().st_mtime
        except FileNotFoundError:
            self.last_file_mtime = 0.0
        self._redraw()
        self.root.after(self.refresh_ms, self._poll)

    def _text(
        self,
        x: int,
        y: int,
        value: str,
        *,
        size: int = 10,
        color: str = FOREGROUND,
        weight: str = "normal",
        anchor: str = "nw",
        width: int | None = None,
    ) -> int:
        return int(
            self.canvas.create_text(
                x,
                y - self.scroll_y,
                text=value,
                fill=color,
                font=("Consolas", size, weight),
                anchor=anchor,
                width=width,
            )
        )

    def _line(self, x0: int, y0: int, x1: int, y1: int, *, color: str = DIM) -> None:
        self.canvas.create_line(
            x0, y0 - self.scroll_y, x1, y1 - self.scroll_y, fill=color
        )

    def _rect(
        self,
        x0: int,
        y0: int,
        x1: int,
        y1: int,
        *,
        fill: str = "",
        outline: str = DIM,
        width: int = 1,
    ) -> None:
        self.canvas.create_rectangle(
            x0,
            y0 - self.scroll_y,
            x1,
            y1 - self.scroll_y,
            fill=fill,
            outline=outline,
            width=width,
        )

    def _section_title(self, y: int, label: str, meta: str = "") -> int:
        self._text(18, y, label.upper(), size=9, color=MUTED, weight="bold")
        if meta:
            self._text(self.canvas.winfo_width() - 18, y, meta, size=8, color=MUTED, anchor="ne")
        self._line(18, y + 19, self.canvas.winfo_width() - 18, y + 19)
        return y + 32

    def _redraw(self) -> None:
        if not hasattr(self, "canvas"):
            return
        self.canvas.delete("all")
        width = max(WIDTH, self.canvas.winfo_width())
        y = 18
        is_live = bool(self.last_file_mtime and time.time() - self.last_file_mtime < 2.5)
        status = "LIVE" if is_live else "IDLE"
        self._text(18, y, "AVATAR / DIAGNOSTICS", size=11, weight="bold")
        self._rect(width - 82, y - 1, width - 18, y + 20, fill=PANEL, outline=FOREGROUND if is_live else DIM)
        self._rect(width - 73, y + 7, width - 68, y + 12, fill=FOREGROUND if is_live else MUTED, outline="")
        self._text(width - 61, y + 3, status, size=8, color=FOREGROUND if is_live else MUTED)
        self._text(18, y + 23, _short(str(self.workspace), 64), size=8, color=MUTED)
        y += 58
        self._line(0, y, width, y, color=FOREGROUND)
        y += 18

        state = self.state
        if state is None:
            self._text(18, y, "WAITING FOR TELEMETRY", size=16, weight="bold")
            self._text(
                18,
                y + 34,
                "Start a shadow or direct collect session in this workspace.",
                size=9,
                color=MUTED,
                width=width - 36,
            )
            if self.tail.error:
                self._text(18, y + 78, _short(self.tail.error, 80), size=8, color=MUTED)
            self.content_height = y + 140
            return

        self._rect(18, y, width - 18, y + 92, fill=PANEL, outline=FOREGROUND)
        self._rect(18, y, 23, y + 92, fill=FOREGROUND, outline="")
        self._text(35, y + 12, "MODEL OUTPUT", size=8, color=MUTED, weight="bold")
        self._text(35, y + 33, _short(state.selected_label, 46), size=15, weight="bold")
        disposition = "DISPATCHED" if state.dispatch_accepted else state.dispatch_reason.upper()
        meta = f"{state.decision_source}  /  {disposition}  /  {state.latency_policy_s * 1000:.0f} MS"
        self._text(35, y + 65, _short(meta, 62), size=8, color=MUTED)
        y += 116

        y = self._section_title(y, "Choices", state.score_kind)
        if not state.choices:
            self._text(18, y, "No legal actions on this frame", size=9, color=MUTED)
            y += 34
        for choice in state.choices[:10]:
            label_color = FOREGROUND if choice.selected else MUTED
            prefix = "> " if choice.selected else "  "
            self._text(18, y, prefix + _short(choice.label, 42), size=9, color=label_color, weight="bold" if choice.selected else "normal")
            self._text(width - 18, y, f"{choice.score:0.2f}", size=8, color=label_color, anchor="ne")
            bar_y = y + 20
            self._rect(18, bar_y, width - 18, bar_y + 7, fill=SUBTLE, outline=DIM)
            fill_w = int((width - 36) * choice.score)
            if fill_w > 0:
                self._rect(18, bar_y, 18 + fill_w, bar_y + 7, fill=FOREGROUND if choice.selected else MUTED, outline="")
            y += 42
        y += 8

        y = self._section_title(y, "OCR output", f"{len(state.ocr_items)} TOKENS")
        if not state.ocr_items:
            self._text(18, y, "No OCR tokens detected", size=9, color=MUTED)
            y += 34
        for item in state.ocr_items[:10]:
            text = _short(str(item.get("text") or ""), 42)
            bbox = item.get("bbox") or ()
            coords = " ".join(f"{float(value):.2f}" for value in bbox) if len(bbox) == 4 else ""
            self._text(18, y, text, size=9, color=FOREGROUND)
            self._text(width - 18, y + 1, coords, size=7, color=MUTED, anchor="ne")
            self._line(18, y + 22, width - 18, y + 22, color=SUBTLE)
            y += 29
        y += 10

        y = self._section_title(y, "Orchestrator", f"DIRECTIVE R{state.directive_revision}")
        intent_lines = textwrap.wrap(state.directive_intent, width=56) or ["awaiting directive"]
        self._text(18, y, "INTENT", size=8, color=MUTED, weight="bold")
        self._text(18, y + 18, "\n".join(intent_lines[:3]), size=9, width=width - 36)
        y += 30 + 16 * min(3, len(intent_lines))
        if state.orchestrator_events:
            for event in reversed(state.orchestrator_events):
                kind = str(event.get("kind") or "event").replace("_", " ").upper()
                reasons = event.get("wake_reason") or ()
                detail = ", ".join(str(reason).replace("_", " ") for reason in reasons)
                if event.get("revision"):
                    detail = f"revision {int(event['revision'])}"
                self._rect(18, y, width - 18, y + (48 if detail else 34), fill=PANEL, outline=DIM)
                self._text(29, y + 9, kind, size=8, weight="bold")
                if detail:
                    self._text(29, y + 27, _short(detail, 54), size=8, color=MUTED)
                y += 56 if detail else 42
        else:
            self._text(18, y, "No orchestrator wake events yet", size=9, color=MUTED)
            y += 34

        y += 6
        self._line(0, y, width, y, color=FOREGROUND)
        self._text(18, y + 14, f"FRAME {state.frame_index:05d}", size=8, color=MUTED)
        focus_text = "FOCUSED" if state.focused else "UNFOCUSED"
        self._text(width - 18, y + 14, f"{state.capture_integrity.upper()} / {focus_text}", size=8, color=MUTED, anchor="ne")
        self.content_height = y + 48
        max_scroll = max(0, self.content_height - self.canvas.winfo_height())
        self.scroll_y = min(self.scroll_y, max_scroll)

    def run(self) -> None:
        self.root.mainloop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Live adaptive diagnostics side panel")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--refresh-ms", type=int, default=250)
    args = parser.parse_args(argv)
    DiagnosticsWindow(args.workspace, refresh_ms=args.refresh_ms).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
