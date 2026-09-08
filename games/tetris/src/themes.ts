/**
 * Color themes for UI accent and block color.
 * Cyan is the default.
 * @module themes
 */

export interface Theme {
  id: string;
  name: string;
  accent: string;
  glow: string;
}

export const THEMES: Theme[] = [
  { id: 'cyan', name: 'Cyan', accent: '#22d3ee', glow: '0 0 40px rgba(34, 211, 238, 0.12)' },
  { id: 'emerald', name: 'Emerald', accent: '#34d399', glow: '0 0 40px rgba(52, 211, 153, 0.12)' },
  { id: 'amber', name: 'Amber', accent: '#fbbf24', glow: '0 0 40px rgba(251, 191, 36, 0.12)' },
  { id: 'rose', name: 'Rose', accent: '#fb7185', glow: '0 0 40px rgba(251, 113, 133, 0.12)' },
  { id: 'violet', name: 'Violet', accent: '#a78bfa', glow: '0 0 40px rgba(167, 139, 250, 0.12)' },
];

const DEFAULT_THEME_ID = 'cyan';

export function getThemeById(id: string): Theme {
  return THEMES.find((t) => t.id === id) ?? THEMES[0];
}

export function getDefaultTheme(): Theme {
  return getThemeById(DEFAULT_THEME_ID);
}

export function getNextThemeId(currentId: string): string {
  const idx = THEMES.findIndex((t) => t.id === currentId);
  const next = (idx + 1) % THEMES.length;
  return THEMES[next].id;
}
