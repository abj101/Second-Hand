from __future__ import annotations

from pathlib import Path

from loop.config import project_root
from loop.live import clear_stop, reset_live


def clear_fixture_memory(
    fixture: str,
    *,
    root: Path | None = None,
) -> list[str]:
    """Remove persisted collect memory for one fixture. Returns deleted paths (as str)."""
    base = (root or project_root()) / "data" / "loop" / fixture
    removed: list[str] = []
    for name in (
        "journal.json",
        "memory.sqlite",
        "semantic_memory.json",
        "skills.json",
        "skills_evidence.jsonl",
    ):
        path = base / name
        if path.exists():
            path.unlink()
            removed.append(str(path))
    return removed


def clear_loop_memory(
    *,
    fixture: str | None = None,
    root: Path | None = None,
) -> dict[str, list[str]]:
    """Clear journal / sqlite / skills for one fixture or every fixture dir under data/loop."""
    loop_root = (root or project_root()) / "data" / "loop"
    if fixture is not None:
        targets = [fixture]
    else:
        targets = sorted(
            p.name
            for p in loop_root.iterdir()
            if p.is_dir() and p.name not in {"weights"}
        )
    out: dict[str, list[str]] = {}
    for name in targets:
        removed = clear_fixture_memory(name, root=root)
        if removed:
            out[name] = removed
    clear_stop(root)
    reset_live(root, message="memory cleared")
    return out
