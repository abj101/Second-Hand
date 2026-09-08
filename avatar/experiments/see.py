from __future__ import annotations

import argparse
import json

from avatar.config import load_config
from avatar.eval.logger import JsonlLogger, new_run_dir
from avatar.experiments.common import add_model_flags, parse_see, safe_call, selected_models
from avatar.fixtures.g2048 import capture_sweep, open_2048
from avatar.models import make_gateway
from avatar.prompts.library import load_manual, see_system, see_user
from avatar.schema import CaptureSettings, TurnLog


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="See experiment: capture sweep + VLM smoke")
    parser.add_argument("--headed", action="store_true")
    add_model_flags(parser)
    args = parser.parse_args(argv)

    cfg = load_config()
    run_dir = new_run_dir("see", "2048")
    frames_dir = run_dir / "frames"
    logger = JsonlLogger(run_dir)
    print(f"See run dir: {run_dir}", flush=True)
    print("Capturing 2048 frames...", flush=True)

    session, close = open_2048(headless=not args.headed)
    try:
        rows = capture_sweep(session, frames_dir)
    finally:
        close()
    print(f"Captured {len(rows)} frames", flush=True)

    (run_dir / "capture_index.json").write_text(
        json.dumps(
            [{k: v for k, v in row.items() if k != "frame"} for row in rows],
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )

    prompt_mode = cfg.get("prompt_mode", "pixels")
    manual = load_manual("2048") if prompt_mode == "manual" else ""
    system = see_system(prompt_mode=prompt_mode, manual=manual)

    model_rows = selected_models(cfg, args)
    print(f"Models: {[model for model, _ in model_rows] or '(none)'}", flush=True)
    for model_id, provider in model_rows:
        print(f"Loading {model_id} ({provider})...", flush=True)
        gateway = make_gateway(cfg, model_id, provider)
        try:
            _see_model(gateway, system, rows, logger, prompt_mode)
        finally:
            gateway.unload()

    print(f"See experiment run written to {run_dir}", flush=True)
    print(f"Frames: {len(rows)}.", flush=True)
    return 0


def _see_model(gateway, system, rows, logger, prompt_mode) -> None:
    model_id = gateway.model
    smoke = [
        row
        for row in rows
        if row["longest_side"] in ("native", 768) and row["jpeg_quality"] == 85
    ]
    for index, row in enumerate(smoke, start=1):
        print(
            f"  [{index}/{len(smoke)}] {model_id} {row['state']} "
            f"ls={row['longest_side']} q={row['jpeg_quality']}",
            flush=True,
        )
        call = safe_call(
            gateway,
            system=system,
            user=see_user("2048", row["state"]),
            frames=[row["frame"]],
        )
        parsed = None if call is None else call.parsed
        see = parse_see(parsed)
        latency = None if call is None else call.latency_ms
        status = "ok" if see.status != "unparseable" else "UNPARSEABLE"
        latency_s = "n/a" if latency is None else f"{latency:.0f}ms"
        print(f"      {status} {latency_s}", flush=True)
        logger.write(
            TurnLog(
                screenshot_ref=row["path"],
                capture_settings=CaptureSettings(
                    longest_side=row["longest_side"],
                    jpeg_quality=row["jpeg_quality"],
                    width=row["width"],
                    height=row["height"],
                    bytes=row["bytes"],
                ),
                model=model_id,
                harness_level="L0",
                fixture="2048",
                paused=True,
                prompt_mode=prompt_mode,
                reasoning=see.reasoning,
                action=see.model_dump(),
                result={"oracle": row["oracle"], "raw": parsed},
                latency_ms=None if call is None else call.latency_ms,
                cost_usd=None if call is None else call.cost_usd,
                stage="see",
            )
        )


if __name__ == "__main__":
    raise SystemExit(main())
