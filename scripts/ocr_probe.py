#!/usr/bin/env python3
"""Standalone RapidOCR probe on saved pipeline frames."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from PIL import Image


def _load_image(path: Path) -> Image.Image:
    return Image.open(path).convert("RGB")


def _probe_rapid(image: Image.Image) -> tuple[list[str], float]:
    from rapidocr import RapidOCR

    from loop.os.ocr import _coerce_rgb_uint8, _parse_rapid_output

    engine = RapidOCR()
    arr = _coerce_rgb_uint8(__import__("numpy").asarray(image))
    t0 = time.perf_counter()
    out = engine(arr, use_cls=False)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    txts, boxes, scores = _parse_rapid_output(out)
    lines: list[str] = []
    if not txts or boxes is None:
        return lines, elapsed_ms
    for i, raw in enumerate(txts):
        text = " ".join(str(raw or "").split())
        if not text:
            continue
        try:
            score = float(scores[i]) if scores is not None else 1.0
        except (TypeError, ValueError, IndexError):
            score = 1.0
        lines.append(f"{text!r} score={score:.3f}")
    return lines, elapsed_ms


def _probe_pipeline(image: Image.Image) -> tuple[list[str], float]:
    from loop.os.ocr import read_words

    t0 = time.perf_counter()
    words = read_words(image, tag="probe")
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    return [f"{w.text!r} score={w.score:.3f}" for w in words], elapsed_ms


def _probe_tesseract(image: Image.Image) -> tuple[list[str], float]:
    from loop.os.ocr import _tess_words

    t0 = time.perf_counter()
    words = _tess_words(image)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    return [f"{w.text!r} score={w.score:.3f}" for w in words], elapsed_ms


def _print_result(label: str, lines: list[str], elapsed_ms: float) -> None:
    print(f"{label}: {len(lines)} words in {elapsed_ms:.1f}ms")
    for line in lines[:10]:
        print(f"  {line}")
    if len(lines) > 10:
        print(f"  ... +{len(lines) - 10} more")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe RapidOCR on saved PNG/JPG frames.")
    parser.add_argument("paths", nargs="*", help="image files to probe")
    parser.add_argument("--dir", type=Path, help="probe every image in a directory")
    parser.add_argument(
        "--compare",
        action="store_true",
        help="run both standalone RapidOCR and loop/os/ocr.py pipeline",
    )
    parser.add_argument("--tesseract", action="store_true", help="also run Tesseract fallback")
    args = parser.parse_args(argv)

    paths: list[Path] = []
    if args.dir is not None:
        paths.extend(sorted(args.dir.glob("*.png")))
        paths.extend(sorted(args.dir.glob("*.jpg")))
        paths.extend(sorted(args.dir.glob("*.jpeg")))
    paths.extend(Path(p) for p in args.paths)
    if not paths:
        parser.error("provide image paths or --dir")

    for path in paths:
        if not path.exists():
            print(f"skip missing {path}", file=sys.stderr)
            continue
        print(f"\n=== {path} ===")
        image = _load_image(path)
        print(f"size={image.size} mode={image.mode}")
        try:
            rapid_lines, rapid_ms = _probe_rapid(image)
            _print_result("rapidocr-standalone", rapid_lines, rapid_ms)
        except Exception as exc:  # noqa: BLE001
            print(f"rapidocr-standalone: FAILED {type(exc).__name__}: {exc}")
        if args.compare:
            try:
                pipe_lines, pipe_ms = _probe_pipeline(image)
                _print_result("pipeline-read_words", pipe_lines, pipe_ms)
            except Exception as exc:  # noqa: BLE001
                print(f"pipeline-read_words: FAILED {type(exc).__name__}: {exc}")
        if args.tesseract:
            try:
                tess_lines, tess_ms = _probe_tesseract(image)
                _print_result("tesseract", tess_lines, tess_ms)
            except Exception as exc:  # noqa: BLE001
                print(f"tesseract: FAILED {type(exc).__name__}: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
