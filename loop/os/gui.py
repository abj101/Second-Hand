from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image

from loop.config import project_root

GPA_REPO = "Salesforce/GPA-GUI-Detector"
GPA_FILE = "model.pt"
GROUNDCUA_REPO = "laywens/uitag-yolo11s-ui-detect-v1"
GROUNDCUA_FILE = "yolo-ui.pt"

CONF = 0.15
IMGSZ = 640
IOU = 0.7

_MODELS: dict[str, dict[str, str]] = {
    "gpa": {
        "repo": GPA_REPO,
        "file": GPA_FILE,
        "dir": "gpa-gui",
        "family": "gpa",
    },
    "groundcua": {
        "repo": GROUNDCUA_REPO,
        "file": GROUNDCUA_FILE,
        "dir": "groundcua",
        "family": "groundcua",
    },
}


@dataclass
class GuiBox:
    x0: float
    y0: float
    x1: float
    y1: float
    score: float = 0.0
    label: str = "icon"


@dataclass
class GuiDetector:
    family: str
    device: str
    path: Path | None = None
    last_error: str = ""
    model: Any = field(default=None, repr=False)

    @property
    def ok(self) -> bool:
        return self.model is not None and not self.last_error


_STATE: GuiDetector | None = None


def weights_dir(model: str = "gpa") -> Path:
    spec = _MODELS.get(model) or _MODELS["gpa"]
    return project_root() / "data" / "loop" / "weights" / spec["dir"]


def weight_path(model: str = "gpa") -> Path:
    spec = _MODELS.get(model) or _MODELS["gpa"]
    return weights_dir(model) / spec["file"]


def gui_status() -> str:
    det = _STATE
    if det is None:
        return "gui=off"
    if not det.ok:
        err = det.last_error.strip()
        return f"gui=unavailable {err}".rstrip()
    return f"gui={det.family} device={det.device}"


def configure_gui(
    *,
    enabled: bool = True,
    device: str = "cpu",
    model: str = "gpa",
    download: bool = False,
) -> str:
    """Session-level load. Never raises; missing extras/weights → unavailable."""
    global _STATE
    if not enabled:
        _STATE = None
        return "gui=off"
    _STATE = _load_detector(device=device or "cpu", model=model or "gpa", download=download)
    return gui_status()


def detect_gui_boxes(image: Image.Image) -> list[GuiBox]:
    det = _STATE
    if det is None or not det.ok or image is None:
        return []
    try:
        return _predict(det, image)
    except Exception as exc:  # noqa: BLE001 — overlay must never abort collect
        det.last_error = f"{type(exc).__name__}: {exc}"
        det.model = None
        return []


def detect_gui_marks(image: Image.Image, *, cap: int = 16) -> list[Any]:
    """AimMark list. Lazy-imports AimMark to avoid an import cycle at module load."""
    from loop.os.aim import AimMark, _CV_MAX_EDGE

    out: list[Any] = []
    for box in detect_gui_boxes(image):
        if (box.x1 - box.x0) > _CV_MAX_EDGE or (box.y1 - box.y0) > _CV_MAX_EDGE:
            continue
        x = float(max(0.0, min(1.0, (box.x0 + box.x1) * 0.5)))
        y = float(max(0.0, min(1.0, (box.y0 + box.y1) * 0.5)))
        label = (box.label or "icon").strip() or "icon"
        out.append(
            AimMark(
                id=len(out) + 1,
                x=x,
                y=y,
                x0=float(max(0.0, min(1.0, box.x0))),
                y0=float(max(0.0, min(1.0, box.y0))),
                x1=float(max(0.0, min(1.0, box.x1))),
                y1=float(max(0.0, min(1.0, box.y1))),
                label=label,
                score=float(box.score),
                source="gui",
            )
        )
        if len(out) >= cap:
            break
    return out


def _load_detector(*, device: str, model: str, download: bool) -> GuiDetector:
    key = (model or "gpa").strip().lower()
    if key not in _MODELS:
        key = "gpa"
    spec = _MODELS[key]
    dest = weight_path(key)
    if not dest.is_file():
        if not download:
            return GuiDetector(
                family="unavailable",
                device=device,
                path=dest,
                last_error=f"missing {dest}",
            )
        err = _download_weights(spec["repo"], spec["file"], dest)
        if err:
            return GuiDetector(family="unavailable", device=device, path=dest, last_error=err)
    try:
        from ultralytics import YOLO
    except Exception as exc:  # noqa: BLE001
        return GuiDetector(
            family="unavailable",
            device=device,
            path=dest,
            last_error=f"{type(exc).__name__}: {exc}",
        )
    try:
        loaded = YOLO(str(dest))
    except Exception as exc:  # noqa: BLE001
        return GuiDetector(
            family="unavailable",
            device=device,
            path=dest,
            last_error=f"{type(exc).__name__}: {exc}",
        )
    return GuiDetector(family=spec["family"], device=device, path=dest, model=loaded)


def _download_weights(repo: str, filename: str, dest: Path) -> str:
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        from huggingface_hub import hf_hub_download
    except Exception as exc:  # noqa: BLE001
        return f"{type(exc).__name__}: {exc}"
    try:
        cached = hf_hub_download(repo_id=repo, filename=filename)
        src = Path(cached)
        if src.resolve() != dest.resolve():
            dest.write_bytes(src.read_bytes())
    except Exception as exc:  # noqa: BLE001
        return f"{type(exc).__name__}: {exc}"
    return ""


def _predict(det: GuiDetector, image: Image.Image) -> list[GuiBox]:
    import numpy as np

    rgb = image.convert("RGB")
    w, h = rgb.size
    if w < 8 or h < 8:
        return []
    device = "cpu" if (det.device or "cpu").strip().lower() != "cuda" else 0
    results = det.model.predict(
        source=np.asarray(rgb),
        conf=CONF,
        imgsz=IMGSZ,
        iou=IOU,
        device=device,
        verbose=False,
    )
    if not results:
        return []
    hit = results[0]
    boxes = getattr(hit, "boxes", None)
    if boxes is None or len(boxes) == 0:
        return []
    names = getattr(hit, "names", None) or {}
    xyxy = boxes.xyxy
    confs = boxes.conf
    clss = getattr(boxes, "cls", None)
    if hasattr(xyxy, "cpu"):
        xyxy = xyxy.cpu().numpy()
        confs = confs.cpu().numpy()
        if clss is not None:
            clss = clss.cpu().numpy()
    out: list[GuiBox] = []
    for i, row in enumerate(xyxy):
        x0, y0, x1, y1 = (float(row[0]), float(row[1]), float(row[2]), float(row[3]))
        score = float(confs[i]) if confs is not None else 0.0
        label = "icon"
        if clss is not None:
            cls_id = int(clss[i])
            raw = names.get(cls_id) if isinstance(names, dict) else None
            if raw:
                token = str(raw).strip()
                if token and token.lower() not in {"object", "item", "0"}:
                    label = token
        out.append(
            GuiBox(
                x0=x0 / w,
                y0=y0 / h,
                x1=x1 / w,
                y1=y1 / h,
                score=score,
                label=label,
            )
        )
    out.sort(key=lambda b: -b.score)
    return out
