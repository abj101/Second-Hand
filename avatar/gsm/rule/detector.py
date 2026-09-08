from __future__ import annotations

import logging
from dataclasses import dataclass
from io import BytesIO
from typing import Any, Protocol

from PIL import Image

log = logging.getLogger("avatar.gsm.detector")

DEFAULT_GSM_DINO_MODEL = "IDEA-Research/grounding-dino-tiny"
DINO_BOX_THRESHOLD = 0.28
DINO_TEXT_THRESHOLD = 0.22


@dataclass(frozen=True)
class DetectedBox:
    label: str
    x: float
    y: float
    w: float = 0.1
    h: float = 0.1
    score: float = 1.0
    tint: tuple[int, int, int] | None = None

    @property
    def cx(self) -> float:
        return self.x + self.w / 2.0


class Detector(Protocol):
    family: str

    def detect(self, frame: Any, labels: list[str]) -> list[DetectedBox]: ...


def frame_to_image(frame: Any) -> Image.Image | None:
    if frame is None:
        return None
    if isinstance(frame, Image.Image):
        return frame.convert("RGB")
    data = getattr(frame, "data", None)
    if not data:
        return None
    try:
        return Image.open(BytesIO(data)).convert("RGB")
    except Exception:  # noqa: BLE001 — GSM must tolerate a dummy grab
        return None


class FakeDetector:
    """Deterministic boxes for tests. No torch. Never the live default."""

    family = "fake"

    def __init__(self, boxes: list[DetectedBox] | None = None) -> None:
        self.boxes = boxes or [
            DetectedBox(label="T tetromino", x=0.15, y=0.2, w=0.12, h=0.12),
        ]

    def detect(self, frame: Any, labels: list[str]) -> list[DetectedBox]:
        _ = frame
        allowed = {lab.lower() for lab in labels} if labels else None
        if allowed is None:
            return list(self.boxes)
        return [b for b in self.boxes if b.label.lower() in allowed]


class PixelBlobDetector:
    """Screenshot-only Tetris piece finder. state_source=pixels, not DINO.

    Quantizes the board crop to 10×20, finds a 1–4 cell component near the top,
    and returns one box. No game API. Ghost/HUD pixels are usually too dim.
    """

    family = "pixel-blob"
    COLS = 10
    ROWS = 20

    def __init__(self, *, brightness: int = 70) -> None:
        self.brightness = brightness

    def detect(self, frame: Any, labels: list[str]) -> list[DetectedBox]:
        image = frame_to_image(frame)
        if image is None:
            return []
        occ = self._occupancy(image)
        cells = self._active_piece(occ)
        if not cells:
            return []
        rows = [r for r, _c in cells]
        cols = [c for _r, c in cells]
        r0, r1 = min(rows), max(rows)
        c0, c1 = min(cols), max(cols)
        label = "T tetromino"
        tet = next((lab for lab in labels if "tetromino" in lab.lower()), None)
        if tet:
            label = tet
        elif labels:
            label = labels[0]
        return [
            DetectedBox(
                label=label,
                x=c0 / self.COLS,
                y=r0 / self.ROWS,
                w=(c1 - c0 + 1) / self.COLS,
                h=(r1 - r0 + 1) / self.ROWS,
                score=1.0,
            )
        ]

    def _occupancy(self, image: Image.Image) -> list[list[bool]]:
        pixels = image.load()
        width, height = image.size
        occ = [[False] * self.COLS for _ in range(self.ROWS)]
        for row in range(self.ROWS):
            for col in range(self.COLS):
                cx = int((col + 0.5) * width / self.COLS)
                cy = int((row + 0.5) * height / self.ROWS)
                occ[row][col] = self._cell_lit(pixels, cx, cy, width, height)
        return occ

    def _cell_lit(self, pixels: Any, cx: int, cy: int, width: int, height: int) -> bool:
        total = 0
        n = 0
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                x = min(width - 1, max(0, cx + dx))
                y = min(height - 1, max(0, cy + dy))
                r, g, b = pixels[x, y][:3]
                total += max(r, g, b)
                n += 1
        return (total / max(n, 1)) >= self.brightness

    def _active_piece(self, occ: list[list[bool]]) -> list[tuple[int, int]] | None:
        comps = _connected_components(occ)
        small = [c for c in comps if 1 <= len(c) <= 4]
        if small:
            return min(small, key=lambda c: sum(r for r, _col in c) / len(c))
        if not comps:
            return None
        top = min(comps, key=lambda c: min(r for r, _col in c))
        return sorted(top, key=lambda rc: rc[0])[:4]


