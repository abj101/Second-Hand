/**
 * Game state and logic: board, piece, score, level, line clear, game over.
 * Pure logic where possible; no rendering or input.
 * @module game
 */

import {
  COLS,
  ROWS,
  LINES_PER_LEVEL,
  BASE_FALL_MS,
  MIN_FALL_MS,
  LEVEL_SPEED_FACTOR,
  LINE_SCORES,
  SPAWN_COL,
  SPAWN_ROW,
} from './constants';
import {
  type TetrominoType,
  getShape,
  randomType,
} from './tetrominoes';

/** Cell value: 0 = empty, or tetromino type for color */
export type CellValue = 0 | TetrominoType;

/** Board: ROWS x COLS, row-major. board[row][col] */
export type Board = CellValue[][];

export type GameStatus = 'idle' | 'playing' | 'paused' | 'gameover';

export interface PieceState {
  type: TetrominoType;
  row: number;
  col: number;
  rotation: number;
}

export interface GameState {
  board: Board;
  piece: PieceState | null;
  nextPiece: TetrominoType;
  holdPiece: TetrominoType | null;
  canHold: boolean;
  score: number;
  level: number;
  linesCleared: number;
  status: GameStatus;
  lastClearedLines: number; // for feedback: number of lines just cleared
  lastLocked: boolean; // true for one frame when piece merges (for lock SFX)
}

/** Create an empty board (all zeros). */
export function createEmptyBoard(): Board {
  return Array.from({ length: ROWS }, () => Array(COLS).fill(0));
}

/** Create initial game state. */
export function createInitialState(): GameState {
  return {
    board: createEmptyBoard(),
    piece: null,
    nextPiece: randomType(),
    holdPiece: null,
    canHold: true,
    score: 0,
    level: 1,
    linesCleared: 0,
    status: 'idle',
    lastClearedLines: 0,
    lastLocked: false,
  };
}

/** Get fall delay in ms for current level. Optional override skips the level curve. */
export function getFallDelayMs(level: number, overrideMs?: number): number {
  if (overrideMs != null && overrideMs > 0) return overrideMs;
  const delay = BASE_FALL_MS * Math.pow(LEVEL_SPEED_FACTOR, level - 1);
  return Math.max(MIN_FALL_MS, Math.floor(delay));
}

/** Check if a piece at (row, col) with given shape fits on the board (no overlap, in bounds). */
export function fits(
  board: Board,
  shape: readonly (readonly number[])[],
  row: number,
  col: number
): boolean {
  const h = shape.length;
  const w = shape[0].length;
  for (let r = 0; r < h; r++) {
    for (let c = 0; c < w; c++) {
      if (!shape[r][c]) continue;
      const nr = row + r;
      const nc = col + c;
      if (nr < 0 || nr >= ROWS || nc < 0 || nc >= COLS) return false;
      if (board[nr][nc]) return false;
    }
  }
  return true;
}

/** Lock current piece onto the board and return new board + lines cleared. */
function lockPiece(
  board: Board,
  piece: PieceState
): { board: Board; linesCleared: number } {
  const shape = getShape(piece.type, piece.rotation);
  const newBoard = board.map((row) => row.slice());
  const h = shape.length;
  const w = shape[0].length;
  for (let r = 0; r < h; r++) {
    for (let c = 0; c < w; c++) {
      if (shape[r][c]) {
        const nr = piece.row + r;
        const nc = piece.col + c;
        if (nr >= 0 && nr < ROWS && nc >= 0 && nc < COLS) {
          newBoard[nr][nc] = piece.type;
        }
      }
    }
  }
  const fullRows: number[] = [];
  for (let r = ROWS - 1; r >= 0; r--) {
    if (newBoard[r].every((cell) => cell !== 0)) fullRows.push(r);
  }
  const linesCleared = fullRows.length;
  if (linesCleared === 0) return { board: newBoard, linesCleared: 0 };

  // Remove full rows and add empty rows at top
  const filtered = newBoard.filter((_, i) => !fullRows.includes(i));
  const emptyRows = Array.from({ length: linesCleared }, () =>
    Array(COLS).fill(0)
  );
  const finalBoard = [...emptyRows, ...filtered];
  return { board: finalBoard, linesCleared };
}

/** Spawn next piece. Returns new piece or null if spawn overlaps (game over). */
function spawnNext(nextType: TetrominoType, board: Board): PieceState | null {
  const piece: PieceState = {
    type: nextType,
    row: SPAWN_ROW,
    col: SPAWN_COL,
    rotation: 0,
  };
  const shape = getShape(piece.type, piece.rotation);
  if (fits(board, shape, piece.row, piece.col)) return piece;
  return null;
}

/** Game action types for the reducer. */
export type GameAction =
  | { type: 'start' }
  | { type: 'pause' }
  | { type: 'resume' }
  | { type: 'restart' }
  | { type: 'move_left' }
  | { type: 'move_right' }
  | { type: 'move_down' }
  | { type: 'rotate_cw' }
  | { type: 'rotate_ccw' }
  | { type: 'hard_drop' }
  | { type: 'hold' }
  | { type: 'tick' };

/**
 * Reducer: (state, action) => newState.
 * Handles all game actions; no side effects.
 */
