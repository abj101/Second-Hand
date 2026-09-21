"""Fetch pinned PlayJev checkout into ignored local cache (no vendoring in repo)."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

DEFAULT_REVISION = "2d7a0280841e3244a299c9c640efc8cefc46d476"
PLAYJEV_URL = "https://github.com/OmniJev/PlayJev"

_HASH_PATHS = (
    "LICENSE",
    "games/mario/LICENSE.md",
    "games/mario/UPSTREAM",
    "games/mario/pj.json",
    "games/mario/pj_hook.js",
    "games/mario/check_det.py",
    "games/mario/NOTES.md",
    "games/mario/TEACHER.md",
    "games/_shared/pj_shim.js",
    "playjev/env.py",
)


def cache_dir(repo_root: Path, revision: str = DEFAULT_REVISION) -> Path:
    return Path(repo_root) / ".cache" / "benchmarks" / "playjev" / revision


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _git_head(checkout: Path) -> str:
    out = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=checkout,
        check=True,
        capture_output=True,
        text=True,
    )
    return out.stdout.strip()


def _git_describe(checkout: Path) -> str:
    out = subprocess.run(
        ["git", "describe", "--always", "--dirty"],
        cwd=checkout,
        check=True,
        capture_output=True,
        text=True,
    )
    return out.stdout.strip()


def ensure_playjev(
    repo_root: Path,
    revision: str = DEFAULT_REVISION,
    *,
    url: str = PLAYJEV_URL,
) -> Path:
    dest = cache_dir(repo_root, revision)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_dir() and (dest / ".git").is_dir():
        subprocess.run(
            ["git", "fetch", "--depth", "1", "origin", revision],
            cwd=dest,
            check=False,
        )
        subprocess.run(["git", "checkout", revision], cwd=dest, check=True)
        _write_fetch_record(dest, revision, url)
        return dest

    if dest.exists():
        raise RuntimeError(f"cache path exists but is not a git checkout: {dest}")

    subprocess.run(["git", "clone", url, str(dest)], check=True)
    subprocess.run(["git", "checkout", revision], cwd=dest, check=True)
    _write_fetch_record(dest, revision, url)
    return dest


def _write_fetch_record(checkout: Path, revision: str, url: str) -> None:
    hashes = {}
    for rel in _HASH_PATHS:
        p = checkout / rel
        if p.is_file():
            hashes[rel] = _sha256(p)
    record = {
        "playjev_url": url,
        "playjev_revision": revision,
        "git_head": _git_head(checkout),
        "git_describe": _git_describe(checkout),
        "file_sha256": hashes,
        "artwork_notice": (
            "Nintendo sprite artwork in games/mario is not licensed for redistribution; "
            "do not commit frames, datasets, or screenshots for public distribution."
        ),
    }
    (checkout / "FETCH_RECORD.json").write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch PlayJev Mario benchmark cache")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--revision", default=DEFAULT_REVISION)
    args = parser.parse_args(argv)
    path = ensure_playjev(args.repo_root.resolve(), args.revision)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
