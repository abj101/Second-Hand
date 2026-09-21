"""Gate 1 schema v2 wave 1a checks."""

from __future__ import annotations

import tempfile
from dataclasses import replace
from pathlib import Path

import pytest

from loop.adaptive.schema import (
    ActionSpec,
    FieldSpec,
    _from_dict,
    atomic_write_revision,
    empty_schema,
    hash_payload,
    load_revision,
    schema_payload,
)


def test_schema_v2_wave1a():
    with pytest.raises(ValueError):
        ActionSpec(
            action_id="act_track_2",
            parameters=(),
            preconditions=(),
            primitive_plan=(),
            expected_effects=(),
        )

    schema = replace(
        empty_schema(),
        fields=(
            FieldSpec(
                "f1",
                "number",
                sensor_id="s0",
                selector={"region": "hud"},
            ),
        ),
    )
    draft = replace(schema, revision=replace(schema.revision, payload_hash=""))
    schema = replace(
        schema,
        revision=replace(schema.revision, payload_hash=hash_payload(schema_payload(draft))),
    )
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = atomic_write_revision(root, schema)
        loaded = load_revision(path)
    assert loaded.fields[0].selector == {"region": "hud"}

    fs = _from_dict(FieldSpec, {"field_id": "f", "value_type": "number"})
    assert fs.field_id == "f"
    assert fs.selector == {}
