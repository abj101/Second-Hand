"""Wave 1 adaptive kernel contract tests."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from loop.adaptive.schema import (
    HEAD_FILENAME,
    atomic_write_revision,
    empty_schema,
    hash_payload,
    load_revision,
    schema_payload,
    with_revision,
    write_head,
)
from loop.adaptive.session import Session
from loop.adaptive.world import (
    CONTROL_NODE_ID,
    ENTITY_NODE_ID,
    ENTITY_TYPE_ID,
    SCALAR_FIELD_ID,
    NeutralWorld,
)

def test_empty_schema_contains_no_domain_model():
    schema = empty_schema()
    assert schema.fields == ()
    assert schema.entity_types == ()
    assert schema.modes == ()
    assert schema.sensors == ()
    assert schema.actions == ()


def test_atomic_revision_write_immutable_and_parent_link():
    schema = empty_schema()
    child = with_revision(schema, parent_id=schema.revision.revision_id)
    assert child.revision.parent_id == schema.revision.revision_id
    assert child.revision.revision_id != schema.revision.revision_id

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        p1 = atomic_write_revision(root, schema)
        assert p1.name == f"{schema.revision.revision_id}.json"
        loaded = load_revision(p1)
        assert loaded.revision.revision_id == schema.revision.revision_id
        assert loaded.revision.payload_hash == schema.revision.payload_hash

        with pytest.raises(FileExistsError):
            atomic_write_revision(root, schema)

        p2 = atomic_write_revision(root, child)
        loaded_child = load_revision(p2)
        assert loaded_child.revision.parent_id == schema.revision.revision_id


def test_neutral_world_labels_vary_but_stable_ids():
    w1 = NeutralWorld()
    w2 = NeutralWorld()
    g1 = w1.reset(7)
    g2 = w2.reset(99)
    ids1 = {n.node_id for n in g1.nodes}
    ids2 = {n.node_id for n in g2.nodes}
    assert ids1 == ids2 == {ENTITY_NODE_ID, CONTROL_NODE_ID, "node_display_primary"}

    _, _, t1 = w1.observe()
    _, _, t2 = w2.observe()
    assert t1["labels"] != t2["labels"]
    assert t1["scalar_field_id"] == t2["scalar_field_id"] == SCALAR_FIELD_ID
    assert t1["entity_type_id"] == ENTITY_TYPE_ID


def test_neutral_world_layout_does_not_change_node_ids():
    base = NeutralWorld()
    scaled = NeutralWorld(layout_scale=1.5, origin=(0.05, 0.05))
    g0 = base.reset(42)
    g1 = scaled.reset(42)
    assert {n.node_id for n in g0.nodes} == {n.node_id for n in g1.nodes}
    base.click(CONTROL_NODE_ID)
    scaled.click(CONTROL_NODE_ID)
    _, _, tb = base.observe()
    _, _, ts = scaled.observe()
    assert tb["scalar_value"] == ts["scalar_value"] == 1


def test_head_points_at_latest_revision_and_session_loads_it():
    schema = empty_schema()
    child = with_revision(schema, parent_id=schema.revision.revision_id)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        schema_dir = root / "schema"
        schema_dir.mkdir()
        atomic_write_revision(schema_dir, schema)
        atomic_write_revision(schema_dir, child)
        head = json.loads((schema_dir / HEAD_FILENAME).read_text(encoding="utf-8"))
        assert head["revision_id"] == child.revision.revision_id
        session = Session(root)
        assert session.schema.revision.revision_id == child.revision.revision_id


def test_session_respects_head_not_lexicographic_max():
    schema = empty_schema()
    child = with_revision(schema, parent_id=schema.revision.revision_id)
    ids = sorted([schema.revision.revision_id, child.revision.revision_id])
    smaller_id, larger_id = ids[0], ids[1]
    assert smaller_id != larger_id
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        schema_dir = root / "schema"
        schema_dir.mkdir()
        atomic_write_revision(schema_dir, schema)
        atomic_write_revision(schema_dir, child)
        write_head(schema_dir, smaller_id)
        session = Session(root)
        assert session.schema.revision.revision_id == smaller_id


def test_load_revision_rejects_tampered_payload_hash():
    schema = empty_schema()
    with tempfile.TemporaryDirectory() as tmp:
        path = atomic_write_revision(Path(tmp), schema)
        data = json.loads(path.read_text(encoding="utf-8"))
        data["revision"]["payload_hash"] = "0" * 64
        path.write_text(json.dumps(data, sort_keys=True, indent=2), encoding="utf-8")
        with pytest.raises(ValueError, match="payload_hash"):
            load_revision(path)


def test_load_revision_rejects_tampered_field():
    schema = empty_schema()
    with tempfile.TemporaryDirectory() as tmp:
        path = atomic_write_revision(Path(tmp), schema)
        data = json.loads(path.read_text(encoding="utf-8"))
        data["fields"] = [{"field_id": "x", "value_type": "number"}]
        path.write_text(json.dumps(data, sort_keys=True, indent=2), encoding="utf-8")
        with pytest.raises(ValueError, match="payload_hash"):
            load_revision(path)


def test_load_revision_requires_parent_file():
    parent = empty_schema()
    orphan = with_revision(parent, parent_id=parent.revision.revision_id)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        orphan_path = root / f"{orphan.revision.revision_id}.json"
        orphan_path.write_text(
            json.dumps(schema_payload(orphan), sort_keys=True, indent=2),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="missing parent"):
            load_revision(orphan_path)


def test_session_missing_head_with_multiple_revisions_raises():
    schema = empty_schema()
    child = with_revision(schema, parent_id=schema.revision.revision_id)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        schema_dir = root / "schema"
        schema_dir.mkdir()
        atomic_write_revision(schema_dir, schema)
        atomic_write_revision(schema_dir, child)
        (schema_dir / HEAD_FILENAME).unlink()
        with pytest.raises(ValueError, match="missing HEAD"):
            Session(root)


def test_schema_hash_stable_for_equal_payload():
    a = empty_schema()
    pa = schema_payload(a)
    h1 = hash_payload(pa)
    h2 = hash_payload(json.loads(json.dumps(pa, sort_keys=True)))
    assert h1 == h2

    pa_for_digest = json.loads(json.dumps(pa, sort_keys=True))
    pa_for_digest["revision"]["payload_hash"] = ""
    assert hash_payload(pa_for_digest) == a.revision.payload_hash

    b = empty_schema()
    pb = schema_payload(b)
    pa_cmp = {k: v for k, v in pa.items() if k != "revision"}
    pb_cmp = {k: v for k, v in pb.items() if k != "revision"}
    assert pa_cmp == pb_cmp
    assert hash_payload(pa_cmp) == hash_payload(pb_cmp)
