import json
from dataclasses import dataclass

import pytest

from loop.adaptive.game_brief import GameBriefError, select_game_brief
from loop.adaptive.cli import cmd_collect
from loop.adaptive.controller import GenerationStore, NativeShadowConfig, _bootstrap_directive
from loop.adaptive.orchestrate import make_evidence_bundle
from loop.adaptive.experiment import make_gateway_scouts
from loop.adaptive.orchestrator import OrchestratorConfig, ensure_directive
from loop.adaptive.schema import SceneGraph, empty_schema
from loop.adaptive.semantic_confirmation import CandidateEvidence, SemanticConfirmer


def _write_brief(root, *, game_id="demo", aliases=None, objective="win"):
    payload = {
        "format_version": 1,
        "game_id": game_id,
        "display_name": "Demo",
        "aliases": aliases or ["Demo Window"],
        "objective": objective,
        "mechanics": ["turns"],
        "controls": [{"action": "click", "input": "primary", "semantics": "select"}],
        "observation_focus": ["target"],
        "strategy": ["survive"],
        "uncertainties": ["bindings"],
        "sources": [{"url": "https://example.test", "note": "test"}],
        "verified_on": "2026-09-22",
    }
    path = root / f"{game_id}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_exact_selection_and_unknown_explicit_error(tmp_path):
    _write_brief(tmp_path, aliases=["Demo Window"])
    assert select_game_brief("demo", directory=tmp_path).game_id == "demo"
    assert select_game_brief(target_title="DEMO WINDOW", directory=tmp_path).game_id == "demo"
    assert select_game_brief(target_title="Demo Window (1)", directory=tmp_path) is None
    with pytest.raises(GameBriefError, match="unknown game brief"):
        select_game_brief("missing", directory=tmp_path)


def test_ambiguous_alias_is_rejected(tmp_path):
    _write_brief(tmp_path, game_id="one", aliases=["same"])
    _write_brief(tmp_path, game_id="two", aliases=["other"])
    second = tmp_path / "two.json"
    payload = json.loads(second.read_text(encoding="utf-8"))
    payload["aliases"] = ["same"]
    second.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(GameBriefError, match="ambiguous"):
        select_game_brief("same", directory=tmp_path)


