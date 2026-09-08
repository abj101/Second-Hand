"""Immutable state reducer built only from sensor observations.

Natural-language planner claims intentionally do not participate in reconciliation.
Callers pass the output of HUD/OCR and the frame model; this module turns those
observations into a small, legal-action constrained view of the current game.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import hypot
import re
from typing import Any, Generic, Iterable, Literal, TypeVar


T = TypeVar("T")
UNKNOWN = "unknown"


@dataclass(frozen=True)
class Field(Generic[T]):
    """A value plus evidence needed to decide whether it may be trusted."""

    value: T | None = None
    source: str = "unknown"
    confidence: float = 0.0
    last_confirmed_step: int | None = None

    @property
    def known(self) -> bool:
        return self.value is not None

    @classmethod
    def unknown(cls) -> "Field[Any]":
        return cls()


@dataclass(frozen=True)
class PlayerState:
    hp: Field[int] = field(default_factory=Field.unknown)
    hp_max: Field[int] = field(default_factory=Field.unknown)
    block: Field[int] = field(default_factory=Field.unknown)
    energy: Field[int] = field(default_factory=Field.unknown)
    energy_max: Field[int] = field(default_factory=Field.unknown)
    gold: Field[int] = field(default_factory=Field.unknown)


@dataclass(frozen=True)
class EnemyState:
    id: str
    label: Field[str] = field(default_factory=Field.unknown)
    bbox: Field[tuple[float, float, float, float]] = field(default_factory=Field.unknown)
    hp: Field[int] = field(default_factory=Field.unknown)
    hp_max: Field[int] = field(default_factory=Field.unknown)
    intent: Field[str] = field(default_factory=Field.unknown)
    targetable: Field[bool] = field(default_factory=Field.unknown)


@dataclass(frozen=True)
class CardState:
    """A card's ID is stable only for the current hand epoch (turn)."""

    id: str
    name: Field[str] = field(default_factory=Field.unknown)
    cost: Field[int] = field(default_factory=Field.unknown)
    bbox: Field[tuple[float, float, float, float]] = field(default_factory=Field.unknown)
    playable: Field[bool] = field(default_factory=Field.unknown)
    selected: Field[bool] = field(default_factory=Field.unknown)


@dataclass(frozen=True)
class UnknownObject:
    id: str
    bbox: Field[tuple[float, float, float, float]] = field(default_factory=Field.unknown)
    label: Field[str] = field(default_factory=Field.unknown)


ActionKind = Literal["dismiss_overlay", "select_card", "play_card", "end_turn", "inspect"]


@dataclass(frozen=True)
class LegalAction:
    kind: ActionKind
    card_id: str | None = None
    target_id: str | None = None
    object_id: str | None = None
    reason: str = ""


@dataclass(frozen=True)
class ActionOutcome:
    action: LegalAction | None = None
    expected_effects: tuple[str, ...] = ()
    observed_effects: tuple[str, ...] = ()
    verified: bool = False


@dataclass(frozen=True)
class GameState:
    step: int
    phase: Field[str] = field(default_factory=Field.unknown)
    underlying_activity: Field[str] = field(default_factory=Field.unknown)
    overlays: tuple[Field[str], ...] = ()
    turn: Field[str] = field(default_factory=Field.unknown)
    player: PlayerState = field(default_factory=PlayerState)
    enemies: tuple[EnemyState, ...] = ()
    hand: tuple[CardState, ...] = ()
    unknown_objects: tuple[UnknownObject, ...] = ()
    selected_card_id: Field[str] = field(default_factory=Field.unknown)
    hand_epoch: int = 0
    last_action: ActionOutcome = field(default_factory=ActionOutcome)
    legal_actions: tuple[LegalAction, ...] = ()


def _field(value: T | None, source: str, confidence: float, step: int) -> Field[T]:
    return Field(value=value, source=source, confidence=confidence, last_confirmed_step=step) if value is not None else Field.unknown()


