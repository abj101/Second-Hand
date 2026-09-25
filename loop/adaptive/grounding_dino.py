"""Grounding DINO zero-shot proposals. Current-frame only; no track memory."""

from __future__ import annotations

import time
from typing import Any

from PIL import Image

_TINY_ID = "IDEA-Research/grounding-dino-tiny"
_BASE_ID = "IDEA-Research/grounding-dino-base"
_VARIANT_IDS = {"tiny": _TINY_ID, "base": _BASE_ID}

CACHED_TINY_SHA256 = "1a2412ef99bd74bcd3c2a246fa1e48581f8889a1300c9051974741314fc042f3"
CACHED_BASE_SHA256 = "5548f844c928c4b6f411fa8cbcc2bfa8dbbba437cb1d513975519f93c2a9ed21"

_THRESHOLD = 0.28
_TEXT_THRESHOLD = 0.22
_FULLSCREEN_AREA = 0.95


class GroundingDinoTracker:
    def __init__(
        self,
        prompts: list[str],
        *,
        variant: str = "tiny",
        model_id: str | None = None,
        detector=None,
        loader=None,
    ) -> None:
        self.prompts = list(prompts)
        self.variant = variant if variant in _VARIANT_IDS else "tiny"
        self.model_id = model_id or _VARIANT_IDS[self.variant]
        self.model_id_loaded: str | None = None
        self._detector = detector
        self._loader = loader
        self._processor = None
        self._model = None
        self._device = None
        self.load_ms = 0.0
        self.last_infer_ms = 0.0
        self.status = "ok"
        self.diagnostics: dict[str, int] = {
            "rejected_malformed": 0,
            "rejected_off_frame": 0,
            "rejected_fullscreen": 0,
        }
        if detector is not None or not self.prompts:
            return
        self._load()

    @property
    def source(self) -> str:
        return "grounding-dino-base" if self.variant == "base" else "grounding-dino"

    def _load(self) -> None:
        started = time.perf_counter()
        try:
            if self._loader is not None:
                bundle = self._loader(self.model_id, self.prompts)
            else:
                bundle = _default_loader(self.model_id, self.prompts)
            self._processor, self._model, self._device = bundle
            self.model_id_loaded = self.model_id
            self.status = "ok"
        except Exception:
            self._processor = None
            self._model = None
            self._device = None
            self.model_id_loaded = None
            self.status = "unavailable"
        self.load_ms = (time.perf_counter() - started) * 1000.0

    def propose(self, image: Image.Image, *, frame_id: str = "") -> list[dict[str, Any]]:
        if self.status != "ok":
            return []
        if not self.prompts and self._detector is None:
            return []
        started = time.perf_counter()
        try:
            if self._detector is not None:
                rows = list(self._detector(image))
            else:
                rows = _infer(self._processor, self._model, self._device, image, self.prompts)
        except Exception:
            self.status = "unavailable"
            self.last_infer_ms = (time.perf_counter() - started) * 1000.0
            return []
        self.last_infer_ms = (time.perf_counter() - started) * 1000.0
        return _rows_to_proposals(rows, frame_id=frame_id, source=self.source, diagnostics=self.diagnostics)


def _default_loader(model_id: str, prompts: list[str]):
    import torch
    from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor

    device = "cuda" if torch.cuda.is_available() else "cpu"
    processor = AutoProcessor.from_pretrained(model_id, local_files_only=True)
    model = AutoModelForZeroShotObjectDetection.from_pretrained(
        model_id, local_files_only=True
    ).to(device).eval()
    return processor, model, device


def _infer(processor, model, device, image: Image.Image, prompts: list[str]) -> list[dict]:
    import torch

    text = ". ".join(prompts) + "."
    inputs = processor(images=image, text=text, return_tensors="pt").to(device)
    with torch.inference_mode():
        output = model(**inputs)
    result = processor.post_process_grounded_object_detection(
        output,
        inputs.input_ids,
        threshold=_THRESHOLD,
        text_threshold=_TEXT_THRESHOLD,
        target_sizes=[image.size[::-1]],
    )[0]
    labels = result.get("text_labels", result.get("labels"))
    rows = []
    width, height = image.size
    for box, score, label in zip(result["boxes"], result["scores"], labels):
        x0, y0, x1, y1 = box.tolist()
        rows.append(
            {
                "bbox": (x0 / width, y0 / height, x1 / width, y1 / height),
                "label": str(label),
                "conf": float(score),
            }
        )
    return rows


def _rows_to_proposals(
    rows: list[dict],
    *,
    frame_id: str,
    source: str,
    diagnostics: dict[str, int],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        bbox = row.get("bbox")
        if not _accept_bbox(bbox, diagnostics):
            continue
        out.append(
            {
                "bbox": tuple(float(v) for v in bbox),
                "role": "world",
                "source": source,
                "class_hint": str(row.get("label") or row.get("class_hint") or ""),
                "confidence": float(row.get("conf") if row.get("conf") is not None else row.get("confidence") or 0.0),
                "frame_id": frame_id,
            }
        )
    return out


def _accept_bbox(bbox, diagnostics: dict[str, int]) -> bool:
    if not isinstance(bbox, (tuple, list)) or len(bbox) != 4:
        diagnostics["rejected_malformed"] += 1
        return False
    try:
        x0, y0, x1, y1 = (float(v) for v in bbox)
    except (TypeError, ValueError):
        diagnostics["rejected_malformed"] += 1
        return False
    if x1 <= x0 or y1 <= y0:
        diagnostics["rejected_malformed"] += 1
        return False
    if x0 < 0.0 or y0 < 0.0 or x1 > 1.0 or y1 > 1.0:
        diagnostics["rejected_off_frame"] += 1
        return False
    if (x1 - x0) * (y1 - y0) >= _FULLSCREEN_AREA:
        diagnostics["rejected_fullscreen"] += 1
        return False
    return True
