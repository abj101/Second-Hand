# ADR 0001: One adaptive perception kernel

Status: accepted
Date: 2026-09-20

## Context

The repository previously contained a large domain-specific agent beside a smaller adaptive kernel. That split kept two authorities, allowed fixed mechanics to leak into runtime behavior, and made successful legacy tests look like evidence for the new architecture.

## Decision

The adaptive kernel is the only product path. A fresh workspace begins with `empty_schema()`. Perception emits a neutral evidence vocabulary; orchestration proposes schema diffs; promotion requires validation and probing; runtime projection alone creates the state snapshot and legal action surface.

Schema revisions are immutable and append-only. Live capture retains source and integrity metadata. Shadow mode cannot emit input. Armed execution fails closed unless a selector resolves uniquely and the action has a verified expected effect.

The legacy agent, fixtures, rewards, training pipeline, alternate command surface, and domain documentation are removed. History remains available in version control; none remains importable by the runtime.

## Live evidence at cutover

A 30-frame live shadow run proved window capture, OCR, ledger persistence, numeric-field proposal, schema compilation, and restart reuse. The restart produced zero new revisions and an unchanged HEAD. It did not meet action qualification: tracking was below 0.95, field continuity was unstable, and no causal action existed. Consequently no live click was attempted and armed operation remains fail-closed.

## Consequences

- There is one architecture and one CLI: `python -m loop collect`.
- Repository purity is checked across all retained Python code.
- Synthetic testing proves the causal pipeline but is not a substitute for live qualification.
- The next milestone is a generic, explicitly armed experiment controller—not another application adapter.

## Clarification — 2026-09-22

The [north-star game-brief contract](../perception-reaction-orchestration-north-star.md#game-brief-purpose-and-authority) permits versioned, game-specific JSON prose as advisory orchestration context. The kernel still starts from an empty schema and uses pixels and receipts for state and action evidence. A brief supplies background and a fallback objective; it is neither a domain-specific runtime nor a source of executable bindings, selectors, or verified effects. Game names and mechanics belong in data, not conditional branches in the kernel.
