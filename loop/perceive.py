from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PIL import Image

from loop.agent.prompts import PERCEIVE_SYSTEM, perceive_user
from loop.config import DEFAULT_MODEL, project_root
from loop.fixtures.elden import load_hud_spec
from loop.fixtures.elden_hud.synth import HUD_DIR, load_labels, write_labeled_set
from loop.model.gateway import VlmGateway
from loop.os.capture import encode_image
from loop.reward.hud import score_hud


def labeled_mae(hud: dict[str, Any], root: Path | None = None) -> dict[str, Any]:
    base = root or HUD_DIR
    if not (base / "labels.jsonl").exists():
        write_labeled_set(base)
    labels = load_labels(base / "labels.jsonl")
    rows: list[dict[str, Any]] = []
    health_err: list[float] = []
    stamina_err: list[float] = []
    fail_tp = fail_fp = fail_fn = fail_tn = 0
    for label in labels:
        path = base / str(label["file"])
        image = Image.open(path).convert("RGB")
        scored = score_hud(image, hud)
        row = {"file": label["file"], "kind": label.get("kind"), "label": label, "scored": scored}
        h_l, h_s = label.get("health_frac"), scored.get("health_frac")
        if isinstance(h_l, (int, float)) and isinstance(h_s, (int, float)):
            err = abs(float(h_s) - float(h_l))
            health_err.append(err)
            row["health_abs_err"] = err
        s_l, s_s = label.get("stamina_frac"), scored.get("stamina_frac")
        if isinstance(s_l, (int, float)) and isinstance(s_s, (int, float)):
            err = abs(float(s_s) - float(s_l))
            stamina_err.append(err)
            row["stamina_abs_err"] = err
        lab_fail = bool(label.get("fail_screen"))
        pred_fail = bool(scored.get("fail_screen"))
        if lab_fail and pred_fail:
            fail_tp += 1
        elif lab_fail and not pred_fail:
            fail_fn += 1
        elif not lab_fail and pred_fail:
            fail_fp += 1
        else:
            fail_tn += 1
        rows.append(row)
    return {
        "n": len(labels),
        "health_mae": (sum(health_err) / len(health_err) if health_err else None),
        "stamina_mae": (sum(stamina_err) / len(stamina_err) if stamina_err else None),
        "health_max_err": (max(health_err) if health_err else None),
        "fail_confusion": {"tp": fail_tp, "fp": fail_fp, "fn": fail_fn, "tn": fail_tn},
        "rows": rows,
    }


def vlm_vs_hud(
    hud: dict[str, Any],
    gateway: VlmGateway,
    *,
    root: Path | None = None,
    limit: int = 8,
) -> dict[str, Any]:
    base = root or HUD_DIR
    labels = load_labels(base / "labels.jsonl")
    health_err: list[float] = []
    fail_confusions = 0
    compared = 0
    for label in labels[:limit]:
        path = base / str(label["file"])
        image = Image.open(path).convert("RGB")
        scored = score_hud(image, hud)
        frame = encode_image(image, longest_side=768, jpeg_quality=85)
        call = gateway.complete_json(system=PERCEIVE_SYSTEM, user=perceive_user(), frames=[frame])
        parsed = call.parsed or {}
        compared += 1
        h_h, h_v = scored.get("health_frac"), parsed.get("health_frac")
        if isinstance(h_h, (int, float)) and isinstance(h_v, (int, float)):
            health_err.append(abs(float(h_v) - float(h_h)))
        hud_fail = bool(scored.get("fail_screen"))
        vlm_fail = bool(parsed.get("fail_screen"))
        if hud_fail != vlm_fail and isinstance(scored.get("health_frac"), (int, float)):
            fail_confusions += 1
    mae = sum(health_err) / len(health_err) if health_err else None
    return {
        "compared": compared,
        "health_mae_vs_hud": mae,
        "health_fail_confusions": fail_confusions,
        "ok": mae is not None and mae < 0.25 and fail_confusions <= compared // 2,
    }


def live_grab(fixture: str = "elden") -> dict[str, Any]:
    from loop.fixtures import make_fixture
    from loop.reward.hud import hud_visible, is_black_frame

    dest_dir = project_root() / "data" / "loop" / fixture / "perceive"
    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        fx = make_fixture(fixture, attach=True)
    except RuntimeError as exc:
        return {"live": False, "reason": str(exc)}
    if fx.controller is None:
        return {"live": False, "reason": "no window"}
    try:
        image = fx.grab_image()
        dest = dest_dir / "live.png"
        image.save(dest)
        scored = fx.scorer(image)
        black = is_black_frame(image)
        hud_dy = 0.0
        source = getattr(fx.controller.capture, "last_source", "client")
        visible = False
        if fixture == "elden":
            from loop.fixtures.elden import load_hud_spec

            if not black and not hud_visible(scored):
                from loop.reward.hud import score_hud_search

                scored, hud_dy = score_hud_search(image, load_hud_spec())
            visible = (not black) and hud_visible(scored)
        return {
            "live": True,
            "path": str(dest),
            "black_grab": black,
            "hud_visible": visible,
            "scored": scored,
            "size": list(image.size),
            "grab_source": source,
            "hud_dy": hud_dy,
        }
    finally:
        fx.close()


def run_perceive(
    fixture: str = "elden",
    *,
    skip_models: bool = False,
    model: str = DEFAULT_MODEL,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "fixture": fixture,
        "labeled": None,
        "vlm": None,
        "live": None,
        "ok": True,
        "black_grab": False,
    }
    if fixture == "sts2":
        live = live_grab(fixture)
        report["live"] = {k: v for k, v in live.items() if k != "scored"} | {"scored": live.get("scored")}
        if live.get("black_grab"):
            report["black_grab"] = True
            report["ok"] = False
            report["reason"] = "live grab is black; use borderless"
        elif not live.get("live"):
            report["ok"] = False
            report["reason"] = str(live.get("reason") or "no window")
        dest = project_root() / "data" / "loop" / fixture / "perceive" / "report.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        return report
    hud = load_hud_spec()
    labeled = labeled_mae(hud)
    report["labeled"] = {
        "n": labeled["n"],
        "health_mae": labeled["health_mae"],
        "stamina_mae": labeled["stamina_mae"],
        "health_max_err": labeled["health_max_err"],
        "fail_confusion": labeled["fail_confusion"],
    }
    if labeled["health_max_err"] is not None and labeled["health_max_err"] >= 0.05:
        report["ok"] = False
        report["reason"] = "score_hud disagrees with labels"
    if not skip_models:
        gateway = VlmGateway(model=model)
        try:
            report["vlm"] = vlm_vs_hud(hud, gateway)
            if report["vlm"] and not report["vlm"].get("ok"):
                report["ok"] = False
                report["reason"] = "VLM cannot read bars used as success"
        finally:
            gateway.unload()
    if fixture == "elden":
        live = live_grab(fixture)
        report["live"] = {k: v for k, v in live.items() if k != "scored"} | {"scored": live.get("scored")}
        if live.get("black_grab"):
            report["black_grab"] = True
            report["ok"] = False
            report["reason"] = "live grab is black; use borderless"
    dest = project_root() / "data" / "loop" / fixture / "perceive" / "report.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report
