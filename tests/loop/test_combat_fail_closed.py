from __future__ import annotations

import json
from types import SimpleNamespace

from PIL import Image

from loop.agent.loop import CollectAgent
from loop.log.schema import ActionLog
from loop.mcp.orchestrator import ToolOrchestrator
from loop.mcp.registry import ToolRegistry
from loop.memory.semantic import SemanticMemoryStore
from loop.memory.screen_objects import ScreenObjectStore
from loop.os.aim import AimGuide, AimMark
from loop.os.aim import action_from_labels
from loop.os.chords import is_empty_chord
from loop.os.frame_model import FrameModel, FrameObject
from loop.state.game_state import reconcile


def _icon_store(tmp_path):
    store = ScreenObjectStore(tmp_path / "objects.db")
    store.save_frame(
        run_id="r", step_id=1, screen="combat",
        marks=(
            AimMark(id=1, x=.30, y=.84, x0=.2, y0=.74, x1=.4, y1=.94, label=":Icon"),
            AimMark(id=2, x=.52, y=.34, x0=.4, y0=.2, x1=.6, y1=.5, label=":Icon"),
        ),
    )
    return store


def _combat_stats():
    return {
        "screen": "combat", "activity": "combat", "turn": "ours",
        "player": {"hp": 80, "energy": 3},
    }


def test_generic_icon_card_goal_is_state_rejected_before_any_dispatch(tmp_path) -> None:
    """Regression for the split-run failure: icon-in-hand + icon-on-board."""
    store = _icon_store(tmp_path)
    agent = object.__new__(CollectAgent)
    agent._screen_objects = store
    agent._tool_orchestrator = ToolOrchestrator(ToolRegistry())
    agent._selected = None
    agent._aim = None
    agent._obs_metrics = {"screen": "combat"}
    agent._host_stats = _combat_stats()
    agent._pending_exec_error = None
    agent._step_illegal_blocked = False
    agent._last_legal_reason = None
    agent._last_targeting_opts = None
    agent._last_tool_events = []
    agent._step_id = 1
    agent.session_id = "prior-failure-signature"
    agent.config = SimpleNamespace(burst_ms=10)
    frame = FrameModel(objects=[
        FrameObject(1, ":Icon", "interactive", role="card", x=.30, y=.84),
        FrameObject(2, ":Icon", "interactive", role="target", x=.52, y=.34),
    ], screen_hint="combat")
    agent._game_state = reconcile(None, step=1, host_stats=_combat_stats(), frame=frame)

    # The reasoner phrase cannot become an action because no typed card/enemy exists.
    assert agent._state_allows_combat_action("Strike", "Slime") is False
    rejected = agent._gate_targeting(
        ActionLog(mark=1, target=2, click_x=.30, click_y=.84, click_x2=.52, click_y2=.34),
        choice="Strike",
    )
    assert is_empty_chord(rejected)
    assert agent._pending_exec_error == "invalid-target:state-gate"
    assert agent._step_illegal_blocked is True
    assert "typed" in (agent._last_legal_reason or "")
    # A caller sees no chord/primitives to dispatch, and therefore cannot create
    # a card transaction from this generic-icon trace.
    assert rejected.mark is None and rejected.target is None
    assert agent._last_tool_events == [{
        "tool": "targeting_validate", "status": "rejected", "reason": "state-gate",
        "choice": "Strike", "frame": {"run_id": "prior-failure-signature", "step_id": 1},
        "kind": "collector_audit",
    }]
    # The same evidence is suitable for the StepResult/StepLog fields that
    # diagnosed the two previous runs: no transaction, no primitives, and an
    # explicit authoritative state rather than a guessed card binding.
    assert not rejected.model_dump(exclude_none=True).get("mark")
    assert agent._game_state.hand == ()
    store.close()


