/**
 * Tetris constants — single source of truth for grid, speeds, and scoring.
 * @module constants
 */

/** Visible playfield width in columns */
export const COLS = 10;

/** Visible playfield height in rows */
export const ROWS = 20;

/** Number of lines cleared before level increases */
export const LINES_PER_LEVEL = 10;

/** Base fall delay in milliseconds at level 1 */
export const BASE_FALL_MS = 1000;

/** Minimum fall delay (cap at high levels) in milliseconds */
export const MIN_FALL_MS = 100;

/** Level multiplier for speed: delay = BASE_FALL_MS * (0.8 ^ (level - 1)), floored to MIN_FALL_MS */
export const LEVEL_SPEED_FACTOR = 0.8;

/** Score per lines cleared: 1=100, 2=300, 3=500, 4=800 (classic-style) */
export const LINE_SCORES: Record<number, number> = {
  1: 100,
  2: 300,
  3: 500,
  4: 800,
};

/** Spawn column (center of board, 0-based). Piece origin is left-most column of shape. */
export const SPAWN_COL = 3;

/** Spawn row (top of visible board, 0-based) */
export const SPAWN_ROW = 0;

/**
 * Avatar Phase 1: URL `?pause_and_decide=1` freezes gravity (ticks) between
 * decisions. Player moves/rotates/drops still apply; the game re-pauses after.
 * URL `?fall_ms=N` overrides fall delay (also useful for a later realtime row).
 */
