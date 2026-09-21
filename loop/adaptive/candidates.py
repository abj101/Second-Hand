"""Candidate actions, directives, and action masks (benchmark path)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CandidateAction:
    action_id: str
    command: str
    description: str
    available: bool = True
    input_budget_s: float = 0.2
    suppress_no_change: bool = False
    retry_policy: str = "allow"


@dataclass(frozen=True)
class ActionCatalog:
    revision: str
    candidates: tuple[CandidateAction, ...]


@dataclass(frozen=True)
class ActionMask:
    allowed_ids: tuple[str, ...]
    blocked: dict[str, str]


@dataclass(frozen=True)
class Directive:
    revision: int
    episode_generation: int
    intent: str
    ttl_s: float
    published_at: float
    forbidden_ids: tuple[str, ...] = ()
    required_mode: str | None = None


def compile_mask(catalog: ActionCatalog, directive: Directive) -> ActionMask:
    blocked: dict[str, str] = {}
    allowed: list[str] = []
    forbidden = set(directive.forbidden_ids)
    for cand in catalog.candidates:
        if not cand.available:
            blocked[cand.action_id] = "unavailable"
            continue
        if cand.action_id in forbidden:
            blocked[cand.action_id] = "forbidden"
            continue
        allowed.append(cand.action_id)
    return ActionMask(allowed_ids=tuple(allowed), blocked=blocked)


def select_if_unambiguous(mask: ActionMask) -> str | None:
    if len(mask.allowed_ids) == 1:
        return mask.allowed_ids[0]
    return None
