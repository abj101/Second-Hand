from __future__ import annotations

import os
import time
from dataclasses import replace
from typing import Any, Literal

import numpy as np
from PIL import Image

from loop.fixtures.base import FixtureBase
from loop.log.schema import ActionLog, StepLog, Terminal
from loop.os.executor import OsController
from loop.os.aim import HAND_Y, _copy_mark, _is_placeholder_label, is_menu_label, label_kind
from loop.os.mouse import MENU_HOLD_S, MENU_HOVER_S
from loop.os.primitives import MousePrimitive, mouse_click, mouse_move
from loop.os.profile import CaptureProfile
from loop.os.tables import resolve
from loop.os.window import find_window
from loop.reward.sts import combat_turn_gate, score_sts

TITLES = ("Slay the Spire 2", "Slay the Spire II")
_ContentBox = tuple[float, float, float, float]
TARGET_ASPECT = 16 / 9
# Relative |w/h - 16/9| / (16/9). 16:10 (~0.10) still plays; 1278x1360 (~0.47) does not.
ASPECT_FAIL = 0.15


def normalize_host_stats(stats: dict[str, Any]) -> dict[str, Any]:
    """Map post-combat Loot UI onto canonical reward screen/activity."""
    from loop.reward.sts import _loot_heading, _normalized_screen, _reward_action_present

    out = dict(stats or {})
    blob = str(out.get("ocr") or "")
    low = blob.lower()
    if "end turn" in low:
        return out
    screen = _normalized_screen(out)
    if screen == "reward" or _loot_heading(blob) or _reward_action_present(out, blob):
        out["screen"] = "reward"
        out["activity"] = "reward"
        out["combat_active"] = False
        out["player"] = None
        out["enemies"] = []
    return out


def resolve_window_title() -> str:
    env = os.environ.get("AVATAR_STS2_WINDOW")
    if env:
        return env
    for title in TITLES:
        if find_window(title) is not None:
            return title
    return TITLES[0]


def letterbox_frac(image: Image.Image, *, threshold: float = 6.0) -> _ContentBox:
    """Client-normalized (x0, y0, x1, y1) of the non-black game rect."""
    gray = np.asarray(image.convert("L"), dtype=np.float32)
    if gray.size == 0:
        return (0.0, 0.0, 1.0, 1.0)
    h, w = gray.shape
    rows = gray.mean(axis=1) > threshold
    cols = gray.mean(axis=0) > threshold
    ys = np.flatnonzero(rows)
    xs = np.flatnonzero(cols)
    if ys.size == 0 or xs.size == 0:
        return (0.0, 0.0, 1.0, 1.0)
    x0, x1 = float(xs[0]) / w, float(xs[-1] + 1) / w
    y0, y1 = float(ys[0]) / h, float(ys[-1] + 1) / h
    if (x1 - x0) < 0.5 or (y1 - y0) < 0.5:
        return (0.0, 0.0, 1.0, 1.0)
    return (x0, y0, x1, y1)


def _aspect(width: float, height: float) -> float:
    return float(width) / max(float(height), 1e-6)


def _aspect_error(width: float, height: float, *, target: float = TARGET_ASPECT) -> float:
    return abs(_aspect(width, height) - target) / target


def fit_aspect_box(
    width: int,
    height: int,
    *,
    aspect: float = TARGET_ASPECT,
) -> _ContentBox:
    """Largest centered `aspect` rectangle inside width×height, as client fractions."""
    if width <= 0 or height <= 0:
        return (0.0, 0.0, 1.0, 1.0)
    have = _aspect(width, height)
    if abs(have - aspect) / aspect < 1e-4:
        return (0.0, 0.0, 1.0, 1.0)
    if have > aspect:
        content_w = height * aspect
        pad = (width - content_w) / 2.0 / width
        return (pad, 0.0, 1.0 - pad, 1.0)
    content_h = width / aspect
    pad = (height - content_h) / 2.0 / height
    return (0.0, pad, 1.0, 1.0 - pad)


def content_box_for(image: Image.Image) -> _ContentBox:
    """Letterbox if black bars exist; else snap a full-frame grab to 16:9."""
    letter = letterbox_frac(image)
    full = letter[0] <= 0.01 and letter[1] <= 0.01 and letter[2] >= 0.99 and letter[3] >= 0.99
    if not full:
        return letter
    w, h = image.size
    return fit_aspect_box(w, h)


