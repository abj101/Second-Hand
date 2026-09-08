# Batch 5A: Truthful Per-Turn Telemetry

**Complexity: Medium**  
**Recommended agent:** standard coding tier  
**Prerequisites:** Batches 1–4 complete

## Objective

Make stored rows distinguish fresh model calls, reused data, and explicit MCP tools without changing policy behavior.

## Owned files

- `loop/log/schema.py` and `loop/log/store.py`.
- `loop/agent/loop.py`, only at actual model/tool call sites and serialization.
- Focused logging/collector tests.

Do not change prompts, action policy, OCR, finalization, memory promotion, or exports.

## Required fields

Add these additive fields on `StepLog` in `loop/log/schema.py` (this batch owns that file). Defaults keep old JSONL loadable.

- `perception_source`: `llm1|ocr_cache|scene_cache|forced_rescan|other`.
- `llm1_called_this_turn`: boolean.
- `llm1_reused_from_step`: integer or null.
- `reasoner_called_this_turn`: boolean.
- `explicit_mcp_tools_called`: ordered list of tool names from **registry-call events only**.

Do **not** add `stop_requested` or `flush_completed` here. Batch 5B owns those on `SessionSummary`.

Set fields where calls/reuse actually happen, never by inspecting populated cached payloads during serialization.

## `tool_events` vs explicit MCP (blocker fix)

`StepLog.tool_events` is a mixed log. It already receives `McpOrchestrator` registry results and will also receive collector-generated validation/audit events. `explicit_mcp_tools_called` **must not** equal the full `tool_events` list.

Every `tool_events` dict must carry `kind` (preferred; accept `provenance` as an alias when reading old rows):

| `kind` | Writer | Counts as explicit MCP? |
|---|---|---|
| `mcp_registry` | Only `ToolRegistry.call` / `McpOrchestrator.run` after a real registry invocation (`loop/mcp/orchestrator.py` currently appends `{tool, args, result, cached, round}` with no kind — add `"kind": "mcp_registry"` there) | yes |
| `collector_audit` | Harness validation, schema retry, combat fail-closed, or other collector-generated audit | no |

Equality rule (the only allowed comparison):

```python
explicit_mcp_tools_called == [
    str(event["tool"])
    for event in tool_events
    if event.get("kind") == "mcp_registry" or event.get("provenance") == "mcp_registry"
]
```

Direct action with no registry call → `explicit_mcp_tools_called == []`. `tool_events` may still contain `collector_audit` rows. Events missing `kind`/`provenance` are non-registry and must not appear in `explicit_mcp_tools_called`.

Provide a small helper (e.g. `registry_tool_names(tool_events)`) and use it at serialization and in tests. Do not filter `tool_events` down to MCP-only; keep audits.

## Required tests

- Stable-frame `skip llm1` records `False` plus the reused step when applicable.
- Fresh and forced calls record `True` and no reused step.
- OCR-only perception is distinguishable from cached LLM1 scene reuse.
- `explicit_mcp_tools_called` matches `registry_tool_names(tool_events)` and is empty on direct action.
- A turn with both a registry call and a collector audit has `len(tool_events) > len(explicit_mcp_tools_called)`; the extra event is `kind=collector_audit` and is absent from the explicit list.
- Old JSONL rows still load (`kind` default/missing → not counted as MCP).

## Focused test command

`python -m pytest tests/loop/test_collect.py tests/loop/test_perceive_gate.py tests/loop/test_store.py tests/loop/test_mcp_tools.py -q`

## Acceptance gate

A row can answer exactly which perception/reason/tool calls occurred without consulting console logs. No decision or action output changes in fixtures.