export function gameReducer(state: GameState, action: GameAction): GameState {
  switch (action.type) {
    case 'start': {
      if (state.status !== 'idle') return state;
      const next = state.nextPiece;
      const piece = spawnNext(next, state.board);
      if (!piece) return { ...state, status: 'gameover', lastLocked: false };
      return {
        ...state,
        status: 'playing',
        piece,
        nextPiece: randomType(),
        canHold: true,
        lastLocked: false,
      };
    }

    case 'pause': {
      if (state.status !== 'playing') return state;
      return { ...state, status: 'paused', lastLocked: false };
    }

    case 'resume': {
      if (state.status !== 'paused') return state;
      return { ...state, status: 'playing', lastLocked: false };
    }

    case 'restart': {
      return {
        ...createInitialState(),
        status: 'idle',
      };
    }

    case 'move_left': {
      if (state.status !== 'playing' || !state.piece) return state;
      const shape = getShape(state.piece.type, state.piece.rotation);
      if (fits(state.board, shape, state.piece.row, state.piece.col - 1)) {
        return {
          ...state,
          piece: { ...state.piece, col: state.piece.col - 1 },
          lastLocked: false,
        };
      }
      return state;
    }

    case 'move_right': {
      if (state.status !== 'playing' || !state.piece) return state;
      const shape = getShape(state.piece.type, state.piece.rotation);
      if (fits(state.board, shape, state.piece.row, state.piece.col + 1)) {
        return {
          ...state,
          piece: { ...state.piece, col: state.piece.col + 1 },
          lastLocked: false,
        };
      }
      return state;
    }

    case 'move_down': {
      if (state.status !== 'playing' || !state.piece) return state;
      const shape = getShape(state.piece.type, state.piece.rotation);
      if (fits(state.board, shape, state.piece.row + 1, state.piece.col)) {
        return {
          ...state,
          piece: { ...state.piece, row: state.piece.row + 1 },
          lastLocked: false,
        };
      }
      // Lock piece
      const { board: newBoard, linesCleared } = lockPiece(
        state.board,
        state.piece
      );
      const scoreAdd = LINE_SCORES[linesCleared] ?? 0;
      const newLines = state.linesCleared + linesCleared;
      const newLevel =
        Math.floor(newLines / LINES_PER_LEVEL) + 1;
      const next = state.nextPiece;
      const nextPiece = spawnNext(next, newBoard);
      if (!nextPiece) {
        return {
          ...state,
          board: newBoard,
          piece: null,
          score: state.score + scoreAdd,
          linesCleared: newLines,
          level: newLevel,
          status: 'gameover',
          lastClearedLines: linesCleared,
          lastLocked: true,
        };
      }
      return {
        ...state,
        board: newBoard,
        piece: nextPiece,
        nextPiece: randomType(),
        canHold: true,
        score: state.score + scoreAdd,
        linesCleared: newLines,
        level: newLevel,
        lastClearedLines: linesCleared,
        lastLocked: true,
      };
    }

    case 'rotate_cw': {
      if (state.status !== 'playing' || !state.piece) return state;
      const rot = (state.piece.rotation + 1) % 4;
      const shape = getShape(state.piece.type, rot);
      if (fits(state.board, shape, state.piece.row, state.piece.col)) {
        return { ...state, piece: { ...state.piece, rotation: rot }, lastLocked: false };
      }
      return state;
    }

    case 'rotate_ccw': {
      if (state.status !== 'playing' || !state.piece) return state;
      const rot = (state.piece.rotation + 3) % 4;
      const shape = getShape(state.piece.type, rot);
      if (fits(state.board, shape, state.piece.row, state.piece.col)) {
        return { ...state, piece: { ...state.piece, rotation: rot }, lastLocked: false };
      }
      return state;
    }

    case 'hard_drop': {
      if (state.status !== 'playing' || !state.piece) return state;
      let row = state.piece.row;
      const shape = getShape(state.piece.type, state.piece.rotation);
      while (fits(state.board, shape, row + 1, state.piece!.col)) {
        row++;
      }
      const dropped = { ...state.piece, row };
      const { board: newBoard, linesCleared } = lockPiece(state.board, dropped);
      const scoreAdd = LINE_SCORES[linesCleared] ?? 0;
      const newLines = state.linesCleared + linesCleared;
      const newLevel = Math.floor(newLines / LINES_PER_LEVEL) + 1;
      const next = state.nextPiece;
      const nextPiece = spawnNext(next, newBoard);
      if (!nextPiece) {
        return {
          ...state,
          board: newBoard,
          piece: null,
          score: state.score + scoreAdd,
          linesCleared: newLines,
          level: newLevel,
          status: 'gameover',
          lastClearedLines: linesCleared,
          lastLocked: true,
        };
      }
      return {
        ...state,
        board: newBoard,
        piece: nextPiece,
        nextPiece: randomType(),
        canHold: true,
        score: state.score + scoreAdd,
        linesCleared: newLines,
        level: newLevel,
        lastClearedLines: linesCleared,
        lastLocked: true,
      };
    }

    case 'hold': {
      if (state.status !== 'playing' || !state.piece || !state.canHold)
        return state;
      const currentType = state.piece.type;
      // Swap: put current in hold; spawn either the piece that was in hold, or next (if hold was empty)
      const spawnType = state.holdPiece !== null ? state.holdPiece : state.nextPiece;
      const nextPieceAfterHold =
        state.holdPiece !== null ? state.nextPiece : randomType();
      const spawnedPiece: PieceState = {
        type: spawnType,
        row: SPAWN_ROW,
        col: SPAWN_COL,
        rotation: 0,
      };
      const shape = getShape(spawnedPiece.type, 0);
      if (!fits(state.board, shape, spawnedPiece.row, spawnedPiece.col)) {
        return { ...state, status: 'gameover', lastLocked: false };
      }
      return {
        ...state,
        piece: spawnedPiece,
        nextPiece: nextPieceAfterHold,
        holdPiece: currentType,
        canHold: false,
        lastLocked: false,
      };
    }

    case 'tick': {
      if (state.status !== 'playing') return state;
      return gameReducer(state, { type: 'move_down' });
    }

    default:
      return state;
  }
}
