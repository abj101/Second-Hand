"""Wave 7 armed policy selection on frozen multi-candidate scores (no GPU)."""

from __future__ import annotations

import pytest

from loop.adaptive.orchestrator import PolicyQualificationScore, select_armed_policy


def _score(
    policy_id: str,
    *,
    effect: float,
    p95: float,
    vram: float | None = None,
    safety: int = 0,
) -> PolicyQualificationScore:
    return PolicyQualificationScore(
        policy_id=policy_id,
        verified_effect_score=effect,
        p95_latency_s=p95,
        peak_vram_gb=vram,
        safety_regressions=safety,
    )


def test_select_armed_policy_requires_beat_deterministic():
    det = _score("deterministic", effect=0.6, p95=0.05)
    fake_08 = _score("decider-0.8b", effect=0.55, p95=0.04, vram=1.5)
    assert select_armed_policy(deterministic=det, candidates=(fake_08,)) is None


def test_select_armed_policy_picks_higher_effect_then_latency_then_vram():
    det = _score("deterministic", effect=0.5, p95=0.1)
    a = _score("decider-0.8b", effect=0.72, p95=0.08, vram=1.6)
    b = _score("decider-2b", effect=0.72, p95=0.06, vram=2.4)
    c = _score("decider-alt", effect=0.72, p95=0.06, vram=1.4)
    assert select_armed_policy(deterministic=det, candidates=(a, b, c)) == "decider-alt"


def test_select_armed_policy_rejects_safety_regressions():
    det = _score("deterministic", effect=0.4, p95=0.1)
    unsafe = _score("decider-0.8b", effect=0.9, p95=0.05, safety=1)
    assert select_armed_policy(deterministic=det, candidates=(unsafe,)) is None


def test_frozen_multi_candidate_manifest_min_two():
    det = _score("deterministic", effect=0.5, p95=0.1)
    winner = _score("decider-0.8b", effect=0.65, p95=0.07, vram=1.5)
    scenarios = (
        {"scenario_id": "fork_left", "candidate_count": 2},
        {"scenario_id": "fork_right", "candidate_count": 3},
    )
    for row in scenarios:
        assert row["candidate_count"] >= 2
    assert (
        select_armed_policy(
            deterministic=det,
            candidates=(winner,),
            min_candidates_per_scenario=2,
        )
        == "decider-0.8b"
    )


@pytest.mark.gpu
def test_gpu_bakeoff_placeholder():
    pytest.skip("opt-in: pinned 0.8B vs 2B on frozen native scenarios with CUDA")
