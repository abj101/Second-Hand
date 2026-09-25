"""One-process offline smoke check for the lean perception stack.

Loads the normal Qwen reaction policy, the GPA GUI detector and RapidOCR Small in
one process, runs one multi-offer Qwen decision, one GUI inference and one
full-frame OCR read, and reports devices, providers, load/inference times, torch
allocated/reserved peaks, whole-device VRAM and CPU usage.

It runs fully offline: Hugging Face offline flags are forced, GPA loads its local
weight file and the RapidOCR models must already be bundled with the wheel. It
never downloads, calls a cloud service, captures a screen or sends input. Missing
weights or dependencies fail explicitly with a nonzero exit instead of being
reported as success, and a CPU fallback is never labelled CUDA success.

The coordinator runs this on the GPU host; a mocked pass is code review, not
environment qualification.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import json
import math
import os
import subprocess
import sys
import time
from hashlib import sha256
from pathlib import Path

from PIL import Image

OFFLINE_ENV = {
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "HF_HUB_DISABLE_TELEMETRY": "1",
}
# Bundled with the rapidocr 3.9.2 wheel; hashed and staged by scripts/setup_perception.ps1.
OCR_MODELS = (
    "PP-OCRv6_det_small.onnx",
    "PP-OCRv6_rec_small.onnx",
    "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
)
_SETUP_HINT = "install the pinned perception profile: scripts/setup_perception.ps1"


class SmokeError(RuntimeError):
    """An explicit, reportable reason this smoke check cannot qualify."""


def _require(name: str, hint: str) -> object:
    try:
        return importlib.import_module(name)
    except ImportError as exc:
        raise SmokeError(f"missing dependency {name!r} ({hint}): {exc}") from exc


def _version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def force_offline() -> dict[str, str]:
    """Disable Hugging Face downloads/telemetry for this process."""
    os.environ.update(OFFLINE_ENV)
    return dict(OFFLINE_ENV)


def load_frame(frame_path: str) -> Image.Image:
    path = Path(frame_path)
    if not path.is_file():
        raise SmokeError(f"missing frame: {path} (--frame must point at a local image)")
    try:
        with Image.open(path) as handle:
            image = handle.convert("RGB")
    except Exception as exc:
        raise SmokeError(f"frame is not a readable image: {path}: {exc}") from exc
    if image.width < 1 or image.height < 1:
        raise SmokeError(f"frame has no pixels: {path} ({image.width}x{image.height})")
    return image


def frame_meta(frame_path: str, image: Image.Image) -> dict[str, object]:
    return {
        "path": str(Path(frame_path)),
        "size": [image.width, image.height],
        "mode": image.mode,
        # Same convention as loop/adaptive/environment.py::_image_digest.
        "digest": sha256(image.tobytes()).hexdigest(),
    }


def cuda_info() -> dict[str, object]:
    torch = _require("torch", _SETUP_HINT)
    if not torch.cuda.is_available():
        raise SmokeError(
            "CUDA is unavailable. The Qwen loader requires CUDA and the GPA detector would "
            "fall back to CPU, so this frame cannot be qualified on this machine. "
            f"Fix the CUDA install ({_SETUP_HINT}) and re-run."
        )
    index = 0
    supported = getattr(torch.cuda, "is_bf16_supported", None)
    return {
        "kind": "cuda",
        "index": index,
        "name": torch.cuda.get_device_name(index),
        "capability": list(torch.cuda.get_device_capability(index)),
        "torch": torch.__version__,
        "cuda_runtime": torch.version.cuda,
        "bf16_supported": bool(supported()) if callable(supported) else None,
    }


def cuda_memory() -> dict[str, object]:
    torch = importlib.import_module("torch")
    if not torch.cuda.is_available():
        return {"available": False, "reason": "cuda unavailable"}
    try:
        return {
            "available": True,
            "allocated_mib": round(torch.cuda.memory_allocated() / 2**20, 1),
            "reserved_mib": round(torch.cuda.memory_reserved() / 2**20, 1),
            "peak_allocated_mib": round(torch.cuda.max_memory_allocated() / 2**20, 1),
            "peak_reserved_mib": round(torch.cuda.max_memory_reserved() / 2**20, 1),
        }
    except Exception as exc:
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}"}


def reset_peak_memory() -> None:
    torch = importlib.import_module("torch")
    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()


def _vram(used_mib: float, total_mib: float, free_mib: float, source: str) -> dict[str, object]:
    used_mib, total_mib, free_mib = float(used_mib), float(total_mib), float(free_mib)
    return {
        "available": True,
        "source": source,
        "used_mib": round(used_mib, 1),
        "total_mib": round(total_mib, 1),
        "free_mib": round(free_mib, 1),
        "free_percent": round(100.0 * free_mib / total_mib, 2) if total_mib > 0 else None,
    }


def whole_device_vram() -> dict[str, object]:
    """Device-level memory in use by every process, not just this one."""
    torch = importlib.import_module("torch")
    if torch.cuda.is_available():
        try:
            free, total = torch.cuda.mem_get_info()
            free_mib, total_mib = free / 2**20, total / 2**20
            return _vram(total_mib - free_mib, total_mib, free_mib, "torch.cuda.mem_get_info")
        except Exception:
            pass  # fall through to the documented nvidia-smi route
    try:
        out = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.used,memory.total,memory.free",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        used, total, free = (float(part) for part in out.stdout.strip().splitlines()[0].split(","))
        return _vram(used, total, free, "nvidia-smi")
    except Exception as exc:
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}"}


def cpu_usage(*, wall_s: float, cpu_s: float) -> dict[str, object]:
    out: dict[str, object] = {
        "wall_seconds": round(wall_s, 3),
        "process_cpu_seconds": round(cpu_s, 3),
        "process_cpu_ratio": round(cpu_s / wall_s, 3) if wall_s > 0 else None,
        "logical_cores": os.cpu_count(),
    }
    try:
        import psutil
    except ImportError:
        out["system_percent"] = None
        out["note"] = "psutil is absent, so whole-system CPU percentage is unavailable, not zero"
        return out
    out["system_percent"] = psutil.cpu_percent(interval=0.2)
    return out


def gpa_weights_path() -> Path:
    from loop.adaptive import gui_yolo

    return Path(gui_yolo._WEIGHTS)


def load_gpa() -> tuple[object, dict[str, object]]:
    from loop.adaptive.gui_yolo import GpaGuiDetector

    weights = gpa_weights_path()
    if not weights.is_file():
        raise SmokeError(
            f"missing GPA weights: {weights} (local file only; this smoke check never downloads)"
        )
    _require("ultralytics", f"GPA GUI detection needs ultralytics; {_SETUP_HINT}")
    started = time.perf_counter()
    detector = GpaGuiDetector()
    load_ms = (time.perf_counter() - started) * 1000.0
    status = str(getattr(detector, "status", "unavailable"))
    if status != "ok":
        raise SmokeError(f"GPA GUI detector failed to load {weights} (status {status!r})")
    return detector, {
        "weights": str(weights),
        "ultralytics": _version("ultralytics"),
        "status": status,
        "load_ms": round(load_ms, 1),
    }


def gpa_infer(detector: object, image: Image.Image) -> dict[str, object]:
    from loop.adaptive import gui_yolo

    status = str(getattr(detector, "status", "unavailable"))
    if status != "ok":
        raise SmokeError(f"GPA GUI detector is {status!r}, refusing to report proposals")
    started = time.perf_counter()
    proposals = detector.propose(image, frame_id="smoke")  # type: ignore[attr-defined]
    wall_ms = (time.perf_counter() - started) * 1000.0
    predict_device = getattr(detector, "_predict_device", None)
    if predict_device is None or str(predict_device).lower() == "cpu":
        raise SmokeError(
            f"GPA GUI detector fell back to CPU (predict device {predict_device!r}); CUDA NMS is "
            "unavailable, so this is not CUDA evidence"
        )
    sample = [
        {
            "bbox": [round(float(v), 4) for v in row.get("bbox", ())],
            "class_hint": str(row.get("class_hint", "")),
            "confidence": round(float(row.get("confidence", 0.0)), 4),
        }
        for row in list(proposals)[:5]
    ]
    return {
        "weights": str(getattr(detector, "weights_path", "")),
        "predict_device": predict_device,
        # conf/iou/imgsz actually handed to ultralytics, i.e. the NMS configuration:
        "predict": dict(gui_yolo._PREDICT_KW),
        "proposals_post_nms": len(proposals),
        "sample": sample,
        "infer_ms": round(float(getattr(detector, "last_infer_ms", 0.0)), 1),
        "wall_ms": round(wall_ms, 1),
    }


def ocr_models_dir() -> Path:
    rapidocr = _require("rapidocr", f"RapidOCR Small; {_SETUP_HINT}")
    return Path(rapidocr.__file__).resolve().parent / "models"  # type: ignore[attr-defined]


def load_ocr() -> tuple[object, dict[str, object]]:
    from loop.adaptive import cli

    _require("onnxruntime", f"RapidOCR's ONNX Runtime provider; {_SETUP_HINT}")
    models_dir = ocr_models_dir()
    missing = [name for name in OCR_MODELS if not (models_dir / name).is_file()]
    if missing:
        raise SmokeError(
            f"missing bundled RapidOCR Small models in {models_dir}: {missing}. "
            "This smoke check never downloads models; reinstall rapidocr 3.9.2 or stage the files."
        )
    started = time.perf_counter()
    engine = cli._rapid_engine(small=True)  # the same Small construction as the live OCR path
    load_ms = (time.perf_counter() - started) * 1000.0
    if engine is None:
        raise SmokeError(
            "RapidOCR Small construction returned None after the model/dependency checks passed; "
            "inspect loop/adaptive/cli.py::_rapid_engine"
        )
    return engine, {
        "rapidocr": _version("rapidocr"),
        "onnxruntime": _version("onnxruntime"),
        "models_dir": str(models_dir),
        "model_type": "Small",
        "providers": ocr_providers(engine),
        "load_ms": round(load_ms, 1),
    }


def ocr_providers(engine: object) -> list[str]:
    """Execution providers actually bound to the ONNX sessions, if introspectable."""
    found: list[str] = []
    for part_name in ("text_det", "text_cls", "text_rec"):
        part = getattr(engine, part_name, None)
        session = getattr(getattr(part, "session", None), "session", None)
        getter = getattr(session, "get_providers", None)
        if callable(getter):
            found.extend(str(name) for name in getter())
    return sorted(set(found))


def ocr_read(engine: object, image: Image.Image) -> dict[str, object]:
    import numpy as np

    from loop.adaptive.cli import _OCR_MIN_SCORE

    started = time.perf_counter()
    try:
        out = engine(np.asarray(image.convert("RGB")), use_cls=False)  # type: ignore[operator]
    except Exception as exc:
        raise SmokeError(f"RapidOCR Small failed on the frame: {type(exc).__name__}: {exc}") from exc
    infer_ms = (time.perf_counter() - started) * 1000.0
    txts = getattr(out, "txts", None)
    boxes = getattr(out, "boxes", None)
    scores = getattr(out, "scores", None)
    if txts is None and isinstance(out, tuple) and len(out) >= 3:
        boxes, txts, scores = out[0], out[1], out[2]
    texts = [str(text) for text in (txts or [])]
    confs = [float(score) for score in (scores or [])]
    kept = [text for text, score in zip(texts, confs) if score >= float(_OCR_MIN_SCORE)]
    top = sorted(zip(texts, confs), key=lambda pair: pair[1], reverse=True)[:5]
    return {
        "calls": 1,
        "use_cls": False,
        "frame_shape": [image.height, image.width, 3],
        "min_score": float(_OCR_MIN_SCORE),
        "raw_tokens": len(texts),
        "tokens": len(kept),
        "boxes": len(boxes) if boxes is not None else None,
        "top_tokens": [{"text": text, "score": round(score, 4)} for text, score in top],
        "infer_ms": round(infer_ms, 1),
    }


def load_qwen() -> tuple[object, dict[str, object]]:
    _require("torch", _SETUP_HINT)
    _require("transformers", f"Qwen loader; {_SETUP_HINT}")
    from loop.adaptive.qwen_policy import (
        QWEN35_08B_MODEL_ID,
        QWEN35_08B_REVISION,
        QwenReactionPolicy,
    )

    started = time.perf_counter()
    try:
        policy = QwenReactionPolicy.from_pretrained()
    except Exception as exc:
        raise SmokeError(
            f"normal Qwen loader failed for {QWEN35_08B_MODEL_ID}@{QWEN35_08B_REVISION} with "
            f"HF_HUB_OFFLINE=1/TRANSFORMERS_OFFLINE=1: {type(exc).__name__}: {exc}. Stage the pinned "
            "snapshot in the Hugging Face cache; this smoke check never downloads."
        ) from exc
    load_ms = (time.perf_counter() - started) * 1000.0
    return policy, {
        "model_id": QWEN35_08B_MODEL_ID,
        "revision": QWEN35_08B_REVISION,
        "loader": "QwenReactionPolicy.from_pretrained",
        "transformers": _version("transformers"),
        "torch": _version("torch"),
        "dtype": "bfloat16",
        "load_ms": round(load_ms, 1),
    }


def qwen_decision(
    policy: object, *, gpa_proposals: int, ocr_tokens: int
) -> dict[str, object]:
    from loop.adaptive.candidates import CandidateAction, Directive
    from loop.adaptive.policy import ReactiveField, ReactiveState

    candidates = (
        CandidateAction("act_attack", "hidden:attack", "Attack the visible enemy."),
        CandidateAction("act_defend", "hidden:defend", "Raise the shield."),
        CandidateAction("act_wait", "hidden:wait", "End the turn."),
    )
    snapshot = ReactiveState(
        snapshot_id="smoke-frame",
        schema_revision_id="smoke-schema",
        episode_id="smoke-episode",
        episode_generation=0,
        mode="combat",
        confidence=1.0,
        fields={
            "gpa_proposals": ReactiveField(gpa_proposals, 1.0, False),
            "ocr_tokens": ReactiveField(ocr_tokens, 1.0, False),
        },
        entities=(),
        relations=(),
    )
    directive = Directive(
        revision=1,
        episode_generation=0,
        intent="choose one reaction for the supplied frame",
        ttl_s=30.0,
        published_at=0.0,
    )
    offered = tuple(candidate.action_id for candidate in candidates)
    started = time.perf_counter()
    decision = policy.score(snapshot, directive, candidates)  # type: ignore[attr-defined]
    infer_ms = (time.perf_counter() - started) * 1000.0
    probabilities = dict(decision.probabilities)
    if decision.candidate_id not in offered:
        raise SmokeError(
            f"Qwen picked {decision.candidate_id!r}, which was not among the offered {list(offered)}"
        )
    if set(probabilities) != set(offered) or not all(
        math.isfinite(float(value)) for value in probabilities.values()
    ):
        raise SmokeError(f"Qwen returned malformed probabilities: {probabilities!r}")
    return {
        "offers": list(offered),
        "offer_count": len(offered),
        "choice": decision.candidate_id,
        "confidence": decision.confidence,
        "probabilities": probabilities,
        "calibrated": False,  # Qwen's one-hot score is not a calibrated confidence
        "gpa_proposals": gpa_proposals,
        "ocr_tokens": ocr_tokens,
        "infer_ms": round(infer_ms, 1),
    }


def run(frame_path: str) -> dict[str, object]:
    offline_env = force_offline()
    image = load_frame(frame_path)
    device = cuda_info()
    wall_start = time.perf_counter()
    cpu_start = time.process_time()
    report: dict[str, object] = {
        "ok": False,  # flipped only after every stage returns real evidence
        "offline_env": offline_env,
        "frame": frame_meta(frame_path, image),
        "device": device,
        "whole_device_before": whole_device_vram(),
        "stages": {},
    }
    stages: dict[str, object] = report["stages"]  # type: ignore[assignment]

    reset_peak_memory()

    detector, gpa_load = load_gpa()
    gpa_load["cuda_memory"] = cuda_memory()
    gpa = gpa_infer(detector, image)
    gpa["cuda_memory"] = cuda_memory()
    stages["gpa"] = {**gpa_load, "inference": gpa}

    engine, ocr_load = load_ocr()
    ocr = ocr_read(engine, image)
    stages["ocr"] = {**ocr_load, "inference": ocr}

    policy, qwen_load = load_qwen()
    qwen_load["cuda_memory"] = cuda_memory()
    decision = qwen_decision(
        policy,
        gpa_proposals=int(gpa["proposals_post_nms"]),  # type: ignore[arg-type]
        ocr_tokens=int(ocr["tokens"]),  # type: ignore[arg-type]
    )
    decision["cuda_memory"] = cuda_memory()
    stages["qwen"] = {**qwen_load, "decision": decision}

    report["whole_device_after"] = whole_device_vram()
    report["resources"] = {
        "gpu": cuda_memory(),
        "whole_device": whole_device_vram(),
        "cpu": cpu_usage(
            wall_s=time.perf_counter() - wall_start,
            cpu_s=time.process_time() - cpu_start,
        ),
    }
    report["ok"] = True
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Offline one-process smoke check for Qwen + GPA + RapidOCR Small."
    )
    parser.add_argument(
        "--frame",
        required=True,
        help="local PNG/JPEG frame to smoke-test; never downloaded or captured",
    )
    args = parser.parse_args(argv)
    try:
        report = run(args.frame)
    except SmokeError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2), file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
