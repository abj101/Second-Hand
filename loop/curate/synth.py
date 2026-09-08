from __future__ import annotations

import hashlib
from typing import Any

from loop.agent.prompts import SYNTH_SYSTEM, synth_user
from loop.log.schema import StepLog
from loop.memory.skills import Skill
from loop.model.gateway import VlmGateway
from loop.os.capture import EncodedFrame
from loop.os.chords import skill_text_leaks


def deterministic_skill(signature: str) -> Skill:
    digest = hashlib.sha1(signature.encode("utf-8")).hexdigest()[:10]
    stall_without_look = "look=0.0" in signature and not signature.startswith("stall=0.0")
    if stall_without_look:
        chord = {"stick_l": None, "stick_r": "e", "held": [], "tapped": [], "duration_ms": 400}
    elif "prompt=1" in signature:
        chord = {"stick_l": None, "stick_r": None, "held": [], "tapped": ["a"], "duration_ms": 400}
    else:
        chord = {"stick_l": "s", "stick_r": None, "held": [], "tapped": [], "duration_ms": 400}
    skill = Skill(
        id=f"sig_{digest}",
        recognize=f"unlabeled cluster {digest}",
        avoid_or_do={"chord": chord, "pitfall": "repeat the last failed heading"},
        end="view changed, prompt cleared, or bars recovered",
        source_signature=signature,
    )
    return _strip_leaks(skill)


def _strip_leaks(skill: Skill) -> Skill:
    leaks = skill_text_leaks(skill.model_dump())
    if not leaks:
        return skill
    chord = (skill.avoid_or_do or {}).get("chord") or {
        "stick_l": "n",
        "held": ["b"],
        "tapped": [],
        "duration_ms": 400,
    }
    skill.recognize = "repeat unlabeled device cluster"
    skill.avoid_or_do = {"chord": chord, "pitfall": "repeat last device names"}
    skill.end = "bars recovered or scene changed"
    return skill


def skill_from_parsed(parsed: dict[str, Any], signature: str) -> Skill | None:
    if not parsed:
        return None
    sid = str(parsed.get("id") or "").strip() or None
    if sid is None:
        sid = f"sig_{hashlib.sha1(signature.encode()).hexdigest()[:10]}"
    skill = Skill(
        id=sid,
        recognize=str(parsed.get("recognize") or ""),
        avoid_or_do=parsed.get("avoid_or_do") if isinstance(parsed.get("avoid_or_do"), dict) else {},
        end=str(parsed.get("end") or ""),
        source_signature=signature,
    )
    if not skill.recognize or not skill.avoid_or_do:
        return None
    return _strip_leaks(skill)


def vlm_synth(
    gateway: VlmGateway,
    *,
    signature: str,
    metrics: dict[str, Any],
    flags: list[str],
    frame: EncodedFrame | None,
) -> Skill:
    user = synth_user(signature=signature, metrics=metrics, flags=flags)
    frames = [frame] if frame is not None else []
    call = gateway.complete_json(system=SYNTH_SYSTEM, user=user, frames=frames)
    skill = skill_from_parsed(call.parsed or {}, signature)
    if skill is not None and not skill_text_leaks(skill.model_dump()):
        return skill
    retry = gateway.complete_json(
        system=SYNTH_SYSTEM,
        user=user + "\nRemove dodge/roll/flask/attack/sprint. Unlabeled chords only.",
        frames=frames,
    )
    skill = skill_from_parsed(retry.parsed or {}, signature)
    if skill is not None and not skill_text_leaks(skill.model_dump()):
        return skill
    return deterministic_skill(signature)


def last_after_metrics(steps: list[StepLog]) -> dict[str, Any]:
    if not steps:
        return {}
    return dict(steps[-1].metrics)
