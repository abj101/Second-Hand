"""Command-line entry point for the adaptive screen agent."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _run_fetch_playjev(argv: list[str] | None = None) -> int:
    repo = Path(__file__).resolve().parents[1]
    script = repo / "scripts" / "fetch_playjev_mario.py"
    spec = importlib.util.spec_from_file_location("fetch_playjev_mario", script)
    if spec is None or spec.loader is None:
        print("fetch script missing", file=sys.stderr)
        return 2
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    args = list(argv or [])
    if "--repo-root" not in args:
        args = ["--repo-root", str(repo), *args]
    return int(mod.main(args))


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help"}:
        print(
            "usage: python -m loop {collect,fetch-playjev,react} ...\n"
            "  collect       adaptive screen collection (see loop.adaptive.cli)\n"
            "  fetch-playjev populate PlayJev Mario cache under .cache/\n"
            "  react         Infinite Mario baseline report (optional benchmark extra)"
        )
        return 0
    command, rest = args[0], args[1:]
    if command == "collect":
        from loop.adaptive.cli import cmd_collect

        return cmd_collect(rest)
    if command == "fetch-playjev":
        return _run_fetch_playjev(rest)
    if command == "react":
        from benchmarks.infinite_mario.run import main as react_main

        return react_main(rest)
    print(f"unknown command {command!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
