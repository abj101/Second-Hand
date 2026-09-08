# Orchestrator Plan: STS2 Combat Reliability

**Overall complexity: High**  
**Execution model: sequential subagents, one batch at a time**  
**Target outcome:** two consecutive clean `no_skill` combat wins, immediate Loot recognition, trustworthy memory, and training-safe traces.

## Run evidence to preserve

Baseline run: `runs/20260906T021305Z_sts2_collect`.

Observed baseline:

- 29 persisted turns; combat won and Loot displayed.
- 19 `play_card`, 7 `click_control`, 2 `end_turn`, 1 empty intent.
- 15 turns without a reported error.
- 8 `source-unconfirmed` errors, 2 stale `target-not-found`, 2 `effect-unverified`, 1 `inventory-inconsistent`.
- Four ineffective `Next` clicks across two repeated pairs.
- All perception schemas were valid on first pass.
- No explicit MCP tool call was made; `tool_events` stayed empty.
- Notebook memory was injected and used, but explicit `memory.ids/snippets` stayed empty.
- The journal accumulated contradictory role/label bindings.

The orchestrator must copy these numbers into the final comparison and must not overwrite or delete the baseline run.

## Plan blocker resolutions (locked before execution)

Do not reopen these. Implementing agents must follow the locked contract in the assigned plan file.

1. **Batch 2 — typed admission.** `allowed_controls_for_turn` migrates off `scene_labels`/`detector_labels: Sequence[str]`. It takes typed inventory/detector **rows**, admits only `_GENERIC_CONTROL_ROLES`, and returns typed rows. Prompt labels are derived after admission. `allowed_sets_from_controls` uses the same allow-list; the current else-branch that dumps non-target roles into controls is forbidden.
2. **Batch 5A — MCP vs audit.** `explicit_mcp_tools_called` equals tool names from `tool_events` where `kind`/`provenance` is `mcp_registry`. Collector-generated validation/audit events use `kind=collector_audit` and must not appear in the explicit list. Never require `explicit_mcp_tools_called == tool_events`.
3. **Batch 5B — session fields.** `stop_requested` and `flush_completed` are additive `SessionSummary` fields owned by Batch 5B, persisted in `summary.json`. Not on `StepLog`. Not on `EpisodeLog`. Batch 5A does not add them.

## Agent assignment policy

Match model/effort to the plan’s stated complexity. Do not use high or extra-high thinking on medium/low batches.

| Plan complexity | Agent | Model |
|---|---|---|
| Low (Batch 6) | economical | `composer-2.5-fast` |
| Medium (2, 3, 5A, 5B) | standard | `composer-2.5` |
| High (1, 4, 5C) | strong | `composer-2.5` first; escalate only if that batch’s gate fails |

Never pick `cursor-grok-4.6-xhigh` or `claude-fable-5-1-thinking-high` because a batch is “important.” Escalate only after a high-complexity batch fails its focused or cumulative gate.

- Give each agent exactly one numbered plan file.
- Require the agent to read this file, its assigned plan, `system.md`, and only the directly relevant source/tests.
- Lower-tier agents must not redesign architecture, change model prompts broadly, or edit unrelated subsystems.
- Do not run batches concurrently. Several batches legitimately touch `loop/agent/loop.py`; parallel work would create semantic conflicts even if Git merges cleanly.
- Start each batch from the commit produced by the preceding batch.
- Require one focused commit per batch. The orchestrator reviews the diff and test evidence before continuing.
- If a batch gate fails, return the same batch to its agent with the exact failure. Do not let a later batch compensate for it.

## Mandatory workflow for every batch

1. Record `git status --short` and preserve unrelated user changes.
2. Read the assigned plan completely.
3. Inspect current behavior and existing tests before editing.
4. Add a failing regression test for each stated bug.
5. Implement the smallest fix that satisfies the plan.
6. Run the focused tests listed in the plan.
7. Run the focused command in the batch, then run the cumulative gate. The cumulative gate is the complete loop suite:

   `python -m pytest tests/loop -q`

8. Review `git diff --check` and the final diff.
9. Commit only files belonging to that batch.
10. Report changed files, tests, remaining risks, and commit hash.

## Batch order and promotion gates

### Batch 1 — Card identity and reflow

Assign a strong agent. Continue only when a failed card selection invalidates/rebuilds the hand inventory, invalidates the old ordinal intent, requires a new decision, and prevents repeated stale-coordinate clicks. Never silently reinterpret an old `card_N` after hand reflow.

### Batch 2 — Action surface and control suppression

Assign a standard agent. Continue only when controls are current-frame typed objects, a twice-blocked control is suppressed for the unchanged surface, and a screen change clears suppression.

### Batch 3 — Combat victory transition

Assign a standard agent. Continue only when combat-to-Loot clears combat state before the next decision and emits a durable `combat_won` transition.

### Batch 4 — Memory truth and export safety

Assign a strong agent. Continue only when contradictory cross-role bindings are rejected, raw OCR cannot become a lesson, and uncertain transactions are excluded from SFT/DPO.

### Batch 5A — Telemetry

Assign a standard agent. Continue only when logs distinguish fresh/cached perception and `explicit_mcp_tools_called` matches registry-call (`kind=mcp_registry`) events only, not collector audits.

### Batch 5B — Graceful finalization

Assign a standard agent. Continue only when stop handling flushes the final row, journal, grounded memory, and database without force termination, and `summary.json` records `stop_requested` / `flush_completed` on `SessionSummary`.

### Batch 5C — OCR cache and performance

Assign a strong agent. Continue only when existing RapidOCR singleton behavior is verified, cache/region changes preserve label fixtures, and timing evidence demonstrates the result.

### Batch 6 — Validation runs

Assign an economical agent with permission to operate the game UI. It makes no product-code changes unless a test fixture alone is required for analysis. It runs repeated baselines and produces a comparison report.

## Stop/rollback rules

Stop the sequence if any batch:

- lowers the focused test pass count;
- introduces a heuristic that rewrites a valid model intent without a safety reason;
- makes mark IDs persistent across frames;
- learns from `source-unconfirmed`, `target-not-found`, `effect-unverified`, or `inventory-inconsistent` rows;
- requires enabling skills to make `no_skill` pass;
- broadens legal controls using journal or prior-frame labels.

Revert only the offending batch commit; never reset the entire working tree.

## Final definition of done

- Two consecutive `no_skill` combat wins from comparable starting states.
- Zero stale target dispatches.
- Zero repeated clicks on an unchanged blocked control.
- At most one `source-unconfirmed` event per combat, with no immediate same-coordinate retry.
- Loot recognized on the first stable post-victory observation.
- No combat entity/card/control survives into the reward inventory.
- No contradictory stable identity roles in `journal.json`.
- No malformed raw-OCR lesson in the notebook.
- All uncertain action rows are absent from SFT and DPO exports.
- Graceful stop produces a complete last row and `memory_grounded.json`.
- Baseline-versus-fixed metrics are recorded in the Batch 6 report.

Only after this definition is met should the orchestrator authorize a separate `skill_on` A/B run.
