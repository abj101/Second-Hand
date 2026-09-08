"""Offline YOLO GUI detector smoke on saved STS2 frames. No live window."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loop.os.aim import AimMark, paint_aim
from loop.os.gui import configure_gui, detect_gui_boxes, detect_gui_marks, gui_status, weight_path


def _frame_paths(frames: str, run_dir: str, limit: int) -> list[Path]:
    roots: list[Path] = []
    if frames:
        roots.append(Path(frames))
    if run_dir:
        roots.append(Path(run_dir) / "frames")
    found: list[Path] = []
    for root in roots:
        if root.is_file() and root.suffix.lower() in {".jpg", ".jpeg", ".png"}:
            found.append(root)
            continue
        if not root.is_dir():
            continue
        found.extend(sorted(root.rglob("obs.jpg")))
        found.extend(sorted(p for p in root.rglob("*.jpg") if p.name != "obs.jpg"))
    # Prefer obs.jpg (raw crop) over vlm.jpg (already numbered).
    obs = [p for p in found if p.name == "obs.jpg"]
    other = [p for p in found if p.name != "obs.jpg" and p.name != "vlm.jpg"]
    ordered = obs + other
    uniq: list[Path] = []
    seen: set[Path] = set()
    for path in ordered:
        key = path.resolve()
        if key in seen:
            continue
        seen.add(key)
        uniq.append(path)
        if limit and len(uniq) >= limit:
            break
    return uniq


def _summarize(marks: list[AimMark]) -> str:
    labels = [m.label for m in marks if m.label]
    lower_right = [m for m in marks if m.x >= 0.88 and m.y >= 0.80]
    lower_left = [m for m in marks if m.x <= 0.12 and m.y >= 0.80]
    blob = " ".join(labels).lower()
    bits = [
        f"boxes={len(marks)}",
        f"lr={len(lower_right)}",
        f"ll={len(lower_left)}",
    ]
    if "singleplayer" in blob:
        bits.append("singleplayer")
    if "confirm" in blob or "got it" in blob or "back" in blob:
        bits.append("named-menu")
    if lower_right:
        bits.append("check-corner")
    if lower_left:
        bits.append("back-corner")
    return " ".join(bits)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Smoke YOLO GUI detector on saved frames.")
    parser.add_argument("--frames", default="", help="directory of screenshots or a single image")
    parser.add_argument("--run-dir", default="", help="collect run dir; uses frames/obs.jpg")
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--overlay", default="", help="directory to write painted JPEGs")
    parser.add_argument("--model", default="gpa", choices=("gpa", "groundcua"))
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--no-download", action="store_true")
    args = parser.parse_args(argv)
    paths = _frame_paths(args.frames, args.run_dir, args.limit)
    if not paths:
        print("no frames. pass --frames or --run-dir", file=sys.stderr)
        return 2
    status = configure_gui(
        enabled=True,
        device=args.device,
        model=args.model,
        download=not args.no_download,
    )
    print(status)
    print(f"weights={weight_path(args.model)}")
    if status.startswith("gui=unavailable") or status == "gui=off":
        print("install optional extra: pip install -e \".[gui]\"", file=sys.stderr)
        return 1
    overlay = Path(args.overlay) if args.overlay else None
    if overlay is not None:
        overlay.mkdir(parents=True, exist_ok=True)
    hits = 0
    for i, path in enumerate(paths, start=1):
        image = Image.open(path).convert("RGB")
        boxes = detect_gui_boxes(image)
        marks = detect_gui_marks(image)
        line = _summarize(marks)
        print(f"{i} {path} {line}")
        for box in boxes[:16]:
            print(
                f"  {box.label} {box.x0:.3f},{box.y0:.3f}-{box.x1:.3f},{box.y1:.3f} "
                f"conf={box.score:.2f}"
            )
        if "check-corner" in line or "singleplayer" in line or "named-menu" in line:
            hits += 1
        if overlay is not None:
            dest = overlay / f"{i:02d}_{path.parent.name}_{path.stem}.jpg"
            paint_aim(image, marks=tuple(marks)).save(dest, format="JPEG", quality=85)
            print(f"  overlay={dest}")
    print(f"done frames={len(paths)} interesting={hits} {gui_status()}")
    if hits == 0:
        print(
            "GPA boxed none of Confirm/Back/Singleplayer on these frames. "
            "Retry with --model groundcua. If both miss, keep --no-gui.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
