# ADR 0002: Benchmark candidate executor

Status: accepted
Date: 2026-09-20

## Context

ADR 0001 defines the adaptive perception kernel and fail-closed desktop execution via `Session.act()`. Benchmark and synthetic evaluation need a reaction loop that steps a generic environment by opaque action identifiers without widening the verified action surface or touching live input paths.

## Decision

Add a benchmark-only path under `loop/adaptive/` that implements:

- An `Environment` protocol with `reset` / `step(action_id)` and evaluation-only `privileged_info` on step results.
- Candidate catalogs, directives, masks, and policies that never embed coordinates, keys, or desktop primitives.
- A dispatch executor that validates snapshot age, directive TTL, and catalog revision before accepting a decision; rejected dispatches trigger watchdog release, not unbounded retry.
- `StateSnapshotV2` as a sidecar perception snapshot; promotion into verified `StateSnapshot` / `ActionSurface` remains a separate, explicit step with empty `action_ids` until arming.

Desktop `Session.act()` remains fail-closed and is not imported or invoked by the benchmark adapter, controller, or executor.

## Relationship to ADR 0001

This ADR extends the kernel with an optional evaluation loop. It does not replace or relax ADR 0001 live capture, promotion, or armed execution rules.

## Consequences

- Synthetic episodes can run in CI without Chromium, CUDA, or game fixtures in `loop/adaptive/`.
- Telemetry and snapshots must pass leak guards; `privileged_info` never enters policy or JSONL snapshot payloads.
- Future game adapters live outside `loop/adaptive/` and implement `Environment` at the integration boundary.
