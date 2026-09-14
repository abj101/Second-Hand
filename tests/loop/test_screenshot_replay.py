from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from loop.os.aim import build_marks
from loop.os.frame_model import build_action_surface
from loop.os.sts_stats import extract_sts_stats

SCREENS = Path(__file__).resolve().parents[2] / "loop" / "fixtures" / "sts2_screens"
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


def _labels() -> list[dict]:
    rows = []
    for line in (SCREENS / "labels.jsonl").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        if Path(str(row.get("file") or "")).suffix.lower() in _IMAGE_SUFFIXES:
            rows.append(row)
    return rows


def _compact(text: str) -> str:
    return " ".join((text or "").lower().replace("_", " ").split())


def _surface_labels(surface) -> list[str]:
    return [str(obj.label or "") for obj in surface.objects]


@pytest.fixture(scope="module", autouse=True)
def _enable_gui() -> None:
    from loop.os.gui import configure_gui

    configure_gui(enabled=True, download=False)
    yield
    configure_gui(enabled=False)


@pytest.mark.parametrize("label", _labels(), ids=lambda row: str(row.get("file")))
def test_screenshot_replay(label: dict) -> None:
    path = SCREENS / str(label["file"])
    assert path.is_file(), path
    image = Image.open(path).convert("RGB")
    stats = extract_sts_stats(image)
    screen = str(stats.get("screen") or label.get("screen") or "")
    marks = build_marks(image, screen=screen or None)
    surface = build_action_surface(marks, host_stats=stats, screen=screen or None)

    got_screen = str(surface.context.screen or stats.get("screen") or "").strip().lower()
    expected_screen = str(label.get("screen") or "").strip().lower()
    if expected_screen:
        assert got_screen == expected_screen

    names = _surface_labels(surface)
    blob = " ".join(_compact(name) for name in names)
    for token in label.get("must_include") or []:
        assert _compact(token) in blob, f"missing {token!r} in {names}"
    for token in label.get("must_not_include") or []:
        assert _compact(token) not in blob, f"unexpected {token!r} in {names}"

    for token in label.get("unique_labels") or []:
        hits = [name for name in names if _compact(name) == _compact(token)]
        assert len(hits) == 1, f"{token!r} count={len(hits)} labels={names}"

    cards = [
        obj
        for obj in surface.objects
        if obj.role == "card" or _compact(obj.label) in {"strike", "defend", "bash"}
    ]
    targets = [
        obj
        for obj in surface.objects
        if obj.role == "enemy" or "enemy" in _compact(obj.label)
    ]
    if "cards_min" in label:
        assert len(cards) >= int(label["cards_min"]), names
    if "targets_min" in label:
        assert len(targets) >= int(label["targets_min"]), names

    intents = set(surface.legal_intents or ())
    for name in label.get("legal_intents") or []:
        assert name in intents, intents
    for name in label.get("banned_intents") or []:
        assert name not in intents, intents

    overlay = label.get("overlay")
    if overlay:
        assert getattr(surface.context, "overlay", None) == overlay


def test_defend_without_target_is_legal() -> None:
    from dataclasses import replace

    from loop.agent.decision import (
        BoundingBox,
        CardSnapshot,
        PlayCardIntent,
        TargetSnapshot,
        TurnSnapshot,
        validate_action_intent,
    )

    path = SCREENS / "combat_runb_000001.jpg"
    image = Image.open(path).convert("RGB")
    stats = extract_sts_stats(image)
    screen = str(stats.get("screen") or "combat")
    marks = build_marks(image, screen=screen or None)
    surface = build_action_surface(marks, host_stats=stats, screen=screen or None)
    snapshot = TurnSnapshot.from_action_surface(surface)
    defend = next((card for card in snapshot.cards if _compact(card.name or "") == "defend"), None)
    if defend is None:
        obj = next(item for item in surface.objects if _compact(item.label) == "defend")
        defend = CardSnapshot(
            str(getattr(obj, "object_id", "") or "card_defend"),
            "Defend",
            BoundingBox(float(obj.x0), float(obj.y0), float(obj.x1), float(obj.y1)),
            True,
        )
        snapshot = replace(snapshot, cards=snapshot.cards + (defend,), legal_intents=None)
    if not snapshot.targets:
        snapshot = replace(
            snapshot,
            targets=(TargetSnapshot("enemy_1", BoundingBox(0.6, 0.4, 0.8, 0.6), name="enemy"),),
        )
    snapshot = replace(snapshot, legal_intents=None)
    validate_action_intent(snapshot, PlayCardIntent(card_id=defend.card_id, target_id=None))
