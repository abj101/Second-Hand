"""One screenshot → open-vocab boxes. No input. Live DINO is optional."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from avatar.config import load_config
from avatar.gsm.registry import TETRIS_LABELS
from avatar.gsm.rule.detector import FakeDetector, OpenVocabDetector, PixelBlobDetector
from avatar.harness.profile import profile_from_config
from avatar.harness.window import WindowCapture, find_window
from loop.os.ground import filter_grounded, format_grounded, paint_boxes, visual_labels_for


def _parse_labels(raw: str, window: str) -> list[str]:
    parts = [p.strip() for p in (raw or "").split(",") if p.strip()]
    if parts:
        return parts
    if "elden" in (window or "").lower():
        return visual_labels_for("elden")
    return list(TETRIS_LABELS)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Smoke open-vocab detector on a window crop.")
    parser.add_argument("--window", default="Tetris")
    parser.add_argument("--fake", action="store_true", help="print FakeDetector boxes (no weights)")
    parser.add_argument("--pixel-blob", action="store_true", help="run PixelBlobDetector on a live crop")
    parser.add_argument("--device", default="cpu", help="cpu or cuda for Grounding DINO")
    parser.add_argument("--labels", default="", help="comma-separated visual phrases")
    parser.add_argument("--overlay", default="", help="write a painted JPEG to this path")
    args = parser.parse_args(argv)
    labels = _parse_labels(args.labels, args.window)
    if args.fake:
        boxes = FakeDetector().detect(None, labels)
        for box in boxes:
            print(f"{box.label} cx={box.cx:.2f} y={box.y:.2f} score={box.score:.2f}")
        print("detector_family=fake")
        grounded = format_grounded(boxes)
        if grounded:
            print(grounded)
        return 0
    cfg = load_config()
    try:
        find_window(args.window)
    except RuntimeError as exc:
        print(exc)
        print("Pass --fake to print stub boxes without a window.")
        return 2
    cap = WindowCapture(args.window, profile=profile_from_config(cfg, "tetris"))
    cap.refresh()
    full = "elden" in args.window.lower()
    image = cap.grab_image(full=full)
    if args.pixel_blob:
        detector = PixelBlobDetector()
    else:
        detector = OpenVocabDetector(device=args.device)
    boxes = detector.detect(image, labels)
    print(f"detector_family={detector.family} boxes={len(boxes)}")
    for box in boxes:
        print(f"{box.label} cx={box.cx:.2f} y={box.y:.2f} score={box.score:.2f}")
    use_elden = "elden" in args.window.lower() or visual_labels_for("elden") == labels
    kept = filter_grounded(boxes) if use_elden else list(boxes)
    if use_elden:
        print(f"kept={len(kept)}/{len(boxes)}")
    grounded = format_grounded(kept)
    if grounded:
        print(grounded)
    overlay = (args.overlay or "").strip()
    if overlay:
        dest = Path(overlay)
        dest.parent.mkdir(parents=True, exist_ok=True)
        paint_boxes(image, kept).save(dest, format="JPEG", quality=85)
        print(f"overlay={dest}")
    if not boxes:
        if detector.family == "pixel-blob":
            print("No bright tetromino blob on this crop. Check gameplay crop / theme contrast.")
        elif detector.family == "unavailable":
            err = getattr(detector, "last_error", "") or "unknown"
            print(
                "No live detector loaded. Install avatar[gsm] (torch + transformers + timm). "
                f"last_error={err}"
            )
        else:
            print("No detections on this crop.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
