"""Command-line entry point for the adaptive screen agent."""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help"}:
        print(
            "usage: python -m loop {collect,diagnostics,react} ...\n"
            "  collect  adaptive screen collection (see loop.adaptive.cli)\n"
            "  diagnostics  live read-only diagnostics side panel\n"
            "  react    Infinite Mario baseline report (optional benchmark extra)"
        )
        return 0
    command, rest = args[0], args[1:]
    if command == "collect":
        from loop.adaptive.cli import cmd_collect

        return cmd_collect(rest)
    if command == "diagnostics":
        from loop.adaptive.diagnostics_window import main as diagnostics_main

        return diagnostics_main(rest)
    if command == "react":
        from benchmarks.infinite_mario.run import main as react_main

        return react_main(rest)
    print(f"unknown command {command!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
