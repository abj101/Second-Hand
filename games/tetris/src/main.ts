/**
 * Entry point: wires game, renderer, and input; initializes canvas and game loop.
 * @module main
 */

import { COLS, LINE_SCORES, ROWS } from './constants';
import { createInitialState, gameReducer, getFallDelayMs } from './game';
import type { GameAction, GameState } from './game';
import { attachKeyboard } from './input';
import { render, renderNextPiece, renderHoldPiece } from './renderer';
import {
  playGameOver,
  playHardDrop,
  playHold,
  playLineClear,
  playLock,
  playMove,
  playRotate,
  playSoftDrop,
  startBGM,
  stopBGM,
  setBGMVolume,
} from './audio';
import { setBlockAccent } from './tetrominoes';
import {
  getThemeById,
  getDefaultTheme,
  getNextThemeId,
  type Theme,
} from './themes';

const THEME_STORAGE_KEY = 'tetris-theme';

function parseAvatarFlags(): { pauseAndDecide: boolean; fallMsOverride?: number } {
  const params = new URLSearchParams(window.location.search);
  const pauseAndDecide =
    params.get('pause_and_decide') === '1' || params.get('avatar') === '1';
  const fallRaw = params.get('fall_ms');
  const fallMsOverride = fallRaw ? Number(fallRaw) : undefined;
  return {
    pauseAndDecide,
    fallMsOverride:
      fallMsOverride != null && Number.isFinite(fallMsOverride)
        ? fallMsOverride
        : undefined,
  };
}

const avatarFlags = parseAvatarFlags();

