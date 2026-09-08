from __future__ import annotations

from typing import Any, Callable

from avatar.memory.episodic import EpisodicMemory

CompleteJson = Callable[..., Any]

CONDENSE_SYSTEM = """You compress gameplay memory into short lessons.
Reply with a single JSON object: {"lessons": "5-8 bullet lines"}.
No markdown fences. Focus on what worked, what was a no-op, and what not to repeat.
"""


def maybe_condense(
    episodic: EpisodicMemory,
    complete_json: CompleteJson | None,
    *,
    min_items: int = 5,
    stale: bool = True,
) -> str | None:
    """Periodic last-10 → lessons. `complete_json` may return a dict or an object with `.parsed`."""
    if complete_json is None:
        return None
    if len(episodic.last_n()) < min_items:
        return None
    if episodic.lessons.strip() and not stale:
        return None
    raw = complete_json(
        system=CONDENSE_SYSTEM,
        user="Last-10 summary:\n" + episodic.last_ten_summary() + '\nReply {"lessons": "..."}',
    )
    parsed = raw if isinstance(raw, dict) else getattr(raw, "parsed", None)
    if not isinstance(parsed, dict):
        return None
    lessons = parsed.get("lessons")
    if not isinstance(lessons, str) or not lessons.strip():
        return None
    episodic.lessons = lessons.strip()
    return episodic.lessons
