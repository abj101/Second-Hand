"""Async orchestration: versioned directives without blocking reaction."""

from __future__ import annotations

import asyncio
import queue
import threading
import time
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Mapping

from loop.adaptive.candidates import Directive
from loop.adaptive.orchestrate import registry_offers
from loop.adaptive.schema import SceneGraph

if TYPE_CHECKING:
    from loop.adaptive.controller import GenerationStore, ReactConfig


# Wake the orchestrator only on product reasons; never on every frame.
ORCHESTRATOR_REVISION_KINDS = frozenset(
    {
        "schema_change",
        "repeated_no_effect",
        "quality_loss",
        "directive_expired",
        "screen_signature",
    }
)

_SCREEN_SIGNATURE_KIND = "screen_signature"
STEER_POLICY_ID = "rule_v1"


def _visible_unkeyed_control_count(graph: SceneGraph) -> int:
    n = 0
    for node in graph.nodes:
        if node.attributes.get("registry_id"):
            continue
        attrs = node.attributes
        if attrs.get("clickable"):
            n += 1
            continue
        if node.node_id.startswith("track_") and (
            attrs.get("label") or attrs.get("numeric") is not None
        ):
            n += 1
    return n


def maybe_screen_steer_wake(
    graph: SceneGraph,
    *,
    previous_signature: frozenset[str] | None,
    previous_unkeyed: int | None,
    negative_evidence_recorded: bool = False,
    forbidden_ids: tuple[str, ...] = (),
) -> tuple[frozenset[str], int, dict[str, object] | None]:
    """Wake steer when registry ids, unkeyed controls, or negative evidence change."""

    signature = frozenset(offer.offer_id for offer in registry_offers(graph))
    unkeyed = _visible_unkeyed_control_count(graph)
    reasons: list[str] = []
    if signature != previous_signature:
        reasons.append("registry_signature_changed")
    if unkeyed != previous_unkeyed:
        reasons.append("unkeyed_control_changed")
    if negative_evidence_recorded:
        reasons.append("negative_evidence")
    wake = bool(reasons)
    if not wake:
        return signature, unkeyed, None
    legal = tuple(sorted(signature))
    filtered_forbidden = tuple(
        sorted(fid for fid in forbidden_ids if fid in signature)
    )
    return (
        signature,
        unkeyed,
        {
            "kind": _SCREEN_SIGNATURE_KIND,
            "policy_id": STEER_POLICY_ID,
            "steer_wake": True,
            "wake_reason": tuple(reasons),
            "registry_signature": tuple(legal),
            "forbidden_ids": filtered_forbidden,
            "surface_action_ids": legal,
        },
    )


@dataclass(frozen=True)
class OrchestratorConfig:
    default_intent: str = ""
    directive_ttl_s: float = 30.0
    game_brief: Mapping[str, object] | None = None


def _default_intent(config: OrchestratorConfig) -> str:
    if config.default_intent:
        return config.default_intent
    brief = config.game_brief
    objective = brief.get("objective") if isinstance(brief, Mapping) else None
    if isinstance(objective, str) and objective.strip():
        return objective.strip()
    return "observe and qualify promoted actions"


@dataclass(frozen=True)
class PolicyQualificationScore:
    policy_id: str
    verified_effect_score: float
    p95_latency_s: float
    peak_vram_gb: float | None = None
    safety_regressions: int = 0


def select_armed_policy(
    *,
    deterministic: PolicyQualificationScore,
    candidates: tuple[PolicyQualificationScore, ...],
    min_candidates_per_scenario: int = 2,
) -> str | None:
    """Pick armed Decider policy id, or None to stay deterministic-only in shadow."""

    if min_candidates_per_scenario < 2:
        raise ValueError("quality scenarios require at least two legal candidates")
    eligible = tuple(c for c in candidates if c.safety_regressions == 0)
    beating = tuple(
        c
        for c in eligible
        if c.verified_effect_score > deterministic.verified_effect_score
    )
    if not beating:
        return None

    def sort_key(c: PolicyQualificationScore) -> tuple[float, float, float]:
        vram = c.peak_vram_gb if c.peak_vram_gb is not None else float("inf")
        return (-c.verified_effect_score, c.p95_latency_s, vram)

    return min(beating, key=sort_key).policy_id