def test_reloaded_identity_is_prompt_context_but_cannot_type_a_fresh_icon(tmp_path) -> None:
    first = SemanticMemoryStore.for_fixture("sts2", root=tmp_path)
    assert first.remember_identity(
        screen="combat", role="enemy", name="Jaw Worm", provenance="hover tooltip OCR",
        confidence=.9, verified=True,
    ) is not None
    first.save()
    fresh = object.__new__(CollectAgent)
    fresh.semantic_memory = SemanticMemoryStore.for_fixture("sts2", root=tmp_path)
    icon_frame = FrameModel(
        objects=[FrameObject(1, ":Icon", "interactive", role="target", x=.5, y=.35)],
        screen_hint="combat",
    )
    fresh._game_state = reconcile(None, step=2, host_stats=_combat_stats(), frame=icon_frame)
    # A different process loads the persisted fact and places it in the actual
    # reasoner context.  It remains an identity hint, not a frame binding.
    block = fresh._state_prompt_block()
    assert "enemy=Jaw Worm" in block
    assert "Verified enemies: (none)" in block
    assert fresh._game_state.unknown_objects


def test_rejected_icon_card_is_superseded_only_by_safe_enemy_inspection_hover(tmp_path) -> None:
    """The unknown->hover->recapture recovery must reach the fixture."""
    store = _icon_store(tmp_path)
    agent = object.__new__(CollectAgent)
    agent._screen_objects = store
    agent._tool_orchestrator = ToolOrchestrator(ToolRegistry())
    agent._selected = None
    agent._obs_metrics = {"screen": "combat"}
    agent._host_stats = _combat_stats()
    agent._pending_exec_error = None
    agent._step_illegal_blocked = False
    agent._last_legal_reason = None
    agent._last_targeting_opts = None
    agent._last_tool_events = []
    agent._step_id = 1
    agent.session_id = "hover-recovery"
    agent.config = SimpleNamespace(burst_ms=10)
    marks = (
        AimMark(id=1, x=.30, y=.84, x0=.2, y0=.74, x1=.4, y1=.94, label=":Icon"),
        AimMark(id=6, x=.52, y=.34, x0=.4, y0=.2, x1=.6, y1=.5, label=":Icon", role="enemy"),
    )
    agent._aim = AimGuide.from_marks(Image.new("RGB", (64, 64)), marks)
    frame = FrameModel(objects=[
        FrameObject(1, ":Icon", "interactive", role="card", x=.30, y=.84),
        FrameObject(6, ":Icon", "interactive", role="target", x=.52, y=.34),
    ], screen_hint="combat")
    agent._game_state = reconcile(None, step=1, host_stats=_combat_stats(), frame=frame)

    rejected = agent._gate_targeting(
        ActionLog(mark=1, target=6, click_x=.30, click_y=.84, click_x2=.52, click_y2=.34),
        choice="Strike",
    )
    assert is_empty_chord(rejected)
    assert agent._pending_exec_error == "invalid-target:state-gate"

    hover = ActionLog(mark=6, click_x=.52, click_y=.34, hover=True, duration_ms=10)
    hover.name = hover.derive_name()
    assert agent._supersede_rejected_action_with_inspection(hover)
    assert agent._pending_exec_error is None
    assert agent._last_tool_events[-1]["status"] == "superseded"
    assert agent._last_tool_events[-1]["replacement"] == "inspection-hover"

    # This mirrors the dispatch boundary: only the hover is delivered, never
    # the rejected icon card chord or a card transaction.
    received: list[ActionLog] = []

    class _Fixture:
        def execute_chord(self, action: ActionLog) -> list[str]:
            received.append(action)
            return ["hover"]

    fixture = _Fixture()
    if agent._pending_exec_error is None:
        fixture.execute_chord(hover)
    assert received == [hover]
    assert received[0].hover and received[0].mark == 6
    assert all(action.mark != 1 for action in received)
    store.close()