def format_geometry(image: Image.Image, box: _ContentBox | None = None) -> str:
    w, h = image.size
    box = box if box is not None else content_box_for(image)
    cw = (box[2] - box[0]) * w
    ch = (box[3] - box[1]) * h
    return (
        f"client={w}x{h} aspect={_aspect(w, h):.3f} "
        f"content={cw:.0f}x{ch:.0f} box={box[0]:.2f},{box[1]:.2f},{box[2]:.2f},{box[3]:.2f}"
    )


def geometry_error(image: Image.Image) -> str | None:
    """None when the client is close to 16:9. Message otherwise."""
    w, h = image.size
    err = _aspect_error(w, h)
    if err <= ASPECT_FAIL:
        return None
    return (
        f"Client {w}x{h} aspect {_aspect(w, h):.3f} is not 16:9 "
        f"(need ~{TARGET_ASPECT:.3f}). Clicks miss the letterbox. "
        "Use borderless 1280x720 or 1920x1080."
    )


def map_content_xy(x: float, y: float, box: _ContentBox) -> tuple[float, float]:
    x0, y0, x1, y1 = box
    return (x0 + x * (x1 - x0), y0 + y * (y1 - y0))


class Sts2Fixture(FixtureBase):
    """Turn-based mouse/key fixture. No pad, no chapel HUD."""

    def __init__(self, *, controller: OsController | None = None) -> None:
        super().__init__(
            id="sts2",
            window_title=resolve_window_title(),
            device="mouse",
            action_table="sts",
            controller=controller,
            profile=CaptureProfile(),
            min_survived_steps=8,
            pace="turn",
            settle_ms=1100,
            quiet_timeout_ms=8000,
            obs_longest_side=1280,
        )
        self._content_box: _ContentBox = (0.0, 0.0, 1.0, 1.0)
        self.pending_aim_label = ""

    def enrich_frame_marks(
        self,
        marks: list[Any],
        context: Any,
        host_stats: dict[str, Any] | None = None,
    ) -> list[Any]:
        """Apply STS entity semantics only on an unobscured combat surface."""
        del host_stats
        allowed = bool(getattr(context, "allows_domain_entities", False))
        out: list[Any] = []
        dialog_labels = {"yes", "no", "got it", "confirm", "continue", "close", "back", "prev", "next"}
        for mark in marks:
            role = str(getattr(mark, "role", "") or "").strip().lower()
            label = " ".join(str(getattr(mark, "label", "") or "").lower().replace("_", " ").split())
            if not allowed:
                if role in {"card", "enemy", "player", "target"} or (
                    bool(getattr(context, "blocking_overlay", False))
                    and label == "end turn"
                ):
                    mark = _copy_mark(mark, role="suppressed", object_id="")
                out.append(mark)
                continue
            if role in {"button", "control", "dialog", "nav_prev", "nav_next"} or label in dialog_labels:
                out.append(mark)
                continue
            if role == "target":
                mark = _copy_mark(mark, role="enemy")
            elif role not in {"enemy", "player", "card"} and (
                float(mark.y) >= HAND_Y
                and label
                and "end turn" not in label
                and not _is_placeholder_label(mark.label)
                and label_kind(mark.label) not in {"progress", "danger"}
            ):
                mark = _copy_mark(mark, role="card")
            out.append(mark)
        return out

    def grab_image(self) -> Image.Image:
        image = super().grab_image()
        self._content_box = content_box_for(image)
        return image

    def observe_image(self, image: Image.Image) -> Image.Image:
        x0, y0, x1, y1 = content_box_for(image)
        self._content_box = (x0, y0, x1, y1)
        w, h = image.size
        return image.crop((int(x0 * w), int(y0 * h), int(x1 * w), int(y1 * h)))

    def check_geometry(self, image: Image.Image) -> None:
        problem = geometry_error(image)
        if problem:
            raise RuntimeError(problem)

    def geometry_line(self, image: Image.Image) -> str:
        return format_geometry(image, self._content_box)

    def scorer(self, image: Image.Image) -> dict[str, Any]:
        return score_sts(image)

    def act_gate(self, image: Image.Image) -> Literal["ours", "theirs", "unknown"]:
        return combat_turn_gate(image)

    def terminal(self, metrics: dict[str, Any], image: Image.Image) -> Terminal:
        del image
        if metrics.get("fail_screen"):
            return "fail"
        if metrics.get("win_screen"):
            return "win"
        return "alive"

    def success(self, steps: list[StepLog], terminal: Terminal) -> bool:
        if terminal == "win":
            return True
        if terminal == "fail":
            return False
        screens = [str((row.metrics or {}).get("screen") or "") for row in steps]
        left_menu = any(kind in {"map", "combat", "reward", "loot", "rest", "shop", "event"} for kind in screens)
        saw_combat = "combat" in screens
        floors = [
            float(row.metrics["floor"])
            for row in steps
            if isinstance((row.metrics or {}).get("floor"), (int, float))
        ]
        floor_up = len(floors) >= 2 and max(floors) > min(floors)
        return bool(saw_combat and left_menu) or floor_up

    def _remap(self, prim: Any) -> Any:
        if not isinstance(prim, MousePrimitive):
            return prim
        x, y = prim.x, prim.y
        if x is None or y is None:
            return prim
        nx, ny = map_content_xy(float(x), float(y), self._content_box)
        x2 = y2 = None
        if prim.x2 is not None and prim.y2 is not None:
            x2, y2 = map_content_xy(float(prim.x2), float(prim.y2), self._content_box)
        return replace(prim, x=nx, y=ny, x2=x2, y2=y2)

    def execute(self, name: str, *, duration_ms: int | None = None) -> list[str]:
        del duration_ms
        if self.controller is None:
            raise RuntimeError("Fixture sts2 has no controller")
        bound = resolve(self.action_table, name)
        self.controller.execute(tuple(self._remap(p) for p in bound.primitives))
        self.executed.append(name)
        return [name]

    def execute_chord(self, action: ActionLog, *, settle: bool = True) -> list[str]:
        if self.controller is None:
            raise RuntimeError("Fixture sts2 has no controller")
        # Every STS interaction here is either a discrete click or a buttonless
        # hover.  Clear any leaked physical button state before moving the
        # cursor so a prior interrupted input cannot turn either operation into
        # a drag.  This does not cancel the game's click-selected card state.
        self.controller.release_all()
        label = self.pending_aim_label
        self.pending_aim_label = ""
        menu = is_menu_label(label)
        hover_s = MENU_HOVER_S if menu else None
        hold_s = MENU_HOLD_S if menu else None
        if action.click_x is not None and action.click_y is not None:
            prim = (
                mouse_move(float(action.click_x), float(action.click_y))
                if action.hover
                else mouse_click(
                    float(action.click_x),
                    float(action.click_y),
                    hover_s=hover_s,
                    hold_s=hold_s,
                )
            )
            self.controller.execute((self._remap(prim),))
            name = action.derive_name() or ("hv" if action.hover else "pt")
            self.executed.append(name)
            sent = [name]
            if action.hover:
                dwell = max(0.35, (action.duration_ms or 700) / 1000)
                time.sleep(dwell)
            # Same-frame target/click_x2 is leftover schema. Combat is two live steps.
        else:
            token = (action.name or "").split("+")[0]
            if token.startswith("mk_"):
                sent = []
            elif token:
                sent = self.execute(token)
            else:
                sent = super().execute_chord(action)
        if settle and self.settle_ms:
            time.sleep(self.settle_ms / 1000)
        return sent

    def execute_card_drag(
        self,
        action: ActionLog,
        *,
        destination_y: float = 0.55,
        settle: bool = False,
    ) -> list[str]:
        """Commit a targetless card with one drag primitive into the play area."""
        if self.controller is None:
            raise RuntimeError("Fixture sts2 has no controller")
        if action.click_x is None or action.click_y is None:
            return []
        self.controller.release_all()
        prim = MousePrimitive(
            op="drag",
            x=float(action.click_x),
            y=float(action.click_y),
            x2=float(action.click_x),
            y2=float(destination_y),
            duration_ms=250,
        )
        self.controller.execute((self._remap(prim),))
        name = action.derive_name() or "card_drag"
        self.executed.append(name)
        if settle and self.settle_ms:
            time.sleep(self.settle_ms / 1000)
        return [name]

    def after_fail(self, *, softlock: bool = False, eval_mode: bool = False) -> Literal["in_game", "snapshot"]:
        del softlock, eval_mode
        if self.controller is not None:
            try:
                self.execute("g")
                time.sleep(0.4)
                self.execute("enter")
            except Exception:  # noqa: BLE001 — continue must not abort the session
                self.controller.release_all()
        return "in_game"
