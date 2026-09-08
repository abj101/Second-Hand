from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODEL = "qwen3.5:9b-q4_K_M"
# Default LLM1 and STS2 ACT cloud model. Other fixtures retain DEFAULT_MODEL.
DEFAULT_CLOUD_MODEL = "gemma4:31b-cloud"
DEFAULT_ACT_MODEL = DEFAULT_CLOUD_MODEL
DEFAULT_ACT_FALLBACK_MODEL = "qwen3.5:4b-instruct-q4_K_M"


def project_root() -> Path:
    return ROOT


def resolve_perceive_model(
    *,
    model: str,
    perceive_model: str | None = None,
    cloud: bool = False,
) -> str:
    """Resolve LLM1, defaulting to cloud Gemma; explicit override wins."""
    if perceive_model:
        return str(perceive_model).strip() or model
    if cloud:
        return DEFAULT_CLOUD_MODEL
    return DEFAULT_CLOUD_MODEL
