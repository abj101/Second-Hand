from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PIL import Image

HUD_DIR = Path(__file__).resolve().parent
LABELS_PATH = HUD_DIR / "labels.jsonl"
LABELED_DIR = HUD_DIR / "labeled"

HEALTH_CROP = (0.05, 0.04, 0.18, 0.08)
STAMINA_CROP = (0.05, 0.13, 0.18, 0.05)
RUNES_CROP = (0.72, 0.88, 0.22, 0.08)
FAIL_CROP = (0.25, 0.40, 0.50, 0.20)

CANVAS = (200, 100)


def _box(crop: tuple[float, float, float, float], size: tuple[int, int]) -> tuple[int, int, int, int]:
    w, h = size
    return int(crop[0] * w), int(crop[1] * h), max(1, int(crop[2] * w)), max(1, int(crop[3] * h))


def _fill_rect(image: Image.Image, x0: int, y0: int, bw: int, bh: int, color: tuple[int, int, int]) -> None:
    for x in range(x0, x0 + bw):
        for y in range(y0, y0 + bh):
            image.putpixel((x, y), color)


def paint_bar(
    image: Image.Image,
    crop: tuple[float, float, float, float],
    frac: float,
    fill: tuple[int, int, int],
    empty: tuple[int, int, int],
) -> Image.Image:
    out = image.copy()
    x0, y0, bw, bh = _box(crop, out.size)
    filled = max(0, min(bw, int(round(bw * frac))))
    for x in range(x0, x0 + bw):
        color = fill if x < x0 + filled else empty
        for y in range(y0, y0 + bh):
            out.putpixel((x, y), color)
    return out


def paint_health(image: Image.Image, frac: float) -> Image.Image:
    return paint_bar(image, HEALTH_CROP, frac, (180, 20, 20), (40, 20, 20))


def paint_stamina(image: Image.Image, frac: float) -> Image.Image:
    return paint_bar(image, STAMINA_CROP, frac, (200, 170, 40), (40, 32, 12))