def test_unresolved_inspect_cannot_fall_back_to_icon_hand_click(tmp_path) -> None:
    """Regression for live step 1.4: inspect must not become ``mk_1`` Strike."""
    store = _icon_store(tmp_path)
    agent = object.__new__(CollectAgent)
    agent._screen_objects = store
    agent._obs_metrics = {"screen": "combat"}
    agent._host_stats = _combat_stats()
    agent._pending_exec_error = None
    agent._step_illegal_blocked = False
    agent._last_legal_reason = None
    agent._last_tool_events = []
    agent._step_id = 4
    agent.session_id = "r"
    agent.config = SimpleNamespace(burst_ms=10)
    agent._two_stage = lambda: True
    agent._aim = AimGuide.from_marks(Image.new("RGB", (64, 64)), (
        AimMark(id=1, x=.30, y=.84, x0=.2, y0=.74, x1=.4, y1=.94, label=":Icon"),
        AimMark(id=6, x=.52, y=.34, x0=.4, y0=.2, x1=.6, y1=.5, label=":Icon", role="enemy"),
    ))

    # ``fill_from_aim`` could have produced this generic hand mark after the
    # requested unidentified_enemy inspection failed to resolve.
    unsafe_fill = ActionLog(mark=1, click_x=.30, click_y=.84, duration_ms=10)
    blocked = agent._final_combat_dispatch_gate(
        unsafe_fill, inspect_requested=True, tag="1.4",
    )
    assert is_empty_chord(blocked)
    assert agent._pending_exec_error == "invalid-target:unresolved-inspect"
    assert agent._last_tool_events[-1]["stage"] == "final-pre-dispatch"

    # If a current unknown enemy exists, its hover is the only safe recovery.
    hover = ActionLog(mark=6, click_x=.52, click_y=.34, hover=True, duration_ms=10)
    safe = agent._final_combat_dispatch_gate(hover, inspect_requested=True, tag="1.4")
    received: list[ActionLog] = []

    class _Fixture:
        def execute_chord(self, action: ActionLog) -> list[str]:
            received.append(action)
            return ["hover"]

    fixture = _Fixture()
    if not is_empty_chord(safe) and safe.hover:
        fixture.execute_chord(safe)
    assert received == [hover]
    assert all(action.mark != 1 for action in received)
    store.close()


def test_tool_only_recheck_cannot_fill_into_icon_card_click(tmp_path) -> None:
    """Regression for live step 1.5 request_recheck -> accidental mk_1."""
    store = _icon_store(tmp_path)
    agent = object.__new__(CollectAgent)
    agent._screen_objects = store
    agent._obs_metrics = {"screen": "combat"}
    agent._host_stats = _combat_stats()
    agent._pending_exec_error = None
    agent._step_illegal_blocked = False
    agent._last_legal_reason = None
    agent._last_tool_events = []
    agent._step_id = 1
    agent.session_id = "r"
    agent.config = SimpleNamespace(burst_ms=10)
    agent._two_stage = lambda: True
    agent._aim = AimGuide.from_marks(Image.new("RGB", (64, 64)), (
        AimMark(id=1, x=.30, y=.84, x0=.2, y0=.74, x1=.4, y1=.94, label=":Icon"),
        AimMark(id=6, x=.52, y=.34, x0=.4, y0=.2, x1=.6, y1=.5, label=":Icon"),
    ))
    generic_fill = ActionLog(mark=1, click_x=.30, click_y=.84, duration_ms=10)
    blocked = agent._final_combat_dispatch_gate(
        generic_fill, inspect_requested=False, tool_only=True, tag="1.5",
    )
    assert is_empty_chord(blocked)
    assert agent._pending_exec_error == "invalid-target:tool-only-no-action"
    assert agent._last_tool_events[-1]["reason"] == "tool-only-no-action"

    received: list[ActionLog] = []

    class _Fixture:
        def execute_chord(self, action: ActionLog) -> list[str]:
            received.append(action)
            return ["click"]

    fixture = _Fixture()
    if not is_empty_chord(blocked):
        fixture.execute_chord(blocked)
    assert received == []
    store.close()


