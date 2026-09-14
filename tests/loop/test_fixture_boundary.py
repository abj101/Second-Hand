"""STS2 dispatch constants belong in loop.fixtures.sts2, not OS/agent files.

A production change that adds a rejection reason string must delete one.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_AGENT = ROOT / "loop" / "agent"
_SCOPED = list((ROOT / "loop" / "os").glob("*.py")) + [
    _AGENT / "loop.py",
    _AGENT / "perceive.py",
    _AGENT / "decision.py",
    _AGENT / "prompts.py",
]


def test_sts2_dispatch_literals_stay_in_fixture() -> None:
    for path in _SCOPED:
        text = path.read_text(encoding="utf-8")
        assert "_MAP_TOOLBAR_X1" not in text, path
        if path.name != "layout_adapter.py":
            assert re.search(r"^ENEMY_Y_MAX\s*=", text, re.M) is None, path
