"""Phase 2 ablation: unpaused Tetris with a VLM every turn (no GSM). Main path: run_gsm.py."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from avatar.experiments.tetris_act import main

if __name__ == "__main__":
    extra = sys.argv[1:]
    argv: list[str] = []
    if "--realtime" not in extra:
        argv.append("--realtime")
    if "--manual" not in extra:
        argv.append("--manual")
    if "--launch" not in extra and "--headed" not in extra:
        argv.append("--launch")
    argv.extend(extra)
    raise SystemExit(main(argv))