def _connected_components(occ: list[list[bool]]) -> list[list[tuple[int, int]]]:
    rows = len(occ)
    cols = len(occ[0]) if occ else 0
    seen = [[False] * cols for _ in range(rows)]
    out: list[list[tuple[int, int]]] = []
    for r in range(rows):
        for c in range(cols):
            if not occ[r][c] or seen[r][c]:
                continue
            stack = [(r, c)]
            seen[r][c] = True
            cells: list[tuple[int, int]] = []
            while stack:
                cr, cc = stack.pop()
                cells.append((cr, cc))
                for nr, nc in ((cr - 1, cc), (cr + 1, cc), (cr, cc - 1), (cr, cc + 1)):
                    if nr < 0 or nc < 0 or nr >= rows or nc >= cols:
                        continue
                    if occ[nr][nc] and not seen[nr][nc]:
                        seen[nr][nc] = True
                        stack.append((nr, nc))
            out.append(cells)
    return out


def _period_caption(labels: list[str]) -> str:
    parts = [str(lab).strip().rstrip(".") for lab in labels if lab and str(lab).strip()]
    if not parts:
        return ""
    return ". ".join(parts) + "."


def _xyxy_to_box(xyxy: Any, img_w: float, img_h: float, label: str, score: float) -> DetectedBox:
    coords = xyxy.tolist() if hasattr(xyxy, "tolist") else list(xyxy)
    x0, y0, x1, y1 = (float(c) for c in coords[:4])
    width = max(img_w, 1.0)
    height = max(img_h, 1.0)
    x = min(max(x0 / width, 0.0), 1.0)
    y = min(max(y0 / height, 0.0), 1.0)
    w = min(max((x1 - x0) / width, 0.0), 1.0 - x)
    h = min(max((y1 - y0) / height, 0.0), 1.0 - y)
    return DetectedBox(label=label, x=x, y=y, w=w, h=h, score=float(score))


