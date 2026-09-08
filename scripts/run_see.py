"""Legacy Phase 1 See experiment (2048 capture sweep)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from avatar.experiments.see import main

if __name__ == "__main__":
    raise SystemExit(main())