def _bbox(obj: Any) -> tuple[float, float, float, float] | None:
    values = tuple(getattr(obj, key, None) if not isinstance(obj, dict) else obj.get(key) for key in ("x0", "y0", "x1", "y1"))
    if all(isinstance(value, (int, float)) for value in values) and values != (0.0, 0.0, 0.0, 0.0):
        return tuple(float(value) for value in values)  # type: ignore[return-value]
    raw = obj.get("bbox") if isinstance(obj, dict) else getattr(obj, "bbox", None)
    if isinstance(raw, (list, tuple)) and len(raw) >= 4:
        try:
            return tuple(float(value) for value in raw[:4])  # type: ignore[return-value]
        except (TypeError, ValueError):
            pass
    x = obj.get("x") if isinstance(obj, dict) else getattr(obj, "x", None)
    y = obj.get("y") if isinstance(obj, dict) else getattr(obj, "y", None)
    return (float(x), float(y), float(x), float(y)) if isinstance(x, (int, float)) and isinstance(y, (int, float)) else None


def _get(obj: Any, name: str, default: Any = None) -> Any:
    return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)


def _center(box: tuple[float, float, float, float] | None) -> tuple[float, float] | None:
    return ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2) if box is not None else None


def _nearest_id(items: Iterable[Any], bbox: tuple[float, float, float, float] | None, *, maximum: float = 0.16) -> str | None:
    point = _center(bbox)
    if point is None:
        return None
    winner: str | None = None
    best = maximum
    for item in items:
        old = _center(item.bbox.value)
        if old is None:
            continue
        distance = hypot(point[0] - old[0], point[1] - old[1])
        if distance < best:
            best, winner = distance, item.id
    return winner


def _parse_cost(label: str) -> int | None:
    match = re.match(r"\s*(\d+)\b", label or "")
    return int(match.group(1)) if match else None


def _placeholder(label: str) -> bool:
    """Geometry is never enough to turn an icon into a game entity."""
    return " ".join((label or "").lower().replace(":", "").split()) in {"", "icon", "unknown", "button", "character"}


def _anonymous_enemy_label(label: str) -> bool:
    """Role can be certain even when the creature identity is not."""
    from loop.agent.entities import is_anonymous_enemy_label

    return is_anonymous_enemy_label(label)


_SPRITE_ENEMY_Y = 0.50
_ENEMY_COLUMN_DX = 0.08


def _obj_xy(obj: Any) -> tuple[float, float] | None:
    return _center(_bbox(obj))


def _is_sprite_band_enemy_geom(obj: Any) -> bool:
    """True for combat body sprites, not HP/intent OCR sitting ~y=0.35."""
    from loop.os.aim import in_combat_sprite_band

    xy = _obj_xy(obj)
    if xy is None:
        return False
    cx, cy = xy
    return cy >= _SPRITE_ENEMY_Y and in_combat_sprite_band(cy, cx)


def _collapse_enemy_columns(objs: list[Any]) -> list[Any]:
    """One enemy per x-column: keep the lower (sprite) mark over HP text."""
    kept: list[Any] = []
    for obj in objs:
        xy = _obj_xy(obj)
        if xy is None:
            continue
        cx, cy = xy
        rival_i = next(
            (
                i
                for i, other in enumerate(kept)
                if abs((_obj_xy(other) or (99.0, 99.0))[0] - cx) < _ENEMY_COLUMN_DX
            ),
            None,
        )
        if rival_i is None:
            kept.append(obj)
            continue
        rival_y = (_obj_xy(kept[rival_i]) or (0.0, -1.0))[1]
        if cy > rival_y:
            kept[rival_i] = obj
    return kept


def _same_turn(previous: GameState | None, turn: str) -> int:
    if previous is None:
        return 0
    # A new player turn starts after the opponent's turn. Unknown does not reset IDs.
    if previous.turn.value == "theirs" and turn == "ours":
        return previous.hand_epoch + 1
    return previous.hand_epoch


