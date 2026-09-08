"""Optional layout adapters for game-specific combat/hand geometry.

Shared aim/core paths delegate band checks and combat-only mark rules here.
Default adapter preserves legacy STS2 combat layout; generic adapter is a no-op
for hand-card takeover and combat sprite-band role stamps.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

# Combat layout bands (tuned from collect step 2).
HUD_CHROME_Y = 0.08
ENEMY_Y_MIN = 0.15
ENEMY_Y_MAX = 0.45
HAND_Y = 0.70

_CORNER_HUD_X = 0.74
_CORNER_HUD_Y = 0.20


@runtime_checkable
class LayoutAdapter(Protocol):
    """Band geometry plus optional combat hand/sprite rules."""

    @property
    def hud_chrome_y(self) -> float: ...

    @property
    def hand_y(self) -> float: ...

    @property
    def enemy_y_min(self) -> float: ...

    @property
    def enemy_y_max(self) -> float: ...

    def in_enemy_band(self, y: float, x: float | None = None) -> bool: ...

    def in_combat_sprite_band(self, y: float, x: float | None = None) -> bool: ...

    def in_corner_hud(self, x: float, y: float) -> bool: ...

    def enables_hand_card_proposals(self, screen: str | None) -> bool: ...

    def enables_combat_role_stamps(self, screen: str | None) -> bool: ...


def _corner_hud_excluded(y: float, x: float | None) -> bool:
    return x is not None and float(x) > _CORNER_HUD_X and float(y) < _CORNER_HUD_Y


class LegacyCombatLayoutAdapter:
    """STS2 combat bands + hand-card crop + sprite-band role stamps (default-on)."""

    hud_chrome_y = HUD_CHROME_Y
    hand_y = HAND_Y
    enemy_y_min = ENEMY_Y_MIN
    enemy_y_max = ENEMY_Y_MAX

    def in_enemy_band(self, y: float, x: float | None = None) -> bool:
        if y < self.hud_chrome_y or y > self.enemy_y_max:
            return False
        if y < self.enemy_y_min:
            return False
        if _corner_hud_excluded(y, x):
            return False
        return True

    def in_combat_sprite_band(self, y: float, x: float | None = None) -> bool:
        if not (self.hud_chrome_y < float(y) < self.hand_y):
            return False
        if _corner_hud_excluded(y, x):
            return False
        return True

    def in_corner_hud(self, x: float, y: float) -> bool:
        return _corner_hud_excluded(y, x)

    def enables_hand_card_proposals(self, screen: str | None) -> bool:
        return (screen or "").strip().lower() == "combat"

    def enables_combat_role_stamps(self, screen: str | None) -> bool:
        return (screen or "").strip().lower() == "combat"


class GenericLayoutAdapter:
    """Geometry-only bands; no hand-card takeover or combat role stamping."""

    hud_chrome_y = HUD_CHROME_Y
    hand_y = HAND_Y
    enemy_y_min = ENEMY_Y_MIN
    enemy_y_max = ENEMY_Y_MAX

    def in_enemy_band(self, y: float, x: float | None = None) -> bool:
        return False

    def in_combat_sprite_band(self, y: float, x: float | None = None) -> bool:
        return False

    def in_corner_hud(self, x: float, y: float) -> bool:
        return False

    def enables_hand_card_proposals(self, screen: str | None) -> bool:
        return False

    def enables_combat_role_stamps(self, screen: str | None) -> bool:
        return False


_DEFAULT_ADAPTER = LegacyCombatLayoutAdapter()


def default_layout_adapter() -> LayoutAdapter:
    return _DEFAULT_ADAPTER


def legacy_combat_layout_adapter() -> LayoutAdapter:
    return LegacyCombatLayoutAdapter()


def generic_layout_adapter() -> LayoutAdapter:
    return GenericLayoutAdapter()
