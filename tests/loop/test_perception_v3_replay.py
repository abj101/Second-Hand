from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from loop.agent.decision import BoundingBox, TargetSnapshot, TurnSnapshot
from loop.agent.loop import CollectAgent, CollectConfig
from loop.agent.perception_cache import PerceptionCache
from loop.fixtures.sts2 import Sts2Fixture
from loop.memory.overlay_state import OverlayState
from loop.memory.screen_objects import ScreenObject, ScreenObjectStore
from loop.os.aim import AimGuide, AimMark
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