def _action_outcome(
    previous: GameState | None,
    action: LegalAction | None,
    expected: Iterable[str],
    current: PlayerState,
    current_enemies: Iterable[EnemyState],
    current_cards: Iterable[CardState],
    current_turn: str | None = None,
) -> ActionOutcome:
    expected_tuple = tuple(expected)
    if action is None:
        return ActionOutcome()
    observed: list[str] = []
    if previous is not None:
        if previous.player.energy.value is not None and current.energy.value is not None and current.energy.value < previous.player.energy.value:
            observed.append("energy_decreased")
        if previous.player.block.value is not None and current.block.value is not None and current.block.value > previous.player.block.value:
            observed.append("block_increased")
        old_enemies = {enemy.id: enemy for enemy in previous.enemies}
        for enemy in current_enemies:
            old = old_enemies.get(enemy.id)
            if old and old.hp.value is not None and enemy.hp.value is not None and enemy.hp.value < old.hp.value:
                observed.append("enemy_hp_decreased")
                break
        turn_ended = previous.turn.value == "ours" and current_turn == "theirs"
        if not turn_ended and action.card_id and action.card_id not in {card.id for card in current_cards}:
            observed.append("card_left_hand")
    # Expected effects are an OR set: any independently sensed expected effect
    # verifies the play. Unrelated changes never do.
    aliases = {"energy_spent": "energy_decreased", "card_leaves_hand": "card_left_hand", "enemy_hp_changes": "enemy_hp_decreased", "player_block_or_status_changes": "block_increased"}
    expected_observed = {aliases.get(effect, effect) for effect in expected_tuple}
    return ActionOutcome(action=action, expected_effects=expected_tuple, observed_effects=tuple(observed), verified=bool(set(observed) & expected_observed))


def _legal_actions(state: GameState, objects: list[Any]) -> tuple[LegalAction, ...]:
    actions: list[LegalAction] = []
    if state.overlays:
        dismiss = next((obj for obj in objects if str(_get(obj, "role", "")) == "button"), None)
        if dismiss is not None:
            actions.append(LegalAction("dismiss_overlay", object_id=str(_get(dismiss, "id")), reason="sensor-confirmed overlay"))
        return tuple(actions)
    if state.underlying_activity.value != "combat" or state.turn.value != "ours":
        return tuple(actions)
    targets = [enemy for enemy in state.enemies if enemy.targetable.value is True]
    selected = state.selected_card_id.value
    for card in state.hand:
        if card.playable.value is not True:
            continue
        if selected == card.id and targets:
            actions.extend(LegalAction("play_card", card_id=card.id, target_id=enemy.id, reason="selected playable card and targetable enemy") for enemy in targets)
        elif selected != card.id:
            actions.append(LegalAction("select_card", card_id=card.id, reason="sensor-confirmed playable hand card"))
    if any("end turn" in str(_get(obj, "label", "")).lower() for obj in objects):
        actions.append(LegalAction("end_turn", reason="sensor-confirmed end-turn control"))
    actions.extend(LegalAction("inspect", object_id=item.id, reason="unidentified interactive object") for item in state.unknown_objects)
    return tuple(actions)


