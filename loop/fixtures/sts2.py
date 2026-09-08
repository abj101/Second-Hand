from __future__ import annotations

import os
import time
from dataclasses import replace
from typing import Any, Literal

from PIL import Image

from loop.fixtures.base import FixtureBase
from loop.log.schema import ActionLog, StepLog, Terminal
from loop.os.executor import OsController
from loop.os.aim import HAND_Y, _copy_mark, _is_placeholder_label, is_menu_label, label_kind
from loop.os.mouse import MENU_HOLD_S, MENU_HOVER_S
from loop.os.primitives import MousePrimitive, mouse_click, mouse_move
from loop.os.geometry import (
    ContentBox as _ContentBox,
    content_box_for,
    crop_content,
    format_geometry,
    geometry_error,
    geometry_from_image,
    letterbox_frac,  # noqa: F401 — re-exported for tests
    map_content_xy,
)
from loop.os.profile import CaptureProfile
from loop.os.tables import resolve
from loop.os.window import find_window
from loop.reward.sts import combat_turn_gate, score_sts

TITLES = ("Slay the Spire 2", "Slay the Spire II")


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
        self._geometry = geometry_from_image(Image.new("RGB", (16, 16), (0, 0, 0)))
        self._grab_size: tuple[int, int] | None = None
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
        self._geometry = geometry_from_image(image)
        self._grab_size = image.size
        return image

    def observe_image(self, image: Image.Image) -> Image.Image:
        incoming = content_box_for(image)
        last = self._content_box
        last_cropped = not (
            last[0] <= 0.01 and last[1] <= 0.01 and last[2] >= 0.99 and last[3] >= 0.99
        )
        incoming_full = (
            incoming[0] <= 0.01 and incoming[1] <= 0.01 and incoming[2] >= 0.99 and incoming[3] >= 0.99
        )
        grab_size = self._grab_size
        already_content = last_cropped and incoming_full and (
            grab_size is None or image.size != grab_size
        )
        if already_content:
            return image
        self._content_box = incoming
        self._geometry = geometry_from_image(image)
        return crop_content(image, self._content_box)

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
