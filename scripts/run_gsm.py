"""Phase 2 Tetris GSM: single VLM planner may invoke_gsm; inner ticks skip the VLM."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from avatar.experiments.tetris_act import main

if __name__ == "__main__":
    extra = sys.argv[1:]
    argv: list[str] = []
    if "--realtime" not in extra:
        argv.append("--realtime")
    if "--gsm" not in extra:
        argv.append("--gsm")
    if "--manual" not in extra and "--pixels" not in extra:
        argv.append("--manual")
    if "--launch" not in extra and "--headed" not in extra:
        argv.append("--launch")
    argv.extend(extra)
    raise SystemExit(main(argv))
