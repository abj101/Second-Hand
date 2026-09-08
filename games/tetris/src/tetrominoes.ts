/**
 * Tetromino shapes, colors, and rotation helpers.
 * Standard 7 pieces: I, O, T, S, Z, J, L.
 * @module tetrominoes
 */

export type TetrominoType = 'I' | 'O' | 'T' | 'S' | 'Z' | 'J' | 'L';

/** Shape as 4x4 grid: 1 = cell filled, 0 = empty. Each element is a row. */
export type ShapeMatrix = readonly (readonly number[])[];

/** Default block accent (cyan). Used until theme is applied. */
const DEFAULT_BLOCK_ACCENT = '#22d3ee';

/** Current block accent — updated when user changes theme. */
let currentBlockAccent = DEFAULT_BLOCK_ACCENT;

/** Set the block accent color (used by theme switcher). */
export function setBlockAccent(hex: string): void {
  currentBlockAccent = hex;
}

export const TETROMINO_COLORS: Record<TetrominoType, string> = {
  I: DEFAULT_BLOCK_ACCENT,
  O: DEFAULT_BLOCK_ACCENT,
  T: DEFAULT_BLOCK_ACCENT,
  S: DEFAULT_BLOCK_ACCENT,
  Z: DEFAULT_BLOCK_ACCENT,
  J: DEFAULT_BLOCK_ACCENT,
  L: DEFAULT_BLOCK_ACCENT,
};

/** Default block color (fallback). */
export const BLOCK_COLOR = DEFAULT_BLOCK_ACCENT;

/** Shapes in default rotation (index 0). CW rotation = next index. */
const SHAPES: Record<TetrominoType, ShapeMatrix[]> = {
  I: [
    [[0, 0, 0, 0], [1, 1, 1, 1], [0, 0, 0, 0], [0, 0, 0, 0]],
    [[0, 0, 1, 0], [0, 0, 1, 0], [0, 0, 1, 0], [0, 0, 1, 0]],
    [[0, 0, 0, 0], [0, 0, 0, 0], [1, 1, 1, 1], [0, 0, 0, 0]],
    [[0, 1, 0, 0], [0, 1, 0, 0], [0, 1, 0, 0], [0, 1, 0, 0]],
  ],
  O: [
    [[1, 1], [1, 1]],
    [[1, 1], [1, 1]],
    [[1, 1], [1, 1]],
    [[1, 1], [1, 1]],
  ],
  T: [
    [[0, 1, 0], [1, 1, 1], [0, 0, 0]],
    [[0, 1, 0], [0, 1, 1], [0, 1, 0]],
    [[0, 0, 0], [1, 1, 1], [0, 1, 0]],
    [[0, 1, 0], [1, 1, 0], [0, 1, 0]],
  ],
  S: [
    [[0, 1, 1], [1, 1, 0], [0, 0, 0]],
    [[0, 1, 0], [0, 1, 1], [0, 0, 1]],
    [[0, 0, 0], [0, 1, 1], [1, 1, 0]],
    [[1, 0, 0], [1, 1, 0], [0, 1, 0]],
  ],
  Z: [
    [[1, 1, 0], [0, 1, 1], [0, 0, 0]],
    [[0, 0, 1], [0, 1, 1], [0, 1, 0]],
    [[0, 0, 0], [1, 1, 0], [0, 1, 1]],
    [[0, 1, 0], [1, 1, 0], [1, 0, 0]],
  ],
  J: [
    [[1, 0, 0], [1, 1, 1], [0, 0, 0]],
    [[0, 1, 1], [0, 1, 0], [0, 1, 0]],
    [[0, 0, 0], [1, 1, 1], [0, 0, 1]],
    [[0, 1, 0], [0, 1, 0], [1, 1, 0]],
  ],
  L: [
    [[0, 0, 1], [1, 1, 1], [0, 0, 0]],
    [[0, 1, 0], [0, 1, 0], [0, 1, 1]],
    [[0, 0, 0], [1, 1, 1], [1, 0, 0]],
    [[1, 1, 0], [0, 1, 0], [0, 1, 0]],
  ],
};

const TETROMINO_TYPES: TetrominoType[] = ['I', 'O', 'T', 'S', 'Z', 'J', 'L'];

/**
 * Returns the shape matrix for a tetromino at a given rotation (0–3).
 */
export function getShape(type: TetrominoType, rotation: number): ShapeMatrix {
  const shapes = SHAPES[type];
  return shapes[rotation % shapes.length];
}

/**
 * Returns the color for a tetromino type (uses current theme accent).
 */
export function getColor(_type: TetrominoType): string {
  return currentBlockAccent;
}

/**
 * Returns a random tetromino type (uniform over I, O, T, S, Z, J, L).
 */
export function randomType(): TetrominoType {
  return TETROMINO_TYPES[Math.floor(Math.random() * TETROMINO_TYPES.length)];
}

/**
 * All tetromino types (for iteration / bags if needed later).
 */
export function allTypes(): readonly TetrominoType[] {
  return TETROMINO_TYPES;
}