def test_bundle_contains_brief_and_digest_changes(tmp_path):
    path = _write_brief(tmp_path, objective="first")
    brief = select_game_brief("demo", directory=tmp_path)
    scene = SceneGraph("screen", ())
    bundle1 = make_evidence_bundle(
        image_hash="frame", scene=scene, schema=empty_schema(), evidence=(), stability={},
        game_brief=brief.as_dict(), game_brief_provenance=brief.provenance(),
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["objective"] = "second"
    path.write_text(json.dumps(payload), encoding="utf-8")
    brief2 = select_game_brief("demo", directory=tmp_path)
    bundle2 = make_evidence_bundle(
        image_hash="frame", scene=scene, schema=empty_schema(), evidence=(), stability={},
        game_brief=brief2.as_dict(), game_brief_provenance=brief2.provenance(),
    )
    assert bundle1["game_brief"]["objective"] == "first"
    assert bundle1["digest"] != bundle2["digest"]
    assert bundle1["game_brief_provenance"]["sha256"] != bundle2["game_brief_provenance"]["sha256"]


def test_gateway_scout_receives_full_brief(monkeypatch, tmp_path):
    gateway = _Gateway()
    monkeypatch.setattr("loop.model.gateway.ProposalGateway.from_env", lambda: gateway)
    scouts = make_gateway_scouts(tmp_path)
    assert scouts is not None
    brief = select_game_brief("sts2")
    bundle = make_evidence_bundle(
        image_hash="frame", scene=SceneGraph("screen", ()), schema=empty_schema(), evidence=(), stability={},
        game_brief=brief.as_dict(), game_brief_provenance=brief.provenance(),
    )
    scouts[0](bundle)
    assert json.loads(gateway.user)["game_brief"]["game_id"] == "sts2"


def test_objective_is_orchestrator_fallback_but_explicit_intent_wins():
    store = _Store()
    brief = {"objective": "complete the climb"}
    d = ensure_directive(store, episode_generation=0, config=OrchestratorConfig(game_brief=brief))
    assert d.intent == "complete the climb"
    explicit = ensure_directive(
        _Store(), episode_generation=0,
        config=OrchestratorConfig(default_intent="operator intent", game_brief=brief),
    )
    assert explicit.intent == "operator intent"


def test_native_shadow_bootstrap_uses_objective_without_reactive_payload():
    directive = _bootstrap_directive(
        GenerationStore(),
        episode_generation=0,
        config=NativeShadowConfig(game_brief={"objective": "survive the climb"}),
    )
    assert directive.intent == "survive the climb"
    assert "game_brief" not in directive.__dict__


def test_cli_synthetic_selection_records_brief_provenance(tmp_path):
    assert cmd_collect(["--workspace", str(tmp_path), "--synthetic", "--game", "sts2", "--steps", "1"]) == 0
    rows = [json.loads(line) for line in (tmp_path / "ledger.jsonl").read_text(encoding="utf-8").splitlines()]
    selected = [row for row in rows if row["kind"] == "game_brief_selected"]
    assert selected and selected[-1]["payload"]["game_id"] == "sts2"
    assert len(selected[-1]["payload"]["sha256"]) == 64


class _Store:
    def __init__(self):
        self.value = None

    def latest(self):
        return self.value

    def publish(self, _generation, directive):
        self.value = directive


@dataclass
class _Call:
    model: str = "m"
    latency_ms: float | None = None
    prompt_tokens: int | None = None
    output_tokens: int | None = None
    parsed: dict | None = None


class _Gateway:
    def __init__(self):
        self.user = ""

    def complete_json(self, *, system, user, json_schema, schema_name, frames, timeout_s=None):
        self.user = user
        return _Call(parsed={
            "target_identity": "target",
            "screen_generation": "generation",
            "classifications": [{"candidate_id": "id", "class": "control", "rationale": "visible"}],
        })


def test_semantic_prompt_receives_brief_and_replacement_clears_context():
    gateway = _Gateway()
    confirmer = SemanticConfirmer(gateway, model="m", game_brief={"objective": "first"})
    candidate = CandidateEvidence("id", "button", (0.1, 0.1, 0.2, 0.2))
    confirmer.observe(
        target_identity="target", screen_generation="generation", observation_id="obs",
        candidates=[candidate], frame=object(),
    )
    assert '"objective":"first"' in gateway.user
    confirmer.set_game_brief({"objective": "second"})
    assert not confirmer._contexts  # context cache is scoped to advisory brief


def test_default_bundle_has_no_brief_leakage():
    bundle = make_evidence_bundle(
        image_hash="frame", scene=SceneGraph("screen", ()), schema=empty_schema(), evidence=(), stability={}
    )
    assert "game_brief" not in bundle


@pytest.mark.parametrize("field,value", [
    ("format_version", True), ("format_version", 2),
    ("verified_on", "20260922"), ("controls", "click"),
    ("objective", "x" * 8001),
])
def test_invalid_brief_is_rejected(tmp_path, field, value):
    path = _write_brief(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload[field] = value
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(GameBriefError):
        select_game_brief("demo", directory=tmp_path)


def test_pinned_title_selection_does_not_retain_previous_game():
    from types import SimpleNamespace
    from loop.adaptive.cli import _select_bound_game_brief

    assert _select_bound_game_brief(None, SimpleNamespace(title="Slay the Spire 2")).game_id == "sts2"
    assert _select_bound_game_brief(None, SimpleNamespace(title="Different game")) is None
    with pytest.raises(GameBriefError):
        select_game_brief("")


def test_semantic_factory_and_reaction_request_boundaries(monkeypatch):
    from loop.adaptive.cli import _make_semantic_confirmer
    from loop.adaptive.decider_policy import build_decider_state
    from loop.adaptive.policy import ReactiveState

    gateway = _Gateway()
    gateway.model = "m"
    monkeypatch.setattr("loop.model.gateway.ProposalGateway.from_env", lambda: gateway)
    brief = select_game_brief("sts2")
    assert _make_semantic_confirmer("off", brief) is None
    confirmer = _make_semantic_confirmer("required", brief)
    assert confirmer._game_brief == brief.as_dict()
    directive = _bootstrap_directive(
        GenerationStore(), episode_generation=0,
        config=NativeShadowConfig(game_brief=brief.as_dict()),
    )
    state = ReactiveState("s", "r", "ep", 0, None, 1.0, {}, (), ())
    payload = build_decider_state(state, directive)
    assert payload["directive"]["intent"] == brief.objective
    serialized = json.dumps(payload)
    assert "game_brief" not in serialized
    assert all(control["input"] not in serialized for control in brief.controls)
