"""Qwen reaction choice parsing. Does not download weights."""

from PIL import Image

from loop.adaptive.candidates import CandidateAction, Directive
from loop.adaptive.policy import DecisionError, ReactiveField, ReactiveState
from loop.adaptive.qwen_policy import (
    QwenReactionPolicy,
    choice_from_text,
    render_choice_prompt,
    run_reaction_choose,
)
from loop.adaptive.schema import SceneGraph, SceneNode


def test_choice_from_text_takes_exact_offered_id():
    assert choice_from_text("act_settings\n", ("act_quit", "act_settings")) == "act_settings"


def test_choice_from_text_rejects_an_id_that_was_not_offered():
    try:
        choice_from_text("act_quit", ("act_settings",))
    except DecisionError as exc:
        assert "not offered" in str(exc)
    else:
        raise AssertionError("expected DecisionError")


def _snapshot() -> ReactiveState:
    return ReactiveState(
        snapshot_id="snap-1",
        schema_revision_id="schema-1",
        episode_id="ep-1",
        episode_generation=0,
        mode="menu",
        confidence=1.0,
        fields={"x": ReactiveField(1, 1.0, False)},
        entities=(),
        relations=(),
    )


def _directive() -> Directive:
    return Directive(
        revision=1,
        episode_generation=0,
        intent="choose",
        ttl_s=30.0,
        published_at=0.0,
    )


def _graph(*pairs: tuple[str, tuple[float, float, float, float]]) -> SceneGraph:
    nodes = tuple(
        SceneNode(
            node_id=f"node-{rid}",
            bbox=bbox,
            evidence_ids=(),
            attributes={"registry_id": rid, "label": "Settings"},
        )
        for rid, bbox in pairs
    )
    return SceneGraph(snapshot_id="snap-1", nodes=nodes)


def test_reaction_choose_offer_branches_and_ambiguous_crop(monkeypatch):
    frame = Image.new("RGB", (100, 100), color=(10, 20, 30))
    snap = _snapshot()
    directive = _directive()
    prompt_images: list[Image.Image | None] = []
    real_render = render_choice_prompt

    def track_render(state, candidates, *, image=None):
        prompt_images.append(image)
        return real_render(state, candidates, image=image)

    monkeypatch.setattr(
        "loop.adaptive.qwen_policy.render_choice_prompt",
        track_render,
    )

    generate_calls = {"n": 0}

    def fake_policy(*, reply: str) -> QwenReactionPolicy:
        policy = QwenReactionPolicy(None, None)

        def _generate(_prompt, *, image=None):
            generate_calls["n"] += 1
            return reply

        policy._generate = _generate  # type: ignore[method-assign]
        return policy

    zero = run_reaction_choose((), fake_policy(reply="unused"), snap, directive)
    assert zero.branch == "release"
    assert generate_calls["n"] == 0

    one = CandidateAction("only", "only", "Audio", available=True)
    single = run_reaction_choose((one,), fake_policy(reply="only"), snap, directive)
    assert single.branch == "deterministic"
    assert single.action_id == "only"
    assert generate_calls["n"] == 0

    two_labels = (
        CandidateAction("rid-a", "rid-a", "Audio", available=True),
        CandidateAction("rid-b", "rid-b", "Video", available=True),
    )
    policy_two = fake_policy(reply="rid-a")
    prompt_images.clear()
    out_two = run_reaction_choose(
        two_labels,
        policy_two,
        snap,
        directive,
        frame_image=frame,
        scene_graph=_graph(("rid-a", (0.1, 0.1, 0.3, 0.3)), ("rid-b", (0.5, 0.5, 0.7, 0.7))),
    )
    assert out_two.branch == "qwen"
    assert generate_calls["n"] == 1
    assert prompt_images == [None]

    same_label = (
        CandidateAction("rid-a", "rid-a", "Settings", available=True),
        CandidateAction("rid-b", "rid-b", "Settings", available=True),
    )
    policy_dup = fake_policy(reply="rid-b")
    prompt_images.clear()
    generate_calls["n"] = 0
    out_dup = run_reaction_choose(
        same_label,
        policy_dup,
        snap,
        directive,
        frame_image=frame,
        scene_graph=_graph(("rid-a", (0.1, 0.1, 0.2, 0.2)), ("rid-b", (0.6, 0.6, 0.7, 0.7))),
    )
    assert out_dup.branch == "qwen"
    assert generate_calls["n"] == 1
    assert len(prompt_images) == 1
    assert prompt_images[0] is not None
