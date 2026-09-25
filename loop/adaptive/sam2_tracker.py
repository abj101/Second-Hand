"""SAM 2 mask propagation. Proposals come from YOLO-World with tracking off."""

from __future__ import annotations

import time

from PIL import Image

from loop.adaptive.evidence import iou
from loop.adaptive.tracker import OCR_TRACK_IOU, TrackHit, TrackMemory, detections_to_raw

_SAM_WEIGHTS = "sam2_s.pt"


def sam2_available() -> bool:
    try:
        import torch
    except Exception:
        return False
    return bool(torch.cuda.is_available())


class Sam2Tracker:
    def __init__(self, prompts: list[str], *, proposer=None, propagator=None) -> None:
        self.prompts = list(prompts)
        self._proposer = proposer
        self._propagator = propagator
        self._yolo = None
        self._sam = None
        self._mem = TrackMemory()
        self.load_ms = 0.0
        self.associator = "stub" if propagator is not None else "sam2"
        self.status = "ok"
        if proposer is not None or propagator is not None:
            return
        if not self.prompts:
            return
        if not sam2_available():
            self.status = "unavailable"
            self.associator = "none"
            return
        self._load()

    def _load(self) -> None:
        started = time.perf_counter()
        try:
            from ultralytics import SAM

            from loop.adaptive.yolo_world import YoloWorldTracker

            yolo = YoloWorldTracker(self.prompts)
            if yolo.status != "ok":
                raise RuntimeError("yolo-world unavailable")
            self._yolo = yolo
            self._sam = SAM(_SAM_WEIGHTS)
            self.status = "ok"
            self.associator = "sam2"
        except Exception:
            self._yolo = None
            self._sam = None
            self.status = "unavailable"
            self.associator = "none"
        self.load_ms = (time.perf_counter() - started) * 1000.0

    def clear_live(self) -> None:
        self._mem.clear_live()

    def reset(self) -> None:
        self._mem.reset()
        if self._yolo is not None:
            self._yolo.reset()

    def update(self, image: Image.Image) -> tuple[TrackHit, ...]:
        if self.status != "ok":
            return ()
        proposals = self._propose(image)
        boxes = _with_live(proposals, self._mem.live)
        if self._propagator is not None:
            refined = list(self._propagator(image, boxes))
        elif self._sam is not None and boxes:
            refined = _mask_boxes(self._sam, image, boxes)
        else:
            refined = []
        # Association is ours. Drop proposer ids so BoT-SORT cannot leak in.
        for row in refined:
            row.pop("track_id", None)
        return self._mem.reconcile(detections_to_raw(image, refined))

    def _propose(self, image: Image.Image) -> list[dict]:
        if self._proposer is not None:
            return [dict(row) for row in self._proposer(image)]
        if self._yolo is None:
            return []
        rows = self._yolo.detect(image)
        for row in rows:
            row.pop("track_id", None)
        return rows


def _with_live(proposals: list[dict], live: dict) -> list[dict]:
    boxes = [dict(row) for row in proposals]
    for _tid, (bbox, _app, label) in live.items():
        if any(iou(bbox, tuple(row["bbox"])) >= OCR_TRACK_IOU for row in boxes):
            continue
        boxes.append({"bbox": bbox, "label": label, "conf": LABEL_LIVE})
    return boxes


LABEL_LIVE = 0.5


def _mask_boxes(model, image: Image.Image, boxes: list[dict]) -> list[dict]:
    width, height = image.size
    pixels = []
    kept = []
    for row in boxes:
        x0, y0, x1, y1 = row["bbox"]
        pixels.append([x0 * width, y0 * height, x1 * width, y1 * height])
        kept.append(row)
    results = model(image, bboxes=pixels, verbose=False)
    masks = getattr(results[0], "masks", None) if results else None
    data = None if masks is None else getattr(masks, "data", None)
    if data is None:
        return kept
    import numpy as np

    array = data.detach().cpu().numpy() if hasattr(data, "detach") else np.asarray(data)
    out = []
    for index, row in enumerate(kept):
        if index >= len(array):
            out.append(row)
            continue
        ys, xs = np.where(array[index] > 0.5)
        if len(xs) == 0:
            out.append(row)
            continue
        bbox = (
            float(xs.min()) / width,
            float(ys.min()) / height,
            float(xs.max() + 1) / width,
            float(ys.max() + 1) / height,
        )
        copied = dict(row)
        copied["bbox"] = bbox
        out.append(copied)
    return out