def test_play_card_tool_contract_requires_success_nonce_and_exact_marks(tmp_path) -> None:
    """A play_card_plan result, not its request text, is the action contract."""
    store = ScreenObjectStore(tmp_path / "typed.db")
    marks = (
        AimMark(id=1, x=.30, y=.84, x0=.2, y0=.74, x1=.4, y1=.94, label="Strike", role="card"),
        AimMark(id=2, x=.45, y=.84, x0=.4, y0=.74, x1=.5, y1=.94, label="Defend", role="card"),
        AimMark(id=3, x=.75, y=.63, x0=.68, y0=.56, x1=.82, y1=.70, label="Slime", role="enemy"),
    )
    store.save_frame(run_id="r", step_id=1, screen="combat", marks=marks)
    agent = object.__new__(CollectAgent)
    agent._screen_objects = store
    agent._obs_metrics = {"screen": "combat"}
    agent._host_stats = _combat_stats()
    agent._pending_exec_error = None
    agent._step_illegal_blocked = False
    agent._last_legal_reason = None
    agent._step_id = 1
    agent.session_id = "r"
    agent.config = SimpleNamespace(burst_ms=10)
    agent._two_stage = lambda: True
    agent._aim = AimGuide.from_marks(Image.new("RGB", (64, 64)), marks)
    action = ActionLog(mark=1, target=3, click_x=.30, click_y=.84, click_x2=.75, click_y2=.63)

    def event(*, ok: bool, run_id: str = "r", step_id: int = 1, card_mark: int = 1, target_mark: int = 3):
        return {
            "tool": "play_card_plan",
            "result": json.dumps({
                "ok": ok,
                "transaction": {
                    "action": "play_card", "card_mark": card_mark,
                    "target_mark": target_mark,
                    "frame": {"run_id": run_id, "step_id": step_id},
                } if ok else None,
            }),
        }

    agent._last_tool_events = [event(ok=True)]
    contract = agent._validated_tool_card_contract()
    assert contract is not None
    assert agent._final_combat_dispatch_gate(
        action, inspect_requested=False, tool_only=False, tool_action_contract=contract,
    ) == action

    agent._last_tool_events = [event(ok=False)]
    assert agent._validated_tool_card_contract() is None
    failed = agent._final_combat_dispatch_gate(action, inspect_requested=False, tool_only=True)
    assert is_empty_chord(failed)
    assert agent._pending_exec_error == "invalid-target:tool-only-no-action"

    agent._pending_exec_error = None
    agent._last_tool_events = [event(ok=True, run_id="stale", step_id=99)]
    assert agent._validated_tool_card_contract() is None
    stale = agent._final_combat_dispatch_gate(action, inspect_requested=False, tool_only=True)
    assert is_empty_chord(stale)
    assert agent._pending_exec_error == "invalid-target:tool-only-no-action"

    agent._pending_exec_error = None
    agent._last_tool_events = [event(ok=True)]
    mismatch = action.model_copy(update={"mark": 2, "click_x": .45})
    blocked = agent._final_combat_dispatch_gate(
        mismatch,
        inspect_requested=False,
        tool_only=False,
        tool_action_contract=agent._validated_tool_card_contract(),
    )
    assert is_empty_chord(blocked)
    assert agent._pending_exec_error == "invalid-target:tool-plan-action-mismatch"
    store.close()


