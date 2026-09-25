"""YOLO-World + BoT-SORT. Imported only when --tracker yolo-world is set."""

from __future__ import annotations

import time

from PIL import Image

from loop.adaptive.tracker import LABEL_CONFIDENCE, TrackHit, TrackMemory, detections_to_raw

_WEIGHTS = "yolov8s-worldv2.pt"


class YoloWorldTracker:
    def __init__(self, prompts: list[str], *, detector=None, loader=None) -> None:
        self.prompts = list(prompts)
        self._detector = detector
        self._loader = loader
        self._model = None
        self._mem = TrackMemory()
        self.load_ms = 0.0
        self.associator = "stub" if detector is not None else "none"
        self.status = "ok"
        if detector is not None or not self.prompts:
            return
        self._load()

    def _load(self) -> None:
        started = time.perf_counter()
        try:
            if self._loader is not None:
                self._model = self._loader(self.prompts)
            else:
                from ultralytics import YOLO

                model = YOLO(_WEIGHTS)
                model.set_classes(self.prompts)
                self._model = model
            self.associator = "botsort"
            self.status = "ok"
        except Exception:
            self._model = None
            self.status = "unavailable"
            self.associator = "none"
        self.load_ms = (time.perf_counter() - started) * 1000.0

    def clear_live(self) -> None:
        self._mem.clear_live()
        _reset_ultralytics(self._model)

    def reset(self) -> None:
        self._mem.reset()
        _reset_ultralytics(self._model)

    def detect(self, image: Image.Image) -> list[dict]:
        """Boxes only. SAM 2 uses this so both arms share proposals."""

        if self.status != "ok" or (not self.prompts and self._detector is None):
            return []
        if self._detector is not None:
            return [_blank_low(dict(row)) for row in self._detector(image)]
        return _predict(self._model, image, self.prompts)

    def update(self, image: Image.Image) -> tuple[TrackHit, ...]:
        if self.status != "ok":
            return ()
        if self._detector is not None:
            return self._mem.reconcile(detections_to_raw(image, list(self._detector(image))))
        if not self.prompts or self._model is None:
            return ()
        try:
            detections = _track(self, image)
        except Exception:
            self.status = "unavailable"
            self.associator = "none"
            return ()
        return self._mem.reconcile(detections_to_raw(image, detections))


def _blank_low(row: dict) -> dict:
    if float(row.get("conf") or 0.0) < LABEL_CONFIDENCE:
        row["label"] = ""
    return row


def _track(tracker: YoloWorldTracker, image: Image.Image) -> list[dict]:
    assert tracker._model is not None
    if tracker.associator != "bytetrack":
        try:
            results = tracker._model.track(
                image, persist=True, tracker="botsort.yaml", verbose=False, conf=0.05
            )
            tracker.associator = "botsort"
            return _rows(results, tracker.prompts, image.size)
        except Exception:
            tracker.associator = "bytetrack"
    results = tracker._model.track(
        image, persist=True, tracker="bytetrack.yaml", verbose=False, conf=0.05
    )
    return _rows(results, tracker.prompts, image.size)


def _predict(model, image: Image.Image, prompts: list[str]) -> list[dict]:
    results = model.predict(image, verbose=False, conf=0.05)
    return _rows(results, prompts, image.size)


def _rows(results, prompts: list[str], size: tuple[int, int]) -> list[dict]:
    if not results:
        return []
    boxes = getattr(results[0], "boxes", None)
    if boxes is None or boxes.xyxy is None:
        return []
    width, height = size
    ids = boxes.id
    clss = boxes.cls
    confs = boxes.conf
    out = []
    for i, xyxy in enumerate(boxes.xyxy.tolist()):
        x0, y0, x1, y1 = xyxy
        conf = float(confs[i]) if confs is not None else 0.0
        label = ""
        if clss is not None and conf >= LABEL_CONFIDENCE:
            index = int(clss[i])
            if 0 <= index < len(prompts):
                label = prompts[index]
        row = {
            "bbox": (x0 / width, y0 / height, x1 / width, y1 / height),
            "label": label,
            "conf": conf,
        }
        if ids is not None:
            row["track_id"] = str(int(ids[i]))
        out.append(row)
    return out


def _reset_ultralytics(model) -> None:
    predictor = getattr(model, "predictor", None)
    if predictor is not None:
        predictor.trackers = []
