"""Geometry-first grounding benchmark over frozen screenshots.

`run_grounding_benchmark` is an oracle-assisted component test: it forces
`gui_marks=[]` and stamps expected enemy boxes onto proposals. Truth leaks into
the predictor by design. Use `run_production_proposal_benchmark` when scoring
the production fusion path; expected objects stay scorer-only.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

from PIL import Image

from loop.config import project_root
from loop.os.aim import AimMark, _merge_aim_marks, apply_enemy_bboxes, build_marks, detect_marks, ocr_click_marks, paint_aim


def box_iou(a: Sequence[float], b: Sequence[float]) -> float:
    ax0, ay0, ax1, ay1 = map(float, a[:4])
    bx0, by0, bx1, by1 = map(float, b[:4])
    ix0, iy0, ix1, iy1 = max(ax0, bx0), max(ay0, by0), min(ax1, bx1), min(ay1, by1)
    inter = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
    union = max(0.0, ax1 - ax0) * max(0.0, ay1 - ay0) + max(0.0, bx1 - bx0) * max(0.0, by1 - by0) - inter
    return inter / union if union > 0 else 0.0


def _role(mark: AimMark) -> str:
    if mark.role in {"card", "enemy", "button"}:
        return mark.role
    return "button" if "end turn" in str(mark.label or "").lower() else ""


def score_grounding(
    marks: Sequence[AimMark],
    expected: Sequence[dict[str, Any]],
    *,
    iou_threshold: float = 0.5,
) -> dict[str, Any]:
    predictions = [mark for mark in marks if _role(mark)]
    used: set[int] = set()
    hits = 0
    role_hits = 0
    click_hits = 0
    matches: list[dict[str, Any]] = []
    for truth in expected:
        bbox = truth.get("bbox") or []
        ranked = sorted(
            ((box_iou((m.x0, m.y0, m.x1, m.y1), bbox), i, m) for i, m in enumerate(predictions) if i not in used),
            reverse=True,
            key=lambda row: row[0],
        )
        if not ranked or ranked[0][0] < iou_threshold:
            matches.append({"id": truth.get("id"), "matched": False, "iou": ranked[0][0] if ranked else 0.0})
            continue
        iou, index, mark = ranked[0]
        used.add(index)
        hits += 1
        role_ok = _role(mark) == str(truth.get("role") or "")
        role_hits += int(role_ok)
        x0, y0, x1, y1 = map(float, bbox[:4])
        click_ok = x0 <= mark.x <= x1 and y0 <= mark.y <= y1
        click_hits += int(click_ok)
        matches.append({"id": truth.get("id"), "matched": True, "iou": iou, "role_ok": role_ok, "click_ok": click_ok})
    total = len(expected)
    return {
        "expected": total,
        "predicted": len(predictions),
        "hits": hits,
        "recall": hits / total if total else 1.0,
        "precision": hits / len(predictions) if predictions else (1.0 if not total else 0.0),
        "role_accuracy": role_hits / total if total else 1.0,
        "click_containment": click_hits / total if total else 1.0,
        "matches": matches,
    }


def gui_detector_health(*, box_count: int, injected: bool = False) -> dict[str, Any]:
    """Distinguish detector-off / unavailable / healthy-zero / ok.

    Injected replay boxes do not count as a live detector result.
    """
    from loop.os.gui import gui_status

    status = gui_status()
    if injected:
        kind = "injected"
    elif status == "gui=off":
        kind = "off"
    elif status.startswith("gui=unavailable"):
        kind = "unavailable"
    elif box_count == 0:
        kind = "healthy_zero"
    else:
        kind = "ok"
    return {"status": status, "kind": kind, "box_count": int(box_count), "injected": injected}


def mark_record(mark: AimMark) -> dict[str, Any]:
    return {
        "id": mark.id,
        "x": float(mark.x),
        "y": float(mark.y),
        "bbox": [float(mark.x0), float(mark.y0), float(mark.x1), float(mark.y1)],
        "label": mark.label,
        "source": mark.source,
        "role": mark.role,
        "score": float(mark.score),
        "confidence": float(mark.confidence),
        "object_id": mark.object_id,
    }


def _mark_from_row(row: dict[str, Any], *, default_source: str = "") -> AimMark:
    bbox = row.get("bbox") or [
        row.get("x0", 0.0),
        row.get("y0", 0.0),
        row.get("x1", 0.0),
        row.get("y1", 0.0),
    ]
    x0, y0, x1, y1 = (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
    x = float(row["x"]) if row.get("x") is not None else (x0 + x1) * 0.5
    y = float(row["y"]) if row.get("y") is not None else (y0 + y1) * 0.5
    return AimMark(
        id=int(row.get("id") or 0),
        x=x,
        y=y,
        x0=x0,
        y0=y0,
        x1=x1,
        y1=y1,
        label=str(row.get("label") or ""),
        score=float(row.get("score") or 0.0),
        object_id=str(row.get("object_id") or ""),
        role=str(row.get("role") or ""),
        source=str(row.get("source") or default_source),
        confidence=float(row.get("confidence") or 0.0),
    )


def _chrome_mark(mark: AimMark) -> bool:
    from loop.os.aim import _bottom_chrome, _is_placeholder_label, is_legend_label

    label = str(mark.label or "")
    if is_legend_label(label) or _is_placeholder_label(label) or _bottom_chrome(mark):
        return True
    compact = label.strip().lower()
    return compact in {"", "icon", "node_0"}


def score_legend_false_promotions(marks: Sequence[AimMark]) -> dict[str, Any]:
    """Footer/legend/placeholder marks must not carry role or confidence."""
    violations: list[dict[str, Any]] = []
    for mark in marks:
        if not _chrome_mark(mark):
            continue
        role = str(mark.role or "").strip()
        confidence = float(mark.confidence or 0.0)
        if role or confidence > 0.0:
            violations.append(
                {
                    "id": mark.id,
                    "label": mark.label,
                    "source": mark.source,
                    "role": role,
                    "confidence": confidence,
                    "x": float(mark.x),
                    "y": float(mark.y),
                }
            )
    return {"count": len(violations), "clean": not violations, "violations": violations}


def score_proposals(
    marks: Sequence[AimMark],
    expected: Sequence[dict[str, Any]],
    *,
    iou_threshold: float = 0.3,
) -> dict[str, Any]:
    """Geometry-only match. Truth never writes roles or labels onto marks."""
    used: set[int] = set()
    hits = 0
    click_hits = 0
    matches: list[dict[str, Any]] = []
    for truth in expected:
        bbox = truth.get("bbox") or []
        ranked = sorted(
            ((box_iou((m.x0, m.y0, m.x1, m.y1), bbox), i, m) for i, m in enumerate(marks) if i not in used),
            reverse=True,
            key=lambda row: row[0],
        )
        if not ranked or ranked[0][0] < iou_threshold:
            click_ok = False
            if bbox and len(bbox) >= 4:
                x0, y0, x1, y1 = map(float, bbox[:4])
                click_ok = any(x0 <= m.x <= x1 and y0 <= m.y <= y1 for m in marks)
            matches.append(
                {
                    "id": truth.get("id"),
                    "matched": False,
                    "iou": ranked[0][0] if ranked else 0.0,
                    "click_ok": click_ok,
                }
            )
            continue
        iou, index, mark = ranked[0]
        used.add(index)
        hits += 1
        x0, y0, x1, y1 = map(float, bbox[:4])
        click_ok = x0 <= mark.x <= x1 and y0 <= mark.y <= y1
        click_hits += int(click_ok)
        matches.append(
            {
                "id": truth.get("id"),
                "matched": True,
                "iou": iou,
                "click_ok": click_ok,
                "source": mark.source,
                "label": mark.label,
            }
        )
    total = len(expected)
    unmatched = max(0, len(marks) - hits)
    return {
        "expected": total,
        "predicted": len(marks),
        "hits": hits,
        "recall": hits / total if total else 1.0,
        "precision": hits / len(marks) if marks else (1.0 if not total else 0.0),
        "click_containment": click_hits / total if total else 1.0,
        "false_positives": unmatched,
        "abstained": bool(total and hits == 0),
        "matches": matches,
    }


def run_production_proposal_benchmark(
    labels_path: Path,
    *,
    debug_dir: Path | None = None,
) -> dict[str, Any]:
    """Production fusion path. Expected objects are scorer-only; never stamped on marks."""
    rows = [json.loads(line) for line in labels_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    results: list[dict[str, Any]] = []
    for row in rows:
        rel = str(row.get("file") or "")
        path = Path(rel) if rel else None
        if path is not None and not path.is_absolute():
            path = project_root() / path
        image = None
        if path is not None and path.exists():
            image = Image.open(path).convert("RGB")
        elif path is not None and rel:
            results.append({"file": str(path), "id": row.get("id"), "missing": True})
            continue
        injected_ocr = row.get("ocr") is not None
        injected_gui = row.get("gui") is not None
        injected_cv = row.get("cv") is not None
        if image is None:
            image = Image.new("RGB", (64, 64), (8, 8, 8))
        words = [_mark_from_row(item, default_source="ocr") for item in row["ocr"]] if injected_ocr else ocr_click_marks(image)
        gui = [_mark_from_row(item, default_source="gui") for item in row["gui"]] if injected_gui else None
        cv = [_mark_from_row(item, default_source="cv") for item in row["cv"]] if injected_cv else None
        live_gui = gui if gui is not None else []
        if gui is None:
            from loop.os.aim import gui_click_marks

            live_gui = gui_click_marks(image)
            gui = live_gui
        if cv is None:
            cv = detect_marks(image)
        screen = str(row.get("screen") or "")
        fusion_audit: dict[str, Any] = {}
        if injected_ocr or injected_gui or injected_cv:
            marks = _merge_aim_marks(
                words,
                cv,
                cap=int(row.get("cap") or 16),
                gui=gui,
                screen=screen or None,
                audit=fusion_audit,
            )
        else:
            marks = list(
                build_marks(
                    image,
                    screen=screen or None,
                    words=words,
                    gui_marks=gui,
                    cv_marks=cv,
                )
            )
        health = gui_detector_health(box_count=len(live_gui), injected=injected_gui)
        score = score_proposals(marks, row.get("objects") or [])
        legend_score = score_legend_false_promotions(marks)
        result = {
            "file": str(path) if path is not None else "",
            "id": row.get("id") or (path.stem if path is not None else ""),
            "screen": screen,
            "observation_id": str(row.get("observation_id") or row.get("id") or (path.stem if path is not None else "")),
            "detector": health,
            "raw": {
                "ocr": [mark_record(m) for m in words],
                "gui": [mark_record(m) for m in gui],
                "cv": [mark_record(m) for m in cv],
            },
            "fused": [mark_record(m) for m in marks],
            "policy_input": [mark_record(m) for m in marks],
            "fusion_audit": fusion_audit,
            "legend_false_promotions": legend_score,
            **score,
        }
        results.append(result)
        if debug_dir is not None:
            debug_dir.mkdir(parents=True, exist_ok=True)
            stem = str(result["observation_id"] or "row")
            (debug_dir / f"{stem}_rejections.json").write_text(
                json.dumps(fusion_audit.get("rejected") or [m for m in score["matches"] if not m.get("matched")], indent=2),
                encoding="utf-8",
            )
            paint_aim(image, marks=marks).save(debug_dir / f"{stem}_fused.jpg")
    valid = [row for row in results if not row.get("missing")]
    expected = sum(int(row["expected"]) for row in valid)
    hits = sum(int(row["hits"]) for row in valid)
    predicted = sum(int(row["predicted"]) for row in valid)
    return {
        "path": "production",
        "oracle_enemy_bboxes": False,
        "forced_empty_gui": False,
        "rows": results,
        "missing": len(results) - len(valid),
        "expected": expected,
        "recall": hits / expected if expected else 0.0,
        "precision": hits / predicted if predicted else 0.0,
        "click_containment": (
            sum(float(row["click_containment"]) * int(row["expected"]) for row in valid) / expected
            if expected
            else 0.0
        ),
        "false_positives": sum(int(row.get("false_positives") or 0) for row in valid),
        "abstentions": sum(1 for row in valid if row.get("abstained")),
        "legend_false_promotions": sum(
            int((row.get("legend_false_promotions") or {}).get("count") or 0) for row in valid
        ),
        "legend_clean": all((row.get("legend_false_promotions") or {}).get("clean", True) for row in valid),
        "transfer_gate": "pending",
    }


def run_grounding_benchmark(
    labels_path: Path,
    *,
    debug_dir: Path | None = None,
) -> dict[str, Any]:
    """Oracle-assisted component test. Truth is applied to predictions before scoring."""
    rows = [json.loads(line) for line in labels_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    results: list[dict[str, Any]] = []
    for row in rows:
        path = Path(str(row["file"]))
        if not path.is_absolute():
            path = project_root() / path
        if not path.exists():
            results.append({"file": str(path), "missing": True})
            continue
        image = Image.open(path).convert("RGB")
        words = ocr_click_marks(image)
        raw_marks = list(build_marks(image, screen=str(row.get("screen") or "combat"), words=words, gui_marks=[]))
        enemy_rows = [obj for obj in row.get("objects", []) if obj.get("role") == "enemy"]
        marks = apply_enemy_bboxes(raw_marks, enemy_rows)
        score = score_grounding(marks, row.get("objects", []))
        results.append({"file": str(path), **score})
        if debug_dir is not None:
            debug_dir.mkdir(parents=True, exist_ok=True)
            stem = f"{path.parent.name}_{path.stem}"
            paint_aim(image, marks=raw_marks).save(debug_dir / f"{stem}_proposals.jpg")
            fused = paint_aim(image, marks=marks)
            fused.save(debug_dir / f"{stem}_semantic.jpg")
            fused.save(debug_dir / f"{stem}_actionable.jpg")
            (debug_dir / f"{stem}_rejections.json").write_text(
                json.dumps([match for match in score["matches"] if not match.get("matched")], indent=2),
                encoding="utf-8",
            )
    valid = [row for row in results if not row.get("missing")]
    expected = sum(int(row["expected"]) for row in valid)
    hits = sum(int(row["hits"]) for row in valid)
    predicted = sum(int(row["predicted"]) for row in valid)
    return {
        "path": "oracle_assisted",
        "oracle_enemy_bboxes": True,
        "forced_empty_gui": True,
        "rows": results,
        "missing": len(results) - len(valid),
        "expected": expected,
        "recall": hits / expected if expected else 0.0,
        "precision": hits / predicted if predicted else 0.0,
        "role_accuracy": sum(float(row["role_accuracy"]) * int(row["expected"]) for row in valid) / expected if expected else 0.0,
        "click_containment": sum(float(row["click_containment"]) * int(row["expected"]) for row in valid) / expected if expected else 0.0,
    }
