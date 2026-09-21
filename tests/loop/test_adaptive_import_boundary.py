"""Import boundary: loop/adaptive must not depend on legacy game/agent stacks."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ADAPTIVE_DIR = REPO_ROOT / "loop" / "adaptive"
LOOP_PKG = REPO_ROOT / "loop"

ALLOWED_LOOP_IMPORT_PREFIXES: tuple[str, ...] = (
    "loop.adaptive",
    "loop.os.geometry",
    "loop.os.capture",
    "loop.os.mouse",
    "loop.os.window",
    "loop.os.primitives",
    "loop.os.keyboard",
    "loop.os.input_owner",
    "loop.model.gateway",
)

_THIRD_PARTY_ROOTS = frozenset({"pydantic", "numpy", "PIL"})


def _loop_import_allowed(module: str) -> bool:
    for prefix in ALLOWED_LOOP_IMPORT_PREFIXES:
        if module == prefix or module.startswith(f"{prefix}."):
            return True
    return False


def _package_parts_for_file(path: Path) -> list[str]:
    rel = path.relative_to(LOOP_PKG)
    parts = list(rel.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    else:
        parts.pop()  # module name → package path
    return parts


def _resolve_import_from(path: Path, node: ast.ImportFrom) -> str | None:
    if node.level == 0:
        return node.module
    pkg = _package_parts_for_file(path)
    drop = node.level - 1
    if drop > len(pkg):
        return None
    base = pkg[: len(pkg) - drop] if drop else pkg
    mod_tail = node.module.split(".") if node.module else []
    full = base + mod_tail
    if not full:
        return "loop"
    return "loop." + ".".join(full)


def _collect_import_modules(path: Path, tree: ast.AST) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.append((node.lineno, alias.name))
        elif isinstance(node, ast.ImportFrom):
            resolved = _resolve_import_from(path, node)
            if resolved:
                out.append((node.lineno, resolved))
    return out


def _check_module(module: str) -> str | None:
    root = module.split(".", 1)[0]
    if root in _THIRD_PARTY_ROOTS:
        return None
    if not module.startswith("loop.") and root != "loop":
        return None
    if not module.startswith("loop."):
        return None
    if not _loop_import_allowed(module):
        return f"loop import not on allow-list: {module}"
    return None


@pytest.fixture(scope="module")
def adaptive_dir() -> Path:
    if not ADAPTIVE_DIR.is_dir():
        pytest.skip("loop/adaptive missing — import boundary enforced when the tree lands")
    return ADAPTIVE_DIR


def test_loop_adaptive_import_boundary(adaptive_dir: Path) -> None:
    violations: list[str] = []
    for path in sorted(adaptive_dir.rglob("*.py")):
        rel = path.relative_to(REPO_ROOT).as_posix()
        source = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=rel)
        except SyntaxError as exc:
            violations.append(f"{rel}:{exc.lineno}: syntax error {exc.msg}")
            continue
        for lineno, module in _collect_import_modules(path, tree):
            err = _check_module(module)
            if err:
                violations.append(f"{rel}:{lineno}: {err}")
    assert not violations, "Import boundary violations:\n" + "\n".join(violations)