class Reconciler:
    """Stateless wrapper; state is explicitly passed and never mutated in place."""

    def reconcile(
        self,
        previous: GameState | None,
        *,
        step: int,
        host_stats: dict[str, Any] | None,
        frame: Any | None,
        executed_action: LegalAction | None = None,
        expected_effects: Iterable[str] = (),
        verified_selected_card_id: str | None = None,
        claims: Iterable[str] = (),
    ) -> GameState:
        del claims  # Planner prose is not sensor evidence and cannot update state.
        stats = host_stats or {}
        objects = list(_get(frame, "objects", []) or [])
        conf = 0.9 if stats.get("ocr_confidence") == "high" else 0.55
        screen = stats.get("screen") or _get(frame, "screen_hint")
        activity = stats.get("activity") or ("combat" if stats.get("combat_active") else None)
        turn = stats.get("turn")
        phase = _field(str(screen), "host_stats", conf, step) if screen else Field.unknown()
        underlying = _field(str(activity), "host_stats", conf, step) if activity else Field.unknown()
        turn_field = _field(str(turn), "host_stats", conf, step) if turn in {"ours", "theirs"} else Field.unknown()
        pdata = stats.get("player") if isinstance(stats.get("player"), dict) else {}
        meta = stats.get("meta") if isinstance(stats.get("meta"), dict) else {}
        player = PlayerState(**{key: _field(pdata.get(key, meta.get(key)), "host_stats", conf, step) for key in ("hp", "hp_max", "block", "energy", "energy_max", "gold")})
        overlay_titles: list[str] = []
        tutorial = stats.get("tutorial")
        if isinstance(tutorial, dict) and tutorial.get("active"):
            overlay_titles.append(str(tutorial.get("title") or "tutorial"))
        if str(screen or "").lower() == "tutorial" and not overlay_titles:
            overlay_titles.append("tutorial")
        overlays = tuple(_field(title, "host_stats", conf, step) for title in overlay_titles)
        epoch = _same_turn(previous, turn_field.value or "unknown")

        host_enemies = list(stats.get("enemies") or [])
        from loop.agent.entities import is_frame_mark_token, is_hp_anchor_label, is_player_row

        def _is_player_obj(obj: Any) -> bool:
            return is_player_row({
                "label": str(_get(obj, "label", "")),
                "role": str(_get(obj, "role", "")),
                "side": str(_get(obj, "side", "")),
                "x": _get(obj, "x"),
                "y": _get(obj, "y"),
                "x0": _get(obj, "x0"),
                "y0": _get(obj, "y0"),
                "x1": _get(obj, "x1"),
                "y1": _get(obj, "y1"),
                "bbox": _get(obj, "bbox"),
            }) or is_hp_anchor_label(str(_get(obj, "label", "")))

        def _box_iou(
            a: tuple[float, float, float, float] | None,
            b: tuple[float, float, float, float] | None,
        ) -> float:
            if a is None or b is None:
                return 0.0
            ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
            ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
            inter = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
            if inter <= 0:
                return 0.0
            union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
            return inter / max(1e-9, union)

        enemy_objs: list[Any] = []
        for obj in objects:
            role = str(_get(obj, "role", "")).lower()
            label = str(_get(obj, "label", ""))
            if _is_player_obj(obj):
                continue
            if role not in {"enemy", "target", "unknown_character"}:
                continue
            if _placeholder(label) and not _anonymous_enemy_label(label):
                continue
            if not _is_sprite_band_enemy_geom(obj):
                continue
            box = _bbox(obj)
            if any(_box_iou(box, _bbox(other)) >= 0.45 for other in enemy_objs):
                continue
            enemy_objs.append(obj)
        enemy_objs = _collapse_enemy_columns(enemy_objs)
        prior_enemies = previous.enemies if previous else ()
        enemies: list[EnemyState] = []
        for index, obj in enumerate(enemy_objs):
            box = _bbox(obj)
            observed_id = str(_get(obj, "object_id", "") or "").strip()
            if is_frame_mark_token(observed_id):
                observed_id = ""
            eid = _nearest_id(prior_enemies, box) or observed_id or f"enemy_{step}_{index + 1}"
            if is_frame_mark_token(eid):
                eid = f"enemy_{step}_{index + 1}"
            label = str(_get(obj, "label", "")).strip()
            from loop.os.aim import _ocr_junk

            anonymous = _anonymous_enemy_label(label) or _ocr_junk(label)
            label_value = None if anonymous or label.lower() in {"", "icon", "unknown"} else label
            host_enemy = host_enemies[index] if index < len(host_enemies) else {}
            corroborated = anonymous and isinstance(host_enemy, dict) and (
                host_enemy.get("hp") is not None or host_enemy.get("hp_max") is not None
            )
            enemies.append(EnemyState(
                id=eid,
                label=_field(label_value, "frame", float(_get(obj, "confidence", 0.5)), step),
                bbox=_field(box, "frame", float(_get(obj, "confidence", 0.5)), step),
                hp=_field(host_enemy.get("hp") if isinstance(host_enemy, dict) else None, "host_stats", conf, step),
                hp_max=_field(host_enemy.get("hp_max") if isinstance(host_enemy, dict) else None, "host_stats", conf, step),
                intent=Field.unknown(),
                targetable=_field(bool(turn_field.value == "ours" and underlying.value == "combat" and (label_value is not None or corroborated)), "host+frame" if corroborated else "reconciler", 0.92 if corroborated else 0.9, step),
            ))
        # OCR-only enemies remain explicit but cannot be targeted without a frame location.
        for index, row in enumerate((stats.get("enemies") or [])[len(enemies):], start=len(enemies) + 1):
            if isinstance(row, dict):
                enemies.append(EnemyState(id=f"enemy_ocr_{step}_{index}", hp=_field(row.get("hp"), "host_stats", conf, step), hp_max=_field(row.get("hp_max"), "host_stats", conf, step)))

        card_objs = [obj for obj in objects if str(_get(obj, "role", "")).lower() == "card" and not _placeholder(str(_get(obj, "label", "")))]
        prior_cards = previous.hand if previous and previous.hand_epoch == epoch else ()
        cards: list[CardState] = []
        for index, obj in enumerate(card_objs):
            box, label = _bbox(obj), str(_get(obj, "label", "")).strip()
            observed_id = str(_get(obj, "object_id", "") or "").strip()
            cid = _nearest_id(prior_cards, box) or observed_id or f"card_{epoch}_{index + 1}"
            cost = _parse_cost(label)
            energy = player.energy.value
            playable = energy is not None and (cost is None or cost <= energy) and turn_field.value == "ours" and underlying.value == "combat"
            selected = bool(previous and previous.selected_card_id.value == cid)
            cards.append(CardState(
                id=cid,
                name=_field(label if label and label.lower() not in {"icon", "unknown"} else None, "frame", float(_get(obj, "confidence", 0.5)), step),
                cost=_field(cost, "frame", 0.6, step), bbox=_field(box, "frame", float(_get(obj, "confidence", 0.5)), step),
                playable=_field(playable, "reconciler", 0.85, step), selected=_field(selected, "reconciler", 0.75, step),
            ))
        selected_id = verified_selected_card_id if verified_selected_card_id in {card.id for card in cards} else None
        if selected_id is None and previous and previous.selected_card_id.value in {card.id for card in cards}:
            selected_id = previous.selected_card_id.value
        cards = [
            CardState(id=card.id, name=card.name, cost=card.cost, bbox=card.bbox, playable=card.playable,
                      selected=_field(card.id == selected_id, "verified_action" if card.id == verified_selected_card_id else "reconciler", 0.9 if card.id == verified_selected_card_id else 0.75, step))
            for card in cards
        ]
        unknowns = tuple(
            UnknownObject(id=f"object_{step}_{_get(obj, 'id', index)}", bbox=_field(_bbox(obj), "frame", float(_get(obj, "confidence", 0.5)), step), label=_field(str(_get(obj, "label", "")) or None, "frame", float(_get(obj, "confidence", 0.5)), step))
            for index, obj in enumerate(objects)
            if str(_get(obj, "kind", "")).lower() in {"unknown", "interactive"} and (
                str(_get(obj, "role", "")).lower() in {"unknown", "icon", "other"}
                or _placeholder(str(_get(obj, "label", "")))
            )
        )
        provisional = GameState(step=step, phase=phase, underlying_activity=underlying, overlays=overlays, turn=turn_field, player=player, enemies=tuple(enemies), hand=tuple(cards), unknown_objects=unknowns, selected_card_id=_field(selected_id, "reconciler", 0.75, step), hand_epoch=epoch, last_action=_action_outcome(previous, executed_action, expected_effects, player, enemies, cards, current_turn=turn_field.value))
        return GameState(**{**provisional.__dict__, "legal_actions": _legal_actions(provisional, objects)})


def reconcile(previous: GameState | None, **kwargs: Any) -> GameState:
    """Convenient functional API for loop integration."""
    return Reconciler().reconcile(previous, **kwargs)
