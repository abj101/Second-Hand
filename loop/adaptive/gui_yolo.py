"""GPA GUI YOLO UI proposals. Optional; no auto-download."""

from __future__ import annotations

import math
import time
from pathlib import Path

from PIL import Image

from loop.adaptive.tracker import clip_bbox

BBox = tuple[float, float, float, float]
FULL_SCREEN_AREA = 0.95
_SOURCE = "gpa-gui"
_WEIGHTS = Path(__file__).resolve().parents[2] / "data" / "loop" / "weights" / "gpa-gui" / "model.pt"
_PREDICT_KW = {"conf": 0.15, "imgsz": 640, "iou": 0.7, "verbose": False}
REJECT_REASONS = ("malformed", "off_frame", "full_screen")


def _empty_counters() -> dict:
    """Counters for a frame the provider actually ran on (zeros are measured)."""
    return {
        "raw": 0,
        "accepted": 0,
        "rejected": dict.fromkeys(REJECT_REASONS, 0),
        "merged": None,  # detector-side NMS drops are not observable here
    }


def _unavailable_counters() -> dict:
    """A frame with no measurement: unavailable, never fabricated zeros."""
    return {"raw": None, "accepted": None, "rejected": None, "merged": None}


def _area(bbox: BBox) -> float:
    x0, y0, x1, y1 = bbox
    return max(0.0, x1 - x0) * max(0.0, y1 - y0)


def validate_bbox(raw) -> tuple[BBox | None, str | None]:
    if not isinstance(raw, (tuple, list)) or len(raw) != 4:
        return None, "malformed"
    try:
        vals = [float(v) for v in raw]
    except (TypeError, ValueError):
        return None, "malformed"
    if not all(math.isfinite(v) for v in vals):
        return None, "malformed"
    x0, y0, x1, y1 = vals
    if x1 <= x0 or y1 <= y0:
        return None, "malformed"
    if min(x0, y0) < 0.0 or max(x1, y1) > 1.0:
        return None, "off_frame"
    bbox = (x0, y0, x1, y1)
    if _area(bbox) >= FULL_SCREEN_AREA:
        return None, "full_screen"
    clipped = clip_bbox(bbox)
    if clipped is None:
        return None, "malformed"
    return clipped, None


def filter_proposals(
    rows: list[dict],
    *,
    frame_id: str,
    role: str,
    source: str,
) -> tuple[list[dict], dict]:
    counters = _empty_counters()
    out: list[dict] = []
    for row in rows:
        bbox, reason = validate_bbox(row.get("bbox"))
        if reason:
            counters["rejected"][reason] += 1
            continue
        try:
            conf = float(row.get("confidence", row.get("conf", 0.0)))
        except (TypeError, ValueError):
            counters["rejected"]["malformed"] += 1
            continue
        if not math.isfinite(conf):
            counters["rejected"]["malformed"] += 1
            continue
        hint = row.get("class_hint", row.get("label", "icon"))
        out.append(
            {
                "bbox": bbox,
                "role": role,
                "source": source,
                "class_hint": str(hint or "icon"),
                "confidence": conf,
                "frame_id": frame_id,
            }
        )
    counters["raw"] = len(rows)
    counters["accepted"] = len(out)
    return out, counters


