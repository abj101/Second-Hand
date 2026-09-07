from __future__ import annotations

from loop.agent.perceive import SceneDescription
from loop.os.aim import AimMark, _label_lateral_nav, _merge_aim_marks, _scope_overlay_marks
from loop.os.frame_model import FrameObject, _control_ids, build_frame_model, fuse_scene_with_frame


def _mark(mid: int, label: str, x: float, y: float) -> AimMark:
    return AimMark(
        id=mid,
        x=x,
        y=y,
        x0=max(0.0, x - 0.05),
        y0=max(0.0, y - 0.05),
        x1=min(1.0, x + 0.05),
        y1=min(1.0, y + 0.05),
        label=label,
    )


def test_interactive_labels_exclude_visible_text() -> None:
    desc = SceneDescription.from_parsed(
        {
            "screen": "tutorial",
            "controls": [{"label": "End Turn", "role": "button"}],
            "visible_text": ["Defeat enemies by playing cards from your hand!"],
        }
    )
    assert "End Turn" in desc.interactive_labels()
    assert "Defeat enemies" not in " ".join(desc.interactive_labels())
    assert "Defeat enemies" in desc.informational_text()[0]


def test_as_text_separates_interactive_and_visible() -> None:
    desc = SceneDescription.from_parsed(
        {
            "screen": "tutorial",
            "controls": [{"label": "Strike", "role": "card"}],
            "visible_text": ["Tutorial body copy here"],
        }
    )
    text = desc.as_text()
    assert "interactive=Strike" in text
    assert "visible=Tutorial body copy here" in text
    assert "controls=" not in text


def test_frame_model_interactive_vs_informational() -> None:
    marks = (
        _mark(1, "Back", 0.08, 0.82),
        _mark(2, "Strike", 0.4, 0.86),
        _mark(3, "Prev", 0.1, 0.5),
        _mark(4, "Next", 0.9, 0.5),
    )
    frame = build_frame_model(
        marks,
        screen="tutorial",
        host_stats={"screen": "tutorial", "tutorial": {"active": True, "step": 1, "total": 3}},
        informational=["Defeat enemies by playing cards from your hand!"],
    )
    assert "Strike" in frame.interactive_labels()
    assert "Back" in frame.interactive_labels()
    assert "Next" in frame.interactive_labels()
    assert frame.layout == "paginated_overlay"
    assert any("Defeat enemies" in t for t in frame.informational_text())


def test_fuse_scene_prefers_frame_labels() -> None:
    marks = (_mark(1, "Strike", 0.4, 0.86),)
    frame = build_frame_model(marks, screen="tutorial")
    fused = fuse_scene_with_frame(["Confirm", "End Turn"], frame)
    assert fused == ["Strike"]


def test_overlay_scope_keeps_back_and_strike() -> None:
    marks = [
        _mark(1, "Back", 0.08, 0.82),
        _mark(2, "Strike", 0.4, 0.86),
        _mark(3, "icon", 0.5, 0.4),
    ]
    scoped = _scope_overlay_marks(marks, screen="tutorial", dialog=False)
    labels = {m.label for m in scoped}
    assert "Back" in labels
    assert "Strike" in labels


def test_lateral_nav_labels_side_icons() -> None:
    marks = [
        _mark(1, "icon", 0.12, 0.5),
        _mark(2, "icon", 0.88, 0.5),
        _mark(3, "Back", 0.08, 0.82),
    ]
    labeled = _label_lateral_nav(marks)
    by_label = {m.label for m in labeled}
    assert "Prev" in by_label
    assert "Next" in by_label


def test_control_ids_regenerate_object_prefixed_domain_ids() -> None:
    enemy = FrameObject(
        id=10, label="Nibbit", kind="interactive", role="enemy", object_id="object:10",
    )
    assert _control_ids([enemy])[0].object_id == "enemy_1"

    card = FrameObject(
        id=8, label="Strike", kind="interactive", role="card", object_id="object:8",
    )
    assert _control_ids([card])[0].object_id == "card_1"

    marked = FrameObject(
        id=9, label="Jaw Worm", kind="interactive", role="enemy", object_id="mark_9",
    )
    assert _control_ids([marked])[0].object_id == "enemy_1"

    chrome = FrameObject(
        id=5, label="icon", kind="chrome", role="unknown", object_id="object:5",
    )
    assert _control_ids([chrome])[0].object_id == "object:5"