def publish_versioned_directive(
    store: GenerationStore,
    *,
    episode_generation: int,
    revision: int,
    config: OrchestratorConfig,
    intent: str | None = None,
    forbidden_ids: tuple[str, ...] = (),
    attention: tuple[str, ...] = (),
    published_at: float | None = None,
) -> Directive:
    now = time.monotonic() if published_at is None else published_at
    directive = Directive(
        revision=revision,
        episode_generation=episode_generation,
        intent=intent or _default_intent(config),
        ttl_s=config.directive_ttl_s,
        published_at=now,
        forbidden_ids=forbidden_ids,
        attention=attention,
    )
    store.publish(episode_generation, directive)
    return directive


def ensure_directive(
    store: GenerationStore,
    *,
    episode_generation: int,
    config: OrchestratorConfig | object,
) -> Directive | None:
    """Non-blocking read of the latest directive; refresh TTL if bootstrap expired."""

    if isinstance(config, OrchestratorConfig):
        orch_cfg = config
    else:
        orch_cfg = OrchestratorConfig(
            default_intent=getattr(config, "default_intent", "progress while surviving"),
            directive_ttl_s=getattr(config, "directive_ttl_s", 30.0),
            game_brief=getattr(config, "game_brief", None),
        )

    directive = store.latest()
    if directive is None:
        return publish_versioned_directive(
            store,
            episode_generation=episode_generation,
            revision=1,
            config=orch_cfg,
        )
    if directive.episode_generation != episode_generation:
        return None
    now = time.monotonic()
    if now - directive.published_at > directive.ttl_s:
        return publish_versioned_directive(
            store,
            episode_generation=episode_generation,
            revision=directive.revision + 1,
            config=orch_cfg,
            intent=directive.intent,
            forbidden_ids=directive.forbidden_ids,
            attention=directive.attention,
        )
    return directive


def _intent_from_event(ev: dict, fallback: str) -> str:
    intent = ev.get("intent")
    return intent if isinstance(intent, str) and intent else fallback


def _forbidden_from_event(ev: dict, prior: tuple[str, ...]) -> tuple[str, ...]:
    raw = ev.get("forbidden_ids")
    if isinstance(raw, (list, tuple)) and all(isinstance(x, str) for x in raw):
        legal = ev.get("surface_action_ids")
        if isinstance(legal, (list, tuple, set, frozenset)) and all(
            isinstance(x, str) for x in legal
        ):
            legal_set = frozenset(legal)
            return tuple(sorted(fid for fid in raw if fid in legal_set))
        return tuple(raw)
    return prior


def _attention_from_event(ev: dict, prior: tuple[str, ...]) -> tuple[str, ...]:
    raw = ev.get("attention")
    if isinstance(raw, (list, tuple)) and all(isinstance(x, str) for x in raw):
        return tuple(raw)
    return prior


def _screen_event_signature(ev: dict) -> frozenset[str] | None:
    raw = ev.get("registry_signature", ev.get("surface_action_ids"))
    if not isinstance(raw, (list, tuple, set, frozenset)):
        return None
    if not all(isinstance(value, str) for value in raw):
        return None
    return frozenset(raw)


def _screen_event_forces_revision(ev: dict) -> bool:
    raw = ev.get("wake_reason")
    return isinstance(raw, (list, tuple, set, frozenset)) and "negative_evidence" in raw


