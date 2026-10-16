# Continuous game runtime and live view

Status: implemented; live gates passed; full regression run has one independently reproduced baseline golden-fixture failure.
Date: 2026-10-15. Branch at planning: `feat/lean-agent`.
Execution: this chat's primary agent is the orchestrator; all delegated agents use `gpt-6-luna`, reasoning `high`.

## 1. Objective and scope

The normal agent runner opens a visible PyBoy window, advances the game at normal speed throughout observation/model requests/logging, and shows current game pixels in the existing live viewer. Agent thinking must never intentionally pause emulation. Loss of window focus, slow viewer clients, and history inspection must not pause the game.

"Actual frames" means copied pixels from the same emulator framebuffer that the visible window renders. Desktop capture is unnecessary. The game renders every emulated frame; the browser displays the freshest available frame and may skip obsolete display frames to stay live. Identical pixels are valid when the game itself is stationary. Do not manufacture animation or replay a backlog to disguise a stopped emulator.

This packet is a development plan, not authorization to start implementation in this planning turn. Subsequent execution uses the packages below without requiring repeated approval for their ordinary edits and local checks. Validation uses fake/delayed models and the local ROM; no paid model requests, budget changes, production autonomy run, push, or automatic commit is part of this work.

## 2. Rechecked findings

- `agent/env_pyboy.py` selects `window="null"`, speed `0`, and advances only on explicit calls. A real-ROM probe observed frame counts `2 -> 2` across 250 ms idle, then `3` after an explicit tick.
- Existing uncommitted work already adds real framebuffer capture through `frame_sink`, `stream.bin`, and viewer playback. Preserve the work's functionality while replacing the parts that prevent continuous bounded delivery. Do not reset the dirty tree.
- `scripts/run_agent.py` executes `Loop.step()` synchronously. Blocking HTTP calls/retries, CPU work, logging, and checkpoint writes currently share the path responsible for advancing game time.
- `agent/observer.py:wait_settled` advances time itself. `Tracker.wrap` and skills rely on ordered frames during input, including the eight frames after release.
- Installed PyBoy 2.8.1 documents that `tick(n)` renders only its final frame. The live owner must call `tick(1, render=True)` rather than batch ticking. Speed `1` provides normal pacing; do not add a second competing rate limiter.
- SDL events must be pumped by the video-initializing thread; use the process main thread for PyBoy creation, events, render/tick, and shutdown. Do not share the live PyBoy instance across threads. [SDL2 thread requirement](https://wiki.libsdl.org/SDL2/SDL_PumpEvents).
- PyBoy's `autopause` plugin is opt-in. Explicitly leave it disabled. Do not use `no_input=True` as a shortcut: that also bypasses window event handling. Test quit and focus behavior.
- The current append-only stream grows indefinitely, the browser queue is unbounded, and restart detection by file size can miss a same-run restart. Encoding/file writes currently run inside the emulator tick callback.
- The existing LIVE badge follows trace/live.json freshness, so long model calls can incorrectly look idle even if frame production is healthy.
- Baseline focused check: `.venv/Scripts/python.exe -m pytest -q -n 0 tests/agent/test_env.py tests/agent/test_watch_run.py tests/agent/test_observer.py`: **14 passed**.

Corrections to the initial proposal: a visible window alone does not solve pauses; neither does a naive background ticker. Keeping every browser frame in order creates delay, so the live transport needs latest-frame semantics. A continuously running game also makes old observations and checkpoint state differ in time; do not falsely label them frame-identical.

## 3. Architecture and contracts to agree before parallel edits

Use installed dependencies and Python's standard threading/queue primitives. Start with one process: main-thread emulator owner, one agent worker, and one lightweight frame publisher worker. Add a process boundary only if the measured cadence gate demonstrates that Python CPU contention prevents this design from working.

### Emulator owner and agent facade

- Keep the current deterministic headless `PyBoyEnv` behavior available to fixtures, evaluations, and offline tests. Make the normal `run_agent.py` invocation select visible continuous mode. Any explicit headless/offline mode must be recorded in the manifest; never silently fall back when window creation fails.
- Implement live ownership and the agent-facing facade in `agent/env_pyboy.py` so existing RAM/state boundaries continue to pass. Do not expose the live PyBoy instance to worker callers.
- Publish copied RGB snapshots tagged with `{generation, frame_seq, captured_monotonic}`. Generation changes on process/session start and state load; sequence increases for every actual emulated frame within a generation, including unchanged images.
- In live mode, `tick(n)` waits for at least `n` new owner frames; it never calls PyBoy or accelerates the game. Preserve `screenshot() -> Image` exactly; an additive `snapshot()` operation returns the image plus its identity. Add optional live capabilities without breaking existing `Env` callers.
- Serialize commands at frame boundaries. A live `press(key, n)` schedules exactly `n` held frames and eight released frames, with release scheduled by the owner independently of worker progress. Deliver the ordered action images and invoke user/tracker callbacks on the agent worker only. Exceptions, cancellation, and window close release keys and wake waiting callers.
- Bound the command queue and idle history; initial idle-history cap: 120 frames. Only one input command may be active. For live input validate `1 <= n <= 600`; retain at most `n + 8` action frames, separately from idle history, so action evidence cannot be overwritten by the idle ring. Deterministic mode retains its existing behavior. Do not let a full queue block the owner.
- No model call, tracker update, user callback, PNG encoding, or disk/network write runs in the owner loop. Locks cover only short publication/queue operations; no worker can hold a lock across a request or I/O.
- Live agent mode suppresses native keyboard/controller mappings for game input, pause, speed, rewind, and direct state save/load; keep quit, focus/window events, and fullscreen handling. Agent inputs still use the command queue. On installed PyBoy 2.8.1 the SDL backend exposes mutable key/controller mapping tables: apply a narrow, reversible live-session filter there if no supported configuration covers this, restore the originals on close, and test the override against this installed version. Do not set `no_input=True` or bypass event pumping. Unexpected backend incompatibility must fail visibly rather than enable uncontrolled state loads.

The additive runtime seam is fixed as follows; preserve existing deterministic method signatures and returns:

```python
# Records are read-only by convention; images/buffers must be owned copies.
FrameSnapshot(generation: str, frame_seq: int, captured_monotonic: float, image: Image)
FrameBatch(frames: tuple[FrameSnapshot, ...], latest: FrameSnapshot, dropped: bool)
RamSnapshot(frame: FrameSnapshot, values: dict[int, int])
SavedState(frame: FrameSnapshot, state: bytes)

# Construct and run on the main thread; env is the agent worker's facade.
LivePyBoyHost(rom=None, *, state=None, name="pokemon_red")
host.env  # existing Env methods plus the additive operations below
host.run(publish) -> None  # blocking owner pump; publish is ONLY a bounded mailbox handoff
host.stop() -> None        # thread-safe signal; idempotent; no caller-side PyBoy operation

env.snapshot() -> FrameSnapshot
env.wait_frame(after: FrameSnapshot, *, timeout: float) -> FrameSnapshot
env.frames_after(after: FrameSnapshot) -> FrameBatch
env.read_ram(addresses: tuple[int, ...]) -> RamSnapshot  # evaluator only
env.save_snapshot() -> SavedState                      # owner-boundary capture

FramePublisher(run: Path)
publisher.publish(frame: FrameSnapshot) -> None  # constant-time slot replacement
publisher.close(*, timeout: float = 1.0) -> None
```

Generation is a fresh UUID string per live session/load. `wait_frame` returns only a newer frame; on generation change it raises `FrameGenerationChanged`, and on deadline it raises `TimeoutError`. `frames_after` reports `dropped=True` on a generation change or missing retained history. Owner shutdown raises `LiveEnvClosed` to every waiter/command; owner failure raises `LiveEnvFailed` with the underlying reason. Queue saturation raises an explicit backpressure error to the caller, never blocks the owner. Runtime agent defines these live types/exceptions in `agent/env_pyboy.py`; viewer uses the snapshot fields without importing PyBoy. Keep `Env.screenshot() -> Image` and `Env.press(...) -> None`; action-frame results are consumed internally by the facade and delivered through the existing callback. `read_ram`/`save_snapshot` stay evaluator/runner-only.

### Observation, tracking, and action age

- Reuse `wait_settled`'s existing quiet/blank/timeout policy. Live mode consumes numbered published frames; count distinct source frames, not repeated polls of one image. Bound the wait by source-frame count and a monotonic deadline; shutdown/owner failure must interrupt it.
- Keep World/Tracker/Loop mutations on the agent worker. Feed available idle frames in order before using track positions. If history overflowed, clear/reseed tracks; never infer scroll or object motion across an unobserved gap. Preserve every action callback and its ordering, without double-updating frames already consumed.
- Carry optional generation/frame sequence/age metadata through live observations and trace rows. Existing offline/golden output shapes remain unchanged unless a test explicitly covers new optional metadata.
- At the input boundary, use the existing blank/scene-cut signal to reject an obviously obsolete observation without pressing buttons. Record the rejection as a bounded observe-only result, retain the existing fallback/budget discipline, and re-observe on the next outer step. Do not reject on exact hash inequality: ambient animation would cause starvation.
- This is a coarse scene-change guard, not semantic target validation. A moving target in an otherwise similar scene can still invalidate an action; record observation age and treat general semantic revalidation as separate follow-up work. Add no extra model calls to this packet.

### Truth, checkpoint, lifecycle

- Truth remains evaluator-only. Capture its required RAM as one owner-boundary batch, and attach that snapshot's generation/frame sequence. Never implement a truth read as hundreds of RPCs to a moving emulator or feed RAM to the agent policy.
- Save emulator state to memory at one frame boundary; return the saved image/frame metadata with the bytes. Write checkpoint files on the agent worker. Do not stop ticking while copying memory directories or writing files.
- Record both the emulator `state_frame_seq` and the agent's `observation_frame_seq`; they may differ. Snapshot at an action-free boundary, with no key held. Do not promise an old perception and current emulator state are identical.
- On live resume, load before starting normal input, create a new generation, and invalidate transient perception/track/pending assumptions that depend on old pixels. Preserve durable world/notebook state and existing truncation/provenance rules. Define safe reconciliation of a legacy pending action in a test before enabling it; never replay already-executed input.
- Model errors, call caps, Ctrl+C, owner exceptions, and window close must end cleanly, propagate the reason, and keep existing checkpoint/error behavior where possible. Window close must not wait for a full network timeout. Use a daemon agent worker and a bounded shutdown join; a synchronous in-flight HTTP request may finish later, but must not keep the process alive. Add optional cancellation support to `Ollama`: check before/after requests and before cache/log side effects, replace retry sleeps with interruptible event waits, and raise `ModelCancelled` on cancellation. Default behavior without a cancellation event remains unchanged. The runner sets cancellation before closing the owner/publisher and checks it before subsequent action or run writes. This does not claim to abort an already-issued HTTP request at the remote server. Never read mutable Loop/World state concurrently to force a final checkpoint: if no safe handoff is possible, retain the last completed checkpoint and report that the interrupted step was not checkpointed. No ROM-adjacent implicit save files (`stop(save=False)`).

### Bounded live transport

- Keep `watch_run.py` as the separate read-only viewer. Replace append-only live playback with a bounded latest-frame artifact; retain BEFORE/AFTER/MOTION history behavior.
- Freeze this concrete interface before parallel work: one `live-frame.bin` per run, atomically replaced by the publisher; record format is `[u32 little-endian JSON-header length][UTF-8 JSON header][PNG bytes]`. Header contains run identity, generation, frame_seq, `captured_monotonic` in seconds, dimensions, and publication heartbeat. A reader sees one coherent metadata/image pair. Both Python processes use the same host's monotonic clock; compute source age server-side and verify that clock assumption in the smoke test.
- Limit JSON headers to 4 KiB and PNG payloads to 256 KiB; expected native dimensions are 160 by 144. Reject truncated/oversized/malformed records or metadata/image dimension mismatch before decode/send. A file is at most `4 + 4096 + 262144` bytes. Use one fixed same-directory temporary path, clean it on orderly close, and overwrite it on retry so contention cannot accumulate files.
- Add `/live-frame` returning the PNG with `X-Run`, generation, sequence, and `X-Frame-Age-Ms` headers and `Cache-Control: no-store`. Source age is calculated from capture time, never publication time. A missing/unavailable source is explicit; never label a trace screenshot as a live frame. Preserve the previous good publication when Windows temporarily denies replace, retry off-owner, and report stale age rather than blocking gameplay.
- Publisher input is a replaceable one-frame slot; it may skip obsolete frames. Encode off-owner, reuse encoded bytes for unchanged pixels, and refresh source metadata only on a newer owner frame. A publisher heartbeat cannot refresh a stalled source's age or sequence. Disk storage is one current artifact plus one bounded temporary artifact, not an accumulating recording. Existing old stream.bin files may remain as historical files but new live sessions must not append them.
- Browser fetch/decode is single-flight. Paint the newest response immediately, retain at most one pending decoded image, and close superseded ImageBitmaps. No nominal-game-speed replay queue. Use a local request epoch to abort/reset on run/generation selection changes, reconnect, or return from a hidden tab. After decode, paint only when the epoch and expected run/generation still match; close rejected bitmaps too.
- Follow mode discovers runs from actual source-frame progress as well as trace files, including startup before the first completed step; pinned runs stay pinned. Source capture freshness and advancing frame_seq determine frame liveness; publisher health and model/agent activity are separate signals. A heartbeat without fresh source frames must never show LIVE.

The user explicitly supersedes `contracts.md`'s old headless/unlimited operational note for normal live runs. Keep that frozen file unchanged; additive facade/metadata changes and the live timing interpretation are documented here. If an incompatible public signature becomes necessary, the orchestrator must first determine whether an additive implementation can avoid it and record the remaining issue.

## 4. Ownership and run order

The orchestrator reads CLAUDE.md, this packet, the current dirty diff, and relevant contracts. Before dispatching editing workers, confirm the explicit owner/facade/publication seam above against the current tree and put it in every worker prompt. Resolve any required additive adjustment here first; workers must not independently invent incompatible signatures. The live runner defaults to continuous visible mode; add `--headless` only as an explicit deterministic/offline runner option and record it in manifest modes without applying it to older unrelated evaluation entrypoints. Every worker applies ponytail full, CodeGraph before indexed code reads/edits, and Headroom for large output. Workers must not undo others' changes or edit files outside their package. Preserve unrelated `.claude/`, audit, and RCR plan files.

1. **Runtime agent — Luna high.** Owns `agent/env_pyboy.py`, `tests/agent/test_env.py`, and new `tests/agent/test_realtime_env.py`. Implements the main-thread owner/facade, snapshots, scheduled inputs, bounded retention, state/RAM capture, cancellation, and deterministic-mode compatibility.
2. **Viewer agent — Luna high, parallel with runtime.** Owns `scripts/watch_run.py` and `tests/agent/test_watch_run.py`. Implements off-owner publisher and `publish(snapshot)`/`close()` lifecycle, bounded artifact and endpoint, browser paint logic, run discovery, and restart/liveness handling. The publisher implementation stays in the existing viewer module unless separation proves necessary.
3. **Integration agent — Luna high, after runtime interfaces land.** Owns `agent/observer.py`, `agent/track.py`, `agent/loop.py`, and their directly associated tests (`test_observer.py`, `test_track.py`, `test_loop.py`). Owns `agent/env.py` only for additive protocol support. Integrates live settling, frame metadata, worker-only tracking/gap reset, and coarse stale-observation handling. Changes to `agent/world.py` require orchestrator coordination first; avoid them if the existing tracker seam is sufficient.
4. **Orchestrator integration, after runtime + viewer interfaces land.** Owns `scripts/run_agent.py`, `scripts/truth_pokered.py`, `agent/model.py`, new `scripts/check_live_view.py`, `tests/agent/test_model.py`, `tests/agent/test_run_provenance.py`, `tests/agent/test_run_agent_budget.py`, and this plan. Wires main-thread ownership with agent/publisher workers, coherent truth/checkpoints, optional model cancellation, mode/provenance/lifecycle, and the offline smoke harness. This can overlap the integration agent only with the frozen interfaces and disjoint files.
5. **Independent review — reuse Luna high workers.** Runtime worker reviews integration; integration worker reviews runtime; viewer worker reviews transport/browser and lifecycle. Reviews are initially read-only. Orchestrator assigns any fixes to the owning worker, reruns affected checks, and resolves findings before completion.

At most three subagents run concurrently. The orchestrator owns cross-package decisions and personally runs acceptance gates. No subagent creates a separate user chat, changes model/call budgets, or launches a funded run.

## 5. Acceptance checks

Package commands (run by the orchestrator after each corresponding handback):

```powershell
.venv/Scripts/python.exe -m pytest -q -n 0 tests/agent/test_env.py tests/agent/test_realtime_env.py
.venv/Scripts/python.exe -m pytest -q -n 0 tests/agent/test_watch_run.py
.venv/Scripts/python.exe -m pytest -q -n 0 tests/agent/test_observer.py tests/agent/test_track.py tests/agent/test_loop.py tests/agent/test_skills.py tests/agent/test_type_text.py
.venv/Scripts/python.exe -m pytest -q -n 0 tests/agent/test_model.py tests/agent/test_run_provenance.py tests/agent/test_run_agent_budget.py tests/agent/test_boundaries.py
```

Required meaningful tests:

- Fake-owner tests prove all PyBoy operations use its owning thread, input release happens at exact frame counts, callback ordering is retained, and a blocked/raising callback cannot stall ticks or leave keys held.
- Live observer sees distinct frame IDs, times out on animation, returns latest nonblank data, and wakes on stop. Tracking overflow invalidates history, input frames remain intact, and snapshots are copied rather than live views.
- Delayed fake-model integration proves actual `Loop.step()` blocking does not stop frame production; exercise a retry-like delay as well as an ordinary response.
- Stop during a fake in-flight request and during retry backoff: native owner closes within the bounded join, waiters wake, and the late model return triggers no input, new request, cache/log write, or run mutation. Test that filtered native pause/speed/load controls cannot bypass the live command/generation rules and that quit still works.
- Frame/RAM batch and serialized emulator state have matching source metadata; checkpoint file writes deliberately delayed off-owner do not stop frames. Resume clears stale transient data, preserves durable state, and never repeats a pending input.
- Transport tests cover bounded storage, read/replace contention, failure recovery, slow client, same-run restart even when new output is larger, new-run follow, startup before trace, pinned selection, and late responses from the previous generation/run. Assert the 0/1 pending-bitmap cap and closure of rejected/superseded bitmaps, fixed artifact byte ceiling under sustained output, malformed-record rejection, and stale-source behavior despite publisher heartbeat.
- Existing deterministic tests and protected golden/checkpoint fixtures remain valid. Do not regenerate goldens to conceal a regression.

Final commands:

```powershell
.venv/Scripts/python.exe -m pytest -q tests/agent
.venv/Scripts/python.exe scripts/check_live_view.py --seconds 15 --model-delay 5 --output .tmp/live-view-check
git diff --check
```

`check_live_view.py` is new, local-only validation tooling: no Ollama/network model calls; isolated temporary run directory; local ROM; visible window. Its main-thread owner uses the same production wiring. It writes a compact JSON report with source/published/display sequence samples, intervals, frame age, generations, input schedule, and fixed buffer/storage limits. Exit nonzero for failed measurable gates. Browser-only facts must be separately verified, not inferred from unit tests.

On the development machine after warmup, target 54–66 actual emulated frames/second across the five-second fake-model wait and no source-frame interval above 250 ms. These are measurable acceptance targets, not an absolute OS scheduling guarantee. If they fail, report the intervals and bottleneck; fix the owner path before loosening targets or adding process IPC. Record state-serialization latency separately.

Use the real local browser through available browser tools to verify the production viewer: visible animation agrees with the emulator, reported live frame age stays below 250 ms in the normal foreground case, slow/throttled clients recover directly to current frames, and historical inspection does not stop the emulator. Confirm focus loss/minimize/restore and native close on Windows. Verify the configured pause/speed hotkeys cannot accidentally violate agent-controlled continuous mode while still pumping close events. Returning from a hidden tab must show fresh frames without replaying accumulated history.

## 6. Completion and handback

The orchestrator marks this packet in progress only when execution starts. Record package results and final measured cadence/frame-age/buffer limits here, together with the runtime mode, command output summary, and any unresolved limitations. No gate is passed from code inspection alone.

Success means visible continuous gameplay through delayed model work, actual current pixels in LIVE, bounded resources, exact input timing, safe checkpoint/resume, correct lifecycle, and passing regression checks. If no browser/native-window verification is available, explicitly leave those gates unverified rather than claim completion.

### Result

- Planning review: three independent `gpt-6-luna` agents at high reasoning reviewed runtime/SDL constraints, observer/tracker/checkpoint integration, and viewer transport. No implementation edits were made by them.
- Second review: runtime and viewer reviewers checked the written packet. Their required corrections are incorporated: explicit additive interfaces, cancellation behavior for in-flight HTTP, native hotkey filtering, generation/run response guards, source-based freshness, hard artifact limits, and atomic checkpoint capture with separate observation/state frame identities.
- Baseline: 14 focused tests passed; real emulator idle probe confirms explicit-tick freezing.
- Implementation: complete with three Luna high implementation agents and independent cross-review. Normal runner execution now uses the visible continuous owner; `--headless` is the explicit deterministic mode. The existing viewer on port 8765 was restarted with the new player.
- Package gates personally run by the orchestrator: runtime **15 passed** (including visible real-ROM SDL test); viewer/model/runner/boundary checks **56 passed**. The integration worker's migrated deterministic runner/live regression selection passed **16 tests**, then the orchestrator's whole-suite run covered the integration package.
- Whole-suite command: `.venv/Scripts/python.exe -m pytest -q -n 4 tests/agent`: **649 passed, 1 failed** in 125.99 seconds. Failure: `tests/agent/test_world_golden.py::test_act_eight_steps`; actual output adds `memory/mints.jsonl`, while the other eight artifacts match. No golden was regenerated.
- Independent baseline proof: a Luna high reviewer extracted `git archive HEAD agent scripts tests pyproject.toml` to `.tmp/live-view-baseline-802fab27-8d89-4e72-8f7e-c0738deab3ff`, copied the existing protected golden fixtures there, verified imports resolve to the archived source, and reproduced exactly the same extra mint ledger with the single golden test (**1 failed**, 14.70 seconds). This is an existing fixture mismatch, not a live-runtime regression. The full-suite gate therefore retains a documented baseline exception.
- Final finished-code smoke: `.venv/Scripts/python.exe scripts/check_live_view.py --seconds 30 --model-delay 5 --output .tmp/live-view-final-check`: **all nine measured checks passed**. Five-second fake-model wait: **59.997 FPS**; maximum source-frame interval after warmup: **17.8 ms**; active-source HTTP frame age median/max: **14/43 ms**. Live artifact: **4,766 bytes**, bounded by **266,244 bytes**. START held for exactly two frames plus eight released callback frames. Checkpoint capture/write completed in **31.9 ms**. Native window shown/minimized/restored/quit verified; source kept advancing. Report: `.tmp/live-view-final-check/report.json`.
- An earlier 120-second native/browser smoke also passed every measured check: **60.004 FPS** during model delay, source interval maximum **27.259 ms**, active HTTP age maximum **42 ms**, fixed artifact **4,765 bytes**. Post-close stale frame ages are reported separately and excluded from active-source delivery measurements.
- Real browser verification through CUA: actual game pixels displayed; no console errors observed. Finished player showed `LIVE / FRAME 898 / 9 MS`; while BEFORE history was selected its hidden live canvas advanced to frame **1216**; FOLLOW LIVE returned directly to frame **1568 / 22 MS**. Source continued through browser focus changes. Executable Node tests additionally cover slow decode, single-flight limits, rejection/closure of late old-generation responses, and immediate recovery.
- `git diff --check` passed. No cloud model requests, budget increases, commits, pushes, or protected-fixture edits.
- Implementation adjustment: use SDL's supported reversible event filter instead of mutating PyBoy's internal key maps. A discovered null-filter restoration bug was corrected and covered by native-close/filter tests. Tracker snapshot/wait identity races found by independent review were fixed and regression tested. The final full suite used four workers to keep resource usage predictable.

## Execution prompt

> Execute `docs/plans/continuous-live-view.md` in this chat. Act as the orchestrator and delegate the named packages using `gpt-6-luna` with high reasoning. Preserve the existing dirty work, freeze interfaces before parallel edits, and personally verify each package and final gate. Use delayed fake models for local validation; do not spend cloud calls or change budgets. Complete the implementation and report measured results and any unverified gates.
