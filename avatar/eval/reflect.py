from __future__ import annotations

from typing import Any

from avatar.models import ModelCall, VlmGateway
from avatar.prompts.library import reflect_system, reflect_user
from avatar.schema import ReflectResponse


_WRAP_KEYS = ("reflection", "analysis", "result")
_VERDICT_KEYS = ("verdict", "status", "result", "outcome")
_TEXT_KEYS = ("visual", "task_design", "action_plan", "execution", "next_step")
_TRUE_STRINGS = {"yes", "true", "1"}


def _coerce_verdict(value: Any) -> str:
    if isinstance(value, dict):
        nested = next((value[key] for key in _VERDICT_KEYS if key in value), None)
        value = nested
    if isinstance(value, str):
        lowered = value.lower().strip()
        if lowered in {"success", "failure", "partial"}:
            return lowered
        return "partial"
    if isinstance(value, bool):
        return "success" if value else "failure"
    return "partial"


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in _TRUE_STRINGS
    if isinstance(value, (int, float)):
        return value == 1
    return False


def _as_str(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return str(value)


def _normalize_qa_note(item: Any) -> dict[str, Any] | None:
    if isinstance(item, str) and item.strip():
        return {"title": item.strip(), "would_file": False, "evidence": ""}
    if not isinstance(item, dict):
        return None
    title = item.get("title") or item.get("bug") or item.get("note") or ""
    return {
        "title": _as_str(title),
        "would_file": _coerce_bool(item.get("would_file", False)),
        "evidence": _as_str(item.get("evidence", "")),
    }


def _normalize_reflect_payload(parsed: dict[str, Any]) -> dict[str, Any]:
    payload = dict(parsed)
    for wrap_key in _WRAP_KEYS:
        inner = payload.get(wrap_key)
        if isinstance(inner, dict) and ("verdict" in inner or "qa_notes" in inner):
            merged = {key: value for key, value in payload.items() if key != wrap_key}
            merged.update(inner)
            payload = merged
            break
    if "verdict" in payload:
        payload["verdict"] = _coerce_verdict(payload["verdict"])
    raw_notes = payload.get("qa_notes")
    if isinstance(raw_notes, str):
        raw_notes = [raw_notes]
    elif isinstance(raw_notes, dict):
        raw_notes = [raw_notes]
    if isinstance(raw_notes, list):
        notes = [note for note in (_normalize_qa_note(item) for item in raw_notes) if note]
        payload["qa_notes"] = notes
    for key in _TEXT_KEYS:
        if key in payload and not isinstance(payload[key], str):
            payload[key] = _as_str(payload[key])
    return payload


def parse_reflect(payload: dict[str, Any] | None) -> ReflectResponse:
    if not payload or "error" in payload:
        return ReflectResponse(verdict="failure", visual="unparseable")
    try:
        return ReflectResponse.model_validate(_normalize_reflect_payload(payload))
    except Exception as exc:  # noqa: BLE001
        return ReflectResponse(verdict="partial", visual=f"unparseable: {exc}")


def reflect_session(
    gateway: VlmGateway,
    *,
    fixture: str,
    trace_summary: str,
    prompt_mode: str = "pixels",
    manual: str = "",
) -> tuple[ReflectResponse, ModelCall]:
    call = gateway.complete_json(
        system=reflect_system(prompt_mode=prompt_mode, manual=manual),
        user=reflect_user(fixture=fixture, trace_summary=trace_summary),
    )
    return parse_reflect(call.parsed), call
