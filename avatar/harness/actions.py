"""Compatibility re-exports. Prefer avatar.harness.tables."""

from __future__ import annotations

from .tables import (  # noqa: F401
    MAX_ACTIONS_PER_CYCLE,
    NDS_TABLE,
    STS_TABLE,
    SWIPE_TABLE,
    TABLES,
    TETRIS_TABLE,
    clip_plan,
    compile_action,
    names,
    prompt_listing,
    resolve,
)
from .primitives import NamedAction as BoundAction  # noqa: F401
