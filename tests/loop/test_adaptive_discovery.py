"""Empty-schema discovery from NeutralWorld pixels."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from loop.adaptive.cli import cmd_collect
from loop.adaptive.schema import HEAD_FILENAME, load_head
from loop.adaptive.session import Session


def test_empty_workspace_discovers_schema_and_reloads_head():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        rc = cmd_collect(["--workspace", str(root), "--synthetic", "--steps", "16"])
        assert rc == 0

        schema_dir = root / "schema"
        head = json.loads((schema_dir / HEAD_FILENAME).read_text(encoding="utf-8"))
        session = Session(root)
        schema = session.schema
        assert schema.revision.revision_id == head["revision_id"]
        assert schema.fields, schema
        assert schema.entity_types, schema
        assert schema.modes, schema
        assert schema.actions, schema
        assert schema.sensors, schema
        assert any(a.expected_effects for a in schema.actions), schema.actions

        again = Session(root)
        assert again.schema.revision.revision_id == head["revision_id"]
        loaded = load_head(schema_dir)
        assert loaded.revision.revision_id == head["revision_id"]
