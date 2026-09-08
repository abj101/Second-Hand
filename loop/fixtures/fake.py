from __future__ import annotations

from typing import Any, Literal

from PIL import Image

from loop.fixtures.base import FixtureBase
from loop.fixtures.elden_hud.synth import (
    FAIL_CROP,
    HEALTH_CROP,
    STAMINA_CROP,
    paint_fail,
    paint_health,
    paint_stamina,
    render_frame,
)
from loop.log.schema import ActionLog
from loop.os.chords import chord_name
from loop.os.profile import CaptureProfile
from loop.os.tables import names
from loop.reward.hud import score_hud

# Re-export for tests that import crops from fake.
__all__ = ["FAIL_CROP", "HEALTH_CROP", "FakeFixture", "paint_fail", "paint_health"]


class FakeFixture(FixtureBase):
    """In-memory fixture for pytest and --skip-models collect. No Steam."""

    def __init__(
        self,
        *,
        health: float = 1.0,
        fail: bool = False,
        steps_until_fail: int | None = None,
        good_action: str = "a",
        bad_action: str = "b",
        min_survived_steps: int = 8,
        stamina: float = 1.0,
        runes: int | None = None,
    ) -> None:
        super().__init__(
            id="fake",
            window_title="Loop Fake",
            device="pad",
            action_table="xbox360",
            profile=CaptureProfile(),
            min_survived_steps=min_survived_steps,
        )
        self.health = health
        self.stamina = stamina
        self.runes = runes
        self.force_fail = fail
        self.steps_until_fail = steps_until_fail
        self.good_action = good_action
        self.bad_action = bad_action
        self.steps = 0
        self.frames: list[Image.Image] = []
        self._hud = {
            "health": {
                "crop": list(HEALTH_CROP),
                "fill_rgb": [180, 20, 20],
                "empty_rgb": [40, 20, 20],
                "tolerance": 28,
                "bar_row": 0.5,
            },
            "stamina": {
                "crop": list(STAMINA_CROP),
                "fill_rgb": [200, 170, 40],
                "empty_rgb": [40, 32, 12],
                "tolerance": 28,
                "bar_row": 0.5,
            },
            "fail_screen": {"crop": list(FAIL_CROP), "min_red_frac": 0.15},
        }

    def _canvas(self) -> Image.Image:
        if self.force_fail or (self.steps_until_fail is not None and self.steps >= self.steps_until_fail):
            return render_frame(health=self.health, stamina=self.stamina, runes=self.runes, fail=True)
        return render_frame(health=self.health, stamina=self.stamina, runes=self.runes, fail=False)

    def after_fail(self, *, softlock: bool = False, eval_mode: bool = False) -> Literal["in_game", "snapshot"]:
        self.health = 1.0
        self.stamina = 1.0
        self.steps = 0
        self.force_fail = False
        if eval_mode or softlock:
            return "snapshot"
        return "in_game"

    def grab_image(self) -> Image.Image:
        frame = self._canvas()
        self.frames.append(frame)
        return frame

    def _apply_action(self, token: str | None) -> None:
        self.steps += 1
        if token == self.good_action:
            self.health = min(1.0, self.health + 0.10)
            self.stamina = min(1.0, self.stamina + 0.05)
        elif token == self.bad_action:
            self.health = max(0.0, self.health - 0.50)
            self.stamina = max(0.0, self.stamina - 0.20)
            if self.health <= 0.02:
                self.force_fail = True
        else:
            self.health = max(0.0, self.health - 0.15)
            self.stamina = max(0.0, self.stamina - 0.05)
        if self.steps_until_fail is not None and self.steps >= self.steps_until_fail:
            self.force_fail = True

    def execute(self, name: str, *, duration_ms: int | None = None) -> list[str]:
        del duration_ms
        if name not in names(self.action_table) and "+" not in name:
            raise KeyError(name)
        token = self.good_action if self.good_action in name.split("+") else (
            self.bad_action if self.bad_action in name.split("+") else name.split("+")[0]
        )
        self._apply_action(token)
        self.executed.append(name)
        return [name]

    def execute_chord(self, action: ActionLog, *, settle: bool = True) -> list[str]:
        del settle
        name = chord_name(action) or action.name or "idle"
        tokens = set(action.held) | set(action.tapped)
        if action.name:
            tokens.update(action.name.split("+"))
        if self.good_action in tokens:
            token = self.good_action
        elif self.bad_action in tokens:
            token = self.bad_action
        else:
            token = name.split("+")[0] if name else None
        self._apply_action(token)
        self.executed.append(name)
        return [name]

    def scorer(self, image: Image.Image) -> dict[str, Any]:
        return score_hud(image, self._hud)

    def success(self, steps, terminal):
        from loop.fixtures.base import alive_success

        return alive_success(steps, terminal, min_survived_steps=self.min_survived_steps)
