"""Gate 1: learn, restart process, reacquire, no revision churn."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from loop.adaptive.schema import HEAD_FILENAME, load_head, revision_files

_REPO = Path(__file__).resolve().parents[2]


def _collect(workspace: Path, steps: int) -> dict:
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "loop",
            "collect",
            "--workspace",
            str(workspace),
            "--synthetic",
            "--steps",
            str(steps),
        ],
        cwd=_REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    return json.loads(proc.stdout)


def test_restart_replay_verifies_four_and_adds_zero_revisions():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        first = _collect(root, 16)
        schema_dir = root / "schema"
        n_revs = len(revision_files(schema_dir))
        head1 = json.loads((schema_dir / HEAD_FILENAME).read_text(encoding="utf-8"))[
            "revision_id"
        ]
        schema = load_head(schema_dir)
        assert schema.actions
        assert all("track_" not in a.action_id for a in schema.actions), [
            a.action_id for a in schema.actions
        ]
        assert first["verified_clicks"] >= 4, first

        second = _collect(root, 4)
        assert second["verified_clicks"] == 4, second
        assert len(revision_files(schema_dir)) == n_revs
        head2 = json.loads((schema_dir / HEAD_FILENAME).read_text(encoding="utf-8"))[
            "revision_id"
        ]
        assert head2 == head1
        assert second["revision_id"] == first["revision_id"]
