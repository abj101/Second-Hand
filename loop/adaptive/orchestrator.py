"""Async orchestration: versioned directives without blocking reaction."""

from __future__ import annotations

import asyncio
import queue
import threading
import time
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from loop.adaptive.candidates import Directive

if TYPE_CHECKING:
    from loop.adaptive.controller import GenerationStore, ReactConfig


# Events that bump directive revision (reaction only enqueues; never awaits orchestrator).
ORCHESTRATOR_REVISION_KINDS = frozenset(
    {
        "halt",
        "timeout",
        "terminal",
        "unfocused",
        "low_confidence",
        "stale_capture",
        "stale_snapshot",
        "slow_inference",
        "malformed_inference",
        "policy_worker_failed",
        "directive_expired",
        "mask_change",
        "catalog_change",
        "schema_change",
        "target_replaced",
        "focus_loss",
        "input_exception",
        "release_failure",
        "reset",
        "shutdown",
        "recovery_locked",
    }
)


@dataclass(frozen=True)
class OrchestratorConfig:
    default_intent: str = "observe and qualify promoted actions"
    directive_ttl_s: float = 30.0


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
    published_at: float | None = None,
) -> Directive:
    now = time.monotonic() if published_at is None else published_at
    directive = Directive(
        revision=revision,
        episode_generation=episode_generation,
        intent=intent or config.default_intent,
        ttl_s=config.directive_ttl_s,
        published_at=now,
        forbidden_ids=forbidden_ids,
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
        )
    return directive


def _intent_from_event(ev: dict, fallback: str) -> str:
    intent = ev.get("intent")
    return intent if isinstance(intent, str) and intent else fallback


def _forbidden_from_event(ev: dict, prior: tuple[str, ...]) -> tuple[str, ...]:
    raw = ev.get("forbidden_ids")
    if isinstance(raw, (list, tuple)) and all(isinstance(x, str) for x in raw):
        return tuple(raw)
    return prior


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
        )

    revision = 1
    intent = orch_cfg.default_intent
    forbidden: tuple[str, ...] = ()

    def _publish(*, bump: bool = False) -> None:
        nonlocal revision, intent, forbidden
        if bump:
            revision += 1
        publish_versioned_directive(
            directive_store,
            episode_generation=episode_generation,
            revision=revision,
            config=orch_cfg,
            intent=intent,
            forbidden_ids=forbidden,
        )

    _publish()
    while not stop.is_set():
        try:
            ev = await asyncio.wait_for(events.get(), timeout=0.25)
        except asyncio.TimeoutError:
            _publish()
            continue
        if not isinstance(ev, dict):
            _publish()
            continue
        kind = str(ev.get("kind") or "")
        if kind in ORCHESTRATOR_REVISION_KINDS:
            intent = _intent_from_event(ev, intent)
            forbidden = _forbidden_from_event(ev, forbidden)
            _publish(bump=True)
        else:
            _publish()


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
        self._intent = config.default_intent
        self._forbidden: tuple[str, ...] = ()
        self._thread = threading.Thread(target=self._run, name="orchestrator", daemon=True)
        self._thread.start()

    @property
    def events(self) -> queue.Queue:
        return self._events

    def notify(self, kind: str, **payload: object) -> None:
        ev = {"kind": kind, **payload}
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
                self._publish()
                continue
            kind = str(ev.get("kind") or "")
            if kind in ORCHESTRATOR_REVISION_KINDS:
                self._intent = _intent_from_event(ev, self._intent)
                self._forbidden = _forbidden_from_event(ev, self._forbidden)
                self._publish(bump=True)
            else:
                self._publish()
