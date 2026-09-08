from __future__ import annotations

import json
from typing import Any, Callable

from PIL import Image

from loop.agent.perceive import SceneDescription, perceive_scene
from loop.agent.prompts import scene_perceive_user, two_stage_system_for
from loop.model.gateway import ModelCall, VlmGateway
from loop.os.capture import EncodedFrame
from loop.os.geometry import encode_observe
from loop.os.sts_stats import extract_sts_stats, stats_to_score_metrics


def format_perceive_tool_result(
    desc: SceneDescription,
    *,
    clickable_labels: list[str] | None = None,
    host_stats: dict[str, Any] | None = None,
    focus: str = "",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "scene": desc.as_text() or desc.scene,
        "screen": desc.screen,
        "controls": desc.interactive_labels(),
        "allowed_controls": desc.interactive_labels(),
        "dialog_options": desc.dialog_options(),
        "tutorial": desc.tutorial_text or None,
        "turn": desc.turn or None,
        "visible_text": list(desc.informational_text()[:12]),
    }
    if desc.combat:
        payload["combat"] = desc.combat
    if host_stats:
        payload["host_screen"] = host_stats.get("screen")
        payload["host_turn"] = host_stats.get("turn")
    if focus.strip():
        payload["focus"] = focus.strip()
    return payload


def register_perceive_tools(
    registry: Any,
    *,
    gateway: VlmGateway,
    image_getter: Callable[[], Image.Image | None],
    clickable_labels_getter: Callable[[], list[str]],
    perceive_max_tokens: int = 1536,
    on_perceive: Callable[[SceneDescription, ModelCall, dict[str, Any]], None] | None = None,
) -> None:
    perceive_system, _ = two_stage_system_for(arm="no_skill")

    def _recheck(focus: str = "", **_: Any) -> str:
        image = image_getter() if callable(image_getter) else None
        if image is None:
            return json.dumps({"error": "no frame"})
        host_stats = extract_sts_stats(image)
        hud = stats_to_score_metrics(host_stats)
        clickable = clickable_labels_getter() if callable(clickable_labels_getter) else []
        frame: EncodedFrame = encode_observe(image, cap=1280, jpeg_quality=90)
        user = scene_perceive_user(
            hud=hud,
            host_stats=host_stats,
            detector_labels=clickable,
            focus=focus,
        )
        desc, call = perceive_scene(
            gateway,
            frame,
            hud=hud,
            host_stats=host_stats,
            detector_labels=clickable,
            max_tokens=perceive_max_tokens,
            perceive_system=perceive_system,
            perceive_user=user,
        )
        if on_perceive is not None:
            on_perceive(desc, call, host_stats)
        return json.dumps(format_perceive_tool_result(
            desc,
            clickable_labels=clickable,
            host_stats=host_stats,
            focus=focus,
        ))

    registry.register(
        "screen_perceive",
        description=(
            "Re-run the vision model on the current frame. Returns fresh scene transcript and "
            "allowed_controls (LLM1). Use when choice is missing from Allowed controls, "
            "the scene transcript seems wrong, or the last action failed."
        ),
        parameters={"focus": "optional string — what to verify, e.g. 'Got it button'"},
        handler=_recheck,
    )
