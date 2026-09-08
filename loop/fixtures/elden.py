from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from typing import Any, Literal

import yaml
from PIL import Image

from loop.fixtures.base import FixtureBase
from loop.os.executor import OsController
from loop.os.profile import CaptureProfile
from loop.reward.hud import hud_visible, score_hud, score_hud_search

HUD_YAML = Path(__file__).with_name("elden_hud.yaml")


def load_hud_spec(path: Path | None = None) -> dict[str, Any]:
    target = path or HUD_YAML
    if not target.exists():
        return {}
    data = yaml.safe_load(target.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def live_save_path(hud: dict[str, Any] | None = None) -> Path | None:
    env = os.environ.get("AVATAR_ELDEN_SAVE")
    if env:
        return Path(env)
    spec = hud or {}
    snap = spec.get("snapshot") if isinstance(spec.get("snapshot"), dict) else {}
    raw = snap.get("live_save")
    if raw:
        return Path(str(raw))
    return None


class EldenFixture(FixtureBase):
    def __init__(self, *, controller: OsController | None = None, hud_path: Path | None = None) -> None:
        super().__init__(
            id="elden",
            window_title="ELDEN RING",
            device="pad",
            action_table="xbox360",
            controller=controller,
            profile=CaptureProfile(),
        )
        self._hud = load_hud_spec(hud_path)
        success = self._hud.get("success") if isinstance(self._hud.get("success"), dict) else {}
        self.min_survived_steps = int(success.get("min_survived_steps") or 16)
        fail_spec = self._hud.get("after_fail") if isinstance(self._hud.get("after_fail"), dict) else {}
        self._confirm = str(fail_spec.get("confirm") or "")
        self._fail_delay_ms = int(fail_spec.get("delay_ms") or 800)
        self._hud_dy = 0.0
        self._hud_fitted = False

    def start_snapshot(self) -> Path | None:
        env = Path(__file__).resolve().parents[2] / "data" / "loop" / "elden" / "start.sl2"
        return env if env.exists() else None

    def scorer(self, image: Image.Image) -> dict[str, Any]:
        if self._hud_fitted:
            if abs(self._hud_dy) < 1e-9:
                return score_hud(image, self._hud)
            shifted = dict(self._hud)
            for key in ("health", "stamina"):
                spec = self._hud.get(key)
                if not isinstance(spec, dict):
                    continue
                spec = dict(spec)
                crop = spec.get("crop")
                if isinstance(crop, (list, tuple)) and len(crop) >= 4:
                    from loop.reward.hud import shift_crop

                    spec["crop"] = shift_crop(crop, self._hud_dy)
                shifted[key] = spec
            return score_hud(image, shifted)
        scored, dy = score_hud_search(image, self._hud)
        if hud_visible(scored):
            self._hud_fitted = True
            self._hud_dy = dy
        return scored

    def _confirm_name(self) -> str:
        if self._confirm:
            return self._confirm
        return "a" if self.action_table == "xbox360" else "enter"

    def restore_snapshot(self) -> bool:
        src = self.start_snapshot()
        dest = live_save_path(self._hud)
        if src is None or dest is None or not src.exists():
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            shutil.copy2(dest, dest.with_suffix(dest.suffix + ".bak"))
        shutil.copy2(src, dest)
        return True

    def after_fail(self, *, softlock: bool = False, eval_mode: bool = False) -> Literal["in_game", "snapshot"]:
        if eval_mode or softlock:
            if self.restore_snapshot():
                return "snapshot"
        if self.controller is not None:
            if self._fail_delay_ms:
                time.sleep(self._fail_delay_ms / 1000)
            try:
                self.execute(self._confirm_name())
            except Exception:  # noqa: BLE001 — continue tap must not abort the session
                self.controller.release_all()
        return "in_game"
