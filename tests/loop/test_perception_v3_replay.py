from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from loop.agent.decision import BoundingBox, TargetSnapshot, TurnSnapshot
from loop.agent.loop import CollectAgent, CollectConfig
from loop.agent.perception_cache import PerceptionCache
from loop.fixtures.sts2 import Sts2Fixture
from loop.memory.overlay_state import OverlayState
from loop.memory.screen_objects import ScreenObject, ScreenObjectStore
from loop.os.aim import AimGuide, AimMark, in_combat_sprite_band, merge_cached_enemy_marks
from loop.os.frame_model import build_action_surface

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "perception3"
_OBJECT_N = re.compile(r"^object:\d+$")
_DOMAIN_ROLES = frozenset({"card", "enemy", "player"})


def _bbox_iou_vals(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    inter = max(0.0, min(ax1, bx1) - max(ax0, bx0)) * max(0.0, min(ay1, by1) - max(ay0, by0))
    area_a = max(0.0, ax1 - ax0) * max(0.0, ay1 - ay0)
    area_b = max(0.0, bx1 - bx0) * max(0.0, by1 - by0)
    union = area_a + area_b - inter
    return inter / union if union else 0.0


def _mark_from_row(row: dict) -> AimMark:
    bbox = row.get("bbox") or [row["x"] - 0.06, row["y"] - 0.05, row["x"] + 0.06, row["y"] + 0.05]
    return AimMark(
        id=int(row["id"]),
        x=float(row["x"]),
        y=float(row["y"]),
        x0=float(bbox[0]),
        y0=float(bbox[1]),
        x1=float(bbox[2]),
        y1=float(bbox[3]),
        label=str(row.get("label") or ""),
        role=str(row.get("role") or ""),
        object_id=str(row.get("object_id") or ""),
        source=str(row.get("source") or "test"),
        confidence=float(row.get("confidence") or 0.9),
    )


def _agent_for_combat(tmp_path: Path, marks: list[AimMark], host: dict) -> CollectAgent:
    image = Image.new("RGB", (128, 96), (40, 40, 40))
    agent = CollectAgent.__new__(CollectAgent)
    agent.fixture = Sts2Fixture()
    agent.session_id = "replay"
    agent._step_id = 4
    agent._host_stats = dict(host)
    agent._obs_metrics = dict(host)
    agent._last_obs_image = image
    agent._aim = AimGuide.from_marks(image, marks)
    agent._screen_objects = ScreenObjectStore(tmp_path / "objects.db")
    agent._action_surface = None
    agent._frame_model = None
    agent._game_state = None
    agent._entity_tracker = None
    agent._reason_controls = []
    agent._last_execution_failure = None
    agent._v2_control_objects = {}
    agent._last_ocr_words = []
    agent._overlay_state = OverlayState()
    agent._perception_cache = PerceptionCache()
    agent.gateway = None
    agent.perceive_gateway = None
    agent._force_perceive = False
    agent._pending_inventory_rescan = False
    agent._empty_action_surface_key = ""
    agent._empty_action_surface_count = 0
    agent._decision_softlock = False
    agent._session_flags = []
    agent._last_frame_mad = 0.0
    agent._turn_transition = False
    agent._pending_exec_error = None
    agent._blocked_sources = {}
    agent._blocked_hand_signature = None
    agent._blocked_targets = {}
    agent._blocked_target_host_count = None
    agent._blocked_controls = set()
    agent._blocked_controls_screen = ""
    return agent


def _scene_from_llm1(payload: dict) -> SimpleNamespace:
    llm1 = payload["llm1"]
    return SimpleNamespace(
        screen=llm1.get("screen") or "combat",
        tutorial=llm1.get("tutorial") or {},
        turn=llm1.get("turn") or "ours",
        controls=list(llm1.get("controls") or []),
        hand_cards=list(llm1.get("hand_cards") or []),
        enemies=list(llm1.get("enemies") or []),
        characters=list(llm1.get("characters") or []),
        informational_text=lambda: [],
    )


def _load_step(step: int) -> dict:
    return json.loads((FIXTURES / f"{step:06d}_llm1.json").read_text(encoding="utf-8"))


def _frozen_image(step: int) -> Image.Image:
    path = FIXTURES / f"{step:06d}_obs.jpg"
    image = Image.open(path)
    image.load()
    assert image.size[0] > 0 and image.size[1] > 0
    return image.convert("RGB")


def _scene_from_sidecar(payload: dict) -> SimpleNamespace:
    llm1 = payload.get("llm1_raw") or payload.get("llm1") or {}
    if isinstance(llm1, str):
        llm1 = json.loads(llm1)
    return _scene_from_llm1({"llm1": llm1})


def _agent_for_replay(tmp_path: Path, payload: dict, image: Image.Image, step: int):
    marks = [_mark_from_row(row) for row in payload["aim_marks"]]
    host = dict(payload.get("host_stats") or {})
    host.setdefault("screen", "combat")
    host.setdefault("activity", "combat")
    agent = _agent_for_combat(tmp_path, marks, host)
    agent._last_obs_image = image
    agent._aim = AimGuide.from_marks(image, marks)
    agent._step_id = step
    agent._game_state = None
    agent._entity_tracker = None
    return agent, marks


def _assert_no_object_n(snapshot: TurnSnapshot) -> None:
    assert not any(_OBJECT_N.match(card.card_id) for card in snapshot.cards)
    assert not any(_OBJECT_N.match(target.target_id) for target in snapshot.targets)


def test_run_b_step1_replay_yields_single_canonical_target(tmp_path: Path) -> None:
    payload = _load_step(1)
    image = _frozen_image(1)
    agent, _marks = _agent_for_replay(tmp_path, payload, image, 1)
    scene = _scene_from_sidecar(payload)
    try:
        surface = agent._finalize_action_surface(scene)
        assert surface is not None
        snapshot = agent._build_turn_snapshot(scene)
        assert len(snapshot.cards) == 5
        assert all(card.card_id.startswith("card_") for card in snapshot.cards)
        assert len(snapshot.targets) == 1
        assert snapshot.targets[0].target_id == "enemy_1"
        assert any(
            "end turn" in control.label.lower().replace("_", " ")
            for control in snapshot.controls
        )
        _assert_no_object_n(snapshot)
        domain = [obj for obj in agent._screen_objects.objects if obj.role in _DOMAIN_ROLES]
        assert all(not _OBJECT_N.match(obj.object_id) for obj in domain)
    finally:
        agent._screen_objects.close()


def test_run_b_step6_replay_dedups_same_sprite(tmp_path: Path) -> None:
    payload = _load_step(6)
    image = _frozen_image(6)
    agent, _marks = _agent_for_replay(tmp_path, payload, image, 6)
    scene = _scene_from_sidecar(payload)
    try:
        surface = agent._finalize_action_surface(scene)
        assert surface is not None
        snapshot = agent._build_turn_snapshot(scene)
        assert len(snapshot.targets) == 1
        target = snapshot.targets[0]
        assert target.target_id.startswith("enemy_")
        assert _OBJECT_N.match(target.target_id) is None
        name = (target.name or "").strip().lower().replace("_", " ")
        labels = {(row.get("label") or "").strip().lower() for row in payload["aim_marks"]}
        if "nibbit" in labels:
            assert name == "nibbit"
        else:
            assert name in {"nibbit", "unknown enemy"}
        enemies = [obj for obj in agent._screen_objects.objects if obj.role == "enemy"]
        assert len(enemies) == 1
        assert enemies[0].object_id.startswith("enemy_")
        assert not _OBJECT_N.match(enemies[0].object_id)
        nibbit_bbox = next(
            (row.get("bbox") for row in payload["aim_marks"] if str(row.get("label") or "").lower() == "nibbit"),
            None,
        )
        if nibbit_bbox:
            overlapping = [
                obj for obj in agent._screen_objects.objects
                if _bbox_iou_vals(
                    (obj.x0, obj.y0, obj.x1, obj.y1),
                    tuple(float(v) for v in nibbit_bbox[:4]),
                ) >= 0.45
            ]
            assert len(overlapping) == 1, [obj.object_id for obj in overlapping]
    finally:
        agent._screen_objects.close()


def test_snapshot_drops_mark_derived_ids_and_counts(tmp_path: Path) -> None:
    """Enemy object_id=object:3 must never appear in TurnSnapshot.targets.

    Orchestrator will add snapshot.dropped_noncanonical on CollectAgent. If that
    attribute exists after a leftover mark-derived store row reaches the snapshot
    builder, it must increment. Absence of object:N is the user-visible contract;
    xfail is not allowed.
    """
    marks = [
        _mark_from_row({
            "id": 1, "label": "Strike", "role": "card", "object_id": "card_1",
            "x": 0.50, "y": 0.90, "bbox": [0.40, 0.80, 0.60, 1.0],
        }),
        _mark_from_row({
            "id": 2, "label": "Nibbit", "role": "enemy", "object_id": "object:3",
            "x": 0.75, "y": 0.58, "bbox": [0.68, 0.50, 0.82, 0.68],
        }),
        _mark_from_row({
            "id": 3, "label": "End Turn", "role": "button", "object_id": "",
            "x": 0.89, "y": 0.82, "bbox": [0.82, 0.78, 0.96, 0.88],
        }),
    ]
    surface = build_action_surface(
        marks, run_id="replay", step_id=1, screen="combat", activity="combat",
    )
    store = ScreenObjectStore(tmp_path / "drop.db")
    store.save_frame(
        run_id="replay", step_id=1, marks=marks, screen="combat",
        context=surface.context, surface=surface,
    )
    frozen = TurnSnapshot.from_action_surface(surface)
    _assert_no_object_n(frozen)
    assert all(not _OBJECT_N.match(obj.object_id) for obj in store.objects if obj.role in _DOMAIN_ROLES)

    image = Image.new("RGB", (128, 96), (40, 40, 40))
    agent = _agent_for_combat(tmp_path / "agent", marks, {"screen": "combat", "activity": "combat"})
    agent._last_obs_image = image
    agent._aim = AimGuide.from_marks(image, marks)
    agent._screen_objects.close()
    agent._screen_objects = store
    agent._action_surface = surface
    agent._game_state = None
    agent._entity_tracker = None
    leftover = ScreenObject(
        mark_id=99, x=0.74, y=0.57, x0=0.67, y0=0.49, x1=0.81, y1=0.67,
        label="Nibbit", kind="interactive", source="test", screen="combat",
        listing="99@740,570:Nibbit", object_id="object:3", role="enemy",
        confidence=0.9, provenance="test",
    )
    agent._screen_objects._objects = [*store.objects, leftover]  # noqa: SLF001
    scene = SimpleNamespace(
        screen="combat", enemies=[], characters=[], controls=[], hand_cards=[],
        tutorial={}, turn="ours", informational_text=lambda: [],
    )
    try:
        snapshot = agent._build_turn_snapshot(scene)
        leftover_ids = [target.target_id for target in snapshot.targets]
        assert not any(_OBJECT_N.match(tid) for tid in leftover_ids), leftover_ids
        assert agent._snapshot_dropped_noncanonical >= 1
        assert "object:3" not in agent._v2_control_objects
    finally:
        store.close()


def _assert_unit_identity_confidence(snapshot: TurnSnapshot) -> None:
    for target in snapshot.targets:
        conf = target.identity_confidence
        assert conf is None or 0.0 <= float(conf) <= 1.0, (target.target_id, conf)


def test_run_b_step7_replay_single_target(tmp_path: Path) -> None:
    payload = _load_step(7)
    image = _frozen_image(7)
    agent, _marks = _agent_for_replay(tmp_path, payload, image, 7)
    scene = _scene_from_sidecar(payload)
    try:
        surface = agent._finalize_action_surface(scene)
        assert surface is not None
        snapshot = agent._build_turn_snapshot(scene)
        assert len(snapshot.targets) == 1
        target = snapshot.targets[0]
        assert target.target_id.startswith("enemy_")
        _assert_no_object_n(snapshot)
        _assert_unit_identity_confidence(snapshot)
        enemies = [obj for obj in agent._screen_objects.objects if obj.role == "enemy"]
        assert len(enemies) == 1
        assert enemies[0].object_id.startswith("enemy_")
    finally:
        agent._screen_objects.close()


def test_run_b_step19_replay_single_target_canonical_id(tmp_path: Path) -> None:
    payload = _load_step(19)
    image = _frozen_image(19)
    agent, _marks = _agent_for_replay(tmp_path, payload, image, 19)
    scene = _scene_from_sidecar(payload)
    try:
        surface = agent._finalize_action_surface(scene)
        assert surface is not None
        snapshot = agent._build_turn_snapshot(scene)
        assert len(snapshot.targets) == 1
        target = snapshot.targets[0]
        assert target.target_id.startswith("enemy_")
        assert _OBJECT_N.match(target.target_id) is None
        _assert_no_object_n(snapshot)
        _assert_unit_identity_confidence(snapshot)
        enemies = [obj for obj in agent._screen_objects.objects if obj.role == "enemy"]
        assert len(enemies) == 1
        assert enemies[0].object_id.startswith("enemy_")
        name = (target.name or "").strip().lower().replace("_", " ")
        assert name in {"nibbit", "unknown enemy"}
    finally:
        agent._screen_objects.close()


def test_identity_confidence_clamped_or_none() -> None:
    high = ScreenObject.from_mark(
        AimMark(
            id=1, x=0.75, y=0.62, x0=0.68, y0=0.55, x1=0.82, y1=0.70,
            label="Nibbit", role="enemy", confidence=143.1, score=143.1,
        ),
        screen="combat",
    )
    assert high.confidence == 0.0
    assert 0.0 <= high.confidence <= 1.0

    mid = ScreenObject.from_mark(
        AimMark(
            id=2, x=0.75, y=0.62, x0=0.68, y0=0.55, x1=0.82, y1=0.70,
            label="Nibbit", role="enemy", confidence=0.72, score=106.0,
        ),
        screen="combat",
    )
    assert mid.confidence == 0.72

    score_fallback = ScreenObject.from_mark(
        AimMark(
            id=3, x=0.75, y=0.62, x0=0.68, y0=0.55, x1=0.82, y1=0.70,
            label="Nibbit", role="enemy", confidence=143.1, score=0.55,
        ),
        screen="combat",
    )
    assert score_fallback.confidence == 0.55

    spilled = TargetSnapshot(
        target_id="enemy_1",
        bbox=BoundingBox(0.68, 0.55, 0.82, 0.70),
        name="Nibbit",
        identity_confidence=143.1,
    )
    assert spilled.identity_confidence is None
    ok = TargetSnapshot(
        target_id="enemy_1",
        bbox=BoundingBox(0.68, 0.55, 0.82, 0.70),
        name="Nibbit",
        identity_confidence=0.72,
    )
    assert ok.identity_confidence == 0.72


def _bbox_center(bbox: tuple[float, float, float, float]) -> tuple[float, float]:
    return ((bbox[0] + bbox[2]) * 0.5, (bbox[1] + bbox[3]) * 0.5)


def _center_dist(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
) -> float:
    ax, ay = _bbox_center(a)
    bx, by = _bbox_center(b)
    return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5


def _combat_scene(
    *,
    enemies: list | None = None,
    hand_cards: list | None = None,
    controls: list | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        screen="combat",
        enemies=list(enemies or []),
        characters=[],
        controls=list(controls or []),
        hand_cards=list(hand_cards or []),
        tutorial={},
        turn="ours",
        informational_text=lambda: [],
        dialog=None,
    )


def test_anchored_single_enemy_routes_to_act_without_blocker(tmp_path: Path) -> None:
    marks = [
        _mark_from_row({
            "id": 1, "label": "Strike", "role": "card", "object_id": "card_1",
            "x": 0.50, "y": 0.90, "bbox": [0.40, 0.80, 0.60, 1.0],
        }),
        _mark_from_row({
            "id": 2, "label": "Nibbit", "role": "enemy", "object_id": "enemy_1",
            "x": 0.75, "y": 0.58, "bbox": [0.68, 0.50, 0.82, 0.68],
        }),
        _mark_from_row({
            "id": 3, "label": "End Turn", "role": "button", "object_id": "",
            "x": 0.89, "y": 0.82, "bbox": [0.82, 0.78, 0.96, 0.88],
        }),
    ]
    agent = _agent_for_combat(tmp_path, marks, {"screen": "combat", "activity": "combat", "enemies": 1})
    agent.config = CollectConfig(identify_before_act=True)
    scene = SimpleNamespace(
        screen="combat",
        enemies=[{
            "label": "Nibbit", "role": "enemy", "confidence": "high",
            "bbox": [680, 500, 820, 680],
        }],
        characters=[{
            "label": "unknown_character_2", "role": "unknown_character",
            "confidence": "low", "inspectable": "true",
            "bbox": [100, 100, 180, 180],
        }],
        controls=[],
        hand_cards=[],
        tutorial={},
        informational_text=lambda: [],
    )
    try:
        surface = agent._finalize_action_surface(scene)
        assert surface is not None
        route, reason = agent._two_stage_reason_route(scene)
        assert route == "act", (route, reason)
        assert agent._two_stage_epistemic_blockers(scene) == []
    finally:
        agent._screen_objects.close()


_BODY_BBOX = (0.69, 0.57, 0.81, 0.72)
_HP_BBOX = (0.70, 0.50, 0.80, 0.58)


def test_two_same_sprite_enemy_marks_below_iou_045_collapse_to_enemy_1(tmp_path: Path) -> None:
    iou = _bbox_iou_vals(_BODY_BBOX, _HP_BBOX)
    dist = _center_dist(_BODY_BBOX, _HP_BBOX)
    assert iou < 0.45, iou
    assert dist <= 0.12, dist
    marks = [
        _mark_from_row({
            "id": 1, "label": "Strike", "role": "card", "object_id": "card_1",
            "x": 0.50, "y": 0.90, "bbox": [0.40, 0.80, 0.60, 1.0],
        }),
        _mark_from_row({
            "id": 2, "label": "unknown enemy", "role": "enemy",
            "x": _bbox_center(_BODY_BBOX)[0], "y": _bbox_center(_BODY_BBOX)[1],
            "bbox": list(_BODY_BBOX),
        }),
        _mark_from_row({
            "id": 3, "label": "unknown enemy", "role": "enemy",
            "x": _bbox_center(_HP_BBOX)[0], "y": _bbox_center(_HP_BBOX)[1],
            "bbox": list(_HP_BBOX),
        }),
        _mark_from_row({
            "id": 4, "label": "End Turn", "role": "button", "object_id": "",
            "x": 0.89, "y": 0.82, "bbox": [0.82, 0.78, 0.96, 0.88],
        }),
    ]
    host = {"screen": "combat", "activity": "combat"}
    agent = _agent_for_combat(tmp_path, marks, host)
    scene = _combat_scene(
        enemies=[{"label": "unknown enemy", "role": "enemy", "confidence": "high"}],
        hand_cards=[{"label": "Strike", "role": "card"}],
        controls=[{"label": "End Turn", "role": "button"}],
    )
    try:
        surface = agent._finalize_action_surface(scene)
        assert surface is not None
        snapshot = agent._build_turn_snapshot(scene)
        assert len(snapshot.targets) == 1, [t.target_id for t in snapshot.targets]
        assert snapshot.targets[0].target_id == "enemy_1"
        enemies = [obj for obj in agent._screen_objects.objects if obj.role == "enemy"]
        assert len(enemies) == 1, [obj.object_id for obj in enemies]
    finally:
        agent._screen_objects.close()


def test_two_named_nibbit_store_rows_snapshot_publishes_one(tmp_path: Path) -> None:
    body = (0.70, 0.55, 0.82, 0.70)
    plate = (0.71, 0.52, 0.81, 0.62)
    iou = _bbox_iou_vals(body, plate)
    dist = _center_dist(body, plate)
    assert iou < 0.45, iou
    assert dist <= 0.12 or iou >= 0.20, (dist, iou)
    marks = [
        _mark_from_row({
            "id": 1, "label": "Strike", "role": "card", "object_id": "card_1",
            "x": 0.50, "y": 0.90, "bbox": [0.40, 0.80, 0.60, 1.0],
        }),
        _mark_from_row({
            "id": 2, "label": "Nibbit", "role": "enemy",
            "x": _bbox_center(body)[0], "y": _bbox_center(body)[1],
            "bbox": list(body),
        }),
        _mark_from_row({
            "id": 3, "label": "Nibbit", "role": "enemy",
            "x": _bbox_center(plate)[0], "y": _bbox_center(plate)[1],
            "bbox": list(plate),
        }),
        _mark_from_row({
            "id": 4, "label": "End Turn", "role": "button",
            "x": 0.89, "y": 0.82, "bbox": [0.82, 0.78, 0.96, 0.88],
        }),
    ]
    agent = _agent_for_combat(tmp_path, marks, {"screen": "combat", "activity": "combat"})
    scene = _combat_scene(
        enemies=[{"label": "Nibbit", "role": "enemy", "confidence": "high"}],
        hand_cards=[{"label": "Strike", "role": "card"}],
        controls=[{"label": "End Turn", "role": "button"}],
    )
    try:
        surface = agent._finalize_action_surface(scene)
        assert surface is not None
        bx, by = _bbox_center(body)
        px, py = _bbox_center(plate)
        rest = [obj for obj in agent._screen_objects.objects if obj.role != "enemy"]
        agent._screen_objects._objects = rest + [
            ScreenObject(
                mark_id=2, x=bx, y=by, x0=body[0], y0=body[1], x1=body[2], y1=body[3],
                label="Nibbit", kind="interactive", source="test", screen="combat",
                listing="2@760,625:Nibbit", object_id="enemy_1", role="enemy",
                confidence=0.9,
            ),
            ScreenObject(
                mark_id=3, x=px, y=py, x0=plate[0], y0=plate[1], x1=plate[2], y1=plate[3],
                label="Nibbit", kind="interactive", source="test", screen="combat",
                listing="3@760,570:Nibbit", object_id="enemy_2", role="enemy",
                confidence=0.8,
            ),
        ]
        snapshot = agent._build_turn_snapshot(scene)
        assert len(snapshot.targets) == 1, [t.target_id for t in snapshot.targets]
        assert isinstance(snapshot.targets[0], TargetSnapshot)
    finally:
        agent._screen_objects.close()


def test_two_separated_enemies_stay_two(tmp_path: Path) -> None:
    left = (0.49, 0.50, 0.61, 0.66)
    right = (0.76, 0.50, 0.88, 0.66)
    dist = _center_dist(left, right)
    assert dist >= 0.25, dist
    marks = [
        _mark_from_row({
            "id": 1, "label": "Strike", "role": "card", "object_id": "card_1",
            "x": 0.50, "y": 0.90, "bbox": [0.40, 0.80, 0.60, 1.0],
        }),
        _mark_from_row({
            "id": 2, "label": "Nibbit", "role": "enemy",
            "x": 0.55, "y": 0.58, "bbox": list(left),
        }),
        _mark_from_row({
            "id": 3, "label": "Louse", "role": "enemy",
            "x": 0.82, "y": 0.58, "bbox": list(right),
        }),
        _mark_from_row({
            "id": 4, "label": "End Turn", "role": "button",
            "x": 0.89, "y": 0.82, "bbox": [0.82, 0.78, 0.96, 0.88],
        }),
    ]
    host = {
        "screen": "combat",
        "activity": "combat",
        "enemies": [
            {"label": "Nibbit", "hp": 12, "hp_max": 20},
            {"label": "Louse", "hp": 8, "hp_max": 16},
        ],
    }
    agent = _agent_for_combat(tmp_path, marks, host)
    scene = _combat_scene(
        enemies=[
            {"label": "Nibbit", "role": "enemy", "confidence": "high"},
            {"label": "Louse", "role": "enemy", "confidence": "high"},
        ],
        hand_cards=[{"label": "Strike", "role": "card"}],
        controls=[{"label": "End Turn", "role": "button"}],
    )
    try:
        surface = agent._finalize_action_surface(scene)
        assert surface is not None
        snapshot = agent._build_turn_snapshot(scene)
        assert len(snapshot.targets) == 2, [t.target_id for t in snapshot.targets]
        ids = {t.target_id for t in snapshot.targets}
        assert ids == {"enemy_1", "enemy_2"}
    finally:
        agent._screen_objects.close()


def test_merge_cached_does_not_reattach_clustered_duplicate() -> None:
    current = [
        AimMark(
            id=1, x=0.50, y=0.90, x0=0.40, y0=0.80, x1=0.60, y1=1.0,
            label="Strike", role="card", source="ocr-hand",
        ),
        AimMark(
            id=2, x=0.75, y=0.645, x0=0.69, y0=0.57, x1=0.81, y1=0.72,
            label="Nibbit", role="enemy", object_id="enemy_1",
            source="test", confidence=0.95,
        ),
    ]
    cached = [
        AimMark(
            id=9, x=0.75, y=0.54, x0=0.70, y0=0.50, x1=0.80, y1=0.58,
            label="Nibbit", role="enemy", object_id="enemy_2",
            source="cache", confidence=0.55,
        ),
    ]
    merged = merge_cached_enemy_marks(current, cached)
    enemy_band = [
        mark for mark in merged
        if in_combat_sprite_band(mark.y, mark.x)
        and (mark.role or "").strip().lower() in {"enemy", "target"}
    ]
    if not enemy_band:
        enemy_band = [mark for mark in merged if in_combat_sprite_band(mark.y, mark.x)]
    assert len(enemy_band) == 1, [(m.object_id, m.label, m.y) for m in enemy_band]


def test_b4_opening_strike_binds_in_hand_band(tmp_path: Path) -> None:
    from loop.agent.decision import legal_intents_for_snapshot
    from loop.os.aim import in_hand_band

    marks = [
        AimMark(id=1, x=0.312, y=0.95, x0=0.25, y0=0.85, x1=0.38, y1=1.0, label="Strike", role="card"),
        AimMark(id=2, x=0.40, y=0.94, x0=0.34, y0=0.84, x1=0.47, y1=1.0, label="Defend", role="card"),
        AimMark(id=3, x=0.50, y=0.93, x0=0.44, y0=0.83, x1=0.57, y1=1.0, label="Defend", role="card"),
        AimMark(id=4, x=0.59, y=0.94, x0=0.53, y0=0.84, x1=0.66, y1=1.0, label="Defend", role="card"),
        AimMark(id=5, x=0.69, y=0.95, x0=0.62, y0=0.85, x1=0.76, y1=1.0, label="Strike", role="card"),
        AimMark(id=10, x=0.75, y=0.62, x0=0.68, y0=0.52, x1=0.82, y1=0.72, label="Jaw Worm", role="enemy"),
        AimMark(id=12, x=0.90, y=0.82, x0=0.82, y0=0.74, x1=0.98, y1=0.90, label="End Turn", role="button"),
    ]
    host = {"screen": "combat", "energy": 3, "hp": 80, "player": {"hp": 80, "energy": 3}}
    agent = _agent_for_combat(tmp_path, marks, host)
    agent._screen_objects.save_frame(run_id="replay", step_id=1, marks=marks, screen="combat")
    agent._aim = AimGuide.from_marks(agent._last_obs_image, marks)
    scene = SimpleNamespace(
        screen="combat", hand_cards=[], enemies=[], characters=[], controls=[], tutorial={},
    )
    try:
        snapshot = agent._build_turn_snapshot(scene)
        card = next(row for row in snapshot.cards if row.card_id == "card_1")
        obj = agent._screen_objects.by_object_id("card_1")
        mark = agent._hand_source_mark(agent._aim_mark_for_object(obj), [])
        assert mark is not None
        assert in_hand_band(mark.x, mark.y)
        assert abs(card.bbox.x0 + card.bbox.x1) / 2 >= 0.18
        agent._record_blocked_source(
            {"source": {"object_id": "card_1", "point": {"x": 0.312, "y": 0.95}}},
            mark,
        )
        snapshot2 = agent._build_turn_snapshot(scene)
        blocked = next(row for row in snapshot2.cards if row.card_id == "card_1")
        assert blocked.playable is False
        legal = legal_intents_for_snapshot(snapshot2)
        assert any(row["kind"] == "play_card" for row in legal)
        agent._last_execution_failure = {"code": "source-unconfirmed:card_1"}
        assert agent._replan_after_source_failure(snapshot2, legal) is True
        assert agent._decision_softlock is False
    finally:
        agent._screen_objects.close()


def test_hp_body_column_stack_collapses_without_host_count(tmp_path: Path) -> None:
    """Live B step 1: HP/intent y≈0.35 over body y≈0.62, host enemies omitted."""
    hp = (0.70, 0.30, 0.80, 0.40)
    body = (0.69, 0.55, 0.81, 0.70)
    dist = _center_dist(hp, body)
    assert dist >= 0.25, dist
    marks = [
        _mark_from_row({
            "id": 1, "label": "Strike", "role": "card", "object_id": "card_1",
            "x": 0.312, "y": 0.95, "bbox": [0.25, 0.85, 0.38, 1.0],
        }),
        _mark_from_row({
            "id": 8, "label": "unknown enemy", "role": "enemy",
            "x": 0.745, "y": 0.622, "bbox": [0.69, 0.55, 0.80, 0.70],
        }),
        _mark_from_row({
            "id": 9, "label": "unidentified enemy", "role": "enemy",
            "x": 0.750, "y": 0.347, "bbox": [0.70, 0.30, 0.80, 0.40],
        }),
        _mark_from_row({
            "id": 10, "label": "End Turn", "role": "button",
            "x": 0.89, "y": 0.82, "bbox": [0.82, 0.78, 0.96, 0.88],
        }),
    ]
    host = {"screen": "combat", "activity": "combat"}
    agent = _agent_for_combat(tmp_path, marks, host)
    scene = _combat_scene(
        enemies=[{"label": "unknown enemy", "role": "enemy", "confidence": "high"}],
        hand_cards=[{"label": "Strike", "role": "card"}],
        controls=[{"label": "End Turn", "role": "button"}],
    )
    try:
        surface = agent._finalize_action_surface(scene)
        assert surface is not None
        snapshot = agent._build_turn_snapshot(scene)
        assert len(snapshot.targets) == 1, [t.target_id for t in snapshot.targets]
        assert snapshot.targets[0].target_id == "enemy_1"
        enemies = [obj for obj in agent._screen_objects.objects if obj.role == "enemy"]
        assert len(enemies) == 1, [obj.object_id for obj in enemies]
    finally:
        agent._screen_objects.close()


def test_b5b_step1_hp_body_marks_publish_one_target(tmp_path: Path) -> None:
    """Replay 20260908T070054Z step 1 / 7 dual Nibbit stack."""
    marks = [
        _mark_from_row({
            "id": 3, "label": "Strike", "role": "card",
            "x": 0.312, "y": 0.95, "bbox": [0.25, 0.85, 0.38, 1.0],
        }),
        _mark_from_row({
            "id": 8, "label": "Nibbit", "role": "enemy",
            "x": 0.745, "y": 0.622, "bbox": [0.68, 0.52, 0.82, 0.72],
        }),
        _mark_from_row({
            "id": 9, "label": "Nibbit", "role": "enemy",
            "x": 0.750, "y": 0.347, "bbox": [0.70, 0.30, 0.80, 0.40],
        }),
        _mark_from_row({
            "id": 10, "label": "End Turn", "role": "button",
            "x": 0.89, "y": 0.82, "bbox": [0.82, 0.78, 0.96, 0.88],
        }),
    ]
    agent = _agent_for_combat(tmp_path, marks, {"screen": "combat", "activity": "combat"})
    scene = _combat_scene(
        enemies=[{"label": "Nibbit", "role": "enemy", "confidence": "high"}],
        hand_cards=[{"label": "Strike", "role": "card"}],
        controls=[{"label": "End Turn", "role": "button"}],
    )
    try:
        agent._finalize_action_surface(scene)
        snapshot = agent._build_turn_snapshot(scene)
        assert [t.target_id for t in snapshot.targets] == ["enemy_1"]
        agent._record_blocked_target("enemy_2")
        snapshot2 = agent._build_turn_snapshot(scene)
        assert all(t.target_id != "enemy_2" for t in snapshot2.targets)
        from loop.agent.decision import PlayCardIntent, validate_action_intent, ActionIntentError
        with pytest.raises(ActionIntentError, match="unknown-target-id"):
            validate_action_intent(
                snapshot2,
                PlayCardIntent(card_id="card_1", target_id="enemy_2"),
            )
        agent._last_execution_failure = {"code": "target-not-found:enemy_2", "transaction_outcome": "target_not_found"}
        from loop.agent.decision import legal_intents_for_snapshot
        legal = legal_intents_for_snapshot(snapshot2)
        assert agent._replan_after_tnf(snapshot2, legal) is True
    finally:
        agent._screen_objects.close()


def _dual_sprite_agent(
    tmp_path: Path,
    upper: tuple[float, float],
    lower: tuple[float, float],
    *,
    upper_label: str = "Nibbit",
    lower_label: str = "Nibbit",
) -> tuple[CollectAgent, SimpleNamespace]:
    """Two enemy marks stacked in one column, host enemy count omitted."""

    def _box(center: tuple[float, float]) -> list[float]:
        cx, cy = center
        return [cx - 0.06, cy - 0.05, cx + 0.06, cy + 0.05]

    marks = [
        _mark_from_row({
            "id": 3, "label": "Strike", "role": "card",
            "x": 0.312, "y": 0.95, "bbox": [0.25, 0.85, 0.38, 1.0],
        }),
        _mark_from_row({
            "id": 6, "label": upper_label, "role": "enemy",
            "x": upper[0], "y": upper[1], "bbox": _box(upper),
        }),
        _mark_from_row({
            "id": 7, "label": lower_label, "role": "enemy",
            "x": lower[0], "y": lower[1], "bbox": _box(lower),
        }),
        _mark_from_row({
            "id": 10, "label": "End Turn", "role": "button",
            "x": 0.89, "y": 0.82, "bbox": [0.82, 0.78, 0.96, 0.88],
        }),
    ]
    agent = _agent_for_combat(tmp_path, marks, {"screen": "combat", "activity": "combat"})
    scene = _combat_scene(
        enemies=[{"label": upper_label, "role": "enemy", "confidence": "high"}],
        hand_cards=[{"label": "Strike", "role": "card"}],
        controls=[{"label": "End Turn", "role": "button"}],
    )
    return agent, scene


def test_b6b_step2_dual_nibbit_dead_band_publishes_one(tmp_path: Path) -> None:
    """20260908T194352Z step 2: two Nibbit rows 0.19 apart, host enemies omitted.

    Batch 6 only ran a stack test above SEPARATED_SPRITE_CENTER, so this pair fell
    through the 0.12-0.25 dead band and published enemy_1 plus enemy_2.
    """
    upper, lower = (0.639, 0.289), (0.748, 0.447)
    dist = ((upper[0] - lower[0]) ** 2 + (upper[1] - lower[1]) ** 2) ** 0.5
    assert 0.12 < dist < 0.25, dist
    agent, scene = _dual_sprite_agent(tmp_path, upper, lower)
    try:
        assert agent._finalize_action_surface(scene) is not None
        snapshot = agent._build_turn_snapshot(scene)
        assert [t.target_id for t in snapshot.targets] == ["enemy_1"], [
            t.target_id for t in snapshot.targets
        ]
    finally:
        agent._screen_objects.close()


def test_b6b_step10_dual_nibbit_same_column_publishes_one(tmp_path: Path) -> None:
    """20260908T194352Z step 10: same column, still inside the dead band."""
    upper, lower = (0.494, 0.353), (0.488, 0.500)
    agent, scene = _dual_sprite_agent(tmp_path, upper, lower)
    try:
        assert agent._finalize_action_surface(scene) is not None
        snapshot = agent._build_turn_snapshot(scene)
        assert [t.target_id for t in snapshot.targets] == ["enemy_1"], [
            t.target_id for t in snapshot.targets
        ]
    finally:
        agent._screen_objects.close()


def test_b6b_step6_split_hp_dual_never_publishes_enemy_2(tmp_path: Path) -> None:
    """20260908T194352Z step 6 shape: the dual that produced target-not-found:enemy_2.

    ACT must never see enemy_2, so the TNF block is not what saves the turn.
    """
    from loop.agent.decision import (
        ActionIntentError,
        PlayCardIntent,
        validate_action_intent,
    )

    agent, scene = _dual_sprite_agent(tmp_path, (0.749, 0.347), (0.745, 0.622))
    try:
        assert agent._finalize_action_surface(scene) is not None
        snapshot = agent._build_turn_snapshot(scene)
        assert [t.target_id for t in snapshot.targets] == ["enemy_1"]
        # Nothing was blocked: the collapse alone kept enemy_2 off the surface.
        assert not agent._blocked_targets
        with pytest.raises(ActionIntentError, match="unknown-target-id"):
            validate_action_intent(
                snapshot,
                PlayCardIntent(card_id="card_1", target_id="enemy_2"),
            )
    finally:
        agent._screen_objects.close()


def test_b6b_step11_named_body_absorbs_anonymous_plate(tmp_path: Path) -> None:
    """20260908T194352Z step 11: Nibbit body plus an unidentified plate above it."""
    agent, scene = _dual_sprite_agent(
        tmp_path,
        (0.773, 0.414),
        (0.795, 0.647),
        upper_label="unknown enemy",
        lower_label="Nibbit",
    )
    try:
        assert agent._finalize_action_surface(scene) is not None
        snapshot = agent._build_turn_snapshot(scene)
        assert [t.target_id for t in snapshot.targets] == ["enemy_1"], [
            t.target_id for t in snapshot.targets
        ]
        assert snapshot.targets[0].name == "Nibbit"
    finally:
        agent._screen_objects.close()


def test_stacked_duplicate_collapse_keeps_side_by_side_same_name(tmp_path: Path) -> None:
    """Two Nibbits standing side by side are two hostiles, not one tall sprite."""
    agent, scene = _dual_sprite_agent(tmp_path, (0.45, 0.58), (0.80, 0.58))
    try:
        assert agent._finalize_action_surface(scene) is not None
        snapshot = agent._build_turn_snapshot(scene)
        assert {t.target_id for t in snapshot.targets} == {"enemy_1", "enemy_2"}, [
            t.target_id for t in snapshot.targets
        ]
    finally:
        agent._screen_objects.close()
