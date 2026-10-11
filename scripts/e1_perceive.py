"""E1: zero-shot affordance perception on the frozen STS2 key (docs/plan.md).

Variants (spec §6, "Locating precisely"):
  v0  plain points                      run: 1 call per frame
  v1  rulers in the margins             run: 1 call per frame
  v2  v0 + zoom-refine per affordance   run: 1 call per affordance (reuses v0 perception)
  v3  v2 + snap to OCR text             score only (needs the OCR cache)

    .venv-agent/Scripts/python.exe scripts/e1_perceive.py run gemma4:31b-cloud --variant v0 [--n 89]
    .venv-perception/Scripts/python.exe scripts/e1_perceive.py ocr        # once; RapidOCR lives in that venv
    .venv-agent/Scripts/python.exe scripts/e1_perceive.py score gemma4:31b-cloud --variant v3 [--n 40]

`run` appends one JSON row per frame to .tmp/e1/<model>/perceive_<variant>.jsonl and resumes where it stopped.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent.locate import refine, snap_to_text  # noqa: E402
from agent.model import Ollama  # noqa: E402
from agent.perceive import perceive  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
KEY = ROOT / ".tmp/phase2/key/labels"
FRAMES = ROOT / ".tmp/phase2/key_sets"
OUT = ROOT / ".tmp/e1"
OCR_CACHE = OUT / "ocr.json"

CLICKABLE = {"card", "end_turn", "map_button", "deck_button", "settings_button", "button", "menu_button",
             "profile_button", "reward_item", "map_node", "event_option", "shop_item", "rest_option",
             "character_option", "enemy", "draw_pile", "discard_pile"}
DONT_CARE = {"ignore"}  # potions, relics, tooltips: clicking them is legitimate but they are not required
PAD = 0.01  # a point within 1% of the frame of a box edge still counts as inside
NEAR = 0.05  # centre error is measured for targets with a point within 5% of frame width


def key_frames() -> list[tuple[str, Path, dict]]:
    rows = []
    for f in sorted(KEY.glob("*.json")):
        label = json.loads(f.read_text(encoding="utf-8"))
        if "sts2" not in label["labeler"]:
            continue
        rows.append((label["frame"], next(FRAMES.glob(f"sts2_*/{label['frame']}.jpg")), label))
    return rows


def inside(point: list[float], bbox: list[float], size: tuple[int, int]) -> bool:
    w, h = size
    x, y = point[0] * w, point[1] * h
    return bbox[0] - PAD * w <= x <= bbox[2] + PAD * w and bbox[1] - PAD * h <= y <= bbox[3] + PAD * h


def score_frame(affordances: list[dict], label: dict) -> dict:
    """Recall over clickable key objects (map nodes separately: the key boxes unreachable nodes too),
    precision over proposed points, and centre error of near-miss-or-hit points."""
    w, h = size = tuple(label["size"])
    objs = [o for o in label["objects"] if not o.get("ambiguous")]
    targets = [o for o in objs if o["category"] in CLICKABLE and o["category"] != "map_node"]
    nodes = [o for o in objs if o["category"] == "map_node"]
    allowed = [o for o in objs if o["category"] in CLICKABLE | DONT_CARE]
    points = [a["at"] for a in affordances]
    hit = [o for o in targets if any(inside(p, o["bbox"], size) for p in points)]
    errors = []
    for o in targets:
        cx, cy = (o["bbox"][0] + o["bbox"][2]) / 2 / w, (o["bbox"][1] + o["bbox"][3]) / 2 / h
        d = min((math.hypot((p[0] - cx), (p[1] - cy) * h / w) for p in points), default=1.0)
        if d <= NEAR:
            errors.append(d)
    return {"targets": len(targets), "hit": len(hit),
            "nodes": len(nodes), "nodes_hit": sum(any(inside(p, o["bbox"], size) for p in points) for o in nodes),
            "points": len(points), "good": sum(any(inside(p, o["bbox"], size) for o in allowed) for p in points),
            "errors": errors, "missed": sorted(o["category"] for o in targets if o not in hit)}


def _rows(path: Path) -> dict[str, dict]:
    return {r["frame"]: r for r in map(json.loads, path.open(encoding="utf-8"))} if path.exists() else {}


def _append(path: Path, row: dict) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def run(model_name: str, variant: str, n: int) -> None:
    out = OUT / model_name.replace(":", "_")
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"perceive_{variant}.jsonl"
    done = _rows(path)
    base = _rows(out / "perceive_v0.jsonl") if variant == "v2" else {}
    model = Ollama(model_name)
    for frame, image_path, _ in key_frames()[:n]:
        if frame in done:
            continue
        image = Image.open(image_path).convert("RGB")
        row = {"frame": frame, "model": model_name, "variant": variant}
        try:
            if variant == "v2":
                rough = base[frame]["affordances"]
                refined, ms, nulls = [], 0.0, 0
                for a in rough:
                    p, reply = refine(model, image, a["target"], a["at"])
                    ms += reply.ms
                    nulls += p is None
                    refined.append({**a, "at": p or a["at"], "rough": a["at"]})
                row |= {"ms": round(ms), "refine_calls": len(rough), "refine_null": nulls, "affordances": refined}
            else:
                result, reply = perceive(model, image, with_rulers=variant == "v1")
                row |= {"ms": round(reply.ms), "parsed": reply.data is not None, **result}
        except Exception as exc:  # noqa: BLE001 - a failed call is a measured failure, not a crash
            row |= {"error": f"{type(exc).__name__}: {exc}"[:300]}
        _append(path, row)
        print(frame, row.get("ms"), len(row.get("affordances", [])), row.get("error", ""), flush=True)


def ocr() -> None:
    """OCR tokens for every key frame, cached as [[text, [x0, y0, x1, y1] fractions], ...] per frame."""
    import numpy as np
    from rapidocr import RapidOCR
    from rapidocr.utils.typings import ModelType

    engine = RapidOCR(params={"Det.model_type": ModelType.MEDIUM, "Rec.model_type": ModelType.MEDIUM})
    cache = json.loads(OCR_CACHE.read_text(encoding="utf-8")) if OCR_CACHE.exists() else {}
    for frame, image_path, _ in key_frames():
        if frame in cache:
            continue
        image = Image.open(image_path).convert("RGB")
        res = engine(np.asarray(image), use_cls=False)
        w, h = image.size
        cache[frame] = [[" ".join(str(t).split()), [min(p[0] for p in b) / w, min(p[1] for p in b) / h,
                                                    max(p[0] for p in b) / w, max(p[1] for p in b) / h]]
                        for t, b, s in zip(res.txts or (), res.boxes if res.boxes is not None else (), res.scores or ())
                        if float(s) >= 0.5 and str(t).strip()]
    OUT.mkdir(parents=True, exist_ok=True)
    OCR_CACHE.write_text(json.dumps(cache), encoding="utf-8")
    print(f"ocr: {len(cache)} frames")


def overlay(image_path: Path, label: dict, affordances: list[dict], dest: Path) -> None:
    img = Image.open(image_path).convert("RGB")
    draw = ImageDraw.Draw(img)
    for o in label["objects"]:
        draw.rectangle(o["bbox"], outline=(0, 200, 0) if o["category"] in CLICKABLE else (90, 90, 90), width=2)
    for a in affordances:
        x, y = a["at"][0] * img.width, a["at"][1] * img.height
        if "rough" in a:
            rx, ry = a["rough"][0] * img.width, a["rough"][1] * img.height
            draw.line([rx, ry, x, y], fill=(255, 160, 0), width=2)
        draw.ellipse([x - 6, y - 6, x + 6, y + 6], fill=(255, 40, 40))
        draw.text((x + 8, y - 8), f"{a['verb']} {a['target']}"[:40], fill=(255, 255, 0))
    img.save(dest)


def score(model_name: str, variant: str, n: int) -> dict:
    out = OUT / model_name.replace(":", "_")
    rows = _rows(out / f"perceive_{'v2' if variant == 'v3' else variant}.jsonl")
    tokens = json.loads(OCR_CACHE.read_text(encoding="utf-8")) if variant == "v3" else {}
    (out / f"overlays_{variant}").mkdir(exist_ok=True)
    tot = {"targets": 0, "hit": 0, "nodes": 0, "nodes_hit": 0, "points": 0, "good": 0}
    errors: list[float] = []
    missed: dict[str, int] = {}
    used = []
    for frame, image_path, label in key_frames()[:n]:
        row = rows.get(frame)
        if row is None or "error" in row:
            continue
        used.append(row)
        affs = row["affordances"]
        if variant == "v3":
            affs = [{**a, "at": snap_to_text(a["target"], a["at"], tokens.get(frame, []))} for a in affs]
        s = score_frame(affs, label)
        for k in tot:
            tot[k] += s[k]
        errors += s["errors"]
        for cat in s["missed"]:
            missed[cat] = missed.get(cat, 0) + 1
        overlay(image_path, label, affs, out / f"overlays_{variant}" / f"{frame}.png")
    ms = sorted(r["ms"] for r in used if "ms" in r)
    report = {
        "model": model_name, "variant": variant, "frames": len(used),
        "recall": round(tot["hit"] / max(tot["targets"], 1), 3),
        "map_node_recall": round(tot["nodes_hit"] / max(tot["nodes"], 1), 3),
        "precision": round(tot["good"] / max(tot["points"], 1), 3),
        "centre_error_median": round(statistics.median(errors), 4) if errors else None,
        "affordances_per_frame": round(tot["points"] / max(len(used), 1), 1),
        "ms_p50": round(statistics.median(ms)) if ms else None,
        "ms_p95": ms[math.ceil(0.95 * len(ms)) - 1] if ms else None,
        "missed_by_category": dict(sorted(missed.items(), key=lambda kv: -kv[1])),
    }
    if variant in ("v2", "v3"):
        calls = sum(r.get("refine_calls", 0) for r in used)
        report["refine_null_rate"] = round(sum(r.get("refine_null", 0) for r in used) / max(calls, 1), 3)
    (out / f"score_{variant}_n{n}.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(json.dumps(report, indent=1))
    return report


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=("run", "ocr", "score"))
    p.add_argument("model", nargs="?")
    p.add_argument("--variant", default="v0", choices=("v0", "v1", "v2", "v3"))
    p.add_argument("--n", type=int, default=89)
    a = p.parse_args()
    if a.cmd == "ocr":
        ocr()
    elif a.cmd == "run":
        run(a.model, a.variant, a.n)
    else:
        score(a.model, a.variant, a.n)


if __name__ == "__main__":
    main()