def test_anonymous_enemy_alias_synthesizes_current_transaction_and_icon_does_not(tmp_path) -> None:
    marks = (
        AimMark(id=1, x=.3, y=.85, x0=.2, y0=.75, x1=.4, y1=.95, label="Strike", role="card"),
        AimMark(id=2, x=.75, y=.63, x0=.68, y0=.56, x1=.82, y1=.70, label="unknown_enemy", role="enemy"),
    )
    store = ScreenObjectStore(tmp_path / "anonymous.db")
    store.save_frame(run_id="r", step_id=1, screen="combat", marks=marks)
    frame = FrameModel(objects=[
        FrameObject(1, "Strike", "interactive", role="card", x=.3, y=.85, x0=.2, y0=.75, x1=.4, y1=.95),
        FrameObject(2, "unknown_enemy", "interactive", role="target", x=.75, y=.63, x0=.68, y0=.56, x1=.82, y1=.70),
    ], screen_hint="combat")
    state = reconcile(None, step=1, host_stats=_combat_stats() | {"enemies": [{"hp": 43, "hp_max": 43}]}, frame=frame)
    alias = state.enemies[0].id
    # This is the exact high-confidence Allowed-target row injected for the reasoner.
    controls = [{"label": alias, "role": "enemy", "confidence": "high"}]
    assert controls[0]["label"] == alias and state.enemies[0].targetable.value

    agent = object.__new__(CollectAgent)
    agent._screen_objects, agent._game_state = store, state
    agent._obs_metrics, agent._host_stats = {"screen": "combat"}, _combat_stats() | {"enemies": [{"hp": 43, "hp_max": 43}]}
    agent._aim = AimGuide.from_marks(Image.new("RGB", (64, 64)), marks)
    agent._step_id, agent.session_id, agent.config = 1, "r", SimpleNamespace(burst_ms=10)
    agent._two_stage = lambda: True
    agent._pending_exec_error = None
    agent._step_illegal_blocked = False
    agent._last_legal_reason = None
    agent._last_tool_events = []
    current_label = agent._resolve_enemy_alias_to_current_label(alias)
    action = action_from_labels(
        agent._aim,
        choice="Strike",
        target_label=current_label,
        thought="focus the unknown enemy sprite; enemy enemy enemy",
        duration_ms=10,
    )
    assert action is not None and action.mark == 1 and action.target == 2
    assert agent._final_combat_dispatch_gate(action, inspect_requested=False) == action

    # A bare icon lacks the anonymous role claim and host/frame corroboration.
    icon_marks = (marks[0], AimMark(id=2, x=.75, y=.63, x0=.68, y0=.56, x1=.82, y1=.70, label=":Icon"))
    store.save_frame(run_id="r", step_id=1, screen="combat", marks=icon_marks)
    agent._aim = AimGuide.from_marks(Image.new("RGB", (64, 64)), icon_marks)
    bad = ActionLog(mark=1, target=2, click_x=.3, click_y=.85, click_x2=.75, click_y2=.63)
    assert is_empty_chord(agent._final_combat_dispatch_gate(bad, inspect_requested=False))
    store.close()


def test_midboard_unknown_inspect_hover_is_safe_and_dispatchable(tmp_path) -> None:
    """Live STS2 sprites sit at y≈0.645; inspect hover must not validate as a card."""
    marks = (
        AimMark(id=1, x=.30, y=.84, x0=.2, y0=.74, x1=.4, y1=.94, label=":Icon"),
        AimMark(id=5, x=.75, y=.645, x0=.69, y0=.58, x1=.81, y1=.71, label="unknown_character_1", role="enemy"),
    )
    store = ScreenObjectStore(tmp_path / "midboard.db")
    store.save_frame(run_id="r", step_id=1, screen="combat", marks=marks)
    agent = object.__new__(CollectAgent)
    agent._screen_objects = store
    agent._obs_metrics = {"screen": "combat"}
    agent._host_stats = _combat_stats()
    agent._pending_exec_error = None
    agent._step_illegal_blocked = False
    agent._last_legal_reason = None
    agent._last_tool_events = []
    agent._step_id = 1
    agent.session_id = "r"
    agent.config = SimpleNamespace(burst_ms=10)
    agent._two_stage = lambda: True
    agent._aim = AimGuide.from_marks(Image.new("RGB", (64, 64)), marks)

    hover = ActionLog(mark=5, click_x=.75, click_y=.645, hover=True, duration_ms=10)
    hover.name = hover.derive_name()
    assert agent._is_safe_inspection_hover(hover) is True
    allowed = agent._final_combat_dispatch_gate(hover, inspect_requested=True, tag="inspect")
    assert allowed == hover
    assert allowed.hover is True
    assert agent._pending_exec_error is None
    assert "card mark" not in str(agent._last_legal_reason or "")

    click = ActionLog(mark=5, click_x=.75, click_y=.645, hover=False, duration_ms=10)
    click.name = click.derive_name()
    forced = agent._final_combat_dispatch_gate(
        click,
        inspect_requested=True,
        tool_inspection_contract={"mark_id": 5},
        tag="inspect",
    )
    assert forced.hover is True
    assert forced.mark == 5
    assert agent._pending_exec_error is None
    store.close()


