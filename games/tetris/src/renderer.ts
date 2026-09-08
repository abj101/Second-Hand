/**
 * Renderer: draws board, current piece, next piece, hold, and UI.
 * No game rules; only draws from GameState.
 * @module renderer
 */

import { COLS, ROWS } from './constants';
import { fits as boardFits } from './game';
import type { Board, GameState, PieceState } from './game';
import { getShape, getColor, type TetrominoType } from './tetrominoes';

export interface RendererConfig {
  canvas: HTMLCanvasElement;
  cellSize: number;
  padding?: number;
}

/** Parse hex color to r, g, b (0–255). */
function hexToRgb(hex: string): { r: number; g: number; b: number } {
  const n = parseInt(hex.slice(1), 16);
  return { r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255 };
}

/** Return CSS color from r,g,b. */
function rgb(r: number, g: number, b: number): string {
  return `rgb(${Math.round(r)},${Math.round(g)},${Math.round(b)})`;
}

/** Draw a single 3D-style block at grid (c, r) with depth and highlights. */
function drawCell(
  ctx: CanvasRenderingContext2D,
  c: number,
  r: number,
  cellSize: number,
  color: string,
  padding: number
): void {
  const { r: cr, g: cg, b: cb } = hexToRgb(color);
  const x = c * cellSize + padding;
  const y = r * cellSize + padding;
  const size = cellSize - padding * 2;
  const radius = Math.max(2, Math.min(5, size / 8));
  const bevel = Math.max(1.5, size * 0.12);

  // Main face (slightly darkened for depth)
  const face = rgb(cr * 0.92, cg * 0.92, cb * 0.92);
  ctx.fillStyle = face;
  ctx.beginPath();
  ctx.roundRect(x, y, size, size, radius);
  ctx.fill();

  // Top bevel (highlight)
  const topLight = rgb(
    Math.min(255, cr * 1.35),
    Math.min(255, cg * 1.35),
    Math.min(255, cb * 1.35)
  );
  const topGrad = ctx.createLinearGradient(x, y, x, y + bevel * 2);
  topGrad.addColorStop(0, topLight);
  topGrad.addColorStop(1, face);
  ctx.fillStyle = topGrad;
  ctx.beginPath();
  ctx.roundRect(x, y, size, bevel * 2, radius);
  ctx.fill();

  // Left bevel (softer highlight)
  const leftGrad = ctx.createLinearGradient(x, y, x + bevel * 1.5, y);
  leftGrad.addColorStop(0, rgb(Math.min(255, cr * 1.15), Math.min(255, cg * 1.15), Math.min(255, cb * 1.15)));
  leftGrad.addColorStop(1, face);
  ctx.fillStyle = leftGrad;
  ctx.beginPath();
  ctx.roundRect(x, y, bevel * 1.5, size, radius);
  ctx.fill();

  // Right edge (shadow)
  const rightDark = rgb(cr * 0.5, cg * 0.5, cb * 0.5);
  const rightGrad = ctx.createLinearGradient(x + size - bevel, y, x + size, y);
  rightGrad.addColorStop(0, face);
  rightGrad.addColorStop(1, rightDark);
  ctx.fillStyle = rightGrad;
  ctx.beginPath();
  ctx.roundRect(x + size - bevel, y, bevel, size, radius);
  ctx.fill();

  // Bottom edge (shadow)
  const bottomGrad = ctx.createLinearGradient(x, y + size - bevel, x, y + size);
  bottomGrad.addColorStop(0, face);
  bottomGrad.addColorStop(1, rightDark);
  ctx.fillStyle = bottomGrad;
  ctx.beginPath();
  ctx.roundRect(x, y + size - bevel, size, bevel, radius);
  ctx.fill();

  // Inner specular (small bright spot)
  const specX = x + size * 0.25;
  const specY = y + size * 0.2;
  const specRad = size * 0.2;
  const specGrad = ctx.createRadialGradient(
    specX, specY, 0, specX, specY, specRad
  );
  specGrad.addColorStop(0, 'rgba(255,255,255,0.5)');
  specGrad.addColorStop(1, 'rgba(255,255,255,0)');
  ctx.fillStyle = specGrad;
  ctx.beginPath();
  ctx.arc(specX, specY, specRad, 0, Math.PI * 2);
  ctx.fill();

  // Outline for crisp edges
  ctx.strokeStyle = 'rgba(0,0,0,0.15)';
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.roundRect(x, y, size, size, radius);
  ctx.stroke();
}

