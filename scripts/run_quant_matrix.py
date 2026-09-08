"""Legacy Phase 1 vision-model quant matrix."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from avatar.experiments.quant_matrix import main

if __name__ == "__main__":
    raise SystemExit(main())
