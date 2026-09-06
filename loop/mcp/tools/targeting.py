from __future__ import annotations

"""Deterministic, current-frame combat grounding tools.

Marks are deliberately frame-local. This module exposes their current semantic
role and never promotes a placeholder detector label (notably ``:Icon``) into
a card or enemy merely because it is in a convenient band.
"""

import hashlib
import json
from typing import Any

from loop.agent.heuristic import HAND_Y
from loop.memory.screen_objects import ScreenObject, ScreenObjectStore
from loop.os.aim import HUD_CHROME_Y, in_combat_sprite_band, label_kind


_PLACEHOLDERS = frozenset({
    "", ":icon", "icon", "button", "buttons", "menu", "menus", "input",
    "input elements", "navigation", "sidebar", "character", "unknown relic",
    "visual", "interactable",
})
_DEFENSIVE_WORDS = frozenset({"defend", "block", "shield", "guard", "barrier"})
_UNKNOWN_WORDS = ("unknown", "unidentified")
_COMBAT_TARGET_ROLES = frozenset({"enemy", "player"})


def _compact(label: str) -> str:
    return " ".join((label or "").lower().replace("_", " ").split())


def _is_placeholder(label: str) -> bool:
    text = _compact(label)
    return text in _PLACEHOLDERS or text.lstrip(":") in _PLACEHOLDERS


def _is_unknown_label(label: str) -> bool:
    text = _compact(label)
    return _is_placeholder(text) or any(word in text for word in _UNKNOWN_WORDS)


def _is_anonymous_enemy(label: str) -> bool:
    text = _compact(label).replace("_", " ").lstrip(":")
    return text.startswith(("unknown character", "unidentified enemy", "unknown enemy"))


def _is_end_turn(label: str) -> bool:
    return "end turn" in _compact(label)


def _is_menu_progress(obj: ScreenObject) -> bool:
    return label_kind(obj.label) == "progress"


def object_role(obj: ScreenObject, *, entity_targets_ok: bool = False) -> str:
    """Return one of card/enemy/player/button/hud/unknown for this exact frame."""
    from loop.agent.entities import is_hp_anchor_label, is_player_row

    screen = (obj.screen or "").lower()
    explicit = (obj.role or "").strip().lower()
    compact = _compact(obj.label)
    # Canonical generic UI semantics are authoritative at every coordinate.
    # A modal button in the lower band must never become a card.
    if explicit in {"button", "control", "dialog", "menu", "nav_prev", "nav_next"}:
        return "button"
    if compact in {"yes", "no", "got it", "confirm", "continue", "close", "back", "prev", "previous", "next"}:
        return "button"
    if is_hp_anchor_label(obj.label):
        return "hud"
    if is_player_row({
        "label": obj.label,
        "role": obj.role,
        "x": obj.x,
        "y": obj.y,
        "x0": obj.x0,
        "y0": obj.y0,
        "x1": obj.x1,
        "y1": obj.y1,
    }):
        # Hero body is a self-target. HP/name plate above it is HUD.
        if in_combat_sprite_band(obj.y, obj.x) and float(obj.y) >= 0.50:
            return "player"
        return "hud"
    if _is_menu_progress(obj) or label_kind(obj.label) in {"progress", "danger"}:
        return "button"
    # Chrome is never a combat target, even if the detector calls it an icon.
    if obj.y <= HUD_CHROME_Y:
        return "hud"
    domain_allowed = screen == "combat" or entity_targets_ok
    if domain_allowed and explicit in {"card", "enemy", "player", "target"}:
        return "enemy" if explicit == "target" else explicit
    if domain_allowed:
        if obj.y >= HAND_Y:
            return "card" if not _is_unknown_label(obj.label) and not _is_end_turn(obj.label) else "unknown"
        if in_combat_sprite_band(obj.y, obj.x):
            # HP/intent text sits ~0.35; only sprite-band bodies are targets.
            if float(obj.y) < 0.50 and explicit != "enemy":
                return "hud"
            if explicit == "enemy" or _is_anonymous_enemy(obj.label):
                return "enemy"
            if _is_unknown_label(obj.label):
                return "unknown"
            if explicit in {"", "target"}:
                return "enemy"
            return "unknown"
    return "unknown"


