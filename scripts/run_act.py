"""Phase 2 2048 runner (same as run_2048.py). Legacy dual-brain: --dual-brain."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from avatar.experiments.act import main

if __name__ == "__main__":
    extra = sys.argv[1:]
    argv: list[str] = []
    if "--launch" not in extra and "--headed" not in extra:
        argv.append("--launch")
    if "--level" not in extra:
        argv.extend(["--level", "L2"])
    argv.extend(extra)
    raise SystemExit(main(argv))