async def orchestrator_worker(
    events: asyncio.Queue,
    directive_store: GenerationStore,
    *,
    episode_generation: int,
    config: ReactConfig | OrchestratorConfig,
    stop: asyncio.Event,
) -> None:
    if isinstance(config, OrchestratorConfig):
        orch_cfg = config
    else:
        orch_cfg = OrchestratorConfig(
            default_intent=config.default_intent,
            directive_ttl_s=config.directive_ttl_s,
            game_brief=getattr(config, "game_brief", None),
        )

    revision = 1
    intent = _default_intent(orch_cfg)
    forbidden: tuple[str, ...] = ()
    attention: tuple[str, ...] = ()
    last_screen_signature: frozenset[str] | None = None

    def _publish(*, bump: bool = False) -> None:
        nonlocal revision, intent, forbidden, attention
        if bump:
            revision += 1
        publish_versioned_directive(
            directive_store,
            episode_generation=episode_generation,
            revision=revision,
            config=orch_cfg,
            intent=intent,
            forbidden_ids=forbidden,
            attention=attention,
        )

    _publish()
    while not stop.is_set():
        try:
            ev = await asyncio.wait_for(events.get(), timeout=0.25)
        except asyncio.TimeoutError:
            _publish()
            continue
        if not isinstance(ev, dict):
            continue
        kind = str(ev.get("kind") or "")
        if kind in ORCHESTRATOR_REVISION_KINDS:
            intent = _intent_from_event(ev, intent)
            forbidden = _forbidden_from_event(ev, forbidden)
            attention = _attention_from_event(ev, attention)
            if kind == _SCREEN_SIGNATURE_KIND:
                signature = _screen_event_signature(ev)
                duplicate = (
                    signature is not None
                    and last_screen_signature is not None
                    and signature == last_screen_signature
                    and not _screen_event_forces_revision(ev)
                )
                if signature is not None:
                    last_screen_signature = signature
                _publish(bump=not duplicate)
            else:
                _publish(bump=True)


class SyncOrchestratorRunner:
    """Daemon thread for sync capture loops; reaction never joins this thread."""

    def __init__(
        self,
        directive_store: GenerationStore,
        *,
        episode_generation: int,
        config: OrchestratorConfig,
        events: queue.Queue | None = None,
    ) -> None:
        self._store = directive_store
        self._episode_generation = episode_generation
        self._config = config
        self._events = events if events is not None else queue.Queue()
        self._stop = threading.Event()
        self._revision = 1
        self._intent = _default_intent(config)
        self._forbidden: tuple[str, ...] = ()
        self._attention: tuple[str, ...] = ()
        self._last_screen_signature: frozenset[str] | None = None
        self._steer_wake_telemetry: list[dict[str, object]] = []
        self._thread = threading.Thread(target=self._run, name="orchestrator", daemon=True)
        self._thread.start()

    @property
    def events(self) -> queue.Queue:
        return self._events

    @property
    def steer_wake_telemetry(self) -> tuple[dict[str, object], ...]:
        """Published Steer wake facts, including the deterministic policy id."""

        return tuple(dict(item) for item in self._steer_wake_telemetry)

    def notify(self, kind: str, **payload: object) -> None:
        ev = {"kind": kind, **payload}
        if kind == _SCREEN_SIGNATURE_KIND:
            self._steer_wake_telemetry.append(
                {
                    "policy_id": str(payload.get("policy_id") or STEER_POLICY_ID),
                    "registry_signature": tuple(
                        str(item)
                        for item in (payload.get("registry_signature") or payload.get("surface_action_ids") or ())
                    ),
                    "wake_reason": tuple(payload.get("wake_reason") or ()),
                }
            )
        try:
            self._events.put_nowait(ev)
        except queue.Full:
            pass

    def close(self) -> None:
        self.notify("shutdown")
        self._stop.set()
        self._thread.join(timeout=2.0)

    def _publish(self, *, bump: bool = False) -> None:
        if bump:
            self._revision += 1
        publish_versioned_directive(
            self._store,
            episode_generation=self._episode_generation,
            revision=self._revision,
            config=self._config,
            intent=self._intent,
            forbidden_ids=self._forbidden,
            attention=self._attention,
        )

    def _run(self) -> None:
        self._publish()
        while not self._stop.is_set():
            try:
                ev = self._events.get(timeout=0.25)
            except queue.Empty:
                self._publish()
                continue
            if not isinstance(ev, dict):
                continue
            kind = str(ev.get("kind") or "")
            if kind in ORCHESTRATOR_REVISION_KINDS:
                self._intent = _intent_from_event(ev, self._intent)
                self._forbidden = _forbidden_from_event(ev, self._forbidden)
                self._attention = _attention_from_event(ev, self._attention)
                if kind == _SCREEN_SIGNATURE_KIND:
                    signature = _screen_event_signature(ev)
                    duplicate = (
                        signature is not None
                        and self._last_screen_signature is not None
                        and signature == self._last_screen_signature
                        and not _screen_event_forces_revision(ev)
                    )
                    if signature is not None:
                        self._last_screen_signature = signature
                    self._publish(bump=not duplicate)
                else:
                    self._publish(bump=True)