class GpaGuiDetector:
    def __init__(
        self,
        *,
        detector=None,
        loader=None,
        weights_path: Path | str | None = None,
    ) -> None:
        self._detector = detector
        self._loader = loader
        self._model = None
        self.weights_path = Path(weights_path or _WEIGHTS)
        self.model_path = ""
        self.model_name = "gpa-gui"
        self.load_ms = 0.0
        self.last_infer_ms = 0.0
        self._predict_device: int | str | None = None
        self.backend = "stub" if detector is not None else "gpa-gui"
        self.fallback = False
        self.status = "ok"
        self.diagnostics: dict = _unavailable_counters()
        if detector is not None:
            self.model_path = "stub"
            return
        self._load()

    @property
    def device(self) -> int | str | None:
        return self._predict_device

    def _provider_fields(self) -> dict:
        if self.backend == "gpa-gui":
            self.fallback = self._predict_device == "cpu" and _cuda()
        return {
            "source": _SOURCE,
            "backend": self.backend,
            "device": self._predict_device,
            "fallback": bool(self.fallback),
        }

    def _unavailable(self, reason: str) -> dict:
        payload = _unavailable_counters()
        payload.update(self._provider_fields())
        payload["available"] = False
        payload["reason"] = reason
        return payload

    def _load(self) -> None:
        started = time.perf_counter()
        try:
            if self._loader is not None:
                self._model = self._loader(self.weights_path)
            else:
                if not self.weights_path.is_file():
                    raise FileNotFoundError(str(self.weights_path))
                from ultralytics import YOLO

                self._model = YOLO(str(self.weights_path))
            self.model_path = str(self.weights_path)
            self.status = "ok"
        except Exception:
            self._model = None
            self.model_path = str(self.weights_path)
            self.status = "unavailable"
        self.load_ms = (time.perf_counter() - started) * 1000.0

    def propose(self, image: Image.Image, *, frame_id: str = "") -> list[dict]:
        if self.status != "ok":
            self.diagnostics = self._unavailable(f"status={self.status}")
            return []
        started = time.perf_counter()
        raw: list[dict] = []
        try:
            if self._detector is not None:
                raw = [dict(row) for row in self._detector(image)]
            elif self._model is not None:
                raw, self._predict_device = _predict_rows(
                    self._model, image, preferred_device=self._predict_device
                )
            else:
                self.status = "unavailable"
                self.diagnostics = self._unavailable("no detector loaded")
                return []
        except Exception:
            # Clear source output on a failed inference: no stale boxes or counts.
            self.status = "unavailable"
            self.diagnostics = self._unavailable("inference failed")
            return []
        finally:
            self.last_infer_ms = (time.perf_counter() - started) * 1000.0
        proposals, counters = filter_proposals(
            raw, frame_id=frame_id, role="ui", source=_SOURCE
        )
        counters.update(self._provider_fields())
        counters["available"] = True
        counters["reason"] = None
        self.diagnostics = counters
        return proposals


def _predict_rows(
    model, image: Image.Image, *, preferred_device: int | str | None = None
) -> tuple[list[dict], int | str]:
    # ponytail: CPU fallback if torchvision was a CPU wheel (no CUDA NMS).
    devices = [preferred_device] if preferred_device is not None else ([0, "cpu"] if _cuda() else ["cpu"])
    results = None
    last: Exception | None = None
    selected: int | str = "cpu"
    for device in devices:
        try:
            results = model.predict(image, device=device, **_PREDICT_KW)
            selected = device
            break
        except NotImplementedError as exc:
            last = exc
            results = None
    if results is None:
        if last is not None:
            raise last
        return [], selected
    if not results:
        return [], selected
    result = results[0]
    boxes = getattr(result, "boxes", None)
    if boxes is None or boxes.xyxy is None:
        return [], selected
    width, height = image.size
    names = getattr(result, "names", {}) or {}
    xyxy = boxes.xyxy.tolist()
    confs = boxes.conf.tolist() if boxes.conf is not None else [0.0] * len(xyxy)
    clss = boxes.cls.tolist() if boxes.cls is not None else [0] * len(xyxy)
    rows: list[dict] = []
    for box, score, cls in zip(xyxy, confs, clss):
        x0, y0, x1, y1 = box
        index = int(cls)
        rows.append(
            {
                "bbox": (x0 / width, y0 / height, x1 / width, y1 / height),
                "class_hint": str(names.get(index, "icon")),
                "conf": float(score),
            }
        )
    return rows, selected


def _cuda() -> bool:
    try:
        import torch
    except Exception:
        return False
    return bool(torch.cuda.is_available())
