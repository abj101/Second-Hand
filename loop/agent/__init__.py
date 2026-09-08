from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from loop.agent.loop import CollectAgent, CollectConfig

__all__ = ["CollectAgent", "CollectConfig"]


def __getattr__(name: str):
    if name == "CollectAgent":
        from loop.agent.loop import CollectAgent

        return CollectAgent
    if name == "CollectConfig":
        from loop.agent.loop import CollectConfig

        return CollectConfig
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