def _object_payload(obj: ScreenObject, *, entity_targets_ok: bool = False) -> dict[str, Any]:
    return {
        "mark": obj.mark_id,
        "label": obj.label,
        "role": object_role(obj, entity_targets_ok=entity_targets_ok),
        "object_id": obj.object_id,
        "bounds": {"x0": obj.x0, "y0": obj.y0, "x1": obj.x1, "y1": obj.y1},
        "current_frame": True,
    }


def _snapshot_identity(store: ScreenObjectStore) -> dict[str, Any]:
    """Return an opaque identity for exactly the inventory currently in ``store``.

    This is deliberately derived at planning time, rather than accepted from an
    MCP caller.  A model can therefore choose stable object IDs, but cannot
    accidentally bind a transaction to a prior run, step, or layout.
    """
    frame = {
        "run_id": str(getattr(store, "_run_id", "")),
        "step_id": int(getattr(store, "_step_id", 0)),
        "hand_generation": int(getattr(store, "_hand_generation", 0) or 0),
    }
    objects = [
        {
            "object_id": obj.object_id,
            "mark_id": obj.mark_id,
            "bounds": [obj.x0, obj.y0, obj.x1, obj.y1],
            "label": obj.label,
            "role": obj.role,
            "screen": obj.screen,
        }
        for obj in store.objects
    ]
    encoded = json.dumps({"frame": frame, "objects": objects}, sort_keys=True, separators=(",", ":"))
    # The nonce need only identify the captured inventory; it is not a secret.
    return {**frame, "snapshot_id": hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]}


def current_snapshot_identity(store: ScreenObjectStore) -> dict[str, Any]:
    """Public read-only identity shared by snapshot construction and planning."""
    return dict(_snapshot_identity(store))


def hand_generation(store: ScreenObjectStore) -> int:
    """Monotonic hand epoch. Batch 2 may read this; it must not change the rule."""
    return int(getattr(store, "_hand_generation", 0) or 0)


def invalidate_hand_snapshot(store: ScreenObjectStore, *, reason: str = "") -> dict[str, Any]:
    """Bump the hand epoch so a prior ordinal intent cannot bind this inventory."""
    store._hand_generation = hand_generation(store) + 1
    identity = _snapshot_identity(store)
    identity["invalidation_reason"] = str(reason or "hand-reflow")
    return identity


def _resolved_object(obj: ScreenObject, *, entity_targets_ok: bool = False) -> dict[str, Any]:
    """Frame-local execution data, emitted only after stable-ID resolution."""
    return {
        "object_id": obj.object_id,
        "mark_id": obj.mark_id,
        "role": object_role(obj, entity_targets_ok=entity_targets_ok),
        "label": obj.label,
        "point": {"x": obj.x, "y": obj.y},
        "bounds": {"x0": obj.x0, "y0": obj.y0, "x1": obj.x1, "y1": obj.y1},
    }


def screen_objects(store: ScreenObjectStore, *, entity_targets_ok: bool = False) -> dict[str, Any]:
    """Typed object inventory. Never persist the mark ids returned here."""
    objects = [_object_payload(obj, entity_targets_ok=entity_targets_ok) for obj in store.objects]
    unknown = [obj for obj in objects if obj["role"] == "unknown"]
    return {
        "frame": {"run_id": getattr(store, "_run_id", ""), "step_id": getattr(store, "_step_id", 0)},
        "objects": objects,
        "cards": [obj for obj in objects if obj["role"] == "card"],
        "enemies": [obj for obj in objects if obj["role"] == "enemy"],
        "players": [obj for obj in objects if obj["role"] == "player"],
        "buttons": [obj for obj in objects if obj["role"] == "button"],
        "hud": [obj for obj in objects if obj["role"] == "hud"],
        "unknown": unknown,
        "inspection_proposals": [
            {"action": "inspect", "target_mark": obj["mark"],
             "expected_effects": {"hovered_mark": obj["mark"], "identity_resolved_or_rejected": True}}
            for obj in unknown
        ],
    }


def _is_hand_card(obj: ScreenObject, *, entity_targets_ok: bool = False) -> bool:
    return object_role(obj, entity_targets_ok=entity_targets_ok) == "card"


def _requires_target(card: ScreenObject) -> bool:
    """Legacy v1 heuristic. Do not use in stable-ID v2 planning."""
    return not bool(set(_compact(card.label).split()) & _DEFENSIVE_WORDS)