function hexToRgb(hex: string): [number, number, number] {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

const CELL_SIZE = 42;
const PADDING = 3;

let state: GameState = createInitialState();
let lastTick = 0;
let bgmEnabled = true;
let currentTheme: Theme = getDefaultTheme();

function applyTheme(theme: Theme): void {
  currentTheme = theme;
  document.documentElement.style.setProperty('--accent', theme.accent);
  document.documentElement.style.setProperty('--shadow-glow', theme.glow);
  setBlockAccent(theme.accent);
  const label = document.getElementById('theme-label');
  if (label) label.textContent = theme.name;
}
const SCORE_POP_DURATION_MS = 700;
let scorePopValue = 0;
let scorePopStartTime = 0;

const canvas = document.getElementById('game-canvas') as HTMLCanvasElement;
const ctx = canvas.getContext('2d');
if (!ctx) throw new Error('Canvas 2D not available');

const boardWidth = COLS * CELL_SIZE + 8;
const boardHeight = ROWS * CELL_SIZE + 8;
canvas.width = boardWidth;
canvas.height = boardHeight;

const nextCanvas = document.getElementById('next-canvas') as HTMLCanvasElement;
const nextCtx = nextCanvas.getContext('2d');
if (nextCtx) {
  nextCanvas.width = 4 * CELL_SIZE;
  nextCanvas.height = 2 * CELL_SIZE;
}

const holdCanvas = document.getElementById('hold-canvas') as HTMLCanvasElement;
const holdCtx = holdCanvas.getContext('2d');
if (holdCtx) {
  holdCanvas.width = 4 * CELL_SIZE;
  holdCanvas.height = 2 * CELL_SIZE;
}

function publishOracle(): void {
  // Backend-only scoring hook. Never send this object to a VLM.
  (window as unknown as { __AVATAR_ORACLE__: unknown }).__AVATAR_ORACLE__ = {
    fixture: 'tetris',
    board: state.board,
    piece: state.piece,
    nextPiece: state.nextPiece,
    holdPiece: state.holdPiece,
    score: state.score,
    level: state.level,
    linesCleared: state.linesCleared,
    status: state.status,
    lastLocked: state.lastLocked,
    pauseAndDecide: avatarFlags.pauseAndDecide,
    fallMsOverride: avatarFlags.fallMsOverride ?? null,
  };
}

/** Player actions that should apply during pause-and-decide (gravity stays frozen). */
const DECIDE_ACTIONS: ReadonlySet<GameAction['type']> = new Set([
  'move_left',
  'move_right',
  'move_down',
  'rotate_cw',
  'rotate_ccw',
  'hard_drop',
  'hold',
]);

function dispatch(action: GameAction): void {
  const prevState = state;
  const prevScore = state.score;
  // Pause-and-decide freezes ticks, not input. Resume, apply, re-pause.
  if (
    avatarFlags.pauseAndDecide &&
    state.status === 'paused' &&
    DECIDE_ACTIONS.has(action.type)
  ) {
    state = gameReducer(state, { type: 'resume' });
  }
  state = gameReducer(state, action);

  if (action.type === 'hard_drop' && state.status === 'playing') {
    playHardDrop();
  }
  if (action.type === 'move_left' || action.type === 'move_right') {
    if (prevState.piece && state.piece && state.piece.col !== prevState.piece.col) {
      playMove();
    }
  }
  if (action.type === 'rotate_cw' || action.type === 'rotate_ccw') {
    if (prevState.piece && state.piece && state.piece.rotation !== prevState.piece.rotation) {
      playRotate();
    }
  }
  if (action.type === 'move_down' && prevState.piece && state.piece && !state.lastLocked) {
    if (state.piece.row > prevState.piece.row) playSoftDrop();
  }
  if (state.lastLocked && state.lastClearedLines === 0) {
    playLock();
  }
  if (action.type === 'hold' && prevState.holdPiece !== state.holdPiece) {
    playHold();
  }
  if (prevState.status !== 'gameover' && state.status === 'gameover') {
    playGameOver();
  }
  if (state.score > prevScore && state.lastClearedLines > 0) {
    scorePopValue = LINE_SCORES[state.lastClearedLines] ?? 0;
    scorePopStartTime = performance.now();
    playLineClear(state.lastClearedLines);
  }

  if (
    avatarFlags.pauseAndDecide &&
    state.status === 'playing' &&
    (action.type === 'start' || state.lastLocked || DECIDE_ACTIONS.has(action.type))
  ) {
    state = gameReducer(state, { type: 'pause' });
  }
  publishOracle();
}

function drawNextPiece(): void {
  if (!nextCtx) return;
  nextCtx.fillStyle = '#11141c';
  nextCtx.beginPath();
  nextCtx.roundRect(0, 0, nextCanvas.width, nextCanvas.height, 8);
  nextCtx.fill();
  renderNextPiece(nextCtx, state.nextPiece, CELL_SIZE, PADDING);
}

function drawHoldPiece(): void {
  if (!holdCtx) return;
  holdCtx.fillStyle = '#11141c';
  holdCtx.beginPath();
  holdCtx.roundRect(0, 0, holdCanvas.width, holdCanvas.height, 8);
  holdCtx.fill();
  renderHoldPiece(holdCtx, state.holdPiece, CELL_SIZE, PADDING);
}

function updateUI(): void {
  const scoreEl = document.getElementById('score');
  const levelEl = document.getElementById('level');
  const linesEl = document.getElementById('lines');
  const statusEl = document.getElementById('status');
  if (scoreEl) scoreEl.textContent = String(state.score);
  if (levelEl) levelEl.textContent = String(state.level);
  if (linesEl) linesEl.textContent = String(state.linesCleared);
  if (statusEl) {
    if (state.status === 'idle') statusEl.textContent = 'Press Enter to Start';
    else if (state.status === 'paused')
      statusEl.textContent = avatarFlags.pauseAndDecide
        ? 'Paused (decide)'
        : 'Paused';
    else if (state.status === 'gameover') statusEl.textContent = 'Game Over';
    else statusEl.textContent = '';
  }
}

function drawScorePop(ctx: CanvasRenderingContext2D): void {
  if (scorePopStartTime <= 0 || scorePopValue <= 0) return;
  const now = performance.now();
  const elapsed = now - scorePopStartTime;
  if (elapsed >= SCORE_POP_DURATION_MS) {
    scorePopStartTime = 0;
    scorePopValue = 0;
    return;
  }
  const t = elapsed / SCORE_POP_DURATION_MS;
  const scale = 1 + 0.4 * (1 - t);
  const alpha = 1 - t * t;
  const cx = canvas.width / 2;
  const y = canvas.height * 0.35;

  // Brief flash overlay when lines clear
  if (elapsed < 120) {
    const flashAlpha = (1 - elapsed / 120) * 0.3;
    ctx.save();
    ctx.fillStyle = `rgba(255, 255, 255, ${flashAlpha})`;
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.restore();
  }

  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.translate(cx, y);
  ctx.scale(scale, scale);
  ctx.fillStyle = '#ffffff';
  ctx.font = 'bold 38px system-ui, sans-serif';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  const [r, g, b] = hexToRgb(currentTheme.accent);
  ctx.shadowColor = `rgba(${r}, ${g}, ${b}, 0.8)`;
  ctx.shadowBlur = 12;
  ctx.fillText(`+${scorePopValue}`, 0, 0);
  ctx.shadowBlur = 0;
  ctx.restore();
}

function draw(): void {
  if (!ctx) return;
  render(ctx, state, { canvas, cellSize: CELL_SIZE, padding: PADDING });
  drawScorePop(ctx);
  drawNextPiece();
  drawHoldPiece();
  updateUI();
}

function syncBGM(): void {
  if (!bgmEnabled) {
    stopBGM();
    return;
  }
  if (state.status === 'playing') {
    startBGM();
    setBGMVolume(0.12);
  } else if (state.status === 'paused') {
    setBGMVolume(0);
  } else {
    stopBGM();
  }
}

function gameLoop(now: number): void {
  requestAnimationFrame(gameLoop);
  if (state.status === 'playing' && state.piece) {
    const delay = getFallDelayMs(state.level, avatarFlags.fallMsOverride);
    if (now - lastTick >= delay) {
      lastTick = now;
      dispatch({ type: 'tick' });
    }
  } else {
    lastTick = now;
  }
  syncBGM();
  draw();
}

// Apply saved theme or default (cyan) on load
const savedThemeId = localStorage.getItem(THEME_STORAGE_KEY);
applyTheme(savedThemeId ? getThemeById(savedThemeId) : getDefaultTheme());

const themeToggle = document.getElementById('theme-toggle');
if (themeToggle) {
  themeToggle.addEventListener('click', () => {
    const nextId = getNextThemeId(currentTheme.id);
    const nextTheme = getThemeById(nextId);
    applyTheme(nextTheme);
    localStorage.setItem(THEME_STORAGE_KEY, nextId);
  });
}

const musicToggle = document.getElementById('music-toggle');
if (musicToggle) {
  musicToggle.addEventListener('click', () => {
    bgmEnabled = !bgmEnabled;
    musicToggle.setAttribute('aria-pressed', String(bgmEnabled));
    syncBGM();
  });
}

attachKeyboard({
  onAction(action) {
    if (action.type === 'start' && state.status === 'gameover') {
      dispatch({ type: 'restart' });
      dispatch({ type: 'start' });
      return;
    }
    if (action.type === 'pause') {
      dispatch(state.status === 'playing' ? { type: 'pause' } : { type: 'resume' });
      return;
    }
    dispatch(action);
  },
});

(window as unknown as { __AVATAR_DISPATCH__: (action: GameAction) => void }).__AVATAR_DISPATCH__ =
  dispatch;

lastTick = performance.now();
publishOracle();
  requestAnimationFrame(gameLoop);
draw();
