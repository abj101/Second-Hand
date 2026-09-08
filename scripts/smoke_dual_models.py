"""Legacy Phase 1: load eyes + text brain together. Fail loudly on VRAM thrash."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from avatar.config import load_config, project_root
from avatar.harness.capture import encode_image
from avatar.models import brain_fallback_id, make_brain, make_eyes

DEFAULT_FRAME = project_root() / "writeups" / "figures" / "see_start_768_q85.jpg"
OOM_HINT = (
    "Dual-resident load failed. Set OLLAMA_MAX_LOADED_MODELS=2 on the Ollama "
    "Windows service (not only this shell), keep both keep_alive=5m, and retry. "
    "If qwen3.5:4b OOMs next to the VLM, pull phi4-mini."
)


def _tiny_frame(path: Path):
    if path.exists():
        image = Image.open(path)
    else:
        image = Image.new("RGB", (64, 64), color=(40, 40, 40))
    return encode_image(image, longest_side=64, jpeg_quality=70)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Smoke dual-resident eyes + brain (no unload)")
    parser.add_argument("--frame", type=Path, default=DEFAULT_FRAME)
    parser.add_argument("--brain", default=None, help="Override brain model id")
    args = parser.parse_args(argv)

    cfg = load_config()
    eyes = make_eyes(cfg)
    print(f"eyes={eyes.model} num_ctx={eyes.num_ctx}", flush=True)

    brain_ids = [args.brain] if args.brain else [make_brain(cfg).model]
    fallback = None if args.brain else brain_fallback_id(cfg)
    if fallback and fallback not in brain_ids:
        brain_ids.append(fallback)

    frame = _tiny_frame(args.frame)
    last_error: BaseException | None = None
    for brain_id in brain_ids:
        brain = make_brain(cfg, model_id=brain_id)
        print(f"brain={brain.model} num_ctx={brain.num_ctx}", flush=True)
        try:
            eyes_call = eyes.complete_json(
                system='Reply with JSON {"ok": true, "screen_kind": "gameplay"}.',
                user="Smoke: classify this tiny frame.",
                frames=[frame],
            )
            brain_call = brain.complete_json(
                system='Reply with JSON {"ok": true}. No markdown.',
                user="Smoke: confirm you are the text planner. Do not describe an image.",
            )
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            print(f"ERROR loading/calling {brain_id}: {exc}", flush=True)
            if fallback and brain_id != fallback:
                print(
                    f"Trying fallback brain {fallback} (still dual-resident, no swap-unload).",
                    flush=True,
                )
                continue
            print(OOM_HINT, file=sys.stderr)
            return 2
        print(
            json.dumps(
                {
                    "eyes_ms": round(eyes_call.latency_ms, 1),
                    "eyes_parsed": eyes_call.parsed,
                    "brain_ms": round(brain_call.latency_ms, 1),
                    "brain_parsed": brain_call.parsed,
                    "brain_model": brain.model,
                },
                default=str,
            ),
            flush=True,
        )
        if eyes_call.parsed is None or brain_call.parsed is None:
            print("WARN: one response was not JSON", file=sys.stderr)
            return 1
        print("dual-resident ok (neither model unloaded)", flush=True)
        return 0

    print(OOM_HINT, file=sys.stderr)
    if last_error:
        print(f"last_error={last_error}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