def _resolve_card(store: ScreenObjectStore, card_mark_id: int | None, choice: str | None, *, entity_targets_ok: bool) -> ScreenObject | None:
    if card_mark_id is not None:
        return store.by_id(int(card_mark_id))
    pick = _compact(choice or "")
    if not pick:
        return None
    for obj in store.objects:
        if _compact(obj.label) == pick and _is_hand_card(obj, entity_targets_ok=entity_targets_ok):
            return obj
    return None


def targeting_options(
    store: ScreenObjectStore, *, card_mark_id: int | None = None, choice: str | None = None,
    selected: str | None = None, entity_targets_ok: bool = False,
) -> dict[str, Any]:
    card = _resolve_card(store, card_mark_id, choice or selected, entity_targets_ok=entity_targets_ok)
    inventory = screen_objects(store, entity_targets_ok=entity_targets_ok)
    players = inventory["players"]
    payload: dict[str, Any] = {
        "cards": inventory["cards"],
        "targets": inventory["enemies"] + players,
        "players": players,
        "inspection_proposals": inventory["inspection_proposals"],
        "requires_target": False,
    }
    if card is None:
        if card_mark_id is not None or choice or selected:
            payload["error"] = "selected card is not a typed current-frame card"
        return payload
    payload["card"] = _object_payload(card, entity_targets_ok=entity_targets_ok)
    payload["requires_target"] = _requires_target(card)
    return payload


def targeting_validate(
    store: ScreenObjectStore, *, card_mark: int | None = None, target_mark: int | None = None,
    entity_targets_ok: bool = False, run_id: str | None = None, step_id: int | None = None,
) -> dict[str, Any]:
    # A supplied nonce is mandatory at dispatch boundaries; keeping it optional
    # here preserves direct/unit use while callers migrate to the transaction API.
    if run_id is not None and str(run_id) != str(getattr(store, "_run_id", "")):
        return {"ok": False, "reason": "stale-frame-run"}
    if step_id is not None and int(step_id) != int(getattr(store, "_step_id", -1)):
        return {"ok": False, "reason": "stale-frame-step"}
    if card_mark is None:
        return {"ok": target_mark is None, "reason": "card play requires a card mark" if target_mark is not None else "no card transaction"}
    card = store.by_id(int(card_mark))
    if card is None:
        return {"ok": False, "reason": f"unknown card mark {card_mark}"}
    role = object_role(card, entity_targets_ok=entity_targets_ok)
    if role == "button" and target_mark is None:
        return {"ok": True, "reason": "non-card button click"}
    if (card.screen or "").lower() != "combat" and not entity_targets_ok and target_mark is None:
        # This validator is also used for ordinary map/menu clicks.  A target
        # would make it a card transaction and is rejected below.
        return {"ok": True, "reason": "non-combat single click"}
    if role != "card":
        return {"ok": False, "reason": f"card mark {card_mark} is {role}, not a typed hand card"}
    requires_target = _requires_target(card)
    if target_mark is None:
        return {"ok": not requires_target, "reason": "card requires target enemy" if requires_target else "valid self card"}
    target = store.by_id(int(target_mark))
    if target is None:
        return {"ok": False, "reason": f"unknown target mark {target_mark}"}
    if object_role(target, entity_targets_ok=entity_targets_ok) not in _COMBAT_TARGET_ROLES:
        return {"ok": False, "reason": f"target mark {target_mark} is not a typed combat target"}
    return {"ok": True, "reason": "valid typed card+target"}


