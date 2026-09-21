"""Wave 3 adaptive orchestrator: generic promote + validate."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

pytest.importorskip("loop.adaptive.schema")

from loop.adaptive.schema import (
    EvidenceRecord,
    PromotionPolicy,
    atomic_write_revision,
    empty_schema,
    load_revision,
)
from loop.adaptive.orchestrate import (
    Proposal,
    SchemaDiff,
    Trace,
    clear_seed_registry_for_tests,
    compile_schema,
    hypothesize,
    load_seed,
    orchestrate,
    probe,
    promote,
    validate,
)

pytestmark = pytest.mark.usefixtures("reset_seed_registry")


@pytest.fixture
def reset_seed_registry():
    clear_seed_registry_for_tests()
    yield
    clear_seed_registry_for_tests()


def _ocr(text: str, *, number: int | None = None) -> EvidenceRecord:
    payload: dict = {"text": text}
    if number is not None:
        payload["number"] = number
    eid = f"ev_{text}_{number}" if number is not None else f"ev_{text}"
    return EvidenceRecord(eid, "ocr", payload)


def _interaction(field_id: str, region: str, action_id: str) -> EvidenceRecord:
    return EvidenceRecord(
        f"ev_ix_{action_id}",
        "interaction_delta",
        {
            "field_id": field_id,
            "clickable_region": region,
            "action_id": action_id,
            "op": "increased",
        },
    )


def _trace(
    before: dict,
    after: dict,
    *,
    action_id: str | None = None,
    label: str = "alpha",
) -> Trace:
    ev = (_ocr(label, number=after.get(label, before.get(label, 0))),)
    return Trace(
        before_fields=before,
        after_fields=after,
        before_evidence=ev,
        after_evidence=ev,
        action_id=action_id,
    )


def _loose_policy() -> PromotionPolicy:
    return PromotionPolicy(min_agreeing_reads=2, min_correlated_deltas=2, require_replay=True)


def test_promote_counter_field_and_increment_action():
    field_id = "alpha"
    action_id = "act_btn"
    policy = _loose_policy()

    evidence = (
        _ocr(field_id, number=0),
        _interaction(field_id, "btn_primary", action_id),
    )
    schema = empty_schema()
    proposals = hypothesize(evidence, schema)
    field_p = next(p for p in proposals if p.kind == "field" and p.suggested_id == field_id)
    action_p = next(p for p in proposals if p.kind == "action" and p.suggested_id == action_id)

    traces = (
        _trace({field_id: 0}, {field_id: 1}, action_id=action_id, label=field_id),
        _trace({field_id: 1}, {field_id: 2}, action_id=action_id, label=field_id),
        _trace({field_id: 2}, {field_id: 2}, action_id=action_id, label=field_id),
    )

    assert validate(field_p, traces, schema, policy).ok
    schema = promote(schema, field_p, traces, policy)
    assert any(f.field_id == field_id for f in schema.fields)

    effect_p = Proposal(
        "effect",
        f"effect_{field_id}_changed",
        {"field_id": field_id, "op": "increased", "action_id": action_id},
    )
    assert validate(action_p, traces, schema, policy).ok
    assert validate(effect_p, traces, schema, policy).ok

    schema = promote(schema, action_p, traces, policy)
    schema = promote(schema, effect_p, traces, policy)
    assert any(a.action_id == action_id for a in schema.actions)
    act = next(a for a in schema.actions if a.action_id == action_id)
    assert act.expected_effects and act.expected_effects[0].field_id == field_id


def test_arbitrary_field_name_same_as_gold():
    policy = _loose_policy()
    for field_id in ("alpha", "gold", "zeta_9"):
        schema = empty_schema()
        evidence = (_ocr(field_id, number=1),)
        field_p = hypothesize(evidence, schema)[0]
        assert field_p.suggested_id == field_id
        traces = (
            _trace({field_id: 0}, {field_id: 1}, label=field_id),
            _trace({field_id: 1}, {field_id: 2}, label=field_id),
        )
        assert validate(field_p, traces, schema, policy).ok
        promoted = promote(schema, field_p, traces, policy)
        assert any(f.field_id == field_id for f in promoted.fields)


def test_unlabeled_numeric_token_gets_spatial_field_id():
    rec = EvidenceRecord(
        "ev_value",
        "ocr",
        {"text": "7/10", "number": 7, "maximum": 10, "bbox": [0.1, 0.2, 0.2, 0.3]},
    )
    proposal = next(p for p in hypothesize((rec,), empty_schema()) if p.kind == "field")
    assert proposal.suggested_id == "field_150_250"
    assert proposal.payload["bbox"] == [0.1, 0.2, 0.2, 0.3]
    traces = (
        Trace({}, {}, (rec,), (rec,)),
        Trace({}, {}, (rec,), (rec,)),
    )
    assert validate(proposal, traces, empty_schema(), _loose_policy()).ok


def test_noop_traces_do_not_validate_effect():
    field_id = "alpha"
    action_id = "act_btn"
    policy = _loose_policy()
    schema = empty_schema()
    effect_p = Proposal(
        "effect",
        f"effect_{field_id}_changed",
        {"field_id": field_id, "op": "increased", "action_id": action_id},
    )
    noop_traces = (
        _trace({field_id: 3}, {field_id: 3}, action_id=action_id, label=field_id),
        _trace({field_id: 3}, {field_id: 3}, action_id=action_id, label=field_id),
    )
    v = validate(effect_p, noop_traces, schema, policy)
    assert not v.ok
    assert any("no-op" in r for r in v.reasons)


def test_seed_bytes_unchanged_second_promote_parent_link():
    policy = _loose_policy()
    field_id = "alpha"
    action_id = "act_btn"

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        seed_dir = root / "seed"
        store_dir = root / "store"
        seed_dir.mkdir()
        store_dir.mkdir()

        seed_schema = empty_schema()
        seed_path = atomic_write_revision(seed_dir, seed_schema)
        seed_bytes = seed_path.read_bytes()

        schema = load_seed(seed_path)
        evidence = (
            _ocr(field_id, number=0),
            _interaction(field_id, "btn", action_id),
        )
        field_p = next(p for p in hypothesize(evidence, schema) if p.kind == "field")
        traces = (
            _trace({field_id: 0}, {field_id: 1}, action_id=action_id, label=field_id),
            _trace({field_id: 1}, {field_id: 2}, action_id=action_id, label=field_id),
        )
        assert validate(field_p, traces, schema, policy).ok

        schema = promote(schema, field_p, traces, policy, store_dir=store_dir)
        parent1 = schema.revision.revision_id
        assert schema.revision.parent_id == seed_schema.revision.revision_id

        action_p = next(p for p in hypothesize(evidence, schema) if p.kind == "action")
        schema = promote(schema, action_p, traces, policy, store_dir=store_dir)
        assert schema.revision.revision_id != parent1
        assert schema.revision.parent_id == parent1

        assert seed_path.read_bytes() == seed_bytes
        store_files = [p for p in store_dir.glob("*.json") if p.name != "HEAD.json"]
        assert len(store_files) == 2
        loaded = load_revision(store_dir / f"{schema.revision.revision_id}.json")
        assert loaded.revision.parent_id == parent1


def test_compile_schema_adds_sensors_for_fields():
    policy = _loose_policy()
    schema = empty_schema()
    field_id = "alpha"
    field_p = Proposal(
        "field",
        field_id,
        {"field_id": field_id, "value_type": "number", "label": field_id},
    )
    traces = (
        _trace({field_id: 0}, {field_id: 1}, label=field_id),
        _trace({field_id: 1}, {field_id: 2}, label=field_id),
    )
    schema = promote(schema, field_p, traces, policy)
    compiled = compile_schema(schema)
    assert len(compiled.sensors) >= 1
    assert any(s.sensor_id == "sensor_alpha" for s in compiled.sensors)


def test_promote_without_agreeing_traces_raises():
    policy = _loose_policy()
    schema = empty_schema()
    field_p = Proposal(
        "field",
        "alpha",
        {"field_id": "alpha", "value_type": "number", "label": "alpha"},
    )
    with pytest.raises(ValueError):
        promote(schema, field_p, (), policy)


def test_repromote_same_field_is_idempotent():
    policy = _loose_policy()
    field_id = "alpha"
    with tempfile.TemporaryDirectory() as tmp:
        store_dir = Path(tmp)
        schema = empty_schema()
        field_p = Proposal(
            "field",
            field_id,
            {"field_id": field_id, "value_type": "number", "label": field_id},
        )
        traces = (
            _trace({field_id: 0}, {field_id: 1}, label=field_id),
            _trace({field_id: 1}, {field_id: 2}, label=field_id),
        )
        schema = promote(schema, field_p, traces, policy, store_dir=store_dir)
        rid = schema.revision.revision_id
        n_files = len([p for p in store_dir.glob("*.json") if p.name != "HEAD.json"])
        again = promote(schema, field_p, traces, policy, store_dir=store_dir)
        assert again.revision.revision_id == rid
        assert len([p for p in store_dir.glob("*.json") if p.name != "HEAD.json"]) == n_files


def test_probe_action_noop_traces_not_ok():
    field_id = "alpha"
    action_id = "act_btn"
    action_p = Proposal(
        "action",
        action_id,
        {
            "action_id": action_id,
            "clickable_region": "btn",
            "field_id": field_id,
        },
    )
    noop_traces = (
        _trace({field_id: 3}, {field_id: 3}, action_id=action_id, label=field_id),
        _trace({field_id: 3}, {field_id: 3}, action_id=action_id, label=field_id),
    )
    assert not probe(action_p, noop_traces).ok


def test_compile_schema_idempotent_same_revision():
    policy = _loose_policy()
    schema = empty_schema()
    field_id = "alpha"
    field_p = Proposal(
        "field",
        field_id,
        {"field_id": field_id, "value_type": "number", "label": field_id},
    )
    traces = (
        _trace({field_id: 0}, {field_id: 1}, label=field_id),
        _trace({field_id: 1}, {field_id: 2}, label=field_id),
    )
    schema = promote(schema, field_p, traces, policy)
    compiled = compile_schema(schema)
    again = compile_schema(compiled)
    assert again.revision.revision_id == compiled.revision.revision_id
    assert again is compiled


def test_orchestrate_without_proposer_equals_hypothesize():
    schema = empty_schema()
    evidence = (
        _ocr("alpha", number=0),
        _interaction("alpha", "btn_primary", "act_btn"),
    )
    assert orchestrate(evidence, schema) == hypothesize(evidence, schema)


def test_orchestrate_proposer_filters_invalid_candidates():
    schema = empty_schema()
    rec = _ocr("alpha", number=1)
    evidence = (rec,)

    def proposer(candidates, _schema):
        yield SchemaDiff(
            "field",
            "bad",
            ("missing_id",),
            {"field_id": "bad", "value_type": "number"},
        )
        yield SchemaDiff(
            "field",
            "alpha",
            (rec.evidence_id,),
            {"field_id": "alpha", "value_type": "number", "label": "alpha"},
        )

    proposals = orchestrate(evidence, schema, proposer=proposer)
    assert len(proposals) == 1
    assert proposals[0].suggested_id == "alpha"
    assert proposals[0].payload["candidate_ids"] == [rec.evidence_id]


def test_orchestrate_from_model_drops_coordinates():
    schema = empty_schema()
    rec = _ocr("alpha", number=1)

    def proposer(candidates, _schema):
        yield SchemaDiff(
            "field",
            "alpha",
            (rec.evidence_id,),
            {"field_id": "alpha", "bbox": [0.1, 0.1, 0.2, 0.2]},
        )

    assert orchestrate((rec,), schema, proposer=proposer, from_model=True) == ()
