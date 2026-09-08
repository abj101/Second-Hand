"""Benchmark perceive prompt variants against frozen STS2 screenshots."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image

from loop.agent.perceive import SceneDescription, perceive_scene
from loop.agent.prompts import PERCEIVE_PROMPTS
from loop.config import project_root
from loop.model.gateway import ModelCall, VlmGateway
from loop.os.geometry import encode_observe
from loop.os.ocr import read_words
from loop.os.sts_stats import extract_sts_stats

_ANTIPATTERN_COMPLETE = re.compile(r"\b(tutorial\s+complete|phase\s+done|ready for map)\b", re.I)
_HP_IN_LABEL = re.compile(r"\b\d{1,3}\s*/\s*\d{1,3}\b")
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


@dataclass
class BenchmarkRow:
    file: str
    prompt: str
    ok: bool
    parse_ok: bool
    screen: str
    expected_screen: str
    includes_hit: int
    includes_total: int
    excludes_clean: bool
    antipatterns: list[str] = field(default_factory=list)
    latency_ms: float = 0.0
    transcript: str = ""


def fixture_dir(fixture: str = "sts2") -> Path:
    return project_root() / "loop" / "fixtures" / f"{fixture}_screens"


def load_labels(path: Path | None = None) -> list[dict[str, Any]]:
    dest = path or (fixture_dir() / "labels.jsonl")
    if not dest.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in dest.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _image_label_path(base: Path, label: dict[str, Any]) -> Path | None:
    rel = str(label.get("file") or "")
    if Path(rel).suffix.lower() not in _IMAGE_SUFFIXES:
        return None
    path = base / rel
    return path if path.exists() else None


def _ocr_labels(image: Image.Image) -> set[str]:
    words = read_words(image)
    return {w.text.strip() for w in words if w.text.strip()}


def _antipatterns(parsed: dict[str, Any] | None, desc: SceneDescription, raw_text: str) -> list[str]:
    hits: list[str] = []
    blob = raw_text + " " + desc.as_text() + " " + json.dumps(desc.raw or {})
    if _ANTIPATTERN_COMPLETE.search(blob):
        hits.append("narrative_complete")
    for row in desc.controls:
        label = str(row.get("label") or "")
        if _HP_IN_LABEL.search(label):
            hits.append(f"hp_in_controls:{label}")
    for key in ("enemies", "hand"):
        vals = desc.combat.get(key)
        if isinstance(vals, list):
            for val in vals:
                s = str(val)
                if _HP_IN_LABEL.search(s):
                    hits.append(f"hp_in_combat_{key}:{s}")
    if parsed and parsed.get("scene") and len(str(parsed["scene"]).split()) > 40:
        hits.append("long_scene_prose")
    return hits


def score_perceive(
    *,
    label: dict[str, Any],
    desc: SceneDescription,
    parsed: dict[str, Any] | None,
    raw_text: str,
) -> tuple[bool, dict[str, Any]]:
    must = [str(x) for x in label.get("must_include") or []]
    must_not = [str(x).lower() for x in label.get("must_not_include") or []]
    transcript = desc.as_text().lower()
    controls = " ".join(desc.control_labels()).lower()
    combined = transcript + " " + controls + " " + raw_text.lower()
    includes_hit = sum(1 for token in must if token.lower() in combined)
    excludes_clean = all(token not in combined for token in must_not)
    expected_screen = str(label.get("screen") or "").lower()
    screen_ok = not expected_screen or str(desc.screen or "").lower() == expected_screen
    anti = _antipatterns(parsed, desc, raw_text)
    parse_ok = parsed is not None and not desc.fallback
    ok = parse_ok and screen_ok and includes_hit == len(must) and excludes_clean and not anti
    return ok, {
        "includes_hit": includes_hit,
        "includes_total": len(must),
        "excludes_clean": excludes_clean,
        "screen_ok": screen_ok,
        "antipatterns": anti,
        "parse_ok": parse_ok,
    }


def run_benchmark(
    *,
    fixture: str = "sts2",
    prompts: list[str] | None = None,
    gateway: VlmGateway | None = None,
    labels_path: Path | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    base = fixture_dir(fixture)
    labels = load_labels(labels_path)
    if limit is not None:
        labels = labels[: max(0, int(limit))]
    prompt_names = prompts or ["v1", "v2"]
    rows: list[BenchmarkRow] = []
    summary: dict[str, dict[str, int]] = {name: {"ok": 0, "n": 0} for name in prompt_names}

    for label in labels:
        rel = str(label.get("file") or "")
        path = _image_label_path(base, label)
        if path is None:
            continue
        image = Image.open(path).convert("RGB")
        hud = extract_sts_stats(image)
        frame = encode_observe(image, cap=1280, jpeg_quality=90)
        for name in prompt_names:
            system = PERCEIVE_PROMPTS.get(name)
            if not system:
                continue
            if gateway is None:
                parsed = None
                call = ModelCall(text="", parsed=None, latency_ms=0.0, model="offline")
                desc = SceneDescription.from_parsed(None, hud=hud)
            else:
                desc, call = perceive_scene(
                    gateway,
                    frame,
                    hud=hud,
                    perceive_system=system,
                )
                parsed = call.parsed if isinstance(call.parsed, dict) else None
            ok, detail = score_perceive(
                label=label,
                desc=desc,
                parsed=parsed,
                raw_text=call.text,
            )
            summary[name]["n"] += 1
            if ok:
                summary[name]["ok"] += 1
            rows.append(
                BenchmarkRow(
                    file=rel,
                    prompt=name,
                    ok=ok,
                    parse_ok=bool(detail["parse_ok"]),
                    screen=str(desc.screen or ""),
                    expected_screen=str(label.get("screen") or ""),
                    includes_hit=int(detail["includes_hit"]),
                    includes_total=int(detail["includes_total"]),
                    excludes_clean=bool(detail["excludes_clean"]),
                    antipatterns=list(detail["antipatterns"]),
                    latency_ms=float(call.latency_ms),
                    transcript=desc.as_text(),
                )
            )

    return {
        "fixture": fixture,
        "dir": str(base),
        "summary": summary,
        "rows": [row.__dict__ for row in rows],
    }


def offline_score_labels(fixture: str = "sts2") -> dict[str, Any]:
    """OCR/hud sanity on fixtures without calling the VLM."""
    base = fixture_dir(fixture)
    out: list[dict[str, Any]] = []
    for label in load_labels():
        path = _image_label_path(base, label)
        if path is None:
            continue
        image = Image.open(path).convert("RGB")
        stats = extract_sts_stats(image)
        ocr_labels = sorted(_ocr_labels(image))
        out.append(
            {
                "file": label.get("file"),
                "expected_screen": label.get("screen"),
                "ocr_screen": stats.get("screen"),
                "tutorial": stats.get("tutorial"),
                "player": stats.get("player"),
                "enemies": stats.get("enemies"),
                "ocr_label_count": len(ocr_labels),
            }
        )
    return {"fixture": fixture, "ocr_rows": out}
