from __future__ import annotations

import argparse
from typing import Any

from avatar.models import ModelCall, VlmGateway, configured_models
from avatar.schema import ActResponse, ActionStep, SeeResponse, UnderstandResponse


def add_model_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--skip-models", action="store_true")
    parser.add_argument(
        "--model",
        action="append",
        default=None,
        metavar="ID",
        help="Only run this configured model id (repeatable). Also honors AVATAR_MODEL.",
    )


def safe_model_tag(model_id: str) -> str:
    """Filesystem-safe slug for frame filenames (Windows forbids : etc.)."""
    for ch in ("/", ":", "\\", "|", "?", "*", "<", ">", '"'):
        model_id = model_id.replace(ch, "_")
    return model_id


def selected_models(cfg: dict[str, Any], args: argparse.Namespace) -> list[tuple[str, str]]:
    if getattr(args, "skip_models", False):
        return []
    return configured_models(cfg, only=getattr(args, "model", None))


def safe_call(gateway: VlmGateway, **kwargs) -> ModelCall | None:
    try:
        return gateway.complete_json(**kwargs)
    except Exception as exc:  # noqa: BLE001 — smoke tests must survive missing keys/models
        return ModelCall(
            text="",
            parsed={"error": str(exc)},
            latency_ms=0.0,
            cost_usd=None,
            model=gateway.model,
            provider=gateway.provider,
        )


def parse_see(parsed: dict | None) -> SeeResponse:
    if not parsed or "error" in parsed:
        return SeeResponse(screen_kind="gameplay", status="unparseable", reasoning=str(parsed))
    return SeeResponse.model_validate(parsed)


def parse_understand(parsed: dict | None) -> UnderstandResponse:
    if not parsed or "error" in parsed:
        return UnderstandResponse(
            screen_kind="gameplay",
            goal="unparseable",
            next_move="",
            reasoning=str(parsed),
        )
    return UnderstandResponse.model_validate(parsed)


def _normalize_act_payload(parsed: dict[str, Any]) -> dict[str, Any]:
    payload = dict(parsed)
    raw_actions = payload.get("actions")
    if not isinstance(raw_actions, list):
        return payload
    steps: list[dict[str, Any]] = []
    for item in raw_actions:
        if isinstance(item, str) and item.strip():
            steps.append({"name": item.strip()})
        elif isinstance(item, dict):
            name = item.get("name") or item.get("action")
            if isinstance(name, str) and name.strip():
                steps.append(
                    {
                        "name": name.strip(),
                        "details": str(item.get("details") or ""),
                        "duration_ms": item.get("duration_ms"),
                    }
                )
    payload["actions"] = steps
    return payload


def parse_act(parsed: dict | None) -> ActResponse:
    if not parsed or "error" in parsed:
        return ActResponse(reasoning=str(parsed))
    payload = _normalize_act_payload(parsed)
    looks_see = "screen_kind" in payload or "board" in payload
    has_act = bool(payload.get("actions")) or bool(payload.get("invoke_skill")) or bool(
        payload.get("invoke_gsm")
    )
    if looks_see and not has_act:
        return ActResponse(reasoning="unparseable: see-shaped payload")
    try:
        return ActResponse.model_validate(payload)
    except Exception as exc:  # noqa: BLE001 — VLM JSON is often slightly off-schema
        return ActResponse(reasoning=f"unparseable: {exc}")
