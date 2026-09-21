"""Mario-specific perception profile (wraps generic motion perception)."""

from __future__ import annotations

from loop.adaptive.environment import StepResult
from loop.adaptive.snapshot import MotionPerception, Provenanced, StateSnapshotV2, assert_no_privileged


def _prov(value: object, *, confidence: float, unknown: bool) -> Provenanced:
    return Provenanced(value, confidence, unknown, ("mario_profile",))


class MarioPerception(MotionPerception):
    def qualify(self, step_result: StepResult) -> StateSnapshotV2:
        snap = super().qualify(step_result)
        fields = dict(snap.fields)
        player_box = None
        if snap.entities:
            ent = snap.entities[0]
            player_box = ent.stable_id
            fields["player_track_id"] = _prov(
                player_box, confidence=ent.confidence, unknown=False
            )
        else:
            fields["player_track_id"] = _prov(None, confidence=0.0, unknown=True)
        fields["nearest_gap"] = _prov(None, confidence=0.0, unknown=True)
        fields["terminal_or_title"] = _prov(None, confidence=0.0, unknown=True)
        out = StateSnapshotV2(
            snapshot_id=snap.snapshot_id,
            schema_revision_id=snap.schema_revision_id,
            episode_id=snap.episode_id,
            episode_generation=snap.episode_generation,
            step=snap.step,
            frame_digest=snap.frame_digest,
            frame_history_digests=snap.frame_history_digests,
            captured_at=snap.captured_at,
            observation_age_s=snap.observation_age_s,
            mode=snap.mode,
            fields=fields,
            entities=snap.entities,
            relations=snap.relations,
            recent_events=snap.recent_events,
            confidence=snap.confidence,
        )
        assert_no_privileged(out)
        return out
