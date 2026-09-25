"""Trainable reaction policy. Weights are Qwen/Qwen3.5-0.8B, not Decider."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

from PIL import Image

from loop.adaptive.candidates import CandidateAction, Directive
from loop.adaptive.decider_policy import build_decider_state
from loop.adaptive.policy import DecisionError, ReactiveState, StructuredDecision, reactive_episode_meta
from loop.adaptive.schema import SceneGraph
from loop.adaptive.snapshot import StateSnapshotV2

QWEN35_08B_MODEL_ID = "Qwen/Qwen3.5-0.8B"
QWEN35_08B_REVISION = "2fc06364715b967f1860aea9cf38778875588b17"

# ponytail: fixed normalized margin; upgrade path is per-control padding from lease metadata
_CROP_MARGIN = 0.04


def choice_from_text(text: str, candidate_ids: tuple[str, ...]) -> str:
    """First reply line that is exactly an offered id."""
    offered = set(candidate_ids)
    for line in text.splitlines():
        token = line.strip().strip("`\"'")
        if token in offered:
            return token
    raise DecisionError("Qwen choice was not offered")


def render_choice_prompt(
    state: dict[str, object],
    candidates: tuple[CandidateAction, ...],
    *,
    image: Image.Image | None = None,
) -> str:
    del image  # vision input is separate from the compact JSON state line
    lines = [
        json.dumps(state, sort_keys=True, default=str),
        "Choose exactly one action id. Reply with that id and nothing else.",
    ]
    for candidate in sorted(candidates, key=lambda item: item.action_id):
        lines.append(f"{candidate.action_id}: {candidate.description}")
    return "\n".join(lines)


def _ambiguous_description(candidates: tuple[CandidateAction, ...]) -> str | None:
    counts: dict[str, int] = {}
    for candidate in candidates:
        counts[candidate.description] = counts.get(candidate.description, 0) + 1
    for description, count in counts.items():
        if count >= 2:
            return description
    return None


def _expand_norm_bbox(bbox: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = bbox
    m = _CROP_MARGIN
    return (
        max(0.0, x0 - m),
        max(0.0, y0 - m),
        min(1.0, x1 + m),
        min(1.0, y1 + m),
    )


def crop_control_region(
    image: Image.Image,
    bbox: tuple[float, float, float, float],
) -> Image.Image:
    """Region crop with neighboring pixels, clamped to the frame."""
    rgb = image.convert("RGB")
    w, h = rgb.size
    x0, y0, x1, y1 = _expand_norm_bbox(bbox)
    px0 = int(max(0, min(w - 1, x0 * w)))
    py0 = int(max(0, min(h - 1, y0 * h)))
    px1 = int(max(px0 + 1, min(w, x1 * w)))
    py1 = int(max(py0 + 1, min(h, y1 * h)))
    return rgb.crop((px0, py0, px1, py1))


def _registry_bbox(scene: SceneGraph, registry_id: str) -> tuple[float, float, float, float] | None:
    for node in scene.nodes:
        if str(node.attributes.get("registry_id") or "") == registry_id:
            return node.bbox
    return None


def _union_bboxes(
    boxes: tuple[tuple[float, float, float, float], ...],
) -> tuple[float, float, float, float]:
    x0 = min(b[0] for b in boxes)
    y0 = min(b[1] for b in boxes)
    x1 = max(b[2] for b in boxes)
    y1 = max(b[3] for b in boxes)
    return (x0, y0, x1, y1)


def choice_crop_for_candidates(
    candidates: tuple[CandidateAction, ...],
    *,
    frame_image: Image.Image | None,
    scene_graph: SceneGraph | None,
) -> Image.Image | None:
    if len(candidates) < 2 or frame_image is None or scene_graph is None:
        return None
    ambiguous = _ambiguous_description(candidates)
    if ambiguous is None:
        return None
    boxes: list[tuple[float, float, float, float]] = []
    for candidate in candidates:
        if candidate.description != ambiguous:
            continue
        bbox = _registry_bbox(scene_graph, candidate.action_id)
        if bbox is not None:
            boxes.append(bbox)
    if not boxes:
        return None
    return crop_control_region(frame_image, _union_bboxes(tuple(boxes)))


def prepare_qwen_worker_crop(
    policy: QwenReactionPolicy,
    candidates: tuple[CandidateAction, ...],
    *,
    frame_image: Image.Image | None = None,
    scene_graph: SceneGraph | None = None,
) -> Image.Image | None:
    """Attach a crop for synchronous scoring; worker jobs freeze a copy at submit."""
    crop = choice_crop_for_candidates(
        candidates,
        frame_image=frame_image,
        scene_graph=scene_graph,
    )
    policy._crop = crop
    return crop


@dataclass(frozen=True)
class ReactionChooseOutcome:
    branch: str
    action_id: str | None = None


def plan_reaction_choose(candidates: tuple[CandidateAction, ...]) -> ReactionChooseOutcome:
    if not candidates:
        return ReactionChooseOutcome("release")
    if len(candidates) == 1:
        return ReactionChooseOutcome("deterministic", candidates[0].action_id)
    return ReactionChooseOutcome("qwen")


def run_reaction_choose(
    candidates: tuple[CandidateAction, ...],
    policy: QwenReactionPolicy | None,
    snapshot: StateSnapshotV2 | ReactiveState,
    directive: Directive,
    *,
    frame_image: Image.Image | None = None,
    scene_graph: SceneGraph | None = None,
) -> ReactionChooseOutcome:
    """Offer-count branch used by the controller Qwen path and phase-4 gate tests."""
    plan = plan_reaction_choose(candidates)
    if plan.branch != "qwen":
        return plan
    if policy is None:
        raise DecisionError("Qwen reaction requires a policy")
    prepare_qwen_worker_crop(
        policy,
        candidates,
        frame_image=frame_image,
        scene_graph=scene_graph,
    )
    decision = policy.score(snapshot, directive, candidates)
    return ReactionChooseOutcome("qwen", decision.candidate_id)


class QwenReactionPolicy:
    """One offered id from the pinned Qwen3.5-0.8B checkpoint.

    The checkpoint has a vision encoder. This scorer sends the compact state.
    Ambiguous duplicate labels may attach a cropped region on the worker thread.
    """

    off_thread = True

    def __init__(self, model: object, processor: object, *, catalog_revision: str = "") -> None:
        self._model = model
        self._processor = processor
        self.catalog_revision = catalog_revision
        self._crop: Image.Image | None = None

    @classmethod
    def from_pretrained(
        cls,
        *,
        model_id: str = QWEN35_08B_MODEL_ID,
        revision: str = QWEN35_08B_REVISION,
        catalog_revision: str = "",
    ) -> "QwenReactionPolicy":
        import torch
        from transformers import AutoModelForImageTextToText, AutoProcessor

        if not torch.cuda.is_available():
            raise RuntimeError("Qwen reaction requires CUDA")
        processor = AutoProcessor.from_pretrained(model_id, revision=revision)
        model = AutoModelForImageTextToText.from_pretrained(
            model_id,
            revision=revision,
            dtype=torch.bfloat16,
        ).to("cuda")
        model.eval()
        return cls(model, processor, catalog_revision=catalog_revision)

    def score(
        self,
        snapshot: StateSnapshotV2 | ReactiveState,
        directive: Directive,
        candidates: tuple[CandidateAction, ...],
    ) -> StructuredDecision:
        if len(candidates) < 2:
            raise DecisionError("Qwen reaction requires at least two candidates")
        state = build_decider_state(snapshot, directive)
        crop = self._crop
        prompt = render_choice_prompt(state, candidates, image=crop)
        text = self._generate(prompt, image=crop)
        choice = choice_from_text(text, tuple(candidate.action_id for candidate in candidates))
        probabilities = {
            candidate.action_id: (1.0 if candidate.action_id == choice else 0.0)
            for candidate in candidates
        }
        snap_id, episode_gen = reactive_episode_meta(snapshot)
        return StructuredDecision(
            candidate_id=choice,
            confidence=1.0,
            probabilities=probabilities,
            decided_at=time.monotonic(),
            episode_generation=episode_gen,
            snapshot_id=snap_id,
            directive_revision=directive.revision,
            catalog_revision=self.catalog_revision,
        )

    def _generate(self, prompt: str, *, image: Image.Image | None = None) -> str:
        import torch

        if image is None:
            content: str | list[object] = prompt
        else:
            content = [{"type": "image", "image": image}, {"type": "text", "text": prompt}]
        messages = [{"role": "user", "content": content}]
        inputs = self._processor.apply_chat_template(  # type: ignore[attr-defined]
            messages,
            add_generation_prompt=True,
            tokenize=True,
            return_dict=True,
            return_tensors="pt",
        )
        inputs = {key: value.to(self._model.device) for key, value in inputs.items()}  # type: ignore[attr-defined]
        with torch.inference_mode():
            output = self._model.generate(  # type: ignore[attr-defined]
                **inputs,
                max_new_tokens=32,
                do_sample=False,
            )
        prompt_len = inputs["input_ids"].shape[-1]
        decoded = self._processor.batch_decode(  # type: ignore[attr-defined]
            output[:, prompt_len:],
            skip_special_tokens=True,
        )
        return decoded[0] if decoded else ""
