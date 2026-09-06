from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from loop.mcp.registry import ToolRegistry
from loop.model.gateway import VlmGateway
from loop.os.chords import flatten_parsed


# Ollama's common 4096-token context must also fit the system/tool schema and
# requested completion. Keep the per-call user body deliberately conservative.
_MAX_REASON_USER_CHARS = 6000
_MAX_TOOL_RESULT_CHARS = 900


def _bounded_prompt(base: str, results: list[str]) -> str:
    """Keep current observation first; retain newest bounded tool evidence."""
    head = str(base or "")[:_MAX_REASON_USER_CHARS]
    if not results:
        return head
    remaining = max(0, _MAX_REASON_USER_CHARS - len(head) - len("\nTool results:\n"))
    rows: list[str] = []
    for row in reversed(results):
        clipped = str(row)[:_MAX_TOOL_RESULT_CHARS]
        if len(clipped) + 3 > remaining:
            break
        rows.append(clipped)
        remaining -= len(clipped) + 3
    return head + ("\nTool results:\n" + "\n".join(f"- {row}" for row in reversed(rows)) if rows else "")


@dataclass
class ToolRunResult:
    parsed: dict[str, Any]
    text: str
    latency_ms: float
    tool_trace: list[str] = field(default_factory=list)
    # Machine-readable audit trail. Keep tool_trace above for existing logs.
    tool_events: list[dict[str, Any]] = field(default_factory=list)
    rounds: int = 0


def _is_tool_call(parsed: dict[str, Any]) -> bool:
    tool = str(parsed.get("tool") or "").strip()
    return bool(tool)


def _has_action(parsed: dict[str, Any]) -> bool:
    if _is_tool_call(parsed):
        return False
    for key in ("mark", "action", "gx", "gy", "stick_l", "invoke_gsm", "choice"):
        if parsed.get(key) not in (None, "", [], {}):
            return True
    return False


def _tool_args_key(tool_name: str, args: dict[str, Any]) -> str:
    try:
        return f"{tool_name}:{json.dumps(args, sort_keys=True, default=str)}"
    except TypeError:
        return f"{tool_name}:{sorted(args.items())!s}"


class ToolOrchestrator:
    def __init__(
        self,
        registry: ToolRegistry,
        *,
        max_rounds: int = 3,
        budget_s: float = 8.0,
    ) -> None:
        self.registry = registry
        self.max_rounds = max(1, int(max_rounds))
        self.budget_s = float(budget_s)

    def run(
        self,
        gateway: VlmGateway,
        *,
        system: str,
        user: str,
        table_name: str,
        reason_max_tokens: int = 768,
        host_hud_injected: bool = False,
        frames: list[Any] | None = None,
    ) -> ToolRunResult:
        del table_name, host_hud_injected
        t0 = time.perf_counter()
        trace: list[str] = []
        tool_events: list[dict[str, Any]] = []
        tool_results: list[str] = []
        prompt = user
        rounds = 0
        last_call = None
        parsed: dict[str, Any] = {}
        text = ""
        cache: dict[str, str] = {}

        sys_with_tools = system + "\n\n" + self.registry.schema_block()

        while rounds < self.max_rounds and (time.perf_counter() - t0) < self.budget_s:
            rounds += 1
            if tool_results:
                prompt = _bounded_prompt(user, tool_results)
            last_call = gateway.complete_text(
                system=sys_with_tools,
                user=prompt,
                json_mode=True,
                max_tokens=reason_max_tokens,
                frames=frames,
            )
            text = last_call.text
            parsed = flatten_parsed(last_call.parsed or {})
            if _has_action(parsed):
                break
            if not _is_tool_call(parsed):
                if (
                    rounds < self.max_rounds
                    and (time.perf_counter() - t0) < self.budget_s
                ):
                    tool_results.append(
                        "hint: your last reply had no action. Emit JSON with "
                        '"choice" (exact label from Allowed controls) and '
                        '"target" when required. Empty JSON is invalid.'
                    )
                    continue
                break
            tool_name = ToolRegistry.normalize_tool_name(str(parsed.get("tool") or ""))
            args = {k: v for k, v in parsed.items() if k not in {"tool", "thought", "goal", "note"}}
            if "query" in parsed and "query" not in args:
                args["query"] = parsed.get("query")
            if "note" in parsed and "note" not in args:
                args["note"] = parsed.get("note")
            cache_key = _tool_args_key(tool_name, args)
            if cache_key in cache:
                result = cache[cache_key]
                cached = True
            else:
                result = self.registry.call(tool_name, args)
                cache[cache_key] = result
                cached = False
            trace.append(f"{tool_name}({args}) -> {result[:200]}")
            tool_events.append({
                "kind": "mcp_registry",
                "tool": tool_name,
                "args": args,
                "result": result,
                "cached": cached,
                "round": rounds,
            })
            tool_results.append(f"{tool_name}: {result[:_MAX_TOOL_RESULT_CHARS]}")
            # Terminal commands are consumed by the collector's guarded input
            # boundary.  Do not ask the model for a second, free-form action.
            try:
                terminal_payload = json.loads(result)
            except (TypeError, ValueError, json.JSONDecodeError):
                terminal_payload = None
            if isinstance(terminal_payload, dict) and terminal_payload.get("terminal") is True:
                parsed = {"tool": tool_name, "terminal": True}
                break
            if tool_name.startswith("memory_") and rounds < self.max_rounds:
                tool_results.append(
                    "hint: labels from memory are strategy notes only — "
                    "choice/target must still match Allowed controls/targets."
                )

        latency_ms = (time.perf_counter() - t0) * 1000
        if last_call is not None:
            latency_ms = max(latency_ms, last_call.latency_ms)
        return ToolRunResult(
            parsed=parsed,
            text=text,
            latency_ms=latency_ms,
            tool_trace=trace,
            tool_events=tool_events,
            rounds=rounds,
        )

    def validate_targeting(
        self,
        store: Any,
        action: Any,
        *,
        selected: str | None = None,
        entity_targets_ok: bool = False,
    ) -> tuple[bool, str]:
        from loop.mcp.tools.targeting import targeting_validate

        card_mark = getattr(action, "mark", None)
        target_mark = getattr(action, "target", None)
        if card_mark is None:
            return True, "no mark"
        result = targeting_validate(
            store,
            card_mark=int(card_mark) if card_mark is not None else None,
            target_mark=int(target_mark) if target_mark is not None else None,
            entity_targets_ok=entity_targets_ok,
        )
        if selected and target_mark is None and not result.get("ok"):
            from loop.agent.heuristic import HAND_Y

            lookup = getattr(store, "by_id", None)
            card = lookup(int(card_mark)) if callable(lookup) and card_mark is not None else None
            if card is not None and float(card.y) < HAND_Y:
                return True, "selected card awaiting target"
        ok = bool(result.get("ok"))
        return ok, str(result.get("reason") or "")