def test_recheck_handler_skips_identical_frame_second_call(tmp_path) -> None:
    from loop.memory.journal import FactJournal
    from loop.memory.notebook import Notebook

    img = Image.new("RGB", (8, 8))
    agent = object.__new__(CollectAgent)
    agent.journal = FactJournal(path=tmp_path / "j.json", fact_cap=8)
    agent.notebook = Notebook(agent.journal)
    agent._obs_metrics = {"screen": "combat", "energy": 3}
    agent._selected = None
    agent._last_obs_image = img
    agent._step_recheck_count = 0
    agent._step_recheck_image = None
    agent._scene_desc = SimpleNamespace(
        as_text=lambda: "combat",
        legal_actions=[],
        unresolved=["unidentified_enemy"],
    )
    agent._two_stage = lambda: True
    agent._game_state = None
    agent._screen_objects = ScreenObjectStore(tmp_path / "obj.db")
    agent.config = SimpleNamespace(burst_ms=10, max_tool_rounds=3, tool_budget_s=5.0)
    rescans: list[str] = []

    def _fake_rescan(*, focus: str = "") -> float:
        rescans.append(focus)
        return 12.0

    agent._rescan_scene = _fake_rescan
    agent._register_turn_tools(listing=None, host_screen="combat")
    first = json.loads(agent._tool_registry.call("request_recheck", {"region_or_entity": "unidentified_enemy"}))
    second = json.loads(agent._tool_registry.call("request_recheck", {"region_or_entity": "unidentified_enemy"}))
    assert first["ok"] is True
    assert first["latency_ms"] == 12.0
    assert rescans == ["unidentified_enemy"]
    assert second["ok"] is False
    assert second["reason"] == "identical-frame"
    assert second.get("skipped") is True
    assert agent._step_recheck_count == 1
    agent._screen_objects.close()


def test_rebind_target_requires_matching_current_typed_id() -> None:
    """Rebinding is by current typed identity, never screen position."""
    from loop.agent.loop import CollectAgent

    for label in ("unidentified_enemy", "unknown_character_1"):
        before = AimMark(
            id=10, x=0.75, y=0.645, x0=0.69, y0=0.58, x1=0.81, y1=0.71, label=label, object_id="enemy_1", role="enemy",
        )
        current = AimMark(
            id=11, x=0.75, y=0.645, x0=0.69, y0=0.58, x1=0.81, y1=0.71, label=label, object_id="enemy_1", role="enemy",
        )
        agent = object.__new__(CollectAgent)
        agent._aim = AimGuide.from_marks(Image.new("RGB", (64, 64)), [current])
        rebound = agent._rebind_target(before)
        assert rebound is not None, label
        assert rebound.id == 11
        assert rebound.label == label


def test_action_requires_enemy_rejects_untyped_coordinate_click() -> None:
    """Coordinates alone never establish a target action."""
    agent = object.__new__(CollectAgent)
    agent._selected = None
    agent._aim = None
    agent._obs_metrics = {"screen": "combat"}
    agent._host_stats = {"screen": "combat"}

    assert agent._action_requires_enemy(
        ActionLog(click_x=0.75, click_y=0.645)
    ) is False


def test_action_requires_enemy_excludes_hud_and_hand() -> None:
    agent = object.__new__(CollectAgent)
    agent._selected = None
    agent._aim = None
    agent._obs_metrics = {"screen": "combat"}
    agent._host_stats = {"screen": "combat"}

    assert agent._action_requires_enemy(ActionLog(click_x=0.5, click_y=0.05)) is False
    assert agent._action_requires_enemy(ActionLog(click_x=0.5, click_y=0.75)) is False


