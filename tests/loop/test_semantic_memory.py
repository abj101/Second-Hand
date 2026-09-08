from __future__ import annotations

from loop.memory.semantic import SemanticMemoryStore, semantic_memory_path


def test_semantic_memory_reloads_across_collect_processes(tmp_path) -> None:
    first = SemanticMemoryStore.for_fixture("sts2", root=tmp_path)
    identity = first.remember_identity(
        screen="combat",
        role="card",
        name="Strike",
        provenance="OCR card title plus a verified play outcome",
        confidence=0.95,
        verified=True,
        step_id=12,
    )
    assert identity is not None
    first.save()

    second = SemanticMemoryStore.for_fixture("sts2", root=tmp_path)
    loaded = second.identities(screen="combat")
    assert len(loaded) == 1
    assert loaded[0].value["name"] == "Strike"
    assert loaded[0].verified is True
    assert second.path == semantic_memory_path("sts2", root=tmp_path)


def test_semantic_memory_rejects_unverified_or_frame_local_identity_bindings(tmp_path) -> None:
    store = SemanticMemoryStore.for_fixture("sts2", root=tmp_path)
    assert store.remember_identity(
        screen="combat", role="card", name="Strike", provenance="OCR", confidence=0.8, verified=False
    ) is None
    assert store.remember_identity(
        screen="combat", role="card", name="mk_3 is Strike", provenance="click", confidence=1.0, verified=True
    ) is None
    assert store.remember_identity(
        screen="combat", role="card", name="Strike", provenance="mk_3 hovered", confidence=1.0, verified=True
    ) is None
    assert not store.facts


def test_semantic_memory_conflict_prefers_verified_higher_confidence_and_decays(tmp_path) -> None:
    store = SemanticMemoryStore.for_fixture("sts2", root=tmp_path)
    first = store.remember_tutorial_rule(
        rule_id="energy",
        trigger="combat turn",
        action="play cards",
        effect="spend energy",
        provenance="tutorial OCR",
        confidence=0.60,
        verified=True,
        step_id=5,
    )
    assert first is not None
    winner = store.remember_tutorial_rule(
        rule_id="energy",
        trigger="combat turn",
        action="end turn",
        effect="refill energy",
        provenance="verified tutorial panel",
        confidence=0.90,
        verified=True,
        step_id=8,
    )
    assert winner is not None
    assert winner.value["action"] == "end turn"
    assert winner.conflict_count == 1
    assert store.conflicts[-1].winner == "contender"
    store.decay(step_id=20, stale_after=10, amount=0.2)
    assert winner.confidence == 0.70
    assert winner.decay_count == 1


def test_structured_tutorial_rule_persists_with_verification_status(tmp_path) -> None:
    store = SemanticMemoryStore.for_fixture("sts2", root=tmp_path)
    rule = store.remember_tutorial_rule(
        rule_id="attack",
        trigger="enemy is alive",
        action="play an attack card",
        effect="enemy loses HP",
        provenance="tutorial OCR confirmed by combat outcome",
        confidence=0.9,
        verified=True,
        step_id=4,
    )
    assert rule is not None
    store.save()
    reloaded = SemanticMemoryStore.for_fixture("sts2", root=tmp_path)
    rules = reloaded.tutorial_rules(verified_only=True)
    assert len(rules) == 1
    assert rules[0].value == {
        "rule_id": "attack",
        "trigger": "enemy is alive",
        "action": "play an attack card",
        "effect": "enemy loses HP",
    }


def test_semantic_store_clear_removes_persisted_file(tmp_path) -> None:
    store = SemanticMemoryStore.for_fixture("sts2", root=tmp_path)
    assert store.remember_tutorial_rule(
        rule_id="block", trigger="need defense", action="play Defend", effect="gain block",
        provenance="tutorial OCR", confidence=0.8, verified=True,
    ) is not None
    store.save()
    assert store.path.exists()
    store.clear()
    assert not store.path.exists()
    assert not store.facts
