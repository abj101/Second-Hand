from __future__ import annotations

from typing import Any


def authoring_cost(
    *,
    author_ms: float | None = None,
    author_usd: float | None = None,
    vlm_calls_avoided: int = 0,
    gsm_ticks: int = 0,
) -> dict[str, Any]:
    """Authoring $ / ms vs later VLM calls skipped. Local Ollama authoring is $0."""
    return {
        "gsm_author_ms": author_ms,
        "gsm_author_usd": author_usd,
        "vlm_calls_avoided": vlm_calls_avoided,
        "gsm_ticks": gsm_ticks,
    }
