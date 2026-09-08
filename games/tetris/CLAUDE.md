# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
npm run dev      # Start Vite dev server (hot reload)
npm run build    # Type-check with tsc, then bundle with Vite → dist/
npm run preview  # Serve the dist/ build locally
```

There are no tests in this project.

## Architecture

This is a browser-based Tetris game built with TypeScript and Vite. All game code lives in `src/`.

**Data flow:** `input.ts` captures keydown events → calls `dispatch()` in `main.ts` → `game.ts` reducer produces new `GameState` → `renderer.ts` draws it to canvas.

**Module responsibilities:**

- [src/game.ts](src/game.ts) — Pure reducer. `gameReducer(state, action) → state`. All game rules live here: piece movement, collision via `fits()`, locking, line clearing, scoring, hold, and level progression. No DOM or audio.
- [src/main.ts](src/main.ts) — Entry point and wiring. Owns canvas elements, the `requestAnimationFrame` loop, `dispatch()` (which calls the reducer and triggers audio side-effects), UI DOM updates, theme persistence via `localStorage`, and BGM lifecycle.
- [src/renderer.ts](src/renderer.ts) — Canvas drawing only. Reads `GameState`, draws the board, current piece, ghost piece (dashed outline at drop position), and next/hold previews. No game logic.
- [src/tetrominoes.ts](src/tetrominoes.ts) — Shape matrices for all 7 pieces (I O T S Z J L) at 4 rotations each. `getColor()` returns the current theme accent (a single accent color for all pieces — not per-piece colors). `setBlockAccent()` updates it when the theme changes.
- [src/themes.ts](src/themes.ts) — Five named themes (cyan, emerald, amber, rose, violet). Each has an `accent` hex and a CSS `glow` shadow string. Theme selection is persisted to `localStorage` under key `tetris-theme`.
- [src/audio.ts](src/audio.ts) — Web Audio API only (no audio files). BGM is a programmatically scheduled chiptune loop. SFX are short synthesized tones. All functions are fire-and-forget and silently swallow errors.
- [src/input.ts](src/input.ts) — Maps `KeyboardEvent.code` to `GameAction` types. Key repeat is allowed only for move left/right/down.
- [src/constants.ts](src/constants.ts) — Single source of truth for grid size (10×20), fall speed curve, scoring, and spawn position.

**Theme system:** When the user picks a theme, `applyTheme()` in `main.ts` sets two CSS custom properties (`--accent`, `--shadow-glow`) on `:root` and calls `setBlockAccent()` so `renderer.ts` uses the new color on the next frame. All 7 tetrominos share the same accent color.

**Game loop:** `requestAnimationFrame` always runs. On each frame, if the game is playing and enough time has elapsed (`getFallDelayMs(level)`), a `tick` action is dispatched (which calls `move_down`). BGM is synced to game status on every frame via `syncBGM()`.

**Pause-and-decide (`?pause_and_decide=1`):** After `start` and after a piece locks, `dispatch()` auto-pauses so gravity (`tick`) is frozen. Player actions (move / rotate / drop / hold) still apply: `dispatch()` briefly resumes, runs the reducer, then re-pauses. Regular pause (no URL flag) still blocks all input.
