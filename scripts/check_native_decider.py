"""Smoke-test the native Windows CUDA/Decider profile."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import math

import torch
import triton
import triton.language as tl


@triton.jit
def _add_kernel(x_ptr, y_ptr, out_ptr, n: tl.constexpr, block: tl.constexpr):
    offsets = tl.arange(0, block)
    mask = offsets < n
    x = tl.load(x_ptr + offsets, mask=mask)
    y = tl.load(y_ptr + offsets, mask=mask)
    tl.store(out_ptr + offsets, x + y, mask=mask)


def check_stack() -> dict[str, object]:
    from decider.infer import Decider  # noqa: F401
    from fla.ops.gla import fused_recurrent_gla  # noqa: F401
    from loop.adaptive.decider_policy import DECIDER_SOURCE_REVISION

    expected = {
        "decider": "0.9.0",
        "flash-linear-attention": "0.5.2",
        "numpy": "1.26.4",
        "torch": "2.14.0+cu130",
        "transformers": "5.17.0",
        "triton-windows": "3.8.0.post28",
    }
    installed = {
        name: importlib.metadata.version(name) for name in expected
    }
    if installed != expected:
        raise RuntimeError(
            f"native profile does not match its manifest: {installed!r}"
        )
    direct_url = json.loads(
        importlib.metadata.distribution("decider").read_text("direct_url.json") or "{}"
    )
    commit = direct_url.get("vcs_info", {}).get("commit_id")
    if commit != DECIDER_SOURCE_REVISION:
        raise RuntimeError(f"unexpected Decider source revision: {commit!r}")

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA PyTorch is not active in this environment")
    x = torch.arange(32, device="cuda", dtype=torch.float32)
    y = torch.full_like(x, 2)
    out = torch.empty_like(x)
    _add_kernel[(1,)](x, y, out, n=x.numel(), block=32)
    torch.cuda.synchronize()
    if not torch.equal(out, x + y):
        raise RuntimeError("Triton GPU kernel returned an incorrect result")
    return {
        "torch": torch.__version__,
        "cuda": torch.version.cuda,
        "gpu": torch.cuda.get_device_name(0),
        "triton": triton.__version__,
        "decider_source_revision": commit,
        "kernel_ok": True,
        "decider_import_ok": True,
        "fla_import_ok": True,
    }


def check_model(*, use_graphs: bool) -> dict[str, object]:
    from loop.adaptive.decider_policy import (
        DECIDER_08B_MODEL_ID,
        DECIDER_08B_MODEL_REVISION,
        DeciderPolicy,
    )
    from loop.adaptive.candidates import CandidateAction, Directive
    from loop.adaptive.snapshot import Provenanced, StateSnapshotV2

    policy = DeciderPolicy.from_huggingface(use_graphs=use_graphs)
    snapshot = StateSnapshotV2(
        snapshot_id="smoke-snapshot",
        schema_revision_id="smoke-schema",
        episode_id="smoke-episode",
        episode_generation=1,
        step=1,
        frame_digest="not-model-visible",
        frame_history_digests=(),
        captured_at=0.0,
        observation_age_s=0.0,
        mode="running",
        fields={"hazard_on_right": Provenanced(True, 1.0, False, ("smoke",))},
        entities=(),
        relations=(),
        recent_events=(),
        confidence=1.0,
    )
    directive = Directive(1, 1, "avoid hazards", 30.0, 0.0)
    candidates = (
        CandidateAction("move_left", "hidden:left", "Move away from a right-side hazard."),
        CandidateAction("move_right", "hidden:right", "Move toward a right-side hazard."),
    )
    decision = policy.score(snapshot, directive, candidates)
    if decision.candidate_id not in {candidate.action_id for candidate in candidates}:
        raise RuntimeError("Decider selected an action that was not offered")
    if not all(math.isfinite(value) for value in decision.probabilities.values()):
        raise RuntimeError("Decider returned non-finite probabilities")
    return {
        "model": DECIDER_08B_MODEL_ID,
        "revision": DECIDER_08B_MODEL_REVISION,
        "graphs": use_graphs,
        "choice": decision.candidate_id,
        "confidence": round(decision.confidence, 4),
        "probabilities": decision.probabilities,
        "peak_vram_mib": round(torch.cuda.max_memory_allocated() / 1024**2, 1),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--model",
        action="store_true",
        help="also download the pinned 0.8B snapshot and run one SystemOne decision",
    )
    parser.add_argument(
        "--graphs",
        action="store_true",
        help="use Decider's compiled CUDA-graph engine during the model check",
    )
    args = parser.parse_args()
    if args.graphs and not args.model:
        parser.error("--graphs requires --model")
    result = {"stack": check_stack()}
    if args.model:
        result["model"] = check_model(use_graphs=args.graphs)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