class _GroundingDinoImpl:
    """Live Grounding DINO forward pass. Torch is imported only here, not at module load."""

    def __init__(self, processor: Any, model: Any, device: str) -> None:
        self.processor = processor
        self.model = model
        self.device = device

    def detect(
        self,
        frame: Any,
        labels: list[str],
        *,
        box_threshold: float | None = None,
        text_threshold: float | None = None,
    ) -> list[DetectedBox]:
        image = frame_to_image(frame)
        caption = _period_caption(labels)
        if image is None or not caption:
            return []
        import torch

        inputs = self.processor(images=image, text=caption, return_tensors="pt")
        if hasattr(inputs, "to"):
            inputs = inputs.to(self.device)
        with torch.no_grad():
            outputs = self.model(**inputs)
        box_thr = DINO_BOX_THRESHOLD if box_threshold is None else float(box_threshold)
        text_thr = DINO_TEXT_THRESHOLD if text_threshold is None else float(text_threshold)
        results = self._post_process(
            outputs, inputs, image, labels, box_threshold=box_thr, text_threshold=text_thr
        )
        result = results[0] if results else {}
        boxes_t = result.get("boxes")
        if boxes_t is None:
            boxes_t = []
        scores = result.get("scores")
        if scores is None:
            scores = []
        text_labels = result.get("text_labels")
        if text_labels is None:
            text_labels = result.get("labels")
        if text_labels is None:
            text_labels = []
        img_w, img_h = image.size
        out: list[DetectedBox] = []
        for i, xyxy in enumerate(boxes_t):
            raw_label = text_labels[i] if i < len(text_labels) else (labels[0] if labels else "object")
            label = str(raw_label)
            raw_score = scores[i] if i < len(scores) else 1.0
            score = float(raw_score.item() if hasattr(raw_score, "item") else raw_score)
            out.append(_xyxy_to_box(xyxy, float(img_w), float(img_h), label, score))
        return out

    def _post_process(
        self,
        outputs: Any,
        inputs: Any,
        image: Image.Image,
        labels: list[str],
        *,
        box_threshold: float,
        text_threshold: float,
    ) -> list[dict[str, Any]]:
        input_ids = getattr(inputs, "input_ids", None)
        if input_ids is None and isinstance(inputs, dict):
            input_ids = inputs.get("input_ids")
        target_sizes = [image.size[::-1]]
        try:
            return self.processor.post_process_grounded_object_detection(
                outputs,
                input_ids,
                box_threshold=box_threshold,
                text_threshold=text_threshold,
                target_sizes=target_sizes,
            )
        except TypeError:
            return self.processor.post_process_grounded_object_detection(
                outputs,
                input_ids=input_ids,
                threshold=box_threshold,
                text_threshold=text_threshold,
                target_sizes=target_sizes,
                text_labels=[list(labels)],
            )


class OpenVocabDetector:
    """Optional Grounding DINO / YOLO-World. Missing deps → empty boxes."""

    family = "open-vocab"

    def __init__(self, model_id: str = "", device: str = "cpu") -> None:
        self.model_id = model_id
        self.device = device or "cpu"
        self.detector_device = self.device
        self.last_error = ""
        self._impl = None
        self._processor = None
        self._model = None
        self.family = "unavailable"
        self._try_load()

    def _try_load(self) -> None:
        # Live weights are optional (avatar[gsm]). Do not import torch at module load.
        try:
            from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor

            model_id = self.model_id or DEFAULT_GSM_DINO_MODEL
            processor = AutoProcessor.from_pretrained(model_id)
            model = AutoModelForZeroShotObjectDetection.from_pretrained(model_id).to(self.device)
            model.eval()
            self._processor = processor
            self._model = model
            self._impl = _GroundingDinoImpl(processor, model, self.device)
            self.family = "grounding-dino"
            self.last_error = ""
            log.info("Loaded Grounding DINO %s on %s", model_id, self.device)
        except Exception as exc:  # noqa: BLE001 — missing transformers/weights is an honest unavailable tag
            self._impl = None
            self._processor = None
            self._model = None
            self.family = "unavailable"
            self.last_error = f"{type(exc).__name__}: {exc}"
            log.warning("Grounding DINO unavailable on %s: %s", self.device, self.last_error)

    def detect(
        self,
        frame: Any,
        labels: list[str],
        *,
        box_threshold: float | None = None,
        text_threshold: float | None = None,
    ) -> list[DetectedBox]:
        if self._impl is None:
            return []
        try:
            return list(
                self._impl.detect(
                    frame,
                    labels,
                    box_threshold=box_threshold,
                    text_threshold=text_threshold,
                )
            )
        except Exception as exc:  # noqa: BLE001 — GSM must still emit empty boxes on a failed forward
            log.warning("Grounding DINO forward failed: %s", exc)
            return []


class FallbackDetector:
    """Primary detector; occupancy fallback when primary returns nothing."""

    def __init__(self, primary: Detector, fallback: Detector) -> None:
        self.primary = primary
        self.fallback = fallback
        self.family = getattr(primary, "family", "") or getattr(fallback, "family", "")

    def detect(self, frame: Any, labels: list[str]) -> list[DetectedBox]:
        boxes = self.primary.detect(frame, labels)
        if boxes:
            return boxes
        return self.fallback.detect(frame, labels)