def paint_runes(image: Image.Image, count: int) -> Image.Image:
    out = image.copy()
    if count <= 0:
        return out
    x0, y0, bw, bh = _box(RUNES_CROP, out.size)
    blob = 6
    gap = 2
    for i in range(count):
        bx = x0 + i * (blob + gap)
        if bx + blob > x0 + bw:
            break
        by = y0 + max(0, (bh - blob) // 2)
        _fill_rect(out, bx, by, blob, blob, (210, 170, 40))
    return out


def paint_fail(image: Image.Image) -> Image.Image:
    out = image.copy()
    x0, y0, bw, bh = _box(FAIL_CROP, out.size)
    _fill_rect(out, x0, y0, bw, bh, (200, 10, 10))
    return out


def blank_canvas(bg: tuple[int, int, int] = (12, 12, 16)) -> Image.Image:
    return Image.new("RGB", CANVAS, bg)


def render_frame(
    *,
    health: float | None = 1.0,
    stamina: float | None = 1.0,
    runes: int | None = None,
    fail: bool = False,
    cutscene: bool = False,
    bg: tuple[int, int, int] = (12, 12, 16),
) -> Image.Image:
    image = blank_canvas(bg)
    if cutscene:
        return image
    if health is not None:
        image = paint_health(image, health)
    if stamina is not None:
        image = paint_stamina(image, stamina)
    if runes:
        image = paint_runes(image, runes)
    if fail:
        image = paint_fail(image)
    return image


FRAME_SPECS: list[dict[str, Any]] = [
    {"id": "full", "health": 1.0, "stamina": 1.0, "kind": "full-bar"},
    {"id": "three_quarter", "health": 0.75, "stamina": 1.0, "kind": "three-quarter"},
    {"id": "half", "health": 0.50, "stamina": 0.80, "runes": 1, "kind": "half-bar"},
    {"id": "half_stamina_low", "health": 0.50, "stamina": 0.25, "kind": "half-bar"},
    {"id": "quarter", "health": 0.25, "stamina": 0.50, "runes": 2, "kind": "quarter"},
    {"id": "quarter_stam_full", "health": 0.25, "stamina": 1.0, "kind": "quarter"},
    {"id": "near_death", "health": 0.10, "stamina": 0.20, "kind": "near-death"},
    {"id": "near_death_stam", "health": 0.08, "stamina": 0.05, "runes": 1, "kind": "near-death"},
    {"id": "empty_health", "health": 0.0, "stamina": 0.30, "kind": "empty-bar"},
    {"id": "empty_both", "health": 0.0, "stamina": 0.0, "kind": "empty-bar"},
    {"id": "fail_half", "health": 0.50, "stamina": 0.40, "fail": True, "kind": "fail-overlay"},
    {"id": "fail_empty", "health": 0.0, "stamina": 0.0, "fail": True, "kind": "fail-overlay"},
    {"id": "fail_near", "health": 0.12, "stamina": 0.10, "fail": True, "kind": "fail-overlay"},
    {"id": "fail_full_hp", "health": 1.0, "stamina": 1.0, "fail": True, "kind": "fail-overlay"},
    {"id": "cutscene_a", "cutscene": True, "kind": "cutscene"},
    {"id": "cutscene_b", "cutscene": True, "bg": (40, 50, 60), "kind": "cutscene"},
    {"id": "cutscene_c", "cutscene": True, "bg": (8, 8, 8), "kind": "cutscene"},
    {"id": "runes_3", "health": 0.90, "stamina": 0.90, "runes": 3, "kind": "runes"},
    {"id": "runes_4", "health": 0.60, "stamina": 0.70, "runes": 4, "kind": "runes"},
    {"id": "stam_empty_hp_full", "health": 1.0, "stamina": 0.0, "kind": "stamina-empty"},
    {"id": "hp_60", "health": 0.60, "stamina": 0.60, "runes": 2, "kind": "mid-bar"},
    {"id": "hp_40", "health": 0.40, "stamina": 0.90, "kind": "mid-bar"},
    {"id": "hp_33", "health": 0.33, "stamina": 0.33, "runes": 1, "kind": "mid-bar"},
    {"id": "hp_15", "health": 0.15, "stamina": 0.85, "kind": "near-death"},
]


def _label_for(spec: dict[str, Any]) -> dict[str, Any]:
    cutscene = bool(spec.get("cutscene"))
    fail = bool(spec.get("fail"))
    runes = spec.get("runes")
    return {
        "file": f"labeled/{spec['id']}.png",
        "kind": spec.get("kind") or spec["id"],
        "health_frac": None if cutscene else spec.get("health"),
        "stamina_frac": None if cutscene else spec.get("stamina"),
        "runes": None if cutscene or not runes else int(runes),
        "fail_screen": False if cutscene else fail,
        "win_screen": False,
    }


def write_labeled_set(directory: Path | None = None) -> Path:
    root = directory or HUD_DIR
    labeled = root / "labeled"
    labeled.mkdir(parents=True, exist_ok=True)
    labels_path = root / "labels.jsonl"
    lines: list[str] = []
    for spec in FRAME_SPECS:
        image = render_frame(
            health=spec.get("health"),
            stamina=spec.get("stamina"),
            runes=spec.get("runes"),
            fail=bool(spec.get("fail")),
            cutscene=bool(spec.get("cutscene")),
            bg=tuple(spec.get("bg") or (12, 12, 16)),  # type: ignore[arg-type]
        )
        dest = labeled / f"{spec['id']}.png"
        image.save(dest)
        lines.append(json.dumps(_label_for(spec)))
    labels_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return labels_path


def load_labels(path: Path | None = None) -> list[dict[str, Any]]:
    target = path or LABELS_PATH
    rows: list[dict[str, Any]] = []
    if not target.exists():
        return rows
    for line in target.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


if __name__ == "__main__":
    write_labeled_set()
    print(f"wrote {LABELS_PATH} ({len(FRAME_SPECS)} frames)")
