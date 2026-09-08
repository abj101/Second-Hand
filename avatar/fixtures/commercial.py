from __future__ import annotations

from dataclasses import dataclass

from avatar.harness.executor import OsController
from avatar.harness.primitives import NamedAction
from avatar.harness.tables import NDS_TABLE, STS_TABLE
from avatar.harness.window import find_window, focus_window, wait_for_window


@dataclass
class WindowFixture:
    title: str
    table_name: str
    notes: str

    def require_window(self):
        target = wait_for_window(self.title, timeout_s=5)
        focus_window(target)
        return target

    def grab(self):
        from avatar.harness.window import WindowCapture

        cap = WindowCapture(self.title, skip_unchanged=False)
        image = cap.grab_image()
        return image, cap.target.bbox() | {"hwnd": cap.target.hwnd}

    def controller(self) -> OsController:
        self.require_window()
        from avatar.config import load_config
        from avatar.harness.profile import profile_from_config

        fixture_key = "platinum" if self.table_name == "nds" else "sts"
        return OsController(self.title, profile=profile_from_config(load_config(), fixture_key))

    def resolve(self, name: str) -> NamedAction:
        table = NDS_TABLE if self.table_name == "nds" else STS_TABLE
        return table[name]


PLATINUM = WindowFixture(
    title="melonDS",
    table_name="nds",
    notes="User-owned Pokémon Platinum dump + melonDS. Dual-screen crop is the full client.",
)

STS = WindowFixture(
    title="Slay the Spire",
    table_name="sts",
    notes="Steam install of Slay the Spire. Computer-use harness only.",
)

ELDEN_RING = WindowFixture(
    title="ELDEN RING",
    table_name="sts",
    notes="Stretch only. Score explore/menus, not bosses.",
)


def platinum_available() -> bool:
    return find_window(PLATINUM.title) is not None


def sts_available() -> bool:
    return find_window(STS.title) is not None
