"""UI screen vs underlying play activity.

Overlays (tutorials, tips, pause chrome) can change ``screen`` without ending
the play mode underneath. Targeting and combat perceive gates should use
``activity``, not ``screen`` alone.

Fixtures publish activity via host/metrics fields (no game-specific names in
the resolver itself):

- ``activity``: string play mode (e.g. ``\"combat\"``, ``\"map\"``, ``\"idle\"``)
- ``combat_active``: bool shorthand → activity ``\"combat\"``

When neither is set, activity defaults to ``screen``.
"""

from __future__ import annotations

from typing import Any


def activity_from_state(
    *,
    screen: str | None = None,
    metrics: dict[str, Any] | None = None,
    host_stats: dict[str, Any] | None = None,
) -> str:
    """Resolve underlying play activity from fixture-published state."""
    for src in (host_stats, metrics):
        if not isinstance(src, dict):
            continue
        raw = src.get("activity")
        if isinstance(raw, str) and raw.strip():
            return raw.strip().lower()
        flag = src.get("combat_active")
        if flag is True:
            return "combat"
    return (screen or "").strip().lower() or "other"


def merge_activity_fields(
    out: dict[str, Any],
    *,
    screen: str | None,
    combat_active: bool | None = None,
    activity: str | None = None,
) -> dict[str, Any]:
    """Attach activity fields without clobbering an explicit caller value."""
    if activity and str(activity).strip():
        out["activity"] = str(activity).strip().lower()
    elif combat_active is True:
        out["activity"] = "combat"
    elif "activity" not in out:
        out["activity"] = (screen or "other").strip().lower() or "other"
    if combat_active is not None:
        out["combat_active"] = bool(combat_active)
    elif out.get("activity") == "combat":
        out["combat_active"] = True
    return out