def test_blocked_source_survives_snapshot_invalidation(tmp_path) -> None:
    from loop.mcp.tools.targeting import invalidate_hand_snapshot
    from loop.memory.screen_objects import ScreenObjectStore

    agent = object.__new__(CollectAgent)
    agent._blocked_sources = {}
    agent._blocked_hand_signature = None
    agent._stale_source_plan = None
    agent._host_stats = {"screen": "combat", "energy": 3}
    agent._obs_metrics = {"screen": "combat", "energy": 3}
    store = ScreenObjectStore(tmp_path / "blocked.db")
    agent._screen_objects = store
    marks = (
        AimMark(id=3, x=0.40, y=0.85, x0=0.30, y0=0.80, x1=0.50, y1=0.90, label="Strike", role="card"),
    )
    store.save_frame(run_id="r", step_id=1, marks=marks, screen="combat")
    plan = {"source": {"object_id": "card_1", "point": {"x": 0.40, "y": 0.85}}}
    card = AimMark(
        3, 0.40, 0.85, 0.30, 0.80, 0.50, 0.90, "Strike", object_id="card_1", role="card",
    )
    agent._record_blocked_source(plan, card)
    invalidate_hand_snapshot(store, reason="source-unconfirmed")
    replayed = {
        "source": {"object_id": "card_1", "point": {"x": 0.40, "y": 0.85}},
        "snapshot": {"snapshot_id": "after-invalidate"},
    }
    assert agent._is_blocked_source_retry(replayed, card) is True
    store.save_frame(
        run_id="r",
        step_id=2,
        screen="combat",
        marks=(
            AimMark(id=3, x=0.55, y=0.85, x0=0.45, y0=0.80, x1=0.65, y1=0.90, label="Strike", role="card"),
        ),
    )
    assert agent._is_blocked_source_retry(replayed, card) is False
    store.close()


def test_blocked_target_dropped_from_snapshot(tmp_path) -> None:
    import pytest
    from loop.agent.decision import ActionIntentError, PlayCardIntent, validate_action_intent

    marks = (
        AimMark(id=1, x=0.31, y=0.95, x0=0.25, y0=0.85, x1=0.38, y1=1.0, label="Strike", role="card"),
        AimMark(id=2, x=0.75, y=0.62, x0=0.68, y0=0.52, x1=0.82, y1=0.72, label="Nibbit", role="enemy"),
        AimMark(id=3, x=0.89, y=0.82, x0=0.82, y0=0.74, x1=0.98, y1=0.90, label="End Turn", role="button"),
    )
    agent = CollectAgent.__new__(CollectAgent)
    agent.fixture = SimpleNamespace(id="sts2")
    agent.session_id = "r"
    agent._step_id = 1
    agent._host_stats = {"screen": "combat", "activity": "combat"}
    agent._obs_metrics = {"screen": "combat"}
    agent._last_obs_image = Image.new("RGB", (64, 48), (20, 20, 20))
    agent._aim = AimGuide.from_marks(agent._last_obs_image, marks)
    agent._screen_objects = ScreenObjectStore(tmp_path / "tnf.db")
    agent._action_surface = None
    agent._game_state = None
    agent._entity_tracker = None
    agent._reason_controls = []
    agent._v2_control_objects = {}
    agent._blocked_sources = {}
    agent._blocked_targets = set()
    agent._blocked_target_host_count = None
    agent._blocked_controls = set()
    agent._blocked_controls_screen = ""
    agent._last_execution_failure = None
    agent._screen_objects.save_frame(run_id="r", step_id=1, marks=marks, screen="combat")
    scene = SimpleNamespace(
        screen="combat", enemies=[], characters=[], controls=[], hand_cards=[], tutorial={},
    )
    try:
        agent._record_blocked_target("enemy_2")
        snapshot = agent._build_turn_snapshot(scene)
        assert all(t.target_id != "enemy_2" for t in snapshot.targets)
        if snapshot.cards:
            with pytest.raises(ActionIntentError, match="unknown-target-id"):
                validate_action_intent(
                    snapshot,
                    PlayCardIntent(card_id=snapshot.cards[0].card_id, target_id="enemy_2"),
                )
        agent._blocked_target_host_count = 1
        agent._host_stats = {
            "screen": "combat",
            "enemies": [{"label": "Nibbit"}, {"label": "Louse"}],
        }
        assert agent._target_is_blocked("enemy_2") is False
    finally:
        agent._screen_objects.close()