def play_card_plan(
    store: ScreenObjectStore, *, card_mark: int | None = None, target_mark: int | None = None,
    choice: str | None = None, entity_targets_ok: bool = False,
    run_id: str | None = None, step_id: int | None = None,
) -> dict[str, Any]:
    # A transaction plan is an execution contract, unlike direct validation
    # helpers used by legacy callers. It must be bound to the frame inventory
    # from which both marks were selected.
    if run_id is None or step_id is None:
        return {"ok": False, "reason": "current-frame run_id and step_id are required", "transaction": None}
    card = _resolve_card(store, card_mark, choice, entity_targets_ok=entity_targets_ok)
    actual_mark = card.mark_id if card is not None else card_mark
    result = targeting_validate(store, card_mark=actual_mark, target_mark=target_mark, entity_targets_ok=entity_targets_ok, run_id=run_id, step_id=step_id)
    if not result["ok"]:
        return {"ok": False, "reason": result["reason"], "transaction": None}
    assert card is not None
    needs_target = _requires_target(card)
    effects: dict[str, bool] = {"energy_spent": True, "card_leaves_hand": True}
    if needs_target:
        target = store.by_id(int(target_mark)) if target_mark is not None else None
        target_role = (
            object_role(target, entity_targets_ok=entity_targets_ok) if target is not None else ""
        )
        if target_role == "player":
            effects["player_block_or_status_changes"] = True
        else:
            effects["enemy_hp_changes"] = True
    else:
        effects["player_block_or_status_changes"] = True
    return {
        "ok": True, "reason": "validated current-frame transaction",
        "transaction": {"action": "play_card", "card_mark": card.mark_id,
                        "target_mark": int(target_mark) if needs_target else None,
                        "frame": {"run_id": getattr(store, "_run_id", ""), "step_id": getattr(store, "_step_id", 0)},
                        "expected_effects": effects},
    }


def play_card(
    store: ScreenObjectStore, *, card_mark: int | None = None, target_mark: int | None = None,
    choice: str | None = None, entity_targets_ok: bool = False,
    run_id: str | None = None, step_id: int | None = None,
) -> dict[str, Any]:
    """Create the terminal, frame-bound command consumed by the live collector.

    This module deliberately has no controller.  Keeping it that way means the
    optional stdio server cannot acquire OS-input authority; CollectAgent owns
    dispatch at its existing focus/staleness boundary.
    """
    plan = play_card_plan(
        store,
        card_mark=card_mark,
        target_mark=target_mark,
        choice=choice,
        entity_targets_ok=entity_targets_ok,
        run_id=run_id,
        step_id=step_id,
    )
    if not plan.get("ok"):
        return {"ok": False, "terminal": True, "reason": plan.get("reason"), "transaction": None}
    return {
        "ok": True,
        "terminal": True,
        "reason": "validated terminal play-card command",
        "transaction": plan["transaction"],
    }


def resolve_planned_source(
    store: ScreenObjectStore,
    *,
    card_id: str,
    snapshot: dict[str, Any] | None,
    entity_targets_ok: bool = False,
) -> dict[str, Any]:
    """Resolve a snapshot-scoped ``card_id``. Never reinterpret a stale ordinal.

    Planner and executor share this lookup. Label/cost/order may describe the
    current-snapshot object, but they cannot mint a new identity after reflow.
    """
    identity = _snapshot_identity(store)
    planned = snapshot if isinstance(snapshot, dict) else {}
    planned_id = str(planned.get("snapshot_id") or "")
    if not planned_id or planned_id != str(identity.get("snapshot_id") or ""):
        return {"ok": False, "reason": "stale-snapshot", "source": None, "identity": identity}
    planned_gen = planned.get("hand_generation")
    if planned_gen is not None and int(planned_gen) != int(identity.get("hand_generation") or 0):
        return {"ok": False, "reason": "stale-snapshot", "source": None, "identity": identity}
    card = store.by_object_id(str(card_id or "").strip())
    if card is None or not _is_hand_card(card, entity_targets_ok=entity_targets_ok):
        return {"ok": False, "reason": "source-not-found", "source": None, "identity": identity}
    return {
        "ok": True,
        "reason": "current-snapshot",
        "source": card,
        "identity": identity,
        "resolved": _resolved_object(card, entity_targets_ok=entity_targets_ok),
    }


