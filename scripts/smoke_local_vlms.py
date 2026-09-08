"""Send one 2048 frame to each configured local VLM. No Playwright."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from avatar.config import load_config, project_root
from avatar.experiments.common import parse_see
from avatar.harness.capture import encode_image
from avatar.models import configured_models, make_gateway
from avatar.prompts.library import see_system, see_user

DEFAULT_FRAME = project_root() / "writeups" / "figures" / "see_start_768_q85.jpg"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Smoke-test local VLMs on one 2048 screenshot")
    parser.add_argument("--model", action="append", default=None, metavar="ID")
    parser.add_argument("--frame", type=Path, default=DEFAULT_FRAME)
    args = parser.parse_args(argv)

    cfg = load_config()
    rows = configured_models(cfg, only=args.model)
    if not rows:
        print("No local models configured.", file=sys.stderr)
        return 2
    if not args.frame.exists():
        print(f"Missing smoke frame: {args.frame}", file=sys.stderr)
        return 2

    image = Image.open(args.frame)
    frame = encode_image(image, longest_side="native", jpeg_quality=85)
    system = see_system()
    user = see_user("2048", "start")
    failures = 0
    for model_id, provider in rows:
        gateway = make_gateway(cfg, model_id, provider)
        print(f"\n=== {model_id} ({provider}) ===")
        try:
            call = gateway.complete_json(system=system, user=user, frames=[frame])
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"ERROR {exc}")
            continue
        finally:
            gateway.unload()
        see = parse_see(call.parsed)
        print(f"latency_ms={call.latency_ms:.0f} parsed={call.parsed is not None}")
        print(json.dumps(see.model_dump(), indent=2)[:1200])
        if call.parsed is None:
            failures += 1
            print("WARN: response was not JSON")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
