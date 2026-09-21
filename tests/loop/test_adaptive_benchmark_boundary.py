"""Benchmark must not leak into loop/adaptive (and vice versa)."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ADAPTIVE_DIR = REPO_ROOT / "loop" / "adaptive"
LOOP_OS_DIR = REPO_ROOT / "loop" / "os"
BENCHMARKS_DIR = REPO_ROOT / "benchmarks"
ADAPTER = REPO_ROOT / "benchmarks" / "infinite_mario" / "adapter.py"
PYPROJECT = REPO_ROOT / "pyproject.toml"

_BANNED_BENCHMARK_MODULES = frozenset(
    {
        "loop.os.mouse",
        "loop.os.wininput",
        "loop.os.keyboard",
    }
)
_BANNED_NATIVE_VENDOR = frozenset({"playjev", "playwright"})

_BANNED_IN_ADAPTIVE = re.compile(r"mariohtml5|playjev|nintendo", re.I)


def _import_modules(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    mods: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.append(node.module)
            for alias in node.names:
                if alias.name != "*":
                    mods.append(f"{node.module}.{alias.name}")
    return mods


@pytest.fixture(scope="module")
def adaptive_dir() -> Path:
    if not ADAPTIVE_DIR.is_dir():
        pytest.skip("loop/adaptive missing")
    return ADAPTIVE_DIR


def test_adaptive_does_not_import_benchmarks_or_playjev(adaptive_dir: Path) -> None:
    violations: list[str] = []
    for path in sorted(adaptive_dir.rglob("*.py")):
        rel = path.relative_to(REPO_ROOT).as_posix()
        for mod in _import_modules(path):
            if mod.startswith("benchmarks") or mod == "playjev" or mod.startswith("playjev."):
                violations.append(f"{rel}: imports {mod}")
    assert not violations, "\n".join(violations)


def test_adaptive_source_has_no_mario_vendor_strings(adaptive_dir: Path) -> None:
    hits: list[str] = []
    for path in sorted(adaptive_dir.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if _BANNED_IN_ADAPTIVE.search(text):
            hits.append(path.relative_to(REPO_ROOT).as_posix())
    assert not hits, "forbidden vendor strings in adaptive:\n" + "\n".join(hits)


def _benchmark_py_files() -> list[Path]:
    if not BENCHMARKS_DIR.is_dir():
        return []
    return sorted(BENCHMARKS_DIR.rglob("*.py"))


def _calls_session_act(path: Path, tree: ast.AST) -> list[int]:
    hits: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "act":
                hits.append(node.lineno)
    return hits


def test_benchmarks_tree_does_not_call_session_act_or_native_input() -> None:
    violations: list[str] = []
    for path in _benchmark_py_files():
        rel = path.relative_to(REPO_ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        for lineno in _calls_session_act(path, tree):
            violations.append(f"{rel}:{lineno}: calls .act()")
        for mod in _import_modules(path):
            root = mod.split(".", 2)
            if len(root) >= 3 and ".".join(root[:3]) in _BANNED_BENCHMARK_MODULES:
                violations.append(f"{rel}: imports {mod}")
            if mod in _BANNED_BENCHMARK_MODULES:
                violations.append(f"{rel}: imports {mod}")
            if mod == "loop.adaptive.session":
                violations.append(f"{rel}: imports loop.adaptive.session")
    assert not violations, "\n".join(violations)


def test_native_control_does_not_import_playjev_or_playwright() -> None:
    violations: list[str] = []
    roots = [ADAPTIVE_DIR, LOOP_OS_DIR]
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.py")):
            rel = path.relative_to(REPO_ROOT).as_posix()
            for mod in _import_modules(path):
                base = mod.split(".", 1)[0]
                if base in _BANNED_NATIVE_VENDOR:
                    violations.append(f"{rel}: imports {mod}")
                if mod.startswith("playjev.") or mod.startswith("playwright."):
                    violations.append(f"{rel}: imports {mod}")
    assert not violations, "\n".join(violations)


def test_adapter_does_not_call_session_act() -> None:
    src = ADAPTER.read_text(encoding="utf-8")
    assert "Session.act" not in src
    tree = ast.parse(src, filename=str(ADAPTER))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "act":
                pytest.fail("adapter must not call .act()")


def test_pyproject_benchmark_extra_pins_playwright() -> None:
    text = PYPROJECT.read_text(encoding="utf-8")
    assert 'benchmark = ["playwright==1.63.0"]' in text or "playwright==1.63.0" in text
