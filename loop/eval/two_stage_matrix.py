"""Collect ungraded LLM1 × LLM2 traces from frozen game screenshots."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

from loop.agent.perceive import SceneDescription, perceive_scene
from loop.agent.prompts import (
    allowed_sets_from_controls,
    reason_controls_for_turn,
    reason_hud_stats,
    reason_state_from_host,
    reason_turn_user_prompt,
)
from loop.config import project_root
from loop.model.gateway import VlmGateway
from loop.os.capture import encode_image
from loop.os.sts_stats import extract_sts_stats

IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp"})
REASON_SYSTEM = """You are choosing one next turn-based-game control from a trusted scene summary.
Reply with compact JSON only: {\"choice\": \"exact Allowed controls label\", \"target\": null}.
Choose exactly one non-empty choice. Copy labels exactly. A target is allowed only when it is one of Allowed targets.
Do not emit coordinates, marks, tools, or markdown."""


@dataclass(frozen=True)
class MatrixCase:
    """A frame to collect, with no embedded ground-truth or grade."""

    file: str


def default_cases(
    *,
    fixture_dir: Path | None = None,
    limit: int | None = None,
) -> list[MatrixCase]:
    """Discover screenshot files rather than binding the collector to labels."""
    base = fixture_dir or (project_root() / "loop" / "fixtures" / "sts2_screens")
    files = sorted(
        path.name for path in base.iterdir() if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
    )
    if limit is not None:
        files = files[: max(0, int(limit))]
    return [MatrixCase(file=name) for name in files]


def _record_base(*, case: MatrixCase, vision_model: str, reasoner_model: str) -> dict[str, Any]:
    return {"file": case.file, "vision_model": vision_model, "reasoner_model": reasoner_model}


def run_matrix(
    *,
    vision_models: list[str],
    reasoner_models: list[str],
    cases: list[MatrixCase] | None = None,
    fixture_dir: Path | None = None,
    vision_num_ctx: int = 8192,
    reason_num_ctx: int = 4096,
) -> dict[str, Any]:
    """Collect raw two-stage traces. This function intentionally does no grading."""
    base = fixture_dir or (project_root() / "loop" / "fixtures" / "sts2_screens")
    selected = cases if cases is not None else default_cases(fixture_dir=base)
    records: list[dict[str, Any]] = []
    vision_gateways = {
        model: VlmGateway(model=model, num_ctx=vision_num_ctx) for model in vision_models
    }
    reason_gateways = {
        model: VlmGateway(model=model, num_ctx=reason_num_ctx) for model in reasoner_models
    }
    try:
        for case in selected:
            candidate = Path(case.file)
            path = candidate if candidate.is_absolute() else base / candidate
            try:
                image = Image.open(path).convert("RGB")
                hud = extract_sts_stats(image)
                frame = encode_image(image, longest_side=512, jpeg_quality=85)
            except Exception as exc:  # retain unreadable fixtures as evidence
                for vision_model in vision_models:
                    for reasoner_model in reasoner_models:
                        records.append(
                            {
                                **_record_base(
                                    case=case, vision_model=vision_model, reasoner_model=reasoner_model
                                ),
                                "stage": "frame_load",
                                "error": str(exc),
                            }
                        )
                continue

            for vision_model, vision_gateway in vision_gateways.items():
                try:
                    desc, vision_call = perceive_scene(
                        vision_gateway,
                        frame,
                        hud=hud,
                        max_tokens=1024,
                        max_schema_retries=0,
                    )
                    # Keep schema_valid as an observation, but do not replace
                    # a model response with an empty fallback before LLM2 sees
                    # it. The later judge can decide whether malformed JSON or
                    # hallucinated fields should count against the model.
                    if not desc.schema_valid and isinstance(vision_call.parsed, dict):
                        recovered = SceneDescription.from_parsed(vision_call.parsed, hud=hud)
                        recovered.schema_valid = False
                        recovered.fallback = True
                        desc = recovered
                    controls = reason_controls_for_turn(
                        controls=desc.controls,
                        uncertain=desc.uncertain,
                        enemies=[str(x.get("label") or "") for x in desc.enemies],
                        tutorial_info=desc.tutorial,
                    )
                    allowed_controls, allowed_targets = allowed_sets_from_controls(controls)
                    reason_user = reason_turn_user_prompt(
                        state=reason_state_from_host(
                            screen=desc.screen, turn=desc.turn, tutorial_info=desc.tutorial
                        ),
                        hud_stats=reason_hud_stats(hud=hud),
                        controls=controls,
                        visible_text=desc.visible_text,
                    )
                except Exception as exc:
                    for reasoner_model in reasoner_models:
                        records.append(
                            {
                                **_record_base(
                                    case=case, vision_model=vision_model, reasoner_model=reasoner_model
                                ),
                                "stage": "vision",
                                "error": str(exc),
                            }
                        )
                    continue

                for reasoner_model, reason_gateway in reason_gateways.items():
                    record = {
                        **_record_base(
                            case=case, vision_model=vision_model, reasoner_model=reasoner_model
                        ),
                        "stage": "complete",
                        "frame_size": {"width": image.width, "height": image.height},
                        "hud": hud,
                        "vision": {
                            "model": vision_call.model,
                            "latency_ms": round(float(vision_call.latency_ms), 1),
                            "parsed": vision_call.parsed,
                            "text": vision_call.text,
                            "scene_summary": desc.as_text(),
                            "schema_valid": desc.schema_valid,
                            "fallback": desc.fallback,
                        },
                        "reason_input": {
                            "system": REASON_SYSTEM,
                            "user": reason_user,
                            "controls": controls,
                            "allowed_controls": sorted(allowed_controls),
                            "allowed_targets": sorted(allowed_targets),
                        },
                    }
                    try:
                        reason_call = reason_gateway.complete_json(
                            system=REASON_SYSTEM, user=reason_user, max_tokens=120
                        )
                        record["reason"] = {
                            "model": reason_call.model,
                            "latency_ms": round(float(reason_call.latency_ms), 1),
                            "parsed": reason_call.parsed,
                            "text": reason_call.text,
                        }
                        record["total_latency_ms"] = round(
                            float(vision_call.latency_ms) + float(reason_call.latency_ms), 1
                        )
                    except Exception as exc:
                        record["stage"] = "reasoner"
                        record["error"] = str(exc)
                    records.append(record)
    finally:
        for gateway in [*vision_gateways.values(), *reason_gateways.values()]:
            gateway.unload()

    return {
        "kind": "ungraded_two_stage_collection",
        "config": {
            "vision_models": vision_models,
            "reasoner_models": reasoner_models,
            "vision_num_ctx": vision_num_ctx,
            "reason_num_ctx": reason_num_ctx,
        },
        "cases": [case.file for case in selected],
        "records": records,
    }


def write_report(report: dict[str, Any], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