def plan_card_by_id(
    store: ScreenObjectStore, *, card_id: str, target_id: str | None = None,
    entity_targets_ok: bool = False,
) -> dict[str, Any]:
    """Create a v2 frame-bound card transaction from stable object IDs.

    Targeting is intentionally *optional*.  Before the source card click the
    runtime cannot reliably know whether a game will auto-resolve the card or
    enter target-selection mode.  A supplied target is consequently a typed
    candidate, never a command to click it and never evidence that a card needs
    a target.  The collector observes the source-click result and decides
    ``auto_resolved``, ``selected``, or ``target_required``.
    """
    snapshot = _snapshot_identity(store)
    resolved = resolve_planned_source(
        store, card_id=card_id, snapshot=snapshot, entity_targets_ok=entity_targets_ok,
    )
    if not resolved.get("ok"):
        return {"ok": False, "reason": resolved["reason"], "transaction": None}
    card = resolved["source"]
    assert card is not None

    target: ScreenObject | None = None
    target_key = str(target_id or "").strip()
    if target_key:
        target = store.by_object_id(target_key)
        if target is None or object_role(target, entity_targets_ok=entity_targets_ok) not in _COMBAT_TARGET_ROLES:
            return {"ok": False, "reason": "target-not-found", "transaction": None}

    return {
        "ok": True,
        "reason": "validated stable-id transaction",
        "transaction": {
            "decision_schema": "v2",
            "action": "play_card",
            "snapshot": snapshot,
            "source": _resolved_object(card, entity_targets_ok=entity_targets_ok),
            # This is a candidate to rebind after a confirmed selection. It is
            # deliberately retained for Defend/self cards as harmless context.
            "target_candidate": _resolved_object(target, entity_targets_ok=entity_targets_ok) if target else None,
            "expected_effects": {
                "auto_resolved": ["energy_spent", "card_leaves_hand", "player_block_or_status_changes", "enemy_hp_changes", "turn_changed"],
                "selected": ["card_lifted", "card_highlighted", "card_moved_in_hand"],
                "target_required_after_source_click": "observe",
            },
        },
    }


def validate_stable_transaction(store: ScreenObjectStore, transaction: dict[str, Any]) -> dict[str, Any]:
    """Confirm that a v2 execution contract still describes the live frame.

    This is for the collector's dispatch boundary, not an MCP decision tool:
    it accepts a previously produced contract and rejects it after *any*
    inventory change.  The caller cannot manufacture a current nonce because
    the original contract carries a hash of the full source inventory.
    """
    if not isinstance(transaction, dict):
        return {"ok": False, "reason": "stale-snapshot"}
    source = transaction.get("source")
    card_id = str((source or {}).get("object_id") or "") if isinstance(source, dict) else ""
    resolved = resolve_planned_source(
        store,
        card_id=card_id,
        snapshot=transaction.get("snapshot") if isinstance(transaction.get("snapshot"), dict) else None,
        entity_targets_ok=True,
    )
    if not resolved.get("ok"):
        return {"ok": False, "reason": str(resolved.get("reason") or "stale-snapshot")}
    candidate = transaction.get("target_candidate")
    if candidate is not None and (
        not isinstance(candidate, dict) or store.by_object_id(str(candidate.get("object_id") or "")) is None
    ):
        return {"ok": False, "reason": "target-not-found"}
    return {"ok": True, "reason": "current-snapshot"}


def play_card_by_id(
    store: ScreenObjectStore, *, card_id: str, target_id: str | None = None,
    entity_targets_ok: bool = False,
) -> dict[str, Any]:
    """Terminal v2 stable-ID command consumed by the collector-owned executor."""
    plan = plan_card_by_id(store, card_id=card_id, target_id=target_id, entity_targets_ok=entity_targets_ok)
    if not plan.get("ok"):
        return {"ok": False, "terminal": True, "reason": plan["reason"], "transaction": None}
    return {
        "ok": True,
        "terminal": True,
        "reason": "validated terminal stable-id play-card command",
        "transaction": plan["transaction"],
    }


def inspect_object(
    store: ScreenObjectStore, *, mark_id: int | None = None,
    run_id: str | None = None, step_id: int | None = None,
) -> dict[str, Any]:
    """Validate a current-frame object for the collector-owned hover/reread path."""
    if run_id is None or step_id is None:
        return {"ok": False, "reason": "current-frame run_id and step_id are required", "inspection": None}
    if str(run_id) != str(getattr(store, "_run_id", "")) or int(step_id) != int(getattr(store, "_step_id", -1)):
        return {"ok": False, "reason": "stale-frame", "inspection": None}
    obj = store.by_id(int(mark_id)) if mark_id is not None else None
    if obj is None or object_role(obj, entity_targets_ok=True) == "hud":
        return {"ok": False, "reason": "unknown or non-inspectable mark", "inspection": None}
    return {
        "ok": True,
        "terminal": True,
        "reason": "validated current-frame inspection",
        "inspection": {"mark_id": obj.mark_id, "frame": {"run_id": str(run_id), "step_id": int(step_id)}},
    }


