from __future__ import annotations

import argparse

from avatar.config import load_config
from avatar.eval.logger import JsonlLogger, new_run_dir
from avatar.experiments.common import add_model_flags, parse_understand, safe_call, selected_models
from avatar.fixtures.g2048 import FIXTURE_STATES, open_2048
from avatar.models import make_gateway
from avatar.prompts.library import load_manual, understand_system, understand_user
from avatar.schema import CaptureSettings, TurnLog


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Understand experiment")
    parser.add_argument("--headed", action="store_true")
    add_model_flags(parser)
    args = parser.parse_args(argv)

    cfg = load_config()
    run_dir = new_run_dir("understand", "2048")
    logger = JsonlLogger(run_dir)
    prompt_mode = cfg.get("prompt_mode", "pixels")
    manual = load_manual("2048") if prompt_mode == "manual" else ""
    system = understand_system(prompt_mode=prompt_mode, manual=manual)
    model_rows = selected_models(cfg, args)
    print(f"Understand run dir: {run_dir}", flush=True)
    print(f"Models: {[model for model, _ in model_rows] or '(none)'}", flush=True)

    session, close = open_2048(headless=not args.headed)
    try:
        frames: list[tuple[str, dict, object, object]] = []
        for state in FIXTURE_STATES:
            print(f"Capturing {state}...", flush=True)
            oracle = session.load_state(state)
            frame = session.encode_board(768, 85)
            path = run_dir / "frames" / f"{state}.jpg"
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(frame.data)
            frames.append((state, oracle, frame, path))

        if not args.skip_models:
            for model_id, provider in model_rows:
                print(f"Loading {model_id} ({provider})...", flush=True)
                gateway = make_gateway(cfg, model_id, provider)
                try:
                    for index, (state, oracle, frame, path) in enumerate(frames, start=1):
                        print(f"  [{index}/{len(frames)}] {model_id} {state}", flush=True)
                        call = safe_call(
                            gateway,
                            system=system,
                            user=understand_user("2048"),
                            frames=[frame],
                        )
                        parsed = None if call is None else call.parsed
                        und = parse_understand(parsed)
                        latency = None if call is None else call.latency_ms
                        status = "ok" if und.goal != "unparseable" else "UNPARSEABLE"
                        latency_s = "n/a" if latency is None else f"{latency:.0f}ms"
                        print(f"      {status} {latency_s} goal={und.goal!r:.80}", flush=True)
                        logger.write(
                            TurnLog(
                                screenshot_ref=str(path),
                                capture_settings=CaptureSettings(
                                    longest_side=768,
                                    jpeg_quality=85,
                                    width=frame.width,
                                    height=frame.height,
                                    bytes=frame.nbytes,
                                ),
                                model=model_id,
                                harness_level="L0",
                                fixture="2048",
                                prompt_mode=prompt_mode,
                                reasoning=und.reasoning,
                                action=und.model_dump(),
                                result={"oracle": oracle, "raw": parsed},
                                latency_ms=latency,
                                cost_usd=None if call is None else call.cost_usd,
                                stage="understand",
                            )
                        )
                finally:
                    gateway.unload()
    finally:
        close()
    print(f"Understand experiment run written to {run_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
