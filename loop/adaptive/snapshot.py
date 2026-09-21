"""StateSnapshotV2 sidecar and motion-based perception."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, fields, is_dataclass

from PIL import Image

from loop.adaptive.environment import StepResult
from loop.adaptive.evidence import assign_track_ids, detect_regions, motion_bbox
from loop.adaptive.schema import ActionSurface, StateSnapshot, empty_schema

_SCHEMA_REV = empty_schema().revision.revision_id
_PRIV_KEYS = frozenset({"privileged_info", "secret"})


@dataclass(frozen=True)
class Provenanced:
    value: object
    confidence: float
    unknown: bool
    provenance: tuple[str, ...]


@dataclass(frozen=True)
class EntityView:
    type: str
    stable_id: str
    attributes: dict[str, Provenanced]
    confidence: float


@dataclass(frozen=True)
class RelationView:
    src: str
    kind: str
    dst: str
    confidence: float


@dataclass(frozen=True)
class StateSnapshotV2:
    snapshot_id: str
    schema_revision_id: str
    episode_id: str
    episode_generation: int
    step: int
    frame_digest: str
    frame_history_digests: tuple[str, ...]
    captured_at: float
    observation_age_s: float
    mode: str | None
    fields: dict[str, Provenanced]
    entities: tuple[EntityView, ...]
    relations: tuple[RelationView, ...]
    recent_events: tuple[dict, ...]
    confidence: float


def snapshot_payload(snap: StateSnapshotV2) -> dict:
    return _to_plain(snap)


def _to_plain(obj: object) -> object:
    if is_dataclass(obj):
        return {f.name: _to_plain(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, dict):
        return {k: _to_plain(v) for k, v in sorted(obj.items())}
    if isinstance(obj, (list, tuple)):
        return [_to_plain(v) for v in obj]
    return obj


def assert_no_privileged(obj: object) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in _PRIV_KEYS:
                raise ValueError(f"privileged leak at key {k!r}")
            assert_no_privileged(v)
        return
    if isinstance(obj, (list, tuple)):
        for v in obj:
            assert_no_privileged(v)
        return
    if is_dataclass(obj):
        for f in fields(obj):
            assert_no_privileged(getattr(obj, f.name))
        return
    if obj == "SECRET":
        raise ValueError("privileged leak: SECRET value")


def to_verified_snapshot(v2: StateSnapshotV2) -> StateSnapshot:
    fields_plain = {k: p.value for k, p in v2.fields.items()}
    entities = {e.stable_id: {"type_id": e.type} for e in v2.entities}
    surface = ActionSurface(
        snapshot_id=v2.snapshot_id,
        schema_revision_id=v2.schema_revision_id,
        action_ids=(),
    )
    return StateSnapshot(
        snapshot_id=v2.snapshot_id,
        schema_revision_id=v2.schema_revision_id,
        mode_id=v2.mode,
        fields=fields_plain,
        entities=entities,
        surface=surface,
    )


def _prov(
    value: object,
    *,
    confidence: float,
    unknown: bool,
    provenance: tuple[str, ...] = ("motion",),
) -> Provenanced:
    return Provenanced(value, confidence, unknown, provenance)


def _center(bbox: tuple[float, float, float, float]) -> tuple[float, float]:
    return ((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)


class MotionPerception:
    def __init__(self) -> None:
        self._prev_image: Image.Image | None = None
        self._prev_regions: list[tuple[float, float, float, float]] = []
        self._prev_track_ids: list[int] = []
        self._digest_history: list[str] = []
        self._cache_key: tuple | None = None
        self._cache_snap: StateSnapshotV2 | None = None

    def reset(self) -> None:
        self._prev_image = None
        self._prev_regions = []
        self._prev_track_ids = []
        self._digest_history = []
        self._cache_key = None
        self._cache_snap = None

    def qualify(self, step_result: StepResult) -> StateSnapshotV2:
        key = (step_result.episode_generation, step_result.step, step_result.frame_digest)
        if self._cache_key == key and self._cache_snap is not None:
            return self._cache_snap

        obs = step_result.observation
        entities: tuple[EntityView, ...] = ()
        dx_p = _prov(0.0, confidence=0.0, unknown=True)
        dy_p = _prov(0.0, confidence=0.0, unknown=True)
        grounded_p = _prov(None, confidence=0.0, unknown=True)
        scroll_p = _prov(0.0, confidence=0.0, unknown=True)
        conf = 0.0

        if isinstance(obs, Image.Image):
            curr = obs.convert("RGB")
            regions = detect_regions(curr)
            mb = motion_bbox(self._prev_image, curr) if self._prev_image is not None else None
            curr_bbox = mb or (regions[0] if regions else None)
            if curr_bbox is not None and self._prev_regions:
                p0 = _center(self._prev_regions[0])
                c0 = _center(curr_bbox)
                dx, dy = c0[0] - p0[0], c0[1] - p0[1]
                dx_p = _prov(dx, confidence=0.7, unknown=False)
                dy_p = _prov(dy, confidence=0.7, unknown=False)
                scroll_p = _prov(dx, confidence=0.7, unknown=False)
                grounded_p = _prov(abs(dy) < 0.02, confidence=0.7, unknown=False)
                conf = 0.7
            elif curr_bbox is not None:
                conf = 0.3 if self._prev_image is None else 0.5
            else:
                conf = 0.1

            track_src = [curr_bbox] if curr_bbox is not None else regions
            if track_src:
                tracks = assign_track_ids(
                    self._prev_regions,
                    track_src,
                    prev_ids=self._prev_track_ids or None,
                )
                if tracks:
                    tid, bbox = tracks[0]
                    entities = (
                        EntityView(
                            type="tracked",
                            stable_id=f"track_{tid}",
                            attributes={},
                            confidence=0.6 if self._prev_regions else 0.4,
                        ),
                    )
                    self._prev_track_ids = [tid]
                    self._prev_regions = [bbox]
            self._prev_image = curr.copy()
        else:
            conf = 0.0

        dig = step_result.frame_digest
        hist = tuple((self._digest_history + [dig])[-3:])
        self._digest_history = list(hist)

        now = time.monotonic()
        captured_at = step_result.capture_ts or now
        snap = StateSnapshotV2(
            snapshot_id=uuid.uuid4().hex,
            schema_revision_id=_SCHEMA_REV,
            episode_id=step_result.episode_id,
            episode_generation=step_result.episode_generation,
            step=step_result.step,
            frame_digest=dig,
            frame_history_digests=hist,
            captured_at=captured_at,
            observation_age_s=max(0.0, now - captured_at),
            mode=None,
            fields={
                "dx": dx_p,
                "dy": dy_p,
                "grounded": grounded_p,
                "scroll_dx": scroll_p,
            },
            entities=entities,
            relations=(),
            recent_events=(),
            confidence=conf,
        )
        assert_no_privileged(snap)
        self._cache_key = key
        self._cache_snap = snap
        return snap