/** Draw a piece (solid blocks). Use isGhost=true for outline-only ghost. */
function drawPiece(
  ctx: CanvasRenderingContext2D,
  piece: PieceState,
  cellSize: number,
  padding: number,
  alpha: number = 1,
  isGhost: boolean = false
): void {
  const shape = getShape(piece.type, piece.rotation);
  const color = getColor(piece.type);
  ctx.save();
  ctx.globalAlpha = alpha;

  if (isGhost) {
    // Ghost: dashed outline only, no 3D fill
    const h = shape.length;
    const w = shape[0].length;
    const { r, g, b } = hexToRgb(color);
    ctx.strokeStyle = `rgba(${r},${g},${b},0.6)`;
    ctx.lineWidth = 2;
    ctx.setLineDash([4, 4]);
    for (let ry = 0; ry < h; ry++) {
      for (let cx = 0; cx < w; cx++) {
        if (shape[ry][cx]) {
          const x = (piece.col + cx) * cellSize + padding;
          const y = (piece.row + ry) * cellSize + padding;
          const size = cellSize - padding * 2;
          const radius = Math.max(2, Math.min(5, size / 8));
          ctx.beginPath();
          ctx.roundRect(x, y, size, size, radius);
          ctx.stroke();
        }
      }
    }
    ctx.setLineDash([]);
  } else {
    const h = shape.length;
    const w = shape[0].length;
    for (let r = 0; r < h; r++) {
      for (let c = 0; c < w; c++) {
        if (shape[r][c]) {
          drawCell(
            ctx,
            piece.col + c,
            piece.row + r,
            cellSize,
            color,
            padding
          );
        }
      }
    }
  }
  ctx.restore();
}


/** Compute ghost row (lowest row where piece fits). */
function getGhostRow(board: Board, piece: PieceState): number {
  const shape = getShape(piece.type, piece.rotation);
  let row = piece.row;
  while (boardFits(board, shape, row + 1, piece.col)) {
    row++;
  }
  return row;
}

/**
 * Renders the full game state to the canvas.
 */
