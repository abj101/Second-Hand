"""Legacy Phase 1 paused Tetris (pause-and-decide). Phase 2 GSM: scripts/run_gsm.py."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from avatar.experiments.tetris_act import main

if __name__ == "__main__":
    raise SystemExit(main())
