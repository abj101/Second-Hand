"""Shared UI progress phrases. No heavy imports — journal and overlay both use this."""

from __future__ import annotations

import re

# Word-boundary phrases for ranking / overlay. Do not put bare "play" here:
# it matches the STS2 unlock sentence "Play a single run with ???".
PROGRESS_PHRASES = (
    "continue",
    "got it",
    "resume",
    "end turn",
    "confirm",
    "embark",
    "proceed",
    "singleplayer",
)

_SKIP_BIND = frozenset(
    {
        "character",
        "icon",
        "locked",
        "relic",
        "unknown relic",
        "button",
        "buttons",
        "menu",
    }
)


def progress_index(text: str) -> int | None:
    compact = " ".join((text or "").lower().split())
    if not compact:
        return None
    for i, word in enumerate(PROGRESS_PHRASES):
        if re.search(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])", compact):
            return i
    tokens = compact.split()
    if tokens == ["play"]:
        return len(PROGRESS_PHRASES)
    return None


def bind_verb_ok(label: str) -> bool:
    compact = " ".join((label or "").lower().split())
    if not compact:
        return False
    if compact.isdigit():
        return False
    if compact in _SKIP_BIND:
        return False
    if len(compact.split()) > 3:
        return False
    return True
