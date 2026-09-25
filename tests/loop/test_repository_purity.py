"""The shipped Python tree contains only the adaptive architecture."""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LOOP_DIR = REPO_ROOT / "loop"
ALLOWED_PACKAGES = {"adaptive", "model", "os"}


def test_loop_has_only_current_architecture_packages() -> None:
    actual = {
        path.name
        for path in LOOP_DIR.iterdir()
        if path.is_dir() and path.name != "__pycache__"
    }
    assert actual == ALLOWED_PACKAGES | {"game_briefs"}
    assert not list((LOOP_DIR / "game_briefs").rglob("*.py")), "game briefs are data, not a runtime package"


def test_all_internal_imports_resolve_to_current_packages() -> None:
    violations: list[str] = []
    for path in sorted(LOOP_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for module in names:
                if not module.startswith("loop."):
                    continue
                package = module.split(".", 2)[1]
                if package not in ALLOWED_PACKAGES:
                    rel = path.relative_to(REPO_ROOT).as_posix()
                    violations.append(f"{rel}:{node.lineno}: {module}")
    assert not violations, "stale internal imports:\n" + "\n".join(violations)
