/**
 * Input: keyboard (and later touch) handling.
 * Emits actions; does not mutate game state directly.
 * @module input
 */

import type { GameAction } from './game';

export type InputAction = GameAction;

const keyToAction: Record<string, InputAction['type']> = {
  ArrowLeft: 'move_left',
  ArrowRight: 'move_right',
  ArrowDown: 'move_down',
  ArrowUp: 'rotate_cw',
  KeyA: 'rotate_ccw',
  KeyD: 'rotate_cw',
  KeyS: 'move_down',
  KeyW: 'rotate_cw',
  Space: 'hard_drop',
  KeyC: 'hold',
  KeyP: 'pause',
  KeyR: 'restart',
  Enter: 'start',
};

/** Map key event to game action, or null if not bound. */
export function keyToGameAction(e: KeyboardEvent): InputAction | null {
  if (e.repeat) {
    // Allow repeat only for left/right/down
    const repeatable = ['move_left', 'move_right', 'move_down'];
    const action = keyToAction[e.code];
    if (action && repeatable.includes(action)) {
      return { type: action };
    }
    return null;
  }
  const type = keyToAction[e.code];
  if (!type) return null;
  return { type };
}

export interface InputHandler {
  onAction: (action: InputAction) => void;
}

/**
 * Attach keyboard listener to document; calls onAction for each recognized key.
 * Returns a cleanup function.
 */
export function attachKeyboard(handler: InputHandler): () => void {
  const listener = (e: KeyboardEvent) => {
    const action = keyToGameAction(e);
    if (action) {
      e.preventDefault();
      handler.onAction(action);
    }
  };
  document.addEventListener('keydown', listener);
  return () => document.removeEventListener('keydown', listener);
}
