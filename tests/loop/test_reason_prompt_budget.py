from __future__ import annotations

from types import SimpleNamespace

from loop.agent.loop import (
    _MAX_REASON_JOURNAL_CHARS,
    _MAX_REASON_USER_CHARS,
    _bounded_reason_journal,
    _bounded_reason_user,
    _bounded_visible_text,
)
from loop.agent.prompts import reason_turn_user_prompt
from loop.mcp.orchestrator import ToolOrchestrator, _MAX_TOOL_RESULT_CHARS
from loop.mcp.registry import ToolRegistry


def test_oversized_reason_context_keeps_state_and_controls_within_budget() -> None:
    journal = _bounded_reason_journal([
        "Authoritative state: phase=combat\nLegal state actions (exclusive): play_card(card_1→enemy_1)",
        "old notebook " * 2000,
        "old journal " * 2000,
    ])
    assert len(journal) <= _MAX_REASON_JOURNAL_CHARS
    prompt = _bounded_reason_user(reason_turn_user_prompt(
        state={"screen": "combat", "turn": "ours"},
        hud_stats={"energy": 3},
        controls=[{"label": "Strike", "confidence": "high", "role": "card"}],
        visible_text=_bounded_visible_text(["current observation " * 1000]),
        journal=journal,
    ))
    assert len(prompt) <= _MAX_REASON_USER_CHARS
    assert "Authoritative state" in prompt
    assert "Legal state actions" in prompt
    assert "Allowed controls" in prompt and "Strike" in prompt
    assert "current observation" in prompt


def test_tool_round_prompt_budget_trims_oversized_results() -> None:
    registry = ToolRegistry()
    registry.register("huge", description="test", parameters={}, handler=lambda **_: "x" * 20000)
    gateway = SimpleNamespace(calls=[])

    def complete_text(**kwargs):  # type: ignore[no-untyped-def]
        gateway.calls.append(kwargs["user"])
        if len(gateway.calls) == 1:
            return SimpleNamespace(text='{"tool":"huge"}', parsed={"tool": "huge"}, latency_ms=1.0)
        return SimpleNamespace(text='{"choice":"Strike"}', parsed={"choice": "Strike"}, latency_ms=1.0)

    gateway.complete_text = complete_text
    base = _bounded_reason_user(
        "State: screen=combat turn=ours\nAllowed controls: Strike\n" + ("context " * 3000)
    )
    result = ToolOrchestrator(registry, max_rounds=2, budget_s=2).run(
        gateway, system="system", user=base, table_name="test", reason_max_tokens=128,
    )
    assert result.parsed["choice"] == "Strike"
    assert len(gateway.calls) == 2
    assert all(len(prompt) <= _MAX_REASON_USER_CHARS for prompt in gateway.calls)
    assert "State: screen=combat" in gateway.calls[1]
    assert "Allowed controls: Strike" in gateway.calls[1]
    assert "x" * (_MAX_TOOL_RESULT_CHARS + 1) not in gateway.calls[1]