def register_targeting_tools(registry: Any, store: ScreenObjectStore, *, selected_getter: Any,
                             entity_targets_ok_getter: Any | None = None) -> None:
    import json

    def _targets_ok() -> bool:
        return bool(entity_targets_ok_getter()) if callable(entity_targets_ok_getter) else False

    def _objects(**_: Any) -> str:
        return json.dumps(screen_objects(store, entity_targets_ok=_targets_ok()))

    def _options(card_mark_id: int | None = None, choice: str = "", **_: Any) -> str:
        selected = selected_getter() if callable(selected_getter) else None
        return json.dumps(targeting_options(store, card_mark_id=card_mark_id, choice=choice or None,
                                            selected=selected, entity_targets_ok=_targets_ok()))

    def _validate(card_mark: int | None = None, target_mark: int | None = None, run_id: str | None = None, step_id: int | None = None, **_: Any) -> str:
        return json.dumps(targeting_validate(store, card_mark=card_mark, target_mark=target_mark,
                                             entity_targets_ok=_targets_ok(), run_id=run_id, step_id=step_id))

    def _plan(card_mark: int | None = None, target_mark: int | None = None, choice: str = "", run_id: str | None = None, step_id: int | None = None, **_: Any) -> str:
        return json.dumps(play_card_plan(store, card_mark=card_mark, target_mark=target_mark,
                                         choice=choice or None, entity_targets_ok=_targets_ok(), run_id=run_id, step_id=step_id))

    def _play(card_mark: int | None = None, target_mark: int | None = None, choice: str = "", run_id: str | None = None, step_id: int | None = None, **_: Any) -> str:
        return json.dumps(play_card(store, card_mark=card_mark, target_mark=target_mark,
                                    choice=choice or None, entity_targets_ok=_targets_ok(), run_id=run_id, step_id=step_id))

    def _plan_by_id(card_id: str = "", target_id: str = "", **_: Any) -> str:
        return json.dumps(plan_card_by_id(
            store, card_id=card_id, target_id=target_id or None, entity_targets_ok=_targets_ok(),
        ))

    def _play_by_id(card_id: str = "", target_id: str = "", **_: Any) -> str:
        return json.dumps(play_card_by_id(
            store, card_id=card_id, target_id=target_id or None, entity_targets_ok=_targets_ok(),
        ))

    def _inspect(mark_id: int | None = None, run_id: str | None = None, step_id: int | None = None, **_: Any) -> str:
        return json.dumps(inspect_object(store, mark_id=mark_id, run_id=run_id, step_id=step_id))

    registry.register("screen_objects", description="List typed current-frame cards, enemies, players, buttons, HUD, and unknown objects.", parameters={}, handler=_objects)
    registry.register("targeting_options", description="List typed card options and legal enemies/self; unresolved targets include inspection proposals.", parameters={"choice": "optional exact card label", "card_mark_id": "optional int"}, handler=_options)
    registry.register("targeting_validate", description="Validate a current-frame card mark and required enemy or player mark before execution; pass run_id and step_id returned by screen_objects.", parameters={"card_mark": "int", "target_mark": "optional int", "run_id": "string", "step_id": "int"}, handler=_validate)
    registry.register("play_card_plan", description="Create a validated current-frame card transaction. Pass the screen_objects run_id and step_id; stale frames are rejected.", parameters={"card_mark": "optional int", "choice": "optional exact card label", "target_mark": "optional int", "run_id": "string", "step_id": "int"}, handler=_plan)
    registry.register("play_card", description="Terminal live card transaction command. The harness verifies, clicks source, refreshes, rebinds target, clicks it, and verifies the effect.", parameters={"card_mark": "optional int", "choice": "optional exact card label", "target_mark": "optional int", "run_id": "string", "step_id": "int"}, handler=_play)
    registry.register("plan_card_by_id", description="V2 deterministic planning from stable card_id and optional target_id. Run/step/snapshot identity and current marks/coordinates are bound internally; target_id is only a post-selection candidate.", parameters={"card_id": "string", "target_id": "optional string"}, handler=_plan_by_id)
    registry.register("play_card_by_id", description="Terminal V2 stable-ID card command. The harness clicks the source, observes whether it auto-resolved or selected, then rebinds and uses target_id only if needed.", parameters={"card_id": "string", "target_id": "optional string"}, handler=_play_by_id)
    registry.register("inspect_object", description="Validate a current-frame object for a harness-owned hover and tooltip reread.", parameters={"mark_id": "int", "run_id": "string", "step_id": "int"}, handler=_inspect)
