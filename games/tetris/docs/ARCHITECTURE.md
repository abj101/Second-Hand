# Architecture

This document describes the modular design of the Tetris app: how state, rendering, and input are separated and how they interact.

## Overview

The app is split into four main concerns:

1. **Game logic** (`game.ts`) — State shape, reducer, rules (collision, line clear, scoring). No DOM, no canvas, no key handling.
2. **Renderer** (`renderer.ts`) — Draws the board, piece, ghost, next/hold preview from `GameState`. No game rules.
3. **Input** (`input.ts`) — Maps keyboard events to `GameAction`s. No direct state mutation.
4. **Main** (`main.ts`) — Entry point: holds state, runs game loop, dispatches actions to the reducer, calls renderer and updates DOM (score, level, status).
5. **Audio** (`audio.ts`) — Optional sound effects and background music via Web Audio API (line clear, hard drop; looping BGM when playing). No game state; invoked from main after reducer updates. BGM can be toggled off by the user via a Music button; main holds the preference and syncs BGM to game state and that preference.

Data flows in one direction: **Input → Main (dispatch) → Game State → Renderer**. Main triggers audio as a side effect when relevant actions occur (e.g. line clear, hard drop) and syncs BGM to status (playing/paused/idle) and user music preference.

## Modules

### `constants.ts`

Single source of truth for:

- Grid dimensions (`COLS`, `ROWS`)
- Spawn position (`SPAWN_COL`, `SPAWN_ROW`)
- Level/speed (`LINES_PER_LEVEL`, `BASE_FALL_MS`, `MIN_FALL_MS`, `LEVEL_SPEED_FACTOR`)
- Scoring (`LINE_SCORES`)

Used by `game.ts` and `renderer.ts`. Changing these values is the only place to tune difficulty and layout.

### `tetrominoes.ts`

- **Data:** Shape matrices (4 rotations per type), colors (CSS hex).
- **Helpers:** `getShape(type, rotation)`, `getColor(type)`, `randomType()`.

No game state; pure data and pure functions. Easy to unit test and to swap (e.g. different color sets).

### `game.ts`

- **State:** `GameState` (board, piece, nextPiece, holdPiece, score, level, linesCleared, status, etc.).
- **Reducer:** `gameReducer(state, action) => newState`. Handles all actions: move, rotate, soft/hard drop, hold, tick, start, pause, restart.
- **Helpers:** `createInitialState()`, `createEmptyBoard()`, `fits()`, `getFallDelayMs(level)`.

`fits()` and the reducer are the only places that know collision and line-clear rules. The reducer is pure (no I/O), so it can be tested without a DOM or canvas.

### `renderer.ts`

- **Input:** `GameState` + `RendererConfig` (canvas, cellSize, padding).
- **Output:** Side effect only — draws on the provided canvas.

Rendering is stateless: given a state, it draws it. It uses `fits()` only to compute the ghost piece row; it does not mutate state or interpret actions.

### `input.ts`

- **Input:** Keyboard events.
- **Output:** `GameAction` (or null if key is not bound).

`attachKeyboard(handler)` registers a keydown listener and calls `handler.onAction(action)`. The handler (in `main.ts`) is responsible for dispatching to the reducer. Input does not see or mutate game state.

### `audio.ts`

- **Input:** Invoked with no arguments (line clear) or line count / context; BGM is started/stopped/volume-set by main.
- **Output:** Side effect only — plays short synthesized tones (line clear, hard drop) and optional looping background music via Web Audio API. No external files; all synthesized. Fails silently if context is not allowed (e.g. autoplay policy).
- **BGM:** `startBGM()`, `stopBGM()`, `setBGMVolume(volume)`. Main calls these based on game status and user music preference (Music toggle). BGM plays only when status is `playing` and user has not turned music off; muted when paused.
- Used by `main.ts` after dispatching actions that cause line clear or hard drop, and each frame to sync BGM to state and preference.

### `main.ts`

- Creates initial state; holds UI preference for BGM (music on/off).
- Sets up canvas size and gets 2D context.
- Registers keyboard handler; on action, optionally maps (e.g. P → pause/resume, Enter on game over → restart + start) then dispatches to reducer.
- Registers Music toggle button; toggles BGM preference and syncs audio.
- Game loop: `requestAnimationFrame`; when status is `playing`, emits `tick` at intervals based on `getFallDelayMs(state.level)`.
- After each frame, calls `syncBGM()` (so BGM follows status and user preference), then `render()` and updates DOM elements (score, level, lines, status text).

## Extensibility

- **New input source (e.g. touch):** Implement another module that emits `GameAction` and call the same `dispatch` in main.
- **Different renderer (e.g. WebGL):** Replace `renderer.ts` with a module that takes `(ctx, state, config)` or equivalent; keep the same `GameState` shape.
- **New game rules (e.g. wall kicks):** Change only `game.ts` (e.g. in rotate actions); renderer and input stay unchanged.
- **Theming:** CSS variables in `styles/main.css`; optionally pass a theme key into renderer for block colors.

## Testing

- **Game logic:** Unit test `gameReducer` with various states and actions; assert board, score, level, status.
- **Tetrominoes:** Unit test `getShape`, `getColor`, `randomType` (or mock `Math.random`).
- **Renderer:** Optional: headless canvas or snapshot tests given a fixed `GameState`.
- **Input:** Optional: simulate `KeyboardEvent` and assert `keyToGameAction` returns the expected action.

No end-to-end or DOM tests are required for core correctness; the reducer is the single source of truth.