class G2048BlobDetector:
    """4×4 occupancy from the 2048 board crop. Screenshot-only, not a game API."""

    family = "pixel-blob"
    COLS = 4
    ROWS = 4
    MAX_BOXES = 16

    def detect(self, frame: Any, labels: list[str]) -> list[DetectedBox]:
        image = frame_to_image(frame)
        if image is None:
            return []
        pixels = image.load()
        width, height = image.size
        label = next((lab for lab in labels if lab), "tile")
        inset = max(1, min(width // (self.COLS * 6), height // (self.ROWS * 6)))
        boxes: list[DetectedBox] = []
        for row in range(self.ROWS):
            for col in range(self.COLS):
                cx = int((col + 0.5) * width / self.COLS)
                cy = int((row + 0.5) * height / self.ROWS)
                rgb = self._cell_rgb(pixels, cx, cy, width, height, inset)
                if rgb is None:
                    continue
                r, g, b = rgb
                boxes.append(
                    DetectedBox(
                        label=label,
                        x=col / self.COLS,
                        y=row / self.ROWS,
                        w=1.0 / self.COLS,
                        h=1.0 / self.ROWS,
                        score=1.0,
                        tint=(r // 16, g // 16, b // 16),
                    )
                )
        return boxes[: self.MAX_BOXES]

    def _tile_lit(
        self, pixels: Any, cx: int, cy: int, width: int, height: int, inset: int = 2
    ) -> bool:
        return self._cell_rgb(pixels, cx, cy, width, height, inset) is not None

    def _cell_rgb(
        self, pixels: Any, cx: int, cy: int, width: int, height: int, inset: int = 2
    ) -> tuple[int, int, int] | None:
        r_sum = g_sum = b_sum = n = 0
        for dy in (-inset, 0, inset):
            for dx in (-inset, 0, inset):
                x = min(max(cx + dx, 0), width - 1)
                y = min(max(cy + dy, 0), height - 1)
                r, g, b = pixels[x, y][:3]
                r_sum += r
                g_sum += g
                b_sum += b
                n += 1
        if n == 0:
            return None
        r, g, b = r_sum / n, g_sum / n, b_sum / n
        chroma = max(r, g, b) - min(r, g, b)
        brightness = (r + g + b) / 3.0
        # Page/header #faf8ef is very light and nearly gray. Empty cells are
        # translucent #eee4da over grid #bbada0 (~192 brightness, low chroma).
        # Occupied: bright 2/4, or darker/saturated 8+.
        if brightness > 230 and chroma < 16:
            return None
        if 155 <= brightness <= 205 and chroma < 40:
            return None
        if brightness > 210 or brightness < 145 or chroma > 50:
            return (int(r), int(g), int(b))
        return None


def default_detector(spec: Any | None = None) -> Detector:
    """Pixel-blob by default. Fake is tests-only. Missing DINO weights fall back to blob CV."""
    family = ""
    fixture = ""
    model_id = ""
    device = "cpu"
    if spec is not None:
        family = str(getattr(spec, "detector_family", "") or "")
        fixture = str(getattr(spec, "fixture", "") or "")
        model_id = str(getattr(spec, "model_id", "") or "")
        device = str(getattr(spec, "detector_device", "") or getattr(spec, "device", "") or "cpu") or "cpu"
    if family == "fake":
        return FakeDetector()
    if family in {"open-vocab", "grounding-dino", "yolo-world"}:
        det = OpenVocabDetector(model_id=model_id, device=device)
        if det.family != "unavailable":
            if fixture == "2048":
                return FallbackDetector(det, G2048BlobDetector())
            return FallbackDetector(det, PixelBlobDetector())
        if fixture == "2048":
            return G2048BlobDetector()
        return PixelBlobDetector()
    if fixture == "2048":
        return G2048BlobDetector()
    return PixelBlobDetector()