export function render(
  ctx: CanvasRenderingContext2D,
  state: GameState,
  config: RendererConfig
): void {
  const { cellSize, padding = 2 } = config;
  const width = COLS * cellSize;
  const height = ROWS * cellSize;
  const boardPadding = 8;

  // Clear — dark gradient background
  const bgGrad = ctx.createLinearGradient(0, 0, 0, config.canvas.height);
  bgGrad.addColorStop(0, '#0a0c12');
  bgGrad.addColorStop(0.5, '#0d0f18');
  bgGrad.addColorStop(1, '#080a10');
  ctx.fillStyle = bgGrad;
  ctx.fillRect(0, 0, config.canvas.width, config.canvas.height);

  // Board outer frame (raised border)
  ctx.save();
  ctx.translate(0, 0);
  const frameW = width + boardPadding * 2;
  const frameH = height + boardPadding * 2;
  const frameRadius = 14;
  // Outer shadow
  ctx.shadowColor = 'rgba(0,0,0,0.5)';
  ctx.shadowBlur = 20;
  ctx.shadowOffsetY = 8;
  ctx.fillStyle = '#0e1016';
  ctx.beginPath();
  ctx.roundRect(0, 0, frameW, frameH, frameRadius);
  ctx.fill();
  ctx.shadowColor = 'transparent';
  ctx.shadowBlur = 0;
  ctx.shadowOffsetY = 0;
  // Inner well — darker
  ctx.fillStyle = '#0c0e14';
  ctx.beginPath();
  ctx.roundRect(boardPadding * 0.5, boardPadding * 0.5, frameW - boardPadding, frameH - boardPadding, frameRadius - 4);
  ctx.fill();
  // Main well background
  ctx.fillStyle = '#11141c';
  ctx.beginPath();
  ctx.roundRect(boardPadding, boardPadding, width, height, 10);
  ctx.fill();
  // Subtle inner border
  ctx.strokeStyle = 'rgba(255,255,255,0.04)';
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.roundRect(boardPadding, boardPadding, width, height, 10);
  ctx.stroke();

  // Grid lines in the well (subtle)
  ctx.strokeStyle = 'rgba(255,255,255,0.03)';
  ctx.lineWidth = 1;
  for (let col = 0; col <= COLS; col++) {
    const px = boardPadding + col * cellSize;
    ctx.beginPath();
    ctx.moveTo(px, boardPadding);
    ctx.lineTo(px, boardPadding + height);
    ctx.stroke();
  }
  for (let row = 0; row <= ROWS; row++) {
    const py = boardPadding + row * cellSize;
    ctx.beginPath();
    ctx.moveTo(boardPadding, py);
    ctx.lineTo(boardPadding + width, py);
    ctx.stroke();
  }

  ctx.translate(boardPadding, boardPadding);

  // Locked cells
  for (let r = 0; r < ROWS; r++) {
    for (let c = 0; c < COLS; c++) {
      const cell = state.board[r][c];
      if (cell) {
        drawCell(ctx, c, r, cellSize, getColor(cell as TetrominoType), padding);
      }
    }
  }

  // Ghost piece — dashed outline at drop position
  if (state.piece && state.status === 'playing') {
    const ghost = {
      ...state.piece,
      row: getGhostRow(state.board, state.piece),
    };
    drawPiece(ctx, ghost, cellSize, padding, 1, true);
  }

  // Current piece
  if (state.piece) {
    drawPiece(ctx, state.piece, cellSize, padding, 1, false);
  }

  ctx.restore();
}

/**
 * Renders the "next" piece preview (small) in a given canvas context.
 * Expects ctx to be already translated to the top-left of the preview area.
 */
export function renderNextPiece(
  ctx: CanvasRenderingContext2D,
  nextType: TetrominoType | null,
  cellSize: number,
  padding: number = 2
): void {
  if (!nextType) return;
  const shape = getShape(nextType, 0);
  const color = getColor(nextType);
  const h = shape.length;
  const w = shape[0].length;
  const offsetX = (4 - w) / 2;
  const offsetY = (2 - h / 2) / 2;
  for (let r = 0; r < h; r++) {
    for (let c = 0; c < w; c++) {
      if (shape[r][c]) {
        drawCell(
          ctx,
          offsetX + c,
          offsetY + r,
          cellSize,
          color,
          padding
        );
      }
    }
  }
}

/**
 * Renders the "hold" piece preview.
 */
export function renderHoldPiece(
  ctx: CanvasRenderingContext2D,
  holdType: TetrominoType | null,
  cellSize: number,
  padding: number = 2
): void {
  if (!holdType) return;
  const shape = getShape(holdType, 0);
  const color = getColor(holdType);
  const h = shape.length;
  const w = shape[0].length;
  const offsetX = (4 - w) / 2;
  const offsetY = (2 - h / 2) / 2;
  for (let r = 0; r < h; r++) {
    for (let c = 0; c < w; c++) {
      if (shape[r][c]) {
        drawCell(
          ctx,
          offsetX + c,
          offsetY + r,
          cellSize,
          color,
          padding
        );
      }
    }
  }
}
