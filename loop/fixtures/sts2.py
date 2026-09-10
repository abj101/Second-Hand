from __future__ import annotations

import os
import time
from dataclasses import replace
from typing import Any, Literal

from PIL import Image

from loop.fixtures.base import FixtureBase
from loop.log.schema import ActionLog, StepLog, Terminal
from loop.os.executor import OsController
from loop.os.aim import _copy_mark, is_menu_label
from loop.os.mouse import MENU_HOLD_S, MENU_HOVER_S
from loop.os.primitives import MousePrimitive, mouse_click, mouse_move
from loop.os.geometry import (
    ContentBox as _ContentBox,
    box_is_full,
    content_box_for,
    crop_content,
    geometry_error,
    geometry_from_image,
    is_top_left_scaled_capture,
    letterbox_frac,  # noqa: F401 — re-exported for tests
    map_content_xy,
)
from loop.os.profile import CaptureProfile
from loop.os.tables import resolve
from loop.os.window import CaptureProvenance, find_window
from loop.reward.sts import combat_turn_gate, score_sts

TITLES = ("Slay the Spire 2", "Slay the Spire II")

# The vertically compacting Loot controls live in this central column.  Keep
# the fixed Skip affordance and the top HUD out of the local refresh region.
_REWARD_STACK_ROI = (0.18, 0.20, 0.82, 0.78)
_IDENTITY_BOX: _ContentBox = (0.0, 0.0, 1.0, 1.0)


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
        self._vision_box: _ContentBox = _IDENTITY_BOX
        self._click_box: _ContentBox = _IDENTITY_BOX
        self._live_vision_box: _ContentBox = _IDENTITY_BOX
        self._live_click_box: _ContentBox = _IDENTITY_BOX
        self._geometry = geometry_from_image(Image.new("RGB", (16, 16), (0, 0, 0)))
        self._live_geometry = self._geometry
        self._grab_size: tuple[int, int] | None = None
        self._epoch_client_size: tuple[int, int] | None = None
        self._grab_provenance: CaptureProvenance | None = None
        self._geometry_ready = False
        self._integrity = "uncertain"
        self._live_integrity = "trusted"
        self._capture_source = ""
        self._epoch_reason = ""
        self.pending_aim_label = ""

    @property
    def _content_box(self) -> _ContentBox:
        return self._click_box

    @_content_box.setter
    def _content_box(self, value: _ContentBox) -> None:
        box = tuple(float(v) for v in value)
        self._click_box = box
        self._vision_box = box
        self._geometry_ready = True
        self._integrity = "trusted"
        if not self._epoch_reason:
            self._epoch_reason = "compat-assign"

    @property
    def _live_box(self) -> _ContentBox:
        return self._live_click_box

    @_live_box.setter
    def _live_box(self, value: _ContentBox) -> None:
        box = tuple(float(v) for v in value)
        self._live_click_box = box
        self._live_vision_box = box

    def click_geometry_trusted(self) -> bool:
        if self._geometry_ready:
            return True
        return self._grab_size is None

    def _reward_stack_roi(self) -> tuple[float, float, float, float]:
        """Return the normalized STS2 Loot column eligible for local refresh."""
        return _REWARD_STACK_ROI

    def neutral_park_xy(self) -> tuple[float, float]:
        return (0.02, 0.50)

    def _reward_reflow_roi(
        self,
        clicked_bbox: tuple[float, float, float, float] | None = None,
        clicked_point: tuple[float, float] | None = None,
    ) -> tuple[float, float, float, float] | None:
        """Return the movable Loot suffix affected by a reward click.

        Loot rows compact upward after a claim.  A click outside the central
        stack (notably Skip) has no fixture-local reflow region and must use
        the regular refresh path instead.
        """
        stack_x0, stack_y0, stack_x1, stack_y1 = self._reward_stack_roi()
        if clicked_bbox is not None:
            x0, y0, x1, y1 = (float(value) for value in clicked_bbox)
            if x1 <= stack_x0 or x0 >= stack_x1 or y1 <= stack_y0 or y0 >= stack_y1:
                return None
            return (stack_x0, max(stack_y0, y0), stack_x1, stack_y1)
        if clicked_point is not None:
            x, y = (float(value) for value in clicked_point)
            if not (stack_x0 <= x <= stack_x1 and stack_y0 <= y <= stack_y1):
                return None
            return (stack_x0, y, stack_x1, stack_y1)
        return None

    def overlay_dismiss_key(self) -> str | None:
        return "escape"

    def enrich_frame_marks(
        self,
        marks: list[Any],
        context: Any,
        host_stats: dict[str, Any] | None = None,
    ) -> list[Any]:
        """Apply STS entity semantics only on an unobscured combat surface."""
        del host_stats
        allowed = bool(getattr(context, "allows_domain_entities", False))
        screen = str(getattr(context, "screen", "") or "").strip().lower()
        out: list[Any] = []
        dialog_labels = {"yes", "no", "got it", "confirm", "continue", "close", "back", "prev", "next"}
        for mark in marks:
            role = str(getattr(mark, "role", "") or "").strip().lower()
            label = " ".join(str(getattr(mark, "label", "") or "").lower().replace("_", " ").split())
            # Reward cards are choices, not combat hand cards.  The detector
            # may type them as cards, so retain them as ordinary controls
            # before noncombat domain-role suppression below.
            if screen == "reward" and role == "card":
                mark = _copy_mark(mark, role="button")
                role = "button"
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
            out.append(mark)
        return out

    def grab_image(self) -> Image.Image:
        image = super().grab_image()
        provenance = None
        capture = getattr(self.controller, "capture", None)
        if capture is not None:
            provenance = getattr(capture, "last_provenance", None)
        self._record_grab(image, provenance)
        return image

    def _record_grab(self, image: Image.Image, provenance: CaptureProvenance | None = None) -> None:
        """Record live capture metadata. Does not commit the epoch click map."""
        self._grab_size = image.size
        self._grab_provenance = provenance
        if provenance is not None:
            live_box = provenance.content_box
            self._live_integrity = provenance.integrity
            self._capture_source = provenance.source
        elif is_top_left_scaled_capture(image):
            live_box = content_box_for(image)
            self._live_integrity = "uncertain"
            self._capture_source = self._capture_source or "image"
        else:
            live_box = content_box_for(image)
            self._live_integrity = "trusted"
            self._capture_source = self._capture_source or "image"
        self._live_vision_box = live_box
        self._live_click_box = live_box
        self._live_geometry = geometry_from_image(image)

    def _is_client_frame(self, image: Image.Image) -> bool:
        grab_size = self._grab_size
        if grab_size is None:
            return False
        return image.size == grab_size

    def commit_observation(self, image: Image.Image) -> None:
        """Bind vision/click maps to a trusted raw client grab. Same size stays frozen."""
        if not self._is_client_frame(image):
            return
        size = image.size
        provenance = self._grab_provenance
        if provenance is not None and provenance.integrity != "trusted":
            integrity = "uncertain"
            reason = provenance.reason
        elif provenance is None and is_top_left_scaled_capture(image):
            integrity = "uncertain"
            reason = "top-left-scaled"
        else:
            integrity = "trusted"
            reason = provenance.reason if provenance is not None else "image"
        self._live_integrity = integrity
        if integrity != "trusted":
            if not self._geometry_ready:
                self._epoch_reason = f"blocked:{reason}"
                self._integrity = "uncertain"
            else:
                self._epoch_reason = f"retain:{reason}"
            return
        if self._epoch_client_size == size and self._geometry_ready:
            return
        box = content_box_for(image)
        if provenance is not None:
            box = provenance.content_box
        bind = "rebind-resize" if self._geometry_ready else "bind"
        self._vision_box = box
        self._click_box = box
        self._geometry = geometry_from_image(image)
        self._epoch_client_size = size
        self._geometry_ready = True
        self._integrity = "trusted"
        self._epoch_reason = bind
        if provenance is not None:
            self._capture_source = provenance.source

    def observe_image(self, image: Image.Image) -> Image.Image:
        incoming = content_box_for(image)
        last = self._vision_box
        last_cropped = not box_is_full(last)
        incoming_full = box_is_full(incoming)
        grab_size = self._grab_size
        already_content = last_cropped and incoming_full and (
            grab_size is None or image.size != grab_size
        )
        if already_content:
            return image
        return crop_content(image, self._vision_box)

    def check_geometry(self, image: Image.Image) -> None:
        problem = geometry_error(image)
        if problem:
            raise RuntimeError(problem)

    def geometry_line(self, image: Image.Image) -> str:
        cw, ch = self._grab_size or image.size
        vx0, vy0, vx1, vy1 = self._vision_box
        cx0, cy0, cx1, cy1 = self._click_box
        source = self._capture_source or "?"
        return (
            f"client={cw}x{ch} source={source} integrity={self._integrity} "
            f"vision={vx0:.2f},{vy0:.2f},{vx1:.2f},{vy1:.2f} "
            f"click={cx0:.2f},{cy0:.2f},{cx1:.2f},{cy1:.2f} "
            f"epoch={self._epoch_reason or 'none'}"
        )

    def _require_click_geometry(self) -> None:
        if not self.click_geometry_trusted():
            raise RuntimeError("untrusted-click-geometry")

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
        def screen_at(index: int) -> str:
            return str((steps[index].metrics or {}).get("screen") or "").strip().lower()

        def floor_at_or_before(index: int) -> float | None:
            for row in reversed(steps[: index + 1]):
                floor = (row.metrics or {}).get("floor")
                if isinstance(floor, (int, float)) and not isinstance(floor, bool):
                    return float(floor)
            return None

        for combat_index, combat_row in enumerate(steps):
            if (
                not (combat_row.metrics or {}).get("combat_won")
                or screen_at(combat_index) != "reward"
            ):
                continue
            reward_index = combat_index
            map_index = next(
                (
                    index
                    for index in range(reward_index + 1, len(steps))
                    if screen_at(index) == "map"
                ),
                None,
            )
            if map_index is None:
                continue

            floor = floor_at_or_before(combat_index)
            if floor is not None and any(
                isinstance((row.metrics or {}).get("floor"), (int, float))
                and not isinstance((row.metrics or {}).get("floor"), bool)
                and float((row.metrics or {})["floor"]) > floor
                for row in steps[map_index + 1 :]
            ):
                return True

            destinations = {"combat", "rest", "shop", "event", "reward", "loot"}
            for index in range(map_index + 1, len(steps)):
                row = steps[index]
                intent = row.intent or {}
                node_advanced = (
                    screen_at(index - 1) == "map"
                    and str(intent.get("kind") or "") == "click_control"
                    and str(intent.get("control_id") or "").startswith("control:node_")
                    and bool(row.result.executed)
                    and row.result.pixel_changed
                )
                if not node_advanced:
                    continue
                if screen_at(index) in destinations:
                    return True
                # STS2 may insert one unlabeled confirmation surface between
                # selecting a map node and entering its encounter.
                if screen_at(index) not in {"", "other"}:
                    continue
                for follow_index in range(index + 1, min(index + 3, len(steps))):
                    follow = steps[follow_index]
                    follow_intent = follow.intent or {}
                    follow_control = str(follow_intent.get("control_id") or "")
                    if screen_at(follow_index) in {"map", "reward"}:
                        break
                    if (
                        screen_at(follow_index) in destinations
                        and str(follow_intent.get("kind") or "") == "click_control"
                        and follow_control.startswith(("control:confirm", "control:continue"))
                        and bool(follow.result.executed)
                        and follow.result.pixel_changed
                    ):
                        return True
        return False

    def _remap(self, prim: Any) -> Any:
        if not isinstance(prim, MousePrimitive):
            return prim
        x, y = prim.x, prim.y
        if x is None or y is None:
            return prim
        nx, ny = map_content_xy(float(x), float(y), self._click_box)
        x2 = y2 = None
        if prim.x2 is not None and prim.y2 is not None:
            x2, y2 = map_content_xy(float(prim.x2), float(prim.y2), self._click_box)
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
        self._require_click_geometry()
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
        self._require_click_geometry()
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
