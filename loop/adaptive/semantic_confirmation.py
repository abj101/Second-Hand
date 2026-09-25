"""Optional cloud vision confirmation for registry candidate semantics.

Off by default: constructed only when a caller enables semantic mode.
No input side effects; cannot promote schema or actions.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Literal, Mapping, Protocol, Sequence

from loop.model.gateway import ModelCall
from loop.os.capture import EncodedFrame

SemanticClass = Literal["control", "decoration", "unknown"]
CandidateStatus = Literal[
    "confirmed", "unresolved", "withheld", "unavailable", "exhausted"
]

_VALID_CLASSES: frozenset[str] = frozenset({"control", "decoration", "unknown"})
_MAX_ATTEMPTS_PER_GENERATION = 2
_FORBIDDEN_KEY_RE = re.compile(
    r"(^action$|^click$|^x$|^y$|bbox|coordinate|replacement_id|actuate|permission)",
    re.I,
)
_SCHEMA_NAME = "semantic_candidate_classification"

_RESPONSE_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "target_identity": {"type": "string"},
        "screen_generation": {"type": "string"},
        "classifications": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "candidate_id": {"type": "string"},
                    "class": {
                        "type": "string",
                        "enum": ["control", "decoration", "unknown"],
                    },
                    "rationale": {"type": "string"},
                },
                "required": ["candidate_id", "class", "rationale"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["target_identity", "screen_generation", "classifications"],
    "additionalProperties": False,
}

_SYSTEM_PROMPT = (
    "You classify on-screen candidates for a neutral UI perception system. "
    "Return only the JSON schema requested. "
    "Each candidate_id must receive exactly one class: control, decoration, or unknown. "
    "Do not return actions, coordinates, clicks, replacement ids, or permission to actuate."
)


class JsonGateway(Protocol):
    def complete_json(
        self,
        *,
        system: str,
        user: str,
        json_schema: dict[str, Any],
        schema_name: str = ...,
        frames: list[EncodedFrame] | None = ...,
        timeout_s: float | None = ...,
    ) -> ModelCall: ...


@dataclass(frozen=True)
class CandidateEvidence:
    candidate_id: str
    appearance_token: str
    box: tuple[float, float, float, float]


@dataclass(frozen=True)
class CandidateConfirmationStatus:
    candidate_id: str
    status: CandidateStatus
    semantic_class: SemanticClass | None = None
    rationale: str | None = None


@dataclass(frozen=True)
class SemanticConfirmationTelemetry:
    model: str
    observation_id: str
    evidence_candidate_ids: tuple[str, ...]
    latency_ms: float | None
    prompt_tokens: int | None
    output_tokens: int | None
    outcome: str
    target_identity_key: str
    screen_generation: str


@dataclass(frozen=True)
class SemanticObservationResult:
    """Per-observe result for integration (task E)."""

    target_identity_key: str
    screen_generation: str
    observation_id: str
    candidates: tuple[CandidateConfirmationStatus, ...]
    telemetry: tuple[SemanticConfirmationTelemetry, ...]


def _canonical_digest(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def evidence_digest(
    *, candidates: Sequence[CandidateEvidence], frame: EncodedFrame
) -> str:
    """Content identity of the evidence a semantic job is allowed to reason about."""

    return _canonical_digest(
        {
            "frame": hashlib.sha256(frame.data).hexdigest(),
            "candidates": [
                {
                    "id": cand.candidate_id,
                    "appearance": cand.appearance_token,
                    "box": [round(float(value), 6) for value in cand.box],
                }
                for cand in candidates
            ],
        }
    )


def brief_digest(game_brief: Mapping[str, Any] | None) -> str:
    if game_brief is None:
        return ""
    return _canonical_digest(dict(game_brief))


@dataclass(frozen=True)
class SemanticJob:
    """Immutable bounded semantic request (P0 frozen job contract).

    Carries target identity, screen generation, origin observation id, evidence
    digest, schema revision and brief digest. ``frame`` is an independent copy of
    immutable encoded bytes, so background work cannot observe live mutations.
    """

    job_id: int
    target_identity_key: str
    screen_generation: str
    observation_id: str
    evidence_digest: str
    schema_revision: str
    brief_digest: str
    candidate_ids: tuple[str, ...]
    candidates: tuple[CandidateEvidence, ...]
    frame: EncodedFrame
    submitted_at: float
    deadline_at: float

    @property
    def coalesce_key(self) -> tuple[object, ...]:
        """Two jobs with the same key are the same request; submit must coalesce."""

        return (
            self.target_identity_key,
            self.screen_generation,
            self.schema_revision,
            self.brief_digest,
            self.evidence_digest,
            self.observation_id,
            self.candidate_ids,
        )


class SemanticJobStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class SemanticJobResult:
    """Result of a bounded semantic job, awaiting session-owner validation.

    ``origin_observation_id`` is the observation the inference saw;
    ``validation_observation_id`` is set only by :meth:`SemanticWorker.validate_result`
    on the session-owner path against a distinct fresh trusted observation.
    """

    status: SemanticJobStatus
    job_id: int
    target_identity_key: str
    screen_generation: str
    origin_observation_id: str
    evidence_digest: str
    schema_revision: str
    brief_digest: str
    candidates: tuple[CandidateConfirmationStatus, ...] = ()
    telemetry: tuple[SemanticConfirmationTelemetry, ...] = ()
    validation_observation_id: str | None = None
    reason: str = ""


@dataclass
class _ContextState:
    observations: dict[str, dict[str, str]] = field(default_factory=dict)
    conflicts: set[str] = field(default_factory=set)
    confirmed: dict[str, SemanticClass] = field(default_factory=dict)
    confirmed_rationale: dict[str, str] = field(default_factory=dict)
    attempts: dict[str, int] = field(default_factory=dict)
    class_cache: dict[tuple[str, str], tuple[SemanticClass, str]] = field(
        default_factory=dict
    )


def _identity_key(target_identity: str | Mapping[str, Any]) -> str:
    if isinstance(target_identity, str):
        return target_identity
    return json.dumps(dict(target_identity), sort_keys=True, separators=(",", ":"))


def _generation_key(screen_generation: int | str) -> str:
    return str(screen_generation)


def _forbidden_keys_present(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if _FORBIDDEN_KEY_RE.search(str(key)):
                return True
            if _forbidden_keys_present(item):
                return True
    elif isinstance(value, list):
        for item in value:
            if _forbidden_keys_present(item):
                return True
    return False


def _validate_parsed_response(
    parsed: dict[str, Any] | None,
    *,
    expected_target_key: str,
    expected_generation: str,
    expected_ids: frozenset[str],
) -> dict[str, tuple[SemanticClass, str]] | None:
    if not isinstance(parsed, dict):
        return None
    if _forbidden_keys_present(parsed):
        return None
    if parsed.get("target_identity") != expected_target_key:
        return None
    if _generation_key(parsed.get("screen_generation", "")) != expected_generation:
        return None
    rows = parsed.get("classifications")
    if not isinstance(rows, list):
        return None
    out: dict[str, tuple[SemanticClass, str]] = {}
    for row in rows:
        if not isinstance(row, dict):
            return None
        if _forbidden_keys_present(row):
            return None
        cid = row.get("candidate_id")
        cls = row.get("class")
        rationale = row.get("rationale")
        if not isinstance(cid, str) or not isinstance(rationale, str):
            return None
        if cls not in _VALID_CLASSES:
            return None
        if cid in out:
            return None
        out[cid] = (cls, rationale)  # type: ignore[assignment]
    if set(out) != expected_ids:
        return None
    return out


def _build_user_prompt(
    *,
    target_identity_key: str,
    screen_generation: str,
    observation_id: str,
    candidates: Sequence[CandidateEvidence],
    game_brief: Mapping[str, Any] | None = None,
) -> str:
    lines = [
        f"target_identity: {target_identity_key}",
        f"screen_generation: {screen_generation}",
        f"observation_id: {observation_id}",
        "Candidate regions (normalized box evidence supplied by runtime; not coordinates to output):",
    ]
    for cand in candidates:
        x0, y0, x1, y1 = cand.box
        lines.append(
            f"- candidate_id={cand.candidate_id!r} "
            f"appearance_token={cand.appearance_token!r} "
            f"box=({x0:.4f},{y0:.4f},{x1:.4f},{y1:.4f})"
        )
    lines.append(
        "Classify every listed candidate_id. Echo target_identity and screen_generation unchanged."
    )
    if game_brief is not None:
        lines.extend(
            (
                "Advisory game brief (context only; classify from the frame and candidate evidence):",
                json.dumps(dict(game_brief), sort_keys=True, separators=(",", ":")),
            )
        )
    return "\n".join(lines)


class SemanticConfirmer:
    def __init__(
        self,
        gateway: JsonGateway | None,
        *,
        model: str,
        timeout_s: float = 30.0,
        game_brief: Mapping[str, Any] | None = None,
    ) -> None:
        self._gateway = gateway
        self._model = (model or "").strip()
        self._timeout_s = timeout_s
        self._game_brief = dict(game_brief) if game_brief is not None else None
        self._contexts: dict[tuple[str, str], _ContextState] = {}
        self._telemetry: list[SemanticConfirmationTelemetry] = []
        self._active_context: tuple[str, str] | None = None
        self._batch_in_flight = False

    def set_game_brief(self, game_brief: Mapping[str, Any] | None) -> None:
        """Replace advisory context when the pinned target changes."""

        next_brief = dict(game_brief) if game_brief is not None else None
        if next_brief != self._game_brief:
            self._contexts.clear()
            self._active_context = None
        self._game_brief = next_brief

    @property
    def game_brief(self) -> Mapping[str, Any] | None:
        return dict(self._game_brief) if self._game_brief is not None else None

    @property
    def telemetry(self) -> tuple[SemanticConfirmationTelemetry, ...]:
        return tuple(self._telemetry)

    def clear_pending(self) -> None:
        """Drop unconfirmed work. Confirmed classes stay until invalidate."""

        for ctx in self._contexts.values():
            ctx.observations.clear()
            ctx.attempts.clear()

    def invalidate(self, candidate_ids: Sequence[str]) -> None:
        """A returned track is classified again."""

        ids = {str(cid) for cid in candidate_ids}
        for ctx in self._contexts.values():
            for cid in ids:
                ctx.observations.pop(cid, None)
                ctx.confirmed.pop(cid, None)
                ctx.confirmed_rationale.pop(cid, None)
                ctx.attempts.pop(cid, None)
                ctx.conflicts.discard(cid)
            ctx.class_cache = {key: value for key, value in ctx.class_cache.items() if key[0] not in ids}

    def start_new_confirmation_cycle(self) -> None:
        """Clear conflict and observation agreement state for the active context."""
        if self._active_context is None:
            return
        ctx = self._contexts[self._active_context]
        ctx.observations.clear()
        ctx.conflicts.clear()
        ctx.confirmed.clear()
        ctx.confirmed_rationale.clear()
        ctx.attempts.clear()

    def observe(
        self,
        *,
        target_identity: str | Mapping[str, Any],
        screen_generation: int | str,
        observation_id: str,
        candidates: Sequence[CandidateEvidence],
        frame: EncodedFrame,
    ) -> SemanticObservationResult:
        target_key = _identity_key(target_identity)
        gen_key = _generation_key(screen_generation)
        ctx_key = (target_key, gen_key)
        self._active_context = ctx_key
        ctx = self._contexts.setdefault(ctx_key, _ContextState())

        call_telemetry: list[SemanticConfirmationTelemetry] = []
        unavailable_global = self._gateway is None or not self._model
        unavailable_now: set[str] = set()

        batch_ids: list[str] = []
        batch_candidates: list[CandidateEvidence] = []

        if not unavailable_global:
            for cand in candidates:
                if cand.candidate_id in ctx.conflicts:
                    continue
                if cand.candidate_id in ctx.confirmed:
                    continue
                if ctx.attempts.get(cand.candidate_id, 0) >= _MAX_ATTEMPTS_PER_GENERATION:
                    continue
                if observation_id in ctx.observations.get(cand.candidate_id, {}):
                    continue
                batch_ids.append(cand.candidate_id)
                batch_candidates.append(cand)

            if batch_ids and not self._batch_in_flight:
                self._batch_in_flight = True
                try:
                    outcome, mapping, call = self._invoke_batch(
                        target_key=target_key,
                        gen_key=gen_key,
                        observation_id=observation_id,
                        batch_candidates=batch_candidates,
                        frame=frame,
                        expected_ids=frozenset(batch_ids),
                    )
                    call_telemetry.append(
                        SemanticConfirmationTelemetry(
                            model=call.model if call else self._model,
                            observation_id=observation_id,
                            evidence_candidate_ids=tuple(batch_ids),
                            latency_ms=call.latency_ms if call else None,
                            prompt_tokens=call.prompt_tokens if call else None,
                            output_tokens=call.output_tokens if call else None,
                            outcome=outcome,
                            target_identity_key=target_key,
                            screen_generation=gen_key,
                        )
                    )
                    self._telemetry.append(call_telemetry[-1])
                    if outcome == "success" and mapping is not None:
                        for cid in batch_ids:
                            cls, rationale = mapping[cid]
                            cand = next(c for c in batch_candidates if c.candidate_id == cid)
                            ctx.class_cache[(cid, cand.appearance_token)] = (cls, rationale)
                            ctx.attempts[cid] = ctx.attempts.get(cid, 0) + 1
                            self._record_observation(
                                ctx, cid, observation_id, cls, rationale=rationale
                            )
                    elif outcome == "rejected":
                        for cid in batch_ids:
                            ctx.attempts[cid] = ctx.attempts.get(cid, 0) + 1
                    elif outcome in {"timeout", "error"}:
                        for cid in batch_ids:
                            ctx.attempts[cid] = ctx.attempts.get(cid, 0) + 1
                            unavailable_now.add(cid)
                finally:
                    self._batch_in_flight = False

        statuses: list[CandidateConfirmationStatus] = []
        for cand in candidates:
            statuses.append(
                self._status_for_candidate(
                    ctx,
                    cand.candidate_id,
                    unavailable_global=unavailable_global,
                    unavailable_now=cand.candidate_id in unavailable_now,
                )
            )

        return SemanticObservationResult(
            target_identity_key=target_key,
            screen_generation=gen_key,
            observation_id=observation_id,
            candidates=tuple(statuses),
            telemetry=tuple(call_telemetry),
        )

    def _invoke_batch(
        self,
        *,
        target_key: str,
        gen_key: str,
        observation_id: str,
        batch_candidates: Sequence[CandidateEvidence],
        frame: EncodedFrame,
        expected_ids: frozenset[str],
    ) -> tuple[str, dict[str, tuple[SemanticClass, str]] | None, ModelCall | None]:
        assert self._gateway is not None
        user = _build_user_prompt(
            target_identity_key=target_key,
            screen_generation=gen_key,
            observation_id=observation_id,
            candidates=batch_candidates,
            game_brief=self._game_brief,
        )
        try:
            call = self._gateway.complete_json(
                system=_SYSTEM_PROMPT,
                user=user,
                json_schema=_RESPONSE_JSON_SCHEMA,
                schema_name=_SCHEMA_NAME,
                frames=[frame],
                timeout_s=self._timeout_s,
            )
        except TimeoutError:
            return "timeout", None, None
        except Exception:  # noqa: BLE001 — public API must not raise
            return "error", None, None

        mapping = _validate_parsed_response(
            call.parsed if isinstance(call.parsed, dict) else None,
            expected_target_key=target_key,
            expected_generation=gen_key,
            expected_ids=expected_ids,
        )
        if mapping is None:
            return "rejected", None, call
        return "success", mapping, call

    def _record_observation(
        self,
        ctx: _ContextState,
        candidate_id: str,
        observation_id: str,
        semantic_class: SemanticClass,
        *,
        rationale: str = "",
    ) -> None:
        if candidate_id in ctx.conflicts or candidate_id in ctx.confirmed:
            return
        by_obs = ctx.observations.setdefault(candidate_id, {})
        if observation_id in by_obs:
            return
        existing_classes = set(by_obs.values())
        if existing_classes and semantic_class not in existing_classes:
            ctx.conflicts.add(candidate_id)
            return
        by_obs[observation_id] = semantic_class
        agreeing = self._agreeing_class(by_obs)
        if agreeing is not None:
            ctx.confirmed[candidate_id] = agreeing
            if rationale:
                ctx.confirmed_rationale[candidate_id] = rationale
            elif candidate_id not in ctx.confirmed_rationale:
                for (cid, _tok), (_cls, rat) in ctx.class_cache.items():
                    if cid == candidate_id and rat:
                        ctx.confirmed_rationale[candidate_id] = rat
                        break

    @staticmethod
    def _agreeing_class(by_obs: dict[str, str]) -> SemanticClass | None:
        if len(by_obs) < 2:
            return None
        classes = set(by_obs.values())
        if len(classes) != 1:
            return None
        return next(iter(classes))  # type: ignore[return-value]

    def _status_for_candidate(
        self,
        ctx: _ContextState,
        candidate_id: str,
        *,
        unavailable_global: bool,
        unavailable_now: bool,
    ) -> CandidateConfirmationStatus:
        if unavailable_global or unavailable_now:
            return CandidateConfirmationStatus(candidate_id, "unavailable")
        if candidate_id in ctx.conflicts:
            return CandidateConfirmationStatus(candidate_id, "unresolved")
        if candidate_id in ctx.confirmed:
            cls = ctx.confirmed[candidate_id]
            return CandidateConfirmationStatus(
                candidate_id,
                "confirmed",
                semantic_class=cls,
                rationale=ctx.confirmed_rationale.get(candidate_id),
            )
        if ctx.attempts.get(candidate_id, 0) >= _MAX_ATTEMPTS_PER_GENERATION:
            return CandidateConfirmationStatus(candidate_id, "exhausted")
        by_obs = ctx.observations.get(candidate_id, {})
        if len(by_obs) == 1:
            return CandidateConfirmationStatus(candidate_id, "withheld")
        return CandidateConfirmationStatus(candidate_id, "unresolved")


@dataclass(frozen=True)
class _SemanticControl:
    """Ordered confirmer mutation applied on the worker thread, never by callers."""

    seq: int
    kind: str
    payload: tuple[str, ...] = ()
    brief: Mapping[str, Any] | None = None


class SemanticWorker:
    """Bounded latest-value semantic runner: one inference in flight, one pending.

    Mirrors the existing policy-worker lifecycle pattern on purpose, but runs on
    its own daemon thread: it is deliberately not the Qwen reaction slot and not
    the directive/release path. ``submit`` and ``poll`` never block, ``shutdown``
    has a bounded join, and a blocked provider therefore cannot stall capture,
    directive publication or release.

    The confirmer is not thread-safe, so it is owned by this thread alone:
    ``set_game_brief``/``clear_pending``/``invalidate``/``start_new_confirmation_cycle``
    only enqueue ordered control records, and the worker applies them between
    jobs in submission order. The worker also owns the brief digest, so
    :meth:`submit` never reads mutable confirmer state from the caller's thread.

    Main-owner rules are enforced outside this class: results only carry
    hypotheses, and :meth:`validate_result` requires a distinct fresh trusted
    observation before a delayed result may be applied.
    """

    def __init__(
        self,
        confirmer: SemanticConfirmer,
        *,
        timeout_s: float = 30.0,
    ) -> None:
        self._confirmer = confirmer
        self._default_timeout_s = timeout_s
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._turn = 0
        self._latest_job = 0
        self._pending: SemanticJob | None = None
        self._inflight: SemanticJob | None = None
        self._finished_job: SemanticJob | None = None
        self._finished: SemanticJobResult | None = None
        self._deadlines: dict[int, float] = {}
        self._barriers: dict[int, int] = {}
        self._coalesced = 0
        self._brief_digest = brief_digest(confirmer.game_brief)
        self._queued_brief_digest = self._brief_digest
        self._control_seq = 0
        self._controls: list[_SemanticControl] = []
        self._shutdown = False
        self._thread = threading.Thread(target=self._loop, name="semantic-worker", daemon=True)
        self._thread.start()

    @property
    def coalesced_submissions(self) -> int:
        return self._coalesced

    @property
    def is_running(self) -> bool:
        return self._thread.is_alive()

    # -- Serialized confirmer control: non-blocking, ordered ------------------

    def set_game_brief(self, game_brief: Mapping[str, Any] | None) -> bool:
        """Queue an advisory-brief change; applied before later queued inference.

        The worker owns the brief digest, so :meth:`submit` never reads mutable
        confirmer state from the caller's thread.
        """

        return self._enqueue_control(
            "set_brief", brief=None if game_brief is None else dict(game_brief)
        )

    def clear_pending(self) -> bool:
        """Queue an unconfirmed-work reset (screen change / replaced surface)."""

        return self._enqueue_control("clear_pending")

    def invalidate(self, candidate_ids: Sequence[str]) -> bool:
        """Queue 'a returned track must be classified again'."""

        return self._enqueue_control(
            "invalidate", payload=tuple(str(cid) for cid in candidate_ids)
        )

    def start_new_confirmation_cycle(self) -> bool:
        """Queue an agreement/conflict reset for the active context."""

        return self._enqueue_control("new_cycle")

    def _enqueue_control(
        self,
        kind: str,
        *,
        payload: tuple[str, ...] = (),
        brief: Mapping[str, Any] | None = None,
    ) -> bool:
        """Append a control record; returns immediately, even with a blocked provider."""

        with self._cond:
            if self._shutdown:
                return False
            self._control_seq += 1
            self._controls.append(
                _SemanticControl(seq=self._control_seq, kind=kind, payload=payload, brief=brief)
            )
            if kind == "set_brief":
                self._queued_brief_digest = brief_digest(brief)
            self._cond.notify_all()
        return True

    def _apply_controls_up_to(self, barrier: int) -> None:
        """Apply queued controls in order; caller holds the condition lock."""

        while self._controls and self._controls[0].seq <= barrier:
            self._apply_control(self._controls.pop(0))

    def _apply_control(self, control: _SemanticControl) -> None:
        confirmer = self._confirmer
        if control.kind == "set_brief":
            confirmer.set_game_brief(control.brief)
            self._brief_digest = brief_digest(control.brief)
            if (
                self._finished_job is not None
                and self._finished_job.brief_digest != self._brief_digest
            ):
                self._mark_finished_unavailable("brief_changed")
            return
        if control.kind == "clear_pending":
            confirmer.clear_pending()
            self._mark_finished_unavailable("semantic_reset")
            return
        if control.kind == "invalidate":
            ids = set(control.payload)
            confirmer.invalidate(control.payload)
            if self._finished_job is not None and ids.intersection(
                self._finished_job.candidate_ids
            ):
                self._mark_finished_unavailable("candidate_invalidated")
            return
        confirmer.start_new_confirmation_cycle()
        self._mark_finished_unavailable("confirmation_cycle_reset")

    def _mark_finished_unavailable(self, reason: str) -> None:
        if self._finished is None or self._finished.status != SemanticJobStatus.READY:
            return
        self._finished = self._unavailable(self._finished_job, reason)

    def submit(
        self,
        *,
        target_identity: str | Mapping[str, Any],
        screen_generation: int | str,
        observation_id: str,
        candidates: Sequence[CandidateEvidence],
        frame: EncodedFrame,
        schema_revision: str = "",
        timeout_s: float | None = None,
        now: float | None = None,
    ) -> SemanticJob:
        """Publish the newest semantic request; never blocks on inference.

        A request identical to the in-flight or pending job is coalesced instead
        of enqueued again, so unchanged frames cannot build a request backlog.
        """

        target_key = _identity_key(target_identity)
        gen_key = _generation_key(screen_generation)
        frozen_candidates = tuple(candidates)
        frozen_frame = replace(frame)
        digest = evidence_digest(candidates=frozen_candidates, frame=frozen_frame)
        candidate_ids = tuple(cand.candidate_id for cand in frozen_candidates)

        with self._cond:
            if self._shutdown:
                raise RuntimeError("semantic worker is shut down")
            b_digest = self._queued_brief_digest
            key = (
                target_key,
                gen_key,
                schema_revision,
                b_digest,
                digest,
                observation_id,
                candidate_ids,
            )
            existing = self._inflight or self._pending or self._finished_job
            if existing is not None and existing.coalesce_key == key:
                self._coalesced += 1
                return existing
            monotonic_now = time.monotonic() if now is None else now
            limit = self._default_timeout_s if timeout_s is None else timeout_s
            self._turn += 1
            job = SemanticJob(
                job_id=self._turn,
                target_identity_key=target_key,
                screen_generation=gen_key,
                observation_id=observation_id,
                evidence_digest=digest,
                schema_revision=schema_revision,
                brief_digest=b_digest,
                candidate_ids=candidate_ids,
                candidates=frozen_candidates,
                frame=frozen_frame,
                submitted_at=monotonic_now,
                deadline_at=monotonic_now + limit,
            )
            if self._pending is not None:
                self._barriers.pop(self._pending.job_id, None)
            self._pending = job
            self._latest_job = job.job_id
            self._deadlines[job.job_id] = job.deadline_at
            self._barriers[job.job_id] = self._control_seq
            self._prune_deadlines()
            self._cond.notify_all()
            return job

    def poll(self, job_id: int, *, now: float | None = None) -> SemanticJobResult:
        """Non-blocking status; timed-out or superseded work is unavailable."""

        if job_id <= 0:
            return self._unavailable(None, "unknown_job")
        monotonic_now = time.monotonic() if now is None else now
        with self._lock:
            latest = self._latest_job
            deadline = self._deadlines.get(job_id)
            job: SemanticJob | None = None
            if self._inflight is not None and self._inflight.job_id == job_id:
                job = self._inflight
            elif self._pending is not None and self._pending.job_id == job_id:
                job = self._pending
            finished = (
                self._finished
                if self._finished_job is not None and self._finished_job.job_id == job_id
                else None
            )
            finished_job = self._finished_job

        if deadline is not None and monotonic_now > deadline:
            return self._unavailable(job or finished_job, "semantic_deadline")
        if job_id < latest:
            return self._unavailable(finished_job, "superseded")
        if finished is not None:
            return finished
        if job is not None or job_id == latest:
            base = job or finished_job
            return self._pending_result(base) if base is not None else self._unavailable(
                None, "unknown_job"
            )
        return self._unavailable(None, "unknown_job")

    def validate_result(
        self,
        result: SemanticJobResult,
        *,
        fresh_observation_id: str,
        fresh_trusted: bool,
        target_identity_key: str | None = None,
        screen_generation: int | str | None = None,
        schema_revision: str | None = None,
        brief_digest: str | None = None,
        relevant_evidence_changed: bool = False,
    ) -> SemanticJobResult:
        """Session-owner validation; the only path that may accept a result.

        Fail-closed: a genuinely stale result (changed target, generation, schema,
        brief or relevant evidence) is discarded, the validation observation must
        differ from the origin one, and an already-validated result is never
        accepted as independent proof. An unchanged object may still accept a
        delayed result from an earlier origin observation.
        """

        if result.status != SemanticJobStatus.READY:
            return replace(result, reason=result.reason or "not_ready")
        if not fresh_trusted:
            return self._reject(result, "untrusted_validation_observation")
        if not fresh_observation_id or fresh_observation_id == result.origin_observation_id:
            return self._reject(result, "validation_must_be_distinct_observation")
        if result.validation_observation_id is not None:
            return self._reject(result, "already_validated")
        if target_identity_key is not None and target_identity_key != result.target_identity_key:
            return self._reject(result, "target_identity_changed")
        if (
            screen_generation is not None
            and _generation_key(screen_generation) != result.screen_generation
        ):
            return self._reject(result, "screen_generation_changed")
        if schema_revision is not None and schema_revision != result.schema_revision:
            return self._reject(result, "schema_revision_changed")
        if brief_digest is not None and brief_digest != result.brief_digest:
            return self._reject(result, "brief_changed")
        if relevant_evidence_changed:
            return self._reject(result, "relevant_evidence_changed")
        return replace(result, validation_observation_id=fresh_observation_id)

    def shutdown(self, *, wait: bool = False, timeout_s: float = 2.0) -> bool:
        """Stop the worker without freezing callers. Returns True when stopped."""

        with self._cond:
            self._shutdown = True
            self._cond.notify_all()
        if wait:
            self._thread.join(timeout=timeout_s)
        return not self._thread.is_alive()

    @staticmethod
    def _reject(result: SemanticJobResult, reason: str) -> SemanticJobResult:
        return replace(result, status=SemanticJobStatus.UNAVAILABLE, reason=reason)

    def _prune_deadlines(self) -> None:
        if len(self._deadlines) <= 64:
            return
        keep = sorted(self._deadlines)[-64:]
        self._deadlines = {key: self._deadlines[key] for key in keep}

    @staticmethod
    def _markers(job: SemanticJob | None) -> tuple[str, str, str, str, str, str]:
        if job is None:
            return ("", "", "", "", "", "")
        return (
            job.target_identity_key,
            job.screen_generation,
            job.observation_id,
            job.evidence_digest,
            job.schema_revision,
            job.brief_digest,
        )

    def _pending_result(self, job: SemanticJob) -> SemanticJobResult:
        target, generation, origin, digest, schema, brief = self._markers(job)
        return SemanticJobResult(
            status=SemanticJobStatus.PENDING,
            job_id=job.job_id,
            target_identity_key=target,
            screen_generation=generation,
            origin_observation_id=origin,
            evidence_digest=digest,
            schema_revision=schema,
            brief_digest=brief,
        )

    def _unavailable(self, job: SemanticJob | None, reason: str) -> SemanticJobResult:
        target, generation, origin, digest, schema, brief = self._markers(job)
        return SemanticJobResult(
            status=SemanticJobStatus.UNAVAILABLE,
            job_id=job.job_id if job is not None else 0,
            target_identity_key=target,
            screen_generation=generation,
            origin_observation_id=origin,
            evidence_digest=digest,
            schema_revision=schema,
            brief_digest=brief,
            reason=reason,
        )

    def _loop(self) -> None:
        while True:
            with self._cond:
                while (
                    not self._shutdown
                    and self._pending is None
                    and not self._controls
                ):
                    self._cond.wait()
                if self._shutdown and self._pending is None:
                    return
                job = self._pending
                self._pending = None
                barrier = (
                    self._control_seq
                    if job is None
                    else self._barriers.pop(job.job_id, 0)
                )
                if job is not None:
                    self._inflight = job
                self._apply_controls_up_to(barrier)

            if job is None:
                continue

            result = self._run_job(job)

            with self._cond:
                self._inflight = None
                if job.job_id == self._latest_job:
                    self._finished_job = job
                    self._finished = result
                else:
                    self._finished_job = None
                    self._finished = None
                if self._pending is not None:
                    self._cond.notify_all()

    def _run_job(self, job: SemanticJob) -> SemanticJobResult:
        try:
            observation = self._confirmer.observe(
                target_identity=job.target_identity_key,
                screen_generation=job.screen_generation,
                observation_id=job.observation_id,
                candidates=job.candidates,
                frame=job.frame,
            )
        except Exception as exc:  # noqa: BLE001 — a worker must not take down the loop
            return self._unavailable(job, f"semantic_worker_failed:{type(exc).__name__}")
        if job.deadline_at < time.monotonic():
            return self._unavailable(job, "semantic_deadline")
        target, generation, origin, digest, schema, brief = self._markers(job)
        return SemanticJobResult(
            status=SemanticJobStatus.READY,
            job_id=job.job_id,
            target_identity_key=target,
            screen_generation=generation,
            origin_observation_id=origin,
            evidence_digest=digest,
            schema_revision=schema,
            brief_digest=brief,
            candidates=tuple(observation.candidates),
            telemetry=tuple(observation.telemetry),
        )
